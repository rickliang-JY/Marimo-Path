# HE-Scope 战略决策文档（STRATEGY）

[English](STRATEGY.md) · **简体中文**

> 日期：2026-08-08 ｜ 依据：六份调研报告（r1 病理 agent、r2 foundation model、r3 benchmark、r4 human-in-the-loop、r5 开源生态、r6 LoopX/loop engineering）+ HE-Scope 现状（ROADMAP §1–2、`../AGENTS.md`）。
> 本文档是决策而非综述：每条结论有明确立场、理由和排除项。引用数字均出自调研文件所附的一手来源。

---

## 0. 一句话战略

**HE-Scope 不打静态基础模型榜，也不做通用病理 agent；做「人圈选 ROI 发起、agent 分析、标注数据库沉淀、弱监督再训练」这一闭环品类的定义者与首个开源参考实现。** 学术上用「人-agent 交互」这一别人无法复制的维度重写两个评测协议（PathAgentBench T3 变体、AUROC-vs-交互预算曲线）；工程上把「agent-native 病理观测器」这一空位占住；loop engineering 走自研薄层、不接 LoopX。

理由（调研支撑）：
- PathAgentBench（2026-07）证明全领域瓶颈在**证据获取**而非推理：文本引导定位最强 mIoU<0.09，被「父框中心点」启发式 3–4 倍吊打，40× 自主探索命中率仅 2.0%——而「人圈 ROI 补上 planner 短板」正是 HE-Scope 的原生交互（r1 §20、r3 §6.1）。
- SPARK（Nature Medicine 2026）证明「agent 写可执行代码调用经典分析」优于「VLM 端到端推理」，是 code-agent 桥接路线的最强背书（r1 §13）。
- r4 系统检索结论：「agent 分析 + 人圈选 + 结构化入库 + 再训练」全链整合是 2026-08 时点仍可主张的空白；唯一高重叠竞品 TissueLab 的反馈是会话级、非数据库中心（r4 §7）。
- r5 确认「marimo-native × agent-native」双原生在两侧生态均无占位者，Trident 随仓库发 SKILL.md 验证了 agent-native 分发形态且成本极低（r5 §5–6）。

**为什么是现在（窗口期判断）**：
1. **评测窗口**：PathAgentBench 发布于 2026-07，agentic 病理评测「刚起步且头部分数极低、标准化窗口正在打开」（r3 §6.1）；半年后必有跟进者定义 HITL 变体，先定义者收引用。
2. **竞品窗口**：TissueLab 尚未正式发表于高水平期刊、未开源持续学习模块；r4 §7.4 明确建议「尽快把数据库闭环 + 圈选协议 + benchmark 做成不可被其覆盖的差异化证据」。
3. **生态窗口**：Trident 是目前唯一随仓库发 agent skill 的病理项目，且只做 CLI 层；「agent 直接驱动 viewer」尚无人做（r5 §7.2），先发成本仅 1 人日量级。
4. **反面纪律**：窗口不意味着铺摊子——本文档的排除项（§1 末）与「不接 LoopX」（§3）同样是窗口期决策：把全部火力压在三目标 + 六维度 P0/P1 上。

**与既有文档的关系**：本文档取代 ROADMAP §3–4 中的两项结论——① ROADMAP Phase B 的「标注→训练循环建模为 LoopX objective」与 §4 的 LoopX spike 计划，经 r6 精读后被 §3 的自研薄 loop 层决策替代（spike 不必再做，证据已在调研阶段闭环）；② ROADMAP Phase A 的「接 UNI 或 CONCH」被 §2f 修正为「GPFM 默认 + UNI2-h 仅学术对照」，原因是 license 红线（UNI/CONCH 均为 CC-BY-NC-ND，平台分发不可用）。ROADMAP 的 Phase C（打交互式 benchmark）与 molab GPU 结论仍然有效，已分别并入 §1 三目标与 §4 资源假设。

---

## 1. 学术目标决策

采用三目标框架：**A（主攻，差异化最大）、B（主攻，机制沉淀）、C（跟进背书）**。A 负责「新协议 + 数量级改进」的冲击力，B 负责把闭环机制变成可引用的标准化贡献，C 负责证明感知端不拖后腿。三者共用同一条技术栈（FM embedding + ABMIL + 交互日志），互相复用实验基建。

**论文组合策略**：A 与 B 最终汇流为一篇主系统论文（human-initiated, agent-analyzed, database-accumulated, weakly-supervised-retrained 闭环，对标 nuclei.io 的 Nat Biomed Eng 形态，r4 §7.4），但发表顺序上 A 的协议短文先行——它投入最小（1–2 人月）、数据现成、不需要真人大规模实验即可成稿，先把「HITL 证据获取协议」的定义权占住；B 的全曲线 + 消融 + crossover 用户研究作为主系统论文的主体随后。C 永不独立成文，只作 baseline 章节与工程背书。

### 目标 A：PathAgentBench human-in-the-loop 变体 —— k 次人圈 ROI 下的定位/诊断曲线

**立场：主攻，第一篇论文就做它。**

