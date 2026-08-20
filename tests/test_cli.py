"""Tests for hescope.cli. Offline; tmp sqlite files; images made with PIL."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from PIL import Image

from hescope.cli import main
from hescope.store.db import SlideRepo, get_engine


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{tmp_path}/cli.db"


@pytest.fixture()
def image_dir(tmp_path):
    """A folder with 2 tiny valid PNGs and 1 bogus .png text file."""
    d = tmp_path / "images"
    d.mkdir()
    for i, color in enumerate([(220, 140, 180), (150, 120, 200)]):
        arr = np.zeros((64, 96, 3), dtype=np.uint8)
        arr[..., 0], arr[..., 1], arr[..., 2] = color
        Image.fromarray(arr, "RGB").save(d / f"slide_{i}.png")
    (d / "bogus.png").write_text("this is not an image at all")
    return d


def test_init_creates_tables(db_url, capsys):
    assert main(["--db", db_url, "init"]) == 0
    out = capsys.readouterr().out
    assert "Initialized" in out
    engine = get_engine(db_url)
    tables = set(sa.inspect(engine).get_table_names())
    assert {"slides", "rois", "agent_runs"} <= tables
    engine.dispose()


def test_ingest_and_list(db_url, image_dir, capsys):
    assert main(["--db", db_url, "init"]) == 0
    capsys.readouterr()
    assert main(["--db", db_url, "ingest", str(image_dir)]) == 0
    captured = capsys.readouterr()
    assert "2 registered" in captured.out
    assert "1 skipped" in captured.out
    assert "bogus.png" in captured.err  # warned about the unreadable file

    assert main(["--db", db_url, "list"]) == 0
    out = capsys.readouterr().out
    assert "slide_0.png" in out
    assert "slide_1.png" in out
    assert "bogus" not in out
    assert "2 slide(s)" in out

    engine = get_engine(db_url)
    slides = SlideRepo(engine).list()
    assert len(slides) == 2
    assert {(s["width"], s["height"]) for s in slides} == {(96, 64)}
    engine.dispose()


def test_ingest_idempotent(db_url, image_dir, capsys):
    main(["--db", db_url, "init"])
    main(["--db", db_url, "ingest", str(image_dir)])
    capsys.readouterr()
    # re-ingest the same folder: no duplicates
    assert main(["--db", db_url, "ingest", str(image_dir)]) == 0
    capsys.readouterr()
    engine = get_engine(db_url)
    slides = SlideRepo(engine).list()
    assert len(slides) == 2
    engine.dispose()
    assert main(["--db", db_url, "list"]) == 0
    assert "2 slide(s)" in capsys.readouterr().out


def test_ingest_kind_and_list_filter(db_url, image_dir, capsys):
    main(["--db", db_url, "init"])
    main(["--db", db_url, "ingest", str(image_dir), "--kind", "tcga"])
    capsys.readouterr()
    assert main(["--db", db_url, "list", "--kind", "tcga"]) == 0
    assert "2 slide(s)" in capsys.readouterr().out
    assert main(["--db", db_url, "list", "--kind", "local"]) == 0
    assert "no slides registered" in capsys.readouterr().out


def test_ingest_missing_path_errors(db_url, capsys):
    main(["--db", db_url, "init"])
    assert main(["--db", db_url, "ingest", "/does/not/exist"]) == 1
    assert "no such file" in capsys.readouterr().err


def test_ingest_reports_merged_duplicate_content_instead_of_double_counting(
    db_url, tmp_path, capsys
):
    """Ingesting a directory with two byte-identical images: registration
    correctly merges them into ONE slide (identity is content, not path --
    that is Phase 1's whole point, and is not itself a bug). What WAS wrong
    is the COUNT the CLI printed: it reported "2 registered" -- one per
    ``register()`` call -- while only 1 slide row ever existed, silently
    dropping the fact that one of the two files landed on an existing row.

    BUILD-PLAN-DB.md Phase 1 debugger round 2, finding 3. The pre-fix
    ``tests/test_cli.py::test_ingest_single_file_and_recursive`` was edited
    (commit f9bc290) to use DIFFERENT pixel content for its two files
    specifically to dodge this collapse rather than pin what happens when it
    occurs -- this test uses IDENTICAL content on purpose, to check the
    report instead of avoiding it.

    Measured against the pre-fix CLI with this exact seed:

        registered slide id=1 a.png (32x32)
        registered slide id=1 b.png (32x32)
        ingest complete: 2 registered, 0 skipped
        actual slide rows in the database: 1
    """
    d = tmp_path / "images"
    d.mkdir()
    payload = np.zeros((32, 32, 3), dtype=np.uint8)
    Image.fromarray(payload, "RGB").save(d / "a.png")
    Image.fromarray(payload, "RGB").save(d / "b.png")  # byte-identical to a.png

    main(["--db", db_url, "init"])
    capsys.readouterr()
    assert main(["--db", db_url, "ingest", str(d)]) == 0
    out = capsys.readouterr().out

    engine = get_engine(db_url)
    slides = SlideRepo(engine).list()
    engine.dispose()
    assert len(slides) == 1, "byte-identical content must merge into one slide row"

    # the report must say ONE new slide and ONE file merged into it -- not
    # "2 registered", which claims a second slide row that was never created
    assert "1 registered" in out
    assert "1 merged" in out
    assert "2 file(s) seen" in out


def test_ingest_single_file_and_recursive(db_url, tmp_path, capsys):
    # distinct pixel content for each file: registration now dedups on
    # CONTENT (hescope.core.identity), not path, so two byte-identical files
    # would otherwise collapse into one slide and defeat this test's point.
    Image.fromarray(np.zeros((32, 32, 3), dtype=np.uint8), "RGB").save(
        tmp_path / "one.png"
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    Image.fromarray(np.full((32, 32, 3), 255, dtype=np.uint8), "RGB").save(
        sub / "two.png"
    )

    main(["--db", db_url, "init"])
    # single file ingest
    assert main(["--db", db_url, "ingest", str(tmp_path / "one.png")]) == 0
    # non-recursive dir ingest finds nothing new at top level (one.png dup)
    assert main(["--db", db_url, "ingest", str(tmp_path)]) == 0
    # recursive picks up sub/two.png
    assert main(["--db", db_url, "ingest", str(tmp_path), "--recursive"]) == 0
    capsys.readouterr()
    engine = get_engine(db_url)
    names = {s["name"] for s in SlideRepo(engine).list()}
    assert names == {"one.png", "two.png"}
    engine.dispose()


def test_db_option_overrides_env(tmp_path, monkeypatch, capsys):
    env_url = f"sqlite:///{tmp_path}/env.db"
    cli_url = f"sqlite:///{tmp_path}/cli.db"
    monkeypatch.setenv("HESCOPE_DB_URL", env_url)
    assert main(["--db", cli_url, "init"]) == 0
    engine = get_engine(cli_url)
    assert "slides" in sa.inspect(engine).get_table_names()
    engine.dispose()
    env_engine = get_engine(env_url)
    assert sa.inspect(env_engine).get_table_names() == []
    env_engine.dispose()


def test_dedupe_slides_merges_rows_that_name_one_file(tmp_path, capsys):
    """R05-2 repair path: a database filled in before paths were canonicalized
    already holds several rows per file, so the fix needs a way to reunite
    them (the shipped data/hescope.db had two rows for the demo slide, one ROI
    hanging off each)."""
    from hescope.store.db import ROIRepo, Slide
    from hescope.core.rois import ROI

    db_url = f"sqlite:///{tmp_path}/dedupe.db"
    slide = tmp_path / "dup.png"
    Image.fromarray(
        np.full((8, 8, 3), 240, dtype=np.uint8), "RGB"
    ).save(slide)
    main(["--db", db_url, "init"])
    engine = get_engine(db_url)
    with sa.orm.Session(engine) as s:  # pre-fix rows: bypass register()
        for path in (str(slide.resolve()), str(slide).replace("\\", "/")):
            s.add(Slide(source_kind="local", name="dup.png", path=path,
                        width=8, height=8, extra_json="{}"))
        s.commit()
    ids = [row["id"] for row in SlideRepo(engine).list()]
    assert len(ids) == 2
    for sid in ids:
        ROIRepo(engine).add(sid, ROI(kind="rect", points=((0.0, 0.0), (4.0, 4.0))))

    assert main(["--db", db_url, "dedupe-slides"]) == 0

    out = capsys.readouterr().out
    assert f"merged slide id={ids[1]} into id={ids[0]}" in out
    assert len(SlideRepo(engine).list()) == 1
    assert len(ROIRepo(engine).for_slide(ids[0])) == 2, "annotations were lost"
    assert main(["--db", db_url, "dedupe-slides"]) == 0  # idempotent
    assert "no duplicate slide paths found" in capsys.readouterr().out
    engine.dispose()


# --- app launcher subcommand -------------------------------------------------


def test_find_app_py_locates_repo_app():
    from hescope.cli import find_app_py

    app_path = find_app_py()
    assert app_path.is_file()
    assert app_path.name == "app.py"


def test_build_marimo_cmd_defaults_and_passthrough():
    from hescope.cli import build_marimo_cmd

    app_py = Path("/x/app.py")
    # str(Path(...)) is platform-dependent ("\x\app.py" on Windows), so build
    # the expectation the same way build_marimo_cmd does.
    app_arg = str(app_py)
    cmd = build_marimo_cmd(app_py)
    assert cmd == ["marimo", "edit", app_arg, "--no-token"]
    cmd = build_marimo_cmd(app_py, port=8888, host="0.0.0.0")
    assert cmd == [
        "marimo", "edit", app_arg, "--no-token",
        "--port", "8888", "--host", "0.0.0.0",
    ]


@pytest.mark.parametrize("argv", [["app"], ["app", "--port", "9999"], []])
def test_app_command_execs_marimo(argv, monkeypatch, capsys):
    """`hescope app` and bare `hescope` replace the process with marimo."""
    calls = []
    monkeypatch.setattr(
        "hescope.cli.os.execvp",
        lambda exe, cmd: calls.append((exe, cmd)),
    )
    assert main(argv) == 0
    assert len(calls) == 1
    exe, cmd = calls[0]
    assert exe == "marimo"
    assert cmd[:2] == ["marimo", "edit"]
    assert cmd[2].endswith("app.py") and Path(cmd[2]).is_file()
    assert "--no-token" in cmd
    if "--port" in argv:
        assert cmd[cmd.index("--port") + 1] == "9999"
    out = capsys.readouterr().out
    assert "Cmd/Ctrl+." in out  # app-view hint is printed


def test_app_command_missing_marimo_binary(monkeypatch, capsys):
    def _raise(exe, cmd):
        raise OSError("No such file or directory")

    monkeypatch.setattr("hescope.cli.os.execvp", _raise)
    assert main(["app"]) == 1
    assert "could not launch marimo" in capsys.readouterr().err


def test_dedupe_slides_dry_run_shows_the_blast_radius_and_changes_nothing(
    tmp_path, capsys
):
    """R07-19: ``dedupe-slides`` was an irreversible one-shot with no preview.

    It rewrites the table a user's annotations hang off, in one uncancellable
    transaction, and its input is the live ``data/hescope.db``. That is why the
    repair of the shipped database sat parked for three review rounds: the tool
    was correct and tested, but there was no way to see what it would do first.
    """
    from hescope.store.db import ROIRepo, Slide
    from hescope.core.rois import ROI

    db_url = f"sqlite:///{tmp_path}/dryrun.db"
    slide = tmp_path / "dup.png"
    Image.fromarray(np.full((8, 8, 3), 240, dtype=np.uint8), "RGB").save(slide)
    main(["--db", db_url, "init"])
    engine = get_engine(db_url)
    with sa.orm.Session(engine) as s:  # pre-fix rows: bypass register()
        for path in (str(slide.resolve()), str(slide).replace("\\", "/")):
            s.add(Slide(source_kind="local", name="dup.png", path=path,
                        width=8, height=8, extra_json="{}"))
        s.commit()
    ids = [row["id"] for row in SlideRepo(engine).list()]
    roi_ids = [
        ROIRepo(engine).add(sid, ROI(kind="rect", points=((0.0, 0.0), (4.0, 4.0))))
        for sid in ids
    ]
    before = {row["id"]: row["path"] for row in SlideRepo(engine).list()}

    assert main(["--db", db_url, "dedupe-slides", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert f"would merge slide id={ids[1]} into id={ids[0]}" in out
    assert f"would move roi id={roi_ids[1]} from slide {ids[1]} to {ids[0]}" in out
    assert "nothing was changed" in out
    assert {row["id"]: row["path"] for row in SlideRepo(engine).list()} == before, (
        "--dry-run wrote to the database"
    )
    assert len(ROIRepo(engine).for_slide(ids[1])) == 1

    # ...and the real run then does exactly what the dry run described.
    assert main(["--db", db_url, "dedupe-slides"]) == 0
    assert len(SlideRepo(engine).list()) == 1
    assert len(ROIRepo(engine).for_slide(ids[0])) == 2
    engine.dispose()


def test_dedupe_slides_dry_run_reports_only_real_path_rewrites(tmp_path, capsys):
    """``merge_duplicate_slide_paths`` assigns the canonical path to EVERY
    keeper, so counting assignments overstates the change by an order of
    magnitude -- on the shipped database, 32 assignments for 2 real rewrites."""
    from hescope.store.db import Slide, plan_duplicate_slide_merge

    db_url = f"sqlite:///{tmp_path}/rewrites.db"
    a = tmp_path / "a.png"
    Image.fromarray(np.full((8, 8, 3), 240, dtype=np.uint8), "RGB").save(a)
    main(["--db", db_url, "init"])
    engine = get_engine(db_url)
    SlideRepo(engine).register(  # already canonical -> no rewrite
        source_kind="local", name="a.png", path=str(a), width=8, height=8
    )
    with sa.orm.Session(engine) as s:  # uncanonical -> one rewrite
        b = tmp_path / "b.png"
        Image.fromarray(np.full((8, 8, 3), 240, dtype=np.uint8), "RGB").save(b)
        s.add(Slide(source_kind="local", name="b.png",
                    path=str(b).replace("\\", "/"), width=8, height=8))
        s.commit()

    plan = plan_duplicate_slide_merge(engine)

    assert plan["merges"] == []
    assert len(plan["rewrites"]) == 1, plan["rewrites"]
    assert plan["rewrites"][0][2] == str(b.resolve())
    engine.dispose()


# --- migrate (Phase 0: versioned schema migrations) -------------------------
#
# Before these, `grep -rn "_cmd_migrate|'migrate'" tests/` matched nothing:
# the CLI subcommand's init_db-before-migrate ordering, its error branch,
# exit code, and both dry-run/real-run report paths were entirely
# unexercised -- which is exactly why the dry-run/real-run divergence below
# went unnoticed by a green suite.


def test_migrate_on_empty_database_reaches_schema_version(db_url, capsys):
    """A fresh database, migrated for real, ends at SCHEMA_VERSION."""
    from hescope.store.migrations import SCHEMA_VERSION, current_version

    assert main(["--db", db_url, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "migrated: version 0 ->" in out
    engine = get_engine(db_url)
    assert current_version(engine) == SCHEMA_VERSION
    engine.dispose()


def test_migrate_second_call_reports_already_at_version(db_url, capsys):
    """A second `migrate` call applies nothing and says so."""
    main(["--db", db_url, "migrate"])
    capsys.readouterr()

    assert main(["--db", db_url, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "already at version" in out


def test_migrate_reports_error_and_exits_1_on_a_raising_migration(
    db_url, capsys, monkeypatch
):
    """A migration that raises: exit code 1, and the error lands on stderr
    (not swallowed, not printed as if it were success)."""
    import hescope.store.migrations as mig

    def _boom(conn):
        raise RuntimeError("synthetic failure for the CLI test")

    monkeypatch.setattr(
        mig,
        "MIGRATIONS",
        (mig.Migration(version=1, name="synthetic failing migration", apply=_boom),),
    )

    rc = main(["--db", db_url, "migrate"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "synthetic failure for the CLI test" in captured.err
    assert "migration stopped" in captured.err


def test_migrate_dry_run_leaves_schema_migrations_absent(db_url, capsys):
    """--dry-run never creates schema_migrations (or anything else)."""
    assert main(["--db", db_url, "migrate", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "nothing was changed" in out
    engine = get_engine(db_url)
    assert "schema_migrations" not in sa.inspect(engine).get_table_names()
    engine.dispose()


def _build_narrow_db(path: Path) -> None:
    """The pre-Phase-0 schema: no agent_runs/interactions tables, no indexes
    on rois, no patch_path/stats_json/magnification columns -- carrying 1
    slide + 1 roi, same shape used by
    tests/test_migrations.py::test_upgraded_database_has_the_same_rois_indexes_as_a_fresh_one.
    """
    import sqlite3

    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE slides (
            id INTEGER PRIMARY KEY, source_kind VARCHAR(32), name VARCHAR(512),
            path VARCHAR(1024) UNIQUE, width INTEGER, height INTEGER,
            mpp FLOAT, extra_json TEXT, created_at DATETIME
        );
        CREATE TABLE rois (
            id INTEGER PRIMARY KEY, slide_id INTEGER, kind VARCHAR(32),
            points_json TEXT, bbox_json TEXT, label VARCHAR(512), notes TEXT,
            created_at DATETIME
        );
        INSERT INTO slides (id, source_kind, name, path, width, height, mpp, extra_json, created_at)
        VALUES (1, 'local', 's.svs', 's.svs', 4000, 3000, 0.25, '{}', '2024-01-01T00:00:00');
        INSERT INTO rois (id, slide_id, kind, points_json, bbox_json, label, notes, created_at)
        VALUES (1, 1, 'rect', '[[0,0],[10,10]]', '[0,0,10,10]', '', '', '2024-01-01T00:00:00');
        """
    )
    con.commit()
    con.close()


