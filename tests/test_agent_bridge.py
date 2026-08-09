"""Tests for hescope.agent_bridge."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from hescope.agent_bridge import (
    AgentBridge,
    ROIPayload,
    magnification_for,
    make_marimo_tool,
)
from hescope.rois import ROI
from hescope.slides import PillowSource


@pytest.fixture()
def source(tmp_path):
    arr = np.zeros((800, 1200, 3), dtype=np.uint8)
    arr[..., 0] = 220
    arr[..., 1] = 140
    arr[..., 2] = 180
    p = tmp_path / "bridge_slide.png"
    Image.fromarray(arr, "RGB").save(p)
    return PillowSource(p)


def test_submit_writes_patch_and_jsonl(source, tmp_path):
    bridge = AgentBridge(tmp_path / "out")
    roi = ROI(kind="rect", points=((10.0, 20.0), (110.0, 220.0)))
    payload = bridge.submit(source, roi)
    assert isinstance(payload, ROIPayload)
    # patch PNG written under out_dir/patches with the naming convention
    patch_file = tmp_path / "out" / "patches" / f"{payload.created_at and ''}"
    assert payload.patch_path.endswith("_rect_10_20_110_220.png")
    saved = list((tmp_path / "out" / "patches").glob("*.png"))
    assert len(saved) == 1
    assert str(saved[0].resolve()) == payload.patch_path
    with Image.open(payload.patch_path) as im:
        assert im.size == (payload.stats["width_px"], payload.stats["height_px"])
    # jsonl appended
    hist_path = tmp_path / "out" / "roi_history.jsonl"
    lines = hist_path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["slide_name"] == "bridge_slide.png"
    # payload fields
    assert payload.slide_dimensions == (1200, 800)
    assert payload.roi["kind"] == "rect"
    assert payload.roi["bbox_level0"] == [10, 20, 110, 220]
    assert payload.roi["points_level0"] == [[10.0, 20.0], [110.0, 220.0]]
    assert payload.mpp is None
    assert payload.magnification is None
    # history / latest
    assert bridge.latest().to_json() == payload.to_json()
    assert len(bridge.history()) == 1
    bridge.submit(source, ROI(kind="circle", points=((50.0, 50.0), (80.0, 50.0))))
    assert len(bridge.history()) == 2
    assert bridge.latest().roi["kind"] == "circle"


def test_to_json_from_json_round_trip(source, tmp_path):
    bridge = AgentBridge(tmp_path / "out")
    roi = ROI(kind="polygon", points=((1.5, 2.5), (50.0, 10.0), (30.0, 90.0)))
    payload = bridge.submit(source, roi)
    rt = ROIPayload.from_json(payload.to_json())
    assert rt.to_json() == payload.to_json()
    assert rt.slide_dimensions == payload.slide_dimensions
    assert rt.roi == payload.roi
    assert rt.stats == payload.stats
    assert rt.patch_path == payload.patch_path


def test_to_agent_prompt_content(source, tmp_path):
    bridge = AgentBridge(tmp_path / "out")
    roi = ROI(kind="rect", points=((0.0, 0.0), (100.0, 100.0)))
    payload = bridge.submit(source, roi)
    prompt = payload.to_agent_prompt()
    assert "bridge_slide.png" in prompt
    assert "rect" in prompt
    assert payload.patch_path in prompt
    assert "x0=0" in prompt


def test_make_marimo_tool_no_selection(tmp_path):
    bridge = AgentBridge(tmp_path / "out")
    tool = make_marimo_tool(lambda: bridge)
    assert tool() == "NO_SELECTION"


def test_make_marimo_tool_returns_latest_json(source, tmp_path):
    bridge = AgentBridge(tmp_path / "out")
    tool = make_marimo_tool(lambda: bridge)
    roi = ROI(kind="rect", points=((5.0, 5.0), (60.0, 70.0)))
    payload = bridge.submit(source, roi)
    assert tool() == payload.to_json()


def test_magnification_for():
    assert magnification_for(None, 4.0) is None
    # 10x reference at 2.0 um/px scaled by downsample
    assert magnification_for(2.0, 1.0) == pytest.approx(10.0)
    assert magnification_for(2.0, 4.0) == pytest.approx(2.5)
    assert magnification_for(0.25, 1.0) == pytest.approx(80.0)
