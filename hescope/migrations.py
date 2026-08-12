"""Versioned, forward-only migration runner for the HE-Scope database.

``hescope/db.py:init_db`` has always had an implicit, additive upgrade path
(``create_all`` + ``PRAGMA table_info`` + ``ALTER TABLE ADD COLUMN``) but no
version number: every database reports ``PRAGMA user_version = 0`` regardless
of what schema it actually holds, and there is nowhere for a later phase to
record "this database has migration N applied" other than re-deriving it from
column presence. This module adds that record.

A migration is a ``(version, name, apply)`` triple. ``apply`` receives a raw
``sqlalchemy.Connection`` (not an ORM session) so a migration can run DDL or
hand-write a backfill without pulling in whatever the ORM models look like at
HEAD — a migration's job is to describe a transition from one shipped schema
to the next, and it must still make sense to read after the ORM models have
moved on.

Version 1 is a stamp, not a change: the tables it describes are created by
``hescope.db.init_db`` (``create_all``), not by this module. Applying it to a
database whose tables already exist (the shipped ``data/hescope.db``) writes
one row to ``schema_migrations`` and nothing else. Later phases add real
migrations at version 2, 3, ... — each one still additive (R-4: no ``DROP``,
no rename, no type change).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import sqlalchemy as sa

from .identity import content_key

SCHEMA_VERSION = 2  # bumped by each phase that adds a migration


def _utcnow() -> datetime:
    """Current UTC time, naive (matches how ``hescope.db`` stores timestamps:
    naive-but-UTC, since an aware datetime is silently stripped of its offset
    by SQLAlchemy's SQLite ``DATETIME`` binding)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _db_datetime(dt: datetime) -> str:
    """Render ``dt`` the same way SQLAlchemy's sqlite ``DATETIME`` type does
    (``YYYY-MM-DD HH:MM:SS.ffffff``), by hand rather than by importing
    ``hescope.db``'s ORM types -- this module must stay readable, and this
    exact statement runnable, after the ORM models have moved on (see the
    module docstring). Migrations write raw SQL, so nothing here relies on
    SQLAlchemy's type-driven bind processing to get the format right."""
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


@dataclass(frozen=True)
class Migration:
    """One forward-only schema step.

    ``apply`` must be idempotent-safe only in the sense that it is never
    called twice by :func:`migrate` for the same database — ``migrate`` skips
    anything with ``version <= current_version(engine)``. It receives the
    live connection inside an open transaction; raising rolls that
    transaction back and stops the run (see :func:`migrate`).
    """

    version: int
    name: str
    apply: Callable[[sa.Connection], None]


def _migration_1_baseline(conn: sa.Connection) -> None:
    """Stamp only. The tables this version describes already come from
    ``hescope.db.init_db`` (``create_all``) — called separately, before this
    migration runs, so an empty database gets its tables first and a
    database that already has them (every shipped ``data/hescope.db``) is
    untouched by this function. This migration exists so ``schema_migrations``
    has a row for version 1 and later migrations have a version to follow."""
    return None


@dataclass(frozen=True)
class _SlideBackfill:
    """One row's worth of what migration 2 will write, computed once and
    shared by :func:`_migration_2_apply` (which writes it) and
    :func:`plan_migration_2` (which only counts it) -- so the dry-run report
    can never drift from what the real run does (the same reason
    ``hescope.db.plan_init_db`` and ``init_db`` share one computation)."""

    slide_id: int
    path: str
    source_kind: str
    created_at: object  # opaque: the exact raw value read from `slides`
    identity_key: str | None
    file_size: int | None
    missing: bool


def _compute_slide_backfills(conn: sa.Connection) -> list[_SlideBackfill]:
    if not sa.inspect(conn).has_table("slides"):
        # A dry run may preview an EMPTY database (no `init_db` call has run
        # yet, for real or otherwise) -- `apply()` never hits this, since the
        # real `migrate` command always runs `init_db(engine)` first, but the
        # read-only preview must not crash just because nothing has created
        # the table it wants to read from yet.
        return []
    rows = conn.execute(
        sa.text("SELECT id, path, source_kind, created_at FROM slides")
    ).all()
    out: list[_SlideBackfill] = []
    for slide_id, path, source_kind, created_at in rows:
        ck = content_key(path) if path else None
        if ck is None:
            out.append(
                _SlideBackfill(slide_id, path, source_kind, created_at, None, None, True)
            )
        else:
            key, size = ck
            out.append(
                _SlideBackfill(slide_id, path, source_kind, created_at, key, size, False)
            )
    return out


def _compute_roi_bbox_backfills(
    conn: sa.Connection,
) -> list[tuple[int, float, float, float, float]]:
    if not sa.inspect(conn).has_table("rois"):
        return []
    out: list[tuple[int, float, float, float, float]] = []
    for roi_id, bbox_json in conn.execute(
        sa.text("SELECT id, bbox_json FROM rois")
    ).all():
        try:
            x0, y0, x1, y1 = json.loads(bbox_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        out.append((roi_id, float(x0), float(y0), float(x1), float(y1)))
    return out


def _conflicting_identities(backfills: list[_SlideBackfill]) -> set[str]:
    """Identity keys shared by two or more resolving (non-missing) rows.

    Shared by :func:`plan_migration_2` and :func:`_migration_2_apply` (the
    apply loop below inlined this same two-pass seen/conflicting logic
    before it was pulled out here) so the preview's "how many rows get a
    real identity" and "how many are duplicate-content and get skipped"
    counts are computed by the exact same rule ``_migration_2_apply`` uses
    to decide which rows to leave ``identity_key`` NULL for -- see that
    function's docstring for why it must not merge them.
    """
    seen: set[str] = set()
    conflicting: set[str] = set()
    for b in backfills:
        if not b.missing:
            if b.identity_key in seen:
                conflicting.add(b.identity_key)
            seen.add(b.identity_key)
    return conflicting


def plan_migration_2(conn: sa.Connection) -> dict:
    """What migration 2's backfill WOULD write, computed read-only.

    Returns ``{"slide_files": N, "missing": N, "distinct_identities": N,
    "duplicate_content_rows": N, "rois_backfilled": N}``. Powers ``hescope
    migrate --dry-run``'s report (R-8: measured, not invented) -- see
    :func:`_compute_slide_backfills` / :func:`_compute_roi_bbox_backfills`,
    which this and the real migration both call, so the preview cannot say
    something different from what running for real would do.

    ``distinct_identities`` counts only rows migration 2 will actually WRITE
    an identity for -- it excludes duplicate-content rows via
    :func:`_conflicting_identities`, the same guard ``_migration_2_apply``
    uses to leave those rows ``identity_key`` NULL rather than colliding on
    the partial unique index. Before this fix ``distinct_identities`` was
    ``len({b.identity_key for b in backfills if not b.missing})`` -- a naive
    set of ALL resolving identities, duplicates included, with no knowledge
    of the apply-side guard -- so a duplicate-content pair (two rows, one
    shared identity_key) was counted as "1 distinct identity" here while the
    real migration wrote 0 identities for that pair (both left NULL, see
    ``_migration_2_apply``). ``duplicate_content_rows`` reports separately
    how many resolving rows fall into a conflicting group and get skipped,
    so a reviewer sees the blast radius the writable count alone hides.
    """
    backfills = _compute_slide_backfills(conn)
    conflicting = _conflicting_identities(backfills)
    identities = {
        b.identity_key
        for b in backfills
        if not b.missing and b.identity_key not in conflicting
    }
    duplicate_content_rows = sum(
        1 for b in backfills if not b.missing and b.identity_key in conflicting
    )
    return {
        "slide_files": len(backfills),
        "missing": sum(1 for b in backfills if b.missing),
        "distinct_identities": len(identities),
        "duplicate_content_rows": duplicate_content_rows,
        "rois_backfilled": len(_compute_roi_bbox_backfills(conn)),
    }


def _migration_2_apply(conn: sa.Connection) -> None:
    """The SVS <-> ROI relationship (BUILD-PLAN-DB.md Phase 1).

    The schema itself -- ``slides.identity_scheme`` / ``identity_key`` /
    ``file_size`` / ``md5sum``, the partial unique index on identity, the
    ``slide_files`` table, and ``rois.bbox_x0..bbox_y1`` -- is declared on
    the current ORM models (``hescope.db.Slide``, ``SlideFile``, ``ROI``)
    and so is already created by ``init_db``'s additive ``create_all`` +
    ``ALTER TABLE`` path BEFORE this function runs (``hescope.cli``'s
    ``migrate`` command calls ``init_db(engine)`` then ``migrate(engine)``;
    see this module's docstring for why that order is load-bearing and why
    a migration does not re-derive DDL the ORM models already describe).
    This function's job is the DATA that schema exists to hold: a
    ``slide_files`` row for every existing slide, a computed identity for
    every path that still resolves, and the four bbox columns for every
    existing ROI.

    Does NOT merge duplicate slides even when two rows resolve to the same
    identity (the partial unique index would then reject the second
    identity UPDATE) -- detecting duplicates is this migration's job, moving
    ROIs between rows is a separate, reviewable, consented step
    (``hescope dedupe-slides``, extended to identity is future work). A
    slide whose identity UPDATE would collide is simply left with
    ``identity_scheme``/``identity_key`` NULL, exactly like a slide whose
    path does not resolve; both cases are diagnosable afterward by asking
    which content hash existing rows disagree about, without this migration
    having already destroyed the evidence.
    """
    now = _utcnow()
    now_str = _db_datetime(now)
    backfills = _compute_slide_backfills(conn)  # one pass; apply() and the
    # dry-run preview both call this, but within one apply() we must not
    # hash every file twice just to find duplicates before writing.
    conflicting_identities = _conflicting_identities(backfills)  # shared with
    # plan_migration_2 (see its docstring) so the preview's counts cannot
    # drift from what this loop below actually decides to write.
    for b in backfills:
        first_seen = _db_datetime(b.created_at) if isinstance(b.created_at, datetime) else b.created_at
        conn.execute(
            sa.text(
                "INSERT INTO slide_files "
                "(slide_id, path, source_kind, first_seen_at, last_seen_at, missing_since) "
                "VALUES (:slide_id, :path, :source_kind, :first_seen_at, :last_seen_at, "
                ":missing_since) "
                "ON CONFLICT(path) DO NOTHING"
            ),
            {
                "slide_id": b.slide_id,
                "path": b.path,
                "source_kind": b.source_kind,
                "first_seen_at": first_seen,
                "last_seen_at": first_seen,
                "missing_since": None if not b.missing else now_str,
            },
        )
        if not b.missing and b.identity_key not in conflicting_identities:
            conn.execute(
                sa.text(
                    "UPDATE slides SET identity_scheme='sha256', identity_key=:key, "
                    "file_size=:size WHERE id=:id"
                ),
                {"key": b.identity_key, "size": b.file_size, "id": b.slide_id},
            )
    for roi_id, x0, y0, x1, y1 in _compute_roi_bbox_backfills(conn):
        conn.execute(
            sa.text(
                "UPDATE rois SET bbox_x0=:x0, bbox_y0=:y0, bbox_x1=:x1, bbox_y1=:y1 "
                "WHERE id=:id"
            ),
            {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "id": roi_id},
        )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="baseline schema (stamp only, no schema change)",
        apply=_migration_1_baseline,
    ),
    Migration(
        version=2,
        name="the SVS <-> ROI relationship: slide identity, slide_files, roi bbox columns",
        apply=_migration_2_apply,
    ),
)


