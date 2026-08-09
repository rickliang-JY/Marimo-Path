# HE-Scope strategy document (STRATEGY)

**English** · [简体中文](STRATEGY.zh-CN.md)

> Date: 2026-08-08 | Basis: six research reports (r1 pathology agents, r2
> foundation models, r3 benchmarks, r4 human-in-the-loop, r5 open-source
> ecosystem, r6 LoopX / loop engineering) plus HE-Scope's current state
> (ROADMAP §1–2, `../AGENTS.md`).
> This document states decisions, not a survey: every conclusion carries an
> explicit position, a rationale and its exclusions. All cited figures come from
> the primary sources attached to the research files.

---

## 0. The strategy in one sentence

**HE-Scope does not compete on static foundation-model leaderboards and does not
build a general pathology agent. It defines — and ships the first open-source
reference implementation of — the loop category "human circles an ROI, agent
analyzes, the annotation database accumulates, weak supervision retrains".**
Academically, it rewrites two evaluation protocols along the one dimension
others cannot copy, human–agent interaction (a PathAgentBench T3 variant, and an
AUROC-vs-interaction-budget curve). In engineering terms, it claims the vacant
"agent-native pathology viewer" position. For loop engineering, it builds its own
thin layer and does not adopt LoopX.

Rationale (research support):

- PathAgentBench (2026-07) shows the field-wide bottleneck is **evidence
  acquisition**, not reasoning: the strongest text-guided localization reaches
  mIoU < 0.09, is beaten 3–4× by a "center point of the parent box" heuristic,
  and 40× autonomous exploration hits only 2.0%. "A human circles the ROI and
  fills the planner's gap" is precisely HE-Scope's native interaction (r1 §20,
  r3 §6.1).
- SPARK (Nature Medicine 2026) shows "agents writing executable code that calls
  classical analyses" beats "end-to-end VLM reasoning" — the strongest
  endorsement of the code-agent bridge route (r1 §13).
- r4's systematic search concludes that integrating the whole chain — agent
  analysis + human selection + structured persistence + retraining — is still a
  defensible gap as of 2026-08. The one closely overlapping competitor,
  TissueLab, keeps feedback at session level rather than database-centric
  (r4 §7).
- r5 confirms that the "marimo-native × agent-native" double is unoccupied in
  both ecosystems, and that Trident shipping a SKILL.md inside its repository
  validates the agent-native distribution form at very low cost (r5 §5–6).

**Why now (the window):**

1. **Evaluation window.** PathAgentBench was released 2026-07; agentic pathology
   evaluation is "just beginning, with very low top scores and a standardization
   window opening" (r3 §6.1). Within six months someone will define a HITL
   variant, and whoever defines it first collects the citations.
2. **Competitor window.** TissueLab has not yet appeared in a high-tier journal
   and has not open-sourced its continual-learning module; r4 §7.4 explicitly
   recommends "turning the database loop + selection protocol + benchmark into
   differentiating evidence they cannot cover, as soon as possible".
3. **Ecosystem window.** Trident is currently the only pathology project
   shipping an agent skill in-repo, and only at the CLI layer; "an agent driving
   the viewer directly" is unclaimed (r5 §7.2), and moving first costs on the
   order of one person-day.
4. **Counter-discipline.** A window is not permission to sprawl. The exclusions
   at the end of §1 and the "do not adopt LoopX" decision in §3 are equally
   window-period decisions: concentrate all firepower on the three goals and the
   six-dimension P0/P1 items.

**Relationship to existing documents.** This document supersedes two conclusions
in ROADMAP §3–4: ① ROADMAP Phase B's "model the annotate → train cycle as a
LoopX objective" and §4's LoopX spike plan are replaced, after r6's close
reading, by §3's own thin loop layer (the spike is unnecessary; the evidence
closed during research); ② ROADMAP Phase A's "adopt UNI or CONCH" is corrected
by §2f to "GPFM as default + UNI2-h for academic comparison only", because of the
license red line (UNI and CONCH are both CC-BY-NC-ND and unusable for platform
distribution). ROADMAP's Phase C (compete on an interactive benchmark) and the
molab GPU conclusion remain valid and have been folded into §1's three goals and
§4's resource assumptions respectively.

---

## 1. Academic goal decisions

A three-goal framework: **A (primary, maximum differentiation), B (primary,
mechanism consolidation), C (follow-on endorsement)**. A carries the impact of
"new protocol + order-of-magnitude improvement", B turns the loop mechanism into
a citable standardized contribution, and C proves the perception side is not
holding us back. All three share one technical stack (FM embedding + ABMIL +
interaction logs) and reuse each other's experimental infrastructure.

**Paper-portfolio strategy.** A and B eventually converge into one main systems
paper (the human-initiated, agent-analyzed, database-accumulated,
weakly-supervised-retrained loop, in the mold of nuclei.io's Nat Biomed Eng
form, r4 §7.4). In publication order, though, A's protocol short paper goes
first: it costs least (1–2 person-months), the data already exists, and it can
be written without a large human study — claiming definitional rights over the
"HITL evidence acquisition protocol" early. B's full curve, ablations and
crossover user study then form the body of the main systems paper. C is never
written up separately; it serves as a baseline section and engineering
endorsement.

### Goal A: a human-in-the-loop variant of PathAgentBench — localization/diagnosis curves under k human-circled ROIs

**Position: primary; make it the first paper.**

- **Hypothesis.** Pure VLM agents break down systematically on evidence
  acquisition (T3: localization mIoU < 0.09, 40× hit rate 2.0%, a three-stage
  decay of 52.2% → 18.5% → 2.0%; pathology-specific models cannot even emit a
  valid bbox tool call). Allowing k human ROI interactions (k ∈ {0,1,3,5}),
  "human circles an ROI → agent reads level-0 coordinates and the source-image
  patch → analysis written back" should close the acquisition gap on a tiny
  human budget: we expect k=1 alone to lift the 40× evidence hit rate from 2%
  to near the coverage of expert diagnostic paths, with joint T1+T4 diagnostic
  accuracy rising monotonically in k and saturating quickly.
