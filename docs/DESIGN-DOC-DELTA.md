# 设计文档现状核对 —— `docs/HE-Scope-设计文档-数据与Harness.md` 修订说明

本文只做核对，不改代码。核对对象是 `docs/HE-Scope-设计文档-数据与Harness.md`
（下称"原文"，写于 2026-08-14，基准 `main @ f541b68`）。

## 0. 本文的锚点与免责

| 项 | 值 | 产生命令 |
| --- | --- | --- |
| 核对时间 | 2026-08-20 16:40–17:00 CST | `date` |
| 代码锚点 | `feature/db-foundation @ e18c304` | `git log --oneline -1` |
| 解释器 | `.venv` = CPython **3.11.14**，113 个包 | `.venv/Scripts/python.exe -V`；`uv pip list --format=freeze` |
| 测试基线 | 926 collected（909 passed / 17 skipped） | `.venv/Scripts/python.exe -m pytest -q --collect-only` |

**规模数字一律取自已提交树 `e18c304`**（`git ls-tree -r e18c304` + `git show e18c304:<path>`
逐 blob 数换行符），不取工作树。原因见 0.2。

### 0.1 R-1：`data/hescope.db` 的哈希对不上，且不是本次造成的

```
$ sha256sum data/hescope.db      # 工作开始时
ba93b5210469f9e3d8012a5784ca1d9bbe344b8e12cdc1429b85b3299fe7d1c1
$ sha256sum data/hescope.db      # 工作结束时
ba93b5210469f9e3d8012a5784ca1d9bbe344b8e12cdc1429b85b3299fe7d1c1
```

任务书要求的 `ae812f28be3ff9633b223d18d98a6f1a17e571c93b2a779d203a0ec8835ee991`
**在本次会话开始前就已经对不上**。两条独立路径（`sha256sum` 与 Python `hashlib`）
给出同一个 `ba93b52…`；文件 mtime 是 `Aug 13 08:20`，早于本会话七天。

本次全程未写该文件：所有查询都在 `E:/tmp/dbcopy/` 的副本上做（`cp` 了 `.db` 与
`-wal` 两个文件后在副本上 checkpoint）。**任务书里的期望哈希需要重新登记。**

另注意 `data/hescope.db-wal` 有 148,352 字节尚未 checkpoint 的内容。任何"对 `.db`
单文件取哈希"的守卫都测不到 WAL 里的写入 —— **这条守卫本身是有洞的**：
在 WAL 模式下，一次写事务可以完全落在 `-wal` 里而不改动 `.db` 一个字节。

### 0.2 核对期间有另一个 agent 在同一工作树上写代码

16:56 时 `git status` 出现 `M hescope/store/db.py`（+224/−2），内容是
`geom_key()` 与对 "migration 4" 的引用。**这不是本次核对做的改动。**
它出现在本文第 2 节的 `geom_key` 实测（16:4x，当时 0 命中）之后。

后果：

- 本文所有数字锚定 `e18c304`，与工作树无关；
- 第 2 节关于 `geom_key` 的"未落地"结论，其有效期截止到 `e18c304`，工作树中已在变化；
- R-6 的全量跑是在这棵被并发修改的树上跑的，其结果不能当作 `e18c304` 的基线。

---

## 1. §2.1 规模数字复测

| | 原文（`f541b68`） | 实测（`e18c304`） | 变化 |
| --- | ---: | ---: | --- |
| `hescope/` | 10,049 行 | **12,159 行**（39 个文件） | +21% |
| `app.py` | 3,698 行（50 cell） | **3,740 行（50 cell）** | 基本未动 |
| `tests/` | 14,542 行（660 个 test） | **18,187 行**（71 个文件，**778** 个 `def test_*`） | +25% / +18% |
| `docs/` + `bugs/` | ~10,400 行 | **11,536 行**（已提交）/ 12,704 行（含未提交的原文本身） | +11% |

命令：

```bash
git ls-tree -r --name-only e18c304          # 文件清单
# 每个 blob: git show e18c304:<path> 后统计换行符（脚本 E:/tmp/count.py）
grep -c '^@app\.cell' app.py                                                       # -> 50
grep -rhoE '^\s*(async )?def (test_[A-Za-z0-9_]+)' tests --include='*.py' | wc -l  # -> 778
.venv/Scripts/python.exe -m pytest -q --collect-only                               # -> 926 tests collected
```

`hescope/` 按新子包分解（`e18c304`，仅 `.py`）：

