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
app = marimo.App(width="full")


@app.cell(hide_code=True)
def _():
    import json
    import math
    import tempfile
    from dataclasses import replace as dc_replace
    from pathlib import Path

    import marimo as mo

    from hescope import analysis_capabilities
    from hescope.agent_bridge import (
        AgentBridge,
        magnification_for,
        make_annotate_roi_tool,
        make_live_selection_tool,
        make_marimo_tool,
        make_query_annotations_tool,
        make_slide_info_tool,
    )
    from hescope.db import export_rois
    from hescope.grid import tissue_fraction_proxy
    from hescope.heatmap import compute_grid, render_heatmap
    from hescope.measure import format_measurement, measure_box
    from hescope.ml import (
        list_models,
        load_model,
        make_prob_metric,
        train_from_annotations,
    )
    from hescope.nuclei import detect_nuclei
    from hescope.overlay import draw_navigator_markers, draw_scale_bar
    from hescope.qc import qc_report
    from hescope.rois import ROI, ViewportState, extract_patch
    from hescope.slides import open_slide
    from hescope.stain import fit_reference, macenko_normalize
    from hescope.viewer import (
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
    )

    return (
        AgentBridge,
        Path,
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
        export_rois,
        extract_patch,
        fit_reference,
        format_measurement,
        json,
        jump_viewport_for_bbox,
        list_models,
        load_model,
        macenko_normalize,
        magnification_for,
        make_annotate_roi_tool,
        make_live_selection_tool,
        make_marimo_tool,
        make_prob_metric,
        make_query_annotations_tool,
        make_slide_info_tool,
        make_roi_figure,
        measure_box,
        mo,
        navigator_image,
        open_slide,
        parse_plotly_selection,
        qc_report,
        raw_plotly_selection,
        render_heatmap,
        render_viewport,
        roi_from_db_row,
        selection_to_roi,
        tempfile,
        tissue_fraction_proxy,
        train_from_annotations,
        viewport_png_bytes,
    )


@app.cell(hide_code=True)
def _(Path):
    from hescope.demo import generate_demo_slide
    from hescope.paths import resolve_runtime_dir

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

    def ensure_demo_slide():
        """Return the demo slide path, generating it in-process if missing.

        Generation is in-process via hescope.demo (shipped with the wheel);
        tools/make_demo_slide.py is not packaged and is no longer required.
        """
        if not DEMO_SLIDE_PATH.exists():
            generate_demo_slide(DEMO_SLIDE_PATH)
        return DEMO_SLIDE_PATH

    return MODELS_DIR, OUT_DIR, ensure_demo_slide


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

    agent_bridge = AgentBridge(OUT_DIR)

    # Module-scope tool for a code agent (marimo AI / pair integration).
    get_latest_selection = make_marimo_tool(lambda: agent_bridge)

    # Click-handler registry: toolbar buttons are built BEFORE the viewer
    # cell exists (so they cannot reference roi_plot without a cycle), while
    # the handlers they trigger live in later cells. Later cells register
    # their handlers here; toolbar buttons look them up at click time.
    ui_actions = {}
    return (
        agent_bridge,
        get_ann_version,
        get_db_msg,
        get_measure_msg,
        get_payload,
        get_rois,
        get_slide_id,
        get_source,
        get_vp,
        set_ann_version,
        set_db_msg,
        set_measure_msg,
        set_payload,
        set_rois,
        set_slide_id,
        set_source,
        set_vp,
        ui_actions,
    )


@app.cell(hide_code=True)
def _(mo):
    # Analysis-panel state (SPEC-ML Part C). All channels are (kind, text)
    # message tuples or opaque result dicts; None = nothing to show.
    # get_stain_ref uses allow_self_loops: the viewer cell reads it AND sets
    # it once (reference fit on the first non-blank viewport image).
    get_stain_ref, set_stain_ref = mo.state(None, allow_self_loops=True)
    get_analysis_result, set_analysis_result = mo.state(None)
    get_analysis_msg, set_analysis_msg = mo.state(None)  # (kind, text) | None
    get_hm_progress, set_hm_progress = mo.state(None)  # (done, total) | None
    get_hm_result, set_hm_result = mo.state(None)  # {"grid","params","png"}
    get_train_msg, set_train_msg = mo.state(None)  # (kind, text) | None
    get_train_info, set_train_info = mo.state(None)  # ModelInfo dict | None
    get_models_version, set_models_version = mo.state(None)  # refresh token
    return (
        get_analysis_msg,
        get_analysis_result,
        get_hm_progress,
        get_hm_result,
        get_models_version,
        get_stain_ref,
        get_train_info,
        get_train_msg,
        set_analysis_msg,
        set_analysis_result,
        set_hm_progress,
        set_hm_result,
        set_models_version,
        set_stain_ref,
        set_train_info,
        set_train_msg,
    )


