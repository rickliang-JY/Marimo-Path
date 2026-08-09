"""Phase-3 Part C integration tests (offline, tmp dirs only).

Covers the app wiring helpers in hescope.viewer (DB bootstrap with graceful
degradation, display pipeline ordering, jump-to-ROI math, DB row -> ROI
geometry) and the SPEC-PHASE3 C.2 item 6 end-to-end flow: register slide ->
add ROI -> AgentBridge(repository=...).submit -> AgentRunRepo recorded ->
export_rois json contains the ROI.
"""

from __future__ import annotations

import csv
import io
import json

import numpy as np
import pytest
from PIL import Image

from hescope.agent_bridge import AgentBridge
from hescope.db import AgentRunRepo, ROIRepo, SlideRepo, export_rois
from hescope.rois import ROI, ViewportState
from hescope.slides import open_slide
from hescope.viewer import (
    apply_display_pipeline,
    bootstrap_db,
    jump_viewport_for_bbox,
    roi_from_db_row,
)


def _make_slide(tmp_path, size=(512, 384), seed=0) -> str:
    """Write a small synthetic RGB slide image and return its path."""
    rng = np.random.default_rng(seed)
    arr = (rng.random((size[1], size[0], 3)) * 255).astype(np.uint8)
    path = tmp_path / "slide.png"
    Image.fromarray(arr, "RGB").save(path)
    return str(path)


# ---------------------------------------------------------------------------
# DB bootstrap (graceful degradation)
# ---------------------------------------------------------------------------


def test_bootstrap_db_ok(tmp_path):
    ctx = bootstrap_db(f"sqlite:///{tmp_path / 'sub' / 'hescope.db'}")
    assert ctx.enabled
    assert ctx.error is None
    assert ctx.engine is not None
    assert ctx.slide_repo is not None and ctx.roi_repo is not None
    assert ctx.run_repo is not None
    # schema initialized: registering a slide works immediately
    sid = ctx.slide_repo.register(
        source_kind="local", name="s", path="/tmp/s.png", width=10, height=10
    )
    assert ctx.slide_repo.get(sid)["name"] == "s"


def test_bootstrap_db_failure_gives_db_free_mode():
    # postgresql URL with no driver installed / unreachable host must be
    # caught by the bootstrap helper: DB-free mode, never an exception.
    ctx = bootstrap_db("postgresql://invalid:5432/x")
    assert not ctx.enabled
    assert ctx.engine is None
    assert ctx.slide_repo is None and ctx.roi_repo is None
    assert ctx.run_repo is None
    assert ctx.error  # non-empty, human-readable description


# ---------------------------------------------------------------------------
# End-to-end (SPEC-PHASE3 C.2 item 6)
# ---------------------------------------------------------------------------


def test_end_to_end_register_submit_record_export(tmp_path):
    ctx = bootstrap_db(f"sqlite:///{tmp_path / 'hescope.db'}")
    assert ctx.enabled

    # 1. register the slide
    slide_path = _make_slide(tmp_path)
    src = open_slide(slide_path)
    sid = ctx.slide_repo.register(
        source_kind="local",
        name=src.name,
        path=slide_path,
        width=src.dimensions[0],
        height=src.dimensions[1],
        mpp=src.mpp,
    )
    assert ctx.slide_repo.get(sid)["path"] == slide_path

    # 2. add an ROI through the repo
    roi = ROI(kind="rect", points=((10.0, 20.0), (110.0, 120.0)))
    repo_roi_id = ctx.roi_repo.add(sid, roi, label="tumor")
    rows = ctx.roi_repo.for_slide(sid)
    assert len(rows) == 1 and rows[0]["label"] == "tumor"

    # 3. bridge submit with repository persists a second ROI row
    bridge = AgentBridge(
        tmp_path / "agent_out", repository=ctx.roi_repo, slide_id=sid
    )
    payload = bridge.submit(src, roi)
    assert payload.roi_id is not None
    assert payload.roi_id != repo_roi_id
    persisted = {r["id"]: r for r in ctx.roi_repo.for_slide(sid)}
    assert payload.roi_id in persisted
    assert persisted[payload.roi_id]["patch_path"] == payload.patch_path
    # jsonl history still written (phase-1 behavior preserved)
    assert bridge.latest() is not None

    # 4. agent run recorded
    run_id = ctx.run_repo.record(
        tool="roi_submit",
        input={"slide_name": payload.slide_name, "kind": payload.roi["kind"]},
        output_text=payload.to_agent_prompt(),
        roi_id=payload.roi_id,
    )
    recent = ctx.run_repo.recent(20)
    assert recent[0]["id"] == run_id
    assert recent[0]["tool"] == "roi_submit"
    assert recent[0]["roi_id"] == payload.roi_id
    assert "HE-Scope ROI selection" in recent[0]["output_text"]
    assert ctx.run_repo.for_roi(payload.roi_id)[0]["id"] == run_id

    # 5. export contains the ROIs (json + csv)
    data = json.loads(export_rois(ctx.engine, slide_id=sid, fmt="json"))
    ids = {d["id"] for d in data}
    assert {repo_roi_id, payload.roi_id} <= ids
    assert all(d["slide_name"] == src.name for d in data)

    csv_text = export_rois(ctx.engine, slide_id=sid, fmt="csv")
    csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert {int(r["id"]) for r in csv_rows} == ids


def test_end_to_end_works_without_repository(tmp_path):
    # DB-free parity: a bridge without a repository behaves exactly as
    # phase-1 (jsonl only, roi_id stays None).
    slide_path = _make_slide(tmp_path)
    src = open_slide(slide_path)
    bridge = AgentBridge(tmp_path / "out")
    payload = bridge.submit(src, ROI(kind="rect", points=((0, 0), (50, 50))))
    assert payload.roi_id is None
    assert bridge.latest() is not None


