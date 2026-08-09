# 互操作与能力路线图

[English](ROADMAP-INTEROP.md) · **简体中文**

写于 2026-08-09,分支 `feature/interop-and-hardening`。

本计划的出发点:吸收既有生态最省力的方式不是把别人的算法搬进来,而是让别人的
**产物**能进能出。这个判断在第一层经受住了依赖解析器的检验,在第二层没有——
所以第二层在下面被重构了,而不是照单全收。

这里的每一条都先对着代码和解析器核过才写下来;数字是实测的,不是估的。

---

## 现状盘点

| 能力 | 位置 | 状态 |
| --- | --- | --- |
| GeoJSON 导出 | `hescope/geojson.py`,82 行 | 有,但**有损**(见下) |
| GeoJSON 导入 | — | 缺 |
| ASAP / QuPath XML | — | 缺 |
| WSI 读取 | `hescope/slides.py` | OpenSlide + tifffile/zarr + Pillow,无 DICOM |
| 染色归一化 | `hescope/stain.py`,173 行 | Macenko、Reinhard |
| 细胞核 | `hescope/nuclei.py`,122 行 | H&E 解卷积 → Otsu → 分水岭 |
| QC | `hescope/qc.py`,77 行 | 组织占比、清晰度、亮度 |
| 手工特征 | `hescope/features.py`,200 行 | 56 维 |
| FM 编码器 | `hescope/embeddings.py` | GPFM / H-optimus-0 / UNI2-h / ResNet18 |

### GeoJSON 导出是有损的,而修复所需的数据其实已经存了

`rois_to_geojson` 只用 `bbox` 构造几何,套索会被压成外接矩形:

```
数据库里存的点 : [[10,10],[90,20],[50,80]]        # 一个三角形
导出的多边形环 : [[10,10],[90,10],[90,80],[10,80],[10,10]]   # 一个矩形
形状是否保留   : False
```

**这不是数据缺失。** `rois` 表本来就同时存了 `points_json` 和 `bbox_json`,
`ROIRepo` 返回的每个行字典里也都带着 `points_json`,只是导出没用它。修起来
只有几行,而且它是任何"往返"的前提——把 QuPath 的多边形导进一个出去就被压平
的系统,没有意义。

---

## 第一层 —— 互操作(最便宜、价值最高,先做)

已确认便宜。`wsidicom` 解析结果:**新增 7 个包,0 卸载,0 降级**。

```
+ cachetools, dicomweb-client, marshmallow, pydicom, retrying,
  universal-pathlib, wsidicom
```

| # | 事项 | 工作量 | 理由 |
| --- | --- | --- | --- |
| 1.1 | **导出保真形状** | 0.5 天 | 往返的前提;数据已经存好了 |
| 1.2 | **GeoJSON 导入** | 1–2 天 | 不写一行算法,所有 QuPath 用户的存量标注立刻可用 |
| 1.3 | **ASAP / QuPath XML 导入** | 1 天 | 真实标注集到达的另一种格式 |
| 1.4 | **DICOM WSI 读取**(`wsidicom`) | 1–2 天 | 一个 `SlideSource` 实现;临床扫描仪越来越多输出 DICOM |
| 1.5 | 往返测试语料 | 0.5 天 | 导入 → 导出 → 导入,几何与类别必须是恒等变换 |

**导入端的设计约束。** 导入必须走系统里同一套 `ROI` 词汇(`rect` / `polygon`
/ `circle`),落进 `rois` 表并填好 `points_json`,使导入的标注与手画的**不可
区分**——可训练、可导出、`query_annotations()` 能查到。QuPath 写的是
`classification: {"name": ...}`,映射到我们的 `label`。两边坐标都是 level-0
像素,这正是它便宜的原因。

**必须测的坑:** 真实世界的 QuPath GeoJSON 里有 `MultiPolygon`、带洞的多边形
(内环)、以及 `Point`/`LineString` 要素。对每一种都要**明确决定**行为——压平、
跳过并计数、还是拒绝整个文件——并写测试。把一个甜甜圈只取外环静默导进来,
比直接拒绝更糟。

---

## 第二层 —— TIAToolbox:**不要作为直接依赖引入**

原命题是"一个 adapter 换来四五个能力,而且因为 TIAToolbox 接受标准 PyTorch
模块,adapter 可以写得很薄"。前半句在 API 层面是对的。后半句对**依赖**不成立,
而依赖才是决定因素。

用解析器针对**当前这个环境**实测:

```
tiatoolbox:  解析 198 个包,下载 107,安装 152,卸载 22
```

会被卸载或降级的 22 个里包括:

```
numpy 2.4.6, torch 2.13.0, torchvision 0.28.0, timm 1.0.28,
scikit-learn 1.9.0, scipy 1.17.1, pillow 12.3.0, tifffile 2026.3.3,
imagecodecs, openslide-python 1.4.6, openslide-bin 4.0.1.2,
huggingface-hub 1.27.0, ipywidgets 8.1.8, joblib, requests, pyyaml, ...
```

其中三项是承重的:

- **`ipywidgets`** —— `anywidget` 建立在它之上,而 `anywidget` 就是
  OpenSeadragon viewer,也就是**主视图**。
- **`openslide-python` / `openslide-bin`** —— 我们读真实 WSI 的方式。
- **`torch` / `torchvision` / `timm`** —— FM 编码器工厂围绕它们构建并钉死版本。

所以把 TIAToolbox 引进主环境,加的不是一个薄 adapter,而是**重写 viewer 和
编码器栈脚下的地基**;而且它每发一个新版,这一切都要重新博弈一次。能力层面的
论证是成立的,问题出在打包。