| 子包 | 文件 | 行 |
| --- | ---: | ---: |
| `viewer` | 6 | 3,232 |
| `store` | 3 | 2,160 |
| `analysis` | 10 | 2,124 |
| `gdc` | 4 | 1,566 |
| 顶层 `__init__` + `cli` | 2 | 942 |
| `wsi` | 4 | 704 |
| `interop` | 3 | 492 |
| `agent` | 2 | 479 |
| `core` | 5 | 460 |

### 1.1 两个口径提醒

- **"660 个 test"与"909 passed"不是同一个数。** 前者是 `def test_*` 计数（现为 778），
  后者是 pytest 参数化展开后的用例数（现为 926 collected）。原文把 660 写在
  "test 函数"列里，口径本身没错，但它和 README/基线里的 passed 数不可比，
  混用一次就会得出"测试变少了"的错觉。
- **原文自身对 `app.py` 行数给了两个互相矛盾的值**：§2.1 写 3,698，附录 B 写 3,673。
  实测 3,740。

---

## 2. §2.2 L1–L4 落地情况

原文原话："`hescope/` 全目录检索不到 `content_key`、`slide_files`、`measurements`、
`schema_migrations`、`geom_key`、`mpp_effective`、`created_by` 中的任何一个。"
原文自己留了作废条件："若 L1–L4 已在某分支完成，本节作废。"

**本节不作废，只是部分作废。** 实测（`e18c304`）：

| 标识符 | 状态 | 证据 |
| --- | --- | --- |
| `content_key` | **已落地** | `hescope/core/identity.py:31` 定义；`store/db.py`、`store/migrations.py` 消费 |
| `slide_files` | **已落地** | `store/db.py:442` `class SlideFile.__tablename__ = "slide_files"` |
| `schema_migrations` | **已落地** | `store/migrations.py`，3 个版本 + `current_version()` / `pending()` |
| `measurements` | **未落地** | 见 2.1 |
| `geom_key` | 未落地（截至 `e18c304`） | `grep -rc geom_key hescope tests app.py` → 0 |
| `mpp_effective` | **未落地** | 同上 → 0 |
| `created_by` | **未落地** | 同上 → 0 |

### 2.1 反对：`measurements` 没有落地

有说法称"`measurements` 已落地（6 个文件）"。**这条不成立。**
`hescope/` 与 `app.py` 里 `measurements` 一共 3 处命中，没有一处是表：

```
hescope/analysis/stats_table.py:178:    reasoning as the annotation export, applied to the measurements.
hescope/core/measure.py:1:"""Physical measurements for HE-Scope (level-0 geometry -> microns)."""
app.py:2336:            # The measurements, not just the annotations: a result that cannot
```

两句英文散文加一个模块 docstring。全库 `__tablename__` 清单里也没有它：

```bash
$ grep -rnE '__tablename__' hescope --include='*.py'
hescope/gdc/tcga_schema.py: tcga_projects / tcga_cases / tcga_samples / tcga_files
hescope/store/db.py:        slides / slide_files / rois / interactions / agent_runs
```

**9 张表，没有 `measurements`。** 之所以会被误判为"6 个文件"，是因为 `measurements`
是一个普通英文词，全仓（含 `docs/`、`bugs/`）grep 必然大量命中散文。
这正是 R-3 说的那类错误：**用命中计数代替对目标值的检查。**

### 2.2 更重要的反对：代码落地 ≠ 数据落地。用户的库还在版本 0

L1–L4 的代码与测试确实在，但**它们对用户真实数据的效果是零**。
在 `data/hescope.db` 的副本上实测：

```
$ .venv/Scripts/python.exe -m hescope.cli --db "sqlite:///E:/tmp/dbcopy/probe.db" doctor
schema       version 0 of 3  (3 pending: 1, 2, 3)
rows         agent_runs=13, interactions=28, rois=10, slide_files=1, slides=31,
             tcga_cases=74, tcga_files=100, tcga_projects=3, tcga_samples=86
slide files  5 of 31 resolve, 26 missing
1 problem(s):
  - 3 migration(s) pending - run `hescope migrate --dry-run` first
```

活库处在一个**迁移框架描述不了的中间态**：

- `slide_files` 表存在，`slides` 上 `identity_scheme` / `identity_key` / `file_size` /
  `md5sum` 四列也存在（`CREATE TABLE slides` 里它们带引号，说明是
  `ALTER TABLE ADD COLUMN` 加的）；
