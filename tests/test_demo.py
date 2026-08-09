"""Tests for hescope.demo (in-package demo slide generation)."""

from __future__ import annotations

from PIL import Image

from hescope.demo import H, SEED, W, generate, generate_demo_slide


def test_generate_deterministic_shape_and_dtype():
    import numpy as np

    a = generate()
    b = generate()
    assert a.shape == (H, W, 3)
    assert a.dtype == np.uint8
    assert (a == b).all(), f"seed {SEED} must make generation deterministic"


def test_generate_demo_slide_writes_png(tmp_path):
    out = generate_demo_slide(tmp_path / "nested" / "demo.png")
    assert out.exists()
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert img.size == (W, H)
