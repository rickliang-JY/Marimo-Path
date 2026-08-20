---
name: he-scope
description: Use when connecting to a running HE-Scope marimo H&E pathology app. When the user asks to read the current selection (selection/ROI), write annotations back (label/notes), query existing annotations, fetch slide metadata, or trigger an analysis/training workflow, attach to the `marimo edit app.py --no-token` session via marimo-pair and call the module-scope tools listed in this file.
---

# HE-Scope agent skill

HE-Scope is a marimo notebook app (`app.py`): an H&E pathology slide viewer with
ROI capture and a code-agent bridge. This skill describes how, as an agent, to
connect to a **running** HE-Scope session, read the user's selection, write
annotations back and drive the analysis loop.

## 1. Connecting (pair)

1. Confirm the app was started in **edit mode**:

   ```bash
   marimo edit app.py --no-token
   ```

   `marimo run` is read-only and marimo-pair cannot attach to it; `marimo edit`
   is required.
2. Remind the user to **keep the browser tab open**, and to press Run once on
   first load — marimo 0.23 loads lazily, so kernel globals do not exist until
   the cells have executed.
3. With the marimo-pair skill installed, tell your agent "connect to my marimo
   notebook"; or enter the kernel with `marimo._code_mode`:

   ```python
   import marimo._code_mode as cm

   async with cm.get_context() as ctx:
       for cell in ctx.cells:  # on first connect, ensure every cell has run
           ctx.run_cell(cell.id)
   ```

4. Hard rule: **never modify `app.py` on disk while the session is alive.** All
   interaction goes through `ctx.create_cell` / `ctx.edit_cell` / `ctx.run_cell`,
   and state is read only through the tool functions below.

## 2. Tool list (kernel globals; all return strings and never raise)

| Tool | Signature | Returns |
| --- | --- | --- |
| `get_current_selection` | `() -> str` | JSON of the box/lasso the user is dragging right now, in level-0 coordinates; the exact string `NO_SELECTION` when nothing is selected |
| `get_latest_selection` | `() -> str` | JSON of the ROIPayload from the most recent "Send to code agent" (includes `patch_path`); `NO_SELECTION` when nothing has been submitted |
| `get_slide_info` | `() -> str` | JSON metadata of the open slide: `{"name", "dimensions": [w,h], "mpp", "levels", "level_downsamples", "db_id", "annotation_count"}`; the exact string `NO_SLIDE` when no slide is open |
| `annotate_roi` | `(roi_id: int, label: str \| None = None, notes: str \| None = None) -> str` | Writes label/notes back to the rois table and returns the updated row as JSON; returns `{"error": ...}` in DB-free mode or when the ROI does not exist |
| `query_annotations` | `(label: str \| None = None, limit: int = 50) -> str` | JSON list of this slide's annotation rows (optionally filtered by exact label); `[]` when no slide is open, `{"error": ...}` in DB-free mode |
| `get_analysis_capabilities` | `() -> str` | JSON of the available analyses (nuclei/QC/stain-norm/heatmap/training), torch availability, and trained models |

Supporting kernel globals: `db` (DBContext, with `db.enabled` / `db.roi_repo` /
`db.run_repo`), `get_source()` (the current SlideSource) and `agent_bridge` (the
jsonl history).

Every annotation and tool call is also recorded in the `interactions` table
(kind: selection_view / roi_submit / label_set / analysis_run / tool_call /
human_gate), which feeds the data flywheel and automation-bias research.

## 3. Typical workflows

### 3.1 Read selection → analyze → write label back → trigger training

```python
import json

raw = get_current_selection()          # 1. zero-click read of the user's selection
if raw != "NO_SELECTION":
    sel = json.loads(raw)
    info = json.loads(get_slide_info())  # 2. slide metadata (mpp, levels)
    # 3. analyze: selection_stats / detect_nuclei / qc_report / heatmap ...
    # 4. write the annotation back (roi_id comes from get_latest_selection()
    #    or query_annotations())
    annotate_roi(sel_roi_id, label="tumor", notes="agent: high nuclei density")
    # 5. once there is enough data, trigger training with
    #    hescope.train_from_annotations(db.engine, ...)
```

### 3.2 Query existing annotations

```python
rows = json.loads(query_annotations(label="tumor", limit=20))
for r in rows:
    print(r["id"], r["label"], r["bbox"])
```

## 4. Loop mode (long-running tasks)

For long tasks that need several rounds, work in the following loop, where
**every step can be interrupted by a human**:

1. **Query.** Get the current state with `query_annotations()` /
   `get_current_selection()`.
2. **Analyze.** Run nuclei/QC/embedding and so on over the patch (see
   `get_analysis_capabilities()`).
3. **Write back.** Persist the conclusion with
   `annotate_roi(roi_id, label=..., notes=...)`, and record the analysis itself
   with `db.run_repo.record(tool=..., ...)`.
4. **Request a human gate.** Before anything irreversible — deleting an ROI,
   overwriting a human annotation, starting a long training run — explain the
   plan to the user and wait for confirmation. Record the human decision in
   interactions (kind="human_gate", writable via `InteractionRepo.record` using
   `db`'s engine).
5. Return to 1 until the task is done or the user stops it.

## 5. Export and interoperability

- QuPath interop:
  `hescope.interop.geojson.export_rois_geojson(db.engine, slide_id, path)` exports all
  annotations for a slide as GeoJSON that QuPath can import (bbox polygons, with
  `classification` mapped from label).
- General export:
  `hescope.store.db.export_rois(db.engine, slide_id=..., fmt="json"|"csv")`.

The full contract is in `AGENTS.md` at the repository root.
