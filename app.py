"""HE-Scope: marimo H&E pathology image viewer.

Run with:  marimo edit app.py   (or)   marimo run app.py

Layout: a left sidebar (Open slide / Display / Navigator / ROIs), a compact
header, ONE toolbar with every mid-session control, and ONE unified plotly
viewer used for zoom, pan AND ROI capture (box / lasso). Secondary panels
(Annotations, Agent console, TCGA browser) live in a collapsed accordion
below the viewer; the agent-connection guide is always visible.

A code agent can call ``get_current_selection()`` (live, zero-click) or
``get_latest_selection()`` (last submitted ROI) at module scope; both return
JSON or the exact string "NO_SELECTION".

If the metadata database is unavailable (bad HESCOPE_DB_URL, missing driver),
the app boots in DB-free mode: annotation storage / agent-run logging panels
show a callout and no-op, while the viewer, ROI tools, TCGA panel and the
jsonl agent bridge keep working.
"""

import marimo

__generated_with = "0.23.16"
app = marimo.App(width="full", css_file="assets/theme.css")


@app.cell(hide_code=True)
def _():
    import json
    import math
    import os
    import tempfile
    import threading
    from dataclasses import replace as dc_replace
    from pathlib import Path

    import marimo as mo

    from hescope import analysis_capabilities
    from hescope.agent.agent_bridge import (
        AgentBridge,
        magnification_for,
        make_annotate_roi_tool,
        make_live_selection_tool,
        make_marimo_tool,
        make_query_annotations_tool,
        make_slide_info_tool,
    )
    from hescope.store.db import export_rois
    from hescope.interop.geojson import slide_geojson_text
    from hescope.interop.importers import (
        import_annotations,
        parse_asap_xml,
        parse_geojson_annotations,
    )
    from hescope.analysis.stats_table import (
        label_summary,
        roi_stats_rows,
        rows_to_csv,
    )
    from hescope.analysis.grid import tissue_fraction_proxy
    from hescope.analysis.heatmap import compute_grid, grid_coverage, render_heatmap
    from hescope.core.measure import format_measurement, measure_box
    from hescope.analysis.ml import (
        list_models,
        load_model,
        make_prob_metric,
        train_from_annotations,
    )
    from hescope.analysis.nuclei import detect_nuclei
    from hescope.viewer.osdviewer import (
        make_viewer,
        osd_current_selection,
        osd_selection_to_roi,
        parse_osd_measure,
        parse_osd_selection,
        raw_osd_selection,
        rois_to_payload,
        viewport_changed,
        viewport_state_from_report,
    )
    from hescope.viewer.overlay import draw_navigator_markers, draw_scale_bar
    from hescope.analysis.qc import qc_report
    from hescope.core.rois import ROI, ViewportState, extract_patch, patch_mpp
    from hescope.wsi.slides import open_slide
    from hescope.viewer.tileserver import (
        DisplayParams,
        SlideRefs,
        ensure_server,
        serve_slide,
    )
    from hescope.viewer.viewer import (
        apply_display_pipeline,
        bootstrap_db,
        current_selection,
        jump_viewport_for_bbox,
        make_roi_figure,
        navigator_image,
        parse_plotly_selection,
        raw_plotly_selection,
        render_viewport,
        roi_from_db_row,
        selection_to_roi,
        viewport_png_bytes,
        viewport_status_line,
    )

    # The OpenSeadragon surface (hescope/osdviewer.py) fed by the loopback tile
    # server (hescope/tileserver.py) is the main viewing surface: real
    # wheel-zoom, real mouse-drag pan, ROI drawing in level-0 coordinates.
    # HESCOPE_DISABLE_OSD=1 falls back to the legacy plotly surface, whose
    # zoom and pan are cosmetic (they rescale an already-rendered bitmap).
    #
    # This was opt-in for one commit, after shipping it on by default left
    # users with a dead viewer. The reason was that a widget can construct
    # fine in Python and still fail to MOUNT in the browser, where nothing
    # server-side can tell -- and with OpenSeadragon driving, the plotly
    # fallback is suppressed. It is on again because that gap is now closed
    # by proof rather than by hope: tests/browser/test_marimo_mount.py drives
    # the real marimo page in headless Chrome and asserts the widget mounts,
    # OpenSeadragon initialises, tiles are actually fetched from the loopback
    # server, and no JS error is raised. Do not flip this back on a hunch --
    # re-run that test.
    def _probe_osd():
        if os.environ.get("HESCOPE_DISABLE_OSD", "").strip().lower() in (
            "1", "true", "yes", "on"
        ):
            return False, "disabled by HESCOPE_DISABLE_OSD"
        try:
            import anywidget  # noqa: F401

            from hescope.viewer.osdviewer import build_esm

            build_esm()  # reads the vendored bundle; cached after the first call
            return True, None
        except Exception as _exc:  # missing dep / missing vendored JS
            return False, f"{type(_exc).__name__}: {_exc}"

    OSD_AVAILABLE, OSD_ERROR = _probe_osd()
    return (
        AgentBridge,
        DisplayParams,
        OSD_AVAILABLE,
        Path,
        ROI,
        SlideRefs,
        ViewportState,
        analysis_capabilities,
        apply_display_pipeline,
        bootstrap_db,
        compute_grid,
        current_selection,
        dc_replace,
        detect_nuclei,
        draw_navigator_markers,
        draw_scale_bar,
        ensure_server,
        export_rois,
        extract_patch,
        format_measurement,
        grid_coverage,
        import_annotations,
        json,
        jump_viewport_for_bbox,
        label_summary,
        list_models,
        load_model,
        magnification_for,
        make_annotate_roi_tool,
        make_live_selection_tool,
        make_marimo_tool,
        make_prob_metric,
        make_query_annotations_tool,
        make_roi_figure,
        make_slide_info_tool,
        make_viewer,
        measure_box,
        mo,
        navigator_image,
        open_slide,
        os,
        osd_current_selection,
        parse_asap_xml,
        parse_geojson_annotations,
        parse_osd_measure,
        patch_mpp,
        qc_report,
        raw_osd_selection,
        raw_plotly_selection,
        render_heatmap,
        render_viewport,
        roi_from_db_row,
        roi_stats_rows,
        rois_to_payload,
        rows_to_csv,
        serve_slide,
        slide_geojson_text,
        threading,
        tissue_fraction_proxy,
        train_from_annotations,
        viewport_changed,
        viewport_png_bytes,
        viewport_state_from_report,
        viewport_status_line,
    )


@app.cell(hide_code=True)
def _(Path):
    from hescope.wsi.demo import generate_demo_slide
    from hescope.core.paths import resolve_runtime_dir

    try:
        _app_dir = Path(__file__).resolve().parent
    except NameError:  # marimo kernel context
        _app_dir = Path.cwd()
    # Writable runtime root. Repo / editable install: the app dir itself
    # (agent_out/, data/, assets/ next to app.py — behavior unchanged).
    # Non-editable install (site-packages): <cwd>/hescope_runtime so we
    # never write user data into the environment's package directory.
    _runtime_dir = resolve_runtime_dir(_app_dir)
    DEMO_SLIDE_PATH = _runtime_dir / "assets" / "demo_he.png"
    OUT_DIR = _runtime_dir / "agent_out"
    MODELS_DIR = _runtime_dir / "data" / "models"
    # Uploads land HERE, not in tempfile.mkdtemp(). A slide's identity is its
    # path (SlideRepo.register is idempotent on UNIQUE(path)), and mkdtemp
    # returns a fresh directory on every call -- so uploading one file twice
    # used to make two unrelated slides, the second of which reported that the
    # slide had no annotations. A stable directory plus a content-derived file
    # name makes re-uploading the same bytes resolve to the same slide, and
    # keeps the row's path from being swept by the OS (R08-1).
    UPLOAD_DIR = _runtime_dir / "data" / "uploads"

    def ensure_demo_slide():
        """Return the demo slide path, generating it in-process if missing.

        Generation is in-process via hescope.wsi.demo (shipped with the wheel);
        tools/make_demo_slide.py is not packaged and is no longer required.
        """
        if not DEMO_SLIDE_PATH.exists():
            generate_demo_slide(DEMO_SLIDE_PATH)
        return DEMO_SLIDE_PATH

    return MODELS_DIR, OUT_DIR, UPLOAD_DIR, ensure_demo_slide


@app.cell(hide_code=True)
def _(bootstrap_db, mo):
    # DB bootstrap with graceful degradation: on any failure the app runs in
    # DB-free mode (db.enabled is False, all DB panels no-op with a callout).
    db = bootstrap_db()
    if db.enabled:
        _backend = db.engine.url.get_backend_name()
        db_status_badge = f"DB: {_backend}"
        db_status_detail = None
    else:
        db_status_badge = "DB-free"
        db_status_detail = mo.callout(
            mo.md(
                f"**Database disabled:** {db.error}. Running in DB-free mode: "
                "annotation storage, agent-run logging and ROI export are off. "
                "The viewer, ROI tools, TCGA panel and the jsonl agent bridge "
                "still work."
            ),
            kind="warn",
        )
    return db, db_status_badge, db_status_detail


@app.cell(hide_code=True)
def _(AgentBridge, OUT_DIR, ViewportState, make_marimo_tool, mo):
    get_source, set_source = mo.state(None)
    get_vp, set_vp = mo.state(
        ViewportState(center=(0.0, 0.0), downsample=1.0, size=(1024, 768))
    )
    get_rois, set_rois = mo.state([])
    get_payload, set_payload = mo.state(None)
    # Phase-3 state: current slide row id (None in DB-free mode), DB panel
    # messages, annotation-table refresh trigger, measurement readout.
    get_slide_id, set_slide_id = mo.state(None)
    get_db_msg, set_db_msg = mo.state(None)  # (kind, text) or None
    get_ann_version, set_ann_version = mo.state(None)  # opaque refresh token
    get_measure_msg, set_measure_msg = mo.state(None)  # (kind, text) or None
    # Tile-server descriptor for the open slide (hescope.viewer.tileserver.serve_slide
    # output) or None when the OpenSeadragon path is unavailable and the plotly
    # fallback is driving the viewer.
    get_tiles, set_tiles = mo.state(None)
    # Camera COMMAND channel: (bbox_level0, token). Programmatic moves (pan
    # buttons, zoom slider, zoom-to-fit, "View ROI", annotation click-to-jump)
    # publish here; a consumer cell forwards them to the widget. The token is a
    # fresh object() per command so an identical bbox still moves the camera.
    get_cam, set_cam = mo.state(None)

    # Plain (non-reactive) bus for the widget consumer cell. It must NOT be
    # mo.state: that cell reads reports and WRITES the viewport state, so
    # holding the "last seen" values in reactive state would make it a
    # self-loop. A plain dict is invisible to the dataflow graph, which is
    # exactly right for de-duplication bookkeeping.
    viewer_bus = {"cam_token": None, "vp": None, "sel_seq": None}

    def move_camera(vp):
        """Programmatic camera move.

        Updates ViewportState (navigator, header, agent contract) AND commands
        the OpenSeadragon widget to the matching level-0 rectangle, so the two
        surfaces never disagree about where the user is looking. The bbox is
        derived from ``vp`` itself, so ``jump_viewport_for_bbox`` stays the
        single source of truth for framing decisions.
        """
        set_vp(vp)
        half_w = vp.size[0] * vp.downsample / 2.0
        half_h = vp.size[1] * vp.downsample / 2.0
        set_cam(
            (
                (
                    vp.center[0] - half_w,
                    vp.center[1] - half_h,
                    vp.center[0] + half_w,
                    vp.center[1] + half_h,
                ),
                object(),
            )
        )

    agent_bridge = AgentBridge(OUT_DIR)

    # Module-scope tool for a code agent (marimo AI / pair integration).
    get_latest_selection = make_marimo_tool(lambda: agent_bridge)

    # Click-handler registry: toolbar buttons are built BEFORE the viewer
    # cell exists (so they cannot reference the viewer without a cycle), while
    # the handlers they trigger live in later cells. Later cells register
    # their handlers here; toolbar buttons look them up at click time. This is
    # also what keeps the toolbar cell OUT of the viewport's dependency set:
    # a toolbar that re-ran on every pan would rebuild its radio/checkboxes
    # and silently reset the user's tool choice.
    ui_actions = {}
    return (
        agent_bridge,
        get_ann_version,
        get_cam,
        get_db_msg,
        get_measure_msg,
        get_payload,
        get_rois,
        get_slide_id,
        get_source,
        get_tiles,
        get_vp,
        move_camera,
        set_ann_version,
        set_db_msg,
        set_measure_msg,
        set_payload,
        set_rois,
        set_slide_id,
        set_source,
        set_tiles,
        set_vp,
        ui_actions,
        viewer_bus,
    )


@app.cell(hide_code=True)
def _(mo):
    # Analysis-panel state (SPEC-ML Part C). All channels are (kind, text)
    # message tuples or opaque result dicts; None = nothing to show.
    get_analysis_result, set_analysis_result = mo.state(None)
    get_analysis_msg, set_analysis_msg = mo.state(None)  # (kind, text) | None
    get_hm_result, set_hm_result = mo.state(None)  # {"grid","params","png"}
    get_train_msg, set_train_msg = mo.state(None)  # (kind, text) | None
    get_train_info, set_train_info = mo.state(None)  # ModelInfo dict | None
    get_models_version, set_models_version = mo.state(None)  # refresh token
    # Background heatmap sweep. A plain thread-shared dict, NOT mo.state, for
    # the same two reasons the TCGA download uses one: a synchronous sweep
    # blocks the kernel so NO cell can re-render while it runs (which is why
    # the progress bar written to explain the wait could never appear, and why
    # the "already running" guard could never be true), and mo.state setters
    # are inert when called from a foreign thread. The worker writes here; the
    # ticker cell renders progress and publishes the finished grid to mo.state
    # on the main thread.
    # "slide" is the SlideSource the values currently sitting in "result" and
    # "msg" were measured on. The worker publishes minutes after the click and
    # writes AFTER _open_slide_path's clear, so clearing at slide-OPEN time
    # (R04-3) cannot reach it; the ticker compares this token against the open
    # slide at PUBLISH time instead (R07-1).
    hm_job = {
        "thread": None, "progress": None, "result": None, "msg": None,
        "slide": None,
    }
    hm_ticker = mo.ui.refresh(options=["1s"], default_interval="1s")
    # Background training run, same shape and for the same reasons: feature
    # extraction is ~0.04 s per patch and a realistic annotation set is
    # hundreds of ROIs, so running it inline froze the kernel (measured:
    # 19.7 s for 20 ROIs before this) with no message and no way to re-render.
    train_job = {"thread": None, "result": None, "msg": None}
    # The user's last pick for the two heatmap dropdowns. A PLAIN dict, not
    # mo.state, on purpose: both dropdowns live in cells that get_models_version
    # re-runs, and a reactive channel would add a dependency edge back into
    # them. Written from their on_change, read as their value= (R07-8).
    hm_choice = {"model": None, "metric": "tissue_fraction"}
    return (
        get_analysis_msg,
        get_analysis_result,
        get_hm_result,
        get_models_version,
        get_train_info,
        get_train_msg,
        hm_choice,
        hm_job,
        hm_ticker,
        set_analysis_msg,
        set_analysis_result,
        set_hm_result,
        set_models_version,
        set_train_info,
        set_train_msg,
        train_job,
    )


@app.cell(hide_code=True)
def _(db, get_slide_id, live_selection, make_live_selection_tool):
    # Zero-click live-selection tool for a code agent (marimo-pair). Reports
    # the selection the user just drew on the viewer, in level-0 coordinates —
    # no "Send to code agent" click required. Returns "NO_SELECTION" when
    # nothing is drawn or no slide is open. Companion of get_latest_selection
    # (last submitted ROI).
    #
    # live_selection() picks the surface (OpenSeadragon or the plotly
    # fallback); this cell only wraps it in the tool contract, so there is
    # exactly ONE place that decides which viewer is authoritative.
    get_current_selection = make_live_selection_tool(
        live_selection, lambda: db, get_slide_id
    )
    return


@app.cell(hide_code=True)
def _(
    db,
    get_slide_id,
    get_source,
    make_annotate_roi_tool,
    make_query_annotations_tool,
    make_slide_info_tool,
):
    # DB-backed agent tools (module scope, same contract as
    # get_current_selection: JSON strings / fixed sentinels, never raise).
    # annotate_roi writes a label/notes back to the rois table;
    # query_annotations lists the current slide's annotation rows;
    # get_slide_info reports metadata for the open slide ("NO_SLIDE" if none).
    annotate_roi = make_annotate_roi_tool(lambda: db)
    query_annotations = make_query_annotations_tool(lambda: db, get_slide_id)
    get_slide_info = make_slide_info_tool(lambda: get_source(), lambda: db, get_slide_id)
    return


