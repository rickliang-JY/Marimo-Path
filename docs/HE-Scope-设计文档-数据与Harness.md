# HE-Scope 设计文档:数据与 Harness

> 状态:构思稿,非施工图。
> 代码核对基准:`rickliang-JY/Marimo-Pathology` @ `main` (f541b68),2026-08-14。
> 依赖数字与 `app.py` 结构数字均为实测,复现方式见附录 A / B。
> 本文档不做竞品对标,不服务于论文或用户增长。判据只有一条:**三个月后你还愿不愿意打开它。**

**两部分:**

- **第一部分 — 数据与 Schema**:什么数据该进来、以什么形状进来、schema 怎么设计才能不被新模态撑坏。
- **第二部分 — Harness**:人和 code agent 怎么用这个东西;哪里能改、哪里不能改;skill 从哪来、怎么不腐烂。

第一部分回答"数据怎么进来",第二部分回答"进来之后怎么用"。两者共用同一批不变量。

---

## 决策速查

一页纸的结论。每条都可追到正文的实测或论证。

| 领域 | 决定 | 依据 |
| --- | --- | --- |
| 集成方式 | 新方向走 **agent + 进程外适配器**,不进 import graph | §3 |
| VALIS / 连续切片配准 | **暂缓**;永不进 core(实测强制 numpy 2.5.2→1.26.4) | §3.2 §7.4 |
| SpatialData | 抄**概念**,不装包(实测 82 新包) | §6.7 |
| 远端 Postgres | **不做**,删掉 README 承诺 | §6.1 |
| 优先补的数据 | 分母(组织分割)> 上下文(临床)> 身份(TSS)> 参照分布 | §5 |
| 第一个新模态 | **Visium**(读取零新依赖);其次 mIF/IMC(同片免配准) | §7.1 §7.3 |
| 新模态怎么进 schema | 加 `layers` + `selection_resolutions` 两张通用表,**不为每个数据源建表** | §6.4 §6.5 |
| 配准缺省值 | `registration DEFAULT 'unregistered'`,**绝不默认恒等** | §6.2 |
| 重数据存放 | zarr;DB 只存清单、来源与决定 | §6.2 |
| 记忆本体 | **数据库**;notebook 是视图,agent memory 是缓存不可信 | §6.6 |
| `app.py` | 先搬走 2,404 行逻辑(占 65%),留 wiring | §9 |
| UI 结构 | 四块,只有"图层列表"增长,且增长在数据里 | §11 |
| 可视化 | **三个坐标系,永远三个**;模态往里填数据,不加 viewer | §12 |
| 方法选择 | **平台不限定**,只强制记录;core 不装任何降维/分割实现 | §8.1 §12.4 |
| 投影视图 | **可选而非必需**;空间数据在切片视图上就能分组 | §12.5 |
| harness 的原理 | 一条界限:**分析有结构,方法只被记录** | §8.1 |
| harness 载体 | **文档**(`AGENTS.md` + `SKILL.md`),**零新目录** | §8.2 §15.0 |
| harness 的强制力 | `tests/test_harness.py` 四个锁 + `skeleton.lock` | §15.3 |
| skill 数量 | 自己写 **~5 个** harness skill;可移植的从社区装 | §14.1 |
| skill 自动生成 | **只提议不提交**,进 `_proposed/`,过锁一才晋升 | §14.5 |
| harness 包化 | **不做**(n=1、可逆性不对称) | §17.1 |
| MCP | **不做,也不预留抽象层**;"预留"= 不叉开真相 | §15.4 |

**最小可开工项**(不依赖任何决策,今天就能动):按 §9.2 的机械判据搬那 2,404 行。

---

## 0. 不再重新论证的前提

1. **三 native**:Python-native、marimo-native、code-agent-native。
2. **昂贵状态留在 kernel**,一切 agent 需要碰的东西放在模块作用域。
3. **依赖稳定性 > 功能广度**。这是单人维护项目的第一约束,不是偏好。
4. **专注 pathology**。扩展方向是"同一张切片上的更多测量层"和"同一个病人的更多上下文",不是"更多学科"。
5. **记忆保存在本地。**

---

# 第一部分 — 数据与 Schema

## 1. 一个判据:一个数字要成立,需要哪些字段

"还应该加什么数据"如果按学科列清单,是没有边界的。换一个问法就有边界了:

> **你现在能对一个 ROI 算出一个数。这个数说不清什么?**

| 说不清的事 | 缺的东西 | 类别 |
| --- | --- | --- |
| 这个数**可比吗** | 分母:组织区域分割;以及测量条件(mpp、方法版本) | 分母 |
| 这个数**是关于什么的** | 诊断、分级、分期 | 上下文 |
| 这个数**有什么后果** | 生存、随访、复发 | 上下文 |
| 这个数**是不是假的** | 送检机构、扫描仪、染色批次 | 身份 |
| 这个数**算高还是低** | 同类测量的参照分布 | 参照 |

**五行里只有一行需要新的成像模态。** 其余四行要么是现有 API 的一次 join,要么是对已有数据的一次聚合。

**HE-Scope 当前的瓶颈不是"像素种类不够",是"像素之外的字段太少"。**

`docs/DATABASE-DESIGN.md` 里 `mpp_effective` 那一列就是这个判据的实例——它让"分辨率不可比的两行不能被平均"变成一句 `WHERE`,而不是部落知识。本文档要做的是把这个思路推广到所有字段,以及推广到 harness。

---

## 2. 现状核对(在 `main` 上实测)

### 2.1 规模

| | 行数 |
| --- | --- |
| `hescope/` | 10,049 |
| `app.py` | 3,698(50 个 cell) |
| `tests/` | 14,542(660 个 test 函数) |
| `docs/` + `bugs/` | ~10,400 行 markdown |

### 2.2 `docs/DATABASE-DESIGN.md` 的 L1–L4 设计在 `main` 上一行没落地

实测:`hescope/` 全目录检索不到 `content_key`、`slide_files`、`measurements`、`schema_migrations`、`geom_key`、`mpp_effective`、`created_by` 中的任何一个。`hescope/db.py` 仍是 `Slide` / `ROI` / `Interaction` / `AgentRun` 四张表,`slides.path` 仍然 `UNIQUE`。

**切片的身份到今天仍然是文件路径。**

你已经写出了一份质量很高的数据库设计(用真实的 50 行 / 31 张片子测出来,partial hash 那段量了 0.17s → 0.002s)。问题不是设计不够好,是它还在 `docs/` 里。**在"身份是路径"的地基上加任何新模态,都会把这个错误复制一遍。**

（若 L1–L4 已在某分支完成,本节作废,施工顺序从 Phase 1 开始。）

### 2.3 三个已知缺陷的当前状态

- **`hescope/overlay.py:48` 的 `Path` 仍未导入。** 比漏 import 更隐蔽:调用点被包在 `try: ... except Exception: pass` 里,`NameError` 被静默吞掉,scale bar 永远走 fallback,永远画 `um` 而不是 `µm`。测试抓不到,因为函数确实"成功返回"了。**这正是你自己归纳的 failure rendered as success。** 建议全仓扫一遍裸 `except Exception: pass`。
- **cell 粒度反向漂移**:41 cell / 最长 127 行 → 50 cell / 最长 294 行。详见 §12。
- **README badge 不只是假 CI,还是错的**:硬编码 `tests-276 passed`,无 `.github/`,实际 790 passed。删掉比补上 CI 更省事,也同样诚实。

---

## 3. 核心架构决定:集成层是 code agent,不是 import graph

所有"能不能加 X"的问题,答案都取决于这一条。

**如果集成层是 import graph**:加一个方向 = 多一组依赖 = 主进程要 import 它。core 随方向数线性膨胀,各方向版本约束互相打架。

**如果集成层是 code agent**:加一个方向 = 写一个 skill,agent 在独立环境的子进程里跑,结果以 parquet / zarr 交回。主进程从头到尾没 import 过它。**core 永远不长。**

"agent-native"的含金量在这里——它不是"agent 能帮我写代码",而是 **agent 承担了原本由 import 承担的集成职责**。

### 3.1 实测证据

干净环境(仅 core 依赖,解析后 64 个包)中各候选包的增量:

| 包 | 新增包数 | 是否改动已有包 |
| --- | ---: | --- |
| `biopython` / `rdkit` / `pyarrow` / `h5py` / `openslide-python` | 1 | 否 |
| `tiffslide` | 2 | 否 |
| `wsidicom>=0.20` | 8 | 否(与 pyproject 注释记录的 7 一致) |
| `pyopenms` | 10 | 否 |
| `anndata` | 16 | 否 |
| `torch` | 27 | 否 |
| `scanpy` | 35 | 否 |
| `timm` | 35 | 否 |
| **`spatialdata`** | **82** | 否 |
| **`valis-wsi`** | **81** | **是** |

### 3.2 决定性的那一行:VALIS

`valis-wsi`(连续切片配准的事实标准)不只是 81 个包,它会**改动 core**:

```
numpy:     2.5.2      →  1.26.4     （大版本回退）
scipy:     1.18.0     →  1.17.1
tifffile:  2026.7.31  →  2026.3.3
```

