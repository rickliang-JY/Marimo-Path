"""Unified database layer for HE-Scope (Phase 3, Part A).

SQLAlchemy 2.0 engine/configuration, typed declarative schema (slides, rois,
agent_runs, interactions) and repository classes used by the app, the CLI and
the agent bridge. Defaults to a SQLite file under ``<project_root>/data/hescope.db``;
any SQLAlchemy URL works (postgres://, mysql+pymysql://, ...).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any, Iterator, Literal, NamedTuple

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, String, Text, event, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

if TYPE_CHECKING:  # avoid a hard runtime dependency cycle with hescope.rois
    from .rois import ROI as ROIGeometry

from .identity import slide_identity
from .paths import resolve_runtime_dir

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Writable runtime root: the repo root for checkouts/editable installs,
# <cwd>/hescope_runtime for non-editable installs (never site-packages).
_RUNTIME_ROOT = resolve_runtime_dir(_PROJECT_ROOT)
DEFAULT_DB_URL = f"sqlite:///{_RUNTIME_ROOT / 'data' / 'hescope.db'}"

_LOG = logging.getLogger(__name__)

#: How long a sqlite connection waits for a lock before raising "database is
#: locked". The measured failure this prevents took 5.5 s to surface, so the
#: window has to be wider than a slow write, not wider than a human's patience.
SQLITE_BUSY_TIMEOUT_MS = 10_000


def _utcnow() -> datetime:
    """Current UTC time (stored naive; always interpreted as UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt: datetime | None) -> str | None:
    """ISO8601 string; naive datetimes are interpreted as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def get_engine(url: str | None = None, *, read_only: bool = False) -> sa.Engine:
    """Resolve the database URL and create an engine.

    Resolution order: explicit ``url`` argument -> ``HESCOPE_DB_URL``
    environment variable -> ``DEFAULT_DB_URL``. For sqlite URLs, parent
    directories of the database file are created as needed.

    ``read_only=True`` skips the one pragma below that persists a header
    change to the FILE (``journal_mode=WAL``); ``busy_timeout`` and
    ``foreign_keys`` are per-connection settings that never touch the file on
    disk, so they are still applied. Use this for any code path that promises
    not to write -- a plain ``get_engine(url)`` silently flips a non-WAL
    database to WAL the moment anything (even a read) connects through it.
    Measured: ``hescope migrate --dry-run`` against a copy of
    ``data/hescope.db`` forced to ``journal_mode=DELETE`` changed the file's
    sha256 and left it in ``journal_mode=WAL``, while printing "nothing was
    changed" -- exactly the guarantee R-1 asks a dry run to keep.
    """
    resolved = url or os.environ.get("HESCOPE_DB_URL") or DEFAULT_DB_URL
    sa_url = sa.engine.make_url(resolved)
    if sa_url.get_backend_name() == "sqlite":
        db_path = sa_url.database
        if db_path and db_path != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(resolved)
    if sa_url.get_backend_name() == "sqlite":
        _file_backed = bool(db_path) and db_path != ":memory:"

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            # pysqlite's default (legacy) isolation-level handling silently
            # COMMITs any open transaction before a DDL statement (CREATE /
            # ALTER / DROP) and never wraps DDL in one to begin with, so
            # "each migration runs in its own transaction; a failure rolls
            # it back" (hescope/migrations.py) was false for any migration
            # that touches the schema -- measured: a CREATE TABLE issued
            # inside `engine.begin()` survived a subsequent exception and
            # rollback. Setting isolation_level=None hands transaction
            # control entirely to SQLAlchemy (paired with the "begin" hook
            # below, which then issues the actual BEGIN), which is SQLite's
            # documented way to make DDL participate in transactions like
            # everything else.
            dbapi_conn.isolation_level = None
            cursor = dbapi_conn.cursor()
            try:
                # Enforce foreign keys (ON DELETE CASCADE / SET NULL).
                cursor.execute("PRAGMA foreign_keys=ON")
                # Wait for a lock instead of failing instantly. Without this a
                # write raises "database is locked" the moment any other
                # connection holds a read -- and this app's normal shape is two
                # readers (the notebook and an agent over marimo-pair) around
                # one writer.
                cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
                if _file_backed and not read_only:
                    # WAL lets readers and the writer proceed concurrently.
                    # Measured on this database: an ROI save under the default
                    # `delete` journal fails after 5.5 s with "database is
                    # locked" while a reader is open; under WAL the same write
                    # completes in 0.00 s.
                    cursor.execute("PRAGMA journal_mode=WAL")
                    row = cursor.fetchone()
                    mode = (row[0] if row else "") or ""
                    if mode.lower() != "wal":
                        # WAL is unavailable on some network filesystems. Say so
                        # -- a silent fall back to `delete` reintroduces exactly
                        # the failure this exists to prevent.
                        _LOG.warning(
                            "SQLite journal_mode is %r, not WAL: concurrent "
                            "readers can make a write fail with 'database is "
                            "locked'. Common cause: the database is on a "
                            "network filesystem.",
                            mode,
                        )
            finally:
                cursor.close()

        @event.listens_for(engine, "begin")
        def _sqlite_begin(conn: sa.Connection) -> None:
            # DEFERRED by default, IMMEDIATE only when the caller asked for a
            # write transaction (`write_session`). Making IMMEDIATE global was
            # measured to be worse than the problem it solved: with one
            # SQLAlchemy reader holding a transaction, a SECOND READER failed
            # after 2266 ms with "database is locked", because IMMEDIATE takes
            # the write lock for every transaction including read-only ones.
            # Under a deferred BEGIN the same reader succeeds in 2 ms. Two
            # concurrent readers are this app's normal shape -- the notebook
            # and an agent over marimo-pair -- so that trade was backwards.
            if conn.info.get("hescope_write"):
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                return
            conn.exec_driver_sql("BEGIN")


    return engine


def sqlite_pragma_report(engine: sa.Engine) -> dict[str, Any]:
    """The three connection pragmas that govern concurrency, read back.

    For a status panel and for tests. Returns ``{}`` for non-sqlite engines;
    never raises -- a diagnostic must not be the thing that breaks.
    """
    if engine.url.get_backend_name() != "sqlite":
        return {}
    try:
        with engine.connect() as conn:
            return {
                "foreign_keys": bool(
                    conn.execute(sa.text("PRAGMA foreign_keys")).scalar()
                ),
                "journal_mode": str(
                    conn.execute(sa.text("PRAGMA journal_mode")).scalar()
                ).lower(),
                "busy_timeout": int(
                    conn.execute(sa.text("PRAGMA busy_timeout")).scalar() or 0
                ),
            }
    except Exception as exc:  # pragma: no cover - diagnostic only
        return {"error": str(exc)}


@contextmanager
def write_session(engine: sa.Engine) -> Iterator[Session]:
    """A Session whose transaction takes SQLite's write lock UP FRONT.

    Read-then-write methods need this. Under WAL a deferred ``BEGIN`` pins the
    read at a snapshot on the first SELECT, and the later write in the same
    transaction has to UPGRADE that snapshot -- which fails immediately with
    SQLITE_BUSY as soon as another connection has committed since, and does not
    honour ``busy_timeout``, because a snapshot upgrade is not a wait-for-lock.
    Measured: 8 threads calling ``update_annotation`` on 8 different ROIs, 6 of
    8 raised "database is locked" under a deferred BEGIN, 0 of 8 under
    IMMEDIATE.

    It is a per-call opt-in and must never become the global default: with a
    reader holding a transaction, a global IMMEDIATE made a SECOND READER fail
    after 2266 ms, against 2 ms under a deferred BEGIN. Two concurrent readers
    are this application's normal shape.

    A no-op on non-sqlite backends, where the flag is simply unread.
    """
    with engine.connect() as conn:
        conn.info["hescope_write"] = True
        try:
            with Session(bind=conn) as session:
                yield session
        finally:
            # MUST be cleared. `Connection.info` lives on the POOLED dbapi
            # connection, so without this the flag survives return-to-pool and
            # the next reader to be handed that connection silently gets
            # BEGIN IMMEDIATE -- reintroducing the reader-blocks-reader failure
            # this helper exists to avoid, intermittently and only under pool
            # reuse, which is the worst way for it to come back.
            conn.info.pop("hescope_write", None)


def normalize_slide_path(path: str | Path) -> str:
    """Canonical form of a slide path — the key ``slides.path`` is stored under.

    ``slides.path`` is UNIQUE and is the only thing tying saved ROIs to a file,
    but the callers spell it differently: app.py passes the raw string from the
    sidebar text box, ``hescope.cli`` passes an already-resolved path. Without
    one shared normalization the SAME file opened as ``E:\\x\\s.svs``,
    ``e:/x/s.svs`` or ``assets/s.svs`` becomes three slide rows, and each row
    sees only its own annotations — ``query_annotations()`` and
    ``get_slide_info()`` then report an empty slide with no error. Normalizing
    inside the repository rather than at the call sites means a future caller
    cannot reintroduce the split by forgetting one (the same reasoning that put
    ``_contained_name`` inside ``_filename_from_headers``).

    Falls back to the input string if the path cannot be resolved (an
    unreachable network share, a name the OS rejects): a slightly worse key
    beats refusing to register the slide.

    Defect 1.3: on Windows, a POSIX-absolute path that has no drive of its
    own (``/mnt/nfs/slides/A1.svs`` -- the spelling a Linux-mounted network
    share, or a path recorded by a workstation of a different OS, would use)
    is not actually "absolute" to ``pathlib.PureWindowsPath`` (it has a root
    but no drive), so ``.resolve()`` silently attaches THIS PROCESS's
    current drive: measured, ``Path('/mnt/nfs/slides/A1.svs').resolve()`` on
    this machine returns ``E:\\mnt\\nfs\\slides\\A1.svs``. Two workstations
    on the same shared store then normalize the SAME file to two different
    keys, splitting the store one slide per workstation -- silently, since
    both keys are individually well-formed. Detected below and kept as the
    original string: unresolved-but-self-consistent beats "resolved" but
    reachable from nowhere else.
    """
    raw = str(path)
    try:
        resolved = str(Path(path).expanduser().resolve())
    except (OSError, ValueError, RuntimeError):
        return raw
    if os.name == "nt":
        raw_win = PureWindowsPath(raw)
        resolved_win = PureWindowsPath(resolved)
        if not raw_win.drive and raw_win.root and resolved_win.drive:
            # a rooted-but-driveless spelling that resolve() turned into a
            # drive-qualified one -- exactly the foreign-path case above.
            return raw
    return resolved


def plan_init_db(engine: sa.Engine, conn: sa.Connection | None = None) -> dict:
    """What :func:`init_db` WOULD do, computed without executing anything.

    Read-only: uses ``sa.inspect`` only, never opens a write transaction.
    Returns ``{"new_tables": [name, ...], "new_table_indexes": {name:
    [index_name, ...]}, "alter_statements": [sql, ...],
    "create_index_statements": [sql, ...]}`` -- ``new_tables`` lists tables
    ``create_all`` would create (with all of their own columns and indexes
    in one ``CREATE TABLE``, so those are not re-listed as separate ALTER /
    CREATE INDEX statements in the other two lists); ``new_table_indexes``
    names the indexes each new table would carry (informational only --
    ``CREATE TABLE`` creates them, nothing here executes a statement for
    them) so a report naming "every object this would create" does not have
    to omit the indexes on a table that does not exist yet.
    ``alter_statements``/``create_index_statements`` are the exact
    ``ALTER TABLE`` / ``CREATE INDEX`` SQL :func:`init_db` runs on tables
    that already exist but are missing a column or index declared on the
    current ORM models.

    ``init_db`` calls this function too (passing the connection it is about
    to write through) and executes exactly the statements it returns, so the
    two can no longer drift: before this function existed, ``migrate
    --dry-run`` computed its own separate (and wrong) idea of "nothing to do"
    while the real ``migrate`` command called ``init_db`` and, on a database
    from before ``interactions``/``agent_runs`` existed, actually created 2
    tables, added 3 columns to ``rois`` and created 8 indexes -- all silently,
    because the dry run never looked at ``init_db`` at all. Measured with a
    narrow (old-schema) scratch database carrying 1 slide + 1 roi:
    ``migrate --dry-run`` printed only "would apply migration 1 ... (stamp
    only, no schema change)" / "nothing was changed", while the real
    ``migrate`` on an identical copy added tables
    ``['agent_runs', 'interactions']``, indexes
    ``['ix_agent_runs_roi_id', 'ix_interactions_kind', 'ix_interactions_roi_id',
    'ix_interactions_session_tag', 'ix_interactions_slide_id', 'ix_rois_label',
    'ix_rois_slide_id', 'ix_slides_source_kind']`` and columns
    ``['patch_path', 'stats_json', 'magnification']`` on ``rois``.
    """
    inspector = sa.inspect(conn) if conn is not None else sa.inspect(engine)
    existing_tables = set(inspector.get_table_names())
    new_tables: list[str] = []
    new_table_indexes: dict[str, list[str]] = {}
    alter_statements: list[str] = []
    create_index_statements: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            new_tables.append(table.name)
            if table.indexes:
                new_table_indexes[table.name] = sorted(ix.name for ix in table.indexes)
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_cols:
                continue
            # Added without NOT NULL on purpose: existing rows have no
            # value for it and SQLite rejects ADD COLUMN NOT NULL without
            # a default.
            type_sql = column.type.compile(engine.dialect)
            alter_statements.append(
                f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {type_sql}'
            )
        existing_indexes = {ix["name"] for ix in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name in existing_indexes:
                continue
            create_index_statements.append(
                str(sa.schema.CreateIndex(index).compile(dialect=engine.dialect)).strip()
            )
    return {
        "new_tables": new_tables,
        "new_table_indexes": new_table_indexes,
        "alter_statements": alter_statements,
        "create_index_statements": create_index_statements,
    }


def init_db(engine: sa.Engine) -> None:
    """Create all tables, then add any columns OR indexes an older database
    is missing.

    ``create_all`` adds missing TABLES but never missing COLUMNS, so a database
    written by a build with a narrower schema (branch switching, a user
    upgrading hescope) came up with ``db.enabled = True`` and then failed every
    ROI write with "table rois has no column named ...". The additive
    ``PRAGMA table_info`` + ``ALTER TABLE`` upgrade below is the same one
    ``SlideCatalog`` already does for its md5sum column; there is no migration
    framework here, so it is additive only — never a drop, a rename or a type
    change. A failure propagates to ``bootstrap_db``, which degrades to DB-free
    mode: an honest "database disabled" beats a live-looking panel whose every
    save answers "Submit failed".

    ``create_all`` also only emits ``CREATE INDEX`` for a table it just
    created — a table that already existed (same branch-switch / upgrade
    scenario) keeps whatever indexes it started with, silently. Measured: a
    ``rois`` table built through this function's own narrow-schema upgrade
    path ends with 0 indexes where a fresh ``create_all`` gives it 2
    (``ix_rois_slide_id``, ``ix_rois_label``) — both report ``version 0``,
    but the upgraded one lacks indexes ``ROIRepo.for_slide`` and
    ``ROIRepo.search`` depend on for anything beyond a table scan. Fixed the
    same way as the column gap: compare declared indexes to what actually
    exists and create only what is missing.

    The ALTER/CREATE INDEX diff itself is computed by :func:`plan_init_db`
    (passed the SAME connection these statements execute on -- see that
    function's docstring for why this matters and for the dry-run report
    that also depends on it).
    """
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # Inspect the SAME connection the ALTER/CREATE INDEX statements run
        # on, not a second one opened fresh off `engine` -- an engine-bound
        # inspector opens its own connection per call, and for a
        # ``:memory:`` database (SingletonThreadPool: one physical
        # connection shared engine-wide) that collided with the transaction
        # this block already holds open ("cannot start a transaction within
        # a transaction") once DDL here became genuinely transactional.
        plan = plan_init_db(engine, conn)
        for stmt in plan["alter_statements"]:
            conn.execute(sa.text(stmt))
        # Indexes run after ALTERs: an index on a column added above must
        # wait for that column to exist, and since both statement lists came
        # from the SAME plan (computed before either ran), executing ALTERs
        # first then indexes is enough -- no need to recompute the plan.
        for stmt in plan["create_index_statements"]:
            conn.execute(sa.text(stmt))


class Base(DeclarativeBase):
    pass


class Slide(Base):
    __tablename__ = "slides"
    __table_args__ = (
        # Partial: many rows may carry identity_scheme IS NULL (a legacy row
        # never enriched, or a path that never resolved during migration 2's
        # backfill) and those must NOT collide with each other -- only rows
        # that both carry a real scheme are compared for uniqueness. SQLite
        # (and Postgres) support a partial unique index directly; this is
        # the one construct in this schema that is sqlite-specific pending
        # Phase 3's backend-portability audit.
        Index(
            "ux_slides_identity",
            "identity_scheme",
            "identity_key",
            unique=True,
            sqlite_where=sa.text("identity_scheme IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(512))
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    width: Mapped[int] = mapped_column()
    height: Mapped[int] = mapped_column()
    mpp: Mapped[float | None] = mapped_column(nullable=True)
    extra_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    # Identity that does not assume a local file (see hescope.identity).
    # ``path`` above stays and stays UNIQUE (R-4); it is now a cache of the
    # most-recently-seen location, and ``slide_files`` is the durable record
    # of every location this slide has been seen at.
    identity_scheme: Mapped[str | None] = mapped_column(String(32), nullable=True)
    identity_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(nullable=True)
    md5sum: Mapped[str | None] = mapped_column(String(32), nullable=True)

    rois: Mapped[list[ROI]] = relationship(
        back_populates="slide", cascade="all, delete-orphan", passive_deletes=True
    )
    files: Mapped[list["SlideFile"]] = relationship(
        back_populates="slide", cascade="all, delete-orphan", passive_deletes=True
    )


class SlideFile(Base):
    """One location a slide has been seen at. Many rows, one ``slide_id`` --
    the same content opened from a mapped drive, a UNC path and a symlink is
    ONE slide with three of these, not three slides (defect 1.1)."""

    __tablename__ = "slide_files"
    __table_args__ = (Index("ix_slide_files_slide", "slide_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slide_id: Mapped[int] = mapped_column(
        ForeignKey("slides.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    source_kind: Mapped[str] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column()
    last_seen_at: Mapped[datetime] = mapped_column()
    missing_since: Mapped[datetime | None] = mapped_column(nullable=True)

    slide: Mapped[Slide] = relationship(back_populates="files")


class ROI(Base):
    __tablename__ = "rois"
    __table_args__ = (
        # Powers ROIRepo.in_viewport: without this, "ROIs on screen" cannot
        # be expressed in SQL at all while bbox lived only in bbox_json TEXT
        # (defect 1.4) -- for_slide had to return every row and filter in
        # Python, 138ms + 170KB at 5000 ROIs on every overlay re-render.
        Index("ix_rois_slide_bbox", "slide_id", "bbox_x0", "bbox_x1"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slide_id: Mapped[int] = mapped_column(
        ForeignKey("slides.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    points_json: Mapped[str] = mapped_column(Text)
    bbox_json: Mapped[str] = mapped_column(Text)  # "[x0,y0,x1,y1]"
    label: Mapped[str] = mapped_column(String(512), default="", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    patch_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    stats_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    magnification: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    # A bbox the database can filter on -- bbox_json stays (R-4) as the
    # exact-shape source; these four are a derived, queryable cache of it,
    # written by the one place that writes bbox_json (ROIRepo.add / the
    # migration-2 backfill), never independently.
    bbox_x0: Mapped[float | None] = mapped_column(nullable=True)
    bbox_y0: Mapped[float | None] = mapped_column(nullable=True)
    bbox_x1: Mapped[float | None] = mapped_column(nullable=True)
    bbox_y1: Mapped[float | None] = mapped_column(nullable=True)

    slide: Mapped[Slide] = relationship(back_populates="rois")
    agent_runs: Mapped[list[AgentRun]] = relationship(back_populates="roi")


INTERACTION_KINDS = (
    "selection_view",
    "roi_submit",
    "label_set",
    "roi_delete",
    "analysis_run",
    "tool_call",
    # reserved: no human-gate UI exists yet, so nothing writes this kind
    "human_gate",
)


class Interaction(Base):
    """Interaction trace row (v1): one row per user/agent interaction.

    Feeds the data flywheel / automation-bias research: every selection view,
    ROI submission, label write-back, analysis run, tool call and human gate
    decision lands here with an arbitrary JSON payload.
    """

    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_tag: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    slide_id: Mapped[int | None] = mapped_column(
        ForeignKey("slides.id", ondelete="SET NULL"), nullable=True, index=True
    )
    roi_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON text
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    roi_id: Mapped[int | None] = mapped_column(
        ForeignKey("rois.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool: Mapped[str] = mapped_column(String(128))
    input_json: Mapped[str] = mapped_column(Text)
    output_text: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ok")  # "ok" | "error"
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    roi: Mapped[ROI | None] = relationship(back_populates="agent_runs")


def _slide_dict(slide: Slide) -> dict:
    return {
        "id": slide.id,
        "source_kind": slide.source_kind,
        "name": slide.name,
        "path": slide.path,
        "width": slide.width,
        "height": slide.height,
        "mpp": slide.mpp,
        "extra_json": slide.extra_json,
        "created_at": _iso(slide.created_at),
        "identity_scheme": slide.identity_scheme,
        "identity_key": slide.identity_key,
        "file_size": slide.file_size,
        "md5sum": slide.md5sum,
    }


def _slide_file_dict(f: SlideFile) -> dict:
    return {
        "id": f.id,
        "slide_id": f.slide_id,
        "path": f.path,
        "source_kind": f.source_kind,
        "first_seen_at": _iso(f.first_seen_at),
        "last_seen_at": _iso(f.last_seen_at),
        "missing_since": _iso(f.missing_since),
    }


def _roi_dict(roi: ROI) -> dict:
    return {
        "id": roi.id,
        "slide_id": roi.slide_id,
        "kind": roi.kind,
        "points_json": roi.points_json,
        "bbox_json": roi.bbox_json,
        "bbox": [int(v) for v in json.loads(roi.bbox_json)],
        "bbox_x0": roi.bbox_x0,
        "bbox_y0": roi.bbox_y0,
        "bbox_x1": roi.bbox_x1,
        "bbox_y1": roi.bbox_y1,
        "label": roi.label,
        "notes": roi.notes,
        "patch_path": roi.patch_path,
        "stats_json": roi.stats_json,
        "magnification": roi.magnification,
        "created_at": _iso(roi.created_at),
    }


def _interaction_dict(rec: Interaction) -> dict:
    return {
        "id": rec.id,
        "session_tag": rec.session_tag,
        "kind": rec.kind,
        "slide_id": rec.slide_id,
        "roi_id": rec.roi_id,
        "payload": rec.payload,
        "created_at": _iso(rec.created_at),
    }


def _agent_run_dict(run: AgentRun) -> dict:
    return {
        "id": run.id,
        "roi_id": run.roi_id,
        "tool": run.tool,
        "input_json": run.input_json,
        "output_text": run.output_text,
        "model": run.model,
        "status": run.status,
        "created_at": _iso(run.created_at),
    }


class RegisterResult(NamedTuple):
    """What :meth:`SlideRepo.register` did, when called with ``report=True``.

    ``inserted`` is ``True`` for a brand-new slide row and ``False`` when
    this call folded into an existing row instead -- the same identity seen
    from a new path, or a legacy row already sitting at ``path``. Added for
    ``hescope ingest``, which used to count every call as a fresh
    registration: two byte-identical files in one directory printed "2
    registered" while only 1 row existed afterward, and the survivor's
    name/path silently became whichever file was seen last (BUILD-PLAN-DB.md
    Phase 1 debugger round 2, "hescope ingest reports 2 registrations for 1
    row").
    """

    id: int
    inserted: bool


class SlideRepo:
    """Repository for the slides table."""

    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine

    def register(
        self,
        *,
        source_kind: str,
        name: str,
        path: str,
        width: int,
        height: int,
        mpp: float | None = None,
        extra: dict | None = None,
        identity: tuple[str, str] | None = None,
        md5sum: str | None = None,
        report: bool = False,
    ) -> int | RegisterResult:
        """Register a slide. Idempotent on identity, not on ``path``.

        ``path`` is canonicalized (see ``normalize_slide_path``). Identity
        resolves in this order: the ``identity`` argument, if given (pass
        this for a slide with no readable local path yet, e.g. one opened
        from a DICOMweb endpoint -- see ``hescope.identity.slide_identity``);
        otherwise a content hash of ``path`` if it is readable
        (``('sha256', ...)``); otherwise ``('path', path)`` as a last resort.
        Re-registering the SAME identity from a NEW path updates the
        existing row (``path`` becomes a cache of the most-recently-seen
        location) rather than creating a second one -- this is what fixes
        defect 1.1 (one file at three paths was three slide rows). It also
        upserts the corresponding ``slide_files`` row (see
        :meth:`_touch_slide_file`).

        ``extra`` is only written when given: omitting the argument is not a
        request to clear the column, and both production callers omit it on
        every slide open.

        ``md5sum`` (BUILD-PLAN-DB.md Phase 2): the GDC-supplied checksum for
        a TCGA download, stored on ``slides.md5sum`` -- distinct from
        ``identity_key``, which is this project's own head/tail content hash
        (:func:`hescope.identity.content_key`), not the GDC md5. Same
        "omitting is not clearing" contract as ``extra``: a plain local open
        (no md5 available) must not blank out a value a download already
        recorded.

        ``report=True`` returns a :class:`RegisterResult` (``id``,
        ``inserted``) instead of a bare ``id`` -- pass it when the caller
        needs to know whether this call created a new row or folded into an
        existing one (``hescope ingest`` does, to report "N registered, M
        already known"). Every existing caller omits it and keeps getting a
        bare ``int``, unchanged.

        Race fix (defect 1.2): this used to be SELECT-by-path, then
        INSERT-or-UPDATE in Python -- two concurrent callers could both see
        "no row" from the SELECT and both attempt the INSERT; the loser
        raised ``IntegrityError`` instead of folding into an UPDATE.
        Measured against a bare (deferred) ``BEGIN`` -- the transaction
        semantics this project used before ``hescope.db.get_engine``'s
        ``BEGIN IMMEDIATE`` hook -- 8 concurrent registrations of ONE slide
        raised ``IntegrityError`` between 2 and 7 times across repeated
        runs (``tests/test_register_race.py`` reproduces this as a control).
        Fixed here with a single ``INSERT ... ON CONFLICT DO UPDATE`` keyed
        on the identity index, re-selected by identity afterward; a second
        attempt keyed on ``path`` handles the legacy case of an
        identity-less row already sitting at this exact path (there is
        nothing for the identity-keyed upsert to conflict with in that
        case, since a NULL ``identity_scheme`` never participates in the
        partial unique index, so it would otherwise collide on ``path``
        instead and raise).

        Duplicate content, no merge (defect: "register() raises
        IntegrityError on duplicate-content slides"): the path-keyed
        fallback above used to assign the incoming identity to the legacy
        row unconditionally. When that identity already belongs to a
        DIFFERENT row -- two byte-identical files registered from two
        paths before either had a computed identity, which is exactly the
        state migration 2 leaves a duplicate-content group in, since it
        deliberately does not merge them -- that UPDATE collides with
        ``ux_slides_identity`` too, and the ``IntegrityError`` used to
        propagate out of ``register`` uncaught (measured on the real
        database: 3 of 5 rows in its two duplicate-content groups). Fixed
        by checking, inside the same locked transaction, whether the
        identity is already owned by a different row; if so this row's
        identity is left NULL instead -- the same non-merging policy
        migration 2 already uses -- and registration still succeeds.
        """
        path = normalize_slide_path(path)
        scheme, key = identity or slide_identity(source_kind, path=path) or ("path", path)
        file_size: int | None = None
        if scheme == "sha256":
            try:
                file_size = Path(path).stat().st_size
            except OSError:
                file_size = None
        now = _utcnow()
        values = dict(
            source_kind=source_kind,
            name=name,
            path=path,
            width=int(width),
            height=int(height),
            mpp=mpp,
            extra_json=json.dumps(extra) if extra is not None else "{}",
            created_at=now,
            identity_scheme=scheme,
            identity_key=key,
            file_size=file_size,
            md5sum=md5sum,
        )

        def _upsert_by_identity(s: Session) -> tuple[int, bool]:
            existing = s.execute(
                select(Slide.id).where(
                    Slide.identity_scheme == scheme, Slide.identity_key == key
                )
            ).scalar_one_or_none()
            ins = sqlite_insert(Slide).values(**values)
            set_ = {
                "source_kind": ins.excluded.source_kind,
                "name": ins.excluded.name,
                "path": ins.excluded.path,
                "width": ins.excluded.width,
                "height": ins.excluded.height,
                "mpp": ins.excluded.mpp,
                "file_size": ins.excluded.file_size,
            }
            if extra is not None:
                set_["extra_json"] = ins.excluded.extra_json
            if md5sum is not None:
                set_["md5sum"] = ins.excluded.md5sum
            stmt = ins.on_conflict_do_update(
                index_elements=[Slide.identity_scheme, Slide.identity_key],
                index_where=Slide.identity_scheme.is_not(None),
                set_=set_,
            )
            s.execute(stmt)
            slide_id = s.execute(
                select(Slide.id).where(
                    Slide.identity_scheme == scheme, Slide.identity_key == key
                )
            ).scalar_one()
            return slide_id, existing is None

        def _upsert_by_path(s: Session, *, assign_identity: bool) -> tuple[int, bool]:
            existing = s.execute(
                select(Slide.id).where(Slide.path == path)
            ).scalar_one_or_none()
            ins = sqlite_insert(Slide).values(**values)
            set_ = {
                "source_kind": ins.excluded.source_kind,
                "name": ins.excluded.name,
                "width": ins.excluded.width,
                "height": ins.excluded.height,
                "mpp": ins.excluded.mpp,
                "file_size": ins.excluded.file_size,
            }
            if assign_identity:
                set_["identity_scheme"] = ins.excluded.identity_scheme
                set_["identity_key"] = ins.excluded.identity_key
            if extra is not None:
                set_["extra_json"] = ins.excluded.extra_json
            if md5sum is not None:
                set_["md5sum"] = ins.excluded.md5sum
            stmt = ins.on_conflict_do_update(
                index_elements=[Slide.path], set_=set_
            )
            s.execute(stmt)
            slide_id = s.execute(
                select(Slide.id).where(Slide.path == path)
            ).scalar_one()
            return slide_id, existing is None

        try:
            with write_session(self.engine) as s:
                slide_id, inserted = _upsert_by_identity(s)
                self._touch_slide_file(s, slide_id, path, source_kind, now)
                s.commit()
                return RegisterResult(slide_id, inserted) if report else slide_id
        except sa.exc.IntegrityError:
            # The identity-keyed upsert found nothing to conflict with (this
            # exact identity is new) but the plain INSERT it fell back to
            # then collided on the `path` UNIQUE constraint -- a legacy row
            # with identity_scheme IS NULL already sits at this path. Fold
            # into THAT row instead of raising -- unless this identity is
            # already owned by a DIFFERENT row (duplicate content), in which
            # case assigning it here would raise the exact same error a
            # second time; leave this row's identity NULL instead. The
            # ownership check has to happen inside this transaction (not
            # before it, and not via a bare `Session`) or a second concurrent
            # caller could interleave between the check and the write, so
            # this branch uses `write_session` too (its docstring is exactly
            # this shape: a read that decides a write, needing the lock
            # up front rather than a mid-transaction upgrade).
            with write_session(self.engine) as s:
                owner_id = s.execute(
                    select(Slide.id).where(
                        Slide.identity_scheme == scheme, Slide.identity_key == key
                    )
                ).scalar_one_or_none()
                path_row_id = s.execute(
                    select(Slide.id).where(Slide.path == path)
                ).scalar_one_or_none()
                assign_identity = owner_id is None or owner_id == path_row_id
                slide_id, inserted = _upsert_by_path(s, assign_identity=assign_identity)
                self._touch_slide_file(s, slide_id, path, source_kind, now)
                s.commit()
                return RegisterResult(slide_id, inserted) if report else slide_id

    def _touch_slide_file(
        self, s: Session, slide_id: int, path: str, source_kind: str, now: datetime
    ) -> None:
        """Upsert the ``slide_files`` row for ``(slide_id, path)``.

        ``first_seen_at`` is set only by the INSERT branch and never touched
        again by the UPDATE branch -- re-registering a slide must not erase
        how long it has actually been known (R-3 is exactly about this class
        of bug: ``migrate-tcga-catalog`` once reset ``first_seen_at`` on
        every row it touched and five count-asserting tests missed it).
        ``last_seen_at`` always refreshes, and ``missing_since`` clears when
        the path resolves again.
        """
        try:
            exists = Path(path).is_file()
        except OSError:
            exists = False
        missing_since = None if exists else now
        ins = sqlite_insert(SlideFile).values(
            slide_id=slide_id,
            path=path,
            source_kind=source_kind,
            first_seen_at=now,
            last_seen_at=now,
            missing_since=missing_since,
        )
        stmt = ins.on_conflict_do_update(
            index_elements=[SlideFile.path],
            set_={
                "slide_id": ins.excluded.slide_id,
                "source_kind": ins.excluded.source_kind,
                "last_seen_at": ins.excluded.last_seen_at,
                "missing_since": ins.excluded.missing_since,
            },
        )
        s.execute(stmt)

    def files_for(self, slide_id: int) -> list[dict]:
        """Every location this slide has been seen at, oldest first."""
        with Session(self.engine) as s:
            stmt = (
                select(SlideFile)
                .where(SlideFile.slide_id == slide_id)
                .order_by(SlideFile.id)
            )
            return [_slide_file_dict(f) for f in s.execute(stmt).scalars()]

    def get(self, slide_id: int) -> dict | None:
        with Session(self.engine) as s:
            slide = s.get(Slide, slide_id)
            return _slide_dict(slide) if slide is not None else None

    def find_by_path(self, path: str) -> dict | None:
        """Look a slide up by file, under any spelling of its path."""
        path = normalize_slide_path(path)
        with Session(self.engine) as s:
            slide = s.execute(
                select(Slide).where(Slide.path == path)
            ).scalar_one_or_none()
            return _slide_dict(slide) if slide is not None else None

    def list(self, source_kind: str | None = None) -> list[dict]:
        with Session(self.engine) as s:
            stmt = select(Slide).order_by(Slide.id)
            if source_kind is not None:
                stmt = stmt.where(Slide.source_kind == source_kind)
            return [_slide_dict(slide) for slide in s.execute(stmt).scalars()]

    def delete(self, slide_id: int) -> None:
        """Delete a slide; its ROIs cascade-delete (and their agent_runs
        roi_id values are set to NULL)."""
        with write_session(self.engine) as s:
            slide = s.get(Slide, slide_id)
            if slide is not None:
                s.delete(slide)
                s.commit()


def plan_duplicate_slide_merge(engine: sa.Engine) -> dict:
    """What :func:`merge_duplicate_slide_paths` WOULD do. Writes nothing.

    ``dedupe-slides`` edits the artefact the whole app exists to protect — a
    user's annotations — irreversibly and in one shot, which is why the repair
    of the shipped ``data/hescope.db`` sat unmade for three review rounds: the
    tool was tested, but there was no way to see its blast radius first
    (R07-19). This is that view.

    Returns ``{"merges": [(deleted_id, kept_id, path), ...], "rewrites":
    [(slide_id, old_path, new_path), ...], "moved_rois": [(roi_id, from_slide,
    to_slide), ...]}``. Note ``rewrites`` lists only rows whose STORED value
    actually changes: ``merge_duplicate_slide_paths`` assigns the canonical
    path to every keeper, but for a row that was already canonical that is a
    no-op, and counting those overstates the change by an order of magnitude.
    """
    merges: list[tuple[int, str, str]] = []
    rewrites: list[tuple[int, str, str]] = []
    moved_rois: list[tuple[int, int, int]] = []
    with Session(engine) as s:
        groups: dict[str, list[Slide]] = {}
        for slide in s.execute(select(Slide).order_by(Slide.id)).scalars():
            groups.setdefault(normalize_slide_path(slide.path), []).append(slide)
        for canonical, rows in groups.items():
            keeper = rows[0]
            for dup in rows[1:]:
                merges.append((dup.id, keeper.id, canonical))
                for roi in s.execute(
                    select(ROI).where(ROI.slide_id == dup.id).order_by(ROI.id)
                ).scalars():
                    moved_rois.append((roi.id, dup.id, keeper.id))
            if keeper.path != canonical:
                rewrites.append((keeper.id, keeper.path, canonical))
    return {"merges": merges, "rewrites": rewrites, "moved_rois": moved_rois}


def merge_duplicate_slide_paths(engine: sa.Engine) -> list[tuple[int, int]]:
    """One-off repair for slide rows written before path normalization.

    ``SlideRepo.register`` now canonicalizes ``slides.path``, but a database
    filled in before that can already hold several rows for one file (the
    shipped ``data/hescope.db`` held ``assets\\demo_he.png`` and
    ``E:\\...\\assets\\demo_he.png``, one ROI hanging off each). Group the
    existing rows by their canonical path; in each group the LOWEST id wins
    (the oldest registration), every other row's ROIs and interactions are
    re-pointed at it, and the duplicate row is deleted. Rows that are already
    unique keep their id and only get their stored path rewritten.

    ROIs are moved BEFORE the duplicate row is deleted — ``slides.rois``
    cascade-deletes, so the other order would destroy exactly the annotations
    this is meant to rescue.

    Returns the ``(deleted_id, kept_id)`` pairs, oldest first.
    """
    merged: list[tuple[int, int]] = []
    with Session(engine) as s:
        groups: dict[str, list[Slide]] = {}
        for slide in s.execute(select(Slide).order_by(Slide.id)).scalars():
            groups.setdefault(normalize_slide_path(slide.path), []).append(slide)
        for canonical, rows in groups.items():
            keeper = rows[0]
            for dup in rows[1:]:
                s.execute(
                    sa.update(ROI)
                    .where(ROI.slide_id == dup.id)
                    .values(slide_id=keeper.id)
                )
                s.execute(
                    sa.update(Interaction)
                    .where(Interaction.slide_id == dup.id)
                    .values(slide_id=keeper.id)
                )
                s.delete(dup)
                merged.append((dup.id, keeper.id))
            # only after the duplicates are gone: slides.path is UNIQUE
            s.flush()
            keeper.path = canonical
        s.commit()
    return merged


def _bbox_columns(roi: "ROIGeometry") -> tuple[float, float, float, float]:
    """Derive the four queryable ``bbox_*`` columns from a geometry ROI.

    The single writer: both ``ROIRepo.add`` and (independently, since a
    migration cannot import evolving ORM/geometry code -- see
    ``hescope.migrations``' module docstring) migration 2's backfill compute
    this from the SAME ``roi.bbox()`` / ``bbox_json`` values so the derived
    columns can never disagree with the JSON they are a queryable cache of.
    """
    x0, y0, x1, y1 = roi.bbox()
    return float(x0), float(y0), float(x1), float(y1)


class ROIRepo:
    """Repository for the rois table."""

    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine

    def add(
        self,
        slide_id: int,
        roi: "ROIGeometry",
        *,
        label: str = "",
        notes: str = "",
        patch_path: str | None = None,
        stats: dict | None = None,
        magnification: float | None = None,
    ) -> int:
        """Persist a geometry ROI (hescope.rois.ROI) for a slide."""
        points = [[float(x), float(y)] for x, y in roi.points]
        bbox = [int(v) for v in roi.bbox()]
        bbox_x0, bbox_y0, bbox_x1, bbox_y1 = _bbox_columns(roi)
        with Session(self.engine) as s:
            rec = ROI(
                slide_id=slide_id,
                kind=roi.kind,
                points_json=json.dumps(points),
                bbox_json=json.dumps(bbox),
                bbox_x0=bbox_x0,
                bbox_y0=bbox_y0,
                bbox_x1=bbox_x1,
                bbox_y1=bbox_y1,
                label=label,
                notes=notes,
                patch_path=patch_path,
                stats_json=json.dumps(stats) if stats is not None else None,
                magnification=magnification,
            )
            s.add(rec)
            s.commit()
            return rec.id  # type: ignore[return-value]

    def update_annotation(
        self,
        roi_id: int,
        *,
        label: str | None = None,
        notes: str | None = None,
    ) -> bool:
        """Update label/notes; ``None`` leaves the field unchanged.

        Returns whether a row was actually updated. It used to return None
        either way, so a caller could only tell "no exception was raised" from
        "the write landed" by re-reading — and app.py's Save button therefore
        reported "Saved annotation for ROI N." unconditionally, including for
        a row a second session had already deleted (R07-14).
        """
        with write_session(self.engine) as s:
            rec = s.get(ROI, roi_id)
            if rec is None:
                return False
            if label is not None:
                rec.label = label
            if notes is not None:
                rec.notes = notes
            s.commit()
            return True

    def get(self, roi_id: int) -> dict | None:
        """Single ROI row by id (same dict shape as ``for_slide``), or None."""
        with Session(self.engine) as s:
            rec = s.get(ROI, roi_id)
            return _roi_dict(rec) if rec is not None else None

    def delete(self, roi_id: int) -> bool:
        """Delete an ROI; agent_runs referencing it get roi_id set to NULL.

        Returns whether a row was actually deleted — see
        :meth:`update_annotation` for why the caller needs to know (R07-14).
        """
        with write_session(self.engine) as s:
            rec = s.get(ROI, roi_id)
            if rec is None:
                return False
            s.delete(rec)
            s.commit()
            return True

    def for_slide(
        self,
        slide_id: int,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict]:
        """ROIs for a slide, ordered by id; each dict includes parsed
        ``bbox`` (ints). ``limit``/``offset`` default to ``None`` (no
        caller breaks); pass ``limit`` to page through a slide with more
        ROIs than should ever be pulled into memory at once -- see
        :meth:`in_viewport` for a viewport-filtered (and always bounded)
        alternative."""
        with Session(self.engine) as s:
            stmt = select(ROI).where(ROI.slide_id == slide_id).order_by(ROI.id)
            if limit is not None:
                stmt = stmt.limit(limit)
            if offset is not None:
                stmt = stmt.offset(offset)
            return [_roi_dict(r) for r in s.execute(stmt).scalars()]

    def in_viewport(
        self,
        slide_id: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        limit: int = 2000,
    ) -> list[dict]:
        """ROIs on ``slide_id`` whose bbox intersects the level-0 rectangle
        ``[x0, y0, x1, y1]``, filtered in SQL via the ``bbox_*`` columns and
        ``ix_rois_slide_bbox`` (defect 1.4: ``bbox_json`` is TEXT, so this
        predicate could not be expressed in SQL at all before -- ``for_slide``
        was the only option, returning every ROI on the slide regardless of
        what was on screen: measured 138 ms + 170 KB at 5 000 ROIs, on every
        overlay re-render).

        Standard axis-aligned-bounding-box intersection test: a ROI is
        included when its bbox and the query rectangle overlap on both axes.
        A ROI whose ``bbox_*`` columns are still NULL (never backfilled, or
        inserted by something other than ``ROIRepo.add``) is excluded rather
        than raising -- SQL's NULL comparisons are neither true nor false, so
        it simply cannot match a predicate, the same way it cannot match a
        WHERE clause on any other column.
        """
        with Session(self.engine) as s:
            stmt = (
                select(ROI)
                .where(
                    ROI.slide_id == slide_id,
                    ROI.bbox_x0 <= x1,
                    ROI.bbox_x1 >= x0,
                    ROI.bbox_y0 <= y1,
                    ROI.bbox_y1 >= y0,
                )
                .order_by(ROI.id)
                .limit(limit)
            )
            return [_roi_dict(r) for r in s.execute(stmt).scalars()]

    def search(
        self,
        *,
        label: str | None = None,
        slide_id: int | None = None,
    ) -> list[dict]:
        with Session(self.engine) as s:
            stmt = select(ROI).order_by(ROI.id)
            if label is not None:
                stmt = stmt.where(ROI.label == label)
            if slide_id is not None:
                stmt = stmt.where(ROI.slide_id == slide_id)
            return [_roi_dict(r) for r in s.execute(stmt).scalars()]


class AgentRunRepo:
    """Repository for the agent_runs table."""

    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine

    def record(
        self,
        *,
        tool: str,
        input: dict,
        output_text: str,
        roi_id: int | None = None,
        model: str | None = None,
        status: str = "ok",
    ) -> int:
        with Session(self.engine) as s:
            rec = AgentRun(
                roi_id=roi_id,
                tool=tool,
                input_json=json.dumps(input),
                output_text=output_text,
                model=model,
                status=status,
            )
            s.add(rec)
            s.commit()
            return rec.id  # type: ignore[return-value]

    def for_roi(self, roi_id: int) -> list[dict]:
        with Session(self.engine) as s:
            stmt = (
                select(AgentRun).where(AgentRun.roi_id == roi_id).order_by(AgentRun.id)
            )
            return [_agent_run_dict(r) for r in s.execute(stmt).scalars()]

    def recent(self, limit: int = 50) -> list[dict]:
        """Most recent runs first."""
        with Session(self.engine) as s:
            stmt = (
                select(AgentRun)
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .limit(limit)
            )
            return [_agent_run_dict(r) for r in s.execute(stmt).scalars()]


class InteractionRepo:
    """Repository for the interactions table (interaction trace v1).

    Fully exception-safe: ``record`` returns the new row id, or None on any
    failure (never raises); ``recent`` / ``for_slide`` return [] on failure.
    """

    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine

    def record(
        self,
        *,
        kind: str,
        payload: dict | None = None,
        session_tag: str | None = None,
        slide_id: int | None = None,
        roi_id: int | None = None,
    ) -> int | None:
        """Append one interaction row; returns the row id or None on failure."""
        try:
            with Session(self.engine) as s:
                rec = Interaction(
                    session_tag=session_tag,
                    kind=kind,
                    slide_id=slide_id,
                    roi_id=roi_id,
                    payload=json.dumps(payload or {}),
                )
                s.add(rec)
                s.commit()
                return rec.id  # type: ignore[return-value]
        except Exception:
            return None

    def recent(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        """Most recent interactions first; optionally filtered by kind."""
        try:
            with Session(self.engine) as s:
                stmt = select(Interaction)
                if kind is not None:
                    stmt = stmt.where(Interaction.kind == kind)
                stmt = stmt.order_by(
                    Interaction.created_at.desc(), Interaction.id.desc()
                ).limit(limit)
                return [_interaction_dict(r) for r in s.execute(stmt).scalars()]
        except Exception:
            return []

    def for_slide(self, slide_id: int, limit: int = 200) -> list[dict]:
        """Interactions for one slide, oldest first."""
        try:
            with Session(self.engine) as s:
                stmt = (
                    select(Interaction)
                    .where(Interaction.slide_id == slide_id)
                    .order_by(Interaction.id)
                    .limit(limit)
                )
                return [_interaction_dict(r) for r in s.execute(stmt).scalars()]
        except Exception:
            return []


_EXPORT_FIELDS = [
    "id",
    "slide_id",
    "slide_name",
    "kind",
    "label",
    "notes",
    "bbox_json",
    "bbox",
    "points_json",
    "patch_path",
    "stats_json",
    "magnification",
    "created_at",
]


def export_rois(
    engine: sa.Engine,
    slide_id: int | None = None,
    fmt: Literal["json", "csv"] = "json",
) -> str:
    """Serialize ROI rows (with slide name) to a JSON string or CSV string."""
    with Session(engine) as s:
        stmt = (
            select(ROI, Slide.name)
            .join(Slide, ROI.slide_id == Slide.id)
            .order_by(ROI.id)
        )
        if slide_id is not None:
            stmt = stmt.where(ROI.slide_id == slide_id)
        records: list[dict] = []
        for roi, slide_name in s.execute(stmt).all():
            d = _roi_dict(roi)
            d["slide_name"] = slide_name
            records.append(d)

    if fmt == "json":
        return json.dumps(records)
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_EXPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            row = dict(rec)
            row["bbox"] = json.dumps(rec["bbox"])
            writer.writerow(row)
        return buf.getvalue()
    raise ValueError(f"unsupported export format: {fmt!r}")
