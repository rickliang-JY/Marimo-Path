"""Tests for hescope.analysis.features (handcrafted vector + optional embedding)."""

import time

import numpy as np
from PIL import Image, ImageDraw

from hescope.analysis.features import (
    FEATURE_DIM,
    FEATURE_NAMES,
    extract_embedding,
    extract_features,
)


def he_image(seed=0, size=256, n_blobs=10, bg=(235, 190, 205)):
    """Synthetic H&E-like image: pink background + purple blobs."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(img)
    for _ in range(n_blobs):
        x, y = rng.integers(25, size - 25, 2)
        r = 14 + int(rng.integers(-3, 4))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(130, 60, 160))
    arr = np.asarray(img).astype(np.float64) + rng.normal(0, 2.0, (size, size, 3))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def test_feature_dim_matches_names():
    assert FEATURE_DIM == len(FEATURE_NAMES)
    assert len(set(FEATURE_NAMES)) == FEATURE_DIM  # unique names


def test_extract_features_shape_dtype():
    vec = extract_features(he_image())
    assert isinstance(vec, np.ndarray)
    assert vec.shape == (FEATURE_DIM,)
    assert vec.dtype == np.float32


def test_extract_features_deterministic():
    img = he_image()
    v1 = extract_features(img)
    v2 = extract_features(img)
    assert np.array_equal(v1, v2)


def test_extract_features_finite_on_degenerate_inputs():
    for img in (
        Image.new("RGB", (256, 256), (255, 255, 255)),  # blank white
        Image.new("RGB", (64, 64), (0, 0, 0)),  # black
        Image.new("RGB", (17, 23), (200, 180, 190)),  # odd small size
    ):
        vec = extract_features(img)
        assert vec.shape == (FEATURE_DIM,)
        assert np.isfinite(vec).all()


def test_extract_features_distinguishes_images():
    v_tissue = extract_features(he_image())
    v_blank = extract_features(Image.new("RGB", (256, 256), (255, 255, 255)))
    assert not np.allclose(v_tissue, v_blank)


def test_nuclei_features_present():
    vec = extract_features(he_image())
    idx = FEATURE_NAMES.index("nuclei_count")
    assert vec[idx] > 0  # blobs are detected by the fast path
    idx_cov = FEATURE_NAMES.index("nuclei_coverage")
    assert 0.0 < vec[idx_cov] < 1.0


def test_histogram_features_sum_to_one_per_channel():
    vec = extract_features(he_image())
    for ch in ("r", "g", "b"):
        idx = [FEATURE_NAMES.index(f"hist_{ch}_{i}") for i in range(8)]
        assert vec[idx].sum() == np.float32(1.0)


def test_extract_features_fast_on_256_patch():
    img = he_image()
    extract_features(img)  # warm-up
    t0 = time.perf_counter()
    extract_features(img)
    dt = time.perf_counter() - t0
    assert dt < 0.5  # per-tile budget from the spec


def test_extract_embedding_none_or_512d_without_raising():
    emb = extract_embedding(he_image())
    if emb is not None:  # torch+weights available (possibly after download)
        assert isinstance(emb, np.ndarray)
        assert emb.shape == (512,)
        assert np.isfinite(emb).all()
    # None is always acceptable (optional dependency / offline); never raises.
