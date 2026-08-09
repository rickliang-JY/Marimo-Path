"""ROI geometry, patch extraction and ROI statistics for HE-Scope."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Literal

import numpy as np
from PIL import Image, ImageDraw

from .slides import SlideSource, best_level_for_downsample


@dataclass(frozen=True)
class ViewportState:
    center: tuple[float, float]  # level-0 coords of viewport center
    downsample: float  # level-0 px per viewport px
    size: tuple[int, int]  # viewport pixel size (w, h)


@dataclass(frozen=True)
class ROI:
    kind: Literal["rect", "polygon", "circle"]
    # level-0 coords; rect: 2 corners; circle: (center, point_on_edge)
    points: tuple[tuple[float, float], ...]

    def bbox(self) -> tuple[int, int, int, int]:
        """Return (x0, y0, x1, y1) as clipped ints (lower bound clipped at 0)."""
        if self.kind == "circle":
            (cx, cy), (ex, ey) = self.points
            r = math.hypot(ex - cx, ey - cy)
            x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r
        else:
            xs = [p[0] for p in self.points]
            ys = [p[1] for p in self.points]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        return (
            max(0, int(math.floor(x0))),
            max(0, int(math.floor(y0))),
            max(0, int(math.ceil(x1))),
            max(0, int(math.ceil(y1))),
        )

    def mask(self, size: tuple[int, int], offset: tuple[float, float]) -> "np.ndarray":
        """Boolean mask of shape (h, w). Pixel (px, py) maps to level-0
        coordinate offset + (px, py)."""
        w, h = size
        ox, oy = offset
        if self.kind == "circle":
            (cx, cy), (ex, ey) = self.points
            r = math.hypot(ex - cx, ey - cy)
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
            xx += ox
            yy += oy
            return (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        img = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(img)
        pts = [(px - ox, py - oy) for px, py in self.points]
        if self.kind == "rect":
            (x0, y0), (x1, y1) = self.points
            pts = [
                (min(x0, x1) - ox, min(y0, y1) - oy),
                (max(x0, x1) - ox, max(y0, y1) - oy),
            ]
            draw.rectangle(pts, fill=1)
        else:  # polygon
            draw.polygon(pts, fill=1)
        return np.asarray(img, dtype=bool)


def viewport_transform(vp: ViewportState) -> tuple[Callable, Callable]:
    """Return (viewport_px -> level0), (level0 -> viewport_px) callables.

    level0 = offset + px * downsample, offset = center - (size/2) * downsample.
    """
    ox = vp.center[0] - (vp.size[0] / 2.0) * vp.downsample
    oy = vp.center[1] - (vp.size[1] / 2.0) * vp.downsample

    def to_level0(p: tuple[float, float]) -> tuple[float, float]:
        return (ox + p[0] * vp.downsample, oy + p[1] * vp.downsample)

    def to_viewport(p: tuple[float, float]) -> tuple[float, float]:
        return ((p[0] - ox) / vp.downsample, (p[1] - oy) / vp.downsample)

    return to_level0, to_viewport


def extract_patch(
    source: SlideSource, roi: ROI, max_size: int = 1024
) -> "Image.Image":
    """Crop the bbox of ``roi`` from the best pyramid level and scale the
    result to <= max_size while keeping aspect."""
    x0, y0, x1, y1 = roi.bbox()
    w = max(1, x1 - x0)
    h = max(1, y1 - y0)
    target_ds = max(1.0, max(w, h) / float(max_size))
    level = best_level_for_downsample(source, target_ds)
    d = source.level_downsamples[level]
    read_size = (max(1, int(math.ceil(w / d))), max(1, int(math.ceil(h / d))))
    patch = source.read_region((x0, y0), level, read_size)
    if max(patch.size) > max_size:
        patch = patch.copy()
        patch.thumbnail((max_size, max_size), Image.LANCZOS)
    return patch


def roi_stats(patch: "Image.Image", roi: ROI | None = None) -> dict:
    """Statistics for an ROI patch image.

    Keys: width_px, height_px, mean_rgb, he_deconvolution
    {hematoxylin_mean, eosin_mean} (skimage rgb2hed), tissue_fraction
    (fraction of pixels with luminance < 0.9*255). H/E means are computed
    over tissue pixels, falling back to all pixels if there are none."""
    from skimage.color import rgb2hed

    arr = np.asarray(patch.convert("RGB"), dtype=np.float64)
    h, w = arr.shape[:2]
    lum = 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]
    tissue = lum < 0.9 * 255.0
    tissue_fraction = float(tissue.mean()) if tissue.size else 0.0
    sel = tissue if tissue.any() else np.ones((h, w), dtype=bool)

    mean_rgb = [float(arr[..., c][sel].mean()) for c in range(3)]

    hed = rgb2hed(arr / 255.0)
    hematoxylin_mean = float(hed[..., 0][sel].mean())
    eosin_mean = float(hed[..., 1][sel].mean())

    return {
        "width_px": int(w),
        "height_px": int(h),
        "mean_rgb": mean_rgb,
        "he_deconvolution": {
            "hematoxylin_mean": hematoxylin_mean,
            "eosin_mean": eosin_mean,
        },
        "tissue_fraction": tissue_fraction,
    }
