"""The TCGA-shaped catalog: program -> project -> case -> sample -> file."""

from __future__ import annotations

import json
import pathlib

import pytest

from hescope.db import get_engine
from hescope.tcga_schema import (
    TcgaCatalog,
    hits_to_rows,
    parse_barcode,
    storage_relpath,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "gdc_files_response.json"


@pytest.fixture()
def rows():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return hits_to_rows(data["data"]["hits"])


@pytest.fixture()
def catalog(tmp_path):
    return TcgaCatalog(get_engine(f"sqlite:///{tmp_path / 'tcga.db'}"))


# --- barcodes --------------------------------------------------------------


@pytest.mark.parametrize(
    "text, case, sample, sample_type",
    [
        ("TCGA-BH-A18H-01A-01-TSA.75dba5a3.svs", "TCGA-BH-A18H", "TCGA-BH-A18H-01A", "Primary Tumor"),
        ("TCGA-OL-A5S0-01Z-00-DX1.49A7AC9D.svs", "TCGA-OL-A5S0", "TCGA-OL-A5S0-01Z", "Primary Tumor"),
        ("TCGA-BH-A18H-11A-01-TS1.svs", "TCGA-BH-A18H", "TCGA-BH-A18H-11A", "Solid Tissue Normal"),
        ("TCGA-BH-A18H", "TCGA-BH-A18H", None, None),   # a case barcode is valid
        ("not-a-barcode.svs", None, None, None),
        ("", None, None, None),
        (None, None, None, None),
    ],
)
def test_parse_barcode(text, case, sample, sample_type):
    """A filename is enough to recover the hierarchy -- no network, no catalog.
    GDC names slide files <barcode>.<uuid>.svs."""
    bc = parse_barcode(text)
    assert bc.case == case
    assert bc.sample == sample
    assert bc.sample_type == sample_type


def test_sample_code_distinguishes_tumour_from_normal():
    assert parse_barcode("TCGA-BH-A18H-01A").sample_type == "Primary Tumor"
    assert parse_barcode("TCGA-BH-A18H-11A").sample_type == "Solid Tissue Normal"


# --- where files land ------------------------------------------------------


def test_layout_mirrors_the_hierarchy(rows):
    """A directory of file UUIDs says nothing about what is in it."""
    paths = [
        storage_relpath(r["file_id"], r["file_name"], r["project_id"],
                        r["case_submitter_id"]).as_posix()
        for r in rows
    ]
    assert all(p.startswith("TCGA-BRCA/TCGA-") for p in paths)
    # the two slides of one case share that case's directory
    a14p = [p for p in paths if "TCGA-E2-A14P" in p]
    assert len(a14p) == 2
    assert len({p.rsplit("/", 1)[0] for p in a14p}) == 1


def test_layout_degrades_one_level_at_a_time():
    assert storage_relpath("fid", "s.svs", "TCGA-BRCA", None).as_posix() == \
        "TCGA-BRCA/unknown-case/s.svs"
    assert storage_relpath("fid", "s.svs", None, None).as_posix() == \
        "unknown-project/unknown-case/s.svs"
    # no file name at all: still somewhere deterministic
    assert storage_relpath("fid-9", None, "P", "C").as_posix() == "P/C/fid-9.svs"


@pytest.mark.parametrize(
    "evil",
    ["../../escape.svs", "..\\..\\escape.svs", "/etc/passwd", "C:/Windows/x.svs",
     "C:evil.svs", "..", ".", "a/b/c.svs"],
)
def test_layout_cannot_escape_the_data_root(evil):
    """Server-supplied strings decide these directory names. This repo has had
    two path-escape findings already (R02-3, R05-1)."""
    rel = storage_relpath("fid", evil, evil, evil)
    assert not rel.is_absolute()
    parts = rel.as_posix().split("/")
    assert len(parts) == 3, rel
    assert ".." not in parts and "" not in parts
    assert ":" not in rel.as_posix()


# --- the catalog -----------------------------------------------------------


def test_upsert_builds_the_hierarchy(catalog, rows):
    assert catalog.upsert_rows(rows) == 3
    stats = catalog.stats()
    assert stats == {
        "projects": 1, "cases": 2, "samples": 3, "files": 3, "downloaded": 0
    }


def test_upsert_is_idempotent(catalog, rows):
    catalog.upsert_rows(rows)
    assert catalog.upsert_rows(rows) == 0, "re-running a search must not duplicate"
    assert catalog.stats()["files"] == 3


def test_a_repeated_search_never_forgets_a_download(catalog, rows, tmp_path):
    """The flat catalog's INSERT OR IGNORE hid a real bug here (R02-2). Pin the
    behaviour: download state survives re-ingesting the same search."""
    catalog.upsert_rows(rows)
    fid = rows[0]["file_id"]
    slide = tmp_path / "downloaded.svs"
    slide.write_bytes(b"x")
    catalog.mark_downloaded(fid, slide, slide_id=42)

    catalog.upsert_rows(rows)  # search again

    assert catalog.local_file(fid) == slide
    got = next(f for f in catalog.files() if f["file_id"] == fid)
    assert got["downloaded"] is True and got["slide_id"] == 42


def test_local_file_rejects_a_stale_path(catalog, rows, tmp_path):
    catalog.upsert_rows(rows)
    fid = rows[0]["file_id"]
    slide = tmp_path / "gone.svs"
    slide.write_bytes(b"x")
    catalog.mark_downloaded(fid, slide)
    assert catalog.local_file(fid) == slide
    slide.unlink()
    assert catalog.local_file(fid) is None
    assert catalog.local_file("no-such-file") is None


def test_files_carries_what_the_flat_table_discarded(catalog, rows):
    catalog.upsert_rows(rows)
    row = catalog.files(project_id="TCGA-BRCA")[0]
    assert row["disease_type"] == "Breast Invasive Carcinoma"
    assert row["sample_submitter_id"].startswith("TCGA-")
    assert row["tissue_type"] == "Tumor"


def test_files_filters_by_case_and_sample_type(catalog, rows):
    catalog.upsert_rows(rows)
    assert len(catalog.files(case_submitter_id="TCGA-E2-A14P")) == 2
    assert catalog.files(case_submitter_id="nobody") == []
    assert len(catalog.files(sample_type="Primary Tumor")) >= 1
    assert catalog.files(downloaded_only=True) == []


def test_hits_to_rows_survives_a_sparse_response():
    """GDC omits fields that were not requested; nothing here may raise."""
    assert hits_to_rows([]) == []
    sparse = hits_to_rows([{"file_id": "f", "file_name": "TCGA-AA-BBBB-01A.svs"}])
    assert sparse[0]["case_submitter_id"] == "TCGA-AA-BBBB"   # from the barcode
    assert sparse[0]["sample_type"] == "Primary Tumor"
    assert sparse[0]["project_id"] is None

    blank = hits_to_rows([{}])
    assert blank[0]["file_id"] == "" and blank[0]["case_submitter_id"] is None


def test_upsert_skips_rows_with_no_file_id(catalog):
    assert catalog.upsert_rows([{"file_name": "x.svs"}]) == 0
    assert catalog.stats()["files"] == 0


# --- migrating the flat catalog into the hierarchy -------------------------


def _flat_catalog(tmp_path, rows):
    """A legacy data/tcga/catalog.db, written by the flat SlideCatalog."""
    from hescope.tcga import SlideCatalog, SlideRecord

    cat = SlideCatalog(tmp_path / "catalog.db")
    cat.upsert([
        SlideRecord(
            file_id=r["file_id"], file_name=r["file_name"],
            file_size=r["file_size"], project_id=r["project_id"],
            case_submitter_id=r["case_submitter_id"],
            sample_type=r["sample_type"], primary_site=r["primary_site"],
            local_path=None, md5sum=r["md5sum"],
        )
        for r in rows
    ])
    return cat


def test_migration_dry_run_changes_nothing(tmp_path, rows, capsys):
    from hescope.cli import main

    _flat_catalog(tmp_path, rows)
    db_url = f"sqlite:///{tmp_path / 'main.db'}"
    assert main(["--db", db_url, "init"]) == 0
    capsys.readouterr()

    assert main([
        "--db", db_url, "migrate-tcga-catalog",
        "--catalog-db", str(tmp_path / "catalog.db"), "--dry-run",
    ]) == 0
    out = capsys.readouterr().out
    assert "would import 3 file row(s)" in out
    assert "nothing was changed" in out

    from hescope.db import get_engine
    assert TcgaCatalog(get_engine(db_url)).stats()["files"] == 0


def test_migration_builds_the_hierarchy_and_keeps_downloads(tmp_path, rows, capsys):
    from hescope.cli import main
    from hescope.db import get_engine

    cat = _flat_catalog(tmp_path, rows)
    slide = tmp_path / "already-downloaded.svs"
    slide.write_bytes(b"x" * 16)
    cat.mark_downloaded(rows[0]["file_id"], str(slide))

    db_url = f"sqlite:///{tmp_path / 'main.db'}"
    main(["--db", db_url, "init"])
    capsys.readouterr()
    assert main([
        "--db", db_url, "migrate-tcga-catalog",
        "--catalog-db", str(tmp_path / "catalog.db"),
    ]) == 0
    out = capsys.readouterr().out
    assert "imported 3 new file row(s)" in out
    assert "files were not moved" in out

    catalog = TcgaCatalog(get_engine(db_url))
    stats = catalog.stats()
    assert stats["files"] == 3 and stats["downloaded"] == 1
    assert stats["cases"] == 2
    # the download survived, and the file was left exactly where it was
    assert catalog.local_file(rows[0]["file_id"]) == slide
    assert slide.is_file()


def test_migration_recovers_the_sample_level_from_the_barcode(tmp_path, rows, capsys):
    """The flat table never stored a sample id. The barcode in the file name is
    the only place sample identity survived, so the level is reconstructed
    rather than dropped."""
    from hescope.cli import main
    from hescope.db import get_engine

    _flat_catalog(tmp_path, rows)
    db_url = f"sqlite:///{tmp_path / 'main.db'}"
    main(["--db", db_url, "init"])
    main(["--db", db_url, "migrate-tcga-catalog",
          "--catalog-db", str(tmp_path / "catalog.db")])
    capsys.readouterr()

    files = TcgaCatalog(get_engine(db_url)).files()
    barcodes = {f["sample_submitter_id"] for f in files}
    assert any(b and b.startswith("TCGA-") for b in barcodes), barcodes


def test_migration_is_idempotent(tmp_path, rows, capsys):
    from hescope.cli import main
    from hescope.db import get_engine

    _flat_catalog(tmp_path, rows)
    db_url = f"sqlite:///{tmp_path / 'main.db'}"
    main(["--db", db_url, "init"])
    args = ["--db", db_url, "migrate-tcga-catalog",
            "--catalog-db", str(tmp_path / "catalog.db")]
    main(args)
    capsys.readouterr()
    assert main(args) == 0
    assert "imported 0 new file row(s)" in capsys.readouterr().out
    assert TcgaCatalog(get_engine(db_url)).stats()["files"] == 3


def test_migration_without_a_catalog_is_not_an_error(tmp_path, capsys):
    from hescope.cli import main

    db_url = f"sqlite:///{tmp_path / 'main.db'}"
    main(["--db", db_url, "init"])
    capsys.readouterr()
    assert main(["--db", db_url, "migrate-tcga-catalog",
                 "--catalog-db", str(tmp_path / "absent.db")]) == 0
    assert "nothing to migrate" in capsys.readouterr().out
