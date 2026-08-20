"""Tests for the DB-backed agent tools in hescope.agent.agent_bridge
(annotate_roi / query_annotations / get_slide_info). Offline; tmp sqlite."""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import text

from hescope.agent.agent_bridge import (
    make_annotate_roi_tool,
    make_query_annotations_tool,
    make_slide_info_tool,
)
from hescope.store.db import InteractionRepo, ROIRepo, SlideRepo
from hescope.core.rois import ROI
from hescope.wsi.slides import PillowSource
from hescope.viewer.viewer import DBContext, bootstrap_db


@pytest.fixture()
def db(tmp_path):
    return bootstrap_db(f"sqlite:///{tmp_path}/tools.db")


@pytest.fixture()
def db_free():
    return DBContext(
        engine=None, slide_repo=None, roi_repo=None, run_repo=None,
        error="test: disabled",
    )


@pytest.fixture()
def slide_id(db):
    return SlideRepo(db.engine).register(
        source_kind="local", name="slide_a.png", path="/tmp/tools_a.png",
        width=1200, height=800, mpp=0.25,
    )


@pytest.fixture()
def source(tmp_path):
    arr = np.zeros((400, 600, 3), dtype=np.uint8)
    arr[..., 0] = 210
    p = tmp_path / "tool_slide.png"
    Image.fromarray(arr, "RGB").save(p)
    return PillowSource(p)


def _rect(x0=10.0, y0=20.0, x1=110.0, y1=220.0) -> ROI:
    return ROI(kind="rect", points=((x0, y0), (x1, y1)))


# --- annotate_roi ----------------------------------------------------------


def test_annotate_roi_success_and_interaction(db, slide_id):
    rid = ROIRepo(db.engine).add(slide_id, _rect(), label="", notes="orig")
    tool = make_annotate_roi_tool(lambda: db)
    out = json.loads(tool(rid, label="tumor", notes="agent wrote this"))
    assert out["id"] == rid
    assert out["label"] == "tumor"
    assert out["notes"] == "agent wrote this"
    assert out["slide_id"] == slide_id
    # DB row actually updated
    assert ROIRepo(db.engine).get(rid)["label"] == "tumor"
    # interaction trace: kind=label_set with roi/slide linkage
    recents = InteractionRepo(db.engine).recent(kind="label_set")
    assert len(recents) == 1
    assert recents[0]["roi_id"] == rid
    assert recents[0]["slide_id"] == slide_id
    payload = json.loads(recents[0]["payload"])
    assert payload["label"] == "tumor" and payload["roi_id"] == rid


def test_annotate_roi_partial_update(db, slide_id):
    repo = ROIRepo(db.engine)
    rid = repo.add(slide_id, _rect(), label="keep", notes="keep notes")
    tool = make_annotate_roi_tool(lambda: db)
    out = json.loads(tool(rid, notes="edited"))
    assert out["label"] == "keep"  # None left unchanged
    assert out["notes"] == "edited"


def test_annotate_roi_interaction_write_failure_is_logged_not_swallowed(
    db, slide_id, caplog
):
    """`_record_interaction` (agent_bridge.py) is documented to "swallow
    everything" so a trace write can never break the tool call it traces --
    but before this test the swallow was total silence. Reproduces a REAL
    failure (drop `interactions` out from under the live engine, a genuine
    sqlalchemy.exc.OperationalError) rather than a mock. annotate_roi must
    still succeed (the documented contract) AND the lost trace row must now
    be logged with the real exception text.
    """
    repo = ROIRepo(db.engine)
    rid = repo.add(slide_id, _rect(), label="", notes="orig")
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE interactions"))

    # The actual swallow-and-return-None lives in InteractionRepo.record
    # (hescope.store.db); _record_interaction's own try/except only sees an
    # exception if something before that call fails, which this scenario
    # does not exercise. Assert on the module that really emits the log.
    tool = make_annotate_roi_tool(lambda: db)
    with caplog.at_level(logging.WARNING, logger="hescope.store.db"):
        out = json.loads(tool(rid, label="tumor"))

    assert out["label"] == "tumor"  # the annotate itself is unaffected
    assert ROIRepo(db.engine).get(rid)["label"] == "tumor"

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("label_set" in r.message for r in warnings), caplog.text
    assert any(
        "OperationalError" in r.message or "no such table" in r.message
        for r in warnings
    ), caplog.text


