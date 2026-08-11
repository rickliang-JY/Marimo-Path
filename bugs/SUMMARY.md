# HE-Scope bug review — ten rounds, 2026-08-09/11

**56 findings fixed** (rounds 01–07: 50, of them 12 high / 17 medium / 21 low;
rounds 08–10: 6 more), 1 wontfix, 4 refuted, 1 demoted to an observation, and 4
open from the user-experience rounds. Every fix carries a named regression test
**verified to fail against the un-fixed code**; the suite grew 286 -> 790.

Rounds 01–07 landed on `feature/interop-and-hardening`; rounds 08–10 are on
`bugs/2026-08-11-round-08` and `bugs/2026-08-11-round-10`.

State as of round 07 (2026-08-10), all four commands run and green:

```
pytest -q                                          705 passed, 16 skipped in 341.34s
pytest -q -W "error::FutureWarning"                705 passed, 16 skipped in 397.62s
python app.py                                      EXIT=0
HESCOPE_BROWSER_TESTS=1 pytest -q tests/browser/   15 passed in 51.79s
```

(The 15 browser tests are 15 of the 16 skips in the default run. Round 07 moved
`filterwarnings = ["error::FutureWarning"]` into `pyproject.toml`, so the two
pytest lines are now the same command.)

---

## What each round found

| Round | Lens | Findings | Where they landed |
| --- | --- | --- | --- |
| [01](2026-08-09-round-01.md) | full read of `hescope/` + `app.py` | 3 high, 3 medium, 1 low | ROI-shape stats, agent never-raise contract, skimage deprecations |
| [02](2026-08-09-round-02.md) | `tcga.py` | 1 high, 1 low | md5 never backfilled -> unverified downloads; path traversal |
| [03](2026-08-09-round-03.md) | coordinates + pixel provenance | 1 high, 1 medium, 2 low | black off-slide padding counted as tissue; annotation jump |
| [04](2026-08-09-round-04.md) | notebook reactivity + session scope | 2 high, 1 medium, 3 low | all six in `app.py` cell wiring |
| [05](2026-08-09-round-05.md) | database + persistence | 1 high, 2 medium, 4 low | slide identity, suite isolation, interaction trace |
| [06](2026-08-09-round-06.md) | analysis + ML | 1 high, 2 medium, 2 low | feature raster, failed sweeps reported as success |
| [07](2026-08-10-round-07.md) | adversarial re-verification of three parallel lenses | 3 high, 8 medium, 8 low | message provenance, a physically wrong number, the shipped database |

Concentration: **23 of 50 are `app.py` cell wiring**, 8 are persistence, 6 are
pixel/coordinate geometry. `app.py` is where the bugs are, and reading it is not
enough to find them — round 04 needed marimo's own dependency graph, round 03
needed to `exec` app.py's real cell source, and round 07 needed both plus real
worker threads.

---

## Classes that recurred — the part worth acting on

Ranked by count. A class that appeared in three or more rounds, found by three
different lenses, is a property of the codebase rather than a coincidence.

