# AGENTS.md — Contract for code agents pairing with HE-Scope

HE-Scope is a marimo notebook app (`app.py`): an H&E pathology image viewer
with ROI capture and a code-agent bridge. This file is the contract for code
agents (Kimi Code / Claude Code / Codex / Hermes) connecting to a LIVE
session via marimo-pair.

## 1. Starting the app

Connection mechanics — the exact launch command, why `marimo run` cannot be
paired, and the lazy-loading code-mode snippet that runs every cell once —
are workflow, not constraint, and now live in exactly one place:
`skills/he-scope/SKILL.md` §1. (They used to be duplicated here too; two
copies of the same recipe is the fastest way for this file to drift out of
sync with itself — see §10 below.)

The one fact that belongs in a *contract* rather than a *how-to*: the app
MUST be running under `marimo edit`, never `marimo run`. Once it is, and
every cell has run at least once, the entry points in §3 below are live in
the kernel globals.

## 2. Hard rules (marimo-pair)

- NEVER edit `app.py` (or any notebook file) on disk during a live session.
  Use `marimo._code_mode` (`ctx.create_cell` / `ctx.edit_cell` /
  `ctx.run_cell`) for all interactions; mutations are applied atomically on
  context exit and trigger reactive re-execution.
- Read state through the tool functions below, not by poking at cell-local
  variables.
- All tools return plain strings (JSON or the exact string `NO_SELECTION`);
  always handle `NO_SELECTION`.

## 3. Entry points (kernel globals)

| Name | What it is |
| --- | --- |
| `get_current_selection()` | Zero-click LIVE selection tool: JSON of the box/lasso the user is dragging on the plotly figure right now, mapped to level-0 slide coordinates. No user click required. |
| `get_latest_selection()` | Last SUBMITTED ROI payload (user clicked "Send to code agent"); persisted to `agent_out/roi_history.jsonl` and to the DB when enabled. |
| `agent_bridge` | `hescope.agent.agent_bridge.AgentBridge` bound to `agent_out/`; `.history()` / `.latest()` give past submitted payloads. |
| `db` | `DBContext` with `slide_repo` / `roi_repo` / `run_repo` when `db.enabled` is True (all None in DB-free mode). |
| `open_slide` | `hescope.wsi.slides.open_slide(path) -> SlideSource`. |
| `ensure_demo_slide()` | Returns the demo slide path, generating `assets/demo_he.png` in-process if missing. |
| `get_source()` / `get_vp()` | State accessors: current `SlideSource | None` and `ViewportState` (center, downsample, size). |
| `roi_plot` | The legacy `mo.ui.plotly` capture surface, and **`None` on the surface the app normally ships**: it is built only when OpenSeadragon is unavailable (`app.py`: `if _src is None or get_tiles() is not None: roi_plot = None`), so with a slide open it is still `None` on a default install. Use `get_current_selection()`, which picks the live surface for you; `roi_plot.value` is the raw plotly selection when the fallback *is* in use. |
| `get_analysis_capabilities()` | Zero-arg tool returning a JSON string: `{"analyses": [...], "torch_embedding_available": bool, "available_encoders": {...}, "models": [...]}` (trained classifiers under `data/models/`). Never raises — on failure returns `{"error": ...}`. `available_encoders` is `{"default": <name>, "torch_importable": bool, "encoders": [...]}`; each encoder spec carries `license` and `commercial_ok`, so **consult it before choosing an encoder** — `uni2h` is `commercial_ok=false` and gated, `gpfm` is MIT. |
| `get_slide_info()` | Zero-arg tool: JSON metadata of the open slide — `{"name", "dimensions": [w, h], "mpp", "levels", "level_downsamples", "db_id", "annotation_count"}` (`db_id`/`annotation_count` are `null` in DB-free mode). Returns the exact string `NO_SLIDE` when no slide is open. Never raises. |
| `annotate_roi(roi_id, label=None, notes=None)` | Writes label/notes back to the rois table (`ROIRepo.update_annotation`) and returns the updated row JSON. Returns `{"error": ...}` in DB-free mode, for an invalid/unknown `roi_id`, or on any failure. Records an `interactions` row (`kind=label_set`). |
| `query_annotations(label=None, limit=50)` | JSON list of the current slide's annotation rows (same dict shape as `ROIRepo.for_slide`), optionally exact-label filtered. Returns `[]` when no slide is open, `{"error": ...}` in DB-free mode. Records an `interactions` row (`kind=tool_call`). |

## 4. Payload schemas

### 4.1 `get_current_selection()` -> live selection (zero-click)

