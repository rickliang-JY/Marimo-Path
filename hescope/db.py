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
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import sqlalchemy as sa
from sqlalchemy import ForeignKey, String, Text, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

if TYPE_CHECKING:  # avoid a hard runtime dependency cycle with hescope.rois
    from .rois import ROI as ROIGeometry

from .paths import resolve_runtime_dir

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Writable runtime root: the repo root for checkouts/editable installs,
# <cwd>/hescope_runtime for non-editable installs (never site-packages).
_RUNTIME_ROOT = resolve_runtime_dir(_PROJECT_ROOT)
DEFAULT_DB_URL = f"sqlite:///{_RUNTIME_ROOT / 'data' / 'hescope.db'}"


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


def get_engine(url: str | None = None) -> sa.Engine:
    """Resolve the database URL and create an engine.

    Resolution order: explicit ``url`` argument -> ``HESCOPE_DB_URL``
    environment variable -> ``DEFAULT_DB_URL``. For sqlite URLs, parent
    directories of the database file are created as needed.
    """
    resolved = url or os.environ.get("HESCOPE_DB_URL") or DEFAULT_DB_URL
    sa_url = sa.engine.make_url(resolved)
    if sa_url.get_backend_name() == "sqlite":
        db_path = sa_url.database
        if db_path and db_path != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(resolved)
    if sa_url.get_backend_name() == "sqlite":
        # Enforce foreign keys (ON DELETE CASCADE / SET NULL) on sqlite.
        @event.listens_for(engine, "connect")
        def _fk_pragma_on(dbapi_conn: Any, _record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def init_db(engine: sa.Engine) -> None:
    """Create all tables (idempotent)."""
    Base.metadata.create_all(engine)


class Base(DeclarativeBase):
    pass


class Slide(Base):
    __tablename__ = "slides"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(512))
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    width: Mapped[int] = mapped_column()
    height: Mapped[int] = mapped_column()
    mpp: Mapped[float | None] = mapped_column(nullable=True)
    extra_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    rois: Mapped[list[ROI]] = relationship(
        back_populates="slide", cascade="all, delete-orphan", passive_deletes=True
    )


class ROI(Base):
    __tablename__ = "rois"

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

    slide: Mapped[Slide] = relationship(back_populates="rois")
    agent_runs: Mapped[list[AgentRun]] = relationship(back_populates="roi")


INTERACTION_KINDS = (
    "selection_view",
    "roi_submit",
    "label_set",
    "analysis_run",
    "tool_call",
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
    }


def _roi_dict(roi: ROI) -> dict:
    return {
        "id": roi.id,
        "slide_id": roi.slide_id,
        "kind": roi.kind,
        "points_json": roi.points_json,
        "bbox_json": roi.bbox_json,
        "bbox": [int(v) for v in json.loads(roi.bbox_json)],
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
    ) -> int:
        """Register a slide. Idempotent on the unique ``path``: re-registering
        the same path returns the existing id and refreshes mutable fields."""
        with Session(self.engine) as s:
            slide = s.execute(
                select(Slide).where(Slide.path == path)
            ).scalar_one_or_none()
            if slide is None:
                slide = Slide(path=path)
                s.add(slide)
            slide.source_kind = source_kind
            slide.name = name
            slide.width = int(width)
            slide.height = int(height)
            slide.mpp = mpp
            slide.extra_json = json.dumps(extra or {})
            s.commit()
            return slide.id  # type: ignore[return-value]

    def get(self, slide_id: int) -> dict | None:
        with Session(self.engine) as s:
            slide = s.get(Slide, slide_id)
            return _slide_dict(slide) if slide is not None else None

    def find_by_path(self, path: str) -> dict | None:
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
        with Session(self.engine) as s:
            slide = s.get(Slide, slide_id)
            if slide is not None:
                s.delete(slide)
                s.commit()


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
        with Session(self.engine) as s:
            rec = ROI(
                slide_id=slide_id,
                kind=roi.kind,
                points_json=json.dumps(points),
                bbox_json=json.dumps(bbox),
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
    ) -> None:
        """Update label/notes; ``None`` leaves the field unchanged."""
        with Session(self.engine) as s:
            rec = s.get(ROI, roi_id)
            if rec is None:
                return
            if label is not None:
                rec.label = label
            if notes is not None:
                rec.notes = notes
            s.commit()

    def get(self, roi_id: int) -> dict | None:
        """Single ROI row by id (same dict shape as ``for_slide``), or None."""
        with Session(self.engine) as s:
            rec = s.get(ROI, roi_id)
            return _roi_dict(rec) if rec is not None else None

    def delete(self, roi_id: int) -> None:
        """Delete an ROI; agent_runs referencing it get roi_id set to NULL."""
        with Session(self.engine) as s:
            rec = s.get(ROI, roi_id)
            if rec is not None:
                s.delete(rec)
                s.commit()

    def for_slide(self, slide_id: int) -> list[dict]:
        """All ROIs for a slide; each dict includes parsed ``bbox`` (ints)."""
        with Session(self.engine) as s:
            stmt = select(ROI).where(ROI.slide_id == slide_id).order_by(ROI.id)
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
