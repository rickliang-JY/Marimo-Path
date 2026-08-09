# PAPERS.md — HE-Scope literature review (archive and writing material)

**English** · [简体中文](PAPERS.zh-CN.md)

> Compiled 2026-08-08, from four research reports: R1 (pathology AI agent
> systems), R2 (foundation models), R3 (benchmark landscape) and R4 (human-AI
> collaboration / active learning).
> Purpose: ① archiving the project's academic direction; ② related-work and
> positioning material for later papers (systems paper / methods paper /
> benchmark paper).
> Conventions: every factual statement carries a source URL (following the
> research files' citations); no papers or figures outside those four reports
> are introduced; proper nouns stay in English.

---

## Contents

1. [Pathology AI agent systems (2024–2026)](#1-pathology-ai-agent-systems-20242026)
2. [Human-AI collaboration and active learning](#2-human-ai-collaboration-and-active-learning)
3. [The evaluation landscape](#3-the-evaluation-landscape)
4. [HE-Scope's positioning and novelty analysis](#4-he-scopes-positioning-and-novelty-analysis)

---

## 1. Pathology AI agent systems (2024–2026)

### 1.0 Field map: four main threads

Pathology AI agents evolved along four threads across 2024–2026 (R1 §0):

1. **Pathology VLM copilots (single model).** Opened by PathChat (Nature 2024),
   followed by Quilt-LLaVA, SlideChat, WSI-LLaVA, PathGen-LLaVA and CPath-Omni,
   entering a reasoning-enhanced generation in 2025–2026 (Patho-R1,
   SmartPath-R1, TeamPath, generally trained with RL/GRPO). The repeatedly
   validated core conclusion: **pathology-specific MLLMs are markedly stronger
   than general frontier models.**
2. **Agentic WSI diagnosis (from ROI to whole slide).** The
   "Supervisor–Explorer / navigate–describe–diagnose" paradigm that exploded in
   2025: PathFinder, CPathAgent, SlideSeek (PathChat+), Pathology-CoT,
   PathNavigate. The agent decides autonomously where to look and at what
   magnification, and outputs a diagnosis with a coordinate-anchored evidence
   chain.
3. **Tool-calling / research-automation agents.** MMedAgent and CT-Agent (calling
   segmentation/detection/retrieval tools); SPARK (Nature Medicine 2026, an
   agent autonomously generating executable analysis code for biomarker
   discovery); SAGE and PathLab (natural-language research intent → reproducible
   computational pathology pipeline); and the commercial Judith (Modella AI).
4. **Evaluation infrastructure.** PathMMU, PathQABench, SlideBench, DDxBench,
   PathAgentBench, PathView-Bench, PathVG and others. The systematic conclusion:
   **answering questions ≠ finding evidence**, and evidence acquisition
   (localization/navigation) is the biggest current weakness (see §3).

### 1.1 Thread one: pathology VLM copilots

#### 1.1.1 PathChat — the opening work (Harvard / MGB Mahmood Lab, Nature 2024)

- **Institution/date:** Harvard Medical School / Mass General Brigham Mahmood Lab
  (Ming Y. Lu et al.), online in Nature 2024-06, Nature 634:466–473.
  https://www.nature.com/articles/s41586-024-07618-3
- **Architecture:** a non-agent end-to-end MLLM. The UNI vision encoder
  (self-supervised pretraining on 100M+ histology patches) → vision-language
  alignment on 1.18M pathology image-text pairs → 13B Llama 2 (the LLaVA 1.1.3
  framework) → fine-tuning on 456K vision-language instructions (999K QA turns).
- **Capabilities:** multi-turn dialogue; morphological description, differential
  diagnosis, grading, IHC/molecular test recommendation, prognosis/treatment
  knowledge Q&A. Input is an ROI image, not a whole WSI.
- **Key results:** a self-built PathQABench (multiple-choice diagnostic questions
  covering 54 diagnoses across 11 organs/practice areas, plus 260 open questions
  and human evaluation by pathology experts); outperforms GPT-4V, LLaVA and
  LLaVA-Med on open questions.
  https://pubmed.ncbi.nlm.nih.gov/38866050/
- **Open source:** code and model released (HuggingFace, research use);
  commercialization by Modella AI.
- **Relevance to HE-Scope:** the "ROI-level copilot" is the baseline, and the
  field has already moved to WSI-level agents. Its three-tier evaluation design
  (multiple choice + open questions + expert human evaluation) is worth
  borrowing. The paper itself notes it needs RLHF to reduce hallucination and
  needs to learn "which clinical information to ask for".

#### 1.1.2 SlideChat — the first WSI-level open-source vision-language assistant (Shanghai AI Laboratory and others, CVPR 2025)

- **Institution/date:** Ying Chen et al., arXiv 2024-10, CVPR 2025.
  https://arxiv.org/html/2410.11761
- **Architecture:** non-agent. Three levels: the CONCH patch encoder + the
  LongNet slide-level encoder (sparse attention over the whole slide's long
  sequence) + a multimodal projection + Qwen2.5-7B; two-stage training
  (cross-domain alignment → visual instruction learning).
- **Data/evaluation:** a self-built SlideInstruction (4.2K WSI-caption + 176K
  WSI-VQA); SlideBench (734 caption pairs, VQA-TCGA 7,827 questions across 13
  tasks, VQA-BCNB 7,274 questions across 7 tasks); overall accuracy 81.17% on
  SlideBench-VQA (TCGA), SOTA on 18 of 22 tasks.
- **Open source:** weights, code, instruction set and benchmark fully released.
- **Relevance to HE-Scope:** the "single model swallows the whole slide" route,
  complementary to the agent route; the CONCH+LongNet combination and the open
  benchmark are reusable infrastructure.

#### 1.1.3 VLM foundations (the agent's "eyes"): CONCH / Quilt-LLaVA / PLIP

- **CONCH** (Mahmood Lab + MIT, Nature Medicine 2024): a CoCa architecture
  trained on 1.17M image-text pairs, ~200M parameters; SOTA on 14 benchmarks
  including zero-shot classification, cross-modal retrieval and segmentation;
  widely used as the patch encoder/retriever in agent systems (SlideChat,
  PathNavigate and others).
  https://www.nature.com/articles/s41591-024-02856-4
- **Quilt-1M / Quilt-LLaVA** (University of Washington Shapiro Lab, NeurIPS 2023
  / CVPR 2024): 1M image-text pairs extracted from YouTube pathology teaching
  videos plus 107K instruction pairs fine-tuning LLaVA.
  https://arxiv.org/html/2606.07549v1 (reference)
- **A VLM comparison study** (measured on 3,507 gastrointestinal WSIs): CONCH
  mean AUC 0.876 > Quilt-LLaVA 0.753 > Quilt-Net 0.666. Model scale is not the
  deciding factor, domain alignment is; and prompt wording
  (dysplasia/atypia/precancerous) markedly affects results.
  https://arxiv.org/html/2505.00134v1
- **Relevance to HE-Scope:** prefer CONCH-style contrastive VLMs as retrieval and
  localization tools; prompt engineering is a systematic risk source and
  templates must be fixed. (For fuller tile/slide FM selection see §4.6 and R2.)

#### 1.1.4 The reasoning-enhanced generation: SmartPath-R1 / TeamPath / Patho-R1 (2025)

- **SmartPath-R1** (HKUST, Hao Chen's group, arXiv:2507.17303): a single MLLM
  doing ROI classification/detection/segmentation/VQA and WSI classification/VQA
  simultaneously; scale-aware SFT + task-aware RL fine-tuning (no CoT annotation
  needed); a MoE handling multi-scale multi-task dynamically; trained on 2.3M
  ROIs + 188K WSIs; validated on 72 tasks.
  https://arxiv.org/abs/2507.17303
- **TeamPath** (Yale + Duke-NUS + University of Tokyo/RIKEN, arXiv:2511.17652):
  built on Patho-R1-7B with GRPO reinforcement fine-tuning (reasoning data from
  o4-mini-generated CoT templates, quality-controlled by Yale pathologists, 20K
  reasoning prompts); an LLM router picks a strategy per task
  (RL/SFT/test-time-scaling experts) at over 80% routing accuracy; beats
  Patho-R1-7B, PathGen-LLaVA-13B and MedGemma-4B across all five PathMMU
  subsets; in human-AI collaboration experiments it acts as a
  verifier/corrector improving pathologists' answers with significant accuracy
  gains (p=0.004).
  https://arxiv.org/html/2511.17652v1
- **Patho-R1 / Patho-AgenticRAG** (West China Hospital of Sichuan University,
  Hong Bu's group, AAAI 2026): a multimodal pathology knowledge base (600+
  authoritative textbooks, 200K+ pages, ColQwen2 embeddings + Milvus/HNSW
  indexing) plus an agentic router and VRAG agent; GRPO Tool-Integrated
  Reasoning training teaches the agent "whether to retrieve, how to rewrite the
  question, which domain tool to call".
  https://arxiv.org/html/2508.02258v1
- **Summary:** R1-style RL reasoning is now mainstream in pathology VLMs;
  "router + multiple experts" is the lightweight agentic form; and positioning
  AI as correcting experts rather than replacing them is easier for clinical
  acceptance.

### 1.2 Thread two: agentic WSI diagnosis

#### 1.2.1 SlideSeek / PathChat+ — the multi-agent WSI diagnosis benchmark (Mahmood Lab, 2025–2026)

- **Institution/date:** Harvard/MIT Mahmood Lab, arXiv:2506.20964 (v1 2025-06;
  v2 2026-03, retitled *Evidence-based diagnostic reasoning with multi-agent
  copilot for human pathology*).
  https://arxiv.org/html/2506.20964v2
- **Architecture (two layers):**
  - **PathChat+** (the non-agent base MLLM): trained on 1.13M instructions, 5.49M
    QA turns and 624K images; supports multi-image input and high-resolution
    multi-ROI analysis.
  - **SlideSeek** (the agent layer): a reasoning LLM as supervisor (tracking
    progress, proposing hypotheses, dispatching tasks) plus multiple explorer
    agents (each calling PathChat+ for morphological description in an assigned
    region/magnification and reporting back) and a report agent synthesizing a
    visually anchored structured report. It examines on average 47.4 regions per
    case (11.9 high, 17.9 medium, 17.6 low magnification), where traditional
    methods must process 1020 ± 783 20× ROIs — navigation cuts computation
    substantially.
- **Key results:** on DDxBench (150 WSIs, 55 tumor types, 41 rare diseases,
  open-ended differential diagnosis) top-1 86.0% and top-3 92.7%, up to 42%
  above general MLLMs; PathChat+ alone reaches top-1 80.0% on expert-preselected
  ROIs (28.7% above Gemini 2.5 Pro). Ablations: removing the supervisor layer
  drops top-1 by 8%; swapping the captioner for a general GPT-5-mini drops it by
  43.3% — **a specialized morphology captioner is the key to agent system
  performance.** Metacognitive calibration: 82.7% accuracy on high-confidence
  cases versus 65.2% on low-confidence ones.
- **Failure modes (very important for platform design):** 45% of errors are
  tumor grading errors; small but decisive lesions get missed (such as a small
  Merkel cell carcinoma focus); and "hallucinated IHC" appears — the supervisor
  assigns an IHC task impossible on H&E and the explorer fabricates a result.
- **Open source:** paper and DDxBench public; the model commercialized through
  Modella.
- **Relevance to HE-Scope:** Supervisor–Explorer plus a specialized VLM captioner
  is an ablation-validated recipe; "tool/modality legality constraints" (against
  hallucinated IHC) is a guardrail any agent platform must build in; and reports
  must carry an ROI-coordinate evidence chain.

#### 1.2.2 PathFinder — a four-agent sequential pipeline (UW Shapiro Lab, 2025)

- **Institution/date:** University of Washington (Seyfioglu/Ghezloo et al.),
  arXiv:2502.08916, 2025-02; project page pathfinder-dx.github.io.
  https://arxiv.org/abs/2502.08916
- **Architecture:** a Triage Agent (benign/suspicious triage) → a Navigation
  Agent and Description Agent iteratively selecting patches and generating
  natural-language descriptions → a Diagnosis Agent synthesizing the diagnosis;
  simulating a pathologist's "scan at low power → inspect at high power → take
  notes → diagnose".
- **Key results:** on 238 M-Path skin biopsy melanoma grading cases, 74%
  accuracy — 8% above the best baseline and 9% above the pathologist average;
  description quality judged by pathologists as comparable to GPT-4o
  (LLM-as-judge, a 5-level Likert scale penalizing diagnostic hallucination).
- **Open source:** data, code and model released. The authors note limitations:
  compute dependence, complex navigation decisions, and occasional Description
  Agent hallucination.
- **Significance:** the earliest multi-agent system to achieve both an
  interpretable evidence chain and above-human-average performance; a sequential
  pipeline is simpler than a hierarchical supervisor but generalizes less well.

#### 1.2.3 CPathAgent — a trained navigation agent (Lin Yang's group, 2025)

- **Institution/date:** Yuxuan Sun et al., arXiv:2505.20510 (2025-05, v2
  2025-10). https://arxiv.org/abs/2505.20510
- **Architecture:** a single model trained in multiple stages to unify
  patch/region/WSI-level capability, navigating the WSI autonomously at inference
  in an agentic manner (observe → move → zoom) and emitting a transparent
  diagnostic summary — learning the navigation policy into the model rather than
  relying on prompt engineering.
- **Evaluation/data:** a self-built PathMMU-HR² (the first expert-validated
  "large region"-level benchmark, filling the scale gap between patch and WSI;
  1,688 expert-validated multi-scale VQA items, CPathAgent 88.6% versus
  Gemini-2.5-Pro 76.4). https://arxiv.org/html/2505.20510v1
- **Significance:** the intermediate scale (large region) is a neglected but
  clinically real unit of observation; trained navigation versus prompted
  navigation are two distinct technical routes.

#### 1.2.4 Pathology-CoT / Pathology-o3 — learning the agent from expert reading behavior (Stanford, 2025)

- **Institution/date:** Sheng Wang's group at Stanford, arXiv:2510.04587
  (2025-10). https://arxiv.org/abs/2510.04587
- **Architecture/data:** an AI Session Recorder embedded in a standard WSI viewer
  unobtrusively records pathologists' real navigation behavior (zoom, move) and
  converts it into behavioral commands plus bounding boxes; the AI drafts a
  rationale for "why look here" with light human review (6× annotation
  speedup), forming the Pathology-CoT dataset (paired "where to look" and "why").
  On that basis it trains the two-stage Pathology-o3 agent: propose ROIs first,
  then reason under behavioral guidance.
- **Key results:** gastrointestinal lymph node metastasis detection — recall 100
  on Stanford internal validation and recall 97.6 on independent external
  validation in Sweden, beating OpenAI o3 and generalizing across backbones.
- **Relevance to HE-Scope (key):** **viewer behavior logs are a gold mine.** As a
  viewing platform, HE-Scope naturally collects the same kind of data — the
  unique data flywheel a platform product has over a pure model product.

#### 1.2.5 PathoSage — evidence adjudication and tool-reliability modeling (2026)

- **Institution/date:** arXiv:2606.07549 (2026-05).
  https://arxiv.org/abs/2606.07549
- **Architecture:** three explicitly separated stages — knowledge retrieval,
  evidence collection and evidence adjudication (Structured Evidence
  Deliberation): heterogeneous tool outputs are assessed independently,
  conflicts analyzed, and the final judgment made in a fresh context to reduce
  anchoring bias. A training-free Beta-Bernoulli empirical system continuously
  models each tool's long-run reliability, forming a similarity-weighted prior.
- **Key results:** mitigates VQA hallucination and classifier disagreement,
  beating strong pathology MLLMs and agentic baselines.
- **Significance:** a direct answer to the context-pollution and tool-conflict
  problems SlideSeek exposed; tool-reliability tracking should be a first-class
  citizen on an agent platform.

#### 1.2.6 PathNavigate — a training-free WSI-VQA agent (Tencent/Peking University and others, 2026)

- **Institution/date:** Chunze Yang, Chen Li et al., arXiv:2605.23559 (2026-05).
  https://arxiv.org/abs/2605.23559
- **Architecture:** training-free, scan-search-readout. First it uses frozen
  pathology features plus an online shared memory to generate a "surprise field"
  pool of anomalous regions at low magnification (scanning before reading the
  question, avoiding question-first misses of decisive morphology the question
  never names); then it retrieves question-conditioned high-magnification
  targets within that pool using PLIP; and finally a frozen
  perceptor-adjudicator stack answers.
- **Key results:** accuracy improvements on WSI-VQA and SlideBench-BCNB, with
  better interpretability of the evidence-selection trajectory; code
  open-sourced.
- **Significance:** a training-free agent is already practical using "anomaly
  prior + frozen features + online memory" — particularly friendly for platforms
  that must accommodate user-supplied models.

### 1.3 Thread three: tool-calling / research-automation agents

#### 1.3.1 SPARK — a training-free autonomous scientific discovery agent (Nature Medicine 2026) ★

- **Institution/date:** Yuri Tolkach et al. (Uniklinik Köln UKK / UKE cohorts,
  Germany), Nature Medicine 2026-04-29.
  https://www.nature.com/articles/s41591-026-04357-y
- **Architecture:** the crewAI framework's agent–task–crew–flow–tool paradigm, a
  four-stage pipeline: idea generation → idea refinement → parameter/code
  implementation → parameter validation. Input is a WSI object after QC,
  organ-specific multi-class tissue segmentation and single-cell detection
  preprocessing. The agent uses language as a universal interface, autonomously
  proposing biological concepts (such as "lymphocyte density within 800 μm of
  the tumor") and writing them as executable analysis tools, **with no model
  training anywhere**, prototyping a new analysis within hours. Tools are
  implemented extra-agentically to save tokens; empirically, agentic memory was
  useless and expensive, so it is disabled entirely.
- **Key results:** across LUAD/LUSC/COAD/BRCA/HNSC multi-cohort data (TCGA,
  PLCO, NLST, UKK, UKE, HAL), the generated interpretable concept library
  markedly improves tumor grading and prognostic stratification; in PD-L1 status
  prediction it captures broader immune-escape features; and it supports
  interaction with pathologists (human proposes the concept, agent implements
  it).
- **Open source:** code, parameters and a reference manual on Zenodo
  (records/18047852).
- **Relevance to HE-Scope (the strongest endorsement):** **"an agent writing code
  to call classical image analysis (segmentation, cell detection, morphometrics)"
  was validated in a top journal as better than direct VLM reasoning** — exactly
  HE-Scope's code-agent bridge positioning. The paper explicitly criticizes VLMs
  for answering "even simple quantitative/reasoning questions" unreliably.

#### 1.3.2 PathLab — an agent society generating reproducible computational pathology research (2026)

- arXiv:2606.20677 (2026-06). A dynamic agent society parses natural-language
  research intent into computational tasks and selects methodological
  components; two modes, Co-pilot (iterative human-AI collaboration) and
  Auto-pilot (fully automatic pipeline generation); built-in domain validation
  (technical compatibility, information-leakage prevention); the output is a
  validated executable configuration rather than code fragments, supporting
  community sharing and reuse. https://arxiv.org/html/2606.20677
- **Significance:** "natural language → reproducible pathology pipeline
  configuration" is highly isomorphic to HE-Scope's marimo notebook artifact;
  leakage prevention and endpoint-definition validation are pathology-specific
  moats.

#### 1.3.3 SAGE — a multi-role agent for biomarker discovery (2026)

- arXiv:2602.00953 (2026-02). Seven role agents — Ontologist, Scientist, Senior
  Scientist, Clinical Feasibility, Debate-based Critic, Coding (executing
  validation on patient cohorts) and Summary; an end-to-end
  discover–explain–validate bladder cancer use case.
  https://arxiv.org/html/2602.00953v1
- **Significance:** role separation plus debate-style review is a design pattern
  for suppressing spurious correlations; the Coding agent running cohort
  analysis directly is exactly the code-agent bridge.

#### 1.3.4 Tool-calling agents outside pathology (pattern references)

- **MMedAgent** (EMNLP 2024 Findings, arXiv:2407.02483): the first multimodal
  medical tool-calling agent; LLaVA-Med as planner, end-to-end instruction
  tuning teaching it to call six tool classes (Grounding DINO localization,
  MedSAM segmentation, BiomedCLIP classification, ChatCAD reporting, RAG),
  including the histology modality; 1.8× LLaVA-Med overall and beating GPT-4o on
  several items; code open-sourced.
  https://arxiv.org/html/2407.02483v2 ; https://github.com/Wangyixinxin/MMedAgent
- **CT-Agent** (Science China Information Sciences 2026): a three-module
  planning / action-space / memory agent for 3D CT question answering.
  http://scis.scichina.com/en/2026/150107.pdf
- Others: CXRAgent (2510.21324), RadAgents (2509.20490), MedSAM-Agent
  (2602.03320, the first multi-turn agentic RL interactive segmentation),
  MedAgent-Pro (2503.18968).
  https://arxiv.org/html/2607.11175v1 (reference)
- **Summary:** "frozen specialized models as tools + an LLM as planner" is mature
  in radiology; pathology's distinguishing factor is the extra dimension of
  gigapixel navigation.

#### 1.3.5 Multi-agent MDT discussion (tumor board simulation, downstream of pathology)

- **EvoMDT** (npj Digital Medicine, 2026-01): five agents — diagnosis, treatment,
  safety, monitoring and coordination — simulating a multidisciplinary tumor
  board, with the coordinator weighting dynamically by confidence and resolving
  conflicts; built on DeepSeek V3/R1.
  https://www.nature.com/articles/s41746-025-02304-8
- A systematic review (Frontiers in Oncology Reviews, 2026-05): the landscape of
  LLMs for MDT, emphasizing that accuracy, safety and clinical utility remain
  unresolved.
  https://www.frontiersin.org/journals/oncology-reviews/articles/10.3389/or.2026.1757059/full

#### 1.3.6 PathGen-1.6M — multi-agent data generation (ICLR 2025)

- Multi-agent collaboration generates 1.6M pathology image-text pairs
  (representative patches) from ~9K TCGA WSIs, training PathGen-LLaVA; also
  releases the PathMMU benchmark (24K+ expert-level multiple-choice questions,
  now the de facto standard test set for pathology MLLMs).
  https://www.nature.com/articles/s43588-025-00818-5
- **Significance:** multi-agent systems are used not only for reasoning but for
  large-scale data synthesis.

### 1.4 Thread four: evaluation infrastructure (brief; see §3)

| Benchmark | Source | What it measures | Key finding |
|---|---|---|---|
| PathMMU | PathGen (ICLR 2025) | ROI-level multiple-choice VQA | General VLMs lag pathology-specific models badly |
| PathQABench | PathChat (Nature 2024) | Multiple-choice diagnosis + open questions + expert human evaluation | A three-tier evaluation design paradigm |
| SlideBench | SlideChat (CVPR 2025) | WSI caption + VQA (21 tasks) | Whole-slide modeling beats patch voting / thumbnails |
| DDxBench | SlideSeek (2025/2026) | Open-ended differential diagnosis over 150 WSIs, 55 tumors | top-1 86% (SlideSeek); grading and small lesions are the disaster areas |
| PathAgentBench | arXiv:2607.19261 (2026-07) | Four capabilities: evidence interpretation / verification / acquisition / integration | Text-guided localization mIoU < 0.09, worse than a centering heuristic; autonomous exploration hits only 2.0% at high magnification |
| PathView-Bench | arXiv:2607.28318 (2026-07) | Fine-grained multi-scale understanding | Very recent, landscape unsettled |
| PathVG | MICCAI 2025 | Pathology visual grounding | — |

PathAgentBench's conclusion is one of the field's most important negative
results: **current models can reason over evidence handed to them but cannot
find it themselves.** https://arxiv.org/abs/2607.19261

### 1.5 Industry developments: Modella AI / PathChat 2 / PathChat DX / Judith

- **Modella AI** (Boston, spun out of the Mahmood Lab in 2024-06). **PathChat 2**
  supports interleaved multi-high-resolution-image and text dialogue inside a
  slide viewer, with markedly stronger differential diagnosis, morphological
  description, instruction following and report summarization, and lets users
  circle an ROI on the slide to ask about morphology, differential diagnosis and
  biomarkers (research/education use). The clinical version **PathChat DX
  received FDA Breakthrough Device Designation in 2025-02** (note: this is not
  marketing approval). **Judith**: a research-facing AI agent — the user
  describes a task in natural language (say, segmenting a cell type) and Judith
  handles modeling, analysis and interpretation, supporting gigapixel WSIs and
  foundation-model biomarker discovery.
- **On 2026-01-13 AstraZeneca announced its acquisition of Modella AI** (billed
  as "the first acquisition of an AI company by a large pharma"), for
  quantitative pathology and biomarkers in oncology R&D.
- Sources: https://www.modella.ai/pathchat ; https://www.modella.ai/judith ;
  https://www.biopharmatrend.com/news/astrazeneca-acquires-modella-ai-to-integrate-foundation-models-into-global-oncology-rd-1463/ ;
  https://www.urotoday.com/conference-highlights/bcantt-2026/170891-bcantt-2026-ai-in-bladder-cancer-whats-real-whats-next-and-what-to-watch-out-for.html
- **Significance:** industry has validated the dual product form of "copilot (for
  humans) + agent (for research automation)"; the FDA Breakthrough Device
  designation shows the regulatory channel is real; and a code agent building
  analysis pipelines automatically (the Judith model) is exactly HE-Scope's
  reference scenario.

### 1.6 Field-consensus open problems (a constraint list for platform design)

Sources: *Computational Pathology in the Era of Emerging Foundation and Agentic
AI* (arXiv:2603.05884), the Lancet Digital Health 2025 commentary
(PIIS2589-7500(25)00115-3), *Foundation Models in Computational Pathology: A
Review…* (arXiv:2502.08333) and the limitation analyses of the individual
systems:

1. **Missing evidence-acquisition capability** (the hardest): PathAgentBench
   localization mIoU < 0.09, 2.0% autonomous exploration hit rate at high
   magnification.
2. **Hallucination and tool/modality legality:** morphological hallucination,
   hallucinated IHC; a pen mark alone can mislead general VLMs at the GPT-4 /
   Claude level (Lancet commentary).
3. **Error propagation in agentic systems:** multi-agent pipelines accumulate and
   amplify error when components are unreliable.
4. **Fine-grained grading and small-lesion detection:** 45% of SlideSeek's
   failures are grading errors.
5. **Scarcity of WSI-level training data and behavior data:** behavioral
   supervision of where experts look and why barely exists (Pathology-CoT's core
   motivation).
6. **Immature evaluation:** agentic workflow evaluation can be gamed; most work
   is retrospective and single-center; unified agent trajectory/cost/safety
   metrics are missing.
7. **Robustness and domain shift:** scanner, stain and preparation differences;
   a 10-dimension deployment risk table (review ①).
8. **The clinical translation gap:** cost, reimbursement, LIS/PACS integration,
   legal liability, automation bias and skill degradation.
9. **Multimodal extension not done:** most systems handle only H&E.
10. **Cost and efficiency:** agentic memory is expensive and useless (SPARK's
    empirical finding).
11. **Closed source impedes reproducible research:** criticized by TeamPath and
    others; open weights plus open benchmarks (the SlideChat model) remain a
    minority.

---

## 2. Human-AI collaboration and active learning

### 2.0 Overall judgment

The loop "human circles → AI analyzes → feedback persisted → retraining" has
**mature work on every individual link** in the literature (active learning,
interactive segmentation, human-AI collaboration reader studies, data
flywheels), but work integrating all three of "**an LLM/VLM agent as the analysis
subject + human ROI selection as the interaction primitive + structured
annotation persistence driving continual retraining**" into one platform is very
rare. The closest are nuclei.io (Nat Biomed Eng 2024, no LLM agent) and
TissueLab (arXiv:2509.20279, 2025-09, co-evolving agentic AI + expert real-time
feedback + active learning). **TissueLab overlaps HE-Scope's positioning the
most** (comparison in §2.6).
https://pubmed.ncbi.nlm.nih.gov/38898173/ ; https://arxiv.org/abs/2509.20279

### 2.1 Active learning for pathology annotation

#### 2.1.1 Representative work and quantitative conclusions

- **Menon et al., ICPR 2022 (CVIT-IIIT):** expert-in-the-loop interactive
  learning with a CNN plus metric-learning retrieval. The expert supplies one
  query patch, the system samples K patches ordered by high-dimensional feature
  distance for expert review, and fine-tunes over several rounds. On 100K
  9-class colorectal cancer patches it reaches SOTA with only about **5%
  annotation** (other interactive methods need 35%–50%); on ICIAR breast tumor
  segmentation it reduces the scanned area to **2%** of the slide (about 250
  patches) at 85% IOU.
  https://dl.acm.org/doi/10.1007/978-3-031-02444-3_38 ;
  https://cvit.iiit.ac.in/images/ConferencePapers/2021/Interactive_Learning.pdf
- **AL + Attention MIL** (ISBI 2023, arXiv:2303.01342): computes a per-WSI
  confidence for attention-MIL, asks experts to annotate ROIs on the least
  certain slides, and with an attention-guiding loss markedly improves
  classification accuracy and convergence speed on CAMELYON17 with very few ROI
  annotations. https://ui.adsabs.harvard.edu/abs/arXiv:2303.01342
- **MyriadAL** (arXiv:2310.16161): a contrastive-learning encoder plus
  pseudo-label refinement and a combined uncertainty query; at very low budget it
  approaches fully supervised accuracy annotating only **5% of data**.
  https://arxiv.org/abs/2310.16161
- **Annotation-Efficient Polyp Segmentation via AL** (arXiv:2403.14350):
  uncertainty-weighted clustering (mixing uncertainty and diversity), with the
  standard "annotation budget vs performance curve" protocol.
  https://arxiv.org/html/2403.14350v1
- **Prototype sampling** (arXiv:2407.06363): uses prototype embeddings from
  image-text databases (ARCH, OpenPath) to select representative regions,
  mitigating the AL cold start. https://arxiv.org/html/2407.06363
- Classic cell segmentation AL: *Active deep learning reduces annotation burden*
  (bioRxiv 2017, uncertainty sampling).
  https://www.biorxiv.org/content/10.1101/211060v2.full-text

#### 2.1.2 The query-strategy spectrum

1. **Uncertainty:** entropy/confidence/margin, Bayesian CNNs.
2. **Diversity / coreset:** k-means feature-space coverage (CoreSet, Sener &
   Savarese 2018).
3. **Hybrid:** uncertainty-weighted clustering, deduplicating an uncertainty
   list, BEMPS scoring rules.
4. **Retrieval / prototype style:** metric-learning nearest-neighbor retrieval
   (Menon), image-text prototypes — this "human gives one example, system finds
   similar ones" pattern is highly isomorphic to HE-Scope's "human circles an
   ROI" interaction.

#### 2.1.3 Measurement protocols and the gap

- **The standard protocol:** an annotation-budget vs performance curve (x =
  number of annotated samples or budget fraction, y = test accuracy / Dice),
  compared against a random-sampling baseline, reporting "annotation required to
  reach X% of fully supervised performance".
- **Magnitudes of the quantitative conclusions:** pathology patch classification
  at 5% annotation ≈ fully supervised (Menon, MyriadAL); WSI segmentation scan
  area reduced to 2%; interactive learning saves roughly another 7× annotation
  over traditional AL.
- **The benchmark gap:** the *Label-Efficient Medical Image Analysis* survey
  (arXiv:2303.12484) states plainly that tasks, organs, budgets and split
  protocols are fragmented across studies, that a standardized benchmark for
  "accuracy per unit annotation cost" is missing, and calls for fixed label
  budgets, cost-aware metrics and standardized human-in-the-loop evaluation
  protocols. https://arxiv.org/html/2303.12484v5 — **this is precisely the gap
  HE-Scope can fill.**

#### 2.1.4 Weak annotation forms (directly relevant to HE-Scope's weakly-supervised training)

- Point-annotated nuclei segmentation: Qu et al., MIDL 2019 (Voronoi/Gaussian
  maps generating pseudo-labels from points; point annotation saves 88% of the
  time, boxes 42%). https://proceedings.mlr.press/v102/qu19a.html
- MIL / slide-level weak supervision: ABMIL, CLAM, IMIL (Phys Med Biol 2023,
  PMID 37311470) and others are already mainstream. **A human-circled ROI can be
  seen as an interactive weak-supervision signal between point and box
  annotation.**

### 2.2 Human-AI collaborative diagnosis (pathologist + AI)

#### 2.2.1 Foundational reader studies

- **Steiner et al., Am J Surg Pathol 2018 (LYNA):** 6 pathologists × 70 lymph
  node slides, a crossover two-mode design (assisted/unassisted + washout). With
  AI assistance, micrometastasis detection sensitivity rose to 91% from 83%
  (p=0.02); reading time fell to 61s from 116s for micrometastases and to 111s
  from 137s for negatives; subjective difficulty dropped significantly.
  **Collaboration beats either party alone.**
  https://pubmed.ncbi.nlm.nih.gov/30312179/
- **Tschandl et al., Nat Med 2020:** human–computer collaboration in skin cancer;
  collaboration beats human or AI alone.
  https://www.nature.com/articles/s41746-024-01031-w (review, ref 10)
- **Raciti et al. (Paige) 2023:** AI-assisted prostate cancer; diagnosis time
  129s → 58s (+55% efficiency), sensitivity 74.5% → 93.5%.
  https://conexiant.com/internal-medicine/articles/breast-cancer-diagnosis-55-percent-gain-in-efficiency-with-ai-assisted-pathology/

#### 2.2.2 Paradigms for measuring collaboration gain

1. **Multi-reader multi-case crossover (MRMC crossover):** the same clinicians
   read the same cases with and without AI in randomized order with a 2–4 week
   washout, so each clinician is their own control — giving statistical power
   even at small sample sizes (typically 6–8 clinicians, 70–658 cases).
2. **Metrics:** accuracy/sensitivity/specificity (plus mixed-effects logistic
   regression adjusted OR), reading time, diagnostic confidence (Likert),
   inter-rater κ, and adoption-rate analysis (adoption reported separately for
   when the AI is right and when it is wrong).
3. **Stratified analysis:** junior versus senior (juniors gain more: +12.5 versus
   +4.4 pp, PulmoFoundation 2026).

#### 2.2.3 The latest large crossover RCTs (2026, design templates)

- **PulmoFoundation** (a lung pathology FM, arXiv:2605.25878, 2026-05): a
  registered crossover RCT (NCT07157618), 8 pathologists × 658 cases × 4 tasks,
  10,528 readings; AI assistance improved accuracy by 8.5pp (adjusted OR=2.31),
  cut time by 18.3%, and raised κ from 0.55 to 0.76. **The key negative finding:
  when the AI was wrong, pathologists adopted the wrong label 77.5% of the time
  (automation bias).** https://arxiv.org/html/2605.25878v2
- **GRACE** (a gastric cancer FM, arXiv:2606.04792): a crossover reader study;
  accuracy 82.0% → 89.9% (OR=1.987), time −14.9%, confidence +9.0%; plus an
  error-correction pattern analysis. https://arxiv.org/abs/2606.04792
- **BRAVE** (a breast FM, arXiv:2605.08207): balanced accuracy 88.5% → 95.1%
  (OR=3.14). https://arxiv.org/html/2605.08207v1

#### 2.2.4 Counter-evidence: collaboration is not always better than solo

- **Vaccaro et al., Nat Hum Behav 2024 (a meta-analysis of 106 experiments):** on
  average human-AI combinations are **worse** than the stronger of human or AI
  alone; decision tasks (including medical diagnosis) often show negative
  synergy; HAI combinations beat AI alone in only 42% of experiments and humans
  alone in 85%. A follow-up re-analysis shows human–LLM teams are more likely to
  show strong synergy on tasks where AI has the advantage.
  https://arxiv.org/pdf/2507.19486
- **HCT (Human Collective Teamwork, arXiv:2603.29866, 2026):** independent
  aggregation (AI voting with two humans, with a tiebreak on disagreement) beats
  the "AI-as-advisor" mode — **the design of the advisory collaboration interface
  itself determines the gain.** https://arxiv.org/html/2603.29866v1
- Over-reliance risk: a study of colorectal high-grade dysplasia (PMC12393786)
  shows AI assistance raises accuracy but also raises over-reliance concerns.
  https://pmc.ncbi.nlm.nih.gov/articles/PMC12393786/

**Implications for HE-Scope:** collaboration gain depends heavily on task and
interface design; "the human goes wrong when the AI does" (77.5% adoption) is a
risk that must be answered head-on — and HE-Scope's interaction structure, where
the human initiates the selection and confirms the result, keeps decision
authority on the human side and can be framed as an anti-automation-bias design.

### 2.3 Interactive segmentation / correction

- **NuClick** (Med Image Anal 2020, PMID 32769053, 200+ citations): a single
  click precisely segments a nucleus/cell, with squiggle guidance for glands;
  clicks and squiggles enter the CNN as an auxiliary channel. An instance
  segmentation model trained on NuClick-generated annotations won first place at
  LYON19. https://pubmed.ncbi.nlm.nih.gov/32769053/
- **Clore** (arXiv:2603.27625, 2026): click-based local refinement — the first n
  clicks do a coarse global segmentation, after which local high-resolution patch
  refinement is triggered; SOTA NoC@90 on GlaS/NuCLS/DigestPath (averaging 1.8
  fewer clicks than RITM/FocalClick). Evaluation protocol: NoC@85/90, NoF@85/90,
  and fixed-click-count mDice curves. https://arxiv.org/html/2603.27625v1
- **PathoSAM** (arXiv:2502.00408, 2025): SAM specialized for pathology nuclei
  segmentation, a generalist over 6 datasets, SOTA in both interactive (point/box
  prompt) and automatic instance segmentation, already integrated into QuPath and
  μSAM. https://arxiv.org/html/2502.00408v2
- **CellPilot** (arXiv:2411.15514): unifying automatic and interactive
  segmentation.
- Methodological lineage: fCN → DeepIGeoS → RITM/SimpleClick/FocalClick → the SAM
  family → agentification (MedSAM-Agent 2026, the first RL-trained interactive
  segmentation agent).
  https://github.com/Nanboy-Ronan/awesome-medical-imaging-agents
- Review: *Single-click interactive cell nucleus segmentation* (ScienceDirect
  2025). https://www.sciencedirect.com/science/article/pii/S1746809425009073

**Relationship to HE-Scope:** "human circles an ROI" is a standard prompt form in
the interactive segmentation literature (box/scribble carries more information
than a click); PathoSAM/Clore can be reused directly as an ROI segmentation
backend, making the human's selection serve as both a segmentation prompt and an
annotation signal.

### 2.4 LLMs / agents as annotation assistants (2024–2026)

#### 2.4.1 On the NLP side (methodologically transferable)

- **ActiveLLM** (arXiv:2405.10808): uses an LLM such as GPT-4 in place of
  uncertainty measures to pick samples for annotation, solving the AL cold start.
  https://arxiv.org/abs/2405.10808
- **A Survey of LLM-based Active Learning** (ACL 2025): the LLM's two roles in
  AL — (a) selecting/generating samples to annotate, (b) acting directly as the
  annotator; a human can stay in either.
  https://aclanthology.org/2025.acl-long.708.pdf
- **MoLLIA** (arXiv:2601.15773, 2026): Mixture-of-LLMs in the loop, combining
  multi-LLM voting annotation with traditional query strategies.
  https://arxiv.org/html/2601.15773v1
- **RLTHF:** LLM pre-annotation plus targeted correction of only 6–7% by humans
  matches fully manual annotation quality.
  https://kili-technology.com/blog/data-annotation-guide-how-to-achieve-high-quality-data-in-complex-ai-data-operations
- **Human–LLM collaborative annotation workflows** (Kang et al. 2024 and
  others): the LLM proposes labels with explanations and humans verify
  low-confidence samples; LLMs suit triage and QC better than fully replacing
  humans. https://arxiv.org/html/2603.02569v1
- **Beyond Labels** (Yao et al. 2023): annotators supply both labels and free-text
  explanations, in a dual-model AL architecture.

#### 2.4.2 On the pathology / medical imaging side

- **PathChat + SlideSeek** (§1.1.1, §1.2.1): a pathology MLLM copilot and
  multi-agent WSI diagnosis, but **with no human in the loop** — no annotation
  persistence, no retraining.
  https://www.nature.com/articles/s41586-024-07618-3 ;
  https://arxiv.org/html/2506.20964v1
- **The pathology agent ecosystem (exploding in 2025–2026):** PathAgent
  (Navigator-Perceptor-Executor, training-free simulation of a pathologist's
  stepwise reasoning, arXiv:2511.17052), CPathAgent, PathFinder, WSI-Agents
  (MICCAI 2025), Pathology-CoT, GIANT/PathNavigate/PathReasoning, NOVA (49 tools
  + code execution), TEAM-Agent (a prognostic agent corrected
  clinician-in-the-loop, medRxiv 2026), CellDX AI Autopilot (an agent guiding
  pathologists to train/deploy classifiers, arXiv 2026). Lists:
  https://github.com/Nanboy-Ronan/awesome-medical-imaging-agents ;
  https://github.com/zhcz328/Awesome-Medical-Agents
- **A direct precedent for annotation agents:** Colon-Bench (an agentic workflow
  producing dense lesion annotation for colonoscopy, 528 videos, 300K boxes,
  2026) — "an agent auto-annotating to build a benchmark" is already accepted as
  a paper form.
- Pathology VLM visual understanding questioned: *Do Pathology VLMs Truly See
  Pathology?* (arXiv:2607.21065) — VLMs' fine-grained morphological understanding
  is unreliable, **which makes human-in-the-loop supervision and correction more
  necessary, not less.** https://arxiv.org/html/2607.21065v1

**Summary:** LLM-as-annotator and LLM-guided AL are a developed system in NLP;
pathology agents have exploded on the analysis/navigation/QA side; but
"**agent-assisted annotation + quality control + annotation persistence +
retraining**" is essentially untouched in pathology (TEAM-Agent and CellDX are
the closest edge cases).

### 2.5 Data flywheels / continual learning

- **The data flywheel paradigm** (the NVIDIA glossary and others): produce
  interaction data → clean and synthesize training signal → fine-tune
  (SFT/LoRA/RLHF/distillation) → evaluate rigorously → deploy gradually →
  collect intrinsic and extrinsic feedback, a self-reinforcing loop.
  High-signal data = wrong predictions, low-confidence outputs, user
  corrections. https://www.nvidia.com/en-us/glossary/data-flywheel/
- **Medical AI drift monitoring** (*Keeping Medical AI Healthy*,
  arXiv:2506.17442): performance drift, output distribution drift (BBSD/MMD,
  softmax entropy, energy score), auxiliary error estimation, calibration drift
  detection and adaptive-window recalibration.
  https://arxiv.org/html/2506.17442v1
- **Human-in-the-loop governance:** route uncertain or anomalous predictions to
  clinical experts for review; studies show 91% of medical AI models degrade over
  time → proactive periodic fine-tuning.
  https://censinet.com/perspectives/ai-model-drift-monitoring-ensuring-ongoing-performance-of-healthcare-ai-vendors
- **Continual learning precedents in clinical pathology are scarce:** searching
  "clinical deployment + continual learning + pathology" turns up essentially no
  direct system; the closest are nuclei.io (rapid dataset and model building in a
  research setting) and TissueLab (which claims to learn continuously from
  clinicians). The regulatory side (FDA PCCP, GMLP) is noted for later research.
- Annotation-platform engineering practice (Kili and others): pre-labeling,
  confidence routing, honeypot quality control and calibration rounds guarding
  against automation over-trust.
  https://kili-technology.com/blog/data-annotation-guide-how-to-achieve-high-quality-data-in-complex-ai-data-operations

**Summary:** the data flywheel is a mature concept in industrial ML but has
**barely been implemented systematically in the computational pathology
literature** — HE-Scope's annotate → persist → retrain → re-annotate loop can be
positioned as "the first open-source reference implementation and empirical study
of a data flywheel in pathology".

### 2.6 Comparison against the three closest works ★

| Dimension | nuclei.io (Nat Biomed Eng 2024) | TissueLab (arXiv:2509.20279, 2025-09) | The PathAgent / SlideSeek family (2025) |
|---|---|---|---|
| Analysis subject | Traditional ML (morphological features + XGBoost-class) | **LLM agent + tool factory** | LLM agent |
| Human-in-the-loop role | Real-time feedback correcting candidate nuclei; active learning selects samples | Experts visualize intermediate results and refine | Collaborative evaluation (the human appears only at evaluation) |
| Interaction primitive | Clicking candidate cells/regions | Conversational instructions + intermediate-result correction | ROI navigation (mostly agent-autonomous) |
| Annotation persistence / data asset | Rapid dataset building (session level) | Claims continuous learning from clinicians | **None** |
| Weakly-supervised retraining | Yes (active learning fine-tuning) | Yes (active learning, adapting to a new disease in minutes) | No (training-free) |
| Database loop | Weak | Partial (not database-centric) | None |
| Evaluation | Two crossover user studies | Quantitative task comparison | VQA benchmark |
| URL | https://pubmed.ncbi.nlm.nih.gov/38898173/ | https://arxiv.org/abs/2509.20279 | https://arxiv.org/abs/2511.17052 |

Overlaps and differences, one by one:

- **nuclei.io** (Zhi Huang / James Zou's group at Stanford) holds the high-tier
  journal precedent for a "pathologist-AI collaboration framework" and the
  "active learning + real-time feedback + crossover user study" paradigm.
  **Overlap:** human-in-the-loop correction plus active-learning retraining.
  **Difference:** no LLM/VLM agent; the interaction is "system lists candidates,
  human clicks to confirm" (AI-led); feedback is session-level, not centered on
  an annotation database, with no cross-task reuse protocol.
- **TissueLab** (the same group, evolved from nuclei.io) holds the "co-evolving
  agentic system + expert feedback + active learning" narrative, claiming
  continuous learning from clinicians and adaptation to a new disease within
  minutes. **The highest overlap.** **Difference:** feedback remains
  conversational co-evolution (session feedback is not persisted); there is no
  unified "circle an ROI" interaction primitive; annotations do not accumulate as
  a first-class asset; and no cross-task weakly-supervised retraining protocol or
  database-loop ablation is given.
- **The PathAgent / SlideSeek lineage** holds the "agent simulates pathologist
  reasoning" narrative. **Overlap:** LLM agents analyzing WSIs with ROI evidence
  chains. **Difference:** training-free and session-level, with the human present
  only at evaluation, and **no annotation-persistence-retraining loop at all.**

**Threat assessment.** If TissueLab publishes formally in a high-tier journal and
open-sources its continual-learning module, HE-Scope's window narrows; nuclei.io
and the PathAgent family seal off the "human-AI collaboration framework" and
"agent reasoning" narrative ports respectively — so novelty must land at the
intersection of the three, the **database-centric loop** (see §4).

---

## 3. The evaluation landscape

### 3.1 WSI classification: saturated

- **The standard protocol:** the 10-fold Monte Carlo cross-validation
  (80/10/10 split by case) established by CLAM (Lu et al., Nat Biomed Eng 2021),
  with mean test AUC ± std plus the "data efficiency curve" paradigm
  (100/75/50/25/10% of the training set), still the de facto standard for
  weakly-supervised WSI classification.
  https://pmc.ncbi.nlm.nih.gov/articles/PMC8711640/
- **Current state:** NSCLC subtyping (LUAD vs LUSC, TCGA+CPTAC, 1,967 slides)
  reaches CLAM AUC 0.956 and 0.975 externally, with the Threads slide encoder at
  0.984 externally; RCC subtyping macro-AUC 0.991; CAMELYON16/17 lymph node
  metastasis detection AUC 0.953. **Subtyping AUC is 0.95+, so unless the
  evaluation dimension changes (few-shot, external generalization, interactive)
  there is no room to differentiate.**
  https://arxiv.org/html/2501.16652v1 ; https://arxiv.org/html/2505.20510v1

### 3.2 Mutation / biomarker prediction: headroom remains, competition is fierce per task

- **LUAD mutation:** the classic task set from Coudray et al. (Nat Med 2018)
  (STK11/EGFR/FAT1/SETBP1/KRAS/TP53), held-out AUC 0.733–0.856.
  https://pmc.ncbi.nlm.nih.gov/articles/PMC9847512/
- **EGFR (the clinical-grade benchmark):** EAGLE (Campanella et al., Nat Med
  2025), N=8,461 international multi-center, internal AUC 0.847, external 0.870,
  prospective silent trial 0.890, able to cut rapid molecular testing by 43% — a
  template for the clinical-translation narrative.
  https://pmc.ncbi.nlm.nih.gov/articles/PMC12443599/
- **MSI / HRD:** DeepSMILE TCGA-CRC AUROC 0.87, TCGA-BRCA HRD 0.81; Swin-T MCO
  4-fold 0.926, external 0.904.
  https://arxiv.org/html/2107.09405 ;
  https://pmc.ncbi.nlm.nih.gov/articles/PMC10073932/
- **TMB:** internal AUC 0.64–0.99, external validation generally dropping
  0.10–0.15; no unified protocol and non-comparable numbers, so not a suitable
  primary benchmark. https://www.mdpi.com/2076-3417/16/3/1340
- **Unified low-prevalence evaluation (the STAMP protocol, Nat Biomed Eng
  2025):** 31 tasks × 19 FMs; mean AUROC on low-prevalence tasks: Prov-GigaPath
  0.74 > Virchow 0.73 > CONCH 0.72; across all 31 tasks CONCH and Virchow2 tie
  for first at 0.71. https://www.nature.com/articles/s41551-025-01516-3
- **The FM-era baseline:** Prov-GigaPath reaches only 0.626 mean macro-AUROC on
  five LUAD gene mutations — FM embeddings plus simple MIL are far from saturated
  on low-prevalence mutations. https://www.mdpi.com/2073-4425/17/4/371

### 3.3 Survival prediction: protocol chaos

- 5-fold CV with inconsistent TCGA five-cancer combinations, and **splits differ
  across papers, making cross-comparison hard.** Pathology-only MIL typically
  reaches c-index 0.60–0.70; multimodal SOTA (DSCASurv/HySurvPred) ranges
  0.65–0.86. https://pmc.ncbi.nlm.nih.gov/articles/PMC11926988/ ;
  https://arxiv.org/html/2503.13862
- In STAMP's unified evaluation the 7 prognostic tasks average at best AUROC 0.63
  (CONCH) — **prognosis is the weakest FM dimension.**

### 3.4 Large unified evaluation suites (2024–2026)

- **PathBench (Ma et al., arXiv:2505.20202, 2025-05):** 15,888 WSIs / 8,549
  patients / 10 hospitals (private data to prevent leakage), 64+ tasks, 19 PFMs,
  an automated leaderboard. Leading: Virchow2 (rank 5.0) > H-optimus-1 (5.9) >
  H-optimus-0 (6.6) > UNI2 (7.1) > mSTAR (7.4); vision FMs still beat VLMs.
  Limitation: the data is private, so you can chase the board but cannot do
  methodology. https://arxiv.org/abs/2505.20202
- **eva (kaiko.ai, MIDL 2024):** an open-source framework plus leaderboard
  (patch-level BACH/CRC/MHIST/PCam, slide-level Camelyon16/PANDA, tile
  segmentation CoNSeP/MoNuSAC). Conclusion: **no model wins everywhere**, and
  pathology-pretrained FMs generally beat natural-image FMs.
  https://github.com/kaiko-ai/eva ; https://openreview.net/pdf?id=FNBQOPj18N
- **STAMP / NBE unified evaluation (El Nahhas et al., Nat Biomed Eng 2025):** 31
  weakly-supervised tasks, 19 FMs, TCGA → CPTAC/DACHS/Kiel external validation;
  includes an n=75/150/300 **scarce-data protocol** (PRISM/Virchow2/CONCH lead on
  small data) — currently the public evaluation closest to a standard few-shot
  adaptation protocol. https://www.nature.com/articles/s41551-025-01516-3
- **HEST / HEST-1k (Jaume et al., NeurIPS 2024):** spatial transcriptomics × H&E,
  9 tasks predicting the top-50 highly variable genes' expression from 112 μm
  patches (PCC, official split); PCC is generally 0.1–0.35, the frontier of
  molecular-morphological association. https://arxiv.org/html/2406.16192v2
- **PathBench (Sun et al., IEEE TMI 2025 — same name, different thing):**
  LMM-facing PatchVQA (5,382 images / 6,335 MCQs with shortcut-resistant
  distractors) plus WSICap (7,000 reports) and WSIVQA.
  https://pubmed.ncbi.nlm.nih.gov/40601458/

### 3.5 VQA / language benchmarks: credibility being rebuilt

| Benchmark | Scale | State (2026) | Source |
|---|---|---|---|
| PathVQA (2020) | 32,799 questions | Closed-form pushed to ~95%; a 2026 audit proves severe text prior (44–53% without looking at the image) → **credibility fallen** | https://ar5iv.labs.arxiv.org/html/2003.10286 ; https://arxiv.org/html/2607.21065v1 |
| PathMMU (ECCV 2024) | 33,428 MCQs, reviewed by 7 pathologists | GPT-4V only 49.8% versus humans 71.8%; CPathAgent 78.6–80.5% (already above the human baseline) | https://arxiv.org/abs/2401.16355 |
| SlideBench / WSI-VQA | 734 captions + 15K VQA | SlideChat SOTA on 18 of 22 tasks; largely multiple-choice-ified, and genuinely open-ended WSI understanding remains a gap | https://arxiv.org/html/2410.11761 |
| PathMMU-HR² | 1,688 expert-validated multi-scale VQA | CPathAgent 88.6%, Gemini-2.5-Pro 76.4 | https://arxiv.org/html/2505.20510v1 |
| PathView-Bench (2026-07) | Multi-scale fine-grained MLLM evaluation | Very recent, landscape unsettled | https://arxiv.org/html/2607.28318v1 |

**Gaps:** (a) trustworthy automatic evaluation of WSI-level open-ended QA and
reporting (currently dependent on GPT-score); (b) older benchmarks without a
"visual evidence necessity" check are losing credibility; (c) **no language
benchmark evaluates the human–agent interaction process at all.**

### 3.6 Active learning / annotation efficiency evaluation: no unified benchmark, four composable protocols

1. **The CLAM data-efficiency curve:** test AUC at 100/75/50/25/10% of the
   training set under 10-fold CV, plus the external BWH cohort — standard
   equipment in every weakly-supervised WSI paper.
2. **The STAMP scarce-data protocol:** n=300/150/75 patient subsampling plus full
   external validation, measuring an FM's label efficiency.
3. **Segmentation active learning (SHAL, arXiv:2607.09831, 2026-07):**
   slide-level AL on TCGA-CRC — Dice ≥ 0.80 at 26% annotation budget (the
   baseline needs 37%), macro Dice 0.846 at full budget, with a generalization
   gap across 5 external cohorts. Measurement paradigm: a performance versus
   annotation-budget curve plus the budget fraction needed to reach a threshold.
   https://arxiv.org/abs/2607.09831
4. **Interactive segmentation click metrics:** NoC@85 / NoC@90, 1-click IoU,
   K-NoC@90 (the SimpleClick/PseudoClick standards).
   https://pmc.ncbi.nlm.nih.gov/articles/PMC11378330/

**The gap is the opportunity:** "**performance versus human interaction volume
(ROI selections / clicks / minutes)**" has no standard protocol at WSI level —
and HE-Scope records every human-agent interaction natively, so it can define and
claim that metric.

### 3.7 Agentic / interactive evaluation: just beginning, with very low top scores

#### 3.7.1 Pathology-specific

- **PathAgentBench (arXiv:2607.19261, 2026-07, NUS + PuzzleLogic + PUMCH) — the
  most important reference point.** 1,822 TCGA WSIs plus 17,135
  pathologist-annotated "diagnostic paths" (2.5× → 10× → 40× three-level nested
  bboxes with findings and diagnosis) across 16 organs, plus 190 private breast
  WSIs. Four tasks:
  - T1 evidence interpretation (image → text, 10,284 MCQs): strongest
    Gemini-3-Flash 63.5%, experts 93.6%.
  - T2 evidence verification (text → image, 10,284 MCQs): strongest 67.7%.
  - **T3 evidence acquisition (agent navigation): total collapse** — the
    strongest text-guided localization reaches mIoU < 0.09 and is beaten 3–4× by
    a "center point of the parent box" heuristic (IoU 0.25–0.28); unconditional
    autonomous exploration hit rate falls 52.2% → 18.5% → **2.0%** (2.5× → 40×);
    pathology-specific models cannot even emit a valid bbox tool call.
  - T4 evidence integration (51,167 MCQs): strongest ~93%, near saturation.
  - The official conclusion: **current VLMs are good evidence scorers and poor
    autonomous planners; evidence acquisition is the bottleneck of the entire
    agent paradigm.** https://arxiv.org/html/2607.19261v1
- **HealthAgentBench:** includes interactive WSI exploration, but pathology gets
  only one tumor localization task.
- Pathology agent systems (CPathAgent, PathAgent, TissueLab, MMNavAgent,
  LAMMI-pathology and others) evaluate on their own terms and cannot be compared
  — **the standardization window is opening.**

#### 3.7.2 Transferable from adjacent fields

| Benchmark | Task | Top score | What to borrow |
|---|---|---|---|
| ScienceAgentBench (2024) | 102 data-driven discovery tasks | Claude-3.5-Sonnet self-debug SR 34.3%; o1 42.2%; expert knowledge +13.7 SR | The "expert-provided knowledge" dimension ≈ human guidance in a human-agent loop |
| MLE-bench (OpenAI, ICLR 2025) | 75 Kaggle competitions | o1-preview+AIDE medals 16.9% (pass@1) → 34.1% (pass@8) | The human leaderboard as a natural baseline; resource-performance scaling |
| MLGym (Meta, 2025) | 13 ML research tasks | The AUP metric | A performance profile relative to the best performer |
| MLAgentBench | 13 tasks | ReAct+Claude Opus 37.5% SR | File/code interaction environment design |

Sources: https://arxiv.org/html/2410.05080v2 ; https://arxiv.org/abs/2410.07095 ;
https://arxiv.org/html/2506.08800v2

**Conclusion:** pathology agent evaluation has just begun and T3 is close to a
total wipeout, which is exactly where the human-agent loop paradigm can deliver
an order-of-magnitude improvement; and ScienceAgentBench has already shown that
"expert knowledge injection" is an effective and community-accepted evaluation
dimension.

### 3.8 Maturity matrix (one-page summary)

| Evaluation layer | Representative benchmark | Maturity | Top level | Gap / opportunity |
|---|---|---|---|---|
| WSI subtyping | The CLAM protocol, CAMELYON | ★★★★★ saturated | AUC 0.95+ | None (unless the dimension changes) |
| Mutation / biomarker | STAMP's 31 tasks, EAGLE | ★★★☆ | Low-prevalence AUROC ~0.63–0.74 | Room remains in few-shot / low prevalence |
| Survival prediction | Splits differ per group | ★★☆ chaotic | c-index 0.60–0.70 | Protocols not unified; unsuitable as a main thrust |
| Unified FM evaluation | PathBench/eva/STAMP/HEST | ★★★★ | Top gap under 2% | Platform baseline only, not a main thrust |
| VQA | PathMMU/SlideBench | ★★★★ (multiple-choice-ified) | Specialized models already above the human baseline | Open-ended WSI QA, correcting the text prior |
| Annotation efficiency | No unified benchmark | ★★ fragmented | — | **The "performance vs human interaction budget" protocol is a gap** |
| Agentic evidence acquisition | PathAgentBench T3 | ★ just beginning | mIoU < 0.09, hit rate 2% | **The human-in-the-loop variant is a gap with order-of-magnitude headroom** |
| Human-agent interaction process | None | 0 | — | **Completely open; the first definer benefits** |

---

## 4. HE-Scope's positioning and novelty analysis

### 4.1 Literature coordinates: the intersection of four subfields

HE-Scope's core loop (human circles ROI → agent analyzes → annotation persisted →
weakly-supervised training → re-annotation) sits at the intersection of four
subfields (R4 §7.1):

- **A. Active / interactive learning** (§2.1): the human supplies annotations and
  the system picks the most valuable samples — mature, but driven by model
  uncertainty rather than an LLM agent.
- **B. Human-AI collaborative diagnosis** (§2.2): AI assists the human — mature,
  but generally advisory collaboration with a static model and no data return
  path.
- **C. Interactive segmentation** (§2.3): the human clicks or circles to correct
  — mature, but the interaction serves only the current segmentation and never
  accumulates as a reusable annotation asset driving global retraining.
- **D. Pathology LLM agents** (§1, §2.4.2): agents analyze WSIs — exploding in
  2025–2026, but almost all training-free, session-level, and **with no
  closed-loop learning.**

**Integrating the whole chain — human-initiated selection + agent analysis +
structured persistence + weakly-supervised retraining + re-annotation — is the
gap.**

### 4.2 Positioning argument: "a database-centric human–agent closed-loop WSI analysis system"

The three strongest supporting pieces of evidence (all facts from §1–§3):

1. **Route endorsement.** SPARK (Nature Medicine 2026) shows an LLM agent writing
   code and calling tools for quantitative pathology analysis beats end-to-end
   VLM answering, and it supports interaction with pathologists (human proposes
   the concept, agent implements it) — HE-Scope's code-agent bridge positioning
   matches the field's strongest endorsement.
   https://www.nature.com/articles/s41591-026-04357-y
2. **Bottleneck fit.** PathAgentBench (2026-07) shows the pure-VLM agent
   bottleneck is **evidence acquisition** (mIoU < 0.09, 2.0% hit rate at 40×)
   rather than reasoning (T4 already ~93%) — "human circles the ROI" is precisely
   the paradigm for closing the planner gap with minimal human interaction; and
   ScienceAgentBench has already shown "expert knowledge injection" is a
   community-accepted evaluation dimension. https://arxiv.org/html/2607.19261v1
3. **Exclusive data flywheel.** Pathology-CoT shows viewer behavior logs can
   become high-value agent training data (6× annotation speedup), while the data
   flywheel has barely been implemented systematically in the computational
   pathology literature (§2.5) — a viewing platform natively owns that data entry
   point, an endowment a pure model product cannot replicate.
   https://arxiv.org/abs/2510.04587

Differentiation space (observed gap, not inference):

1. **A loop with the database as a first-class citizen.** nuclei.io's and
   TissueLab's feedback is session-level; HE-Scope structurally accumulates every
   human selection, agent analysis and confirmation/correction into a
   **queryable, versionable, cross-task-reusable annotation database** (ROI
   coordinates × morphological description × label × interaction history). The
   literature contains no systematic work or public benchmark for "a pathology
   interactive annotation database + a continual retraining protocol" (the
   Label-Efficient survey explicitly notes the missing benchmark, §2.1.3).
2. **Human ROI selection as a unified interaction primitive.** Unlike nuclei.io's
   "system lists candidates, human clicks to confirm" (AI-led) or PathChat 2's
   "circle then ask" (no learning), HE-Scope is "**human initiates the selection
   → agent analyzes and returns a correctable result → confirmation persists
   it**" — human-led initiation, agent-led analysis, database-accumulated
   consensus, with the structural advantage of being anti-automation-bias
   (§2.2.4).
3. **A weakly-supervised retraining protocol.** A human-circled ROI is natively a
   weak annotation between point, box and scribble (supported by the literature
   in §2.1.4 and §2.3), enabling a new evaluation protocol — the
   "selection budget vs downstream performance" curve, distinct from AL's sample
   budget protocol.
4. **marimo as a reproducible computational vehicle.** Interactive analysis and
   the executable notebook share one environment, so the annotation process is
   itself reproducible — an engineering differentiator (a bonus for the paper
   rather than the main claim; echoing PathLab's "configuration as asset" idea,
   §1.3.2).

### 4.3 Draft novelty claim

**Main claim:** "**the first database-centric human–agent closed-loop pathology
analysis system**" (a human-initiated, agent-analyzed, database-accumulated,
weakly-supervised-retrained closed loop for WSI analysis), supported by three
separately submittable contributions:

1. **A systems paper** (Nat Biomed Eng / Nat Commun / Med Image Anal style): the
   loop architecture, two crossover user studies (matching nuclei.io: 6–8
   pathologists each across 2 tasks, MRMC crossover with a 2–4 week washout, the
   §2.2.2 paradigm), and closed-loop learning curves (downstream task performance
   and annotation cost as a function of interaction rounds).
2. **A methods contribution:** the selection-driven weakly-supervised retraining
   protocol plus the "selection budget vs performance curve" evaluation paradigm,
   compared against random/uncertainty/coreset active learning baselines,
   quantifying annotation savings (against the 5% and 2% magnitudes in §2.1.1 and
   SHAL's 26% versus 37%, §3.6).
3. **A benchmark/data contribution:** the interactive annotation database plus a
   closed-loop learning benchmark (answering the standardization gap the
   Label-Efficient survey identifies) — the highest-citation angle. It can also
   define a human-in-the-loop variant of PathAgentBench: allow k human ROI
   interactions (k ∈ {0,1,3,5}) and report T3 localization mIoU / hit rate and
   downstream joint T1+T4 diagnostic accuracy against k, with the expectation
   that k=1 alone lifts the 40× hit rate from 2% to near expert-path coverage —
   a dual contribution of "order-of-magnitude improvement + a new protocol"
   (§3.7).

**Control experiments that must be run** (otherwise reviewers will reject on
TissueLab/nuclei.io grounds):

- (a) **An ablation against TissueLab-style "session feedback not persisted"**:
  show that database accumulation delivers cross-session and cross-task gains
  (the downstream performance difference between persist-and-reuse and
  session-level feedback at equal annotation budget).
- (b) **Automation-bias measurement:** report adoption and correction rates for
  wrong agent output, demonstrating the safety of the human-initiated structure
  (against PulmoFoundation's 77.5% warning figure, §2.2.3; this is the
  credibility red line for 2026 reader studies).
- (c) **Closed loop versus one-shot active learning** annotation efficiency (the
  learning-curve difference at equal budget).

### 4.4 Risks and mitigations

| Risk | Description | Mitigation |
|---|---|---|
| **The TissueLab threat (highest)** | The greatest overlap with HE-Scope (the same group evolved from nuclei.io, already holding the "co-evolving agentic + expert feedback + active learning" narrative); if it publishes formally in a high-tier journal and open-sources its continual-learning module, the window narrows | Turn the three points — database loop, selection interaction protocol, benchmark — into differentiating evidence they cannot cover, as fast as possible; run the §4.3 (a) ablation explicitly in every paper |
| **PathAgentBench data availability unconfirmed** | The benchmark is very new (2026-07) and its GitHub data (1,822 WSIs / 17,135 paths / the 50-slide Mode A subset) must be confirmed released | Check before committing; if unreleased, reproduce the T3 protocol on a self-built subset first |
| Collaboration gain is not necessarily positive | The Vaccaro meta-analysis: decision tasks often show negative synergy (§2.2.4) | Keep final confirmation authority with the human in the interface design; pre-register the crossover design; report adoption-rate decomposition |
| Evaluation challenged as gameable | The Lancet commentary: LLM evaluation of agentic workflows is still preliminary | Follow the CONSORT-AI / STARD-AI / CLAIM reporting standards (annotator qualifications, annotation tools, de-identification item by item); fixed budgets and cost-aware metrics |
| Unreliable VLM components | *Do Pathology VLMs Truly See Pathology?* questions fine-grained morphological understanding; SlideSeek's hallucinated IHC | Build in modality legality constraints, tool-reliability tracking (the PathoSage pattern), and coordinate-level evidence chains |

### 4.5 Supporting FM selection (serving the loop; details in R2)

The loop's weakly-supervised retraining and its retrieval/localization tools
depend on a tile/slide embedding FM. Key points (R2):

- The leading models (Virchow2, H-optimus-1/0, UNI2, Prov-GigaPath, GPFM) differ
  by under 2% on routine tasks, so **selection should weigh license, inference
  cost and ecosystem instead.**
- Platform first choices: **GPFM** (MIT, mean rank 1.6 across 72 tasks, distilled
  from UNI+Phikon+CONCH) or **H-optimus-0** (Apache 2.0, first on both counts in
  an independent clinical benchmark); UNI2-h (CC-BY-NC-ND) is an option for
  academic positioning. Slide-level upgrade path: TITAN (non-commercial) or
  GigaPath-Flash (Apache 2.0).
- Prefer CONCH-style contrastive VLMs for retrieval/localization tools (§1.1.3);
  TRIDENT is recommended as the unified integration layer.
  https://github.com/mahmoodlab/TRIDENT
- Risks: licensing (CC-BY-NC-ND forbids commercial use), gated approval,
  timm/transformers version fragmentation, and magnification/preprocessing
  consistency.

### 4.6 The positioning in one sentence

HE-Scope sits at the intersection of three lines — active-learning annotation
(the Menon/nuclei.io lineage) × pathology LLM agents (the PathAgent/TissueLab
lineage) × interactive segmentation (the NuClick/PathoSAM lineage). The
representative work on each line is mature, but **the intersection — especially a
continual, database-centric loop — remains a defensible gap as of 2026-08**. The
key to novelty holding up is an explicit differentiation experiment against
TissueLab plus quantitative evidence from closed-loop learning curves.

---

## Appendix A: quick reference of core citations

### Pathology AI agent systems

| System | Source | URL |
|---|---|---|
| PathChat | Nature 2024 | https://www.nature.com/articles/s41586-024-07618-3 |
| SlideSeek / PathChat+ | arXiv:2506.20964 | https://arxiv.org/html/2506.20964v2 |
| PathChat 2 / DX / Judith / Modella | Official site | https://www.modella.ai/pathchat |
| AstraZeneca acquires Modella | 2026-01 | https://www.biopharmatrend.com/news/astrazeneca-acquires-modella-ai-to-integrate-foundation-models-into-global-oncology-rd-1463/ |
| SlideChat / SlideBench | CVPR 2025 | https://arxiv.org/html/2410.11761 |
| CONCH | Nat Med 2024 | https://www.nature.com/articles/s41591-024-02856-4 |
| PathFinder | arXiv:2502.08916 | https://arxiv.org/abs/2502.08916 |
| CPathAgent / PathMMU-HR² | arXiv:2505.20510 | https://arxiv.org/abs/2505.20510 |
| Pathology-CoT / o3 | arXiv:2510.04587 | https://arxiv.org/abs/2510.04587 |
| SmartPath-R1 | arXiv:2507.17303 | https://arxiv.org/abs/2507.17303 |
| TeamPath | arXiv:2511.17652 | https://arxiv.org/html/2511.17652v1 |
| Patho-AgenticRAG | arXiv:2508.02258 | https://arxiv.org/html/2508.02258v1 |
| PathoSage | arXiv:2606.07549 | https://arxiv.org/abs/2606.07549 |
| PathNavigate | arXiv:2605.23559 | https://arxiv.org/abs/2605.23559 |
| SPARK | Nat Med 2026 | https://www.nature.com/articles/s41591-026-04357-y |
| PathLab | arXiv:2606.20677 | https://arxiv.org/html/2606.20677 |
| SAGE | arXiv:2602.00953 | https://arxiv.org/html/2602.00953v1 |
| MMedAgent | EMNLP 2024 | https://arxiv.org/html/2407.02483v2 |
| PathAgent | arXiv:2511.17052 | https://arxiv.org/abs/2511.17052 |
| Pathology agent list | GitHub | https://github.com/Nanboy-Ronan/awesome-medical-imaging-agents |

### Human-AI collaboration / active learning

| Work | Source | URL |
|---|---|---|
| nuclei.io | Nat Biomed Eng 2024 | https://pubmed.ncbi.nlm.nih.gov/38898173/ |
| TissueLab | arXiv:2509.20279 | https://arxiv.org/abs/2509.20279 |
| Menon interactive learning | ICPR 2022 | https://cvit.iiit.ac.in/images/ConferencePapers/2021/Interactive_Learning.pdf |
| MyriadAL | arXiv:2310.16161 | https://arxiv.org/abs/2310.16161 |
| AL + Attention MIL | ISBI 2023 | https://ui.adsabs.harvard.edu/abs/arXiv:2303.01342 |
| Label-Efficient MIA survey | arXiv:2303.12484 | https://arxiv.org/html/2303.12484v5 |
| Steiner LYNA | AJSP 2018 | https://pubmed.ncbi.nlm.nih.gov/30312179/ |
| PulmoFoundation RCT | arXiv:2605.25878 | https://arxiv.org/html/2605.25878v2 |
| GRACE | arXiv:2606.04792 | https://arxiv.org/abs/2606.04792 |
| BRAVE | arXiv:2605.08207 | https://arxiv.org/html/2605.08207v1 |
| Vaccaro meta-analysis | Nat Hum Behav 2024 | https://arxiv.org/pdf/2507.19486 |
| HCT | arXiv:2603.29866 | https://arxiv.org/html/2603.29866v1 |
| NuClick | MedIA 2020 | https://pubmed.ncbi.nlm.nih.gov/32769053/ |
| Clore | arXiv:2603.27625 | https://arxiv.org/html/2603.27625v1 |
| PathoSAM | arXiv:2502.00408 | https://arxiv.org/html/2502.00408v2 |
| ActiveLLM | arXiv:2405.10808 | https://arxiv.org/abs/2405.10808 |
| LLM-AL survey | ACL 2025 | https://aclanthology.org/2025.acl-long.708.pdf |
| Medical AI drift | arXiv:2506.17442 | https://arxiv.org/html/2506.17442v1 |
| NVIDIA data flywheel | Official site | https://www.nvidia.com/en-us/glossary/data-flywheel/ |
| CONSORT-AI / reporting standards | PMC | https://pmc.ncbi.nlm.nih.gov/articles/PMC8183333/ |

### Benchmarks

| Benchmark | Source | URL |
|---|---|---|
| CLAM | Nat Biomed Eng 2021 | https://pmc.ncbi.nlm.nih.gov/articles/PMC8711640/ |
| Coudray DeepPATH | Nat Med 2018 | https://pmc.ncbi.nlm.nih.gov/articles/PMC9847512/ |
| EAGLE | Nat Med 2025 | https://pmc.ncbi.nlm.nih.gov/articles/PMC12443599/ |
| STAMP / NBE 31 tasks | Nat Biomed Eng 2025 | https://www.nature.com/articles/s41551-025-01516-3 |
| PathBench (Ma) | arXiv:2505.20202 | https://arxiv.org/abs/2505.20202 |
| PathBench (Sun, TMI) | IEEE TMI 2025 | https://pubmed.ncbi.nlm.nih.gov/40601458/ |
| eva | MIDL 2024 | https://github.com/kaiko-ai/eva |
| HEST-1k | NeurIPS 2024 | https://arxiv.org/html/2406.16192v2 |
| PathMMU | ECCV 2024 | https://arxiv.org/abs/2401.16355 |
| PathVQA text-prior audit | arXiv:2607.21065 | https://arxiv.org/html/2607.21065v1 |
| PathAgentBench | arXiv:2607.19261 | https://arxiv.org/html/2607.19261v1 |
| PathView-Bench | arXiv:2607.28318 | https://arxiv.org/html/2607.28318v1 |
| SHAL | arXiv:2607.09831 | https://arxiv.org/abs/2607.09831 |
| ScienceAgentBench | arXiv:2410.05080 | https://arxiv.org/html/2410.05080v2 |
| MLE-bench | arXiv:2410.07095 | https://arxiv.org/abs/2410.07095 |

### FM selection (supporting; details in R2)

GPFM (https://bio.rodeo/models/gpfm), UNI (https://github.com/mahmoodlab/UNI),
CONCH (https://github.com/mahmoodlab/CONCH), Virchow2
(https://www.paige.ai/foundation-models), H-optimus
(https://www.bioptimus.com/h-optimus), Prov-GigaPath
(https://github.com/prov-gigapath/prov-gigapath), TITAN
(https://github.com/mahmoodlab/TITAN), and TRIDENT as the unified integration
layer (https://github.com/mahmoodlab/TRIDENT).

---

*This document synthesizes research reports R1–R4. When adding literature or
updating figures, update the corresponding section and the appendix quick
reference together, and keep the convention that every fact carries a URL.*
