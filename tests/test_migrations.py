"""Tests for hescope.store.migrations (Phase 0: the migration framework).

Offline; tmp sqlite files. Never touches data/hescope.db (R-1).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
import sqlalchemy as sa

from hescope.store.db import (
    ROI,
    LayerRepo,
    MeasurementRepo,
    ROIRepo,
    SelectionResolutionRepo,
    Slide,
    SlideRepo,
    geom_key,
    get_engine,
    init_db,
)
from hescope.core.identity import content_key
from hescope.core.rois import ROI as ROIGeometry
from hescope.store.migrations import (
    MIGRATIONS,
    SCHEMA_VERSION,
    Migration,
    MigrationReport,
    _validate_migrations,
    current_version,
    migrate,
    plan_migration_2,
    plan_migration_4,
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
    import hescope.store.migrations as mig

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
    assert upgraded_indexes == fresh_indexes == {
        "ix_rois_slide_id", "ix_rois_label", "ix_rois_slide_bbox",
    }


# --- 6. an out-of-order or gapped MIGRATIONS tuple is rejected at import time


def test_the_real_migrations_tuple_passes_validation():
    """Production ``MIGRATIONS`` is validated by ``_validate_migrations`` at
    module import time (see the call right after its definition in
    hescope/migrations.py) -- the fact that ``import hescope.store.migrations``
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


# =============================================================================
# Migration 2: the SVS <-> ROI relationship (BUILD-PLAN-DB.md Phase 1)
# =============================================================================
#
# These insert rows the way a database written BEFORE migration 2 would have
# them -- bypassing SlideRepo.register/ROIRepo.add, which already write
# identity/bbox columns -- so identity_scheme, identity_key and bbox_x0..y1
# are NULL going in, matching the shipped data/hescope.db's actual shape.


def _legacy_slide(engine, *, path, created_at, source_kind="local", name="s") -> int:
    with sa.orm.Session(engine) as s:
        slide = Slide(
            source_kind=source_kind, name=name, path=path,
            width=10, height=10, extra_json="{}", created_at=created_at,
        )
        s.add(slide)
        s.commit()
        return slide.id


def _legacy_roi(engine, slide_id: int, bbox: tuple[int, int, int, int]) -> int:
    import json

    with sa.orm.Session(engine) as s:
        roi = ROI(
            slide_id=slide_id, kind="rect",
            points_json=json.dumps([[bbox[0], bbox[1]], [bbox[2], bbox[3]]]),
            bbox_json=json.dumps(list(bbox)),
        )
        s.add(roi)
        s.commit()
        return roi.id


