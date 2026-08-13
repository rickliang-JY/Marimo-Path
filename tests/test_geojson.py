"""Tests for hescope.interop.geojson (QuPath GeoJSON export). Offline; tmp sqlite."""

from __future__ import annotations

import json

import pytest

from hescope.store.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.interop.geojson import (
    export_rois_geojson,
    rois_to_geojson,
    slide_geojson_text,
)
from hescope.core.rois import ROI


@pytest.fixture()
def engine(tmp_path):
    eng = get_engine(f"sqlite:///{tmp_path}/geo.db")
    init_db(eng)
    return eng


@pytest.fixture()
def slide_id(engine):
    return SlideRepo(engine).register(
        source_kind="local", name="slide_a.png", path="/tmp/geo_a.png",
        width=1200, height=800, mpp=0.25,
    )


def _rect(x0=10.0, y0=20.0, x1=110.0, y1=220.0) -> ROI:
    return ROI(kind="rect", points=((x0, y0), (x1, y1)))


def test_rois_to_geojson_structure():
    rows = [
        {
            "id": 7,
            "kind": "rect",
            "label": "tumor",
            "notes": "dense",
            "bbox": [10, 20, 110, 220],
        },
        {"id": 8, "kind": "circle", "label": "", "notes": "",
         "bbox": [0, 0, 5, 5]},
    ]
    fc = rois_to_geojson(rows, mpp=0.5)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 2

    f0 = fc["features"][0]
    assert f0["type"] == "Feature"
    geom = f0["geometry"]
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1]  # closed ring
    assert ring[:4] == [[10.0, 20.0], [110.0, 20.0], [110.0, 220.0], [10.0, 220.0]]
    props = f0["properties"]
    assert props["roi_id"] == 7
    assert props["kind"] == "rect"
    assert props["label"] == "tumor"
    assert props["notes"] == "dense"
    assert props["mpp"] == pytest.approx(0.5)
    # QuPath classification mapped from a non-empty label
    assert props["classification"] == {"name": "tumor"}
    # empty label -> no classification property
    assert "classification" not in fc["features"][1]["properties"]
    # whole document is JSON-serializable
    json.dumps(fc)


def test_rois_to_geojson_row_level_mpp_wins():
    rows = [{"id": 1, "kind": "rect", "label": "", "notes": "",
             "bbox": [0, 0, 1, 1], "mpp": 0.1}]
    fc = rois_to_geojson(rows, mpp=0.5)
    assert fc["features"][0]["properties"]["mpp"] == pytest.approx(0.1)


def test_rois_to_geojson_empty_and_invalid():
    assert rois_to_geojson([]) == {"type": "FeatureCollection", "features": []}
    # rows without a usable bbox are skipped, not fatal
    fc = rois_to_geojson([{"id": 1, "kind": "rect", "bbox": None}])
    assert fc["features"] == []


def test_export_rois_geojson_round_trip(engine, slide_id, tmp_path):
    repo = ROIRepo(engine)
    rid = repo.add(slide_id, _rect(), label="stroma", notes="round trip")
    repo.add(slide_id, _rect(0, 0, 5, 5))  # unlabeled
    out = tmp_path / "nested" / "rois.geojson"

    fc = export_rois_geojson(engine, slide_id, out)
    assert out.exists()
    on_disk = json.loads(out.read_text())
    assert on_disk == fc

    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 2
    by_id = {f["properties"]["roi_id"]: f for f in fc["features"]}
    f = by_id[rid]
    # key fields consistent with the DB row
    row = repo.get(rid)
    assert f["properties"]["label"] == row["label"] == "stroma"
    assert f["properties"]["notes"] == row["notes"] == "round trip"
    assert f["properties"]["kind"] == row["kind"] == "rect"
    assert f["properties"]["mpp"] == pytest.approx(0.25)  # slide mpp
    x0, y0, x1, y1 = row["bbox"]
    ring = f["geometry"]["coordinates"][0]
    assert ring[0] == [float(x0), float(y0)]
    assert ring[2] == [float(x1), float(y1)]


def test_export_rois_geojson_empty_slide(engine, slide_id, tmp_path):
    out = tmp_path / "empty.geojson"
    fc = export_rois_geojson(engine, slide_id, out)
    assert fc == {"type": "FeatureCollection", "features": []}
    assert json.loads(out.read_text()) == fc


