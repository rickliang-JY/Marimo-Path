# HE-Scope bug review — six rounds, 2026-08-09/10

**31 findings fixed** (9 high, 9 medium, 13 low), 1 wontfix, 2 refuted, 1
demoted to an observation. Every fix carries a named regression test; the suite
grew 286 -> 596. Nothing is committed — all six rounds live in one working tree
on `feature/interop-and-hardening`.

State as of the audit round (2026-08-10), all four commands run and green:

```
pytest -q                                          596 passed, 16 skipped in 221.41s
pytest -q -W "error::FutureWarning"                596 passed, 16 skipped in 206.80s
python app.py                                      EXIT=0
HESCOPE_BROWSER_TESTS=1 pytest -q tests/browser/   15 passed in 51.79s
```

(The 15 browser tests are 15 of the 16 skips in the default run.)

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

Concentration: **12 of 31 are `app.py` cell wiring**, 6 are persistence, 5 are
pixel/coordinate geometry. `app.py` is where the bugs are, and reading it is not
enough to find them — round 04 needed marimo's own dependency graph and round 03
needed to `exec` app.py's real cell source.

---

## Classes that recurred — the part worth acting on

Ranked by count. A class that appeared in three or more rounds, found by three
different lenses, is a property of the codebase rather than a coincidence.

**1. Failure rendered as success — 5 findings, 4 rounds, 4 lenses.**
R01-4 (TCGA "Downloaded and opened" before anything was opened), R04-2 (green
"Sent ROI to agent" for the previous slide's rectangle), R05-7 (`db.enabled=True`
then every save fails), R06-2 (a sweep in which *every* tile raised renders as
the bare thumbnail under a green success callout), R06-4 (a failed
`HESCOPE_EMBEDDER` trains a handcrafted model under "Model trained."). Every one
has the same shape: **the message is composed from what was attempted, not from
what happened**, and the success path is unconditional. This is the single
highest-yield thing to sweep for.

**2. A number and the pixels it describes drift apart — 4 findings.**
R03-1 (off-slide padding was black, and black passes the tissue test), R03-4
(the metric grid spans more than the slide but was stretched to fit it), R04-3
(slide A's heatmap blended onto slide B's thumbnail), R06-1 (the handcrafted
feature vector is raster-dependent and nothing matched training raster to tile
raster). All silent, all on the analysis path. R03-4's own fix is what silently
rescaled R04-3's wrong grid into place — the fixes compose badly when the
provenance is not carried with the data.

**3. A second place re-deriving what one owner already decides — 4 findings.**
The three dead toolbar buttons (pre-round-01, pinned by
`tests/test_selection_routing.py`), R03-3 (`set_vp` where only `move_camera`
reaches the widget), R04-1 (the annotation panel reading the viewport itself),
R04-5 (a handler reasoning about a measure vocabulary it cannot see). `app.py`
states this invariant twice in prose; both leaks were introduced *by an earlier
round's own fix*.

**4. State that outlives its subject — 3 findings.**
R04-2/R04-3 (ROIs, payload, analysis result and heatmap survive a slide change),
R05-2 (annotations orphaned when the same file is re-keyed under a different
path spelling). Derived state carrying no record of what it was derived from.

**5. Documented behaviour with no implementation — 3 findings, 1 still open.**
R05-3 (the `interactions` table README calls the automation-bias data source had
writers for 2 of 6 kinds, none of them human), R05-8 (the "one click" GeoJSON
export had no caller in `app.py`), and the still-open `available_encoders` gap
below. Docs are load-bearing here because `AGENTS.md` is a contract.