def test_migration_2_backfills_slide_files_preserving_first_seen_at(tmp_path):
    """R-3: the whole point. A slide row's created_at must appear UNCHANGED
    in its slide_files.first_seen_at -- compared here as the raw SQL value
    on both sides (bypassing any ORM/format re-derivation), the same
    SOURCE-vs-DESTINATION comparison the plan calls out as the exact check
    five count-asserting tests missed for migrate-tcga-catalog."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig2.db")
    init_db(engine)
    real_file = tmp_path / "s.svs"
    real_file.write_bytes(b"content")
    created = datetime(2024, 1, 1, 12, 30, 0)
    slide_id = _legacy_slide(engine, path=str(real_file.resolve()), created_at=created)

    report = migrate(engine)
    assert report.error is None
    assert current_version(engine) == SCHEMA_VERSION

    with engine.connect() as conn:
        source_created_at = conn.execute(
            sa.text("SELECT created_at FROM slides WHERE id=:id"), {"id": slide_id}
        ).scalar_one()
        dest = conn.execute(
            sa.text(
                "SELECT first_seen_at, last_seen_at, missing_since, path, source_kind "
                "FROM slide_files WHERE slide_id=:id"
            ),
            {"id": slide_id},
        ).one()
    assert dest.first_seen_at == source_created_at, (
        "slide_files.first_seen_at must equal the SOURCE slides.created_at "
        f"value exactly; got {dest.first_seen_at!r} vs {source_created_at!r}"
    )
    assert dest.last_seen_at == source_created_at
    assert dest.missing_since is None
    assert dest.path == str(real_file.resolve())
    assert dest.source_kind == "local"


def test_migration_2_marks_a_non_resolving_path_missing_and_leaves_identity_null(
    tmp_path,
):
    engine = get_engine(f"sqlite:///{tmp_path}/mig2.db")
    init_db(engine)
    missing_path = str(tmp_path / "does-not-exist.svs")
    slide_id = _legacy_slide(engine, path=missing_path, created_at=datetime(2024, 1, 1))

    migrate(engine)

    with engine.connect() as conn:
        slide_row = conn.execute(
            sa.text("SELECT identity_scheme, identity_key, file_size FROM slides WHERE id=:id"),
            {"id": slide_id},
        ).one()
        file_row = conn.execute(
            sa.text("SELECT missing_since FROM slide_files WHERE slide_id=:id"),
            {"id": slide_id},
        ).one()
    assert slide_row.identity_scheme is None
    assert slide_row.identity_key is None
    assert slide_row.file_size is None
    assert file_row.missing_since is not None


def test_migration_2_computes_a_real_identity_for_a_resolving_path(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/mig2.db")
    init_db(engine)
    real_file = tmp_path / "s.svs"
    real_file.write_bytes(b"hello identity")
    slide_id = _legacy_slide(
        engine, path=str(real_file.resolve()), created_at=datetime(2024, 1, 1)
    )
    expected_key, expected_size = content_key(real_file)

    migrate(engine)

    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT identity_scheme, identity_key, file_size FROM slides WHERE id=:id"),
            {"id": slide_id},
        ).one()
    assert row.identity_scheme == "sha256"
    assert row.identity_key == expected_key
    assert row.file_size == expected_size


def test_migration_2_backfills_roi_bbox_columns_from_bbox_json(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/mig2.db")
    init_db(engine)
    slide_id = _legacy_slide(
        engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1)
    )
    roi_id = _legacy_roi(engine, slide_id, (10, 20, 110, 220))

    migrate(engine)

    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT bbox_x0, bbox_y0, bbox_x1, bbox_y1 FROM rois WHERE id=:id"
            ),
            {"id": roi_id},
        ).one()
    assert (row.bbox_x0, row.bbox_y0, row.bbox_x1, row.bbox_y1) == (10.0, 20.0, 110.0, 220.0)


def test_migration_2_does_not_merge_duplicate_content_or_crash(tmp_path):
    """Two legacy rows whose files happen to have IDENTICAL content: this
    migration must not crash (the partial unique index would reject a
    second identical (scheme, key) UPDATE) and must not pick a winner --
    merging duplicate slides is explicitly out of scope for this migration
    (BUILD-PLAN-DB.md's non-goals); detecting them is as far as it goes, so
    both rows are left with identity NULL rather than one arbitrarily
    claiming the identity and the other silently staying unidentified.

    This guard was verified necessary by removing it and re-running this
    exact test against the un-guarded migration: it raised
    ``sqlite3.IntegrityError: UNIQUE constraint failed: slides.identity_scheme,
    slides.identity_key`` and the whole migration transaction rolled back
    (R-2).
    """
    engine = get_engine(f"sqlite:///{tmp_path}/mig2.db")
    init_db(engine)
    payload = b"byte-identical slide content"
    p1 = tmp_path / "a.svs"
    p1.write_bytes(payload)
    p2 = tmp_path / "b.svs"
    p2.write_bytes(payload)
    sid1 = _legacy_slide(engine, path=str(p1.resolve()), created_at=datetime(2024, 1, 1))
    sid2 = _legacy_slide(engine, path=str(p2.resolve()), created_at=datetime(2024, 1, 1))

    report = migrate(engine)

    assert report.error is None, f"migration must not crash on duplicate content: {report.error}"
    with engine.connect() as conn:
        rows = {
            r.id: (r.identity_scheme, r.identity_key)
            for r in conn.execute(
                sa.text("SELECT id, identity_scheme, identity_key FROM slides")
            ).all()
        }
    assert rows[sid1] == (None, None)
    assert rows[sid2] == (None, None)
    # additive, not destructive: both locations are still recorded
    assert len(SlideRepo(engine).files_for(sid1)) == 1
    assert len(SlideRepo(engine).files_for(sid2)) == 1


def test_plan_migration_2_matches_what_migration_2_actually_writes(tmp_path):
    """The dry-run preview and the real migration share one computation
    (see hescope.store.migrations._compute_slide_backfills /
    _compute_roi_bbox_backfills / _conflicting_identities) so they cannot
    silently drift -- assert that invariant directly rather than trusting
    the shared-code comment.

    Includes a duplicate-content pair (sid3/sid4: byte-identical files) on
    top of the original resolving+missing pair, so this test cannot pass by
    construction the way a duplicate-free seed would (see BUILD-PLAN-DB.md
    Phase 1 debugger round 2, finding 1). Before the fix,
    ``plan_migration_2``'s ``distinct_identities`` was
    ``len({b.identity_key for b in backfills if not b.missing})`` -- every
    RESOLVING identity, duplicates included, with no knowledge of
    ``_migration_2_apply``'s ``conflicting_identities`` guard that leaves
    every row in a duplicate-content group identity-NULL. Measured against
    that pre-fix code with exactly this seed:

        preview["distinct_identities"] == 2  (1 from sid1, 1 from the dup pair)
        actual identities written      == 1  (only sid1; the dup pair wrote 0)
        AssertionError: assert 2 == 1
    """
    engine = get_engine(f"sqlite:///{tmp_path}/mig2.db")
    init_db(engine)
    real_file = tmp_path / "s.svs"
    real_file.write_bytes(b"resolves")
    missing_path = str(tmp_path / "gone.svs")
    sid1 = _legacy_slide(engine, path=str(real_file.resolve()), created_at=datetime(2024, 1, 1))
    sid2 = _legacy_slide(engine, path=missing_path, created_at=datetime(2024, 1, 1), name="gone")
    _legacy_roi(engine, sid1, (0, 0, 10, 10))
    _legacy_roi(engine, sid2, (5, 5, 15, 15))

    dup_payload = b"byte-identical duplicate-content pair for finding 1"
    dup_a = tmp_path / "dup_a.svs"
    dup_b = tmp_path / "dup_b.svs"
    dup_a.write_bytes(dup_payload)
    dup_b.write_bytes(dup_payload)
    sid3 = _legacy_slide(
        engine, path=str(dup_a.resolve()), created_at=datetime(2024, 1, 1), name="dup_a"
    )
    sid4 = _legacy_slide(
        engine, path=str(dup_b.resolve()), created_at=datetime(2024, 1, 1), name="dup_b"
    )

    with engine.connect() as conn:
        preview = plan_migration_2(conn)

    migrate(engine)

    with engine.connect() as conn:
        actual_files = conn.execute(sa.text("SELECT COUNT(*) FROM slide_files")).scalar_one()
        actual_missing = conn.execute(
            sa.text("SELECT COUNT(*) FROM slide_files WHERE missing_since IS NOT NULL")
        ).scalar_one()
        actual_rois = conn.execute(
            sa.text("SELECT COUNT(*) FROM rois WHERE bbox_x0 IS NOT NULL")
        ).scalar_one()
        actual_identities = conn.execute(
            sa.text(
                "SELECT COUNT(DISTINCT identity_key) FROM slides WHERE identity_scheme IS NOT NULL"
            )
        ).scalar_one()
        dup_rows = conn.execute(
            sa.text(
                "SELECT identity_scheme, identity_key FROM slides WHERE id IN (:a, :b)"
            ),
            {"a": sid3, "b": sid4},
        ).all()

    assert preview["slide_files"] == actual_files == 4
    assert preview["missing"] == actual_missing == 1
    assert preview["rois_backfilled"] == actual_rois == 2
    # the whole point: the preview's writable-identity count must equal what
    # migration 2 actually wrote, EVEN WITH a duplicate-content pair present
    # (sid1 gets a real identity; sid2 is missing; sid3/sid4 collide and
    # both stay NULL, so only 1 identity is written in total)
    assert preview["distinct_identities"] == actual_identities == 1
    assert preview["duplicate_content_rows"] == 2, (
        "the 2 duplicate-content rows must be reported separately, not "
        "folded silently into (or dropped from) distinct_identities"
    )
    assert all(scheme is None and key is None for scheme, key in dup_rows), (
        "duplicate-content rows must stay identity-NULL, not silently claim one"
    )


def test_plan_migration_2_on_an_empty_database_is_all_zero(tmp_path):
    """The dry-run preview must not crash when nothing has ever created the
    `slides`/`rois` tables it wants to read from (a brand-new database)."""
    engine = get_engine(f"sqlite:///{tmp_path}/empty.db")
    with engine.connect() as conn:
        preview = plan_migration_2(conn)
    assert preview == {
        "slide_files": 0, "missing": 0, "distinct_identities": 0,
        "duplicate_content_rows": 0, "rois_backfilled": 0,
    }


def test_migration_2_is_a_noop_on_an_already_up_to_date_empty_database(tmp_path):
    """A fresh database (created via init_db, which already writes the
    current schema) must migrate cleanly through version 2 with zero
    backfill work to do -- migration 2 must not assume there is always at
    least one legacy row to process."""
    engine = get_engine(f"sqlite:///{tmp_path}/fresh.db")
    init_db(engine)

    report = migrate(engine)

    assert report.error is None
    assert current_version(engine) == SCHEMA_VERSION
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM slide_files")).scalar_one() == 0


# =============================================================================
# migrate() must be self-contained (BUILD-PLAN-DB.md Phase 1 debugger round 2,
# finding 2): the CLI happens to call init_db(engine) before migrate(engine),
# which is the only reason `hescope migrate` works. Calling migrate() by
# itself, the way a library caller or a script (not hescope.cli) would, must
# not depend on that ordering.
# =============================================================================


def test_migrate_reaches_schema_version_on_a_legacy_database_with_no_init_db_call(
    tmp_path,
):
    """``migrate(engine)`` alone -- NOT ``init_db(engine)`` then
    ``migrate(engine)`` -- on a hand-built legacy-schema database (``slides``
    and ``rois`` only; no ``slide_files`` table, no identity/bbox columns,
    exactly the narrow schema migration 2's own DDL is declared against on
    the ORM models, not in this module -- see ``_migration_2_apply``'s
    docstring) must still reach ``SCHEMA_VERSION``.

    Before the fix this raised and left the version stuck at 1. Measured
    against the pre-fix ``migrate()`` with this exact seed (one legacy slide
    row, so migration 2's backfill loop has something to write and isn't
    silently a no-op the way an all-empty database is):

        sqlite3.OperationalError: no such table: slide_files
        [SQL: INSERT INTO slide_files (slide_id, path, source_kind,
        first_seen_at, last_seen_at, missing_since) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO NOTHING]
        report.to_version == 1
        report.error is not None
    """
    import sqlite3

    db_path = tmp_path / "legacy_no_init.db"
    con = sqlite3.connect(db_path)
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
        INSERT INTO slides (source_kind, name, path, width, height, extra_json, created_at)
        VALUES ('local', 's1', 'C:/nope/s1.svs', 10, 10, '{}', '2024-01-01 00:00:00');
        """
    )
    con.commit()
    con.close()

    engine = get_engine(f"sqlite:///{db_path}")

    report = migrate(engine)  # NOTE: no init_db(engine) call before this

    assert report.error is None, f"migrate() must be self-contained: {report.error}"
    assert report.to_version == SCHEMA_VERSION
    assert current_version(engine) == SCHEMA_VERSION
    with engine.connect() as conn:
        assert "slide_files" in sa.inspect(conn).get_table_names()
        assert conn.execute(sa.text("SELECT COUNT(*) FROM slide_files")).scalar_one() == 1


