"""Tests for hescope.db (unified database layer). Offline; tmp sqlite files."""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime

import pytest
import sqlalchemy as sa
from PIL import Image

from hescope import db as dbmod
from hescope.db import (
    DEFAULT_DB_URL,
    AgentRunRepo,
    InteractionRepo,
    ROIRepo,
    SlideRepo,
    export_rois,
    get_engine,
    init_db,
)
from hescope.rois import ROI


@pytest.fixture()
def engine(tmp_path):
    eng = get_engine(f"sqlite:///{tmp_path}/nested/dir/test.db")
    init_db(eng)
    return eng


@pytest.fixture()
def slide_id(engine):
    repo = SlideRepo(engine)
    return repo.register(
        source_kind="local", name="slide_a.png", path="/tmp/slide_a.png",
        width=1200, height=800, mpp=0.25, extra={"origin": "test"},
    )


def _rect(x0=10.0, y0=20.0, x1=110.0, y1=220.0) -> ROI:
    return ROI(kind="rect", points=((x0, y0), (x1, y1)))


# --- engine / config -------------------------------------------------------


def test_default_db_url_points_at_project_data_dir():
    assert DEFAULT_DB_URL.startswith("sqlite:///")
    expected = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(dbmod.__file__))),
        "data", "hescope.db",
    )
    assert DEFAULT_DB_URL == f"sqlite:///{expected}"


def test_get_engine_default_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv("HESCOPE_DB_URL", raising=False)
    eng = get_engine()
    assert str(eng.url) == DEFAULT_DB_URL
    eng.dispose()