numpy 从 2.5 退到 1.26 会连锁影响 `zarr>=3`、`imagecodecs`、`scikit-image`——你 core 的一半。它还会拉进 `jpype1` + `scyjava` + `cjdk`(Java 运行时)、`pyvips`(libvips 系统库)以及整套 CUDA wheel。

**VALIS 永远不能进 core,任何形式都不行。** 只能是进程外适配器。这不是偏好判断,是一次测量。

### 3.3 次要但要记住的风险:numba

`scanpy` 今天干净(35 包、零改动),但它带进 `numba 0.67.0`,而 numba 声明 `numpy<2.6`。当前 numpy 是 2.5.2——**只剩一个小版本余量**。numpy 2.6 发布后,同时装了 scanpy 和 core 的环境会被 numba 把 numpy 钉住。

**"今天能装"不等于"可以进 core"。** numba/llvmlite 是科学 Python 生态里对 numpy 版本最敏感的一对。

### 3.4 三层环境模型

```
┌─────────────────────────────────────────┐
│ core env      marimo + 11 个依赖          │  ← 永不增长
│               kernel 常驻昂贵状态          │
└─────────────────────────────────────────┘
              ↕  数据级契约(zarr / parquet / json)
┌─────────────────────────────────────────┐
│ adapter envs  每方向一个独立环境           │  ← 随意增长
│               scanpy / valis / torch ...  │
│               agent 在子进程里调用          │
└─────────────────────────────────────────┘
```

**契约是数据,不是 API。** 适配器输出必须是文件(zarr 数组 + json manifest),不是 Python 对象。这样契约可测试、可缓存、可检查,升级适配器不需要重启 kernel。

---

## 4. 数据分类:按"相对切片的形状",不按学科

| 形状 | 数据 | 连接键 | 能否叠在片子上 | schema 成本 |
| --- | --- | --- | :---: | --- |
| 每病人一个值 | bulk RNA、突变、CNV、甲基化、TMB/MSI、临床、生存 | case | 否 | 一次 join |
| 每片子一个值 | 扫描仪、染色批、QC、片级评分 | slide | 否 | 一列 |
| 每 tile 一个向量 | 基础模型 embedding | slide + 坐标 | 是 | **已有** |
| 每空间点一个向量 | Visium、Xenium、mIF、IMC | slide + 配准 | 是 | reader + 配准 |
| 每细胞一个向量,**无坐标** | 解离式 scRNA-seq、流式 | case | **否** | 一次 join |

### 4.1 "RNA"至少是三样不同的东西

- **bulk RNA-seq** — 每病人一条向量。TCGA 配对最齐全,`tcga.py` 加一个下载分支即可。便宜,但**不检验选择面抽象**(片子上画哪都是同一条数)。
- **解离式 scRNA-seq** — 每细胞一条向量,**无坐标**。永远无法叠在切片上,只能回答"这个病人有哪些细胞类型"。
- **空间转录组** — 每点一条向量,**有坐标**。**唯一能被 ROI 选中的那一种。**

把三者混称"RNA 数据"设计一张表,会得到一个谁也服务不了的 schema。

---

## 5. 缺什么,按性价比排序

### 5.1 分母(组织区域分割)—— 最被低估

"同一 ROI 密度差 29.4 倍"未必全是坐标/mpp 问题。细胞密度是除以 **ROI 面积**、**组织面积**、还是**上皮面积**?三个数可差一个量级,而且**都对**。

**组织区域分割(上皮 / 间质 / 坏死 / 腔隙 / 脂肪)不是新功能,是让现有所有数字变得可比的前提。** 没有它,每一个密度都缺一个未声明的分母。

现成路线:Cerberus 这类多任务框架在共享编码器上同时预测细胞核、腺体、腔隙和组织区域;HoVer-Net / CellViT 已把细胞核分为 neoplastic / epithelial / inflammatory / connective 等类,可直接给出"上皮核 vs 间质核"的分层计数。

**排在所有新模态之前。**

### 5.2 上下文(临床 / 分期 / 生存)—— 最便宜

GDC `cases` 端点 `expand=diagnoses` 一次调用返回:`primary_diagnosis`、`morphology`(ICD-O-3)、`tumor_grade`、`ajcc_pathologic_stage/_t/_n/_m`、`vital_status`、`days_to_death`、`days_to_last_follow_up`、`age_at_diagnosis`、`tissue_or_organ_of_origin`、`site_of_resection_or_biopsy`。

`tcga.py` 已在打这个 API,`cases`/`samples` 表已在设计里。**纯加行不加架构。**

⚠️ GDC 上的 pathology report 是**扫描件 PDF**,要结构化得先 OCR。别把它和上述结构化字段混为一谈。

### 5.3 身份(批次 / 病人 / 蜡块)—— 几乎白送,影响最大

`TCGA-BH-A18H` 里的 `BH` 就是 tissue source site。这不是可选元数据:

- 有研究在 3,000+ 例、6 种癌型上证明,TCGA 中生存、突变、分期等特征在不同送检机构间差异显著,深度学习模型可**轻易从图像识别出送检机构**;常用染色归一化和增强消不掉(一阶染色差异只能部分降低,二阶 Haralick 特征几乎不受影响),由此导致对生存、突变、分期的预测精度被系统性高估。
- 更值得注意:**病理基础模型的 embedding 也编码机构信息**。有工作直接从多个 FM 的特征向量预测 TSS 且准确率很高;也有工作在 TCGA-COAD/STAD 上用三种特征提取器测得 TSS 预测 AUROC > 0.95。

**推论:embedding registry 必须记录 TSS。** 否则任何"形态 ↔ 分子"相关都可能只是在识别医院。一列字符串换掉一整类假发现。

同时 `samples` 层(术前活检 vs 术后切除、原发 vs 转移、多蜡块、治疗前后)目前设计里有位置但无人使用。**这不需要任何新模态,就是已有 H&E 被正确组织起来**,却能问"同一肿瘤在不同蜡块上的异质性"这种单片永远看不到的问题。

### 5.4 参照分布 —— 零新数据

每个测量都是绝对值:`tissue_fraction = 0.62`。**0.62 是高是低?**

`measurements` 按 `(name, method, mpp 分桶)` 聚合分位数,一个 ROI 就能显示成"在你 412 个标注中排 p73"。

附带价值可能比主价值大:**若早有这个视图,29.4 倍会在第一天跳出来。** 同一指标呈双峰是不可能不被看见的。**绝对值会骗人,分布不会。**

同类零成本聚合:每片测量覆盖度、agent 提议 vs 人接受比率(`created_by` 的直接产物)、每次重算与上次的差异分布。

### 5.5 新成像模态 —— 按"要不要配准"排序,不按"有多酷"

> **若只加一个,选同切片的多重成像(mIF / IMC)或空间转录组,不要选 IHC。**

不是 IHC 不重要——它临床相关性最高——而是连续切片配准是独立且很深的工程问题(见 §3.2),做不好会污染上面所有数字,而你现在还没有"未配准就是未配准"的兜底机制。

---

## 6. Schema 设计

### 6.1 先拆一个混淆:"online 数据库"是两件事

**一是远端数据源**(GDC / TCIA / IDC / 10x):只读、不拥有、随时可能改版。
**二是远端存储后端**(往里写记录的 Postgres)。

"记忆保存在本地"已经把答案定死:**只需要前者。** 记忆本地化和远端后端是互斥目标,同时要就等于要做双向同步和冲突解决,那是团队协作软件的成本结构。

→ **README 里的 Postgres 承诺应该删掉,而不是补上 Phase 3。**

### 6.2 三条不变量

> **I. 外部标识永远不做主键。**
> 外部记录以 `(source, external_id)` 唯一约束入库,换本地 surrogate id;FK 只指向本地 id。远端改版不影响本地记忆。
>
> **II. 任何空间实体必须声明 frame 和到 level-0 的变换。**
> 不知道就是 `unregistered`,**不能默认恒等**。恒等默认值就是下一个 29.4 倍。
>
> **III. 重数据进 zarr,DB 只存清单、来源和决定。**
> 人的判断以几何形式存,机器的结果以"可检测漂移"的摘要存。

### 6.3 分层

沿用 `DATABASE-DESIGN.md` 的 L1–L4,加 L5:

- **L1 标本上下文** — `projects` / `cases` / `samples`,`UNIQUE (source, external_id)`。**建议扩充**:`cases` 加临床字段(§5.2),`samples` 加 `tissue_source_site`(§5.3)。
- **L2 切片身份** — `slides`(`content_key` 唯一)+ `slide_files`(多路径一片子)。**当前最大的地基缺口。**
- **L3 观察** — `annotations`(几何为唯一真相)+ `measurements`(带 `method` / `mpp_effective` / `params_json`)。
- **L4 溯源** — `interactions` / `agent_runs` / `schema_migrations`,FK 真正声明并每连接 `PRAGMA foreign_keys=ON`。
- **L5 测量层** — 见下。

### 6.4 L5:让新模态不再需要新表

`tcga_*` 那组表的教训在眼前:为一个数据源建一套表,结果 `tcga_files.slide_id` 50 行一次没写过,整个层级变成家具。Visium 来了建 `visium_spots`、Xenium 来了建 `xenium_cells`,三年后同一个下场。

加**两个通用概念**,之后任何模态都是插行:

```sql
CREATE TABLE layers (              -- 一张片子上的一个测量层
  id             INTEGER PRIMARY KEY,
  slide_id       INTEGER NOT NULL REFERENCES slides(id) ON DELETE CASCADE,
  kind           TEXT NOT NULL,      -- 'nuclei' | 'tile_features' | 'visium'
                                     -- | 'xenium' | 'mif' | 'imc'
                                     -- | 'tissue_regions' | 'embedding_projection'
  source         TEXT NOT NULL,
  external_id    TEXT,
  frame          TEXT NOT NULL,      -- 'level0' | 'fullres_capture' | '<projection_id>'
                                     -- 不得包含算法名:算法属于 params_json(§8.1)
  transform_json TEXT,               -- 到 level-0 的仿射;NULL 表示未知
  registration   TEXT NOT NULL
                 DEFAULT 'unregistered',  -- ← 这条表最重要的一个默认值
  store_uri      TEXT NOT NULL,      -- zarr 路径,重数据在这里
  n_entities     INTEGER NOT NULL,
  version        INTEGER NOT NULL DEFAULT 1,
  params_json    TEXT NOT NULL DEFAULT '{}',
  created_at     TEXT NOT NULL,
  UNIQUE (slide_id, kind, source, external_id)
);
```

**实体坐标不进 SQL。** Xenium 一片可达数十万细胞;而你要的操作是"点在多边形内"——numpy 一次向量化(几十万点毫秒级),不是 SQL 该干的。坐标和表达矩阵都放 zarr,DB 只有清单。这也符合"昂贵状态留在 kernel":layer 加载一次,常驻,agent 反复切。

### 6.5 选择的持久化:存决定,不存结果

不要建 `selection_members` 这种百万行关联表。ROI 的语义是**多边形**,那才是人做出的决定;命中哪些实体是可推导的。但推导结果会随 layer 版本变化:

```sql
CREATE TABLE selection_resolutions (
  annotation_id INTEGER NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
  layer_id      INTEGER NOT NULL REFERENCES layers(id)      ON DELETE CASCADE,
  layer_version INTEGER NOT NULL,
  n_selected    INTEGER NOT NULL,
  index_digest  TEXT    NOT NULL,   -- 命中索引集合的哈希
  resolved_at   TEXT    NOT NULL,
  PRIMARY KEY (annotation_id, layer_id, layer_version)
);
```

半年后重开一个 ROI,若 `n_selected` 或 `index_digest` 对不上,系统能**说出来**。

**这是 29.4 倍那一类问题的通用解法:不是保证结果不变,是保证结果变了不沉默。**

### 6.6 记忆的归属:三者不是选择关系

| | 角色 | 可信度 |
| --- | --- | --- |
| **数据库** | 记忆本体 | **唯一真相** |
| marimo notebook(`.py`) | 记忆的一个视图 / 可复现脚本 | 可重建 |
| 导出 HTML | 一次快照 | 只读留档 |
| agent memory | 缓存 | 会漂、不可版本化,**不能当真相** |

所以"记忆继承"的工程内容其实是:**`schema_migrations` 不是可选项**——记忆必须能跨 schema 变更存活。

### 6.7 可以直接抄的概念模型:SpatialData

scverse 的 SpatialData 已把这个问题做过一遍:五种原语(Images、Labels、Points、Shapes、Tables)+ 坐标变换对齐到公共坐标系(CCS),序列化到 OME-NGFF 兼容的 Zarr;并用交互式地标点把两张 Xenium 和一张 Visium 对齐到同一坐标系。

这几乎就是 §6.4 独立推出来的东西。**建议:概念上对齐 SpatialData(它是发表过的标准),但不依赖这个包**(82 个新包,见 §3.1)。

额外好处:你的 zarr 输出天然可被 squidpy / napari-spatialdata 读取——**那是别人 import 你的数据,不是你 import 别人的栈。**

---

## 7. 各模态接入代价

### 7.1 Visium —— 读取零新依赖 ★推荐首选

| 文件 | 读取方式 | 新依赖 |
| --- | --- | --- |
| `filtered_feature_bc_matrix/matrix.mtx.gz` | `scipy.io.mmread` + `gzip` | 无 |
| `barcodes.tsv.gz` / `features.tsv.gz` | 纯文本 | 无 |
| `spatial/tissue_positions.csv` | csv | 无 |
| `spatial/scalefactors_json.json` | json | 无 |

**core 依然 11 个依赖。** scanpy 那套留给适配器环境。

三个必须记下来的坑:

1. **行列反了。** `pxl_row_in_fullres` 是 **y**,`pxl_col_in_fullres` 是 **x**。
2. **"fullres" 不是你的 `.svs`。** 它指当初交给 Space Ranger 的用户图像。若那是另一台设备拍的亮场图,与 WSI level-0 之间需要仿射变换,往往靠地标点手动求 → `registration = 'unregistered'` 直到求出来。
3. **文件名随版本变。** Space Ranger v2.0 起 `tissue_positions_list.csv` 改名 `tissue_positions.csv` 并加表头。`scalefactors_json.json` 的字段(`microns_per_pixel`、`tissue_hires_scalef`、`spot_diameter_fullres`、`regist_target_img_scalef` 等)不保证都存在。

**Visium HD 例外**:`tissue_positions` 是 **parquet**,需 `pyarrow`(实测 +1 包,可接受);`spot_diameter_fullres` 语义变为方格边长(2 µm)。

### 7.2 Xenium

每细胞一条向量 + 每转录本一个坐标。数量级跳一级(数十万细胞),正是"坐标进 zarr 不进 SQL"的理由。10x 有公开的 Visium/Xenium 同一乳腺癌样本配对数据集,是验证跨模态对齐的现成材料。

### 7.3 mIF / IMC —— 架构上最契合

**同一张切片多轮成像,坐标就是同一个,配准问题消失。** 且天然是"每细胞一条 marker 向量",与 §6.4 的实体抽象完全同构。

格式事实标准是 **OME-TIFF**:各家原始格式(`.vsi`、MCD 等)先经 QuPath / MCD Viewer 转 OME-TIFF,多轮之间用 palom / wsireg 拼成多通道图,再由 MCMICRO / DeepCell 做分割和定量,产出单细胞 marker 矩阵 + 坐标——**正好就是一个 layer**。

也已有工作在**同一张玻片上**依次做 H&E、免疫荧光和 IMC,以规避连续切片对齐困难。**它把"多模态"从配准问题降级为读取问题。**

代价:公开数据比 IHC 少;IMC 分辨率较低(1 µm²/pixel)且 ROI 面积小(常 < 2 mm²),不是全片。

### 7.4 IHC / 特殊染色 —— 临床价值最高,工程代价最高 ⚠️暂缓

VALIS 全自动、支持 IHC 与 IF、支持 11–20 轮染色循环、可输出 ome.tiff。但见 §3.2:**81 包,且强制 numpy 2.5.2 → 1.26.4。**

→ 必须是进程外适配器,输入两个文件路径,输出一个变换矩阵 json。**主进程只消费那个 json,永不 import VALIS。**
→ 建议等 §6.4 的 `registration` 机制立住后再做。

### 7.5 bulk RNA / 突变 / 临床 / 生存

GDC 同一 case 既有诊断切片也有 STAR counts,`tcga.py` 加一个下载分支即可。**便宜,但不检验选择面抽象。**

### 7.6 配对放射影像 —— 唯一"新模态但不需要配准"的入口

TCIA / IDC 上很多 TCGA 队列有配对 CT/MRI/PET,而且 **TCIA 的 Patient ID 与 TCGA 的 Patient ID 完全相同**。TCGA 相关基线影像为术前影像。

你已在读 DICOM(`dicom_source.py`),放射与病理 DICOM 是同一标准的不同 IOD。它不试图叠在切片上——是同一个病人的另一个视角,`frame = 'independent'`,不需要配准。

### 7.7 明确不建议(至少现阶段)

- **解离式 scRNA-seq**:无坐标,只能当 case 级附属信息,不检验任何抽象。
- **化学信息学 / 质谱**:`rdkit` 只 +1 包、`pyopenms` +10 包,依赖上很便宜——**但其核心交互不是"在图上选一块"**,硬塞会撑坏选择面抽象。**这是架构理由,不是依赖理由。**

---

# 第二部分 — Harness

## 8. Harness 是什么

### 8.1 唯一的分界线:分析 vs 方法

Harness 的全部原理是一条界限。

**分析**是"我想知道什么",**方法**是"我这次打算怎么算"。前者稳定,后者一次一变。

判据很硬:**换一个方法,这句话还成立吗?**

| 说法 | 换方法后 | 是什么 |
| --- | --- | --- |
| "圈内和圈外的表达差异" | 换 Wilcoxon 还是 DESeq2 都成立 | **分析** |
| "这个 ROI 里上皮细胞的密度" | 换 HoVer-Net 还是 CellViT 都成立 | **分析** |
| "UMAP 上那一簇" | **换个投影就不成立** | 方法 |

第三行是关键:**它看起来像分析,其实是方法泄漏进了描述——而它会被存进数据库。**

**Harness 对两边的动作是同一个:记录。区别只在于对分析有结构(表、列、约束),对方法只有一个 `params_json`。**