def _validate_migrations(migrations: tuple[Migration, ...]) -> None:
    """Reject a ``MIGRATIONS`` tuple that is not ordered and gapless from 1.

    Called at import time (below) so a bad edit — two migrations both
    claiming version 3, version 2 defined before version 1, a skipped
    version — fails the moment the module loads rather than surfacing later
    as a silently-wrong ``current_version()`` in production.
    """
    actual = [m.version for m in migrations]
    expected = list(range(1, len(migrations) + 1))
    if actual != expected:
        raise ValueError(
            "MIGRATIONS must be ordered and gapless starting at 1, got "
            f"{actual!r}"
        )


_validate_migrations(MIGRATIONS)


_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  applied_at TEXT NOT NULL
)
"""


def _ensure_schema_migrations_table(conn: sa.Connection) -> None:
    conn.execute(sa.text(_SCHEMA_MIGRATIONS_DDL))


def current_version(engine: sa.Engine) -> int:
    """Highest version recorded in ``schema_migrations``; 0 if that table is
    absent (a database ``migrate()`` has never touched) or empty."""
    if "schema_migrations" not in sa.inspect(engine).get_table_names():
        return 0
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT MAX(version) FROM schema_migrations")
        ).scalar()
    return int(result) if result is not None else 0


def pending(engine: sa.Engine) -> list[Migration]:
    """Migrations with ``version > current_version(engine)``, in order."""
    applied_through = current_version(engine)
    return [m for m in MIGRATIONS if m.version > applied_through]


@dataclass(frozen=True)
class MigrationReport:
    """What :func:`migrate` did (or, under ``dry_run``, would do).

    ``to_version`` is the version actually reached when migrations ran (the
    last one committed, or ``from_version`` unchanged if none did); under
    ``dry_run`` it is the version reaching the end of ``skipped`` would leave
    the database at, computed without writing anything. ``error`` is the
    stringified exception from the migration that stopped the run, or
    ``None`` on a clean run.
    """

    from_version: int
    to_version: int
    applied: list[str]
    skipped: list[str]
    error: str | None = None


def migrate(engine: sa.Engine, *, dry_run: bool = False) -> MigrationReport:
    """Apply every pending migration, each in its own transaction.

    Calls ``hescope.db.init_db(engine)`` first (unless ``dry_run``) so this
    function is self-contained: migration 2's DDL (``slide_files``, the
    identity columns/index, the bbox columns) is declared on the ORM models
    and created by ``init_db``'s additive ``create_all`` + ``ALTER TABLE``
    path, not by this module (see ``_migration_2_apply``'s docstring) --
    calling ``migrate()`` without an ``init_db`` first used to raise
    ``sqlite3.OperationalError: no such table: slide_files`` and leave the
    version stuck at 1, on any legacy-schema database (measured; see
    ``tests/test_migrations.py::test_migrate_is_self_contained_...``).
    ``init_db`` is idempotent and additive-only (R-4), so calling it here
    even when ``hescope.cli`` has already called it once is a no-op, not a
    double-write. Imported locally (not at module top) to avoid a
    module-level import cycle with ``hescope.db``.

    A migration that raises rolls back ONLY that migration's transaction
    (``schema_migrations`` included, since the table is created inside the
    same transaction the first time it is needed) and stops the run —
    migrations already committed before it stay committed, and the ones
    after it are reported in ``skipped``, never attempted.

    ``dry_run=True`` never calls ``init_db``, never opens a write
    transaction, never calls any migration's ``apply``, and never touches
    ``schema_migrations`` — it only reads the current version and computes
    what running for real would do. Safe to call against a database this
    process must never write to (R-1): point it at a copy. (The CLI's
    ``--dry-run`` path additionally previews what ``init_db`` itself would
    do, via ``hescope.db.plan_init_db`` against a read-only engine — see
    ``hescope.cli._cmd_migrate`` — since this function's own dry run does
    not touch ``init_db`` at all.)
    """
    if not dry_run:
        from .db import init_db

        init_db(engine)

    from_version = current_version(engine)
    todo = pending(engine)

    if dry_run:
        return MigrationReport(
            from_version=from_version,
            to_version=todo[-1].version if todo else from_version,
            applied=[],
            skipped=[f"{m.version}: {m.name}" for m in todo],
            error=None,
        )

    applied: list[str] = []
    version = from_version
    for m in todo:
        try:
            with engine.begin() as conn:
                _ensure_schema_migrations_table(conn)
                m.apply(conn)
                conn.execute(
                    sa.text(
                        "INSERT INTO schema_migrations "
                        "(version, name, applied_at) "
                        "VALUES (:version, :name, :applied_at)"
                    ),
                    {
                        "version": m.version,
                        "name": m.name,
                        "applied_at": _utcnow().isoformat(),
                    },
                )
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            remaining = [f"{x.version}: {x.name}" for x in todo if x.version > version]
            return MigrationReport(
                from_version=from_version,
                to_version=version,
                applied=applied,
                skipped=remaining,
                error=str(exc),
            )
        applied.append(f"{m.version}: {m.name}")
        version = m.version

    return MigrationReport(
        from_version=from_version,
        to_version=version,
        applied=applied,
        skipped=[],
        error=None,
    )
