"""A TCGA-shaped catalog: program -> project -> case -> sample -> file.

Why this exists
---------------
The original catalog was one flat table, ``tcga_slides``, with ``project_id``,
``case_submitter_id``, ``sample_type`` and ``primary_site`` repeated on every
row. That cannot answer the questions the data is actually organised around --
"which cases do I have slides for", "how many primary tumours in TCGA-BRCA",
"is this the same patient as that one" -- and it discarded most of what the GDC
already sends: ``project.disease_type``, ``sample_id``, the sample's own
barcode (``TCGA-BH-A18H-01A``), ``tissue_type``, and every case or sample past
the first.

Downloaded files were equally opaque: ``data/tcga/<file-uuid>/<name>.svs``, so
the directory tree said nothing about what was in it.

What lives where
----------------
**Metadata in the database. Pixels on disk.** Whole-slide images are 100 MB to
2 GB and both OpenSlide and tifffile need a real filesystem path they can
memory-map and read regions from; storing them as BLOBs would defeat the tile
server, which exists precisely to avoid materialising a slide in memory. So the
database owns the structure and points at the files, and the files are laid out
along the same hierarchy so the tree is legible on its own:

    data/tcga/TCGA-BRCA/TCGA-BH-A18H/TCGA-BH-A18H-01Z-00-DX1.<uuid>.svs

:func:`storage_relpath` is the single definition of that layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

__all__ = [
    "TcgaBase",
    "TcgaProject",
    "TcgaCase",
    "TcgaSample",
    "TcgaFile",
    "Barcode",
    "parse_barcode",
    "storage_relpath",
    "init_tcga_schema",
    "plan_init_tcga_schema",
    "TcgaCatalog",
    "hits_to_rows",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc_datetime(value: object) -> datetime | None:
    """Normalize a caller-supplied timestamp into the naive-UTC form every
    ``DATETIME`` column in this project is stored as (see ``hescope.db``'s
    naive-UTC convention: an aware datetime handed straight to SQLAlchemy's
    sqlite ``DATETIME`` binding has its offset silently discarded, not
    converted).

    Used for both ``first_seen_at`` and ``downloaded_at`` -- the same
    provenance-timestamp shape, and the same fix (BUILD-PLAN-DB round 3
    finding 4: ``downloaded_at`` had the identical defect ``first_seen_at``
    was already fixed for, one column over, in the same import path).

    Accepts a ``datetime`` (aware or naive) or an ISO8601 string -- the flat
    ``SlideCatalog`` writes ``datetime.now(timezone.utc).isoformat()``, e.g.
    ``'2026-08-09T09:58:01.886485+00:00'`` (see ``SlideCatalog._now``).
    Returns ``None`` for ``None`` or anything unparseable rather than
    raising: a provenance timestamp that cannot be read must not abort an
    otherwise-good import (the caller falls back to 'now', still better than
    crashing on one bad row).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    return None


class TcgaBase(DeclarativeBase):
    """Separate declarative base: these tables are additive and can be created
    in an existing HE-Scope database without touching the core metadata."""


class TcgaProject(TcgaBase):
    __tablename__ = "tcga_projects"

    project_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    program: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    disease_type: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    primary_site: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)


class TcgaCase(TcgaBase):
    """A patient. Keyed on the submitter barcode (TCGA-BH-A18H) because the
    GDC ``case_id`` UUID is not in the response unless it is explicitly
    requested, and the barcode is what a pathologist actually recognises."""

    __tablename__ = "tcga_cases"

    submitter_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    case_uuid: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    project_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("tcga_projects.project_id"), nullable=True, index=True
    )


class TcgaSample(TcgaBase):
    """A specimen taken from a case: TCGA-BH-A18H-01A.

    The two-digit code after the patient is what separates a primary tumour
    (01) from a normal (11), which is the distinction most analyses turn on.
    """

    __tablename__ = "tcga_samples"

    sample_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    submitter_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    case_submitter_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("tcga_cases.submitter_id"), nullable=True, index=True
    )
    sample_type: Mapped[str | None] = mapped_column(sa.String(128), nullable=True, index=True)
    tissue_type: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)