@app.cell(hide_code=True)
def _(
    OSD_AVAILABLE,
    Path,
    SlideRefs,
    UPLOAD_DIR,
    ViewportState,
    db,
    ensure_demo_slide,
    mo,
    open_slide,
    os,
    serve_slide,
    set_analysis_msg,
    set_analysis_result,
    set_db_msg,
    set_hm_result,
    set_measure_msg,
    set_payload,
    set_rois,
    set_slide_id,
    set_source,
    set_tiles,
    set_vp,
    viewer_bus,
):
    # Sidebar "Open slide" panel. Hardened: every widget is constructed in
    # its own try/except, so a failure renders a callout in place of that
    # widget instead of the whole loader section disappearing.
    def _open_slide_path(p, source_kind="local"):
        src = open_slide(p)
        set_source(src)
        set_measure_msg(None)
        # ...and every MESSAGE about the old slide, not just the data behind
        # it. set_db_msg was the one channel this function did not reset, and
        # it is the channel carrying every success string in the app: "Sent
        # ROI to agent: rect bbox=[...] -- the agent reads it with
        # get_latest_selection()" stood, verbatim and actionable, over the
        # newly opened slide, so an agent that followed the callout analysed
        # slide A's region while the user looked at slide B (R07-4). Cleared
        # BEFORE the tile-server and registration failures below, which write
        # to this same channel about the NEW slide.
        set_db_msg(None)
        # A slide boundary invalidates EVERYTHING derived from the old slide.
        # All of it is level-0 geometry or level-0 pixel statistics, and none
        # of it carries the slide it came from, so left in place it is
        # silently re-attributed to the new one: "Send to code agent" with
        # nothing drawn resubmits slide A's rectangle, persists it as a row of
        # slide B's annotations and reports success; "Analyze current
        # selection" extracts slide A's bbox out of slide B; the navigator
        # blends slide A's metric grid onto slide B's thumbnail (the
        # `except Exception: pass` guard there never fires -- a grid from
        # another slide does not raise, render_heatmap just resizes it); and
        # overlay_rois draws slide A's outlines over slide B.
        set_rois([])
        set_payload(None)
        set_analysis_result(None)
        set_analysis_msg(None)
        set_hm_result(None)
        _w, _h = src.dimensions
        # Bootstrap viewport. With OpenSeadragon this is replaced by the real
        # container geometry as soon as the widget reports back; it still has
        # to be coherent in the meantime (navigator rectangle, header readout).
        set_vp(
            ViewportState(
                center=(_w / 2.0, _h / 2.0),
                downsample=max(src.level_downsamples),
                size=(1024, 768),
            )
        )
        # New slide: forget the previous slide's de-dup bookkeeping, otherwise
        # the first report of the new slide can be mistaken for a repeat.
        viewer_bus["vp"] = None
        viewer_bus["sel_seq"] = None
        # Register the slide with the loopback tile server that feeds
        # OpenSeadragon. refs are fitted ONCE per slide (never per tile): an
        # H/E channel view normalized per tile turns blank background into
        # convincing fake structure. SlideRefs.fit never raises.
        if OSD_AVAILABLE:
            try:
                _info = serve_slide(src, refs=SlideRefs.fit(src))
                # width/height let the widget recognize a same-slide tile-source
                # swap (channel view) and keep the user's position instead of
                # snapping back to the whole-slide view.
                _info["tile_source"] = {
                    **_info["tile_source"],
                    "width": _info["width"],
                    "height": _info["height"],
                }
                set_tiles(_info)
            except Exception as _exc:  # port blocked, source unreadable, ...
                set_tiles(None)
                set_db_msg(
                    (
                        "warn",
                        f"Tile server unavailable ({_exc}); falling back to the "
                        "legacy plotly viewer (cosmetic zoom only).",
                    )
                )
        else:
            set_tiles(None)
        if db.enabled:
            try:
                set_slide_id(
                    db.slide_repo.register(
                        source_kind=source_kind,
                        name=src.name,
                        path=str(p),
                        width=_w,
                        height=_h,
                        mpp=src.mpp,
                    )
                )
            except Exception as _exc:  # DB hiccup: keep viewing, no crash
                set_slide_id(None)
                set_db_msg(("warn", f"Slide registration failed: {_exc}"))
        else:
            set_slide_id(None)

    # The three open actions below are the ONLY click handlers in this file
    # that had no error path. marimo swallows an exception raised inside an
    # on_click callback (it logs to the KERNEL's stderr -- the terminal running
    # `marimo edit`, not the browser -- and returns normally), so a mistyped
    # path or an unreadable upload changed nothing on screen at all: no
    # callout, no message, and the previous slide still rendered. That is
    # byte-identical to successfully re-opening the same slide (R07-5). The
    # comment above says the panel is hardened, and its WIDGETS are; the
    # ACTIONS were not.
    def _on_open_clicked(_):
        _v = getattr(path_input, "value", None)
        if _v:
            try:
                _open_slide_path(_v, source_kind="local")
            except Exception as _exc:
                set_db_msg(("danger", f"Could not open {_v}: {_exc}"))

    def _on_demo_clicked(_):
        try:
            _open_slide_path(str(ensure_demo_slide()), source_kind="local")
        except Exception as _exc:
            set_db_msg(("danger", f"Could not open the demo slide: {_exc}"))

    # Open a slide on startup so the app comes up showing an image instead of
    # an empty frame waiting for a click. ensure_demo_slide() reuses the
    # cached assets/demo_he.png, so after the first run this is a file open,
    # not a 15s synthesis. HESCOPE_NO_AUTO_OPEN=1 opts out.
    #
    # The "already did it" flag lives in viewer_bus, NOT in a get_source()
    # read: this cell builds the loader widgets, so taking a reactive
    # dependency on the slide would rebuild the path box and buttons every
    # time the slide changes.
    if not viewer_bus.get("auto_opened") and os.environ.get(
        "HESCOPE_NO_AUTO_OPEN", ""
    ).strip().lower() not in ("1", "true", "yes", "on"):
        viewer_bus["auto_opened"] = True
        try:
            _open_slide_path(str(ensure_demo_slide()), source_kind="local")
        except Exception:
            pass  # a failed auto-open must never block the loader panel

    def _on_upload(files):
        if files:
            _f = files[0]
            try:
                # Content-derived name in a STABLE directory: the same bytes
                # always land on the same path, so re-uploading one file
                # resolves to the same slide row and its annotations come with
                # it. mkdtemp gave every upload a new identity and put the row
                # on a path the OS is free to delete (R08-1).
                import hashlib as _hashlib

                UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                _digest = _hashlib.sha256(_f.contents).hexdigest()[:12]
                _dest = UPLOAD_DIR / f"{_digest}-{Path(_f.name).name}"
                if not _dest.exists():
                    _dest.write_bytes(_f.contents)
                _open_slide_path(str(_dest), source_kind="upload")
            except Exception as _exc:
                set_db_msg(("danger", f"Could not open {_f.name}: {_exc}"))

    try:
        path_input = mo.ui.text(
            label="Slide path", placeholder="/path/to/slide.png", full_width=True
        )
    except Exception as _exc:
        path_input = mo.callout(
            mo.md(f"**Path input unavailable:** `{_exc}`"), kind="warn"
        )
    try:
        open_button = mo.ui.button(label="Open", on_click=_on_open_clicked)
    except Exception as _exc:
        open_button = mo.callout(
            mo.md(f"**Open button unavailable:** `{_exc}`"), kind="warn"
        )
    try:
        demo_button = mo.ui.button(
            label="Generate & open demo slide", on_click=_on_demo_clicked
        )
    except Exception as _exc:
        demo_button = mo.callout(
            mo.md(f"**Demo slide unavailable:** `{_exc}`"), kind="warn"
        )
    try:
        file_upload = mo.ui.file(
            filetypes=[
                ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svs", ".ndpi", ".mrxs"
            ],
            label="Upload a slide image",
            on_change=_on_upload,
        )
    except Exception as _exc:
        file_upload = mo.callout(
            mo.md(f"**File upload unavailable:** `{_exc}`"), kind="warn"
        )

    loader_panel = mo.vstack(
        [
            mo.md("**Open slide**"),
            mo.hstack([path_input, open_button], justify="start", align="end", gap=0.5),
            demo_button,
            file_upload,
        ]
    )
    open_slide_path = _open_slide_path  # shared with the TCGA panel
    return loader_panel, open_slide_path


@app.cell(hide_code=True)
def _(mo):
    # Sidebar "Display" panel. These affect the DISPLAYED viewer only:
    # selection coordinates, extracted patches, ROI statistics and everything
    # written to the database read UNADJUSTED source pixels
    # (hescope.core.rois.extract_patch -> SlideSource.read_region).
    #
    # brightness / contrast / gamma are continuous sliders, so on the
    # OpenSeadragon surface they are applied as a CSS filter on the tile canvas
    # (instant, no round trip, no tile-cache invalidation). channel view is a
    # true per-pixel colour transform CSS cannot express, so it is baked into
    # the tiles server-side, normalized against a range fitted once per slide.
    brightness_slider = mo.ui.slider(
        start=0.2, stop=3.0, step=0.05, value=1.0,
        label="brightness", show_value=True,
    )
    contrast_slider = mo.ui.slider(
        start=0.2, stop=3.0, step=0.05, value=1.0,
        label="contrast", show_value=True,
    )
    gamma_slider = mo.ui.slider(
        start=0.3, stop=3.0, step=0.05, value=1.0,
        label="gamma", show_value=True,
    )
    channel_dropdown = mo.ui.dropdown(
        options=["rgb", "r", "g", "b", "hematoxylin", "eosin"],
        value="rgb",
        label="channel view",
    )
    overlay_checkbox = mo.ui.checkbox(value=True, label="show ROI overlays")
    # NOTE: the "stain normalize (Macenko)" checkbox was removed here. It was
    # dead-wired — the viewer cell declared the stain helpers but never called
    # them, so the checkbox did nothing at all. It cannot simply be re-wired to
    # the tiled viewer either: hescope.analysis.stain.macenko_normalize refits the
    # SOURCE stain matrix from whatever image it is handed, so a blank
    # background tile normalizes to solid black. Bringing it back needs a
    # pinned source matrix (a `source_reference=` argument on
    # macenko_normalize) plus a canonical H&E target; hescope/tileserver.py
    # already carries the plumbing (DisplayParams.stain_norm, SlideRefs).
    display_panel = mo.vstack(
        [
            mo.md("**Display**"),
            brightness_slider,
            contrast_slider,
            gamma_slider,
            channel_dropdown,
            overlay_checkbox,
        ]
    )
    return (
        brightness_slider,
        channel_dropdown,
        contrast_slider,
        display_panel,
        gamma_slider,
        overlay_checkbox,
    )


@app.cell(hide_code=True)
def _(db, get_ann_version, get_slide_id):
    # Refresh trigger for the annotation browser / overlays / agent runs.
    get_ann_version()
    _sid = get_slide_id()
    db_roi_rows = []
    db_roi_error = None
    if db.enabled and _sid is not None:
        try:
            db_roi_rows = db.roi_repo.for_slide(_sid)
        except Exception as _exc:  # DB read failed: degrade, never crash
            db_roi_rows = []
            db_roi_error = str(_exc)
    return db_roi_error, db_roi_rows


@app.cell(hide_code=True)
def _(annotation_table, db_roi_rows, get_rois, roi_from_db_row):
    # ROIs drawn on the unified viewer: session ROIs first, then the
    # persisted ROIs of the current slide. The row selected in the annotation
    # browser is highlighted via selected_index.
    _session_rois = list(get_rois())
    _db_rois = [roi_from_db_row(_r) for _r in db_roi_rows]
    overlay_rois = _session_rois + _db_rois
    selected_index = None
    _sel = annotation_table.value if annotation_table is not None else None
    if _sel:
        _sel_id = _sel[0].get("id")
        for _i, _r in enumerate(db_roi_rows):
            if _r["id"] == _sel_id:
                selected_index = len(_session_rois) + _i
                break
    return overlay_rois, selected_index


@app.cell(hide_code=True)
def _(
    draw_navigator_markers,
    get_hm_result,
    get_source,
    get_vp,
    grid_coverage,
    hm_nav_checkbox,
    mo,
    navigator_image,
    overlay_checkbox,
    overlay_rois,
    render_heatmap,
    viewport_png_bytes,
):
    # Sidebar "Navigator" panel: small thumbnail (max 200px) with the
    # viewport rectangle and ROI markers. This is the ONLY second image in
    # the app; the main area has exactly one plotly figure. While the
    # Analysis panel's "show heatmap on navigator" checkbox is on and a
    # heatmap grid exists, the thumbnail is heatmap-blended.
    _src = get_source()
    _vp = get_vp()
    if _src is None:
        navigator_panel = mo.vstack(
            [mo.md("**Navigator**"), mo.md("*No slide open.*")]
        )
    else:
        _nav_img = navigator_image(_src, _vp, max_size=200)
        _hm = get_hm_result()
        # "show heatmap on navigator" had TWO silent no-ops: no heatmap
        # computed yet, and a grid that does not fit this slide. Both left the
        # plain thumbnail on screen and said nothing, which is indistinguishable
        # from a dead checkbox -- and is exactly what it was reported as. The
        # tick is a request; if it cannot be honoured, say why, here, next to
        # the image that did not change.
        _hm_note = None
        if hm_nav_checkbox.value:
            if _hm is None:
                _hm_note = (
                    "heatmap requested, but none has been computed yet — "
                    "run one under **Analysis › Heatmap**"
                )
            else:
                try:
                    # coverage: the grid's cell count is rounded UP, so it spans
                    # more than the slide. Without it the overlay is stretched
                    # over the thumbnail and drifts off the tissue it measured.
                    _nav_img = render_heatmap(
                        _nav_img,
                        _hm["grid"],
                        coverage=grid_coverage(
                            _src.dimensions,
                            _hm["grid"].shape,
                            tile=int(_hm["params"]["tile"]),
                            downsample=float(_hm["params"]["downsample"]),
                        ),
                    )
                except Exception as _exc:
                    # Reachable with a grid computed for a DIFFERENT slide.
                    _hm_note = (
                        "heatmap not drawn on the navigator "
                        f"({type(_exc).__name__}: {_exc}) — re-run the sweep "
                        "for this slide"
                    )
        if overlay_checkbox.value and overlay_rois:
            _nav_img = draw_navigator_markers(
                _nav_img, overlay_rois, _src.dimensions
            )
        _nav_parts = [
            mo.md("**Navigator**"),
            mo.image(viewport_png_bytes(_nav_img)),
        ]
        if _hm_note is not None:
            _nav_parts.append(mo.md(f"*{_hm_note}*"))
        navigator_panel = mo.vstack(_nav_parts)
    return (navigator_panel,)


@app.cell(hide_code=True)
def _(
    db_roi_rows,
    dc_replace,
    get_rois,
    get_source,
    get_vp,
    jump_viewport_for_bbox,
    mo,
    move_camera,
    set_db_msg,
    set_rois,
):
    # Sidebar "ROIs" panel: session ROI list with per-row view/delete
    # buttons. View reuses the annotation-browser jump (center on the bbox,
    # zoom so it fills the viewport); only state getters/setters and
    # imported helpers are referenced, so no reactive cycle is introduced.
    #
    # It also has to ACCOUNT FOR the saved ROIs, even though it does not list
    # them. The viewer draws `overlay_rois = session + persisted`, so a slide
    # reopened in a new session shows its saved outlines on the image while
    # this panel -- reading get_rois() alone -- said "No ROIs yet: draw on the
    # viewer". The picture and the panel were describing the same slide from
    # two different stores, and the panel's copy was the false one.
    _rois = get_rois()
    _saved = len(db_roi_rows)

    def _make_view(_idx):
        def _view(_):
            _lst = get_rois()
            if not 0 <= _idx < len(_lst):
                return
            _src = get_source()
            if _src is None:
                set_db_msg(("warn", "Open a slide to view this ROI."))
                return
            _center, _ds = jump_viewport_for_bbox(
                _lst[_idx].bbox(),
                get_vp().size,
                max_downsample=float(max(_src.level_downsamples)),
            )
            move_camera(dc_replace(get_vp(), center=_center, downsample=_ds))
            set_db_msg(("info", f"Viewer centered on ROI [{_idx}]."))

        return _view

    def _make_delete(_idx):
        def _del(_):
            _lst = list(get_rois())
            if 0 <= _idx < len(_lst):
                _gone = _lst.pop(_idx)
                set_rois(_lst)
                set_db_msg(("info", f"Deleted ROI [{_idx}] ({_gone.kind})."))

        return _del

    def _on_clear_rois(_, _n=_saved):
        set_rois([])
        # "All session ROIs cleared" over a viewer still showing every saved
        # outline read as a button that had not worked. Name what stayed.
        _msg = "Session ROIs cleared."
        if _n:
            _msg += f" The {_n} saved ROI(s) of this slide are still on the image."
        set_db_msg(("info", _msg))

    clear_rois_button = mo.ui.button(
        # Labelled for what it actually clears: the session list. The saved
        # ROIs are deleted from the Annotations panel, one row at a time.
        label="Clear session ROIs", on_click=_on_clear_rois
    )
    if _rois:
        roi_view_buttons = [
            mo.ui.button(label="View", on_click=_make_view(_i))
            for _i in range(len(_rois))
        ]
        roi_delete_buttons = [
            mo.ui.button(label="Delete", on_click=_make_delete(_i))
            for _i in range(len(_rois))
        ]
        _rows = [
            mo.hstack(
                [
                    mo.md(f"**[{_i}]** {_r.kind} bbox={_r.bbox()}"),
                    roi_view_buttons[_i],
                    roi_delete_buttons[_i],
                ],
                gap=0.5,
                justify="space-between",
            )
            for _i, _r in enumerate(_rois)
        ]
    else:
        roi_view_buttons = []
        roi_delete_buttons = []
        _rows = [
            mo.md(
                "*No ROIs yet: draw on the viewer, then 'Add ROI' or "
                "'Send to code agent'.*"
            )
            if not _saved
            # The outlines ARE on the image, drawn from the database. Claiming
            # "No ROIs yet" here is contradicted by what the user is looking at.
            else mo.md(
                f"*Nothing drawn this session. **{_saved} saved ROI(s)** for "
                "this slide are already outlined on the image — open the "
                "**Annotations** panel to browse, label or delete them.*"
            )
        ]
    if _rois and _saved:
        _rows.append(
            mo.md(
                f"*Plus **{_saved} saved ROI(s)** from earlier sessions, also "
                "outlined on the image (Annotations panel).*"
            )
        )
    roi_panel = mo.vstack([mo.md("**ROIs**"), *_rows, clear_rois_button])
    return (roi_panel,)