def test_migrate_dry_run_still_touches_nothing_and_does_not_call_init_db(tmp_path):
    """The self-containment fix must not leak into the dry-run path: a dry
    run still must not write anything, including the tables ``init_db``
    would create (R-1's whole premise -- a dry run against a copy of the
    live database must be safe even if that copy has an old schema)."""
    import sqlite3

    db_path = tmp_path / "legacy_dry.db"
    con = sqlite3.connect(db_path)
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
        """
    )
    con.commit()
    con.close()
    before_tables = {"slides", "rois"}

    engine = get_engine(f"sqlite:///{db_path}", read_only=True)
    report = migrate(engine, dry_run=True)

    assert report.error is None
    assert set(sa.inspect(engine).get_table_names()) == before_tables, (
        "dry_run must not call init_db -- no new tables from it either"
    )
    assert current_version(engine) == 0


# =============================================================================
# Migration 3: TCGA download -> storage -> injection (BUILD-PLAN-DB.md Phase 2)
# =============================================================================


def test_schema_version_is_4_for_l5_measurement_layers():
    assert SCHEMA_VERSION == 4
    assert [m.version for m in MIGRATIONS] == [1, 2, 3, 4]


def test_migration_3_gives_tcga_files_slide_id_a_real_foreign_key(tmp_path):
    """Defect 2.1: tcga_files.slide_id was indexed but UNCONSTRAINED --
    inserting a slide_id that names no row in `slides` was accepted
    silently. Must be seen to fail today (before migration 3 exists): this
    exact insert raises nothing and the bogus row is readable back."""
    from hescope.gdc.tcga_schema import TcgaCatalog

    engine = get_engine(f"sqlite:///{tmp_path}/mig3_fk.db")
    init_db(engine)
    TcgaCatalog(engine)  # creates tcga_files (and siblings)

    report = migrate(engine)
    assert report.error is None
    assert current_version(engine) == SCHEMA_VERSION

    with engine.connect() as conn:
        with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
            conn.execute(
                sa.text(
                    "INSERT INTO tcga_files (file_id, first_seen_at, slide_id) "
                    "VALUES ('ghost-file', :now, 999999)"
                ),
                {"now": datetime(2024, 1, 1)},
            )


def test_migration_3_preserves_existing_tcga_files_data_across_the_fk_rebuild(tmp_path):
    """Adding the FK requires SQLite's copy/drop/rename table-rebuild (no
    ALTER TABLE ADD CONSTRAINT exists). Every column of every pre-existing
    row must survive it byte-for-byte -- source vs destination (R-3), not a
    count."""
    from hescope.gdc.tcga_schema import TcgaCatalog

    engine = get_engine(f"sqlite:///{tmp_path}/mig3_preserve.db")
    init_db(engine)
    cat = TcgaCatalog(engine)
    cat.upsert_rows(
        [
            {
                "file_id": "keep-1", "file_name": "keep.svs", "file_size": 42,
                "md5sum": "f" * 32, "project_id": "TCGA-BRCA",
                "case_submitter_id": "TCGA-AA-0001", "sample_id": "TCGA-AA-0001-01A",
                "first_seen_at": datetime(2021, 5, 6, 7, 8, 9),
            }
        ]
    )
    with engine.connect() as conn:
        before = dict(
            conn.execute(
                sa.text("SELECT * FROM tcga_files WHERE file_id='keep-1'")
            ).mappings().one()
        )

    migrate(engine)

    with engine.connect() as conn:
        after = dict(
            conn.execute(
                sa.text("SELECT * FROM tcga_files WHERE file_id='keep-1'")
            ).mappings().one()
        )
    assert after == before, f"the FK rebuild altered existing data:\n{before}\nvs\n{after}"


def test_migration_3_links_a_downloaded_file_to_its_already_registered_slide(tmp_path):
    """R-3: compares a SOURCE value (the slide.id a tcga_files row's
    local_path actually content-matches) to a DESTINATION value
    (tcga_files.slide_id after migration), not a count. Mirrors the real
    database's actual state: exactly one TCGA file is downloaded, and it was
    already registered as a slide (by the normal open path) before
    migration 3 ever runs."""
    from hescope.gdc.tcga_schema import TcgaCatalog

    engine = get_engine(f"sqlite:///{tmp_path}/mig3_link.db")
    init_db(engine)
    real_file = tmp_path / "already-registered.svs"
    real_file.write_bytes(b"the actual bytes of a downloaded tcga slide")
    slide_id = SlideRepo(engine).register(
        source_kind="tcga", name="already-registered.svs",
        path=str(real_file.resolve()), width=10, height=10,
    )
    cat = TcgaCatalog(engine)
    cat.upsert_rows([{"file_id": "fid-linked", "file_name": "already-registered.svs", "file_size": 44}])
    assert cat.mark_downloaded("fid-linked", str(real_file.resolve())) is True

    report = migrate(engine)
    assert report.error is None

    with engine.connect() as conn:
        linked = conn.execute(
            sa.text("SELECT slide_id FROM tcga_files WHERE file_id='fid-linked'")
        ).scalar_one()
    assert linked == slide_id, (
        f"tcga_files.slide_id must equal the SOURCE slide's real id "
        f"({slide_id!r}); got {linked!r}"
    )


def test_migration_3_does_not_link_a_file_with_no_matching_slide(tmp_path):
    """A downloaded file that was never opened/registered has no slide row
    to point at. Migration 3 must not invent one (that is exactly defect
    2.4's shape) -- it leaves slide_id NULL and counts the row as
    unlinkable."""
    from hescope.store.migrations import plan_migration_3
    from hescope.gdc.tcga_schema import TcgaCatalog

    engine = get_engine(f"sqlite:///{tmp_path}/mig3_nomatch.db")
    init_db(engine)
    orphan_file = tmp_path / "never-opened.svs"
    orphan_file.write_bytes(b"downloaded but never registered as a slide")
    cat = TcgaCatalog(engine)
    cat.upsert_rows([{"file_id": "fid-orphan", "file_name": "never-opened.svs", "file_size": 1}])
    cat.mark_downloaded("fid-orphan", str(orphan_file.resolve()))

    with engine.connect() as conn:
        preview = plan_migration_3(conn)
    assert preview["would_link"] == 0
    assert preview["could_not_link"] == 1

    migrate(engine)

    with engine.connect() as conn:
        linked = conn.execute(
            sa.text("SELECT slide_id FROM tcga_files WHERE file_id='fid-orphan'")
        ).scalar_one()
        slides_count = conn.execute(sa.text("SELECT COUNT(*) FROM slides")).scalar_one()
    assert linked is None
    assert slides_count == 0, "migration 3 must never invent a slide row"


def test_migration_3_does_not_relink_an_already_linked_file(tmp_path):
    """A tcga_files row that already carries a slide_id (written at download
    time by app.py, per BUILD-PLAN-DB.md Phase 2) must be left exactly as
    is, not re-derived from a possibly-stale local_path."""
    from hescope.gdc.tcga_schema import TcgaCatalog

    engine = get_engine(f"sqlite:///{tmp_path}/mig3_already.db")
    init_db(engine)
    real_file = tmp_path / "s.svs"
    real_file.write_bytes(b"content")
    slide_id = SlideRepo(engine).register(
        source_kind="tcga", name="s.svs", path=str(real_file.resolve()), width=1, height=1,
    )
    cat = TcgaCatalog(engine)
    cat.upsert_rows([{"file_id": "fid-already", "file_name": "s.svs", "file_size": 7}])
    cat.mark_downloaded("fid-already", str(real_file.resolve()), slide_id=slide_id)

    migrate(engine)

    with engine.connect() as conn:
        linked = conn.execute(
            sa.text("SELECT slide_id FROM tcga_files WHERE file_id='fid-already'")
        ).scalar_one()
    assert linked == slide_id


def test_migration_3_is_idempotent(tmp_path):
    from hescope.gdc.tcga_schema import TcgaCatalog

    engine = get_engine(f"sqlite:///{tmp_path}/mig3_idem.db")
    init_db(engine)
    real_file = tmp_path / "s.svs"
    real_file.write_bytes(b"idempotent content")
    slide_id = SlideRepo(engine).register(
        source_kind="tcga", name="s.svs", path=str(real_file.resolve()), width=1, height=1,
    )
    cat = TcgaCatalog(engine)
    cat.upsert_rows([{"file_id": "fid-idem", "file_name": "s.svs", "file_size": 7}])
    cat.mark_downloaded("fid-idem", str(real_file.resolve()))

    first = migrate(engine)
    second = migrate(engine)

    assert first.error is None and second.error is None
    assert second.applied == []
    with engine.connect() as conn:
        linked = conn.execute(
            sa.text("SELECT slide_id FROM tcga_files WHERE file_id='fid-idem'")
        ).scalar_one()
        fk_rows = conn.execute(sa.text('PRAGMA foreign_key_list("tcga_files")')).all()
    assert linked == slide_id
    assert any(r[2] == "slides" for r in fk_rows), "the FK must still be present after a second run"


def test_plan_migration_3_on_an_empty_database_is_all_zero(tmp_path):
    from hescope.store.migrations import plan_migration_3

    engine = get_engine(f"sqlite:///{tmp_path}/empty3.db")
    with engine.connect() as conn:
        preview = plan_migration_3(conn)
    assert preview == {
        "tcga_files_exists": False, "fk_present": False,
        "with_local_path": 0, "already_linked": 0,
        "dangling_slide_ids": 0,
        "would_link": 0, "could_not_link": 0,
    }


def test_migrate_reaches_schema_version_with_no_prior_tcga_tables(tmp_path):
    """migrate() must be self-contained for migration 3 the same way it
    already is for migration 2 (BUILD-PLAN-DB.md Phase 1 debugger round 2):
    a database that never had a TcgaCatalog instantiated (no tcga_* tables
    at all) must still reach SCHEMA_VERSION in one migrate() call."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig3_selfcontained.db")

    report = migrate(engine)

    assert report.error is None, f"migrate() must be self-contained: {report.error}"
    assert current_version(engine) == SCHEMA_VERSION
    with engine.connect() as conn:
        assert "tcga_files" in sa.inspect(conn).get_table_names()
        fk_rows = conn.execute(sa.text('PRAGMA foreign_key_list("tcga_files")')).all()
    assert any(r[2] == "slides" for r in fk_rows)