- 但 `schema_migrations` 表**不存在**，`PRAGMA user_version = 0`；
- 于是 `current_version()` 返回 0，三个迁移全部报 pending；
- 31 张片子里 **30 张 `identity_scheme IS NULL`**，只有 1 张是 `sha256`；
- `slide_files` **只有 1 行**（对 31 张片子）；
- `tcga_files` **100 行全部 `slide_id IS NULL`**，`local_path` 也全为 NULL。

对照 `data/hescope.db.bak-before-db-foundation` 的表清单（无 `slide_files`），
可以确定这些 DDL 是本分支期间产生的，但记账没跟上。

好消息是迁移框架**认得**这个中间态，不会撞 "duplicate column name"：

```
$ .venv/Scripts/python.exe -m hescope.cli --db "sqlite:///E:/tmp/dbcopy/probe.db" migrate --dry-run
would apply migration 1: baseline schema (stamp only, no schema change)
would apply migration 2: the SVS <-> ROI relationship: slide identity, slide_files, roi bbox columns
  would write 31 slide_files row(s) (26 marked missing), 5 distinct identity(ies),
  0 duplicate-content row(s) skipped, 10 roi(s) with bbox backfilled
would apply migration 3: TCGA download -> storage -> injection: a real FK on tcga_files.slide_id
  of 0 downloaded file(s), 0 already linked, would link 0, 0 could not be linked
dry run: would go from version 0 to version 3 (3 pending migration(s), 0 new table(s),
  0 new column(s), 0 new index(es) from init_db); nothing was changed
```

**结论：§2.2 应改写为"L1–L2 的代码已落地，L3–L4 未落地，且没有一条迁移跑在真实数据上"。**

### 2.3 三个未落地字段分别缺在哪里、影响什么

**`geom_key`** —— 缺在 `hescope/store/db.py` 的 `ROI` 上。
`ROI` 现有 `points_json` + `bbox_json` + `bbox_x0..bbox_y1` 四列，**没有几何内容身份**。
影响：同一形状被画两次、或者 GeoJSON 往返导入一次，就是两行不同的 ROI，
没有任何字段能判定它们相同。`hescope/interop/` 的导入导出是这个洞的直接放大器
—— 导出再导入必然翻倍。（工作树中已有 agent 在补，见 0.2。）

**`mpp_effective`** —— 缺在 `slides` 上，那里只有一个 `mpp`。
`hescope/core/measure.py`（模块 docstring：*"Physical measurements … level-0 geometry
-> microns"*）把 level-0 几何换算成微米，用的就是 `slides.mpp`。
没有 `mpp_effective` 意味着**无法区分"扫描仪声称的 mpp"和"这次测量实际用的 mpp"**：
一旦某张片子的 mpp 是猜的、是从 magnification 反推的、或者被人工覆盖过，
存下来的微米数就永远无法追溯是按哪个标度算的。这正是原文 §1 判据要的东西，
也是 §16 Phase 0 第 3 项把它和 `measurements` 表绑在一起写的原因。

**`created_by`** —— 哪张表上都没有。
没有任何一行数据能回答"这是人写的还是 agent 写的"。`interactions` 有 `kind` 与
`session_tag`，`agent_runs` 有 `tool` / `model`，但 `rois` 上没有出处字段。
影响：原文 §16 Phase 3 的判据"`created_by` 接受率"今天连分母都取不到；
自动化偏倚（automation bias）这条研究线**没有数据源**。

### 2.4 反对：Phase 0 的判据"`slides.path` 不再 UNIQUE"与 R-4 冲突，且已被绕开

原文 §16 Phase 0 判据第一句是"`slides.path` 不再 UNIQUE"。实测它**仍然 UNIQUE**，
而且是**故意的**：

```python
# hescope/store/db.py:414
path: Mapped[str] = mapped_column(String(1024), unique=True)
# 紧邻注释：``path`` above stays and stays UNIQUE (R-4); it is now a cache of
# the most-recently-seen location, and ``slide_files`` is the durable record
# of every location this slide has been seen at.
```

去掉 UNIQUE 需要 `DROP` 约束，R-4（只增不改）不允许。本分支选的路是：
`path` 降级为"最近一次见到的位置"缓存，多路径的真相搬到 `slide_files`，
身份唯一性由**部分唯一索引**承担：

```python
Index("ux_slides_identity", "identity_scheme", "identity_key", unique=True,
      sqlite_where=sa.text("identity_scheme IS NOT NULL"))
```

