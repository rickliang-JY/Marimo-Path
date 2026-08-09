"""Grid metric computation and matplotlib-free heatmap rendering
(SPEC-ML Part B.3).

``compute_grid`` evaluates a metric per tile over :func:`hescope.grid.iter_grid`
and returns a float array with NaN where tiles were skipped.
``render_heatmap`` colorizes such a grid with a hardcoded 256-entry viridis
LUT (exact published viridis data, stored zlib+base64 compressed) and
alpha-blends it onto a base image only where the grid is not NaN.
"""

from __future__ import annotations

import base64
import zlib
from typing import Callable

import numpy as np
from PIL import Image

from .grid import grid_shape, iter_grid
from .slides import SlideSource

# Exact 256x3 uint8 viridis LUT (published matplotlib viridis data sampled
# at 256 points), zlib-compressed and base64-encoded to keep the source
# compact. Decoded once at import; requires only numpy + stdlib.
_VIRIDIS_B64 = (
    "eNoBAAP//EQBVEQCVkUEV0UFWUYHWkYIXEYKXUYLXkcNYEcOYUcQY0cRZEcT"
    "ZUgUZ0gWaEgXaUgYakgabEgbbUgcbkgdb0gfcEggcUghc0gjdEgkdUgldkgm"
    "d0goeEgpeUcqekcsekcte0cufEcvfUYwfkYyfkYzf0Y0gEU1gUU3gUU4gkQ5"
    "g0Q6g0Q7hEM9hEM+hUI/hUJAhkJBhkFCh0FEh0BFiEBGiD9HiD9IiT5JiT5K"
    "iT5Mij1Nij1OijxPijxQiztRiztSizpTizpUjDlVjDlWjDhYjDhZjDdajDdb"
    "jTZcjTZdjTVejTVfjTRgjTRhjTNijTNjjTJkjjJljjFmjjFnjjFojjBpjjBq"
    "ji9rji9sji5tji5uji5vji1wji1xjixxjixyjixzjit0jit1jip2jip3jip4"
    "jil5jil6jil7jih8jih9jid+jid/jieAjiaBjiaCjiaCjiWDjiWEjiWFjiSG"
    "jiSHjiOIjiOJjiOKjSKLjSKMjSKNjSGOjSGPjSGQjSGRjCCSjCCSjCCTjB+U"
    "jB+Vix+Wix+Xix+Yix+Zih+aih6bih6ciR6diR+eiR+fiB+giB+hiB+hhx+i"
    "hyCjhiCkhiGlhSGmhSKnhSKohCOpgySqgyWrgiWsgiatgSetgSiugCmvfyqw"
    "fyyxfi2yfS6zfC+0fDG1ezK2ejS2eTW3eTe4eDi5dzq6dju7dT28dD+8c0C9"
    "ckK+cUS/cEbAb0jBbkrBbUzCbE7Da1DEalLFaVTFaFbGZ1jHZVrIZFzIY17J"
    "YmDKYGPLX2XLXmfMXGnNW2zNWm7OWHDPV3PQVnXQVHfRU3rRUXzSUH/TToHT"
    "TYTUS4bVSYnVSIvWRo7WRZDXQ5PXQZXYQJjYPpvZPJ3ZO6DaOaLaN6XbNqjb"
    "NKrcMq3cMLDdL7LdLbXeK7jeKbreKL3fJsDfJcLfI8XgIcjgIMrhH83hHdDh"
    "HNLiG9XiGtjiGdrjGd3jGN/jGOLkGOXkGefkGerlGuzlG+/lHPHlHfTmHvbm"
    "IPjmIfvnI/3nJRfoSno="
)


def _decode_viridis() -> np.ndarray:
    raw = zlib.decompress(base64.b64decode(_VIRIDIS_B64))
    return np.frombuffer(raw, dtype=np.uint8).reshape(256, 3).copy()


_VIRIDIS_LUT = _decode_viridis()


def get_colormap_lut(name: str = "viridis") -> np.ndarray:
    """Return the 256x3 uint8 LUT for a supported colormap name."""
    if name != "viridis":
        raise ValueError(f"unsupported colormap: {name!r} (only 'viridis')")
    return _VIRIDIS_LUT


def grid_bbox_to_level0(
    gx: int, gy: int, *, tile: int, downsample: float
) -> tuple[int, int, int, int]:
    """Exact level-0 bbox (x0, y0, x1, y1) of grid cell (gx, gy)."""
    cell = tile * downsample
    return (
        int(gx * cell),
        int(gy * cell),
        int((gx + 1) * cell),
        int((gy + 1) * cell),
    )


