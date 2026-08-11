# Design — from a toolbox to an analysis partner

Four questions, answered against the code as it stands on `main` (`f541b68`,
2026-08-11) rather than against intentions:

1. Does the front end actually expose what we built?
2. How far can the marimo ↔ code-agent channel really go?
3. How deep should an agent be allowed to go on one slide, and what would let
   it iterate rather than answer once? (Reviewed **LoopX** for this — §3.0.)
4. Zoom, ROI editing, ROI storage, ROI loading — what is slow, and why.

Every number below was measured on the 81671 x 18211 TCGA/HCMI slide in this
repository on 2026-08-11. Reproduction commands are named at each claim.

---

## Part 1 — Front end vs capability: no, we have not shipped it all

Rounds 08–10 audited this mechanically. The honest picture is four categories,
not two.

### 1.1 Wired and honest (the majority)

All 39 module-scope `mo.ui` controls have a handler or a reader — there are no
dead controls. All nine module-scope names `AGENTS.md` promises resolve in
marimo's graph. Every expensive action (`Analyze`, `Run heatmap`, `Train`)
guards its precondition and says so. All 20 panels cover their empty and error
states.

### 1.2 Built, tested, and unreachable

| Capability | State | Why it matters |
| --- | --- | --- |
| **Stain normalisation, 3 of 4 methods** | `normalize_stain` / `STAIN_METHODS` referenced **0 times** in `app.py`. Only `macenko_normalize` is reachable, and only when `refs.stain_source` is set, which no control sets. | Reinhard, Ruifrok and Vahadane are implemented and tested and cannot be selected. The reason is real and documented: Macenko refits the source matrix per tile, so a blank background tile normalises to solid black. **Fixing this needs a slide-level reference matrix, not a checkbox.** |
| **Whole-slide sweep results** | `set_hm_result` is `mo.state`. No table, no file. `interactions` records the *settings* (`metric`, `tile`, `downsample`) and never the grid. | Minutes of compute on this slide, discarded at restart, with nothing on screen saying so. The database can tell you a sweep ran and not what it found. |

*(GeoJSON/ASAP import was in this table until round 10 wired it.)*

### 1.3 Present but shallower than the user's mental model

**ROI editing does not exist.** You can draw, add, and delete a row. You cannot
move a box, reshape a polygon, nudge a vertex, or delete by clicking the outline
on the canvas. Every correction is: delete the row, redraw from scratch. For a
pathologist adjusting a boundary this is the difference between a tool and a
demo.

**Per-ROI View/Delete left the sidebar.** Round 08 moved added ROIs into the
database, so the ROIs panel's per-row buttons — built from the session list —
no longer render in normal operation. The capability lives in the Annotations
browser and the panel signposts it, but it moved without the user moving it.

### 1.4 The structural answer

Section 1.2's two entries share one cause with the database findings: **there is
nowhere to put a result that is not an ROI.** A stain reference matrix belongs to
a slide; a sweep grid belongs to a slide and a parameter set; a measurement
belongs to an ROI and the resolution it was taken at. `docs/DATABASE-DESIGN.md`
proposes exactly those homes. Until they exist, every new analysis capability
will land in `mo.state` and evaporate — which is why this document treats the
schema work as a prerequisite for Parts 2 and 3, not a parallel track.

---

## Part 2 — marimo pair: read, run, author

### 2.1 What the channel actually is (measured)

`marimo edit --mcp code-mode` mounts an MCP server at `/mcp/server` exposing
`list_sessions()` and `execute_code(session_id, code)`. Verified end to end this
session: session `s_xolnzm`, `get_current_selection()` returning live geometry,
`get_slide_info()`, `db.roi_repo.for_slide(...)` — all from outside the browser,
zero clicks from the user.

`execute_code` evaluates in marimo's **scratchpad**: a shallow copy of kernel
globals. Notebook variables are readable by name; new top-level bindings are
discarded when the call ends. That is a read/probe channel, and it is what the
project already uses.

The part we have not used is `marimo._code_mode` (`cm`), a private agent API
reachable from the scratchpad:

```python
import marimo._code_mode as cm
async with cm.get_context() as ctx:
    cid = ctx.create_cell("hist = np.histogram(patch_gray, bins=64)")
    ctx.run_cell(cid)
```

