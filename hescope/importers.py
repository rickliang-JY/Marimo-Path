"""Import annotations produced by other pathology tools.

The cheapest way to absorb the existing ecosystem is to let other people's
artifacts move in and out, so this is the inbound half of
:mod:`hescope.geojson`: QuPath GeoJSON and ASAP/CAMELYON XML in, HE-Scope
:class:`~hescope.rois.ROI` objects out, in level-0 pixel coordinates.

Imported annotations go through :meth:`hescope.db.ROIRepo.add` like any drawn
one, so they are indistinguishable afterwards -- exportable, trainable on, and
visible to ``query_annotations()``.

**Nothing is dropped silently.** Real annotation files contain geometries this
system has no equivalent for, and quietly importing an approximation of a
pathologist's outline is worse than refusing it. Every parse returns an
:class:`ImportReport` whose ``skipped`` counts and ``warnings`` say exactly
what happened and why, and the caller is expected to surface it.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .rois import ROI

__all__ = [
    "ImportedROI",
    "ImportReport",
    "parse_geojson_annotations",
    "parse_asap_xml",
    "import_annotations",
]

#: Below this many distinct vertices a ring encloses no area worth importing.
_MIN_RING_POINTS = 3


@dataclass(frozen=True)
class ImportedROI:
    """One annotation ready to be persisted: geometry plus its class."""

    roi: ROI
    label: str = ""
    notes: str = ""


@dataclass
class ImportReport:
    """What an import produced, and everything it could not represent."""

    rois: list[ImportedROI] = field(default_factory=list)
    #: reason -> how many source features it applied to
    skipped: dict[str, int] = field(default_factory=dict)
    #: things imported, but not faithfully
    warnings: list[str] = field(default_factory=list)

    def skip(self, reason: str, n: int = 1) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + n

    @property
    def n_imported(self) -> int:
        return len(self.rois)

    @property
    def n_skipped(self) -> int:
        return sum(self.skipped.values())

    def summary(self) -> str:
        """One line fit to show a user."""
        parts = [f"{self.n_imported} annotation(s) imported"]
        if self.n_skipped:
            detail = ", ".join(
                f"{n} {reason}" for reason, n in sorted(self.skipped.items())
            )
            parts.append(f"{self.n_skipped} skipped ({detail})")
        if self.warnings:
            parts.append("; ".join(self.warnings))
        return "; ".join(parts)


def _ring_to_roi(ring: Iterable[Any]) -> ROI | None:
    """A closed GeoJSON ring -> a polygon ROI, or None if it has no area.

    The duplicated closing vertex GeoJSON requires is dropped: an ROI stores
    the outline, not the wire format.
    """
    pts: list[tuple[float, float]] = []
    for p in ring or []:
        try:
            pts.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError, IndexError):
            return None
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(set(pts)) < _MIN_RING_POINTS:
        return None  # degenerate: a point, a line, or a repeated vertex
    return ROI(kind="polygon", points=tuple(pts))


def parse_geojson_annotations(source: str | Path | dict) -> ImportReport:
    """Parse QuPath-style GeoJSON into ROIs.

    ``source`` may be a path, the JSON text, or an already-parsed object.
    Accepts a FeatureCollection, a bare Feature, or a list of Features --
    QuPath has exported all three shapes.

    Geometry handling, chosen so that nothing is misrepresented:

    * ``Polygon``     -> one ROI from the exterior ring. Interior rings (holes)
      cannot be represented, so a polygon that has them is imported and
      **reported**: silently keeping the outer ring of a donut changes the
      region the pathologist drew.
    * ``MultiPolygon`` -> one ROI **per part**. The parts are disjoint, so
      collapsing them into a single outline would invent area between them.
    * ``Point`` / ``LineString`` / ``MultiPoint`` / ``MultiLineString`` ->
      skipped; they enclose nothing an ROI could measure.
    * anything degenerate (fewer than three distinct vertices) -> skipped.

    The class comes from QuPath's ``properties.classification.name``, falling
    back to ``properties.name``. ``properties.objectType`` is preserved in the
    ROI's notes, so imported detections stay distinguishable from annotations.
    """
    report = ImportReport()

    if isinstance(source, (str, Path)):
        text = (
            Path(source).read_text(encoding="utf-8")
            if isinstance(source, Path) or "\n" not in str(source) and Path(str(source)).exists()
            else str(source)
        )
        try:
            data = json.loads(text)
        except (ValueError, OSError) as exc:
            report.skip(f"unreadable GeoJSON ({type(exc).__name__})")
            return report
    else:
        data = source

    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features") or []
    elif isinstance(data, dict) and data.get("type") == "Feature":
        features = [data]
    elif isinstance(data, list):
        features = data
    else:
        report.skip("not a GeoJSON FeatureCollection")
        return report

    holes = 0
    for feature in features:
        if not isinstance(feature, dict):
            report.skip("malformed feature")
            continue
        geometry = feature.get("geometry") or {}
        gtype = geometry.get("type")
        coords = geometry.get("coordinates")
        props = feature.get("properties") or {}

        classification = props.get("classification")
        label = ""
        if isinstance(classification, dict):
            label = str(classification.get("name") or "")
        if not label and isinstance(props.get("name"), str):
            label = props["name"]
        object_type = props.get("objectType")
        notes = f"imported from GeoJSON ({object_type})" if object_type else "imported from GeoJSON"

        if gtype == "Polygon":
            rings = coords or []
            if len(rings) > 1:
                holes += 1
            roi = _ring_to_roi(rings[0] if rings else [])
            if roi is None:
                report.skip("degenerate polygon")
                continue
            report.rois.append(ImportedROI(roi, label=label, notes=notes))
        elif gtype == "MultiPolygon":
            added = 0
            for part in coords or []:
                if len(part or []) > 1:
                    holes += 1
                roi = _ring_to_roi(part[0] if part else [])
                if roi is not None:
                    report.rois.append(ImportedROI(roi, label=label, notes=notes))
                    added += 1
            if added == 0:
                report.skip("degenerate polygon")
        elif gtype in {"Point", "MultiPoint", "LineString", "MultiLineString"}:
            report.skip(f"{gtype} has no area")
        elif gtype is None:
            report.skip("feature without geometry")
        else:
            report.skip(f"unsupported geometry {gtype}")

    if holes:
        report.warnings.append(
            f"{holes} polygon(s) had interior rings (holes); only the outer "
            "outline was imported"
        )
    return report


def parse_asap_xml(source: str | Path) -> ImportReport:
    """Parse an ASAP / CAMELYON ``ASAP_Annotations`` XML file into ROIs.

    ``source`` may be a path or the XML text.

    * ``Polygon`` / ``Spline`` -> polygon ROI. ``Rectangle`` -> polygon ROI as
      well, because ASAP stores four corners that are not necessarily axis
      aligned; forcing them into a two-corner ``rect`` would square off a
      rotated box.
    * ``Dot`` -> skipped, it has no area.

    **Vertices are ordered by the ``Order`` attribute, not by document
    order.** ASAP does not guarantee the two agree, and trusting document
    order yields a self-intersecting polygon that still passes every shape
    check -- a wrong region that looks entirely valid.

    The class comes from ``PartOfGroup``; ASAP's unclassified sentinel
    ``"None"`` becomes an empty label rather than a class literally called
    "None".
    """
    report = ImportReport()
    text = str(source)
    path = Path(text) if len(text) < 4096 else None
    if path is not None and path.is_file():
        text = path.read_text(encoding="utf-8")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        report.skip(f"unreadable ASAP XML ({exc.msg})")
        return report

    for ann in root.findall(".//Annotation"):
        kind = (ann.get("Type") or "").strip()
        group = (ann.get("PartOfGroup") or "").strip()
        label = "" if group.lower() in {"", "none"} else group
        name = (ann.get("Name") or "").strip()
        notes = f"imported from ASAP XML{f' ({name})' if name else ''}"

        if kind == "Dot":
            report.skip("Dot has no area")
            continue
        if kind and kind not in {"Polygon", "Rectangle", "Spline"}:
            report.skip(f"unsupported ASAP type {kind}")
            continue

        ordered: list[tuple[int, tuple[float, float]]] = []
        malformed = False
        for i, c in enumerate(ann.findall(".//Coordinate")):
            try:
                x = float(c.get("X"))  # type: ignore[arg-type]
                y = float(c.get("Y"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                malformed = True
                break
            try:
                order = int(c.get("Order"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                order = i  # no Order attribute: fall back to document order
            ordered.append((order, (x, y)))
        if malformed:
            report.skip("malformed coordinate")
            continue

        ordered.sort(key=lambda item: item[0])
        pts = [p for _, p in ordered]
        if len(set(pts)) < _MIN_RING_POINTS:
            report.skip("degenerate annotation")
            continue
        report.rois.append(
            ImportedROI(ROI(kind="polygon", points=tuple(pts)), label=label, notes=notes)
        )

    return report


def import_annotations(
    engine: Any,
    slide_id: int,
    report: ImportReport,
) -> list[int]:
    """Persist a parsed report's ROIs against ``slide_id``.

    Returns the new ``rois`` row ids. Geometry is written to ``points_json``
    exactly as a drawn ROI would be, which is what makes an imported
    annotation indistinguishable from one made here.
    """
    from .db import ROIRepo

    repo = ROIRepo(engine)
    return [
        repo.add(slide_id, item.roi, label=item.label, notes=item.notes)
        for item in report.rois
    ]
