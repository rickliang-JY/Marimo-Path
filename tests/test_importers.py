"""Importing annotations made in QuPath and ASAP.

The fixtures are deliberately not happy-path: each carries a case an importer
written against the obvious shape gets silently wrong. Getting these right is
the whole value of the feature -- an approximation of a pathologist's outline,
imported without saying so, is worse than refusing the file.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from hescope.importers import (
    import_annotations,
    parse_asap_xml,
    parse_geojson_annotations,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
GEOJSON = FIXTURES / "qupath_annotations.geojson"
ASAP = FIXTURES / "asap_annotations.xml"


# --- QuPath GeoJSON --------------------------------------------------------


@pytest.fixture(scope="module")
def geojson_report():
    return parse_geojson_annotations(GEOJSON)


def test_plain_polygons_import_with_their_class(geojson_report):
    by_label = {}
    for item in geojson_report.rois:
        by_label.setdefault(item.label, []).append(item)
    assert "Tumor" in by_label and "Stroma" in by_label
    stroma = by_label["Stroma"][0].roi
    assert stroma.kind == "polygon"
    # the closing vertex GeoJSON requires is not part of the outline
    assert stroma.points == (
        (300.0, 300.0), (900.0, 300.0), (900.0, 700.0), (300.0, 700.0)
    )


def test_multipolygon_becomes_one_roi_per_part(geojson_report):
    """The parts are disjoint. Collapsing them into one outline would invent
    area in the gap between them."""
    xs = [
        min(p[0] for p in item.roi.points)
        for item in geojson_report.rois
        if item.label == "Tumor"
    ]
    assert 4000.0 in xs and 4500.0 in xs, "both MultiPolygon parts should import"


def test_zero_area_geometries_are_skipped_with_a_reason(geojson_report):
    assert geojson_report.skipped.get("Point has no area") == 1
    assert geojson_report.skipped.get("LineString has no area") == 1
    assert geojson_report.skipped.get("degenerate polygon") == 1


def test_holes_are_reported_not_silently_flattened(geojson_report):
    """A donut imported as a disc is a different region. We cannot represent
    the hole, so the user has to be told."""
    assert any("interior rings" in w for w in geojson_report.warnings)
    necrosis = next(i for i in geojson_report.rois if i.label == "Necrosis")
    assert necrosis.roi.points[0] == (2000.0, 2000.0)  # outer ring


def test_summary_states_both_halves(geojson_report):
    summary = geojson_report.summary()
    assert "6 annotation(s) imported" in summary
    assert "3 skipped" in summary
    assert "interior rings" in summary


def test_unparseable_input_is_reported_not_raised():
    report = parse_geojson_annotations({"type": "Topology"})
    assert report.n_imported == 0 and report.n_skipped == 1
    assert parse_geojson_annotations([]).n_imported == 0


def test_accepts_a_bare_feature_and_a_list():
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
        },
        "properties": {"classification": {"name": "X"}},
    }
    assert parse_geojson_annotations(feature).n_imported == 1
    assert parse_geojson_annotations([feature, feature]).n_imported == 2


# --- ASAP / CAMELYON XML ---------------------------------------------------


@pytest.fixture(scope="module")
def asap_report():
    return parse_asap_xml(ASAP)


def test_vertices_follow_the_order_attribute_not_document_order(asap_report):
    """THE ASAP trap. In the fixture the Necrosis annotation lists Order
    2,0,3,1. Trusting document order gives a self-intersecting polygon that
    passes every shape check -- a wrong region that looks entirely valid."""
    necrosis = next(i for i in asap_report.rois if i.label == "Necrosis")
    assert necrosis.roi.points == (
        (2000.0, 2000.0), (2600.0, 2000.0), (2600.0, 2600.0), (2000.0, 2600.0)
    )


def test_dot_is_skipped(asap_report):
    assert asap_report.skipped.get("Dot has no area") == 1


def test_asap_none_group_is_not_a_class_called_none(asap_report):
    labels = {i.label for i in asap_report.rois}
    assert "None" not in labels
    assert "" in labels, "PartOfGroup=None should import unclassified"


def test_rectangle_keeps_its_four_corners(asap_report):
    """ASAP rectangles are four stored corners and need not be axis aligned,
    so squashing them into a two-corner rect would square off a rotated box."""
    stroma = next(i for i in asap_report.rois if i.label == "Stroma")
    assert len(stroma.roi.points) == 4


def test_bad_xml_is_reported_not_raised():
    report = parse_asap_xml("<ASAP_Annotations><oops>")
    assert report.n_imported == 0 and report.n_skipped == 1


# --- persistence and the round trip ---------------------------------------


def _engine(tmp_path):
    from hescope.db import get_engine, init_db

    engine = get_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    init_db(engine)
    return engine


def test_imported_rois_are_indistinguishable_from_drawn_ones(tmp_path):
    """They must land with points_json populated, or they cannot be exported,
    trained on, or seen by query_annotations()."""
    from hescope.db import ROIRepo, SlideRepo

    engine = _engine(tmp_path)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="s.svs", path=str(tmp_path / "s.svs"),
        width=10000, height=10000, mpp=None,
    )
    report = parse_geojson_annotations(GEOJSON)
    ids = import_annotations(engine, slide_id, report)
    assert len(ids) == report.n_imported

    rows = ROIRepo(engine).for_slide(slide_id)
    assert len(rows) == report.n_imported
    for row in rows:
        pts = json.loads(row["points_json"])
        assert len(pts) >= 3, "geometry must survive the write, not just a bbox"


def test_geojson_round_trip_preserves_geometry_and_class(tmp_path):
    """import -> persist -> export -> import must be identity on the outline
    and the class. This is what makes the export/import pair worth having; it
    only works because the exporter stopped flattening shapes to their bbox."""
    from hescope.db import SlideRepo
    from hescope.geojson import slide_feature_collection

    engine = _engine(tmp_path)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="s.svs", path=str(tmp_path / "s.svs"),
        width=10000, height=10000, mpp=None,
    )
    first = parse_geojson_annotations(GEOJSON)
    import_annotations(engine, slide_id, first)

    exported = slide_feature_collection(engine, slide_id)
    second = parse_geojson_annotations(exported)

    assert second.n_imported == first.n_imported
    for a, b in zip(first.rois, second.rois):
        assert b.roi.kind == a.roi.kind
        assert b.roi.points == a.roi.points, "the outline changed on a round trip"
        assert b.label == a.label


def test_asap_round_trips_through_our_geojson(tmp_path):
    """An ASAP file imported here must survive export as QuPath GeoJSON, which
    is the practical path from a CAMELYON ground-truth set into QuPath."""
    from hescope.db import SlideRepo
    from hescope.geojson import slide_feature_collection

    engine = _engine(tmp_path)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="s.svs", path=str(tmp_path / "s.svs"),
        width=10000, height=10000, mpp=None,
    )
    first = parse_asap_xml(ASAP)
    import_annotations(engine, slide_id, first)
    second = parse_geojson_annotations(slide_feature_collection(engine, slide_id))

    assert second.n_imported == first.n_imported
    for a, b in zip(first.rois, second.rois):
        assert b.roi.points == a.roi.points
        assert b.label == a.label