@app.cell(hide_code=True)
def _(
    current_selection,
    get_source,
    get_vp,
    make_live_selection_tool,
    raw_plotly_selection,
    roi_plot,
):
    # Zero-click live-selection tool for a code agent (marimo-pair). Reports
    # the RAW figure selection (box/lasso) mapped to level-0 coordinates the
    # moment the user drags on the unified plotly viewer — no "Send to code
    # agent" click required. Returns "NO_SELECTION" when nothing is drawn or
    # no slide is open. Companion of get_latest_selection (last submitted ROI).
    def _live_plotly_value():
        try:
            _plot = roi_plot
        except NameError:  # defensive: viewer cell not run in this kernel
            return None
        # raw_plotly_selection reads the private _selection_data attr: for an
        # image-only figure .value is the (empty) selected-points list, not
        # the selection dict (marimo 0.23).
        return raw_plotly_selection(_plot)

    get_current_selection = make_live_selection_tool(
        lambda: current_selection(get_source(), get_vp(), _live_plotly_value())
    )
    return (get_current_selection,)


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
    return annotate_roi, get_slide_info, query_annotations


@app.cell(hide_code=True)
def _(
    Path,
    ViewportState,
    db,
    ensure_demo_slide,
    mo,
    open_slide,
    set_db_msg,
    set_measure_msg,
    set_slide_id,
    set_source,
    set_vp,
    tempfile,
):
    # Sidebar "Open slide" panel. Hardened: every widget is constructed in
    # its own try/except, so a failure renders a callout in place of that
    # widget instead of the whole loader section disappearing.
    def _open_slide_path(p, source_kind="local"):
        src = open_slide(p)
        set_source(src)
        set_measure_msg(None)
        _w, _h = src.dimensions
        set_vp(
            ViewportState(
                center=(_w / 2.0, _h / 2.0),
                downsample=max(src.level_downsamples),
                size=(1024, 768),
            )
        )
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

    def _on_open_clicked(_):
        _v = getattr(path_input, "value", None)
        if _v:
            _open_slide_path(_v, source_kind="local")

    def _on_demo_clicked(_):
        _open_slide_path(str(ensure_demo_slide()), source_kind="local")

    def _on_upload(files):
        if files:
            _f = files[0]
            _tmp = Path(tempfile.mkdtemp(prefix="hescope_upload_")) / _f.name
            _tmp.write_bytes(_f.contents)
            _open_slide_path(str(_tmp), source_kind="upload")

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
            mo.hstack([path_input, open_button], justify="start", gap=0.5),
            demo_button,
            file_upload,
        ]
    )
    open_slide_path = _open_slide_path  # shared with the TCGA panel
    return loader_panel, open_slide_path