- **假设**：纯 VLM agent 在证据获取上系统性崩坏（T3：定位 mIoU<0.09、40× 命中率 2.0%、52.2%→18.5%→2.0% 的三级衰减；病理专用模型甚至发不出合法 bbox 工具调用）。允许 k 次人类 ROI 交互（k∈{0,1,3,5}）后，「人圈 ROI → agent 读 level-0 坐标+原图 patch → 分析写回」能用极少人类预算把 acquisition 短板补齐：预期 k=1 即可把 40× 证据命中率从 2% 拉到接近专家诊断路径覆盖率，T1+T4 联合诊断准确率随 k 单调上升并快速饱和。
- **实验设计**：
  1. 复用 PathAgentBench 公开的 1,822 张 TCGA WSI + 17,135 条病理医师诊断路径（2.5×→10×→40× 三级嵌套 bbox），先用其 50-slide Mode A 子集跑通协议，再扩展；
  2. 定义 HITL 变体协议：agent 每轮可请求一次人类 ROI 圈选（预算 k），人类输入直接替换/引导 agent 的下一级定位；横轴 k，纵轴 T3 定位 mIoU/命中率 + 下游 T1（证据解读，最强基线 Gemini-3-Flash 63.5%、专家 93.6%）与 T4（证据整合，最强 ~93% 近饱和）联合诊断准确率；
  3. 基线：k=0（纯 agent，复现官方负结果）、居中启发式（IoU 0.25–0.28）、oracle（专家路径直接给定）三条参照线；
  4. 人圈选可先用「从专家路径回放模拟」完成规模化曲线，再补小规模真人验证。
- **所需资源**：TCGA 已接入，无需新标注；VLM 调用约 $0.2–0.3/slide（官方 Mode B 数据）；**1–2 人月**；算力为 CPU + API 调用，molab GPU 非必需。
- **里程碑**：M1 数据/协议跑通 + k=0 基线复现（第 3 周）；M2 模拟人全 k 曲线 + 统计（第 6 周）；M3 真人小样本验证 + 成文（第 10 周）。
- **风险与缓解**：
  - *风险 1*：benchmark 太新（2026-07），GitHub 数据可能未完整放出。缓解：第 1 周做数据可用性核查，若缺则降级为「用其协议在 TCGA 自建 200-slide 子集 + 2 名标注者按三级 bbox 规范标注」，论文叙事改为「协议复刻 + HITL 扩展」。
  - *风险 2*：模拟人圈选被质疑「不真实」。缓解：真人子实验（6–8 名读者，交叉设计，r4 §6.2 范式）作为稳健性章节；协议代码与模拟器全部开源，让他人可复现。
  - *风险 3*：纯 agent 组快速跟进。缓解：他们没有交互基础设施与日志管线，复现成本高；我们先发 + 开源协议占据定义权。
- **预期产出**：会议/期刊短文（benchmark/协议类），投稿目标 MICCAI 2027 workshop → 正文，或 npj Digital Medicine / Nature Communications 的 brief communication；核心 claim = 「evidence acquisition 的瓶颈可以用个位数人类交互解决，纯自主 agent 路线短期内不必追」。
- **评测纪律（写进协议）**：① 所有 k 曲线的「人」输入只提供坐标，不提供诊断结论——否则混淆 acquisition 与 reasoning 的贡献；② 报告成本侧（每 slide 的人类秒数 + agent token 成本），呼应 PathAgentBench 对「轨迹/成本/安全性统一指标缺失」的批评（r1 §21）；③ 失败分析单独成章：分级错误与小病灶漏检是 SlideSeek 已知的两大重灾区（45% 错误为分级错误，r1 §2），我们的 HITL 曲线必须按这两类分解，证明人圈选对哪类失败真正有效。

### 目标 B：「AUROC vs 人类交互预算」标注效率协议 —— NSCLC/BRCA 亚型 + LUAD 突变三任务

**立场：主攻，与 A 并行，是闭环机制的标准化沉淀，长期引用潜力最高。**

- **假设**：在三个标准任务上，HE-Scope 的「agent 挑高信息量 slide/ROI → 人确认/圈选 → 写回 DB → 重训」闭环曲线，在统一横轴「人类交互预算」（slide 数、ROI 数、分钟数）下，达到同等 AUROC 所需预算比 CLAM 式随机子采样曲线低 2–4 倍，且不弱于 uncertainty/coreset 主动学习基线。
- **实验设计**：
  1. 任务集：TCGA-NSCLC 亚型（LUAD vs LUSC，CLAM 基线 AUC 0.956±0.020）、TCGA-BRCA 亚型（IDC vs ILC，ABMIL balanced acc 80.5 / DSMIL 84.7）、TCGA-LUAD 突变（EGFR/STK11/KRAS，Coudray held-out AUC 0.733–0.856；FM+简单 MIL 在 LUAD 五基因上 macro-AUROC 仅 0.626，头部空间真实存在）；
  2. 协议：FM embedding（GPFM/UNI2-h）+ ABMIL，10-fold Monte Carlo CV（CLAM 协议）；三条曲线同图：CLAM 数据效率（100/75/50/25/10%）、STAMP 稀缺数据（n=75/150/300）、HE-Scope 闭环曲线（横轴统一换算为交互预算）；
  3. 基线：random / uncertainty / coreset 三种 AL 采样；闭环消融（无 DB 沉淀的会话级反馈，显式对照 TissueLab 模式）；
  4. 附带报告分割子任务的 NoC@90 类比指标（NuClick/Clore 协议移植）。
- **所需资源**：全部公开数据 + 单卡 GPU（FM 特征 + ABMIL 很轻，molab 96GB 足够）；2–3 名标注者小规模用户研究（内部人员可代病理医师做 ROI 圈选）；**2–3 人月**。
- **里程碑**：M1 三任务 CLAM/STAMP 基线曲线复现（第 4 周）；M2 闭环管线接通 + 预实验（第 8 周）；M3 全曲线 + 消融 + 自动化偏倚测量（第 12 周，论文主体）。
- **风险与缓解**：
  - *风险 1*：LUAD 突变任务信号弱（0.63 档），曲线差异可能不显著。缓解：把它定位为「困难任务上交互预算价值更大」的分层分析，主结论押在 NSCLC/BRCA 两条干净曲线上；
  - *风险 2*：「标注效率」无公认协议被审稿人质疑自说自话。缓解：主动引用 Label-Efficient MIA 综述（arXiv:2303.12484）对标准化协议的公开呼吁 + SHAL（2026-07，26% 预算达 Dice≥0.80 vs 基线 37%）的同频语境，把「无标准」转化为「先定义者」叙事；
  - *风险 3*：TissueLab 正式发表收窄窗口。缓解：数据库沉淀消融 + 圈选预算协议是其架构覆盖不了的差异点，必须在首篇就钉死。
