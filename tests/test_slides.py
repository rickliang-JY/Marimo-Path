"""Tests for hescope.slides."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from hescope.slides import (
    PillowSource,
    SlideSource,
    best_level_for_downsample,
    open_slide,
)


@pytest.fixture()
def slide_path(tmp_path):
    arr = np.zeros((1500, 2000, 3), dtype=np.uint8)
    arr[..., 0] = 120
    arr[..., 1] = 200
    arr[..., 2] = 60
    # marker block in the top-left corner
    arr[0:100, 0:100] = (255, 0, 0)
    p = tmp_path / "test_slide.png"
    Image.fromarray(arr, "RGB").save(p)
    return p


def test_pillowsource_pyramid_levels(slide_path):
    src = PillowSource(slide_path)
    assert src.name == "test_slide.png"
    assert src.dimensions == (2000, 1500)
    # 2000x1500 -> 1000x750 -> 500x375 (min dim 375 <= 512, stop)
    assert src.level_count == 3
    assert src.level_downsamples == (1.0, 2.0, 4.0)
    assert src.mpp is None
    assert isinstance(src, SlideSource)


def test_read_region_exact_size_and_content(slide_path):
    src = PillowSource(slide_path)
    img = src.read_region((0, 0), 0, (50, 50))
    assert img.size == (50, 50)
    arr = np.asarray(img)
    assert (arr == (255, 0, 0)).all()  # inside the red marker block


def test_read_region_clipping_and_padding(slide_path):
    src = PillowSource(slide_path)
    # region extends past the right/bottom edges -> white padding
    img = src.read_region((1900, 1400), 0, (200, 200))
    assert img.size == (200, 200)
    arr = np.asarray(img)
    assert (arr[:100, :100] == (120, 200, 60)).all()  # in-bounds part
    assert (arr[100:, :] == 255).all()  # padded rows
    assert (arr[:, 100:] == 255).all()  # padded cols
    # negative origin -> white padding top/left
    img2 = src.read_region((-20, -20), 0, (50, 50))
    assert img2.size == (50, 50)
    arr2 = np.asarray(img2)
    assert (arr2[:20, :] == 255).all()
    assert (arr2[:, :20] == 255).all()
    # level 1 read: location is in level-0 coords
    img3 = src.read_region((0, 0), 1, (25, 25))
    assert img3.size == (25, 25)
    assert (np.asarray(img3)[:, :, 0] > 200).all()  # still the red marker


def test_get_thumbnail(slide_path):
    src = PillowSource(slide_path)
    thumb = src.get_thumbnail((256, 256))
    assert max(thumb.size) <= 256


def test_best_level_for_downsample(slide_path):
    src = PillowSource(slide_path)
    assert best_level_for_downsample(src, 0.5) == 0
    assert best_level_for_downsample(src, 1.0) == 0
    assert best_level_for_downsample(src, 3.0) == 1
    assert best_level_for_downsample(src, 4.0) == 2
    assert best_level_for_downsample(src, 100.0) == 2


def test_open_slide_falls_back_to_pillow(slide_path):
    src = open_slide(slide_path)
    assert isinstance(src, PillowSource)
    assert src.dimensions == (2000, 1500)


# --- R01-6: read_region must slice through _y_idx/_x_idx, not assume [0, 1] -


def _fake_tifffile_source(arr, y_idx, x_idx):
    """A TifffileSource with its axis mapping set and zarr stubbed out, so the
    region-slicing logic can be exercised without a real pyramidal TIFF."""
    from hescope.slides import TifffileSource

    src = object.__new__(TifffileSource)
    src._y_idx, src._x_idx = y_idx, x_idx
    src.level_downsamples = (1.0,)
    src._zarr = lambda level: arr  # type: ignore[assignment]
    return src


def test_read_region_respects_axis_order():
    import numpy as np

    # Interleaved YXS (the common Aperio layout): 4 rows, 6 cols, 3 samples.
    yxs = np.zeros((4, 6, 3), dtype=np.uint8)
    for x in range(6):
        yxs[:, x, :] = x * 10
    got = np.asarray(_fake_tifffile_source(yxs, 0, 1).read_region((2, 1), 0, (3, 2)))
    assert got.shape == (2, 3, 3)
    assert list(got[0, :, 0]) == [20, 30, 40]  # columns x = 2, 3, 4

    # Planar SYX: same picture, samples first. The axis mapping must be
    # followed, and the result reordered back to (Y, X, C).
    syx = np.transpose(yxs, (2, 0, 1)).copy()
    assert syx.shape == (3, 4, 6)
    got2 = np.asarray(_fake_tifffile_source(syx, 1, 2).read_region((2, 1), 0, (3, 2)))
    assert got2.shape == (2, 3, 3)
    assert list(got2[0, :, 0]) == [20, 30, 40]
    assert (got2 == got).all()