class TcgaFile(TcgaBase):
    """One downloadable slide image.

    ``local_path`` is set on download and is the join back to the pixels;
    ``slide_id`` is the row in the core ``slides`` table once the file has been
    registered there, which is what lets an annotation be traced back to a
    patient.
    """

    __tablename__ = "tcga_files"

    file_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    file_name: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    file_size: Mapped[int | None] = mapped_column(nullable=True)
    md5sum: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    sample_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("tcga_samples.sample_id"), nullable=True, index=True
    )
    case_submitter_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    local_path: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(default=_utcnow)
    slide_id: Mapped[int | None] = mapped_column(nullable=True, index=True)


# ---------------------------------------------------------------------------
# TCGA barcodes
# ---------------------------------------------------------------------------

#: TCGA-BH-A18H-01A-01-TSA -> project/tss, participant, sample+vial, portion,
#: slide. Everything after the participant is optional, because a case barcode
#: is a legitimate barcode.
_BARCODE_RE = re.compile(
    r"^(?P<program>TCGA|TARGET|CPTAC|HCM)-(?P<tss>[A-Z0-9]{2,4})-(?P<participant>[A-Z0-9]{4,6})"
    r"(?:-(?P<sample>\d{2})(?P<vial>[A-Z])?)?"
    r"(?:-(?P<portion>\d{2})(?P<analyte>[A-Z])?)?"
    r"(?:-(?P<plate_or_slide>[A-Z0-9]{3,4}))?",
    re.IGNORECASE,
)

#: The sample-type digits that matter most often. GDC sends the human-readable
#: name too; this is for when all you have is a barcode (a bare filename).
SAMPLE_TYPE_CODES = {
    "01": "Primary Tumor",
    "02": "Recurrent Tumor",
    "03": "Primary Blood Derived Cancer - Peripheral Blood",
    "06": "Metastatic",
    "07": "Additional Metastatic",
    "10": "Blood Derived Normal",
    "11": "Solid Tissue Normal",
    "12": "Buccal Cell Normal",
}


@dataclass(frozen=True)
class Barcode:
    """The parts of a TCGA barcode. Every field may be None: a barcode is
    legitimately truncated at any level."""

    program: str | None = None
    case: str | None = None          # TCGA-BH-A18H
    sample: str | None = None        # TCGA-BH-A18H-01A
    sample_code: str | None = None   # "01"
    sample_type: str | None = None   # "Primary Tumor", from the code
    slide: str | None = None         # DX1 / TSA


def parse_barcode(text: str | None) -> Barcode:
    """Pull the hierarchy out of a TCGA barcode, or as much of it as is there.

    Accepts a bare barcode or a filename that starts with one -- GDC slide
    files are named ``<barcode>.<uuid>.svs``, so this recovers the structure
    from a file on disk with no network and no catalog.
    """
    if not text:
        return Barcode()
    stem = str(text).split("/")[-1].split("\\")[-1]
    m = _BARCODE_RE.match(stem)
    if not m:
        return Barcode()
    g = m.groupdict()
    case = None
    if g["program"] and g["tss"] and g["participant"]:
        case = f"{g['program'].upper()}-{g['tss'].upper()}-{g['participant'].upper()}"
    sample = None
    if case and g["sample"]:
        sample = f"{case}-{g['sample']}{(g['vial'] or '').upper()}"
    return Barcode(
        program=g["program"].upper() if g["program"] else None,
        case=case,
        sample=sample,
        sample_code=g["sample"],
        sample_type=SAMPLE_TYPE_CODES.get(g["sample"] or ""),
        slide=(g["plate_or_slide"] or "").upper() or None,
    )


def _safe_component(text: str | None, fallback: str) -> str:
    """One path component: no separators, no drive letters, no traversal.

    Server-supplied strings decide these directory names, and this repo has
    already had two path-escape findings (R02-3, R05-1) from exactly that.
    """
    raw = str(text or "").strip()
    raw = raw.split(":")[-1]
    raw = raw.replace("\\", "/").split("/")[-1]
    raw = re.sub(r"[^A-Za-z0-9._-]", "_", raw).strip("._")
    return raw or fallback


