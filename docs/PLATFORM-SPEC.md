# Platform specification — storage, interfaces, interop, autonomy, UI

Synthesis of five parallel explorations run against this repository on
2026-08-11 (workflow `wf_2c515e76-d07`, 5 agents, 310 tool calls). Every claim
below was either measured by an agent with a cited command, or re-measured here
before being written down. Where the two disagreed, the number reproduced here
is the one quoted.

---

## Part 0 — What this exercise corrected

Three committed documents were wrong. That is the most valuable output, so it
goes first.

### 0.1 `DATABASE-DESIGN.md` §2.1 — foreign keys ARE enforced (my error)

I wrote that "every `ON DELETE CASCADE` in the DDL is decorative", on the
strength of `PRAGMA foreign_keys` returning `0`. **Three of the five agents
independently caught it.** The measurement was taken on a bare
`sqlite3.connect`, which is not how the application connects:

```
sqlite3.connect(...)   PRAGMA foreign_keys -> 0
get_engine(...)        PRAGMA foreign_keys -> 1      <- the app
```

`hescope/db.py:63-70` registers a `connect` listener setting the pragma, present
since the initial commit (`git log -S`), and `tests/test_db.py` already covers
the cascade. §2.1 has been rewritten with the narrower defects that are real:
`interactions.roi_id` and `tcga_files.slide_id` carry no `ForeignKey` at all
(an `interactions(roi_id=9999)` insert is **accepted**), raw-`sqlite3` write
paths bypass the pragma, and no test asserts it.

**The lesson is not "check twice".** It is that I measured the *store* and
reported it as a property of the *application*, which is the same shape as the
defect class this project has spent ten rounds naming.

### 0.2 `DESIGN-AGENT-WORKBENCH.md` §1.2 — the stain reference matrix already exists

I wrote that re-wiring stain normalisation "needs a slide-level reference
matrix, not a checkbox". The matrix is already fitted and stored:
`hescope/tileserver.py:334-337` calls `_stain.fit_reference(thumb)` into
`SlideRefs.stain_source`. What is missing is smaller and nameable: a control,
and a method selector wired to `STAIN_METHODS`.

### 0.3 `bugs/SUMMARY.md` — two of three "return None" claims are wrong

Round 08's suggested reading list said `db.trace`, `AgentRunRepo.record` and
`SlideCatalog.mark_downloaded` all return `None`. Measured: `AgentRunRepo.record`
returns the new row id (`hescope/db.py:567`), and `DBContext.trace` returns
`int | None` (`hescope/viewer.py:449`). Only `SlideCatalog.mark_downloaded`
holds.

---

## Part 1 — Storage conventions

Rules, each justified by a measured defect. Written to be pasted into a
contributor doc.

### 1.1 WAL, not the default journal — the one with a user-visible cost

```
PRAGMA journal_mode -> delete          (measured, the default)
```

With `journal_mode=delete`, an ROI save **fails after 5.5 s with "database is
locked"** the moment anything else holds a read; the same write under WAL
completes in 0.00 s. Two marimo sessions, or the app plus a `hescope` CLI call,
or the app plus an agent reading over marimo-pair, are all that is required —
and the last of those is the workflow this project is built around.

> **Rule.** `get_engine` sets `journal_mode=WAL` and `busy_timeout` on every
> SQLite connection, beside the existing `foreign_keys=ON`. A test asserts all
> three.

### 1.2 One timestamp convention

Four coexist, two of them writing into the same file: `hescope/db.py:35` naive
UTC, `hescope/tcga_schema.py:57` **aware** UTC, `hescope/ml.py:69` aware ISO
strings, `hescope/agent_bridge.py:146` `%Y%m%dT%H%M%S%f` with no offset.

The aware one is a live hazard, not a style preference: SQLAlchemy's SQLite
`DATETIME` **silently discards the offset**. Storing `12:00+08:00` writes
`'2026-01-01 12:00:00.000000'`, and `db._iso` then relabels it `+00:00` — an
eight-hour error with no exception anywhere. It is accidentally correct today
only because every writer happens to be in UTC.

> **Rule.** Store naive UTC. Convert at the read boundary (`db._iso` already
> does). Never pass an aware datetime to a SQLite `DATETIME` column; a test
> rejects one.

