"""Tests for hescope.migrations (Phase 0: the migration framework).

Offline; tmp sqlite files. Never touches data/hescope.db (R-1).
"""

from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy as sa

from hescope.db import get_engine, init_db
from hescope.migrations import (
    MIGRATIONS,
    SCHEMA_VERSION,
    Migration,
    MigrationReport,
    _validate_migrations,
    current_version,
    migrate,
    pending,
)


@pytest.fixture()
def engine(tmp_path):
    """A freshly created (empty) database, tables already in place."""
    eng = get_engine(f"sqlite:///{tmp_path}/mig.db")
    init_db(eng)
    return eng


# --- 1. a fresh database ends at SCHEMA_VERSION with every migration recorded


def test_fresh_database_migrates_to_schema_version_with_every_migration_recorded(
    engine,
):
    report = migrate(engine)

    assert current_version(engine) == SCHEMA_VERSION
    assert report.from_version == 0
    assert report.to_version == SCHEMA_VERSION
    assert report.error is None
    assert len(report.applied) == len(MIGRATIONS)

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT version, name FROM schema_migrations ORDER BY version")
        ).all()
    assert [r[0] for r in rows] == [m.version for m in MIGRATIONS]
    assert [r[1] for r in rows] == [m.name for m in MIGRATIONS]
    with engine.connect() as conn:
        applied_ats = conn.execute(
            sa.text("SELECT applied_at FROM schema_migrations")
        ).scalars().all()
    assert all(a for a in applied_ats), "applied_at must be recorded, not blank"


# --- 2. migrate() is idempotent -- a second call applies nothing


def test_migrate_is_idempotent_second_call_applies_nothing(engine):
    first = migrate(engine)
    assert first.applied  # sanity: the first call actually did something

    second = migrate(engine)

    assert second.applied == []
    assert second.skipped == []
    assert second.error is None
    assert second.from_version == SCHEMA_VERSION
    assert second.to_version == SCHEMA_VERSION
    assert current_version(engine) == SCHEMA_VERSION
    with engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM schema_migrations")
        ).scalar()
    assert count == len(MIGRATIONS), "a second run must not insert duplicate rows"


# --- 3. dry_run=True changes neither schema_migrations nor any table


def test_dry_run_touches_nothing(engine):
    before_tables = set(sa.inspect(engine).get_table_names())

    report = migrate(engine, dry_run=True)

    assert report.from_version == 0
    assert report.to_version == SCHEMA_VERSION
    assert report.applied == []
    assert report.skipped == [f"{m.version}: {m.name}" for m in MIGRATIONS]
    assert report.error is None
    # the whole point: nothing was written
    assert "schema_migrations" not in sa.inspect(engine).get_table_names()
    assert set(sa.inspect(engine).get_table_names()) == before_tables
    assert current_version(engine) == 0

    # and running for real afterward still works exactly as if the dry run
    # never happened
    real = migrate(engine)
    assert real.from_version == 0
    assert real.to_version == SCHEMA_VERSION


def test_dry_run_through_a_read_only_engine_does_not_mutate_a_non_wal_file(tmp_path):
    """A dry run must be safe to point at a database this process must never
    write to (R-1) -- including the FILE ITSELF, not just its rows.

    A plain ``get_engine(url)`` flips ``journal_mode`` to WAL (a persistent
    header change) on the very first connection, dry run or not -- so
    ``migrate(engine, dry_run=True)`` through a plain engine silently writes
    to a non-WAL file. Measured: sha256 changed and ``journal_mode`` read
    back as ``'wal'`` after a dry run that reported "nothing was changed".
    ``get_engine(url, read_only=True)`` (used here) skips exactly that one
    pragma.
    """
    import hashlib

    db_path = tmp_path / "ro.db"
    setup_engine = get_engine(f"sqlite:///{db_path}")
    init_db(setup_engine)
    setup_engine.dispose()  # release the pooled connection before flipping the journal mode below
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=DELETE")
    con.close()

    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    ro_engine = get_engine(f"sqlite:///{db_path}", read_only=True)
    report = migrate(ro_engine, dry_run=True)

    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    con = sqlite3.connect(db_path)
    mode_after = con.execute("PRAGMA journal_mode").fetchone()[0]
    con.close()

    assert report.skipped, "sanity: there must have been something pending to report"
    assert after == before, "the read-only dry run wrote to the database file"
    assert mode_after == "delete"


def test_dry_run_on_an_up_to_date_database_reports_nothing_pending(engine):
    migrate(engine)  # bring it to SCHEMA_VERSION for real

    report = migrate(engine, dry_run=True)

    assert report.from_version == SCHEMA_VERSION
    assert report.to_version == SCHEMA_VERSION
    assert report.applied == []
    assert report.skipped == []


# --- 4. a migration that raises leaves the version at the previous value,
#        with no partial rows


