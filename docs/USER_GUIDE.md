# HE-Scope 使用指南

H&E 病理图像观测平台 —— 基于 marimo 的交互式全切片查看器，支持圈选 ROI、
标注管理、TCGA 公共数据接入，并可与 code agent(Kimi Code / Claude Code /
Codex / Hermes 等)实时联动。

---

## 目录

1. [快速开始](#1-快速开始)
2. [界面分区与操作](#2-界面分区与操作)
3. [圈选(ROI)与测量](#3-圈选roi与测量)
4. [标注与数据库](#4-标注与数据库)
5. [TCGA 公共数据](#5-tcga-公共数据)
6. [分析功能(统计 / QC / 热力图 / 训练分类器)](#6-分析功能统计--qc--热力图--训练分类器)
7. [与 code agent 联动](#7-与-code-agent-联动)
8. [常见问题(FAQ)](#8-常见问题faq)
9. [数据存储位置一览](#9-数据存储位置一览)
10. [降级模式](#10-降级模式)
11. [后续路线](#11-后续路线)

---

## 1. 快速开始

```bash
cd project
pip install -e .        # 依赖以 pyproject.toml 为准;requirements.txt 是兼容入口
hescope app             # = marimo edit app.py --no-token(开发模式,agent 联动需要)

# 等价的手动写法(在仓库根目录):
# marimo edit app.py --no-token      # 开发模式
# marimo run app.py                  # 纯使用模式(不暴露代码编辑器)
```

浏览器打开提示的地址(默认 `http://localhost:2718`)。

> **注意**:`--no-token` 很重要——这是 marimo-pair 自动发现会话的前提
> (详见第 7 节)。在可信环境内使用。

第一次使用时,点击 **"Generate & open demo slide"** 按钮,平台会在本地
生成一张 6000×4000 的合成 H&E 演示切片(约 15 秒),随后所有功能都可以
立即体验。

---

## 2. 界面分区与操作

应用为"左侧边栏 + 主区"布局。**全应用只有一张主图**:缩放、平移、圈选
都在同一张图上完成。

### 2.1 左侧边栏(Sidebar)
自上而下四个区块:
- **Open slide(加载切片)**:三种方式打开图像——Demo slide 一键生成
  合成 H&E 演示切片;粘贴本地路径(.svs / .tiff / .ndpi / .png / .jpg)
  点 Open;或直接上传文件。打开的切片自动登记到数据库(若启用)。
- **Display(显示调节)**:brightness / contrast / gamma 滑杆、
  channel view 通道视图(`rgb`、单通道 `r/g/b` 灰度、`hematoxylin` /
  `eosin` H&E 颜色反卷积通道)、show ROI overlays 开关。仅影响显示,
  不改数据,也不影响选区坐标。
- **Navigator(导航器)**:200px 缩略图,红框指示当前视野位置;开启
  overlay 后标出所有 ROI 的位置。
- **ROIs(会话 ROI 列表)**:本次会话圈选的 ROI,可按序号删除或清空。

### 2.2 主区顶部:标题行与工具条(Toolbar)
- **标题行**:应用名、当前切片名/尺寸/mpp/等效放大倍率、数据库状态
  徽标(已连后端名或 `DB-free`)。
- **工具条**:所有会话中常用操作集中在这一条紧凑工具条里——
  鼠标模式(pan 平移 / box select 框选 / lasso 套索)、zoom(downsample)
  滑杆、Zoom to fit、平移方向按钮组(◀ ▶ ▲ ▼,步进 1/4 视野)、
  measure mode、box as circle、Add ROI、Send to code agent。

### 2.3 统一视图(Unified viewer)
主区唯一的一张大图(plotly):
- **滚轮缩放、拖拽平移**(鼠标模式选 pan,或用图上的模式栏)为纯视觉
  操作,选区坐标始终是视图像素坐标,不受影响;
- **圈选**:鼠标模式选 box select 或 lasso 后直接在大图上拖拽(见第 3 节);
- 已有 ROI 轮廓直接叠加显示在这张图上(红色;标注浏览器选中的为绿色)。

### 2.4 状态行与折叠面板
大图下方是状态行(视野中心/放大倍率、测量结果、提示信息)。再往下是
默认折叠的面板:**Annotations**(标注浏览器 + 编辑 + 导出)、
**Agent console**(agent 提示、提交历史、agent 运行记录)、
**TCGA browser**(检索/下载,含进度条)。最底部是**agent 联动指南**
(默认展开,教你怎么把 notebook 连上 code agent)。

> 调节管线顺序:读取区域 → 缩放 → 亮度/对比度/gamma → 通道视图 →
> 叠加 ROI 轮廓。patch 提取与统计始终基于**未调节**的原图数据,
> 保证选区坐标与颜色真实。

---

## 3. 圈选(ROI)与测量

### 3.1 圈选操作
1. 在工具条选择鼠标模式:**box select(矩形)** 或 **lasso(套索多边形)**;
2. 直接在主图上拖拽圈选(可先滚轮放大再圈,坐标自动换算);
3. (可选)勾选 **box as circle**,框选将被解释为内切圆;
4. 点击工具条的 **Add ROI** 将其加入左侧边栏的 ROI 列表;列表中可
   按序号删除或清空。

所有坐标自动换算回 level-0(原图全分辨率)像素坐标。

### 3.2 发送给 code agent
点击工具条的 **Send to code agent**:
- 自动提取圈选区域 patch(PNG)、计算统计(均值 RGB、H&E 反卷积强度、
  组织占比等);
- 生成结构化 payload,写入历史(`agent_out/roi_history.jsonl`);
- 数据库启用时同步写入 `rois` 表并记录一次 `agent_runs`;
- **Agent console** 面板展示给 agent 的提示文本与完整 JSON。

### 3.3 测量模式
勾选工具条的 **measure mode** 后,框选不会被存为 ROI,点 **Add ROI**
时直接显示物理尺寸:

```
512.0 x 384.0 px = 128.0 x 96.0 um (diag 160.0 um)
```

已知 mpp 时显示微米;未知时仅显示像素。取消勾选即恢复正常圈选。

---

## 4. 标注与数据库

### 4.1 标注浏览器(Annotation browser)
数据库启用时,这里列出当前切片的所有已持久化 ROI(来自 Send to code
agent)。核心交互:

- **点击任意一行 → 视野自动跳转**:居中到该 ROI,缩放至占视野约 80%,
  并在叠加层中绿色高亮;
- **编辑**:选中行后修改 label(如 tumor / stroma / necrosis)和 notes,
  点 Save annotation;
- **删除**:Delete ROI;
- **导出**:JSON / CSV 一键下载全部标注。

### 4.2 Agent 运行记录(Agent runs)
每次 Send to code agent 都会记录:工具名、状态、关联 ROI、模型、时间、
输出摘要。这张表也是 **agent 回写分析结果**的地方(见 7.4)。

### 4.3 命令行批量入库
```bash
python -m hescope.cli init                      # 建表
python -m hescope.cli ingest /path/to/slides -r # 递归登记整个目录
python -m hescope.cli list                      # 查看已登记切片
```

### 4.4 换数据库
默认 SQLite 零配置。切换 PostgreSQL/MySQL 只需设环境变量:

```bash
export HESCOPE_DB_URL="postgresql://user:pass@host:5432/hescope"
marimo edit app.py --no-token
```

---

## 5. TCGA 公共数据

### 5.1 检索
滚到 TCGA 面板:选择癌种项目(TCGA-BRCA / LUAD / LUSC / COAD / KIRC /
GBM / OV / ALL),可选填样本类型(如 `Primary Tumor`),点 **Search GDC**。
结果入本地目录库,表格展示文件名、病例号、样本类型、大小、是否已下载。

### 5.2 下载与打开
选中一行 → **Download & Open**:
- 出现**进度条**,实时显示 `Downloading… 128.4 / 532.0 MB (24%)`;
- 下载中重复点击会被拦截(提示已在下载);
- 完成后自动切换进 viewer,一切圈选/标注功能立即可用。

> **注意**:多数 TCGA 切片为 100MB–2GB,首次下载需要等待;文件缓存在
> `data/tcga/`,第二次打开秒载。GDC 开放数据**无需 token**。

**并行下载**:下载默认使用 8 路 HTTP Range 并发请求(GDC 接口原生支持),
高延迟网络下可显著提速。可用环境变量 `HESCOPE_DL_WORKERS` 调整并发数
(默认 8,限制在 1–16;设为 1 则退回传统单线程下载)。下载中的临时文件
名为 `<文件名>.part`,只有在校验(文件大小,以及 GDC 提供的 md5)通过后
才会改名为正式文件;中断的 `.part` 不会续传,重新下载会从头开始,而
已完成的文件总是直接跳过。若并发下载中途出错,会自动回退为普通单流
下载,无需手动干预。

---

## 6. 分析功能(统计 / QC / 热力图 / 训练分类器)

主区下方的折叠面板里有一个 **Analysis** 面板(在 Annotations 旁边),
提供四类分析能力。所有分析都做**优雅降级**:没圈选、没标注、没模型时
只会给出提示(callout),不会崩溃。

### 6.1 分析当前选区(Analyze current selection)
在统一视图上拖一个框/套索,然后点 **Analyze current selection**:
- 对选区 patch 运行**细胞核检测**(H&E 反卷积 + Otsu + 分水岭分割),
  输出细胞核数量、密度(个/mm²,切片有 mpp 时)、平均面积、覆盖率;
- 同时输出 **QC 报告**:组织占比、清晰度(blur score)、是否模糊、亮度;
- 结果以紧凑表格 + 提示条展示。
没有实时选区时,自动退回分析**最近一次提交的 ROI**;两者都没有时按钮
下方会给出提示。

### 6.2 染色归一化开关(Macenko)
左侧边栏 **Display** 面板新增 **stain normalize (Macenko, display-only)**
复选框:勾选后视图图像会做 Macenko 染色归一化。参考统计量只在**第一张
非空白视图图像**上拟合一次并缓存;仅影响**显示**,不改变圈选坐标和
后续分析读取的原始像素。

### 6.3 热力图(Heatmap)
Analysis 面板里选择:
- **metric**:`tissue_fraction`(组织占比)、`nuclei_density`(每 tile
  细胞核计数,tile 较大时自动降采样控制开销)、以及训练好模型后的
  `model_prob:<标签>`(该标签的预测概率);
- **model**:从 `data/models/` 下已训练的模型中选择(训练见 6.4);
- **tile size**:128 / 256 / 512。
点 **Run heatmap** 开始全片扫块计算,有进度提示;运行中重复点击会被
拦截。结果以 viridis 伪彩叠加在切片缩略图上,显示在 Analysis 面板里;
勾选 **show heatmap on navigator** 后,左侧导航图也会换成热力图叠加版
(再点取消即恢复)。计算出的网格与参数保留在会话状态中。

### 6.4 训练分类器(Train from annotations)
输入模型名,点 **Train from annotations**:用标注面板里打过标签的 ROI
patch 训练一个 StandardScaler + LogisticRegression 弱监督分类器
(需要每个标签至少 2 个样本、至少 2 个不同标签)。成功后以表格展示
标签、样本数、交叉验证准确率(cv_accuracy);数据不足时以警告提示具体
原因。**需要数据库可用**(DB-free 模式下会提示无法训练)。训练完成后
热力图的 model 下拉框会自动刷新。

### 6.5 agent 可以调什么
内核全局新增零参数工具 `get_analysis_capabilities()`:返回 JSON,包含
可用分析列表、`torch_embedding_available`(纯 find_spec 探测,不会触发
模型权重下载)和已训练模型列表;永不抛异常(失败时返回
`{"error": ...}`)。分析函数本身在 `hescope` 包顶层直接可用:
`hescope.detect_nuclei`、`hescope.qc_report`、
`hescope.macenko_normalize`、`hescope.compute_grid`、
`hescope.render_heatmap`、`hescope.train_from_annotations`、
`hescope.predict_patch` 等,agent 可按 `../AGENTS.md` 第 8 节的契约直接调用。

---

## 7. 与 code agent 联动

这是平台的核心特色:**agent 能直接进入运行中的 notebook,读取你圈选的
内容,并把分析结果写回界面**。

### 7.1 原理
官方 skill [marimo-pair](https://github.com/marimo-team/marimo-pair) 让
code agent 连接正在运行的 marimo 内核,在其中执行 Python。HE-Scope 在
内核全局中预置了工具函数,agent 按名调用即可。

### 7.2 一次性配置
```bash
# 任何支持 Agent Skills 的 agent(Kimi Code / Codex / ...):
npx skills add marimo-team/marimo-pair

# Claude Code:
/plugin marketplace add marimo-team/marimo-pair
/plugin install marimo-pair@marimo-pair
```
仓库根目录的 `../AGENTS.md` 是写给 agent 的契约文档——agent 进入项目目录
后会自动读取,无需你手工教它。

### 7.3 启动与连接
1. 用 `marimo edit app.py --no-token` 启动,**浏览器保持打开**;
2. 首次打开时 marimo 是惰性加载(cells 未执行),在界面上点一次
   Run(或让 agent 自己触发运行);
3. 在你的 agent 里直接说,例如:
   > "连接我的 marimo notebook,看看我现在圈选了什么"

agent 会发现服务器、连上内核,然后就可以使用了。

> **为什么必须用 `marimo edit` 而不是 `marimo run`?**
> `marimo run` 是只读模式:官方在服务端禁用了代码执行接口
> (`/api/sessions` 与 `/execute` 都要求 edit 权限,run 模式返回
> 401),marimo-pair 原理上无法附加到 run 模式的会话。如果你想要
> 隐藏所有 cell 的纯净 app 界面:启动后点右下角工具栏的眼睛图标
> (Toggle app view)或按 Cmd/Ctrl + `.` ——界面与 run 模式完全
> 一致,但会话仍是 edit session,agent 连接不受影响。本应用所有
> cell 默认已 `hide_code`,代码区默认不显示。

### 7.4 agent 可用的入口(内核全局函数)

| 入口 | 作用 |
|---|---|
| `get_current_selection()` | **零点击**:你在图上拖框/套索的**当前实时选区**(坐标、bbox、缩放),没圈时返回 `NO_SELECTION` |
| `get_latest_selection()` | 最近一次 **Send to code agent** 提交的完整 payload(JSON:坐标、patch 路径、H&E 统计) |
| `agent_bridge` | 提交历史(`agent_bridge.history()`)、patch 文件目录 |
| `db.roi_repo` / `db.run_repo` | (数据库启用时)标注与 agent 运行记录 |
| `open_slide(path)` | 让 agent 自己打开一张切片 |
| `get_analysis_capabilities()` | 可用分析能力 + 已训练模型的 JSON(见 6.5;永不抛异常) |

**典型闭环**:
1. 你在图上圈一块可疑区域(不用点任何按钮);
2. 对 agent 说:"分析我圈的地方"→ agent 调 `get_current_selection()`,
   拿到坐标和 patch 图像进行分析;
3. agent 通过 `db.run_repo.record(...)` 把结论写回;
4. 你在 **Agent runs** 面板直接看到 agent 的分析结果。

### 7.5 一个真实示例
```
你:  (在图上拖一个框)
你:  "这块区域是什么?对比一下我标注过的 tumor 区域"
agent: [调用 get_current_selection() → 拿到 bbox + patch]
       [调用 db.roi_repo.search(label="tumor") → 拿到历史标注]
       [分析对比,调用 db.run_repo.record(...) 写回结论]
你:  (在 Agent runs 面板看到结论;在标注浏览器点选跳转复查)
```

---

## 8. 常见问题(FAQ)

### 换了一个 agent,之前的记录还在吗?
**在。** 所有持久数据都存在平台侧,与用哪个 agent 无关:

| 数据 | 位置 | 换 agent 后 |
|---|---|---|
| 标注(ROI、label、notes) | `data/hescope.db` 的 `rois` 表 | 完整保留 |
| agent 运行记录 | `data/hescope.db` 的 `agent_runs` 表(含 `model` 字段,可区分是哪个 agent 写的) | 完整保留 |
| 圈选 patch 图像 | `agent_out/patches/*.png` | 完整保留 |
| 提交历史 | `agent_out/roi_history.jsonl` | 完整保留 |
| TCGA 目录与已下载切片 | `data/tcga/` | 完整保留 |

无论 Kimi Code、Claude Code、Codex 还是 Hermes,连上同一个运行中的
notebook(或同一项目目录),看到的都是同一份记录。

**会丢的只有会话内临时状态**:未点 Send 的实时选区、未提交的 ROI 列表
——它们随 notebook 重启消失。所以重要的圈选记得 **Send to code agent**
落盘。

### 换电脑/多人共享呢?
复制整个项目目录(重点是 `data/` 和 `agent_out/`)即可完整迁移;
或者把 `HESCOPE_DB_URL` 指向一个 PostgreSQL 服务器,多人多端共享同一
标注库(切片文件仍需共享存储)。

### agent 说连不上 / 找不到我的 notebook?
检查三点:① 是否用 `--no-token` 启动;② 浏览器页面是否开着;
③ cells 是否已运行(页面上有输出才算)。agent 端确认装了 marimo-pair
skill。

### agent 调 `get_current_selection()` 返回 NO_SELECTION?
两种可能:你确实还没在图上圈(先拖个框);或者 cells 尚未运行。
注意:**实时选区**用 `get_current_selection()`,**已提交的历史**用
`get_latest_selection()`,两者不同。

### 没有网络/没有 openslide/数据库坏了还能用吗?
都能。见下一节降级模式。

---

## 9. 数据存储位置一览

```
project/
├── data/
│   ├── hescope.db            # 主库:slides / rois / agent_runs(SQLite 默认)
│   └── tcga/
│       ├── catalog.db        # TCGA 检索目录缓存
│       └── <file_id>/*.svs   # 已下载的切片
├── agent_out/
│   ├── roi_history.jsonl     # 每次 Send 的完整 payload(追加写)
│   └── patches/*.png         # 圈选区域图像
└── assets/demo_he.png        # 演示切片(可重新生成)
```

`data/` 与 `agent_out/` 均被 git 忽略,删除即清空全部记录。

---

## 10. 降级模式

平台按"任何环境都能跑"设计,三个维度独立降级:

| 缺失 | 表现 | 仍可用 |
|---|---|---|
| 数据库不可达 | 顶部黄色提示条;标注/agent runs 面板显示不可用 | 看图、圈选、测量、TCGA、jsonl 桥接 |
| 无 openslide | 自动改用 tifffile 后端读 SVS(区域级读取,内存安全) | 全部功能 |
| 无网络 | TCGA 检索不可用(提示条) | 本地切片全部功能 |

---

## 11. 后续路线

规划中、尚未实现的增强(按优先级):
- **BigQuery 队列筛选**:接 ISB-CGC 公共数据集,按临床/分子条件
  (分期、分型、表达量)筛选 TCGA 切片队列,替代按项目翻文件;
- **云存储后端**:GCS/S3 上的切片直读与标注库托管;
- **更多 agent 入口**:MCP server 封装、批量分析流水线;
- **多用户协作**:PostgreSQL + 共享存储的标注协同。

---

*技术细节参见 `../README.md`;agent 接入契约参见 `../AGENTS.md`;
本指南对应平台版本见 git 仓库 master 分支。*