# =============================================================================
# BUILD-PLAN-DB.md Phase 2 debugger, round 3
# =============================================================================
#
# finding 1: `migrate --dry-run` crashed on exactly the legacy databases
# migration 3 exists for -- a version-0 database (no schema_migrations table,
# `slides` carries no identity_scheme/identity_key columns yet) with a
# tcga_files row whose local_path resolves. `plan_migration_3` -> its
# `_find_slide_for_local_path` helper -> unconditionally queried
# `slides.identity_scheme`, a column migration 2 (not yet applied, and never
# applied by a dry run -- R-1) adds. The real (non-dry-run) migrate() never
# hit this because it calls init_db() first.


def test_plan_migration_3_does_not_crash_when_slides_predates_the_identity_columns(
    tmp_path,
):
    """Seen to fail before the fix:

        sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such
        column: identity_scheme
        [SQL: SELECT id FROM slides WHERE identity_scheme='sha256' AND
        identity_key=?]

    on a hand-built version-0 database (no identity columns on `slides`,
    exactly the pre-Phase-1 shape) carrying one tcga_files row whose
    local_path resolves to a real file on disk -- the exact input shape
    migration 3's own backfill exists to preview.
    """
    from hescope.store.migrations import plan_migration_3

    db_path = tmp_path / "pre_migration_2.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE slides (
            id INTEGER PRIMARY KEY, source_kind VARCHAR(32), name VARCHAR(512),
            path VARCHAR(1024) UNIQUE, width INTEGER, height INTEGER,
            mpp FLOAT, extra_json TEXT, created_at DATETIME
        );
        CREATE TABLE tcga_files (
            file_id VARCHAR(64) PRIMARY KEY, file_name VARCHAR(512),
            file_size INTEGER, md5sum VARCHAR(64), sample_id VARCHAR(64),
            case_submitter_id VARCHAR(64), project_id VARCHAR(64),
            local_path VARCHAR(1024), downloaded_at DATETIME,
            first_seen_at DATETIME NOT NULL, slide_id INTEGER
        );
        """
    )
    con.commit()
    con.close()

    real_file = tmp_path / "downloaded-before-migration-2.svs"
    real_file.write_bytes(b"a tcga download recorded before migration 2 ever ran")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO tcga_files (file_id, local_path, first_seen_at) "
        "VALUES ('fid-pre-mig2', ?, '2024-01-01 00:00:00')",
        (str(real_file.resolve()),),
    )
    con.commit()
    con.close()

    engine = get_engine(f"sqlite:///{db_path}", read_only=True)
    with engine.connect() as conn:
        preview = plan_migration_3(conn)  # must not raise

    assert preview["with_local_path"] == 1
    assert preview["could_not_link"] == 1, (
        "no slide has ever been registered for this path, so this row "
        "cannot be linked yet -- the point is that computing this must not "
        "crash, not that it finds a match"
    )

    # and the SAME database migrates cleanly for real (migrate() calls
    # init_db() first) -- the preview must not be the thing that cannot
    # handle what the real run handles fine.
    real_report = migrate(get_engine(f"sqlite:///{db_path}"))
    assert real_report.error is None


# finding 2: a tcga_files.slide_id that already names no row in `slides`
# (possible today because the column carries no real constraint until this
# migration adds one -- e.g. merge_duplicate_slide_paths deleting the row it
# pointed at) aborted the FK rebuild's FK-checked INSERT ... SELECT
# permanently, and the dry run reported it as "already linked" with no hint
# of the abort.


def test_migration_3_recovers_a_dangling_slide_id_instead_of_aborting(tmp_path):
    """Seen to fail before the fix -- a tcga_files.slide_id pointing at a
    slide row that no longer exists aborted every subsequent migrate() call
    identically:

        (sqlite3.IntegrityError) FOREIGN KEY constraint failed
        [SQL: INSERT INTO tcga_files__mig3_rebuild ...]

    with the database stuck at version 2 and no recovery path. The fix nulls
    the dangling reference before the FK-checked rebuild and then re-resolves
    it through the same content-key/path matching every other unlinked row
    goes through -- recovering the link (the file's local_path still points
    at another registered slide with identical content) instead of leaving
    it lost.

    ``SlideRepo.delete`` is used to CREATE the dangling reference here: it
    removes a slide (cascading its ROIs) but -- like
    ``merge_duplicate_slide_paths`` before its own round 3 fix (see
    tests/test_db.py::test_merge_duplicate_slide_paths_repoints_a_linked_tcga_file)
    -- never touches ``tcga_files``, since the two tables live on separate
    ``DeclarativeBase``s with no ORM relationship between them. A real,
    shipped deletion path, not a synthetic one.
    """
    from hescope.store.migrations import plan_migration_3
    from hescope.store.db import Slide, SlideRepo
    from hescope.gdc.tcga_schema import TcgaCatalog

    engine = get_engine(f"sqlite:///{tmp_path}/mig3_dangling.db")
    init_db(engine)
    real_file = tmp_path / "s.svs"
    real_file.write_bytes(b"content for the dangling-fk repro")

    with sa.orm.Session(engine) as s:
        keeper = Slide(
            source_kind="local", name="s", path=str(real_file.resolve()),
            width=10, height=10, extra_json="{}", created_at=datetime(2024, 1, 1),
        )
        s.add(keeper)
        s.commit()
        keeper_id = keeper.id
        # a second row with the SAME content, standing in for whatever
        # slide a tcga_files row was once linked to before it was deleted.
        dup = Slide(
            source_kind="local", name="s-copy", path=str(tmp_path / "s-copy.svs"),
            width=10, height=10, extra_json="{}", created_at=datetime(2024, 1, 1),
        )
        s.add(dup)
        s.commit()
        dup_id = dup.id

    cat = TcgaCatalog(engine)
    cat.upsert_rows([{"file_id": "fid-dangling", "file_name": "s.svs", "file_size": 7}])
    assert cat.mark_downloaded("fid-dangling", str(real_file.resolve()), slide_id=dup_id) is True

    SlideRepo(engine).delete(dup_id)  # the real, shipped deletion path

    with engine.connect() as conn:
        slide_ids = set(conn.execute(sa.text("SELECT id FROM slides")).scalars().all())
        linked_before = conn.execute(
            sa.text("SELECT slide_id FROM tcga_files WHERE file_id='fid-dangling'")
        ).scalar_one()
    assert linked_before == dup_id
    assert dup_id not in slide_ids, "sanity: the dup row must actually be gone"

    with engine.connect() as conn:
        preview = plan_migration_3(conn)
    assert preview["dangling_slide_ids"] == 1

    report = migrate(engine)

    assert report.error is None, f"migration 3 must not abort on a dangling slide_id: {report.error}"
    assert current_version(engine) == SCHEMA_VERSION
    with engine.connect() as conn:
        linked_after = conn.execute(
            sa.text("SELECT slide_id FROM tcga_files WHERE file_id='fid-dangling'")
        ).scalar_one()
    assert linked_after == keeper_id, (
        "the dangling reference must be recovered by re-matching the row's "
        f"local_path, landing on the keeper slide ({keeper_id}); got {linked_after!r}"
    )

    # and a second migrate() call (the exact retry the un-fixed code could
    # never complete) is a true no-op
    second = migrate(engine)
    assert second.error is None
    assert second.applied == []


def test_plan_migration_3_reports_dangling_slide_ids_separately_from_already_linked(
    tmp_path,
):
    from hescope.store.migrations import plan_migration_3
    from hescope.store.db import Slide
    from hescope.gdc.tcga_schema import TcgaCatalog

    engine = get_engine(f"sqlite:///{tmp_path}/mig3_dangling_preview.db")
    init_db(engine)
    real_file = tmp_path / "s.svs"
    real_file.write_bytes(b"dangling preview content")
    with sa.orm.Session(engine) as s:
        slide = Slide(
            source_kind="local", name="s", path=str(real_file.resolve()),
            width=1, height=1, extra_json="{}", created_at=datetime(2024, 1, 1),
        )
        s.add(slide)
        s.commit()
        real_slide_id = slide.id

    cat = TcgaCatalog(engine)
    cat.upsert_rows([{"file_id": "fid-x", "file_name": "s.svs", "file_size": 1}])
    cat.mark_downloaded("fid-x", str(real_file.resolve()), slide_id=999999)  # names no row

    with engine.connect() as conn:
        preview = plan_migration_3(conn)

    assert preview["dangling_slide_ids"] == 1
    assert preview["already_linked"] == 0, (
        "a slide_id that names no row in `slides` must not be counted as a "
        "valid existing link"
    )
    assert preview["would_link"] == 1, (
        f"the dangling row's local_path resolves to a real, registered "
        f"slide ({real_slide_id}) and should be recoverable"
    )


# finding 3: the FK rebuild hardcoded the column list, so any tcga_files
# column outside it (an ALTER TABLE ADD COLUMN from code ahead of this
# migration, or any future column) was silently dropped -- a DROP disguised
# as "additive" (R-4).


def test_migration_3_preserves_a_column_outside_the_original_hardcoded_list(tmp_path):
    """Seen to fail before the fix: migrate(e3) reported no error, and the
    extra column and its data were simply gone afterward --
    ``sqlite3.OperationalError: no such column: future_note``."""
    from hescope.gdc.tcga_schema import TcgaCatalog

    engine = get_engine(f"sqlite:///{tmp_path}/mig3_extracol.db")
    init_db(engine)
    TcgaCatalog(engine)  # creates tcga_files

    with engine.connect() as conn:
        conn.execute(sa.text("ALTER TABLE tcga_files ADD COLUMN future_note TEXT"))
        conn.commit()
        conn.execute(
            sa.text(
                "INSERT INTO tcga_files (file_id, first_seen_at, future_note) "
                "VALUES ('f-extra', :now, 'do not lose me')"
            ),
            {"now": datetime(2020, 1, 1)},
        )
        conn.commit()

    report = migrate(engine)
    assert report.error is None

    with engine.connect() as conn:
        cols = {c["name"] for c in sa.inspect(conn).get_columns("tcga_files")}
        assert "future_note" in cols
        value = conn.execute(
            sa.text("SELECT future_note FROM tcga_files WHERE file_id='f-extra'")
        ).scalar_one()
    assert value == "do not lose me"


# =============================================================================
# Migration 4: L5 measurement layers (design doc §6.4 / §6.5), and Phase 0's
# remaining three columns (measurements.mpp_effective, rois.geom_key,
# rois.created_by -- docs/DATABASE-DESIGN.md L3)
# =============================================================================


def _legacy_roi_at(
    engine, slide_id: int, kind: str, points: list[list[float]], *, created_by=None
) -> int:
    """A ``rois`` row the way a database written BEFORE migration 4 would
    have it -- ``geom_key``/``created_by`` NULL going in, bypassing
    ``ROIRepo.add`` -- with caller-chosen geometry (rather than
    ``_legacy_roi``'s fixed bbox-derived rect) so duplicate-geometry pairs
    can be constructed on purpose."""
    import json as _json

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    with sa.orm.Session(engine) as s:
        roi = ROI(
            slide_id=slide_id,
            kind=kind,
            points_json=_json.dumps(points),
            bbox_json=_json.dumps(bbox),
            created_by=created_by,
        )
        s.add(roi)
        s.commit()
        return roi.id


def test_migration_4_backfills_geom_key_matching_the_source_geometry(tmp_path):
    """R-3: geom_key must equal the SAME hash ``hescope.store.db.geom_key``
    computes from the row's own (pre-migration) kind/points_json -- compared
    here against an independent call to that function, not against whatever
    the migration happened to write."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    points = [[12.4, 30.6], [112.0, 230.0]]
    roi_id = _legacy_roi_at(engine, slide_id, "rect", points)

    migrate(engine)

    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT geom_key FROM rois WHERE id=:id"), {"id": roi_id}
        ).one()
    expected = geom_key("rect", [(p[0], p[1]) for p in points])
    assert row.geom_key == expected


