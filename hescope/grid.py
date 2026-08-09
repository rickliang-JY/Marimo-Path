"""Regular tile grid sweep over a slide (SPEC-ML Part B.2).

``iter_grid`` walks the slide at a target downsample, reading tiles from the
best pyramid level and skipping background tiles via a fast luminance proxy
(no skimage). ``grid_shape`` gives the (rows, cols) of the full grid so
callers can pre-allocate heatmap arrays.

Grid origin (0, 0) is the top-left of the slide. Each grid cell covers
``tile * downsample`` level-0 pixels in both axes, so the exact level-0
bounding box of cell (gx, gy) is::

    (gx * tile * downsample, gy * tile * downsample,
     (gx + 1) * tile * downsample, (gy + 1) * tile * downsample)

(border cells may extend past the slide edge; reads are clipped/padded by
the slide source).
"""

from __future__ import annotations

import math
from typing import Iterator

import numpy as np
from PIL import Image

from .slides import SlideSource, best_level_for_downsample

# Rec. 709 luma coefficients, matching hescope.rois.roi_stats.
_LUMA = (0.2126, 0.7152, 0.0722)
_LUMA_THRESHOLD = 0.9 * 255.0

# Side length of the thumbnail used for the fast tissue proxy.
_PROXY_THUMB = 32


def tissue_fraction_proxy(tile: "Image.Image") -> float:
    """Fast tissue proxy: fraction of pixels with luminance < 0.9 * 255,
    computed on a small (<= 32 px) bilinear thumbnail of the tile."""
    thumb = tile.convert("RGB")
    w, h = thumb.size
    if max(w, h) > _PROXY_THUMB:
        scale = _PROXY_THUMB / max(w, h)
        thumb = thumb.resize(
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            Image.BILINEAR,
        )
    arr = np.asarray(thumb, dtype=np.float32)
    lum = arr[..., 0] * _LUMA[0] + arr[..., 1] * _LUMA[1] + arr[..., 2] * _LUMA[2]
    return float((lum < _LUMA_THRESHOLD).mean()) if lum.size else 0.0


def grid_shape(
    source: SlideSource, *, tile: int = 256, downsample: float = 16.0
) -> tuple[int, int]:
    """(rows, cols) of the full tile grid at the given tile/downsample."""
    cell = tile * downsample
    w, h = source.dimensions
    cols = max(1, int(math.ceil(w / cell)))
    rows = max(1, int(math.ceil(h / cell)))
    return rows, cols


def iter_grid(
    source: SlideSource,
    *,
    tile: int = 256,
    downsample: float = 16.0,
    tissue_min: float = 0.05,
    max_tiles: int = 4000,
) -> Iterator[tuple[int, int, tuple[int, int, int, int], "Image.Image"]]:
    """Sweep the slide at ``downsample``, yielding tissue-bearing tiles.

    Yields ``(gx, gy, bbox_level0, pil_tile)`` in row-major order where
    ``bbox_level0 = (x0, y0, x1, y1)`` is exact (``gx * tile * downsample``
    etc.; border cells may extend past the slide edge) and ``pil_tile`` is an
    RGB image of exactly ``tile`` x ``tile`` pixels read from the best
    pyramid level for ``downsample``.

    Tiles whose fast luminance tissue fraction is below ``tissue_min`` are
    skipped. At most ``max_tiles`` tiles are yielded (a safety cap for huge
    slides); the cap is documented and deterministic (row-major order).
    """
    level = best_level_for_downsample(source, downsample)
    level_ds = float(source.level_downsamples[level])
    # Pixels to read at `level` to cover one grid cell, then resize to tile.
    read_size = max(tile, int(round(tile * downsample / level_ds)))

    rows, cols = grid_shape(source, tile=tile, downsample=downsample)
    cell = tile * downsample

    yielded = 0
    for gy in range(rows):
        if yielded >= max_tiles:
            break
        for gx in range(cols):
            if yielded >= max_tiles:
                break
            x0 = int(gx * cell)
            y0 = int(gy * cell)
            x1 = int((gx + 1) * cell)
            y1 = int((gy + 1) * cell)
            region = source.read_region((x0, y0), level, (read_size, read_size))
            if region.size != (tile, tile):
                region = region.resize((tile, tile), Image.BILINEAR)
            if tissue_fraction_proxy(region) < tissue_min:
                continue
            yield gx, gy, (x0, y0, x1, y1), region
            yielded += 1
