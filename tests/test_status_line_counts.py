"""R09-1: the status strip must not report 0 ROIs on a slide that has some.

Round 09's lens is what the screen tells the user versus what is true. This one
was **introduced by round 08's own fix** and caught in the same session, which
is the whole argument for the lens: R08-2 moved "Add ROI" from the session list
to the database, and the strip under the viewer kept counting

    _n_rois = len(get_rois())        # the session list
    f"... | {_n_rois} ROI(s) this session | ..."

so after the fix the counter reads 0 no matter how many ROIs the user has added
— while the viewer draws every one of them, because the overlay reads
``session + persisted``.

Exactly the class bugs/SUMMARY.md names as *a second place re-deriving what one
owner decides*, and the same split that made the ROIs panel say "No ROIs yet"
over four visible outlines.
"""

from __future__ import annotations

import pytest

from hescope.rois import ROI, ViewportState


class _MO:
    """Captures what the strip renders."""

    def __init__(self):
        self.texts: list[str] = []

    def md(self, text=""):
        self.texts.append(str(text))
        return ("md", text)

    def callout(self, body, kind="info"):
        return ("callout", body, kind)

    def vstack(self, parts):
        return ("vstack", parts)


class _Source:
    dimensions = (81671, 18211)
    mpp = 0.2526


class _DB:
    enabled = True


def _render(*, session_rois, saved_rows, selection=None):
    """Run app.py's own status-strip cell; returns the markdown it emitted."""
    import app as appmod

    appmod.app._maybe_initialize()
    cell = appmod.app._graph.cells["Pvdt"]

    mo = _MO()
    ns: dict = {
        "db": _DB(),
        "db_roi_rows": list(saved_rows),
        "db_status_detail": None,
        "get_db_msg": lambda: None,
        "get_measure_msg": lambda: None,
        "get_rois": lambda: list(session_rois),
        "get_source": lambda: _Source(),
        "get_vp": lambda: ViewportState(
            center=(40835.0, 9105.0), downsample=16.0, size=(1702, 820)
        ),
        "live_selection": lambda: selection,
        "magnification_for": lambda mpp, ds: 12.3,
        "mo": mo,
    }
    missing = [
        r for r in cell.refs
        if r not in ns and r not in ("Exception", "len", "float", "int", "str")
    ]
    assert not missing, f"the status cell grew new dependencies: {missing}"
    exec(cell.body, ns)
    return "\n".join(mo.texts)


def _rect(x=10.0):
    return ROI(kind="rect", points=((x, 20.0), (x + 100.0, 100.0)))


def _row(i):
    return {"id": i, "kind": "rect", "bbox": [0, 0, 10, 10], "label": ""}


# --- the regression --------------------------------------------------------


def test_saved_rois_are_counted_not_reported_as_zero():
    text = _render(session_rois=[], saved_rows=[_row(1), _row(2), _row(3)])
    assert "0 ROI" not in text, (
        "three ROIs are saved on this slide and drawn on the image, and the "
        f"strip says zero: {text!r}"
    )
    assert "3 ROI(s) on this slide" in text


def test_an_empty_slide_still_reads_zero():
    assert "0 ROI(s) on this slide" in _render(session_rois=[], saved_rows=[])


def test_session_and_saved_are_both_accounted_for():
    """DB-free mode keeps using the session list, so both must be reported,
    and the breakdown has to say which is which."""
    text = _render(session_rois=[_rect()], saved_rows=[_row(1), _row(2)])
    assert "3 ROI(s) on this slide" in text
    assert "2 saved" in text and "1 this session" in text, (
        f"one drawn this session plus two saved; the strip shows: {text!r}"
    )


# --- what must not regress -------------------------------------------------


def test_the_selection_readout_survives():
    sel = {
        "kind": "rect",
        "bbox_level0": [11443, 10697, 12883, 11991],
        "points_level0": ((11443.0, 10697.0), (12883.0, 11991.0)),
    }
    text = _render(session_rois=[], saved_rows=[], selection=sel)
    assert "selection: rect 1440x1294 px" in text
    # 1440 * 0.2526 = 363.7 -> 364; 1294 * 0.2526 = 326.9 -> 327. From the
    # LEVEL-0 bbox, never the patch dimensions (R07-2).
    assert "364x327 um" in text
    assert "not added yet" in text


def test_no_selection_says_so():
    text = _render(session_rois=[], saved_rows=[])
    assert "no selection" in text


@pytest.mark.parametrize("n", [1, 7, 42])
def test_the_count_tracks_the_database(n):
    """Asserted on the whole phrase: a bare `str(n) in text` passes by luck on
    `viewport 1702x820`, which is how a test comes to pass on broken code."""
    text = _render(session_rois=[], saved_rows=[_row(i) for i in range(n)])
    assert f"{n} ROI(s) on this slide" in text
