"""'Add ROI' in measure mode must not destroy the measurement it just made.

The measure vocabulary is surface-specific. OpenSeadragon reports a measure
drag as ``kind="measure"``, which ``parse_osd_selection`` refuses ON PURPOSE
(``_ROI_KINDS = ("rect", "polygon")``) so a measurement can never reach the
agent contract as an ROI. ``_on_add_roi`` only ever asked ``live_selection()``,
which is therefore ``None`` during a measurement -- and its "no selection"
branch writes into ``set_measure_msg``, the SAME channel the widget's report
cell had just published the measurement on. Clicking "Add ROI" right after
measuring replaced "400.0 x 300.0 px" with "No selection: drag a box or lasso
on the viewer first", a statement that contradicts what the user had just
done (R04-5).

The branch was not dead code, which is why it could not simply be deleted: a
rect drawn BEFORE measure mode was switched on is still what
``live_selection()`` returns, because the widget does not clear its
``selection`` trait when the tool changes (``osdviewer.py`` binds
``change:tool`` to ``applyTool``, which only touches gesture settings and the
cursor). Both routes are pinned below.

Driven through ``app.run()``'s real widget and the real
``ui_actions["add_roi"]`` toolbar action.
"""

from __future__ import annotations

import pytest

from hescope.wsi.demo import generate_demo_slide


@pytest.fixture(scope="module")
def notebook_defs():
    import app as appmod

    _outputs, defs = appmod.app.run()
    defs["open_slide_path"](str(generate_demo_slide("assets/demo_he.png")))
    return defs


@pytest.fixture(scope="module")
def viewer(notebook_defs):
    v = notebook_defs["osd_viewer"]
    if v is None:
        pytest.skip("OpenSeadragon surface unavailable in this environment")
    return v


@pytest.fixture(autouse=True)
def _clean_slate(notebook_defs, viewer):
    notebook_defs["set_rois"]([])
    notebook_defs["set_measure_msg"](None)
    viewer.selection = {}
    yield
    notebook_defs["measure_checkbox"]._update(False)


def _drag(viewer, kind, points, seq):
    """What the widget reports after a drag with the given tool."""
    viewer.selection = {"kind": kind, "points_level0": points, "seq": seq}


def test_add_roi_in_measure_mode_publishes_the_measurement_not_a_warning(
    notebook_defs, viewer
):
    notebook_defs["measure_checkbox"]._update(True)
    _drag(viewer, "measure", [[100.0, 100.0], [500.0, 400.0]], 11)

    # the premise: this geometry is invisible to the ROI vocabulary
    assert notebook_defs["live_selection"]() is None

    notebook_defs["ui_actions"]["add_roi"](None)

    kind, text = notebook_defs["get_measure_msg"]()
    assert (kind, text) == ("info", "400.0 x 300.0 px"), (
        "'Add ROI' in measure mode answered a real measurement with "
        f"{(kind, text)!r}, overwriting the readout in the one channel both "
        "share"
    )
    assert notebook_defs["get_rois"]() == [], "a measurement must never become an ROI"


def test_a_rect_drawn_before_measure_mode_is_still_measured(notebook_defs, viewer):
    """The reachable branch: the widget keeps `selection` across a tool change."""
    _drag(viewer, "rect", [[200.0, 200.0], [800.0, 500.0]], 12)
    notebook_defs["measure_checkbox"]._update(True)
    assert notebook_defs["live_selection"]() is not None

    notebook_defs["ui_actions"]["add_roi"](None)

    assert notebook_defs["get_measure_msg"]() == ("info", "600.0 x 300.0 px")
    assert notebook_defs["get_rois"]() == []


def test_measure_mode_with_nothing_drawn_still_says_so(notebook_defs, viewer):
    """Silence would be worse than the wrong message: the click needs an answer."""
    notebook_defs["measure_checkbox"]._update(True)
    notebook_defs["ui_actions"]["add_roi"](None)

    kind, text = notebook_defs["get_measure_msg"]()
    assert kind == "warn" and "measure" in text.lower()


def test_add_roi_outside_measure_mode_is_unchanged(notebook_defs, viewer):
    """Guard against the fix overreaching: the ordinary path must still add.

    R08-2 moved the destination, not the intent: with a database available the
    ROI is written to the ``rois`` table rather than to the session list, so
    that the Statistics panel, the three exports and the annotation editor --
    all of which read the database -- can see it. What this test guards is
    unchanged: an ordinary drag plus Add ROI stores exactly that geometry, and
    says nothing in the measurement channel.
    """
    db = notebook_defs["db"]
    slide_id = notebook_defs["get_slide_id"]()

    def _stored():
        if db.enabled and slide_id is not None:
            return [
                (r["kind"], tuple(r["bbox"]))
                for r in db.roi_repo.for_slide(slide_id)
            ]
        return [(r.kind, r.bbox()) for r in notebook_defs["get_rois"]()]

    before = _stored()
    _drag(viewer, "rect", [[300.0, 400.0], [700.0, 900.0]], 13)
    notebook_defs["ui_actions"]["add_roi"](None)

    added = _stored()[len(before):]
    assert added == [("rect", (300, 400, 700, 900))]
    # The measurement channel is for measurements; a save reports its own id
    # there, and must never leave a stale measurement behind.
    _msg = notebook_defs["get_measure_msg"]()
    assert _msg is None or _msg[0] == "success"
