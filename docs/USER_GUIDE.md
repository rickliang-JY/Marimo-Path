# HE-Scope user guide

**English** · [简体中文](USER_GUIDE.zh-CN.md)

An H&E pathology image viewing platform — an interactive marimo whole-slide
viewer with ROI selection, annotation management and TCGA public-data access,
able to pair live with a code agent (Kimi Code / Claude Code / Codex / Hermes
and others).

---

## Contents

1. [Quick start](#1-quick-start)
2. [Layout and controls](#2-layout-and-controls)
3. [ROIs and measurement](#3-rois-and-measurement)
4. [Annotations and the database](#4-annotations-and-the-database)
5. [TCGA public data](#5-tcga-public-data)
6. [Analysis: statistics / QC / heatmaps / training a classifier](#6-analysis-statistics--qc--heatmaps--training-a-classifier)
7. [Pairing with a code agent](#7-pairing-with-a-code-agent)
8. [FAQ](#8-faq)
9. [Where data lives](#9-where-data-lives)
10. [Degraded modes](#10-degraded-modes)
11. [What comes next](#11-what-comes-next)

---

## 1. Quick start

```bash
cd project
pip install -e .        # pyproject.toml is the source of truth; requirements.txt is a shim
hescope app             # = marimo edit app.py --no-token (edit mode, required for agent pairing)

# The equivalent manual commands, from the repository root:
# marimo edit app.py --no-token      # edit mode
# marimo run app.py                  # pure viewing mode (no code editor exposed)
```

Open the printed address in your browser (`http://localhost:2718` by default).

> **Note:** `--no-token` matters — it is what lets marimo-pair discover the
> session automatically (see section 7). Use it in a trusted environment.

On first use, click **"Generate & open demo slide"**. The platform generates a
6000×4000 synthetic H&E demo slide locally (about 15 seconds), after which every
feature is immediately available.

---

## 2. Layout and controls

The app is a left sidebar plus a main area. **The whole app has exactly one main
figure**: zooming, panning and selecting all happen on it.

### 2.1 Sidebar

Four blocks, top to bottom:

- **Open slide.** Three ways to open an image: generate the synthetic H&E demo
  slide in one click; paste a local path (.svs / .tiff / .ndpi / .png / .jpg)
  and press Open; or upload a file directly. Opened slides are registered in the
  database automatically when it is enabled.
- **Display.** Brightness / contrast / gamma sliders, channel view (`rgb`,
  single-channel `r`/`g`/`b` grayscale, and the `hematoxylin` / `eosin` H&E
  color-deconvolution channels), and a "show ROI overlays" toggle. These affect
  display only — never the data, and never selection coordinates.
- **Navigator.** A 200px thumbnail with a red box marking the current viewport;
  with overlays on, it also marks every ROI position.
- **ROIs.** The ROIs selected in this session; delete by index or clear all.

### 2.2 Title row and toolbar

- **Title row:** app name, current slide name / dimensions / mpp / equivalent
  magnification, and a database status badge (the connected backend's name, or
  `DB-free`).
- **Toolbar:** every frequently used control in one compact strip — mouse mode
  (pan / box select / lasso), a zoom (downsample) slider, Zoom to fit, pan
  direction buttons (◀ ▶ ▲ ▼, stepping a quarter viewport), measure mode, box as
  circle, Add ROI, and Send to code agent.

### 2.3 The unified viewer

The single large plotly figure in the main area:

- **Scroll to zoom, drag to pan** (with mouse mode set to pan, or via the mode
  bar on the figure). These are purely visual: selection coordinates are always
  view-pixel coordinates and are unaffected.
- **Selection:** set mouse mode to box select or lasso, then drag directly on
  the figure (see section 3).
- Existing ROI outlines are overlaid on this same figure — red, or green for the
  one selected in the annotation browser.

### 2.4 Status line and collapsible panels

Below the figure is a status line (viewport center / magnification, measurement
results, hints). Below that sit panels collapsed by default: **Annotations**
(browser + editor + export), **Agent console** (agent prompt, submission
history, agent run records) and **TCGA browser** (search / download, with a
progress bar). At the very bottom is the **agent pairing guide**, expanded by
default, which walks you through connecting the notebook to a code agent.

> Adjustment pipeline order: read region → resize → brightness/contrast/gamma →
> channel view → overlay ROI outlines. Patch extraction and statistics always
> work from the **unadjusted** source data, so selection coordinates and colors
> stay faithful.

---

## 3. ROIs and measurement

### 3.1 Making a selection

1. Pick a mouse mode in the toolbar: **box select** or **lasso**.
2. Drag directly on the main figure — you may zoom in first, and coordinates are
   converted automatically.
3. Optionally tick **box as circle**, and the box is interpreted as its inscribed
   circle.
4. Click **Add ROI** in the toolbar to add it to the sidebar ROI list, where you
   can delete by index or clear all.

All coordinates are converted back to level-0 (full-resolution) pixels.

### 3.2 Sending to a code agent

Click **Send to code agent** in the toolbar:

- The selected region's patch is extracted (PNG) and statistics are computed
  (mean RGB, H&E deconvolution intensities, tissue fraction, ...).
- A structured payload is generated and appended to the history
  (`agent_out/roi_history.jsonl`).
- With the database enabled, the ROI is also written to the `rois` table and one
  `agent_runs` row is recorded.
- The **Agent console** panel shows the prompt text presented to the agent along
  with the full JSON.

### 3.3 Measure mode

With **measure mode** ticked in the toolbar, a box selection is not stored as an
ROI; pressing **Add ROI** displays physical dimensions instead:

```
512.0 x 384.0 px = 128.0 x 96.0 um (diag 160.0 um)
```

Micrometers are shown when mpp is known, pixels only when it is not. Untick to
return to normal selection.

---

## 4. Annotations and the database

### 4.1 Annotation browser

With the database enabled, this lists every persisted ROI for the current slide
(those that came from Send to code agent). The core interactions:

- **Click any row → the viewport jumps.** It centers on that ROI, zooms so the
  ROI fills about 80% of the view, and highlights it green in the overlay.
- **Edit:** with a row selected, change its label (tumor / stroma / necrosis,
  ...) and notes, then press Save annotation.
- **Delete:** Delete ROI.
- **Export:** download all annotations as JSON or CSV in one click.

### 4.2 Agent runs

Every Send to code agent records the tool name, status, associated ROI, model,
timestamp and an output summary. This table is also where **the agent writes
analysis results back** (see 7.4).

### 4.3 Bulk registration from the command line

```bash
python -m hescope.cli init                      # create tables
python -m hescope.cli ingest /path/to/slides -r # register a whole tree recursively
python -m hescope.cli list                      # list registered slides
```

### 4.4 Changing databases

SQLite is the zero-configuration default. Switching to PostgreSQL/MySQL is just
an environment variable:

```bash
export HESCOPE_DB_URL="postgresql://user:pass@host:5432/hescope"
marimo edit app.py --no-token
```

---

## 5. TCGA public data

### 5.1 Search

Scroll to the TCGA panel, choose a cancer project (TCGA-BRCA / LUAD / LUSC /
COAD / KIRC / GBM / OV / ALL), optionally enter a sample type (such as
`Primary Tumor`), and press **Search GDC**. Results enter the local catalog and
the table shows file name, case ID, sample type, size and download state.

### 5.2 Download and open

Select a row → **Download & Open**:

- A **progress bar** appears, updating live:
  `Downloading… 128.4 / 532.0 MB (24%)`.
- Clicking again mid-download is blocked with a "already downloading" notice.
- On completion the viewer switches to the new slide, and every selection and
  annotation feature is immediately available.

> **Note:** most TCGA slides are 100 MB–2 GB, so the first download takes a
> while. Files are cached under `data/tcga/`, and opening them again is instant.
> GDC open data needs **no token**.

**Parallel download.** Downloads use 8 concurrent HTTP Range requests by default
(natively supported by the GDC endpoint), which helps considerably on
high-latency links. `HESCOPE_DL_WORKERS` tunes the concurrency (default 8,
clamped to 1–16; set it to 1 to fall back to a traditional single-threaded
download). The in-progress temporary file is named `<filename>.part` and is only
renamed to the final file after verification passes — file size, plus the md5
that GDC provides. An interrupted `.part` is **not** resumed; a fresh download
starts from the beginning, while already-completed files are always skipped. If
a concurrent download errors partway through, it automatically falls back to an
ordinary single-stream download with no manual intervention.

---

## 6. Analysis: statistics / QC / heatmaps / training a classifier

Among the collapsible panels below the main area is an **Analysis** panel (next
to Annotations) offering four kinds of analysis. All of them degrade gracefully:
with no selection, no annotations or no model they show a callout rather than
crashing.

### 6.1 Analyze current selection

Drag a box or lasso on the unified viewer, then press **Analyze current
selection**:

- **Nuclei detection** runs on the selected patch (H&E deconvolution + Otsu +
  watershed segmentation), reporting nuclei count, density (per mm² when the
  slide has mpp), mean area and coverage.
- A **QC report** is produced at the same time: tissue fraction, blur score,
  whether it is blurry, and brightness.
- Results are shown as a compact table plus a status strip.

With no live selection it falls back to analyzing the **most recently submitted
ROI**; with neither available, a hint appears under the button.

### 6.2 Stain normalization toggle (Macenko)

The sidebar **Display** panel has a **stain normalize (Macenko, display-only)**
checkbox. When ticked, the view image is Macenko-normalized. The reference
statistics are fitted once on the **first non-blank view image** and cached. This
affects **display only** — selection coordinates and the raw pixels read by
downstream analysis are unchanged.

### 6.3 Heatmaps

In the Analysis panel, choose:

- **metric:** `tissue_fraction`, `nuclei_density` (nuclei count per tile,
  automatically downsampled for large tiles to control cost), and — once a model
  is trained — `model_prob:<label>` (predicted probability for that label).
- **model:** one of the trained models under `data/models/` (training in 6.4).
- **tile size:** 128 / 256 / 512.

Press **Run heatmap** to scan the whole slide tile by tile, with progress shown;
clicking again while it runs is blocked. The result is overlaid on the slide
thumbnail in viridis false color inside the Analysis panel. Tick **show heatmap
on navigator** and the sidebar navigator switches to the heatmap overlay too
(untick to restore). The computed grid and its parameters stay in session state.

### 6.4 Train from annotations

Enter a model name and press **Train from annotations**: the labeled ROI patches
from the annotation panel train a StandardScaler + LogisticRegression
weakly-supervised classifier (requiring at least 2 samples per label and at
least 2 distinct labels). On success a table shows labels, sample counts and
cross-validation accuracy (`cv_accuracy`); with insufficient data a warning
explains exactly why. **A database is required** — DB-free mode reports that
training is unavailable. After training, the heatmap model dropdown refreshes
automatically.

### 6.5 What the agent can call

A zero-argument tool `get_analysis_capabilities()` is added to the kernel
globals. It returns JSON listing the available analyses,
`torch_embedding_available` (a pure `find_spec` probe that never triggers a
weight download) and the trained models. It never raises — on failure it returns
`{"error": ...}`. The analysis functions themselves are available directly at the
`hescope` top level: `hescope.detect_nuclei`, `hescope.qc_report`,
`hescope.macenko_normalize`, `hescope.compute_grid`, `hescope.render_heatmap`,
`hescope.train_from_annotations`, `hescope.predict_patch` and others. Agents can
call them directly per the contract in `../AGENTS.md` section 8.

---

## 7. Pairing with a code agent

This is the platform's core feature: **an agent can enter the running notebook,
read what you have selected, and write analysis results back into the
interface**.

### 7.1 How it works

The official skill
[marimo-pair](https://github.com/marimo-team/marimo-pair) lets a code agent
connect to a running marimo kernel and execute Python inside it. HE-Scope
pre-installs tool functions in the kernel globals; the agent calls them by name.

### 7.2 One-time setup

```bash
# Any agent supporting Agent Skills (Kimi Code / Codex / ...):
npx skills add marimo-team/marimo-pair

# Claude Code:
/plugin marketplace add marimo-team/marimo-pair
/plugin install marimo-pair@marimo-pair
```

`../AGENTS.md` at the repository root is the contract written for agents — an
agent entering the project directory reads it automatically, so you do not have
to teach it by hand.

### 7.3 Starting and connecting

1. Start with `marimo edit app.py --no-token` and **keep the browser open**.
2. On first load marimo is lazy (cells have not executed) — press Run once in
   the UI, or let the agent trigger the run itself.
3. Then just say to your agent, for example:
   > "Connect to my marimo notebook and see what I have selected."

The agent discovers the server, attaches to the kernel, and is ready.

> **Why `marimo edit` and not `marimo run`?**
> `marimo run` is read-only: the code-execution endpoints are disabled
> server-side (`/api/sessions` and `/execute` both require edit permission and
> return 401 in run mode), so marimo-pair fundamentally cannot attach to a
> run-mode session. If you want a clean app interface with every cell hidden:
> after starting, click the eye icon in the bottom-right toolbar (Toggle app
> view) or press Cmd/Ctrl + `.`. The interface becomes identical to run mode,
> but the session is still an edit session and agent connection is unaffected.
> Every cell in this app is `hide_code` by default, so the code area is hidden
> already.

### 7.4 Entry points available to the agent (kernel globals)

| Entry point | Purpose |
|---|---|
| `get_current_selection()` | **Zero-click**: the box/lasso you are dragging on the figure right now (coordinates, bbox, zoom). Returns `NO_SELECTION` when nothing is selected. |
| `get_latest_selection()` | The complete payload from the most recent **Send to code agent** (JSON: coordinates, patch path, H&E statistics). |
| `agent_bridge` | Submission history (`agent_bridge.history()`) and the patch file directory. |
| `db.roi_repo` / `db.run_repo` | Annotations and agent run records (when the database is enabled). |
| `open_slide(path)` | Lets the agent open a slide itself. |
| `get_analysis_capabilities()` | JSON of available analysis capabilities plus trained models (see 6.5; never raises). |

**The typical loop:**

1. You circle a suspicious region on the figure — no button press needed.
2. You tell the agent "analyze what I circled" → it calls
   `get_current_selection()`, gets the coordinates and the patch image, and
   analyzes them.
3. The agent writes its conclusion back via `db.run_repo.record(...)`.
4. You see the agent's analysis directly in the **Agent runs** panel.

### 7.5 A real example

```
You:    (drag a box on the figure)
You:    "What is this region? Compare it with the tumor areas I annotated."
Agent:  [calls get_current_selection() -> gets bbox + patch]
        [calls db.roi_repo.search(label="tumor") -> gets past annotations]
        [analyzes, compares, calls db.run_repo.record(...) to write back]
You:    (read the conclusion in the Agent runs panel; click through in the
         annotation browser to re-inspect)
```

---

## 8. FAQ

### I switched agents — are my previous records still there?

**Yes.** All persistent data lives on the platform side, independent of which
agent you use:

| Data | Location | After switching agents |
|---|---|---|
| Annotations (ROI, label, notes) | The `rois` table in `data/hescope.db` | Fully retained |
| Agent run records | The `agent_runs` table in `data/hescope.db` (its `model` field distinguishes which agent wrote it) | Fully retained |
| Selected patch images | `agent_out/patches/*.png` | Fully retained |
| Submission history | `agent_out/roi_history.jsonl` | Fully retained |
| TCGA catalog and downloaded slides | `data/tcga/` | Fully retained |

Whether it is Kimi Code, Claude Code, Codex or Hermes, connecting to the same
running notebook (or the same project directory) shows the same records.

**Only in-session transient state is lost**: a live selection you never sent, and
an ROI list you never submitted — both disappear when the notebook restarts. So
remember to **Send to code agent** for selections that matter.

### What about a different machine, or sharing with others?

Copy the whole project directory (`data/` and `agent_out/` are the important
parts) for a complete migration. Alternatively, point `HESCOPE_DB_URL` at a
PostgreSQL server so several people on several machines share one annotation
store — slide files still need shared storage.

### The agent says it cannot connect / cannot find my notebook

Check three things: ① did you start with `--no-token`; ② is the browser page
still open; ③ have the cells run (there must be output on the page). On the agent
side, confirm the marimo-pair skill is installed.

### The agent calls `get_current_selection()` and gets NO_SELECTION

Two possibilities: you genuinely have not selected anything yet (drag a box
first), or the cells have not run. Note the distinction: **live selection** is
`get_current_selection()`, **submitted history** is `get_latest_selection()`.

### Does it work with no network / no OpenSlide / a broken database?

All of them work. See the next section.

---

## 9. Where data lives

```
project/
├── data/
│   ├── hescope.db            # main store: slides / rois / agent_runs (SQLite by default)
│   └── tcga/
│       ├── catalog.db        # TCGA search catalog cache
│       └── <file_id>/*.svs   # downloaded slides
├── agent_out/
│   ├── roi_history.jsonl     # the full payload of every Send (append-only)
│   └── patches/*.png         # selected region images
└── assets/demo_he.png        # the demo slide (regenerable)
```

`data/` and `agent_out/` are both gitignored; deleting them wipes every record.

---

## 10. Degraded modes

The platform is designed to run in any environment, degrading independently
along three axes:

| Missing | What you see | Still available |
|---|---|---|
| Database unreachable | A yellow notice at the top; the annotation and agent-runs panels report unavailable | Viewing, selection, measurement, TCGA, the jsonl bridge |
| No OpenSlide | Automatically reads SVS through the tifffile backend (region-level reads, memory-safe) | Everything |
| No network | TCGA search unavailable (notice shown) | Every feature for local slides |

---

## 11. What comes next

Planned but not yet implemented, in priority order:

- **BigQuery cohort filtering:** connect ISB-CGC public datasets to filter TCGA
  slide cohorts by clinical/molecular criteria (stage, subtype, expression),
  replacing per-project file browsing.
- **Cloud storage backends:** direct reads of slides on GCS/S3 and a hosted
  annotation store.
- **More agent entry points:** an MCP server wrapper and batch analysis
  pipelines.
- **Multi-user collaboration:** PostgreSQL plus shared storage for collaborative
  annotation.

---

*Technical detail is in `../README.md`; the agent contract is in
`../AGENTS.md`. The platform version this guide describes is the master branch
of the git repository.*