| | 分析 | 方法 |
| --- | --- | --- |
| 谁决定 | 人 | agent / 当时的语境 |
| 变化频率 | 慢 | 每次可能不同 |
| 进 schema | **有结构地进** | 只作为 `params_json` 记录 |
| 进 core | 概念进 | **实现不进** |
| harness 职责 | 保证它被完整记录 | 保证它被完整记录,**然后闭嘴** |

> **Harness 的职责不是规定怎么分析,是保证任何分析都说得清自己是怎么算的。**

这条界限比它看起来重要:

**一、它是"不限定方法"能成立的唯一前提。** 不限定方法不等于放任。放任的版本是 agent 想怎么算怎么算、算完存个数——半年后你看到 `0.62`,不知道它怎么来的,这个数就是垃圾。**能不限定方法,恰恰是因为方法被完整记录了。记录到位,自由才安全;没有记录,自由就是熵。**

**二、它解释了 29.4 倍。** 同一 ROI 两个差 29.4 倍的密度,大概率**两次都对**,只是方法不同(分母不同、mpp 不同、分割器不同)。它之所以是一个 bug 而不是两条并存的记录,是因为**方法没被记下来,于是两个数看起来在回答同一个问题**。§5.1 的分母、§6.4 的 `params_json`、§15.3 的锁,都是同一件事的不同位置。

**三、它决定了 core 装什么。** 分析进 core(`measurements`、`layers`、选择契约),方法留在适配器环境。**core 一个降维包、一个分割模型都不该装——装了就等于 core 对一个方法表了态。**

**四、它是平台层不写方法名的理由。** 任何算法名出现在表结构里(`frame='umd@v1'` 之类)都是这条界限被越过的信号:结构意味着平台承诺了某种语义,而平台对具体算法没有任何承诺可给。

### 8.2 五个组成部分

下面五样全是 §8.1 的推论:

- **分区** —— 骨架是分析的结构,草稿是方法的实验场
- **台面** —— 分析层的稳定名字
- **出口** —— 方法算完之后,结果带着方法进证据库
- **skill** —— 告诉 agent 什么时候不能相信一个数,**也就是方法没记全的时候**
- **锁** —— 保证以上没被悄悄破坏

| 组成 | 回答的问题 | 载体 | 能否只用 markdown |
| --- | --- | --- | :---: |
| **分区** | 哪里能改、哪里不能改 | `AGENTS.md` + `app.py` 一行标记注释 | ✅ |
| **台面** | agent 写代码时能假设什么存在 | `AGENTS.md` 列名字,`app.py` 骨架区提供 | ✅ |
| **出口** | 探索的结果怎么变成记忆 | `AGENTS.md` 描述,`hescope/db.py` 实现 | ✅ |
| **skill** | 怎么做一件事、什么时候不能相信一个数 | `skills/` + `plugin.json` | ✅✅ |
| **锁** | 上面四条怎么保证没被悄悄破坏 | `tests/test_harness.py` | **❌** |

**前四样是文档,第五样不是——因为文档不会失败。**

这一点对本项目尤其要紧:HE-Scope 是 agent-native 的,它的接口本来就该是 markdown(整个 Agent Skills 标准就是 markdown)。**所以 harness 应该由文档定义,不需要一个专门的 `harness/` 目录。** 但正因为走文档路线,文档和代码脱节是**静默的**——改一个函数名,`AGENTS.md` 不会变红,agent 照着旧名字调,失败,然后开始猜。

> **一份会被验证的文档,才配当 harness。**
> 那个验证不是一个目录,是一个文件:`tests/test_harness.py`。

好消息是这份文档你已经写了大半:`AGENTS.md` 273 行,已覆盖启动方式、hard rules、entry points、payload schemas、写回结果、端到端示例。缺的只有分区、台面、不变量三节,以及那个验证。详见 §15。

---

## 9. `app.py` 的两层:骨架与草稿

### 9.1 先看现状(实测)

按 cell 拆 `app.py`,统计行数与 `hescope` 引用次数:

| 行数 | 引用 hescope 次数 |
| ---: | ---: |
| 294 | 1 |
| 227 | **0** |
| 203 | **0** |
| 180 | 19 |
| 174 | **0** |
| 145 | **0** |
| 119 | **0** |

超过 80 行的 cell 合计 **2,404 行,占 `app.py` 的 65%**。除了那个 180 行的(真正的 wiring),其余大 cell **几乎不调用 `hescope/`**。

**结论:`app.py` 里那 65% 不是 UI,是逻辑。** 它只是恰好写在了 UI 文件里。

这解释了两件事:为什么 `bugs/SUMMARY.md` 里 50 个问题有 23 个在 `app.py` cell wiring;以及为什么 `hescope/` 有 660 个测试而 `app.py` 只有两个浏览器测试(591 行,只能测 mount 和 OSD)——**那 2,404 行逻辑目前测不到。**

### 9.2 搬运判据(机械,无需主观判断)

> **一个 cell 若超过 80 行且几乎不引用 `hescope`,它就是一个还没被提取的模块。**

搬完之后:那些逻辑立刻进入 660 个测试的覆盖范围;`app.py` 缩到约 1,200 行;cell 变小,marimo 的反应式 DAG 变细,**agent 编辑精度回来了**。

这一步不需要想清楚 UI 该长什么样,是纯粹搬运,收益立刻兑现。

### 9.3 两层结构

搬完之后,`app.py` 分成寿命完全不同的两层:

```python
# ============================================================
#  骨架区(以上)
#  人维护 · 受粒度断言约束 · 受骨架锁保护 · agent 不得修改
# ============================================================

# ...（四块固定 UI,见 §11）...

# ============================================================
#  ▼▼▼ 草稿区(以下)▼▼▼
#  agent 只在此线以下追加 · 不计入 cell 预算 · 会话结束回收
# ============================================================
```

**为什么不用独立的 `scratch.py`:** 那样 kernel state 就断了。切片句柄、embedding、tissue mask 全要重新加载,而"昂贵状态跨迭代常驻"正是整个架构的立论。草稿必须和骨架同一个 kernel,所以必须同一个文件。**接受这个 marimo 约束,然后用一条注释线管理它。**

---

## 10. 台面:草稿区靠什么活着

探索区如果只是一块空白,每写一个草稿 cell 都要重新推导当前状态,那它没用。让它有用的是几个**契约级模块作用域名字**——人和 agent 都直接对着它们写代码:

```python
# 台面(骨架区末尾定义,草稿区可直接使用)
current_slide        # Slide:含 content_key / mpp / 标本上下文
current_selection    # 多边形 + 它来自哪个视图
available_layers     # list[Layer]:带 registration 状态
resolve(layer_id)    # -> 实体索引数组
```

**四个就够。** "探索区"的实质不是底部那片空白,而是"在这条线以下,这四个名字保证是活的、是最新的"。

有了它,一个草稿 cell 就是三五行:

```python
idx  = resolve(visium_layer.id)
expr = layer_array(visium_layer)[idx]
mo.ui.table(top_genes(expr))
```

agent 写这种代码不会出错,因为它不需要猜任何东西。

### 10.1 出口:探索的结果怎么活下来

草稿是易失的,这没问题。但如果一次探索算出了一个有意义的数,而它只存在于一个会被回收的 cell 里,**那这次探索对你的记忆没有任何贡献**,三个月后你什么也想不起来。

所以草稿区必须有出口:

```
草稿 cell 算出一个数
  → record_measurement(annotation_id, name, value, method, params, mpp_effective)
  → 进 measurements 表,带 provenance
```

**这是"探索"和"记忆"之间唯一的桥。** 没有它,草稿区就是一个高级 REPL;有了它,每次探索都往证据库加一行,而且那一行知道自己在什么条件下成立。

若探索产出的是判断而非数字,出口是 `annotations`(带 `created_by='agent'`)。

**没有出口的探索结果就让它死掉,这是对的。有出口而不走,才是浪费。**

### 10.2 回收

`Interaction` 表已有 `session_tag`。草稿 cell 按 session 打标,一个"清空本次草稿"的动作就够。个人项目里"手动清空 + 有用的先晋升到 `hescope/`"完全够用。

### 10.3 一个值得花十行代码的人体工学细节

骨架加草稿之后,切片视图在 cell #10,草稿在 cell #55,来回滚动很烦。廉价解法:**草稿区顶部放一个回声 cell**,重新渲染当前切片缩略图 + 选区轮廓 + 上下文条(复用 §12 那个函数)。

视线不用离开草稿区,而且它顺带验证了"人和 agent 看同一个真相"——回声显示的就是 agent 拿到的。

---

## 11. UI 结构:四块,只有一块会增长

"五个分析面板默认全折叠、agent 无存在感"不是样式问题:**五个就已经要折叠,说明面板模型在五这个量级就撑不住了。** 加两个模态就是十五个。

所以中心不能是面板:

| 块 | 内容 | 是否增长 |
| --- | --- | --- |
| 1. 选择面 | 切片视图 | 固定 |
| 2. 上下文条 | 我在看谁的哪张片子、配准状态 | 固定 |
| 3. 图层列表 | 由 `layers` 表驱动,注册表渲染 | **唯一会增长,但增长在数据里不在代码里** |
| 4. 证据轨迹 | agent 运行 + 测量记录 + provenance | 固定 |

面板降级成"图层自己带的东西",由模态模块提供,不在 `app.py` 里硬编码。

### 11.1 注册表:让 UI 代码不随模态数量增长

