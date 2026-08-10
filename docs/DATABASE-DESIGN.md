# Database design — what is wrong, and what to build instead

Every number below was measured against the live `data/hescope.db` on
2026-08-10, not estimated. Reproduce with the queries quoted in each section.

---

## 1. The finding that matters

```
tcga_projects   2 rows      tcga_files.md5sum populated       50 / 50
tcga_cases     38 rows      tcga_files.slide_id written        0 / 50
tcga_samples   39 rows      slides reachable from TCGA         0 / 31
tcga_files     50 rows      slides with NO specimen context   31 / 31
```

**The specimen hierarchy and the slides you actually open are two disconnected
graphs.** The TCGA tables were built to answer "which case is this slide from",
and `tcga_files.slide_id` — the single column that would answer it — has never
been written. Not once, in 50 files and 31 slides.

So "show me every ROI on primary-tumour slides from case TCGA-XX" is not a hard
query. It is a query with **no join path that has rows in it**. The hierarchy is
furniture.

The link is *available*: `md5sum` is populated for all 50 files. Nothing
consumes it (`records_to_rows` omits it entirely — SUMMARY.md open item 6, still
latent). The data to connect the two graphs is sitting in the database.

---

## 2. Six structural faults, with evidence

### 2.1 Foreign keys are not enforced

```sql
PRAGMA foreign_keys;   -- 0
```

SQLite ignores foreign keys unless the pragma is set **per connection**, and
nothing sets it. Every `ON DELETE CASCADE` and `ON DELETE SET NULL` in the DDL
is decorative. Deleting a slide today leaves its ROIs behind, pointing at a row
that is gone — silently.

There are no orphans yet only because nothing has been deleted. The first slide
deletion creates them.

Two references are not even declared: `interactions.roi_id` and
`tcga_files.slide_id` are indexed but unconstrained, while `agent_runs.roi_id`
next door has a proper FK. Nothing distinguishes these cases; the difference is
an accident.

### 2.2 A slide's identity is its file path

`SlideRepo.register` is idempotent on `UNIQUE(path)`. `normalize_slide_path`
canonicalises two *spellings* of one path — it cannot know that two *different*
paths hold the same slide.

```
id=3    assets\demo_he.png                              2 ROIs
id=31   ...\scratchpad\demo_he.png                      0 ROIs
```

Same image, two identities, and the annotations are only on one of them. Repeat
this across the table:

```
14x  other_slide.png      17 rows share the fingerprint (600, 400, mpp=None)
 4x  small_slide.png
 2x  demo_he.png
```

and 18 of 31 rows point at files that no longer exist. `slides` is not a table
of slides. It is an append-only log of *paths this app once opened*.

This is the structural form of R05-2 / R07-3. `hescope dedupe-slides` repairs
the symptom by heuristic; the cause is that the table has no notion of a slide's
identity independent of where its bytes happen to sit.

### 2.3 Measurements are an opaque blob

```sql
SELECT avg(tissue_fraction) FROM rois;   -- OperationalError: no such column
```

Every measurement lives inside `stats_json`. The entire `hescope/stats_table.py`
module exists to read those blobs row by row and reshape them in Python, because
the database cannot aggregate its own contents.

The shape happens to be uniform today (10 of 10 rows carry the same five keys),
so this is not a data-cleanliness problem. It is that the schema declines to
model the thing the tool exists to produce.

It also loses the one field that makes measurements comparable. Two ROIs on the
same slide, measured this session:

| ROI | bbox (level-0) | patch | effective mpp |
| --- | --- | --- | --- |
| 9 | 1440 x 1294 | 1024 x 920 | 0.355 µm/px |
| 10 | 3935 x 3295 | 1024 x 857 | **0.971 µm/px** |

Their eosin means differ by 40%. Nothing in the database says they were measured
at resolutions 2.7x apart, so nothing stops anyone — a person or an agent —
averaging them. That is open item 3 (R06-1) and R07-2, and it is a *schema*
problem: the comparability key is not stored.

### 2.4 Nothing constrains duplicates