```json
{
  "kind": "rect | polygon",
  "points_level0": [[x, y], "..."],
  "bbox_level0": [x0, y0, x1, y1],
  "viewport_downsample": 2.0,
  "slide": "demo_he.png",
  "slide_dimensions": [6000, 4000],
  "mpp": null
}
```

All coordinates are level-0 (full resolution) pixels. `rect` has 2 corner
points; `polygon` (lasso) has >= 3 vertices. The circle checkbox is a UI
concern — the live tool reports raw box/lasso geometry only.

Turn it into patch stats (roi_stats keys + a saved PNG a multimodal agent
can open):

```python
import json
from hescope.agent.agent_bridge import selection_stats

raw = get_current_selection()
if raw != "NO_SELECTION":
    stats = selection_stats(get_source(), json.loads(raw))
    # stats: width_px, height_px, mean_rgb, he_deconvolution
    #        {hematoxylin_mean, eosin_mean}, tissue_fraction, patch_path
```

### 4.2 `get_latest_selection()` -> submitted ROIPayload

```json
{
  "slide_name": "demo_he.png",
  "slide_dimensions": [6000, 4000],
  "mpp": null,
  "magnification": null,
  "roi": {"kind": "rect", "points_level0": [[x, y], "..."],
          "bbox_level0": [x0, y0, x1, y1]},
  "patch_path": "/abs/path/agent_out/patches/<ts>_rect_x0_y0_x1_y1.png",
  "stats": {"width_px": 0, "height_px": 0, "mean_rgb": [0, 0, 0],
            "he_deconvolution": {"hematoxylin_mean": 0.0, "eosin_mean": 0.0},
            "tissue_fraction": 0.0},
  "created_at": "ISO8601",
  "roi_id": null
}
```

`ROIPayload.from_json(raw)` reconstructs the dataclass;
`payload.to_agent_prompt()` renders a human+LLM-readable summary.

## 5. Patch image locations

- Submitted ROIs: `agent_out/patches/<utc-ts>_<kind>_<x0>_<y0>_<x1>_<y1>.png`
  (absolute path in `payload.patch_path`); history JSONL at
  `agent_out/roi_history.jsonl`.
- Live-selection stats (`selection_stats`): a lazily created per-process
  temp dir (`tempfile.mkdtemp(prefix="hescope_live_patches_")`) unless an
  explicit `out_dir` is passed; files named
  `live_<utc-ts>_<kind>_<x0>_<y0>_<x1>_<y1>.png`.

## 6. Writing results back

Record an agent run in the DB (no-op safely when `db.enabled` is False):

```python
if db.enabled:
    run_id = db.run_repo.record(
        tool="my_analysis",            # short tool/agent name
        input={"bbox_level0": sel["bbox_level0"], "slide": sel["slide"]},
        output_text="Description of what the agent concluded.",
        roi_id=None,                   # or a rois-table id if one exists
        model="my-model-name",
        status="ok",                   # or "error"
    )
```

`AgentRunRepo.record` returns the new row id; runs are visible in the app's
"Agent runs" panel. To annotate ROIs persistently, use the
`annotate_roi(roi_id, label=..., notes=...)` tool (or
`db.roi_repo.update_annotation` directly).

User/agent interactions are traced in the `interactions` table via
`InteractionRepo` (`record` / `recent` / `for_slide`; fully exception-safe —
`record` returns None on failure). Both sides write: the agent tools record
`selection_view` (`get_current_selection`), `tool_call` (`query_annotations`,
`get_slide_info`) and `label_set` (`annotate_roi`), while the notebook's own
handlers record `roi_submit`, `label_set`, `roi_delete` and `analysis_run`
with `"actor": "human"` in the payload, so an agent-written label and a
user-typed one can be told apart. `human_gate` is reserved — no UI writes it
yet. For QuPath interop,
`hescope.interop.geojson.export_rois_geojson(db.engine, slide_id, path)` writes a
FeatureCollection to disk (bbox polygons, `classification` mapped from
`label`); `slide_geojson_text(db.engine, slide_id)` returns the same document
as a string, and is what the Annotations panel's GeoJSON download button
hands the user.

## 7. Quick end-to-end example (via marimo-pair execute-code)

```python
import json, marimo._code_mode as cm

async with cm.get_context() as ctx:
    g = ctx.globals
    raw = g["get_current_selection"]()
    if raw != "NO_SELECTION":
        sel = json.loads(raw)
        print("live bbox (level-0):", sel["bbox_level0"], "on", sel["slide"])
```

## 8. Repo-local agent skill

A ready-to-install Agent Skills package lives at `skills/he-scope/SKILL.md`
(frontmatter `name: he-scope`). It condenses this contract into a skill:
pair connection steps, the full tool list with return schemas (including
`annotate_roi` / `query_annotations` / `get_slide_info`), the canonical
read-selection → analyze → write-label → train workflow, and guidance for
loop-style long tasks with a human gate before irreversible actions. Install
it (or point your agent at the file) when you want the HE-Scope workflow
available on demand.

