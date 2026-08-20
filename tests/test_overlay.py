"""Tests for hescope.viewer.overlay (Part B.2)."""

from __future__ import annotations

import logging
import sys
import types

import numpy as np
import pytest
from PIL import Image, ImageFont

from hescope.viewer import overlay
from hescope.viewer.overlay import (
    _scale_bar_font_and_label,
    draw_navigator_markers,
    draw_rois,
)
from hescope.core.rois import ROI, ViewportState

# Viewport: 400x300 px, downsample 1, centered on level-0 (500, 400)
# -> level-0 x in [300, 700), y in [250, 550); viewport (0,0) == level0 (300, 250).
VP = ViewportState(center=(500.0, 400.0), downsample=1.0, size=(400, 300))


def blank(w: int = 400, h: int = 300) -> Image.Image:
    return Image.new("RGB", (w, h), (255, 255, 255))


def test_rect_draws_expected_pixels_and_preserves_input():
    img = blank()
    before = img.tobytes()
    roi = ROI(kind="rect", points=((310.0, 260.0), (410.0, 360.0)))
    out = draw_rois(img, [roi], VP, width=1)
    # input untouched
    assert img.tobytes() == before
    assert out is not img
    # outline in viewport px: corners at (10,10) and (110,110)
    px = out.load()
    assert px[10, 10] == (255, 60, 60)  # top-left corner
    assert px[60, 10] == (255, 60, 60)  # top edge
    assert px[60, 110] == (255, 60, 60)  # bottom edge
    assert px[10, 60] == (255, 60, 60)  # left edge
    assert px[110, 60] == (255, 60, 60)  # right edge
    assert px[60, 60] == (255, 255, 255)  # interior untouched


def test_polygon_closed_polyline():
    img = blank()
    tri = ROI(kind="polygon", points=((350.0, 300.0), (450.0, 300.0), (400.0, 400.0)))
    out = draw_rois(img, [tri], VP, width=1)
    arr = np.asarray(out)
    red = (arr[..., 0] == 255) & (arr[..., 1] == 60) & (arr[..., 2] == 60)
    # viewport coords: (50,50), (150,50), (100,150)
    assert red[50, 50]  # vertex 1
    assert red[50, 150]  # vertex 2
    assert red[150, 100]  # vertex 3
    assert red[50, 100]  # midpoint of top edge
    # closing edge (100,150) -> (50,50): passes near (75,100)
    assert red[100, 75] or red[99, 75] or red[101, 75] or red[100, 76]


def test_circle_ellipse_from_center_and_radius():
    img = blank()
    # center level0 (500,400) == viewport (200,150); edge point gives r=50 px
    circ = ROI(kind="circle", points=((500.0, 400.0), (550.0, 400.0)))
    out = draw_rois(img, [circ], VP, width=1)
    arr = np.asarray(out)
    red = (arr[..., 0] == 255) & (arr[..., 1] == 60) & (arr[..., 2] == 60)
    assert red[150, 250]  # rightmost point of circle
    assert red[150, 150]  # leftmost point
    assert red[100, 200]  # topmost point
    assert red[200, 200]  # bottommost point
    assert not red[150, 200]  # center untouched


def test_offscreen_roi_skipped_without_error():
    img = blank()
    far = ROI(kind="rect", points=((5000.0, 5000.0), (5100.0, 5100.0)))
    out = draw_rois(img, [far], VP)
    assert np.asarray(out).sum() == np.asarray(img).sum()  # nothing drawn


def test_mixed_on_and_offscreen():
    img = blank()
    on = ROI(kind="rect", points=((310.0, 260.0), (410.0, 360.0)))
    off = ROI(kind="circle", points=((9000.0, 9000.0), (9050.0, 9000.0)))
    out = draw_rois(img, [off, on], VP, width=1)
    assert out.load()[60, 60] == (255, 255, 255)
    assert out.load()[10, 10] == (255, 60, 60)


def test_selected_index_uses_selected_color():
    img = blank()
    a = ROI(kind="rect", points=((310.0, 260.0), (410.0, 360.0)))
    b = ROI(kind="rect", points=((420.0, 260.0), (460.0, 300.0)))
    out = draw_rois(img, [a, b], VP, width=1, selected_index=1)
    px = out.load()
    assert px[10, 10] == (255, 60, 60)  # a -> default color
    assert px[120, 10] == (60, 200, 60)  # b -> selected color


def test_downsample_scaling():
    # downsample 2: level0 (300,250) -> viewport (0,0); r=100 level0 -> 50 px
    vp = ViewportState(center=(500.0, 400.0), downsample=2.0, size=(400, 300))
    img = blank()
    circ = ROI(kind="circle", points=((500.0, 400.0), (600.0, 400.0)))
    out = draw_rois(img, [circ], vp, width=1)
    arr = np.asarray(out)
    red = (arr[..., 0] == 255) & (arr[..., 1] == 60) & (arr[..., 2] == 60)
    assert red[150, 250]  # center (200,150) + 50 px to the right
    assert red[150, 150]


def test_navigator_markers_scale_and_immutability():
    thumb = Image.new("RGB", (100, 100), (255, 255, 255))
    before = thumb.tobytes()
    # slide 1000x1000 -> thumbnail 1/10 scale
    roi = ROI(kind="rect", points=((200.0, 300.0), (400.0, 500.0)))
    out = draw_navigator_markers(thumb, [roi], (1000, 1000))
    assert thumb.tobytes() == before
    px = out.load()
    # bbox scaled /10: (20,30)-(40,50)
    assert px[20, 30] == (255, 60, 60)
    assert px[30, 30] == (255, 60, 60)
    assert px[40, 50] == (255, 60, 60)
    assert px[30, 40] == (255, 255, 255)  # interior untouched