- **预期产出**：主系统论文的核心章节（投 Nature BME / MIA / Nat Commun 系统论文路线），或独立的 benchmark/协议论文（引用率最高的切口）；同时产出开源的「交互预算协议」评测代码包。

### 目标 C：eva + HEST 子集 parity 背书（跟进，不主攻）

**立场：纯工程背书，不追求榜首。**

- **假设**：HE-Scope 接入 FM embedding 后在标准下游任务上达到官方报告值的 parity（±2%），证明感知端不拖后腿。
- **实验设计**：eva patch 级四任务（BACH/CRC/MHIST/PCam，linear probe balanced accuracy）+ HEST 9 任务中裁 2–3 个（patch→基因表达 PCC，若空间转录组数据接入成本高则只保 2 个）；对照 r2 §9 的公开榜单数字（如 GPFM 72 任务 rank 1.6、EVA 榜 Virchow2 0.794 / UNI 0.783）做 sanity check。
- **所需资源**：**约 1 人月**，纯工程；算力用 molab。
- **里程碑**：encoder factory 骨架完成后 2 周内出 parity 报告（第 6–8 周）。
- **风险与缓解**：数值不 parity 多因预处理不一致（20x/224px/归一化约定，r2 §11 风险 4）——直接用 TRIDENT 的预处理管线或对齐其 transform，杜绝静默掉点。
- **预期产出**：不写独立论文；作为 A/B 论文的 baseline 章节与平台 README 的可信度材料。

### 明确排除项及理由

| 排除项 | 理由（调研依据） |
|---|---|
| **生存预测（c-index）主攻** | 各论文 split 不统一、横向不可比；病理单模态 MIL 典型 c-index 仅 0.60–0.70，STAMP 7 个预后任务平均 AUROC 最高仅 0.63——是 FM 最弱维度；多模态路线需基因组数据工程，头部被 Mahmood 系占据（r3 §1.3）。 |
| **TMB 预测** | 无统一协议，内部 AUC 跨度 0.64–0.99、外部普遍掉 0.10–0.15，数字不可比，无法做可信声明（r3 §1.2）。 |
| **分割/检测主攻（MoNuSAC/PanNuke/CoNSeP）** | 重像素标注、卷增量刷点赛道，与人-agent ROI 闭环禀赋弱相关；仅借 NuClick/PathoSAM 作工具组件、eva 顺带覆盖（r3 §4）。 |
| PathVQA / 封闭式 patch VQA | 已被刷到 ~95% 且 text-prior 审计（不看图拿 44–53%）摧毁公信力（r3 §3）。 |
| PathBench（Ma 2025）打榜 | 私有数据 + 私有 leaderboard，只能打榜不能做方法学创新（r3 §2.1）。 |
| 自研病理 FM 冲榜 | 头部模型差距 <2% 属噪声量级，胜负手是 10 万–310 万张切片的数据与算力，我们没有入场券（ROADMAP §2、r2 §0/§9）。 |

---

## 2. 开源迭代路线图（六维度）

总原则（r5 §6 归纳的采用漏斗规律）：**安装一步成功、首跑成本趋零、模型 zoo 统一收口、扩展 API 分层、论文引用绑定、工程质量即信任、license 分层、与既有生态共生**。病理开源 stars 天花板 ≈1.7k（CLAM），这是「小而深」社区——目标不是流量而是成为品类默认选项。以下每维度按「现状 → 差距 → 迭代项（P0/P1/P2 + 粗略工作量）」给出。

**采用漏斗的量化参照**（r5 §0–3 实测，作为我们 12 个月后的对标线）：QuPath 1,413 stars + 40 万下载 + 5,000 引用（十年积累）；TRIDENT 614 stars / 18 个月（增速远超前辈，证明 FM 时代新工具的增长斜率）；TIAToolbox PyPI 下载 11 万+（宽松 license + Colab 示例的转化力）；Slideflow 3.0 从 GPL 改 Apache-2.0 并拆三个 license 分包。我们的北极星指标不是 stars，而是：**外部论文引用 HE-Scope 完成的分析次数**——这要求 P0 的首跑体验、示例数据、SKILL.md 先于一切花哨功能。

### a. code agent 交互

**现状**：marimo-pair 桥（需 `marimo edit` 模式），三个只读工具——`get_current_selection()`（零点击 live 圈选）、`get_latest_selection()`、`get_analysis_capabilities()`；写回仅有 `db.run_repo.record()` 与 `db.roi_repo.update_annotation()` 两个代码级 API（`../AGENTS.md` §3/§6）；交互历史落 `agent_out/roi_history.jsonl` + DB agent_runs。

**差距**：agent 只能「看」不能「写」——不能回写标注、不能触发训练、不能请求人工复核；AGENTS.md 是给人看的契约文档，不是 SKILL.md 标准格式；交互轨迹无统一落库 schema，撑不起目标 A/B 的曲线统计。

**调研启示**：① Trident 随仓库发 `.claude/skills/trident/SKILL.md`，把「encoder↔分辨率配对、目录结构、常见坑」烘进 skill，README 直接宣传 agent 开箱驱动——顶级实验室对 agent-native 的背书（r5 §3.3）；② SPARK 证明「agent 写可执行代码做分析」是 Nature Medicine 级范式（r1 §13）；③ Pathology-CoT 证明 viewer 行为日志是金矿（6 倍标注提速、外部验证 recall 97.6），观测平台天然拥有该数据入口（r1 §8）。

**迭代项**：