def test_annotate_roi_missing_and_invalid(db, slide_id):
    tool = make_annotate_roi_tool(lambda: db)
    out = json.loads(tool(9999, label="x"))
    assert "error" in out and "9999" in out["error"]
    out = json.loads(tool("not-an-int", label="x"))
    assert "error" in out


def test_annotate_roi_db_free(db_free):
    tool = make_annotate_roi_tool(lambda: db_free)
    out = json.loads(tool(1, label="x"))
    assert "error" in out


def test_annotate_roi_never_raises(db):
    tool = make_annotate_roi_tool(lambda: db)

    class _ExplodingRepo:
        def get(self, rid):
            raise RuntimeError("boom")

    db.roi_repo = _ExplodingRepo()
    out = json.loads(tool(1, label="x"))
    assert "error" in out and "boom" in out["error"]


# --- query_annotations -----------------------------------------------------


def test_query_annotations_filters_and_limits(db, slide_id):
    repo = ROIRepo(db.engine)
    repo.add(slide_id, _rect(), label="tumor")
    repo.add(slide_id, _rect(0, 0, 5, 5), label="stroma")
    repo.add(slide_id, _rect(1, 1, 6, 6), label="tumor")
    other = SlideRepo(db.engine).register(
        source_kind="local", name="other.png", path="/tmp/tools_other.png",
        width=5, height=5,
    )
    repo.add(other, _rect(), label="tumor")  # different slide: excluded

    tool = make_query_annotations_tool(lambda: db, lambda: slide_id)
    all_rows = json.loads(tool())
    assert len(all_rows) == 3
    tumor = json.loads(tool(label="tumor"))
    assert [r["label"] for r in tumor] == ["tumor", "tumor"]
    assert all(r["slide_id"] == slide_id for r in tumor)
    limited = json.loads(tool(limit=1))
    assert len(limited) == 1
    # interaction trace: kind=tool_call
    recents = InteractionRepo(db.engine).recent(kind="tool_call")
    assert recents
    assert json.loads(recents[0]["payload"])["tool"] == "query_annotations"


def test_query_annotations_no_slide_returns_empty_list(db):
    tool = make_query_annotations_tool(lambda: db, lambda: None)
    assert tool() == "[]"


def test_query_annotations_db_free(db_free):
    tool = make_query_annotations_tool(lambda: db_free, lambda: 1)
    out = json.loads(tool())
    assert "error" in out


# --- get_slide_info --------------------------------------------------------


def test_get_slide_info_no_slide(db):
    tool = make_slide_info_tool(lambda: None, lambda: db, lambda: None)
    assert tool() == "NO_SLIDE"


def test_get_slide_info_with_slide(db, source, slide_id):
    ROIRepo(db.engine).add(slide_id, _rect(), label="tumor")
    ROIRepo(db.engine).add(slide_id, _rect(0, 0, 5, 5))
    tool = make_slide_info_tool(lambda: source, lambda: db, lambda: slide_id)
    info = json.loads(tool())
    assert info["name"] == "tool_slide.png"
    assert info["dimensions"] == [600, 400]
    assert info["mpp"] is None  # PillowSource has no mpp
    assert info["levels"] == len(source.level_downsamples)
    assert info["db_id"] == slide_id
    assert info["annotation_count"] == 2


def test_get_slide_info_db_free(db_free, source):
    tool = make_slide_info_tool(lambda: source, lambda: db_free, lambda: None)
    info = json.loads(tool())
    assert info["name"] == "tool_slide.png"
    assert info["db_id"] is None
    assert info["annotation_count"] is None