**1. Failure rendered as success — 11 findings, 5 rounds, 6 lenses.**
R01-4 (TCGA "Downloaded and opened" before anything was opened), R04-2 (green
"Sent ROI to agent" for the previous slide's rectangle), R05-7 (`db.enabled=True`
then every save fails), R06-2 (a sweep in which *every* tile raised renders as
the bare thumbnail under a green success callout), R06-4 (a failed
`HESCOPE_EMBEDDER` trains a handcrafted model under "Model trained."), and round
07's R07-5 (a failed Open writes nothing at all), R07-6 (a failed export
downloads as `rois.json`), R07-13 ("Downloaded" for a file nothing fetched),
R07-14 (green over two repository no-ops), R07-15 (200 of 250 rows presented as
the whole catalog) and R07-1 (a stale sweep republished under "96 cells
measured"). Every one has the same shape: **the message is composed from what
was attempted, not from what happened**, and the success path is unconditional.

Round 07 attacked it as a class rather than one patch at a time, and that is
the part to keep:

* `tests/test_message_channels.py::test_every_click_handler_has_an_error_path`
  — an AST lint over every `on_click`/`on_change` handler, with a named
  exemption list carrying a reason each. It found four handlers nobody had
  filed.
* `::test_opening_a_slide_resets_every_message_channel` — enumerates the
  channels from the loader cell's own signature, so a channel added later is
  covered without editing the test.
* Repositories now report what they DID (`update_annotation -> bool`,
  `delete -> bool`), which is the one-line shape the remaining instances need.

**2. A number and the pixels it describes drift apart — 6 findings.**
R03-1 (off-slide padding was black, and black passes the tissue test), R03-4
(the metric grid spans more than the slide but was stretched to fit it), R04-3
(slide A's heatmap blended onto slide B's thumbnail), R06-1 (the handcrafted
feature vector is raster-dependent and nothing matched training raster to tile
raster), R07-1 (R04-3 again, through a worker thread), R07-2
(`density_per_mm2` computed against the thumbnail's area rather than the ROI's
— measured 16.00x too large on a 4096 px ROI). All silent, all on the analysis
path. R03-4's own fix is what silently rescaled R04-3's wrong grid into place —
the fixes compose badly when the provenance is not carried with the data.

**Round 07's rule, which is what actually closed R04-3/R07-1:** provenance
travels WITH the value and is checked at PUBLISH time. Clearing derived state
when the subject changes cannot work once anything is asynchronous, because the
worker writes after the clear.

**3. A second place re-deriving what one owner already decides — 4 findings.**
The three dead toolbar buttons (pre-round-01, pinned by
`tests/test_selection_routing.py`), R03-3 (`set_vp` where only `move_camera`
reaches the widget), R04-1 (the annotation panel reading the viewport itself),
R04-5 (a handler reasoning about a measure vocabulary it cannot see). `app.py`
states this invariant twice in prose; both leaks were introduced *by an earlier
round's own fix*.

**4. State that outlives its subject — 5 findings.**
R04-2/R04-3 (ROIs, payload, analysis result and heatmap survive a slide change),
R05-2 (annotations orphaned when the same file is re-keyed under a different
path spelling), R07-4 (`set_db_msg` outlives its slide — the one channel
`_open_slide_path` did not reset, and the one carrying every success string in
the app), R07-17 (a finished worker result discarded by the next click).
Derived state carrying no record of what it was derived from.

**5. A control rebuilt by a token that has nothing to do with it — 2 findings.**
R04-4 (tile slider + navigator checkbox reset by a train) and R07-8 (the model
and metric dropdowns, the two controls round 04 neither fixed nor justified).
marimo resets a re-constructed `mo.ui` element to its default by design, so any
cell that both reads a refresh token and builds an input loses user state.
R04-4's fix (move the control to its own cell) does not always transfer —
R07-8's metric options genuinely depend on the model — and the remedy there is
to remember the pick in a non-reactive dict and honour it only while it is
still on offer. **Note the guard shape:** asserting over a cell's `.refs`
cannot see a transitive descendant, so the obvious extension of
`test_sweep_controls_are_not_rebuilt_by_a_training_run` is a false green.

**6. Documented behaviour with no implementation, or vice versa — 5 findings.**
R05-3 (the `interactions` table README calls the automation-bias data source had
writers for 2 of 6 kinds, none of them human), R05-8 (the "one click" GeoJSON
export had no caller in `app.py`), R07-7 (the GeoJSON button's behaviour
contradicted the comment above all three Export buttons), R07-11
(`available_encoders` undocumented in both agent-facing places; the `roi_plot`
row wrong for the surface the app ships), R07-2 (AGENTS.md's worked recipe
reproduced a real numeric error). Docs are load-bearing here because
`AGENTS.md` is a contract.

**7. A display value read back as data — 2 findings + 1 observation.**
R03-2 (the bbox stringified for the table, then parsed back as coordinates),
R05-2 (the raw sidebar path string used as a database key). Round 02 logged a
third instance that is still latent (`records_to_rows` omits `md5sum`, so
app.py's `_sel[0].get("md5sum") or <catalog>` has a permanently dead branch).

**8. Synchronous work in a marimo click handler — 2 findings.**
R04-6 (heatmap sweep, 9.15 s frozen kernel) and R06-7 (training, 19.73 s). Both
fixed by copying the worker-thread + ticker shape the TCGA download already
used and documented. The pattern existed; it just was not applied.
**R04-6's fix is also what reopened R04-3 as R07-1** — moving work off the main
thread invalidates every "clear it at the boundary" guard around it, silently.

**9. Server-supplied string joined onto a path — 2 findings.**
R02-3 fixed it for the `Content-Disposition` name; R05-1 found the identical bug
in `file_id`, at the site R02-3's fix did not cover. Both fixes were then placed
*inside* the function rather than at the join sites, for that reason.

---

## Still open

| # | Item | Evidence |
| --- | --- | --- |
| 1 | **Two always-on 1 s tickers** (`tcga_ticker`, `hm_ticker`) never idle. Gating one needs it rebuilt, which resets it. | Round 01 + round 04 observations. |
| 2 | **Nothing clamps a selection to the slide.** `bbox_level0` in the agent contract can exceed the slide; this is what made R03-1 reachable. Arguably correct (the user did drag there) but undecided. | Round 03 observation. |
| 3 | **R06-1 matched the per-pixel raster, not the field of view.** A 1024 px ROI patch covering 2048 level-0 px and a 256 px tile covering 256 still see different amounts of tissue. | Stated explicitly in round 06 so it is not read as solved. |
| 4 | **`HESCOPE_TILE_PARALLEL_READ=1` removes `entry.read_lock` from the tile path entirely.** `TileServer.tile_bytes` is `if plan.use_overview or _parallel_reads_enabled(): render_tile(...)`. Off by default and never measured enabled, so a future round must not benchmark with it set and blame the wrong thing. | Round 07 observation. |
| 5 | **26 temp-directory slide rows remain in `data/hescope.db`.** Each has a unique path, so `dedupe-slides` does not touch them; deleting them is a separate destructive action with no user-visible benefit. | Round 07, R07-3. `conftest.py` isolates `pytest`; the rows predate R05-4, plus one written by round 07's own scratchpad probes, which bypass `conftest`. |
| 6 | **`records_to_rows` omits `md5sum`,** so app.py's `_sel[0].get("md5sum") or <catalog lookup>` has a permanently dead first branch. | Round 02 observation, still latent. |
| 7 | **Factory-produced click handlers are outside R07-5's lint.** `on_click=_make_view(_i)` is not reachable by name, so the per-ROI View/Delete buttons are neither guarded nor checked. | Round 07, R07-5; the test docstring says so rather than papering over it. Still true after F01 widened the `ui_actions` guard — that guard starts from `ui_actions[...] = <name>`, and a factory call is neither. |

**Open item 4 is now measured** (2026-08-11, `docs/DESIGN-AGENT-WORKBENCH.md` §4.1): 28 cold 256 px tiles at DZI level 15 of the 81671x18211 slide. Flag unset: 2163 ms sequential, 2168 ms on 8 threads — **1.00x**, the reads are fully serialised. Flag set: 2210 ms sequential, **425 ms** on 8 threads — **5.20x**, and the parallel fetch returned byte-identical tiles. Not yet proven and required before changing the default: the OpenSlide backend, sustained load, memory, and distributions rather than single runs.

**Field reports** (defects hit in the running app rather than found by review)
are in `2026-08-10-field-reports.md`. F01 — Add ROI raising `NameError` on every
click — matters beyond its one-line fix: the guard written for the *previous*
instance of that defect was scoped to the reproduction (`ui_actions[...] =
lambda ...`) rather than to the rule, so it passed on the next instance. It has
been replaced with an AST guard over everything reachable from `ui_actions`,
plus a test that runs marimo's own mangled bytecode and deletes the cell-private
names before clicking.

**Closed by round 07:** the R05-2 split in `data/hescope.db` (R07-3 — repaired
on the live artefact, with a backup at `data/hescope.db.bak-before-round07-dedupe`
and a new `--dry-run` that made the change reviewable); the
`available_encoders` doc gap (R07-11); R01-4's missing regression test
(R07-18); and the `-W "error::FutureWarning"` guard, which now lives in
`pyproject.toml` (R07-9). The unsynchronised `SlideSource` reads were
**refuted** rather than fixed — see below.

**Wontfix:** R02-1 — the round's input was a placeholder (`"title": "t"`,
`"evidence": "e"`); number retired, not reused.

**Refuted, recorded so they are not re-filed:** R06-5 (`nuclei_density` count
semantics — documented design; the claimed 5.7x spread measured 1.62x), R06-6
(`available_encoders.default` is the registry's license-safe default, not a
claim about the active feature path), R07-B2 (SlideSource reads outside the
tile server's `read_lock` corrupting pixels and 404ing tiles — the three-arm
experiment was built twice, on a synthetic pyramidal SVS and on the real
81671x18211 TCGA slide with the tile cache neutered so every request reached
the source, and measured **zero** corrupt thumbnails, zero differing tiles,
zero 404s across 2106 + 1256 navigator renders), and the claim that `NO_SLIDE`
is an undocumented sentinel (it is documented verbatim at `AGENTS.md:62`; only
the §2 summary sentence is incomplete, which is a prose nit). R05-6
(`for_slide` returns the oldest N) was demoted to an observation — it is the
documented contract and had no caller, though R05-3's new writers now give it
real data to truncate.

**Why R07-B2 is safe — the old open item 6 was right in outcome, wrong in
mechanism.** `tifffile` flips `TiffFile.filehandle.lock` from `NullContext` to
a real `RLock` the moment `aszarr` opens a zarr store, and both
`series.asarray()` (the `get_thumbnail` path) and the zarr store then take it.
Measured: `NullContext` on a cold source -> `RLock` after ONE `read_region` ->
still `NullContext` after a `get_thumbnail` alone. `TileServer.register()`
pre-warms `read_region((0,0), lvl, (1,1))` for every level **in the calling
thread** before returning the key, so the lock is armed before any tile thread
or Navigator render runs. The pre-warm is not warming a cache; it is arming
tifffile's own file-handle lock. A future backend that never builds a zarr
store would not get it — which is the real form of the concern.

---

## What round 08 should look at first

1. **Give a lens to the code that has still never had one.** Seven rounds have
   covered analysis, persistence, coordinates, reactivity, TCGA, ML and message
   provenance. Untouched: `hescope/adjust.py` and the display pipeline,
   `hescope/overlay.py`, `hescope/stain.py`, and the three newest modules —
   `hescope/importers.py`, `hescope/tcga_schema.py` and
   `hescope/dicom_source.py` — which landed *during* round 07 and have never
   been reviewed at all. Interop fidelity is also unexamined:
   `docs/ROADMAP-INTEROP.md` records that the GeoJSON export is lossy in
   wording that reads as though it were not, and round 07 changed
   `slide_geojson_text`'s `slide_id=None` contract without that doc mentioning
   it.

2. **Audit the remaining `("success", ...)` writes against round 07's lint.**
   The lint proves a handler *has* an error path; it does not prove the success
   path is conditional on anything. The ROI repository now returns `bool` —
   find the callers that still ignore an outcome. `db.trace`,
   `AgentRunRepo.record` and `SlideCatalog.mark_downloaded` all return None
   today, so no caller of theirs can tell success from a silent no-op.

3. **Carry provenance the way R07-1 does, everywhere asynchronous.**
   `train_job` has exactly the same shape as `hm_job` and exactly the same
   hand-off, and nothing checks which slide (or which annotation set) a
   finished training run was derived from. It was not measured this round.

4. **Settle the two deferred design calls** (still-open 2 and 3): clamping a
   selection to the slide, and whether the heatmap's field of view should be
   matched to the training patch's rather than only its raster. Both have been
   deferred three rounds and are cheap once someone decides.

5. **Method notes that paid off, worth reusing.** `app.run()` cannot see
   reactivity bugs (it runs each cell once, applies no cell-private name
   mangling, re-runs nothing) and reading cannot see them either. The two
   instruments that work are marimo's own `DirectedGraph`
   (`app._maybe_initialize(); app._graph`) and `ast`-slicing app.py's real cell
   source and `exec`ing it — both are in `tests/` now. Round 07 adds two more,
   also in `tests/`: **spy on the module a cell builds its widgets through**
   (`tcga_panel`, `mo.ui.button`, `mo.download`) so the test clicks the REAL
   widget instead of digging through layout internals; and **assert on the
   cell's parameter list** (`missing = [p for p in params if p not in deps]`),
   which is what caught every new dependency this round introduced.

---

## Rounds 08-10 — the user-experience lens (2026-08-11)

Three rounds run against one question: **what the code can do, versus what the
person operating it can reach and can see.** Ten distinct findings, six fixed,
four open. (R10-2 and R10-3 are R08-4 and R09-4 carried forward, not new
numbers.)

**The widest gap.** "Add ROI" wrote the session list only, so an ROI added with
that button was invisible to the Statistics panel, to all three exports and to
the annotation editor — every one of which reads the database — and gone at the
next restart. The button that actually saved was "Send to code agent". Fixed:
the database is the owner when there is one, the session list stays the store in
DB-free mode.

**Two regressions caught in the round after the one that caused them.** Moving
that store left the status strip counting an empty list ("0 ROI(s)" over an
image drawing four), and left "not added yet" describing regions the user had
just saved. Both fixed before either shipped. A third — `set_ann_version(
get_ann_version() + 1)` inventing an integer contract for an opaque token —
raised **after** the row was written, so the ROI was saved and the strip said
"Add ROI failed".

**Doors that did not exist.** `hescope/importers.py` was complete, tested and
referenced by nothing outside its own tests: QuPath and ASAP annotations could
not get in, while export had three buttons. Now wired, and reporting `skipped`
and `warnings` rather than a count alone.

**An integrity claim the app could not always keep.** R07-13 established that
"Downloaded" means md5-verified in this app; the download path said it whether
or not a checksum was available to check against. Now three distinguishable
outcomes, failing closed when the flag is absent.

**Still open from these rounds:** a whole-slide sweep's grid is never persisted
(only its settings reach `interactions`, so the database can say a sweep ran and
not what it found); three of four stain-normalisation methods have no way to be
selected, for the documented reason that Macenko refits per tile and blanks a
background tile to black; and the sidebar's per-ROI View/Delete now render only
in DB-free mode, the capability having moved to the Annotations browser.

**Checked and recorded as clean**, so a later round does not repeat them: all 39
UI controls have a handler or a reader; the nine module-scope names AGENTS.md
promises all resolve in marimo's graph; every expensive action guards its
precondition; the two status-strip channels stack rather than overwrite; all 20
panels cover their empty and error states; and marimo's `_invalidate_cell_state`
deletes a cell's defs before re-running it, which is what makes the Statistics
panel's `dir()` guard sound.
