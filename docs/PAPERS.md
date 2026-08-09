# PAPERS.md — HE-Scope 学术文献综述（存档与写作素材库）

> 整理日期：2026-08-08。整理自 R1（病理 AI agent 系统）、R2（基础模型）、R3（benchmark 全景）、R4（人机协作/主动学习）四份调研报告。
> 用途：① 项目学术方向存档；② 后续论文（系统论文 / 方法论文 / benchmark 论文）的 related work 与定位论证素材。
> 约定：所有事实性陈述附来源 URL（沿用调研文件引用）；不引入四份报告之外的论文与数字；专有名词保留英文。

---

## 目录

1. [病理 AI Agent 系统综述（2024–2026）](#1-病理-ai-agent-系统综述20242026)
2. [人机协作与主动学习综述](#2-人机协作与主动学习综述)
3. [评测格局综述](#3-评测格局综述)
4. [HE-Scope 的定位与 novelty 分析](#4-he-scope-的定位与-novelty-分析)

---

## 1. 病理 AI Agent 系统综述（2024–2026）

### 1.0 领域地图：四条主线

2024–2026 年病理 AI agent 领域沿四条主线演进（R1 §0）：

1. **病理 VLM copilot（单模型）**：PathChat（Nature 2024）开创，随后 Quilt-LLaVA、SlideChat、WSI-LLaVA、PathGen-LLaVA、CPath-Omni 跟进，2025–2026 年进入推理增强一代（Patho-R1、SmartPath-R1、TeamPath，普遍采用 RL/GRPO 训练）。反复被验证的核心结论：**病理专用 MLLM 显著强于通用前沿模型**。
2. **Agentic WSI 诊断（从 ROI 到整张切片）**：2025 年爆发的「Supervisor–Explorer / 导航—描述—诊断」范式：PathFinder、CPathAgent、SlideSeek（PathChat+）、Pathology-CoT、PathNavigate。agent 自主决定「看哪里、看多大倍率」，输出带坐标证据链的诊断。
3. **工具调用 / 科研自动化 agent**：MMedAgent、CT-Agent（调用分割/检测/检索工具）；SPARK（Nature Medicine 2026，agent 自主生成可执行分析代码做生物标志物发现）；SAGE、PathLab（自然语言研究意图 → 可复现计算病理 pipeline）；商业化 Judith（Modella AI）。
4. **评测基建**：PathMMU、PathQABench、SlideBench、DDxBench、PathAgentBench、PathView-Bench、PathVG 等。系统性结论是「会答题 ≠ 会找证据」，证据获取（localization/navigation）是当前最大短板（详见 §3）。

### 1.1 主线一：病理 VLM copilot

#### 1.1.1 PathChat — 开山之作（Harvard / MGB Mahmood Lab, Nature 2024）

- **机构/时间**：哈佛医学院 / Mass General Brigham Mahmood Lab（Ming Y. Lu 等），Nature 2024-06 在线，Nature 634:466–473。https://www.nature.com/articles/s41586-024-07618-3
- **架构**：非 agent 的端到端 MLLM。UNI 视觉编码器（1 亿+ 组织学 patch 自监督预训练）→ 118 万病理图文对做视觉-语言对齐 → 13B Llama 2（LLaVA 1.1.3 框架）→ 45.6 万条视觉-语言指令（99.9 万 QA 轮次）微调。
- **能力**：多轮对话；形态学描述、鉴别诊断、分级、建议 IHC/分子检测、预后/治疗知识问答；输入为 ROI 图像（非整张 WSI）。
- **关键结果**：自建 PathQABench（多选诊断题覆盖 11 个器官/实践领域 54 种诊断 + 260 道开放题 + 病理专家人评）；开放题上优于 GPT-4V、LLaVA、LLaVA-Med。https://pubmed.ncbi.nlm.nih.gov/38866050/
- **开源**：代码与模型开放（HuggingFace，研究用途）；商业化由 Modella AI 进行。
- **对 HE-Scope 的意义**：「ROI 级 copilot」是 baseline，行业已转向 WSI 级 agent；其「多选 + 开放题 + 专家人评」三层评测设计可借鉴；论文自述需 RLHF 降幻觉、需学会「该问什么临床信息」。

#### 1.1.2 SlideChat — 首个 WSI 级开源视觉-语言助手（上海人工智能实验室等, CVPR 2025）

- **机构/时间**：Ying Chen 等，arXiv 2024-10，CVPR 2025。https://arxiv.org/html/2410.11761
- **架构**：非 agent。三级结构：patch 编码器 CONCH + slide 级编码器 LongNet（稀疏注意力处理整张切片长序列）+ 多模态投影 + Qwen2.5-7B；两阶段训练（跨域对齐 → 视觉指令学习）。
- **数据/评测**：自建 SlideInstruction（4.2K WSI-caption + 176K WSI-VQA）；SlideBench（Caption 734 对、VQA-TCGA 7827 题/13 任务、VQA-BCNB 7274 题/7 任务）；SlideBench-VQA(TCGA) 总准确率 81.17%，22 项任务中 18 项 SOTA。
- **开源**：完全开放权重、代码、指令集与基准。
- **对 HE-Scope 的意义**：与 agent 路线互补的「单模型吞整片」路线；CONCH+LongNet 组合与开源基准是可复用基建。

#### 1.1.3 VLM 基座（agent 的「眼睛」）：CONCH / Quilt-LLaVA / PLIP

- **CONCH**（Mahmood Lab + MIT, Nature Medicine 2024）：CoCa 架构、117 万图文对训练，~200M 参数；zero-shot 分类、跨模态检索、分割等 14 项基准 SOTA；被广泛用作 agent 系统的 patch 编码器/检索器（SlideChat、PathNavigate 等）。https://www.nature.com/articles/s41591-024-02856-4
- **Quilt-1M / Quilt-LLaVA**（华盛顿大学 Shapiro Lab, NeurIPS 2023 / CVPR 2024）：从 YouTube 病理教学视频抽取 100 万图文对 + 10.7 万指令对微调 LLaVA。https://arxiv.org/html/2606.07549v1（参考文献）
- **VLM 对比研究**（3507 张消化道 WSI 实测）：CONCH 平均 AUC 0.876 > Quilt-LLaVA 0.753 > Quilt-Net 0.666；模型规模不是决定因素，领域对齐才是；提示词措辞（dysplasia/atypia/precancerous）显著影响结果。https://arxiv.org/html/2505.00134v1
- **对 HE-Scope 的意义**：平台选型优先 CONCH 类对比学习 VLM 做检索/定位工具；提示词工程是系统性风险源，需固化模板。（更全的 tile/slide FM 选型见 §4.6 与 R2。）

#### 1.1.4 推理增强一代：SmartPath-R1 / TeamPath / Patho-R1（2025）

- **SmartPath-R1**（HKUST Hao Chen 团队, arXiv:2507.17303）：单 MLLM 同时做 ROI 分类/检测/分割/VQA 与 WSI 分类/VQA；尺度相关 SFT + 任务感知 RL 微调（无需 CoT 标注）；MoE 动态处理多尺度多任务；训练数据 230 万 ROI + 18.8 万 WSI；72 项任务验证。https://arxiv.org/abs/2507.17303
- **TeamPath**（Yale + Duke-NUS + 东京大学/RIKEN, arXiv:2511.17652）：基座 Patho-R1-7B，GRPO 强化微调（推理数据由 o4-mini 生成 CoT 模板、耶鲁病理医生质控，20K 推理 prompt）；LLM 路由器按任务选策略（RL/SFT/test-time scaling 专家），路由准确率 >80%；PathMMU 五个子集全面超 Patho-R1-7B、PathGen-LLaVA-13B、MedGemma-4B 等；人机协作实验中作为 verifier/corrector 修正病理医生答案，准确率显著提升（p=0.004）。https://arxiv.org/html/2511.17652v1
- **Patho-R1 / Patho-AgenticRAG**（四川大学华西医院 Hong Bu 团队, AAAI 2026）：多模态病理知识库（600+ 权威教科书、20 万+ 页面，ColQwen2 嵌入 + Milvus/HNSW 索引）+ agentic router + VRAG agent；GRPO 的 Tool-Integrated Reasoning 训练让 agent 学会「是否检索、如何改写问题、调用哪个领域工具」。https://arxiv.org/html/2508.02258v1
- **小结**：R1 式 RL 推理已进入病理 VLM 主流；「router + 多专家」是 agent 化的轻量形态；「AI 纠正专家而非替代专家」的定位更易被临床接受。

### 1.2 主线二：Agentic WSI 诊断

#### 1.2.1 SlideSeek / PathChat+ — 多 agent WSI 诊断标杆（Mahmood Lab, 2025–2026）

- **机构/时间**：哈佛/MIT Mahmood Lab，arXiv:2506.20964（2025-06 v1；2026-03 v2 更名《Evidence-based diagnostic reasoning with multi-agent copilot for human pathology》）。https://arxiv.org/html/2506.20964v2
- **架构（双层）**：
  - **PathChat+**（非 agent 底座 MLLM）：1.13M 指令、5.49M QA 轮次、62.4 万张图训练；支持多图输入与高分辨率多 ROI 分析。
  - **SlideSeek**（agent 层）：推理 LLM 作 supervisor（跟踪进度、提出假设、分派任务）+ 多个 explorer agent（各在指定区域/倍率调用 PathChat+ 做形态学描述并回报）+ report agent 合成视觉锚定的结构化报告。平均每病例检查 47.4 个区域（高倍 11.9 / 中倍 17.9 / 低倍 17.6），而传统方法需处理 1020±783 个 20× ROI——导航显著降低计算量。
- **关键结果**：DDxBench（150 张 WSI、55 种肿瘤、41 种罕见病，开放式鉴别诊断）上 top-1 86.0%、top-3 92.7%，比通用 MLLM 高最多 42%；PathChat+ 单模型在专家预选 ROI 上 top-1 80.0%（比 Gemini 2.5 Pro 高 28.7%）。消融：去掉 supervisor 层级 top-1 降 8%；captioner 换成通用 GPT-5-mini 降 43.3%——**专用形态学 captioner 是 agent 系统性能的关键**。元认知校准：高置信病例准确率 82.7% vs 低置信 65.2%。
- **失败模式（对平台设计极重要）**：45% 错误为肿瘤分级错误；漏掉小而决定性的病灶（如 Merkel 细胞癌小灶）；出现「幻觉 IHC」——supervisor 布置 H&E 上不可能完成的 IHC 任务，explorer 竟然编造结果。
- **开源**：论文与 DDxBench 公开；模型经 Modella 商业化。
- **对 HE-Scope 的意义**：Supervisor–Explorer + 专用 VLM captioner 是被消融验证的有效配方；「工具/模态合法性约束」（防幻觉 IHC）是 agent 平台必须内建的护栏；报告要带 ROI 坐标证据链。

#### 1.2.2 PathFinder — 四 agent 顺序流水线（UW Shapiro Lab, 2025）

- **机构/时间**：华盛顿大学（Seyfioglu/Ghezloo 等），arXiv:2502.08916，2025-02；项目页 pathfinder-dx.github.io。https://arxiv.org/abs/2502.08916
- **架构**：Triage Agent（良性/可疑分流）→ Navigation Agent + Description Agent 迭代选 patch、生成自然语言描述 → Diagnosis Agent 综合诊断；模拟病理医生「低倍扫 → 高倍看 → 记笔记 → 下诊断」。
- **关键结果**：M-Path 皮肤活检黑色素瘤分级 238 例，准确率 74%，比最佳 baseline 高 8%、比病理医生平均水平高 9%；描述质量经病理医生评估与 GPT-4o 相当（LLM-as-judge，5 级 Likert 惩罚诊断幻觉）。
- **开源**：数据/代码/模型开放。作者自述局限：依赖算力、导航决策复杂、Description Agent 偶发幻觉。
- **意义**：最早同时实现「可解释证据链 + 超人类平均」的多 agent 系统；顺序流水线比层级 supervisor 简单但泛化性弱。

#### 1.2.3 CPathAgent — 训练型导航 agent（Lin Yang 团队, 2025）

- **机构/时间**：Yuxuan Sun 等，arXiv:2505.20510（2025-05，v2 2025-10）。https://arxiv.org/abs/2505.20510
- **架构**：单模型经多阶段训练统一 patch/region/WSI 三级能力，推理时以 agent 方式自主在 WSI 上导航（观察 → 移动 → 变焦），输出透明诊断摘要——把导航策略学进模型而非靠 prompt 工程。
- **评测/数据**：自建 PathMMU-HR²（首个专家验证的「大区域」级基准，填补 patch 与 WSI 之间的尺度空白，1688 专家验证多尺度 VQA，CPathAgent 88.6% vs Gemini-2.5-Pro 76.4）。https://arxiv.org/html/2505.20510v1
- **意义**：「中间尺度（large region）」是被忽视但临床真实的观测单位；训练型导航 vs prompt 型导航是两条技术路线。

#### 1.2.4 Pathology-CoT / Pathology-o3 — 从专家阅片行为学 agent（Stanford, 2025）

- **机构/时间**：斯坦福大学 Sheng Wang 团队，arXiv:2510.04587（2025-10）。https://arxiv.org/abs/2510.04587
- **架构/数据**：AI Session Recorder 嵌入标准 WSI viewer，无感记录病理医生真实导航行为（缩放、移动），转成行为命令 + 边界框；AI 起草「为什么看这里」的 rationale、人工轻量复核（标注提速 6 倍），构成 Pathology-CoT 数据集（「看哪里」+「为什么」配对）；基于此训练 Pathology-o3 两阶段 agent：先提议 ROI，再行为引导推理。
- **关键结果**：胃肠淋巴结转移检测——Stanford 内部验证 recall 100、瑞典独立外部验证 recall 97.6，超过 OpenAI o3 且跨 backbone 泛化。
- **对 HE-Scope 的意义（关键）**：**viewer 行为日志是金矿**——HE-Scope 作为观测平台天然能采集同类数据，这是平台型产品相对纯模型产品的独特数据飞轮。

#### 1.2.5 PathoSage — 证据裁决与工具可靠性建模（2026）

- **机构/时间**：arXiv:2606.07549（2026-05）。https://arxiv.org/abs/2606.07549
- **架构**：三阶段显式分离——知识检索、证据收集、证据裁决（Structured Evidence Deliberation）：异质工具输出独立评估、冲突分析、在全新上下文中出最终判断以减少锚定偏差；免训练 Beta-Bernoulli 经验系统持续建模各工具长期可靠性，形成相似度加权先验。
- **关键结果**：缓解 VQA 幻觉与分类器分歧，优于强病理 MLLM 与 agentic baseline。
- **意义**：直接回应 SlideSeek 暴露的「上下文污染/工具冲突」问题；「工具可靠性追踪」应成为 agent 平台一等公民。

#### 1.2.6 PathNavigate — 免训练 WSI-VQA agent（腾讯/北大等, 2026）

- **机构/时间**：Chunze Yang、Chen Li 等，arXiv:2605.23559（2026-05）。https://arxiv.org/abs/2605.23559
- **架构**：training-free，scan-search-readout：先用冻结病理特征 + 在线共享记忆在低倍下生成「surprise field」异常区域池（先扫片再看问题，避免 question-first 漏掉问题未命名的决定性形态），再在池内用 PLIP 做问题条件检索选高倍目标，最后冻结 perceptor-adjudicator 栈作答。
- **关键结果**：WSI-VQA 与 SlideBench-BCNB 上准确率提升、证据选择轨迹可解释性更好；代码开源。
- **意义**：免训练 agent 用「异常先验 + 冻结特征 + 在线记忆」即可实用——对需兼容用户自带模型的平台尤其友好。

### 1.3 主线三：工具调用 / 科研自动化 agent

#### 1.3.1 SPARK — 无训练自主科学发现 agent（Nature Medicine 2026）★

- **机构/时间**：Yuri Tolkach 等（德国 Uniklinik Köln UKK / UKE 队列），Nature Medicine 2026-04-29。https://www.nature.com/articles/s41591-026-04357-y
- **架构**：crewAI 框架的 agent–task–crew–flow–tool 范式，四段流水线：想法生成 → 想法精炼 → 参数/代码实现 → 参数验证。输入为经质控 + 器官特异多类组织分割 + 单细胞检测预处理后的 WSI 对象；agent 用语言作通用接口，自主提出生物学概念（如「距肿瘤 800μm 内的淋巴细胞密度」）并写成可执行分析工具，**全程无模型训练**，数小时即可原型化新分析。工具实现为 extra-agentic 以省 token；经验上 agentic memory 无益且费钱，全部禁用。
- **关键结果**：LUAD/LUSC/COAD/BRCA/HNSC 多队列（TCGA、PLCO、NLST、UKK、UKE、HAL）：生成的可解释概念库显著提升肿瘤分级与预后分层；PD-L1 状态预测中捕获更广的免疫逃逸特征；支持与病理医生交互（人提概念、agent 实现）。
- **开源**：代码、参数与参考手册存 Zenodo（records/18047852）。
- **对 HE-Scope 的意义（最强背书）**：**「agent 写代码调用经典图像分析（分割/细胞检测/形态计量）」在顶级期刊被验证优于 VLM 直接推理**——这正是 HE-Scope code-agent 桥接路线的定位；论文明确批评 VLM「连简单的定量/推理问题都答不可靠」。

#### 1.3.2 PathLab — agent 社会生成可复现计算病理研究（2026）

- arXiv:2606.20677（2026-06）。动态 agent 社会把自然语言研究意图解析为计算任务并选型方法学组件；双模式 Co-pilot（迭代人机协作）与 Auto-pilot（全自动 pipeline 生成）；内建领域验证（技术兼容性、信息泄漏防范）；输出经验证的可执行配置而非代码片段，支持社区共享复用。https://arxiv.org/html/2606.20677
- 意义：「自然语言 → 可复现病理 pipeline 配置」与 HE-Scope 的 marimo notebook 产物高度同构；防数据泄漏、终点定义校验是病理特有护城河。

#### 1.3.3 SAGE — 生物标志物发现的多角色 agent（2026）

- arXiv:2602.00953（2026-02）。7 个角色 agent——Ontologist、Scientist、Senior Scientist、Clinical Feasibility、Debate-based Critic、Coding（在患者队列上执行验证）、Summary；膀胱癌用例端到端「发现—解释—验证」。https://arxiv.org/html/2602.00953v1
- 意义：角色分工 + 辩论式评审是抑制虚假相关性的设计模式；Coding agent 直接跑队列分析 = code-agent 桥接。

#### 1.3.4 病理之外的工具调用 agent（模式参考）

- **MMedAgent**（EMNLP 2024 Findings, arXiv:2407.02483）：首个多模态医学工具调用 agent；LLaVA-Med 作 planner，端到端指令微调学会调用 6 类工具（Grounding DINO 定位、MedSAM 分割、BiomedCLIP 分类、ChatCAD 报告、RAG），含组织学模态；总分 1.8× 于 LLaVA-Med，多项超 GPT-4o；代码开源。https://arxiv.org/html/2407.02483v2 ；https://github.com/Wangyixinxin/MMedAgent
- **CT-Agent**（Science China Information Sciences 2026）：3D CT 问答的规划-动作空间-记忆三模块 agent。http://scis.scichina.com/en/2026/150107.pdf
- 其余：CXRAgent（2510.21324）、RadAgents（2509.20490）、MedSAM-Agent（2602.03320，多轮 agentic RL 交互分割）、MedAgent-Pro（2503.18968）。https://arxiv.org/html/2607.11175v1（参考文献）
- 小结：「冻结专用模型作工具 + LLM 作规划器」已在放射影像成熟；病理的特殊性在于 gigapixel 导航这一额外维度。

#### 1.3.5 多 agent 诊疗讨论（MDT 模拟，病理的下游）

- **EvoMDT**（npj Digital Medicine, 2026-01）：诊断/治疗/安全/监测/协调五 agent 模拟多学科会诊，协调者按置信度动态加权、解决冲突；底座 DeepSeek V3/R1。https://www.nature.com/articles/s41746-025-02304-8
- 系统综述（Frontiers in Oncology Reviews, 2026-05）：LLM 用于 MDT 的全景，强调准确率/安全/临床效用未决。https://www.frontiersin.org/journals/oncology-reviews/articles/10.3389/or.2026.1757059/full

#### 1.3.6 PathGen-1.6M — 多 agent 造数据（ICLR 2025）

- 用多 agent 协作从 TCGA ~9K WSI 生成 160 万病理图文对（代表 patch），训练出 PathGen-LLaVA；并发布 PathMMU 基准（2.4 万+ 专家级多选题，已成病理 MLLM 事实标准测试集）。https://www.nature.com/articles/s43588-025-00818-5
- 意义：multi-agent 不仅用于推理，也用于大规模数据合成。

### 1.4 主线四：评测基建（简述，详见 §3）

| 基准 | 来源 | 测什么 | 关键发现 |
|---|---|---|---|
| PathMMU | PathGen（ICLR 2025） | ROI 级多选 VQA | 通用 VLM 大幅落后病理专用模型 |
| PathQABench | PathChat（Nature 2024） | 多选诊断 + 开放题 + 专家人评 | 三层评测设计范式 |
| SlideBench | SlideChat（CVPR 2025） | WSI caption + VQA（21 任务） | 整片建模 > patch 投票/缩略图 |
| DDxBench | SlideSeek（2025/2026） | 150 WSI、55 肿瘤开放式鉴别诊断 | top-1 86%（SlideSeek）；分级与小病灶是失败重灾区 |
| PathAgentBench | arXiv:2607.19261（2026-07） | 证据解释/验证/获取/整合四能力 | 文本引导定位 mIoU<0.09，不如居中启发式；自主探索高倍命中率仅 2.0% |
| PathView-Bench | arXiv:2607.28318（2026-07） | 细粒度多尺度理解 | 最新，格局未定 |
| PathVG | MICCAI 2025 | 病理视觉定位 | — |

PathAgentBench 的结论是全领域最重要的负结果之一：**当前模型「会推理现成的证据，不会自己找证据」**。https://arxiv.org/abs/2607.19261

### 1.5 产业化动态：Modella AI / PathChat 2 / PathChat DX / Judith

- **Modella AI**（波士顿，2024-06 从 Mahmood Lab 分拆）。**PathChat 2** 支持 slide viewer 内多高分辨率图 + 文本交错对话，鉴别诊断/形态描述/指令遵循/报告总结显著增强，并支持用户在切片上圈选 ROI 就形态、鉴别诊断、biomarker 提问（研究/教育用途）。临床版 **PathChat DX 于 2025-02 获 FDA Breakthrough Device Designation**（注意：不等于获批上市）。**Judith**：面向科研的 AI agent——用户自然语言描述任务（如分割某类细胞），Judith 自动完成建模、分析、解释，支持 gigapixel WSI 与 foundation-model 生物标志物发现。
- **2026-01-13 AstraZeneca 宣布收购 Modella AI**（称「大药企首次收购 AI 公司」），用于肿瘤 R&D 定量病理与生物标志物。
- 来源：https://www.modella.ai/pathchat ；https://www.modella.ai/judith ；https://www.biopharmatrend.com/news/astrazeneca-acquires-modella-ai-to-integrate-foundation-models-into-global-oncology-rd-1463/ ；https://www.urotoday.com/conference-highlights/bcantt-2026/170891-bcantt-2026-ai-in-bladder-cancer-whats-real-whats-next-and-what-to-watch-out-for.html
- **意义**：产业界验证了「copilot（人）+ agent（科研自动化）」双产品形态；FDA 突破性器械认定说明监管通道真实存在；code-agent 自动建分析流程（Judith 模式）正是 HE-Scope 的对标场景。

### 1.6 领域共识 Open Problems（对平台设计的约束清单）

来源：《Computational Pathology in the Era of Emerging Foundation and Agentic AI》（arXiv:2603.05884）、Lancet Digital Health 2025 述评（PIIS2589-7500(25)00115-3）、《Foundation Models in Computational Pathology: A Review…》（arXiv:2502.08333）及各系统局限分析：

1. **证据获取能力缺失**（最硬核）：PathAgentBench 定位 mIoU<0.09、高倍自主探索命中率 2.0%。
2. **幻觉与工具/模态合法性**：形态学幻觉、幻觉 IHC；pen mark 即可误导 GPT-4/Claude 级通用 VLM（Lancet 述评）。
3. **agentic 系统误差传播**：组件不可靠时多 agent 流水线累积放大误差。
4. **细粒度分级与小病灶检测**：SlideSeek 45% 失败为分级错误。
5. **WSI 级训练数据与行为数据稀缺**：专家「看哪里、为什么」的行为监督几乎不存在（Pathology-CoT 的核心动机）。
6. **评测不成熟**：agentic 工作流评测可投机；多为回顾性、单中心；缺统一 agent 轨迹/成本/安全性指标。
7. **鲁棒性与域偏移**：扫描仪/染色/制片差异；10 维部署风险表（综述①）。
8. **临床转化鸿沟**：成本、报销、LIS/PACS 集成、法律责任、自动化偏见与技能退化。
9. **多模态扩展未做**：多数系统只处理 H&E。
10. **成本与效率**：agentic memory 费钱且无益（SPARK 实证）。
11. **闭源阻碍可复现研究**：TeamPath 等批评；开放权重 + 开放基准（SlideChat 模式）仍属少数。

---

## 2. 人机协作与主动学习综述

### 2.0 总体判断

「人圈选 → AI 分析 → 反馈入库 → 重训练」闭环在文献中**每个环节单独都有成熟工作**（主动学习、交互式分割、人机协作 reader study、数据飞轮），但把「**LLM/VLM agent 作为分析主体 + 人圈选 ROI 作为交互原语 + 结构化标注入库驱动持续再训练**」三者整合成一个平台的工作极少。最接近的是 nuclei.io（Nat Biomed Eng 2024，无 LLM agent）与 TissueLab（arXiv:2509.20279，2025-09，共演化 agentic AI + 专家实时反馈 + 主动学习）。**TissueLab 是与 HE-Scope 定位最重叠的工作**（对比见 §2.6）。https://pubmed.ncbi.nlm.nih.gov/38898173/ ；https://arxiv.org/abs/2509.20279

### 2.1 主动学习（Active Learning）用于病理标注

#### 2.1.1 代表性工作与量化结论

- **Menon et al., ICPR 2022（CVIT-IIIT）**：CNN + 度量学习检索的 expert-in-the-loop 交互学习。专家给一个 query patch，系统按高维特征距离排序采样 K 个 patch 请专家复核，多轮微调。100K 结直肠癌 9 类 patch 上仅需约 **5% 标注量**即达 SOTA（其他交互方法需 35%–50%）；ICIAR 乳腺肿瘤分割上将扫描区域缩减至全片 **2%**（约 250 个 patch），IOU 85%。https://dl.acm.org/doi/10.1007/978-3-031-02444-3_38 ；https://cvit.iiit.ac.in/images/ConferencePapers/2021/Interactive_Learning.pdf
- **AL + Attention MIL**（ISBI 2023, arXiv:2303.01342）：对 attention-MIL 计算每个 WSI 的置信度，选最不确定切片请专家标注 ROI，配合 attention-guiding loss，CAMELYON17 上以极少量 ROI 标注显著提升分类精度、加速收敛。https://ui.adsabs.harvard.edu/abs/arXiv:2303.01342
- **MyriadAL**（arXiv:2310.16161）：对比学习编码器 + 伪标签精炼 + 不确定性组合查询，极低预算下仅标注 **5% 数据**即接近全监督精度。https://arxiv.org/abs/2310.16161
- **Annotation-Efficient Polyp Segmentation via AL**（arXiv:2403.14350）：uncertainty-weighted clustering（混合 uncertainty + diversity），标准「标注预算-性能曲线」协议。https://arxiv.org/html/2403.14350v1
- **Prototype sampling**（arXiv:2407.06363）：利用图文数据库（ARCH、OpenPath）中的原型 embedding 选代表性区域，缓解 AL 冷启动。https://arxiv.org/html/2407.06363
- 经典细胞分割 AL：Active deep learning reduces annotation burden（bioRxiv 2017, uncertainty sampling）。https://www.biorxiv.org/content/10.1101/211060v2.full-text

#### 2.1.2 查询策略谱系

1. **Uncertainty**：熵/置信度/margin、贝叶斯 CNN。
2. **Diversity / Coreset**：k-means 特征空间覆盖（CoreSet, Sener & Savarese 2018）。
3. **Hybrid**：不确定度加权聚类、uncertainty 列表去冗余、BEMPS 评分规则。
4. **检索/原型式**：度量学习近邻检索（Menon）、图文原型——这类「人给一个例子、系统找相似」与 HE-Scope 的「人圈 ROI」交互模式高度同构。

#### 2.1.3 度量协议与空白

- 标准协议：**标注预算-性能曲线**（x = 已标注样本数或预算比例，y = test accuracy / Dice），与 random sampling 基线对比；报告「达到全监督 X% 性能所需标注量」。
- 量化结论量级：病理 patch 分类 5% 标注 ≈ 全监督（Menon、MyriadAL）；WSI 分割扫描面积减至 2%；交互学习比传统 AL 再省约 7 倍标注。
- **benchmark 缺口**：Label-Efficient Medical Image Analysis 综述（arXiv:2303.12484）明确指出：各研究任务、器官、预算、划分协议碎片化，缺乏「单位标注成本换取的精度」的标准化基准，呼吁固定标签预算、成本感知指标、标准化 human-in-the-loop 评测协议。https://arxiv.org/html/2303.12484v5 —— **这正是 HE-Scope 可贡献的空白**。

#### 2.1.4 弱标注形态（与 HE-Scope 弱监督训练直接相关）

- 点标注核分割：Qu et al., MIDL 2019（Voronoi/高斯图从点生成伪标签，点标注省 88% 时间、框省 42%）。https://proceedings.mlr.press/v102/qu19a.html
- MIL/切片级弱监督：ABMIL、CLAM、IMIL（Phys Med Biol 2023, PMID 37311470）等已是主流；**人圈 ROI 可视为介于点标注与框标注之间的交互弱监督信号**。

### 2.2 人机协作诊断（pathologist + AI）

#### 2.2.1 奠基性 reader studies

- **Steiner et al., Am J Surg Pathol 2018（LYNA）**：6 名病理医生 × 70 张淋巴结切片，交叉双模式（assisted/unassisted + washout）。AI 辅助后微转移检出敏感度 91% vs 83%（p=0.02），阅片时间微转移 61s vs 116s、阴性 111s vs 137s，主观难度显著下降。**协作 > 单独任一方**。https://pubmed.ncbi.nlm.nih.gov/30312179/
- **Tschandl et al., Nat Med 2020**：皮肤癌 human–computer collaboration，协作优于人或 AI 单独。https://www.nature.com/articles/s41746-024-01031-w（综述引 10）
- **Raciti et al.（Paige）2023**：前列腺癌 AI 辅助，诊断时间 129s→58s（效率 +55%），敏感度 74.5%→93.5%。https://conexiant.com/internal-medicine/articles/breast-cancer-diagnosis-55-percent-gain-in-efficiency-with-ai-assisted-pathology/

#### 2.2.2 协作增益的度量范式

1. **多读者多病例交叉设计（MRMC crossover）**：同批医生在有/无 AI 两模式下读同一批病例，随机顺序 + 2–4 周 washout，医生当自己的对照 → 小样本也有统计功效（典型 6–8 名医生、70–658 病例）。
2. **指标**：准确率/敏感度/特异度（+ 混合效应 logistic 回归 adjusted OR）、阅片时间、诊断置信度（Likert）、inter-rater κ、采纳率分析（AI 正确/错误时的采纳率分别统计）。
3. **分层分析**：junior vs senior（junior 增益更大：+12.5 vs +4.4 pp，PulmoFoundation 2026）。

#### 2.2.3 最新大规模交叉 RCT（2026，设计模板）

- **PulmoFoundation**（肺病理 FM, arXiv:2605.25878, 2026-05）：注册交叉 RCT（NCT07157618），8 名病理医生 × 658 病例 × 4 任务，10,528 次判读；AI 辅助准确率 +8.5pp（adjusted OR=2.31），时间 −18.3%，κ 0.55→0.76。**关键负面发现：当 AI 错时，病理医生 77.5% 采纳错误标签（automation bias）**。https://arxiv.org/html/2605.25878v2
- **GRACE**（胃癌 FM, arXiv:2606.04792）：交叉 reader study，准确率 82.0%→89.9%（OR=1.987），时间 −14.9%，置信度 +9.0%；并做「错误纠正模式」分析。https://arxiv.org/abs/2606.04792
- **BRAVE**（乳腺 FM, arXiv:2605.08207）：平衡准确率 88.5%→95.1%（OR=3.14）。https://arxiv.org/html/2605.08207v1

#### 2.2.4 反面证据：协作不总是 > 单独

- **Vaccaro et al., Nat Hum Behav 2024（106 项实验 meta 分析）**：平均而言 human-AI 组合**不如**人/AI 中较强者单独；决策类任务（含医疗诊断）常为负协同；HAI 组合仅在 42% 实验中超过 AI 单独、85% 实验中超过人单独。后续再分析显示 human–LLM 团队在 AI 优势任务上更可能出现强协同。https://arxiv.org/pdf/2507.19486
- **HCT（Human Collective Teamwork, arXiv:2603.29866, 2026）**：独立聚合（AI 与两人投票、分歧时 tiebreak）优于「AI-as-advisor」模式——**建议式协作界面的设计本身决定增益**。https://arxiv.org/html/2603.29866v1
- 过度依赖风险：结直肠高级别异型增生研究（PMC12393786）显示 AI 辅助提升准确率但也有 over-reliance 讨论。https://pmc.ncbi.nlm.nih.gov/articles/PMC12393786/

**对 HE-Scope 的启示**：协作增益高度依赖任务与界面设计；「AI 错了人也跟着错」（77.5% 采纳率）是必须正面回应的风险——HE-Scope 的人圈选发起、人最终确认的交互结构恰好把决策权留在人侧，可包装为「anti-automation-bias 设计」。

### 2.3 交互式分割 / 校正

- **NuClick**（Med Image Anal 2020, PMID 32769053, 被引 200+）：核/细胞单击即精确分割，腺体用 squiggle 引导；点击/squiggle 作为辅助通道输入 CNN；用 NuClick 生成的标注训练的实例分割模型获 LYON19 第一名。https://pubmed.ncbi.nlm.nih.gov/32769053/
- **Clore**（arXiv:2603.27625, 2026）：click-based local refinement：前 n 次点击做全局粗分割，之后触发局部 patch 高分辨率精修；GlaS/NuCLS/DigestPath 上 NoC@90 达 SOTA（比 RITM/FocalClick 平均省 1.8 次点击）。评测协议：NoC@85/90、NoF@85/90、固定点击数 mDice 曲线。https://arxiv.org/html/2603.27625v1
- **PathoSAM**（arXiv:2502.00408, 2025）：SAM 在病理核分割的专门化，6 数据集 generalist，交互（点/框 prompt）与自动实例分割均 SOTA，已集成 QuPath 与 μSAM。https://arxiv.org/html/2502.00408v2
- **CellPilot**（arXiv:2411.15514）：统一自动 + 交互分割。
- 方法论谱系：fCN → DeepIGeoS → RITM/SimpleClick/FocalClick → SAM 系 → agent 化（MedSAM-Agent 2026，首个 RL 训练的交互分割 agent）。https://github.com/Nanboy-Ronan/awesome-medical-imaging-agents
- 综述：Single-click interactive cell nucleus segmentation（ScienceDirect 2025）。https://www.sciencedirect.com/science/article/pii/S1746809425009073

**与 HE-Scope 的关系**：「人圈 ROI」在交互分割文献中是标准 prompt 形态（box/scribble 比 click 信息量更高）；可直接复用 PathoSAM/Clore 作为 ROI 分割后端，把「人圈选」同时用作分割 prompt 和标注信号。

### 2.4 LLM / agent 作为标注助手（2024–2026）

#### 2.4.1 NLP 侧（方法论可迁移）

- **ActiveLLM**（arXiv:2405.10808）：用 GPT-4 等 LLM 替代不确定性度量挑选待标注样本，解决 AL 冷启动。https://arxiv.org/abs/2405.10808
- **A Survey of LLM-based Active Learning**（ACL 2025）：LLM 在 AL 两环节的角色——（a）选择/生成待标注样本，（b）直接作为标注器；人可保留在任一环。https://aclanthology.org/2025.acl-long.708.pdf
- **MoLLIA**（arXiv:2601.15773, 2026）：Mixture-of-LLMs in the loop，多 LLM 投票标注 + 传统查询策略组合。https://arxiv.org/html/2601.15773v1
- **RLTHF**：LLM 初标 + 仅 6–7% 人力定向修正即匹配全人工标注质量。https://kili-technology.com/blog/data-annotation-guide-how-to-achieve-high-quality-data-in-complex-ai-data-operations
- **Human–LLM 协作标注工作流**（Kang et al. 2024 等）：LLM 提议标签 + 解释、人验证低置信样本；LLM 更适合做 triage 和 QC 而非完全替代人。https://arxiv.org/html/2603.02569v1
- **Beyond Labels**（Yao et al. 2023）：标注者同时给标签和自由文本解释，双模型 AL 架构。

#### 2.4.2 病理/医学影像侧

- **PathChat + SlideSeek**（§1.1.1、§1.2.1）：病理 MLLM copilot 与多 agent WSI 诊断，但**闭环里没有人**：无标注入库、无再训练。https://www.nature.com/articles/s41586-024-07618-3 ；https://arxiv.org/html/2506.20964v1
- **病理 agent 生态（2025–2026 爆发）**：PathAgent（Navigator-Perceptor-Executor，training-free 模拟病理医生逐步推理，arXiv:2511.17052）、CPathAgent、PathFinder、WSI-Agents（MICCAI 2025）、Pathology-CoT、GIANT/PathNavigate/PathReasoning、NOVA（49 工具 + 代码执行）、TEAM-Agent（clinician-in-the-loop 修正的预后 agent, medRxiv 2026）、CellDX AI Autopilot（agent 引导病理医生训练/部署分类器, arXiv 2026）。清单：https://github.com/Nanboy-Ronan/awesome-medical-imaging-agents ；https://github.com/zhcz328/Awesome-Medical-Agents
- **标注 agent 的直接先例**：Colon-Bench（agentic 工作流做结肠镜密集病灶标注，528 视频、300K 框，2026）——「agent 自动标注生成 benchmark」已被接受为论文形式。
- 病理 VLM 视觉理解被质疑：「Do Pathology VLMs Truly See Pathology?」（arXiv:2607.21065）——VLM 形态学细粒度理解不可靠，**更凸显人在环监督/校正的必要性**。https://arxiv.org/html/2607.21065v1

**小结**：LLM-as-annotator / LLM-guided AL 在 NLP 已成体系；病理 agent 在「分析/导航/问答」侧爆发，但「**agent 辅助标注 + 质量控制 + 标注入库 + 再训练**」在病理领域基本空白（TEAM-Agent 与 CellDX 是最接近的边缘案例）。

### 2.5 数据飞轮 / 持续学习

- **数据飞轮范式**（NVIDIA glossary 等）：生产交互数据 → 清洗/合成训练信号 → 微调（SFT/LoRA/RLHF/蒸馏）→ 严格评测 → 灰度部署 → 收集内/外在反馈，自增强循环；高信号数据 = 错误预测、低置信输出、用户纠正。https://www.nvidia.com/en-us/glossary/data-flywheel/
- **医疗 AI 漂移监控**（Keeping Medical AI Healthy, arXiv:2506.17442）：性能漂移、输出分布漂移（BBSD/MMD、softmax 熵、energy score）、辅助误差估计、校准漂移检测与自适应窗口再校准。https://arxiv.org/html/2506.17442v1
- **Human-in-the-loop 治理**：不确定/异常预测路由给临床专家复核；研究显示 91% 医疗 AI 模型随时间失效 → 主动式周期性微调。https://censinet.com/perspectives/ai-model-drift-monitoring-ensuring-ongoing-performance-of-healthcare-ai-vendors
- **临床病理里的持续学习先例少**：检索「clinical deployment + continual learning + pathology」基本无直接系统；最接近的是 nuclei.io（研究场景快速建库建模型）和 TissueLab（宣称 continuously learns from clinicians）。监管侧（FDA PCCP、GMLP）列为后续调研点。
- 标注平台工程实践（Kili 等）：pre-labeling + confidence routing + honeypot 质控 + 防 automation over-trust 的校准轮次。https://kili-technology.com/blog/data-annotation-guide-how-to-achieve-high-quality-data-in-complex-ai-data-operations

**小结**：「数据飞轮」在工业 ML 是成熟概念，在**计算病理学术文献中几乎未被系统实现**——HE-Scope 的标注入库 → 重训练 → 再标注闭环可定位为「病理领域数据飞轮的第一个开源参考实现 + 实证研究」。

### 2.6 与 HE-Scope 最接近的三个工作对比 ★

| 维度 | nuclei.io (Nat Biomed Eng 2024) | TissueLab (arXiv:2509.20279, 2025-09) | PathAgent / SlideSeek 系 (2025) |
|---|---|---|---|
| 分析主体 | 传统 ML（形态特征 + XGBoost 类） | **LLM agent + 工具工厂** | LLM agent |
| 人在环角色 | 实时反馈纠正候选核，主动学习选样本 | 专家可视化中间结果并 refine | 协作评估（人只在评测端） |
| 交互原语 | 点击候选细胞/区域 | 会话式指令 + 中间结果修正 | ROI 导航（agent 自主为主） |
| 标注入库/数据资产 | 快速建数据集（会话级） | 宣称 continuous learning from clinicians | **无** |
| 弱监督再训练 | 有（active learning 微调） | 有（主动学习，分钟级适应新病种） | 无（training-free） |
| 数据库闭环 | 弱 | 部分（未以数据库为中心） | 无 |
| 评测 | 2 个 crossover 用户研究 | 任务量化对比 | VQA benchmark |
| URL | https://pubmed.ncbi.nlm.nih.gov/38898173/ | https://arxiv.org/abs/2509.20279 | https://arxiv.org/abs/2511.17052 |

逐一说明重叠与差异：

- **nuclei.io**（Stanford Zhi Huang/James Zou 团队）：占住「pathologist-AI collaboration framework」的高水平期刊先例与「主动学习 + 实时反馈 + crossover 用户研究」范式。**重叠**：人在环纠正 + 主动学习再训练。**差异**：无 LLM/VLM agent；交互是「系统列候选、人点确认」（AI 主导）；反馈为会话级，不以标注数据库为中心、无跨任务复用协议。
- **TissueLab**（同一团队从 nuclei.io 演进）：占住「co-evolving agentic system + expert feedback + active learning」叙事，宣称从临床医生持续学习、分钟级适应新病种。**重叠度最高**。**差异**：反馈仍是会话式共演化（会话反馈不入库）；无「圈选 ROI」这一统一交互原语；标注不作为一等公民资产沉淀；未给出跨任务弱监督再训练协议与数据库闭环消融。
- **PathAgent / SlideSeek 一脉**：占住「agent 模拟病理医生推理」叙事。**重叠**：LLM agent 分析 WSI、ROI 证据链。**差异**：training-free、会话级，人只在评测端出现，**完全没有标注入库-重训练闭环**。

**威胁评估**：TissueLab 若正式发表于高水平期刊并开源其持续学习模块，HE-Scope 窗口收窄；nuclei.io 与 PathAgent 系分别封住「人机协作框架」与「agent 推理」两个叙事端口——novelty 必须落在**数据库为中心的闭环**这一三者交汇处（见 §4）。

---

## 3. 评测格局综述

### 3.1 WSI 分类：已饱和

- **标准协议**：CLAM（Lu et al., Nat Biomed Eng 2021）确立的 10-fold Monte Carlo 交叉验证（80/10/10 按 case 划分），mean test AUC ± std + 「数据效率曲线」（100/75/50/25/10% 训练集）范式，至今是弱监督 WSI 分类事实标准。https://pmc.ncbi.nlm.nih.gov/articles/PMC8711640/
- **现状**：NSCLC 亚型（LUAD vs LUSC，TCGA+CPTAC 1967 slides）CLAM AUC 0.956、外部 0.975，slide-encoder Threads 外部 AUC 0.984；RCC 亚型 macro-AUC 0.991；CAMELYON16/17 淋巴结转移检测 AUC 0.953。**亚型分类 AUC 0.95+，除非换评测维度（few-shot、外部泛化、交互式），无差异化空间。** https://arxiv.org/html/2501.16652v1 ；https://arxiv.org/html/2505.20510v1

### 3.2 突变 / 生物标志物预测：头部空间仍在，单点竞争激烈

- **LUAD 突变**：Coudray et al.（Nat Med 2018）经典任务集（STK11/EGFR/FAT1/SETBP1/KRAS/TP53），held-out AUC 0.733–0.856。https://pmc.ncbi.nlm.nih.gov/articles/PMC9847512/
- **EGFR（临床级标杆）**：EAGLE（Campanella et al., Nat Med 2025）N=8,461 国际多中心，内部 AUC 0.847、外部 0.870、前瞻 silent trial 0.890，可减少 43% 快速分子检测——「临床转化叙事」模板。https://pmc.ncbi.nlm.nih.gov/articles/PMC12443599/
- **MSI / HRD**：DeepSMILE TCGA-CRC AUROC 0.87、TCGA-BRCA HRD 0.81；Swin-T MCO 4-fold 0.926、外部 0.904。https://arxiv.org/html/2107.09405 ；https://pmc.ncbi.nlm.nih.gov/articles/PMC10073932/
- **TMB**：内部 AUC 0.64–0.99、外部验证普遍掉 0.10–0.15；无统一协议、数字不可比，不宜作主攻 benchmark。https://www.mdpi.com/2076-3417/16/3/1340
- **低患病率统一评测（STAMP 协议, Nat Biomed Eng 2025）**：31 任务 × 19 FM；低患病率任务平均 AUROC：Prov-GigaPath 0.74 > Virchow 0.73 > CONCH 0.72；全 31 任务平均 CONCH/Virchow2 0.71 并列第一。https://www.nature.com/articles/s41551-025-01516-3
- **FM 时代基线**：Prov-GigaPath 在 LUAD 五基因突变上平均 macro-AUROC 仅 0.626——FM embedding + 简单 MIL 在低患病率突变上远未饱和。https://www.mdpi.com/2073-4425/17/4/371

### 3.3 生存预测：协议混乱

- 5-fold CV，TCGA 五癌种组合不一、**各论文 split 不统一，横向比较困难**。病理单模态 MIL 典型 c-index 0.60–0.70；多模态 SOTA（DSCASurv/HySurvPred）0.65–0.86 不等。https://pmc.ncbi.nlm.nih.gov/articles/PMC11926988/ ；https://arxiv.org/html/2503.13862
- STAMP 统一评测中 7 个预后任务平均 AUROC 最高仅 0.63（CONCH）——**预后是 FM 最弱维度**。

### 3.4 大规模统一评测套件（2024–2026）

- **PathBench（Ma et al., arXiv:2505.20202, 2025-05）**：15,888 WSI / 8,549 患者 / 10 家医院（私有数据防泄漏），64+ 任务、19 个 PFM、自动化 leaderboard。榜首 Virchow2（rank 5.0）> H-optimus-1（5.9）> H-optimus-0（6.6）> UNI2（7.1）> mSTAR（7.4）；视觉 FM 仍强于 VLM。局限：数据私有，只能打榜不能做方法学。https://arxiv.org/abs/2505.20202
- **eva（kaiko.ai, MIDL 2024）**：开源框架 + leaderboard（patch 级 BACH/CRC/MHIST/PCam + slide 级 Camelyon16/PANDA + tile 分割 CoNSeP/MoNuSAC）。结论：**没有常胜模型**，病理预训练 FM 普遍优于自然图像 FM。https://github.com/kaiko-ai/eva ；https://openreview.net/pdf?id=FNBQOPj18N
- **STAMP / NBE 统一评测（El Nahhas et al., Nat Biomed Eng 2025）**：31 个弱监督任务、19 FM、TCGA→CPTAC/DACHS/Kiel 外部验证；含 n=75/150/300 **稀缺数据协议**（PRISM/Virchow2/CONCH 小数据领先）——目前最接近「few-shot 适应标准协议」的公开评测。https://www.nature.com/articles/s41551-025-01516-3
- **HEST / HEST-1k（Jaume et al., NeurIPS 2024）**：空间转录组 × H&E，9 个任务从 112μm patch 预测 top-50 高变基因表达（PCC，官方 split）；PCC 普遍 0.1–0.35，属「分子形态关联」前沿。https://arxiv.org/html/2406.16192v2
- **PathBench（Sun et al., IEEE TMI 2025，同名不同物）**：面向 LMM 的 PatchVQA（5,382 图/6,335 MCQ，防 shortcut 干扰项）+ WSICap（7,000 份报告）+ WSIVQA。https://pubmed.ncbi.nlm.nih.gov/40601458/

### 3.5 VQA / 语言类 benchmark：公信力在重构

| Benchmark | 规模 | 现状（2026） | 出处 |
|---|---|---|---|
| PathVQA (2020) | 32,799 问 | 封闭式刷到 ~95%；2026 年审计证明存在严重 text-prior（不看图也能拿 44–53%）→ **公信力下降** | https://ar5iv.labs.arxiv.org/html/2003.10286 ；https://arxiv.org/html/2607.21065v1 |
| PathMMU (ECCV 2024) | 33,428 MCQ，7 名病理医师审核 | GPT-4V 仅 49.8% vs 人类 71.8%；CPathAgent 78.6–80.5%（已超人类基线） | https://arxiv.org/abs/2401.16355 |
| SlideBench / WSI-VQA | 734 caption + 15K VQA | SlideChat 22 任务中 18 项 SOTA；以「选择题化」为主，真实开放式 WSI 理解仍是空白 | https://arxiv.org/html/2410.11761 |
| PathMMU-HR² | 1,688 专家验证多尺度 VQA | CPathAgent 88.6%，Gemini-2.5-Pro 76.4 | https://arxiv.org/html/2505.20510v1 |
| PathView-Bench (2026-07) | 多尺度细粒度 MLLM 评测 | 最新，格局未定 | https://arxiv.org/html/2607.28318v1 |

**空白**：(a) WSI 级开放式 QA/报告的可信自动评测（目前依赖 GPT-score）；(b) 无「视觉证据必要性」校验的老 benchmark 公信力崩塌中；(c) **没有任何语言类 benchmark 评估「人-agent 交互过程」**。

### 3.6 主动学习 / 标注效率评测：无统一 benchmark，四类可拼接协议

1. **CLAM 数据效率曲线**：10-fold CV 下训练集 100/75/50/25/10% 报 test AUC + 外部 BWH 队列——每个弱监督 WSI 论文的标配。
2. **STAMP 稀缺数据协议**：n=300/150/75 患者子采样 + 全量外部验证，衡量 FM 的 label efficiency。
3. **分割主动学习（SHAL, arXiv:2607.09831, 2026-07）**：TCGA-CRC slide 级 AL——26% 标注预算达 Dice≥0.80（基线需 37%），满预算 macro Dice 0.846，5 个外部队列泛化 gap。度量范式：性能 vs 标注预算曲线 + 达到阈值的预算比例。https://arxiv.org/abs/2607.09831
4. **交互分割 clicks 度量**：NoC@85 / NoC@90、1-click IoU、K-NoC@90（SimpleClick/PseudoClick 等标准）。https://pmc.ncbi.nlm.nih.gov/articles/PMC11378330/

**空白即机会**：「**性能 vs 人工交互量（ROI 圈选次数/点击数/分钟）**」在 WSI 级任务上没有标准协议——HE-Scope 天然记录每一次人-agent 交互，可直接定义并占据这个度量。

### 3.7 Agentic / 交互式评测：刚起步，头部分数极低

#### 3.7.1 病理专属

- **PathAgentBench（arXiv:2607.19261, 2026-07，NUS+PuzzleLogic+协和）——最重要对标物**。1,822 张 TCGA WSI + 17,135 条病理医师标注「诊断路径」（2.5×→10×→40× 三级嵌套 bbox + findings + 诊断），16 器官；另有 190 张私有乳腺 WSI。四任务：
  - T1 证据解读（image→text，10,284 MCQ）：最强 Gemini-3-Flash 63.5%，专家 93.6%；
  - T2 证据核验（text→image，10,284 MCQ）：最强 67.7%；
  - **T3 证据获取（agent 导航）：全面崩坏**——文本引导定位最强 mIoU < 0.09，被「父框中心点」启发式（IoU 0.25–0.28）3–4 倍吊打；自主探索无条件命中率 52.2%→18.5%→**2.0%**（2.5×→40×）；病理专用模型甚至发不出合法 bbox 工具调用；
  - T4 证据整合（51,167 MCQ）：最强 ~93%，接近饱和。
  - 官方结论：**当前 VLM 是好的 evidence scorer、糟糕的 autonomous planner；证据获取是整个 agent 范式的瓶颈**。https://arxiv.org/html/2607.19261v1
- **HealthAgentBench**：含交互式 WSI 探索，但病理仅一个肿瘤定位任务。
- 病理 agent 系统（CPathAgent、PathAgent、TissueLab、MMNavAgent、LAMMI-pathology 等）评测各自为政、无法互比——**标准化窗口正在打开**。

#### 3.7.2 相邻领域可借鉴

| Benchmark | 任务 | 头部分数 | 借鉴点 |
|---|---|---|---|
| ScienceAgentBench (2024) | 102 个数据驱动发现任务 | Claude-3.5-Sonnet self-debug SR 34.3%；o1 42.2%；专家知识 +13.7 SR | 「expert-provided knowledge」维度 ≈ 人-agent 闭环中的人类指导 |
| MLE-bench (OpenAI, ICLR 2025) | 75 个 Kaggle 竞赛 | o1-preview+AIDE 奖牌 16.9%（pass@1）→34.1%（pass@8） | 人类 leaderboard 作天然基线；资源-性能 scaling |
| MLGym (Meta, 2025) | 13 个 ML 研究任务 | AUP 指标 | 相对最优者的性能剖面 |
| MLAgentBench | 13 任务 | ReAct+Claude Opus 37.5% SR | 文件/代码交互环境设计 |

来源：https://arxiv.org/html/2410.05080v2 ；https://arxiv.org/abs/2410.07095 ；https://arxiv.org/html/2506.08800v2

**结论**：病理 agent 评测刚起步且 T3 近乎全军覆没，正是「人-agent 闭环」范式能给出数量级改进的地方；ScienceAgentBench 已证明「专家知识注入」是有效且被社区接受的评测维度。

### 3.8 成熟度矩阵（一页总结）

| 评测层 | 代表 benchmark | 成熟度 | 头部水平 | 空白/机会 |
|---|---|---|---|---|
| WSI 亚型分类 | CLAM 协议、CAMELYON | ★★★★★ 饱和 | AUC 0.95+ | 无（除非换维度） |
| 突变/标志物 | STAMP 31 任务、EAGLE | ★★★☆ | 低患病率 AUROC ~0.63–0.74 | few-shot/低患病率仍有空间 |
| 生存预测 | 各家 split 不一 | ★★☆ 混乱 | c-index 0.60–0.70 | 协议不统一，不宜主攻 |
| FM 统一评测 | PathBench/eva/STAMP/HEST | ★★★★ | 头部差距 <2% | 只作平台 baseline，不做主攻 |
| VQA | PathMMU/SlideBench | ★★★★（选择题化） | 专用模型已超人类基线 | 开放式 WSI QA、text-prior 纠偏 |
| 标注效率 | 无统一 benchmark | ★★ 碎片化 | — | **「性能 vs 人类交互预算」协议空白** |
| Agentic 证据获取 | PathAgentBench T3 | ★ 刚起步 | mIoU<0.09、命中率 2% | **human-in-the-loop 变体空白，数量级改进空间** |
| 人-agent 交互过程评测 | 无 | 0 | — | **完全空白，先定义者受益** |

---

## 4. HE-Scope 的定位与 novelty 分析

### 4.1 文献坐标：四个子领域的交集

HE-Scope 的核心闭环（人圈 ROI → agent 分析 → 标注入库 → 弱监督训练 → 再标注）位于四个子领域交汇处（R4 §7.1）：

- **A. 主动学习 / 交互学习**（§2.1）：人提供标注、系统选最有价值样本——成熟，但驱动者是模型不确定性而非 LLM agent。
- **B. 人机协作诊断**（§2.2）：AI 辅助人——成熟，但一般是静态模型的建议式协作，无数据回流。
- **C. 交互式分割**（§2.3）：人点击/圈选校正——成熟，但交互只用于当前分割，不沉淀为可复用标注资产驱动全局再训练。
- **D. 病理 LLM agent**（§1、§2.4.2）：agent 分析 WSI——2025–2026 爆发，但几乎全部是 training-free、会话级、**无闭环学习**。

**「人圈选发起 + agent 分析 + 结构化入库 + 弱监督再训练 + 再标注」全链整合 = 空白。**

### 4.2 定位论证：「以标注数据库为中心的人-agent 闭环 WSI 分析系统」

三条最强支撑证据（均来自 §1–§3 的事实）：

1. **路线背书**：SPARK（Nature Medicine 2026）证明「LLM agent 写代码/调工具做定量病理分析」优于「VLM 端到端回答」，且支持与病理医生交互（人提概念、agent 实现）——HE-Scope 的 code-agent 桥接定位与领域最强背书一致。https://www.nature.com/articles/s41591-026-04357-y
2. **瓶颈对接**：PathAgentBench（2026-07）证明纯 VLM agent 的瓶颈在**证据获取**（mIoU<0.09、40× 命中率 2.0%）而非推理（T4 已 ~93%）——「人圈 ROI」正是用最少人类交互补齐 planner 短板的范式；ScienceAgentBench 已证明「专家知识注入」是被社区接受的评测维度。https://arxiv.org/html/2607.19261v1
3. **数据飞轮独占性**：Pathology-CoT 证明 viewer 行为日志可变成高价值 agent 训练数据（标注提速 6 倍）；而「数据飞轮」在计算病理学术文献中几乎未被系统实现（§2.5）——观测平台天然拥有该数据入口，是纯模型产品无法复制的禀赋。https://arxiv.org/abs/2510.04587

差异化空间（observed gap，非推断）：

1. **数据库为一等公民的闭环**：nuclei.io/TissueLab 的反馈是会话级的；HE-Scope 把每次人圈选-agent 分析-确认/修正**结构化沉淀为可查询、可版本化、跨任务复用的标注数据库**（ROI 坐标 × 形态描述 × 标签 × 交互历史）。文献中没有「病理交互标注数据库 + 持续再训练协议」的系统化工作与公开基准（Label-Efficient 综述明确指出 benchmark 缺失，§2.1.3）。
2. **人圈选 ROI 作为统一交互原语**：区别于 nuclei.io 的「系统列候选、人点确认」（AI 主导）和 PathChat 2 的「人圈后问答」（无学习），HE-Scope 是「**人发起圈选 → agent 分析并给出可修正结果 → 确认即入库**」——人主导发起、agent 主导分析、数据库沉淀共识，兼有 anti-automation-bias 的结构优势（§2.2.4）。
3. **弱监督再训练协议**：人圈选天然是介于点/框/scribble 之间的弱标注（§2.1.4、§2.3 文献支持），可定义「圈选预算-下游性能」曲线这一新评测协议（区别于 AL 的样本预算协议）。
4. **marimo / 可复现计算载体**：交互分析与可执行 notebook 同一环境，标注过程本身可复现——工程差异化（论文加分项而非主 claim；呼应 PathLab 的「配置即资产」理念，§1.3.2）。

### 4.3 Novelty claim 草案

**主 claim**：「**首个以标注数据库为中心的人-agent 闭环病理分析系统**」（human-initiated, agent-analyzed, database-accumulated, weakly-supervised-retrained closed loop for WSI analysis），由三个可拆分投稿的贡献支撑：

1. **系统论文**（Nat Biomed Eng / Nat Commun / Med Image Anal 风格）：闭环架构 + 2 个 crossover 用户研究（对标 nuclei.io：各 6–8 名病理医生、2 任务，MRMC crossover + 2–4 周 washout，§2.2.2 范式）+ 闭环学习曲线（随交互轮数，下游任务性能/标注成本变化）。
2. **方法贡献**：圈选驱动的弱监督再训练协议 + 「圈选预算-性能曲线」评测范式，与 random/uncertainty/coreset 主动学习基线对比，量化标注节省（对标 §2.1.1 的 5%、2% 数字量级与 SHAL 的 26% vs 37%，§3.6）。
3. **基准/数据贡献**：交互标注数据库 + 闭环学习 benchmark（呼应 Label-Efficient 综述指出的标准化缺失）——引用率最高的切口；并可定义 PathAgentBench 的 human-in-the-loop 变体：允许 k 次人类 ROI 交互（k∈{0,1,3,5}），报告 T3 定位 mIoU / 命中率 / 下游 T1+T4 联合诊断准确率 vs k 的曲线，预期 k=1 即把 40× 命中率从 2% 拉到接近专家路径覆盖率，形成「数量级改进 + 新协议」双重贡献（§3.7）。

**必须做的对照实验**（否则会被审稿人用 TissueLab/nuclei.io 拒掉）：

- (a) **vs TissueLab 式「会话反馈不入库」消融**：证明数据库沉淀带来跨会话/跨任务增益（同一标注预算下，入库-复用 vs 会话级反馈的下游性能差）。
- (b) **自动化偏倚测量**：报告错误 agent 输出的采纳/纠正率，证明人发起结构的安全性（对标 PulmoFoundation 的 77.5% 警示数字，§2.2.3；这是 2026 年读者研究的可信度红线）。
- (c) **闭环 vs 一次性主动学习**的标注效率对比（同预算下的学习曲线差）。

### 4.4 风险与缓解

| 风险 | 说明 | 缓解 |
|---|---|---|
| **TissueLab 威胁（最高）** | 与 HE-Scope 重叠度最高（同一团队从 nuclei.io 演进，已占住「co-evolving agentic + expert feedback + active learning」叙事）；若正式发表于高水平期刊并开源持续学习模块，窗口收窄 | 尽快把「数据库闭环 + 圈选交互协议 + benchmark」三点做成不可被其覆盖的差异化证据；所有论文显式做 §4.3 (a) 消融 |
| **PathAgentBench 数据可得性待确认** | benchmark 太新（2026-07），需确认其 GitHub 数据（1,822 WSI / 17,135 路径 / 50-slide Mode A 子集）已放出 | 立项前核查；若未放出，先用自建子集复现 T3 协议 |
| 协作增益不一定为正 | Vaccaro meta 分析：决策类任务常为负协同（§2.2.4） | 界面设计保留人最终确认权；预先注册 crossover 设计；报告采纳率分解 |
| 评测被质疑可投机 | Lancet 述评：agentic 工作流 LLM 评测仍在初步阶段 | 遵循 CONSORT-AI / STARD-AI / CLAIM 报告规范（标注者资质、标注工具、去标识逐项对应）；固定预算、成本感知指标 |
| VLM 组件不可靠 | 「Do Pathology VLMs Truly See Pathology?」质疑细粒度形态理解；SlideSeek 幻觉 IHC | 内建模态合法性约束、工具可靠性追踪（PathoSage 模式）、坐标级证据链 |

### 4.5 支撑性 FM 选型（服务闭环，详见 R2）

闭环的弱监督再训练与检索/定位工具依赖 tile/slide embedding FM。要点（R2）：

- 头部模型（Virchow2、H-optimus-1/0、UNI2、Prov-GigaPath、GPFM）在常规任务差距 <2%，**选型更应看 license、推理成本与生态**。
- 平台首选：**GPFM**（MIT，72 任务平均 rank 1.6，蒸馏自 UNI+Phikon+CONCH）或 **H-optimus-0**（Apache 2.0，独立临床 benchmark 双料第一）；学术定位可选 UNI2-h（CC-BY-NC-ND）。slide 级升级路径：TITAN（非商用）或 GigaPath-Flash（Apache 2.0）。
- 检索/定位工具优先 CONCH 类对比学习 VLM（§1.1.3）；接入统一层推荐 TRIDENT。https://github.com/mahmoodlab/TRIDENT
- 风险：license（CC-BY-NC-ND 禁止商用）、gated 审批、timm/transformers 版本碎片化、倍率/预处理一致性。

### 4.6 一句话定位

HE-Scope 处于「主动学习标注（Menon/nuclei.io 一脉）× 病理 LLM agent（PathAgent/TissueLab 一脉）× 交互式分割（NuClick/PathoSAM 一脉）」三线交汇处；三线各自的代表工作都成熟，但**交汇处（尤其以数据库为核心的持续闭环）在 2026-08 时点仍是可主张的空白**；novelty 成立的关键是与 TissueLab 的显式区分实验和闭环学习曲线的量化证据。

---

## 附录 A：核心参考文献速查表

### 病理 AI Agent 系统

| 系统 | 出处 | URL |
|---|---|---|
| PathChat | Nature 2024 | https://www.nature.com/articles/s41586-024-07618-3 |
| SlideSeek / PathChat+ | arXiv:2506.20964 | https://arxiv.org/html/2506.20964v2 |
| PathChat 2 / DX / Judith / Modella | 官网 | https://www.modella.ai/pathchat |
| AstraZeneca 收购 Modella | 2026-01 | https://www.biopharmatrend.com/news/astrazeneca-acquires-modella-ai-to-integrate-foundation-models-into-global-oncology-rd-1463/ |
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
| 病理 agent 清单 | GitHub | https://github.com/Nanboy-Ronan/awesome-medical-imaging-agents |

### 人机协作 / 主动学习

| 工作 | 出处 | URL |
|---|---|---|
| nuclei.io | Nat Biomed Eng 2024 | https://pubmed.ncbi.nlm.nih.gov/38898173/ |
| TissueLab | arXiv:2509.20279 | https://arxiv.org/abs/2509.20279 |
| Menon 交互学习 | ICPR 2022 | https://cvit.iiit.ac.in/images/ConferencePapers/2021/Interactive_Learning.pdf |
| MyriadAL | arXiv:2310.16161 | https://arxiv.org/abs/2310.16161 |
| AL + Attention MIL | ISBI 2023 | https://ui.adsabs.harvard.edu/abs/arXiv:2303.01342 |
| Label-Efficient MIA 综述 | arXiv:2303.12484 | https://arxiv.org/html/2303.12484v5 |
| Steiner LYNA | AJSP 2018 | https://pubmed.ncbi.nlm.nih.gov/30312179/ |
| PulmoFoundation RCT | arXiv:2605.25878 | https://arxiv.org/html/2605.25878v2 |
| GRACE | arXiv:2606.04792 | https://arxiv.org/abs/2606.04792 |
| BRAVE | arXiv:2605.08207 | https://arxiv.org/html/2605.08207v1 |
| Vaccaro meta | Nat Hum Behav 2024 | https://arxiv.org/pdf/2507.19486 |
| HCT | arXiv:2603.29866 | https://arxiv.org/html/2603.29866v1 |
| NuClick | MedIA 2020 | https://pubmed.ncbi.nlm.nih.gov/32769053/ |
| Clore | arXiv:2603.27625 | https://arxiv.org/html/2603.27625v1 |
| PathoSAM | arXiv:2502.00408 | https://arxiv.org/html/2502.00408v2 |
| ActiveLLM | arXiv:2405.10808 | https://arxiv.org/abs/2405.10808 |
| LLM-AL 综述 | ACL 2025 | https://aclanthology.org/2025.acl-long.708.pdf |
| 医疗 AI 漂移 | arXiv:2506.17442 | https://arxiv.org/html/2506.17442v1 |
| NVIDIA 数据飞轮 | 官网 | https://www.nvidia.com/en-us/glossary/data-flywheel/ |
| CONSORT-AI / 报告规范 | PMC | https://pmc.ncbi.nlm.nih.gov/articles/PMC8183333/ |

### Benchmark

| 基准 | 出处 | URL |
|---|---|---|
| CLAM | Nat Biomed Eng 2021 | https://pmc.ncbi.nlm.nih.gov/articles/PMC8711640/ |
| Coudray DeepPATH | Nat Med 2018 | https://pmc.ncbi.nlm.nih.gov/articles/PMC9847512/ |
| EAGLE | Nat Med 2025 | https://pmc.ncbi.nlm.nih.gov/articles/PMC12443599/ |
| STAMP / NBE 31 任务 | Nat Biomed Eng 2025 | https://www.nature.com/articles/s41551-025-01516-3 |
| PathBench (Ma) | arXiv:2505.20202 | https://arxiv.org/abs/2505.20202 |
| PathBench (Sun, TMI) | IEEE TMI 2025 | https://pubmed.ncbi.nlm.nih.gov/40601458/ |
| eva | MIDL 2024 | https://github.com/kaiko-ai/eva |
| HEST-1k | NeurIPS 2024 | https://arxiv.org/html/2406.16192v2 |
| PathMMU | ECCV 2024 | https://arxiv.org/abs/2401.16355 |
| PathVQA text-prior 审计 | arXiv:2607.21065 | https://arxiv.org/html/2607.21065v1 |
| PathAgentBench | arXiv:2607.19261 | https://arxiv.org/html/2607.19261v1 |
| PathView-Bench | arXiv:2607.28318 | https://arxiv.org/html/2607.28318v1 |
| SHAL | arXiv:2607.09831 | https://arxiv.org/abs/2607.09831 |
| ScienceAgentBench | arXiv:2410.05080 | https://arxiv.org/html/2410.05080v2 |
| MLE-bench | arXiv:2410.07095 | https://arxiv.org/abs/2410.07095 |

### FM 选型（支撑性，详见 R2）

GPFM（https://bio.rodeo/models/gpfm）、UNI（https://github.com/mahmoodlab/UNI）、CONCH（https://github.com/mahmoodlab/CONCH）、Virchow2（https://www.paige.ai/foundation-models）、H-optimus（https://www.bioptimus.com/h-optimus）、Prov-GigaPath（https://github.com/prov-gigapath/prov-gigapath）、TITAN（https://github.com/mahmoodlab/TITAN）、TRIDENT 统一接入层（https://github.com/mahmoodlab/TRIDENT）。

---

*本文档由 R1–R4 调研报告综合而成；新增文献或数字更新时，请同步更新对应章节与附录速查表，并保持「事实必附 URL」的约定。*
