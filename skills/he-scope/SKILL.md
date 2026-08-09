---
name: he-scope
description: 连接正在运行的 HE-Scope marimo 病理 H&E 应用时使用。当用户要求读取当前圈选（selection/ROI）、回写标注（label/notes）、查询已有标注、获取切片元数据或触发分析/训练工作流时，通过 marimo-pair 附加到 `marimo edit app.py --no-token` 会话并调用本文件列出的 module-scope 工具。
---

# HE-Scope agent skill

HE-Scope 是一个 marimo notebook 应用（`app.py`）：H&E 病理切片查看器 + ROI
圈选 + code-agent 桥。本 skill 描述如何作为 agent 连接到一个**正在运行的**
HE-Scope 会话，读取用户圈选、回写标注并驱动分析闭环。

## 1. 连接步骤（pair）

1. 确认应用已以 **edit 模式** 启动：

   ```bash
   marimo edit app.py --no-token
   ```

   `marimo run` 是只读模式，marimo-pair 无法附加；必须 `marimo edit`。
2. 提醒用户**保持浏览器页面打开**，首次打开点一次 Run（marimo 0.23 懒加载，
   cells 未执行前内核全局变量不存在）。
3. 安装 marimo-pair skill 后，对你的 agent 说"连接我的 marimo notebook"；
   或用 `marimo._code_mode` 进入内核：

   ```python
   import marimo._code_mode as cm

   async with cm.get_context() as ctx:
       for cell in ctx.cells:  # 首次连接先确保所有 cell 已执行
           ctx.run_cell(cell.id)
   ```

4. 硬规则：**绝不在会话存活期间改磁盘上的 `app.py`**；一切交互走
   `ctx.create_cell / ctx.edit_cell / ctx.run_cell`，状态只通过下面的工具函数读。

## 2. 工具清单（内核全局，均返回字符串、永不 raise）

| 工具 | 签名 | 返回 |
| --- | --- | --- |
| `get_current_selection` | `() -> str` | 用户正在拖动的 box/lasso 实时圈选 JSON（level-0 坐标）；无圈选返回精确字符串 `NO_SELECTION` |
| `get_latest_selection` | `() -> str` | 最近一次 "Send to code agent" 提交的 ROIPayload JSON（含 patch_path）；无提交返回 `NO_SELECTION` |
| `get_slide_info` | `() -> str` | 当前切片元数据 JSON：`{"name", "dimensions": [w,h], "mpp", "levels", "level_downsamples", "db_id", "annotation_count"}`；无切片返回精确字符串 `NO_SLIDE` |
| `annotate_roi` | `(roi_id: int, label: str \| None = None, notes: str \| None = None) -> str` | 回写 label/notes 到 rois 表，返回更新后行 JSON；DB-free 或 roi 不存在返回 `{"error": ...}` |
| `query_annotations` | `(label: str \| None = None, limit: int = 50) -> str` | 当前切片标注行 JSON 列表（可选 label 精确过滤）；无打开切片返回 `[]`，DB-free 返回 `{"error": ...}` |
| `get_analysis_capabilities` | `() -> str` | 可用分析（nuclei/QC/stain-norm/heatmap/training）、torch 可用性、已训练模型的 JSON |

辅助内核全局：`db`（DBContext，`db.enabled` / `db.roi_repo` / `db.run_repo`）、
`get_source()`（当前 SlideSource）、`agent_bridge`（jsonl 历史）。

所有标注/工具调用同时落入 `interactions` 表（kind:
selection_view/roi_submit/label_set/analysis_run/tool_call/human_gate），
供数据飞轮与自动化偏倚研究使用。

## 3. 典型工作流

### 3.1 读圈选 → 分析 → 回写 label → 触发训练

```python
import json

raw = get_current_selection()          # 1. 零点击读用户圈选
if raw != "NO_SELECTION":
    sel = json.loads(raw)
    info = json.loads(get_slide_info())  # 2. 切片元数据（mpp、levels）
    # 3. 分析：selection_stats / detect_nuclei / qc_report / heatmap ...
    # 4. 回写标注（roi_id 来自 get_latest_selection() 或 query_annotations()）
    annotate_roi(sel_roi_id, label="tumor", notes="agent: high nuclei density")
    # 5. 数据足够后用 hescope.train_from_annotations(db.engine, ...) 触发训练
```

### 3.2 查询既有标注

```python
rows = json.loads(query_annotations(label="tumor", limit=20))
for r in rows:
    print(r["id"], r["label"], r["bbox"])
```

## 4. Loop 工作模式（长任务）

对需要多轮的长任务，按以下循环工作，**每一步都可被人工打断**：

1. **查询**：`query_annotations()` / `get_current_selection()` 获取当前状态。
2. **分析**：在 patch 上跑 nuclei/QC/embedding 等（见
   `get_analysis_capabilities()`）。
3. **回写**：`annotate_roi(roi_id, label=..., notes=...)` 把结论落库；
   用 `db.run_repo.record(tool=..., ...)` 记录分析过程。
4. **请求人工 gate**：涉及不可逆操作（删除 ROI、覆盖人工标注、启动长训练）
   前，先向用户说明计划并等待确认；把人工决定记入 interactions
   （kind="human_gate"，可经 `db` 的 engine 用 `InteractionRepo.record` 写入）。
5. 回到 1，直到任务完成或用户叫停。

## 5. 导出与共生

- QuPath 互操作：`hescope.geojson.export_rois_geojson(db.engine, slide_id, path)`
  把某张切片的全部标注导出为 QuPath 可导入的 GeoJSON（bbox 多边形 +
  `classification` 映射自 label）。
- 通用导出：`hescope.db.export_rois(db.engine, slide_id=..., fmt="json"|"csv")`。

完整契约见仓库根目录 `AGENTS.md`。