@app.cell(hide_code=True)
def _(mo):
    # Sidebar "Display" panel: adjustments affect the DISPLAYED unified
    # viewer only; selection coordinates are unaffected (same extents).
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
    # Display-only Macenko stain normalization: the reference is fitted ONCE
    # on the first non-blank viewport image and cached (mo.state); it only
    # changes what is displayed — selection coordinates and any downstream
    # analysis still read the raw slide pixels.
    stain_norm_checkbox = mo.ui.checkbox(
        value=False, label="stain normalize (Macenko, display-only)"
    )
    display_panel = mo.vstack(
        [
            mo.md("**Display**"),
            brightness_slider,
            contrast_slider,
            gamma_slider,
            channel_dropdown,
            overlay_checkbox,
            stain_norm_checkbox,
        ]
    )
    return (
        brightness_slider,
        channel_dropdown,
        contrast_slider,
        display_panel,
        gamma_slider,
        overlay_checkbox,
        stain_norm_checkbox,
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
        if hm_nav_checkbox.value and _hm is not None:
            try:
                _nav_img = render_heatmap(_nav_img, _hm["grid"])
            except Exception:
                pass  # stale/mismatched grid: show the plain thumbnail
        if overlay_checkbox.value and overlay_rois:
            _nav_img = draw_navigator_markers(
                _nav_img, overlay_rois, _src.dimensions
            )
        navigator_panel = mo.vstack(
            [mo.md("**Navigator**"), mo.image(viewport_png_bytes(_nav_img))]
        )
    return (navigator_panel,)


@app.cell(hide_code=True)
def _(
    dc_replace,
    get_rois,
    get_source,
    get_vp,
    jump_viewport_for_bbox,
    mo,
    set_db_msg,
    set_rois,
    set_vp,
):
    # Sidebar "ROIs" panel: session ROI list with per-row view/delete
    # buttons. View reuses the annotation-browser jump (center on the bbox,
    # zoom so it fills the viewport); only state getters/setters and
    # imported helpers are referenced, so no reactive cycle is introduced.
    _rois = get_rois()

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
            set_vp(dc_replace(get_vp(), center=_center, downsample=_ds))
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

    def _on_clear_rois(_):
        set_rois([])
        set_db_msg(("info", "All session ROIs cleared."))

    clear_rois_button = mo.ui.button(
        label="Clear all ROIs", on_click=_on_clear_rois
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
        ]
    roi_panel = mo.vstack([mo.md("**ROIs**"), *_rows, clear_rois_button])
    return roi_panel, roi_delete_buttons, roi_view_buttons


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
def _(db_status_badge, get_source, get_vp, magnification_for, mo):
    # Compact header row: app title + slide info + DB status badge.
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
def _(
    ViewportState,
    dc_replace,
    get_source,
    get_vp,
    mo,
    set_vp,
    ui_actions,
):
    # THE toolbar: every control the user touches mid-session lives in this
    # one compact row (wraps on narrow windows). Muted, flat styling only.
    _src = get_source()
    if _src is not None:
        _max_ds = float(max(_src.level_downsamples))
    else:
        _max_ds = 8.0

    def _pan(dx, dy):
        # step = 25% of the viewport, in level-0 coordinates
        _vp = get_vp()
        _sx = _vp.size[0] * 0.25 * _vp.downsample
        _sy = _vp.size[1] * 0.25 * _vp.downsample
        set_vp(
            dc_replace(
                _vp, center=(_vp.center[0] + dx * _sx, _vp.center[1] + dy * _sy)
            )
        )

    pan_west = mo.ui.button(label="◀", on_click=lambda _: _pan(-1, 0))
    pan_east = mo.ui.button(label="▶", on_click=lambda _: _pan(1, 0))
    pan_north = mo.ui.button(label="▲", on_click=lambda _: _pan(0, -1))
    pan_south = mo.ui.button(label="▼", on_click=lambda _: _pan(0, 1))
    pan_cluster = mo.hstack(
        [pan_west, pan_east, pan_north, pan_south], justify="start", gap=0.1
    )

    def _on_zoom_fit(_):
        _s = get_source()
        if _s is None:
            return
        _w, _h = _s.dimensions
        set_vp(
            ViewportState(
                center=(_w / 2.0, _h / 2.0),
                downsample=max(_s.level_downsamples),
                size=get_vp().size,
            )
        )

    zoom_fit_button = mo.ui.button(label="Zoom to fit", on_click=_on_zoom_fit)
    zoom_slider = mo.ui.slider(
        start=1.0,
        stop=max(_max_ds, 1.0),
        step=0.5,
        value=max(_max_ds, 1.0),
        label="zoom (downsample)",
        show_value=True,
        on_change=lambda v: set_vp(dc_replace(get_vp(), downsample=max(float(v), 1.0))),
    )

    dragmode_radio = mo.ui.radio(
        options={"pan": "pan", "box select": "select", "lasso": "lasso"},
        value="box select",
        label="mouse",
        inline=True,
    )
    measure_checkbox = mo.ui.checkbox(label="measure mode")
    circle_checkbox = mo.ui.checkbox(label="box as circle")

    def _fire(name):
        # Handlers are registered by later cells (they need roi_plot, which
        # does not exist yet when this toolbar is built).
        def _handler(_btn):
            _fn = ui_actions.get(name)
            if _fn is not None:
                _fn(_btn)

        return _handler

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
    apply_display_pipeline,
    brightness_slider,
    channel_dropdown,
    contrast_slider,
    dragmode_radio,
    draw_scale_bar,
    fit_reference,
    gamma_slider,
    get_source,
    get_stain_ref,
    get_vp,
    macenko_normalize,
    make_roi_figure,
    mo,
    overlay_checkbox,
    overlay_rois,
    render_viewport,
    selected_index,
    set_stain_ref,
    stain_norm_checkbox,
    tissue_fraction_proxy,
):
    # THE unified viewer: exactly ONE plotly figure. Wheel-zoom, pan (mouse
    # mode "pan" or plotly modebar) and ROI drawing (box / lasso) all happen
    # on this surface. The image shown is the ADJUSTED viewport (display
    # pipeline + ROI outlines); axis extents equal the viewport size in
    # pixels, so selection coordinates map 1:1 to viewport pixels regardless
    # of client-side zoom — parse_plotly_selection / current_selection keep
    # working unchanged. uirevision is keyed on the viewport state: plotly
    # client zoom survives overlay/adjustment re-renders, while server-driven
    # moves (pan buttons, downsample slider, zoom-to-fit, annotation jump)
    # reset the view to the freshly rendered region.
    _src = get_source()
    _vp = get_vp()
    if _src is None:
        roi_plot = None
        viewer_view = mo.callout(
            mo.md("Open a slide from the sidebar to begin."), kind="info"
        )
    else:
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
        _fig = make_roi_figure(
            _display_img,
            dragmode=dragmode_radio.value or "select",
            uirevision=_uirev,
        )
        # roi_plot is exposed at module scope (None before a slide is open)
        # so the zero-click get_current_selection tool can read its live
        # selection via raw_plotly_selection(roi_plot).
        roi_plot = mo.ui.plotly(_fig, config=getattr(_fig, "_config", None))
        viewer_view = roi_plot
    viewer_view
    return (roi_plot,)