这是 §3 那个原则在 UI 层的同一件事。**你的代码库里已经有这个模式了**——`hescope/embeddings.py` 就是"薄注册表 + 懒加载"。复制到图层渲染上:

```python
# hescope/layer_ui.py
@dataclass
class LayerRenderer:
    kind: str
    label: str
    make_overlay: Callable   # layer -> OSD overlay
    make_panel:   Callable   # layer -> mo.ui element
    available:    Callable   # () -> bool   适配器环境在不在

LAYER_RENDERERS: dict[str, LayerRenderer] = {}
```

`app.py` 里只剩一个 cell:读当前切片的 `layers` 行,查注册表,渲染。**加 Visium = 新增一个模块 + 注册一行,`app.py` 零改动。**

`available()` 用于 graceful degradation:适配器环境没装,图层显示为"不可用",而不是整个 notebook 炸掉。

---

## 12. 可视化的控制:三个坐标系,不是 N 种图

新的可视化(RNA 表达、embedding 投影、差异表达结果)会把 §11.1 的注册表逼到边界——**UMAP 不在切片坐标系里,`make_overlay` 装不下它。**

正确的问法不是"能加几种图",是"**有几个坐标系**"。答案是三个,而且应该永远是三个:

| 视图类 | frame | 是否选择面 | 数量 |
| --- | --- | :---: | --- |
| **切片视图** | `level0` | 是(画多边形) | 1 |
| **投影视图** | `<projection_id>` | 是(套索) | 1 |
| **汇总视图** | 无坐标系 | 否(只读) | 1 |

**一个模态不带来新的 viewer,它往这三个里填数据。**

Visium 进来不是"加一个 Visium 视图",而是:一个点集(→ 切片视图)、一个表达 UMAP(→ 投影视图)、一张差异表达表(→ 汇总视图)。Xenium 一样,mIF 一样,IMC 一样。**viewer 数量恒定为三,模态数量无上界。**

Embedding 可视化本来就是投影视图,不需要任何新东西,只是往里喂另一个矩阵。

### 12.1 投影也是一个 layer

不需要新概念,`layers` 表已经能装:

```
kind='embedding_projection',  frame='<projection_id>',
registration='n/a',           store_uri=<zarr>,     -- 一个 (n, 2) 数组
params_json={'source_layer': 12, 'method': 'umap',
             'n_neighbors': 15, 'seed': 0, 'deterministic': false}
```

**注意 `frame` 里没有算法名**:方法属于 `params_json`(§8.1)。`deterministic` 是一个被记录的**事实**,不是一条禁令——它让"这个选择重算后还是不是同一批实体"变成可查的,而不是靠自觉。

它的实体**就是切片上那些实体的同一批 id**。所以联动不靠坐标——UMAP 和切片之间没有任何几何关系,也不需要有。

> **三个视图靠实体身份联动,不靠坐标。**
> 共享状态只有一个:`Selection = (layer_id, 实体索引数组)`,常驻 kernel。

### 12.2 接受新视图的判据

> **新视图必须能把自己的选择表达成 `(layer_id, 索引数组)`。表达不出来的,只能是只读汇总视图。**

只读汇总视图(小提琴图、火山图、排序基因表、分位数分布)不受数量限制,因为它们不参与状态,加一个的成本是常数。

### 12.3 这套机制你已经做出来一半了

`hescope/viewer.py` 有 `parse_plotly_selection`,`hescope/osdviewer.py` 有 `parse_osd_selection`,而且你写了 `test_osd_selection_matches_plotly_contract` 断言两者一致。

**那个测试就是答案的形状。** 要做的是把契约从"两个实现互相对齐"提升为"任何选择面都对齐到同一个规范形式",然后每加一个选择面就加一行契约测试。**这是项目里已经做对但还没被提取成原则的东西。**

### 12.4 投影后端:core 不选

按 §8.1,**降维方法属于方法层,core 不表态。** core 只需要会读一个 `(n, 2)` 数组和一份 `params_json`;数组是 UMAP、t-SNE、MDE、谱嵌入还是 PCA 算出来的,它不需要知道。

**所以 core 一个降维包都不装。** 下表不是推荐排序,是**决策时会用到的成本信息**——放哪个适配器环境、值不值得为一次探索装,由当时的问题决定。

| 包 | 新增包数(core 环境实测) | 改动 core | 关键传递依赖 |
| --- | ---: | --- | --- |
| `openTSNE` | **4** | 否 | scikit-learn |
| `umap-learn` | 8 | 否 | **numba + llvmlite** |
| `pymde` | 37(含 25 个 CUDA wheel) | 否 | **torch + torchvision** |

几点事实,供选择时参考:

- `pymde` 的 37 有误导性:若 `[ml]` extra 已装(本项目已有 torch),净增量按其 `requires_dist` 推算约十来个包,主要是 matplotlib 那一支和 torchvision。**(推算值,非实测——容器里 torch 装不下。)**
- **`umap-learn` 是三者中唯一引入 numba 的**。§3.3 那个风险(`numba 0.67.0` 声明 `numpy<2.6`,core 现为 2.5.2)在它这条路径上。装进适配器环境无妨,装进 core 会踩雷。
- **`pymde` 是框架不是算法**:MDE 把谱嵌入、PCA、MDS、Isomap、UMAP、力导向布局统一为"选一个 distortion function + 一组约束"。它还支持约束(如 `Standardized()` 强制嵌入中心化、特征不相关),因而能产出**可复现的布局**——对需要把选择固化为证据的场景有用。规模上 CPU 可到几十万 item / 几百万边,GPU 可到百万级 / 千万级边;scvi-tools 与 rapids-singlecell 都以它作为 GPU 加速的 UMAP 替代。

### 12.5 投影是不是必需的

**很多时候不是。** 空间数据本身已经有 2D 坐标——Visium 的 spot、Xenium 的细胞、mIF 的细胞都在切片坐标系里。"圈内 vs 圈外差异表达"这类问题,**在切片视图上就能做完,一个降维都不用跑**(§16 Phase 5 的判据据此简化)。

投影视图只在你想按**特征相似性**而不是**位置**分组时才需要。

而"特征相似性"本身也不只有降维一条路:在两个原始特征轴上画门(流式细胞术用了四十年的做法)同样是一个投影,而且它的轴是原始特征、完全确定性、人能说出"我圈的是 CD8 高、Ki-67 低那一群"——**那句话本身就是 provenance**。

**平台不排序这些方法,平台只保证每一个都说得清自己是什么。**

### 12.6 投影上的选择:记录义务

scvi-tools 在其 `mde` API 上加了一句很克制的注解:**高维空间可视化在单细胞组学里的适用性仍是开放的研究问题**,并引用了对降维图的批评文献。

这对本项目比对别人更要紧,因为**投影视图在本设计里是一个选择面**:人在上面套索,而那个选择会变成 `annotations`、变成 `measurements`,进入证据库。**降维图的不确定性会被固化成人的判断。**

按 §8.1,应对方式不是禁用某类方法,而是**提高记录义务**。`AGENTS.md` 的不变量加两条(见 §15.1-C):

```
- 投影视图上的选择必须记录 projection_id、method、params 与随机种子
- 非确定性投影上的选择,重算时必须比对 index_digest 并报告差异
```

第二条比"不得仅凭投影距离下结论"强,因为它**不替使用者做判断,却让判断有据可依**:它不禁止你在 UMAP 上圈东西,它保证你重算时会知道圈到的不是同一批。这正是 §6.5 的那句话——**不保证结果不变,保证结果变了不沉默。**

而且它是可查的:`layers.params_json.deterministic` 加 `selection_resolutions.index_digest`,不需要任何新字段。

### 12.7 性能:提前定一条线

投影视图上 Xenium 一片几十万点,plotly 的 SVG 路径会直接死。定阈值,超过就换渲染路径:

- 点数 ≤ ~5 万:`Scattergl`(WebGL),套索选择照常
- 点数 > 阈值:服务端栅格化成图,选择走"框选 → 后端算索引"

第二条路你已有基础设施——`tileserver.py` 就是干这个的。这也正是 §12.4 里 `pymde` 那类 GPU 后端真正会被触发的时刻。**不要为大点集引入 datashader 或 deck.gl**,那是又一个 82 包的故事。

### 12.8 绘图栈规则(与依赖规则同构)

- **一个绘图栈。** 已选 plotly。不要为一张小提琴图引入 altair、为一张网络图引入 bokeh——那是 UI 层的依赖膨胀。
- **自定义交互走 anywidget。** OSD viewer 已是这条路。真正需要新交互范式的东西走这里,不引入新框架。
- **外部工具靠数据互通,不靠 import。** 按 SpatialData 约定写 zarr,napari / squidpy / vitessce 可直接读你的输出——**别人 import 你的数据,你的 core 一个包都不多。**

---

## 13. 三类问题,三种机制

"新数据进来了"和"我想看看这个病人的信息"是两类完全不同的问题,混在一起就会觉得"什么都要新建 cell,好像不太优雅"。

**不优雅的不是"建 cell",是"查一条已知答案的信息也要建 cell"。**