| 优先级 | 迭代项 | 工作量 |
|---|---|---|
| **P0** | **agent 回写标注工具**：`submit_annotation(roi_id, label, notes, confidence)` 与 `request_human_review(roi_id, question)` 两个 kernel 工具函数，封装现有 roi_repo/run_repo，把人确认挂为标注生效前置条件（anti-automation-bias 结构，r4 §2.4） | 2–3 天 |
| **P0** | **仓库内置 `.claude/skills/he-scope/SKILL.md`**：从 AGENTS.md 提炼为 Agent Skills 标准格式（frontmatter name/description + <500 行正文 + progressive disclosure），覆盖启动、lazy kernel 坑、三工具、回写、GeoJSON | 1 天 |
| **P0** | **交互轨迹落库格式 v1**：`interactions` 表（人圈选→agent 分析→人确认/纠正→重训练 全链事件，带 k 次交互计数与时间戳），直接服务目标 A/B 的预算统计 | 2–3 天 |
| P1 | agent 触发训练/评估工具（`trigger_training(task_schema)`， gated by 人确认） | 3–5 天 |
| P1 | 工具/模态合法性护栏：分析工具白名单 + 「H&E 上禁止声称 IHC/分子结果」的 payload 校验（SlideSeek 幻觉 IHC 失败教训，r1 §2） | 2 天 |
| P2 | MCP server（FastMCP 薄封装 kernel 工具，LoopX 4 工具模式，r6 §1.3），让非 marimo-pair runtime 也能接入 | 3–5 天 |

**交互轨迹 schema 要点**（这是目标 A/B 的数据底座，第一版就要定对）：每个事件记录 `event_kind`（human_roi / agent_analysis / human_confirm / human_correct / retrain_trigger）、`slide_id`、`roi_id`、`run_id`、`wall_clock_seconds`（人侧耗时，预算曲线的「分钟数」横轴）、`campaign_id`（可空）。人纠正事件必须同时存「agent 原输出」与「人改后值」——这对差值就是自动化偏倚测量（错误 agent 输出的采纳/纠正率，PulmoFoundation 77.5% 警示数字的内置测量，r4 §6.2 红线）与 Pathology-CoT 式行为数据的原料。轨迹导出为一行一事件的 JSONL（对 Pathology-CoT 的「行为命令+边界框」格式保持可映射），使数据集贡献可以脱离平台单独发布。

### b. 前端交互

**现状**：统一 plotly viewer（缩放/平移/圈选一体）+ sidebar + 单 Navigator 缩略图；ROI 圈选→level-0 坐标→原图重裁 patch 链路已通。

**差距**（参照 QuPath/Slideflow/HALO）：无标注对象的管理面板（列表/编辑/删除/分类着色）；无分析结果叠层（热力图、embedding 聚类 mosaic）的交互探索；无「保存分析设置并复用于新切片」的 recipe 心智（HALO/QuPath 的用户及格线，r5 §4）；无多 ROI 比对视图。

**我们的差异化**（不与 QuPath 正面竞争）：「观测即文档」——一次观测会话天然是可复现、可 git 管理、可 uvx 一键重放的 Python 文件（reproducible viewing，QuPath 工程文件不具备此可移植性，r5 §7.2）；「embedding-aware 观测」——在切片上刷选区域实时看 embedding/注意力/聚类（Trident/CLAM 输出后缺的交互层，Slideflow Studio 有 mosaic 但不 agent 化）。

**迭代项**：

| 优先级 | 迭代项 | 工作量 |
|---|---|---|
| **P0** | **GeoJSON 导出**（ROI 标注 → GeoJSON，QuPath 可直接打开编辑回写；Trident 验证的共生路线，r5 §3.3/§7.1） | 1–2 天 |
| P1 | 标注列表面板（rois 表驱动：筛选、着色、跳转定位、删除） | 3–5 天 |
| P1 | 结果叠层 v1：训练好的分类器热力图 + 置信度渲染到 viewer（复用现有 heatmap 管线） | 5–8 天 |
| P2 | recipe/分析设置保存复用（task schema 序列化为可分享的 notebook 片段） | 5 天 |
| P2 | embedding 刷选探索（刷选区域 → UMAP 高亮 / 最近邻检索） | 8–10 天 |

### c. 数据库对接

**现状**：SQLAlchemy + SQLite，slides/rois/agent_runs 三表，可降级 DB-free 模式。

**差距**：单机 SQLite 无法支撑多 agent 并发 claim（loop 层需要行锁/乐观锁语义）；无云端/协作路径；无与 Trident 特征生态的对接（.h5 特征含 coords，已是社区事实格式）。

**演进路径**（不一步到位上云，分三档）：

| 优先级 | 迭代项 | 工作量 |
|---|---|---|
| **P0** | 数据模型扩展：interactions 表 + agent_runs 扩展 loop 字段（见 §3），全部用 SQLAlchemy 抽象层写，方言无关 | 含在 a/§3 |
| P1 | **Trident .h5 特征导入器**：把 Trident 当上游管线（组织分割/切块/embedding），HE-Scope 只做交互观测层，不重造预处理（r5 §7.1 捷径） | 3–5 天 |
| P1 | SQLite→PostgreSQL 切换路径验证（loop 层 claim 用 `SELECT ... FOR UPDATE` 语义时刚需；先保证方言兼容，不强推部署） | 2–3 天 |
| P2 | 云端协作形态评估：molab 托管 + GitHub 为 source of truth 的 demo 部署；真正多人协作（worklist/病例级）是商业壁垒区，开源不硬碰（r5 §4 Aiforia 教训） | 调研 2 天 + 视结论 |

### d. UI 设计

**现状**：marimo 单 notebook app，hide_code 单元格，app view 模式可用；demo slide 自动生成（`ensure_demo_slide()`）。

