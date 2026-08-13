"""Reusable marimo UI building blocks used by app.py.

Rendering is matplotlib-free: the viewport is rendered as a PIL image and the
ROI capture surface is a Plotly figure shown via ``mo.ui.plotly``.

Two encoders live here and the distinction is load-bearing:

* :func:`viewport_png_bytes` -- LOSSLESS PNG, for anything that is data or
  gets saved (navigator thumbnail, heatmap export, report images).
* :func:`viewport_jpeg_bytes` / :func:`viewport_data_uri` -- LOSSY JPEG, for
  the ON-SCREEN frame only. Never for patches, statistics, or the database:
  those go through ``hescope.core.rois.extract_patch``, which reads unadjusted
  source pixels straight from ``SlideSource.read_region``.
"""

from __future__ import annotations

import base64
import io
import json
import math
from dataclasses import dataclass, replace as dc_replace
from typing import Any, Iterable, Literal

from PIL import Image, ImageDraw

from ..agent.agent_bridge import magnification_for  # re-exported for app convenience
from ..core.rois import ROI, ViewportState, viewport_transform
from ..wsi.slides import SlideSource, best_level_for_downsample

__all__ = [
    "render_viewport",
    "render_viewport_overscan",
    "DEFAULT_OVERSCAN",
    "viewport_png_bytes",
    "viewport_jpeg_bytes",
    "viewport_data_uri",
    "navigator_image",
    "make_roi_figure",
    "ROI_FIGURE_CONFIG",
    "parse_plotly_selection",
    "raw_plotly_selection",
    "selection_to_roi",
    "selection_dict_from_roi",
    "current_selection",
    "magnification_for",
    "DBContext",
    "bootstrap_db",
    "roi_from_db_row",
    "jump_viewport_for_bbox",
    "apply_display_pipeline",
]

# Fraction of extra frame rendered around the nominal viewport so plotly's
# native drag reveals real pixels instead of running off the bitmap edge.
# 1.5 = +25% of the viewport on every side for 2.25x the pixel work; measured
# at ~9.5 ms and ~0.8 MB per frame against ~70 ms / 2.4 MB for the old path.
DEFAULT_OVERSCAN = 1.5


def render_viewport(source: SlideSource, vp: ViewportState) -> "Image.Image":
    """Render the viewport: pick the best level for the viewport downsample,
    read the region there, resize to the viewport size."""
    level = best_level_for_downsample(source, vp.downsample)
    d = source.level_downsamples[level]
    factor = vp.downsample / d  # extra zoom applied client-side
    read_w = max(1, int(round(vp.size[0] * factor)))
    read_h = max(1, int(round(vp.size[1] * factor)))
    to_level0, _ = viewport_transform(vp)
    x0, y0 = to_level0((0.0, 0.0))
    img = source.read_region((int(round(x0)), int(round(y0))), level, (read_w, read_h))
    if img.size != vp.size:
        img = img.resize(vp.size, Image.LANCZOS)
    return img


def render_viewport_overscan(
    source: SlideSource, vp: ViewportState, *, overscan: float = DEFAULT_OVERSCAN
) -> tuple["Image.Image", tuple[int, int]]:
    """Render a frame ``overscan`` times larger than ``vp.size``, CENTERED on
    the same ``vp.center`` at the same ``vp.downsample``.

    Returns ``(img, (ox, oy))`` where ``(ox, oy)`` is the symmetric margin in
    VIEWPORT pixels: the image's top-left pixel sits at viewport coordinate
    ``(-ox, -oy)``. ``overscan <= 1.0`` returns exactly
    ``render_viewport(source, vp)`` and ``(0, 0)``.

    The margin is computed as an integer FIRST and the frame is then sized
    ``vp.size + 2 * margin``, rather than rounding a scaled size and splitting
    the leftover. That keeps the margin exactly symmetric, which is what makes
    the overscan frame a genuine ViewportState (same center, same downsample,
    larger size) -- see :func:`apply_display_pipeline`.
    """
    vw, vh = vp.size
    extra = max(0.0, float(overscan) - 1.0) / 2.0
    ox = int(round(vw * extra))
    oy = int(round(vh * extra))
    if ox <= 0 and oy <= 0:
        return render_viewport(source, vp), (0, 0)
    big = dc_replace(vp, size=(vw + 2 * ox, vh + 2 * oy))
    return render_viewport(source, big), (ox, oy)


