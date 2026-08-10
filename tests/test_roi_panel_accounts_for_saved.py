"""The ROIs panel must not deny ROIs the viewer is drawing.

Reported from the running app, with a screenshot: two red outlines on the
slide and on the navigator, while the sidebar said *"No ROIs yet: draw on the
viewer"* and the status strip said *"0 ROI(s) this session"*.

Both were reading real data; they were reading DIFFERENT data. The viewer draws
``overlay_rois = session + persisted``, and the panel read ``get_rois()`` alone,
which starts empty in every new session. Confirmed against the live kernel over
marimo's MCP channel, on the user's own slide (slide_id 2)::

    session rois : 0   -> []
    overlay_rois : 4                 <- what the picture shows
    db rows      : 4   (annotation_count 4)

bugs/SUMMARY.md class 5: "a second place re-deriving what one owner decides".
Here the panel did not re-derive it -- it read half of it, and phrased that half
as a statement about the whole slide.

The same split made "Clear all ROIs" report *"All session ROIs cleared."* over a
viewer still showing every saved outline, which is indistinguishable from a
button that did not work.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from hescope.rois import ROI

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _roi_panel_cell():
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
            continue
        src = ast.get_source_segment(SOURCE, node)
        if src is None or '# Sidebar "ROIs" panel' not in src:
            continue
        ns: dict = {}
        exec(compile("\n" * (node.lineno - 1) + src, str(APP), "exec"), ns)
        return ns[node.name], [a.arg for a in node.args.args]
    raise AssertionError("no @app.cell in app.py builds the ROIs panel")


class _MO:
    def __init__(self):
        self.md_texts: list[str] = []
        self.buttons: dict = {}

    def md(self, text):
        self.md_texts.append(text)
        return ("md", text)

    def hstack(self, parts, **kw):
        return ("hstack", parts)

    def vstack(self, parts, **kw):
        return ("vstack", parts)

    class _UI:
        def __init__(self, outer):
            self._outer = outer

        def button(self, *, label, on_click):
            self._outer.buttons[label] = on_click
            return ("button", label)

    @property
    def ui(self):
        return _MO._UI(self)


def _rect(x=10.0):
    return ROI(kind="rect", points=((x, 20.0), (x + 100.0, 100.0)))


def _run(*, session, saved):
    """Render the panel; returns (mo recorder, published messages)."""
    cell, params = _roi_panel_cell()
    published: list = []
    mo = _MO()
    deps = {
        "db_roi_rows": [{"id": i} for i in range(saved)],
        "dc_replace": lambda obj, **kw: obj,
        "get_rois": lambda: list(session),
        "get_source": lambda: None,
        "get_vp": lambda: None,
        "jump_viewport_for_bbox": lambda *a, **k: ((0.0, 0.0), 1.0),
        "mo": mo,
        "move_camera": lambda vp: None,
        "set_db_msg": published.append,
        "set_rois": lambda rois: None,
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the ROIs panel cell grew new dependencies: {missing}"
    cell(**{p: deps[p] for p in params})
    return mo, published


def _text(mo: _MO) -> str:
    return "\n".join(mo.md_texts)


# --- the reported contradiction -------------------------------------------


def test_saved_rois_are_not_reported_as_no_rois_yet():
    mo, _pub = _run(session=[], saved=4)
    text = _text(mo)
    assert "No ROIs yet" not in text, (
        "the viewer is drawing 4 outlines from the database and the panel "
        "told the user there are none -- the screenshot shows both at once"
    )
    assert "4 saved ROI(s)" in text
    assert "Annotations" in text, "the panel must point at where they live"


def test_a_genuinely_empty_slide_still_says_so():
    mo, _pub = _run(session=[], saved=0)
    assert "No ROIs yet" in _text(mo), "the empty state must survive the fix"


def test_session_rois_are_listed_and_the_saved_ones_accounted_for():
    mo, _pub = _run(session=[_rect(), _rect(300.0)], saved=3)
    text = _text(mo)
    assert "**[0]** rect" in text and "**[1]** rect" in text
    assert "3 saved ROI(s)" in text, (
        "the session list is complete but the picture has 3 more outlines on it"
    )


def test_no_saved_line_when_there_is_nothing_saved():
    mo, _pub = _run(session=[_rect()], saved=0)
    assert "saved ROI(s)" not in _text(mo)


# --- the button that looked broken ----------------------------------------


def test_clearing_the_session_says_what_stayed_behind():
    mo, published = _run(session=[_rect()], saved=4)
    label = next(k for k in mo.buttons if "lear" in k)
    assert "session" in label.lower(), (
        f"{label!r} clears only the session list, and the saved outlines stay "
        "on the image; the label must not promise otherwise"
    )

    mo.buttons[label](object())
    kind, text = published[-1]
    assert kind == "info"
    assert "4 saved ROI(s)" in text and "still on the image" in text, (
        f"{text!r} over a viewer still showing 4 outlines reads as a button "
        "that did nothing"
    )


def test_clearing_an_all_session_slide_makes_no_claim_about_saved_rois():
    mo, published = _run(session=[_rect()], saved=0)
    label = next(k for k in mo.buttons if "lear" in k)
    mo.buttons[label](object())
    assert "saved" not in published[-1][1]


@pytest.mark.parametrize("saved", [1, 2, 9])
def test_the_count_is_the_database_count_not_a_guess(saved):
    mo, _pub = _run(session=[], saved=saved)
    assert f"{saved} saved ROI(s)" in _text(mo)