@app.cell(hide_code=True)
def _(display_panel, loader_panel, mo, navigator_panel, roi_panel):
    # Left sidebar: everything session-setup-ish lives here.
    mo.sidebar(
        mo.vstack(
            [
                loader_panel,
                mo.md("---"),
                display_panel,
                mo.md("---"),
                navigator_panel,
                mo.md("---"),
                roi_panel,
            ]
        ),
        width="300px",
    )
    return


@app.cell(hide_code=True)
def _(Path, db_status_badge, get_source, get_vp, magnification_for, mo):
    # Compact header row: app icon + title + slide info + DB status badge.
    # Icon resolves relative to app.py itself (bundled asset, NOT the
    # runtime data dir); _app_dir is cell-private so derive it locally.
    try:
        _here = Path(__file__).resolve().parent
    except NameError:
        # marimo kernel context
        _here = Path.cwd()
    _icon_path = _here / "image" / "Marimo-icon.svg"
    if not _icon_path.exists():
        _icon_path = _here / "image" / "Marimo-icon.png"
    if _icon_path.exists():
        # Rounded 36px brand mark in the app bar. Graceful no-icon fallback
        # when the image/ folder is absent (e.g. pip wheel install).
        _icon = mo.image(
            str(_icon_path), alt="HE-Scope icon", width=36, height=36, rounded=True
        )
    else:
        _icon = mo.md("")
    _src = get_source()
    if _src is None:
        _info = "No slide open"
    else:
        _vp = get_vp()
        _mag = magnification_for(_src.mpp, _vp.downsample)
        _mag_s = (
            f"magnification ~{_mag:.1f}x"
            if _mag is not None
            else f"downsample x{_vp.downsample:g}"
        )
        _mpp_s = (
            f"{_src.mpp} um/px" if _src.mpp is not None else "mpp unknown"
        )
        _w, _h = _src.dimensions
        _info = f"{_src.name} | {_w} x {_h} px | {_mpp_s} | {_mag_s}"
    _header_row = mo.hstack(
        [
            _icon,
            mo.md("**HE-Scope** — H&E viewer + code-agent bridge"),
            mo.md(_info),
            mo.md(f"`{db_status_badge}`"),
            # Static agent status strip (right-aligned via CSS). Tools:
            # get_current_selection, get_latest_selection, get_slide_info,
            # annotate_roi, query_annotations, get_analysis_capabilities.
            mo.Html(
                '<span class="hescope-agent-status">'
                "agent tools: 6 · pair: marimo-pair</span>"
            ),
        ],
        justify="start",
        align="center",
        gap=1.0,
        wrap=True,
    )
    # Styled by the theme CSS cell below (.hescope-app-bar).
    mo.Html(f'<div class="hescope-app-bar">{_header_row.text}</div>')
    return


@app.cell(hide_code=True)
def _(get_source, mo, ui_actions):
    # THE toolbar: every control the user touches mid-session lives in this
    # one compact row (wraps on narrow windows). Muted, flat styling only.
    #
    # THIS CELL MUST NOT REFERENCE get_vp. Every widget here is CONSTRUCTED on
    # each run, and a re-constructed mo.ui element comes back at its default
    # value — so a toolbar inside the viewport's dependency set would reset the
    # user's tool choice on every pan. Its only reactive input is the slide
    # (which legitimately changes the zoom range). Everything that needs the
    # live viewport is a named action in ui_actions, registered by the camera
    # cell below and looked up at click time.
    _src = get_source()
    if _src is not None:
        _max_ds = float(max(_src.level_downsamples))
    else:
        _max_ds = 8.0

    def _fire(name):
        def _handler(value):
            _fn = ui_actions.get(name)
            if _fn is not None:
                _fn(value)

        return _handler

    pan_west = mo.ui.button(label="◀", on_click=_fire("pan_w"))
    pan_east = mo.ui.button(label="▶", on_click=_fire("pan_e"))
    pan_north = mo.ui.button(label="▲", on_click=_fire("pan_n"))
    pan_south = mo.ui.button(label="▼", on_click=_fire("pan_s"))
    pan_cluster = mo.hstack(
        [pan_west, pan_east, pan_north, pan_south], justify="start", gap=0.1
    )

    zoom_fit_button = mo.ui.button(label="Zoom to fit", on_click=_fire("zoom_fit"))
    # A COMMAND, not a readout: the wheel changes the real magnification
    # continuously and this slider is not rebuilt to follow it (rebuilding
    # would reset the rest of the toolbar). The live figure is in the header
    # and in the status line under the viewer.
    zoom_slider = mo.ui.slider(
        start=1.0,
        stop=max(_max_ds, 1.0),
        step=0.5,
        value=max(_max_ds, 1.0),
        label="zoom",
        # The raw downsample is a slide-derived float ("8.001340482573728").
        # The readable form -- magnification, or a rounded downsample -- is in
        # the status line under the viewer, which has the mpp to compute it.
        show_value=False,
        on_change=_fire("zoom"),
    )

    dragmode_radio = mo.ui.radio(
        options={"pan": "pan", "box select": "select", "lasso": "lasso"},
        value="box select",
        label="mouse",
        inline=True,
    )
    measure_checkbox = mo.ui.checkbox(label="measure mode")
    circle_checkbox = mo.ui.checkbox(label="box as circle")

    add_roi_button = mo.ui.button(label="Add ROI", on_click=_fire("add_roi"))
    send_button = mo.ui.button(
        label="Send to code agent", kind="success", on_click=_fire("send")
    )

    _toolbar_row = mo.hstack(
        [
            dragmode_radio,
            zoom_slider,
            zoom_fit_button,
            pan_cluster,
            measure_checkbox,
            circle_checkbox,
            add_roi_button,
            send_button,
        ],
        justify="start",
        align="center",
        wrap=True,
        gap=0.5,
    )
    # Styled by the theme CSS cell below (.hescope-toolbar).
    mo.Html(f'<div class="hescope-toolbar">{_toolbar_row.text}</div>')
    return circle_checkbox, dragmode_radio, measure_checkbox


@app.cell(hide_code=True)
def _(
    ViewportState,
    dc_replace,
    get_source,
    get_vp,
    jump_viewport_for_bbox,
    move_camera,
    set_db_msg,
    ui_actions,
):
    # Camera actions for the toolbar buttons AND for the annotation browser.
    # Kept out of every UI-building cell on purpose (see the comment in the
    # toolbar cell): this cell reads the live viewport, so it re-runs whenever
    # the view moves, and it must therefore build no UI. Referencing the
    # set_db_msg SETTER adds no dependency edge -- only the getter would.
    #
    # Every action below reports its own failure, because marimo swallows an
    # exception raised inside an on_click callback into the kernel's stderr
    # and the click then looks exactly like a click that worked (R07-5).
    def _pan(dx, dy):
        # step = 25% of the viewport, in level-0 coordinates
        _vp = get_vp()
        _sx = _vp.size[0] * 0.25 * _vp.downsample
        _sy = _vp.size[1] * 0.25 * _vp.downsample
        move_camera(
            dc_replace(
                _vp, center=(_vp.center[0] + dx * _sx, _vp.center[1] + dy * _sy)
            )
        )

    def _on_zoom_fit(_):
        _s = get_source()
        if _s is None:
            return
        try:
            _w, _h = _s.dimensions
            move_camera(
                ViewportState(
                    center=(_w / 2.0, _h / 2.0),
                    downsample=max(_s.level_downsamples),
                    size=get_vp().size,
                )
            )
        except Exception as _exc:
            set_db_msg(("danger", f"Zoom to fit failed: {_exc}"))

    def _on_zoom(v):
        if get_source() is None:
            return
        try:
            move_camera(dc_replace(get_vp(), downsample=max(float(v), 1.0)))
        except Exception as _exc:
            set_db_msg(("danger", f"Zoom failed: {_exc}"))

    def _on_jump_bbox(bbox):
        """Center the viewer on a level-0 bbox (annotation click-to-jump).

        This handler lives HERE, with the other camera actions, for the same
        reason they do: reading the live viewport is exactly what puts a cell
        inside get_vp's dependency set. It used to live in the annotation
        browser, which therefore re-ran on every pan and zoom -- rebuilding
        mo.ui.table and, through it, the label/notes boxes. A re-constructed
        mo.ui element comes back at its DEFAULT value by design (marimo stamps
        every element with a fresh token so a re-run resets it), so a mouse
        drag destroyed the row selection, the ROI highlight and any half-typed
        label. The Annotations panel is the one panel holding user-TYPED data;
        it must depend on slide identity and the ROI rows, nothing else.
        """
        _s = get_source()
        if _s is None or not bbox:
            return
        try:
            _center, _ds = jump_viewport_for_bbox(
                bbox,
                get_vp().size,
                max_downsample=float(max(_s.level_downsamples)),
            )
            # move_camera, not set_vp: set_vp only updates ViewportState
            # (navigator/header), leaving the OpenSeadragon surface -- the
            # default one, the one actually showing the slide -- exactly where
            # it was.
            move_camera(dc_replace(get_vp(), center=_center, downsample=_ds))
        except Exception as _exc:
            set_db_msg(("danger", f"Could not jump to {bbox}: {_exc}"))

    # `fn=_pan` binds the function OBJECT now. A bare `lambda _: _pan(...)`
    # defers the lookup to click time, and by then marimo's cell-private name
    # mangling (_pan -> _cell_<id>_pan) has taken it out of scope: every arrow
    # button raised `NameError: name '_cell_ROlb_pan' is not defined`. The
    # entries below are safe because they store the function object directly
    # rather than a lambda that names it.
    ui_actions["pan_w"] = lambda _v, fn=_pan: fn(-1, 0)
    ui_actions["pan_e"] = lambda _v, fn=_pan: fn(1, 0)
    ui_actions["pan_n"] = lambda _v, fn=_pan: fn(0, -1)
    ui_actions["pan_s"] = lambda _v, fn=_pan: fn(0, 1)
    ui_actions["zoom_fit"] = _on_zoom_fit
    ui_actions["zoom"] = _on_zoom
    ui_actions["jump_bbox"] = _on_jump_bbox
    return


@app.cell(hide_code=True)
def _(OSD_AVAILABLE, get_source, get_tiles, make_viewer, mo, os):
    # THE viewer: an OpenSeadragon surface fed by the loopback tile server.
    # Real wheel-zoom, real drag-pan, real detail — the browser fetches 256px
    # JPEG tiles straight from the slide pyramid instead of receiving one flat
    # re-rendered bitmap per move.
    #
    # *** THIS CELL'S DEPENDENCY SET IS LOAD-BEARING. ***
    # It depends on SLIDE IDENTITY ONLY. Every other control (tool, overlay,
    # brightness/contrast/gamma, channel view, ROI list, camera commands) is a
    # trait SET on the already-running widget by the cells below. Re-running
    # this cell destroys the widget and its comm — silently, with no exception
    # — so any control that leaked into this signature would present as "my
    # zoom keeps resetting" or "the widget stopped reporting", diagnosed
    # nowhere near the cause.
    # For the same reason this cell must NEVER read osd_viewer.value.
    def _viewer_height() -> int:
        try:
            _h = int(os.environ.get("HESCOPE_VIEWER_HEIGHT", "") or 820)
        except ValueError:
            _h = 820
        return max(400, min(2000, _h))

    _src = get_source()
    _tiles = get_tiles()
    osd_viewer = None
    # Renders nothing when OpenSeadragon is not driving: the legacy cell below
    # owns the viewer then. This cell must never be the reason the page shows
    # an empty viewing area -- every branch that suppresses the fallback has
    # to put something actionable here instead.
    _view = mo.md("")
    if _src is None and not OSD_AVAILABLE:
        _view = mo.md("")  # the legacy cell already prompts to open a slide
    elif _src is None:
        _view = mo.callout(
            mo.md("Open a slide from the sidebar to begin."), kind="info"
        )
    elif _tiles is not None:
        try:
            osd_viewer = mo.ui.anywidget(
                # Taller than the 640 default: whole-slide scans are usually
                # portrait, so with a full-width viewport the fit-to-window
                # zoom is bound by HEIGHT and the extra width just becomes
                # letterboxing. Height is what makes the slide bigger.
                # HESCOPE_VIEWER_HEIGHT overrides it (clamped to 400..2000).
                make_viewer(_src, _tiles["tile_source"], height=_viewer_height())
            )
            # The escape hatch travels WITH the widget. A widget that fails to
            # mount in the browser raises nothing in Python, so this text is
            # the only thing the user would see in that case -- without it the
            # symptom is a dead rectangle and no way to know what to do.
            _view = mo.vstack(
                [
                    osd_viewer,
                    mo.md(
                        "<sub>OpenSeadragon viewer (experimental). If this area "
                        "is blank or does not respond to the mouse, restart "
                        "without `HESCOPE_ENABLE_OSD` to return to the classic "
                        "viewer.</sub>"
                    ),
                ]
            )
        except Exception as _exc:  # broken anywidget install, comm failure
            osd_viewer = None
            _view = mo.callout(
                mo.md(
                    f"**OpenSeadragon viewer could not start:** `{_exc}`. "
                    "Restart without `HESCOPE_ENABLE_OSD` to use the classic "
                    "viewer."
                ),
                kind="danger",
            )
    elif OSD_AVAILABLE:
        # Enabled, slide open, but no tile source: the tile server refused to
        # start. The legacy cell renders in this state (it keys off tiles being
        # None), so say why rather than leaving an unexplained downgrade.
        _view = mo.callout(
            mo.md(
                "**Tile server unavailable**, so the classic viewer is shown "
                "below. Its wheel-zoom and drag are cosmetic — use the toolbar "
                "zoom and pan controls to move the data window."
            ),
            kind="warn",
        )
    _view
    return (osd_viewer,)


@app.cell(hide_code=True)
def _(
    apply_display_pipeline,
    brightness_slider,
    channel_dropdown,
    contrast_slider,
    dragmode_radio,
    draw_scale_bar,
    gamma_slider,
    get_source,
    get_tiles,
    get_vp,
    make_roi_figure,
    mo,
    overlay_checkbox,
    overlay_rois,
    render_viewport,
    selected_index,
):
    # LEGACY FALLBACK viewer, used only when the OpenSeadragon path is
    # unavailable (no anywidget, missing vendored bundle, HESCOPE_DISABLE_OSD,
    # or the tile server refused to start). One plotly figure showing the
    # server-rendered viewport; its zoom/pan are cosmetic — moving the data
    # window still means changing ViewportState through the toolbar.
    #
    # roi_plot stays bound at module scope in BOTH branches (None when
    # OpenSeadragon is driving), because AGENTS.md documents it as a kernel
    # global: an agent poking the old name should get None, not NameError.
    _src = get_source()
    if _src is None or get_tiles() is not None:
        roi_plot = None
        fallback_view = mo.md("")
    else:
        _vp = get_vp()
        _viewport_img = render_viewport(_src, _vp)  # unadjusted capture base
        _display_img = apply_display_pipeline(
            _viewport_img,
            _vp,
            brightness=float(brightness_slider.value or 1.0),
            contrast=float(contrast_slider.value or 1.0),
            gamma=float(gamma_slider.value or 1.0),
            channel=channel_dropdown.value or "rgb",
            rois=overlay_rois,
            show_overlays=bool(overlay_checkbox.value),
            selected_index=selected_index,
        )
        # Scale bar (bottom-right) whenever the slide reports a physical
        # pixel size; uses the level-0 mpp scaled by the current downsample.
        if _src.mpp is not None:
            _display_img = draw_scale_bar(_display_img, _src.mpp, _vp.downsample)
        _uirev = (
            f"vp-{_vp.center[0]:.0f}-{_vp.center[1]:.0f}-{_vp.downsample:g}"
        )
        # No overscan here on purpose: render_viewport_overscan would let the
        # cosmetic drag reveal real pixels, but draw_scale_bar positions the
        # bar relative to the image's own bottom-right corner, so on an
        # overscan frame it lands outside the visible viewport.
        _fig = make_roi_figure(
            _display_img,
            dragmode=dragmode_radio.value or "select",
            uirevision=_uirev,
        )
        roi_plot = mo.ui.plotly(_fig, config=getattr(_fig, "_config", None))
        fallback_view = roi_plot
    fallback_view
    return (roi_plot,)


