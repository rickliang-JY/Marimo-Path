"""Tests for hescope.core.rois."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from hescope.core.rois import (
    ROI,
    ViewportState,
    extract_patch,
    roi_stats,
    viewport_transform,
)
from hescope.wsi.slides import PillowSource


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


# --- R01-1: roi_stats must respect the ROI shape, not just its bbox --------


def _triangle_patch():
    """Patch == the bbox of a triangle lasso: purple inside, brown outside."""
    from PIL import ImageDraw

    tri = ROI(kind="polygon", points=((0.0, 0.0), (99.0, 0.0), (0.0, 99.0)))
    img = Image.new("RGB", (99, 99), (140, 110, 40))  # brown, outside
    ImageDraw.Draw(img).polygon(
        [(0, 0), (99, 0), (0, 99)], fill=(110, 40, 140)  # purple, inside
    )
    return tri, img


def test_roi_stats_respects_roi_shape():
    tri, patch = _triangle_patch()
    # the triangle fills only about half of its own bounding box
    from hescope.core.rois import roi_shape_mask

    m = roi_shape_mask(patch, tri)
    assert 0.45 < float(m.mean()) < 0.55

    shaped = roi_stats(patch, tri)
    assert shaped["mean_rgb"] == pytest.approx([110.0, 40.0, 140.0], abs=0.5)

    # without the roi the bbox is used, which mixes in the brown outside
    bbox_only = roi_stats(patch)
    assert bbox_only["mean_rgb"] != pytest.approx([110.0, 40.0, 140.0], abs=0.5)
    assert (
        shaped["he_deconvolution"]["hematoxylin_mean"]
        > bbox_only["he_deconvolution"]["hematoxylin_mean"]
    )


def test_roi_stats_mask_scales_to_downsampled_patch():
    """extract_patch downsamples; the shape mask must scale with it."""
    tri, patch = _triangle_patch()
    small = patch.resize((33, 33), Image.NEAREST)  # 3x downsample of the bbox
    shaped = roi_stats(small, tri)
    assert shaped["mean_rgb"] == pytest.approx([110.0, 40.0, 140.0], abs=6.0)
    assert shaped["width_px"] == 33 and shaped["height_px"] == 33


def test_roi_stats_rect_unchanged_by_roi_arg():
    """A rect's patch IS its bbox, so passing the roi must change nothing."""
    arr = np.zeros((40, 60, 3), dtype=np.uint8)
    arr[:20] = (200, 120, 170)
    arr[20:] = (255, 255, 255)
    patch = Image.fromarray(arr, "RGB")
    rect = ROI(kind="rect", points=((0.0, 0.0), (60.0, 40.0)))
    assert roi_stats(patch, rect) == roi_stats(patch)


def test_roi_mask_scale_matches_manual_downsample():
    circle = ROI(kind="circle", points=((50.0, 50.0), (75.0, 50.0)))  # r=25
    full = circle.mask((100, 100), (0.0, 0.0))
    half = circle.mask((50, 50), (0.0, 0.0), 2.0)  # 2 level-0 px per mask px
    assert half.shape == (50, 50)
    # same disc, half the linear resolution -> same area fraction
    assert float(half.mean()) == pytest.approx(float(full.mean()), abs=0.02)