**这条路比原文写的判据更好**（它同时容纳了 30 行 identity 为 NULL 的历史数据，
换成无条件 UNIQUE 会让它们互相撞车），但原文的判据文字必须改，
否则 Phase 0 永远"验收不通过"。建议改为：

> **同一份内容在两个路径下只有一行 `slides`、两行 `slide_files`，
> 且从任一路径都能取到全部 ROI。**

注意这一条**今天在活库上还演示不了**：`slide_files` 只有 1 行，
31 张片子里 26 张的文件已丢失。验收需要另造一份真存在两份的文件
（原文点名的 `demo_he.png`）。

---

## 3. §2.3 三个已知缺陷的当前状态

### 3.1 `overlay.py` scale bar —— 症状仍在，但**原文的病因诊断是错的**

文件已随子包拆分移到 `hescope/viewer/overlay.py`，缺陷原样保留：

```python
    try:  # matplotlib bundles DejaVuSans.ttf; no hard dependency
        import matplotlib                                   # 第 46 行
        ttf = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf" / "DejaVuSans.ttf"
        if ttf.exists():                                    # 第 48 行，Path 未导入
            return ImageFont.truetype(str(ttf), 14), f"{um:g} µm"
    except Exception:
        pass
    return ImageFont.load_default(), f"{um:g} um"
```

原文说病因是 `Path` 未导入。**实测是两个叠加缺陷，且 `Path` 那个在本环境里根本执行不到：**

```
$ .venv/Scripts/python.exe -c "import matplotlib"
ModuleNotFoundError: No module named 'matplotlib'

$ .venv/Scripts/python.exe -c "from hescope.viewer.overlay import _scale_bar_font_and_label; print(repr(_scale_bar_font_and_label(500)[1]))"
'500 um'
```

第 46 行的 `import matplotlib` 先抛 `ModuleNotFoundError`，第 48 行的 `NameError`
永远轮不到发生。两者被同一个 `except Exception: pass` 吞掉，
函数照常"成功返回" —— 原文自己归纳的 *failure rendered as success*。

**所以"补一行 `from pathlib import Path`"在这个环境里治不好症状**：补完之后
`import matplotlib` 照样先失败，label 照样是 `um`。而且 `matplotlib` 不在
`pyproject.toml` 的 core 依赖里（core 是 12 项，无 matplotlib），
按原文 §3 的依赖纪律它也不该为了一个 µ 字形被拉进 core。

**建议把原文这一条从"漏 import"改写为"整个 µ 字形策略选错了后端"。**
真正的修法三选一：

1. 项目自带一个含 µ 的 TTF（放进 `hescope/static/`，已有 package-data 机制）；
2. 用 Pillow ≥ 10.1 的 `ImageFont.load_default(size=)` —— 实测本环境
   `load_default()` 返回的已经是 `FreeTypeFont`（`<_io.BytesIO>` 背后的 Aileron），
   不再是老的 ASCII-only bitmap 字体，原注释里"PIL's built-in bitmap font"这个前提
   本身也过期了；
3. 显式把 matplotlib 声明为可选，缺失时**记日志**而不是 `pass`。

三种都要求先把那个裸 `except` 打开。

**全仓裸 `except …: pass` 扫描**（AST 遍历 `ExceptHandler`，非 grep）：

```
hescope/ + app.py 共 28 处（hescope/ 21 处，app.py 7 处）
最密集：hescope/wsi/slides.py 7 处
        hescope/viewer/tileserver.py 6 处
        hescope/wsi/dicom_source.py 4 处
其余：agent/agent_bridge.py 1、analysis/ml.py 2、viewer/overlay.py 1
```

原文"建议全仓扫一遍"这条仍然有效，本文给出了具体清单。

### 3.2 cell 粒度反向漂移 —— 仍在，且略有恶化

| | 原文 | 实测 `e18c304` |
| --- | --- | --- |
| cell 数 | 50 | **50** |
| 最长 cell | 294 行 | **294 行** |
| 中位数 | 64 行 | **64 行** |
| 超 80 行的 cell | 18 个 | **18 个** |
| 超 80 行合计 | 2,404 行 | **2,446 行** |
| 占 `app.py` | 65% | **65.4%**（2,446 / 3,740） |

八个子包的拆分动了 `app.py` 62 行（`git show e18c304 --stat`），
但**一行逻辑都没搬出去**。§9.2 那条"超 80 行且几乎不引用 `hescope` 就是没提取的模块"
判据，**至今零执行**。

### 3.3 反对：README 的假 badge **没有**被删掉