- **Experimental design.**
  1. Reuse PathAgentBench's public 1,822 TCGA WSIs and 17,135 pathologist
     diagnostic paths (2.5× → 10× → 40× three-level nested bboxes); get the
     protocol working on its 50-slide Mode A subset first, then extend.
  2. Define the HITL variant protocol: each round the agent may request one
     human ROI selection (budget k), and the human input directly replaces or
     guides the agent's next-level localization. The x-axis is k; the y-axis is
     T3 localization mIoU / hit rate plus joint diagnostic accuracy on
     downstream T1 (evidence interpretation; strongest baseline Gemini-3-Flash
     63.5%, experts 93.6%) and T4 (evidence integration; strongest ~93%, near
     saturation).
  3. Baselines: k=0 (pure agent, reproducing the official negative result), the
     centering heuristic (IoU 0.25–0.28), and an oracle (expert path given
     directly) — three reference lines.
  4. Human selection can first be scaled out by "replaying simulations from
     expert paths", with a small real-human validation added afterwards.
- **Resources required.** TCGA is already integrated, so no new annotation is
  needed; VLM calls cost roughly $0.2–0.3 per slide (official Mode B figures);
  **1–2 person-months**; compute is CPU plus API calls, and the molab GPU is not
  required.
- **Milestones.** M1 data/protocol working + k=0 baseline reproduced (week 3);
  M2 full simulated-human k curve + statistics (week 6); M3 small real-human
  validation + write-up (week 10).
- **Risks and mitigations.**
  - *Risk 1*: the benchmark is very new (2026-07) and the GitHub data may not be
    fully released. Mitigation: run a data-availability check in week 1; if it
    is missing, fall back to "build a 200-slide subset on TCGA under their
    protocol, annotated by 2 annotators following the three-level bbox spec",
    and reframe the paper as "protocol replication + HITL extension".
  - *Risk 2*: simulated human selection is challenged as unrealistic.
    Mitigation: a real-human sub-experiment (6–8 readers, crossover design, the
    r4 §6.2 paradigm) as a robustness section; the protocol code and simulator
    are fully open-sourced so others can reproduce it.
  - *Risk 3*: pure-agent groups catch up quickly. Mitigation: they lack the
    interaction infrastructure and logging pipeline, making replication
    expensive; we move first and open-source the protocol to hold definitional
    rights.
- **Expected output.** A conference/journal short paper (benchmark/protocol
  type), targeting a MICCAI 2027 workshop → main track, or a brief communication
  in npj Digital Medicine / Nature Communications. Core claim: "the evidence
  acquisition bottleneck can be solved with single-digit human interactions, so
  the fully autonomous agent route need not be chased in the short term."
- **Evaluation discipline (written into the protocol).** ① Across all k curves,
  the human input supplies coordinates only, never diagnostic conclusions —
  otherwise the contributions of acquisition and reasoning are confounded.
  ② Report the cost side (human seconds per slide plus agent token cost),
  answering PathAgentBench's criticism that unified trajectory/cost/safety
  metrics are missing (r1 §21). ③ Failure analysis gets its own section: grading
  errors and missed small lesions are SlideSeek's two known disaster areas (45%
  of errors are grading errors, r1 §2), so our HITL curves must decompose along
  those two classes and show which failure type human selection actually helps.

### Goal B: an "AUROC vs human interaction budget" annotation-efficiency protocol — three tasks across NSCLC/BRCA subtyping and LUAD mutation

**Position: primary, in parallel with A; the standardized consolidation of the
loop mechanism, with the highest long-term citation potential.**

- **Hypothesis.** On three standard tasks, HE-Scope's loop — agent picks
  high-information slides/ROIs → human confirms or circles → written back to the
  DB → retrain — reaches a given AUROC at 2–4× lower budget than a CLAM-style
  random subsampling curve on a unified "human interaction budget" x-axis (slide
  count, ROI count, minutes), and is no worse than uncertainty/coreset active
  learning baselines.
- **Experimental design.**
  1. Task set: TCGA-NSCLC subtyping (LUAD vs LUSC, CLAM baseline AUC
     0.956 ± 0.020), TCGA-BRCA subtyping (IDC vs ILC, ABMIL balanced accuracy
     80.5 / DSMIL 84.7), and TCGA-LUAD mutation (EGFR/STK11/KRAS, Coudray
     held-out AUC 0.733–0.856; FM + simple MIL reaches only macro-AUROC 0.626
     across five LUAD genes, so real headroom exists).
  2. Protocol: FM embedding (GPFM/UNI2-h) + ABMIL, 10-fold Monte Carlo CV (the
     CLAM protocol); three curves on one plot — CLAM data efficiency
     (100/75/50/25/10%), STAMP scarce data (n=75/150/300), and the HE-Scope loop
     curve (x-axis converted to a unified interaction budget).
  3. Baselines: random / uncertainty / coreset AL sampling; plus a loop ablation
     using session-level feedback with no DB accumulation, as an explicit
     contrast with the TissueLab mode.
  4. Additionally report an NoC@90-style metric for the segmentation subtask
     (ported from the NuClick/Clore protocol).
