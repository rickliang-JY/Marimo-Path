# Build plan — database foundation

Authoritative build instructions for three phases the user prioritised:

1. **ROI storage anchored to a slide** (the SVS ↔ ROI relationship)
2. **TCGA download → storage → database injection**
3. **User-supplied databases** (backend and source diversity)

Baseline: `feature/db-foundation` @ `9b11ffe`, **797 passed / 16 skipped**,
`data/hescope.db` now WAL with 8 tables and `user_version = 0`.

---

## 0. Rules that bind every phase

These are not style preferences. Each one is here because breaking it has
already cost this project real data or a false green.

**R-1. Never write to `data/hescope.db`.** It holds the user's real slides,
ROIs and TCGA catalogue. Migrations are developed and demonstrated against a
**copy** (`tools/proto_db_migration.py` shows the pattern). Applying anything to
the live file is the user's decision and is out of scope for every phase here.

**R-2. Every change ships with a test that was RUN against the un-fixed code and
seen to fail.** Not "a test exists". If the change is additive and no
pre-existing behaviour was wrong, the test must still pin the new invariant in a
way that fails if the implementation is removed. State in the test docstring
what was observed to fail.

**R-3. A migration test compares a SOURCE value to its DESTINATION value.**
Counts are not evidence. `migrate-tcga-catalog` reset `first_seen_at` on all 50
rows and five count-asserting tests passed.

**R-4. Additive only.** No `DROP`, no rename, no type change in this plan. Old
readers must keep working after every phase. New columns are nullable or carry a
default; new tables are new.

**R-5. Never start or stop the marimo server, and never edit `app.py` while one
runs.** It is currently down; keep it down. marimo's autosave rewrites `app.py`
from kernel state and has already silently reverted a committed fix.

**R-6. The full suite must pass** (`pytest -q`) before a phase is done. Record
the count.

**R-7. Measure dependency cost before adding one**
(`uv pip install --dry-run <pkg> --python .venv/Scripts/python.exe`), and report
installs/removals. `tiatoolbox` was rejected at 152/22 because it uninstalls
`ipywidgets`; `wsidicom` accepted at 7/0.

**R-8. Do not invent numbers.** Every claim in a commit message or docstring is
either measured with a quoted command or marked unverified.

---

## Phase 0 — migration framework (prerequisite for 1–3)

### Goal

A versioned, forward-only migration runner, and the current schema stamped as
version 1, so phases 1–3 have somewhere to put their schema changes.

### Why first

`PRAGMA user_version = 0`, there is no runner, and `init_db`'s implicit upgrade
(`create_all` + `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`,
`hescope/db.py:161-200`) produces a **materially different schema** from a fresh
`create_all`: an upgraded `rois` carries 0 indexes where a fresh one carries 2,
and a `server_default` is silently dropped. Both report version 0.

### Changes

New module `hescope/migrations.py`:

```python
SCHEMA_VERSION = 1          # bumped by each phase

@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sa.Connection], None]

MIGRATIONS: tuple[Migration, ...] = (...)   # ordered, gapless from 1

def current_version(engine) -> int          # reads schema_migrations, 0 if absent
def pending(engine) -> list[Migration]
def migrate(engine, *, dry_run: bool = False) -> MigrationReport
```

```sql
CREATE TABLE schema_migrations (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
```

* `migrate()` runs each pending migration **in its own transaction**; a failure
  rolls that migration back and stops, leaving the version at the last success.
* `dry_run=True` returns what WOULD run and touches nothing.
* `MigrationReport` carries `from_version`, `to_version`, `applied: list[str]`,
  `skipped`, and `error` — a report, not a bool (§Interface rule below).
* Version 1 is **a stamp, not a change**: on a database whose tables already
  exist it records `version=1` and does nothing else; on an empty database
  `init_db` creates the tables and then the stamp is recorded.
* `init_db` keeps its additive `ALTER TABLE` behaviour for now (removing it is a
  later decision) but **must also add the missing indexes** it currently skips.

CLI: `hescope migrate [--dry-run] [--db-url URL]` printing the report.

### Tests (`tests/test_migrations.py`)

1. A fresh database ends at `SCHEMA_VERSION` with every migration recorded.
2. `migrate()` is idempotent — a second call applies nothing.
3. `dry_run=True` changes neither `schema_migrations` nor any table.
4. A migration that raises leaves the version at the previous value and no
   partial rows (use an injected failing migration).
