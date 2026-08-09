"""Tests for hescope.qc (tissue mask, blur score, QC report)."""

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from hescope.qc import BLUR_THRESHOLD, blur_score, qc_report, tissue_mask


def he_image(seed=0, size=256, n_blobs=10, blur=0.0):
    """Synthetic H&E-like image: pink background + purple blobs."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (size, size), (235, 190, 205))
    d = ImageDraw.Draw(img)
    for _ in range(n_blobs):
        x, y = rng.integers(25, size - 25, 2)
        r = 14 + int(rng.integers(-3, 4))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(130, 60, 160))
    arr = np.asarray(img).astype(np.float64) + rng.normal(0, 2.0, (size, size, 3))
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    return out.filter(ImageFilter.GaussianBlur(blur)) if blur else out


def test_tissue_mask_bool_and_nonempty_on_he():
    mask = tissue_mask(he_image())
    assert mask.dtype == bool
    assert mask.shape == (256, 256)
    assert 0.01 < mask.mean() < 0.9


def test_tissue_mask_white_image_empty():
    mask = tissue_mask(Image.new("RGB", (256, 256), (255, 255, 255)))
    assert not mask.any()


def test_tissue_mask_covers_blob_centers():
    rng = np.random.default_rng(0)
    img = Image.new("RGB", (256, 256), (235, 190, 205))
    d = ImageDraw.Draw(img)
    centers = []
    for _ in range(6):
        x, y = rng.integers(40, 216, 2)
        centers.append((int(x), int(y)))
        d.ellipse([x - 15, y - 15, x + 15, y + 15], fill=(130, 60, 160))
    mask = tissue_mask(img)
    assert all(mask[y, x] for x, y in centers)


def test_blur_score_sharp_vs_blurred():
    sharp = blur_score(he_image())
    blurred = blur_score(he_image(blur=3.0))
    assert sharp > blurred
    assert sharp > BLUR_THRESHOLD > blurred  # documents the calibrated split


def test_blur_score_flat_image_zero():
    assert blur_score(Image.new("RGB", (64, 64), (200, 200, 200))) == 0.0


def test_qc_report_keys_and_values():
    report = qc_report(he_image())
    assert set(report) == {"tissue_fraction", "blur_score", "is_blurry", "brightness_mean"}
    assert 0.0 < report["tissue_fraction"] < 1.0
    assert report["blur_score"] > BLUR_THRESHOLD
    assert report["is_blurry"] is False
    assert 150.0 < report["brightness_mean"] < 230.0


def test_qc_report_blurry_flag():
    report = qc_report(he_image(blur=3.0))
    assert report["is_blurry"] is True


def test_qc_report_white_image():
    report = qc_report(Image.new("RGB", (256, 256), (255, 255, 255)))
    assert report["tissue_fraction"] == 0.0
    assert report["is_blurry"] is True
    assert report["brightness_mean"] == 255.0