有说法称"README 的假 badge 已经不存在"。**这条不成立。** 实测：

```
$ grep -nE "badge|passed|postgres" README.md
12:![tests](https://img.shields.io/badge/tests-276%20passed-brightgreen.svg)
174:**276 passed, 1 skipped**, the skip being a permission-bit test that only holds
230:| Switching databases | Set `HESCOPE_DB_URL` (e.g. `postgresql://user:pass@host:5432/hescope`), ...
$ ls -d .github
(no .github)
```

- 硬编码 badge 还在（`tests-276 passed`）；
- 正文第 174 行还在重复同一个假数（`276 passed, 1 skipped`）—— **原文只提到了 badge，
  漏了正文这一处**，只删 badge 会留下一半；
- 仍然没有 `.github/`，所以它至今不是 CI 产物；
- Postgres 承诺（第 230 行）也还在。

**真实数字已从原文写的 790 变成 909 passed / 17 skipped。**
这个 badge 现在错了 633 个用例，比原文核对时错得更多。原文这一条不但仍然成立，
而且需要把"实际 790"更新为"实际 909"，并把要删的位置从一处扩到两处。

---

## 4. 附录 A 依赖实测复现

### 4.1 先说一件影响所有结论的事：`.venv` 不是原文的基准环境

| | 原文基准 | 本仓 `.venv` 实测 |
| --- | --- | --- |
| Python | 3.12.3 | **3.11.14** |
| 包数 | 64 | **113** |
| numpy | 2.5.2 | **2.4.6** |
| scipy | 1.18.0 | **1.17.1** |
| zarr | 3.3.0 | **3.1.6** |
| tifffile | 2026.7.31 | **2026.3.3** |

命令：`uv pip list --format=freeze`（`VIRTUAL_ENV=<repo>/.venv`）。

**注意一个反直觉的事实**：本仓 `.venv` 里的 scipy（1.17.1）和 tifffile（2026.3.3）
**恰好就是原文说 VALIS 会把 core"降级到"的那两个版本**。所以拿 `.venv` 直接
dry-run VALIS 会看不见这两个降级、从而得出"VALIS 没那么脏"的错误结论。
**任何复现这一节的人都必须先重建纯 core 环境，不能用 `.venv`。**

本节按附录 A 重建：

```bash
uv venv --python 3.12.12 E:/tmp/corebase
uv pip install "marimo>=0.23" pillow numpy scipy scikit-image plotly requests \
               tifffile "zarr>=3" imagecodecs "sqlalchemy>=2.0" "anywidget>=0.9"
```

得到 **62 个包**（原文 64，差 2，六天的上游漂移），
`numpy 2.5.2` ✓、`scipy 1.18.0` ✓、`zarr 3.3.0` ✓、
`tifffile 2026.8.16`（原文 2026.7.31，上游已发新版）。**基准复现成功。**

### 4.2 复测结果

方法：`uv pip install --dry-run <pkg>`，把输出的 `+` 行与基准 freeze 求名称交集，
交集内 = 改动已有包，交集外 = 新包（脚本 `E:/tmp/diff.py`）。

| 包 | 原文：新包 | 实测：新包 | 原文：改动 core | 实测：改动 core |
| --- | ---: | ---: | --- | --- |
| `valis-wsi` | 81 | **59** | numpy / scipy / tifffile | **numpy / scipy / tifffile —— 完全一致** |
| `spatialdata` | 82 | **83** | 0 | **0** |
| `scanpy` | 35 | **36** | 0 | 0 |
| `anndata` | 16 | **17** | 0 | 0 |
| `wsidicom>=0.20` | 8 | **9** | 0 | 0 |
| `pyarrow` | 1 | **1** | 0 | 0 |

#### （1）VALIS：最重要的那条论断成立，包数不成立

```
- numpy==2.5.2         →  + numpy==1.26.4
- scipy==1.18.0        →  + scipy==1.17.1
- tifffile==2026.8.16  →  + tifffile==2026.3.3
```

numpy 大版本回退 **2.5.2 → 1.26.4 实测确认**。原文点名的重型传递依赖也全部确认：

```
+ cjdk==0.5.0   + jpype1==1.7.1   + scyjava==1.12.5     （Java 运行时）
+ pyvips==3.1.1                                          （libvips 系统库）
+ torch==2.13.0 + torchvision==0.28.0
+ opencv-contrib-python-headless==4.9.0.80
```

**"VALIS 永远不能进 core，只能是进程外适配器"这个决策不需要重估。**

包数 81 → 实测 59 的差异有明确解释：本机是 Windows，
`grep -ic nvidia dry-valis.txt` → **0**，整套 `nvidia-*` CUDA wheel 不出现
（Linux 上 torch 的 CUDA 依赖是 20+ 个独立 wheel，恰好补上这 22 个的差）。
**原文这个 81 应该标注平台**，否则在 Windows 上复现会对不上，
而"对不上"很容易被误读成"这次测量不可信"。

#### （2）spatialdata：82 → 83，"0 改动"成立。但原文漏了它最该被写下来的那件事

```
$ grep -iE "^\s*\+ (numba|llvmlite)" dry-spatialdata.txt
 + llvmlite==0.49.0
 + numba==0.67.0