5. **The index gap**: a database built by the old narrow path, upgraded through
   `init_db`, has the same indexes on `rois` as a fresh `create_all`. This is the
   one that must be seen to fail first — it fails today.
6. An out-of-order or gapped `MIGRATIONS` tuple is rejected at import time.

### Done when

`pytest -q tests/test_migrations.py` green, full suite green, and
`hescope migrate --dry-run` against a **copy** of `data/hescope.db` reports
"would stamp version 1, apply 0 changes".

---

## Phase 1 — the SVS ↔ ROI relationship

### Goal

An ROI stays attached to its slide regardless of where the file moves, how it is
opened, or how many times it is registered concurrently; and the viewer can ask
for only the ROIs on screen.

### The four defects being fixed (all measured)

| # | Defect | Evidence |
| --- | --- | --- |
| 1.1 | Identity is the file path | `demo_he.png` is slides 3 and 31, the second with 0 ROIs; 18 of 31 rows point at files that no longer exist |
| 1.2 | `register` is check-then-act | 8 concurrent registrations of one slide → **3 raised `IntegrityError`** |
| 1.3 | `normalize_slide_path` mangles foreign paths | on Windows, `/mnt/nfs/slides/A1.svs` → `E:\mnt\nfs\slides\A1.svs`, so a shared store splits per workstation |
| 1.4 | `bbox_json` is TEXT | the viewport query cannot be expressed at all; `for_slide` returns everything — 138 ms + 170 KB at 5 000 ROIs, on every overlay re-render |

### Schema (migration version 2)

```sql
-- identity that does not assume a local file
ALTER TABLE slides ADD COLUMN identity_scheme TEXT;   -- 'sha256'|'dicomweb'|'omero'|'path'
ALTER TABLE slides ADD COLUMN identity_key    TEXT;
ALTER TABLE slides ADD COLUMN file_size       INTEGER;
ALTER TABLE slides ADD COLUMN md5sum          TEXT;
CREATE UNIQUE INDEX ux_slides_identity ON slides(identity_scheme, identity_key)
  WHERE identity_scheme IS NOT NULL;

-- many locations, one slide
CREATE TABLE slide_files (
  id            INTEGER PRIMARY KEY,
  slide_id      INTEGER NOT NULL REFERENCES slides(id) ON DELETE CASCADE,
  path          TEXT NOT NULL UNIQUE,
  source_kind   TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  missing_since TEXT
);
CREATE INDEX ix_slide_files_slide ON slide_files(slide_id);

-- a bbox the database can filter on
ALTER TABLE rois ADD COLUMN bbox_x0 REAL;
ALTER TABLE rois ADD COLUMN bbox_y0 REAL;
ALTER TABLE rois ADD COLUMN bbox_x1 REAL;
ALTER TABLE rois ADD COLUMN bbox_y1 REAL;
CREATE INDEX ix_rois_slide_bbox ON rois(slide_id, bbox_x0, bbox_x1);
```

`slides.path` **stays** and stays UNIQUE — R-4. It becomes a cache of the
most-recently-seen path; `slide_files` is the record.

### Backfill (inside migration 2)

* For every existing slide, insert one `slide_files` row from `slides.path`,
  `first_seen_at = last_seen_at = slides.created_at`, and set `missing_since` to
  the migration timestamp **iff the path does not resolve**.
* Compute `identity_scheme='sha256'`, `identity_key=<content_key>`, `file_size`
  for every path that resolves; leave both NULL where it does not (the partial
  unique index above permits many NULLs).
* Backfill the four `bbox_*` columns from `bbox_json`.
* **Do not merge duplicate slides in this migration.** Detecting them is
  migration 2's job; merging them moves ROIs between rows and is a separate,
  reviewable step (`hescope dedupe-slides --by-identity --dry-run`), because it
  is the only destructive operation in the plan.

### Code

`hescope/identity.py` (new):

```python
CONTENT_KEY_HEAD = 1 << 20      # 1 MiB
def content_key(path) -> tuple[str, int] | None:
    """sha256(size || head 1MiB || tail 1MiB), and the size. Measured 0.002 s
    on a 177 MB slide against 0.17 s for a full hash."""
def slide_identity(source_kind, path=None, **kw) -> tuple[str, str] | None:
    """(scheme, key). 'path' scheme is the last resort, never the default for a
    readable local file."""
```

