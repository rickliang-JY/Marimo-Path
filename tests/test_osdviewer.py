"""Contract tests for the OpenSeadragon viewing surface (offline, no browser).

Everything that decides a coordinate or builds an agent payload lives in plain
functions in :mod:`hescope.viewer.osdviewer`; this file is the proof that those
functions agree, byte-for-byte, with the plotly path they replace.

What is NOT covered here (browser-only): that OpenSeadragon actually boots
inside a marimo cell, that mouse gestures reach the draw handlers, and that
the SVG overlay lands on the right screen pixels. See
``tests/browser/test_osd_cdp.py``.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image

from hescope.viewer import osdviewer as osd
from hescope.core.measure import measure_box
from hescope.core.rois import ROI, ViewportState, viewport_transform
from hescope.wsi.slides import PillowSource
from hescope.viewer.viewer import current_selection, selection_to_roi

# The exact viewport tests/test_live_selection.py pins the agent contract to.
VP = ViewportState(center=(1000.0, 800.0), downsample=2.0, size=(400, 300))
# The plotly box drag it uses, and the OSD payload for the SAME rectangle:
# offset = center - (size/2)*downsample = (600, 500), so viewport px (10, 20)
# is level-0 (620, 540) and (110, 70) is (820, 640).
PLOTLY_BOX = {"range": {"x": [10, 110], "y": [20, 70]}}
OSD_BOX = {"kind": "rect", "points_level0": [[620, 540], [820, 640]], "seq": 1}
PLOTLY_LASSO = {"lasso": {"x": [5, 105, 55], "y": [10, 10, 60]}}
OSD_LASSO = {
    "kind": "polygon",
    "points_level0": [[610, 520], [810, 520], [710, 620]],
    "seq": 2,
}


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
# Vendored JavaScript / offline promise
# ---------------------------------------------------------------------------


def test_vendored_osd_file_and_license_exist():
    js = osd.osd_source_path()
    assert js.is_file()
    assert js.stat().st_size > 200_000
    assert (js.parent / "LICENSE.txt").is_file()
    assert (js.parent / "THIRD_PARTY.md").is_file()


def test_vendored_osd_version_matches():
    # A bundle swap that forgets to bump OSD_VERSION would otherwise ship a
    # provenance note that lies.
    assert osd.vendored_osd_version() == osd.OSD_VERSION


def test_build_esm_wrapper_is_present():
    esm = osd.build_esm()
    # openseadragon.min.js ends in a UMD footer that calls `this`, which is
    # undefined at ES-module top level -> TypeError -> silent blank widget.
    assert ".call(globalThis)" in esm
    assert "return OpenSeadragon;" in esm
    assert len(esm) > 250_000


def test_build_esm_drops_source_map_comment():
    # The .map is not vendored (it would be a 404 and a network request), and
    # the trailing // comment would swallow the appended `return`.
    assert "sourceMappingURL" not in osd.build_esm()
    assert "sourceMappingURL" in osd.osd_source_path().read_text(encoding="utf-8")


def test_widget_js_makes_no_network_references():
    """The widget half must not reach for anything off-machine.

    The one allowed ``http://`` is the SVG namespace literal, which is an
    identifier that is never fetched.
    """
    js = osd._WIDGET_JS.replace('"http://www.w3.org/2000/svg"', '"<svg-ns>"')
    for needle in ("http://", "https://", "cdn.", "unpkg", "jsdelivr", "<script"):
        assert needle not in js, needle
    # prefixUrl "" + no navigator/navigation controls == no sprite PNG requests
    assert 'prefixUrl: ""' in js
    assert "showNavigationControl: false" in js
    assert "showNavigator: false" in js


def test_build_esm_is_cached():
    assert osd.build_esm() is osd.build_esm()


def test_overlay_colours_match_the_server_renderer():
    """The client overlay and hescope.viewer.overlay.draw_rois must not drift.

    draw_rois bakes (255, 60, 60) / (60, 200, 60) into the exported PNG; the
    SVG overlay is what the user actually looks at. A mismatch would make a
    report look different from the screen it was taken from.
    """
    import inspect

    from hescope.viewer import overlay

    assert osd.ROI_STROKE == "#ff3c3c"
    assert osd.ROI_STROKE_SELECTED == "#3cc83c"
    assert tuple(int(osd.ROI_STROKE[i : i + 2], 16) for i in (1, 3, 5)) == (255, 60, 60)
    assert tuple(
        int(osd.ROI_STROKE_SELECTED[i : i + 2], 16) for i in (1, 3, 5)
    ) == (60, 200, 60)
    sig = inspect.signature(overlay.draw_rois)
    assert sig.parameters["color"].default == (255, 60, 60)
    assert sig.parameters["selected_color"].default == (60, 200, 60)
    # the JS half carries its own copies; they must agree
    assert f'const ROI_STROKE = "{osd.ROI_STROKE}";' in osd._WIDGET_JS
    assert (
        f'const ROI_STROKE_SELECTED = "{osd.ROI_STROKE_SELECTED}";' in osd._WIDGET_JS
    )


# ---------------------------------------------------------------------------
# Coordinates: OSD viewport rect <-> ViewportState
# ---------------------------------------------------------------------------


def test_full_image_bounds_map_to_image_centre_on_a_wide_slide():
    """The W0-vs-H0 trap, caught absolutely rather than by round trip.

    OSD normalizes BOTH axes by image WIDTH, so a 4000x1000 image spans
    y in [0, 0.25]. Bounds covering the whole image must therefore report the
    centre as (2000, 500). Using H0 for cy would give (2000, 125) -- a
    perfectly well-formed ViewportState holding a wrong answer.
    """
    dims = (4000, 1000)
    bounds = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.25}
    vp = osd.viewport_from_osd(bounds, (800, 200), dims)
    assert vp.center == pytest.approx((2000.0, 500.0))
    assert vp.downsample == pytest.approx(5.0)
    assert vp.size == (800, 200)


def test_full_image_bounds_on_a_tall_slide():
    # The mirror case: a portrait slide spans y in [0, 4.0].
    dims = (1000, 4000)
    bounds = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 4.0}
    vp = osd.viewport_from_osd(bounds, (250, 1000), dims)
    assert vp.center == pytest.approx((500.0, 2000.0))
    assert vp.downsample == pytest.approx(4.0)


@pytest.mark.parametrize(
    "container",
    [(1024, 768), (1600, 400), (400, 1600), (1, 1)],
)
def test_viewport_round_trip(container):
    dims = (6000, 4000)
    vp = ViewportState(center=(2345.5, 1234.25), downsample=3.25, size=container)
    back = osd.viewport_from_osd(
        osd.osd_bounds_from_viewport(vp, dims), container, dims
    )
    assert back.center[0] == pytest.approx(vp.center[0], abs=1e-6)
    assert back.center[1] == pytest.approx(vp.center[1], abs=1e-6)
    assert back.downsample == pytest.approx(vp.downsample, rel=1e-9)
    assert back.size == vp.size


def test_osd_bounds_from_viewport_aspect_matches_container():
    # OSD derives bounds.height from the container aspect; the inverse must
    # produce a rectangle with that same aspect or the view would jump.
    dims = (6000, 4000)
    vp = ViewportState(center=(3000.0, 2000.0), downsample=2.0, size=(1600, 400))
    b = osd.osd_bounds_from_viewport(vp, dims)
    assert b["height"] / b["width"] == pytest.approx(400 / 1600)


def test_viewport_from_osd_matches_a_real_browser_payload():
    """Regression anchor captured from headless Chrome.

    OSD was asked to fitBounds the level-0 rect (2000, 300)-(2400, 600) on a
    4000x1000 image in a 966x400 container; this is the viewport trait it
    reported back. The centre must be the bbox centre and the downsample must
    be level-0 px per container px.
    """
    report = {
        "bounds": {
            "x": 0.45943750000000005,
            "y": 0.07499999999999998,
            "width": 0.181125,
            "height": 0.075,
        },
        "container": [966, 400],
        "image": [4000, 1000],
        "ack_seq": 7,
        "why": "goto_bbox",
    }
    vp = osd.viewport_state_from_report(report, (4000, 1000))
    assert vp is not None
    assert vp.center[0] == pytest.approx(2200.0, abs=1e-6)
    assert vp.center[1] == pytest.approx(450.0, abs=1e-6)
    assert vp.downsample == pytest.approx(0.75, rel=1e-9)
    assert vp.size == (966, 400)


def test_viewport_state_from_report_accepts_flat_shape():
    vp = osd.viewport_state_from_report(
        {"cx": 100.0, "cy": 200.0, "ds": 4.0, "w": 800, "h": 600}, (6000, 4000)
    )
    assert vp == ViewportState(center=(100.0, 200.0), downsample=4.0, size=(800, 600))


@pytest.mark.parametrize(
    "report",
    [
        None,
        {},
        [],
        "nope",
        {"why": "open"},
        {"bounds": {"x": 0, "y": 0, "width": 0, "height": 0}, "container": [800, 600]},
        {"bounds": {"x": "a", "y": 0, "width": 1, "height": 1}, "container": [8, 6]},
        {"bounds": {"x": float("nan"), "y": 0, "width": 1, "height": 1},
         "container": [8, 6]},
        {"cx": 1.0, "cy": 2.0, "ds": 0.0, "w": 10, "h": 10},
        {"cx": 1.0, "cy": 2.0},
    ],
)
def test_viewport_state_from_report_never_raises(report):
    assert osd.viewport_state_from_report(report, (6000, 4000)) is None


def test_viewport_changed_epsilon():
    a = ViewportState(center=(1000.0, 800.0), downsample=2.0, size=(400, 300))
    assert osd.viewport_changed(None, a) is True
    # sub-pixel drift and sub-1% zoom drift are noise, not a new viewport
    tiny = ViewportState(center=(1000.5, 800.0), downsample=2.005, size=(400, 300))
    assert osd.viewport_changed(a, tiny) is False
    moved = ViewportState(center=(1010.0, 800.0), downsample=2.0, size=(400, 300))
    assert osd.viewport_changed(a, moved) is True
    zoomed = ViewportState(center=(1000.0, 800.0), downsample=2.5, size=(400, 300))
    assert osd.viewport_changed(a, zoomed) is True
    resized = ViewportState(center=(1000.0, 800.0), downsample=2.0, size=(401, 300))
    assert osd.viewport_changed(a, resized) is True


# ---------------------------------------------------------------------------
# parse_osd_selection
# ---------------------------------------------------------------------------


def test_parse_rect_normalizes_corners():
    sel = osd.parse_osd_selection(
        {"kind": "rect", "points_level0": [[820, 640], [620, 540]]}
    )
    assert sel == {"kind": "rect", "points_level0": [(620.0, 540.0), (820.0, 640.0)]}


def test_parse_polygon_keeps_order():
    sel = osd.parse_osd_selection(OSD_LASSO)
    assert sel["kind"] == "polygon"
    assert sel["points_level0"] == [(610.0, 520.0), (810.0, 520.0), (710.0, 620.0)]


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        [],
        "str",
        42,
        {"kind": "rect"},
        {"kind": "rect", "points_level0": []},
        {"kind": "rect", "points_level0": [[1, 2]]},
        {"kind": "box", "points_level0": [[1, 2], [3, 4]]},  # plotly vocabulary
        {"kind": "lasso", "points_level0": [[1, 2], [3, 4], [5, 6]]},
        {"kind": "measure", "points_level0": [[1, 2], [3, 4]]},
        {"kind": "polygon", "points_level0": [[1, 2], [3, 4]]},
        {"kind": "polygon", "points_level0": "abc"},
        {"kind": "rect", "points_level0": [["a", "b"], [3, 4]]},
        {"kind": "rect", "points_level0": [[float("nan"), 0], [3, 4]]},
        {"kind": "rect", "points_level0": [[float("inf"), 0], [3, 4]]},
        {"kind": "rect", "points_level0": [[1], [3, 4]]},
        {"kind": "rect", "points_level0": ["12", "34"]},
        {"kind": "rect", "points_level0": 7},
        {"kind": None, "points_level0": [[1, 2], [3, 4]]},
    ],
)
def test_parse_osd_selection_guard_matrix(value):
    assert osd.parse_osd_selection(value) is None


def test_parse_osd_selection_only_emits_contract_kinds():
    """kind must never leave the AGENTS.md 4.1 vocabulary.

    parse_plotly_selection emits the INTERMEDIATE kinds box/lasso which
    selection_to_roi renames; this path has no rename step, so a copy-paste of
    the plotly branch would leak "box" into get_current_selection().
    """
    inputs = [
        OSD_BOX,
        OSD_LASSO,
        {"kind": "rect", "points_level0": [[0, 0], [1, 1], [2, 2]]},
        {"kind": "polygon", "points_level0": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    ]
    for v in inputs:
        sel = osd.parse_osd_selection(v)
        assert sel is not None
        assert sel["kind"] in ("rect", "polygon")


def test_parse_osd_selection_key_name_blocks_double_transform():
    """The output key is points_level0, not points, on purpose.

    OSD already reports level-0 coordinates. Feeding them to
    hescope.viewer.viewer.selection_to_roi would apply viewport_transform a SECOND
    time and produce plausible, silently wrong bboxes. The key name makes that
    mistake raise instead.
    """
    sel = osd.parse_osd_selection(OSD_BOX)
    assert "points" not in sel
    with pytest.raises(KeyError):
        selection_to_roi(sel, VP)


# ---------------------------------------------------------------------------
# osd_selection_to_roi
# ---------------------------------------------------------------------------


def test_selection_to_roi_rect_is_identity_in_level0():
    roi = osd.osd_selection_to_roi(osd.parse_osd_selection(OSD_BOX))
    assert roi.kind == "rect"
    assert roi.points == ((620.0, 540.0), (820.0, 640.0))


def test_selection_to_roi_as_circle_matches_plotly_semantics():
    roi = osd.osd_selection_to_roi(osd.parse_osd_selection(OSD_BOX), as_circle=True)
    plot = selection_to_roi(
        {"kind": "box", "points": [(10.0, 20.0), (110.0, 70.0)]}, VP, as_circle=True
    )
    assert roi.kind == "circle" == plot.kind
    assert roi.points == plot.points
    # radius = half the SHORTER side (100 level-0 px tall box -> r = 50)
    (cx, cy), (ex, ey) = roi.points
    assert math.hypot(ex - cx, ey - cy) == pytest.approx(50.0)


def test_selection_to_roi_polygon():
    roi = osd.osd_selection_to_roi(osd.parse_osd_selection(OSD_LASSO))
    assert roi.kind == "polygon"
    assert roi.points == ((610.0, 520.0), (810.0, 520.0), (710.0, 620.0))


# ---------------------------------------------------------------------------
# THE AGENT CONTRACT: byte-identical to the plotly path
# ---------------------------------------------------------------------------


def test_osd_box_selection_is_byte_identical_to_plotly(source):
    plot = current_selection(source, VP, PLOTLY_BOX)
    osd_sel = osd.osd_current_selection(source, VP, {"selection": OSD_BOX})
    assert osd_sel == plot
    # and it is the value tests/test_live_selection.py pins
    assert osd_sel["bbox_level0"] == [620, 540, 820, 640]
    assert list(osd_sel.keys()) == [
        "kind",
        "points_level0",
        "bbox_level0",
        "viewport_downsample",
        "slide",
        "slide_dimensions",
        "mpp",
    ]


def test_osd_polygon_selection_is_byte_identical_to_plotly(source):
    plot = current_selection(source, VP, PLOTLY_LASSO)
    osd_sel = osd.osd_current_selection(source, VP, {"selection": OSD_LASSO})
    assert osd_sel == plot
    assert osd_sel["kind"] == "polygon"


def test_contract_dict_types(source):
    sel = osd.osd_current_selection(source, VP, {"selection": OSD_BOX})
    assert sel["kind"] == "rect"
    assert all(isinstance(v, float) for p in sel["points_level0"] for v in p)
    assert all(isinstance(v, int) for v in sel["bbox_level0"])
    assert isinstance(sel["viewport_downsample"], float)
    assert isinstance(sel["slide"], str)
    assert sel["slide_dimensions"] == [2400, 2000]
    assert sel["mpp"] is None


def test_osd_current_selection_none_cases(source):
    assert osd.osd_current_selection(None, VP, {"selection": OSD_BOX}) is None
    assert osd.osd_current_selection(source, VP, None) is None
    assert osd.osd_current_selection(source, VP, {}) is None
    assert osd.osd_current_selection(source, VP, {"selection": {}}) is None
    # a measurement is a UI readout, never an ROI: it must not reach the tool
    assert (
        osd.osd_current_selection(
            source,
            VP,
            {"selection": {"kind": "measure", "points_level0": [[0, 0], [10, 10]]}},
        )
        is None
    )


@pytest.mark.parametrize(
    "garbage",
    [
        0,
        "",
        "NO_SELECTION",
        [],
        [1, 2, 3],
        {"selection": "x"},
        {"selection": {"kind": "rect"}},
        {"selection": {"kind": "rect", "points_level0": [[None, None], [1, 1]]}},
        {"kind": "rect"},
        object(),
    ],
)
def test_osd_current_selection_never_raises(source, garbage):
    assert osd.osd_current_selection(source, VP, garbage) is None


def test_contract_dict_delegates_to_viewer_when_available(source, monkeypatch):
    """There must be exactly ONE implementation of the 7-key shape in use."""
    import hescope.viewer.viewer as viewer_mod

    calls = []
    real = viewer_mod.selection_dict_from_roi

    def spy(src, roi, downsample):
        calls.append((src, roi, downsample))
        return real(src, roi, downsample)

    monkeypatch.setattr(viewer_mod, "selection_dict_from_roi", spy)
    out = osd.osd_current_selection(source, VP, {"selection": OSD_BOX})
    assert len(calls) == 1
    assert calls[0][2] == 2.0
    assert out["bbox_level0"] == [620, 540, 820, 640]


def test_contract_dict_fallback_matches_viewer_exactly(source, monkeypatch):
    """The inline fallback exists only for a viewer.py without the extraction;
    it must be indistinguishable from the real thing."""
    import hescope.viewer.viewer as viewer_mod

    expected = osd.osd_current_selection(source, VP, {"selection": OSD_BOX})
    monkeypatch.delattr(viewer_mod, "selection_dict_from_roi")
    assert osd.osd_current_selection(source, VP, {"selection": OSD_BOX}) == expected


def test_osd_current_selection_uses_vp_only_for_downsample(source):
    """The geometry is already level-0, so moving the viewport must not move
    the reported ROI -- only ``viewport_downsample`` may change."""
    a = osd.osd_current_selection(source, VP, {"selection": OSD_BOX})
    other = ViewportState(center=(9.0, 9.0), downsample=8.0, size=(100, 100))
    b = osd.osd_current_selection(source, other, {"selection": OSD_BOX})
    assert a["points_level0"] == b["points_level0"]
    assert a["bbox_level0"] == b["bbox_level0"]
    assert b["viewport_downsample"] == 8.0


# ---------------------------------------------------------------------------
# raw_osd_selection / measure / clicks / overlay payload
# ---------------------------------------------------------------------------


class _FakeElement:
    """Stand-in for mo.ui.anywidget, which proxies traits AND exposes .value."""

    def __init__(self, value):
        self.value = value

    def __getattr__(self, name):
        try:
            return self.value[name]
        except (KeyError, TypeError):
            raise AttributeError(name) from None


def test_raw_osd_selection_shapes():
    assert osd.raw_osd_selection({"selection": OSD_BOX}) == OSD_BOX
    assert osd.raw_osd_selection(OSD_BOX) == OSD_BOX
    assert osd.raw_osd_selection(_FakeElement({"selection": OSD_BOX})) == OSD_BOX
    assert osd.raw_osd_selection(None) is None
    assert osd.raw_osd_selection({}) is None
    assert osd.raw_osd_selection({"selection": {}}) is None
    assert osd.raw_osd_selection({"viewport": {"why": "open"}}) is None
    assert osd.raw_osd_selection(_FakeElement({"tool": "pan"})) is None


def test_parse_osd_measure_and_measure_box():
    payload = {"kind": "measure", "points_level0": [[820, 640], [620, 540]]}
    corners = osd.parse_osd_measure(payload)
    assert corners == ((620.0, 540.0), (820.0, 640.0))
    m = measure_box(corners[0], corners[1], 0.5)
    assert m.width_px == pytest.approx(200.0)
    assert m.height_px == pytest.approx(100.0)
    assert m.width_um == pytest.approx(100.0)


@pytest.mark.parametrize(
    "value",
    [None, {}, OSD_BOX, {"kind": "measure"}, {"kind": "measure", "points_level0": [[1, 1]]}],
)
def test_parse_osd_measure_guards(value):
    assert osd.parse_osd_measure(value) is None


def test_parse_clicked_roi():
    assert osd.parse_clicked_roi("3#7") == 3
    assert osd.parse_clicked_roi("0#1") == 0
    assert osd.parse_clicked_roi("") is None
    assert osd.parse_clicked_roi(None) is None
    assert osd.parse_clicked_roi("abc") is None
    assert osd.parse_clicked_roi("-1#2") is None
    assert osd.parse_clicked_roi(5) is None


def test_rois_to_payload_is_level0_and_json_safe():
    import json

    rois = [
        ROI(kind="rect", points=((0, 0), (10, 20))),
        ROI(kind="polygon", points=((0, 0), (5, 0), (5, 5))),
        ROI(kind="circle", points=((100, 100), (110, 100))),
    ]
    payload = osd.rois_to_payload(rois, selected_index=1)
    assert [p["kind"] for p in payload] == ["rect", "polygon", "circle"]
    assert [p["selected"] for p in payload] == [False, True, False]
    # coordinates are handed to the client UNPROJECTED: the SVG overlay draws
    # in level-0 space and OSD supplies the transform
    assert payload[0]["points"] == [[0.0, 0.0], [10.0, 20.0]]
    json.dumps(payload)


def test_rois_to_payload_empty_and_unselected():
    assert osd.rois_to_payload([]) == []
    payload = osd.rois_to_payload([ROI(kind="rect", points=((0, 0), (1, 1)))])
    assert payload[0]["selected"] is False


# ---------------------------------------------------------------------------
# The widget object
# ---------------------------------------------------------------------------


def test_widget_trait_defaults():
    w = osd.HEScopeViewer()
    assert w.tool == "pan"
    assert w.overlay_visible is True
    assert w.viewport == {}
    assert w.selection == {}
    assert w.clicked_roi == ""
    assert w.tile_source == {}
    assert w.goto_bbox == []
    assert w.command_seq == 0
    assert w.mpp == 0.0
    assert osd.OSD_VERSION in w._esm[:200]


def test_widget_goto_bumps_command_seq():
    # Python-commanded moves must be distinguishable from user gestures, or
    # the consumer cell fights the user's pan.
    w = osd.HEScopeViewer()
    w.goto([10, 20, 30, 40])
    assert w.goto_bbox == [10.0, 20.0, 30.0, 40.0]
    assert w.command_seq == 1
    w.goto([1, 2, 3, 4])
    assert w.command_seq == 2


def test_widget_set_rois_and_viewport_state():
    w = osd.HEScopeViewer()
    w.set_rois([ROI(kind="rect", points=((0, 0), (10, 10)))], selected_index=0)
    assert w.rois[0]["selected"] is True
    assert w.viewport_state((6000, 4000)) is None
    w.viewport = {
        "bounds": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.25},
        "container": [800, 200],
    }
    vp = w.viewport_state((4000, 1000))
    assert vp.center == pytest.approx((2000.0, 500.0))


def test_make_viewer_carries_mpp_and_tile_source(source):
    ts = {"width": 2400, "height": 2000, "tileSize": 256}
    w = osd.make_viewer(source, ts, tool="rect", height=512)
    assert w.tile_source == ts
    assert w.tool == "rect"
    assert w.height == 512
    assert w.mpp == 0.0  # PillowSource has mpp None -> 0.0 sentinel
    assert osd.make_viewer(source, {}).tile_source == {}


def test_widget_value_shape_round_trips_through_osd_current_selection(source):
    """What app.py will actually hold: mo.ui.anywidget(...).value, a trait dict."""
    w = osd.HEScopeViewer()
    w.selection = dict(OSD_BOX)
    value = {"tool": w.tool, "selection": w.selection, "viewport": w.viewport}
    assert osd.osd_current_selection(source, VP, value) == current_selection(
        source, VP, PLOTLY_BOX
    )


# ---------------------------------------------------------------------------
# Cross-check against viewport_transform itself
# ---------------------------------------------------------------------------


def test_viewport_from_osd_agrees_with_viewport_transform():
    """A viewport reported by OSD must place its own corners where
    viewport_transform says they are -- that map is what the whole agent
    contract rests on, and it is never touched by this module."""
    dims = (6000, 4000)
    container = (1024, 768)
    vp = ViewportState(center=(2500.0, 1500.0), downsample=1.5, size=container)
    bounds = osd.osd_bounds_from_viewport(vp, dims)
    back = osd.viewport_from_osd(bounds, container, dims)
    to_level0, _ = viewport_transform(back)
    assert to_level0((0.0, 0.0)) == pytest.approx((1732.0, 924.0))
    assert to_level0((1024.0, 768.0)) == pytest.approx((3268.0, 2076.0))


def test_osd_current_selection_matches_plotly_none_semantics():
    """R01-style contract check: the OSD getter is a drop-in for the plotly
    one, so a missing source OR a missing viewport returns None rather than
    raising. Found by the round-02 adversarial review."""
    from hescope.viewer.osdviewer import osd_current_selection
    from hescope.wsi.slides import PillowSource
    from PIL import Image
    import numpy as np, tempfile, pathlib

    d = pathlib.Path(tempfile.mkdtemp())
    p = d / "s.png"
    Image.fromarray(np.zeros((40, 60, 3), np.uint8), "RGB").save(p)
    src = PillowSource(p)
    sel = {"kind": "rect", "points_level0": [[1.0, 1.0], [10.0, 10.0]]}

    assert osd_current_selection(None, None, sel) is None
    assert osd_current_selection(src, None, sel) is None   # used to raise
    assert osd_current_selection(src, None, None) is None
