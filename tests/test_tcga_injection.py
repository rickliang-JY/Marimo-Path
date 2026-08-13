"""BUILD-PLAN-DB.md Phase 2: TCGA download -> storage -> database injection.

A completed download must not just leave a file on disk -- it must become a
slide row connected to its case and sample, automatically, at download time
(defect 2.1: the hierarchy and the slides you open used to be two
disconnected graphs -- ``tcga_files.slide_id`` written 0 of 50, 0 of 31
slides reachable from the hierarchy). These tests simulate the completed
download the way app.py's worker thread does it -- ``SlideRepo.register``
followed by ``TcgaCatalog.mark_downloaded(..., slide_id=...)`` -- without
going through the network or the marimo cell machinery (that wiring is
covered end-to-end by ``tests/test_tcga_open_report.py``).

Offline; tmp sqlite files. Never touches data/hescope.db (R-1).
"""

from __future__ import annotations

import sqlalchemy as sa

from hescope.store.db import SlideRepo, get_engine, init_db
from hescope.gdc.tcga_schema import TcgaCatalog


def _simulate_completed_download(engine, *, file_id, local_path, md5, hits_row) -> int:
    """What app.py's ``_on_download_open`` worker does once a file has
    finished downloading: register it as a slide (identity from its own
    content, ``md5sum`` from the GDC-supplied checksum), then link the
    ``tcga_files`` row back to that slide. Returns the slide id."""
    slide_id = SlideRepo(engine).register(
        source_kind="tcga",
        name=local_path.name,
        path=str(local_path),
        width=10,
        height=10,
        md5sum=md5,
    )
    cat = TcgaCatalog(engine)
    cat.upsert_rows([hits_row])
    assert cat.mark_downloaded(file_id, str(local_path), slide_id=slide_id) is True
    return slide_id


def _hits_row(file_id: str, file_name: str) -> dict:
    return {
        "file_id": file_id,
        "file_name": file_name,
        "file_size": 123,
        "md5sum": "5" * 32,
        "project_id": "TCGA-BRCA",
        "project_name": "Breast Invasive Carcinoma",
        "disease_type": "Breast Invasive Carcinoma",
        "primary_site": "Breast",
        "program": "TCGA",
        "case_submitter_id": "TCGA-BH-A18H",
        "case_uuid": None,
        "sample_id": "TCGA-BH-A18H-01A",
        "sample_submitter_id": "TCGA-BH-A18H-01A",
        "sample_type": "Primary Tumor",
        "tissue_type": "Tumor",
    }


def test_a_completed_download_produces_a_linked_slide(tmp_path):
    """The three writes a completed download must leave behind: a slides
    row, a slide_files row for it, and a tcga_files.slide_id pointing at
    it."""
    engine = get_engine(f"sqlite:///{tmp_path}/inject.db")
    init_db(engine)
    local_path = tmp_path / "TCGA-BH-A18H-01A-01-TS1.svs"
    local_path.write_bytes(b"pretend slide pixels")

    slide_id = _simulate_completed_download(
        engine, file_id="fid-1", local_path=local_path, md5="5" * 32,
        hits_row=_hits_row("fid-1", local_path.name),
    )

    repo = SlideRepo(engine)
    slide = repo.get(slide_id)
    assert slide is not None
    assert slide["md5sum"] == "5" * 32

    files_for_slide = repo.files_for(slide_id)
    assert len(files_for_slide) == 1
    assert files_for_slide[0]["path"] == str(local_path.resolve())

    with engine.connect() as conn:
        linked = conn.execute(
            sa.text("SELECT slide_id FROM tcga_files WHERE file_id='fid-1'")
        ).scalar_one()
    assert linked == slide_id


def test_the_case_sample_project_chain_resolves_from_the_slide_in_one_query(tmp_path):
    """'Every ROI on primary-tumour slides from case X' needs a join path
    that has rows in it -- this is that join, slide -> tcga_files ->
    tcga_samples -> tcga_cases -> tcga_projects, in a single query."""
    engine = get_engine(f"sqlite:///{tmp_path}/inject_join.db")
    init_db(engine)
    local_path = tmp_path / "TCGA-BH-A18H-01A-01-TS1.svs"
    local_path.write_bytes(b"pretend slide pixels for the join test")

    slide_id = _simulate_completed_download(
        engine, file_id="fid-join", local_path=local_path, md5="6" * 32,
        hits_row=_hits_row("fid-join", local_path.name),
    )

    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT s.id, p.project_id, c.submitter_id, sm.sample_type "
                "FROM slides s "
                "JOIN tcga_files f ON f.slide_id = s.id "
                "JOIN tcga_samples sm ON sm.sample_id = f.sample_id "
                "JOIN tcga_cases c ON c.submitter_id = sm.case_submitter_id "
                "JOIN tcga_projects p ON p.project_id = c.project_id "
                "WHERE s.id = :sid"
            ),
            {"sid": slide_id},
        ).one()
    assert row.id == slide_id
    assert row.project_id == "TCGA-BRCA"
    assert row.submitter_id == "TCGA-BH-A18H"
    assert row.sample_type == "Primary Tumor"


def test_re_registering_a_finished_download_is_idempotent(tmp_path):
    """A retried or re-run download (the same file, same identity) must fold
    into the SAME slide row, not create a second one -- and the tcga_files
    row must keep pointing at the one slide."""
    engine = get_engine(f"sqlite:///{tmp_path}/inject_idem.db")
    init_db(engine)
    local_path = tmp_path / "TCGA-BH-A18H-01A-01-TS1.svs"
    local_path.write_bytes(b"pretend slide pixels, registered twice")

    slide_id_1 = _simulate_completed_download(
        engine, file_id="fid-idem", local_path=local_path, md5="7" * 32,
        hits_row=_hits_row("fid-idem", local_path.name),
    )
    slide_id_2 = _simulate_completed_download(
        engine, file_id="fid-idem", local_path=local_path, md5="7" * 32,
        hits_row=_hits_row("fid-idem", local_path.name),
    )

    assert slide_id_1 == slide_id_2
    with engine.connect() as conn:
        slide_count = conn.execute(sa.text("SELECT COUNT(*) FROM slides")).scalar_one()
        linked = conn.execute(
            sa.text("SELECT slide_id FROM tcga_files WHERE file_id='fid-idem'")
        ).scalar_one()
    assert slide_count == 1, "a retried download must not create a second slide row"
    assert linked == slide_id_1


def test_mark_downloaded_on_an_unknown_id_leaves_the_hierarchy_untouched(tmp_path):
    """Same contract pinned directly against TcgaCatalog in
    tests/test_tcga_schema.py, re-asserted here at the injection boundary: a
    stale or wrong file_id must not silently create a metadata-free
    tcga_files row (defect 2.4), and must not create a slide either."""
    engine = get_engine(f"sqlite:///{tmp_path}/inject_unknown.db")
    init_db(engine)
    cat = TcgaCatalog(engine)

    assert cat.mark_downloaded("no-such-file-id", str(tmp_path / "x.svs")) is False

    with engine.connect() as conn:
        files_count = conn.execute(sa.text("SELECT COUNT(*) FROM tcga_files")).scalar_one()
    assert files_count == 0