## 9. Analysis capabilities

The analytics stack (SPEC-ML) is plain module-scope code — no UI required.
Everything below works offline with numpy/scipy/scikit-image (+ joblib /
scikit-learn for training); torch/torchvision are OPTIONAL and only used
lazily by `hescope.analysis.features.extract_embedding`.

```python
import json

import hescope
from hescope.core.rois import ROI, extract_patch, patch_mpp

# What is available right now (never raises; check the "error" key):
caps = json.loads(get_analysis_capabilities())
# -> {"analyses": [...], "torch_embedding_available": bool,
#     "available_encoders": {...}, "models": [...]}

# Nuclei + QC on the live selection patch:
raw = get_current_selection()
if raw != "NO_SELECTION":
    sel = json.loads(raw)
    roi = ROI(kind=sel["kind"],
              points=tuple(tuple(p) for p in sel["points_level0"]))
    patch = extract_patch(get_source(), roi, max_size=1024)
    # NOT sel["mpp"] -- that is the LEVEL-0 mpp, and extract_patch downsamples
    # anything wider than max_size. detect_nuclei's mpp is microns per PATCH
    # pixel, so the level-0 value overstates density_per_mm2 by the extraction
    # downsample SQUARED (16x for a 4096 px ROI). patch_mpp does the division.
    mpp = patch_mpp(get_source(), roi, patch)
    labels, stats = hescope.detect_nuclei(patch, mpp=mpp)
    # stats: NucleiStats(count, density_per_mm2, mean_area_px,
    #                    mean_intensity_h, mask_coverage)
    qc = hescope.qc_report(patch, mpp=mpp)
    # qc: {"tissue_fraction", "blur_score", "is_blurry", "brightness_mean"}

# Whole-slide metric grid + heatmap overlay (tissue_fraction_proxy is the
# fast built-in metric; any fn(pil_tile) -> float works):
src = get_source()
grid = hescope.compute_grid(src, hescope.tissue_fraction_proxy,
                            tile=256, downsample=16.0)
thumb = src.get_thumbnail((512, 512)).convert("RGB")
blended = hescope.render_heatmap(thumb, grid)  # NaN cells stay untouched

# Train / use a weakly-supervised patch classifier from labeled ROIs
# (requires db.enabled; needs >= 2 labels with >= 2 patches each):
if db.enabled:
    info = hescope.train_from_annotations(db.engine, name="tumor_vs_stroma",
                                          models_dir="data/models")
    # info: ModelInfo(name, labels, feature_dim, cv_accuracy, n_samples, ...)
    model, meta = hescope.load_model("tumor_vs_stroma", "data/models")
    probs = hescope.predict_patch(model, meta, patch)  # {label: prob}
    metric = hescope.make_prob_metric(model, meta, "tumor")
    tumor_grid = hescope.compute_grid(src, metric, tile=256, downsample=16.0)
```

Direct imports also work: `hescope.analysis.nuclei.detect_nuclei`,
`hescope.analysis.qc.qc_report`, `hescope.analysis.stain.macenko_normalize`,
`hescope.analysis.features.extract_features`, `hescope.analysis.grid.iter_grid`,
`hescope.analysis.heatmap.compute_grid`, `hescope.analysis.ml.train_from_annotations` —
all are additionally re-exported at the `hescope` top level.

Notes:
- `compute_grid` cells skipped by the tissue filter are `np.nan` in the
  returned grid; `render_heatmap` blends only non-NaN cells. A tile whose
  metric *raised* is also `np.nan`, so pass `error_cb=fn(gx, gy, exc)` if you
  need to tell "no tissue here" from "the metric is broken" — without it a
  sweep in which every tile failed is byte-identical to the bare thumbnail.
- `extract_embedding` (ResNet18, 512-d) returns `None` when torch /
  torchvision / weights are unavailable; the first successful call may
  download weights. Check `caps["torch_embedding_available"]` first — it is
  a pure `find_spec` probe and never triggers a download.
- `train_from_annotations` raises `ValueError` with a clear message when
  there is not enough labeled data; catch it and report it to the user.
- Always check `info.warning` (also `meta["warning"]`): it is non-None when a
  `HESCOPE_EMBEDDER` could not be loaded and training fell back to the 56
  handcrafted features, and when labeled ROIs were skipped because their
  patch file is gone — including when that dropped a whole class.
- Handcrafted models record `meta["feature_raster"]` and are scored at that
  raster: `extract_features` is raster-dependent (nuclei counts, mean nucleus
  area, blur), so a tile must be resampled to the training raster before it
  is comparable. `predict_patch` / `make_prob_metric` do this for you.