**差距与采用漏斗对策**（r5 §6 规律逐条对齐）：

| 优先级 | 迭代项 | 工作量 |
|---|---|---|
| **P0** | 首跑体验：`uvx marimo edit --sandbox` 一条命令跑通（PEP 723 inline 依赖补齐）+ README 顶部 30 秒 GIF + 三行 quickstart | 1–2 天 |
| **P0** | 示例数据自动下载（pooch 拉 1–2 张 TCGA/OpenSlide 样例，histolab 模式——「第一次跑通成本趋零」的关键） | 1–2 天 |
| P1 | 示例 notebook 序列（examples/：加载切片→圈选→标注→训练→agent 会话），托管 molab 实现零安装在线试玩（TIAToolbox Colab 标杆的 marimo 版） | 3–5 天 |
| P1 | 30 秒能懂的默认界面：开屏即切片 + 圈选 + 一个可点的「让 agent 分析」按钮；高级面板全部折叠 | 2–3 天 |
| P2 | mkdocs 文档站 + docstring 生成 API 文档（Slideflow/Sphinx 模式，工程质量即信任） | 3–5 天 |

### e. 特殊 AI 功能（agent 可触发的分析能力扩展）

**现状**：Macenko/Reinhard 染色归一、细胞核检测、QC、56 维手工特征、可选 ResNet18 embedding、heatmap、LogisticRegression 弱监督训练（ROADMAP §1）。

**差距**（参照 SPARK/PathChat 能力面）：SPARK 的范式是 agent 自主把「距肿瘤 800μm 内的淋巴细胞密度」这类概念写成可执行分析工具——我们的分析栈停在单 ROI 统计，没有空间/形态计量能力；PathChat 类「形态学描述/鉴别诊断问答」能力为零（依赖外部 VLM）。

**迭代项**（只加「code-agent 桥接范式内」的能力，不追 VLM 端到端）：

| 优先级 | 迭代项 | 工作量 |
|---|---|---|
| **P0** | **FM encoder factory 骨架 + mock 测试**（见 f；`get_embedding(patch, model="gpfm")` 统一接口，mock encoder 保证无 GPU CI 全绿） | 3–5 天 |
| P1 | 相似 ROI 检索（embedding 近邻，「人圈一个例子→找相似区域」= 检索式主动学习，Menon 范式，r4 §1.2） | 3–5 天 |
| P1 | 空间形态计量工具包：基于现有核检测 + ROI 坐标系的距离/密度/边界指标（SPARK 概念库的最小内核） | 5–8 天 |
| P1 | 交互分割后端接入：PathoSAM/NuClick 式「圈选→mask」，把圈选同时用作分割 prompt 与标注信号（r4 §3 文献支持的自然组合） | 5–8 天 |
| P2 | 免训练 surprise-guided 扫描（PathNavigate 模式：冻结特征 + 低倍异常场，给 agent 提供「建议看哪里」的低成本导航，r1 §16） | 8–10 天 |
| P2 | VLM captioner 外挂接口（用户自带 PathChat/API 模型，做 ROI 形态学描述；平台不绑模型，PathoSage 式工具可靠性追踪留钩子） | 3–5 天 |

### f. pathology foundation model 接入

**立场（采用 r2 §11 结论，一字不改地执行）**：

- **产品默认路径：GPFM（`majiabo/GPFM`，MIT）**。ViT-L/14 307M、1024 维、单卡 8GB 可批量推理；72 任务 benchmark 平均 rank 1.6（42 项第一，UNI 仅 3.7/6 项）；蒸馏自 UNI+Phikon+CONCH 三教师；**MIT 无商用风险、无需申请**——对平台分发是唯一稳妥首选。
- **学术对照路径：UNI2-h（`MahmoodLab/UNI2-h`，CC-BY-NC-ND）**。生态最成熟（TRIDENT 原生）、生存任务第一梯队、1536 维；仅用于学术实验与论文对照，681M 推理成本中等偏高。
- **商用强性能备选：H-optimus-0（Apache 2.0）**。独立临床 benchmark（Campanella 2025）检测+生物标志物双料第一，「最强可商用权重」；代价 1.1B、4.6GB 显存、75 tiles/s，只适合服务端批量提特征。
- **第二梯队备查**：Midnight-12k（MIT）/OpenMidnight（Apache-2.0，公开复现榜平均 0.775 最高）/Hibou-L（Apache-2.0，性能≈UNI 的轻量可商用选项，注意 transformers 4.x 依赖）；slide 级升级路径留 TITAN（非商用）与 GigaPath-Flash（Apache-2.0，22M+21M，CPU 可跑 slide 聚合），先以 tile embedding + 均值/ABMIL 聚合过渡（MADELEINE 证明 MEAN 基线已很强）。

**接入工程决策**：不自研完整 zoo。采用「**自研薄 encoder factory + 可选 TRIDENT 上游**」的混合：factory 只做 `load_encoder(name) -> (model, transform, dim)` 与 `embed_patch/embed_roi` 两个接口，模型注册表含 license 字段；TRIDENT 作为特征预处理的上游兼容（导入其 .h5），不把 TRIDENT 设为运行时依赖（其自定义非商用 license 与我们的 Apache-2.0 核心冲突）。依赖碎片化风险（timm 0.9.16 钉死、transformers 4.x/5.x、flash-attn）用独立 extras profile（`.[fm-gpfm]` 等）+ CI 版本钉死缓解（r2 §10 风险表）。

