"""Tests for hescope.adjust (Part B.3)."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from hescope.adjust import apply_adjustments, channel_view


def gradient_img(w: int = 64, h: int = 48) -> Image.Image:
    x = np.linspace(0, 255, w, dtype=np.float64)
    arr = np.stack(
        [np.tile(x, (h, 1)), np.tile(x, (h, 1)) * 0.6, np.tile(x, (h, 1)) * 0.3],
        axis=-1,
    ).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def he_patch(w: int = 64, h: int = 64) -> tuple[Image.Image, np.ndarray]:
    """Pink background with dark purple blobs; returns (img, blob_mask)."""
    rng = np.random.default_rng(0)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[..., 0] = 236  # pink stroma
    arr[..., 1] = 170
    arr[..., 2] = 205
    noise = rng.normal(0, 4, (h, w, 3))
    arr = np.clip(arr.astype(np.float64) + noise, 0, 255).astype(np.uint8)
    mask = np.zeros((h, w), dtype=bool)
    yy, xx = np.mgrid[0:h, 0:w]
    for cx, cy, r in ((20, 20, 8), (44, 40, 9), (24, 46, 6)):
        blob = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        mask |= blob
        arr[blob] = (96, 40, 140)  # dark purple nuclei
    return Image.fromarray(arr, "RGB"), mask


def test_identity_params_preserve_pixels():
    img = gradient_img()
    out = apply_adjustments(img, brightness=1.0, contrast=1.0, gamma=1.0)
    assert np.array_equal(np.asarray(out), np.asarray(img))
    assert out is not img


def test_input_image_not_mutated():
    img = gradient_img()
    before = img.tobytes()
    apply_adjustments(img, brightness=2.0, contrast=1.5, gamma=2.0)
    assert img.tobytes() == before


def test_brightness_increases_mean():
    img = gradient_img()
    m0 = np.asarray(img, dtype=np.float64).mean()
    m2 = np.asarray(apply_adjustments(img, brightness=2.0), dtype=np.float64).mean()
    assert m2 > m0


def test_contrast_zero_uniform_gray():
    img = gradient_img()
    out = np.asarray(apply_adjustments(img, contrast=0.0))
    assert out.min() == out.max()  # completely uniform


def test_gamma_monotonic():
    img = gradient_img()
    dark = np.asarray(apply_adjustments(img, gamma=0.5), dtype=np.float64).mean()
    ident = np.asarray(img, dtype=np.float64).mean()
    bright = np.asarray(apply_adjustments(img, gamma=2.0), dtype=np.float64).mean()
    assert dark < ident < bright


def test_gamma_lut_matches_formula():
    img = Image.new("RGB", (1, 1), (128, 128, 128))
    out = apply_adjustments(img, gamma=2.0)
    expected = int(round(255.0 * (128.0 / 255.0) ** (1.0 / 2.0)))
    assert abs(out.load()[0, 0][0] - expected) <= 1


def test_gamma_nonpositive_treated_as_identity():
    img = gradient_img()
    out = apply_adjustments(img, gamma=0.0)
    assert np.array_equal(np.asarray(out), np.asarray(img))


def test_channel_view_modes_and_sizes():
    img = gradient_img()
    for ch, mode in (("rgb", "RGB"), ("r", "L"), ("g", "L"), ("b", "L"),
                     ("hematoxylin", "L"), ("eosin", "L")):
        out = channel_view(img, ch)
        assert out.size == img.size
        assert out.mode == mode, ch


def test_channel_view_rgb_is_copy():
    img = gradient_img()
    out = channel_view(img, "rgb")
    assert out is not img
    assert np.array_equal(np.asarray(out), np.asarray(img))


def test_channel_view_single_channel_values():
    img = gradient_img()
    arr = np.asarray(img)
    assert np.array_equal(np.asarray(channel_view(img, "r")), arr[..., 0])
    assert np.array_equal(np.asarray(channel_view(img, "g")), arr[..., 1])
    assert np.array_equal(np.asarray(channel_view(img, "b")), arr[..., 2])


def test_hematoxylin_darker_inside_purple_blobs():
    img, mask = he_patch()
    out = np.asarray(channel_view(img, "hematoxylin"), dtype=np.float64)
    assert out[mask].mean() < out[~mask].mean()


def test_eosin_view_runs_on_he_patch():
    img, mask = he_patch()
    out = channel_view(img, "eosin")
    assert out.mode == "L" and out.size == img.size


def test_channel_view_unknown_raises():
    with pytest.raises(ValueError):
        channel_view(gradient_img(), "cyan")
