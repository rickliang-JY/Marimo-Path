"""Tests for hescope.rois."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from hescope.rois import (
    ROI,
    ViewportState,
    extract_patch,
    roi_stats,
    viewport_transform,
)
from hescope.slides import PillowSource


@pytest.fixture()
def slide_path(tmp_path):
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 255, (1500, 2000, 3), dtype=np.uint8)
    p = tmp_path / "slide.png"
    Image.fromarray(arr, "RGB").save(p)
    return p


def test_viewport_transform_round_trip():
    vp = ViewportState(center=(1000.0, 750.0), downsample=4.0, size=(800, 600))
    to_level0, to_viewport = viewport_transform(vp)
    # offset = center - (size/2) * downsample = (1000-1600, 750-1200) = (-600, -450)
    assert to_level0((0.0, 0.0)) == (-600.0, -450.0)
    assert to_level0((400.0, 300.0)) == (1000.0, 750.0)  # center maps to center
    for p in [(0.0, 0.0), (10.5, 77.25), (799.0, 599.0)]:
        rt = to_viewport(to_level0(p))
        assert rt == pytest.approx(p)
        rt2 = to_level0(to_viewport(p))
        assert rt2 == pytest.approx(p)


def test_roi_bbox_rect_polygon_circle():
    rect = ROI(kind="rect", points=((10.2, 20.7), (110.9, 220.1)))
    assert rect.bbox() == (10, 20, 111, 221)
    # corners in any order
    rect2 = ROI(kind="rect", points=((110.9, 220.1), (10.2, 20.7)))
    assert rect2.bbox() == (10, 20, 111, 221)
    poly = ROI(kind="polygon", points=((5.0, 5.0), (50.0, 10.0), (30.0, 40.0)))
    assert poly.bbox() == (5, 5, 50, 40)
    circle = ROI(kind="circle", points=((100.0, 100.0), (130.0, 100.0)))  # r=30
    assert circle.bbox() == (70, 70, 130, 130)
    # negative coords clipped at 0
    neg = ROI(kind="rect", points=((-50.0, -50.0), (20.0, 30.0)))
    assert neg.bbox() == (0, 0, 20, 30)


def test_roi_mask():
    rect = ROI(kind="rect", points=((10.0, 10.0), (30.0, 20.0)))
    m = rect.mask((100, 100), (0.0, 0.0))
    assert m.shape == (100, 100)
    assert m.dtype == bool
    assert m[15, 20]
    assert not m[5, 5]
    circle = ROI(kind="circle", points=((50.0, 50.0), (60.0, 50.0)))  # r=10
    mc = circle.mask((100, 100), (0.0, 0.0))
    assert mc[50, 50]
    assert mc[50, 59]
    assert not mc[50, 61]
    assert not mc[10, 10]
    poly = ROI(kind="polygon", points=((10.0, 10.0), (90.0, 10.0), (50.0, 90.0)))
    mp = poly.mask((100, 100), (0.0, 0.0))
    assert mp[20, 50]  # inside triangle
    assert not mp[80, 10]  # outside triangle


def test_extract_patch_size_cap(slide_path):
    src = PillowSource(slide_path)
    big = ROI(kind="rect", points=((0.0, 0.0), (2000.0, 1500.0)))
    patch = extract_patch(src, big, max_size=512)
    assert max(patch.size) <= 512
    # aspect kept: 2000x1500 -> 512x384
    assert patch.size == (512, 384)
    small = ROI(kind="rect", points=((100.0, 100.0), (300.0, 250.0)))
    patch2 = extract_patch(src, small, max_size=1024)
    assert max(patch2.size) <= 1024
    assert patch2.size[0] >= 199 and patch2.size[1] >= 149  # near-native res


def test_roi_stats_keys():
    arr = np.zeros((100, 100, 3), dtype=np.uint8)
    arr[:50] = (200, 120, 170)  # pinkish tissue
    arr[50:] = (255, 255, 255)  # white background
    stats = roi_stats(Image.fromarray(arr, "RGB"))
    assert stats["width_px"] == 100
    assert stats["height_px"] == 100
    assert len(stats["mean_rgb"]) == 3
    assert all(isinstance(v, float) for v in stats["mean_rgb"])
    assert set(stats["he_deconvolution"].keys()) == {
        "hematoxylin_mean",
        "eosin_mean",
    }
    assert stats["tissue_fraction"] == pytest.approx(0.5)
    # all-white image: no tissue pixels -> fallback path, still valid stats
    white = roi_stats(Image.new("RGB", (20, 20), (255, 255, 255)))
    assert white["tissue_fraction"] == 0.0
    assert white["he_deconvolution"]["hematoxylin_mean"] >= 0.0
