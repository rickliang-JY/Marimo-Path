"""Tests for hescope.geojson (QuPath GeoJSON export). Offline; tmp sqlite."""

from __future__ import annotations

import json

import pytest

from hescope.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.geojson import export_rois_geojson, rois_to_geojson
from hescope.rois import ROI


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