@app.cell(hide_code=True)
def _(
    DisplayParams,
    brightness_slider,
    channel_dropdown,
    circle_checkbox,
    contrast_slider,
    dragmode_radio,
    ensure_server,
    gamma_slider,
    get_tiles,
    measure_checkbox,
    osd_viewer,
    overlay_checkbox,
    overlay_rois,
    rois_to_payload,
    selected_index,
):
    # Python -> widget COMMANDS. Every one of these is a trait set on the
    # already-running viewer, never a rebuild, so changing a control keeps the
    # user exactly where they were looking. Setting a trait to its current
    # value is a no-op in traitlets, so this cell re-running (it does, on every
    # viewport report) sends nothing over the wire.
    if osd_viewer is not None:
        # Tool: the widget owns the pointer, so the plotly dragmode vocabulary
        # is translated here rather than leaking into the toolbar.
        if measure_checkbox.value:
            _tool = "measure"
        elif (dragmode_radio.value or "select") == "pan":
            _tool = "pan"
        elif dragmode_radio.value == "lasso":
            _tool = "lasso"
        elif circle_checkbox.value:
            _tool = "circle"  # draws an ellipse preview, still EMITS a rect
        else:
            _tool = "rect"
        osd_viewer.tool = _tool

        # Continuous sliders -> CSS filter on the tile canvas (instant, no
        # round trip, browser tile cache untouched). The SVG ROI overlay is a
        # sibling of that canvas, so outlines and the scale bar keep their
        # true colours.
        osd_viewer.display = {
            "brightness": float(brightness_slider.value or 1.0),
            "contrast": float(contrast_slider.value or 1.0),
            "gamma": float(gamma_slider.value or 1.0),
        }

        # ROI outlines live in RAW LEVEL-0 COORDINATES in an SVG overlay, so
        # they stay 2px crisp at 40x and cost no server round trip.
        osd_viewer.rois = rois_to_payload(overlay_rois, selected_index)
        osd_viewer.overlay_visible = bool(overlay_checkbox.value)

        # Channel view is a per-pixel colour transform CSS cannot express, so
        # it is baked into the tiles: a new tile source for the same slide.
        # The widget re-opens preserving the viewport.
        _tiles = get_tiles() or {}
        _key = _tiles.get("key")
        if _key:
            try:
                _srv = ensure_server()
                if _srv.get(_key) is not None:
                    _ts = dict(
                        _srv.tile_source_dict(
                            _key,
                            display=DisplayParams(
                                channel=channel_dropdown.value or "rgb"
                            ),
                        )
                    )
                    _ts["width"] = _tiles.get("width")
                    _ts["height"] = _tiles.get("height")
                    osd_viewer.tile_source = _ts
            except Exception:
                pass  # keep the current tiles rather than blanking the viewer
    return


@app.cell(hide_code=True)
def _(
    format_measurement,
    get_source,
    measure_box,
    osd_viewer,
    parse_osd_measure,
    set_measure_msg,
    set_vp,
    viewer_bus,
    viewport_changed,
    viewport_state_from_report,
):
    # Widget -> Python REPORTS. This is what keeps ViewportState in sync with
    # what the user is actually looking at, so the navigator rectangle, the
    # magnification readout, jump_viewport_for_bbox and the agent contract's
    # viewport_downsample all stay meaningful after a mouse gesture.
    #
    # It deliberately does NOT read get_vp: writing the state it also read
    # would be a reactive self-loop. The "last applied" values live in the
    # plain viewer_bus dict, which the dataflow graph cannot see.
    if osd_viewer is not None:
        _val = osd_viewer.value or {}
        _src = get_source()
        if _src is not None:
            _new_vp = viewport_state_from_report(
                _val.get("viewport"), _src.dimensions
            )
            if _new_vp is not None and viewport_changed(viewer_bus["vp"], _new_vp):
                viewer_bus["vp"] = _new_vp
                set_vp(_new_vp)
            # Measure mode: the widget emits kind="measure", which
            # parse_osd_selection refuses on purpose (a measurement is a UI
            # readout and must never reach the agent contract as an ROI).
            _sel = _val.get("selection") or {}
            _seq = _sel.get("seq")
            if _seq is not None and _seq != viewer_bus["sel_seq"]:
                viewer_bus["sel_seq"] = _seq
                _pts = parse_osd_measure(_sel)
                if _pts is not None:
                    set_measure_msg(
                        (
                            "info",
                            format_measurement(
                                measure_box(_pts[0], _pts[1], _src.mpp)
                            ),
                        )
                    )
    return


@app.cell(hide_code=True)
def _(get_cam, osd_viewer, viewer_bus):
    # Programmatic camera moves (pan buttons, zoom slider, zoom-to-fit, "View"
    # on a session ROI, annotation click-to-jump) reach the widget here.
    #
    # The token guard is required, not defensive: this cell also re-runs on
    # every viewport report, and re-issuing the last goto each time would drag
    # the view back and fight the user's mouse.
    _cmd = get_cam()
    if osd_viewer is not None and _cmd is not None:
        _bbox, _token = _cmd
        if _token is not viewer_bus["cam_token"]:
            viewer_bus["cam_token"] = _token
            try:
                osd_viewer.goto(_bbox)
            except Exception:
                pass  # a camera command must never break the notebook
    return


@app.cell(hide_code=True)
def _(
    current_selection,
    get_source,
    get_vp,
    osd_current_selection,
    osd_viewer,
    parse_osd_measure,
    raw_osd_selection,
    raw_plotly_selection,
    roi_plot,
):
    # THE ONE PLACE that decides which viewing surface is authoritative.
    #
    # The two paths are NOT interchangeable and mixing them is silent:
    # OpenSeadragon reports LEVEL-0 coordinates already, while a plotly
    # selection is in viewport pixels and must go through viewport_transform.
    # Feeding one to the other's converter produces plausible-looking bboxes
    # that pass every shape check and land in the database. Hence: one branch,
    # one helper each, and nothing else in the notebook re-derives this.
    def live_selection():
        _src = get_source()
        if _src is None:
            return None
        if osd_viewer is not None:
            return osd_current_selection(_src, get_vp(), osd_viewer.value)
        if roi_plot is not None:
            # raw_plotly_selection reads the private _selection_data attr: for
            # an image-only figure .value is the (empty) selected-points list,
            # not the selection dict (marimo 0.23).
            return current_selection(
                _src, get_vp(), raw_plotly_selection(roi_plot)
            )
        return None

    def live_measure():
        """Level-0 corners of what the user wants MEASURED, or None.

        The companion to live_selection(), and here for the same reason: the
        measure vocabulary is surface-specific and a handler that cannot see
        it reasons about a surface it does not understand. OpenSeadragon
        reports a measure drag as kind="measure", which parse_osd_selection
        refuses ON PURPOSE (a measurement is a UI readout and must never reach
        the agent contract as an ROI) -- so live_selection() is None during a
        measurement, and "Add ROI" in measure mode used to answer a real
        measurement with "No selection: drag a box ... first", overwriting the
        readout the widget had just published into that same channel.

        The plotly surface has no measure tool at all; there a box selection
        IS the measurement. The rect fallback covers it, and also covers a
        rect drawn on the OSD surface before measure mode was switched on --
        the widget does not clear `selection` when the tool changes.
        """
        if osd_viewer is not None:
            _pts = parse_osd_measure(raw_osd_selection(osd_viewer.value))
            if _pts is not None:
                return _pts
        _sel = live_selection()
        if _sel is not None and _sel["kind"] == "rect":
            _p = _sel["points_level0"]
            if len(_p) >= 2:
                return (
                    (float(_p[0][0]), float(_p[0][1])),
                    (float(_p[1][0]), float(_p[1][1])),
                )
        return None

    return live_measure, live_selection


@app.cell(hide_code=True)
def _(
    db,
    db_roi_rows,
    db_status_detail,
    get_db_msg,
    get_measure_msg,
    get_rois,
    get_source,
    get_vp,
    live_selection,
    mo,
    viewport_status_line,
):
    # Status line under the viewer: viewport readout + measurement / DB
    # message callouts. The viewport-readout TEXT itself (selection state,
    # ROI count, magnification -- see hescope/viewer/viewer.py for the R07-2 /
    # R08-2 / R09-1 / R09-2 history behind that string) is built by
    # hescope.viewer.viewer.viewport_status_line, moved there (design doc
    # §9.2 extraction) so it sits inside hescope's own test suite
    # (tests/test_viewport_status_line.py) instead of only being reachable by
    # executing this cell's body (tests/test_status_line_counts.py still does
    # that, now asserting the hand-off). This cell keeps everything that
    # reads LIVE marimo state (get_source / get_vp / live_selection /
    # get_rois) and every mo.md / mo.callout / mo.vstack call.
    _parts = []
    _src = get_source()
    if _src is not None:
        _vp = get_vp()
        try:
            _sel = live_selection()
        except Exception:  # a status line must never be the thing that breaks
            _sel = None
        _parts.append(
            mo.md(viewport_status_line(_src, _vp, _sel, db_roi_rows, get_rois()))
        )
    _mm = get_measure_msg()
    if _mm is not None:
        _parts.append(mo.callout(mo.md(f"**Measurement:** {_mm[1]}"), kind=_mm[0]))
    _dm = get_db_msg()
    if _dm is not None:
        _kind = _dm[0] if _dm[0] in ("info", "warn", "success", "danger") else "info"
        _parts.append(mo.callout(mo.md(_dm[1]), kind=_kind))
    if not db.enabled and db_status_detail is not None:
        _parts.append(db_status_detail)
    if not _parts:
        _parts.append(mo.md(""))
    mo.vstack(_parts)
    return


@app.cell(hide_code=True)
def _(
    ROI,
    circle_checkbox,
    db,
    format_measurement,
    get_rois,
    get_slide_id,
    get_source,
    live_measure,
    live_selection,
    measure_box,
    measure_checkbox,
    set_ann_version,
    set_measure_msg,
    set_rois,
    ui_actions,
):
    # "Add ROI" action for the toolbar button (registered by name; the
    # button itself lives in the toolbar cell above the viewer).
    #
    # Goes through live_selection(), the one place that knows which surface is
    # authoritative. It used to read roi_plot directly, which is None whenever
    # OpenSeadragon is driving -- so this handler returned immediately and the
    # button did nothing at all, silently. The agent tool kept working
    # throughout, because it already went through live_selection(); that gap
    # between "the agent can read my selection" and "the UI ignores it" is
    # exactly what a second selection path buys you.
    def _add_roi_or_measure():
        # Measure mode FIRST, through live_measure(): asking live_selection()
        # here is blind to the OpenSeadragon measure vocabulary (kind
        # "measure" is refused on purpose), so the None branch below fired on
        # a real measurement and wrote "No selection ..." over the readout the
        # widget had just published -- the warning and the measurement share
        # this one set_measure_msg channel.
        if measure_checkbox.value:
            # Measure mode: a box becomes a measurement, never an ROI.
            _mpts = live_measure()
            if _mpts is None:
                set_measure_msg(
                    (
                        "warn",
                        "Measure mode: drag a BOX on the viewer to measure; "
                        "lasso/circle selections are not measured.",
                    )
                )
                return
            _src = get_source()
            _mpp = _src.mpp if _src is not None else None
            set_measure_msg(
                ("info", format_measurement(measure_box(_mpts[0], _mpts[1], _mpp)))
            )
            return
        # level-0 coordinates on both surfaces (contract dict), so nothing
        # here needs viewport_transform.
        _sel = live_selection()
        if _sel is None:
            set_measure_msg(
                ("warn", "No selection: drag a box or lasso on the viewer first.")
            )
            return
        _kind = _sel["kind"]
        _pts = tuple(tuple(float(c) for c in p) for p in _sel["points_level0"])
        if circle_checkbox.value and _kind == "rect" and len(_pts) == 2:
            # Same inscribed-circle geometry as selection_to_roi(as_circle=True),
            # applied in level-0 space: centre plus a point on the edge.
            (_x0, _y0), (_x1, _y1) = _pts
            _cx, _cy = (_x0 + _x1) / 2.0, (_y0 + _y1) / 2.0
            _r = min(abs(_x1 - _x0), abs(_y1 - _y0)) / 2.0
            _roi = ROI(kind="circle", points=((_cx, _cy), (_cx + _r, _cy)))
        else:
            _roi = ROI(kind=_kind, points=_pts)
        # The DATABASE is the owner when there is one (R08-2). This used to
        # write the session list only, so an ROI "added" here was absent from
        # the Statistics panel, from all three exports and from the annotation
        # editor -- every one of which reads the rois table -- and was gone at
        # the next restart. The button that actually saved was the one labelled
        # "Send to code agent". The session list remains the store in DB-free
        # mode, where db.enabled is False and there is nowhere else to put it.
        _sid = get_slide_id()
        if db.enabled and _sid is not None:
            try:
                _rid = db.roi_repo.add(_sid, _roi)
            except Exception as _exc:
                set_measure_msg(("danger", f"Could not save ROI: {_exc}"))
                return
            # An OPAQUE token, like every other writer of this state -- it is
            # `mo.state(None)` and the six other call sites all pass object().
            # `get_ann_version() + 1` invented an integer contract for it and
            # raised TypeError on the None it starts as, AFTER the row had
            # already been written: the ROI was saved and the strip said
            # "Add ROI failed".
            set_ann_version(object())  # refresh the panels
            set_measure_msg(("success", f"Saved ROI {_rid} to this slide."))
            return
        set_rois(get_rois() + [_roi])
        set_measure_msg(None)

    # `_run=_add_roi_or_measure` is a DEFAULT ARGUMENT on purpose, and the one
    # detail that makes this button work. marimo renames a cell-private name
    # (`_add_roi_or_measure` -> `_cell_ZBYS_add_roi_or_measure`) and then drops
    # it when the cell finishes; a handler that merely NAMES it in its body is
    # resolved on click, long after that, and raises NameError -- which is what
    # "Add ROI failed: name '_cell_ZBYS_add_roi_or_measure' is not defined" was.
    # A default is evaluated at def time, so the function object itself is
    # captured while it still exists. Same defect that killed the arrow buttons
    # (`lambda _v, fn=_pan: ...`), one shape further out.
    def _on_add_roi(_, _run=_add_roi_or_measure):
        try:
            _run()
        except Exception as _exc:  # marimo swallows this otherwise (R07-5)
            set_measure_msg(("danger", f"Add ROI failed: {_exc}"))

    ui_actions["add_roi"] = _on_add_roi
    return