**6. A display value read back as data — 2 findings + 1 observation.**
R03-2 (the bbox stringified for the table, then parsed back as coordinates),
R05-2 (the raw sidebar path string used as a database key). Round 02 logged a
third instance that is still latent (`records_to_rows` omits `md5sum`, so
app.py's `_sel[0].get("md5sum") or <catalog>` has a permanently dead branch).

**7. Synchronous work in a marimo click handler — 2 findings.**
R04-6 (heatmap sweep, 9.15 s frozen kernel) and R06-7 (training, 19.73 s). Both
fixed by copying the worker-thread + ticker shape the TCGA download already
used and documented. The pattern existed; it just was not applied.

**8. Server-supplied string joined onto a path — 2 findings.**
R02-3 fixed it for the `Content-Disposition` name; R05-1 found the identical bug
in `file_id`, at the site R02-3's fix did not cover. Both fixes were then placed
*inside* the function rather than at the join sites, for that reason.

---

## Still open

| # | Item | Evidence |
| --- | --- | --- |
| 1 | **`data/hescope.db` still carries the R05-2 split.** `hescope dedupe-slides` exists and is tested, but has never been run on real data — a data change nobody authorized. | slides `3` (`assets\demo_he.png`) and `5` (`E:\...\assets\demo_he.png`) are the same file; ROI 1 hangs off one and ROI 3 off the other. Plus 12 `pytest-of-hp` junk rows from before R05-4. |
| 2 | **`AGENTS.md` under-documents the agent contract.** `get_analysis_capabilities()` returns 4 top-level keys; `AGENTS.md:61` and `:206` both list 3. `available_encoders` is undocumented. | `sorted(analysis_capabilities())` -> `['analyses','available_encoders','models','torch_embedding_available']`. Round 06 dismissed this as "the already-recorded R02-4"; **no round ever wrote an R02-4** — corrected in place there. |
| 3 | **R01-4 still has no regression test.** The only finding in six rounds with "test coverage: none" still standing. Round 03 built the technique that would fix it (`ast`-slice + `exec` app.py's own cell source) and named this as the next candidate; two rounds later it is still uncovered. | `bugs/2026-08-09-round-01.md` R01-4; `tests/test_annotation_jump.py` is the working template. |
| 4 | **R01-5 and R01-7 are pinned only by `-W "error::FutureWarning"`.** Reverting either passes a plain `pytest -q`. | Measured; both quoted as marked corrections in round 01. Whatever runs this suite unattended must keep the flag. |
| 5 | **Two always-on 1 s tickers** (`tcga_ticker`, `hm_ticker`) never idle. Gating one needs it rebuilt, which resets it. | Round 01 + round 04 observations. |
| 6 | **The heatmap/training workers read `SlideSource` outside the tile server's `read_lock`.** Safe today only because `TileServer.register` pre-warms every level; a lazily-built future source would bite. Durable fix is to move the lock onto the source. | Round 04 observation. |
| 7 | **Nothing clamps a selection to the slide.** `bbox_level0` in the agent contract can exceed the slide; this is what made R03-1 reachable. Arguably correct (the user did drag there) but undecided. | Round 03 observation. |
| 8 | **R06-1 matched the per-pixel raster, not the field of view.** A 1024 px ROI patch covering 2048 level-0 px and a 256 px tile covering 256 still see different amounts of tissue. | Stated explicitly in round 06 so it is not read as solved. |

**Wontfix:** R02-1 — the round's input was a placeholder (`"title": "t"`,
`"evidence": "e"`); number retired, not reused.

**Refuted, recorded so they are not re-filed:** R06-5 (`nuclei_density` count
semantics — documented design; the claimed 5.7x spread measured 1.62x), R06-6
(`available_encoders.default` is the registry's license-safe default, not a
claim about the active feature path). R05-6 (`for_slide` returns the oldest N)
was demoted to an observation — it is the documented contract and had no caller,
though R05-3's new writers now give it real data to truncate.

---

## What round 07 should look at first

1. **Sweep the success messages.** Class 1 is the biggest and the cheapest to
   attack directly: enumerate every `("success", ...)` / `mo.callout(kind=...)`
   write in `app.py` and ask of each "what did the code actually verify before
   saying this?" Four different lenses each stumbled into one instance; a pass
   aimed at the class should find the rest in one go. Round 06's `error_cb` on
   `compute_grid` is the template — count what failed, then choose the message.

2. **Finish the reactivity guard.** `tests/test_notebook_reactivity.py` asserts
   the rule for `get_vp` and, per element, for `get_models_version`. Measured
   over marimo's graph today, one control is still exposed and unjustified:
   `hm_metric_dropdown` (cell `wAgl`) is rebuilt by `get_models_version` and
   constructs with a hardcoded `value="tissue_fraction"`, so a successful
   "Train from annotations" resets the user's chosen heatmap metric — R04-4's
   exact class, on the one control round 04 neither fixed nor justified.
   (`get_source` -> the toolbar cell and `get_slide_id` -> the Annotations panel
   are also in the closure but are *deliberate*, and the cells say so; that is
   why the rule must stay per-element rather than blanket.)

3. **Take the two data-hygiene actions in "Still open" 1 and 2.** Both are
   small, both are user-visible, and item 1 is live corruption of the artefact
   the app exists to protect.

4. **Give a lens to the code that has never had one.** Five rounds covered
   analysis, persistence, coordinates, reactivity and TCGA. Untouched:
   `hescope/tileserver.py` + `hescope/osdviewer.py` under concurrency (round 04
   flagged the unsynchronised reader but did not test it), `hescope/adjust.py`
   and the display pipeline, `hescope/overlay.py`, and interop fidelity
   (`docs/ROADMAP-INTEROP.md` records that the GeoJSON export is lossy in
   wording that reads as though it were not).

5. **Method note that paid off twice, worth reusing.** `app.run()` cannot see
   reactivity bugs (it runs each cell once, applies no cell-private name
   mangling, re-runs nothing) and reading cannot see them either. The two
   instruments that worked are marimo's own `DirectedGraph`
   (`app._maybe_initialize(); app._graph`) and `ast`-slicing app.py's real cell
   source and `exec`ing it. Both are in `tests/` now — start there rather than
   rebuilding them.
