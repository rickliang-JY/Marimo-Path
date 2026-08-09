# HE-Scope strategic roadmap archive

**English** · [简体中文](ROADMAP.zh-CN.md)

> Archived: 2026-08-08
> Status: a summary of strategy-discussion conclusions; work has not started.
> The next step is to review existing capabilities as a whole, then decide which
> item to begin with.

---

## 1. Background: where HE-Scope stands

A marimo H&E whole-slide image viewing platform
(`/mnt/agents/output/project`, master @ b802f24, 208 tests green):

- A unified plotly viewer (zoom / pan / selection in one) plus a sidebar and a
  single navigator thumbnail.
- The ROI loop: selection → level-0 coordinate mapping → patch re-cropped from
  the source image (not a screenshot) → DB persistence → agent payload.
- The agent bridge (marimo-pair, requires `marimo edit` mode):
  `get_current_selection()` (zero-click), `get_latest_selection()`,
  `get_analysis_capabilities()`.
- TCGA/GDC access: search, parallel chunked download (resumable, md5 verified),
  catalog.
- The analysis stack (informed by slideflow): Macenko/Reinhard stain
  normalization, nuclei detection, QC, 56-dimension features, optional ResNet18
  embedding, heatmaps, weakly-supervised LogisticRegression training.
- DB: SQLAlchemy (slides / rois / agent_runs), degrading to a DB-free mode.

---

## 2. Core strategic judgment

**Do not compete on static foundation-model leaderboards.** The 2025–2026
pathology foundation-model boards (CONCH, Virchow2, Prov-GigaPath, UNI2-h,
H-optimus-1 and others) are dominated by models trained on 100k–3.1M slides with
0.6–1.1B parameters. The deciding factors are data and compute; we have no
ticket to that game and should not build our own contender.

**HE-Scope's moat is the human–agent–database loop**: a human circles an ROI →
the agent reads the coordinates and the source-image patch → analysis is written
back to the DB → annotations become training data. That loop is an asset no
static benchmark can measure.

**Borrow rather than build.** CONCH, UNI and similar models publish tile
embeddings on HuggingFace. Wiring them in lifts the perception side from
ResNet18 to near-SOTA at zero training cost.

---

## 3. Three-phase route

### Phase A — perception upgrade (best value; recommended first)

- Adopt UNI or CONCH tile embeddings as a backend, replacing or coexisting with
  the current ResNet18.
- The existing 56-dimension feature and LogisticRegression training pipeline and
  the heatmap pipeline are reused wholesale; only the feature extractor changes.
- Expected: few-shot annotation training moves from "demonstrable" to the
  linear-probe AUROC 0.95+ tier.
- **GPU source: molab (see §5). No local GPU is no longer a blocker.**

### Phase B — formalize the agent loop + LoopX integration (see §4)

- Complete the tool set: the agent can write annotations back, trigger training,
  query DB history and request human review.
- Every interaction (human selects → agent analyzes → human corrects → retrain)
  lands as a trajectory in the database — a complete active-learning log.