| | 边界 | 频率 | 谁来做 | 写不写库 |
| --- | --- | --- | --- | --- |
| **查询**<br>"这片子是谁的、什么分期、有哪些 layer" | 有界,答案形状已知 | 每次都用 | UI 固定位 | 只读 |
| **摄入**<br>"新来一批数据,注册进去" | 有界,流程可重复 | 偶尔 | 函数 + 一个按钮 | **写** |
| **探索**<br>"圈内 vs 圈外差异表达,再按分期分层" | 无界,每次不同 | 每次都不同 | agent 建草稿 cell | 只读 |

> **中间那一列是关键:agent 可以即兴读,不该即兴写。**

摄入会改数据库,而即兴生成的代码 + 写库是最危险的组合——§6.2 的三条不变量全都在写入路径上生效,绕过一次就留下一条永久的坏记录。**摄入必须是稳定函数,skill 只调用它,不重新实现它。**

### 13.1 查询:人和 agent 必须读同一个函数

agent 那侧其实做完了:`make_slide_info_tool`、`make_query_annotations_tool`、`make_live_selection_tool` 都在 `agent_bridge.py` 里。缺的是 UI 那侧——**上下文条应该渲染同一个函数的输出**,而不是自己再拼一遍。

这不是琐碎问题,是 HITL 工具里最要命的失败模式:**人和 agent 看到不同的真相。** 你在 selection 上已经防住了(§12.3),同一个模式必须复制到 slide info 上,并加一条契约测试锁死。

**配准状态尤其要放进上下文条**:有一层显示 `unregistered`,人一眼就看到;藏在数据库里就等着变成下一个 29.4 倍。

---

## 14. Skill 体系

### 14.1 两种 skill,不要混

参考库(K-Dense `scientific-agent-skills`:161 个 skill;JimLiu `science-skills`:29 个)的结构是 `skills/<name>/SKILL.md` + `references/`,根目录一个 `plugin.json`,遵循开放的 Agent Skills 标准。你现在 `skills/he-scope/SKILL.md` 的 frontmatter 格式**已经是对的**。

但你要写的 skill 和那 161 个不是同一种东西:

| | 可移植 skill | harness skill |
| --- | --- | --- |
| 描述的是 | 一个**库**怎么用 | 你的**活着的 app** 怎么用 |
| 例子 | scanpy、biopython、pydeseq2 | he-scope、he-scope-visium |
| 前置条件 | 装了这个包 | **kernel 里有 `current_slide`** |
| 谁能写 | 任何人 | 只有你 |
| 过期速度 | 慢(外部库变才变) | **快(改一个函数名当场过期)** |
| 该有多少 | 161 个,别人写好了 | **五个左右** |

**你不需要写一百个 skill,你需要写五个 harness skill,让 agent 从社区装可移植的那些。**

### 14.2 结合方式是分层调用,不是塞进同一个目录

```
he-scope skill(harness,你写)
  1. get_current_selection()      → 多边形
  2. resolve(layer_id)            → 实体索引
  3. 导出 parquet 到适配器环境
  4. ── 调用 scanpy / pydeseq2 skill(社区写的)──
  5. record_measurement(...)      → 写回证据库
```

**harness 拥有工作流,可移植 skill 拥有库知识。** 第 4 步你一个字都不用写。

### 14.3 分发:标准已经解决了,不用自己发明

安装走 `npx skills add <owner>/<repo>` 或 `gh skill install <owner>/<repo>`,**安装器负责把 skill 放进各 host 该放的目录**(Claude Code、Cursor、Codex、Gemini CLI 各不相同)。

所以"code agent 有自己的 skill 存储位置"不是难点——**仓库是源,agent 的目录是安装目标。** 你不需要知道它放哪。

要做的只有两件:根目录加 `plugin.json`,保持 `skills/` 目录约定。

**不要把外部 skill vendor 进你的仓库。** 那是依赖膨胀换了个形式。README 里写一行"建议同时安装 K-Dense 的 scanpy / pydeseq2 skill"就够了。

### 14.4 `skills/` 的组织

```
skills/
  he-scope/              工作流:怎么连接、一次分析怎么跑
  he-scope-visium/       每个模态一个 harness skill
  he-scope-embedding/
  _proposed/             自动生成的草案,不进 plugin.json,安装器看不见
plugin.json              让 npx skills add 能装
```

仓库其余部分的位置见 §15.0。**没有 `harness/` 目录**(理由见 §17.1)。

### 14.5 自动生成 skill(Hermes 式):只提议,不提交

从会话自动提取 skill 很有吸引力,失败模式也很确定:**会得到一堆近似重复、没人验证过的 skill,三个月后不知道哪个能信。** 而 skill 库的价值完全建立在"里面每一条都可信"上——出现两条互相矛盾的,整个库的可信度归零。

让它能工作的纪律,就是晋升门槛:

```
草稿 cell → 用过三次 → hescope/ 里的函数(带测试)→ 才成为 skill
```

自动生成的产物一律进 `_proposed/`,必须通过 §15.3 锁一的 API 契约测试才能晋升。**这样你不用逐字审草案——审不过测试的直接不用看。**

这条路径顺带回答了"skill 怎么自动收集整理":**不需要自动收集,晋升本身就是筛选。** 也解释了为什么现在"概念有、DDL 有草案、代码零"——**因为还没有草稿区,没有东西可以晋升。先有草稿区,skill 才有来源。**

### 14.6 harness skill 里最有价值的一节:"失败时"

那 161 个 skill 教的是"怎么用一个库"。你的 harness skill 该教的是**"在这个项目里,什么时候不能相信一个数"**——这些是你十轮 bug review 的产物,任何公开 skill 库里绝对不会有。

**不变量的单一来源是 `AGENTS.md`(§15.1-C),永远适用;skill 的「失败时」只写它在这个具体工作流下怎么触发。** 例如 Visium skill:

```markdown
## 失败时
- layer.registration != 'affine' → 停下,先跑地标配准,不要用恒等变换硬对
- 圈内 spot 数 < 20 → 差异表达不可信,报告 n 而不是 p 值
- 圈内圈外 mpp_effective 不同 → 面积归一化前先声明,见 AGENTS.md 不变量
```

**这些规则现在只活在 `bugs/` 目录里,agent 读不到。** 把它们搬进 `AGENTS.md` + 各 skill 的这一节,是整套体系里投入产出比最高的一段文字。

---

## 15. 文档即 harness

### 15.0 结论:零新目录

Harness 不需要一个 `harness/` 目录。**每一样东西该放哪,现有约定已经定死了**,只是分散在几个地方看不出来:

| 东西 | 放哪 | 理由 |
| --- | --- | --- |
| 分区规则、台面清单、不变量 | `AGENTS.md` | 项目宪法,agent 进仓库就读 |
| 怎么连接、一次分析怎么跑 | `skills/he-scope/SKILL.md` | 工作流,按 description 触发 |
| `resolve()`、`available_layers` | `hescope/layers.py` | 真逻辑,和 layers 表的代码在一起 |
| `record_measurement()` | `hescope/db.py` 加 `MeasurementRepo` | 已有 `SlideRepo`/`ROIRepo`/`AgentRunRepo`/`InteractionRepo`,照抄 |
| `current_slide` / `current_selection` | `app.py` 骨架区模块作用域 | 它们是**状态**不是函数,只能活在 kernel 里 |
| `parse_app_cells` / `skeleton_hashes` / `parse_agents_md_api` | `tests/` | **只有测试用**,不进 `hescope/`,不是产品代码 |
| 四个锁 | `tests/test_harness.py` | 约两百行,进已有的 `tests/` |
| 骨架哈希 | `skeleton.lock` | 仓库根目录一个文件 |

最后三行是关键:**锁的解析工具是测试工具。** 把 AST 遍历放进 `hescope/` 会让不属于产品的东西混进产品命名空间——那才是真正该避免的结构。

**净增量:两个文件(`tests/test_harness.py`、`skeleton.lock`)+ `app.py` 一行注释。其余都是往现有位置加东西。**

### 15.1 `AGENTS.md` 要加的三节

现有的 273 行已覆盖启动、hard rules、entry points、payload schemas、写回结果。缺三节,合计约 40 行。

**A. 分区**

```markdown
## 分区
app.py 以 `# ▼▼▼ SCRATCH ▼▼▼` 为界:
- 线以上 = 骨架。不得修改(含 ctx.edit_cell)。
  改动会导致 test_skeleton_unchanged 失败。