### 1.3 Migrations: the one that exists already lost data

`hescope migrate-tcga-catalog` **reset `first_seen_at` on all 50 TCGA rows** —
the source carries one timestamp (`2026-08-09T09:58:01`), the destination
carries 50 distinct insert times (`2026-08-10 11:01:56.53x`). 25 hours of
provenance, gone. Cause: `hescope/cli.py:312-327` builds its rows without a
`first_seen_at` key, so the column falls to `default=_utcnow`.

Its five tests could not have caught it: they assert counts (`stats()["files"]
== 3`) and never compare a source column to its destination column.

Worse, `init_db`'s implicit upgrade (`create_all` + `PRAGMA table_info` +
`ALTER TABLE ADD COLUMN`, `db.py:98-129`) produces a **materially different
schema** from a fresh `create_all` — an upgraded `rois` has zero indexes where a
fresh one has two, and a `server_default` is silently dropped — while both
report `user_version = 0`.

> **Rule.** A `schema_migrations` table, forward-only, each step a function with
> a version. Stamp the current schema as version 1 before anything else. Every
> migration ships with a test that reads a value from the source and asserts it
> in the destination — counts are not evidence. Back up before, `--dry-run`
> first (the pattern R07-3 already established).

### 1.4 Invariants belong in the database

Declare the two missing foreign keys (§0.1). Add the uniqueness that stops the
duplicate ROIs already in the file. Make the raw-`sqlite3` paths take the same
pragmas, or route them through `get_engine`.

### 1.5 What an agent's write must carry that a human's need not

`created_by` is not enough. An autonomous write must also carry the **method and
version**, the **parameters**, and the **effective resolution** — see Part 4,
where a number's resolution turns out to be the whole ballgame.

---

## Part 2 — Interface conventions

### 2.1 The current contract is four contracts

`update_annotation`/`delete` → `bool`; `add`/`register`/`record` → `int`;
`InteractionRepo.record` → `int | None`, never raises; `SlideRepo.delete` →
`None`; `SlideCatalog.mark_downloaded` → `None`; `import_annotations` →
`list[int]`. Nothing picks between them.

The consequences are concrete. `TcgaCatalog.mark_downloaded` on an unknown id
**invents a metadata-free row that then counts as downloaded**
(`tcga_schema.py:370-379`), while `SlideCatalog.mark_downloaded` on the same
input silently no-ops — two implementations of one name behaving oppositely, and
neither reporting which happened.

> **Rule.** A write returns what it did: the id it created, or `bool` for
> "existing row changed". Never `None`. A method that cannot fail says so in its
> name or docstring; everything else raises. Agent-facing wrappers never raise —
> that is `AGENTS.md`'s contract — and must therefore convert, not swallow.

### 2.2 The agent's only write path drops its own outcome

`hescope/agent_bridge.py:275` calls `roi_repo.update_annotation(rid, ...)` and
**discards the `bool`** that round 07 added precisely so a caller could tell a
no-op from a write. Measured consequence: with the row deleted between the
existence check and the write, `annotate_roi` returned the bare JSON string
`'null'` — a fourth return shape, documented nowhere — and still wrote an
`interactions` row claiming a label was set.

### 2.3 Paging is done in Python

`query_annotations(limit=50)` fetches **every** row and slices. Measured: 17.4 ms
at 5 000 ROIs, against 0.7 ms for SQL `LIMIT 50` — flat to 20 000. And
`ROIRepo.for_slide` has no limit at all (138 ms and 170 KB at 5 000, on every
overlay re-render).

> **Rule.** Every list method takes `limit`/`offset` and pushes them into SQL. A
> viewport query takes a bbox. No method returns an unbounded result set.

### 2.4 A versioned public API

The repositories are internal. A lab writing its own tooling (Part 3) needs a
surface that will not move: `hescope.api` with an explicit version, wrapping the
repos, returning plain dicts, and covered by tests that fail when the shape
changes.

---

## Part 3 — Bring your own database

The user's point: **we must not assume every user downloads from TCGA and has no
database of their own.** Measured, the story is worse than "one env var" and the
cheapest win is already paid for.