def test_slide_geojson_text_matches_the_file_export(engine, slide_id, tmp_path):
    """R05-8: the download button needs the document as a string, and it must
    be the SAME document the file exporter writes -- not a second
    implementation that can drift."""
    repo = ROIRepo(engine)
    repo.add(slide_id, _rect(), label="stroma")
    out = tmp_path / "rois.geojson"

    assert json.loads(slide_geojson_text(engine, slide_id)) == export_rois_geojson(
        engine, slide_id, out
    )


def test_slide_geojson_text_with_no_slide_open_is_an_empty_collection(engine):
    """`get_slide_id()` is None whenever no slide is open; the button is
    always clickable, so this must not raise."""
    assert json.loads(slide_geojson_text(engine, None)) == {
        "type": "FeatureCollection",
        "features": [],
    }


def test_slide_geojson_text_with_no_slide_open_exports_every_roi(engine, slide_id):
    """R07-7: `slide_id=None` means ALL ROIs, as it does for ``export_rois``.

    The three Export buttons sit side by side under one comment promising
    "the current slide's ROIs (or all when no slide is open)". JSON and CSV
    honoured it; GeoJSON answered an empty FeatureCollection, which QuPath
    opens happily and renders as "this slide has no annotations". The test
    above only pinned that behaviour on an EMPTY database, where it is
    indistinguishable from this one.
    """
    repo = ROIRepo(engine)
    rid = repo.add(slide_id, _rect(), label="tumour")

    fc = json.loads(slide_geojson_text(engine, None))

    assert [f["properties"]["roi_id"] for f in fc["features"]] == [rid]
    # mpp is per-slide and these rows can span slides, so it rides per row.
    assert fc["features"][0]["properties"]["mpp"] == pytest.approx(0.25)


# --- tier 1.1: the export must be faithful to the drawn shape --------------


def _ring(kind, points_json, bbox):
    from hescope.interop.geojson import rois_to_geojson

    row = {
        "id": 1, "kind": kind, "label": "tumor", "notes": "",
        "points_json": points_json, "bbox": bbox,
    }
    fc = rois_to_geojson([row])
    return fc["features"][0]["geometry"]["coordinates"][0]


def test_lasso_exports_its_own_vertices_not_its_bbox():
    """The regression: every ROI used to export as its bounding box, so a
    lasso reached QuPath as a rectangle -- a different region than the one the
    pathologist drew. rois.points_json always held the real geometry."""
    ring = _ring("polygon", "[[10,10],[90,20],[50,80]]", [10, 10, 90, 80])
    assert ring == [[10.0, 10.0], [90.0, 20.0], [50.0, 80.0], [10.0, 10.0]]
    # the bbox corners that are NOT vertices must not appear
    assert [90.0, 80.0] not in ring


def test_rect_exports_as_its_box():
    ring = _ring("rect", "[[90,80],[10,10]]", [10, 10, 90, 80])  # corners any order
    assert ring == [
        [10.0, 10.0], [90.0, 10.0], [90.0, 80.0], [10.0, 80.0], [10.0, 10.0]
    ]


def test_circle_is_sampled_into_a_ring():
    import math

    ring = _ring("circle", "[[50,50],[75,50]]", [25, 25, 75, 75])
    assert len(ring) == 65 and ring[0] == ring[-1]  # closed
    for x, y in ring:
        assert math.hypot(x - 50.0, y - 50.0) == pytest.approx(25.0)


def test_unusable_geometry_falls_back_to_the_bbox():
    """A row with no or broken points_json must still export something."""
    expected = [
        [10.0, 10.0], [90.0, 10.0], [90.0, 80.0], [10.0, 80.0], [10.0, 10.0]
    ]
    assert _ring("polygon", "not json", [10, 10, 90, 80]) == expected
    assert _ring("polygon", None, [10, 10, 90, 80]) == expected
    assert _ring("polygon", "[[1,2]]", [10, 10, 90, 80]) == expected  # too few


def test_every_exported_ring_is_closed():
    from hescope.interop.geojson import rois_to_geojson

    rows = [
        {"id": 1, "kind": "polygon", "points_json": "[[0,0],[10,0],[5,9]]",
         "bbox": [0, 0, 10, 9], "label": "", "notes": ""},
        {"id": 2, "kind": "rect", "points_json": "[[0,0],[4,4]]",
         "bbox": [0, 0, 4, 4], "label": "", "notes": ""},
        {"id": 3, "kind": "circle", "points_json": "[[5,5],[8,5]]",
         "bbox": [2, 2, 8, 8], "label": "", "notes": ""},
    ]
    for feature in rois_to_geojson(rows)["features"]:
        ring = feature["geometry"]["coordinates"][0]
        assert ring[0] == ring[-1], "GeoJSON rings must be closed"