```
slide_id=2  bbox [11443,10697,12883,11991]  x3   (ids 7,8,9)
slide_id=3  bbox [1000,800,1400,1200]       x2   (ids 1,3)
```

Three identical ROIs because "Send to code agent" was clicked three times. Each
is a full row and each counts as an independent sample in `label_summary`'s `n`
and SD. A per-label mean over that data is wrong in a way no reader can see.

### 2.5 Derived values are stored as second truths

`bbox_json` is recomputable from `points_json`; `tcga_files.case_submitter_id`
and `project_id` duplicate what `tcga_samples` already knows. They agree today
(measured: 0 disagreements, 0 broken sample chains) — nothing keeps them that
way. This is the recurring class from `bugs/SUMMARY.md`: *a second place
re-deriving what one owner decides*.

`bbox_json` is worse than redundant: it is JSON **text**, so it cannot be used
for a spatial query. "Which annotations contain this point" is unanswerable in
SQL despite the data being right there.

### 2.6 There is no migration story

```sql
PRAGMA user_version;   -- 0
```

No version marker, no runner, no `alembic`. `hescope/db.py:106` states it
outright: *"there is no migration"*. Yet `hescope migrate-tcga-catalog` exists —
migrations are already happening, ad hoc, with nothing recording what has been
applied to a given file.

---

## 3. The design

**Organise around the specimen, not the file.** A path is an accident of where
bytes sit this week. A slide is a physical object cut from a sample, taken from
a case, belonging to a project. That hierarchy is what a pathologist works in,
what TCGA publishes, and what the questions are asked in.

Four layers, each with one owner.

### L1 — Specimen context (generalised beyond TCGA)

```sql
CREATE TABLE projects (
  id            INTEGER PRIMARY KEY,
  source        TEXT NOT NULL,             -- 'tcga' | 'local' | 'import'
  external_id   TEXT,                      -- 'TCGA-BRCA'
  program TEXT, name TEXT, disease_type TEXT, primary_site TEXT,
  UNIQUE (source, external_id)
);

CREATE TABLE cases (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  source        TEXT NOT NULL,
  external_id   TEXT,                      -- 'TCGA-BH-A18H'
  case_uuid     TEXT,
  UNIQUE (source, external_id)
);

CREATE TABLE samples (
  id            INTEGER PRIMARY KEY,
  case_id       INTEGER REFERENCES cases(id) ON DELETE CASCADE,
  source        TEXT NOT NULL,
  external_id   TEXT,
  submitter_id  TEXT,                      -- 'TCGA-BH-A18H-01A'
  sample_type   TEXT,                      -- 'Primary Tumor' | 'Solid Tissue Normal'
  tissue_type   TEXT,
  UNIQUE (source, external_id)
);
```

`source` is what lets a locally scanned slide sit in the same hierarchy as a
TCGA one instead of needing a parallel set of tables. The TCGA-specific columns
that do not generalise stay in `extra_json`.

### L2 — The slide, identified by content

```sql
CREATE TABLE slides (
  id            INTEGER PRIMARY KEY,
  content_key   TEXT NOT NULL UNIQUE,      -- sha256(size || head 1MiB || tail 1MiB)
  file_size     INTEGER NOT NULL,
  md5sum        TEXT,                      -- authoritative when the source supplies one
  sample_id     INTEGER REFERENCES samples(id) ON DELETE SET NULL,
  name          TEXT NOT NULL,
  width INTEGER NOT NULL, height INTEGER NOT NULL,
  mpp REAL, objective_power REAL, level_count INTEGER, vendor TEXT,
  extra_json    TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE slide_files (                 -- many locations, one slide
  id            INTEGER PRIMARY KEY,
  slide_id      INTEGER NOT NULL REFERENCES slides(id) ON DELETE CASCADE,
  path          TEXT NOT NULL UNIQUE,
  source_kind   TEXT NOT NULL,             -- 'local' | 'tcga' | 'upload'
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  missing_since TEXT                       -- set when the path stops resolving
);
```