### 3.1 What is measured

| Claim in our docs | Measured |
| --- | --- |
| "Switching to PostgreSQL is just an environment variable" (`README.md:230`, `USER_GUIDE.md §4.4`) | **No driver ships.** `psycopg2`, `psycopg`, `pymysql`, `asyncpg` all absent; no `db` extra in `pyproject.toml`. Plus three unfixed SQLite-specific assumptions. |
| Slide identity is "an accident of where bytes sit" — an identity problem | Also a **race**: 8 concurrent registrations of one slide raised `IntegrityError` on **3 of 8**. `SlideRepo.register` is check-then-act. |
| `normalize_slide_path` canonicalises spellings | On Windows it turns a peer's `/mnt/nfs/slides/A1.svs` into `E:\mnt\nfs\slides\A1.svs`. **A shared store splits per workstation before identity is even discussed.** |
| DICOMweb would be a new dependency | **`dicomweb-client` 0.61.1 is already installed** — `wsidicom` hard-depends on it — and `WsiDicom.open_web` exists. Reading from a PACS costs **0 new packages**. |

### 3.2 `content_key NOT NULL` is wrong for this case

`DATABASE-DESIGN.md` §3 makes a content hash the slide's identity, `NOT NULL`.
That forecloses every slide that is not a local file — which is the entire BYO
case. A DICOMweb series has no file and no size; its identity is
`(StudyInstanceUID, SeriesInstanceUID)`. An OMERO image's is its image id.

> **Revision.** Identity becomes `(scheme, key)`: `('sha256', '<hash>')` for a
> local file, `('dicomweb', '<study>/<series>')`, `('omero', '<id>')`,
> `('path', '<normalised>')` as the last resort. `UNIQUE(scheme, key)`. The
> content hash stays the default for local files and stops being the only
> vocabulary.

### 3.3 The stance

**Adapters that import, plus export, plus a live reader for DICOMweb** — not
full federation. Federation means every query crosses a network the lab controls
and we do not, and it makes the ROI/measurement provenance chain depend on their
uptime. Import gives us one store to reason about; DICOMweb gets a live reader
because it is free (§3.1) and because a PACS is authoritative in a way a folder
is not.

Ordered by measured cost:

1. **DICOMweb read** — 0 new packages, `WsiDicom.open_web`.
2. **A folder of SVS + a CSV of labels** — no new code beyond a CSV column map.
3. **QuPath project directory** — `hescope/importers.py` already parses its
   GeoJSON; what is missing is walking a project rather than one file.
4. **Postgres** — one driver plus fixing the three SQLite assumptions.
5. **OMERO** — a real client dependency, measure before committing.

---

## Part 4 — Autonomy, skills, and the report

### 4.1 The finding that changes the plan

Both design documents treat `mpp_effective` as *the* comparability fix: record
the resolution and a `WHERE` clause can exclude incomparable rows.

**That is necessary and nowhere near sufficient.** Reproduced here on the user's
own ROI 10, with `patch_mpp` computed correctly and passed to `detect_nuclei`
at every step:

| `max_size` | patch | effective mpp | nuclei | density/mm² |
| --- | --- | --- | --- | --- |
| 256 | 256×214 | 3.889 | 167 | 201.5 |
| 512 | 512×429 | 1.941 | 595 | 718.7 |
| 1024 | 1024×857 | 0.971 | 1 916 | 2 314.7 |
| 2048 | 2048×1715 | 0.485 | 4 904 | 5 927.3 |

**29.4x spread on an unchanged region.** (One agent measured 103.85x over a wider
range; same phenomenon.) A "density per mm²" that moves 29-fold with an
extraction cap is not a property of the tissue. It is a property of the raster,
wearing a physical unit.

Recording the mpp lets you *notice*. It does not make the number mean anything.

> **Rule.** A skill declares a **measured validity range** of effective mpp, and
> a run outside it is **refused**, not labelled. `skill_runs.outcome` gains
> `rejected:out_of_range`. This is stronger than anything in the two design docs
> and it replaces their `mpp_effective` conclusion.

Two further measured hazards: `blur_score` moves 6.72x with the same cap, and
two functions both named `tissue_fraction` disagree by **2378x** on one patch.