def viewport_png_bytes(img: "Image.Image") -> bytes:
    """Encode a PIL image as LOSSLESS PNG bytes (for mo.image).

    Use this for anything that is kept: the navigator thumbnail, heatmap
    exports, saved patches. The on-screen viewer frame uses
    :func:`viewport_jpeg_bytes` instead.
    """
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def viewport_jpeg_bytes(
    img: "Image.Image", quality: int = 85, *, subsampling: int = 0
) -> bytes:
    """Encode the DISPLAY frame as JPEG bytes.

    Lossy, and deliberately so: PNG-encoding a 1024x768 viewport costs
    60-204 ms and ~1.0-1.5 MB, while JPEG q=85 costs ~6-9 ms and ~0.3-0.5 MB.

    ``subsampling=0`` (4:4:4, no chroma subsampling) because H&E nuclei at 40x
    are exactly the high-frequency colour detail that 4:2:0 destroys -- the
    saving is not worth softening what a pathologist is looking at.

    NEVER use this for agent payloads, patch extraction, ROI statistics, or
    anything written to the database. Those paths read unadjusted source
    pixels via ``hescope.core.rois.extract_patch``.
    """
    buf = io.BytesIO()
    img.convert("RGB").save(
        buf, format="JPEG", quality=int(quality), subsampling=int(subsampling)
    )
    return buf.getvalue()


def viewport_data_uri(img: "Image.Image", *, quality: int = 85) -> str:
    """``data:image/jpeg;base64,...`` for the display frame (see
    :func:`viewport_jpeg_bytes` for why JPEG and why display-only)."""
    raw = viewport_jpeg_bytes(img, quality)
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def navigator_image(
    source: SlideSource, vp: ViewportState, max_size: int = 256
) -> "Image.Image":
    """Thumbnail (max max_size px) with a red rectangle overlay marking the
    current viewport."""
    thumb = source.get_thumbnail((max_size, max_size)).convert("RGB")
    sx = thumb.width / source.dimensions[0]
    sy = thumb.height / source.dimensions[1]
    to_level0, _ = viewport_transform(vp)
    x0, y0 = to_level0((0.0, 0.0))
    x1, y1 = to_level0((float(vp.size[0]), float(vp.size[1])))
    draw = ImageDraw.Draw(thumb)
    draw.rectangle(
        [x0 * sx, y0 * sy, x1 * sx, y1 * sy],
        outline=(255, 0, 0),
        width=2,
    )
    return thumb


# Plotly config for the unified viewer: wheel zoom, no logo, no autoscale /
# reset buttons (server-side downsample + zoom-to-fit own resolution changes).
# Plotly does NOT serialize config into the figure JSON; ``mo.ui.plotly``
# accepts it as a separate argument, so make_roi_figure stashes it on the
# figure as ``fig._config`` for the caller to forward verbatim.
ROI_FIGURE_CONFIG = {
    "scrollZoom": True,
    "displaylogo": False,
    "modeBarButtonsToRemove": ["autoScale2d", "resetScale2d"],
}