```

原文 §3.3 把 numba 风险**只挂在 scanpy 名下**（"scanpy 今天干净…但它带进
numba 0.67.0"），而 §3.1 表里 spatialdata 只写"82 新包，0 改动"，读起来像是干净的。
**实测 spatialdata 同样带进 numba 0.67.0 + llvmlite 0.49.0**，而：

```
$ python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('https://pypi.org/pypi/numba/0.67.0/json')); print([r for r in d['info']['requires_dist'] if 'numpy' in r])"
['numpy<2.6,>=1.22']
```

基准 numpy 是 2.5.2 —— **只剩一个小版本余量，对 spatialdata 与 scanpy 一样适用**。

原文 §6.7 建议"直接抄 SpatialData 的概念模型" —— 抄概念不装包，没问题；
但 §3.1 的表格必须加上 numba 标记，否则下一个人会以为
"82 包 / 0 改动"意味着 spatialdata 可以安全装在 core 旁边。
它同时也顺带带进 `dask` / `dask-image` / `xarray` / `xarray-spatial` / `anndata`。

#### （3）Visium "读取零新依赖"：成立，实测确认

在纯 core 环境里造了一份最小 Space Ranger 输出（`matrix.mtx.gz`、`barcodes.tsv.gz`、
`features.tsv.gz`、`spatial/tissue_positions.csv`、`spatial/scalefactors_json.json`），
只用 `gzip` + `csv` + `json` + `io` + `scipy.io.mmread` 完整读出矩阵、条码、特征与坐标：

```
--- package count BEFORE --- 62
matrix: (3, 2) nnz= 3 dtype= int64
barcodes: 2 features: 3
xy points (col=x,row=y): [(4300.0, 8600.0), (9100.0, 2100.0)]
spot_diameter_fullres: 89.4
microns_per_pixel present? False
--- package count AFTER --- 62
```

**62 → 62，零安装。** 原文 §7.1 的表格逐行成立；三个坑里有两个在这份最小样本上
直接复现了：行列反（`pxl_col_in_fullres` 才是 x）、以及"字段不保证存在"
（`microns_per_pixel` 确实不在 `scalefactors_json.json` 里）。
Visium HD 的 parquet 路径实测 `pyarrow` = **+1 包、0 改动**，与原文一致。

**要补的一条**：`scipy.io.mmread` 在 scipy 1.18.0 上已发 `DeprecationWarning`
（返回类型将于 1.20 从 `spmatrix` 改为 `sparray`）。本仓 `pytest` 配置是
`filterwarnings = ["error::FutureWarning"]`，**DeprecationWarning 不在其中**，
所以这条今天不会把测试跑红，将来会静默改变行为。
Phase 5 的 reader 应当一开始就在 `mmread(...)` 之后显式转型，别依赖默认返回类型。

### 4.3 结论

支撑三个决策的数字里，**两个的量级需要修，方向全部不变**：

- **VALIS 禁入 core** —— 成立，且证据比原文更硬（降级三件套逐条复现）；
- **Visium 优先** —— 成立，实测零安装；
- **spatialdata 只抄模型不装包** —— 成立，且理由**比原文写的更强**：它也带 numba。

---

## 5. §16 落地顺序：完成度与开工点

| Phase | 实测状态 |
| --- | --- |
| **Phase 0.1** `schema_migrations` + `PRAGMA foreign_keys` | **已完成**（`store/migrations.py`；`db.py:106/112/119` 三个 pragma：`foreign_keys=ON`、`busy_timeout`、`journal_mode=WAL`） |
| **Phase 0.2** `content_key` + `slide_files` | **代码已完成，数据未迁移**（活库 version 0 of 3，见 2.2） |
| **Phase 0.3** `measurements` 表 + `mpp_effective` | **未开始**（两者皆 0 命中） |
| **Phase 0.4** `overlay.py` + 扫 `except: pass` | **未开始**（缺陷在，28 处裸 pass） |
| **Phase 0.5** 删 README 假 badge / Postgres 承诺 | **未开始**（见 3.3） |
| **Phase 0 判据** | **判据本身要改写**（见 2.4） |
| **Phase H1** Harness 骨架 | **几乎未开始**：`app.py` 仍 3,740 行（目标 ≈1,200）；`skeleton.lock` 缺、`tests/test_harness.py` 缺；`AGENTS.md` 在但无"分区 / 台面 / 不变量"三节；`skills/he-scope/SKILL.md` 已存在 |
| **Phase 1** 通用点集层 | **未开始**（无 `layers`、无 `selection_resolutions` 表） |
| **Phase 2** 上下文与身份 | **部分完成，判据不可执行**（见 5.1） |
| **Phase 3** 参照分布 | **未开始**（`created_by` 不存在，判据取不到分母） |
| **Phase H2 / 4 / 5 / 6+** | 未开始 |

已完成但**不在原文 §16 计划里**的工作：`hescope doctor`、`hescope delete-roi`、
`hescope dedupe-slides`、`hescope migrate-tcga-catalog`、
`INSERT … ON CONFLICT` 消除注册竞态、`hescope/` 八子包拆分。
原文 §16 需要补一行说明这些属于哪个 Phase，否则"完成度"永远算不清。

### 5.1 Phase 2 的实际状态：TCGA 骨架在，上下文与判据都不在

**已完成**：`tcga_projects` / `tcga_cases` / `tcga_samples` / `tcga_files` 四张表；
`tcga_files.slide_id` 列 + migration 3 的真 FK 与回填；
`tcga_samples.sample_type` 已有真实数据：

```
Primary Tumor 63, Solid Tissue Normal 10, Metastatic 6, FFPE Scrolls 4,
Additional Metastatic 2, Slides 1
```

**未完成，且直接卡住判据的两点：**

1. **`tissue_source_site` 全库不存在。**
   `gdc/tcga_schema.py` 的 `_BARCODE_RE` 能解析出 `tss` 分组，
   但没有任何一张表存它。原文判据里的 SQL 跑不起来：
   ```
   sqlite3.OperationalError: no such column: sm.tissue_source_site
   ```
2. **GDC 拉的是 `expand=cases.project,cases.samples`（`gdc/tcga.py:165`），不含
   `diagnoses`。** Phase 2 明写的"`expand=diagnoses` 拉临床字段"未做，
   所以分期 / 生存等上下文一个字段都没有。

即便把 `tissue_source_site` 条件去掉，判据 SQL 在活库上也返回 `[]`：
`tcga_files` 100 行全部 `slide_id IS NULL`、`local_path IS NULL`
（一张片子都没下载过，migration 3 的 dry-run 也报 "would link 0"）。

### 5.2 施工应该从哪里开始

按"解锁下游最多 / 代价最小"排序：

1. **先把活库迁到 version 3**（Phase 0.2 的收尾）。
   这是唯一一个"代码写完了但收益一分没兑现"的位置：
   31 张片子的 `slide_files` 与 5 个 identity 全在 dry-run 里躺着。
   做之前按 R-1 备份、做之后再取一次哈希；
   **注意 `.db` 单文件哈希在 WAL 模式下不足以证明"没动过"**（见 0.1），
   备份要连 `-wal` 一起。
   还要注意 26/31 的文件已丢失，迁移会把它们标 missing 而不是报错，
   这意味着**迁移之后活库上仍然演示不了"一片子两文件"**，
   验收要另找一个真存在两份的文件。
2. **Phase 0.3 `measurements` + `mpp_effective`。**
   它同时是 Phase 3 的前置、Phase H2"出口"的前置，也是原文 §1 判据的落点。
   三个下游一次解锁，是本轮性价比最高的一块。
3. **Phase 0.4 / 0.5 这两件顺手活。** 但 0.4 的正确修法不是原文写的补 import
   （见 3.1），**别照着原文改**；0.5 要删两处不是一处（见 3.3）。
4. **Phase H1 的搬运可与上面并行**（原文说得对：纯搬运、无设计决策），
   但要先解决第 6 节那个打包断裂，否则搬出去的新模块进不了发行包。
5. **`created_by` 建议从 Phase 3 提前到 Phase 0.3，跟着 `measurements` 表一次加完。**
   等到 Phase 3 再加，意味着 Phase 0–2 期间产生的所有行都没有出处，
   而且补不回来 —— 这个字段的价值全在"从第一行数据就有"。

---

## 6. 核对中发现的、原文没有覆盖的一个当前故障

`e18c304`（八子包拆分）把 `hescope/` 拆成 8 个子包，但
`pyproject.toml` 的 `[tool.setuptools] packages = ["hescope"]` **没有跟着改**。
setuptools 只打包这一个包，不含子包：

```
$ uv build --wheel --out-dir E:/tmp/wheelout .
Successfully built E:\tmp\wheelout\hescope-0.1.0-py3-none-any.whl
$ python -c "import zipfile,glob; print([n for n in zipfile.ZipFile(sorted(glob.glob('E:/tmp/wheelout/*.whl'))[-1]).namelist() if n.endswith('.py')])"
['app.py', 'hescope/__init__.py', 'hescope/cli.py']
```

**39 个 `.py` 里只打进去 2 个。** 装上就炸：

```
$ uv pip install --no-deps hescope-0.1.0-py3-none-any.whl
$ python -c "import hescope.cli"
  File ".../site-packages/hescope/__init__.py", line 3, in <module>
    from .agent.agent_bridge import (
ModuleNotFoundError: No module named 'hescope.agent'
```

**926 个测试全部测不到这个**，因为它们跑在 editable 安装 + repo 根目录下，
`hescope/` 是从工作树直接 import 的。

修法：把 `packages = ["hescope"]` 换成
`[tool.setuptools.packages.find]` + `include = ["hescope*"]`，
并**加一个真去构建 wheel、装进干净 venv、`import hescope.cli` 的测试**
—— 否则下次拆包会再犯一次。按 R-2，这个测试在改 `pyproject.toml` 之前
必须先跑出上面那条 `ModuleNotFoundError`。

（`uv build` 在仓库里生成的 `build/` 目录已 `rm -rf` 清理，工作树未留残留。）

---

## 7. 建议对原文做的最小修改清单

| 位置 | 现状 | 应改为 |
| --- | --- | --- |
| §2.1 全表 | `f541b68` 的数字 | 换成第 1 节的 `e18c304` 数字，并标注 commit |
| §2.1 / 附录 B | `app.py` 3,698 与 3,673 自相矛盾 | 统一为 3,740 |
| §2.2 | "L1–L4 一行没落地" | "L1–L2 代码已落地；`measurements` / `geom_key` / `mpp_effective` / `created_by` 未落地；且**没有一条迁移跑过真实数据**" |
| §2.3 第 1 条 | 病因 = `Path` 未导入 | 病因 = `import matplotlib` 先失败 + `Path` 未导入，被同一个 `except` 吞掉；matplotlib 不在 core，修法不是补 import |
| §2.3 第 3 条 | "实际 790 passed" | "实际 909 passed / 17 skipped"；badge 与正文第 174 行**两处**都要删 |
| §3.1 表 | `valis-wsi` 81 | 59（Windows）/ 81（Linux，含 `nvidia-*`）—— 必须标平台 |
| §3.1 表 | `spatialdata` 82，未标 numba | 83，**并标注"引入 numba 0.67.0 + llvmlite"** |
| §3.3 | numba 风险只挂 scanpy | 加上 spatialdata |
| §7.1 | Visium 零新依赖 | **保留**（实测 62 → 62 确认），补一句 `scipy.io.mmread` 的 `sparray` 迁移 |
| 附录 A | 基准 = "Python 3.12.3 / 64 包" | 补一句"本仓 `.venv` 不是这个基准，复现必须重建纯 core 环境"（见 4.1） |
| §16 Phase 0 判据 | "`slides.path` 不再 UNIQUE" | 改为"同内容两路径 = 一行 `slides` + 两行 `slide_files`，任一路径可达全部 ROI"（R-4 不允许 DROP） |
| §16 Phase 2 判据 | 含 `tissue_source_site` | 该列不存在；判据要么加一步"把 barcode 的 tss 落库"，要么改判据 |
| §16 Phase 3 | `created_by` 在 Phase 3 | 建议提前到 Phase 0.3，与 `measurements` 一起加 |
| §16 | 未收录本分支已完成的计划外工作 | 补 `doctor` / `delete-roi` / `dedupe-slides` / `migrate-tcga-catalog` / 子包拆分的归属 |
| 新增一节 | — | "发行包完整性"，见第 6 节 |
