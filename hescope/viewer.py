"""Reusable marimo UI building blocks used by app.py.

Rendering is matplotlib-free: the viewport is rendered as a PIL image,
encoded to PNG bytes and shown with ``mo.image``; the ROI capture surface is
a Plotly figure shown via ``mo.ui.plotly``.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any, Iterable, Literal

import numpy as np
from PIL import Image, ImageDraw

from .agent_bridge import magnification_for  # re-exported for app convenience
from .rois import ROI, ViewportState, viewport_transform
from .slides import SlideSource, best_level_for_downsample

__all__ = [
    "render_viewport",
    "viewport_png_bytes",
    "navigator_image",
    "make_roi_figure",
    "ROI_FIGURE_CONFIG",
    "parse_plotly_selection",
    "raw_plotly_selection",
    "selection_to_roi",
    "current_selection",
    "magnification_for",
    "DBContext",
    "bootstrap_db",
    "roi_from_db_row",
    "jump_viewport_for_bbox",
    "apply_display_pipeline",
]


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


def viewport_png_bytes(img: "Image.Image") -> bytes:
    """Encode a PIL image as PNG bytes (for mo.image)."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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
    img: "Image.Image", dragmode: str = "select", *, uirevision: str = "constant"
) -> "Any":
    """Build the unified-viewer Plotly figure for the viewport image.

    This is the ONE interactive surface of the app: wheel-zoom, pan and ROI
    drawing all happen on it.

    dragmode "pan"    -> native plotly panning (no selection),
    dragmode "select" -> box selection (value["range"]),
    dragmode "lasso"  -> freehand polygon (value["lasso"]/value["lasso_points"]).
    Axes are in viewport pixel coordinates with explicit extents matching the
    image size, so selection coordinates map 1:1 to viewport pixels regardless
    of client-side zoom. Axes intentionally have no fixedrange: plotly
    wheel-zoom/pan work natively (visual only; data coordinates are unchanged).
    ``uirevision`` keeps client zoom across re-renders with the same revision.
    The plotly config is attached as ``fig._config`` (see ROI_FIGURE_CONFIG).
    """
    import plotly.express as px

    arr = np.asarray(img.convert("RGB"))
    height, width = arr.shape[0], arr.shape[1]
    fig = px.imshow(arr)
    fig.update_layout(
        dragmode=dragmode,
        uirevision=uirevision,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(
            range=[-0.5, width - 0.5],
            showticklabels=False,
            showgrid=False,
            zeroline=False,
        ),
        yaxis=dict(
            range=[height - 0.5, -0.5],
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
        return {
            "kind": "box",
            "points": [(min(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1))],
        }
    lasso = value.get("lasso") or value.get("lasso_points")
    if isinstance(lasso, dict) and "x" in lasso and "y" in lasso:
        xs, ys = lasso["x"], lasso["y"]
        pts = [(float(x), float(y)) for x, y in zip(xs, ys)]
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


def current_selection(
    source: SlideSource | None, vp: ViewportState, plotly_value: dict | None
) -> dict | None:
    """Map the RAW plotly UI value (the live figure selection) to a level-0
    selection dict. Returns None when there is no selection or no source.

    Uses parse_plotly_selection + selection_to_roi (both in this module).
    Return shape::

        {"kind": "rect"|"polygon",
         "points_level0": [[x, y], ...],
         "bbox_level0": [x0, y0, x1, y1],
         "viewport_downsample": float,
         "slide": str,
         "slide_dimensions": [w, h],
         "mpp": float | None}

    (The circle checkbox is a UI concern; the live tool reports the raw
    box/lasso geometry only.)
    """
    if source is None:
        return None
    sel = parse_plotly_selection(plotly_value)
    if sel is None:
        return None
    roi = selection_to_roi(sel, vp, as_circle=False)
    x0, y0, x1, y1 = roi.bbox()
    return {
        "kind": roi.kind,
        "points_level0": [[float(x), float(y)] for x, y in roi.points],
        "bbox_level0": [x0, y0, x1, y1],
        "viewport_downsample": float(vp.downsample),
        "slide": source.name,
        "slide_dimensions": [int(source.dimensions[0]), int(source.dimensions[1])],
        "mpp": source.mpp,
    }


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
    slide_repo: Any | None  # hescope.db.SlideRepo
    roi_repo: Any | None  # hescope.db.ROIRepo
    run_repo: Any | None  # hescope.db.AgentRunRepo
    error: str | None

    @property
    def enabled(self) -> bool:
        return self.engine is not None


def bootstrap_db(url: str | None = None) -> DBContext:
    """Create the engine, initialize the schema and build the repositories.

    Never raises: any failure (bad URL, missing driver, unreachable server)
    returns a disabled DBContext so the app can run in DB-free mode.
    """
    from .db import AgentRunRepo, ROIRepo, SlideRepo, get_engine, init_db

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
    """
    from .adjust import apply_adjustments, channel_view
    from .overlay import draw_rois

    out = apply_adjustments(
        img, brightness=brightness, contrast=contrast, gamma=gamma
    )
    out = channel_view(out, channel)
    roi_list = list(rois)
    if show_overlays and roi_list:
        out = draw_rois(out, roi_list, vp, selected_index=selected_index)
    return out