`hescope/db.py`:

* `SlideRepo.register(...)` gains `identity: tuple[str, str] | None = None`, and
  resolves in this order: supplied identity → computed from a readable path →
  `('path', normalize_slide_path(path))`.
* **Race fix**: replace check-then-act with `INSERT ... ON CONFLICT DO UPDATE`
  on the identity index (and on `path` for the legacy route), then re-select.
  Must survive 16 concurrent registrations with 0 errors and exactly 1 row.
* `register` also upserts the `slide_files` row (`last_seen_at` refreshed,
  `missing_since` cleared when the path resolves again).
* `ROIRepo.add` writes the four `bbox_*` columns; a single private helper
  derives them from the geometry so there is one writer.
* New `ROIRepo.in_viewport(slide_id, x0, y0, x1, y1, *, limit=2000) -> list[dict]`
  pushing the bbox predicate into SQL.
* `ROIRepo.for_slide` gains `limit: int | None = None` (default unchanged so no
  caller breaks) and `offset`.
* `normalize_slide_path` must not apply local drive semantics to a path that is
  not on this machine: if `Path(p).resolve()` would change a POSIX absolute path
  into a drive-prefixed one, keep the original string.

### Tests

* `tests/test_slide_identity.py` — content key stability (same bytes → same key,
  one byte changed → different key), the `path` fallback, and DICOMweb/OMERO
  identities round-tripping without a file.
* `tests/test_register_race.py` — **16 threads registering one slide: 0 errors,
  1 row.** This must be seen to fail today (measured: 3 of 8 raise).
* `tests/test_slide_files.py` — one file at three paths is one slide with three
  `slide_files` rows; a path that disappears gets `missing_since` and the ROIs
  stay reachable.
* `tests/test_roi_viewport.py` — `in_viewport` returns only intersecting ROIs;
  compare against a Python filter over `for_slide` on 2 000 ROIs and assert
  identical id sets; assert the SQL path is faster (report both numbers).
* `tests/test_migrations.py` gains migration-2 cases including **R-3**: a slide
  row's `created_at` appears unchanged in its `slide_files.first_seen_at`.

### Done when

Full suite green; a dry-run of migration 2 against a **copy** of the real
database reports the counts it would write (expect: 31 `slide_files` rows, 18
marked missing, ~28 distinct identities, 10 ROIs backfilled) and the report is
included in the commit message.

---

## Phase 2 — TCGA download → storage → injection

### Goal

A downloaded TCGA file becomes a slide row that is connected to its case and
sample, automatically, at download time.

### The defects

| # | Defect | Evidence |
| --- | --- | --- |
| 2.1 | The hierarchy is an island | `tcga_files.slide_id` written **0 of 50**; 0 of 31 slides reachable from the hierarchy; the join exists and has no rows |
| 2.2 | The catalogue is stored twice | `data/tcga/catalog.db` (`tcga_slides`, 50) and `data/hescope.db` (`tcga_files`, 50) hold the same 50 files |
| 2.3 | `records_to_rows` omits `md5sum` | so app.py's `_sel[0].get("md5sum") or <catalog scan>` has a permanently dead first branch, and the fallback is an O(n) scan of up to 100 000 rows per click |
| 2.4 | `TcgaCatalog.mark_downloaded` on an unknown id | **invents a metadata-free row that then counts as downloaded** (`tcga_schema.py:370-379`) |

### Changes (migration version 3 + code)

* `tcga_files.slide_id` gains a real `ForeignKey(slides.id) ON DELETE SET NULL`
  (it is currently indexed but unconstrained).
* **Write the link.** On a completed download, `mark_downloaded` takes the
  `slide_id` produced by registering the file and stores it; `slides` gains its
  identity from the **GDC-supplied md5** (`identity_scheme='sha256'` is the
  content key; store the GDC md5 in `slides.md5sum` and verify it).
* Backfill in migration 3: for every `tcga_files` row with a `local_path` that
  resolves, register/lookup the slide and set `slide_id`. Report how many linked
  and how many could not be.
* `records_to_rows` carries `md5sum`, and the app's md5 lookup becomes a keyed
  query rather than a scan.
* `mark_downloaded` on an unknown id returns `False` and writes nothing —
  both implementations, same contract.
