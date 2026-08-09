# AGENTS.md — Contract for code agents pairing with HE-Scope

HE-Scope is a marimo notebook app (`app.py`): an H&E pathology image viewer
with ROI capture and a code-agent bridge. This file is the contract for code
agents (Kimi Code / Claude Code / Codex / Hermes) connecting to a LIVE
session via marimo-pair.

## 1. Starting the app

```bash
hescope app                          # installed CLI (pip install -e .)
# or, equivalently, from the repo root:
marimo edit app.py --no-token
```

The app MUST run under `marimo edit`: marimo-pair requires the edit-mode
APIs (`marimo run` is read-only — the server returns 401 on `/api/sessions`
and blocks `/execute`, so pairing cannot attach). Users who want a
chrome-free UI can enable "app view" (eye icon in the bottom-right toolbar,
or Cmd/Ctrl + `.`) — the UI then looks exactly like run mode, but the
session stays an edit session and agent attachment is unaffected. All cells
in this app are `hide_code` by default.

marimo 0.23 opens lazy: kernel globals do NOT exist until the cells have
run. Either ask the user to press "Run" in the notebook, or run all cells
yourself via marimo-pair code mode:

```python
import marimo._code_mode as cm

async with cm.get_context() as ctx:
    for cell in ctx.cells:
        ctx.run_cell(cell.id)
```

After that, the entry points below are live in the kernel globals.

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
| `agent_bridge` | `hescope.agent_bridge.AgentBridge` bound to `agent_out/`; `.history()` / `.latest()` give past submitted payloads. |
| `db` | `DBContext` with `slide_repo` / `roi_repo` / `run_repo` when `db.enabled` is True (all None in DB-free mode). |
| `open_slide` | `hescope.slides.open_slide(path) -> SlideSource`. |
| `ensure_demo_slide()` | Returns the demo slide path, generating `assets/demo_he.png` in-process if missing. |
| `get_source()` / `get_vp()` | State accessors: current `SlideSource | None` and `ViewportState` (center, downsample, size). |
| `roi_plot` | The `mo.ui.plotly` capture surface (`None` before a slide is open); `roi_plot.value` is the raw plotly selection. |
| `get_analysis_capabilities()` | Zero-arg tool returning a JSON string: `{"analyses": [...], "torch_embedding_available": bool, "models": [...]}` (trained classifiers under `data/models/`). Never raises — on failure returns `{"error": ...}`. |
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
from hescope.agent_bridge import selection_stats

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
`hescope.geojson.export_rois_geojson(db.engine, slide_id, path)` writes a
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
lazily by `hescope.features.extract_embedding`.

```python
import json

import hescope
from hescope.rois import ROI, extract_patch

# What is available right now (never raises; check the "error" key):
caps = json.loads(get_analysis_capabilities())
# -> {"analyses": [...], "torch_embedding_available": bool, "models": [...]}

# Nuclei + QC on the live selection patch:
raw = get_current_selection()
if raw != "NO_SELECTION":
    sel = json.loads(raw)
    roi = ROI(kind=sel["kind"],
              points=tuple(tuple(p) for p in sel["points_level0"]))
    patch = extract_patch(get_source(), roi, max_size=1024)
    labels, stats = hescope.detect_nuclei(patch, mpp=sel["mpp"])
    # stats: NucleiStats(count, density_per_mm2, mean_area_px,
    #                    mean_intensity_h, mask_coverage)
    qc = hescope.qc_report(patch, mpp=sel["mpp"])
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

Direct imports also work: `hescope.nuclei.detect_nuclei`,
`hescope.qc.qc_report`, `hescope.stain.macenko_normalize`,
`hescope.features.extract_features`, `hescope.grid.iter_grid`,
`hescope.heatmap.compute_grid`, `hescope.ml.train_from_annotations` —
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
