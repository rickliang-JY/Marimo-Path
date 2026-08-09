# HE-Scope 战略路线存档

[English](ROADMAP.md) · **简体中文**

> 存档日期：2026-08-08
> 状态：战略讨论结论汇总，尚未开工。下一步是先整体检验现有能力，再决定从哪一项开始。

---

## 1. 背景：HE-Scope 现状

marimo 病理 H&E 全切片图像观测平台（`/mnt/agents/output/project`，master @ b802f24，208 tests 全绿）：

- 统一 plotly viewer（缩放/平移/圈选一体）+ sidebar + 单 Navigator 缩略图；
- ROI 闭环：圈选 → level-0 坐标映射 → 原图重裁 patch（非截图）→ DB 持久化 → agent payload；
- agent 桥（marimo-pair，需 `marimo edit` 模式）：`get_current_selection()`（零点击）、`get_latest_selection()`、`get_analysis_capabilities()`；
- TCGA/GDC 接入：搜索、并行分块下载（断点续传、md5 校验）、catalog；
- 分析栈（借鉴 slideflow）：Macenko/Reinhard 染色归一、细胞核检测、QC、56 维特征、可选 ResNet18 embedding、heatmap、LogisticRegression 弱监督训练；
- DB：SQLAlchemy（slides / rois / agent_runs），可降级 DB-free 模式。

---

## 2. 核心战略判断

**不打静态基础模型榜。** 2025–2026 病理基础模型榜（CONCH、Virchow2、Prov-GigaPath、UNI2-h、H-optimus-1 等）由 10 万~310 万张切片、6 亿~11 亿参数的模型霸榜，胜负手是数据和算力，我们没有入场券，也不应自研冲榜。

**HE-Scope 的护城河是「人-agent-数据库闭环」**：人圈 ROI → agent 读坐标+原图 patch → 分析写回 DB → 标注变训练数据。这个 loop 是静态 benchmark 测不出的资产。

**借势而非自研**：CONCH/UNI 等模型开放了 tile embedding（HuggingFace），直接接入即可把感知端从 ResNet18 升到准 SOTA 水准，零训练成本。

---

## 3. 三阶段路线

### Phase A — 感知升级（性价比最高，建议最先做）
- 接入 UNI 或 CONCH tile embedding 作为 backend，替换/并存现有 ResNet18；
- 现有 56 维特征 + LogisticRegression 训练管线、heatmap 管线全部复用，只换特征提取器；
- 预期：few-shot 标注训练从"能演示"升到线性探测 AUROC 0.95+ 档次；
- **GPU 来源：molab（见 §5），本地无 GPU 不再是阻塞项。**

### Phase B — agent loop 正式化 + LoopX 接入（见 §4）
- 工具补全：agent 可写回标注、触发训练、查 DB 历史、请求人工复核；
- 每次交互（人圈选→agent 分析→人纠正→再训练）作为 trajectory 落库 = 主动学习完整日志；
- 定义 3~5 个标准任务 schema（如「TCGA-BRCA 上找肿瘤区并出密度热图」「ROI 分类置信度低于阈值请求人工」）；
- 「标注→训练→评估→重标注」循环建模为 LoopX objective。

### Phase C — 打"我们能赢"的 benchmark
不打静态榜（必输），打交互式/Agentic 评测（空白区）：
- **标注效率曲线**：达到同等 AUROC 所需人工点击数，有 loop vs 无 loop 对比（主动学习标准度量，系统天然产生此数据）；
- **Agent 任务成功率**：TCGA 有 ground truth 的任务上，人+agent 协作 vs 纯 agent vs 纯模型三方对比；
- 静态榜仅用于证明 parity（CONCH embedding 跑标准任务，证明感知端不拖后腿）；
- 最终产出目标：human-agent collaborative pathology analysis 方向的系统论文 + 可持续积累的数据飞轮平台。

---

## 4. LoopX 评估结论

仓库：https://github.com/huangruiteng/loopx

**是什么**：长程 agent 工作的轻量状态内核 + 本地优先控制面。durable objective、gates、可执行 todos、evidence log、quota 感知自动唤醒；agent 无头（peer 模式），跨 Codex / Claude Code / Cursor 保持状态连续。不管"活怎么干"，只管"loop 的治理"。

**与 HE-Scope 的映射**：

| LoopX 概念 | HE-Scope 场景对应物 |
|---|---|
| durable objective | 跨天的标注-训练 campaign（如「20 张 TCGA-BRCA 标 200 个 ROI，训出 AUROC≥0.9」） |
| gates（人类判断） | 病理医生复核标注后才允许训练；结果不达标不晋级 |
| executable todos | 标注批次、下载批次、训练、评估、重训练 |
| evidence log | DB 的 agent_runs + ROI 标注历史 + patch 路径（已有一半） |
| quota-aware auto-wake | 主动学习中"不确定度最高的 N 个 patch 才叫醒人/agent" |
| peer agents 无主从 | Kimi Code / Claude Code / Codex 轮流推进同一 campaign |