* **2.2 is a decision, not a fix**: state the intended end state in the phase's
  own doc — `hescope.db` is the record, `catalog.db` is a cache of GDC search
  results — and make `migrate-tcga-catalog` idempotent and provenance-preserving
  (**it must not reset `first_seen_at`**; that is R-3's exemplar).

### Tests

* `tests/test_tcga_injection.py` — a simulated completed download produces a
  slide row, a `slide_files` row, and a `tcga_files.slide_id` pointing at it;
  the case/sample/project chain resolves from the slide in one query.
* Re-running the download link is idempotent (no second slide).
* `mark_downloaded('no-such-id')` returns False and adds no row — **seen to fail
  today for `TcgaCatalog`**.
* A migration-3 test asserting `first_seen_at` from the source catalogue is
  preserved (the exact defect that lost 25 h).

### Done when

Full suite green, and a dry-run against a copy reports the number of
`tcga_files` rows it would link (expect 1 — only one slide is downloaded).

---

## Phase 3 — user-supplied databases

### Goal

The app runs on a database the user already has, and reads slides from a store
the user already has.

### Two independent axes — do not conflate them

**3a. Backend diversity (the SQL engine).** `README.md:230` promises "switching
to PostgreSQL is just an environment variable". Measured: **no driver ships**
(psycopg2 / psycopg / pymysql / asyncpg all absent, no `db` extra in
`pyproject.toml`), plus SQLite-specific assumptions.

* Add optional extras: `[project.optional-dependencies] postgres = ["psycopg[binary]>=3"]`,
  `mysql = ["pymysql>=1"]`. **Measure and report the install cost of each.**
* Audit every SQLite-specific construct and either make it portable or guard it
  by backend: the pragma hook (already guarded), `init_db`'s `PRAGMA table_info`
  upgrade, `INSERT ... ON CONFLICT` (Postgres-compatible; MySQL is not — use
  SQLAlchemy's dialect-aware upsert or a documented limitation), and any
  `sqlite3` import outside `SlideCatalog`.
* A test matrix that runs the repository contract tests against SQLite and, when
  `HESCOPE_TEST_PG_URL` is set, Postgres — **skipped, not failed, when unset**.
* Correct `README.md` and `USER_GUIDE.md` to say what is actually true.

**3b. Source diversity (where slides live).** Phase 1's `(scheme, key)` identity
already admits non-file slides. The cheapest real win is measured:
`dicomweb-client` **0.61.1 is already installed** (a hard dependency of
`wsidicom`) and `WsiDicom.open_web` exists, so DICOMweb reading costs **0 new
packages**.

* `hescope/sources/dicomweb.py`: open a slide by `(study_uid, series_uid)` from
  a DICOMweb endpoint, producing a `SlideSource` with
  `identity=('dicomweb', f'{study}/{series}')`.
* A folder-of-SVS + labels-CSV importer with an explicit column map.
* A QuPath **project** importer (walk a project; the per-file GeoJSON parser
  already exists in `hescope/importers.py`).

### Tests

* Contract tests parametrised over backends (Postgres skipped when unset).
* DICOMweb source tested against a recorded/served fixture, never a live PACS.
* The folder+CSV importer: a report of what it skipped and why, as
  `import_annotations` already does.

### Done when

Full suite green; `pytest` with `HESCOPE_TEST_PG_URL` set is documented as the
Postgres path; README/USER_GUIDE no longer overstate.

---

## Interface rules that apply to all new code

* **A write returns what it did**: the new id, or `bool` for "an existing row
  changed". Never `None`.
* **Agent-facing wrappers never raise** (`AGENTS.md` is a contract) and therefore
  must convert an exception into the documented error shape rather than swallow
  it. `annotate_roi` currently discards `update_annotation`'s bool and can return
  a bare `'null'` — fix it in whichever phase touches it.
* **No method returns an unbounded result set.** Every list takes `limit`.
* Timestamps are naive UTC in the database, converted at the read boundary.
  Never pass an aware datetime to a SQLite `DATETIME` column — SQLAlchemy
  silently discards the offset.

---

## Explicit non-goals

Out of scope for all three phases, to keep the diff reviewable:

* Merging duplicate slides in the live database (a separate consented step).
* The `measurements` table and the resolution-validity work — that is the
  autonomy track, not the storage track.
* Any change to `app.py`'s UI beyond what a new repository signature forces.
* Removing `init_db`'s additive upgrade path.
* OMERO.