- 线以下 = 草稿区。ctx.create_cell 追加于此,随意。
```

注意:现有规则是"NEVER edit `app.py` on disk,一律用 `ctx.create_cell`",而 `create_cell` 本来就是追加——**只要标记线在文件底部,agent 的 cell 天然落在草稿区。** 分区这件事已经做了一大半,只差把规则从"不许碰磁盘"细化成"线以上不许碰"。

**B. 台面**

```markdown
## 台面(骨架区保证提供)
current_slide / current_selection / available_layers / resolve(layer_id)
直接用,不要自己从数据库重新推导。
```

**C. 不变量** —— 三节里价值最高的一节

```markdown
## 不变量(什么时候不能相信一个数)
- registration == 'unregistered' → 停下来问用户,不得假设恒等变换
- 两行 mpp_effective 差超过阈值 → 不得平均,报告为不可比
- 队列未记录 tissue_source_site → 不得下"形态 ↔ 分子"结论
- 密度类测量 → 必须声明分母来源
- 投影视图上的选择 → 必须记录 projection_id、method、params 与随机种子
- 非确定性投影上的选择 → 重算时比对 index_digest 并报告差异
```

**这六条是全项目不变量的单一来源。** 各 skill 的「失败时」只写它们在具体工作流下怎么触发(§14.6),不重复定义。

这四条现在只活在 `bugs/` 目录里,**agent 读不到**。搬进 `AGENTS.md` 是十行字换掉一整类错误。

### 15.2 先解决一个已存在的重复

`AGENTS.md`(273 行)与 `skills/he-scope/SKILL.md`(115 行)**都在讲同一件事**:必须 `marimo edit --no-token`、marimo 0.23 懒加载所以 globals 不存在、要先 Run 一次。

**这是最容易漂的地方**——改一处忘另一处,而两处 agent 都会读。既然走"文档即 harness"路线,文档自己先不能有两份真相。

| | 角色 | 何时被读 | 内容 |
| --- | --- | --- | --- |
| **AGENTS.md** | 项目宪法 | 进仓库自动读 | 分区、台面、不变量、出口。**永远适用,不讲怎么做** |
| **SKILL.md** | 工作流 | 按 description 匹配触发 | 怎么连接、懒加载怎么处理、一次分析怎么跑 |

按此划分,**连接方式那一整段只留在 SKILL.md**,`AGENTS.md` 换成一行指针。加了三节之后 `AGENTS.md` 总长可能反而更短——**而且它变成一份纯约束文件,agent 一进来就能读完。**

### 15.3 四个锁

Harness 的每一条约束都必须有一个会失败的测试,否则它只是散文。四个断言,一个文件。

#### 锁一:API 契约锁 —— 防文档静默过期

可移植 skill 描述外部库,库不变就不过期。**harness skill 描述你自己的 API,你改一个函数名它当场过期,而且是静默过期**:agent 照旧文档调,失败,然后开始猜,猜出来的东西可能还能跑,只是算的是别的东西。

对 HITL 工具,这是最坏的一类错误,和 failure rendered as success 同族。

```python
def test_documented_api_exists():
    claimed = parse_documented_api("AGENTS.md", "skills/he-scope/SKILL.md")
    live    = module_scope_names("app.py")
    assert claimed <= live, f"文档声明了不存在的 API: {claimed - live}"
```

**这一步把 `AGENTS.md` 从"描述"变成"契约"。** 在一个 660 测试的项目里,面向 agent 的文档是目前唯一还没被测试保护的接口面——而在"文档即 harness"的路线下,它恰好是最重要的那个接口面。

#### 锁二:骨架锁 —— 防 agent 越界

在 `AGENTS.md` 里写"只在线以下追加"是必要的,但 agent 会犯错,而你不会立刻发现。

```python
def test_skeleton_unchanged():
    assert current_skeleton_hashes() == load_lock("skeleton.lock")
```

agent 改了骨架 → 测试立刻红 → 它自己就知道越界了。**比权限控制简单,比信任可靠。** 你自己要改骨架时,更新 lock 是一个显式动作。

#### 锁三:粒度锁 —— 防 cell 再次漂移

你已经漂过一次:41 → 50 cell,最长 127 → 294 行。靠自律守不住,靠断言可以。用 AST 解析 `app.py` 即可,不需要 CI:

```python
def test_cell_granularity():
    cells = parse_app_cells("app.py", region="skeleton")   # 只管骨架区
    oversized = [(c.name, c.lines) for c in cells if c.lines > 80]
    assert not oversized, f"cells exceed 80 lines: {oversized}"
    assert len(cells) <= 60
```

**起步时会有 18 个失败**,所以先记一个基线允许清单并逐步缩短,而不是一次性重构。

这和你在依赖上做的事是同一个思路:**把你最在意的约束变成可测量、会报警的东西**,而不是文档里的一句话。

#### 锁四:依赖锁

```python
def test_core_env_has_no_downgrades():
    # 解析 core 依赖,断言 pip 解析结果不含任何 uninstall / downgrade