def test_migration_4_backfills_created_by_to_user_when_null(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/mig4.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    roi_id = _legacy_roi_at(engine, slide_id, "rect", [[0, 0], [10, 10]], created_by=None)

    migrate(engine)

    with engine.connect() as conn:
        value = conn.execute(
            sa.text("SELECT created_by FROM rois WHERE id=:id"), {"id": roi_id}
        ).scalar_one()
    assert value == "user"


def test_migration_4_leaves_an_already_set_created_by_alone(tmp_path):
    """R-3: source value 'agent' -> target value 'agent', unchanged -- the
    backfill must not clobber a value some other write path already set."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    roi_id = _legacy_roi_at(
        engine, slide_id, "rect", [[0, 0], [10, 10]], created_by="agent"
    )

    migrate(engine)

    with engine.connect() as conn:
        value = conn.execute(
            sa.text("SELECT created_by FROM rois WHERE id=:id"), {"id": roi_id}
        ).scalar_one()
    assert value == "agent"


def test_migration_4_reports_duplicate_geom_key_groups_without_merging(tmp_path):
    """Two ROIs on the SAME slide with IDENTICAL geometry: this migration
    must not merge them (deciding which survives is a separate, consented
    step -- see the module-level migration-4 docstring) and must not crash
    on a same-key pair even though DATABASE-DESIGN.md's prototype DDL is
    UNIQUE(slide_id, geom_key). Both rows keep their own row and BOTH get
    the (identical) computed geom_key -- 'report, do not silently drop'."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    points = [[5, 5], [50, 50]]
    roi1 = _legacy_roi_at(engine, slide_id, "rect", points)
    roi2 = _legacy_roi_at(engine, slide_id, "rect", [list(p) for p in points])

    report = migrate(engine)

    assert report.error is None
    with engine.connect() as conn:
        rows = {
            r.id: r.geom_key
            for r in conn.execute(
                sa.text("SELECT id, geom_key FROM rois")
            ).all()
        }
    assert rows[roi1] is not None and rows[roi1] == rows[roi2]
    # additive, not destructive: both rows still exist
    assert set(rows) == {roi1, roi2}


def test_plan_migration_4_matches_what_migration_4_actually_writes(tmp_path):
    """Shared-computation guarantee (see :class:`_RoiBackfill`'s docstring):
    the dry-run preview's counts cannot drift from what the real migration
    does, including the duplicate-group count."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    _legacy_roi_at(engine, slide_id, "rect", [[0, 0], [1, 1]], created_by="agent")
    _legacy_roi_at(engine, slide_id, "rect", [[9, 9], [10, 10]], created_by=None)
    dup_points = [[3, 3], [4, 4]]
    # both duplicate rows also have created_by=None (_legacy_roi_at's default),
    # so 3 of the 4 rows (everything but the 'agent' one) need the backfill.
    _legacy_roi_at(engine, slide_id, "polygon", dup_points)
    _legacy_roi_at(engine, slide_id, "polygon", [list(p) for p in dup_points])

    with engine.connect() as conn:
        preview = plan_migration_4(conn)

    migrate(engine)

    with engine.connect() as conn:
        actual_created_by_backfilled = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM rois WHERE created_by='user'"
            )
        ).scalar_one()
        actual_rois = conn.execute(sa.text("SELECT COUNT(*) FROM rois")).scalar_one()

    assert preview["rois"] == actual_rois == 4
    assert preview["created_by_backfilled"] == actual_created_by_backfilled == 3
    assert preview["duplicate_geom_key_groups"] == 1
    assert preview["duplicate_geom_key_rois"] == 2


def test_plan_migration_4_on_an_empty_database_is_all_zero(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_empty.db")
    with engine.connect() as conn:
        preview = plan_migration_4(conn)
    assert preview == {
        "rois": 0,
        "created_by_backfilled": 0,
        "duplicate_geom_key_groups": 0,
        "duplicate_geom_key_rois": 0,
    }


def test_plan_migration_4_does_not_crash_when_rois_predates_created_by(tmp_path):
    """Seen to fail before the fix:

        sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such
        column: created_by
        [SQL: SELECT id, slide_id, kind, points_json, created_by FROM rois]

    Reproduced by running ``plan_migration_4`` against a COPY of the REAL
    shipped ``data/hescope.db`` rather than one of this test file's own
    fixtures: every fixture here calls ``init_db()`` (or ``migrate()``,
    which calls it) before touching migration 4, so ``rois.created_by``
    already exists by the time any OTHER test in this file reaches this
    code path -- the real database's own bootstrap runs ``init_db`` too
    (through the app, at every slide open), but ``migrate()`` itself has
    never run against it, so it sits exactly here: migrations 1-3's columns
    present, migration 4's two new ones absent. A hand-built table
    reproduces that shape without touching the real file (R-1).
    """
    db_path = tmp_path / "pre_migration_4.db"
    con = sqlite3.connect(db_path)
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
            created_at DATETIME,
            bbox_x0 FLOAT, bbox_y0 FLOAT, bbox_x1 FLOAT, bbox_y1 FLOAT
        );
        """  # migrations 1-3's shape; migration 4's geom_key/created_by absent
    )
    con.execute(
        "INSERT INTO slides (id, source_kind, name, path, width, height, "
        "extra_json, created_at) VALUES (1, 'local', 's', 'x.svs', 10, 10, "
        "'{}', '2024-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO rois (id, slide_id, kind, points_json, bbox_json, "
        "created_at) VALUES (1, 1, 'rect', '[[0,0],[10,10]]', '[0,0,10,10]', "
        "'2024-01-01 00:00:00')"
    )
    con.commit()
    con.close()

    engine = get_engine(f"sqlite:///{db_path}", read_only=True)
    with engine.connect() as conn:
        preview = plan_migration_4(conn)  # must not raise

    assert preview["rois"] == 1
    assert preview["created_by_backfilled"] == 1, (
        "the column does not exist yet, so every row needs the backfill -- "
        "computing this must not crash, not merely avoid crashing by "
        "reporting zero"
    )

    # and the SAME database migrates cleanly for real (migrate() calls
    # init_db() first, which adds the column before _migration_4_apply runs).
    real_report = migrate(get_engine(f"sqlite:///{db_path}"))
    assert real_report.error is None