@app.cell(hide_code=True)
def _(
    db,
    db_status_detail,
    get_db_msg,
    get_measure_msg,
    get_source,
    get_vp,
    magnification_for,
    mo,
):
    # Status line under the viewer: viewport readout + measurement / DB
    # message callouts.
    _parts = []
    _src = get_source()
    if _src is not None:
        _vp = get_vp()
        _mag = magnification_for(_src.mpp, _vp.downsample)
        _mag_s = (
            f"magnification ~{_mag:.1f}x"
            if _mag is not None
            else f"downsample x{_vp.downsample:g}"
        )
        _parts.append(
            mo.md(
                f"center=({_vp.center[0]:.0f}, {_vp.center[1]:.0f}) | "
                f"{_mag_s} | viewport {_vp.size[0]}x{_vp.size[1]} px"
            )
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
    circle_checkbox,
    format_measurement,
    get_rois,
    get_source,
    get_vp,
    measure_box,
    measure_checkbox,
    parse_plotly_selection,
    raw_plotly_selection,
    roi_plot,
    selection_to_roi,
    set_measure_msg,
    set_rois,
    ui_actions,
):
    # "Add ROI" action for the toolbar button (registered by name; the
    # button itself lives in the toolbar cell above the viewer).
    def _on_add_roi(_):
        if roi_plot is None:
            return
        # raw_plotly_selection handles marimo 0.23 image-only figures,
        # where .value is [] and the selection lives on _selection_data.
        _sel = parse_plotly_selection(raw_plotly_selection(roi_plot))
        if _sel is None:
            set_measure_msg(
                ("warn", "No selection: drag a box or lasso on the viewer first.")
            )
            return
        if measure_checkbox.value:
            # Measure mode: box selections become a measurement, not an
            # ROI. Circle/lasso selections are ignored with a hint.
            if _sel["kind"] == "box":
                _rect = selection_to_roi(_sel, get_vp())
                _src = get_source()
                _mpp = _src.mpp if _src is not None else None
                _m = measure_box(_rect.points[0], _rect.points[1], _mpp)
                set_measure_msg(("info", format_measurement(_m)))
            else:
                set_measure_msg(
                    (
                        "warn",
                        "Measure mode: only box selections are measured; "
                        "lasso/circle selections are ignored.",
                    )
                )
            return
        _roi = selection_to_roi(
            _sel, get_vp(), as_circle=bool(circle_checkbox.value)
        )
        set_rois(get_rois() + [_roi])
        set_measure_msg(None)

    ui_actions["add_roi"] = _on_add_roi
    return


