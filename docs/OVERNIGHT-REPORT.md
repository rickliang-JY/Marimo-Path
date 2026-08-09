# HE-Scope overnight task report (night of 2026-08-08 → morning of 08-09)

**English** · [简体中文](OVERNIGHT-REPORT.zh-CN.md)

## Task recap

Three instructions from the user: ① survey the current state of pathology agent
papers; ② decide the next direction based on that literature (an academic
benchmark plus six-dimension open-source iteration); ③ decide whether to adopt
LoopX or build our own loop-engineering skill. The requirement was to produce an
overall plan first, then make changes according to it.

## Execution

- **Stage 1** — a six-way parallel research swarm (pathology agents / foundation
  models / benchmarks / human-AI collaboration / open-source ecosystem / a close
  reading of LoopX), 60+ effective searches plus close reading of the
  repository. Raw reports are in `/mnt/agents/output/research/r1~r6-*.md`.
- **Stage 2** — two synthesis documents: `PAPERS.md` (550 lines of literature
  review, 146 citations) and `STRATEGY.md` (304 lines of strategic decisions,
  including a milestone table).
- **Stage 3** — `ROADMAP.md` §6 updated with the final decision.
- **Stage 4** — two-way parallel implementation (isolated in worktrees), then
  merge.
- **Stage 5** — live validation against a real marimo-pair kernel, A–E all PASS;
  **246 tests green** (up from 208).

## Core research conclusions at a glance

1. **The field.** Pathology agents exploded across 2025–2026 (the PathChat
   family, SPARK, PathFinder and others). PathAgentBench carries a key negative
   result: pure agents locate evidence at mIoU < 0.09 with a 2% hit rate.
   "Finding the evidence" is the biggest weakness, and that is exactly where our
   human–agent loop enters.
2. **Novelty positioning.** "The first annotation-database-centric human–agent
   closed-loop WSI analysis system." The largest threat is TissueLab, so an
   ablation contrasting session feedback written back to the database is
   mandatory.
3. **Three academic goals.** A = a human-in-the-loop variant of PathAgentBench
   (a curve over k human-circled ROIs); B = an annotation-efficiency protocol,
   "AUROC vs human interaction budget", where we hold an exclusive data
   advantage; C = eva + HEST parity as an endorsement.
4. **FM selection.** GPFM (MIT) as default, UNI2-h for academic use only,
   H-optimus-0 (Apache) for commercial use; CC-BY-NC-ND is the red line.
5. **LoopX.** Not adopted as a dependency — it would create two conflicting
   sources of truth and it is too young. Instead: our own thin DB-backed loop
   layer plus a `he-scope-loop` SKILL.md, with a re-evaluation of their provider
   RFC tracked at six weeks.
6. **Open-source gap.** "marimo-native + agent-native" is unoccupied. The
   strategy is symbiosis with Trident (.h5) and QuPath (GeoJSON), with a JOSS
   paper to anchor citations.

## Changes already implemented (on master, visible in `git log`)

- **hescope/embeddings.py** — the FM encoder factory: a
  GPFM / UNI2-h / H-optimus-0 / resnet18 registry with license and gated
  metadata, the default red line (nc-nd never becomes the default), lazy loading
  (importing pulls in neither torch nor the network), and `embed_tiles`.
- **hescope/ml.py** — `HESCOPE_EMBEDDER` as an optional embedding backend across
  the whole path (training / heatmap / predict), automatic fallback to the
  56-dimension features with a warning on failure, and `ModelInfo` recording
  encoder and dim (backward compatible).
- **hescope/db.py** — the `interactions` trace table v1 (6 kinds, laying the
  groundwork for the data flywheel and automation-bias research) plus
  `InteractionRepo`.
- **Three new agent tools** (validated live): `annotate_roi` (write annotations
  back), `query_annotations` (query the annotation store) and
  `get_slide_info()`. All of them record interactions.
- **hescope/geojson.py** — QuPath-compatible GeoJSON export (classification
  mapped from label).
- **skills/he-scope/SKILL.md** — the repo-local agent skill (Trident pattern):
  pairing steps, schemas for the 6 tools, the read-selection → analyze →
  write-back → train workflow, and loop-mode guidance.
- AGENTS.md and the app.py tool documentation updated to match.

## Open decisions for the user (per the milestone table in STRATEGY.md)

1. **Whether to start W3–4**: real-hardware validation of GPFM loading (needs a
   GPU — via a molab spike, or wait for a local environment?) plus reproducing
   the k=0 baseline for academic goal A (needs confirmation that the
   PathAgentBench GitHub data has been released).
2. Scheduling the **molab link spike** (HE-Scope on molab paired with a local
   agent).
3. When to start the **thin loop layer** (campaigns/gates tables + CLI +
   `he-scope-loop` skill).
4. Whether to open-source on GitHub — a smooth `uvx` first run and pooch sample
   data are P0 prerequisites.

## Known limitations

- The real GPFM loading path has not been validated on real hardware (timm
  hf-hub; mock tests cover the code path).
- Designed behavior of the send tool: when the ROI list is non-empty it resends
  the last ROI, and the live-selection fallback only triggers on an empty list.
  This is documented behavior.
- Research note: survival prediction, TMB, segmentation as a main thrust and
  PathVQA have all been explicitly ruled out (rationale in STRATEGY.md §1).
