"""Tests for hescope.heatmap (SPEC-ML Part B.3)."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from hescope.grid import grid_shape, tissue_fraction_proxy
from hescope.heatmap import (
    compute_grid,
    get_colormap_lut,
    grid_bbox_to_level0,
    render_heatmap,
)
from hescope.slides import open_slide

TILE = 128
DS = 4.0
CELL = int(TILE * DS)


def _make_slide(tmp_path, size=(1024, 768), tissue_cells=((0, 0), (0, 1))):
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for gx, gy in tissue_cells:
        draw.rectangle(
            [gx * CELL + 16, gy * CELL + 16, (gx + 1) * CELL - 16, (gy + 1) * CELL - 16],
            fill=(120, 40, 140),
        )
    path = tmp_path / "slide.png"
    img.save(path)
    return open_slide(path)


def test_grid_bbox_to_level0_exact():
    assert grid_bbox_to_level0(0, 0, tile=128, downsample=4.0) == (0, 0, 512, 512)
    assert grid_bbox_to_level0(2, 3, tile=128, downsample=4.0) == (
        1024,
        1536,
        1536,
        2048,
    )
    assert grid_bbox_to_level0(1, 1, tile=256, downsample=16.0) == (
        4096,
        4096,
        8192,
        8192,
    )


def test_compute_grid_shape_and_nan(tmp_path):
    src = _make_slide(tmp_path, tissue_cells=((0, 0), (0, 1)))
    grid = compute_grid(src, tissue_fraction_proxy, tile=TILE, downsample=DS)
    assert grid.shape == grid_shape(src, tile=TILE, downsample=DS) == (2, 2)
    # tissue cells have a finite metric; white cells were skipped -> NaN
    assert np.isfinite(grid[0, 0]) and np.isfinite(grid[1, 0])
    assert grid[0, 0] > 0.5
    assert np.isnan(grid[0, 1]) and np.isnan(grid[1, 1])


def test_compute_grid_progress_callback(tmp_path):
    src = _make_slide(tmp_path)
    calls = []
    grid = compute_grid(
        src,
        tissue_fraction_proxy,
        tile=TILE,
        downsample=DS,
        tissue_min=0.0,
        progress_cb=lambda done, total: calls.append((done, total)),
    )
    total = int(np.prod(grid.shape))
    assert calls, "progress callback was never called"
    assert all(t == total for _d, t in calls)
    assert calls[-1] == (total, total)
    dones = [d for d, _t in calls]
    assert dones == sorted(dones)


def test_compute_grid_max_tiles_leaves_nan(tmp_path):
    src = _make_slide(tmp_path)
    grid = compute_grid(
        src, tissue_fraction_proxy, tile=TILE, downsample=DS,
        tissue_min=0.0, max_tiles=1,
    )
    assert np.isfinite(grid[0, 0])
    assert np.isnan(grid[1, 1])


def test_lut_shape_and_endpoints():
    lut = get_colormap_lut("viridis")
    assert lut.shape == (256, 3)
    assert lut.dtype == np.uint8
    # published viridis endpoints
    assert tuple(lut[0]) == (68, 1, 84)
    assert tuple(lut[255]) == (253, 231, 37)
    with pytest.raises(ValueError):
        get_colormap_lut("jet")


def test_render_heatmap_size_alpha_and_nan_transparency():
    base = Image.new("RGB", (64, 48), (10, 20, 30))
    grid = np.array([[0.0, 1.0], [np.nan, 0.5]])

    out = render_heatmap(base, grid, alpha=1.0)
    assert out.size == base.size
    arr = np.asarray(out)
    lut = get_colormap_lut("viridis")
    # alpha=1: valid quadrants are pure LUT colors (nearest resize -> exact)
    assert tuple(arr[12, 16]) == tuple(lut[0])    # value 0 -> vmin
    assert tuple(arr[12, 48]) == tuple(lut[255])  # value 1 -> vmax
    # NaN quadrant stays transparent even at alpha=1
    assert tuple(arr[36, 16]) == (10, 20, 30)

    # alpha=0: output identical to base everywhere
    out0 = render_heatmap(base, grid, alpha=0.0)
    assert np.array_equal(np.asarray(out0), np.asarray(base))

    # alpha blend math on a valid quadrant
    out_half = render_heatmap(base, grid, alpha=0.5)
    expected = 0.5 * np.array([10, 20, 30]) + 0.5 * lut[0].astype(float)
    assert np.allclose(np.asarray(out_half)[12, 16], expected, atol=1)


def test_render_heatmap_all_nan_returns_base_copy():
    base = Image.new("RGB", (32, 32), (200, 100, 50))
    grid = np.full((3, 3), np.nan)
    out = render_heatmap(base, grid, alpha=0.45)
    assert out.size == base.size
    assert np.array_equal(np.asarray(out), np.asarray(base))
    assert out is not base  # new image


def test_render_heatmap_explicit_vmin_vmax():
    base = Image.new("RGB", (32, 32), (0, 0, 0))
    lut = get_colormap_lut("viridis")
    grid = np.array([[5.0]])
    out = render_heatmap(base, grid, alpha=1.0, vmin=0.0, vmax=10.0)
    assert tuple(np.asarray(out)[16, 16]) == tuple(lut[128])