def make_roi_figure(
    img: "Image.Image",
    dragmode: str = "select",
    *,
    uirevision: str = "constant",
    viewport_size: tuple[int, int] | None = None,
    quality: int = 85,
) -> "Any":
    """Build the unified-viewer Plotly figure for the viewport image.

    This is the ONE interactive surface of the app: wheel-zoom, pan and ROI
    drawing all happen on it.

    dragmode "pan"    -> native plotly panning (no selection),
    dragmode "select" -> box selection (value["range"]),
    dragmode "lasso"  -> freehand polygon (value["lasso"]/value["lasso_points"]).

    The axis coordinate system is ALWAYS "viewport pixels, (0, 0) at the
    NOMINAL viewport's top-left", whatever ``img`` actually contains. When
    ``img`` is an overscan frame from :func:`render_viewport_overscan`, pass
    the nominal ``viewport_size``: the image is then attached at
    ``x0 = -ox, y0 = -oy`` while the axis range stays pinned to the nominal
    viewport, so plotly's native drag reveals real pixels out to the overscan
    edge and a box dragged into that margin still maps to correct level-0
    coordinates through the unchanged
    parse_plotly_selection -> selection_to_roi chain (``viewport_transform``
    is a pure affine map with no clamping, so it extrapolates exactly).
    ``viewport_size`` defaults to ``img.size``, i.e. no overscan.

    The frame travels as a self-encoded JPEG data URI on a ``go.Image`` trace.
    ``px.imshow`` is deliberately NOT used: it emits a base64 PNG (measured
    1.4-2.2 MB of figure JSON per frame), and its ``binary_format="jpg"``
    escape hatch cannot reach q=85 because ``_plotly_utils/data_utils.py``
    passes ``compress_level`` (a PNG kwarg) to PIL's JPEG saver, silently
    pinning quality at PIL's default 75.

    Axes intentionally have no fixedrange: plotly wheel-zoom/pan work natively
    (visual only; data coordinates are unchanged). ``uirevision`` keeps client
    zoom across re-renders with the same revision. The plotly config is
    attached as ``fig._config`` (see ROI_FIGURE_CONFIG).
    """
    import plotly.graph_objects as go

    vw, vh = viewport_size if viewport_size is not None else img.size
    vw, vh = int(vw), int(vh)
    # Symmetric margin; floor-halved so an odd difference biases the image
    # right/down by one pixel rather than desynchronising the axes.
    ox = (img.width - vw) // 2
    oy = (img.height - vh) // 2
    fig = go.Figure(
        go.Image(
            source=viewport_data_uri(img, quality=quality),
            x0=-ox,
            y0=-oy,
            dx=1,
            dy=1,
            # px.imshow's default template reads %{z}, which does not exist
            # behind a `source`; blank it rather than show "color: [, , ]".
            hovertemplate="",
        )
    )
    fig.update_layout(
        dragmode=dragmode,
        uirevision=uirevision,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(
            range=[-0.5, vw - 0.5],
            showticklabels=False,
            showgrid=False,
            zeroline=False,
        ),
        yaxis=dict(
            range=[vh - 0.5, -0.5],
            showticklabels=False,
            showgrid=False,
            zeroline=False,
        ),
    )
    fig._config = dict(ROI_FIGURE_CONFIG)
    return fig


def parse_plotly_selection(value: dict | None) -> dict | None:
    """Normalize a mo.ui.plotly selection value.

    Returns {"kind": "box", "points": [(x0,y0),(x1,y1)]} for box selections or
    {"kind": "lasso", "points": [(x,y), ...]} for lasso selections, in
    viewport pixel coordinates. Returns None when there is no usable
    selection."""
    if not isinstance(value, dict) or not value:
        return None
    rng = value.get("range")
    if isinstance(rng, dict) and "x" in rng and "y" in rng:
        try:
            x0, x1 = float(rng["x"][0]), float(rng["x"][1])
            y0, y1 = float(rng["y"][0]), float(rng["y"][1])
        except (TypeError, ValueError, IndexError):
            return None
        if not all(math.isfinite(v) for v in (x0, x1, y0, y1)):
            return None
        return {
            "kind": "box",
            "points": [(min(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1))],
        }
    lasso = value.get("lasso") or value.get("lasso_points")
    if isinstance(lasso, dict) and "x" in lasso and "y" in lasso:
        # same guard as the box branch above: a null/non-numeric coordinate
        # or a non-iterable axis means "no usable selection", not a crash
        try:
            xs, ys = lasso["x"], lasso["y"]
            pts = [(float(x), float(y)) for x, y in zip(xs, ys)]
        except (TypeError, ValueError, IndexError):
            return None
        # Finiteness, NOT just parseability: float("nan") raises none of the
        # exceptions above, and a NaN coordinate rides all the way out through
        # get_current_selection() as a bare `NaN` token. AGENTS.md 2 makes
        # "the tools return JSON" a hard rule, and NaN is not JSON -- Python's
        # json.loads accepts it, so a Python agent never notices, while
        # JSON.parse in a JS agent throws. hescope.viewer.osdviewer._finite_points is
        # the same guard on the OpenSeadragon surface; this is the twin it
        # says it mirrors (R07-16).
        if not all(math.isfinite(x) and math.isfinite(y) for x, y in pts):
            return None
        if len(pts) >= 3:
            return {"kind": "lasso", "points": pts}
    return None


