<div align="center">

<img src="image/Marimo-icon.png" alt="HE-Scope" width="160">

# HE-Scope

**marimo-native + agent-native 的病理 H&E 全切片(WSI)观测与人-agent 闭环分析平台。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-%E2%89%A53.10-blue.svg)
![marimo](https://img.shields.io/badge/marimo-%E2%89%A50.23-8B5FA8.svg)

[English](README.md) · **简体中文**

</div>

在浏览器里浏览超大病理切片、圈选 ROI,code agent 经
[marimo-pair](https://github.com/marimo-team/marimo-pair) 实时读取你的圈选、
在其上运行分析栈、把标注回写——人和 agent 在同一份数据、同一个内核里闭环协作。

HE-Scope 本体是一个 marimo notebook(`app.py`),背后是一个普通 Python 包
(`hescope/`)。agent 接触到的一切都是模块级代码,因此整套分析栈都能脱离 UI
无头运行。

## 特性

- **统一 viewer**:deep-zoom 视口(金字塔最优层 + resize),缩放/平移、导航
  缩略图、亮度/对比度/gamma 调节、H&E 通道视图、ROI 叠加与物理尺寸测量——
  全部在同一张图上完成。
- **ROI 闭环**:框选/套索/圆形圈选直接映射到 level-0 坐标;"Send to code
  agent" 一键导出 patch PNG + 统计(均值 RGB、H&E 解卷积、组织占比)并持久化;
  agent 读取 → 分析 → 回写标注,全程落库可追溯。
- **6 个 agent 工具**(notebook 模块级,marimo-pair 直连):
  `get_current_selection()`(零点击 live 圈选)、`get_latest_selection()`、
  `get_analysis_capabilities()`、`get_slide_info()`、`annotate_roi()`(回写)、
  `query_annotations()`。契约详见 [AGENTS.md](AGENTS.md)。
- **TCGA / GDC 接入**:免 token 检索开放访问 TCGA 切片,本地 SQLite catalog
  缓存,100MB–2GB SVS 并行分块下载(断点不续传、完成跳过、md5 校验),
  tifffile/zarr 内存安全读取。
- **分析栈**(纯模块级代码,无需 UI):细胞核检测、QC 报告、Macenko/Reinhard
  染色归一化、56 维手工特征、全片网格指标 + heatmap 叠加、弱监督
  LogisticRegression 训练(标注 → 模型 → 概率 heatmap)。
- **FM encoder factory**(`hescope.embeddings`):GPFM(MIT,默认)、
  H-optimus-0(Apache-2.0)、UNI2-h(CC-BY-NC-ND,仅学术对照、永不为默认)、
  ResNet18(ImageNet)本地回退;注册表零重依赖可导入,权重懒加载。
- **interactions 轨迹**:圈选查看、ROI 提交、标注回写、ROI 删除、分析运行、
  agent 工具调用统一落 `interactions` 表 —— notebook 里的按钮和 agent 工具
  都会写,人写的标注与 agent 写的标注由行内 `actor` 区分(记录过程完全异常
  安全)。`human_gate` 为保留类型:尚无人工闸门界面,因此无人写入。
- **GeoJSON 导出**:标注面板的 *Export ROIs (GeoJSON, QuPath)* 一键导出当前
  切片标注为 QuPath 兼容 FeatureCollection。
- **降级模式**:无数据库 / 无 OpenSlide / 无网络时应用照常运行,降级以
  callout 呈现,绝不崩 notebook。

## 快速开始

需要 **Python ≥ 3.10**。推荐用虚拟环境隔离,仓库 `.gitignore` 已忽略 `.venv/`。

### 1. 建虚拟环境并安装

<details open>
<summary><b>uv(推荐,最快)</b></summary>

```bash
uv venv --python 3.11                 # 创建 .venv
uv pip install -e ".[test]"           # 核心依赖 + pytest
```
</details>

<details>
<summary><b>标准 venv + pip</b></summary>

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Windows Git Bash:   source .venv/Scripts/activate
# macOS / Linux:      source .venv/bin/activate
pip install -e ".[test]"
```
</details>

> 依赖以 **`pyproject.toml`** 为单一事实来源;`requirements.txt` 只是转发到
> `-e .` 的兼容入口。

### 2. 可选依赖(按需)

| extra | 装什么 | 解锁什么 |
| --- | --- | --- |
| `.[wsi]` | `openslide-python` | 真实 WSI 格式(.svs / .ndpi / .mrxs)的原生读取 |
| `.[ml]` | `scikit-learn` `joblib` `torch` `torchvision` `timm` | 弱监督训练 + FM embedding |
| `.[test]` | `pytest` | 测试套件 |

```bash
uv pip install -e ".[wsi,ml,test]"      # 一次装全
```

**只想要训练、不想装 torch**(省约 1GB):单独装 `scikit-learn joblib` 即可。
`hescope.ml` 的训练、预测、概率热力图全部可用,只有 FM embedding 走懒加载降级。

**Windows 装 OpenSlide**:`openslide-python` 只是绑定,还需要 OpenSlide 本体
动态库。最省事的方式是同时装官方预编译二进制包,免去手工配 DLL 路径:

```bash
uv pip install openslide-bin openslide-python
```

> 不装 OpenSlide 也能跑:`hescope.slides` 会自动回退到 tifffile/zarr 后端做
> 区域级读取(内存安全),只是覆盖的专有格式少一些。

### 3. 启动

```bash
hescope app                                  # = marimo edit app.py --no-token
hescope app --port 2718 --host 127.0.0.1     # 显式指定端口/地址
```

浏览器打开提示地址(默认 `http://localhost:2718`)后,按 **Cmd/Ctrl + `.`**
隐藏代码 cell(app view)。首次使用点 **"Generate & open demo slide"**
生成一张 6000×4000 合成 H&E 演示切片,即可体验全部功能。

> **必须先激活虚拟环境再执行 `hescope app`。** 该命令用 `os.execvp` 把自己
> 替换成 `marimo`,靠 `PATH` 查找可执行文件;直接调 `.venv/Scripts/hescope.exe`
> 而不激活环境时,`PATH` 里没有 `marimo`,会报
> `error: could not launch marimo: [Errno 2] No such file or directory`。
> 激活后即正常。等价的手动写法:`marimo edit app.py --no-token`。

必须用 `marimo edit`,不能用 `marimo run`:run 模式是只读的,服务端会对
`/api/sessions` 与 `/execute` 返回 401,marimo-pair 无法附加。app view 只是
隐藏代码的显示开关,会话仍是 edit session,不影响 agent 连接。

### 4. agent 侧配对(任选其一)

```bash
npx skills add marimo-team/marimo-pair   # 通用 marimo 配对 skill
# 或直接把仓库内置 skill 指给你的 agent:skills/he-scope/SKILL.md
```

配对后 agent 即可调用上述 6 个工具,完成
读圈选 → 分析 → 回写标注 → 训练 的闭环。

### 5. 无头自检

```bash
pytest                          # 全部离线运行,无需网络
python app.py                   # 顺序执行所有 cell 一次(冒烟测试)
hescope init                    # 建库(默认 data/hescope.db)
hescope ingest /path/to/slides -r && hescope list   # 批量登记 + 查看
```

### 已验证环境

以下组合已在本仓库实测通过(`pytest` → **约 940 passed, 17 skipped**;测于
2026-08-20)。passed 数字会在相邻两次运行间浮动 1——因为有一条竞态回归测试
本身就是概率性的(见该测试模块的 docstring),所以"约 940"是"自己跑一遍"
的意思,不是承诺。17 个跳过里:15 个需要真实 Chrome(`HESCOPE_BROWSER_TESTS=1`)、
1 个是只在 POSIX 下成立、Windows 跑不了的权限位检查,1 个是那条竞态测试本次
没复现出竞态。想看当前跳过清单,跑 `pytest -rs`。本仓库没有 CI——没有
`.github/`,没有任何自动化在跑这个数字——上面那行 `tests-N passed` 徽章曾经
两次被真实测试数量甩开都没人发现(徽章钉死在 276,实测先变成 909,现在到
这里),这也是删掉它而不是补数字的原因:一个要靠人手动更新的数字迟早会漂,
漂了的徽章比没有徽章更糟。

| 组件 | 版本 |
| --- | --- |
| Windows 11 / Python | 3.11 |
| marimo | 0.23.16 |
| numpy · scipy · scikit-image | 2.4.6 · 1.17.1 · 0.26.0 |
| torch(CPU)· torchvision · timm | 2.13.0+cpu · 0.28.0 · 1.0.28 |
| scikit-learn · joblib | 1.9.0 · 1.5.3 |
| openslide-python · OpenSlide 本体 | 1.4.6 · 4.0.1 |
| zarr · tifffile · SQLAlchemy | 3.1.6 · 2026.3.3 · 2.0.51 |

## 文档地图

| 文档 | 内容 |
| --- | --- |
| [docs/USER_GUIDE.zh-CN.md](docs/USER_GUIDE.zh-CN.md) | 使用指南:界面操作、agent 联动、数据持久化、FAQ([English](docs/USER_GUIDE.md)) |
| [AGENTS.md](AGENTS.md) | code agent 配对契约:启动、硬性规则、工具清单、payload schema、回写示例(英文) |
| [skills/he-scope/SKILL.md](skills/he-scope/SKILL.md) | Agent Skills 标准格式的仓库内置 skill(英文) |
| [docs/ROADMAP.zh-CN.md](docs/ROADMAP.zh-CN.md) | 实施路线图与已完成阶段([English](docs/ROADMAP.md)) |
| [docs/STRATEGY.zh-CN.md](docs/STRATEGY.zh-CN.md) | 战略决策:学术目标、开源路线、FM license 红线([English](docs/STRATEGY.md)) |
| [docs/PAPERS.zh-CN.md](docs/PAPERS.zh-CN.md) | 学术文献综述与写作素材库([English](docs/PAPERS.md)) |
| [docs/OVERNIGHT-REPORT.zh-CN.md](docs/OVERNIGHT-REPORT.zh-CN.md) | 最近一次 overnight 研发报告([English](docs/OVERNIGHT-REPORT.md)) |

## 仓库布局

```
app.py                  marimo notebook 应用(UI 组装;随包分发,hescope app 启动)
hescope/                Python 包:slides / rois / viewer / agent_bridge / db /
                        tcga / 分析栈(nuclei, qc, stain, features, grid,
                        heatmap, ml)/ embeddings(FM factory)/ geojson / cli
skills/he-scope/        Agent Skills 包(SKILL.md)
tools/make_demo_slide.py  合成 H&E 演示切片生成器
tests/                  pytest 套件(离线;GDC API 用真实录制响应 mock)
docs/                   使用指南、路线图、战略与文献文档
assets/theme.css        应用样式表,经 marimo.App(css_file=...) 加载
image/                  logo 与标题栏图标(README 用图 + Marimo-icon.svg)
data/                   下载的 TCGA 切片 + catalog + hescope.db(gitignored)
agent_out/              agent 产物:patch PNG + roi_history.jsonl(gitignored)
```

## 排障

| 症状 | 原因 / 处理 |
| --- | --- |
| `error: could not launch marimo: [Errno 2] No such file or directory` | 虚拟环境没激活,`PATH` 里找不到 `marimo`。激活后重试,或直接跑 `marimo edit app.py --no-token`。 |
| agent 连不上 notebook | 三项必查:① 用 `--no-token` 启动;② 浏览器页面保持打开;③ cells 已执行(marimo 0.23 惰性加载,globals 在跑完之前不存在)。 |
| `get_current_selection()` 返回 `NO_SELECTION` | 图上还没拖框/套索,或 cells 未运行。实时选区用 `get_current_selection()`,已提交历史用 `get_latest_selection()`。 |
| 打不开 `.svs` / `.ndpi` | 装 `openslide-bin openslide-python`;不装则自动回退 tifffile/zarr,专有格式覆盖较少。 |
| `train_from_annotations` 报 `ValueError` | 标注不足:至少 2 个不同 label,每个 label 至少 2 个 patch,且需要数据库可用。 |
| 热力图 / 训练报缺 sklearn | `uv pip install scikit-learn joblib`(无需 torch)。 |
| TCGA 检索无结果或超时 | GDC 开放数据免 token,但需要外网;并发数可用 `HESCOPE_DL_WORKERS`(1–16,默认 8)调整,设 1 退回单线程。 |
| 想换数据库 | 设 `HESCOPE_DB_URL`(如 `postgresql://user:pass@host:5432/hescope`),或 `hescope --db <URL> init`。 |

## License

本项目本体以 **MIT License** 发布(见 [LICENSE](LICENSE))。

病理 foundation model 的**权重各有独立 license**,不受本项目 MIT 覆盖。
`hescope.embeddings` 注册表强制执行 license 红线:只有可商用且非 gated 的
编码器才有资格成为默认(当前默认 GPFM,MIT);CC-BY-NC-ND 模型(如 UNI2-h)
仅登记用于学术对照,**永远不进入默认路径**;H-optimus-0(Apache-2.0)为
可商用替代;ResNet18(ImageNet)为无 license 顾虑的本地回退。使用任何 FM
权重前请遵守其各自条款。
