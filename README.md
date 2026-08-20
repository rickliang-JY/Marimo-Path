<div align="center">

<img src="image/Marimo-icon.png" alt="HE-Scope" width="160">

# HE-Scope

**A marimo-native, agent-native H&E whole-slide viewer with a human–agent analysis loop.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-%E2%89%A53.10-blue.svg)
![marimo](https://img.shields.io/badge/marimo-%E2%89%A50.23-8B5FA8.svg)

**English** · [简体中文](README.zh-CN.md)

</div>

Browse gigapixel pathology slides in the browser, circle a region, and let your
code agent read that selection live — through [marimo-pair](https://github.com/marimo-team/marimo-pair) —
run the analysis stack on it, and write annotations back. Human and agent work
the same data, in the same kernel, in a closed loop.

HE-Scope is a single marimo notebook (`app.py`) backed by a plain Python package
(`hescope/`). Everything the agent touches is module-scope code, so the whole
analysis stack works headless, with no UI in the way.

## Features

- **One unified viewer.** A deep-zoom viewport (best pyramid level + resize)
  with pan/zoom, a navigator thumbnail, brightness/contrast/gamma controls,
  H&E channel views, ROI overlays and physical-size measurement — all on a
  single figure.
- **The ROI loop.** Box, lasso and circle selections map straight to level-0
  coordinates. "Send to code agent" exports a patch PNG plus statistics (mean
  RGB, H&E deconvolution, tissue fraction) and persists them; the agent reads,
  analyzes and writes annotations back, all traceable in the database.
- **Six agent tools**, module-scope in the notebook and reachable directly over
  marimo-pair: `get_current_selection()` (zero-click live selection),
  `get_latest_selection()`, `get_analysis_capabilities()`, `get_slide_info()`,
  `annotate_roi()` (write-back) and `query_annotations()`. Full contract in
  [AGENTS.md](AGENTS.md).
- **TCGA / GDC access.** Token-free search over open-access TCGA slides, a local
  SQLite catalog cache, parallel chunked download of 100 MB–2 GB SVS files (no
  resume, completed files skipped, md5 verified), and memory-safe reads through
  tifffile/zarr.
- **Analysis stack** — plain module-scope code, no UI required: nuclei
  detection, QC reports, Macenko/Reinhard stain normalization, 56 hand-crafted
  features, whole-slide metric grids with heatmap overlay, and weakly-supervised
  LogisticRegression training (annotations → model → probability heatmap).
- **FM encoder factory** (`hescope.embeddings`): GPFM (MIT, the default),
  H-optimus-0 (Apache-2.0), UNI2-h (CC-BY-NC-ND, academic comparison only and
  never the default) and a local ResNet18 (ImageNet) fallback. The registry
  imports with no heavy dependencies; weights load lazily.
- **Interaction trace.** Selection views, ROI submissions, label write-backs,
  ROI deletions, analysis runs and agent tool calls all land in the
  `interactions` table — from the notebook's own buttons as well as from the
  agent tools, so a label the user typed and a label the agent wrote are both
  recorded and are told apart by the row's `actor`. Recording is fully
  exception-safe. (`human_gate` is a reserved kind: there is no human-gate UI
  yet, so nothing writes it.)
- **GeoJSON export.** One click — Annotations → *Export ROIs (GeoJSON,
  QuPath)* — turns the open slide's annotations into a QuPath-compatible
  FeatureCollection.
- **Graceful degradation.** No database, no OpenSlide, or no network — the app
  still runs. Every degradation surfaces as a callout instead of crashing the
  notebook.

## Getting started

Requires **Python ≥ 3.10**. A virtual environment is recommended; `.venv/` is
already gitignored.

### 1. Create an environment and install

<details open>
<summary><b>uv (recommended, fastest)</b></summary>

```bash
uv venv --python 3.11                 # creates .venv
uv pip install -e ".[test]"           # core dependencies + pytest
```
</details>

<details>
<summary><b>Standard venv + pip</b></summary>

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Windows Git Bash:   source .venv/Scripts/activate
# macOS / Linux:      source .venv/bin/activate
pip install -e ".[test]"
```
</details>

> **`pyproject.toml` is the single source of truth** for dependencies.
> `requirements.txt` is only a compatibility shim that forwards to `-e .`.

### 2. Optional extras

| Extra | Installs | Unlocks |
| --- | --- | --- |
| `.[wsi]` | `openslide-python` | Native reads of real WSI formats (.svs / .ndpi / .mrxs) |
| `.[ml]` | `scikit-learn` `joblib` `torch` `torchvision` `timm` | Weakly-supervised training + FM embeddings |
| `.[test]` | `pytest` | The test suite |

```bash
uv pip install -e ".[wsi,ml,test]"      # everything at once
```

**Want training without torch?** Install just `scikit-learn joblib` and save
roughly 1 GB. All of `hescope.ml` — training, prediction, probability heatmaps —
works; only FM embedding falls back through its lazy-loading path.

**OpenSlide on Windows.** `openslide-python` is only the binding and still needs
the OpenSlide native library. The least painful route is to install the official
prebuilt binaries alongside it, which avoids configuring DLL paths by hand:

```bash
uv pip install openslide-bin openslide-python
```

> You can skip OpenSlide entirely: `hescope.slides` falls back to a
> tifffile/zarr backend for memory-safe region reads. You simply cover fewer
> proprietary formats.

### 3. Launch

```bash
hescope app                                  # = marimo edit app.py --no-token
hescope app --port 2718 --host 127.0.0.1     # explicit port / host
```

Open the printed address (`http://localhost:2718` by default), then press
**Cmd/Ctrl + `.`** to hide the code cells (app view). On first use, click
**"Generate & open demo slide"** to synthesize a 6000×4000 H&E demo slide and
exercise every feature immediately.

> **Activate the virtual environment before running `hescope app`.** The command
> replaces itself with `marimo` via `os.execvp`, which resolves the executable
> through `PATH`. Calling `.venv/Scripts/hescope.exe` without activating leaves
> `marimo` off `PATH` and fails with
> `error: could not launch marimo: [Errno 2] No such file or directory`.
> Activating fixes it. The equivalent manual command is
> `marimo edit app.py --no-token`.

Use `marimo edit`, not `marimo run`. Run mode is read-only — the server returns
401 for `/api/sessions` and `/execute` — so marimo-pair cannot attach. App view
is only a display toggle that hides code; the session stays an edit session and
agent pairing is unaffected.

### 4. Pair your agent

```bash
npx skills add marimo-team/marimo-pair   # the general marimo pairing skill
# or point your agent at the bundled skill: skills/he-scope/SKILL.md
```

Once paired, the agent can call the six tools above and run the full loop: read
selection → analyze → write annotations back → train.

### 5. Headless self-check

```bash
pytest                          # fully offline, no network needed
python app.py                   # executes every cell once (smoke test)
hescope init                    # create the database (default data/hescope.db)
hescope ingest /path/to/slides -r && hescope list   # bulk register + inspect
```

### Verified environment

The combination below was tested in this repository — `pytest` reports
**~940 passed, 17 skipped** (measured 2026-08-20; the exact passed count can
shift by one between runs because one race-condition regression test is
probabilistic by design — see its module docstring — so treat "~940" as
"run it yourself", not a promise). Of the 17 skips: 15 need a real Chrome
browser (`HESCOPE_BROWSER_TESTS=1`), 1 is a POSIX-only permission-bit check
that cannot run on Windows, and 1 is that same race test on a run where the
race did not reproduce. `pytest -rs` prints the live list. There is no CI in
this repo — no `.github/`, nothing runs this automatically — which is also
why there used to be a `tests-N passed` badge above: it went stale twice
(measured at 276, then real counts moved to 909 and then here without
anyone updating it) because a number that has to be hand-updated on every PR
will drift, and a stale badge is worse than no badge.

| Component | Version |
| --- | --- |
| Windows 11 / Python | 3.11 |
| marimo | 0.23.16 |
| numpy · scipy · scikit-image | 2.4.6 · 1.17.1 · 0.26.0 |
| torch (CPU) · torchvision · timm | 2.13.0+cpu · 0.28.0 · 1.0.28 |
| scikit-learn · joblib | 1.9.0 · 1.5.3 |
| openslide-python · OpenSlide library | 1.4.6 · 4.0.1 |
| zarr · tifffile · SQLAlchemy | 3.1.6 · 2026.3.3 · 2.0.51 |

## Documentation

| Document | Contents |
| --- | --- |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | User guide: interface, agent pairing, data persistence, FAQ ([中文](docs/USER_GUIDE.zh-CN.md)) |
| [AGENTS.md](AGENTS.md) | Code-agent contract: startup, hard rules, tool list, payload schemas, write-back examples |
| [skills/he-scope/SKILL.md](skills/he-scope/SKILL.md) | The bundled skill, in standard Agent Skills format |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Implementation roadmap and completed phases ([中文](docs/ROADMAP.zh-CN.md)) |
| [docs/STRATEGY.md](docs/STRATEGY.md) | Strategy: academic goals, open-source path, the FM license red line ([中文](docs/STRATEGY.zh-CN.md)) |
| [docs/PAPERS.md](docs/PAPERS.md) | Literature review and writing material ([中文](docs/PAPERS.zh-CN.md)) |
| [docs/OVERNIGHT-REPORT.md](docs/OVERNIGHT-REPORT.md) | The most recent overnight development report ([中文](docs/OVERNIGHT-REPORT.zh-CN.md)) |

## Repository layout

```
app.py                  The marimo notebook app (UI assembly; shipped with the
                        package and launched by `hescope app`)
hescope/                Python package: slides / rois / viewer / agent_bridge /
                        db / tcga, the analysis stack (nuclei, qc, stain,
                        features, grid, heatmap, ml), embeddings (FM factory),
                        geojson, cli
skills/he-scope/        Agent Skills package (SKILL.md)
tools/make_demo_slide.py  Synthetic H&E demo-slide generator
tests/                  pytest suite (offline; the GDC API is mocked with a
                        real recorded response)
docs/                   User guide, roadmap, strategy and literature documents
assets/theme.css        App stylesheet, loaded via marimo.App(css_file=...)
image/                  Logo and app-bar icon (README assets + Marimo-icon.svg)
data/                   Downloaded TCGA slides + catalog + hescope.db (gitignored)
agent_out/              Agent artifacts: patch PNGs + roi_history.jsonl (gitignored)
```

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `error: could not launch marimo: [Errno 2] No such file or directory` | The virtual environment is not activated, so `marimo` is not on `PATH`. Activate it and retry, or run `marimo edit app.py --no-token` directly. |
| The agent cannot connect to the notebook | Check three things: ① started with `--no-token`; ② the browser tab is still open; ③ the cells have run — marimo 0.23 loads lazily and the globals do not exist until they do. |
| `get_current_selection()` returns `NO_SELECTION` | Nothing has been dragged on the figure yet, or the cells have not run. Live selections come from `get_current_selection()`; submitted history from `get_latest_selection()`. |
| `.svs` / `.ndpi` will not open | Install `openslide-bin openslide-python`. Without it the tifffile/zarr fallback takes over, which covers fewer proprietary formats. |
| `train_from_annotations` raises `ValueError` | Not enough labeled data: you need at least 2 distinct labels with at least 2 patches each, and a reachable database. |
| Heatmaps or training complain about missing sklearn | `uv pip install scikit-learn joblib` (no torch required). |
| TCGA search returns nothing or times out | GDC open data needs no token but does need internet access. Tune concurrency with `HESCOPE_DL_WORKERS` (1–16, default 8); set it to 1 to fall back to a single stream. |
| Switching databases | Set `HESCOPE_DB_URL` (e.g. `postgresql://user:pass@host:5432/hescope`), or run `hescope --db <URL> init`. |

## License

The project itself is released under the **MIT License** (see [LICENSE](LICENSE)).

**Pathology foundation-model weights carry their own separate licenses** and are
not covered by this project's MIT license. The `hescope.embeddings` registry
enforces a license red line: only commercially usable, non-gated encoders are
eligible to be the default (currently GPFM, MIT). CC-BY-NC-ND models such as
UNI2-h are registered for academic comparison only and **never enter the default
path**. H-optimus-0 (Apache-2.0) is the commercially usable alternative, and
ResNet18 (ImageNet) is the license-free local fallback. Comply with each set of
terms before using any FM weights.