`ctx` gives `create_cell` / `edit_cell` / `run_cell`, the document view
(`ctx.cells`), the dataflow view (`ctx.graph.descendants()` / `.ancestors()`),
`ctx.packages.add()`, and `ctx.set_ui_value()`.

**So yes — an agent can write new analysis code into the running app and execute
it, and the result is durable notebook state rather than a scratch value.** That
was the open question, and the answer is that the mechanism already exists.

### 2.2 The tension that has to be designed around

marimo's contract is that **the running kernel is the source of truth and the
`.py` file is what the kernel writes from it.** Two consequences:

* Editing `app.py` on disk while a session is live is unsafe. This bit us: a
  `git stash` during a live session fed marimo the pre-fix file, its autosave
  (`after_delay`, 1 s) wrote that back, and a committed fix was silently lost —
  the commit message described a change the commit did not contain.
* Therefore an agent-created cell **is a permanent edit to the product**. Twenty
  exploratory cells from one investigation become twenty cells in `app.py`.

Any design where the agent authors code must answer: *where does exploration go,
and how does anything good get out of it?*

### 2.3 Proposal: two notebooks and a promotion path

```
    exploration                 promotion                  product
 ┌──────────────────┐      ┌──────────────────┐     ┌──────────────────┐
 │ scratch.py       │      │ hescope/*.py     │     │ app.py           │
 │ agent-authored   │ ───► │ a real function, │ ──► │ one cell, one    │
 │ cells, disposable│      │ with a test that │     │ control, wired   │
 │ per investigation│      │ fails pre-fix    │     │ to the UI        │
 └──────────────────┘      └──────────────────┘     └──────────────────┘
      cm.create_cell            ordinary PR              ordinary PR
```

* **Exploration** happens in a second marimo notebook opened on the same kernel
  data, not in `app.py`. It is expected to be messy and is expected to be thrown
  away. `cm.create_cell` is the right tool there and the wrong tool in `app.py`.
* **Promotion** is deliberate: a finding that survives becomes a function in
  `hescope/` with a regression test verified to fail against the un-fixed code —
  the discipline this repo already holds itself to across ten rounds.
* **The product** only ever gains a cell through a reviewed change.

This keeps `cm`'s power without letting a week of agent exploration silt up the
notebook a pathologist opens.

### 2.4 What the agent is missing today

The contract in `AGENTS.md` is read-mostly: six tools that report selection,
slide, capabilities and annotations, plus `annotate_roi` to write a label back.
An agent that is to *analyse* rather than *report* needs three more things, all
of which are schema-shaped:

| Need | Today | Required |
| --- | --- | --- |
| Record a measurement it computed | only via `roi_stats` on submit | `measurements(annotation_id, name, value, unit, method, mpp_effective, params_json)` |
| Ask for a region it has not seen | `extract_patch` at a fixed cap | a patch request carrying an explicit mpp, so the agent controls resolution |
| Know what it already tried | nothing | its own runs, queryable (`agent_runs` exists; nothing joins it to findings) |

---

## Part 3 — How deep, and how to iterate

### 3.0 Do not build the loop engine — LoopX is one, and it is free

Reviewed `E:\Good Github Repo\Agent\loopx` (2026-08-11). It is **a state kernel
and local-first control plane for long-running agents**, explicitly not an agent
framework: it holds objective, gates, todos, scope, evidence and quota across
turns while Codex / Claude Code / Cursor execute bounded slices. That is the
part §3.1 below was about to specify from scratch.

**Cost, measured the same way as every other dependency this session:**

```
uv pip install --dry-run -e <loopx>   ->   1 package, 0 removals
pyproject.toml                        ->   dependencies = []
requires-python                       ->   >=3.11   (this venv is 3.11.14)
```

One package, **zero transitive dependencies**, nothing uninstalled. Compare
`wsidicom` (7 installs / 0 removals, adopted) and `tiatoolbox` (152 installs /
22 removals including `ipywidgets`, which the viewer sits on — rejected). This
is the cheapest integration measured in this project.

State is JSONL ledgers on disk under the project, keyed by `goal_id` and
`domain_pack` (`upsert_domain_state_jsonl`, `ledger.jsonl`) — local-first, no
server, reviewable in git. `loopx/claude_goal_mode/` ships `commands`, `hooks`,
`mcp`, `statusline` and a `settings.example.json`: it integrates with the exact
harness this project is already driven from.

#### The pack that already fits

`loopx/domain_packs/ml_experiment.py` is a built-in pack for iterative
experiments, and its vocabulary maps onto slide analysis almost term for term:

| LoopX (`ml_experiment`) | HE-Scope |
| --- | --- |
| `hypothesis_ledger_v0`, status `active → supported \| weakened \| retired` | "tumour reads higher than stroma on this cohort" |
| `dataset_window_contract_v0` | the cohort — project / case / sample from `DATABASE-DESIGN.md` |
| `primary_metric`, `baseline_value` vs `candidate_value`, `higher_is_better` | `tissue_fraction`, `hematoxylin_mean`, nuclei density; label A vs label B, or model v1 vs v2 |
| `classify_primary_metric_delta` → `improved / flat / regressed` | the comparison the Statistics panel makes by eye today |
| **`guardrail_status`: `clean / warning / failed`** | **the comparability check** |
| `experiment_replan_v0` | the next sweep, narrowed to where the metric separated |

**The `guardrail_status` slot is the important one.** LoopX supplies the field
and refuses to record a result without it; we supply the domain check — and we
already know what it is. Two ROIs measured this session came out at 0.355 and
0.971 µm/px with eosin means 40% apart. A hypothesis "supported" by comparing
those two is not supported; it is `guardrail_status="failed"`. LoopX gives the
loop a place to say so, which is precisely what ten bug rounds say this codebase
needs: **plausible output is the failure mode, not crashes.**

#### One constraint that lands on our schema

The pack enforces a public/private boundary in code: `_compact_public_text`
rejects absolute paths, `~`, drive letters, URLs (`https|file|s3|gs|tos|hdfs`),
and credential-like markers. Our slide identity is
`E:\...\data\tcga\<uuid>\HCM-CSHL-...svs` — **an absolute path, which the pack
will refuse.** A ledger entry has to name a slide by a public alias.

That is one more argument for `DATABASE-DESIGN.md`'s content identity: a
`content_key` prefix and a case `submitter_id` are public aliases by
construction, and a file path never can be.

#### What we would actually build

A `packages/loopx-pathology-slide-discovery` pack mirroring the shipped
`packages/loopx-finance-value-discovery` (`CONTRACT.md`, `extension.toml`,
`gates.py`, `metric_packs.py`, `reducer.py`, `replay.py`, `boundary.py`) — the
domain vocabulary, the gates, and the guardrails. **Not** the scheduler, the
quota, the evidence store, or the handoff protocol, all of which exist.

### 3.1 The loop, expressed in that vocabulary

An agent that answers once is a fancier button. The loop worth running is the
one a pathologist actually runs:

```
  hypothesis ──► sample ──► measure ──► compare ──► refine
       ▲                                              │
       └──────────────── stop when dry ───────────────┘
```

Concretely, on one slide: *sweep coarsely → rank tiles → drop candidate ROIs in
the top decile → measure each → compare against the labelled ROIs → tighten the
metric → sweep again in the region that separated best.* Stop when two rounds
add nothing new, which is the same "loop until dry" shape this project's own bug
rounds used.

**Three things block that loop today**, and all three are Part 1 findings:

1. The sweep grid is not persisted, so round *n+1* cannot see round *n*'s map.
2. Measurements live in a JSON blob, so "compare" cannot be a query.
3. Nothing records the resolution a measurement was taken at, so the comparison
   step is not even sound — measured this session, two ROIs on one slide came
   out at 0.355 and 0.971 µm/px and their eosin means differ by 40%, with
   nothing in the database to say the comparison is invalid.

### 3.2 The ROI pyramid

Rather than one flat list of boxes, ROIs want a level:

| Level | Extent | Produced by | Cardinality |
| --- | --- | --- | --- |
| L0 field | whole slide | sweep | 1 |
| L1 region | mm scale | thresholded sweep cells | 10¹–10² |
| L2 ROI | 100 µm–1 mm | user or agent | 10²–10³ |
| L3 object | nuclei / glands | detector | 10⁴–10⁶ |

This is one nullable `parent_id` on `annotations` plus a `level` column, and it
buys three things: the agent can refine a region into ROIs without losing the
provenance chain; the viewer can load only the levels that are legible at the
current zoom (Part 4); and "tumour fraction" becomes well defined — a ratio of
L3 objects within an L2 ROI within an L1 region.

### 3.3 A skill database

The user's proposal is that validated analyses accumulate rather than being
re-derived. Made concrete, a "skill" is a named, versioned, parameterised
analysis with a recorded track record:

```sql
CREATE TABLE skills (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
  kind TEXT NOT NULL,            -- 'metric' | 'detector' | 'qc' | 'report'
  entrypoint TEXT NOT NULL,      -- 'hescope.nuclei:detect_nuclei'
  params_schema TEXT NOT NULL, source TEXT, created_by TEXT,
  created_at TEXT NOT NULL, UNIQUE (name, version)
);
CREATE TABLE skill_runs (
  id INTEGER PRIMARY KEY,
  skill_id INTEGER NOT NULL REFERENCES skills(id),
  annotation_id INTEGER REFERENCES annotations(id) ON DELETE CASCADE,
  params_json TEXT NOT NULL, outcome TEXT NOT NULL,   -- ok | error | rejected
  runtime_ms INTEGER, note TEXT, created_at TEXT NOT NULL
);
```

Self-improvement then means something checkable rather than aspirational: a new
version is proposed, run against the ROIs the previous version was run against,
and kept only if it agrees with the human labels at least as well. Without
`skill_runs` there is no such comparison, and "the skill improves itself" is a
claim nobody can audit.

**With LoopX (§3.0) that comparison is not ours to invent.** A skill version is
a `candidate_value` against the previous version's `baseline_value` on the same
`dataset_window`, classified by `classify_primary_metric_delta` into
`improved / flat / regressed`, and admitted only when `guardrail_status` is
`clean`. `skills` / `skill_runs` remain ours — they are the *catalogue* and the
per-ROI execution record, which is domain data. The *promotion decision* is
LoopX's ledger. Building both would be building the comparison twice, which is
the recurring defect class this repository has spent ten rounds naming.

**The honest caution.** Everything in 3.1–3.3 is generative: an agent that writes
code, runs it, and stores what it found. This repository's ten bug rounds are
one long argument that *plausible-looking output is the failure mode*, not
crashes. So the loop must carry the same discipline the rounds do — a finding is
provisional until a check that would have failed before it existed passes now.
`skill_runs.outcome` is where that lives.

---

## Part 4 — Zoom, editing, storage, loading

### 4.1 Zoom smoothness is one flag, measured

The slide is 81671 x 18211. The DZI pyramid has **18 levels**; the file has
**4** (downsamples 1, 4, 16, 32). Fourteen of the eighteen are synthesised on
demand, and the expensive ones are those falling between file levels.

Single-tile latency (256 px, `serve_slide` + HTTP):

| DZI level | scale | cold p50 | cold max | warm p50 |
| --- | --- | --- | --- | --- |
| 17 | 1/1 | 10 ms | 26 ms | 1 ms |
| 16 | 1/2 | 27 ms | 46 ms | 1 ms |
| **15** | **1/4** | **84 ms** | **99 ms** | 1 ms |
| 14 | 1/8 | 42 ms | 57 ms | 1 ms |
| 12 | 1/32 | 44 ms | 57 ms | 1 ms |

A zoom step is not one tile: a 1702 x 820 viewport at 256 px is ~28 tiles. At
level 15:

| `HESCOPE_TILE_PARALLEL_READ` | sequential | 8 threads | speedup |
| --- | --- | --- | --- |
| unset (**the default**) | 2163 ms | 2168 ms | **1.00x** |
| `=1` | 2210 ms | **425 ms** | **5.20x** |

**A zoom step costs 2.16 s today and 0.43 s with a flag that already exists.**
`bugs/SUMMARY.md` open item 4 records that this flag had never been benchmarked;
it has now. The parallel fetch also returned **byte-identical** tiles to the
sequential fetch, which is consistent with round 07's refutation of the
corruption hypothesis (zero differing tiles across 2106 + 1256 renders).

*Not yet proven, and required before flipping the default:* one process, one
slide, one backend (tifffile/zarr — the OpenSlide path is untested), single runs
rather than distributions, and no memory-under-load measurement. The next step is
a benchmark harness, not a default change.

Three further levers, in order of value:

1. **Prefetch the destination level during the zoom animation.** OSD already
   shows the blurry parent while children load; the 2.16 s is dead time that
   could start before the gesture ends.
2. **Cache the synthesised levels to disk.** Levels 14–16 are recomputed from
   level 0 every cold start; they are deterministic.
3. **Prefer the parent level when the child is not warm** rather than blocking.

### 4.2 ROI loading does not scale, and the fix is in the schema

