"""R08-2: the button labelled "Add ROI" must actually add the ROI.

Round 08's lens is the gap between what the code can do and what the user can
reach. This was the widest one. Measured across the four handlers that touch ROI
state before the fix:

    _add_roi_or_measure  ("Add ROI")             session list only
    _on_send             ("Send to code agent")  session + rois table + PNG + jsonl

and everything else in the app reads the DATABASE: the Statistics panel
(``roi_stats_rows(engine, slide_id)``), all three exports (``export_rois``,
``slide_geojson_text``) and the annotation editor (``db_roi_rows``).

So an ROI "added" with Add ROI was absent from the statistics, absent from every
export, could not be labelled, and was gone at the next restart — while the
button that did save was the one named after sending it to an agent. That is
what produced the user-reported screenshot: four outlines on the image and a
sidebar reading "No ROIs yet".

Driven through app.py's own compiled cell body, with marimo's cell-private names
dropped before the click, so the test exercises the same code path the browser
does (see tests/test_toolbar_actions.py for why that matters).
"""

from __future__ import annotations

import pytest

from hescope.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.rois import ROI


@pytest.fixture()
def engine(tmp_path):
    eng = get_engine(f"sqlite:///{(tmp_path / 'h.db').as_posix()}")
    init_db(eng)
    return eng


@pytest.fixture()
def slide_id(engine):
    return SlideRepo(engine).register(
        source_kind="local", name="s.svs", path="s.svs",
        width=4000, height=3000, mpp=0.25,
    )


class _Check:
    def __init__(self, value=False):
        self.value = value


class _DB:
    def __init__(self, engine=None, *, enabled=True, broken=False):
        self.enabled = enabled
        self.engine = engine
        self.roi_repo = _BrokenRepo() if broken else (
            ROIRepo(engine) if engine is not None else None
        )


class _BrokenRepo:
    def add(self, *a, **kw):
        raise RuntimeError("database is gone")


def _click_add_roi(*, db, slide_id, selection=None, circle=False, measure=False):
    """Run app.py's ROI cell, drop the cell-private names as marimo does, click.

    Returns (session_rois, published_messages, ann_version_bumps).
    """
    import app as appmod

    from hescope.rois import ROI as _ROI

    appmod.app._maybe_initialize()
    cell = appmod.app._graph.cells["ZBYS"]

    session: list = []
    published: list = []
    bumps: list = []

    ns: dict = {
        "ROI": _ROI,
        "circle_checkbox": _Check(circle),
        "measure_checkbox": _Check(measure),
        "db": db,
        "format_measurement": lambda m: "measured",
        "get_rois": lambda: list(session),
        "get_slide_id": lambda: slide_id,
        "get_source": lambda: None,
        "live_measure": lambda: None,
        "live_selection": lambda: selection,
        "measure_box": lambda a, b, mpp: {},
        "set_ann_version": bumps.append,
        "set_measure_msg": published.append,
        "set_rois": lambda rois: session.__setitem__(slice(None), rois),
        "ui_actions": (actions := {}),
    }
    exec(cell.body, ns)
    for key in [k for k in ns if k.startswith("_cell_")]:
        del ns[key]

    actions["add_roi"](object())
    return session, published, bumps


SEL = {"kind": "rect", "points_level0": ((10.0, 20.0), (110.0, 100.0))}


# --- the fix ---------------------------------------------------------------


def test_add_roi_writes_a_row_the_rest_of_the_app_can_see(engine, slide_id):
    session, published, bumps = _click_add_roi(
        db=_DB(engine), slide_id=slide_id, selection=SEL
    )

    rows = ROIRepo(engine).for_slide(slide_id)
    assert len(rows) == 1, (
        "Add ROI did not reach the rois table, so the Statistics panel, all "
        "three exports and the annotation editor cannot see this ROI"
    )
    assert rows[0]["kind"] == "rect"
    assert rows[0]["bbox"] == [10, 20, 110, 100]
    assert bumps, "the panels are not told to refresh, so nothing appears until a re-run"
    assert published and published[-1][0] == "success"
    assert str(rows[0]["id"]) in published[-1][1], "the message must name the row"


def test_the_saved_roi_survives_a_new_session(engine, slide_id):
    """The whole point: a fresh session reads the database, not the old list."""
    _click_add_roi(db=_DB(engine), slide_id=slide_id, selection=SEL)

    fresh_session, _p, _b = _click_add_roi(
        db=_DB(engine), slide_id=slide_id, selection=SEL
    )
    assert fresh_session == [], "the session list is not the store any more"
    assert len(ROIRepo(engine).for_slide(slide_id)) == 2


def test_a_circle_selection_is_saved_as_a_circle(engine, slide_id):
    _click_add_roi(db=_DB(engine), slide_id=slide_id, selection=SEL, circle=True)
    assert ROIRepo(engine).for_slide(slide_id)[0]["kind"] == "circle"


# --- and the paths that must NOT change ------------------------------------


def test_db_free_mode_still_uses_the_session_list(slide_id):
    """``db.enabled`` is False when bootstrap_db degrades. There is nowhere
    else to put the ROI, so the old behaviour is the correct behaviour."""
    session, published, _b = _click_add_roi(
        db=_DB(None, enabled=False), slide_id=None, selection=SEL
    )
    assert len(session) == 1 and isinstance(session[0], ROI)
    assert published[-1] is None, "the DB-free path clears the strip, as before"


def test_with_no_slide_open_it_falls_back_rather_than_losing_the_roi(engine):
    session, _p, _b = _click_add_roi(db=_DB(engine), slide_id=None, selection=SEL)
    assert len(session) == 1, "an ROI with no slide to attach to must not vanish"


def test_a_failed_write_is_reported_and_not_counted_as_saved(engine, slide_id):
    session, published, bumps = _click_add_roi(
        db=_DB(engine, broken=True), slide_id=slide_id, selection=SEL
    )
    kind, text = published[-1]
    assert kind == "danger" and "database is gone" in text
    assert not bumps, "a failed write must not tell the panels to refresh"
    assert session == [], "and must not silently fall back to the session list"


def test_no_selection_still_says_so(engine, slide_id):
    _s, published, _b = _click_add_roi(
        db=_DB(engine), slide_id=slide_id, selection=None
    )
    assert published[-1][0] == "warn" and "No selection" in published[-1][1]
    assert ROIRepo(engine).for_slide(slide_id) == []