def test_migration_4_is_idempotent(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/mig4.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    roi_id = _legacy_roi_at(engine, slide_id, "rect", [[0, 0], [10, 10]])

    migrate(engine)
    with engine.connect() as conn:
        first = conn.execute(
            sa.text("SELECT geom_key, created_by FROM rois WHERE id=:id"), {"id": roi_id}
        ).one()

    second = migrate(engine)  # nothing pending; must not re-run migration 4
    with engine.connect() as conn:
        second_row = conn.execute(
            sa.text("SELECT geom_key, created_by FROM rois WHERE id=:id"), {"id": roi_id}
        ).one()

    assert second.applied == []
    assert (second_row.geom_key, second_row.created_by) == (first.geom_key, first.created_by)


# --- layers: the DEFAULT 'unregistered' guardrail (design doc §6.2 II) -----


def test_layers_registration_defaults_to_unregistered_not_identity(tmp_path):
    """Pins design doc §6.2's central guardrail at the DATABASE level: a raw
    INSERT that never even mentions the ``registration`` column -- not
    ``LayerRepo.add``, not any Python default that a future edit could
    accidentally change to something falsely reassuring -- must still land
    as 'unregistered', never an identity/aligned default. This is the exact
    failure design doc §6.4 calls "the next 29.4x": a layer silently treated
    as aligned when no one has ever verified that.
    """
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_layers.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO layers "
                "(slide_id, kind, source, frame, store_uri, n_entities, created_at) "
                "VALUES (:sid, 'visium', 'space_ranger', 'fullres_capture', "
                "'zarr:///x', 500, :now)"
            ),
            {"sid": slide_id, "now": datetime(2024, 1, 1)},
        )

    with engine.connect() as conn:
        value = conn.execute(
            sa.text("SELECT registration FROM layers WHERE slide_id=:sid"),
            {"sid": slide_id},
        ).scalar_one()
    assert value == "unregistered"


