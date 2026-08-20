"""ROI outline and navigator marker drawing for HE-Scope viewports.

All drawing happens on a copy of the input image; caller images are never
mutated. Level-0 ROI coordinates are mapped into viewport pixel space with
``hescope.core.rois.viewport_transform``.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from ..core.rois import ROI, ViewportState, viewport_transform

_LOG = logging.getLogger(__name__)

# Candidate physical lengths for the viewport scale bar.
SCALE_BAR_CANDIDATES_UM: tuple[float, ...] = (20, 50, 100, 200, 500, 1000)


def pick_scale_bar_um(
    mpp: float,
    viewport_downsample: float,
    image_width: int,
    candidates: Iterable[float] = SCALE_BAR_CANDIDATES_UM,
) -> float:
    """Pick the candidate µm length whose on-screen width is closest to
    1/5 of the image width (the classic pathology scale-bar size).

    A bar of ``c`` µm spans ``c / (mpp * viewport_downsample)`` viewport
    pixels.
    """
    um_per_px = mpp * viewport_downsample
    target_px = image_width / 5.0
    return min(candidates, key=lambda c: abs(c / um_per_px - target_px))


def _scale_bar_font_and_label(um: float):
    """Return (font, label) for the scale-bar caption.

    Neither of PIL's own fonts has a MICRO SIGN (µ) glyph: the classic
    bitmap font is ASCII-only, and Pillow's ``ImageFont.load_default()``
    fallback (a bundled "Aileron" TrueType font, "with a more limited
    character set" per its own docstring) renders µ as a tofu box too --
    verified by comparing its rendered mask for chr(181) against a codepoint
    guaranteed absent from the font: identical bitmaps. So a real µ needs a
    font from elsewhere; prefer the bundled DejaVuSans that ships inside
    matplotlib (an optional, NOT a core, dependency -- see the ``fonts``
    extra in pyproject.toml) which does have the glyph; fall back to the
    ASCII-safe "um" label with the default font when matplotlib is not
    installed or its bundled font cannot be found/loaded.

    Each fallback branch is a deliberate, logged degradation, not a silently
    swallowed error: only the two specific failure modes below are caught,
    so a genuine bug (e.g. a future refactor breaking this function some
    other way) still raises instead of quietly producing an "um" label.
    """
    try:  # matplotlib bundles DejaVuSans.ttf; optional dependency
        import matplotlib
    except ModuleNotFoundError:
        _LOG.debug(
            "matplotlib not installed; scale bar falls back to the ASCII "
            "'um' label instead of 'µm' (install the 'fonts' extra for a "
            "proper micro-sign glyph)"
        )
        return ImageFont.load_default(), f"{um:g} um"

    ttf = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf" / "DejaVuSans.ttf"
    if not ttf.exists():
        _LOG.warning(
            "matplotlib is installed but its bundled DejaVuSans.ttf was not "
            "found at %s; scale bar falls back to the ASCII 'um' label",
            ttf,
        )
        return ImageFont.load_default(), f"{um:g} um"

    try:
        font = ImageFont.truetype(str(ttf), 14)
    except OSError as exc:
        _LOG.warning(
            "matplotlib's bundled DejaVuSans.ttf at %s could not be loaded "
            "as a font (%s); scale bar falls back to the ASCII 'um' label",
            ttf, exc,
        )
        return ImageFont.load_default(), f"{um:g} um"

    return font, f"{um:g} µm"


def draw_scale_bar(
    img: Image.Image,
    mpp: float | None,
    viewport_downsample: float,
    *,
    candidates: Iterable[float] = SCALE_BAR_CANDIDATES_UM,
) -> Image.Image:
    """Return a NEW image with a scale bar drawn in the bottom-right corner.

    White background plate + black bar + black label with a white outline
    (``stroke_width``) so it stays readable on any tissue color. Returns a
    plain copy (no drawing) when ``mpp`` is None / non-positive or the
    downsample is non-positive.
    """
    out = img.convert("RGB").copy()
    if mpp is None or mpp <= 0 or viewport_downsample <= 0:
        return out
    w, h = out.size
    um = pick_scale_bar_um(mpp, viewport_downsample, w, candidates)
    um_per_px = mpp * viewport_downsample
    bar_px = max(1, int(round(um / um_per_px)))

    margin = max(8, min(w, h) // 40)
    bar_h = max(3, h // 150)
    font, label = _scale_bar_font_and_label(um)

    draw = ImageDraw.Draw(out)
    tb = draw.textbbox((0, 0), label, font=font, stroke_width=1)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = max(3, bar_h)

    plate_w = max(bar_px, tw) + 2 * pad
    plate_h = th + bar_h + 3 * pad  # label row + gap + bar row
    x1 = w - margin
    y1 = h - margin
    x0 = x1 - plate_w
    y0 = y1 - plate_h
    # White plate (slightly transparent feel via solid white — always opaque
    # so text never collides with tissue texture underneath).
    draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255))

    bar_x1 = x1 - pad
    bar_x0 = bar_x1 - bar_px
    bar_y1 = y1 - pad
    bar_y0 = bar_y1 - bar_h
    draw.rectangle([bar_x0, bar_y0, bar_x1, bar_y1], fill=(0, 0, 0))

    label_x = x1 - pad - tw
    label_y = y0 + pad - tb[1]
    draw.text(
        (label_x, label_y),
        label,
        font=font,
        fill=(0, 0, 0),
        stroke_width=1,
        stroke_fill=(255, 255, 255),
    )
    return out


def _viewport_bbox(roi: ROI, to_viewport) -> tuple[float, float, float, float]:
    """ROI bbox mapped to viewport pixel space (floats, may be out of range)."""
    x0, y0, x1, y1 = roi.bbox()
    if roi.kind == "circle":
        (cx, cy), (ex, ey) = roi.points
        r = math.hypot(ex - cx, ey - cy)
        x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r
    else:
        xs = [p[0] for p in roi.points]
        ys = [p[1] for p in roi.points]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    vx0, vy0 = to_viewport((x0, y0))
    vx1, vy1 = to_viewport((x1, y1))
    return (vx0, vy0, vx1, vy1)


def _fully_outside(bbox: tuple[float, float, float, float], w: int, h: int) -> bool:
    x0, y0, x1, y1 = bbox
    return x1 < 0 or y1 < 0 or x0 >= w or y0 >= h


def draw_rois(
    viewport_img: Image.Image,
    rois: Iterable[ROI],
    vp: ViewportState,
    *,
    color: tuple[int, int, int] = (255, 60, 60),
    width: int = 2,
    selected_index: int | None = None,
    selected_color: tuple[int, int, int] = (60, 200, 60),
) -> Image.Image:
    """Return a NEW image with each ROI outline drawn in viewport pixel space.

    rect -> rectangle outline; polygon -> closed polyline; circle -> ellipse
    derived from center + radius (radius = distance between the two stored
    points). ROIs fully outside the viewport are skipped (no error).
    """
    out = viewport_img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    _, to_viewport = viewport_transform(vp)
    vw, vh = vp.size

    for idx, roi in enumerate(rois):
        vbbox = _viewport_bbox(roi, to_viewport)
        if _fully_outside(vbbox, vw, vh):
            continue
        c = selected_color if (selected_index is not None and idx == selected_index) else color

        if roi.kind == "rect":
            (x0, y0), (x1, y1) = roi.points
            px0, py0 = to_viewport((min(x0, x1), min(y0, y1)))
            px1, py1 = to_viewport((max(x0, x1), max(y0, y1)))
            draw.rectangle([px0, py0, px1, py1], outline=c, width=width)
        elif roi.kind == "polygon":
            pts = [to_viewport(p) for p in roi.points]
            if len(pts) >= 2:
                pts = pts + [pts[0]]  # close the polyline
                draw.line(pts, fill=c, width=width, joint="curve")
        elif roi.kind == "circle":
            (cx, cy), (ex, ey) = roi.points
            r = math.hypot(ex - cx, ey - cy)
            vcx, vcy = to_viewport((cx, cy))
            vr = r / vp.downsample
            draw.ellipse(
                [vcx - vr, vcy - vr, vcx + vr, vcy + vr], outline=c, width=width
            )
        else:  # unknown kind: draw its bbox as a fallback outline
            draw.rectangle(list(vbbox), outline=c, width=width)

    return out


def draw_navigator_markers(
    thumbnail: Image.Image,
    rois: Iterable[ROI],
    slide_dimensions: tuple[int, int],
    *,
    color: tuple[int, int, int] = (255, 60, 60),
) -> Image.Image:
    """Scale ROI bboxes onto the thumbnail and draw small rectangles.

    Returns a NEW image; the input thumbnail is not modified.
    """
    out = thumbnail.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    sw, sh = slide_dimensions
    tw, th = thumbnail.size
    sx = tw / float(sw) if sw else 1.0
    sy = th / float(sh) if sh else 1.0

    for roi in rois:
        if roi.kind == "circle":
            (cx, cy), (ex, ey) = roi.points
            r = math.hypot(ex - cx, ey - cy)
            x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r
        else:
            xs = [p[0] for p in roi.points]
            ys = [p[1] for p in roi.points]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        box = [x0 * sx, y0 * sy, x1 * sx, y1 * sy]
        # clip to thumbnail bounds
        box[0] = min(max(box[0], 0), tw)
        box[1] = min(max(box[1], 0), th)
        box[2] = min(max(box[2], 0), tw)
        box[3] = min(max(box[3], 0), th)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        draw.rectangle(box, outline=color, width=1)

    return out