def storage_relpath(
    file_id: str,
    file_name: str | None = None,
    project_id: str | None = None,
    case_submitter_id: str | None = None,
) -> Path:
    """Where a downloaded slide belongs, relative to the TCGA data root.

    ``<project>/<case>/<file_name>`` -- so the directory tree mirrors the
    hierarchy and is readable without the database. Falls back a level at a
    time (project, then case, then the file id) when the metadata is missing,
    so a file always has somewhere to go.

    THE SINGLE DEFINITION of the layout: everything that writes or looks for a
    downloaded slide goes through here, or the two will drift and a file will
    be downloaded twice.
    """
    bc = parse_barcode(file_name)
    project = _safe_component(project_id, "unknown-project")
    case = _safe_component(case_submitter_id or bc.case, "unknown-case")
    name = _safe_component(file_name, f"{_safe_component(file_id, 'slide')}.svs")
    return Path(project) / case / name


def init_tcga_schema(engine: sa.Engine) -> None:
    """Create the TCGA tables if they are absent. Additive and idempotent."""
    TcgaBase.metadata.create_all(engine)


def plan_init_tcga_schema(engine: sa.Engine, conn: sa.Connection | None = None) -> dict:
    """What :func:`init_tcga_schema` WOULD do, computed read-only.

    Mirrors ``hescope.db.plan_init_db``'s contract (see that function's
    docstring for why this pairing exists at all): ``tcga_projects`` /
    ``tcga_cases`` / ``tcga_samples`` / ``tcga_files`` live on their own
    ``DeclarativeBase`` (``TcgaBase``, deliberately separate from
    ``hescope.db.Base`` -- see this module's docstring), so ``plan_init_db``
    never sees them and a ``migrate --dry-run`` that only asked it would
    under-report exactly the class of gap ``plan_init_db`` itself exists to
    close for the core schema -- measured: on a database with none of the
    TCGA tables yet, a real (non-dry-run) ``migrate()`` call now creates them
    (``hescope.migrations.migrate`` calls ``init_tcga_schema`` for migration
    3's self-containment) while a dry run that only consulted
    ``plan_init_db`` named none of them.

    Returns ``{"new_tables": [name, ...], "new_table_indexes": {name:
    [index_name, ...]}}`` -- no ``alter_statements``/``create_index_statements``
    keys, unlike ``plan_init_db``: the TCGA tables have no pre-Phase-2
    narrower schema to upgrade FROM (they shipped with every current index
    from the start), so ``create_all`` either creates a table whole or
    leaves an existing one untouched; there is no column/index gap on an
    EXISTING tcga table for this function to compute (the one exception --
    ``tcga_files.slide_id``'s foreign key -- is migration 3's own job, see
    ``hescope.migrations.plan_migration_3``, not this function's).
    """
    inspector = sa.inspect(conn) if conn is not None else sa.inspect(engine)
    existing_tables = set(inspector.get_table_names())
    new_tables: list[str] = []
    new_table_indexes: dict[str, list[str]] = {}
    for table in TcgaBase.metadata.sorted_tables:
        if table.name not in existing_tables:
            new_tables.append(table.name)
            if table.indexes:
                new_table_indexes[table.name] = sorted(ix.name for ix in table.indexes)
    return {"new_tables": new_tables, "new_table_indexes": new_table_indexes}


def hits_to_rows(hits: Iterable[dict]) -> list[dict]:
    """Flatten GDC ``/files`` hits into per-file dicts carrying the hierarchy.

    Unlike the original ``_record_from_hit``, this keeps the identifiers the
    response already contains -- sample_id, the sample's own barcode,
    tissue_type, disease_type -- instead of collapsing them into four columns.
    """
    rows: list[dict] = []
    for hit in hits or []:
        cases = hit.get("cases") or [{}]
        case = cases[0] if cases else {}
        project = case.get("project") or {}
        samples = case.get("samples") or [{}]
        sample = samples[0] if samples else {}
        file_name = hit.get("file_name") or ""
        bc = parse_barcode(file_name)
        rows.append(
            {
                "file_id": hit.get("file_id") or hit.get("id") or "",
                "file_name": file_name,
                "file_size": int(hit.get("file_size") or 0),
                "md5sum": hit.get("md5sum"),
                "project_id": project.get("project_id"),
                "project_name": project.get("name"),
                "disease_type": project.get("disease_type"),
                "primary_site": project.get("primary_site")
                or case.get("primary_site"),
                "program": (project.get("program") or {}).get("name")
                if isinstance(project.get("program"), dict)
                else project.get("program") or bc.program,
                "case_submitter_id": case.get("submitter_id") or bc.case,
                "case_uuid": case.get("case_id"),
                "sample_id": sample.get("sample_id"),
                "sample_submitter_id": sample.get("submitter_id") or bc.sample,
                "sample_type": sample.get("sample_type") or bc.sample_type,
                "tissue_type": sample.get("tissue_type"),
            }
        )
    return rows


