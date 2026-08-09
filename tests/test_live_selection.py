"""Tests for the zero-click live-selection tool (SPEC-PAIR, offline)."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest
from PIL import Image

from hescope.agent_bridge import make_live_selection_tool, selection_stats
from hescope.rois import ViewportState, viewport_transform
from hescope.slides import PillowSource
from hescope.viewer import current_selection, raw_plotly_selection

# Known viewport: offset = center - (size/2) * downsample = (600, 500).
VP = ViewportState(center=(1000.0, 800.0), downsample=2.0, size=(400, 300))
# Raw mo.ui.plotly value for a box drag (viewport pixel coords).
BOX_VALUE = {"range": {"x": [10, 110], "y": [20, 70]}}
# Raw mo.ui.plotly value for a lasso drag.
LASSO_VALUE = {"lasso": {"x": [5, 105, 55], "y": [10, 10, 60]}}


@pytest.fixture()
def source(tmp_path):
    arr = np.zeros((2000, 2400, 3), dtype=np.uint8)
    arr[..., 0] = 220
    arr[..., 1] = 140
    arr[..., 2] = 180
    p = tmp_path / "live_slide.png"
    Image.fromarray(arr, "RGB").save(p)
    return PillowSource(p)


# ---------------------------------------------------------------------------
# current_selection
# ---------------------------------------------------------------------------


def test_current_selection_no_source_returns_none():
    assert current_selection(None, VP, BOX_VALUE) is None


def test_current_selection_empty_value_returns_none(source):
    assert current_selection(source, VP, None) is None
    assert current_selection(source, VP, {}) is None
    assert current_selection(source, VP, {"range": {}}) is None
    assert current_selection(source, VP, "not-a-dict") is None


def test_current_selection_box_maps_to_level0(source):
    sel = current_selection(source, VP, BOX_VALUE)
    assert sel is not None
    assert sel["kind"] == "rect"
    to_level0, _ = viewport_transform(VP)
    p0 = to_level0((10.0, 20.0))
    p1 = to_level0((110.0, 70.0))
    assert sel["points_level0"] == [[p0[0], p0[1]], [p1[0], p1[1]]]
    # bbox matches viewport_transform exactly (here all coords are ints)
    assert sel["bbox_level0"] == [620, 540, 820, 640]
    assert sel["bbox_level0"] == [
        int(p0[0]),
        int(p0[1]),
        int(p1[0]),
        int(p1[1]),
    ]
    assert sel["viewport_downsample"] == 2.0
    assert sel["slide"] == source.name
    assert sel["slide_dimensions"] == [
        source.dimensions[0],
        source.dimensions[1],
    ]
    assert sel["mpp"] is None


def test_current_selection_lasso_maps_polygon_points(source):
    sel = current_selection(source, VP, LASSO_VALUE)
    assert sel is not None
    assert sel["kind"] == "polygon"
    to_level0, _ = viewport_transform(VP)
    expected = [
        list(to_level0((float(x), float(y))))
        for x, y in zip(LASSO_VALUE["lasso"]["x"], LASSO_VALUE["lasso"]["y"])
    ]
    assert sel["points_level0"] == expected
    xs = [p[0] for p in expected]
    ys = [p[1] for p in expected]
    assert sel["bbox_level0"] == [min(xs), min(ys), max(xs), max(ys)]


def test_current_selection_supports_lasso_points_key(source):
    sel = current_selection(
        source, VP, {"lasso_points": LASSO_VALUE["lasso"]}
    )
    assert sel is not None
    assert sel["kind"] == "polygon"
    assert len(sel["points_level0"]) == 3


# ---------------------------------------------------------------------------
# raw_plotly_selection
# ---------------------------------------------------------------------------


class _FakePlotlyElement:
    """Stand-in for a mo.ui.plotly element (marimo 0.23 shape)."""

    def __init__(self, value=None, selection_data=None, has_sd=True):
        self.value = value
        if has_sd:
            self._selection_data = selection_data


def test_raw_plotly_selection_prefers_private_selection_data():
    # marimo 0.23: .value == [] for image-only figures; the raw selection
    # dict lives on the private _selection_data attribute.
    el = _FakePlotlyElement(value=[], selection_data=BOX_VALUE)
    assert raw_plotly_selection(el) == BOX_VALUE


def test_raw_plotly_selection_falls_back_to_dict_value():
    # Other marimo versions may expose the selection dict as .value directly.
    el = _FakePlotlyElement(value=BOX_VALUE, has_sd=False)
    assert raw_plotly_selection(el) == BOX_VALUE
    el_sd_none = _FakePlotlyElement(value=LASSO_VALUE, selection_data=None)
    assert raw_plotly_selection(el_sd_none) == LASSO_VALUE


def test_raw_plotly_selection_empty_returns_none():
    assert raw_plotly_selection(_FakePlotlyElement(value=[])) is None
    el = _FakePlotlyElement(value=[], selection_data={})
    assert raw_plotly_selection(el) is None


def test_raw_plotly_selection_none_returns_none():
    assert raw_plotly_selection(None) is None


def test_raw_plotly_selection_plain_dict_passthrough():
    assert raw_plotly_selection(BOX_VALUE) is BOX_VALUE
    assert raw_plotly_selection(LASSO_VALUE) is LASSO_VALUE
    assert raw_plotly_selection({}) is None


def test_current_selection_with_element_wrapper(source):
    # End-to-end: an image-only figure element whose .value is [] still
    # yields a mapped selection via raw_plotly_selection.
    el = _FakePlotlyElement(value=[], selection_data=BOX_VALUE)
    sel = current_selection(source, VP, raw_plotly_selection(el))
    assert sel is not None
    assert sel["kind"] == "rect"
    assert sel["bbox_level0"] == [620, 540, 820, 640]


# ---------------------------------------------------------------------------
# make_live_selection_tool
# ---------------------------------------------------------------------------


def test_live_selection_tool_no_selection():
    tool = make_live_selection_tool(lambda: None)
    assert tool() == "NO_SELECTION"


def test_live_selection_tool_json_round_trip(source):
    sel = current_selection(source, VP, BOX_VALUE)
    tool = make_live_selection_tool(lambda: sel)
    out = tool()
    assert isinstance(out, str)
    assert json.loads(out) == sel


def test_live_selection_tool_reflects_getter_changes(source):
    holder = {"sel": None}
    tool = make_live_selection_tool(lambda: holder["sel"])
    assert tool() == "NO_SELECTION"
    holder["sel"] = current_selection(source, VP, LASSO_VALUE)
    assert json.loads(tool())["kind"] == "polygon"


# ---------------------------------------------------------------------------
# selection_stats
# ---------------------------------------------------------------------------


def test_selection_stats_returns_stats_and_patch(source, tmp_path):
    sel = current_selection(source, VP, BOX_VALUE)
    out = selection_stats(source, sel, out_dir=tmp_path / "live")
    for key in (
        "width_px",
        "height_px",
        "mean_rgb",
        "he_deconvolution",
        "tissue_fraction",
    ):
        assert key in out
    patch_path = out["patch_path"]
    assert os.path.isfile(patch_path)
    assert patch_path.startswith(str((tmp_path / "live").resolve()))
    with Image.open(patch_path) as img:
        assert img.size == (out["width_px"], out["height_px"])
    # box is 200x100 level-0 px; max_size default 1024 keeps native size
    assert (out["width_px"], out["height_px"]) == (200, 100)


def test_selection_stats_default_temp_dir(source):
    sel = current_selection(source, VP, LASSO_VALUE)
    out = selection_stats(source, sel)
    assert os.path.isfile(out["patch_path"])
    assert "live_" in os.path.basename(out["patch_path"])


# --- R01-3: the lasso branch must be guarded like the box branch -----------


def test_lasso_malformed_returns_none():
    from hescope.viewer import parse_plotly_selection

    # box branch already guarded; lasso must behave identically
    assert parse_plotly_selection({"range": {"x": [None, 5], "y": [0, 5]}}) is None
    assert parse_plotly_selection({"lasso": {"x": [0, 1, None], "y": [0, 1, 2]}}) is None
    assert parse_plotly_selection({"lasso": {"x": 5, "y": 7}}) is None
    # a well-formed lasso still parses
    ok = parse_plotly_selection({"lasso": {"x": [0, 10, 5], "y": [0, 0, 10]}})
    assert ok["kind"] == "lasso" and len(ok["points"]) == 3