**License 红线（硬规则，写进 CI 检查）**：
1. **CC-BY-NC-ND 模型（UNI2-h、CONCHv1.5、TITAN、H-optimus-1、MUSK、H0-mini）不进默认路径、不进 PyPI 依赖、不出现在示例 notebook 的默认参数里**；只允许作为 opt-in 学术对照，文档显著标注非商用限制。
2. 默认/示例/CI 路径只允许：GPFM（MIT）、H-optimus-0（Apache-2.0）、Midnight/OpenMidnight（MIT/Apache-2.0）、Hibou（Apache-2.0）、GigaPath-Flash（Apache-2.0）。
3. 模型注册表逐条记录 license 与 gated 状态，README 设 license 说明节（Slideflow 三分包策略为未来拆分预留心智，r5 §2）。

**两个执行细节**：① 倍率/预处理一致性是静默掉点的最大来源——各模型有标准输入约定（20x、224/256px、特定归一化，如 Midnight 用 mean/std=0.5），encoder factory 的 transform 必须与官方对齐并以目标 C 的 parity 数字作为回归测试门槛；② 基准污染警惕——Virchow 系训练数据（MSKCC）与部分公开评测队列重叠，选型判断一律以第三方独立评测（Campanella 2025、EVA、PathBench）为准，不采信厂商自报数字（r2 §9/§11 风险 5）。

---

## 3. Loop engineering 决策

### 3.1 结论：不接 LoopX 为依赖；自研 DB 上的薄 loop 层（r6 §3.3 路线 C，采纳）

**不接 LoopX 的四条理由**（r6 §1.4–1.5、§3.1 证据）：
1. **双事实源冲突**：LoopX 状态是纯文件（`.loopx/` + `.codex/goals/` + `~/.codex/loopx/`），pluggable-state-provider 在 v0.4.2 仅「RFC accepted、contract evidence，not a shipped runtime migration」——无法把 kernel 指向我们的 SQLAlchemy DB，接入必然双写漂移；
2. **成熟度风险**：创建仅 ~2.3 个月（2026-05-31）、v0.4.2、两周三个 minor 的 breaking 演进、实质单人主导（20 contributor 但 huangruiteng 绝对主导）、31.6 万行 agent 生成风格代码作为依赖的供应链/维护风险不可接受；
3. **无 domain 语义**：goal/todo 围绕代码任务设计，病理 gate（ROI 标注复核、模型晋升）只能塞进 user_gate 自由文本，gate 类型/quota 语义无法原生建模；
4. **问题重叠度低**：LoopX 解决的「跨 runtime 控制面」问题在 HE-Scope 已被 marimo + DB 解决大半；其 dashboard 是本地只读 loopback，与 marimo 不融合。

**借什么**（LoopX 的真正价值在协议与原则，r6 §1.2/§3.3）：
- **五命令 tick 协议**：`should-run → claim → update → refresh → spend`（deliberately small）；
- **六条设计原则**：具体化 human gate（gate 必须是具体问题而非「等老板」）、诚实的 safe fallback（blocked 主线允许 P1/P2 继续但不得掩盖 gate 未解）、feedback/reward 不是权限（不能绕过 gate/claim/quota）、紧凑 evidence（writeback 替代聊天总结）、quota 保护的不只是算力也是人的注意力（monitor-only 无状态变化的 turn 保持安静）、完成标准机器可验证；
- **三层分工**（LoopX 官方《Embed LoopX In Your Agent Runner》自己描述的）：状态事实源（=我们的 DB）/ 行为契约（=SKILL.md）/ 唤醒调度（=runner），skill 管行为契约、确定性逻辑放 CLI、状态放外部存储——这正是社区已验证的轻量形态（r6 §2.3）。

### 3.2 设计草案

**数据模型变更（SQLAlchemy，方言无关）**：

| 表/字段 | 内容 |
|---|---|
| `campaigns`（新表） | id、objective（如「20 张 TCGA-BRCA 标 200 ROI，训出 AUROC≥0.9」）、definition_of_done（机器可验证表达式，如 `last_eval.auroc >= 0.9`）、status（active/blocked/done）、quota_lane JSON（slot_minutes、allowed_slots、spent_slots、window_hours） |
| `loop_todos`（新表） | todo_id、campaign_id、task_class（`annotate_batch` / `download` / `train` / `evaluate` / `retrain`）、status（open/blocked/deferred/done）、priority、required_capabilities、claimed_by + claimed_at + lease_ttl（软 claim + TTL 起步，切 PostgreSQL 后升级 `FOR UPDATE` 硬锁）、evidence_ref（指向 roi_id/run_id，不允许自由文本当证据） |
| `gates`（新表） | gate_id、campaign_id、kind（`roi_annotation_review` / `model_promotion` / `data_release`）、question（具体化问题文本）、blocking（bool）、resolution（approved/rejected + 人 + 时间戳）、fallback_allowed（审计过的 P1/P2 降级路径描述） |
| `agent_runs`（扩字段） | 加 campaign_id、todo_id、claim_id、quota_spent（slot 数）、evidence_refs JSON、turn_result_kind（validated_progress / validated_completion / host_failure / validation_failed …，借 LoopX 的 10 种结果词汇） |

**tick 协议五命令在我们的语境下的对应物**：

| LoopX 命令 | HE-Scope loop 层对应 | 语义 |
|---|---|---|
| `quota should-run` | `hescope-loop should-run --campaign C` | 检查 quota lane（eligible/throttled）+ 有无 open todo + 无未解 blocking gate；返回 scheduler_hint（退避/自停）与 interaction_contract；monitor-only 且无状态变化 → quiet skip，不消耗 quota（保护病理医生的注意力） |
| `todo claim` | `hescope-loop claim --todo T --agent A` | 软 claim 写入 claimed_by/claimed_at/lease_ttl；已被认领且 lease 未过期 → fail-closed 拒绝 |
| `todo update` | `hescope-loop update --todo T --evidence ...` | 写回进展/证据引用/派生下一 todo；evidence 必须是 DB 引用（run_id/roi_id/eval 指标），不接受聊天式总结 |
| `refresh-state` | `hescope-loop status --campaign C` | 派生只读投影：campaign 进度、open todos、pending gates、quota 余量——agent 每轮 fresh context 读盘恢复（社区共识 #2），不维持长会话漂移 |
| `quota spend-slot` | `hescope-loop spend --run R` | 一个经过验证的 slice（validated_progress/completion）完成后才记账；preflight 失败 / dry-run 不消耗 |