### 替代方案,按推荐顺序

**2a. 直接把缺的两种染色归一化补上(推荐)。** TIAToolbox 提供的四种里,
Macenko 和 Reinhard 我们已经有了。Ruifrok 是固定矩阵解卷积——
`skimage.color.rgb2hed` 本身就是它,等于在已经 import 的代码上包一层。
Vahadane 需要稀疏 NMF,即 `sklearn.decomposition.DictionaryLearning`,
而 scikit-learn 已经是依赖。成本约一天,**零新增包、零降级**,拿到染色归一化
的绝大部分价值而不付任何打包代价。

**2b. 真的被细胞核质量卡住时,再把 TIAToolbox 放到进程外跑 HoVer-Net。**
单独一个 uv 管理的环境 + 一层子进程边界,交换一个 patch 和一份实例 JSON
(bbox、质心、轮廓、类别概率)。因为两个环境永不相遇,依赖冲突自然消失。
成本是真实的(一次安装、一层进程边界、一份 schema),但它是有界且可逆的,
也是同时拥有 HoVer-Net 和现有 torch 钉版的唯一办法。

**2c. 不要 vendor TIAToolbox 源码。** license 和维护成本两头都不划算。

**已明确跳过:** PathML(模型选择有限、扩展路径不清晰)与 HistomicsTK
(重心已转向 Digital Slide Archive 生态)。

---

## 第三层 —— 点状能力,按需再建

被真正卡住时再做,不要提前建。

| 事项 | 触发条件 |
| --- | --- |
| InstanSeg / CellViT 实例分割 | 分水岭在真实切片上肉眼可见地失效 |
| HistoQC / GrandQC | 有人被我们 77 行 `qc.py` 放行的切片误导 |
| 空间形态计量(SPARK 式) | 出现"距肿瘤 N µm 内的淋巴细胞"这类具体问题 |

`qc.py` 只有 77 行,对一个真实痛点来说太薄,而且是三者中最可能先被需要的——
笔迹、失焦区域、组织褶皱和气泡,它现在全都看不见。

---

## UI

实际用下来,当前界面的问题:

1. **工具条混了两个时代。** OSD 接管之后,方向键和 downsample 滑杆已经是
   遗迹——平移缩放现在是鼠标的事。它们应该收成一个紧凑的次级分组或快捷键,
   而不是占据工具条最宽的位置。
2. **缩放读数是生的。** 现在显示 `8.001340482573728`。应该显示放大倍率
   (`5.0×`)或取整的 downsample;这个数字没人用得上。
3. **选区状态在画布之外不可见。** 虚线轮廓现在会保留(今天刚修),但没有任何
   文字说明"已选中 1 个、尚未添加",点 Add ROI 也只有侧栏列表多一行——而用户
   未必在看那里。
4. **有价值的东西全折叠着。** Annotations、Agent console、Analysis、TCGA 全是
   手风琴。新用户看到的只有一个 viewer 和一条工具条,根本不知道这个应用还有
   分析栈。
5. **缺少切片级的方位感。** 导航图告诉你"在哪",但没有任何视图告诉你
   "整张片子上哪些地方被标注过"。

按价值排序的改法:

- **viewer 下方一条状态栏**:当前选区(类型 + µm 尺寸)、ROI 计数、最近一次
  agent 动作。一行,常驻,取代靠猜。
- **重排工具条**:工具(pan / box / lasso / measure)做成左侧分段控件,视图控制
  (fit、缩放读数、方向键)收到右侧折叠,动作(Add ROI、Send)钉在最右。
  控件更少、更大。
- **切片打开时把 Analysis 从手风琴里提出来**,哪怕只是状态栏旁边一个
  "分析选区"的入口。
- **导航图上叠加 ROI**,让标注过的区域在切片级可见。

---

## 统计能力

`roi_stats` 现在返回单个 ROI 的均值 RGB、H&E 解卷积均值、组织占比。这是
**单点、当下**的,没有办法比较 ROI、看分布、或者刻画一整张切片。

值得做的,由便宜到贵:

1. **跨 ROI 对比表。** 当前切片的每个 ROI 一行,带统计量和标签。这只是一次
   查询加一张表——不需要新数学,却把孤立的测量变成了证据。
2. **统计量导出 CSV/JSON。** 标注已经能导出,把计算出的统计量也接上,
   结果才能离开这个工具。
3. **切片级摘要。** 组织面积(mm²)、按标签的 ROI 计数、以及我们为热力图
   本来就算过的那张网格上的细胞核密度分布。
4. **按标签的聚合与离散度。** 每个统计量按标签的均值 ± 标准差——这才让
   "肿瘤与间质在 H 密度上有差异"从印象变成主张。
5. **模型概率热力图的不确定度。** 现在只显示概率,不显示置信度,而那恰恰是
   病理医生最该保持怀疑的地方。

第 1–3 项都是几天的量级,而且全部复用数据库里已有的数据。

---

## 执行顺序

1. Bug 第 02–06 轮落地,测试全绿。*(进行中)*
2. 第一层 1.1 —— 导出保真形状。小,且解锁后续。
3. 第一层 1.2 / 1.3 —— GeoJSON 与 ASAP XML 导入,配往返语料。
4. 第一层 1.4 —— DICOM `SlideSource`。
5. 统计 1–2 —— 对比表与导出。
6. UI —— 状态栏与工具条重排。
7. 第二层 2a —— 在树内补 Ruifrok 与 Vahadane。
8. 2b 与第三层,等真实使用反馈再评估,不靠推测。