def test_migrate_dry_run_names_every_object_init_db_would_create(tmp_path, capsys):
    """`migrate --dry-run` is supposed to be a preview of `migrate`. The real
    command runs ``init_db(engine)`` before ``migrate(engine)``
    (hescope/cli.py:_cmd_migrate) -- a dry run that only asks ``migrate()``
    what it would do silently omits everything ``init_db`` would add.

    Measured against the pre-fix CLI (dry-run branch called only
    ``migrate(engine, dry_run=True)``) on a narrow (old-schema) database with
    1 slide + 1 roi: the dry run printed just "would apply migration 1:
    baseline schema (stamp only, no schema change)" / "nothing was changed",
    while running the SAME database for real added tables
    ``agent_runs``/``interactions``, 8 indexes and 3 columns on ``rois`` --
    none of them named anywhere in the dry-run output.
    """
    import sqlite3

    dry_db = tmp_path / "dry.db"
    real_db = tmp_path / "real.db"
    _build_narrow_db(dry_db)
    _build_narrow_db(real_db)

    assert main(["--db", f"sqlite:///{dry_db}", "migrate", "--dry-run"]) == 0
    dry_out = capsys.readouterr().out

    assert main(["--db", f"sqlite:///{real_db}", "migrate"]) == 0
    capsys.readouterr()

    def _objects(path):
        con = sqlite3.connect(path)
        names = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index') "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        roi_cols = {row[1] for row in con.execute("PRAGMA table_info(rois)").fetchall()}
        con.close()
        return names, roi_cols

    real_objects, real_roi_cols = _objects(real_db)
    dry_objects_after, dry_roi_cols_after = _objects(dry_db)  # must equal the narrow baseline

    assert dry_objects_after == {"slides", "rois"}, "the dry run itself must not have written"

    # everything the real run created beyond the narrow baseline (schema_migrations
    # is covered separately by the "would apply migration 1" line) must be named
    new_objects = real_objects - dry_objects_after - {"schema_migrations"}
    new_columns = real_roi_cols - dry_roi_cols_after
    assert new_objects, "sanity: the real run must actually have created something"
    for name in sorted(new_objects):
        assert name in dry_out, (
            f"{name!r} was created by the real `migrate` run but never named "
            "in `migrate --dry-run`'s output"
        )
    for name in sorted(new_columns):
        assert name in dry_out, (
            f"column {name!r} was added by the real `migrate` run but never "
            "named in `migrate --dry-run`'s output"
        )