**human gate 如何挂在训练管线上**：`train`/`retrain` task_class 的 todo 完成条件（DoD）里硬编码前置检查——`roi_annotation_review` 类 gate 未 resolution=approved 时，训练 todo 的 update 被拒（feedback 不是权限）。`model_promotion` gate 挂在「评估指标达标 → 模型写入 `data/models/` 可用列表」之间：评估自动跑（机器可验证），但晋升进 `get_analysis_capabilities()` 可见模型列表必须人批准。marimo UI 侧，pending gates 渲染为原生确认卡片（具体问题 + approve/reject 按钮），不跳外部工具——gate 即 marimo 原生交互，这是相对 LoopX 文本协议的领域优势。

**分发形态**：
- 小 CLI：`hescope-loop`（click/argparse，约数百行 Python，确定性逻辑全在 CLI 不放 prompt）；
- `he-scope-loop` SKILL.md（Agent Skills 标准 frontmatter，<500 行）：教任意 runtime 的 code agent「should-run → claim → bounded turn → 验证 → writeback → spend」的行为契约，跨 Codex/Claude Code/Kimi Code/自研 runner 复用；
- 初版调度不搞 heartbeat automation，人手动或 cron 唤醒 runner 即可；每轮 fresh context + `status` 投影恢复。

**失败模式预案**（社区共识的六类失败在我们语境下的对策，r6 §2.1）：无退出条件 → campaigns.definition_of_done 机器可验证表达式；同策略重复失败 → `update` 命令检测连续 N 次 validation_failed 自动转 replan_required 并升 gate；context overflow → 每轮 fresh context + `status` 投影恢复，不维持长会话；目标模糊 → campaign 创建时强制填写 objective + DoD + non-goals 三字段；缺工具权限 → write_scope 白名单列在 loop_todos；quota 失控 → allowed_slots 硬上限 + quiet skip 约定（blakecrosley 报告无预算时 token 消耗 10x 的教训）。

### 3.3 与 LoopX 的跟踪复评节点

- **复评触发条件 1**：LoopX pluggable-state-provider RFC shipped（可写 DB-backed provider）→ 评估「LoopX kernel + HE-Scope provider」是否优于自研层；
- **复评触发条件 2**：LoopX 通用 MCP 面（lifecycle reads + todo/gate/lease writes）成熟稳定 → 评估以 MCP 互操作替代自研 CLI；
- **复评节奏**：每 6 周看一次 release notes（下一次约 2026-09-19）；我们的数据模型刻意与其概念同构（todo/gate/quota/evidence），届时迁移成本可控；
- **不做的事**：不 fork LoopX、不把其文件投影作为中间层（DB→文件投影双向同步收益不抵成本，r6 §3.2）、现阶段不评估 Gas Town/Beads 系（20+ agent 代码工厂式并行不是我们的场景）。

---

## 4. 总里程碑表（未来 3 个月，2026-08-08 → 2026-11-08）

三条线编成可执行序列：**工程线（E）= 开源迭代 P0 项 + FM 接入；学术线（R）= 目标 A/B/C 实验；loop 线（L）= §3 薄 loop 层**。「过夜已实施项」指每一冲刺结束时，过夜批次必须已交付并全绿的工程产出。

| 双周 | 工程线 E | 学术线 R | loop 线 L | 过夜已实施项（硬性交付） |
|---|---|---|---|---|
| **W1–2**（8/08–8/21） | P0 四件：agent 回写标注工具（submit_annotation/request_human_review）、交互轨迹落库格式 v1、GeoJSON 导出、SKILL.md（he-scope） | 目标 A：PathAgentBench 数据可用性核查 + 协议代码骨架；目标 C：FM 选型下载与环境验证（GPFM 优先） | 数据模型变更设计定稿（campaigns/loop_todos/gates/agent_runs 扩展字段） | ① agent 回写标注工具 + 测试；② 仓库 SKILL.md；③ GeoJSON 导出 + QuPath 打开验证截图；④ interactions 表 migration |
| **W3–4**（8/22–9/04） | FM encoder factory 骨架 + mock 测试（GPFM 注册，license 字段 + CI 红线检查）；首跑体验（uvx sandbox + pooch 示例数据 + README GIF） | 目标 A：k=0 纯 agent 基线复现（T3 定位/命中率，对齐官方数字 mIoU<0.09、2.0%）；k 模拟器（专家路径回放） | `hescope-loop` CLI 五命令实现 + 单测 | ⑤ FM encoder factory 骨架 + mock 测试 CI 全绿；⑥ uvx 一键首跑 + 示例数据自动下载；⑦ A 目标 k=0 基线数字 |
| **W5–6**（9/05–9/18） | GPFM 真机接通（molab GPU）+ 相似 ROI 检索（embedding 近邻）；标注列表面板 | 目标 A：全 k（0/1/3/5）模拟人曲线 + 统计；目标 C：eva patch 四任务 parity 跑批启动 | gates 挂训练管线（DoD 前置检查 + model_promotion gate）+ marimo gate 确认卡片 | ⑧ GPFM embedding 在 demo slide 上端到端（mock→真机切换零代码改动）；⑨ A 目标主曲线数据 + 图 |
| **W7–8**（9/19–10/02） | 结果叠层 v1（热力图渲染）；示例 notebook 序列 + molab 在线试玩托管 | 目标 C：eva parity 报告（±2% 判过）；目标 B：三任务 CLAM/STAMP 基线曲线复现（NSCLC 0.956±0.020、LUAD 突变 0.63–0.85 区间对齐） | he-scope-loop SKILL.md 发布；最小 campaign（demo slide 10 ROI 两轮主动学习）端到端跑通 | ⑩ eva parity 报告；⑪ B 目标三条基线曲线复现；⑫ loop 层最小 campaign 全流程日志落库 |
| **W9–10**（10/03–10/16） | 空间形态计量工具包 v1（距离/密度指标，SPARK 概念最小内核）；agent 触发训练工具（人确认 gated） | 目标 B：闭环管线接通预实验（agent 挑样→人圈→重训 3 轮）；目标 A：真人小样本验证设计（交叉设计，6–8 读者） | SQLite→PostgreSQL claim 路径验证；LoopX 第一次 6 周复评（9/19 起执行） | ⑬ B 目标闭环预实验曲线（3 轮）；⑭ 形态计量工具 + 测试；⑮ LoopX 复评纪要 |
| **W11–12**（10/17–10/30） | 工具合法性护栏（白名单 + 模态校验）；MCP server spike | 目标 A：真人验证执行 + 论文初稿；目标 B：全预算曲线 + random/uncertainty/coreset 基线 + 数据库沉淀消融 | 全链路压测：多 agent 轮流 claim 同一 campaign 的并发正确性 | ⑯ A 目标论文初稿（含真人验证）；⑰ B 目标全曲线 + 消融数据 |
| **W13**（10/31–11/08） | 缓冲区：还债、文档、release v0.2.0（monthly minor + Updates 时间线启动） | 目标 A 投稿准备（MICCAI workshop / npj DM brief communication 二选一）；目标 B 论文主体撰写启动 | 复评 loop 层 vs 实际 campaign 使用的摩擦点，定 P1 迭代 | ⑱ v0.2.0 release + 两篇论文状态报告 |

