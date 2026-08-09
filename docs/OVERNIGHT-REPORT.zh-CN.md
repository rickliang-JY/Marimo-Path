# HE-Scope 过夜任务成果报告（2026-08-08 夜 → 08-09 晨）

[English](OVERNIGHT-REPORT.md) · **简体中文**

## 任务回顾
用户三大指令：①调研 pathology agent 论文现状；②基于论文定下一步方向（学术 benchmark + 开源六维度迭代）；③LoopX 结合或自研 loop engineering skill 的决策。要求：先整体计划，再按计划修改。

## 执行过程
- **Stage 1**：6 路并行研究蜂群（病理 agent / 基础模型 / benchmark / 人机协作 / 开源生态 / LoopX 精读），60+ 次有效搜索 + 仓库精读，原始报告在 `/mnt/agents/output/research/r1~r6-*.md`；
- **Stage 2**：两份综合文档——`PAPERS.md`（550 行文献综述，146 处引用）、`STRATEGY.md`（304 行战略决策，含里程碑表）；
- **Stage 3**：`ROADMAP.md` §6 更新为最终决策；
- **Stage 4**：两路并行实施（worktree 隔离）+ 合并；
- **Stage 5**：真实 marimo-pair 内核 live 验证 A–E 全 PASS；**246 tests 全绿**（208 → 246）。

## 核心研究结论（速览）
1. **领域**：2025–2026 病理 agent 爆发（PathChat 系、SPARK、PathFinder 等）；PathAgentBench 关键负结果——纯 agent 证据定位 mIoU<0.09、命中率 2%，「找证据」是最大短板，恰是我们人-agent 闭环的切入点；
2. **novelty 定位**：「首个以标注数据库为中心的人-agent 闭环 WSI 分析系统」；最大威胁 TissueLab（必须做会话反馈入库消融对照）；
3. **学术三目标**：A=PathAgentBench human-in-the-loop 变体（k 次人圈 ROI 曲线）；B=「AUROC vs 人类交互预算」标注效率协议（独家数据优势）；C=eva+HEST parity 背书；
4. **FM 选型**：GPFM（MIT）默认 / UNI2-h 仅学术 / H-optimus-0（Apache）商用；CC-BY-NC-ND 红线；
5. **LoopX**：不接为依赖（双事实源冲突、过年轻）；自研 DB 薄 loop 层 + he-scope-loop SKILL.md，6 周跟踪其 provider RFC 复评；
6. **开源空位**：「marimo-native + agent-native」无人占据；Trident(.h5)/QuPath(GeoJSON) 共生策略；JOSS 论文绑引用。

## 已实施修改（master，git log 可查）
- **hescope/embeddings.py**：FM encoder factory——GPFM/UNI2-h/H-optimus-0/resnet18 注册表（license/gated 元数据）、默认红线（nc-nd 永不进默认）、懒加载（import 零 torch 零网络）、`embed_tiles`；
- **hescope/ml.py**：`HESCOPE_EMBEDDER` 可选 embedding backend（训练/heatmap/predict 全路径），失败自动回退 56 维 + warning，ModelInfo 记录 encoder/dim（向后兼容）；
- **hescope/db.py**：`interactions` 交互轨迹表 v1（6 种 kind，数据飞轮/自动化偏倚研究奠基）+ `InteractionRepo`；
- **三个新 agent 工具**（live 验证通过）：`annotate_roi`（回写标注）、`query_annotations`（查标注库）、`get_slide_info()`；全部记录 interactions；
- **hescope/geojson.py**：QuPath 兼容 GeoJSON 导出（classification 从 label 映射）；
- **skills/he-scope/SKILL.md**：仓库内置 agent skill（Trident 模式）：pair 步骤、6 工具 schema、读圈选→分析→回写→训练工作流、loop 模式指引；
- AGENTS.md / app.py 工具说明同步更新。

## 待用户决策项（按 STRATEGY.md 里程碑表）
1. **W3–4 是否开工**：GPFM 真机加载验证（需 GPU——走 molab spike 还是等本地环境？）+ 学术目标 A 的 k=0 基线复现（需确认 PathAgentBench GitHub 数据是否已放出）；
2. **molab 链路 spike** 排期（HE-Scope 上 molab + 本地 agent pair 连通）；
3. **loop 薄层**（campaigns/gates 表 + CLI + he-scope-loop skill）开工时机；
4. 是否推 GitHub 开源（uvx 首跑体验 + pooch 示例数据是 P0 前置项）。

## 已知限制
- GPFM 真实加载路径未经真机验证（timm hf-hub，mock 测试已覆盖代码路径）；
- send 工具的设计行为：rois 列表非空时重发最后一个 ROI，live 选区回退仅在空列表时触发（文档化行为）；
- 研究备忘：生存预测/TMB/分割主攻/PathVQA 已明确排除（理由见 STRATEGY.md §1）。
