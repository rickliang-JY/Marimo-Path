"""Comparable statistics across the ROIs of a slide.

``roi_stats`` measures one region at a time, which is enough to look at a
region and not enough to compare two, see a distribution, or say that tumour
and stroma differ. Everything here is a query and a reshape over data the
database already holds -- no new measurement, no new maths.

THE UNIT TRAP
-------------
An ROI row carries two different pixel counts and they are not
interchangeable:

* ``bbox`` is in LEVEL-0 pixels -- the region the user actually outlined;
* ``stats["width_px"]`` / ``["height_px"]`` are the PATCH's dimensions, and
  ``extract_patch`` reads from a downsampled pyramid level and then caps the
  result at ``max_size``.

For a 4096 px ROI those differ by 4x. Physical size therefore comes from the
bbox and the slide's mpp, never from the patch dimensions -- deriving an area
from the patch is what overstated ``density_per_mm2`` by the square of the
downsample (bugs/2026-08-10-round-07.md, R07-2).
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterable

__all__ = [
    "STAT_COLUMNS",
    "roi_stats_rows",
    "label_summary",
    "rows_to_csv",
    "rows_to_json",
]

#: Numeric columns worth comparing and aggregating, in display order.
STAT_COLUMNS: tuple[str, ...] = (
    "width_px",
    "height_px",
    "area_mm2",
    "tissue_fraction",
    "hematoxylin_mean",
    "eosin_mean",
    "mean_r",
    "mean_g",
    "mean_b",
)


def _loads(raw: Any) -> dict:
    """Parse a JSON column that may be absent, empty or malformed."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def roi_stats_rows(
    engine: Any, slide_id: int, mpp: float | None = None
) -> list[dict]:
    """One flat, comparable row per ROI of ``slide_id``.

    ``mpp`` is the slide's microns-per-pixel (a per-slide constant the ROI
    rows do not store). When it is None the physical columns are None rather
    than a guess.

    ROIs with no recorded statistics still get a row: they were drawn, they
    have a label and a size, and dropping them would quietly under-report how
    much of the slide has been annotated.
    """
    from .db import ROIRepo

    rows: list[dict] = []
    for row in ROIRepo(engine).for_slide(slide_id):
        stats = _loads(row.get("stats_json"))
        bbox = row.get("bbox")
        if not bbox and row.get("bbox_json"):
            bbox = _loads_list(row.get("bbox_json"))
        x0, y0, x1, y1 = (list(bbox) + [0, 0, 0, 0])[:4] if bbox else (0, 0, 0, 0)
        # LEVEL-0 pixels: the region drawn, not the patch measured (see above)
        w_px = max(0, int(x1) - int(x0))
        h_px = max(0, int(y1) - int(y0))
        um = _f(mpp)
        w_um = w_px * um if um else None
        h_um = h_px * um if um else None
        area_mm2 = (w_um * h_um) / 1e6 if (w_um and h_um) else None

        he = _loads(stats.get("he_deconvolution"))
        rgb = stats.get("mean_rgb") or []
        rows.append(
            {
                "roi_id": row.get("id"),
                "kind": row.get("kind"),
                "label": row.get("label") or "",
                "notes": row.get("notes") or "",
                "bbox_level0": [int(x0), int(y0), int(x1), int(y1)],
                "width_px": w_px,
                "height_px": h_px,
                "width_um": w_um,
                "height_um": h_um,
                "area_mm2": area_mm2,
                "tissue_fraction": _f(stats.get("tissue_fraction")),
                "hematoxylin_mean": _f(he.get("hematoxylin_mean")),
                "eosin_mean": _f(he.get("eosin_mean")),
                "mean_r": _f(rgb[0]) if len(rgb) > 0 else None,
                "mean_g": _f(rgb[1]) if len(rgb) > 1 else None,
                "mean_b": _f(rgb[2]) if len(rgb) > 2 else None,
                "has_stats": bool(stats),
                "created_at": str(row.get("created_at") or ""),
            }
        )
    return rows


def _loads_list(raw: Any) -> list:
    try:
        out = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return out if isinstance(out, list) else []


def label_summary(rows: Iterable[dict]) -> list[dict]:
    """Per-label aggregates: n, and mean +/- SD of each numeric column.

    The dispersion is the point. A mean on its own invites "tumour reads
    higher than stroma" from two overlapping distributions; reporting SD
    alongside n is what makes that a claim someone can check. Uses the
    population SD, and reports None rather than 0.0 for a single sample, so a
    lone ROI cannot look like a tight distribution.
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("label") or "", []).append(row)

    out: list[dict] = []
    for label in sorted(groups):
        members = groups[label]
        summary: dict[str, Any] = {"label": label, "n": len(members)}
        for column in STAT_COLUMNS:
            values = [
                v for v in (row.get(column) for row in members) if v is not None
            ]
            if not values:
                summary[f"{column}_mean"] = None
                summary[f"{column}_sd"] = None
                continue
            mean = sum(values) / len(values)
            summary[f"{column}_mean"] = mean
            if len(values) < 2:
                summary[f"{column}_sd"] = None  # not a spread of one
            else:
                var = sum((v - mean) ** 2 for v in values) / len(values)
                summary[f"{column}_sd"] = math.sqrt(var)
        out.append(summary)
    return out


def rows_to_csv(rows: list[dict]) -> str:
    """CSV of whatever rows are given, header from the first row's keys.

    Results that cannot leave the tool are not results; this is the same
    reasoning as the annotation export, applied to the measurements.
    """
    import csv
    import io

    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {k: ("" if v is None else _csv_value(v)) for k, v in row.items()}
        )
    return buf.getvalue()


def _csv_value(value: Any) -> Any:
    """Lists become compact JSON so a bbox stays one cell."""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, separators=(",", ":"))
    return value


def rows_to_json(rows: list[dict]) -> str:
    return json.dumps(rows, indent=2, default=str)