### 4.2 QC does not cover what autonomy needs

`qc_report` detects gross defocus (σ≥2) and little else relevant here. A
greyscale, non-H&E patch passes it (`is_blurry=False`) while `roi_stats` on the
same image returns a confident H&E deconvolution. An unattended run would
measure haematoxylin on an image with no haematoxylin in it.

### 4.3 The sweep is biased, not merely truncated

`iter_grid`'s `max_tiles=4000` cap breaks row-major order
(`hescope/grid.py:92-98`), so a truncated sweep is **systematically biased
toward the top of the slide** and the un-swept remainder is not random. An
autonomous loop that samples from a truncated sweep inherits that bias silently.

### 4.4 What a report must carry

The generated report is the highest-risk artefact in the system — it is what a
human reads and acts on.

> **Every number in a report carries: the ROI it came from, the method and
> version that produced it, the effective mpp, and the QC verdict of the patch it
> was measured on.** A number that cannot supply all four is not printed. Claims
> are separated into *measured* and *inferred*, and an inferred claim names the
> measured ones it rests on.

### 4.5 The human gate

`human_gate` is a reserved `interactions` kind with no UI and no writer. It
should stop the loop on: a skill refused for range (§4.1), a QC verdict below
threshold, a label the agent proposes that contradicts a human label on an
overlapping ROI, and the first write to any slide the human has not opened.

---

## Part 5 — Frontend

### 5.1 The primitive for ROI editing already exists, unwired

The canvas already emits an ROI-click event: `clicked_roi` appears **5 times in
`hescope/osdviewer.py`, 9 times in its tests, and 0 times in `app.py`.**
Selection — the thing every other interaction (move, vertex drag, delete on
canvas) has to be built on — is implemented, unit-tested, and consumed by
nothing.

That reframes §1.3 of the workbench design from "ROI editing does not exist" to
"ROI editing is one consumer away from its first increment".

### 5.2 "Zoom to fit" does not fit

Measured: it frames 54 471 of the slide's 81 671 px, and the toolbar slider
cannot reach the downsample that would.

### 5.3 The agent has no presence in the UI

If an agent analyses autonomously (Part 4), the user cannot see what it is
doing, what it found, or stop it. This is the biggest gap the other four parts
create, it needs **no schema change**, and the mechanism exists — the
`interactions` table already records every agent tool call with an actor.

> A persistent agent strip: what is running, on which ROI, how long, what it last
> concluded, and a stop control. Backed by `interactions` + `skill_runs`, not by
> new state.

### 5.4 Order for the open UI items

1. `clicked_roi` consumer → select-and-delete on canvas (§5.1).
2. Agent presence strip (§5.3).
3. Sidebar message surface (round 09's R09-3).
4. Stain method selector — smaller than believed (§0.2).
5. Zoom-to-fit correctness (§5.2).

---

## Part 6 — Revised order of work

| # | Step | Why here |
| --- | --- | --- |
| 1 | WAL + `busy_timeout` + assert the pragmas | A 5.5 s lock failure on a two-process workflow we already ship. One line, one test. |
| 2 | `schema_migrations`, stamp v1, migration test convention | The next migration must not do what the last one did. |
| 3 | Identity as `(scheme, key)`; fix `register`'s race; stop `normalize_slide_path` mangling foreign paths | Unblocks Part 3 and corrects `DATABASE-DESIGN.md` §3. |
| 4 | `measurements` **with a validity range**, not just `mpp_effective` | Part 4.1 — the 29.4x finding makes this the gate on all autonomy. |
| 5 | `clicked_roi` consumer; agent presence strip | Pure UI, no schema, immediately felt. |
| 6 | DICOMweb read (0 packages), then folder+CSV, then QuPath project | Cheapest interop first. |
| 7 | Skill contract + report schema + human gate; then LoopX | Only after 4 — a governed loop over numbers that move 29x is governance theatre. |

The change from the previous plan: **step 4's bar moved up** (a recorded
resolution is not enough — a declared validity range is), and **LoopX moved
down**. Adopting a control plane is still right and still costs one
dependency-free package; running a loop before the numbers under it are
resolution-stable would produce auditable nonsense.