def test_layers_registration_column_rejects_an_explicit_null(tmp_path):
    """The guardrail is NOT NULL, not merely "usually has a default" -- an
    explicit NULL (a caller trying to bypass the default) must be rejected
    by the schema itself, not silently accepted as 'unknown-and-untracked'."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_layers_null.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)

    with pytest.raises(sa.exc.IntegrityError, match="registration"):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO layers "
                    "(slide_id, kind, source, frame, store_uri, n_entities, "
                    "created_at, registration) "
                    "VALUES (:sid, 'visium', 'space_ranger', 'fullres_capture', "
                    "'zarr:///x', 500, :now, NULL)"
                ),
                {"sid": slide_id, "now": datetime(2024, 1, 1)},
            )


def test_layer_repo_add_omitting_registration_gets_the_db_default(tmp_path):
    """Same guardrail, through the application API: ``LayerRepo.add`` with no
    ``registration`` argument must produce the identical 'unregistered'
    result as the raw-SQL test above -- the app layer must not quietly paper
    over (or diverge from) the DB-level guarantee."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_layers2.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)

    from hescope.store.db import LayerRepo as _LayerRepo

    layer_id = _LayerRepo(engine).add(
        slide_id=slide_id, kind="visium", source="space_ranger",
        frame="fullres_capture", store_uri="zarr:///x", n_entities=500,
    )
    layer = _LayerRepo(engine).get(layer_id)
    assert layer["registration"] == "unregistered"


def test_layers_unique_index_rejects_a_duplicate_identity(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_layers3.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)
    repo = LayerRepo(engine)
    repo.add(
        slide_id=slide_id, kind="visium", source="space_ranger",
        external_id="run-1", frame="fullres_capture", store_uri="zarr:///x",
        n_entities=500,
    )
    with pytest.raises(sa.exc.IntegrityError):
        repo.add(
            slide_id=slide_id, kind="visium", source="space_ranger",
            external_id="run-1", frame="fullres_capture", store_uri="zarr:///y",
            n_entities=999,
        )


