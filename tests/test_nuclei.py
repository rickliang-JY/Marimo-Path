"""Tests for hescope.analysis.nuclei (watershed nuclei detection)."""

import numpy as np
from PIL import Image, ImageDraw

from hescope.analysis.nuclei import NucleiStats, detect_nuclei


def he_image(seed=0, size=256, blobs=((60, 60), (180, 80), (90, 190)), r=14):
    """Pink background + well-separated purple blobs (deterministic)."""
    img = Image.new("RGB", (size, size), (235, 190, 205))
    d = ImageDraw.Draw(img)
    for x, y in blobs:
        d.ellipse([x - r, y - r, x + r, y + r], fill=(130, 60, 160))
    rng = np.random.default_rng(seed)
    arr = np.asarray(img).astype(np.float64) + rng.normal(0, 1.5, (size, size, 3))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def test_detects_separated_blobs():
    labels, stats = detect_nuclei(he_image())
    assert stats.count == 3
    assert labels.shape == (256, 256)
    assert labels.dtype == np.int32
    assert len(np.unique(labels)) == 4  # background + 3 nuclei


def test_stats_fields_and_types():
    _, stats = detect_nuclei(he_image())
    assert isinstance(stats, NucleiStats)
    assert stats.density_per_mm2 is None  # mpp unknown
    assert stats.mean_area_px > 0
    assert stats.mean_intensity_h > 0
    assert 0.0 < stats.mask_coverage < 1.0


def test_density_with_mpp():
    _, stats = detect_nuclei(he_image(), mpp=0.5)
    # area = (256*0.5)^2 um2 = 16384 um2 = 0.016384 mm2; 3 nuclei -> ~183.1/mm2
    assert stats.density_per_mm2 is not None
    assert stats.density_per_mm2 == np.float64(3 / 0.016384)


def test_blank_image_no_crash_zero_count():
    labels, stats = detect_nuclei(Image.new("RGB", (256, 256), (255, 255, 255)))
    assert stats.count == 0
    assert stats.mask_coverage == 0.0
    assert stats.mean_area_px == 0.0
    assert not labels.any()


def test_flat_gray_image_no_crash():
    labels, stats = detect_nuclei(Image.new("RGB", (128, 128), (128, 128, 128)))
    assert stats.count == 0
    assert not labels.any()


def test_watershed_separates_touching_blobs():
    # Two overlapping nuclei that connected components would merge into one.
    img = he_image(blobs=((110, 128), (146, 128)), r=20)
    labels, stats = detect_nuclei(img)
    assert stats.count == 2


def test_min_size_filters_specks():
    img = he_image(blobs=((60, 60), (180, 80)), r=14)
    d = ImageDraw.Draw(img)
    d.ellipse([200, 200, 203, 203], fill=(130, 60, 160))  # tiny speck (~13 px)
    _, stats_small = detect_nuclei(img, min_size_px=20)
    _, stats_tiny = detect_nuclei(img, min_size_px=5)
    assert stats_small.count == 2
    assert stats_tiny.count == 3


def test_explicit_h_threshold():
    labels, stats = detect_nuclei(he_image(), h_threshold=0.05)
    assert stats.count == 3


def test_deterministic():
    img = he_image()
    l1, s1 = detect_nuclei(img)
    l2, s2 = detect_nuclei(img)
    assert np.array_equal(l1, l2)
    assert s1 == s2


# --- R01-5: min_size boundary must stay strict (< min_size), not <= --------


def test_min_size_boundary_is_strict():
    """skimage's successor parameter drops components <= the threshold; the
    original min_size dropped only strictly smaller ones. Pin the original."""
    import numpy as np

    from hescope.analysis.nuclei import remove_small_objects_strict

    a = np.zeros((20, 20), dtype=bool)
    a[1, 1:5] = True   # a 4-pixel component
    a[10, 10:13] = True  # a 3-pixel component
    kept = remove_small_objects_strict(a, 4)
    assert kept[1, 1:5].all()      # exactly 4 -> kept (area < 4 is dropped)
    assert not kept[10, 10:13].any()  # 3 -> dropped
    # nothing is removed below the trivial threshold
    assert remove_small_objects_strict(a, 1).sum() == a.sum()
