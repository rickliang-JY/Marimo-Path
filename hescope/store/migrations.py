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
``hescope.store.db.init_db`` (``create_all``), not by this module. Applying it to a
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

from ..core.identity import content_key

SCHEMA_VERSION = 4  # bumped by each phase that adds a migration


def _utcnow() -> datetime:
    """Current UTC time, naive (matches how ``hescope.store.db`` stores timestamps:
    naive-but-UTC, since an aware datetime is silently stripped of its offset
    by SQLAlchemy's SQLite ``DATETIME`` binding)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _db_datetime(dt: datetime) -> str:
    """Render ``dt`` the same way SQLAlchemy's sqlite ``DATETIME`` type does
    (``YYYY-MM-DD HH:MM:SS.ffffff``), by hand rather than by importing
    ``hescope.store.db``'s ORM types -- this module must stay readable, and this
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
    ``hescope.store.db.init_db`` (``create_all``) — called separately, before this
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
    ``hescope.store.db.plan_init_db`` and ``init_db`` share one computation)."""

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
    the current ORM models (``hescope.store.db.Slide``, ``SlideFile``, ``ROI``)
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


# ---------------------------------------------------------------------------
# Migration 3: TCGA download -> storage -> injection (BUILD-PLAN-DB.md Phase 2)
# ---------------------------------------------------------------------------

def _tcga_files_has_slide_fk(conn: sa.Connection) -> bool:
    """Whether ``tcga_files.slide_id`` already carries a REAL foreign key to
    ``slides(id)`` at the SQLite level (``PRAGMA foreign_key_list``, not the
    ORM model -- ``TcgaFile`` deliberately does not declare one; see
    :func:`_rebuild_tcga_files_with_slide_fk`'s docstring for why). ``False``
    when the table does not exist at all, so the caller's "would create it
    WITH the fk" and "would add the fk to what is already there" both read
    the same signal."""
    if not sa.inspect(conn).has_table("tcga_files"):
        return False
    rows = conn.execute(sa.text('PRAGMA foreign_key_list("tcga_files")')).all()
    # PRAGMA foreign_key_list columns: (id, seq, table, from, to, on_update,
    # on_delete, match)
    return any(r[2] == "slides" and r[3] == "slide_id" for r in rows)


def _rebuild_tcga_files_with_slide_fk(conn: sa.Connection) -> None:
    """Add a REAL ``FOREIGN KEY(slide_id) REFERENCES slides(id)`` to
    ``tcga_files`` (defect 2.1: it was indexed but unconstrained).

    SQLite has no ``ALTER TABLE ... ADD CONSTRAINT``: adding a table-level
    constraint after the fact means the documented SQLite technique --
    create a new table with the constraint, copy every row across, drop the
    old table, rename the new one into place -- inside THIS migration's own
    transaction, so a failure partway rolls the whole thing back (the same
    guarantee ``engine.begin()`` already gives every other migration; see
    ``hescope.store.db``'s pragma hook for why DDL here is transactional at all).
    All columns and all data are preserved exactly; only the constraint (and
    the indexes SQLite drops along with the table) are re-created -- this is
    additive in effect, even though the mechanism is a drop (R-4's intent is
    "old readers keep working, no data lost", both of which hold here: same
    table name, same columns, same rows, same indexes, once this returns).

    ``TcgaFile.slide_id`` (the ORM model, ``hescope/tcga_schema.py``) does
    NOT declare this ``ForeignKey`` itself, deliberately: ``TcgaFile`` lives
    on ``TcgaBase``, a SEPARATE ``DeclarativeBase`` from ``slides``'
    ``hescope.store.db.Base`` (by design -- see ``tcga_schema.py``'s module
    docstring), and a cross-``MetaData`` ``ForeignKey`` makes
    ``create_all()`` raise ``NoReferencedTableError`` the moment it tries to
    topologically sort tables to create (measured: any ``TcgaBase.metadata
    .create_all(engine)`` call, on ANY database, fresh or not, raises this
    the instant the model gains ``ForeignKey("slides.id")`` -- confirmed
    against a two-``DeclarativeBase`` reproduction of exactly this shape).
    So the constraint is added here, in raw SQL, uniformly for every
    database this migration ever runs against -- a brand-new ``tcga_files``
    ``init_tcga_schema`` just created (see :func:`migrate`'s docstring: it
    calls ``init_tcga_schema`` first, the same self-containment
    ``init_db`` already gets) rebuilds just as an old one with 50 rows does;
    :func:`_tcga_files_has_slide_fk` makes a second run a no-op either way.

    The new table's column list is read from ``PRAGMA table_info`` on the
    LIVE table, not from a hardcoded constant -- a hardcoded list silently
    DROPS any column the live table has beyond it (defect: an
    ``ALTER TABLE tcga_files ADD COLUMN`` issued by code ahead of this
    migration, or any future column, was gone with no error after this
    rebuild). Reading the actual columns means the CREATE/INSERT can never
    drift from what the table really holds, by construction, rather than by
    a comment asking two independent lists to stay in sync.
    """
    info_rows = conn.execute(sa.text('PRAGMA table_info("tcga_files")')).all()
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
    col_names = [r[1] for r in info_rows]
    pk_cols = [r[1] for r in info_rows if r[5]]
    col_defs = []
    for _cid, name, coltype, notnull, dflt_value, _pk in info_rows:
        parts = [f'"{name}"']
        if coltype:
            parts.append(coltype)
        if notnull:
            parts.append("NOT NULL")
        if dflt_value is not None:
            parts.append(f"DEFAULT {dflt_value}")
        col_defs.append(" ".join(parts))
    constraints = [f'PRIMARY KEY ({", ".join(pk_cols)})']
    if "sample_id" in col_names:
        constraints.append(
            "FOREIGN KEY(sample_id) REFERENCES tcga_samples (sample_id)"
        )
    constraints.append(
        "FOREIGN KEY(slide_id) REFERENCES slides (id) ON DELETE SET NULL"
    )

    conn.execute(sa.text("DROP TABLE IF EXISTS tcga_files__mig3_rebuild"))
    conn.execute(
        sa.text(
            "CREATE TABLE tcga_files__mig3_rebuild ("
            + ", ".join(col_defs + constraints)
            + ")"
        )
    )
    cols = ", ".join(f'"{c}"' for c in col_names)
    conn.execute(
        sa.text(
            f"INSERT INTO tcga_files__mig3_rebuild ({cols}) "
            f"SELECT {cols} FROM tcga_files"
        )
    )
    conn.execute(sa.text("DROP TABLE tcga_files"))
    conn.execute(
        sa.text("ALTER TABLE tcga_files__mig3_rebuild RENAME TO tcga_files")
    )
    for col in ("case_submitter_id", "slide_id", "sample_id", "project_id"):
        if col in col_names:
            conn.execute(
                sa.text(f'CREATE INDEX ix_tcga_files_{col} ON tcga_files ({col})')
            )


@dataclass(frozen=True)
class _TcgaLinkCandidate:
    """One ``tcga_files`` row with a recorded ``local_path``: what migration
    3's backfill would do (:func:`plan_migration_3`) or does
    (:func:`_migration_3_apply`) with it -- one computation shared by both,
    for the same reason ``_SlideBackfill`` is shared by migration 2's plan
    and apply (see that dataclass's docstring).

    ``dangling`` marks a row whose CURRENT ``slide_id`` names no row in
    ``slides`` (round 3 finding 2: possible today because the column carries
    no real constraint until this migration adds one -- e.g.
    ``merge_duplicate_slide_paths`` deleting the row it pointed at). Such a
    row is treated exactly like an unlinked one: ``already_linked_slide_id``
    is ``None`` and ``matched_slide_id`` goes through the same
    content-key/path matching as any other row, so the link is recovered
    rather than the FK rebuild's FK-checked copy aborting on it.
    """

    file_id: str
    local_path: str
    already_linked_slide_id: int | None
    matched_slide_id: int | None
    dangling: bool = False


def _slides_identity_columns_exist(conn: sa.Connection) -> bool:
    """Whether ``slides`` already carries migration 2's identity columns
    (``identity_scheme`` / ``identity_key``).

    ``False`` on a database migration 2 has never touched -- exactly the
    database ``migrate --dry-run`` must be able to preview (R-1: it must
    never call ``init_db`` -- see :func:`migrate`'s docstring), and exactly
    the shape a real (non-dry-run) ``migrate()`` call always sees for a
    moment too, since migration 2 runs before migration 3 in the same call.
    Round 3 finding 1: :func:`_find_slide_for_local_path` used to query
    ``identity_scheme`` unconditionally, so the read-only preview crashed
    (``OperationalError: no such column: identity_scheme``) on precisely the
    pre-Phase-1 databases migration 3 exists for, while the real run never
    hit it because ``init_db`` (called first) had already added the column.
    """
    if not sa.inspect(conn).has_table("slides"):
        return False
    cols = {c["name"] for c in sa.inspect(conn).get_columns("slides")}
    return "identity_scheme" in cols


def _find_slide_for_local_path(
    conn: sa.Connection, local_path: str, *, identity_ready: bool = True
) -> int | None:
    """Look up (never create) the ``slides`` row a downloaded TCGA file
    belongs to. Tried in order:

    1. Content identity -- the same ``('sha256', content_key(path))`` Phase 1
       backfills onto every resolving slide (:func:`_compute_slide_backfills`
       above), so a file downloaded once and opened once already carries a
       matching row most of the time. Skipped entirely when
       ``identity_ready`` is ``False`` (``slides.identity_scheme`` does not
       exist yet on this database -- see
       :func:`_slides_identity_columns_exist`), since the column the query
       needs simply is not there.
    2. An exact ``path`` match -- covers a slide row whose identity is NULL
       (a duplicate-content group migration 2 deliberately leaves unlinked,
       or a row from before Phase 1 ran at all) but whose ``path`` happens
       to already equal this local_path, which is exactly the shape the real
       database's one downloaded file is in: registered by the ordinary open
       path before this migration exists.

    Returns ``None`` -- never inserts a row -- when the path does not
    resolve to a readable file, or resolves but matches nothing already
    known. A brand-new ``slides`` row needs real ``width``/``height``, which
    only opening the file can provide; inventing one with placeholder
    dimensions here would be exactly defect 2.4's shape (a metadata-free row
    standing in for a real one) transplanted from ``mark_downloaded`` into
    this migration, just because a migration COULD write a row that
    satisfies the new foreign key. A file downloaded but never opened is
    correctly reported as "could not link", not silently half-registered.
    """
    if identity_ready:
        ck = content_key(local_path)
        if ck is not None:
            key, _size = ck
            found = conn.execute(
                sa.text(
                    "SELECT id FROM slides WHERE identity_scheme='sha256' "
                    "AND identity_key=:key"
                ),
                {"key": key},
            ).scalar()
            if found is not None:
                return int(found)
    # Local import: this module must not depend on hescope.store.db at module
    # level (see migrate()'s docstring on the import cycle that avoids).
    from .db import normalize_slide_path

    norm = normalize_slide_path(local_path)
    found = conn.execute(
        sa.text("SELECT id FROM slides WHERE path=:p"), {"p": norm}
    ).scalar()
    return int(found) if found is not None else None


def _compute_tcga_link_candidates(conn: sa.Connection) -> list[_TcgaLinkCandidate]:
    if not sa.inspect(conn).has_table("tcga_files"):
        return []
    identity_ready = _slides_identity_columns_exist(conn)
    valid_slide_ids: set[int] = (
        set(conn.execute(sa.text("SELECT id FROM slides")).scalars().all())
        if sa.inspect(conn).has_table("slides")
        else set()
    )
    rows = conn.execute(
        sa.text(
            "SELECT file_id, local_path, slide_id FROM tcga_files "
            "WHERE local_path IS NOT NULL"
        )
    ).all()
    out: list[_TcgaLinkCandidate] = []
    for file_id, local_path, slide_id in rows:
        if slide_id is not None and slide_id in valid_slide_ids:
            out.append(_TcgaLinkCandidate(file_id, local_path, slide_id, slide_id))
            continue
        dangling = slide_id is not None  # non-null but names no row in slides
        matched = _find_slide_for_local_path(
            conn, local_path, identity_ready=identity_ready
        )
        out.append(_TcgaLinkCandidate(file_id, local_path, None, matched, dangling))
    return out


def plan_migration_3(conn: sa.Connection) -> dict:
    """What migration 3 WOULD do, computed read-only. Powers ``hescope
    migrate --dry-run``'s report, the same self-non-drifting contract as
    :func:`plan_migration_2` (this and :func:`_migration_3_apply` both call
    :func:`_compute_tcga_link_candidates` / :func:`_tcga_files_has_slide_fk`,
    so the preview cannot say something different from what running for
    real would do).

    Returns ``{"tcga_files_exists": bool, "fk_present": bool,
    "with_local_path": N, "already_linked": N, "dangling_slide_ids": N,
    "would_link": N, "could_not_link": N}``. ``fk_present`` is meaningless
    (``False``) when ``tcga_files_exists`` is ``False`` -- there is no table
    to have a constraint on yet. ``dangling_slide_ids`` (round 3 finding 2)
    counts rows whose CURRENT ``slide_id`` names no row in ``slides`` --
    these are excluded from ``already_linked`` (a dangling reference is not
    a valid link) and folded into ``would_link``/``could_not_link`` instead,
    since the migration re-resolves them rather than aborting on them.
    """
    exists = sa.inspect(conn).has_table("tcga_files")
    candidates = _compute_tcga_link_candidates(conn)
    already_linked = sum(1 for c in candidates if c.already_linked_slide_id is not None)
    dangling = sum(1 for c in candidates if c.dangling)
    would_link = sum(
        1 for c in candidates
        if c.already_linked_slide_id is None and c.matched_slide_id is not None
    )
    could_not_link = sum(
        1 for c in candidates
        if c.already_linked_slide_id is None and c.matched_slide_id is None
    )
    return {
        "tcga_files_exists": exists,
        "fk_present": _tcga_files_has_slide_fk(conn),
        "with_local_path": len(candidates),
        "already_linked": already_linked,
        "dangling_slide_ids": dangling,
        "would_link": would_link,
        "could_not_link": could_not_link,
    }


def _migration_3_apply(conn: sa.Connection) -> None:
    """TCGA download -> storage -> injection (BUILD-PLAN-DB.md Phase 2).

    ``tcga_files`` itself is created by ``init_tcga_schema`` (called by
    :func:`migrate` before any migration runs -- see that function's
    docstring, the same self-containment fix migration 2 already needed for
    ``slide_files``), so by the time this runs the table always exists,
    fresh or legacy. This function's job is (1) clear any dangling
    ``slide_id`` (round 3 finding 2 -- see below), (2) give ``slide_id`` a
    real foreign key, via a table rebuild since SQLite cannot ``ALTER TABLE
    ADD CONSTRAINT`` (see :func:`_rebuild_tcga_files_with_slide_fk`), and
    (3) backfill ``slide_id`` for every row whose ``local_path`` resolves to
    an already-registered slide -- never inventing one (see
    :func:`_find_slide_for_local_path`'s docstring on why not).
    """
    # A slide_id that already names no row in `slides` -- possible today
    # because the column carries no real constraint until the rebuild below
    # adds one. Real, shipped ways to reach this state: SlideRepo.delete()
    # (removes a slide but never touches tcga_files -- the two tables have
    # no ORM relationship, living on separate DeclarativeBases), and, on a
    # database from before round 3's OTHER fix, merge_duplicate_slide_paths
    # deleting the row it pointed at (that path is now closed -- see
    # hescope.store.db.merge_duplicate_slide_paths -- but a database this
    # migration reaches after upgrading from an older version can already
    # carry the dangling reference from before the fix existed). Must be
    # cleared BEFORE the rebuild's INSERT ... SELECT, which is
    # FK-checked immediately (see _rebuild_tcga_files_with_slide_fk's
    # docstring) and would otherwise abort the WHOLE migration, permanently:
    # every subsequent migrate() call hits the identical row and fails the
    # identical way, with the version stuck and no recovery path in the
    # codebase. Clearing it here and then letting the matching loop below
    # (which runs on every row with slide_id NULL, including the one just
    # cleared) re-resolve it from local_path is what recovers the link
    # instead of merely avoiding the crash.
    conn.execute(
        sa.text(
            "UPDATE tcga_files SET slide_id=NULL WHERE slide_id IS NOT NULL "
            "AND slide_id NOT IN (SELECT id FROM slides)"
        )
    )
    if not _tcga_files_has_slide_fk(conn):
        _rebuild_tcga_files_with_slide_fk(conn)
    for c in _compute_tcga_link_candidates(conn):
        if c.already_linked_slide_id is None and c.matched_slide_id is not None:
            conn.execute(
                sa.text("UPDATE tcga_files SET slide_id=:sid WHERE file_id=:fid"),
                {"sid": c.matched_slide_id, "fid": c.file_id},
            )


# ---------------------------------------------------------------------------
# Migration 4: L5 measurement layers, and Phase 0's remaining three columns
# (design doc §6.4 / §6.5 / §6.2; docs/DATABASE-DESIGN.md L3)
# ---------------------------------------------------------------------------
#
# The DDL for all of it -- the new tables `layers`, `selection_resolutions`,
# `measurements` (which brings `mpp_effective` with it, since the column has
# nowhere to live until the table exists), and the two new `rois` columns
# `geom_key` / `created_by` -- is declared on the current ORM models
# (`hescope.store.db.Layer`, `SelectionResolution`, `Measurement`, `ROI`) and so
# is already created by `init_db`'s additive `create_all` + `ALTER TABLE`
# path BEFORE this function runs (`migrate()` calls `init_db(engine)` first;
# see this module's docstring on why that order is load-bearing, same as
# migration 2's slide_files). `layers`, `selection_resolutions` and
# `measurements` are BRAND NEW tables with no legacy data anywhere to carry
# forward, so `create_all` alone is their whole story -- this migration does
# not touch them. `rois` already exists in every real database, so its two
# new columns land NULL on every existing row; THIS function's job is that
# data: a real `geom_key` for every existing ROI, and `created_by` backfilled
# to 'user' wherever it is still NULL.
#
# Deliberately NOT done here (documented, not silently dropped): merging the
# `rois` rows that turn out to share a `(slide_id, geom_key)` -- the live
# database has 2 such groups (docs/DATABASE-DESIGN.md §5 step 5) -- and
# extracting `measurements` rows out of the existing `rois.stats_json` blobs
# (DATABASE-DESIGN.md's own step 4). Both are legitimate follow-on migrations
# with their own consent question ("which of a duplicate pair survives",
# "does a stats_json-derived row get a real mpp_effective or NULL"); folding
# either into this one would make an additive, always-safe-to-run migration
# into one that needs a decision, which is exactly the distinction migration
# 2 already drew between "backfill an identity" (always safe) and "merge two
# rows" (`hescope dedupe-slides`, a separate consented command).


@dataclass(frozen=True)
class _RoiBackfill:
    """One ``rois`` row's worth of what migration 4 will write, computed once
    and shared by :func:`_migration_4_apply` (which writes it) and
    :func:`plan_migration_4` (which only counts it) -- the same
    non-drifting-preview contract as :class:`_SlideBackfill` (migration 2)
    and :class:`_TcgaLinkCandidate` (migration 3): see their docstrings for
    why one shared computation, not two independent ones, is the point."""

    roi_id: int
    slide_id: int
    geom_key: str
    created_by: str | None  # current (pre-migration) value; None -> needs backfill


def _rois_has_created_by_column(conn: sa.Connection) -> bool:
    """Whether ``rois`` already carries migration 4's ``created_by`` column.

    ``False`` on a database migration 4 has never touched -- exactly the
    shape ``migrate --dry-run`` must be able to preview (R-1: it never calls
    ``init_db`` -- see :func:`migrate`'s docstring), and precisely the shape
    of the REAL shipped ``data/hescope.db`` today (it has migrations 1-3's
    columns from ``init_db``'s ordinary bootstrap, but ``migrate()`` itself
    has never run against it, so ``schema_migrations`` -- and migration 4's
    two new ``rois`` columns -- do not exist yet). ``_compute_roi_backfills``
    used to ``SELECT ... created_by`` unconditionally, so
    ``plan_migration_4`` crashed with ``OperationalError: no such column:
    created_by`` the moment it was run against a COPY of the real database
    rather than one of this test file's fixtures (every fixture here calls
    ``init_db`` first, which the real database's own bootstrap already does
    too -- but ``init_db`` alone does not add migration 4's columns; only
    ``migrate()``, which fixtures also call, does). The real (non-dry-run)
    apply path never sees ``False`` here: ``migrate()`` calls ``init_db``
    before any migration's ``apply`` runs, so by the time
    :func:`_migration_4_apply` calls this the columns already exist -- the
    same ``identity_ready`` split migration 3's
    ``_slides_identity_columns_exist`` already uses for the identical
    reason, one migration over.
    """
    if not sa.inspect(conn).has_table("rois"):
        return False
    cols = {c["name"] for c in sa.inspect(conn).get_columns("rois")}
    return "created_by" in cols


def _compute_roi_backfills(conn: sa.Connection) -> list[_RoiBackfill]:
    if not sa.inspect(conn).has_table("rois"):
        # A dry run may preview an EMPTY database, the same reason
        # `_compute_slide_backfills` guards this identically -- see that
        # function's docstring.
        return []
    # Local import: this module must not depend on hescope.store.db at
    # module level (see migrate()'s docstring on the import cycle that
    # avoids). geom_key() is the SAME function ROIRepo.add uses for every
    # NEW row (hescope.store.db.geom_key's docstring), imported here rather
    # than re-implemented so a row written by ROIRepo.add and an identical
    # one recovered by this backfill are guaranteed to collide, not merely
    # intended to.
    from .db import geom_key as _geom_key

    has_created_by = _rois_has_created_by_column(conn)
    select_cols = "id, slide_id, kind, points_json" + (
        ", created_by" if has_created_by else ""
    )
    rows = conn.execute(sa.text(f"SELECT {select_cols} FROM rois")).all()
    out: list[_RoiBackfill] = []
    for row in rows:
        roi_id, slide_id, kind, points_json = row[0], row[1], row[2], row[3]
        created_by = row[4] if has_created_by else None
        try:
            raw_points = json.loads(points_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_points = []
        points = [(p[0], p[1]) for p in raw_points]
        out.append(
            _RoiBackfill(roi_id, slide_id, _geom_key(kind, points), created_by)
        )
    return out


def _duplicate_geom_key_groups(
    backfills: list[_RoiBackfill],
) -> dict[tuple[int, str], list[int]]:
    """``(slide_id, geom_key) -> [roi_id, ...]`` for every group of 2+ ROIs
    that would collide under ``docs/DATABASE-DESIGN.md``'s prototype
    ``UNIQUE (slide_id, geom_key)`` -- computed so it can be REPORTED, per
    that doc's own instruction ("report the 2 duplicate groups rather than
    silently dropping them"); never enforced as an actual constraint by this
    migration (see :func:`hescope.store.db.geom_key`'s docstring for why)."""
    groups: dict[tuple[int, str], list[int]] = {}
    for b in backfills:
        groups.setdefault((b.slide_id, b.geom_key), []).append(b.roi_id)
    return {key: ids for key, ids in groups.items() if len(ids) > 1}


def plan_migration_4(conn: sa.Connection) -> dict:
    """What migration 4's ``rois`` backfill WOULD write, computed read-only.

    Returns ``{"rois": N, "created_by_backfilled": N,
    "duplicate_geom_key_groups": K, "duplicate_geom_key_rois": M}``. Powers
    ``hescope migrate --dry-run``'s report the same way
    :func:`plan_migration_2` / :func:`plan_migration_3` do -- shares
    :func:`_compute_roi_backfills` with :func:`_migration_4_apply` so the
    preview cannot say something different from what running for real would
    do. Every resolving row gets a ``geom_key`` (it is a pure function of
    data already on the row, never a decision), so there is no
    ``geom_key_backfilled`` count separate from ``rois`` -- but
    ``duplicate_geom_key_groups``/``duplicate_geom_key_rois`` surface the
    blast radius a future UNIQUE-enforcing migration would need to resolve
    (see this module's migration-4 section docstring).
    """
    backfills = _compute_roi_backfills(conn)
    dup_groups = _duplicate_geom_key_groups(backfills)
    return {
        "rois": len(backfills),
        "created_by_backfilled": sum(1 for b in backfills if not b.created_by),
        "duplicate_geom_key_groups": len(dup_groups),
        "duplicate_geom_key_rois": sum(len(ids) for ids in dup_groups.values()),
    }


def _migration_4_apply(conn: sa.Connection) -> None:
    """Backfill ``rois.geom_key`` (every row) and ``rois.created_by``
    (rows where it is still NULL, to 'user' -- see this module's migration-4
    section docstring for why 'user' and not something more specific: every
    ROW predates the column existing at all, i.e. predates any code path
    that could have written 'agent' or 'import', so 'user' is not a guess,
    it is the only value that was ever true for these rows).

    One UPDATE per row (``COALESCE`` leaves an already-non-NULL
    ``created_by`` alone -- relevant only if this ever runs twice on a
    database where something else set it between calls; ordinary
    idempotency is already handled by ``migrate`` never re-running an
    applied version, see :func:`migrate`'s docstring).
    """
    for b in _compute_roi_backfills(conn):
        conn.execute(
            sa.text(
                "UPDATE rois SET geom_key=:gk, "
                "created_by=COALESCE(created_by, 'user') WHERE id=:id"
            ),
            {"gk": b.geom_key, "id": b.roi_id},
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
    Migration(
        version=3,
        name="TCGA download -> storage -> injection: a real FK on tcga_files.slide_id, and the backfill",
        apply=_migration_3_apply,
    ),
    Migration(
        version=4,
        name="L5 measurement layers (layers, selection_resolutions) and measurements; "
             "backfill rois.geom_key and rois.created_by",
        apply=_migration_4_apply,
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

    Calls ``hescope.store.db.init_db(engine)`` AND ``hescope.gdc.tcga_schema
    .init_tcga_schema(engine)`` first (unless ``dry_run``) so this function
    is self-contained: migration 2's DDL (``slide_files``, the identity
    columns/index, the bbox columns) is declared on the core ORM models and
    created by ``init_db``'s additive ``create_all`` + ``ALTER TABLE`` path,
    not by this module (see ``_migration_2_apply``'s docstring) -- calling
    ``migrate()`` without an ``init_db`` first used to raise
    ``sqlite3.OperationalError: no such table: slide_files`` and leave the
    version stuck at 1, on any legacy-schema database (measured; see
    ``tests/test_migrations.py::test_migrate_is_self_contained_...``).
    Migration 3 needs the same guarantee for ``tcga_files`` (normally
    created lazily, only when something instantiates a ``TcgaCatalog`` --
    ``init_tcga_schema`` here means ``migrate()`` alone, with no TCGA code
    ever having run against this database, still reaches SCHEMA_VERSION;
    see ``tests/test_migrations.py::test_migrate_reaches_schema_version_with_no_prior_tcga_tables``).
    Both are idempotent and additive-only (R-4), so calling them here even
    when ``hescope.cli`` has already called ``init_db`` once is a no-op, not
    a double-write. Imported locally (not at module top) to avoid a
    module-level import cycle with ``hescope.store.db`` / ``hescope.gdc.tcga_schema``.

    A migration that raises rolls back ONLY that migration's transaction
    (``schema_migrations`` included, since the table is created inside the
    same transaction the first time it is needed) and stops the run —
    migrations already committed before it stay committed, and the ones
    after it are reported in ``skipped``, never attempted.

    ``dry_run=True`` never calls ``init_db`` or ``init_tcga_schema``, never
    opens a write transaction, never calls any migration's ``apply``, and
    never touches ``schema_migrations`` — it only reads the current version
    and computes what running for real would do. Safe to call against a
    database this process must never write to (R-1): point it at a copy.
    (The CLI's ``--dry-run`` path additionally previews what ``init_db``
    itself would do, via ``hescope.store.db.plan_init_db`` against a read-only
    engine — see ``hescope.cli._cmd_migrate`` — since this function's own
    dry run does not touch ``init_db`` at all; migration 3's own preview,
    :func:`plan_migration_3`, reports ``tcga_files_exists`` for the
    equivalent gap on the TCGA side.)
    """
    if not dry_run:
        from .db import init_db
        from ..gdc.tcga_schema import init_tcga_schema

        init_db(engine)
        init_tcga_schema(engine)

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
