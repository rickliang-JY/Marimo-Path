"""Tests for hescope.stain (Macenko + Reinhard normalization)."""

import numpy as np
import pytest
from PIL import Image, ImageDraw
from skimage.color import rgb2hed

from hescope.stain import (
    REINHARD_REF_MEAN,
    REINHARD_REF_STD,
    STANDARD_STAIN_MATRIX,
    fit_reference,
    macenko_normalize,
    reinhard_normalize,
)


def he_image(seed=0, size=256, n_blobs=10, blob=(130, 60, 160), bg=(235, 190, 205)):
    """Synthetic H&E-like image: pink background + purple blobs."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(img)
    for _ in range(n_blobs):
        x, y = rng.integers(25, size - 25, 2)
        r = 14 + int(rng.integers(-3, 4))
        d.ellipse([x - r, y - r, x + r, y + r], fill=blob)
    arr = np.asarray(img).astype(np.float64)
    arr += rng.normal(0, 2.0, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def two_stain_pair():
    """Same tissue geometry rendered with two different stain tints."""
    size = 256
    rng = np.random.default_rng(3)
    d_img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(d_img)
    for _ in range(10):
        x, y = rng.integers(30, size - 30, 2)
        d.ellipse([x - 18, y - 18, x + 18, y + 18], fill=255)
    mask = np.asarray(d_img) > 0

    def tint(h_col, e_col):
        r = np.random.default_rng(5)
        arr = np.zeros(mask.shape + (3,))
        arr[mask] = h_col
        arr[~mask] = e_col
        arr += r.normal(0, 3, arr.shape)
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    return tint((120, 50, 150), (240, 200, 210)), tint((60, 60, 170), (250, 170, 160))


def _hed_mean(img):
    hed = rgb2hed(np.asarray(img.convert("RGB")))
    return hed.reshape(-1, 3).mean(axis=0)


def test_fit_reference_schema():
    ref = fit_reference(he_image())
    assert set(ref) == {"stain_matrix", "max_conc"}
    sm = np.asarray(ref["stain_matrix"])
    mc = np.asarray(ref["max_conc"])
    assert sm.shape == (2, 3)
    assert mc.shape == (2,)
    assert np.all(np.isfinite(sm)) and np.all(np.isfinite(mc))
    assert np.all(mc > 0)


def test_fit_reference_deterministic():
    img = he_image()
    assert fit_reference(img) == fit_reference(img)


def test_macenko_with_reference_deterministic():
    img = he_image()
    ref = fit_reference(img)
    a = np.asarray(macenko_normalize(img, reference=ref))
    b = np.asarray(macenko_normalize(img, reference=ref))
    assert np.array_equal(a, b)


def test_macenko_self_reference_runs():
    img = he_image()
    out = macenko_normalize(img)
    assert isinstance(out, Image.Image)
    assert out.size == img.size
    assert out.mode == "RGB"


def test_macenko_blank_image_no_crash():
    blank = Image.new("RGB", (256, 256), (255, 255, 255))
    ref = fit_reference(blank)  # falls back to STANDARD_STAIN_MATRIX
    assert np.allclose(ref["stain_matrix"], STANDARD_STAIN_MATRIX)
    out = macenko_normalize(blank)
    assert out.size == blank.size


def test_macenko_normalization_reduces_stain_gap():
    img_a, img_b = two_stain_pair()
    d_before = float(np.linalg.norm(_hed_mean(img_a) - _hed_mean(img_b)))
    ref = fit_reference(img_a)
    norm_a = macenko_normalize(img_a, reference=ref)
    norm_b = macenko_normalize(img_b, reference=ref)
    d_after = float(np.linalg.norm(_hed_mean(norm_a) - _hed_mean(norm_b)))
    assert d_after < d_before  # loose assert: normalization helps


def test_reinhard_deterministic_and_shape():
    img = he_image()
    a = reinhard_normalize(img)
    b = reinhard_normalize(img)
    assert a.size == img.size and a.mode == "RGB"
    assert np.array_equal(np.asarray(a), np.asarray(b))


def test_reinhard_matches_reference_stats_loosely():
    img = he_image()
    out = reinhard_normalize(img)
    from skimage.color import rgb2lab

    lab = rgb2lab(np.asarray(out)).reshape(-1, 3)
    assert lab[:, 0].mean() == pytest.approx(REINHARD_REF_MEAN[0], abs=3.0)
    assert lab[:, 0].std() == pytest.approx(REINHARD_REF_STD[0], rel=0.25)


def test_reinhard_blank_image_no_crash():
    blank = Image.new("RGB", (64, 64), (255, 255, 255))  # flat channel guard
    out = reinhard_normalize(blank)
    assert out.size == blank.size
    assert np.isfinite(np.asarray(out, dtype=float)).all()