class TcgaCatalog:
    """Read/write access to the TCGA hierarchy in an HE-Scope database."""

    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine
        init_tcga_schema(engine)

    def upsert_rows(self, rows: Iterable[dict]) -> int:
        """Insert search results, preserving anything already recorded.

        Download state (``local_path``, ``downloaded_at``, ``slide_id``) is
        never touched: re-running a search must not forget that a file is
        already on disk.

        ``row["first_seen_at"]``, when present, is used verbatim (via
        :func:`_coerce_utc_datetime`) as the NEW row's ``first_seen_at``
        instead of the ORM default (``_utcnow``, i.e. "the moment this
        import ran"). This is what ``migrate-tcga-catalog`` needs: without
        it, every file carried over from the flat catalog lost its real
        discovery time and silently took on the import's timestamp instead
        -- measured as a 25-hour loss across all 50 rows of the real
        database (BUILD-PLAN-DB.md Phase 2, R-3's exemplar). A row with no
        ``first_seen_at`` key (an ordinary GDC search result, genuinely
        first seen right now) still gets ``_utcnow()``, unchanged.
        """
        n = 0
        with Session(self.engine) as s:
            for row in rows:
                fid = row.get("file_id")
                if not fid:
                    continue
                pid = row.get("project_id")
                if pid and s.get(TcgaProject, pid) is None:
                    s.add(
                        TcgaProject(
                            project_id=pid,
                            program=row.get("program"),
                            name=row.get("project_name"),
                            disease_type=row.get("disease_type"),
                            primary_site=row.get("primary_site"),
                        )
                    )
                    s.flush()
                cid = row.get("case_submitter_id")
                if cid and s.get(TcgaCase, cid) is None:
                    s.add(
                        TcgaCase(
                            submitter_id=cid,
                            case_uuid=row.get("case_uuid"),
                            project_id=pid,
                        )
                    )
                    s.flush()
                sid = row.get("sample_id")
                if sid and s.get(TcgaSample, sid) is None:
                    s.add(
                        TcgaSample(
                            sample_id=sid,
                            submitter_id=row.get("sample_submitter_id"),
                            case_submitter_id=cid,
                            sample_type=row.get("sample_type"),
                            tissue_type=row.get("tissue_type"),
                        )
                    )
                    s.flush()
                if s.get(TcgaFile, fid) is None:
                    kwargs: dict[str, Any] = dict(
                        file_id=fid,
                        file_name=row.get("file_name"),
                        file_size=row.get("file_size"),
                        md5sum=row.get("md5sum"),
                        sample_id=sid,
                        case_submitter_id=cid,
                        project_id=pid,
                    )
                    first_seen = _coerce_utc_datetime(row.get("first_seen_at"))
                    if first_seen is not None:
                        kwargs["first_seen_at"] = first_seen
                    s.add(TcgaFile(**kwargs))
                    n += 1
                else:
                    # backfill only what was missing; never clobber download state
                    rec = s.get(TcgaFile, fid)
                    if rec is not None and not rec.md5sum and row.get("md5sum"):
                        rec.md5sum = row["md5sum"]
            s.commit()
        return n

    def mark_downloaded(
        self,
        file_id: str,
        local_path: str | Path,
        slide_id: int | None = None,
        *,
        downloaded_at: datetime | str | None = None,
    ) -> bool:
        """Record where the file landed, and optionally the ``slides`` row it
        was registered as -- that link is what lets an annotation be traced
        back to a case.

        Returns ``False`` and writes nothing when ``file_id`` is not already
        a known ``TcgaFile`` row. Before this fix an unknown id silently
        INSERTed a brand-new, metadata-free row (no project, case, sample or
        even a ``first_seen_at`` derived from a real search) that then
        counted as "downloaded" in every stat and listing -- defect 2.4
        (BUILD-PLAN-DB.md Phase 2), measured by
        ``tests/test_tcga_schema.py::test_mark_downloaded_on_unknown_id_returns_false_and_writes_nothing``
        failing against the un-fixed code. A completed download always comes
        from a row this catalog already knows (search results are upserted
        before anything is downloaded), so "unknown id" is a caller bug or a
        stale reference, not a legitimate new file to invent metadata for.

        ``downloaded_at`` only advances when ``local_path`` actually changes
        (or was unset) -- calling this again with the SAME path (the shape
        ``migrate-tcga-catalog`` re-runs in) is then a true no-op on that
        column too, not a provenance-erasing timestamp bump every time. When
        it DOES advance, a caller-supplied ``downloaded_at`` (normalized via
        :func:`_coerce_utc_datetime`, same as ``upsert_rows``' ``first_seen_at``)
        is used instead of ``_utcnow()`` -- round 3 finding 4: without this,
        ``migrate-tcga-catalog`` carrying a flat catalog's already-recorded
        download over always replaced it with the moment the IMPORT ran, the
        identical provenance loss ``first_seen_at`` was already fixed for,
        one column over. A caller that omits it (an ordinary download
        completing right now) still gets ``_utcnow()``, unchanged.
        """
        with Session(self.engine) as s:
            rec = s.get(TcgaFile, file_id)
            if rec is None:
                return False
            new_path = str(local_path)
            if rec.local_path != new_path:
                rec.local_path = new_path
                coerced = (
                    _coerce_utc_datetime(downloaded_at)
                    if downloaded_at is not None
                    else None
                )
                rec.downloaded_at = coerced if coerced is not None else _utcnow()
            if slide_id is not None:
                rec.slide_id = slide_id
            s.commit()
        return True

    def local_file(self, file_id: str) -> Path | None:
        """The recorded local copy, if it is still on disk. Same contract as
        the flat catalog's method of the same name: a stale path is not an
        openable slide."""
        with Session(self.engine) as s:
            rec = s.get(TcgaFile, file_id)
            if rec is None or not rec.local_path:
                return None
        path = Path(rec.local_path)
        return path if path.is_file() else None

    def files(
        self,
        project_id: str | None = None,
        case_submitter_id: str | None = None,
        sample_type: str | None = None,
        downloaded_only: bool = False,
        limit: int = 500,
    ) -> list[dict]:
        """Files joined to their sample, case and project."""
        with Session(self.engine) as s:
            q = (
                sa.select(TcgaFile, TcgaSample, TcgaProject)
                .join(TcgaSample, TcgaFile.sample_id == TcgaSample.sample_id, isouter=True)
                .join(TcgaProject, TcgaFile.project_id == TcgaProject.project_id, isouter=True)
            )
            if project_id:
                q = q.where(TcgaFile.project_id == project_id)
            if case_submitter_id:
                q = q.where(TcgaFile.case_submitter_id == case_submitter_id)
            if sample_type:
                q = q.where(TcgaSample.sample_type == sample_type)
            if downloaded_only:
                q = q.where(TcgaFile.local_path.is_not(None))
            out = []
            for f, sample, project in s.execute(q.limit(limit)).all():
                out.append(
                    {
                        "file_id": f.file_id,
                        "file_name": f.file_name,
                        "file_size": f.file_size,
                        "md5sum": f.md5sum,
                        "project_id": f.project_id,
                        "disease_type": project.disease_type if project else None,
                        "primary_site": project.primary_site if project else None,
                        "case_submitter_id": f.case_submitter_id,
                        "sample_submitter_id": sample.submitter_id if sample else None,
                        "sample_type": sample.sample_type if sample else None,
                        "tissue_type": sample.tissue_type if sample else None,
                        "local_path": f.local_path,
                        "downloaded": bool(f.local_path),
                        "slide_id": f.slide_id,
                    }
                )
            return out

    def stats(self) -> dict:
        """Counts at every level -- the questions a flat table could not
        answer."""
        with Session(self.engine) as s:
            scalar = lambda q: int(s.execute(q).scalar_one() or 0)  # noqa: E731
            return {
                "projects": scalar(sa.select(sa.func.count()).select_from(TcgaProject)),
                "cases": scalar(sa.select(sa.func.count()).select_from(TcgaCase)),
                "samples": scalar(sa.select(sa.func.count()).select_from(TcgaSample)),
                "files": scalar(sa.select(sa.func.count()).select_from(TcgaFile)),
                "downloaded": scalar(
                    sa.select(sa.func.count())
                    .select_from(TcgaFile)
                    .where(TcgaFile.local_path.is_not(None))
                ),
            }