@app.cell(hide_code=True)
def _(
    AgentBridge,
    OUT_DIR,
    ROI,
    agent_bridge,
    db,
    get_payload,
    get_rois,
    get_slide_id,
    get_source,
    get_vp,
    live_selection,
    magnification_for,
    mo,
    set_ann_version,
    set_db_msg,
    set_payload,
    set_rois,
    ui_actions,
):
    # "Send to code agent" action for the toolbar button + the payload view
    # shown in the Agent console accordion.
    def _on_send(_):
        _src = get_source()
        if _src is None:
            set_db_msg(("warn", "Open a slide first (sidebar → Open slide)."))
            return
        _rois = get_rois()
        if not _rois:
            # Common case: the user dragged a box/lasso on the viewer but
            # never clicked "Add ROI" — fall back to the LIVE selection.
            # Through live_selection(), so this works on whichever surface is
            # driving; reading roi_plot here made it a no-op under
            # OpenSeadragon, reporting "nothing to send" over a real selection.
            _sel = live_selection()
            if _sel is None:
                set_db_msg(
                    (
                        "warn",
                        "Nothing to send: drag a box or lasso on the viewer "
                        "first (or click 'Add ROI').",
                    )
                )
                return
            _roi = ROI(
                kind=_sel["kind"],
                points=tuple(
                    tuple(float(c) for c in p) for p in _sel["points_level0"]
                ),
            )
            set_rois(get_rois() + [_roi])
            _rois = get_rois()
        _mag = magnification_for(_src.mpp, get_vp().downsample)
        _sid = get_slide_id()
        # DB mode: persist the ROI row through the bridge; DB-free mode:
        # phase-1 behavior (jsonl history only).
        if db.enabled and _sid is not None:
            _bridge = AgentBridge(OUT_DIR, repository=db.roi_repo, slide_id=_sid)
        else:
            _bridge = agent_bridge
        try:
            _payload = _bridge.submit(_src, _rois[-1], magnification=_mag)
        except Exception as _exc:  # never crash the notebook
            set_db_msg(("danger", f"Submit failed: {_exc}"))
            return
        set_payload(_payload)
        set_db_msg(
            (
                "success",
                f"Sent ROI to agent: {_payload.roi['kind']} "
                f"bbox={_payload.roi['bbox_level0']} — the agent reads it "
                "with get_latest_selection().",
            )
        )
        # Interaction trace for the HUMAN submit. Recorded whenever the DB is
        # up, including the DB-free-ROI case (roi_id None), because the event
        # being traced is the click, not the row.
        db.trace(
            "roi_submit",
            payload={
                "actor": "human",
                "kind": _payload.roi["kind"],
                "bbox_level0": _payload.roi["bbox_level0"],
                "magnification": _payload.magnification,
            },
            slide_id=_sid,
            roi_id=_payload.roi_id,
        )
        if db.enabled and _payload.roi_id is not None:
            set_ann_version(object())  # refresh annotation browser
            try:
                db.run_repo.record(
                    tool="roi_submit",
                    input={
                        "slide_name": _payload.slide_name,
                        "kind": _payload.roi["kind"],
                        "bbox_level0": _payload.roi["bbox_level0"],
                        "mpp": _payload.mpp,
                        "magnification": _payload.magnification,
                        "patch_path": _payload.patch_path,
                    },
                    output_text=_payload.to_agent_prompt(),
                    roi_id=_payload.roi_id,
                )
            except Exception as _exc:
                set_db_msg(("warn", f"Agent run recording failed: {_exc}"))

    ui_actions["send"] = _on_send

    _tools_md = mo.md(
        "Agent tools (module scope): `get_current_selection()` — live, "
        "zero-click box/lasso selection mapped to level-0 coordinates; "
        "`get_latest_selection()` — last submitted ROI payload. Both return "
        "the exact string `NO_SELECTION` when nothing is available. "
        "`get_slide_info()` — JSON metadata of the open slide "
        "(name/dimensions/mpp/levels/DB id/annotation count), or the exact "
        "string `NO_SLIDE`. `annotate_roi(roi_id, label=None, notes=None)` — "
        "write a label/notes back to the rois table; returns the updated row "
        "JSON (or an `{\"error\": ...}` object in DB-free mode / unknown "
        "roi_id). `query_annotations(label=None, limit=50)` — JSON list of "
        "the current slide's annotation rows, optionally label-filtered. "
        "`get_analysis_capabilities()` — JSON of available analyses "
        "(nuclei/QC/stain-norm/heatmaps/training), torch availability and "
        "trained models; never raises."
    )
    _payload = get_payload()
    if _payload is None:
        _payload_view = mo.md(
            "No selection sent yet. A code agent can call "
            "`get_latest_selection()` to fetch the latest ROI payload JSON."
        )
    else:
        _payload_view = mo.vstack(
            [
                mo.md("```\n" + _payload.to_agent_prompt() + "\n```"),
                mo.accordion(
                    {"raw payload JSON": mo.md("```json\n" + _payload.to_json() + "\n```")}
                ),
            ]
        )
    agent_payload_view = mo.vstack([_tools_md, _payload_view])
    return (agent_payload_view,)


@app.cell(hide_code=True)
def _(agent_bridge, get_payload, json, mo):
    get_payload()  # refresh history when a new payload is submitted
    _hist = agent_bridge.history()
    if _hist:
        _rows = [
            {
                "created_at": _p.created_at,
                "kind": _p.roi["kind"],
                "bbox_level0": json.dumps(_p.roi["bbox_level0"]),
                "patch": _p.patch_path,
            }
            for _p in _hist
        ]
        _hist_view = mo.ui.table(_rows, label="ROI history")
    else:
        _hist_view = mo.md("*History is empty.*")
    history_view = mo.vstack([mo.md("### Submission history"), _hist_view])
    return (history_view,)


@app.cell(hide_code=True)
def _(db, get_ann_version, get_payload, mo):
    get_payload()  # refresh after each submission
    get_ann_version()
    if not db.enabled:
        _runs_view = mo.callout(
            mo.md("Database disabled: agent runs are not recorded."),
            kind="warn",
        )
    else:
        try:
            _runs = db.run_repo.recent(20)
            if _runs:
                _run_rows = [
                    {
                        "tool": _r["tool"],
                        "status": _r["status"],
                        "roi_id": _r["roi_id"],
                        "model": _r["model"],
                        "created_at": _r["created_at"],
                        "output": (_r["output_text"] or "")[:80],
                    }
                    for _r in _runs
                ]
                _runs_view = mo.ui.table(_run_rows, label="Agent runs")
            else:
                _runs_view = mo.md("*No agent runs recorded yet.*")
        except Exception as _exc:  # DB read failed: degrade, never crash
            _runs_view = mo.callout(
                mo.md(f"Could not load agent runs: {_exc}"), kind="danger"
            )
    runs_view = mo.vstack([mo.md("### Agent runs"), _runs_view])
    return (runs_view,)


@app.cell(hide_code=True)
def _(db, db_roi_error, db_roi_rows, get_slide_id, mo, ui_actions):
    # Annotation browser (inside the Annotations accordion). Selecting a row
    # jumps the unified viewer: center on the ROI bbox, zoom so it fills
    # ~80% of the viewport (clamped to the valid downsample range).
    #
    # *** THIS CELL MUST NOT REFERENCE get_vp. *** It builds mo.ui.table, and
    # the annotation editor below builds the label/notes boxes off its value,
    # so a re-run wipes the row selection, the ROI highlight and any typed
    # text. The jump needs the live viewport, so it is a NAMED ACTION in
    # ui_actions (registered by the camera cell, looked up at click time) --
    # the same arrangement the toolbar uses for exactly the same reason.
    if not db.enabled:
        annotation_table = None
        ann_browser_view = mo.callout(
            mo.md(
                "Database disabled: annotations are not persisted. ROIs "
                "captured this session still work with the agent bridge "
                "(jsonl history)."
            ),
            kind="warn",
        )
    else:
        if db_roi_error is not None:
            _err_view = mo.callout(
                mo.md(f"Could not load annotations: {db_roi_error}"),
                kind="danger",
            )
        else:
            _err_view = mo.md("")

        # The table shows the bbox as text (a list renders badly in a cell),
        # so the NUMERIC bbox has to be carried separately and looked up by
        # row id. Reading rows[0]["bbox"] back out of the table handed
        # jump_viewport_for_bbox the string "[x0, y0, x1, y1]", which it
        # unpacks character by character -> ValueError, i.e. clicking a row
        # never jumped anywhere.
        # Built through a factory so the lookup arrives as a FUNCTION
        # PARAMETER: a cell-private name (`_bbox_by_id`) referenced from a
        # handler body is mangled and discarded when the cell ends, and the
        # handler would die at click time. Same shape as the ROI panel's
        # `_make_view(_idx)`.
        def _make_row_jump(bbox_by_id):
            def _on_row_selected(rows):
                if not rows:
                    return
                try:
                    bbox = bbox_by_id.get(int(rows[0].get("id")))
                except (TypeError, ValueError):
                    return
                if not bbox:
                    return
                # Looked up at CLICK time, like every toolbar button:
                # `ui_actions` is a notebook global (no leading underscore),
                # so it survives marimo's cell-private name mangling, and the
                # handler that reads the live viewport stays in the camera
                # cell where a viewport-triggered re-run costs nothing.
                jump = ui_actions.get("jump_bbox")
                if jump is not None:
                    jump(bbox)

            return _on_row_selected

        _on_row_selected = _make_row_jump(
            {int(_r["id"]): [float(_v) for _v in _r["bbox"]] for _r in db_roi_rows}
        )

        _table_rows = [
            {
                "id": _r["id"],
                "kind": _r["kind"],
                "label": _r["label"],
                "bbox": str(_r["bbox"]),
                "created_at": _r["created_at"],
            }
            for _r in db_roi_rows
        ]
        if _table_rows:
            annotation_table = mo.ui.table(
                _table_rows,
                selection="single",
                on_change=_on_row_selected,
                label="Saved ROIs (select a row to jump the viewer)",
            )
            _ann_view = annotation_table
        else:
            annotation_table = None
            _hint = (
                "No saved annotations yet. Draw an ROI and use "
                "'Send to code agent' to persist it."
                if get_slide_id() is not None
                else "Open a slide to see its saved annotations."
            )
            _ann_view = mo.md(f"*{_hint}*")
        ann_browser_view = mo.vstack([_err_view, _ann_view])
    return ann_browser_view, annotation_table


@app.cell(hide_code=True)
def _(
    annotation_table,
    db,
    db_roi_rows,
    export_rois,
    get_slide_id,
    get_source,
    import_annotations,
    mo,
    parse_asap_xml,
    parse_geojson_annotations,
    roi_stats_rows,
    rows_to_csv,
    set_ann_version,
    set_db_msg,
    slide_geojson_text,
):
    # Annotation editor + export (inside the Annotations accordion).
    if not db.enabled:
        ann_edit_view = mo.callout(
            mo.md("Database disabled: annotation editing and export are off."),
            kind="warn",
        )
    else:
        _sel = annotation_table.value if annotation_table is not None else None
        _row = _sel[0] if _sel else None
        _notes = ""
        if _row is not None:
            for _r in db_roi_rows:
                if _r["id"] == _row["id"]:
                    _notes = _r["notes"]
                    break
        label_input = mo.ui.text(
            value=_row["label"] if _row is not None else "",
            label="label",
            placeholder="e.g. tumor, stroma, necrosis",
        )
        notes_input = mo.ui.text_area(
            value=_notes,
            label="notes",
            placeholder="free-form annotation notes",
            rows=3,
        )

        def _selected_roi_id():
            _s = annotation_table.value if annotation_table is not None else None
            if not _s:
                return None
            try:
                return int(_s[0]["id"])
            except (KeyError, TypeError, ValueError):
                return None

        def _on_save(_):
            _rid = _selected_roi_id()
            if _rid is None:
                set_db_msg(("warn", "Select an ROI row first."))
                return
            try:
                # ...ask the repository what it DID. update_annotation is
                # documented to no-op on a row that is gone, and this message
                # used to be written on the sole condition that nothing raised,
                # so "Saved annotation for ROI 1." was reported in green for a
                # row a second session had already deleted (R07-14).
                _saved = db.roi_repo.update_annotation(
                    _rid, label=label_input.value, notes=notes_input.value
                )
                if not _saved:
                    set_ann_version(object())  # the table is out of date
                    set_db_msg(
                        (
                            "warn",
                            f"ROI {_rid} no longer exists (deleted in another "
                            "session?); nothing was saved.",
                        )
                    )
                    return
                # Trace the HUMAN label write. The agent's annotate_roi tool
                # has always recorded this kind; without the line below the
                # interactions table said only the agent ever labels anything.
                db.trace(
                    "label_set",
                    payload={
                        "actor": "human",
                        "roi_id": _rid,
                        "label": label_input.value,
                        "notes": notes_input.value,
                    },
                    slide_id=get_slide_id(),
                    roi_id=_rid,
                )
                set_ann_version(object())
                set_db_msg(("success", f"Saved annotation for ROI {_rid}."))
            except Exception as _exc:
                set_db_msg(("danger", f"Save failed: {_exc}"))

        def _on_delete(_):
            _rid = _selected_roi_id()
            if _rid is None:
                set_db_msg(("warn", "Select an ROI row first."))
                return
            try:
                # Same rule as _on_save: report the outcome, not the attempt.
                if not db.roi_repo.delete(_rid):
                    set_ann_version(object())
                    set_db_msg(
                        (
                            "warn",
                            f"ROI {_rid} was already gone; nothing was deleted.",
                        )
                    )
                    return
                # Recorded AFTER the row is gone, and deliberately not
                # roi_id-linked in the FK sense: a rejected ROI is the most
                # informative event the trace can hold, and the row it names
                # no longer exists to be re-read.
                db.trace(
                    "roi_delete",
                    payload={"actor": "human", "roi_id": _rid},
                    slide_id=get_slide_id(),
                )
                set_ann_version(object())
                set_db_msg(("success", f"Deleted ROI {_rid}."))
            except Exception as _exc:
                set_db_msg(("danger", f"Delete failed: {_exc}"))

        save_ann_button = mo.ui.button(
            label="Save annotation", kind="success", on_click=_on_save
        )
        delete_ann_button = mo.ui.button(
            label="Delete ROI", kind="danger", on_click=_on_delete
        )

        # Export the current slide's ROIs (or all when no slide is open).
        #
        # A failure must not be delivered as a successful download. It used to
        # be: the except returned "export failed: <exc>" and mo.download handed
        # that string over as rois.json / rois.csv / rois.geojson, with the
        # right filename and the right mimetype, so the browser performed an
        # ordinary download and the user walked away believing the annotations
        # were exported. The failure then surfaced somewhere else entirely --
        # QuPath rejecting the GeoJSON, a script's json.loads raising -- with
        # nothing pointing back at the click (R07-6).
        #
        # mo.download evaluates a callable `filename` as well as callable
        # `data`, both at click time, so the name can report what happened.
        # _pending caches the one export per click; whichever callable marimo
        # invokes first fills it, the other consumes it. Order-independent by
        # construction, and it never serves a cached body from an earlier
        # click.
        _pending: dict = {}

        def _export_once(fmt, produce):
            if fmt in _pending:
                return _pending[fmt]
            try:
                _pending[fmt] = (True, produce())
            except Exception as _exc:
                _pending[fmt] = (False, f"export failed: {_exc}")
            return _pending[fmt]

        def _export_data(fmt, produce):
            _ok, _text = _export_once(fmt, produce)
            _pending.pop(fmt, None)  # this click is over
            return _text

        def _export_filename(fmt, produce, good):
            _ok, _text = _export_once(fmt, produce)
            # A ".txt" the user cannot mistake for their annotations, instead
            # of a correctly-named file with an error message inside it.
            return good if _ok else f"{good}.EXPORT-FAILED.txt"

        def _rois(fmt):
            return lambda: export_rois(db.engine, slide_id=get_slide_id(), fmt=fmt)

        def _geojson():
            # README advertises "one click turns annotations into a
            # QuPath-compatible FeatureCollection"; until R05-8 the only
            # entry point was hescope.interop.geojson, which app.py never called, so
            # the one interop feature on the user-facing list was agent-only.
            # slide_id=None means ALL ROIs here, exactly as it does for
            # export_rois beside it -- it used to mean an empty
            # FeatureCollection (R07-7).
            return slide_geojson_text(db.engine, get_slide_id())

        export_json_button = mo.download(
            data=lambda: _export_data("json", _rois("json")),
            filename=lambda: _export_filename("json", _rois("json"), "rois.json"),
            mimetype="application/json",
            label="Export ROIs (JSON)",
        )
        export_csv_button = mo.download(
            data=lambda: _export_data("csv", _rois("csv")),
            filename=lambda: _export_filename("csv", _rois("csv"), "rois.csv"),
            mimetype="text/csv",
            label="Export ROIs (CSV)",
        )
        export_geojson_button = mo.download(
            data=lambda: _export_data("geojson", _geojson),
            filename=lambda: _export_filename(
                "geojson", _geojson, "rois.geojson"
            ),
            mimetype="application/geo+json",
            label="Export ROIs (GeoJSON, QuPath)",
        )
        def _stats_csv():
            # The measurements, not just the annotations: a result that cannot
            # leave the tool is not a result. Same hardened path as the three
            # above, so a failure arrives as .EXPORT-FAILED.txt rather than as
            # a correctly-named file with an error inside it (R07-6).
            _src_stats = get_source()
            return rows_to_csv(
                roi_stats_rows(
                    db.engine,
                    get_slide_id(),
                    mpp=_src_stats.mpp if _src_stats is not None else None,
                )
            )

        export_stats_button = mo.download(
            data=lambda: _export_data("stats", _stats_csv),
            filename=lambda: _export_filename(
                "stats", _stats_csv, "roi_statistics.csv"
            ),
            mimetype="text/csv",
            label="Export statistics (CSV)",
        )
        # IMPORT, beside the three exports. Round 08 found hescope/importers.py
        # complete, tested and referenced by nothing outside its own tests: the
        # roadmap presented Tier 1.1 interop as delivered, export had three
        # buttons and import had no door at all, so QuPath and ASAP annotations
        # could not get in (R08-4 / R10-2).
        def _on_import_annotations(files):
            if not files:
                return
            _f = files[0]
            _isid = get_slide_id()
            if not db.enabled or _isid is None:
                set_db_msg(
                    ("warn", "Open a slide with the database enabled to import.")
                )
                return
            try:
                _text = _f.contents.decode("utf-8", errors="replace")
                _report = (
                    parse_asap_xml(_text)
                    if _f.name.lower().endswith(".xml")
                    else parse_geojson_annotations(_text)
                )
                _new_ids = import_annotations(db.engine, _isid, _report)
            except Exception as _exc:
                set_db_msg(("danger", f"Import of {_f.name} failed: {_exc}"))
                return
            # An import that keeps 40 of 47 features and reports "imported" is
            # this project's signature failure. Everything the parser could not
            # represent is in report.skipped / report.warnings, so say it here
            # and drop the kind to `warn` whenever anything was lost.
            _bits = [f"Imported {len(_new_ids)} annotation(s) from {_f.name}."]
            if _report.skipped:
                _bits.append(
                    "Skipped "
                    + ", ".join(
                        f"{_n} {_reason}"
                        for _reason, _n in sorted(_report.skipped.items())
                    )
                    + "."
                )
            _bits.extend(_report.warnings[:3])
            if len(_report.warnings) > 3:
                _bits.append(f"(+{len(_report.warnings) - 3} more warnings)")
            _lossless = bool(_new_ids) and not _report.skipped and not _report.warnings
            set_db_msg(("success" if _lossless else "warn", " ".join(_bits)))
            set_ann_version(object())

        import_ann_button = mo.ui.file(
            filetypes=[".geojson", ".json", ".xml"],
            label="Import annotations (QuPath GeoJSON / ASAP XML)",
            on_change=_on_import_annotations,
        )
        ann_edit_view = mo.vstack(
            [
                mo.hstack([label_input, notes_input]),
                mo.hstack([save_ann_button, delete_ann_button]),
                mo.hstack(
                    [export_json_button, export_csv_button, export_geojson_button]
                ),
                mo.hstack([export_stats_button]),
                import_ann_button,
            ]
        )
    return (ann_edit_view,)