def test_migrate_dry_run_reports_migration_2_backfill_counts(tmp_path, capsys):
    """`migrate --dry-run`'s report for migration 2 (Phase 1: the SVS <-> ROI
    relationship) must name the counts it would write -- not just that
    migration 2 is pending -- so a reviewer can see the blast radius before
    running it against the real database (the same reasoning as
    `plan_duplicate_slide_merge`, R07-19)."""
    narrow_db = tmp_path / "narrow.db"
    _build_narrow_db(narrow_db)  # 1 slide (path 's.svs', does not resolve), 1 roi

    assert main(["--db", f"sqlite:///{narrow_db}", "migrate", "--dry-run"]) == 0
    out = capsys.readouterr().out

    assert "would apply migration 2" in out
    assert "would write 1 slide_files row(s) (1 marked missing)" in out
    assert "0 distinct identity(ies)" in out
    assert "1 roi(s) with bbox backfilled" in out

    # and the dry run really did not write anything
    con = sa.create_engine(f"sqlite:///{narrow_db}")
    with con.connect() as c:
        assert "slide_files" not in sa.inspect(c).get_table_names()
    con.dispose()


def test_migrate_dry_run_does_not_crash_on_a_pre_migration_2_database_with_a_tcga_download(
    tmp_path, capsys
):
    """BUILD-PLAN-DB round 3 finding 1: `migrate --dry-run` must not crash on
    exactly the databases migration 3 exists for -- a legacy (schema version
    0) database, before migration 2 has ever added `slides.identity_scheme`,
    that already has a tcga_files row with a `local_path` (the shape app.py
    writes at download time, or `migrate-tcga-catalog` writes on import).

    Measured against the un-fixed CLI:

        sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such
        column: identity_scheme
        [SQL: SELECT id FROM slides WHERE identity_scheme='sha256' AND
        identity_key=?]
        exit=1

    printed after the migration-2 report line -- a half-printed report and a
    stack trace instead of the preview the operator was told to check first.
    """
    import sqlite3

    db_path = tmp_path / "narrow_with_download.db"
    _build_narrow_db(db_path)
    con = sqlite3.connect(db_path)
    con.execute(
        """
        CREATE TABLE tcga_files (
            file_id VARCHAR(64) PRIMARY KEY, file_name VARCHAR(512),
            file_size INTEGER, md5sum VARCHAR(64), sample_id VARCHAR(64),
            case_submitter_id VARCHAR(64), project_id VARCHAR(64),
            local_path VARCHAR(1024), downloaded_at DATETIME,
            first_seen_at DATETIME NOT NULL, slide_id INTEGER
        )
        """
    )
    real_file = tmp_path / "downloaded.svs"
    real_file.write_bytes(b"downloaded before migration 2 ever ran")
    con.execute(
        "INSERT INTO tcga_files (file_id, local_path, first_seen_at) "
        "VALUES ('fid-narrow', ?, '2024-01-01 00:00:00')",
        (str(real_file.resolve()),),
    )
    con.commit()
    con.close()

    rc = main(["--db", f"sqlite:///{db_path}", "migrate", "--dry-run"])
    out = capsys.readouterr()

    assert rc == 0, f"dry run crashed:\n{out.err}"
    assert "would apply migration 3" in out.out
    assert "could not be linked" in out.out