**依赖与关键路径**：FM encoder factory（W3–4）是目标 B/C 一切实验的前置；agent 回写 + 轨迹落库（W1–2）是目标 B 闭环曲线与 loop 层的前置；目标 A 相对独立（CPU+API 即可），与工程线弱耦合——若工程线延期，A 线不受影响，这是把 A 排在首篇论文的原因。

**3 个月末的验收姿态**：一篇可投出的 HITL benchmark 论文（A）、一套复现完毕 + 闭环预实验数据的标注效率协议（B）、eva parity 背书（C）、v0.2.0 开源版本（agent 可写、SKILL.md、FM factory、GeoJSON、loop CLI），以及一个跑通过真实两周 campaign 的薄 loop 层。

**跨线风险总表**（不重复各节内部分解，只列会击穿整体计划的风险）：

| 风险 | 触发条件 | 缓解 | 降级方案 |
|---|---|---|---|
| PathAgentBench 数据未放出 | W1 核查失败 | 协议复刻 + 自建子集（§1.A 风险 1） | 目标 A 转纯协议论文，用 200-slide 自标注子集 |
| molab GPU 不可用/限额 | W5 实测失败 | GPFM 307M 可在 CPU 慢速跑小样本；本地无 GPU 只影响吞吐不影响正确性 | parity 报告裁剪到 BACH/MHIST 两个小任务 |
| 人力不足以三线并行 | 任一里程碑连续两周滑动 | 保 A 线（弱耦合、最先发）> 保 E 线 P0 > B 线延期 | B 线缩为 NSCLC 单任务，loop 层缩为数据模型 + should-run/claim 两命令 |
| TissueLab 抢先正式发表 | 任意时刻 | B 论文的数据库沉淀消融 + 圈选预算协议是其架构外差异点 | 论文 related work 正面引用并做显式对比实验 |
| LoopX 突然成熟（provider shipped） | 复评发现 | 数据模型已同构，迁移成本可控（§3.3） | 仅在确实优于自研层时迁移，否则继续跟踪 |

---

## 附：关键引用数字索引

- PathAgentBench T3：定位 mIoU<0.09、居中启发式 IoU 0.25–0.28、40× 命中率 2.0%、T1 最强 63.5%/专家 93.6%、T4 ~93%；1,822 WSI/17,135 路径（r1 §20、r3 §6.1）
- CLAM：NSCLC AUC 0.956±0.020、10-fold MC CV 协议、25% 数据点（r3 §1.1）
- Coudray LUAD 突变：held-out AUC 0.733–0.856；GigaPath FM+MIL 五基因 macro-AUROC 0.626（r3 §1.2）
- SHAL：26% 标注预算 Dice≥0.80 vs 基线 37%（r3 §5）
- Menon/MyriadAL：~5% 标注达 SOTA；WSI 分割扫描面积减至 2%（r4 §1.1）
- PulmoFoundation RCT：AI 辅助 +8.5pp、时间 −18.3%、**AI 错时 77.5% 采纳**（自动化偏倚红线，r4 §2.3/§6.2）
- GPFM：MIT、ViT-L/14 307M、72 任务 rank 1.6；H-optimus-0：Apache-2.0、Campanella 双料第一、4.6GB 显存/75 tiles/s；CC-BY-NC-ND 清单（r2 §5/§8/§11）
- TRIDENT：614 stars/18 个月、自带 `.claude/skills/trident/SKILL.md`、GeoJSON↔QuPath 共生（r5 §3.3）
- SlideSeek：47.4 区域/例 vs 传统 1020±783 ROI；幻觉 IHC 失败模式（r1 §2）
- SPARK：Nature Medicine 2026，无训练 agent 写代码做生物标志物发现（r1 §13）
- Pathology-CoT：行为日志标注提速 6 倍、外部验证 recall 97.6（r1 §8）
- LoopX：3,555 stars/~2.3 个月、v0.4.2、31.6 万行、实质单人、pluggable-state-provider 仅 RFC（r6 §1.4–1.5）
