"""QuPath-compatible GeoJSON export for saved ROI annotations.

HE-Scope persists ROIs in the ``rois`` table (see hescope.db); this module
serializes them as a GeoJSON FeatureCollection that QuPath can import
("Objects → Annotations → Import from GeoJSON"). Geometry follows the shape
the user actually drew -- a lasso exports as its own vertices, a circle as a
sampled ring -- and a non-empty label maps to the QuPath ``classification``
property (``{"name": label}``).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable


#: Vertices used to approximate a circle ROI as a GeoJSON ring. GeoJSON has no
#: circle geometry, and QuPath imports rings, so a circle has to be sampled.
_CIRCLE_SEGMENTS = 64


def _bbox_polygon(bbox: Iterable[float]) -> dict:
    """Closed counterclockwise polygon ring for a [x0, y0, x1, y1] bbox."""
    x0, y0, x1, y1 = [float(v) for v in bbox]
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return {"type": "Polygon", "coordinates": [ring]}


def _closed_ring(points: list[list[float]]) -> list[list[float]]:
    """Close a ring if it is not already closed (GeoJSON requires it)."""
    if points and points[0] != points[-1]:
        return [*points, list(points[0])]
    return points


def roi_geometry(kind: str | None, points: Any, bbox: Iterable[float]) -> dict:
    """GeoJSON geometry for one ROI, faithful to the shape the user drew.

    ``rect`` is the two-corner box, ``polygon`` is the lasso's own vertices,
    and ``circle`` -- stored as (centre, point-on-edge) -- is sampled into a
    ring, because GeoJSON has no circle. Anything unusable falls back to the
    bbox, which is what this module used to do for *every* ROI: a lasso
    exported as its bounding box, silently changing the region a QuPath user
    then sees. The geometry was always available -- ``rois.points_json`` is
    stored next to ``bbox_json`` and ``ROIRepo`` returns it -- it just was not
    read.
    """
    pts: list[list[float]] = []
    if isinstance(points, str):
        try:
            points = json.loads(points)
        except (TypeError, ValueError):
            points = None
    if isinstance(points, (list, tuple)):
        for p in points:
            try:
                pts.append([float(p[0]), float(p[1])])
            except (TypeError, ValueError, IndexError):
                pts = []
                break

    if kind == "circle" and len(pts) == 2:
        (cx, cy), (ex, ey) = pts
        r = math.hypot(ex - cx, ey - cy)
        if r > 0:
            ring = [
                [
                    cx + r * math.cos(2.0 * math.pi * i / _CIRCLE_SEGMENTS),
                    cy + r * math.sin(2.0 * math.pi * i / _CIRCLE_SEGMENTS),
                ]
                for i in range(_CIRCLE_SEGMENTS)
            ]
            return {"type": "Polygon", "coordinates": [_closed_ring(ring)]}
    elif kind == "rect" and len(pts) == 2:
        (x0, y0), (x1, y1) = pts
        lo_x, hi_x = min(x0, x1), max(x0, x1)
        lo_y, hi_y = min(y0, y1), max(y0, y1)
        ring = [[lo_x, lo_y], [hi_x, lo_y], [hi_x, hi_y], [lo_x, hi_y]]
        return {"type": "Polygon", "coordinates": [_closed_ring(ring)]}
    elif kind == "polygon" and len(pts) >= 3:
        return {"type": "Polygon", "coordinates": [_closed_ring(pts)]}

    return _bbox_polygon(bbox)


def rois_to_geojson(rows: list[dict], mpp: float | None = None) -> dict:
    """Convert ROIRepo row dicts to a GeoJSON FeatureCollection.

    Each feature's properties carry roi_id / kind / label / notes / mpp; a
    non-empty label additionally maps to QuPath's ``classification`` object.
    ``mpp`` (microns-per-pixel) is a per-slide constant passed in by the
    caller (ROI rows do not store it); a row-level ``mpp`` key wins when
    present.
    """
    features: list[dict] = []
    for row in rows:
        bbox = row.get("bbox")
        if bbox is None and row.get("bbox_json"):
            bbox = json.loads(row["bbox_json"])
        if not bbox or len(bbox) != 4:
            continue  # cannot build a geometry; skip rather than crash
        label = row.get("label") or ""
        properties: dict[str, Any] = {
            "roi_id": row.get("id"),
            "kind": row.get("kind"),
            "label": label,
            "notes": row.get("notes") or "",
            "mpp": row.get("mpp", mpp),
        }
        if label:
            # QuPath maps feature.properties.classification onto the imported
            # annotation's path-class.
            properties["classification"] = {"name": label}
        features.append(
            {
                "type": "Feature",
                "geometry": roi_geometry(
                    row.get("kind"), row.get("points_json"), bbox
                ),
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def slide_feature_collection(engine: Any, slide_id: int) -> dict:
    """FeatureCollection for one slide's saved ROIs, straight from the DB.

    Split out of ``export_rois_geojson`` so the notebook's download button can
    hand the user the same bytes without writing a file first: the README
    advertises a one-click GeoJSON export, and for a while the only entry point
    was the file-writing function, which app.py never called (R05-8).
    """
    from .db import ROIRepo, SlideRepo

    rows = ROIRepo(engine).for_slide(slide_id)
    slide = SlideRepo(engine).get(slide_id)
    mpp = slide.get("mpp") if slide else None
    return rois_to_geojson(rows, mpp=mpp)


def slide_geojson_text(engine: Any, slide_id: int | None) -> str:
    """``slide_feature_collection`` as the JSON text a download hands over.

    ``slide_id`` of None (no slide open) yields an empty FeatureCollection
    rather than an error, so the button is always safe to click.
    """
    if slide_id is None:
        return json.dumps({"type": "FeatureCollection", "features": []}, indent=2)
    return json.dumps(slide_feature_collection(engine, slide_id), indent=2)


def export_rois_geojson(
    engine: Any,
    slide_id: int,
    path: str | Path,
) -> dict:
    """Export one slide's ROIs to a QuPath-readable GeoJSON file.

    Returns the FeatureCollection dict that was written (also useful without
    reading the file back). Parent directories are created as needed.
    """
    fc = slide_feature_collection(engine, slide_id)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc, indent=2), encoding="utf-8")
    return fc
