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


# --- R03-1: OpenSlideSource must pad OUTSIDE the slide with white ------------


class _StubOpenSlide:
    """Minimal stand-in for ``openslide.OpenSlide``.

    Reproduces the one behaviour that matters here: a read that runs past the
    slide edge comes back RGBA with the out-of-bounds part TRANSPARENT, which
    becomes BLACK the moment ``OpenSlideSource.read_region`` calls
    ``.convert("RGB")``.
    """

    def __init__(self, arr):
        self._arr = arr  # (h, w, 3) uint8, level 0
        h, w = arr.shape[:2]
        self.dimensions = (w, h)
        self.level_count = 1
        self.level_downsamples = (1.0,)
        self.properties = {}

    def read_region(self, location, level, size):
        x0, y0 = location
        w, h = size
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))  # transparent, like openslide
        ah, aw = self._arr.shape[:2]
        sx0, sy0 = max(0, x0), max(0, y0)
        sx1, sy1 = min(aw, x0 + w), min(ah, y0 + h)
        if sx1 > sx0 and sy1 > sy0:
            block = self._arr[sy0:sy1, sx0:sx1]
            out.paste(Image.fromarray(block, "RGB"), (sx0 - x0, sy0 - y0))
        return out


def _fake_openslide_source(arr):
    from hescope.slides import OpenSlideSource

    src = object.__new__(OpenSlideSource)
    src.name = "stub.svs"
    src._slide = _StubOpenSlide(arr)
    src.dimensions = src._slide.dimensions
    src.level_count = 1
    src.level_downsamples = (1.0,)
    src.mpp = None
    return src


def test_openslide_read_region_pads_outside_the_slide_with_white():
    """The off-slide part of a read must be white (255, 255, 255).

    It used to be black: the crop box added the window size to its right and
    bottom edges, doubling it, so the transparent-turned-black region past the
    slide edge was pasted over the white padding. Black counts as tissue
    (luminance < 0.9 * 255), so an ROI straddling the edge reported inflated
    tissue_fraction / H&E means, and every border cell of a heatmap sweep was
    kept as "tissue".
    """
    arr = np.full((40, 60, 3), 200, dtype=np.uint8)
    src = _fake_openslide_source(arr)

    # right edge: 30 in-slide columns, 30 past it
    img = src.read_region((30, 0), 0, (60, 40))
    out = np.asarray(img)
    assert img.size == (60, 40)
    assert (out[:, :30] == 200).all()
    assert (out[:, 30:] == 255).all()

    # bottom edge
    out2 = np.asarray(src.read_region((0, 20), 0, (60, 40)))
    assert (out2[:20, :] == 200).all()
    assert (out2[20:, :] == 255).all()

    # negative origin: white on the top/left, image content after it
    out3 = np.asarray(src.read_region((-10, -5), 0, (30, 30)))
    assert (out3[:5, :] == 255).all()
    assert (out3[:, :10] == 255).all()
    assert (out3[5:, 10:] == 200).all()

    # fully inside: unchanged, every pixel is slide content
    out4 = np.asarray(src.read_region((10, 10), 0, (20, 20)))
    assert (out4 == 200).all()


def test_openslide_and_pillow_pad_identically(slide_path):
    """The three backends document the same contract; pin them together."""
    pil = PillowSource(slide_path)
    w, h = pil.dimensions
    arr = np.asarray(Image.open(slide_path).convert("RGB"))
    osl = _fake_openslide_source(arr)
    for loc in [(w - 50, 0), (0, h - 50), (-30, -30), (w - 10, h - 10)]:
        a = np.asarray(pil.read_region(loc, 0, (100, 100)))
        b = np.asarray(osl.read_region(loc, 0, (100, 100)))
        assert (a == b).all(), f"backends disagree at {loc}"