def compute_grid(
    source: SlideSource,
    metric_fn: Callable[["Image.Image"], float],
    *,
    tile: int = 256,
    downsample: float = 16.0,
    tissue_min: float = 0.05,
    max_tiles: int = 4000,
    progress_cb: Callable[[int, int], None] | None = None,
    error_cb: Callable[[int, int, BaseException], None] | None = None,
) -> np.ndarray:
    """Evaluate ``metric_fn(pil_tile) -> float`` over the slide grid.

    Returns a ``(rows, cols)`` float64 array matching
    :func:`hescope.grid.grid_shape`; cells skipped by the tissue filter (or
    beyond ``max_tiles``) are ``np.nan``. ``progress_cb(done, total)`` is
    called with ``total = rows * cols`` grid cells; ``done`` counts cells
    swept so far, and a final ``progress_cb(total, total)`` call is
    guaranteed at completion.

    ``error_cb(gx, gy, exc)`` is called for every tile whose metric raised.
    A tile that failed and a tile that was never attempted both end up as
    ``NaN``, and :func:`render_heatmap` leaves NaN cells untouched, so a
    sweep in which EVERY tile raised is byte-identical to the bare
    thumbnail: without this callback a caller cannot tell "no tissue here"
    from "the metric is broken" and reports total failure as a success.
    """
    rows, cols = grid_shape(source, tile=tile, downsample=downsample)
    total = rows * cols
    grid = np.full((rows, cols), np.nan, dtype=np.float64)

    last_done = 0
    for gx, gy, _bbox, pil_tile in iter_grid(
        source, tile=tile, downsample=downsample,
        tissue_min=tissue_min, max_tiles=max_tiles,
    ):
        try:
            grid[gy, gx] = float(metric_fn(pil_tile))
        except Exception as exc:
            grid[gy, gx] = np.nan
            if error_cb is not None:
                error_cb(gx, gy, exc)
        last_done = gy * cols + gx + 1
        if progress_cb is not None:
            progress_cb(last_done, total)
    if progress_cb is not None and last_done < total:
        progress_cb(total, total)
    return grid


def grid_coverage(
    slide_dimensions: tuple[int, int],
    grid_shape_rc: tuple[int, int],
    *,
    tile: int,
    downsample: float,
) -> tuple[float, float]:
    """How much of ``base_img`` a grid actually spans, as (fx, fy) >= 1.0.

    ``grid_shape`` rounds the cell count UP, so a grid of ``cols`` columns
    covers ``cols * tile * downsample`` level-0 pixels -- up to one whole
    cell MORE than the slide is wide. Pass the result to
    :func:`render_heatmap` as ``coverage``; without it the grid is stretched
    to the slide's extent and every cell is drawn ``fy`` too tall, which
    walks the bottom row off the tissue it was measured on.
    """
    rows, cols = int(grid_shape_rc[0]), int(grid_shape_rc[1])
    cell = float(tile) * float(downsample)
    w, h = float(slide_dimensions[0]), float(slide_dimensions[1])
    fx = (cols * cell / w) if w > 0 else 1.0
    fy = (rows * cell / h) if h > 0 else 1.0
    return (max(1.0, fx), max(1.0, fy))


def render_heatmap(
    base_img: "Image.Image",
    grid: np.ndarray,
    *,
    alpha: float = 0.45,
    colormap: str = "viridis",
    vmin: float | None = None,
    vmax: float | None = None,
    coverage: tuple[float, float] | None = None,
) -> "Image.Image":
    """Overlay a metric grid on ``base_img`` (returns a new RGB image).

    The grid is nearest-neighbor resized to the base image size, colorized
    via the hardcoded viridis LUT, and alpha-blended ONLY where the grid is
    not NaN (NaN cells leave the base image untouched). ``vmin``/``vmax``
    default to the nan-aware min/max of the grid; a degenerate (constant or
    all-NaN) range maps all valid cells to the top of the colormap.

    ``coverage`` is ``(fx, fy)``, the fraction of ``base_img`` the grid spans
    -- see :func:`grid_coverage`. It defaults to ``(1.0, 1.0)``, i.e. "the
    grid covers exactly the base image", which is only true when the slide
    divides evenly into cells. Anything else silently mis-registers the
    overlay against the tissue underneath it.
    """
    lut = get_colormap_lut(colormap)
    grid = np.asarray(grid, dtype=np.float64)
    if grid.ndim != 2:
        raise ValueError("grid must be a 2D array")

    base = base_img.convert("RGB")
    w, h = base.size

    valid = ~np.isnan(grid)
    if vmin is None:
        vmin = float(np.nanmin(grid)) if valid.any() else 0.0
    if vmax is None:
        vmax = float(np.nanmax(grid)) if valid.any() else 1.0
    span = vmax - vmin

    # Normalize to colormap indices [0, 255]; index content is irrelevant
    # where the cell is NaN (masked out below).
    if span > 0:
        idx = np.clip(np.round((grid - vmin) / span * 255.0), 0, 255)
    else:
        idx = np.where(valid, 255.0, 0.0)
    idx = np.nan_to_num(idx, nan=0.0)
    # Scale to the extent the grid actually covers, then crop back to the
    # base image: the grid's last row/column may hang off the slide.
    fx, fy = (1.0, 1.0) if coverage is None else (
        max(1.0, float(coverage[0])), max(1.0, float(coverage[1]))
    )
    full = (max(w, int(round(w * fx))), max(h, int(round(h * fy))))
    idx_img = Image.fromarray(idx.astype(np.uint8), "L").resize(full, Image.NEAREST)
    mask_img = Image.fromarray((valid * 255).astype(np.uint8), "L").resize(
        full, Image.NEAREST
    )
    if full != (w, h):
        idx_img = idx_img.crop((0, 0, w, h))
        mask_img = mask_img.crop((0, 0, w, h))

    idx_full = np.asarray(idx_img, dtype=np.int64)
    mask_full = np.asarray(mask_img, dtype=np.float64) / 255.0  # 1.0 where valid
    colors = lut[idx_full].astype(np.float64)  # (h, w, 3)

    base_arr = np.asarray(base, dtype=np.float64)
    a = (mask_full * float(alpha))[..., None]
    blended = base_arr * (1.0 - a) + colors * a
    out = np.clip(np.round(blended), 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB")