`slide_files` is the whole fix for §2.2. The 18 dead paths become
`missing_since` stamps on files, not 18 phantom slides; `demo_he.png` at two
locations becomes one slide with two files, and its two ROIs are reachable from
either.

**Why a partial hash.** Measured on the 177 MB TCGA slide: full sha256 0.17 s,
`size + head + tail` **0.002 s** — 84x cheaper, and it does not grow with the
2–8 GB WSIs this tool is aimed at. Collisions are guarded by also storing
`file_size`, and by `md5sum` where the source is authoritative (GDC supplies it
for all 50 files today).

### L3 — Observation

```sql
CREATE TABLE annotations (                 -- was: rois
  id            INTEGER PRIMARY KEY,
  slide_id      INTEGER NOT NULL REFERENCES slides(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL,             -- rect | polygon | circle
  points_json   TEXT NOT NULL,             -- the ONLY geometry of record
  geom_key      TEXT NOT NULL,             -- hash(kind, points rounded to 1 px)
  bbox_x0 REAL NOT NULL, bbox_y0 REAL NOT NULL,   -- derived cache, one writer
  bbox_x1 REAL NOT NULL, bbox_y1 REAL NOT NULL,
  label         TEXT NOT NULL DEFAULT '',
  notes         TEXT NOT NULL DEFAULT '',
  created_by    TEXT NOT NULL,             -- 'user' | 'agent' | 'import'
  origin        TEXT,                      -- 'add_roi' | 'send' | 'geojson' | 'asap'
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (slide_id, geom_key)
);
```

Four `REAL` bbox columns instead of one JSON string buys the spatial query the
app cannot currently express:

```sql
-- annotations under the cursor
SELECT * FROM annotations
 WHERE slide_id = ? AND bbox_x0 <= ? AND bbox_x1 >= ?
                    AND bbox_y0 <= ? AND bbox_y1 >= ?;
```

`created_by` matters in a tool where an agent writes annotations: today nothing
distinguishes a region a pathologist drew from one a model proposed.

```sql
CREATE TABLE measurements (
  id            INTEGER PRIMARY KEY,
  annotation_id INTEGER NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,             -- 'tissue_fraction' | 'hematoxylin_mean'
  value         REAL,
  unit          TEXT,                      -- '' | 'um' | 'mm2' | '1/mm2'
  method        TEXT NOT NULL,             -- 'roi_stats@v2' | 'macenko' | model id
  mpp_effective REAL,                      -- the comparability key (§2.3)
  params_json   TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL
);
```

Now the question the tool is for is a query:

```sql
SELECT a.label, count(*) n, avg(m.value), max(m.value) - min(m.value)
  FROM measurements m JOIN annotations a ON a.id = m.annotation_id
 WHERE a.slide_id = ? AND m.name = 'tissue_fraction'
   AND m.mpp_effective BETWEEN 0.30 AND 0.45      -- comparable resolutions only
 GROUP BY a.label;
```

The `mpp_effective` filter is the point. It makes the R07-2 / R06-1 trap
*expressible*: rows measured 2.7x apart cannot be silently averaged, because
excluding them is a `WHERE` clause rather than a piece of tribal knowledge.

Re-measuring appends instead of overwriting, so a metric has a history.

### L4 — Provenance

Keep `interactions` and `agent_runs`, with the FKs actually declared and
enforced. Add:

```sql
CREATE TABLE schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL,
  note       TEXT
);
```

and set the pragma on every connection, which is a four-line SQLAlchemy hook:

```python
@sa.event.listens_for(engine, "connect")
def _fk_on(dbapi_conn, _rec):
    dbapi_conn.execute("PRAGMA foreign_keys=ON")
```

---

## 4. Migration path

Staged, each step independently shippable and reversible, on a copy first. The
live file gets a backup before any step (as `dedupe-slides` already does).