def test_navigator_markers_circle_uses_bbox():
    thumb = Image.new("RGB", (100, 100), (255, 255, 255))
    circ = ROI(kind="circle", points=((500.0, 500.0), (600.0, 500.0)))  # r=100
    out = draw_navigator_markers(thumb, [circ], (1000, 1000))
    arr = np.asarray(out)
    red = (arr[..., 0] == 255) & (arr[..., 1] == 60) & (arr[..., 2] == 60)
    # bbox (400,400)-(600,600) -> (40,40)-(60,60)
    assert red[40, 40] and red[60, 60]
    assert red.any()


# ---------------------------------------------------------------------------
# _scale_bar_font_and_label: two stacked, historically-swallowed defects.
#
# Before the fix this function was:
#
#   try:
#       import matplotlib
#       ttf = Path(matplotlib.__file__).parent / ...   # Path never imported
#       ...
#   except Exception:
#       pass
#   return ImageFont.load_default(), f"{um:g} um"
#
# In THIS environment (matplotlib not installed) it raised
# ModuleNotFoundError: No module named 'matplotlib' on the `import` line.
# Even with matplotlib installed it would have raised
# NameError: name 'Path' is not defined on the next line (verified directly
# by executing that exact snippet -- see the PR/session notes). Both are
# swallowed by the bare `except Exception: pass`, so the function always
# "successfully" returned the ASCII "um" fallback -- a function that returns
# without error is not a signal that its content is right; only asserting on
# the returned label catches this class of bug.
# ---------------------------------------------------------------------------


def _mask_is_tofu(font: "ImageFont.FreeTypeFont", ch: str) -> bool:
    """True if ``font`` renders ``ch`` identically to a codepoint guaranteed
    absent from every font (U+FFFE, a permanently-unassigned noncharacter) --
    i.e. as the font's fallback ".notdef" glyph, a tofu box."""
    absent = chr(0xFFFE)
    a, b = np.array(font.getmask(ch)), np.array(font.getmask(absent))
    return a.shape == b.shape and bool((a == b).all())


def test_pil_default_font_cannot_render_micro_sign():
    """Documents WHY the fallback label is ASCII "um", not "µm": PIL's own
    bundled font (used by ImageFont.load_default()) has no MICRO SIGN glyph.
    If a future Pillow release adds one, this pins the current known-false
    assumption rather than letting the overlay module silently rely on a
    property no test ever checked."""
    assert _mask_is_tofu(ImageFont.load_default(size=14), "µ")


def test_scale_bar_label_has_micro_sign_when_matplotlib_available():
    """The happy path, exercised for real: hescope[fonts] (matplotlib) is
    installed in this environment. Asserts CONTENT, not just a successful
    return -- a NameError from the missing `Path` import, or matplotlib
    being unimportable, would both have been silently swallowed into the
    exact same "500 um" string the old code always produced."""
    pytest.importorskip("matplotlib")
    font, label = _scale_bar_font_and_label(500)
    assert label == "500 µm"
    assert "µ" in label
    assert isinstance(font, ImageFont.FreeTypeFont)
    # and the glyph is a REAL one, not another tofu box
    assert not _mask_is_tofu(font, "µ")


def test_scale_bar_label_falls_back_and_logs_when_matplotlib_missing(
    monkeypatch, caplog
):
    """Simulates the exact environment this session found: matplotlib not
    installed. `import matplotlib` raises ModuleNotFoundError -- forced here
    via the standard sys.modules-None trick so the test does not depend on
    whether the optional `fonts` extra happens to be installed. The fallback
    must still happen (no crash) AND must be observable: a deliberate
    degradation that never logs is indistinguishable from the swallowed bug
    this test suite exists to prevent.
    """
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    with caplog.at_level(logging.DEBUG, logger=overlay.__name__):
        font, label = _scale_bar_font_and_label(500)
    assert label == "500 um"
    assert "µ" not in label
    assert any(
        "matplotlib" in r.message and r.levelno == logging.DEBUG
        for r in caplog.records
    )


def test_scale_bar_label_falls_back_and_logs_when_ttf_file_missing(
    monkeypatch, tmp_path, caplog
):
    """matplotlib importable, but its bundled DejaVuSans.ttf is not where
    expected (e.g. a stripped-down matplotlib install). Must degrade to the
    ASCII label -- and say so -- rather than raise FileNotFoundError deep
    inside ImageFont.truetype."""
    fake_mpl = types.ModuleType("matplotlib")
    fake_mpl.__file__ = str(tmp_path / "matplotlib" / "__init__.py")
    monkeypatch.setitem(sys.modules, "matplotlib", fake_mpl)
    # deliberately do NOT create mpl-data/fonts/ttf/DejaVuSans.ttf under tmp_path
    with caplog.at_level(logging.WARNING, logger=overlay.__name__):
        font, label = _scale_bar_font_and_label(500)
    assert label == "500 um"
    assert any(
        "DejaVuSans" in r.message and r.levelno == logging.WARNING
        for r in caplog.records
    )


def test_scale_bar_label_survives_unrelated_matplotlib_import_error(monkeypatch):
    """A non-ModuleNotFoundError raised while importing matplotlib (e.g. a
    real bug in some unrelated matplotlib submodule import hook) must NOT be
    silently swallowed -- only "matplotlib is not installed" is a deliberate,
    documented degradation. Anything else is a bug that should surface."""
    class _BrokenImport(types.ModuleType):
        pass

    def _raise_on_import(name, *a, **k):
        if name == "matplotlib":
            raise RuntimeError("simulated unrelated import-time failure")
        return real_import(name, *a, **k)

    import builtins

    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", _raise_on_import)
    with pytest.raises(RuntimeError, match="simulated unrelated import-time failure"):
        _scale_bar_font_and_label(500)
