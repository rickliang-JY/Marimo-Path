"""hescope.viewer.viewer.viewport_status_line: the status-strip text that used
to be built inline inside app.py's status-line cell.

Moved out (design doc §9.2 extraction, HE-Scope-设计文档-数据与Harness.md) so this
string-formatting logic sits in hescope's own test suite instead of only being
reachable by executing app.py's cell body. tests/test_status_line_counts.py
still exists and still executes the real cell -- it now asserts the cell hands
off to this function and checks the resulting markdown end-to-end; this module
tests the function directly, the way any other hescope helper is tested.

The assertions below are the same regressions the original inline comments in
app.py documented (R07-2 um-from-level-0, R09-1/R08-2 saved+session counting,
R09-2 selection-vs-saved wording) -- re-asserted here against the real
function, not a stub.
"""

from __future__ import annotations

import pytest

from hescope.core.rois import ROI, ViewportState
from hescope.viewer.viewer import viewport_status_line


class _Source:
    dimensions = (81671, 18211)
    mpp = 0.2526


def _vp():
    return ViewportState(center=(40835.0, 9105.0), downsample=16.0, size=(1702, 820))


def _row(i):
    return {"id": i, "kind": "rect", "bbox": [0, 0, 10, 10], "label": ""}


def _rect(x=10.0):
    return ROI(kind="rect", points=((x, 20.0), (x + 100.0, 100.0)))


def test_saved_rois_are_counted_not_reported_as_zero():
    text = viewport_status_line(_Source(), _vp(), None, [_row(1), _row(2), _row(3)], [])
    assert "0 ROI" not in text, (
        "three ROIs are saved on this slide and drawn on the image, and the "
        f"strip says zero: {text!r}"
    )
    assert "3 ROI(s) on this slide" in text


def test_an_empty_slide_still_reads_zero():
    text = viewport_status_line(_Source(), _vp(), None, [], [])
    assert "0 ROI(s) on this slide" in text


def test_session_and_saved_are_both_accounted_for():
    """DB-free mode keeps using the session list, so both must be reported,
    and the breakdown has to say which is which."""
    text = viewport_status_line(_Source(), _vp(), None, [_row(1), _row(2)], [_rect()])
    assert "3 ROI(s) on this slide" in text
    assert "2 saved" in text and "1 this session" in text, (
        f"one drawn this session plus two saved; the strip shows: {text!r}"
    )


def test_the_selection_readout_reports_um_from_level0_not_the_patch():
    sel = {
        "kind": "rect",
        "bbox_level0": [11443, 10697, 12883, 11991],
        "points_level0": ((11443.0, 10697.0), (12883.0, 11991.0)),
    }
    text = viewport_status_line(_Source(), _vp(), sel, [], [])
    assert "selection: rect 1440x1294 px" in text
    # 1440 * 0.2526 = 363.7 -> 364; 1294 * 0.2526 = 326.9 -> 327. From the
    # LEVEL-0 bbox, never the patch dimensions (R07-2).
    assert "364x327 um" in text
    assert "not added yet" in text


def test_a_selection_matching_a_saved_bbox_reads_added():
    sel = {
        "kind": "rect",
        "bbox_level0": [0.0, 0.0, 10.0, 10.0],
        "points_level0": ((0.0, 0.0), (10.0, 10.0)),
    }
    text = viewport_status_line(_Source(), _vp(), sel, [_row(1)], [])
    assert "— added" in text


def test_no_selection_says_so():
    text = viewport_status_line(_Source(), _vp(), None, [], [])
    assert "no selection" in text


@pytest.mark.parametrize("n", [1, 7, 42])
def test_the_count_tracks_the_database(n):
    """Asserted on the whole phrase: a bare `str(n) in text` passes by luck on
    `viewport 1702x820`, which is how a test comes to pass on broken code."""
    text = viewport_status_line(_Source(), _vp(), None, [_row(i) for i in range(n)], [])
    assert f"{n} ROI(s) on this slide" in text
