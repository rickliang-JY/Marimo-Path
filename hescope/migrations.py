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

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import sqlalchemy as sa

SCHEMA_VERSION = 1  # bumped by each phase that adds a migration


def _utcnow() -> datetime:
    """Current UTC time, naive (matches how ``hescope.db`` stores timestamps:
    naive-but-UTC, since an aware datetime is silently stripped of its offset
    by SQLAlchemy's SQLite ``DATETIME`` binding)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="baseline schema (stamp only, no schema change)",
        apply=_migration_1_baseline,
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

    A migration that raises rolls back ONLY that migration's transaction
    (``schema_migrations`` included, since the table is created inside the
    same transaction the first time it is needed) and stops the run —
    migrations already committed before it stay committed, and the ones
    after it are reported in ``skipped``, never attempted.

    ``dry_run=True`` never opens a write transaction, never calls any
    migration's ``apply``, and never touches ``schema_migrations`` — it only
    reads the current version and computes what running for real would do.
    Safe to call against a database this process must never write to
    (R-1): point it at a copy.
    """
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