- **Resources required.** All public data plus a single GPU (FM features + ABMIL
  are light, and molab's 96 GB is plenty); a small user study with 2–3
  annotators (internal staff can stand in for pathologists on ROI selection);
  **2–3 person-months**.
- **Milestones.** M1 CLAM/STAMP baseline curves reproduced on all three tasks
  (week 4); M2 loop pipeline connected + pilot experiment (week 8); M3 full
  curves + ablations + automation-bias measurement (week 12, the paper's body).
- **Risks and mitigations.**
  - *Risk 1*: the LUAD mutation task has a weak signal (the 0.63 tier), so curve
    differences may not be significant. Mitigation: position it as a stratified
    analysis showing "interaction budget is worth more on hard tasks", and rest
    the main conclusion on the two clean NSCLC/BRCA curves.
  - *Risk 2*: reviewers object that "annotation efficiency" has no agreed
    protocol and we are talking to ourselves. Mitigation: proactively cite the
    Label-Efficient MIA survey (arXiv:2303.12484) and its public call for
    standardized protocols, alongside the same-frequency context of SHAL
    (2026-07, Dice ≥ 0.80 at 26% budget versus 37% for the baseline), turning
    "no standard" into a "first definer" narrative.
  - *Risk 3*: TissueLab's formal publication narrows the window. Mitigation: the
    database-accumulation ablation and the selection-budget protocol are
    differences their architecture cannot cover, and must be nailed down in the
    first paper.
- **Expected output.** The core chapter of the main systems paper (the Nature
  BME / MIA / Nat Commun systems-paper route), or a standalone benchmark/protocol
  paper (the highest-citation angle); plus an open-source evaluation package for
  the "interaction budget protocol".

### Goal C: eva + HEST subset parity endorsement (follow-on, not primary)

**Position: purely an engineering endorsement; no pursuit of the top spot.**

- **Hypothesis.** Once HE-Scope adopts FM embeddings, it reaches parity (±2%)
  with officially reported values on standard downstream tasks, proving the
  perception side is not holding us back.
- **Experimental design.** eva's four patch-level tasks (BACH/CRC/MHIST/PCam,
  linear-probe balanced accuracy) plus 2–3 of HEST's 9 tasks (patch → gene
  expression PCC; keep only 2 if spatial-transcriptomics data integration proves
  expensive). Sanity-check against the public leaderboard numbers in r2 §9 (for
  example GPFM's rank 1.6 over 72 tasks, and the EVA board's Virchow2 0.794 /
  UNI 0.783).
- **Resources required.** **About 1 person-month**, pure engineering; compute on
  molab.
- **Milestones.** A parity report within 2 weeks of the encoder factory skeleton
  being finished (weeks 6–8).
- **Risks and mitigations.** Missing parity usually comes from inconsistent
  preprocessing (20x/224px/normalization conventions, r2 §11 risk 4) — use
  TRIDENT's preprocessing pipeline directly, or align to its transform, to
  eliminate silent losses.
- **Expected output.** No standalone paper; a baseline section for the A/B
  papers and credibility material for the platform README.

### Explicit exclusions and why

| Excluded | Rationale (research basis) |
|---|---|
| **Survival prediction (c-index) as a main thrust** | Splits differ across papers and are not comparable; pathology-only MIL typically reaches c-index 0.60–0.70, and STAMP's 7 prognostic tasks average at best AUROC 0.63 — the weakest FM dimension. The multimodal route needs genomics data engineering, and the top of that field is occupied by the Mahmood group (r3 §1.3). |
| **TMB prediction** | No unified protocol; internal AUC spans 0.64–0.99 and external validation generally drops 0.10–0.15, so the numbers are not comparable and no credible claim can be made (r3 §1.2). |
| **Segmentation/detection as a main thrust (MoNuSAC/PanNuke/CoNSeP)** | Heavy pixel annotation on an incremental score-chasing track, weakly related to the human–agent ROI loop's endowment. Use NuClick/PathoSAM only as tool components, with eva providing incidental coverage (r3 §4). |
| PathVQA / closed-form patch VQA | Already pushed to ~95%, and a text-prior audit (44–53% without looking at the image) has destroyed its credibility (r3 §3). |
| Chasing the PathBench (Ma 2025) leaderboard | Private data plus a private leaderboard: you can chase the board but cannot do methodological innovation (r3 §2.1). |
| Building our own pathology FM to chase the board | Gaps between top models are under 2%, within noise; the deciding factors are 100k–3.1M slides of data and compute, and we have no ticket (ROADMAP §2, r2 §0/§9). |

---

## 2. Open-source iteration roadmap (six dimensions)

Guiding principle (the adoption-funnel regularities distilled in r5 §6):
**installation succeeds in one step, first-run cost trends to zero, the model zoo
has one entry point, the extension API is layered, paper citations are bound in,
engineering quality is trust, licenses are tiered, and we live symbiotically with
the existing ecosystem.** The pathology open-source star ceiling is around 1.7k
(CLAM) — a small, deep community. The goal is not traffic but becoming the
category default. Each dimension below is given as current state → gap →
iteration items (P0/P1/P2 with rough effort).

**Quantified adoption-funnel references** (measured in r5 §0–3, our benchmark
line for 12 months out): QuPath at 1,413 stars, 400k downloads and 5,000
citations (ten years of accumulation); TRIDENT at 614 stars in 18 months (growth
far outpacing its predecessors, proving the growth slope of new tools in the FM
era); TIAToolbox at 110k+ PyPI downloads (the conversion power of a permissive
license plus Colab examples); Slideflow 3.0 moving from GPL to Apache-2.0 and
splitting into three license sub-packages. Our north-star metric is not stars but
**the number of analyses completed with HE-Scope that external papers cite** —
which requires P0's first-run experience, sample data and SKILL.md to come before
anything fancy.

### a. Code-agent interaction

**Current state.** The marimo-pair bridge (requires `marimo edit` mode) and
three read-only tools — `get_current_selection()` (zero-click live selection),
`get_latest_selection()` and `get_analysis_capabilities()`. Write-back exists
only as two code-level APIs, `db.run_repo.record()` and
`db.roi_repo.update_annotation()` (`../AGENTS.md` §3/§6). Interaction history
lands in `agent_out/roi_history.jsonl` plus the DB's agent_runs.

**Gap.** The agent can only look, not write — it cannot write annotations back,
trigger training, or request human review. AGENTS.md is a contract document for
humans, not standard SKILL.md format. Interaction traces have no unified
persistence schema, which cannot support the curve statistics of goals A and B.

