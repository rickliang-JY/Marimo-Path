"""Tests for hescope.analysis.grid (SPEC-ML Part B.2)."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from hescope.analysis.grid import grid_shape, iter_grid, tissue_fraction_proxy
from hescope.wsi.slides import open_slide

TILE = 128
DS = 4.0
CELL = int(TILE * DS)  # 512 level-0 px per cell


def _make_slide(tmp_path, size=(1024, 768), tissue_cells=((0, 0), (0, 1))):
    """White slide with dark 'tissue' rectangles inside the given grid cells."""
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for gx, gy in tissue_cells:
        x0, y0 = gx * CELL + 16, gy * CELL + 16
        x1, y1 = (gx + 1) * CELL - 16, (gy + 1) * CELL - 16
        draw.rectangle([x0, y0, x1, y1], fill=(120, 40, 140))
    path = tmp_path / "slide.png"
    img.save(path)
    return open_slide(path)


def test_grid_shape_exact(tmp_path):
    src = _make_slide(tmp_path)
    assert grid_shape(src, tile=TILE, downsample=DS) == (2, 2)
    # 1024/512 = 2 cols, 768/512 -> ceil = 2 rows
    assert grid_shape(src, tile=256, downsample=2.0) == (2, 2)
    assert grid_shape(src, tile=100, downsample=1.0) == (8, 11)  # ceil math


def test_iter_grid_skips_background_and_bbox_exact(tmp_path):
    src = _make_slide(tmp_path, tissue_cells=((0, 0), (0, 1)))
    tiles = list(iter_grid(src, tile=TILE, downsample=DS, tissue_min=0.05))
    coords = [(gx, gy) for gx, gy, _bbox, _t in tiles]
    assert coords == [(0, 0), (0, 1)]  # column 1 is pure white -> skipped
    for gx, gy, bbox, tile_img in tiles:
        # exact level-0 mapping: gx*tile*downsample etc.
        assert bbox == (gx * CELL, gy * CELL, (gx + 1) * CELL, (gy + 1) * CELL)
        assert tile_img.size == (TILE, TILE)
        assert tile_img.mode == "RGB"


def test_iter_grid_tissue_min_zero_yields_all_row_major(tmp_path):
    src = _make_slide(tmp_path)
    tiles = list(iter_grid(src, tile=TILE, downsample=DS, tissue_min=0.0))
    assert [(gx, gy) for gx, gy, _b, _t in tiles] == [
        (0, 0),
        (1, 0),
        (0, 1),
        (1, 1),
    ]


def test_iter_grid_max_tiles_cap(tmp_path):
    src = _make_slide(tmp_path)
    tiles = list(
        iter_grid(src, tile=TILE, downsample=DS, tissue_min=0.0, max_tiles=3)
    )
    assert len(tiles) == 3
    assert [(gx, gy) for gx, gy, _b, _t in tiles] == [(0, 0), (1, 0), (0, 1)]


def test_tissue_fraction_proxy():
    white = Image.new("RGB", (64, 64), (255, 255, 255))
    dark = Image.new("RGB", (64, 64), (100, 30, 120))
    assert tissue_fraction_proxy(white) == pytest.approx(0.0)
    assert tissue_fraction_proxy(dark) == pytest.approx(1.0)
    half = Image.new("RGB", (64, 64), (255, 255, 255))
    ImageDraw.Draw(half).rectangle([0, 0, 31, 63], fill=(100, 30, 120))
    assert tissue_fraction_proxy(half) == pytest.approx(0.5, abs=0.05)


def test_iter_grid_reads_best_level(tmp_path, monkeypatch):
    """Tiles must be read via source.read_region at the best pyramid level."""
    src = _make_slide(tmp_path)
    calls = []
    orig = src.read_region

    def spy(location, level, size):
        calls.append((location, level, size))
        return orig(location, level, size)

    monkeypatch.setattr(src, "read_region", spy)
    list(iter_grid(src, tile=TILE, downsample=DS, tissue_min=0.0, max_tiles=1))
    assert calls, "read_region was not called"
    (loc, level, size), = calls[:1]
    assert loc == (0, 0)
    # best level for ds=4 on this slide is the largest pyramid level with
    # downsample <= 4
    assert src.level_downsamples[level] <= DS
    if src.level_count > level + 1:
        assert src.level_downsamples[level + 1] > DS
    # covering CELL level-0 px needs CELL/level_ds pixels at that level
    expected = max(TILE, round(CELL / src.level_downsamples[level]))
    assert size == (expected, expected)
