"""Tests for hescope.overlay.draw_scale_bar / pick_scale_bar_um."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from hescope.overlay import (
    SCALE_BAR_CANDIDATES_UM,
    draw_scale_bar,
    pick_scale_bar_um,
)


def _img(w=800, h=600, color=(240, 220, 230)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


class TestPickScaleBarUm:
    def test_picks_closest_to_one_fifth_width(self):
        # mpp=0.25, ds=1, width=800 -> target 160px; 1 µm = 4px.
        # candidates in px: 20->80, 50->200, 100->400, ... -> 50 µm (200px)
        # is closest to 160px.
        assert pick_scale_bar_um(0.25, 1.0, 800) == 50

    def test_downsample_scales_choice(self):
        # mpp=0.25, ds=8, width=800 -> 1 µm = 0.5px; candidates in px:
        # 100->50, 200->100, 500->250, 1000->500; target 160 -> 200 µm.
        assert pick_scale_bar_um(0.25, 8.0, 800) == 200

    def test_large_mpp_picks_big_candidate(self):
        # mpp=2.0, ds=4, width=1000 -> 1 µm = 0.125px; target 200px needs
        # 1600 µm -> nearest candidate is 1000.
        assert pick_scale_bar_um(2.0, 4.0, 1000) == 1000

    def test_tiny_mpp_picks_small_candidate(self):
        # mpp=0.05, ds=1, width=500 -> 1 µm = 20px; target 100px needs
        # 5 µm -> nearest candidate is 20.
        assert pick_scale_bar_um(0.05, 1.0, 500) == 20

    def test_custom_candidates(self):
        assert pick_scale_bar_um(0.25, 1.0, 800, candidates=(10, 40)) == 40

    def test_result_is_a_candidate(self):
        for mpp in (0.1, 0.23, 0.5, 1.0):
            for ds in (1.0, 2.0, 7.5, 32.0):
                assert pick_scale_bar_um(mpp, ds, 1024) in SCALE_BAR_CANDIDATES_UM


class TestDrawScaleBar:
    def test_size_and_mode_unchanged(self):
        src = _img()
        out = draw_scale_bar(src, mpp=0.5, viewport_downsample=2.0)
        assert out.size == src.size
        assert out.mode == "RGB"

    def test_input_not_mutated(self):
        src = _img()
        before = np.asarray(src).copy()
        draw_scale_bar(src, mpp=0.5, viewport_downsample=1.0)
        assert (np.asarray(src) == before).all()

    def test_actually_draws_something(self):
        src = _img()
        out = draw_scale_bar(src, mpp=0.5, viewport_downsample=1.0)
        assert not (np.asarray(out) == np.asarray(src)).all()

    def test_draws_in_bottom_right(self):
        src = _img(400, 300)
        out = np.asarray(draw_scale_bar(src, mpp=0.25, viewport_downsample=1.0))
        base = np.asarray(src)
        diff = np.argwhere((out != base).any(axis=2))
        ys, xs = diff[:, 0], diff[:, 1]
        assert xs.min() >= 200  # right half only
        assert ys.min() >= 150  # bottom half only

    @pytest.mark.parametrize("mpp", [None, 0.0, -1.0])
    def test_invalid_mpp_returns_unchanged_copy(self, mpp):
        src = _img()
        out = draw_scale_bar(src, mpp=mpp, viewport_downsample=2.0)
        assert out.size == src.size and out.mode == "RGB"
        assert (np.asarray(out) == np.asarray(src)).all()

    def test_nonpositive_downsample_returns_unchanged_copy(self):
        src = _img()
        out = draw_scale_bar(src, mpp=0.5, viewport_downsample=0.0)
        assert (np.asarray(out) == np.asarray(src)).all()

    def test_output_png_bytes_valid(self):
        out = draw_scale_bar(_img(), mpp=0.25, viewport_downsample=4.0)
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        buf.seek(0)
        with Image.open(buf) as reloaded:
            assert reloaded.format == "PNG"
            assert reloaded.size == out.size
            reloaded.load()

    def test_grayscale_input_becomes_rgb(self):
        src = Image.new("L", (300, 200), 200)
        out = draw_scale_bar(src, mpp=0.5, viewport_downsample=1.0)
        assert out.mode == "RGB"
