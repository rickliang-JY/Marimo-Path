"""Comparable statistics across a slide's ROIs."""

from __future__ import annotations

import json

import pytest

from hescope.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.rois import ROI
from hescope.stats_table import (
    label_summary,
    roi_stats_rows,
    rows_to_csv,
    rows_to_json,
)


@pytest.fixture()
def slide(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'stats.db'}")
    init_db(engine)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="s.svs", path=str(tmp_path / "s.svs"),
        width=10000, height=10000, mpp=0.5,
    )
    return engine, slide_id


def _add(engine, slide_id, *, box, label, tissue=None, h=None, rgb=None):
    stats = None
    if tissue is not None:
        stats = {
            "width_px": 128, "height_px": 128,   # PATCH pixels, deliberately
            "mean_rgb": rgb or [10.0, 20.0, 30.0],
            "he_deconvolution": {"hematoxylin_mean": h, "eosin_mean": 0.01},
            "tissue_fraction": tissue,
        }
    return ROIRepo(engine).add(
        slide_id,
        ROI(kind="rect", points=((float(box[0]), float(box[1])),
                                 (float(box[2]), float(box[3])))),
        label=label, stats=stats,
    )


def test_physical_size_comes_from_the_bbox_not_the_patch(slide):
    """THE UNIT TRAP. bbox is level-0; stats width_px/height_px are the
    patch's, and extract_patch downsamples. Deriving area from the patch is
    what overstated density_per_mm2 by the downsample squared (R07-2)."""
    engine, slide_id = slide
    _add(engine, slide_id, box=(0, 0, 1024, 512), label="tumor", tissue=0.5, h=0.2)

    row = roi_stats_rows(engine, slide_id, mpp=0.5)[0]
    assert row["width_px"] == 1024 and row["height_px"] == 512   # not 128
    assert row["width_um"] == pytest.approx(512.0)               # 1024 * 0.5
    assert row["area_mm2"] == pytest.approx(512.0 * 256.0 / 1e6)


def test_without_mpp_the_physical_columns_are_none_not_a_guess(slide):
    engine, slide_id = slide
    _add(engine, slide_id, box=(0, 0, 100, 100), label="x", tissue=0.5, h=0.1)
    row = roi_stats_rows(engine, slide_id, mpp=None)[0]
    assert row["width_px"] == 100
    assert row["width_um"] is None and row["area_mm2"] is None


def test_an_roi_without_statistics_still_gets_a_row(slide):
    """It was drawn and it has a label. Dropping it would under-report how
    much of the slide is annotated."""
    engine, slide_id = slide
    _add(engine, slide_id, box=(0, 0, 10, 10), label="measured", tissue=0.5, h=0.1)
    _add(engine, slide_id, box=(20, 20, 30, 30), label="unmeasured")

    rows = roi_stats_rows(engine, slide_id, mpp=0.5)
    assert len(rows) == 2
    bare = next(r for r in rows if r["label"] == "unmeasured")
    assert bare["has_stats"] is False
    assert bare["tissue_fraction"] is None
    assert bare["width_px"] == 10, "geometry is known even with no statistics"


def test_malformed_stats_do_not_break_the_table(slide):
    engine, slide_id = slide
    roi_id = _add(engine, slide_id, box=(0, 0, 8, 8), label="x")
    with engine.connect() as conn:
        import sqlalchemy as sa

        conn.execute(
            sa.text("UPDATE rois SET stats_json = :s WHERE id = :i"),
            {"s": "{not json", "i": roi_id},
        )
        conn.commit()
    rows = roi_stats_rows(engine, slide_id, mpp=0.5)
    assert len(rows) == 1 and rows[0]["tissue_fraction"] is None


def test_label_summary_reports_spread_not_just_a_mean(slide):
    """A mean alone invites 'tumour reads higher' from two overlapping
    distributions; n and SD are what make it checkable."""
    engine, slide_id = slide
    for tissue in (0.40, 0.60):
        _add(engine, slide_id, box=(0, 0, 100, 100), label="tumor",
             tissue=tissue, h=0.3)
    _add(engine, slide_id, box=(0, 0, 100, 100), label="stroma",
         tissue=0.10, h=0.05)

    summary = {s["label"]: s for s in label_summary(roi_stats_rows(engine, slide_id, 0.5))}
    assert summary["tumor"]["n"] == 2
    assert summary["tumor"]["tissue_fraction_mean"] == pytest.approx(0.50)
    assert summary["tumor"]["tissue_fraction_sd"] == pytest.approx(0.10)
    assert summary["stroma"]["n"] == 1


def test_a_single_sample_reports_no_standard_deviation(slide):
    """0.0 would read as a tight distribution; there is no spread of one."""
    engine, slide_id = slide
    _add(engine, slide_id, box=(0, 0, 100, 100), label="only", tissue=0.5, h=0.2)
    only = label_summary(roi_stats_rows(engine, slide_id, 0.5))[0]
    assert only["n"] == 1
    assert only["tissue_fraction_mean"] == pytest.approx(0.5)
    assert only["tissue_fraction_sd"] is None


def test_label_summary_of_nothing_is_empty():
    assert label_summary([]) == []


def test_unlabelled_rois_group_under_the_empty_label(slide):
    engine, slide_id = slide
    _add(engine, slide_id, box=(0, 0, 10, 10), label="", tissue=0.2, h=0.1)
    assert label_summary(roi_stats_rows(engine, slide_id, 0.5))[0]["label"] == ""


def test_csv_export_keeps_a_bbox_in_one_cell(slide):
    """A bbox is a list; written raw it would spill across four columns and
    silently shift every field to its right."""
    import csv
    import io

    engine, slide_id = slide
    _add(engine, slide_id, box=(1, 2, 3, 4), label="a", tissue=0.5, h=0.1)
    csv_text = rows_to_csv(roi_stats_rows(engine, slide_id, 0.5))

    header, *body = list(csv.reader(io.StringIO(csv_text)))
    assert "roi_id" in header and "area_mm2" in header
    assert len(body) == 1
    assert len(body[0]) == len(header), "a value spilled into another column"
    assert body[0][header.index("bbox_level0")] == "[1,2,3,4]"


def test_csv_of_nothing_is_empty_not_a_header_alone(slide):
    engine, slide_id = slide
    assert rows_to_csv(roi_stats_rows(engine, slide_id, 0.5)) == ""


def test_json_export_round_trips(slide):
    engine, slide_id = slide
    _add(engine, slide_id, box=(0, 0, 10, 10), label="a", tissue=0.5, h=0.1)
    rows = roi_stats_rows(engine, slide_id, 0.5)
    assert json.loads(rows_to_json(rows))[0]["label"] == "a"


def test_none_survives_export_as_empty_rather_than_the_string_none(slide):
    engine, slide_id = slide
    _add(engine, slide_id, box=(0, 0, 10, 10), label="bare")
    line = rows_to_csv(roi_stats_rows(engine, slide_id, None)).splitlines()[1]
    assert ",None," not in line