def test_layer_deleted_when_its_slide_is_deleted(tmp_path):
    """ON DELETE CASCADE on layers.slide_id (design doc §6.4's DDL)."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_layers4.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)
    layer_id = LayerRepo(engine).add(
        slide_id=slide_id, kind="visium", source="space_ranger",
        frame="fullres_capture", store_uri="zarr:///x", n_entities=500,
    )

    SlideRepo(engine).delete(slide_id)

    assert LayerRepo(engine).get(layer_id) is None


# --- measurements: mpp_effective is the comparability key (design doc §1) --


def test_measurements_mpp_effective_makes_incomparable_rows_excludable(tmp_path):
    """The exact query docs/DATABASE-DESIGN.md L3 motivates ``mpp_effective``
    with: two measurements of the SAME name on the SAME annotation, one at a
    comparable mpp and one 2.7x off, and a WHERE clause that keeps only the
    comparable one -- turning the R07-2 trap into SQL instead of tribal
    knowledge."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_meas.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)
    roi_id = ROIRepo(engine).add(slide_id, ROIGeometry("rect", ((0, 0), (100, 100))))
    mrepo = MeasurementRepo(engine)
    mrepo.record(
        annotation_id=roi_id, name="tissue_fraction", method="roi_stats@v2",
        value=0.62, unit="", mpp_effective=0.355,
    )
    mrepo.record(
        annotation_id=roi_id, name="tissue_fraction", method="roi_stats@v2",
        value=0.90, unit="", mpp_effective=0.971,
    )

    with engine.connect() as conn:
        comparable = conn.execute(
            sa.text(
                "SELECT value FROM measurements "
                "WHERE annotation_id=:aid AND name='tissue_fraction' "
                "AND mpp_effective BETWEEN 0.30 AND 0.45"
            ),
            {"aid": roi_id},
        ).scalars().all()
    assert comparable == [0.62]
    # re-measuring appended, it did not overwrite: both rows still exist
    assert len(mrepo.for_annotation(roi_id)) == 2


def test_measurement_deleted_when_its_annotation_is_deleted(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_meas2.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)
    roi_id = ROIRepo(engine).add(slide_id, ROIGeometry("rect", ((0, 0), (100, 100))))
    m_id = MeasurementRepo(engine).record(
        annotation_id=roi_id, name="tissue_fraction", method="roi_stats@v2", value=0.5,
    )

    assert ROIRepo(engine).delete(roi_id) is True

    with engine.connect() as conn:
        remaining = conn.execute(
            sa.text("SELECT COUNT(*) FROM measurements WHERE id=:id"), {"id": m_id}
        ).scalar_one()
    assert remaining == 0


# --- rois.geom_key / created_by written by ROIRepo.add ---------------------


def test_roi_repo_add_writes_a_geom_key_matching_the_stored_geometry(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_roiadd.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    roi = ROIGeometry("polygon", ((1.4, 2.6), (10.0, 3.0), (5.0, 9.0)))

    roi_id = ROIRepo(engine).add(slide_id, roi)

    stored = ROIRepo(engine).get(roi_id)
    assert stored["geom_key"] == geom_key("polygon", roi.points)
    assert stored["created_by"] == "user"


def test_roi_repo_add_records_an_agent_authored_roi(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_roiadd2.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))

    roi_id = ROIRepo(engine).add(
        slide_id, ROIGeometry("rect", ((0, 0), (5, 5))), created_by="agent",
    )

    assert ROIRepo(engine).get(roi_id)["created_by"] == "agent"


# --- selection_resolutions: records a decision, upserts on re-resolve ------


def test_selection_resolution_repo_records_and_reads_back(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_sel.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)
    roi_id = ROIRepo(engine).add(slide_id, ROIGeometry("rect", ((0, 0), (100, 100))))
    layer_id = LayerRepo(engine).add(
        slide_id=slide_id, kind="visium", source="space_ranger",
        frame="fullres_capture", store_uri="zarr:///x", n_entities=500,
    )
    srepo = SelectionResolutionRepo(engine)

    srepo.record(
        annotation_id=roi_id, layer_id=layer_id, layer_version=1,
        n_selected=42, index_digest="abc123",
    )

    got = srepo.get(roi_id, layer_id, 1)
    assert got["n_selected"] == 42
    assert got["index_digest"] == "abc123"


def test_selection_resolution_repo_upserts_the_same_layer_version(tmp_path):
    """Re-resolving the SAME (annotation, layer, layer_version) overwrites --
    it must not accumulate duplicate rows for a re-run at the same version."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_sel2.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)
    roi_id = ROIRepo(engine).add(slide_id, ROIGeometry("rect", ((0, 0), (100, 100))))
    layer_id = LayerRepo(engine).add(
        slide_id=slide_id, kind="visium", source="space_ranger",
        frame="fullres_capture", store_uri="zarr:///x", n_entities=500,
    )
    srepo = SelectionResolutionRepo(engine)
    srepo.record(annotation_id=roi_id, layer_id=layer_id, layer_version=1,
                 n_selected=42, index_digest="abc123")

    srepo.record(annotation_id=roi_id, layer_id=layer_id, layer_version=1,
                 n_selected=41, index_digest="def456")

    assert srepo.get(roi_id, layer_id, 1)["n_selected"] == 41
    assert len(srepo.for_annotation(roi_id)) == 1


def test_selection_resolution_repo_keeps_a_new_layer_version_as_a_separate_row(tmp_path):
    """A NEW layer_version (the layer's data changed) gets its OWN row rather
    than overwriting the old one -- this is the drift-detection mechanism
    design doc §6.5/§12.6 describe: comparing the two rows is how a re-open
    six months later can say the hit set changed, instead of staying silent."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_sel3.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)
    roi_id = ROIRepo(engine).add(slide_id, ROIGeometry("rect", ((0, 0), (100, 100))))
    layer_id = LayerRepo(engine).add(
        slide_id=slide_id, kind="visium", source="space_ranger",
        frame="fullres_capture", store_uri="zarr:///x", n_entities=500,
    )
    srepo = SelectionResolutionRepo(engine)
    srepo.record(annotation_id=roi_id, layer_id=layer_id, layer_version=1,
                 n_selected=42, index_digest="abc123")

    srepo.record(annotation_id=roi_id, layer_id=layer_id, layer_version=2,
                 n_selected=39, index_digest="zzz999")

    assert len(srepo.for_annotation(roi_id)) == 2
    assert srepo.get(roi_id, layer_id, 1)["n_selected"] == 42
    assert srepo.get(roi_id, layer_id, 2)["n_selected"] == 39


def test_selection_resolution_deleted_when_its_layer_is_deleted(tmp_path):
    """ON DELETE CASCADE on selection_resolutions.layer_id."""
    engine = get_engine(f"sqlite:///{tmp_path}/mig4_sel4.db")
    init_db(engine)
    slide_id = _legacy_slide(engine, path=str(tmp_path / "x.svs"), created_at=datetime(2024, 1, 1))
    migrate(engine)
    roi_id = ROIRepo(engine).add(slide_id, ROIGeometry("rect", ((0, 0), (100, 100))))
    layer_id = LayerRepo(engine).add(
        slide_id=slide_id, kind="visium", source="space_ranger",
        frame="fullres_capture", store_uri="zarr:///x", n_entities=500,
    )
    SelectionResolutionRepo(engine).record(
        annotation_id=roi_id, layer_id=layer_id, layer_version=1,
        n_selected=1, index_digest="x",
    )

    SlideRepo(engine).delete(slide_id)  # cascades slides -> layers -> selection_resolutions

    with engine.connect() as conn:
        remaining = conn.execute(
            sa.text("SELECT COUNT(*) FROM selection_resolutions")
        ).scalar_one()
    assert remaining == 0
