"""Tests for hescope.agent.agent_bridge."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from hescope.agent.agent_bridge import (
    AgentBridge,
    ROIPayload,
    magnification_for,
    make_marimo_tool,
)
from hescope.core.rois import ROI
from hescope.wsi.slides import PillowSource


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


# --- R01-2: get_latest_selection must never raise --------------------------


_GOOD_LINE = (
    '{"slide_name":"a.png","slide_dimensions":[10,10],"mpp":null,'
    '"magnification":null,"roi":{"kind":"rect","points_level0":[[0,0],[1,1]],'
    '"bbox_level0":[0,0,1,1]},"patch_path":"p.png","stats":{},'
    '"created_at":"2026-01-01T00:00:00+00:00","roi_id":null}'
)


def test_history_skips_corrupt_lines(tmp_path):
    """A process killed mid-write leaves a truncated final line; it must not
    make the whole history unreadable."""
    from hescope.agent.agent_bridge import AgentBridge

    bridge = AgentBridge(tmp_path)
    (tmp_path / "roi_history.jsonl").write_text(
        _GOOD_LINE + "\n" + '{"slide_name":"b.png","slide_dim\n',
        encoding="utf-8",
    )
    hist = bridge.history()
    assert len(hist) == 1
    assert hist[0].slide_name == "a.png"
    assert bridge.latest().slide_name == "a.png"


def test_get_latest_selection_never_raises(tmp_path):
    from hescope.agent.agent_bridge import AgentBridge, make_marimo_tool

    bridge = AgentBridge(tmp_path)
    (tmp_path / "roi_history.jsonl").write_text(
        '{"slide_name":"b.png","slide_dim\n', encoding="utf-8"
    )
    # the only line is corrupt -> no payload, but a string, never an exception
    assert make_marimo_tool(lambda: bridge)() == "NO_SELECTION"

    class Boom:
        def latest(self):
            raise RuntimeError("bridge exploded")

    out = make_marimo_tool(lambda: Boom())()
    assert json.loads(out)["error"].startswith("RuntimeError")


def test_get_current_selection_never_raises():
    from hescope.agent.agent_bridge import make_live_selection_tool

    def boom():
        raise ValueError("malformed plotly selection")

    out = make_live_selection_tool(boom)()
    assert json.loads(out)["error"].startswith("ValueError")
    assert make_live_selection_tool(lambda: None)() == "NO_SELECTION"