def raw_plotly_selection(element) -> dict | None:
    """Return the raw plotly selection dict from a mo.ui.plotly element.

    marimo 0.23 stores it on the private ``_selection_data`` attribute
    (``.value`` is the extracted-points list, which is empty for image-only
    figures). Falls back to ``.value`` when it is a non-empty dict (other
    marimo versions). A plain dict passed instead of an element is returned
    as-is when non-empty. Returns None when there is no usable selection."""
    if element is None:
        return None
    if isinstance(element, dict):
        return element if element else None
    sd = getattr(element, "_selection_data", None)
    if isinstance(sd, dict) and sd:
        return sd
    v = getattr(element, "value", None)
    if isinstance(v, dict) and v:
        return v
    return None


def selection_to_roi(
    selection: dict, vp: ViewportState, as_circle: bool = False
) -> ROI:
    """Map a parsed plotly selection (viewport px) to an ROI in level-0
    coordinates. A box selection with as_circle=True becomes a circle ROI
    (center + edge point derived from the box)."""
    to_level0, _ = viewport_transform(vp)
    if selection["kind"] == "box":
        (x0, y0), (x1, y1) = selection["points"]
        if as_circle:
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            r = min(x1 - x0, y1 - y0) / 2.0
            return ROI(
                kind="circle",
                points=(to_level0((cx, cy)), to_level0((cx + r, cy))),
            )
        return ROI(kind="rect", points=(to_level0((x0, y0)), to_level0((x1, y1))))
    pts = tuple(to_level0(p) for p in selection["points"])
    return ROI(kind="polygon", points=pts)


def selection_dict_from_roi(
    source: SlideSource | None, roi: ROI | None, downsample: float
) -> dict | None:
    """Build THE agent-contract selection dict from a level-0 ROI.

    This is the single source of truth for the 7-key shape documented in
    AGENTS.md 4.1. It is extracted from :func:`current_selection` so an
    alternative viewer front-end can reuse the exact contract without editing
    this module (and without a second copy of the key list drifting from this
    one). Returns None when there is no source or no ROI.

    Return shape::

        {"kind": "rect"|"polygon",
         "points_level0": [[x, y], ...],
         "bbox_level0": [x0, y0, x1, y1],
         "viewport_downsample": float,
         "slide": str,
         "slide_dimensions": [w, h],
         "mpp": float | None}
    """
    if source is None or roi is None:
        return None
    x0, y0, x1, y1 = roi.bbox()
    return {
        "kind": roi.kind,
        "points_level0": [[float(x), float(y)] for x, y in roi.points],
        "bbox_level0": [x0, y0, x1, y1],
        "viewport_downsample": float(downsample),
        "slide": source.name,
        "slide_dimensions": [int(source.dimensions[0]), int(source.dimensions[1])],
        "mpp": source.mpp,
    }


def current_selection(
    source: SlideSource | None, vp: ViewportState, plotly_value: dict | None
) -> dict | None:
    """Map the RAW plotly UI value (the live figure selection) to a level-0
    selection dict. Returns None when there is no selection or no source.

    Uses parse_plotly_selection + selection_to_roi + selection_dict_from_roi
    (all in this module). Return shape: see :func:`selection_dict_from_roi`.

    (The circle checkbox is a UI concern; the live tool reports the raw
    box/lasso geometry only.)
    """
    if source is None:
        return None
    sel = parse_plotly_selection(plotly_value)
    if sel is None:
        return None
    roi = selection_to_roi(sel, vp, as_circle=False)
    return selection_dict_from_roi(source, roi, vp.downsample)


# ---------------------------------------------------------------------------
# Phase-3 integration helpers (DB bootstrap, annotation wiring, display
# pipeline). These are the testable building blocks used by app.py.
# ---------------------------------------------------------------------------