- Define 3–5 standard task schemas (e.g. "find tumor regions on TCGA-BRCA and
  produce a density heatmap", "request human review when ROI classification
  confidence falls below a threshold").
- Model the annotate → train → evaluate → re-annotate cycle as a LoopX
  objective.

### Phase C — compete on a benchmark we can win

Not the static boards (a guaranteed loss) but interactive / agentic evaluation,
which is open ground:

- **Annotation-efficiency curves**: human clicks required to reach a given
  AUROC, with loop versus without. This is the standard active-learning metric,
  and the system produces the data naturally.
- **Agent task success rate**: on TCGA tasks that have ground truth, a
  three-way comparison of human+agent, agent alone, and model alone.
- Static boards are used only to demonstrate parity — run CONCH embeddings on
  standard tasks to show the perception side is not holding us back.
- Final deliverable: a systems paper on human–agent collaborative pathology
  analysis, plus a platform whose data flywheel keeps accumulating.

---

## 4. LoopX evaluation

Repository: https://github.com/huangruiteng/loopx

**What it is.** A lightweight state kernel and local-first control plane for
long-horizon agent work: durable objectives, gates, executable todos, an
evidence log and quota-aware auto-wake. Agents are headless (peer mode), keeping
state continuous across Codex / Claude Code / Cursor. It does not care how the
work gets done, only how the loop is governed.

**Mapping onto HE-Scope:**

| LoopX concept | HE-Scope counterpart |
|---|---|
| durable objective | A multi-day annotate-and-train campaign, e.g. "label 200 ROIs across 20 TCGA-BRCA slides and train to AUROC ≥ 0.9" |
| gates (human judgment) | Training is allowed only after a pathologist reviews the annotations; results that miss the bar do not advance |
| executable todos | Annotation batches, download batches, training, evaluation, retraining |
| evidence log | The DB's agent_runs + ROI annotation history + patch paths (half of this already exists) |
| quota-aware auto-wake | In active learning, only the N most uncertain patches wake a human or agent |
| peer agents, no master | Kimi Code / Claude Code / Codex taking turns advancing the same campaign |

**Positioning.** HE-Scope already has domain state (three tables) but lacks
campaign governance across sessions, agents and days — precisely the layer LoopX
supplies.

**Integration principles:**

- **Thin adapter, removable.** Core state stays in our own DB and LoopX acts
  only as the campaign control plane; evidence points at the DB's roi_id/run_id,
  and gates hang off the training pipeline's threshold checks.
- **Keep it out of the perception/analysis path.** LoopX understands nothing
  about pathology images. It improves the reviewability and throughput of
  long-horizon experiments; it raises no AUROC directly.
- **Risk.** An early single-author project of unknown maturity and maintenance
  continuity — run a spike for evidence before deciding.

**Spike plan** (not started): clone LoopX, get the quickstart working, model a
minimal campaign (two active-learning rounds over 10 ROIs on the demo slide) as
a LoopX objective, validate that the adapter layer is feasible, and produce an
evidence-based adopt/decline conclusion.

---

## 5. molab evaluation (GPU source)

Site: https://molab.marimo.io (marimo's official hosted cloud platform)

**Key facts:**

- Free; 4 CPU + 32 GB RAM by default.
- **An NVIDIA RTX Pro 6000 Blackwell GPU (96 GB VRAM) can be attached**, toggled
  from the notebook-specs button in the app header (CoreWeave-backed).
- torch and other ML packages are preinstalled; startup is near-instant.
- Limits: 12 hours maximum per session, shutdown after 90 minutes idle, limited
  persistent storage per notebook.
- GitHub integration, with GitHub as the source of truth.

**Pairing with a local agent is officially supported.** In a molab notebook,
actions (top right) → "Pair with an agent"; a local agent with the marimo-pair
skill installed (Claude Code / Codex / Kimi Code, ...) runs the connect command,
and from then on all code executes in the molab sandbox kernel with the same
experience as local pairing. Every HE-Scope agent interface works unchanged.

**Three adaptation pitfalls:**

1. Package structure: molab is single-notebook-centric, so the multi-file
   `hescope/` package needs to be pushed to GitHub and installed with
   `pip install git+https://...`, or handled through the GitHub mirror
   mechanism.
2. Storage: a single TCGA SVS is 100 MB–2 GB and molab's persistent storage is
   limited — use few large slides, go the GCS mirror route, or keep molab to the
   demo plus small samples.
3. The 12-hour cap: split long downloads and long training into resumable
   segments (the downloader already has `.part` checkpoint logic).

**Consequence:** the GPU blocker for Phase A is lifted. CONCH/UNI embedding
extraction and linear-probe training can run directly on molab's 96 GB GPU while
the agent stays local.

---

## 6. Next steps (night of 2026-08-08: the overnight research supersedes this section's original candidate list)

The overnight research swarm (6 parallel investigations; raw reports in
`/mnt/agents/output/research/`) produced two core documents. The candidate list
formerly in this section is void; these take precedence:

- **PAPERS.md** — literature review: the four main threads of pathology AI
  agents, human-AI collaboration and active learning, the evaluation landscape,
  and HE-Scope's positioning and novelty analysis (146 citations).
- **STRATEGY.md** — strategic decisions: the three academic goals (A: a
  human-in-the-loop variant of PathAgentBench; B: an "AUROC vs human interaction
  budget" annotation-efficiency protocol; C: eva + HEST parity as endorsement),
  the six-dimension open-source iteration route (P0–P2 priorities), the LoopX
  hybrid decision (not adopted as a dependency; build a thin DB-backed loop
  layer plus a `he-scope-loop` SKILL.md), and a table of 7 biweekly sprint
  milestones running to 2026-11-08.

**Core decisions at a glance:**

1. Academic positioning: "the first annotation-database-centric human–agent
   closed-loop WSI analysis system". The largest novelty threat is TissueLab, so
   an ablation contrasting session feedback written back to the database is
   mandatory.
2. FM selection: **GPFM (MIT) as the default**, UNI2-h for academic use only,
   H-optimus-0 (Apache) as the commercial alternative. CC-BY-NC-ND models (UNI,
   CONCH, TITAN and the like) hit a red line and never enter the default path;
   all of them are wired in through the encoder factory.
3. LoopX: not adopted as a dependency (file state versus DB creates two
   conflicting sources of truth; at 2.5 months old, v0.4.x breaks quickly).
   Build a thin loop layer instead, and re-evaluate their pluggable-state-provider
   RFC every 6 weeks.
4. Open-source gap: a double opening at "marimo-native + agent-native", plus a
   "reproducible viewing" narrative nobody has claimed. Be symbiotic with
   Trident (.h5) and QuPath (GeoJSON) rather than competitive, and anchor
   citations with JOSS early.

**Items implemented overnight** are in `git log` (master) and in STRATEGY.md §4,
column W1–2.

---

## 7. Deferred (confirmed earlier)

- BigQuery cohort search (ISB-CGC).
- GCS mirror download / Google Cloud storage integration.
- Other marimo cloud/database combinations (to be explored together during the
  storage-optimization phase).