@app.cell(hide_code=True)
def _(Path, db, mo):
    from hescope.gdc import tcga_panel
    from hescope.gdc.tcga import GDCClient, SlideCatalog, safe_file_id

    # marimo rule: imported names must be unique across cells -> underscore.
    from hescope.core.paths import resolve_runtime_dir as _resolve_runtime_dir

    try:
        _tcga_app_dir = Path(__file__).resolve().parent
    except NameError:  # marimo kernel context
        _tcga_app_dir = Path.cwd()
    # Same writable-root rule as OUT_DIR/MODELS_DIR (see the constants cell).
    TCGA_DATA_DIR = _resolve_runtime_dir(_tcga_app_dir) / "data" / "tcga"
    TCGA_DATA_DIR.mkdir(parents=True, exist_ok=True)

    tcga_client = GDCClient()
    tcga_catalog = SlideCatalog(TCGA_DATA_DIR / "catalog.db")

    # The TCGA-shaped catalog (project -> case -> sample -> file), in the MAIN
    # database rather than a second one, so a downloaded file joins to its
    # slides row and from there to its annotations. The flat SlideCatalog above
    # stays: it still drives the results table, and both are written on
    # download until the flat one is retired.
    from hescope.gdc.tcga_schema import TcgaCatalog as _TcgaCatalog
    from hescope.gdc.tcga_schema import hits_to_rows as _hits_to_rows
    from hescope.gdc.tcga_schema import storage_relpath as _storage_relpath

    try:
        tcga_db = _TcgaCatalog(db.engine) if db.enabled else None
    except Exception:  # a DB that cannot take the additive tables
        tcga_db = None
    tcga_hits_to_rows = _hits_to_rows
    tcga_storage_relpath = _storage_relpath

    get_tcga_records, set_tcga_records = mo.state([])
    get_tcga_msg, set_tcga_msg = mo.state(None)  # (kind, text) or None
    # Plain thread-shared channels for the background download worker.
    # mo.state setters do not trigger reactive UI updates when called from
    # foreign threads, and a synchronous download would block the kernel
    # (no cell can re-render mid-download), so the worker writes here and
    # the 1s refresh ticker in the status cell renders/consumes these
    # values on the main thread.
    # "downloaded" says whether the file behind "open_path" was fetched and
    # md5-verified by this session, or was simply already on disk. The status
    # cell used to compose its message from the branch it was on rather than
    # from what happened, and reported "Downloaded and opened X" for a file
    # nothing had fetched or checked (R07-13, R01-4's own class).
    tcga_dl = {
        "thread": None, "progress": None, "msg": None, "open_path": None,
        "downloaded": False,
    }
    # 1s auto-refresh ticker (value read by the status cell -> re-render)
    tcga_ticker = mo.ui.refresh(options=["1s"], default_interval="1s")
    return (
        TCGA_DATA_DIR,
        get_tcga_msg,
        get_tcga_records,
        set_tcga_msg,
        set_tcga_records,
        tcga_catalog,
        tcga_client,
        tcga_db,
        tcga_dl,
        tcga_hits_to_rows,
        tcga_panel,
        tcga_storage_relpath,
        tcga_ticker,
    )


@app.cell(hide_code=True)
def _(
    mo,
    set_tcga_msg,
    set_tcga_records,
    tcga_catalog,
    tcga_client,
    tcga_db,
    tcga_hits_to_rows,
    tcga_panel,
):
    def _on_search_gdc(_):
        try:
            _proj = project_dropdown.value
            _records, _total = tcga_client.search_slides(
                project_id=None if _proj in (None, "ALL") else str(_proj),
                sample_type=sample_type_input.value or None,
                size=50,
            )
            tcga_catalog.upsert(_records)
            # Build the hierarchy at SEARCH time, from the raw hits: the
            # project/case/sample rows exist before anything is downloaded, so
            # the catalog can answer "which cases are there" without a single
            # byte of pixel data. Best effort -- the results table is driven by
            # the flat catalog above and must not depend on this.
            if tcga_db is not None:
                try:
                    tcga_db.upsert_rows(
                        tcga_hits_to_rows(getattr(tcga_client, "last_hits", []))
                    )
                except Exception:
                    pass
            set_tcga_records(
                tcga_panel.merge_download_state(
                    _records, tcga_catalog.search(limit=100000)
                )
            )
            set_tcga_msg(
                ("info", f"GDC returned {len(_records)} of {_total} matching slides.")
            )
        except Exception as _exc:  # network down / HTTP error: never crash
            set_tcga_msg(("danger", f"GDC search failed: {_exc}"))

    def _on_browse_catalog(_v):
        # limit=100000 to match the two neighbouring calls in this panel:
        # SlideCatalog.search defaults to 200, so browsing a catalog with more
        # rows than that quietly showed a truncated list directly under a
        # status line reading "catalog: N slides" computed from every row
        # (R07-15). And this action has to write its OWN message, or a red
        # "GDC search failed" from an earlier click stands above a result list
        # that was produced successfully (R07-4).
        try:
            _records = tcga_catalog.search(limit=100000)
            set_tcga_records(_records)
            set_tcga_msg(
                ("info", f"Local catalog: {len(_records)} slides.")
            )
        except Exception as _exc:
            set_tcga_msg(("danger", f"Reading the local catalog failed: {_exc}"))

    (
        tcga_filter_layout,
        project_dropdown,
        sample_type_input,
        tcga_search_button,
        tcga_browse_switch,
    ) = tcga_panel.make_filter_controls(_on_search_gdc, _on_browse_catalog)
    tcga_filter_view = mo.vstack(
        [
            mo.md(
                "Search open-access TCGA whole-slide images on the NCI GDC "
                "portal (no token needed). Downloads are 100MB-2GB SVS files, "
                "stored under `data/tcga/`."
            ),
            tcga_filter_layout,
        ]
    )
    return (tcga_filter_view,)


@app.cell(hide_code=True)
def _(
    TCGA_DATA_DIR,
    db,
    get_tcga_records,
    mo,
    open_slide,
    set_tcga_msg,
    tcga_catalog,
    tcga_client,
    tcga_db,
    tcga_dl,
    tcga_hits_to_rows,
    tcga_panel,
    tcga_storage_relpath,
):
    tcga_results_table = tcga_panel.make_results_table(get_tcga_records())

    def _on_download_open(_):
        _t = tcga_dl["thread"]
        if _t is not None and _t.is_alive():
            set_tcga_msg(("warn", "Download already in progress."))
            return
        _sel = tcga_results_table.value
        if not _sel:
            set_tcga_msg(("warn", "Select a slide row first."))
            return
        _fid = str(_sel[0]["file_id"])

        # ALREADY ON DISK -> open it, without touching the network.
        # download_slide cannot be used for this: it probes the server with a
        # Range request before it can decide the local file is complete, so
        # offline it burns its retry budget and then raises over a slide that
        # is already downloaded. That made a downloaded slide unreachable from
        # the UI whenever the network was down, since this button was the only
        # route to it. Hand the path to the same open_path channel the
        # download worker uses, so the open (and any failure to open) is
        # handled on the main thread exactly as before.
        _local = tcga_catalog.local_file(_fid)
        if _local is not None:
            # downloaded=False: nothing was fetched and no md5 was verified on
            # this path, so the status cell must not report "Downloaded and
            # opened". "Downloaded" is an integrity claim in this app --
            # download_slide checks expected_md5, and local_file only checks
            # that the path exists (R07-13).
            # `msg` is deliberately left alone: it may hold a result the 1s
            # ticker has not drained yet (R07-17).
            tcga_dl.update(progress=None, open_path=str(_local), downloaded=False)
            return

        # integrity check source: md5 carried by the table row if any,
        # otherwise a keyed (PRIMARY KEY) lookup in the local catalog by
        # file_id -- previously an O(n) scan of up to 100,000 catalog rows,
        # in Python, on every click (BUILD-PLAN-DB.md Phase 2, defect 2.3).
        _md5 = _sel[0].get("md5sum")
        if not _md5:
            _cat_rec = tcga_catalog.get(_fid)
            _md5 = _cat_rec.md5sum if _cat_rec is not None else None
        # Only `progress` (and the link-failure flag below) is reset. Clearing
        # `msg` and `open_path` here threw away a hand-off the ticker had not
        # drained yet -- and open_path is a finished, md5-verified, possibly
        # gigabyte download, which would then sit on disk never opened and
        # never mentioned (R07-17).
        tcga_dl["progress"] = (0, None)
        # Reset for THIS attempt -- otherwise a stale True from an earlier
        # download would incorrectly re-surface on a click that never
        # touches the catalog link at all (e.g. the "already on disk" path
        # above, which returns before this point).
        tcga_dl["catalog_link_failed"] = False

        # Where this download belongs: <project>/<case>/ under the TCGA root,
        # so the directory tree mirrors the hierarchy instead of being a wall
        # of file UUIDs. Falls back a level at a time when the row is missing
        # metadata, and every component is sanitised inside storage_relpath --
        # these names come from the server (R02-3, R05-1).
        _row = _sel[0]
        _dest_dir = (
            TCGA_DATA_DIR
            / tcga_storage_relpath(
                _fid,
                _row.get("file_name"),
                _row.get("project_id"),
                _row.get("case_submitter_id"),
            ).parent
        )

        def _work():
            try:
                _path = tcga_client.download_slide(
                    _fid,
                    # file_id is server-supplied: contained before it is
                    # joined onto a path, or a "../" in it walks the
                    # download out of TCGA_DATA_DIR (R05-1). storage_relpath
                    # applies the same containment to the other components.
                    _dest_dir,
                    progress_cb=lambda _d, _t: tcga_dl.__setitem__(
                        "progress", (_d, _t)
                    ),
                    expected_md5=_md5,
                )
                tcga_catalog.mark_downloaded(_fid, str(_path))
                # STRAIGHT INTO THE DATABASE, not on first open. Registering
                # here is what makes a downloaded slide a first-class row with
                # its TCGA hierarchy attached, rather than a file that only
                # becomes known if somebody happens to open it. Both writes
                # are best-effort: a catalog that cannot be written must not
                # cost the user the download.
                if tcga_db is not None:
                    try:
                        _sid = None
                        if db.enabled:
                            _src_reg = open_slide(_path)
                            _w_reg, _h_reg = _src_reg.dimensions
                            _sid = db.slide_repo.register(
                                source_kind="tcga",
                                name=_src_reg.name,
                                path=str(_path),
                                width=_w_reg,
                                height=_h_reg,
                                mpp=_src_reg.mpp,
                                md5sum=_md5,
                            )
                            _close = getattr(_src_reg, "close", None)
                            if callable(_close):
                                _close()
                        _linked = tcga_db.mark_downloaded(_fid, str(_path), slide_id=_sid)
                        if not _linked:
                            # mark_downloaded returns False when file_id names
                            # no row this catalog already knows (defect 2.4) --
                            # the search-time upsert that would normally have
                            # created it is itself best-effort (see
                            # _on_search_gdc above) and can have been skipped
                            # entirely, e.g. this row only ever came from
                            # "Browse local catalog". One retry: seed it from
                            # the client's last search hits, then try again.
                            try:
                                tcga_db.upsert_rows(
                                    tcga_hits_to_rows(
                                        getattr(tcga_client, "last_hits", [])
                                    )
                                )
                            except Exception:
                                pass
                            _linked = tcga_db.mark_downloaded(
                                _fid, str(_path), slide_id=_sid
                            )
                        tcga_dl["catalog_link_failed"] = not _linked
                    except Exception:
                        # the write itself raised (not just "returned False")
                        # -- the link is not written either way.
                        tcga_dl["catalog_link_failed"] = True
                # consumed on the main thread by the status cell (ticker).
                # Report only what this thread actually did: opening happens
                # later on the main thread and can still fail, so the final
                # message is written there.
                tcga_dl["downloaded"] = True
                # Carried alongside `downloaded` for the same reason it exists:
                # the main-thread cell rewrites this message after opening, so
                # a flag the worker sets is the only way the wording it chose
                # survives (R10-1).
                tcga_dl["md5_verified"] = bool(_md5)
                tcga_dl["open_path"] = str(_path)
                # "Downloaded" is an integrity claim in this app (R07-13), and
                # _finalize_part verifies only `if expected_md5:`. When the md5
                # could not be resolved -- the table rows carry none, so the
                # catalog scan above is the only source -- the file is written
                # on a size check alone. Saying "Downloaded" either way makes a
                # verified gigabyte and an unverified one byte-identical on
                # screen, which is the one thing that word is supposed to
                # settle (R10-1).
                tcga_dl["msg"] = (
                    ("success", f"Downloaded and md5-verified {_path.name}")
                    if _md5
                    else (
                        "warn",
                        f"Downloaded {_path.name}, size checked but NOT "
                        "md5-verified: no checksum was available for this file.",
                    )
                )
            except Exception as _exc:  # network / HTTP error: never crash
                tcga_dl["msg"] = ("danger", f"Download failed: {_exc}")
            finally:
                tcga_dl["progress"] = None

        import threading

        tcga_dl["thread"] = threading.Thread(target=_work, daemon=True)
        tcga_dl["thread"].start()

    # "Open slide", not "Download & Open": a row already on disk opens
    # straight away with no network, and only a missing one is downloaded.
    # The label is static because this cell must stay out of the results
    # table's dependency set -- a label keyed on the selection would rebuild
    # the button on every click.
    tcga_download_button = mo.ui.button(
        label="Open slide (downloads if needed)",
        kind="success",
        on_click=_on_download_open,
    )
    tcga_results_view = mo.vstack([tcga_results_table, tcga_download_button])
    return (tcga_results_view,)