**Research implications.** ① Trident ships
`.claude/skills/trident/SKILL.md` in-repo, baking encoder↔resolution pairing,
directory structure and common pitfalls into the skill, and advertises
out-of-the-box agent driving right in the README — a top lab endorsing
agent-native (r5 §3.3). ② SPARK proves "agents writing executable analysis code"
is a Nature Medicine-tier paradigm (r1 §13). ③ Pathology-CoT proves viewer
behavior logs are a gold mine (6× annotation speedup, external validation recall
97.6), and a viewing platform naturally owns that data entry point (r1 §8).

**Iteration items:**

| Priority | Item | Effort |
|---|---|---|
| **P0** | **Agent annotation write-back tools**: two kernel tool functions, `submit_annotation(roi_id, label, notes, confidence)` and `request_human_review(roi_id, question)`, wrapping the existing roi_repo/run_repo and making human confirmation a precondition for an annotation taking effect (an anti-automation-bias structure, r4 §2.4) | 2–3 days |
| **P0** | **A repo-local `.claude/skills/he-scope/SKILL.md`**: distilled from AGENTS.md into standard Agent Skills format (frontmatter name/description + under 500 lines of body + progressive disclosure), covering startup, the lazy-kernel pitfall, the three tools, write-back and GeoJSON | 1 day |
| **P0** | **Interaction trace persistence format v1**: an `interactions` table (the full chain of human selects → agent analyzes → human confirms/corrects → retrain, with k interaction counts and timestamps), serving the budget statistics of goals A and B directly | 2–3 days |
| P1 | Agent training/evaluation trigger tools (`trigger_training(task_schema)`, gated on human confirmation) | 3–5 days |
| P1 | Tool/modality legality guardrails: an analysis tool allowlist plus payload validation for "no claiming IHC or molecular results from H&E" (the lesson of SlideSeek's hallucinated IHC failure, r1 §2) | 2 days |
| P2 | An MCP server (a thin FastMCP wrapper over the kernel tools, the LoopX 4-tool pattern, r6 §1.3) so non-marimo-pair runtimes can connect | 3–5 days |

**Interaction trace schema essentials** (this is the data foundation for goals A
and B and must be right in v1): every event records `event_kind` (human_roi /
agent_analysis / human_confirm / human_correct / retrain_trigger), `slide_id`,
`roi_id`, `run_id`, `wall_clock_seconds` (time spent on the human side, the
"minutes" x-axis of the budget curve) and `campaign_id` (nullable). Human
correction events must store **both** the agent's original output and the
human-corrected value — that pair is the automation-bias measurement (adoption
and correction rates for wrong agent output, an in-built measurement of
PulmoFoundation's 77.5% warning figure, the r4 §6.2 red line) and the raw
material for Pathology-CoT-style behavior data. Traces export as one-event-per-line
JSONL (kept mappable to Pathology-CoT's "behavioral command + bounding box"
format) so dataset contributions can be published independently of the platform.

### b. Front-end interaction

**Current state.** A unified plotly viewer (zoom/pan/selection in one) plus a
sidebar and a single navigator thumbnail; the ROI selection → level-0
coordinates → patch re-cropped from the source chain works.

**Gap** (against QuPath/Slideflow/HALO): no management panel for annotation
objects (list/edit/delete/class coloring); no interactive exploration of
analysis-result overlays (heatmaps, embedding cluster mosaics); no "save analysis
settings and reuse them on a new slide" recipe model (the user pass mark set by
HALO/QuPath, r5 §4); no multi-ROI comparison view.

**Our differentiation** (not competing head-on with QuPath): "viewing as
documentation" — a viewing session is natively a reproducible, git-managed,
uvx-replayable Python file (reproducible viewing; QuPath project files have no
such portability, r5 §7.2); and "embedding-aware viewing" — brush a region on the
slide and see embeddings/attention/clusters live (the interaction layer missing
after Trident/CLAM output; Slideflow Studio has a mosaic but is not
agent-driven).

**Iteration items:**

| Priority | Item | Effort |
|---|---|---|
| **P0** | **GeoJSON export** (ROI annotations → GeoJSON, openable and editable in QuPath and writable back; the symbiosis route Trident validated, r5 §3.3/§7.1) | 1–2 days |
| P1 | Annotation list panel (driven by the rois table: filter, color, jump-to, delete) | 3–5 days |
| P1 | Result overlay v1: render a trained classifier's heatmap plus confidence into the viewer (reusing the existing heatmap pipeline) | 5–8 days |
| P2 | Recipe / analysis-setting save and reuse (task schema serialized into a shareable notebook fragment) | 5 days |
| P2 | Embedding brush exploration (brush a region → UMAP highlight / nearest-neighbor retrieval) | 8–10 days |

### c. Database integration

**Current state.** SQLAlchemy + SQLite, three tables (slides/rois/agent_runs),
degrading to a DB-free mode.

**Gap.** Single-machine SQLite cannot support concurrent claims from multiple
agents (the loop layer needs row-lock / optimistic-lock semantics); there is no
cloud or collaboration path; and there is no integration with the Trident feature
ecosystem (.h5 features carry coords and are already a de facto community
format).

**Evolution path** (do not jump straight to the cloud; three tiers):

| Priority | Item | Effort |
|---|---|---|
| **P0** | Data-model extension: the interactions table plus loop fields added to agent_runs (see §3), all written through the SQLAlchemy abstraction layer and dialect-agnostic | Included in a/§3 |
| P1 | **Trident .h5 feature importer**: treat Trident as the upstream pipeline (tissue segmentation / tiling / embedding) and keep HE-Scope as the interactive viewing layer rather than rebuilding preprocessing (the r5 §7.1 shortcut) | 3–5 days |
| P1 | Validate the SQLite → PostgreSQL switch path (required once loop-layer claims use `SELECT ... FOR UPDATE` semantics; guarantee dialect compatibility first without pushing deployment) | 2–3 days |
| P2 | Evaluate a cloud collaboration form: a molab-hosted demo with GitHub as source of truth. True multi-user collaboration (worklist / case level) is commercial-moat territory, and open source should not attack it head-on (the Aiforia lesson, r5 §4) | 2 days of research + depends on findings |

### d. UI design

**Current state.** A single-notebook marimo app, hide_code cells, app view mode
available, and demo slide auto-generation (`ensure_demo_slide()`).

**Gaps and adoption-funnel countermeasures** (aligned item by item with the r5 §6
regularities):

| Priority | Item | Effort |
|---|---|---|
| **P0** | First-run experience: one command, `uvx marimo edit --sandbox`, working end to end (PEP 723 inline dependencies completed) plus a 30-second GIF at the top of the README and a three-line quickstart | 1–2 days |
| **P0** | Automatic sample-data download (pooch fetching 1–2 TCGA/OpenSlide samples, the histolab pattern — the key to making first-run cost trend to zero) | 1–2 days |
| P1 | An example notebook sequence (examples/: load slide → select → annotate → train → agent session), hosted on molab for zero-install online trial (a marimo version of the TIAToolbox Colab benchmark) | 3–5 days |
| P1 | A default interface understandable in 30 seconds: slide, selection and one clickable "let the agent analyze" button on open, with every advanced panel collapsed | 2–3 days |
| P2 | An mkdocs documentation site plus docstring-generated API docs (the Slideflow/Sphinx pattern; engineering quality is trust) | 3–5 days |

### e. Special AI features (analysis capabilities an agent can trigger)

**Current state.** Macenko/Reinhard stain normalization, nuclei detection, QC,
56 hand-crafted features, optional ResNet18 embedding, heatmaps, and
weakly-supervised LogisticRegression training (ROADMAP §1).

**Gap** (against the SPARK/PathChat capability surface): SPARK's paradigm is an
agent autonomously turning a concept like "lymphocyte density within 800 μm of
the tumor" into an executable analysis tool — our stack stops at single-ROI
statistics with no spatial/morphometric capability; and PathChat-style
"morphological description / differential diagnosis Q&A" capability is zero
(delegated to an external VLM).

**Iteration items** (add only capabilities inside the code-agent bridge
paradigm; do not chase end-to-end VLMs):

| Priority | Item | Effort |
|---|---|---|
| **P0** | **FM encoder factory skeleton + mock tests** (see f; a unified `get_embedding(patch, model="gpfm")` interface, with a mock encoder keeping GPU-free CI green) | 3–5 days |
| P1 | Similar-ROI retrieval (embedding nearest neighbors: "the human circles one example → find similar regions" = retrieval-based active learning, the Menon paradigm, r4 §1.2) | 3–5 days |
| P1 | A spatial morphometrics toolkit: distance/density/boundary metrics built on the existing nuclei detection and ROI coordinate system (the minimal kernel of SPARK's concept library) | 5–8 days |
| P1 | Interactive segmentation backend: PathoSAM/NuClick-style "selection → mask", using the selection simultaneously as a segmentation prompt and an annotation signal (a natural combination supported by the r4 §3 literature) | 5–8 days |
| P2 | Training-free surprise-guided scanning (the PathNavigate pattern: frozen features plus a low-magnification anomaly field, giving the agent low-cost "where to look next" navigation, r1 §16) | 8–10 days |
| P2 | A VLM captioner plug-in interface (users bring their own PathChat/API model for ROI morphological description; the platform binds no model, leaving a hook for PathoSage-style tool-reliability tracking) | 3–5 days |

### f. Pathology foundation-model integration

**Position (adopting r2 §11's conclusion and executing it verbatim):**

- **Product default path: GPFM (`majiabo/GPFM`, MIT).** ViT-L/14, 307M, 1024
  dimensions, batch inference on a single 8 GB card; average rank 1.6 across a
  72-task benchmark (first on 42, versus UNI's 3.7 and 6); distilled from three
  teachers, UNI + Phikon + CONCH. **MIT means no commercial risk and no
  application process** — the only safe first choice for platform distribution.
- **Academic comparison path: UNI2-h (`MahmoodLab/UNI2-h`, CC-BY-NC-ND).** The
  most mature ecosystem (native in TRIDENT), first tier on survival tasks, 1536
  dimensions; for academic experiments and paper comparison only, with
  moderately high 681M inference cost.
- **Commercial high-performance alternative: H-optimus-0 (Apache 2.0).** First
  on both detection and biomarkers in an independent clinical benchmark
  (Campanella 2025), "the strongest commercially usable weights"; the cost is
  1.1B parameters, 4.6 GB VRAM and 75 tiles/s, suitable only for server-side
  batch feature extraction.
- **Second tier, for reference:** Midnight-12k (MIT) / OpenMidnight
  (Apache-2.0, highest public-reproduction average at 0.775) / Hibou-L
  (Apache-2.0, a lightweight commercially usable option performing ≈ UNI; note
  the transformers 4.x dependency). For the slide-level upgrade path, keep TITAN
  (non-commercial) and GigaPath-Flash (Apache-2.0, 22M+21M, slide aggregation
  runnable on CPU) in reserve, transitioning first via tile embedding with
  mean/ABMIL aggregation (MADELEINE shows the MEAN baseline is already strong).

**Integration engineering decision.** Do not build a complete zoo. Use a hybrid:
**our own thin encoder factory plus optional TRIDENT upstream.** The factory
exposes only two interfaces, `load_encoder(name) -> (model, transform, dim)` and
`embed_patch`/`embed_roi`, with a model registry carrying a license field.
TRIDENT is supported as upstream feature preprocessing (import its .h5) but is
not a runtime dependency — its custom non-commercial license conflicts with our
Apache-2.0 core. Dependency fragmentation risk (timm pinned at 0.9.16,
transformers 4.x/5.x, flash-attn) is mitigated with separate extras profiles
(`.[fm-gpfm]` and friends) plus pinned CI versions (the r2 §10 risk table).

**License red line (a hard rule, written into CI checks):**

1. **CC-BY-NC-ND models (UNI2-h, CONCHv1.5, TITAN, H-optimus-1, MUSK, H0-mini)
   do not enter the default path, do not become PyPI dependencies, and do not
   appear as default parameters in example notebooks.** They are permitted only
   as opt-in academic comparisons, with the non-commercial restriction
   prominently documented.
2. The default, example and CI paths permit only GPFM (MIT), H-optimus-0
   (Apache-2.0), Midnight/OpenMidnight (MIT/Apache-2.0), Hibou (Apache-2.0) and
   GigaPath-Flash (Apache-2.0).
3. The model registry records license and gated status per entry, and the README
   carries a license section (Slideflow's three-package split reserves the mental
   model for a future split, r5 §2).

**Two execution details.** ① Magnification and preprocessing consistency is the
biggest source of silent score loss — each model has standard input conventions
(20x, 224/256px, specific normalization; Midnight uses mean/std = 0.5), so the
encoder factory's transform must align with the official one, with goal C's
parity numbers as the regression threshold. ② Beware benchmark contamination:
the Virchow family's training data (MSKCC) overlaps some public evaluation
cohorts, so selection judgments always rest on independent third-party
evaluations (Campanella 2025, EVA, PathBench) and never on vendor self-reported
numbers (r2 §9/§11 risk 5).

---

## 3. Loop engineering decision

### 3.1 Conclusion: do not adopt LoopX as a dependency; build a thin loop layer on our DB (r6 §3.3 route C, adopted)

**Four reasons not to adopt LoopX** (evidence in r6 §1.4–1.5, §3.1):

1. **Two conflicting sources of truth.** LoopX state is pure files (`.loopx/` +
   `.codex/goals/` + `~/.codex/loopx/`), and as of v0.4.2 the
   pluggable-state-provider is only "RFC accepted, contract evidence, not a
   shipped runtime migration" — the kernel cannot be pointed at our SQLAlchemy
   DB, so integration inevitably means dual writes and drift.
2. **Maturity risk.** Created only ~2.3 months ago (2026-05-31), at v0.4.2, with
   three breaking minors in two weeks, substantially led by one person (20
   contributors but huangruiteng overwhelmingly dominant), and 316k lines of
   agent-generated-style code. As a dependency, the supply-chain and maintenance
   risk is unacceptable.
3. **No domain semantics.** Goals and todos are designed around coding tasks;
   pathology gates (ROI annotation review, model promotion) can only be stuffed
   into free-text user_gate, and gate type and quota semantics cannot be modeled
   natively.
4. **Low problem overlap.** The "cross-runtime control plane" problem LoopX
   solves is mostly already solved in HE-Scope by marimo plus the DB, and its
   dashboard is a local read-only loopback that does not integrate with marimo.

**What to borrow** (LoopX's real value is in its protocol and principles,
r6 §1.2/§3.3):

- **The five-command tick protocol:**
  `should-run → claim → update → refresh → spend` (deliberately small).
- **Six design principles:** make the human gate concrete (a gate must be a
  specific question, not "wait for the boss"); an honest safe fallback (a blocked
  main line may let P1/P2 continue but must not disguise an unresolved gate);
  feedback/reward is not permission (it cannot bypass gate/claim/quota); compact
  evidence (writeback replaces chat summaries); quota protects not only compute
  but human attention (a monitor-only turn with no state change stays quiet);
  and completion criteria are machine-verifiable.
- **Three-layer separation** (as LoopX's own *Embed LoopX In Your Agent Runner*
  describes it): the state source of truth (= our DB), the behavioral contract
  (= SKILL.md) and wake scheduling (= the runner) — the skill governs the
  behavioral contract, deterministic logic lives in the CLI, and state lives in
  external storage. This is exactly the lightweight form the community has
  already validated (r6 §2.3).

### 3.2 Design draft

**Data-model changes (SQLAlchemy, dialect-agnostic):**

| Table / field | Contents |
|---|---|
| `campaigns` (new table) | id, objective (e.g. "label 200 ROIs across 20 TCGA-BRCA slides and train to AUROC ≥ 0.9"), definition_of_done (a machine-verifiable expression such as `last_eval.auroc >= 0.9`), status (active/blocked/done), quota_lane JSON (slot_minutes, allowed_slots, spent_slots, window_hours) |
| `loop_todos` (new table) | todo_id, campaign_id, task_class (`annotate_batch` / `download` / `train` / `evaluate` / `retrain`), status (open/blocked/deferred/done), priority, required_capabilities, claimed_by + claimed_at + lease_ttl (starting with a soft claim + TTL, upgraded to a hard `FOR UPDATE` lock after moving to PostgreSQL), evidence_ref (pointing at roi_id/run_id; free text is not accepted as evidence) |
| `gates` (new table) | gate_id, campaign_id, kind (`roi_annotation_review` / `model_promotion` / `data_release`), question (concrete question text), blocking (bool), resolution (approved/rejected + person + timestamp), fallback_allowed (a description of the audited P1/P2 degradation path) |
| `agent_runs` (extended fields) | Add campaign_id, todo_id, claim_id, quota_spent (slot count), evidence_refs JSON, turn_result_kind (validated_progress / validated_completion / host_failure / validation_failed, ... — borrowing LoopX's 10-item result vocabulary) |

**The five tick commands mapped into our context:**

| LoopX command | HE-Scope loop layer | Semantics |
|---|---|---|
| `quota should-run` | `hescope-loop should-run --campaign C` | Check the quota lane (eligible/throttled) plus whether any open todo exists and no blocking gate is unresolved; return a scheduler_hint (back off / self-stop) and the interaction_contract. Monitor-only with no state change → quiet skip, consuming no quota (protecting the pathologist's attention) |
| `todo claim` | `hescope-loop claim --todo T --agent A` | A soft claim writing claimed_by/claimed_at/lease_ttl; already claimed with an unexpired lease → fail-closed rejection |
| `todo update` | `hescope-loop update --todo T --evidence ...` | Write back progress, evidence references and derived next todos; evidence must be a DB reference (run_id/roi_id/eval metric), never a chat-style summary |
| `refresh-state` | `hescope-loop status --campaign C` | A derived read-only projection: campaign progress, open todos, pending gates, remaining quota — the agent restores from disk with fresh context each round (community consensus #2) rather than sustaining a drifting long session |
| `quota spend-slot` | `hescope-loop spend --run R` | Accounting happens only after a validated slice (validated_progress/completion) completes; preflight failures and dry runs consume nothing |

**How the human gate hangs off the training pipeline.** The definition of done
for `train`/`retrain` todos hard-codes a precondition check: while a
`roi_annotation_review` gate has no resolution=approved, updates to the training
todo are rejected (feedback is not permission). The `model_promotion` gate sits
between "evaluation metric passes" and "the model is written into the usable
list under `data/models/`": evaluation runs automatically (machine-verifiable),
but promotion into the model list visible to `get_analysis_capabilities()`
requires human approval. On the marimo UI side, pending gates render as native
confirmation cards (a concrete question plus approve/reject buttons) with no jump
to an external tool — the gate *is* a native marimo interaction, which is our
domain advantage over LoopX's text protocol.

**Distribution form:**

- A small CLI, `hescope-loop` (click/argparse, a few hundred lines of Python,
  with all deterministic logic in the CLI rather than the prompt).
- A `he-scope-loop` SKILL.md (standard Agent Skills frontmatter, under 500
  lines) teaching any runtime's code agent the behavioral contract
  "should-run → claim → bounded turn → validate → writeback → spend", reusable
  across Codex / Claude Code / Kimi Code / a custom runner.
- The first version does no heartbeat automation: waking the runner manually or
  by cron is enough, with fresh context plus a `status` projection restoring each
  round.

**Failure-mode plan** (countermeasures in our context for the community's six
failure classes, r6 §2.1): no exit condition → a machine-verifiable
campaigns.definition_of_done expression; repeated failure under the same policy
→ the `update` command detects N consecutive validation_failed results,
automatically switches to replan_required and escalates a gate; context overflow
→ fresh context each round plus a `status` projection, never sustaining a long
session; vague goals → campaign creation forces three fields, objective + DoD +
non-goals; missing tool permissions → a write_scope allowlist recorded on
loop_todos; runaway quota → a hard allowed_slots ceiling plus the quiet-skip
convention (blakecrosley's report of 10× token consumption without a budget).

### 3.3 Re-evaluation triggers for LoopX

- **Trigger 1:** LoopX's pluggable-state-provider RFC ships (a DB-backed
  provider becomes writable) → evaluate whether "LoopX kernel + an HE-Scope
  provider" beats our own layer.
- **Trigger 2:** LoopX's general MCP surface (lifecycle reads plus todo/gate/lease
  writes) matures and stabilizes → evaluate replacing our CLI with MCP
  interoperability.
- **Cadence:** review the release notes every 6 weeks (next around 2026-09-19).
  Our data model is deliberately isomorphic to their concepts
  (todo/gate/quota/evidence), so migration cost stays manageable.
- **What we will not do:** not fork LoopX, not use its file projection as an
  intermediate layer (bidirectional DB↔file projection sync costs more than it
  returns, r6 §3.2), and not evaluate the Gas Town / Beads family at this stage
  (20+ agent code-factory parallelism is not our scenario).

---

## 4. Master milestone table (the next 3 months, 2026-08-08 → 2026-11-08)

Three lines woven into an executable sequence: **engineering (E) = open-source
iteration P0 items + FM integration; research (R) = goal A/B/C experiments; loop
(L) = the thin loop layer of §3**. "Overnight deliverables" are the engineering
outputs the overnight batch must have delivered and kept green by the end of each
sprint.

| Fortnight | Engineering E | Research R | Loop L | Overnight deliverables (mandatory) |
|---|---|---|---|---|
| **W1–2** (8/08–8/21) | Four P0 items: agent annotation write-back tools (submit_annotation/request_human_review), interaction trace persistence format v1, GeoJSON export, SKILL.md (he-scope) | Goal A: PathAgentBench data-availability check + protocol code skeleton; Goal C: FM selection download and environment validation (GPFM first) | Data-model changes finalized (campaigns/loop_todos/gates/agent_runs extended fields) | ① agent write-back tools + tests; ② repo SKILL.md; ③ GeoJSON export + a QuPath-open verification screenshot; ④ interactions table migration |
| **W3–4** (8/22–9/04) | FM encoder factory skeleton + mock tests (GPFM registered, license field + CI red-line check); first-run experience (uvx sandbox + pooch sample data + README GIF) | Goal A: k=0 pure-agent baseline reproduced (T3 localization / hit rate, aligned to the official mIoU < 0.09 and 2.0%); the k simulator (expert path replay) | `hescope-loop` CLI five commands implemented + unit tests | ⑤ FM encoder factory skeleton + mock tests, CI green; ⑥ one-command uvx first run + automatic sample-data download; ⑦ goal A k=0 baseline numbers |
| **W5–6** (9/05–9/18) | GPFM connected on real hardware (molab GPU) + similar-ROI retrieval (embedding nearest neighbors); annotation list panel | Goal A: full simulated-human curve over k (0/1/3/5) + statistics; Goal C: eva patch four-task parity batch started | Gates hung off the training pipeline (DoD precondition check + model_promotion gate) + marimo gate confirmation cards | ⑧ GPFM embedding end-to-end on the demo slide (mock → real hardware with zero code change); ⑨ goal A main curve data + figure |
| **W7–8** (9/19–10/02) | Result overlay v1 (heatmap rendering); example notebook sequence + molab online trial hosting | Goal C: eva parity report (±2% to pass); Goal B: CLAM/STAMP baseline curves reproduced on all three tasks (NSCLC 0.956 ± 0.020, LUAD mutation aligned to the 0.63–0.85 range) | he-scope-loop SKILL.md published; a minimal campaign (two active-learning rounds over 10 ROIs on the demo slide) working end to end | ⑩ eva parity report; ⑪ goal B's three baseline curves reproduced; ⑫ loop layer's minimal campaign with the full-flow log persisted |
| **W9–10** (10/03–10/16) | Spatial morphometrics toolkit v1 (distance/density metrics, the minimal kernel of SPARK's concepts); agent training trigger tool (gated on human confirmation) | Goal B: loop pipeline connected, pilot experiment (agent picks samples → human circles → retrain, 3 rounds); Goal A: real-human small-sample validation designed (crossover design, 6–8 readers) | SQLite → PostgreSQL claim path validated; first 6-week LoopX re-evaluation (executed from 9/19) | ⑬ goal B loop pilot curves (3 rounds); ⑭ morphometrics tools + tests; ⑮ LoopX re-evaluation minutes |
| **W11–12** (10/17–10/30) | Tool legality guardrails (allowlist + modality validation); MCP server spike | Goal A: real-human validation executed + paper first draft; Goal B: full budget curves + random/uncertainty/coreset baselines + database-accumulation ablation | Full-path stress test: correctness of multiple agents taking turns claiming the same campaign concurrently | ⑯ goal A paper first draft (including real-human validation); ⑰ goal B full curves + ablation data |
| **W13** (10/31–11/08) | Buffer: pay down debt, documentation, release v0.2.0 (monthly minor + the Updates timeline started) | Goal A submission preparation (MICCAI workshop or npj DM brief communication); Goal B main body writing started | Re-evaluate the loop layer against friction points found in real campaign use, and set the P1 iteration | ⑱ v0.2.0 release + status report on both papers |

**Dependencies and the critical path.** The FM encoder factory (W3–4) is a
prerequisite for every goal B/C experiment; agent write-back plus trace
persistence (W1–2) is a prerequisite for goal B's loop curves and for the loop
layer. Goal A is relatively independent (CPU + API suffice) and only weakly
coupled to the engineering line — if engineering slips, A is unaffected, which is
why A is scheduled as the first paper.

**Acceptance posture at three months.** A submittable HITL benchmark paper (A);
a fully reproduced annotation-efficiency protocol with loop pilot data (B); the
eva parity endorsement (C); the v0.2.0 open-source release (agent write access,
SKILL.md, FM factory, GeoJSON, loop CLI); and a thin loop layer that has survived
a real two-week campaign.

**Cross-line risk table** (not repeating the per-section decompositions; only
risks that would break the overall plan):

| Risk | Trigger | Mitigation | Fallback |
|---|---|---|---|
| PathAgentBench data not released | Week 1 check fails | Protocol replication + a self-built subset (§1.A risk 1) | Goal A becomes a pure protocol paper using a 200-slide self-annotated subset |
| molab GPU unavailable / rate-limited | Week 5 measurement fails | GPFM at 307M can run small samples slowly on CPU; no local GPU affects throughput, not correctness | Trim the parity report to the two small tasks, BACH and MHIST |
| Not enough people for three parallel lines | Any milestone slips two weeks running | Protect line A (weakly coupled, ships first) > protect line E's P0 > defer line B | Line B shrinks to the single NSCLC task; the loop layer shrinks to the data model plus the should-run and claim commands |
| TissueLab publishes formally first | Any time | Paper B's database-accumulation ablation and selection-budget protocol are differences outside their architecture | Cite them directly in related work and run an explicit comparison experiment |
| LoopX matures suddenly (provider shipped) | Found at re-evaluation | The data model is already isomorphic, so migration cost is manageable (§3.3) | Migrate only if it genuinely beats our own layer; otherwise keep tracking |

---

## Appendix: index of key cited figures

- PathAgentBench T3: localization mIoU < 0.09, centering heuristic IoU
  0.25–0.28, 40× hit rate 2.0%, T1 strongest 63.5% / experts 93.6%, T4 ~93%;
  1,822 WSIs / 17,135 paths (r1 §20, r3 §6.1)
- CLAM: NSCLC AUC 0.956 ± 0.020, 10-fold MC CV protocol, the 25% data point
  (r3 §1.1)
- Coudray LUAD mutation: held-out AUC 0.733–0.856; GigaPath FM+MIL five-gene
  macro-AUROC 0.626 (r3 §1.2)
- SHAL: Dice ≥ 0.80 at 26% annotation budget versus 37% for the baseline
  (r3 §5)
- Menon/MyriadAL: SOTA at ~5% annotation; WSI segmentation scan area reduced to
  2% (r4 §1.1)
- PulmoFoundation RCT: AI assistance +8.5pp, time −18.3%, **77.5% adoption when
  the AI was wrong** (the automation-bias red line, r4 §2.3/§6.2)
- GPFM: MIT, ViT-L/14 307M, rank 1.6 across 72 tasks; H-optimus-0: Apache-2.0,
  first on both in Campanella, 4.6 GB VRAM / 75 tiles/s; the CC-BY-NC-ND list
  (r2 §5/§8/§11)
- TRIDENT: 614 stars / 18 months, ships `.claude/skills/trident/SKILL.md`,
  GeoJSON ↔ QuPath symbiosis (r5 §3.3)
- SlideSeek: 47.4 regions per case versus a traditional 1020 ± 783 ROIs; the
  hallucinated-IHC failure mode (r1 §2)
- SPARK: Nature Medicine 2026, a training-free agent writing code for biomarker
  discovery (r1 §13)
- Pathology-CoT: 6× annotation speedup from behavior logs, external validation
  recall 97.6 (r1 §8)
- LoopX: 3,555 stars / ~2.3 months, v0.4.2, 316k lines, substantially
  single-author, pluggable-state-provider still only an RFC (r6 §1.4–1.5)