| v | Step | Risk |
| --- | --- | --- |
| 1 | `schema_migrations`; turn `PRAGMA foreign_keys=ON`; declare the two missing FKs | **Behaviour change**: deletes start cascading. Needs tests before, not after. |
| 2 | `slides.content_key` / `file_size` / `md5sum` + `slide_files`; backfill by hashing paths that still resolve; merge rows sharing a key, moving annotations to the survivor | Merging is the destructive part. Report the plan, apply on confirmation — the `--dry-run` pattern R07-3 established. |
| 3 | `projects` / `cases` / `samples` from the `tcga_*` tables with `source='tcga'`; add `slides.sample_id`; **backfill via `md5sum`** | Low: additive. This is the step that gives §1's empty join its first rows. |
| 4 | `measurements`; backfill from `stats_json` (uniform 5-key shape, 10 rows); keep `stats_json` as a cache for one release | Low: additive, old readers keep working. |
| 5 | `annotations.geom_key` + `UNIQUE`; **report** the 2 duplicate groups rather than silently dropping them | Needs a decision on which of ids 7/8/9 survives. |

Steps 3 and 4 are additive and could land first if the appetite for step 1's
delete-semantics change is low.

---

## 5. Verified, not proposed

Steps 2–4 were prototyped against a **copy** of the live database
(`scratchpad/proto_migration.py`; `data/hescope.db` was never opened for
writing). Output:

```
slides    31 rows  ->  28 distinct slides  (18 paths no longer resolve)
slides linked to a sample via md5sum: 1    (live database today: 0)
measurements extracted from stats_json blobs: 30 rows

Q1  one slide, every location it has been seen at
    slide 16  small_slide.png    3 paths, 0 missing
    slide 3   demo_he.png        2 paths, 0 missing

Q2  which case/sample is this slide from?
    HCM-CSHL-0817-C22-01A-02-S2-HE
      sample=HCM-CSHL-0817-C22-01A (Primary Tumor)
      case=HCM-CSHL-0817-C22   project=HCMI-CMDC

Q3  comparable measurements only, aggregated in SQL
    eosin_mean        n=4  mean=0.0197   mpp 0.355 .. 0.971
    hematoxylin_mean  n=4  mean=0.0362   mpp 0.355 .. 0.971
    tissue_fraction   n=4  mean=0.8517   mpp 0.355 .. 0.971
```

All three were unanswerable before. Q3's `mpp` column is the one to look at: the
spread 0.355–0.971 is the R07-2 trap, and it is now a value in a row rather than
something a reader has to already know.

Only **one** slide links to a sample because only one slide has actually been
downloaded — that is the correct answer, not a shortfall of the backfill.

### The tables are misnamed, which is itself evidence

Q2 resolves the project as **HCMI-CMDC** — the Human Cancer Models Initiative,
not TCGA. The `tcga_*` tables are already holding non-TCGA data, because the GDC
API they were built against serves every program under one schema. Naming the
tables after one program was wrong the day the second program arrived, and it
has. `source`-tagged `projects` / `cases` / `samples` is not future-proofing; it
is describing what the rows already are.

---

## 6. Trade-offs, stated

- **Long-form `measurements` costs rows and needs a pivot for display.**
  `stats_table.py` already pivots; it would read from SQL instead of from JSON.
  What is bought: aggregation in the database, measurement history, and a
  comparability key that cannot be forgotten.
- **A partial content hash can collide in theory.** Guarded by `file_size` and
  by `md5sum` where the source is authoritative. Full hashing stays available
  behind a flag for anyone who wants it; the measured cost is 0.17 s per open on
  this slide, and grows with file size where the partial hash does not.
- **`UNIQUE(slide_id, geom_key)` forbids deliberate duplicates.** Today's
  duplicates are all accidents (a double-clicked Send). If duplicates are ever
  wanted, the honest form is an explicit `replicate_of` column, not an absent
  constraint.
- **Enabling foreign keys changes what deletes do.** That is the point, and it
  is why it is step 1 with tests, rather than a silent flip.
- **None of this helps DB-free mode**, where `db.enabled` is False and the
  session list is the only store. That split is a separate design question — see
  the ROI-panel discussion — and this schema neither fixes nor worsens it.