@app.cell(hide_code=True)
def _(
    Path,
    get_tcga_msg,
    get_tcga_records,
    mo,
    open_slide_path,
    set_tcga_msg,
    set_tcga_records,
    tcga_catalog,
    tcga_dl,
    tcga_panel,
    tcga_ticker,
):
    # 1s ticker (created in the state cell): re-renders this cell so
    # progress written by the background download worker (plain tcga_dl
    # dict) becomes visible, and completion side effects (open slide,
    # refresh table, show message) run on the main thread where mo.state
    # updates are reactive.
    tcga_ticker.value  # dependency: re-run on every tick

    if tcga_dl["open_path"]:
        _p = tcga_dl["open_path"]
        # Did THIS session fetch and verify the file, or was it already there?
        # Both branches reach the same open, and both used to end in the same
        # "Downloaded and opened" string (R07-13).
        _fetched = tcga_dl.get("downloaded", False)
        tcga_dl["open_path"] = None
        _name = Path(_p).name
        # A verified download can still be unopenable (no backend handles
        # that SVS variant). Report that honestly instead of letting the
        # exception break this cell and leave the worker's message claiming
        # success.
        _verified = tcga_dl.get("md5_verified", False)
        try:
            open_slide_path(_p, source_kind="tcga")
            if not _fetched:
                tcga_dl["msg"] = ("success", f"Opened {_name} (already downloaded)")
            elif _verified:
                tcga_dl["msg"] = (
                    "success", f"Downloaded, md5-verified and opened {_name}"
                )
            else:
                # Fetched, but on a size check alone. "Downloaded" is an
                # integrity claim here (R07-13); do not make it on a file
                # nothing checksummed (R10-1).
                tcga_dl["msg"] = (
                    "warn",
                    f"Downloaded and opened {_name}, but it was NOT "
                    "md5-verified: no checksum was available for this file.",
                )
        except Exception as _exc:
            tcga_dl["msg"] = (
                "danger",
                (
                    f"Downloaded {_name}, but opening it failed: {_exc}"
                    if _fetched
                    else f"{_name} is already on disk, but opening it "
                    f"failed: {_exc}"
                ),
            )
        # A real download that could not be linked into the TCGA hierarchy
        # (BUILD-PLAN-DB round 3 finding 6): the worker recorded that
        # mark_downloaded returned False (or raised) even after retrying, so
        # say so here instead of letting a success/warn message imply the
        # slide is now connected to its case/sample when it is not. Only
        # meaningful when THIS session fetched the file -- the "already on
        # disk" click never attempts a link at all.
        if _fetched and tcga_dl.get("catalog_link_failed", False):
            _kind, _text = tcga_dl["msg"]
            tcga_dl["msg"] = (
                "warn",
                f"{_text} The TCGA catalog link could not be written, so "
                "this slide is not yet connected to its case/sample.",
            )
        try:
            set_tcga_records(
                tcga_panel.merge_download_state(
                    get_tcga_records(), tcga_catalog.search(limit=100000)
                )
            )
        except Exception:
            pass  # table refresh is cosmetic; never break the status cell
    if tcga_dl["msg"]:
        set_tcga_msg(tcga_dl["msg"])
        tcga_dl["msg"] = None

    tcga_status_view = mo.hstack(
        [
            tcga_panel.status_view(
                get_tcga_msg(), tcga_dl["progress"], tcga_catalog.stats()
            ),
            tcga_ticker,
        ],
        justify="space-between",
        align="start",
    )
    return (tcga_status_view,)


@app.cell(hide_code=True)
def _(
    ROI,
    db,
    detect_nuclei,
    extract_patch,
    get_payload,
    get_rois,
    get_slide_id,
    get_source,
    live_selection,
    mo,
    patch_mpp,
    qc_report,
    set_analysis_result,
):
    # "Analyze current selection" (Analysis accordion): runs nuclei detection
    # + QC on the LIVE selection from whichever surface is driving; falls back
    # to the last submitted ROI payload, then to the last session ROI, when
    # nothing is drawn.
    def _on_analyze(_):
        try:
            _src = get_source()
            if _src is None:
                set_analysis_result(("warn", "Open a slide first.", None))
                return
            # live_selection() again: reading roi_plot here meant "Analyze
            # current selection" ignored anything drawn on OpenSeadragon and
            # silently fell through to the last submitted ROI.
            _sel = live_selection()
            _roi = None
            _origin = "live selection"
            if _sel is not None:
                _roi = ROI(
                    kind=_sel["kind"],
                    points=tuple(
                        (float(p[0]), float(p[1]))
                        for p in _sel["points_level0"]
                    ),
                )
            else:
                _payload = get_payload()
                if _payload is not None:
                    _roi = ROI(
                        kind=_payload.roi["kind"],
                        points=tuple(
                            (float(p[0]), float(p[1]))
                            for p in _payload.roi["points_level0"]
                        ),
                    )
                    _origin = "last submitted ROI"
                elif get_rois():
                    _roi = get_rois()[-1]
                    _origin = "last session ROI"
            if _roi is None:
                set_analysis_result(
                    (
                        "warn",
                        "No selection: drag a box or lasso on the viewer "
                        "first (or submit an ROI with 'Send to code agent').",
                        None,
                    )
                )
                return
            _patch = extract_patch(_src, _roi, max_size=1024)
            # detect_nuclei's mpp is microns per PATCH pixel -- it multiplies
            # it by the patch's own height and width to get an area in mm^2 --
            # and extract_patch thumbnails anything wider than 1024 level-0 px.
            # Passing the slide's LEVEL-0 mpp therefore divided the count by an
            # area 1/downsample^2 too small: measured 16.00x overstatement on a
            # 4096 px ROI of the TCGA slide, under a green "Analyzed" callout
            # that prints the true patch size right next to it (R07-2).
            _mpp = patch_mpp(_src, _roi, _patch)
            _labels, _nuc = detect_nuclei(_patch, mpp=_mpp)
            _qc = qc_report(_patch, mpp=_mpp)
            db.trace(
                "analysis_run",
                payload={
                    "actor": "human",
                    "analysis": "nuclei+qc",
                    "origin": _origin,
                    "bbox": [int(v) for v in _roi.bbox()],
                    "nuclei_count": _nuc.count,
                },
                slide_id=get_slide_id(),
            )
            set_analysis_result(
                (
                    "ok",
                    _origin,
                    {
                        "bbox": [int(v) for v in _roi.bbox()],
                        "patch_size": list(_patch.size),
                        "nuclei": {
                            "count": _nuc.count,
                            "density_per_mm2": _nuc.density_per_mm2,
                            "mean_area_px": round(_nuc.mean_area_px, 1),
                            "mean_intensity_h": round(_nuc.mean_intensity_h, 4),
                            "mask_coverage": round(_nuc.mask_coverage, 4),
                        },
                        "qc": {
                            k: (round(v, 4) if isinstance(v, float) else v)
                            for k, v in _qc.items()
                        },
                    },
                )
            )
        except Exception as _exc:  # never crash the notebook
            set_analysis_result(("danger", f"Analysis failed: {_exc}", None))

    analyze_button = mo.ui.button(
        label="Analyze current selection", on_click=_on_analyze
    )
    return (analyze_button,)


@app.cell(hide_code=True)
def _(analyze_button, get_analysis_result, mo):
    # Selection-analysis result view: compact tables + callouts.
    _res = get_analysis_result()
    _parts = [
        mo.md("### Selection analysis"),
        analyze_button,
        mo.md(
            "*Analyzes the live box/lasso selection; with nothing drawn it "
            "falls back to the last submitted ROI.*"
        ),
    ]
    if _res is not None:
        _kind, _text, _data = _res
        if _kind == "ok" and _data is not None:
            _parts.append(
                mo.callout(
                    mo.md(
                        f"Analyzed **{_text}** bbox={_data['bbox']} "
                        f"(patch {_data['patch_size'][0]}x"
                        f"{_data['patch_size'][1]} px)"
                    ),
                    kind="success",
                )
            )
            _parts.append(
                mo.ui.table([_data["nuclei"]], label="Nuclei (H&E)")
            )
            _parts.append(mo.ui.table([_data["qc"]], label="QC"))
        else:
            _parts.append(mo.callout(mo.md(_text), kind=_kind))
    analysis_select_view = mo.vstack(_parts)
    return (analysis_select_view,)


@app.cell(hide_code=True)
def _(MODELS_DIR, get_models_version, hm_choice, list_models, mo):
    # Heatmap controls (Analysis accordion). The model dropdown is rebuilt
    # whenever get_models_version changes (after training) -- refreshing its
    # OPTIONS is the whole point of the token. Refreshing its SELECTION is not:
    # marimo stamps a re-constructed mo.ui element with a fresh token so it
    # comes back at its default, which for this dropdown was "nothing chosen".
    # A successful "Train from annotations" therefore silently un-picked the
    # user's model -- on the one click after which they most want a
    # model_prob sweep (R07-8, R04-4's class). hm_choice is a plain
    # (non-reactive) dict, so remembering the pick adds no dependency edge.
    get_models_version()
    try:
        hm_models = list_models(str(MODELS_DIR))
    except Exception:
        hm_models = []
    _model_opts = [_m.get("name", "?") for _m in hm_models]
    hm_model_dropdown = mo.ui.dropdown(
        options=_model_opts,
        # A value no longer on offer raises out of the cell, so a model that
        # was deleted or renamed falls back to "nothing chosen".
        value=hm_choice["model"] if hm_choice["model"] in _model_opts else None,
        label="model (for model_prob metrics)",
        allow_select_none=True,
        on_change=lambda _v: hm_choice.__setitem__("model", _v),
    )
    return hm_model_dropdown, hm_models


@app.cell(hide_code=True)
def _(mo):
    # Sweep geometry + navigator blend. Deliberately NOT in the cell above:
    # that one reads get_models_version() so the model list refreshes after
    # training, and a re-run RE-CONSTRUCTS every mo.ui element in the cell,
    # which brings it back at its default value. Parked together, a successful
    # "Train from annotations" silently reset the user's tile size and their
    # "show heatmap on navigator" choice -- neither of which has anything to do
    # with the model list. Same rule as the toolbar cell states above.
    hm_tile_slider = mo.ui.slider(
        start=128, stop=512, step=128, value=256,
        label="tile size", show_value=True,
    )
    hm_nav_checkbox = mo.ui.checkbox(
        value=False, label="show heatmap on navigator"
    )
    return hm_nav_checkbox, hm_tile_slider


@app.cell(hide_code=True)
def _(hm_choice, hm_model_dropdown, hm_models, mo):
    # Metric dropdown, built dynamically from the selected model's labels.
    #
    # This cell is a DESCENDANT of the model dropdown, so it re-runs on every
    # successful train too, and its hardcoded value= reset the user's chosen
    # metric to "tissue_fraction" at the same time (R07-8). It cannot be moved
    # out of that closure the way R04-4 moved the tile slider: its options
    # genuinely depend on the selected model's labels. So the previous choice
    # is remembered in the same non-reactive dict instead, and honoured only
    # while it is still on offer -- picking a model_prob label the new model
    # does not have would raise out of the cell.
    _opts = ["tissue_fraction", "nuclei_density"]
    _name = hm_model_dropdown.value
    if _name:
        _meta = next(
            (_m for _m in hm_models if _m.get("name") == _name), None
        )
        if _meta:
            _opts += [
                f"model_prob:{_lab}" for _lab in _meta.get("labels", [])
            ]
    hm_metric_dropdown = mo.ui.dropdown(
        options=_opts,
        value=hm_choice["metric"] if hm_choice["metric"] in _opts else "tissue_fraction",
        label="heatmap metric",
        on_change=lambda _v: hm_choice.__setitem__("metric", _v),
    )
    return (hm_metric_dropdown,)


@app.cell(hide_code=True)
def _(
    MODELS_DIR,
    compute_grid,
    db,
    detect_nuclei,
    get_slide_id,
    get_source,
    grid_coverage,
    hm_job,
    hm_metric_dropdown,
    hm_model_dropdown,
    hm_tile_slider,
    load_model,
    make_prob_metric,
    mo,
    render_heatmap,
    set_analysis_msg,
    set_hm_result,
    threading,
    tissue_fraction_proxy,
    viewport_png_bytes,
):
    # "Run heatmap" action. Same shape as the TCGA download button: the sweep
    # runs on a WORKER THREAD and reports through the plain hm_job dict.
    #
    # It used to run inline, which made two things that look functional
    # impossible: marimo resolves state updates only after the runner finishes
    # (runtime.py: `await runner.run_all()` then `resolve_state_updates`), so
    # no cell could re-render while the handler was on the stack and the
    # progress bar never had a value other than the None the `finally` left
    # behind -- and by the same token the in-flight guard could never be true,
    # so a second click simply re-ran the whole sweep. Measured on the
    # 6000x4000 demo slide: 0.4 s for tissue_fraction, 9.2 s for
    # nuclei_density; a real WSI plans hundreds of cells and blocks the kernel
    # for minutes with nothing on screen.
    def _quick_nuclei_count(_tile_img):
        # Cap tile cost: count nuclei on a <= 256 px working image.
        _img = _tile_img
        if max(_img.size) > 256:
            _img = _img.copy()
            _img.thumbnail((256, 256))
        return float(detect_nuclei(_img)[1].count)

    def _on_run_heatmap(_):
        _t = hm_job["thread"]
        if _t is not None and _t.is_alive():
            set_analysis_msg(("warn", "Heatmap already running."))
            return
        _src = get_source()
        if _src is None:
            set_analysis_msg(("warn", "Open a slide first."))
            return
        _metric = hm_metric_dropdown.value or "tissue_fraction"
        _tile = int(hm_tile_slider.value or 256)
        # Metric resolution stays on the MAIN thread: "select a model first"
        # and "unknown metric" are user errors about the controls, and they
        # must be answered immediately rather than a tick later.
        try:
            if _metric == "tissue_fraction":
                _metric_fn = tissue_fraction_proxy
            elif _metric == "nuclei_density":
                _metric_fn = _quick_nuclei_count
            elif str(_metric).startswith("model_prob:"):
                _model_name = hm_model_dropdown.value
                if not _model_name:
                    raise ValueError(
                        "Select a model for model_prob metrics "
                        "(train one below first)."
                    )
                _pipeline, _meta = load_model(
                    str(_model_name), str(MODELS_DIR)
                )
                _metric_fn = make_prob_metric(
                    _pipeline, _meta, str(_metric).split(":", 1)[1]
                )
            else:
                raise ValueError(f"unknown metric {_metric!r}")
        except Exception as _exc:  # never crash the notebook
            set_analysis_msg(("danger", f"Heatmap failed: {_exc}"))
            return
        # Pick a downsample that keeps the grid <= ~48 cells on the long
        # axis so sweeps stay interactive; >= 1.0 (never upsample).
        _w, _h = _src.dimensions
        _ds = max(1.0, max(_w, _h) / (_tile * 48.0))
        # Hand over anything the 1s ticker has not drained yet BEFORE this
        # click overwrites the slots. Worker and ticker share three plain dict
        # entries with no ordering guarantee, so a sweep that finished in the
        # last second was simply discarded by the reset below (R07-17). Only
        # the GRID is rescued: its message is superseded by "sweep started"
        # on the very next line, and only a grid measured on the slide still
        # open can honestly be shown (R07-1).
        if hm_job["result"] is not None and hm_job["slide"] is _src:
            set_hm_result(hm_job["result"])
        hm_job.update(progress=(0, 1), result=None, msg=None, slide=_src)
        set_analysis_msg(("info", f"Heatmap '{_metric}': sweep started."))
        # Traced on the MAIN thread, before the worker starts: the sweep runs
        # for minutes and may be abandoned, and what the trace is about is the
        # user asking for it.
        db.trace(
            "analysis_run",
            payload={
                "actor": "human",
                "analysis": "heatmap",
                "metric": _metric,
                "tile": _tile,
                "downsample": round(_ds, 3),
            },
            slide_id=get_slide_id(),
        )

        def _work():
            # compute_grid writes NaN for a tile whose metric RAISED, which is
            # the same NaN a background tile gets, and render_heatmap leaves
            # NaN cells untouched -- so a sweep in which every tile failed
            # renders as the bare thumbnail. Counting the failures is the only
            # way this can be told apart from "no tissue here" (R06-2).
            _fail = {"n": 0, "first": None}

            def _note_failure(_gx, _gy, _exc):
                _fail["n"] += 1
                if _fail["first"] is None:
                    _fail["first"] = f"{type(_exc).__name__}: {_exc}"

            try:
                _grid = compute_grid(
                    _src,
                    _metric_fn,
                    tile=_tile,
                    downsample=_ds,
                    progress_cb=lambda _d, _t: hm_job.__setitem__(
                        "progress", (_d, _t)
                    ),
                    error_cb=_note_failure,
                )
                _thumb = _src.get_thumbnail((512, 512)).convert("RGB")
                _blended = render_heatmap(
                    _thumb,
                    _grid,
                    coverage=grid_coverage(
                        _src.dimensions, _grid.shape, tile=_tile, downsample=_ds
                    ),
                )
                # Consumed on the main thread by the ticker cell: mo.state
                # setters do nothing reactive from a foreign thread.
                hm_job["result"] = {
                    "grid": _grid,
                    "params": {
                        "metric": _metric,
                        "tile": _tile,
                        "downsample": round(_ds, 3),
                        "rows": int(_grid.shape[0]),
                        "cols": int(_grid.shape[1]),
                    },
                    "png": viewport_png_bytes(_blended),
                }
                # NaN != NaN: counts the cells the metric returned a value
                # for, without pulling numpy into the notebook.
                _valid = sum(1 for _v in _grid.flat if _v == _v)
                _where = (
                    f"grid {_grid.shape[0]}x{_grid.shape[1]} "
                    f"(tile {_tile}, downsample {_ds:.2f})"
                )
                if _valid == 0 and _fail["n"]:
                    hm_job["msg"] = (
                        "danger",
                        f"Heatmap '{_metric}': all {_fail['n']} tiles failed, "
                        f"nothing was measured -- the image below is the plain "
                        f"thumbnail. First error: {_fail['first']}. {_where}.",
                    )
                elif _valid == 0:
                    hm_job["msg"] = (
                        "warn",
                        f"Heatmap '{_metric}': no tile passed the tissue "
                        f"filter, so nothing is overlaid. {_where}.",
                    )
                elif _fail["n"]:
                    hm_job["msg"] = (
                        "warn",
                        f"Heatmap '{_metric}': {_fail['n']} of "
                        f"{_fail['n'] + _valid} measured tiles failed and are "
                        f"blank. First error: {_fail['first']}. {_where}.",
                    )
                else:
                    hm_job["msg"] = (
                        "success",
                        f"Heatmap '{_metric}': {_valid} cells measured, "
                        f"{_where}.",
                    )
            except Exception as _exc:  # never crash the notebook
                hm_job["msg"] = ("danger", f"Heatmap failed: {_exc}")
            finally:
                hm_job["progress"] = None

        hm_job["thread"] = threading.Thread(target=_work, daemon=True)
        hm_job["thread"].start()

    hm_run_button = mo.ui.button(
        label="Run heatmap", kind="success", on_click=_on_run_heatmap
    )
    return (hm_run_button,)