**定位**：HE-Scope 已有「领域状态」（三张表），缺「跨会话、跨 agent、跨天的 campaign 治理」——LoopX 恰好补这层。

**集成原则**：
- **薄适配、可摘除**：核心状态留在自己的 DB，LoopX 只当 campaign 控制面；evidence 指向 DB 的 roi_id/run_id，gate 挂在训练管线的达标检查上；
- **不进感知/分析链路**：LoopX 对病理图像零理解，提升的是长程实验的可复盘性与推进效率，不直接提升任何 AUROC；
- **风险**：单作者早期项目，成熟度与维护持续性未知——先做 spike 实证再决定。

**Spike 计划**（未开工）：克隆 LoopX、跑通 quickstart、把最小 campaign（demo slide 10 个 ROI 两轮主动学习）建模为 LoopX objective，验证适配层可行性，出"接/不接"实证结论。

---

## 5. molab 评估结论（GPU 来源）

官网：https://molab.marimo.io （marimo 官方托管云平台）

**关键事实**：
- 免费；默认 4 CPU + 32GB RAM；
- **可挂 NVIDIA RTX Pro 6000 Blackwell GPU（96GB VRAM）**，app 头部 notebook specs 按钮开关（CoreWeave -backed）；
- 预装 torch 等 ML 包，秒级启动；
- 限制：单 session 最长 12 小时、空闲 90 分钟关闭、每 notebook 限量持久存储；
- GitHub 集成，GitHub 为 source of truth。

**接本地 agent：官方支持。** molab notebook 右上 actions → "Pair with an agent" → 本地 agent（装了 marimo-pair skill 的 Claude Code / Codex / Kimi Code 等）执行连接指令后，所有代码在 molab 沙箱内核执行，与本地 pair 体验一致。HE-Scope 的全部 agent 接口原样可用。

**适配要点（三个坑）**：
1. 包结构：molab 以单 notebook 为中心，`hescope/` 多文件包需推 GitHub 后 `pip install git+https://...`，或用 GitHub mirror 机制；
2. 存储：TCGA SVS 单文件 100MB–2GB，molab 持久存储有限——大切片少量用 / 走 GCS mirror 路线 / molab 上只跑 demo + 小样本；
3. 12 小时上限：长下载长训练切可续跑段（下载器已有 .part 断点逻辑）。

**意义**：Phase A 的 GPU 阻塞解除——CONCH/UNI embedding 提取和线性探测训练可直接在 molab 96GB GPU 上跑，agent 留在本地。

---

## 6. 下一步（2026-08-08 夜：已由过夜研究取代本节原候选清单）

过夜研究蜂群（6 路并行调研，原始报告在 `/mnt/agents/output/research/`）已产出两份核心文档，本节候选清单作废，以它们为准：

- **PAPERS.md**——文献综述：病理 AI agent 四主线、人机协作/主动学习、评测格局、HE-Scope 定位与 novelty 分析（146 处引用）；
- **STRATEGY.md**——战略决策：学术三目标（A：PathAgentBench human-in-the-loop 变体；B：「AUROC vs 人类交互预算」标注效率协议；C：eva+HEST parity 背书）、开源六维度迭代路线（P0–P2 优先级）、LoopX 混合路线决策（不接为依赖，自研 DB 薄 loop 层 + he-scope-loop SKILL.md）、7 个双周冲刺里程碑表（至 2026-11-08）。

**核心决策速览**：
1. 学术定位：「首个以标注数据库为中心的人-agent 闭环 WSI 分析系统」；最大 novelty 威胁是 TissueLab，必须做会话反馈入库的消融对照；
2. FM 选型：**GPFM（MIT）默认** / UNI2-h 仅学术 / H-optimus-0（Apache）商用备选；CC-BY-NC-ND 模型（UNI/CONCH/TITAN 等）立红线不进默认路径；经 encoder factory 统一接入；
3. LoopX：不接依赖（文件态 vs DB 双事实源冲突、2.5 个月龄 v0.4.x 快速 breaking）；自研薄 loop 层，每 6 周跟踪其 pluggable-state-provider RFC 复评；
4. 开源空位：「marimo-native + agent-native」双重空位 + 「可复现的观测（reproducible viewing）」叙事无人占据；与 Trident(.h5)/QuPath(GeoJSON) 共生而非竞争；尽早 JOSS 绑引用。

**过夜已实施项**见 git log（master）与 STRATEGY.md §4 W1–2 栏。

---

## 7. 暂缓事项（早前已确认推迟）

- BigQuery cohort 搜索（ISB-CGC）；
- GCS mirror 下载 / Google Cloud storage 集成；
- marimo 其他 cloud/数据库结合（待 storage 优化阶段统一探究）。