@app.cell(hide_code=True)
def _(
    AgentBridge,
    OUT_DIR,
    agent_bridge,
    db,
    get_payload,
    get_rois,
    get_slide_id,
    get_source,
    get_vp,
    magnification_for,
    mo,
    parse_plotly_selection,
    raw_plotly_selection,
    roi_plot,
    selection_to_roi,
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
            _sel = parse_plotly_selection(
                raw_plotly_selection(roi_plot) if roi_plot is not None else None
            )
            if _sel is None:
                set_db_msg(
                    (
                        "warn",
                        "Nothing to send: drag a box or lasso on the viewer "
                        "first (or click 'Add ROI').",
                    )
                )
                return
            _roi = selection_to_roi(_sel, get_vp())
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
def _(
    db,
    db_roi_error,
    db_roi_rows,
    dc_replace,
    get_slide_id,
    get_source,
    get_vp,
    jump_viewport_for_bbox,
    mo,
    set_vp,
):
    # Annotation browser (inside the Annotations accordion). Selecting a row
    # jumps the unified viewer: center on the ROI bbox, zoom so it fills
    # ~80% of the viewport (clamped to the valid downsample range).
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

        def _on_row_selected(rows):
            if not rows:
                return
            _bbox = rows[0].get("bbox")
            _src = get_source()
            if not _bbox or _src is None:
                return
            _center, _ds = jump_viewport_for_bbox(
                _bbox,
                get_vp().size,
                max_downsample=float(max(_src.level_downsamples)),
            )
            set_vp(dc_replace(get_vp(), center=_center, downsample=_ds))

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
    mo,
    set_ann_version,
    set_db_msg,
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
                db.roi_repo.update_annotation(
                    _rid, label=label_input.value, notes=notes_input.value
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
                db.roi_repo.delete(_rid)
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

        def _export(fmt):
            # Export the current slide's ROIs (or all when no slide is open);
            # failures surface as downloadable text, never a crash.
            try:
                return export_rois(db.engine, slide_id=get_slide_id(), fmt=fmt)
            except Exception as _exc:
                return f"export failed: {_exc}"

        export_json_button = mo.download(
            data=lambda: _export("json"),
            filename="rois.json",
            mimetype="application/json",
            label="Export ROIs (JSON)",
        )
        export_csv_button = mo.download(
            data=lambda: _export("csv"),
            filename="rois.csv",
            mimetype="text/csv",
            label="Export ROIs (CSV)",
        )
        ann_edit_view = mo.vstack(
            [
                mo.hstack([label_input, notes_input]),
                mo.hstack([save_ann_button, delete_ann_button]),
                mo.hstack([export_json_button, export_csv_button]),
            ]
        )
    return (ann_edit_view,)


@app.cell(hide_code=True)
def _(Path, mo):
    from hescope import tcga_panel
    from hescope.tcga import GDCClient, SlideCatalog

    # marimo rule: imported names must be unique across cells -> underscore.
    from hescope.paths import resolve_runtime_dir as _resolve_runtime_dir

    try:
        _tcga_app_dir = Path(__file__).resolve().parent
    except NameError:  # marimo kernel context
        _tcga_app_dir = Path.cwd()
    # Same writable-root rule as OUT_DIR/MODELS_DIR (see the constants cell).
    TCGA_DATA_DIR = _resolve_runtime_dir(_tcga_app_dir) / "data" / "tcga"
    TCGA_DATA_DIR.mkdir(parents=True, exist_ok=True)

    tcga_client = GDCClient()
    tcga_catalog = SlideCatalog(TCGA_DATA_DIR / "catalog.db")

    get_tcga_records, set_tcga_records = mo.state([])
    get_tcga_msg, set_tcga_msg = mo.state(None)  # (kind, text) or None
    get_tcga_progress, set_tcga_progress = mo.state(None)  # (done, total)|None
    return (
        TCGA_DATA_DIR,
        get_tcga_msg,
        get_tcga_progress,
        get_tcga_records,
        set_tcga_msg,
        set_tcga_progress,
        set_tcga_records,
        tcga_catalog,
        tcga_client,
        tcga_panel,
    )


@app.cell(hide_code=True)
def _(
    mo,
    set_tcga_msg,
    set_tcga_records,
    tcga_catalog,
    tcga_client,
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
        set_tcga_records(tcga_catalog.search())

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
    get_tcga_progress,
    get_tcga_records,
    mo,
    open_slide_path,
    set_tcga_msg,
    set_tcga_progress,
    set_tcga_records,
    tcga_catalog,
    tcga_client,
    tcga_panel,
):
    tcga_results_table = tcga_panel.make_results_table(get_tcga_records())

    def _on_download_open(_):
        if get_tcga_progress() is not None:
            set_tcga_msg(("warn", "Download already in progress."))
            return
        _sel = tcga_results_table.value
        if not _sel:
            set_tcga_msg(("warn", "Select a slide row first."))
            return
        _fid = str(_sel[0]["file_id"])
        try:
            # integrity check source: md5 carried by the table row if any,
            # otherwise look it up in the local catalog by file_id
            _md5 = _sel[0].get("md5sum")
            if not _md5:
                _md5 = next(
                    (
                        _r.md5sum
                        for _r in tcga_catalog.search(limit=100000)
                        if _r.file_id == _fid and _r.md5sum
                    ),
                    None,
                )
            set_tcga_progress((0, None))
            _path = tcga_client.download_slide(
                _fid,
                TCGA_DATA_DIR / _fid,
                progress_cb=lambda _d, _t: set_tcga_progress((_d, _t)),
                expected_md5=_md5,
            )
            tcga_catalog.mark_downloaded(_fid, str(_path))
            set_tcga_records(
                tcga_panel.merge_download_state(
                    get_tcga_records(), tcga_catalog.search(limit=100000)
                )
            )
            open_slide_path(str(_path), source_kind="tcga")
            set_tcga_msg(("success", f"Downloaded and opened {_path.name}"))
        except Exception as _exc:  # network / HTTP error: never crash
            set_tcga_msg(("danger", f"Download failed: {_exc}"))
        finally:
            set_tcga_progress(None)

    tcga_download_button = mo.ui.button(
        label="Download & Open", kind="success", on_click=_on_download_open
    )
    tcga_results_view = mo.vstack([tcga_results_table, tcga_download_button])
    return (tcga_results_view,)


@app.cell(hide_code=True)
def _(get_tcga_msg, get_tcga_progress, tcga_catalog, tcga_panel):
    tcga_status_view = tcga_panel.status_view(
        get_tcga_msg(), get_tcga_progress(), tcga_catalog.stats()
    )
    return (tcga_status_view,)


@app.cell(hide_code=True)
def _(
    current_selection,
    detect_nuclei,
    extract_patch,
    get_payload,
    get_rois,
    get_source,
    get_vp,
    mo,
    qc_report,
    raw_plotly_selection,
    roi_plot,
    ROI,
    set_analysis_result,
):
    # "Analyze current selection" (Analysis accordion): runs nuclei detection
    # + QC on the LIVE plotly selection; falls back to the last submitted ROI
    # payload, then to the last session ROI, when nothing is drawn.
    def _on_analyze(_):
        try:
            _src = get_source()
            if _src is None:
                set_analysis_result(("warn", "Open a slide first.", None))
                return
            _sel = current_selection(
                _src,
                get_vp(),
                raw_plotly_selection(roi_plot) if roi_plot is not None else None,
            )
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
            _labels, _nuc = detect_nuclei(_patch, mpp=_src.mpp)
            _qc = qc_report(_patch, mpp=_src.mpp)
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
def _(MODELS_DIR, get_models_version, list_models, mo):
    # Heatmap controls (Analysis accordion). The model dropdown is rebuilt
    # whenever get_models_version changes (after training).
    get_models_version()
    try:
        hm_models = list_models(str(MODELS_DIR))
    except Exception:
        hm_models = []
    hm_model_dropdown = mo.ui.dropdown(
        options=[_m.get("name", "?") for _m in hm_models],
        label="model (for model_prob metrics)",
        allow_select_none=True,
    )
    hm_tile_slider = mo.ui.slider(
        start=128, stop=512, step=128, value=256,
        label="tile size", show_value=True,
    )
    hm_nav_checkbox = mo.ui.checkbox(
        value=False, label="show heatmap on navigator"
    )
    return hm_model_dropdown, hm_models, hm_nav_checkbox, hm_tile_slider


@app.cell(hide_code=True)
def _(hm_model_dropdown, hm_models, mo):
    # Metric dropdown, built dynamically from the selected model's labels.
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
        options=_opts, value="tissue_fraction", label="heatmap metric"
    )
    return (hm_metric_dropdown,)


@app.cell(hide_code=True)
def _(
    MODELS_DIR,
    compute_grid,
    detect_nuclei,
    get_hm_progress,
    get_source,
    hm_metric_dropdown,
    hm_model_dropdown,
    hm_tile_slider,
    load_model,
    make_prob_metric,
    mo,
    render_heatmap,
    set_analysis_msg,
    set_hm_progress,
    set_hm_result,
    tissue_fraction_proxy,
    viewport_png_bytes,
):
    # "Run heatmap" action: same in-flight guard pattern as the TCGA
    # download button (progress state != None means busy).
    def _quick_nuclei_count(_tile_img):
        # Cap tile cost: count nuclei on a <= 256 px working image.
        _img = _tile_img
        if max(_img.size) > 256:
            _img = _img.copy()
            _img.thumbnail((256, 256))
        return float(detect_nuclei(_img)[1].count)

    def _on_run_heatmap(_):
        if get_hm_progress() is not None:
            set_analysis_msg(("warn", "Heatmap already running."))
            return
        _src = get_source()
        if _src is None:
            set_analysis_msg(("warn", "Open a slide first."))
            return
        _metric = hm_metric_dropdown.value or "tissue_fraction"
        _tile = int(hm_tile_slider.value or 256)
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
            # Pick a downsample that keeps the grid <= ~48 cells on the long
            # axis so sweeps stay interactive; >= 1.0 (never upsample).
            _w, _h = _src.dimensions
            _ds = max(1.0, max(_w, _h) / (_tile * 48.0))
            set_hm_progress((0, 1))
            _grid = compute_grid(
                _src,
                _metric_fn,
                tile=_tile,
                downsample=_ds,
                progress_cb=lambda _d, _t: set_hm_progress((_d, _t)),
            )
            _thumb = _src.get_thumbnail((512, 512)).convert("RGB")
            _blended = render_heatmap(_thumb, _grid)
            set_hm_result(
                {
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
            )
            set_analysis_msg(
                (
                    "success",
                    f"Heatmap '{_metric}': grid {_grid.shape[0]}x"
                    f"{_grid.shape[1]} (tile {_tile}, downsample {_ds:.2f}).",
                )
            )
        except Exception as _exc:  # never crash the notebook
            set_analysis_msg(("danger", f"Heatmap failed: {_exc}"))
        finally:
            set_hm_progress(None)

    hm_run_button = mo.ui.button(
        label="Run heatmap", kind="success", on_click=_on_run_heatmap
    )
    return (hm_run_button,)


@app.cell(hide_code=True)
def _(
    get_analysis_msg,
    get_hm_progress,
    get_hm_result,
    hm_metric_dropdown,
    hm_model_dropdown,
    hm_nav_checkbox,
    hm_run_button,
    hm_tile_slider,
    mo,
):
    # Heatmap controls + progress + result image (Analysis accordion).
    _parts = [
        mo.md("### Heatmap"),
        mo.hstack([hm_model_dropdown, hm_metric_dropdown, hm_tile_slider]),
        mo.hstack([hm_run_button, hm_nav_checkbox]),
    ]
    _prog = get_hm_progress()
    if _prog is not None:
        _done, _total = _prog
        _pct = int(_done * 100 / _total) if _total else 0
        _parts.append(
            mo.Html(
                '<div style="margin:4px 0;">'
                f"Heatmap running: {_done}/{_total} cells ({_pct}%)"
                '<div style="width:100%;height:8px;background:#e8e4dc;'
                'border-radius:4px;overflow:hidden;">'
                f'<div style="height:100%;width:{_pct}%;background:#5b8c5a;">'
                "</div></div></div>"
            )
        )
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
    set_models_version,
    set_train_info,
    set_train_msg,
    train_from_annotations,
):
    # "Train from annotations" (Analysis accordion): weakly-supervised patch
    # classifier from labeled ROIs in the DB. Requires the database.
    train_name_input = mo.ui.text(
        label="model name", value="default", placeholder="e.g. tumor_vs_stroma"
    )

    def _on_train(_):
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
        try:
            _info = train_from_annotations(
                db.engine, name=_name, models_dir=str(MODELS_DIR)
            )
            set_train_info(
                {
                    "name": _info.name,
                    "labels": ", ".join(_info.labels),
                    "n_samples": _info.n_samples,
                    "cv_accuracy": (
                        round(_info.cv_accuracy, 3)
                        if _info.cv_accuracy is not None
                        else "n/a"
                    ),
                    "feature_dim": _info.feature_dim,
                }
            )
            set_train_msg(("success", f"Model '{_info.name}' trained."))
            set_models_version(object())  # refresh heatmap model dropdown
        except ValueError as _exc:  # not enough labeled data: expected path
            set_train_info(None)
            set_train_msg(("warn", str(_exc)))
        except Exception as _exc:  # never crash the notebook
            set_train_info(None)
            set_train_msg(("danger", f"Training failed: {_exc}"))

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

    return (get_analysis_capabilities,)


@app.cell(hide_code=True)
def _(analysis_select_view, heatmap_view, mo, train_view):
    # The Analysis accordion panel content.
    analysis_view = mo.vstack(
        [analysis_select_view, mo.md("---"), heatmap_view, mo.md("---"), train_view]
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
    tcga_filter_view,
    tcga_results_view,
    tcga_status_view,
):
    # Secondary panels live in a collapsed accordion below the viewer.
    mo.accordion(
        {
            "Annotations": mo.vstack([ann_browser_view, ann_edit_view]),
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
def _(mo):
    # Theme CSS (injected late so it wins over earlier cell markup).
    #
    # Selector basis (marimo 0.23.16):
    #  - `marimo-accordion`, `marimo-sidebar`, `marimo-callout-output` are
    #    marimo's own custom elements, confirmed via the server-rendered
    #    `.text` of mo.accordion()/mo.sidebar()/mo.callout() (the frontend
    #    upgrades them in-place, keeping the tag names);
    #  - accordion section headers render as <h3><button> inside
    #    `marimo-accordion` (radix-ui accordion markup);
    #  - `.hescope-*` classes are OUR OWN wrapper divs/spans (header app
    #    bar, toolbar, agent status strip) and are immune to marimo
    #    internals.
    # Deliberately no global `*` / `body` rules; visual language: muted warm
    # neutrals (#f7f5f0 family), 1px hairline separators, generous padding,
    # no gradients or saturated fills.
    mo.Html(
        """
<style>
/* App bar: warm low-saturation strip with a hairline bottom separator. */
.hescope-app-bar {
  background: #f7f5f0;
  border-bottom: 1px solid #ddd8d0;
  padding: 10px 14px;
  margin-bottom: 10px;
}
/* Agent status strip: pushed to the far right of the app-bar flex row. */
.hescope-agent-status {
  margin-left: auto;
  font-size: 12px;
  color: #8a8578;
  white-space: nowrap;
}
/* Toolbar: same warm hairline treatment as the app bar; compact padding. */
.hescope-toolbar {
  border: 1px solid #ddd8d0;
  border-radius: 6px;
  background: #faf9f7;
  padding: 6px 10px;
  margin-bottom: 6px;
}
/* Accordion: tighten the title/content rhythm (radix <h3>/<button>). */
marimo-accordion h3 {
  margin: 0;
}
marimo-accordion button {
  padding-top: 6px;
  padding-bottom: 6px;
}
/* Sidebar panels: consistent, slightly tighter stacking. */
marimo-sidebar {
  padding-right: 4px;
}
</style>
"""
    )
    return


@app.cell(hide_code=True)
def _(mo):
    # Always-visible agent connection guide (open by default).
    mo.md(
        """
    ### Connect your agent (agent 联动指南)

    *English: install the marimo-pair skill in your code agent, start this
    app with `marimo edit app.py --no-token`, keep the browser tab open and
    press Run once, then tell your agent "connect to my marimo notebook".
    After you circle something, the agent reads it with
    `get_current_selection()`; submitted ROIs are in `get_latest_selection()`
    (patch image paths included).*

    1. **安装 skill**:Kimi Code / Codex 等支持 Agent Skills 的 agent 执行
       `npx skills add marimo-team/marimo-pair`;Claude Code 执行
       `/plugin marketplace add marimo-team/marimo-pair`,然后
       `/plugin install marimo-pair@marimo-pair`。
    2. **启动**:用 `marimo edit app.py --no-token` 启动本应用,并保持浏览器
       页面打开;首次打开点一次 Run 让 cells 执行(否则内核是空的)。
    3. **为什么必须用 `marimo edit` 而不是 `marimo run`**:`marimo run`
       是只读模式,官方在服务端禁用了代码执行接口(`/api/sessions` 与
       `/execute` 都要求 edit 权限),marimo-pair 原理上无法在 run 模式
       附加。想隐藏所有 cell 获得纯净 app 界面:启动后点右下角工具栏的
       眼睛图标(Toggle app view)或按 Cmd/Ctrl + `.`,界面与 run 模式
       完全一致,但会话仍是 edit session,agent 连接不受影响。本应用所有
       cell 默认已 hide_code。
    4. **对你的 agent 说**:"连接我的 marimo notebook"——它会用
       marimo-pair 的 discover/execute 脚本进入内核。
    5. **圈选之后**:你只要在图上拖框/套索,agent 调
       `get_current_selection()` 就能读到(零点击);点过 Send to code agent
       的完整记录在 `get_latest_selection()`;patch 图片路径在返回的 JSON
       里,多模态 agent 可以直接看图。
    6. **更多 agent 工具**(模块作用域,与上面两个同约定——返回 JSON
       字符串或固定哨兵值,绝不抛异常):`get_slide_info()` 返回当前切片
       元数据 JSON(名称/尺寸/mpp/层级/DB id/标注数,未打开切片时返回
       `NO_SLIDE`);`query_annotations(label=None, limit=50)` 返回当前
       切片的标注行 JSON 列表,可按 label 过滤;`annotate_roi(roi_id,
       label=None, notes=None)` 把标签/备注回写到 rois 表(DB-free 模式
       或未知 roi_id 返回 `{"error": ...}`);`get_analysis_capabilities()`
       返回可用分析(nuclei/QC/染色归一化/热图/训练)、torch 可用性与已
       训练模型的 JSON。
    7. **换 agent 记录不丢**:标注在 `data/hescope.db`,历史在
       `agent_out/`,与 agent 无关。
    8. **AGENTS.md**:项目根目录的 AGENTS.md 是给 agent 的完整契约,agent
       进目录会自动读到。
    """
    )
    return


if __name__ == "__main__":
    app.run()