```

这条能自动守住 §3 那个最要紧的约束,而且是 VALIS 那个 numpy 回退唯一能被自动发现的方式。

### 15.4 关于 MCP:不预留抽象层

skill 与 MCP 不是同一层——skill 是给 agent 读的**过程说明**(无运行时),MCP 是**跑着的服务器**(有协议、有生命周期)。两者互补:MCP 提供工具,skill 告诉 agent 何时用、以及何时不该相信结果。

MCP 确实能解决 marimo-pair 现在那三个脆弱前提(必须 `marimo edit --no-token`、必须保持标签页打开、必须先 Run 一次让 globals 存在),而且对 host 无关。但现在做,是拿一个**已打通且验证过**的东西去换一个更大的东西。

**不需要为它预留抽象层。** 让 MCP 以后能接进来的,不是接口设计,是这一条:

> **UI 显示的、marimo-pair 拿到的,是同一个函数的输出(§13.1)。**

这条成立,MCP 就是那个函数的第三个客户端,包一层协议即可,而且锁一自动覆盖它。这条不成立,预留多少接口都没用,因为届时要对齐的是三份互相漂移的实现。

**所以"为 MCP 留接口"的具体动作只有一个,而且它本就在 Phase H1 里:上下文条渲染 `slide_info` 的返回值,不自己拼数据。** 零额外成本,零新抽象。

---

## 16. 落地顺序

每阶段有一个"做完了"的客观判据。**Phase 0 / 1 / H1 在任何新数据到来之前就能回本。**

### Phase 0 — 地基

1. `schema_migrations` + 每连接 `PRAGMA foreign_keys=ON`
2. `content_key` + `slide_files`:身份从路径迁到内容(**有损迁移,需对现有 31 张片子 dry-run**)
3. `measurements` 表 + `mpp_effective`
4. 顺手:`overlay.py` 的 `Path`;全仓扫 `except Exception: pass`
5. 顺手:删掉 README 的假 badge 和 Postgres 承诺

**判据**:`slides.path` 不再 UNIQUE;`demo_he.png` 在两个位置是一片子两文件,两个 ROI 从任一路径都可达。

### Phase H1 — Harness 骨架(可与 Phase 0 并行)

1. 按 §9.2 判据搬那 2,404 行(**纯搬运,无设计决策,可立即开工**)
2. `app.py` 加一行标记注释;定义 §10 的四个台面变量
3. `AGENTS.md` 加三节(分区 / 台面 / 不变量),并把连接说明去重到 SKILL.md(§15.1–15.2)
4. `tests/test_harness.py` + `skeleton.lock`:四个锁(§15.3),粒度锁先记基线
5. 上下文条渲染 `slide_info` 同一个函数(§13.1)——**这一步同时就是 MCP 的"预留接口"(§15.4)**

**判据**:`app.py` ≈ 1,200 行;骨架锁存在且通过;agent 改骨架会红;`AGENTS.md` 里声明的每个 entry point 都能被锁一验到。

### Phase 1 — 通用点集层

加载 `(x, y, payload)` → OSD 渲染 → ROI 选择返回子集。**立刻被现有 `nuclei.py` 消费**——抽象先被已有功能验证,再拿去接新数据。同时建 `layers` 与 `selection_resolutions`,并立起 §12 的选择规范形式。

**判据**:细胞核计数走点集路径结果与旧路径一致;`registration` 对未配准层真的报 `unregistered`。

### Phase 2 — 上下文与身份

GDC `expand=diagnoses` 拉临床字段;从 barcode 解析 TSS 写入 `samples`;把 `tcga_files.slide_id` 通过 md5/content_key 真正写上。

**判据**:`WHERE sample_type='Primary Tumor' AND tissue_source_site='BH'` 能返回 ROI。

### Phase 3 — 参照分布

分位数视图;测量显示为 "p73 / n=412";覆盖度视图;`created_by` 接受率。

**判据**:任一指标的分布图能被一眼看出双峰或不双峰。

### Phase H2 — 草稿区与 skill

草稿区 session 打标与回收 + `hescope/db.py` 里的 `MeasurementRepo`(出口)+ `skills/_proposed/` + `plugin.json`。**第一版就这几样,约两百行,其中一半是已有东西的重新导出。**

**判据**:一次探索的结果能落进 `measurements` 并带 provenance;`npx skills add` 能装上你的 harness skill。

### Phase 4 — 组织区域分割(分母)

进程外适配器,产出 `kind='tissue_regions'` 的 layer。所有密度类测量增加"分母来源"字段。

**判据**:同一 ROI 的密度能同时给出三个分母下的三个数,且各自标注分母。

### Phase 5 — 第一个真正的新模态:Visium

reader(零新依赖)→ layer → agent 在适配器环境跑分析 → 交回排序基因表。

**判据**:在片子上画一个圈,对圈内 vs 圈外做一次差异表达,结果可复现且带 provenance(方法、参数、随机种子齐全)。

**注意这一步不需要投影视图**——空间数据本身就有坐标,圈内外分组在切片视图上即可完成(§12.5)。投影视图是后续可选路径,不是 Visium 接入的前置条件。

### Phase 6+ — 视情况

mIF/IMC(同片,无配准)→ 放射(独立 frame)→ IHC(需 VALIS 适配器,最后做)。

---

## 17. 明确不做的事

| 不做 | 理由 |
| --- | --- |
| 远端 Postgres 后端 | 解决多机/多人问题,已决定记忆本地化(§6.1) |
| 报告引擎 / 模板系统 | marimo 本身能导出 HTML;报告 = notebook + 证据表 |
| GPU 集群调度器 | 只要"内容哈希寻址 + 结果落 zarr + job 可恢复"成立,跑在哪无所谓 |
| 基础模型推理加速 | 真问题是 embedding registry 的缓存键;缓存做对了一张片子只慢一次 |
| 依赖 `spatialdata` 包 | 82 个新包;只借概念(§6.7) |
| vendor 外部 skill 库 | 依赖膨胀换了个形式(§14.3) |
| 第二个绘图栈 | UI 层的依赖膨胀(§12.5) |
| 化学信息学 / 质谱 | 依赖便宜但交互范式不同,会撑坏选择面抽象(§7.7) |
| 连续切片配准(现阶段) | VALIS 会降级 numpy;等 `registration` 机制立住(§7.4) |
| 独立的 `scratch.py` | 会切断 kernel state,违背核心原则(§9.3) |
| **独立的 `harness/` 目录** | 每样东西都有现成位置;锁的解析工具属于 `tests/`(§15.0) |
| **harness 框架化 / 包化 / 发 PyPI** | 见下方说明 |
| **为 MCP 预留抽象层** | 真正的"预留"是不叉开真相,不是接口设计(§15.4) |
| 竞品对标 | 判据是"三个月后还想不想打开",不是别人的 benchmark |

### 17.1 为什么不做 harness 框架/包化

1. **成本即时,收益要等第二个使用者。** 版本号、向后兼容、发布流程、install 说明立刻发生;收益要等到有第二个项目用它。现在有零个。
2. **能打包的正好是便宜的那一半。** 机制(锁、分区、草稿区)几百行且无设计难度;契约(台面、出口、不变量、"失败时")是全部的设计难度,而且按定义不可打包——**它就是领域本身**。
3. **n=1 提取不出正确的接口。** 从一个实例提取的"可复用接口"是猜的;接口要从第二、第三个实例之间提取才准。
4. **包边界会把接缝焊死,而接缝还在动。** "机制 vs 契约"这条线是个假设。在目录里画错了直接挪;在包里,挪接缝叫 breaking change。
5. **消耗的是注意力,不只是时间。** 一个包会持续产生决策(minor 还是 major?会破坏谁?写不写 changelog?),这些决策在 n=1 时回报为零,但照样占位置。
6. **可逆性不对称。** 目录 → 包,以后随时能做,成本很小;包 → 目录,做不了,因为一旦有人依赖就回不去。**保持现状是保留选项,包化是关闭选项。**

> **复制比依赖便宜。** 真有第二个项目要用,`cp` 过去,两边各自演化。抄来的副本发散完全没关系;**一个只有一个用户的包,是永久的税。**

对本项目还有一个特定风险:**"harness 框架"是第三次转向的完美伪装**——它看起来是在为 HE-Scope 服务,实际是在给一个不存在的用户群做基础设施。

**该翻转这个决定的信号**(在此之前不翻转):

| 信号 | 说明 |
| --- | --- |
| 同时跑两个 marimo 项目,**同一个锁的 bug 修了两遍** | 真实重复,不是想象的 |
| 有别人真的开口要 | 不是"可能有人要" |
| 契约那半也稳定到能定接口了 | 说明 n 已经 > 1 |

---

## 18. 风险与失败模式

1. **最可能的死法:一个人维护四个学科方向。** §3.4 的三层环境模型之所以值得,不是因为优雅,而是它让"加一个方向"的成本从改核心变成写一个 skill。
2. **第二可能的死法:在错误的地基上加东西。** Phase 0 不做完就做 Phase 5,等于把"身份是路径"复制到每个新模态。
3. **最隐蔽的死法:沉默的错误。** `overlay.py` 那个被 `except` 吞掉的 `NameError` 是缩影,29.4 倍是同一类。`registration DEFAULT 'unregistered'`、`selection_resolutions.index_digest`、三个锁,都是为这一类设计的护栏。
4. **harness 特有的死法:文档与代码脱节。** 走"文档即 harness"路线,这条风险是内生的:文档过期不会报错,agent 会开始猜,而猜出来的结果可能还能跑。**§15.3 锁一是唯一的解**,也是这条路线的准入代价。
5. **skill 库贬值。** 两条互相矛盾的 skill 就能让整个库失去可信度。晋升门槛(§14.5)不是流程洁癖,是库的存活条件。
6. **假发现风险。** 不记录 TSS,任何"形态 ↔ 分子"相关都可能只是在识别医院——而且基础模型 embedding 也带这个信号(§5.3)。
7. **依赖漂移。** numba 的 `numpy<2.6` 只剩一个小版本余量(§3.3)。§15.3 锁四的依赖锁能自动守住。

---

## 附录 A — 依赖实测方法

基准环境:Python 3.12.3,`venv`,仅安装 core 依赖:

```
marimo>=0.23  pillow  numpy  scipy  scikit-image  plotly  requests
tifffile  zarr>=3  imagecodecs  sqlalchemy>=2.0  anywidget>=0.9
```

解析后 64 个包。基准版本:`numpy 2.5.2`、`scipy 1.18.0`、`zarr 3.3.0`、`tifffile 2026.7.31`。

```bash
pip install --dry-run --report r.json <pkg>
# r.json 的 install 列表 = 需要安装或改版的包
# 与基准 pip freeze 求名称交集 = 会被改动的已有包
```

关键结果:

- `spatialdata` = 82 新包,0 改动
- `valis-wsi` = 81 新包,**改动 numpy / scipy / tifffile**(numpy 2.5.2 → 1.26.4)
- `scanpy` = 35 新包,0 改动,但引入 `numba 0.67.0 (numpy<2.6)`
- `wsidicom` = 8 新包,与 pyproject 注释中记录的 7 基本一致(说明这套测法与你原来的一致)
- 投影后端:`openTSNE` = 4 新包(无 numba);`umap-learn` = 8 新包(**引入 numba + llvmlite**);`pymde` = 37 新包(其中 25 个是 CUDA wheel,无 numba)。三者均不改动 core。`pymde` 在已装 `[ml]` extra 时的净增量为**推算值,非实测**

## 附录 B — `app.py` 结构实测方法

用 `ast` / 行扫描定位 `@app.cell` 边界,统计每 cell 行数与 `hescope` 引用次数:

- 50 个 cell,中位数 64 行,最长 294 行,18 个超过 80 行
- 超 80 行的 cell 合计 2,404 行,占 3,673 行的 **65%**
- 其中最大的几个 cell 的 `hescope` 引用次数为 1 / 0 / 0 / 0 / 0 —— **即逻辑而非 wiring**

同一段脚本可直接作为 §15.3 锁三(粒度锁)的实现基础。

## 附录 C — 参考

- SpatialData(五原语 + 公共坐标系 + OME-NGFF/Zarr):Marconato et al., *Nature Methods*, 2024 — https://www.nature.com/articles/s41592-024-02212-x
- Space Ranger 空间输出字段定义:https://www.10xgenomics.com/support/software/space-ranger/latest/analysis/outputs/spatial-outputs
- GDC API `cases` / `diagnoses` 字段:https://docs.gdc.cancer.gov/API/Users_Guide/Search_and_Retrieval/
- 站点特异性数字组织学签名与模型偏倚:Howard et al., *Nature Communications*, 2021 — https://www.nature.com/articles/s41467-021-24698-1
- 基础模型 embedding 中的批次效应:https://arxiv.org/html/2411.05489
- ComBat 对病理深度特征的批次校正(TSS 预测 AUROC > 0.95):https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11470259/
- VALIS 全自动 WSI 配准:Gatenbee et al., *Nature Communications*, 2023 — https://www.nature.com/articles/s41467-023-40218-9
- HoVer-Net:https://arxiv.org/pdf/1812.06499 ;CellViT:https://arxiv.org/pdf/2306.15350
- 病理基础模型基准:*Nature Biomedical Engineering*, 2025 — https://www.nature.com/articles/s41551-025-01516-3
- TCIA 与 TCGA 的 Patient ID 一致性:https://www.cancerimagingarchive.net/collection/tcga-brca/
- 多重成像技术与 OME-TIFF 工作流综述:*British Journal of Cancer*, 2024 — https://www.nature.com/articles/s41416-024-02882-6
- 同一玻片上 H&E + IF + IMC:*Cytometry Part A*, 2023 — https://onlinelibrary.wiley.com/doi/10.1002/cyto.a.24789
- PyMDE / Minimum-Distortion Embedding:Agrawal, Ali & Boyd, arXiv:2103.02559 — https://arxiv.org/abs/2103.02559 ;代码 https://github.com/cvxgrp/pymde
- scvi-tools 对 `mde` 的注解(降维可视化适用性的开放问题):https://docs.scvi-tools.org/en/1.0.0/api/reference/scvi.model.utils.mde.html
- Agent Skills 标准:https://agentskills.io/ ;Agent Plugins:https://agent-plugins.org/
- K-Dense scientific-agent-skills(161 skills):https://github.com/K-Dense-AI/scientific-agent-skills
- JimLiu science-skills(29 skills):https://github.com/JimLiu/science-skills