## 10. Partitioning

`app.py` is split by one marker, `▼▼▼ SCRATCH ▼▼▼`, in the last cell before
the `if __name__ == "__main__":` footer:

- **Above and including the marker cell = skeleton.** Human-maintained. Do
  not edit it on disk (§2 already forbids that for the whole file during a
  live session) and do not propose edits to it via `ctx.edit_cell` either —
  every skeleton cell is covered by `skeleton.lock` and by the cell-count /
  cell-length ceiling, both enforced in `tests/test_harness.py`. An edit
  here makes `test_skeleton_unchanged` fail; that failure is the point, not
  a bug to route around.
- **Below the marker cell = scratch.** `ctx.create_cell` appends new cells
  just above the module footer — i.e. immediately after the marker cell —
  so using `ctx.create_cell` already keeps you on the right side of the
  line without having to think about it. Scratch cells do not count against
  the skeleton's cell-count / cell-length budget, and are session-scoped:
  promote anything worth keeping into `hescope/` (with a test, per R-2 in
  the repo's engineering log) before the session's scratch cells are
  cleared — see §11.

**Why the marker lives *inside* a cell, not as a bare comment between two
`@app.cell` blocks:** marimo regenerates the entire file from its cell list
on every save (`marimo/_ast/codegen.py:generate_filecontents`) — anything
that is not part of a cell's own source text is silently dropped on the
next save, live-session autosave included. A comment inside a cell's body
is part of that cell's code and survives; a free-floating comment between
cells does not. (This is also why `# NEVER edit app.py on disk during a
live session` in §2 is the load-bearing rule, not this one — a hand edit
that doesn't go through `ctx.*` can be clobbered by the next autosave
regardless of where the marker lives; see the repo's engineering log on
marimo autosave clobbering `app.py` edits.)

Today the skeleton is the entire notebook — `app.py`'s 2,400-line
extraction (design doc §9.2) has not happened yet — and the scratch region
is empty until an agent or a human uses it.

## 11. Countertop

The scratch region does not need to re-derive state from the database:
everything in §3's entry-point table is already live in kernel globals by
the time a cell runs. A scratch cell can call `get_source()`, `get_vp()`,
`get_current_selection()`, `get_slide_info()`, `query_annotations()`,
`agent_bridge`, `db` and `open_slide` directly — the same names the
workflow example in `skills/he-scope/SKILL.md` §3 uses.

Design doc §10 additionally specifies a richer, layer-based countertop:
`current_slide`, `current_selection` (the drawn polygon plus which view it
came from), `available_layers` (`list[Layer]`, each with its `registration`
status) and `resolve(layer_id)` (`-> ndarray` of hit entity indices),
built on the `layers` / `selection_resolutions` tables already in
`hescope/store/db.py`. **Those two tables exist; the four names do not
exist in `app.py` yet** — Phase 1 ("通用点集层") has not started. Do not
assume `current_slide` or `resolve()` are callable until this paragraph is
replaced by a row in §3's table: until then they are a plan, not a
contract, and `tests/test_harness.py` deliberately does not check for them
(see that file's docstring on why Lock 1 is table-driven rather than
"every backtick in the document").

## 12. Invariants

When not to trust a number — these are the single source of truth; each
`skills/*/SKILL.md`'s own "failure" notes only say how a given workflow
trips one of these, they do not redefine them.

- A layer's `registration` is `'unregistered'` (`hescope/store/db.py`'s
  `layers.registration`, which defaults to exactly that string) → stop and
  ask the user; never substitute an identity transform for a real one.
- Two measurements differ in `mpp_effective` (`measurements.mpp_effective`)
  beyond a stated tolerance → do not average them; report them as
  not-comparable instead.
- A cohort has no recorded tissue-source-site / submitting-institution
  information → do not draw a "morphology ↔ molecular" conclusion from it.
  Site-specific digital-pathology signatures are picked up by both
  handcrafted features and foundation-model embeddings (design doc §5.3);
  without provenance you cannot tell a real biological effect from an
  institution fingerprint.
- Any density-style measurement (count per area) → must declare its
  denominator's source (which tissue-region kind the area came from) before
  it is comparable to another density number.
- A selection made on a projection view (anything whose `frame` is a
  `<projection_id>`, not `level0`) → must record `projection_id`, `method`,
  `params` and the random seed used to produce the projection (the layer's
  `params_json`), not just the polygon.
- A selection made on a non-deterministic projection → on recompute,
  compare `index_digest` (`selection_resolutions.index_digest`) against the
  prior value and report if it changed; never assume the same polygon still
  hits the same entities.