def test_a_raising_migration_leaves_version_at_last_success_and_writes_no_partial_row(
    engine, monkeypatch
):
    """Migration 2 (injected) raises. Migration 1 must stay committed;
    migration 2 must leave no schema_migrations row and no side effect from
    its partial apply."""
    import hescope.migrations as mig

    calls: list[str] = []

    def _boom(conn: sa.Connection) -> None:
        calls.append("ran")
        conn.execute(sa.text("CREATE TABLE would_be_orphaned (id INTEGER PRIMARY KEY)"))
        raise RuntimeError("synthetic failure in migration 2")

    injected = (
        MIGRATIONS[0],
        Migration(version=2, name="synthetic failing migration", apply=_boom),
    )
    monkeypatch.setattr(mig, "MIGRATIONS", injected)

    report = mig.migrate(engine)

    assert calls == ["ran"], "the failing migration must actually have run"
    assert report.from_version == 0
    assert report.to_version == 1, "version 1 (the only success) must stick"
    assert report.applied == [f"{injected[0].version}: {injected[0].name}"]
    assert report.skipped == ["2: synthetic failing migration"]
    assert report.error is not None and "synthetic failure" in report.error
    assert mig.current_version(engine) == 1

    with engine.connect() as conn:
        versions = conn.execute(
            sa.text("SELECT version FROM schema_migrations")
        ).scalars().all()
    assert versions == [1], "no partial row for the failed migration"
    # the DDL the failing migration ran before raising must have been rolled
    # back along with it -- this is what "own transaction" means
    assert "would_be_orphaned" not in sa.inspect(engine).get_table_names()


# --- 5. the index gap -- must be seen to fail against the un-fixed init_db


def test_upgraded_database_has_the_same_rois_indexes_as_a_fresh_one(tmp_path):
    """A database built through the OLD narrow schema (before ``slide_id``
    and ``label`` were declared ``index=True`` on the ORM model), then
    upgraded through ``init_db``'s additive ``ALTER TABLE`` path, must end up
    with the same indexes on ``rois`` as a database ``init_db`` creates from
    scratch. Both report ``schema version 0`` (no ``schema_migrations`` row
    yet), but before the ``init_db`` fix the upgraded one silently had 0
    indexes where the fresh one had 2 (``ix_rois_slide_id``, ``ix_rois_label``)
    -- observed by running this exact comparison against the pre-fix
    ``init_db``:

        upgraded rois indexes: set()
        fresh rois indexes:    {'ix_rois_label', 'ix_rois_slide_id'}
        AssertionError: assert set() == {'ix_rois_label', 'ix_rois_slide_id'}

    ``ROIRepo.for_slide`` and ``ROIRepo.search`` filter on exactly those two
    columns, so the missing indexes are a real, silent table-scan regression,
    not a cosmetic gap.
    """
    old_db = tmp_path / "old_narrow.db"
    con = sqlite3.connect(old_db)
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
            patch_path VARCHAR(1024), stats_json TEXT, magnification FLOAT,
            created_at DATETIME
        );
        """  # the narrow schema that predates index=True on slide_id/label
    )
    con.commit()
    con.close()

    upgraded = get_engine(f"sqlite:///{old_db}")
    init_db(upgraded)  # the additive upgrade path under test
    upgraded_indexes = {ix["name"] for ix in sa.inspect(upgraded).get_indexes("rois")}
    upgraded_version = current_version(upgraded)
    upgraded.dispose()

    fresh = get_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    init_db(fresh)
    fresh_indexes = {ix["name"] for ix in sa.inspect(fresh).get_indexes("rois")}
    fresh_version = current_version(fresh)
    fresh.dispose()

    assert upgraded_version == fresh_version == 0, (
        "both report the same (absent) schema_migrations version -- the "
        "index gap is invisible from the version alone, which is why it "
        "needs this direct comparison"
    )
    assert upgraded_indexes == fresh_indexes == {"ix_rois_slide_id", "ix_rois_label"}


# --- 6. an out-of-order or gapped MIGRATIONS tuple is rejected at import time


def test_the_real_migrations_tuple_passes_validation():
    """Production ``MIGRATIONS`` is validated by ``_validate_migrations`` at
    module import time (see the call right after its definition in
    hescope/migrations.py) -- the fact that ``import hescope.migrations``
    above succeeded already exercises this. Re-assert it explicitly so a
    future edit that removes the tuple from the source can't make this test
    pass for the wrong reason."""
    _validate_migrations(MIGRATIONS)  # must not raise


def test_a_gapped_migrations_tuple_is_rejected():
    with pytest.raises(ValueError, match="ordered and gapless"):
        _validate_migrations(
            (Migration(version=1, name="a", apply=lambda c: None),
             Migration(version=3, name="c", apply=lambda c: None))
        )


def test_an_out_of_order_migrations_tuple_is_rejected():
    with pytest.raises(ValueError, match="ordered and gapless"):
        _validate_migrations(
            (Migration(version=2, name="b", apply=lambda c: None),
             Migration(version=1, name="a", apply=lambda c: None))
        )


def test_a_migrations_tuple_not_starting_at_1_is_rejected():
    with pytest.raises(ValueError, match="ordered and gapless"):
        _validate_migrations((Migration(version=2, name="b", apply=lambda c: None),))


def test_an_empty_migrations_tuple_is_valid():
    _validate_migrations(())  # vacuously ordered and gapless


# --- current_version / pending on a database migrate() has never touched --


def test_current_version_is_zero_before_schema_migrations_exists(engine):
    assert "schema_migrations" not in sa.inspect(engine).get_table_names()
    assert current_version(engine) == 0


def test_pending_lists_everything_before_the_first_migrate_call(engine):
    assert [m.version for m in pending(engine)] == [m.version for m in MIGRATIONS]


def test_pending_is_empty_after_migrate(engine):
    migrate(engine)
    assert pending(engine) == []


# --- MigrationReport is a report, not a bool -------------------------------


def test_migration_report_shape():
    report = MigrationReport(
        from_version=0, to_version=1, applied=["1: x"], skipped=[], error=None
    )
    assert report.from_version == 0
    assert report.to_version == 1
    assert report.applied == ["1: x"]
    assert report.skipped == []
    assert report.error is None
