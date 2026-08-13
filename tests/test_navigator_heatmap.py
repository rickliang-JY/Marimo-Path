"""The navigator must say why it ignored "show heatmap on navigator".

Reported from the running app: *"show heatmap on navigator" 的按钮好像没有什么用*
— the checkbox appeared to do nothing.

The blend itself was never broken. Measured on the real 81671x18211 TCGA slide,
a 5x20 grid over the 200x45 navigator thumbnail changes **9000 of 9000 pixels**
with a mean absolute delta of 51/255. What was broken is that the checkbox had
two paths on which it silently did nothing:

  * no heatmap computed yet -- ``if hm_nav_checkbox.value and _hm is not None``
    skipped the whole block;
  * a grid that does not fit this slide -- ``except Exception: pass``.

On both, the plain thumbnail stayed on screen with no word about why. A control
whose failure is indistinguishable from a dead control *is* a dead control from
where the user sits (bugs/SUMMARY.md class 1, inverted: not a false success, but
a real failure rendered as nothing at all).

Driven through app.py's OWN navigator cell so a rewrite of the panel cannot
leave this passing on unwired code.
"""

from __future__ import annotations

import ast
import pathlib

import numpy as np
import pytest
from PIL import Image

from hescope.analysis.heatmap import grid_coverage, render_heatmap

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _navigator_cell():
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
            continue
        src = ast.get_source_segment(SOURCE, node)
        if src is None or '# Sidebar "Navigator" panel' not in src:
            continue
        ns: dict = {}
        exec(compile("\n" * (node.lineno - 1) + src, str(APP), "exec"), ns)
        return ns[node.name], [a.arg for a in node.args.args]
    raise AssertionError("no @app.cell in app.py builds the navigator panel")


class _Source:
    dimensions = (81671, 18211)


class _Check:
    def __init__(self, value):
        self.value = value


class _MO:
    """Records the markdown the panel emits, and returns the image bytes."""

    def __init__(self):
        self.md_texts: list[str] = []
        self.images: list = []

    def md(self, text):
        self.md_texts.append(text)
        return ("md", text)

    def image(self, data):
        self.images.append(data)
        return ("image", data)

    def vstack(self, parts):
        return ("vstack", list(parts))


def _run(*, checkbox: bool, hm, nav_size=(200, 45)):
    """Render the panel; returns (mo recorder, the image handed to mo.image)."""
    cell, params = _navigator_cell()
    plain = Image.new("RGB", nav_size, (240, 230, 240))
    mo = _MO()
    deps = {
        "draw_navigator_markers": lambda img, rois, dims: img,
        "get_hm_result": lambda: hm,
        "get_source": lambda: _Source(),
        "get_vp": lambda: object(),
        "grid_coverage": grid_coverage,
        "hm_nav_checkbox": _Check(checkbox),
        "mo": mo,
        "navigator_image": lambda src, vp, max_size=200: plain.copy(),
        "overlay_checkbox": _Check(False),
        "overlay_rois": [],
        "render_heatmap": render_heatmap,
        "viewport_png_bytes": lambda img: img,  # keep the Image for comparison
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the navigator cell grew new dependencies: {missing}"
    cell(**{p: deps[p] for p in params})
    return mo, mo.images[-1]


def _good_hm(rows=5, cols=20):
    rng = np.random.default_rng(0)
    return {
        "grid": rng.random((rows, cols)),
        "params": {"tile": 256, "downsample": 16.0},
    }


def _notes(mo: _MO) -> list[str]:
    return [t for t in mo.md_texts if t.startswith("*") and "Navigator" not in t]


# --- the two silent paths --------------------------------------------------


def test_ticking_the_box_with_no_heatmap_says_so():
    mo, _img = _run(checkbox=True, hm=None)
    notes = _notes(mo)
    assert notes, (
        "the checkbox was ticked, the picture did not change, and the panel "
        "said nothing -- which is what 'this button does nothing' looks like"
    )
    assert "none has been computed" in notes[0]
    assert "Heatmap" in notes[0], "the note must point at where to run one"


@pytest.mark.parametrize(
    "broken, why",
    [
        (
            {"grid": np.zeros((5, 20)), "params": {"tile": 256}},
            "KeyError: session state carrying an older result-dict shape",
        ),
        (
            {"grid": np.zeros(20), "params": {"tile": 256, "downsample": 16.0}},
            "IndexError: a 1-D grid, i.e. a sweep that produced a single row",
        ),
    ],
)
def test_a_malformed_heatmap_result_is_reported_not_swallowed(broken, why):
    """What ACTUALLY raises on this path, measured rather than assumed.

    The original comment said "stale/mismatched grid", but ``grid_coverage``
    clamps degenerate params to (1.0, 1.0) and ``render_heatmap`` accepts a 0x0
    grid, so neither raises -- and a grid from another slide cannot reach here
    at all, because opening a slide calls ``set_hm_result(None)``. The reachable
    failures are a malformed result dict and a non-2-D grid.
    """
    mo, _img = _run(checkbox=True, hm=broken)
    notes = _notes(mo)
    assert notes, f"the failure was swallowed by `pass` ({why})"
    assert "re-run the sweep" in notes[0]
    assert why.split(":")[0] in notes[0], f"the note must name the fault: {notes[0]}"


# --- and the paths that must stay quiet ------------------------------------


def test_a_good_heatmap_is_drawn_and_needs_no_note():
    mo, img = _run(checkbox=True, hm=_good_hm())
    assert not _notes(mo), f"a successful blend must not explain itself: {_notes(mo)}"

    plain, _p = _run(checkbox=False, hm=_good_hm())
    changed = np.abs(
        np.asarray(img.convert("RGB"), np.int16)
        - np.asarray(_p.convert("RGB"), np.int16)
    )
    assert (changed.sum(axis=2) > 0).all(), "the blend did not reach the thumbnail"


def test_an_unticked_box_never_explains_itself():
    for hm in (None, _good_hm()):
        mo, _img = _run(checkbox=False, hm=hm)
        assert not _notes(mo), "the box is off; there is nothing to explain"


@pytest.mark.parametrize("rows,cols", [(1, 1), (5, 20), (12, 51)])
def test_the_blend_reaches_every_pixel_of_a_wide_thumbnail(rows, cols):
    """The reported slide is 81671x18211 -- 4.48:1, so a max_size=200
    navigator is 200x45. The overlay must still land on all of it."""
    mo, img = _run(checkbox=True, hm=_good_hm(rows, cols))
    assert not _notes(mo)
    assert img.size == (200, 45)