def test_get_engine_env_override(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/env.db"
    monkeypatch.setenv("HESCOPE_DB_URL", url)
    eng = get_engine()
    assert str(eng.url) == url
    eng.dispose()


def test_get_engine_explicit_arg_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HESCOPE_DB_URL", f"sqlite:///{tmp_path}/env.db")
    url = f"sqlite:///{tmp_path}/explicit.db"
    eng = get_engine(url)
    assert str(eng.url) == url
    eng.dispose()


def test_get_engine_creates_sqlite_parent_dirs(tmp_path):
    db_file = tmp_path / "deep" / "deeper" / "x.db"
    assert not db_file.parent.exists()
    eng = get_engine(f"sqlite:///{db_file}")
    assert db_file.parent.is_dir()
    init_db(eng)
    assert db_file.exists()
    eng.dispose()


def test_get_engine_sqlite_memory_ok():
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    eng.dispose()


# --- SlideRepo -------------------------------------------------------------


def test_slide_register_and_get(engine):
    repo = SlideRepo(engine)
    sid = repo.register(
        source_kind="local", name="a.png", path="/p/a.png",
        width=100, height=50, mpp=0.5, extra={"k": "v"},
    )
    row = repo.get(sid)
    assert row is not None
    assert row["source_kind"] == "local"
    assert row["name"] == "a.png"
    assert row["width"] == 100 and row["height"] == 50
    assert row["mpp"] == pytest.approx(0.5)
    assert json.loads(row["extra_json"]) == {"k": "v"}
    # created_at is an ISO string parseable as a datetime
    assert datetime.fromisoformat(row["created_at"]) is not None
    assert repo.get(9999) is None


def test_slide_register_idempotent_refreshes_fields(engine):
    repo = SlideRepo(engine)
    sid1 = repo.register(
        source_kind="local", name="a.png", path="/p/dup.png", width=10, height=10
    )
    sid2 = repo.register(
        source_kind="tcga", name="renamed.png", path="/p/dup.png",
        width=20, height=30, mpp=0.25,
    )
    assert sid1 == sid2
    row = repo.get(sid1)
    assert row["name"] == "renamed.png"
    assert row["source_kind"] == "tcga"
    assert row["width"] == 20 and row["height"] == 30
    assert row["mpp"] == pytest.approx(0.25)
    assert len(repo.list()) == 1


def test_slide_find_by_path_and_list_filter(engine):
    repo = SlideRepo(engine)
    repo.register(source_kind="local", name="a.png", path="/p/a.png", width=1, height=1)
    repo.register(source_kind="tcga", name="b.svs", path="/p/b.svs", width=2, height=2)
    found = repo.find_by_path("/p/b.svs")
    assert found is not None and found["name"] == "b.svs"
    assert repo.find_by_path("/nope") is None
    assert len(repo.list()) == 2
    only_tcga = repo.list("tcga")
    assert [s["name"] for s in only_tcga] == ["b.svs"]


# --- ROIRepo ---------------------------------------------------------------


def test_roi_add_and_for_slide(engine, slide_id):
    repo = ROIRepo(engine)
    rid = repo.add(
        slide_id, _rect(), label="tumor", notes="first",
        patch_path="/tmp/patch.png", stats={"width_px": 100},
        magnification=10.0,
    )
    repo.add(slide_id, ROI(kind="circle", points=((50.0, 50.0), (80.0, 50.0))))
    rows = repo.for_slide(slide_id)
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["id"] == rid
    assert r0["kind"] == "rect"
    assert r0["label"] == "tumor"
    assert r0["notes"] == "first"
    assert r0["patch_path"] == "/tmp/patch.png"
    assert r0["magnification"] == pytest.approx(10.0)
    assert json.loads(r0["points_json"]) == [[10.0, 20.0], [110.0, 220.0]]
    assert r0["bbox"] == [10, 20, 110, 220]
    assert all(isinstance(v, int) for v in r0["bbox"])
    assert json.loads(r0["stats_json"]) == {"width_px": 100}
    assert datetime.fromisoformat(r0["created_at"]) is not None
    assert rows[1]["label"] == "" and rows[1]["notes"] == ""
    assert rows[1]["stats_json"] is None
    assert repo.for_slide(9999) == []


def test_roi_update_annotation_partial(engine, slide_id):
    repo = ROIRepo(engine)
    rid = repo.add(slide_id, _rect(), label="orig", notes="keep me")
    repo.update_annotation(rid, label="new label")
    row = repo.for_slide(slide_id)[0]
    assert row["label"] == "new label"
    assert row["notes"] == "keep me"  # None left it unchanged
    repo.update_annotation(rid, notes="edited")
    row = repo.for_slide(slide_id)[0]
    assert row["label"] == "new label"
    assert row["notes"] == "edited"
    # updating a non-existent ROI is a no-op
    repo.update_annotation(9999, label="x")


def test_roi_delete_and_search(engine, slide_id):
    repo = ROIRepo(engine)
    r1 = repo.add(slide_id, _rect(), label="a")
    r2 = repo.add(slide_id, _rect(0, 0, 5, 5), label="b")
    other_slide = SlideRepo(engine).register(
        source_kind="local", name="other.png", path="/p/other.png",
        width=5, height=5,
    )
    repo.add(other_slide, _rect(), label="a")
    assert {r["id"] for r in repo.search(label="a")} == {r1, repo.search(slide_id=other_slide)[0]["id"]}
    assert {r["id"] for r in repo.search(label="a", slide_id=slide_id)} == {r1}
    assert len(repo.search()) == 3
    repo.delete(r2)
    assert {r["id"] for r in repo.for_slide(slide_id)} == {r1}


def test_roi_cascade_delete_on_slide_delete(engine, slide_id):
    roi_repo = ROIRepo(engine)
    run_repo = AgentRunRepo(engine)
    rid = roi_repo.add(slide_id, _rect())
    run_id = run_repo.record(tool="t", input={}, output_text="o", roi_id=rid)
    SlideRepo(engine).delete(slide_id)
    assert roi_repo.for_slide(slide_id) == []
    # agent run survives but its roi_id was set to NULL
    runs = [r for r in run_repo.recent() if r["id"] == run_id]
    assert runs and runs[0]["roi_id"] is None


# --- AgentRunRepo ----------------------------------------------------------


def test_agent_run_record_for_roi_recent(engine, slide_id):
    roi_repo = ROIRepo(engine)
    run_repo = AgentRunRepo(engine)
    rid = roi_repo.add(slide_id, _rect())
    id1 = run_repo.record(
        tool="roi_submit", input={"roi_id": rid}, output_text="first",
        roi_id=rid, model="m1",
    )
    id2 = run_repo.record(tool="roi_submit", input={}, output_text="second", roi_id=rid)
    id3 = run_repo.record(tool="other", input={}, output_text="third", status="error")
    for_roi = run_repo.for_roi(rid)
    assert [r["id"] for r in for_roi] == [id1, id2]
    assert for_roi[0]["tool"] == "roi_submit"
    assert json.loads(for_roi[0]["input_json"]) == {"roi_id": rid}
    assert for_roi[0]["model"] == "m1"
    assert for_roi[0]["status"] == "ok"
    recent = run_repo.recent()
    assert [r["id"] for r in recent] == [id3, id2, id1]  # newest first
    assert recent[0]["status"] == "error"
    assert [r["id"] for r in run_repo.recent(limit=2)] == [id3, id2]
    assert datetime.fromisoformat(recent[0]["created_at"]) is not None


def test_agent_run_roi_id_set_null_on_roi_delete(engine, slide_id):
    roi_repo = ROIRepo(engine)
    run_repo = AgentRunRepo(engine)
    rid = roi_repo.add(slide_id, _rect())
    run_repo.record(tool="t", input={}, output_text="o", roi_id=rid)
    roi_repo.delete(rid)
    runs = run_repo.recent()
    assert len(runs) == 1
    assert runs[0]["roi_id"] is None


# --- InteractionRepo -------------------------------------------------------


def test_interaction_record_recent_for_slide(engine, slide_id):
    repo = InteractionRepo(engine)
    other_slide = SlideRepo(engine).register(
        source_kind="local", name="other.png", path="/p/other2.png",
        width=5, height=5,
    )
    id1 = repo.record(
        kind="selection_view",
        payload={"bbox_level0": [0, 0, 10, 10]},
        session_tag="sess-1",
        slide_id=slide_id,
    )
    id2 = repo.record(
        kind="label_set", payload={"label": "tumor"},
        slide_id=slide_id, roi_id=123,
    )
    id3 = repo.record(kind="tool_call", payload={"tool": "query_annotations"},
                      slide_id=other_slide)
    assert id1 is not None and id2 is not None and id3 is not None

    recent = repo.recent()
    assert [r["id"] for r in recent] == [id3, id2, id1]  # newest first
    r = recent[1]
    assert r["kind"] == "label_set"
    assert r["session_tag"] is None
    assert r["slide_id"] == slide_id
    assert r["roi_id"] == 123
    assert json.loads(r["payload"]) == {"label": "tumor"}
    assert datetime.fromisoformat(r["created_at"]) is not None

    assert [r["id"] for r in repo.recent(limit=1)] == [id3]
    assert [r["id"] for r in repo.recent(kind="label_set")] == [id2]

    for_slide = repo.for_slide(slide_id)
    assert [r["id"] for r in for_slide] == [id1, id2]  # oldest first
    assert for_slide[0]["session_tag"] == "sess-1"
    assert repo.for_slide(9999) == []


def test_interaction_record_defaults_and_bad_payload(engine):
    repo = InteractionRepo(engine)
    rid = repo.record(kind="human_gate")
    assert rid is not None
    row = repo.recent()[0]
    assert row["payload"] == "{}"
    assert row["session_tag"] is None
    assert row["slide_id"] is None and row["roi_id"] is None


def test_interaction_repo_exception_safe(tmp_path):
    # engine whose schema was never initialized: everything degrades, no raise
    eng = get_engine(f"sqlite:///{tmp_path}/uninitialized.db")
    repo = InteractionRepo(eng)
    assert repo.record(kind="tool_call") is None
    assert repo.recent() == []
    assert repo.for_slide(1) == []
    eng.dispose()


def test_interaction_slide_id_set_null_on_slide_delete(engine, slide_id):
    repo = InteractionRepo(engine)
    iid = repo.record(kind="roi_submit", slide_id=slide_id)
    SlideRepo(engine).delete(slide_id)
    rows = [r for r in repo.recent() if r["id"] == iid]
    assert rows and rows[0]["slide_id"] is None


def test_roi_repo_get(engine, slide_id):
    repo = ROIRepo(engine)
    rid = repo.add(slide_id, _rect(), label="g", notes="n")
    row = repo.get(rid)
    assert row is not None
    assert row["id"] == rid
    assert row["label"] == "g" and row["notes"] == "n"
    assert row["bbox"] == [10, 20, 110, 220]
    assert repo.get(9999) is None


# --- export_rois -----------------------------------------------------------


def test_export_rois_json(engine, slide_id):
    roi_repo = ROIRepo(engine)
    roi_repo.add(slide_id, _rect(), label="x", patch_path="/p.png")
    other = SlideRepo(engine).register(
        source_kind="tcga", name="b.svs", path="/p/b.svs", width=1, height=1
    )
    roi_repo.add(other, _rect(0, 0, 3, 3))
    all_rows = json.loads(export_rois(engine))
    assert len(all_rows) == 2
    assert all_rows[0]["slide_name"] == "slide_a.png"
    assert all_rows[0]["bbox"] == [10, 20, 110, 220]
    filtered = json.loads(export_rois(engine, slide_id=other))
    assert len(filtered) == 1
    assert filtered[0]["slide_name"] == "b.svs"


def test_export_rois_csv(engine, slide_id):
    roi_repo = ROIRepo(engine)
    roi_repo.add(slide_id, _rect(), label="csv,label", notes="multi\nline")
    text = export_rois(engine, fmt="csv")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 1
    assert rows[0]["slide_name"] == "slide_a.png"
    assert rows[0]["label"] == "csv,label"
    assert rows[0]["bbox_json"] == "[10, 20, 110, 220]"
    with pytest.raises(ValueError):
        export_rois(engine, fmt="xml")  # type: ignore[arg-type]


# --- AgentBridge integration ----------------------------------------------


@pytest.fixture()
def png_source(tmp_path):
    import numpy as np

    from hescope.slides import PillowSource

    arr = np.zeros((200, 300, 3), dtype=np.uint8)
    arr[..., 0] = 200
    arr[..., 2] = 160
    p = tmp_path / "bridge_src.png"
    Image.fromarray(arr, "RGB").save(p)
    return PillowSource(p)


def test_agent_bridge_with_repository_persists(engine, slide_id, png_source, tmp_path):
    from hescope.agent_bridge import AgentBridge

    bridge = AgentBridge(tmp_path / "out", repository=ROIRepo(engine), slide_id=slide_id)
    payload = bridge.submit(png_source, _rect())
    assert payload.roi_id is not None
    rows = ROIRepo(engine).for_slide(slide_id)
    assert len(rows) == 1
    assert rows[0]["id"] == payload.roi_id
    assert rows[0]["patch_path"] == payload.patch_path
    assert json.loads(rows[0]["stats_json"]) == payload.stats
    # roi_id is part of the serialized payload/history
    assert json.loads(payload.to_json())["roi_id"] == payload.roi_id
    assert bridge.latest().roi_id == payload.roi_id


def test_agent_bridge_repository_without_slide_id_raises(engine, png_source, tmp_path):
    from hescope.agent_bridge import AgentBridge

    bridge = AgentBridge(tmp_path / "out", repository=ROIRepo(engine), slide_id=None)
    with pytest.raises(ValueError, match="slide_id"):
        bridge.submit(png_source, _rect())


def test_agent_bridge_without_repository_unchanged(png_source, tmp_path):
    from hescope.agent_bridge import AgentBridge

    bridge = AgentBridge(tmp_path / "out")
    payload = bridge.submit(png_source, _rect())
    assert payload.roi_id is None
    assert json.loads(payload.to_json())["roi_id"] is None


def test_roipayload_from_json_tolerates_missing_roi_id(png_source, tmp_path):
    from hescope.agent_bridge import AgentBridge, ROIPayload

    bridge = AgentBridge(tmp_path / "out")
    payload = bridge.submit(png_source, _rect())
    legacy = json.loads(payload.to_json())
    del legacy["roi_id"]
    rt = ROIPayload.from_json(json.dumps(legacy))
    assert rt.roi_id is None
    assert rt.slide_name == payload.slide_name