# ---------------------------------------------------------------------------
# Display pipeline (overlays drawn AFTER adjustments/channel view)
# ---------------------------------------------------------------------------


def _uniform_source(tmp_path, gray=60):
    path = tmp_path / "uniform.png"
    Image.new("RGB", (256, 256), (gray, gray, gray)).save(path)
    return open_slide(str(path))


def test_pipeline_overlay_stays_crisp_after_adjustments(tmp_path):
    src = _uniform_source(tmp_path, gray=60)
    vp = ViewportState(center=(128.0, 128.0), downsample=1.0, size=(256, 256))
    base = src.read_region((0, 0), 0, (256, 256))
    roi = ROI(kind="rect", points=((64.0, 64.0), (192.0, 192.0)))

    out = apply_display_pipeline(
        base, vp, brightness=2.0, rois=[roi], show_overlays=True
    )
    px = out.convert("RGB").getpixel((64, 128))  # on the ROI outline
    bg = out.convert("RGB").getpixel((10, 10))
    assert px == (255, 60, 60)  # exact overlay color, not brightened
    assert bg == (120, 120, 120)  # background WAS brightened (60 * 2)


def test_pipeline_overlay_highlight_selected(tmp_path):
    src = _uniform_source(tmp_path)
    vp = ViewportState(center=(128.0, 128.0), downsample=1.0, size=(256, 256))
    base = src.read_region((0, 0), 0, (256, 256))
    rois = [
        ROI(kind="rect", points=((32.0, 32.0), (96.0, 96.0))),
        ROI(kind="rect", points=((128.0, 128.0), (224.0, 224.0))),
    ]
    out = apply_display_pipeline(base, vp, rois=rois, selected_index=1)
    rgb = out.convert("RGB")
    assert rgb.getpixel((128, 176)) == (60, 200, 60)  # selected outline
    assert rgb.getpixel((32, 64)) == (255, 60, 60)  # normal outline


def test_pipeline_channel_view_and_toggle(tmp_path):
    src = _uniform_source(tmp_path)
    vp = ViewportState(center=(128.0, 128.0), downsample=1.0, size=(256, 256))
    base = src.read_region((0, 0), 0, (256, 256))
    roi = ROI(kind="rect", points=((64.0, 64.0), (192.0, 192.0)))

    # overlays off: no overlay pixels even though ROIs are given
    out_off = apply_display_pipeline(base, vp, rois=[roi], show_overlays=False)
    assert out_off.convert("RGB").getpixel((64, 128)) != (255, 60, 60)

    # channel view applied before overlays; result stays displayable
    out_r = apply_display_pipeline(base, vp, channel="r", rois=[roi])
    assert out_r.mode == "RGB"  # draw_rois converts back to RGB
    out_h = apply_display_pipeline(base, vp, channel="hematoxylin")
    assert out_h.size == (256, 256)


# ---------------------------------------------------------------------------
# Jump-to-ROI math
# ---------------------------------------------------------------------------


def test_jump_viewport_for_bbox_fill_and_clamp():
    # 2048x1536 bbox in a 1024x768 viewport -> exactly 2.5x to fill 80%
    center, ds = jump_viewport_for_bbox(
        (0, 0, 2048, 1536), (1024, 768), max_downsample=16.0
    )
    assert center == (1024.0, 768.0)
    assert ds == pytest.approx(2.5)


def test_jump_viewport_for_bbox_clamps_to_valid_range():
    # tiny bbox -> clamped to 1.0 (native res)
    _, ds = jump_viewport_for_bbox((0, 0, 10, 10), (1024, 768), max_downsample=16.0)
    assert ds == 1.0
    # huge bbox -> clamped to the slide's max downsample
    _, ds = jump_viewport_for_bbox(
        (0, 0, 100000, 100000), (1024, 768), max_downsample=8.0
    )
    assert ds == 8.0
    # degenerate bbox does not blow up
    center, ds = jump_viewport_for_bbox((5, 5, 5, 5), (1024, 768), max_downsample=4.0)
    assert center == (5.0, 5.0) and ds >= 1.0


# ---------------------------------------------------------------------------
# DB row -> ROI geometry (annotation browser overlays)
# ---------------------------------------------------------------------------


def test_roi_from_db_row_roundtrip(tmp_path):
    ctx = bootstrap_db(f"sqlite:///{tmp_path / 'x.db'}")
    sid = ctx.slide_repo.register(
        source_kind="local", name="s", path="/p.png", width=100, height=100
    )
    roi = ROI(kind="polygon", points=((1.5, 2.5), (30.0, 40.0), (50.5, 5.0)))
    rid = ctx.roi_repo.add(sid, roi)
    row = [r for r in ctx.roi_repo.for_slide(sid) if r["id"] == rid][0]
    geom = roi_from_db_row(row)
    assert geom.kind == "polygon"
    assert geom.points == roi.points
    assert geom.bbox() == roi.bbox()


def test_annotation_update_and_delete(tmp_path):
    # Same repo calls the Save/Delete buttons use.
    ctx = bootstrap_db(f"sqlite:///{tmp_path / 'x.db'}")
    sid = ctx.slide_repo.register(
        source_kind="upload", name="s", path="/p2.png", width=100, height=100
    )
    rid = ctx.roi_repo.add(sid, ROI(kind="rect", points=((0, 0), (10, 10))))
    ctx.roi_repo.update_annotation(rid, label="stroma", notes="near capsule")
    row = ctx.roi_repo.for_slide(sid)[0]
    assert row["label"] == "stroma" and row["notes"] == "near capsule"
    ctx.roi_repo.delete(rid)
    assert ctx.roi_repo.for_slide(sid) == []
