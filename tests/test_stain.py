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


# --- tier 2a: Ruifrok and Vahadane -----------------------------------------
#
# The two normalizations TIAToolbox would have brought, implemented in-tree
# instead. See docs/ROADMAP-INTEROP.md: tiatoolbox resolves to 152 installs and
# 22 uninstalls against this environment, among them ipywidgets, which the
# OpenSeadragon viewer depends on. Ruifrok is a fixed published matrix and
# Vahadane is sparse NMF via scikit-learn, which is already a dependency.


TRUE_STAINS = np.array([[0.60, 0.72, 0.35], [0.18, 0.86, 0.48]])


def _mixed_stain_image(n: int = 220, seed: int = 7):
    """A synthetic H&E field with a KNOWN stain matrix and CO-LOCALISED stains.

    Every pixel mixes both stains, which is the regime the estimators actually
    disagree in. A two-colour image cannot tell them apart: its OD cloud is two
    rays, so Macenko's angular extremes and Vahadane's dictionary recover the
    same pair, and a test built on one would pass no matter which was used.
    """
    from hescope.stain import _normalize_rows

    truth = _normalize_rows(TRUE_STAINS.copy())
    rng = np.random.default_rng(seed)
    conc = np.stack([
        rng.gamma(2.0, 0.35, n * n),          # hematoxylin: nuclei
        rng.gamma(2.0, 0.30, n * n) + 0.15,   # eosin: stroma, everywhere
    ])
    od = (truth.T @ conc).T
    rgb = np.clip((255.0 + 1) * np.exp(-od) - 1, 0, 255)
    return Image.fromarray(rgb.reshape(n, n, 3).astype(np.uint8), "RGB"), truth


def _angle_error_deg(estimated, truth):
    from hescope.stain import _normalize_rows

    cos = (_normalize_rows(np.asarray(estimated)) * truth).sum(axis=1)
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


@pytest.mark.parametrize("method", ["macenko", "ruifrok", "vahadane", "reinhard"])
def test_every_method_returns_a_usable_image(method):
    from hescope.stain import STAIN_METHODS, normalize_stain

    assert method in STAIN_METHODS
    img, _ = _mixed_stain_image(64)
    out = normalize_stain(img, method)
    assert out.size == img.size and out.mode == "RGB"
    assert np.isfinite(np.asarray(out, dtype=float)).all()


def test_unknown_method_names_the_ones_that_exist():
    from hescope.stain import normalize_stain

    with pytest.raises(ValueError, match="vahadane"):
        normalize_stain(Image.new("RGB", (8, 8), (200, 150, 200)), "not-a-method")


def test_ruifrok_uses_the_fixed_matrix_and_ignores_the_image():
    """Its whole point: no estimation, so two different patches give the same
    stain vectors and two runs are comparable."""
    from hescope.stain import STANDARD_STAIN_MATRIX, _normalize_rows, fit_reference

    a, _ = _mixed_stain_image(64, seed=1)
    b = Image.fromarray(
        np.full((64, 64, 3), (250, 245, 250), np.uint8), "RGB"  # nearly blank
    )
    ma = np.array(fit_reference(a, method="ruifrok")["stain_matrix"])
    mb = np.array(fit_reference(b, method="ruifrok")["stain_matrix"])
    assert np.allclose(ma, mb)
    assert np.allclose(ma, _normalize_rows(np.array(STANDARD_STAIN_MATRIX)), atol=1e-6)


def test_vahadane_is_not_macenko_under_another_name():
    """The silent fallback in _stain_matrix_vahadane could hide a broken
    configuration -- it did during development, where positive_code=True was
    rejected by the 'lars' coder and every call quietly returned the Macenko
    estimate. Pin that it really factorises."""
    from hescope.stain import fit_reference

    img, _ = _mixed_stain_image()
    mac = np.array(fit_reference(img, method="macenko")["stain_matrix"])
    vah = np.array(fit_reference(img, method="vahadane")["stain_matrix"])
    assert not np.allclose(vah, mac, atol=1e-4)


def test_vahadane_recovers_co_localised_stains_better_than_macenko():
    """The documented reason Vahadane exists. Macenko takes the extremes of the
    OD angular distribution, which degrades when both stains contribute to the
    same pixel; sparse NMF holds up."""
    from hescope.stain import fit_reference

    img, truth = _mixed_stain_image()
    mac = _angle_error_deg(fit_reference(img, method="macenko")["stain_matrix"], truth)
    vah = _angle_error_deg(fit_reference(img, method="vahadane")["stain_matrix"], truth)
    assert vah.max() < mac.max(), f"vahadane {vah} should beat macenko {mac}"
    assert vah.max() < 10.0, f"vahadane should land within 10 deg of truth, got {vah}"


def test_vahadane_is_deterministic():
    from hescope.stain import fit_reference

    img, _ = _mixed_stain_image(96)
    first = fit_reference(img, method="vahadane")["stain_matrix"]
    second = fit_reference(img, method="vahadane")["stain_matrix"]
    assert np.allclose(first, second)


def test_vahadane_falls_back_rather_than_raising_without_sklearn(monkeypatch):
    """scikit-learn lives in the .[ml] extra, so a core install has no NMF.
    Stain normalization is a display aid; losing it must not take a slide down."""
    import builtins

    from hescope.stain import _stain_matrix_from_od, _stain_matrix_vahadane, _optical_density, _to_rgb_array, _tissue_pixels

    real_import = builtins.__import__

    def no_sklearn(name, *args, **kwargs):
        if name.startswith("sklearn"):
            raise ImportError("no scikit-learn")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sklearn)
    img, _ = _mixed_stain_image(64)
    tissue = _tissue_pixels(_optical_density(_to_rgb_array(img)), 0.85)
    assert np.allclose(_stain_matrix_vahadane(tissue), _stain_matrix_from_od(tissue))


@pytest.mark.parametrize("method", ["macenko", "ruifrok", "vahadane"])
def test_stain_vectors_are_unit_length_and_hematoxylin_first(method):
    """All three must agree on row order, or a reference fitted with one and
    applied with another silently swaps the two stains."""
    from hescope.stain import fit_reference

    img, _ = _mixed_stain_image(96)
    m = np.array(fit_reference(img, method=method)["stain_matrix"])
    assert np.allclose(np.linalg.norm(m, axis=1), 1.0, atol=1e-6)
    assert m[0][0] >= m[1][0], "row 0 must be the hematoxylin-like vector"


@pytest.mark.parametrize("method", ["macenko", "ruifrok", "vahadane"])
def test_a_blank_patch_does_not_raise(method):
    """Below _MIN_TISSUE_PIXELS there is nothing to estimate from."""
    from hescope.stain import normalize_stain

    blank = Image.new("RGB", (32, 32), (255, 255, 255))
    assert normalize_stain(blank, method).size == (32, 32)