`ROIRepo.for_slide` has no limit — it returns every ROI of the slide, and the
overlay cell parses all of them into `ROI` objects on every re-render (measured
with an in-memory SQLite database):

| ROIs on the slide | `for_slide` | parse | total per render | payload |
| --- | --- | --- | --- | --- |
| 100 | 3.4 ms | 0.2 ms | 3.6 ms | 3 KB |
| 1 000 | 46.0 ms | 2.8 ms | 48.8 ms | 32 KB |
| 5 000 | 90.5 ms | 47.9 ms | 138.4 ms | 170 KB |

Ten ROIs today, so nothing hurts. One imported QuPath project is 10³–10⁴, and
that is 140 ms plus 170 KB over the comm **on every label save, every delete,
every import** — because the cell re-runs on `ann_version`. It correctly does
*not* depend on the viewport (round 04 fixed that), so panning is free.

The fix is not a cache. It is the four `REAL` bbox columns from
`docs/DATABASE-DESIGN.md`, which turn "which annotations are on screen" into a
query the database can answer:

```sql
SELECT * FROM annotations
 WHERE slide_id = ? AND level <= ?          -- the pyramid level (3.2)
   AND bbox_x0 <= ? AND bbox_x1 >= ? AND bbox_y0 <= ? AND bbox_y1 >= ?;
```

Today `bbox_json` is **JSON text**, so this query cannot be written at all.

**Loading policy, stated as a rule:** load the ROIs whose bbox intersects the
viewport at a pyramid level legible at the current zoom, plus a one-viewport
margin; refresh on pan only when the margin is crossed, never on every frame.

### 4.3 ROI editing

What is missing (§1.3) is mostly client-side and does not need the schema:
select an outline by clicking it, drag it, drag a vertex, delete with a key.
`hescope/osdviewer.py` already owns a selection layer and a `renderSelection()`
that draws from a single source of truth, so this is an extension of a mechanism
that exists rather than a new one.

Two things that must be settled first, because they are data decisions:

* **Editing an ROI that has measurements invalidates them.** With
  `measurements` as rows (Part 2.4), the correct behaviour is to delete the
  measurements when the geometry changes — silently keeping them is the same
  defect class as everything in `bugs/SUMMARY.md`.
* **An edit needs an owner.** `created_by` distinguishes a region a pathologist
  drew from one a model proposed; an edit needs the same, or the provenance is
  lost the first time an agent adjusts a human's boundary.

---

## Part 5 — Order of work

Sequenced so each step is independently shippable, and so nothing is built on a
store that does not exist yet.

| # | Step | Unblocks | Cost |
| --- | --- | --- | --- |
| 1 | Benchmark harness for tile reads, then flip `HESCOPE_TILE_PARALLEL_READ` if it holds on both backends | 5.2x zoom, today | small |
| 2 | `DATABASE-DESIGN.md` steps 3–4: specimen hierarchy + `measurements` with `mpp_effective` | Parts 2.4, 3.1, 3.2 | medium, additive |
| 3 | Persist sweep grids; add `annotations.level` + `parent_id` | the ROI pyramid, loop round *n+1* | medium |
| 4 | Viewport-scoped ROI loading on the new bbox columns | 10³–10⁴ ROIs | small, after 2 |
| 5 | Canvas ROI editing (select / move / vertex / delete) | §1.3 | medium, client-side |
| 6 | Exploration notebook + `cm` promotion path | agent-authored analysis without silting up `app.py` | small |
| 7 | Install LoopX; drive one real loop with the built-in `ml_experiment` pack | proves the control plane before any pathology code is written | **small** |
| 8 | `packages/loopx-pathology-slide-discovery`: gates, metric packs, guardrails | Part 3 as a governed loop | medium |
| 9 | `skills` / `skill_runs` as the domain catalogue under it | skill iteration with an auditable promotion rule | medium |

Steps 1 and 4 are the ones a user feels immediately. Step 2 is the one
everything after it depends on. **Step 7 moved ahead of the pathology pack on
purpose**: LoopX costs one dependency-free package, so the honest order is to
run a governed loop with the generic pack first and find out where its
vocabulary does not fit, rather than designing a domain pack against a control
plane nobody here has operated yet.

Note what step 7 does *not* require: no schema change, no `app.py` change, no
new UI. It is a control plane beside the app, reading the same database. That is
why it can run in parallel with steps 2–5 instead of queueing behind them.