@app.cell(hide_code=True)
def _(
    get_source,
    hm_job,
    hm_ticker,
    mo,
    set_analysis_msg,
    set_hm_result,
    set_models_version,
    set_train_info,
    set_train_msg,
    train_job,
):
    # 1s ticker (created in the analysis-state cell): re-renders THIS cell so
    # progress written by the background heatmap worker (plain hm_job dict)
    # becomes visible, and so the finished grid is published to mo.state on
    # the main thread, where state updates are reactive.
    #
    # It is a cell of its own, and its view is stacked next to heatmap_view
    # rather than inside it, so the once-a-second re-render does not drag the
    # heatmap PNG through a fresh base64 data URI every tick.
    #
    # The background TRAINING run is drained here too rather than from a
    # second ticker: it needs exactly the same main-thread hand-off, and one
    # mo.ui.refresh widget must not be displayed from two cells.
    hm_ticker.value  # dependency: re-run on every tick

    # PROVENANCE IS CHECKED HERE, AT PUBLISH TIME, not cleared at open time.
    # A sweep of the 81671x18211 TCGA slide runs for minutes and the user is
    # free to change slides while it does; the worker then writes its grid and
    # its green "N cells measured" into hm_job long after _open_slide_path
    # cleared hm_result, and this cell used to publish both unconditionally.
    # The navigator's `except Exception: pass` cannot catch that -- a grid from
    # another slide does not raise, render_heatmap just resizes it -- so slide
    # A's metric grid landed on slide B's tissue under a success callout, with
    # grid_coverage rescaling it into a registration that means nothing
    # (R07-1, the live half of R04-3 that R04-6's worker thread reopened).
    _hm_stale = hm_job["slide"] is not None and hm_job["slide"] is not get_source()
    if hm_job["result"] is not None:
        if not _hm_stale:
            set_hm_result(hm_job["result"])
        hm_job["result"] = None
    if hm_job["msg"] is not None:
        if _hm_stale:
            # Say so rather than drop it: the sweep really did run, and an
            # empty panel where a result was expected is its own small lie.
            set_analysis_msg(
                (
                    "warn",
                    "A heatmap sweep of "
                    f"'{getattr(hm_job['slide'], 'name', 'another slide')}' "
                    "finished after the slide was changed. Its grid is not "
                    "shown, because it does not describe the slide on screen.",
                )
            )
        else:
            set_analysis_msg(hm_job["msg"])
        hm_job["msg"] = None

    if train_job["result"] is not None:
        set_train_info(train_job["result"])
        train_job["result"] = None
        set_models_version(object())  # refresh heatmap model dropdown
    if train_job["msg"] is not None:
        set_train_msg(train_job["msg"])
        train_job["msg"] = None

    _prog = hm_job["progress"]
    if _prog is None:
        _prog_view = mo.md("")
    else:
        _done, _total = _prog
        _pct = int(_done * 100 / _total) if _total else 0
        _prog_view = mo.Html(
            '<div style="margin:4px 0;">'
            f"Heatmap running: {_done}/{_total} cells ({_pct}%)"
            '<div style="width:100%;height:8px;background:#e8e4dc;'
            'border-radius:4px;overflow:hidden;">'
            f'<div style="height:100%;width:{_pct}%;background:#5b8c5a;">'
            "</div></div></div>"
        )
    hm_progress_view = mo.hstack(
        [_prog_view, hm_ticker], justify="space-between", align="center"
    )
    return (hm_progress_view,)


@app.cell(hide_code=True)
def _(
    get_analysis_msg,
    get_hm_result,
    hm_metric_dropdown,
    hm_model_dropdown,
    hm_nav_checkbox,
    hm_run_button,
    hm_tile_slider,
    mo,
):
    # Heatmap controls + result image (Analysis accordion). The progress bar
    # is deliberately NOT here -- see the ticker cell above.
    _parts = [
        mo.md("### Heatmap"),
        mo.hstack([hm_model_dropdown, hm_metric_dropdown, hm_tile_slider]),
        mo.hstack([hm_run_button, hm_nav_checkbox]),
    ]
    _msg = get_analysis_msg()
    if _msg is not None:
        _kind = _msg[0] if _msg[0] in ("info", "warn", "success", "danger") else "info"
        _parts.append(mo.callout(mo.md(_msg[1]), kind=_kind))
    _hm = get_hm_result()
    if _hm is not None:
        _p = _hm["params"]
        _parts.append(mo.image(_hm["png"]))
        _parts.append(
            mo.md(
                f"*metric `{_p['metric']}` | tile {_p['tile']} | "
                f"downsample {_p['downsample']} | grid {_p['rows']}x{_p['cols']} "
                "(grid + params kept in session state)*"
            )
        )
    else:
        _parts.append(mo.md("*No heatmap computed yet.*"))
    heatmap_view = mo.vstack(_parts)
    return (heatmap_view,)


@app.cell(hide_code=True)
def _(
    MODELS_DIR,
    db,
    mo,
    set_train_info,
    set_train_msg,
    threading,
    train_from_annotations,
    train_job,
):
    # "Train from annotations" (Analysis accordion): weakly-supervised patch
    # classifier from labeled ROIs in the DB. Requires the database.
    #
    # Runs on a WORKER THREAD through the plain train_job dict, exactly like
    # the heatmap sweep above: reading and featurizing every labelled patch
    # is seconds of work (measured at 19.7 s for 20 ROIs while it was inline)
    # and marimo resolves state updates only after the handler returns, so an
    # inline run froze the whole notebook with nothing on screen. The ticker
    # cell publishes the result on the main thread.
    train_name_input = mo.ui.text(
        label="model name", value="default", placeholder="e.g. tumor_vs_stroma"
    )

    def _on_train(_):
        _t = train_job["thread"]
        if _t is not None and _t.is_alive():
            set_train_msg(("warn", "Training already running."))
            return
        if not db.enabled:
            set_train_info(None)
            set_train_msg(
                (
                    "warn",
                    "Training requires the database (DB-free mode active): "
                    "labeled ROIs are read from the annotations store.",
                )
            )
            return
        _name = (train_name_input.value or "").strip() or "default"
        train_job.update(result=None, msg=None)
        # Cleared here, on the main thread, so a failed run cannot leave the
        # previous model's table standing under its error message.
        set_train_info(None)
        set_train_msg(("info", f"Training '{_name}': started."))

        def _work():
            try:
                _info = train_from_annotations(
                    db.engine, name=_name, models_dir=str(MODELS_DIR)
                )
            except ValueError as _exc:  # not enough labeled data: expected path
                train_job["msg"] = ("warn", str(_exc))
            except Exception as _exc:  # never crash the notebook
                train_job["msg"] = ("danger", f"Training failed: {_exc}")
            else:
                train_job["result"] = {
                    "name": _info.name,
                    "labels": ", ".join(_info.labels),
                    "n_samples": _info.n_samples,
                    "cv_accuracy": (
                        round(_info.cv_accuracy, 3)
                        if _info.cv_accuracy is not None
                        else "n/a"
                    ),
                    # Which feature space this model actually lives in. A
                    # HESCOPE_EMBEDDER that fails to load falls back to the
                    # handcrafted vector, and that used to be visible only as
                    # a feature_dim nobody reads as a checksum.
                    "encoder": _info.encoder or "handcrafted",
                    "feature_dim": _info.feature_dim,
                }
                if _info.warning:
                    # ModelInfo.warning is how the fallback and a dropped
                    # class report themselves; it was persisted to meta.json
                    # and never shown, so both read as a plain success.
                    train_job["msg"] = (
                        "warn",
                        f"Model '{_info.name}' trained, but: {_info.warning}",
                    )
                else:
                    train_job["msg"] = (
                        "success", f"Model '{_info.name}' trained."
                    )

        train_job["thread"] = threading.Thread(target=_work, daemon=True)
        train_job["thread"].start()

    train_button = mo.ui.button(
        label="Train from annotations", on_click=_on_train
    )
    return train_button, train_name_input


@app.cell(hide_code=True)
def _(get_train_info, get_train_msg, mo, train_button, train_name_input):
    # Train controls + ModelInfo table / message callout.
    _parts = [
        mo.md("### Train classifier"),
        mo.md(
            "*Trains a StandardScaler + LogisticRegression patch classifier "
            "from ROIs that have a label (Annotations panel). Needs >= 2 "
            "labels with >= 2 samples each.*"
        ),
        mo.hstack([train_name_input, train_button]),
    ]
    _msg = get_train_msg()
    if _msg is not None:
        _kind = _msg[0] if _msg[0] in ("info", "warn", "success", "danger") else "info"
        _parts.append(mo.callout(mo.md(_msg[1]), kind=_kind))
    _info = get_train_info()
    if _info is not None:
        _parts.append(mo.ui.table([_info], label="Model info"))
    train_view = mo.vstack(_parts)
    return (train_view,)


@app.cell(hide_code=True)
def _(MODELS_DIR, analysis_capabilities, json):
    # Zero-arg agent tool (module scope, like get_current_selection): JSON
    # with the available analyses, whether torch embeddings are usable, and
    # the trained models. NEVER raises.
    def get_analysis_capabilities():
        """Return a JSON string describing HE-Scope analysis capabilities."""
        try:
            return json.dumps(analysis_capabilities(str(MODELS_DIR)))
        except Exception as _exc:
            return json.dumps({"error": f"{type(_exc).__name__}: {_exc}"})

    return


@app.cell(hide_code=True)
def _(
    db,
    get_ann_version,
    get_slide_id,
    get_source,
    label_summary,
    mo,
    roi_stats_rows,
):
    # Every ROI of this slide as one comparable row, plus per-label aggregates.
    # roi_stats measures one region at a time, which is enough to look at a
    # region and not enough to compare two -- this is the same data reshaped so
    # a claim like "tumour reads higher than stroma" can be checked rather than
    # asserted. Pure query: no new measurement happens here.
    #
    # Depends on ann_version so it refreshes when a label is saved or an ROI
    # deleted, and NOT on the viewport -- panning must not rebuild this table
    # (R04-1).
    get_ann_version()
    _stats_src = get_source()
    if not db.enabled:
        stats_table_view = mo.callout(
            mo.md("Database disabled: per-ROI statistics are unavailable."),
            kind="warn",
        )
    elif get_slide_id() is None:
        stats_table_view = mo.md("_Open a slide to compare its ROIs._")
    else:
        try:
            _stat_rows = roi_stats_rows(
                db.engine,
                get_slide_id(),
                mpp=_stats_src.mpp if _stats_src is not None else None,
            )
        except Exception as _exc:
            _stat_rows = []
            stats_table_view = mo.callout(
                mo.md(f"Could not read ROI statistics: `{_exc}`"), kind="danger"
            )
        if _stat_rows:
            _measured = sum(1 for _r in _stat_rows if _r["has_stats"])
            _note = f"{len(_stat_rows)} ROI(s)"
            if _measured < len(_stat_rows):
                # Say which rows carry no measurement rather than showing
                # blanks the reader has to interpret.
                _note += (
                    f" — {len(_stat_rows) - _measured} drawn but never measured "
                    "(send them to the agent, or run Analyze)"
                )
            if _stats_src is not None and _stats_src.mpp is None:
                _note += "; slide has no mpp, so physical sizes are blank"
            stats_table_view = mo.vstack(
                [
                    mo.md(f"**Per-ROI statistics** — {_note}"),
                    mo.ui.table(_stat_rows, selection=None, pagination=True),
                    mo.md("**By label**"),
                    mo.ui.table(
                        label_summary(_stat_rows), selection=None, pagination=True
                    ),
                ]
            )
        elif "stats_table_view" not in dir():
            stats_table_view = mo.md("_No ROIs on this slide yet._")
    return (stats_table_view,)


@app.cell(hide_code=True)
def _(analysis_select_view, heatmap_view, hm_progress_view, mo, train_view):
    # The Analysis accordion panel content.
    analysis_view = mo.vstack(
        [
            analysis_select_view,
            mo.md("---"),
            heatmap_view,
            hm_progress_view,
            mo.md("---"),
            train_view,
        ]
    )
    return (analysis_view,)


@app.cell(hide_code=True)
def _(
    agent_payload_view,
    analysis_view,
    ann_browser_view,
    ann_edit_view,
    history_view,
    mo,
    runs_view,
    stats_table_view,
    tcga_filter_view,
    tcga_results_view,
    tcga_status_view,
):
    # Secondary panels live in a collapsed accordion below the viewer.
    mo.accordion(
        {
            "Annotations": mo.vstack([ann_browser_view, ann_edit_view]),
            "Statistics": stats_table_view,
            "Analysis": analysis_view,
            "Agent console": mo.vstack(
                [agent_payload_view, history_view, runs_view]
            ),
            "TCGA browser": mo.vstack(
                [tcga_filter_view, tcga_results_view, tcga_status_view]
            ),
        },
        multiple=True,
    )
    return


@app.cell(hide_code=True)
def _():
    # Theme CSS lives in assets/theme.css, loaded via
    # marimo.App(css_file=...) at the top of this file. A style-only
    # mo.Html cell is dropped by the marimo 0.23 frontend (empty output,
    # rules never applied), so the stylesheet must not live in a cell.
    return


@app.cell(hide_code=True)
def _(mo):
    # Always-visible agent connection guide (open by default).
    mo.md("""
    ### Connect your agent

    1. **Install the skill.** On agents that support Agent Skills (Kimi Code,
       Codex, ...): `npx skills add marimo-team/marimo-pair`. On Claude Code:
       `/plugin marketplace add marimo-team/marimo-pair`, then
       `/plugin install marimo-pair@marimo-pair`.
    2. **Start the app** with `marimo edit app.py --no-token`, keep the
       browser tab open, and press Run once on first load so the cells
       execute — until they do, the kernel globals do not exist.
    3. **Why `marimo edit` and not `marimo run`.** Run mode is read-only:
       the server requires edit permission for `/api/sessions` and
       `/execute` and returns 401 otherwise, so marimo-pair cannot attach to
       a run-mode session. For a chrome-free UI, use app view instead — the
       eye icon in the bottom-right toolbar, or Cmd/Ctrl + `.`. It looks
       exactly like run mode but the session stays an edit session, so agent
       pairing is unaffected. Every cell here is `hide_code` by default.
    4. **Tell your agent** "connect to my marimo notebook". It will use
       marimo-pair's discover/execute scripts to enter the kernel.
    5. **After you circle something**, just drag a box or lasso on the
       figure — the agent reads it with `get_current_selection()`, no click
       needed. Anything you sent with "Send to code agent" is in
       `get_latest_selection()`. Both carry the patch image path in their
       JSON, so a multimodal agent can open the image directly.
    6. **More agent tools** (module scope, same contract as the two above:
       they return a JSON string or a fixed sentinel, and never raise).
       `get_slide_info()` returns the open slide's metadata as JSON (name,
       dimensions, mpp, levels, DB id, annotation count) or `NO_SLIDE`.
       `query_annotations(label=None, limit=50)` returns this slide's
       annotation rows as a JSON list, optionally filtered by label.
       `annotate_roi(roi_id, label=None, notes=None)` writes a label/notes
       back to the rois table, returning `{"error": ...}` in DB-free mode or
       for an unknown `roi_id`. `get_analysis_capabilities()` reports the
       available analyses (nuclei, QC, stain normalization, heatmaps,
       training), torch availability and any trained models.
    7. **Your records survive an agent switch.** Annotations live in
       `data/hescope.db` and history in `agent_out/`, independent of which
       agent wrote them.
    8. **AGENTS.md** at the repo root is the full contract for agents; an
       agent entering the project directory picks it up automatically.
    """)
    return


if __name__ == "__main__":
    app.run()