@dataclass
class DBContext:
    """Result of bootstrapping the HE-Scope database layer.

    ``enabled`` is False in DB-free mode (engine creation / schema init
    failed); ``error`` then describes the failure and all repos are None.
    """

    engine: Any | None
    slide_repo: Any | None  # hescope.store.db.SlideRepo
    roi_repo: Any | None  # hescope.store.db.ROIRepo
    run_repo: Any | None  # hescope.store.db.AgentRunRepo
    error: str | None

    @property
    def enabled(self) -> bool:
        return self.engine is not None

    def trace(self, kind: str, **fields: Any) -> int | None:
        """Append one interaction-trace row; returns its id, or None.

        README/AGENTS.md promise that selection views, ROI submissions, label
        write-backs, analysis runs and tool calls all land in ``interactions``,
        but only the DB-backed AGENT tools ever wrote one: every human action
        in the notebook went untraced, so ``label_set`` recorded the agent
        writing a label and not the user typing it — backwards for the
        automation-bias question the table exists to answer. This is the
        notebook's side of that write.

        Same contract as ``InteractionRepo.record``: never raises, and it is a
        no-op in DB-free mode, so a UI handler can call it without a
        ``try``/``except`` of its own. Tracing must never be able to break the
        action it is tracing.
        """
        if self.engine is None:
            return None
        try:
            from ..store.db import InteractionRepo

            return InteractionRepo(self.engine).record(kind=kind, **fields)
        except Exception:
            return None


def bootstrap_db(url: str | None = None) -> DBContext:
    """Create the engine, initialize the schema and build the repositories.

    Never raises: any failure (bad URL, missing driver, unreachable server)
    returns a disabled DBContext so the app can run in DB-free mode.
    """
    from ..store.db import AgentRunRepo, ROIRepo, SlideRepo, get_engine, init_db

    try:
        engine = get_engine(url)
        init_db(engine)
    except Exception as exc:
        return DBContext(
            engine=None,
            slide_repo=None,
            roi_repo=None,
            run_repo=None,
            error=f"{type(exc).__name__}: {exc}",
        )
    return DBContext(
        engine=engine,
        slide_repo=SlideRepo(engine),
        roi_repo=ROIRepo(engine),
        run_repo=AgentRunRepo(engine),
        error=None,
    )


def roi_from_db_row(row: dict) -> ROI:
    """Reconstruct ROI geometry from a ROIRepo row dict (points_json)."""
    points = tuple(
        (float(p[0]), float(p[1])) for p in json.loads(row["points_json"])
    )
    return ROI(kind=row["kind"], points=points)


def jump_viewport_for_bbox(
    bbox: Iterable[float],
    vp_size: tuple[int, int],
    *,
    max_downsample: float,
    fill: float = 0.8,
) -> tuple[tuple[float, float], float]:
    """Compute (center, downsample) so that ``bbox`` (level-0 x0,y0,x1,y1)
    fills ~``fill`` of a viewport of ``vp_size`` pixels.

    The downsample is clamped to [1.0, max_downsample]."""
    x0, y0, x1, y1 = (float(v) for v in bbox)
    w = max(x1 - x0, 1.0)
    h = max(y1 - y0, 1.0)
    vw, vh = float(vp_size[0]), float(vp_size[1])
    ds = max(w / (vw * fill), h / (vh * fill))
    ds = min(max(ds, 1.0), max(float(max_downsample), 1.0))
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0), ds


def apply_display_pipeline(
    img: "Image.Image",
    vp: ViewportState,
    *,
    brightness: float = 1.0,
    contrast: float = 1.0,
    gamma: float = 1.0,
    channel: Literal["rgb", "r", "g", "b", "hematoxylin", "eosin"] = "rgb",
    rois: Iterable[ROI] = (),
    show_overlays: bool = True,
    selected_index: int | None = None,
) -> "Image.Image":
    """Viewport display pipeline applied AFTER read_region + resize:

    apply_adjustments -> channel_view -> draw_rois. Overlays are drawn last
    so ROI outlines stay crisp regardless of the adjustments.

    ``img`` may be an overscan frame (:func:`render_viewport_overscan`), i.e.
    larger than ``vp.size``. Overscan is symmetric about ``vp.center`` at the
    same downsample, so a frame of size ``img.size`` IS a valid ViewportState
    differing from ``vp`` only in ``size`` -- handing that to ``draw_rois``
    keeps every outline on its true level-0 position instead of shifting it
    by the overscan margin. With no overscan ``img.size == vp.size`` and the
    substitution is the identity, so it is applied unconditionally.
    """
    from .adjust import apply_adjustments, channel_view
    from .overlay import draw_rois

    out = apply_adjustments(
        img, brightness=brightness, contrast=contrast, gamma=gamma
    )
    out = channel_view(out, channel)
    roi_list = list(rois)
    if show_overlays and roi_list:
        out = draw_rois(
            out, roi_list, dc_replace(vp, size=out.size), selected_index=selected_index
        )
    return out