def test_migrate_dry_run_does_not_mutate_a_non_wal_database(tmp_path, capsys):
    """`migrate --dry-run` must not write to the file it promises not to
    touch (R-1's whole premise for a dry run). Before the fix, the version
    probe opened its connection through the same pragma-hooked engine
    `migrate` (no --dry-run) writes through, and that hook flips
    journal_mode to WAL -- a persistent header change -- the instant
    anything connects, dry run or not.

    Measured on a copy forced to journal_mode=DELETE: sha256 changed and
    journal_mode read back as 'wal' after a --dry-run call that printed
    'nothing was changed'.
    """
    import hashlib
    import sqlite3

    from hescope.store.db import init_db

    db_path = tmp_path / "nonwal.db"
    engine = get_engine(f"sqlite:///{db_path}")
    init_db(engine)
    engine.dispose()

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=DELETE")
    con.close()

    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    assert main(["--db", f"sqlite:///{db_path}", "migrate", "--dry-run"]) == 0
    capsys.readouterr()

    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    con = sqlite3.connect(db_path)
    mode_after = con.execute("PRAGMA journal_mode").fetchone()[0]
    con.close()

    assert after == before, "dry run wrote to the database file"
    assert mode_after == "delete"


def test_migrate_dry_run_reports_migration_4_backfill_counts(tmp_path, capsys):
    """`hescope migrate --dry-run` must print migration 4's detail, not just
    its title.

    plan_migration_4 existed and was correct from the day it was written, and
    NOTHING CALLED IT. The dry run printed

        would apply migration 4: L5 measurement layers ...

    and no detail line, so the duplicate (slide_id, geom_key) groups --
    the one thing docs/DATABASE-DESIGN.md §5 step 5 says must be REPORTED
    rather than silently dropped -- were invisible in the only place a user
    would look before touching a real database.

    Observed before the fix, against a copy of the real database: migration 2
    and 3 each printed a detail line, migration 4 printed none, while calling
    plan_migration_4(conn) directly on the same file returned
    {'rois': 10, 'created_by_backfilled': 10, 'duplicate_geom_key_groups': 2,
     'duplicate_geom_key_rois': 5}.

    Third instance of this defect class on this branch (ba84d17 "make migrate
    --dry-run tell the truth", 4b93f58 "preview vs actual identity drift").
    """
    from hescope.core.rois import ROI
    from hescope.store.db import ROIRepo, SlideRepo, get_engine, init_db

    url = f"sqlite:///{(tmp_path / 'm4.db').as_posix()}"
    engine = get_engine(url)
    init_db(engine)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="s.svs", path=str(tmp_path / "s.svs"),
        width=1000, height=800, mpp=0.5,
    )
    repo = ROIRepo(engine)
    # two ROIs with IDENTICAL geometry -> one duplicate group, plus a third
    same = ROI(kind="rect", points=((0.0, 0.0), (10.0, 10.0)))
    repo.add(slide_id, same)
    repo.add(slide_id, same)
    repo.add(slide_id, ROI(kind="rect", points=((50.0, 50.0), (70.0, 70.0))))

    main(["--db", url, "migrate", "--dry-run"])
    out = capsys.readouterr().out

    assert "would apply migration 4" in out
    assert "would backfill geom_key on 3 roi(s)" in out, (
        f"migration 4 printed a title and no detail line:\n{out}"
    )
    assert "1 duplicate (slide_id, geom_key) group(s) covering 2 roi(s)" in out, (
        "the duplicate groups DATABASE-DESIGN.md §5 says must be reported are "
        f"invisible in the dry run:\n{out}"
    )
    assert "not merged" in out, "the user must be told they are kept, not dropped"


def test_migrate_dry_run_says_nothing_about_duplicates_when_there_are_none(
    tmp_path, capsys
):
    """Guard against the fix overreaching: no duplicate line on a clean slide."""
    from hescope.core.rois import ROI
    from hescope.store.db import ROIRepo, SlideRepo, get_engine, init_db

    url = f"sqlite:///{(tmp_path / 'clean.db').as_posix()}"
    engine = get_engine(url)
    init_db(engine)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="s.svs", path=str(tmp_path / "s.svs"),
        width=1000, height=800, mpp=0.5,
    )
    ROIRepo(engine).add(slide_id, ROI(kind="rect", points=((0.0, 0.0), (10.0, 10.0))))

    main(["--db", url, "migrate", "--dry-run"])
    out = capsys.readouterr().out
    assert "would backfill geom_key on 1 roi(s)" in out
    assert "duplicate (slide_id, geom_key)" not in out
