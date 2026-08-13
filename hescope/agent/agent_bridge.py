"""ROIPayload schema and the code-agent bridge ("marimo pair skill").

Whatever the user circles in the viewer is packaged into an ROIPayload and
returned to a code agent (marimo AI / pair integration).
"""

from __future__ import annotations

import functools
import json
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.rois import ROI, extract_patch, roi_stats
from ..wsi.slides import SlideSource

if TYPE_CHECKING:
    from ..store.db import ROIRepo

MPP_10X = 2.0  # microns-per-pixel reference for 10x


def magnification_for(mpp: float | None, downsample: float) -> float | None:
    """Approx display magnification: 10x reference at 2.0 um/px, scaled by the
    current downsample. None if mpp is unknown."""
    if mpp is None or mpp <= 0:
        return None
    return MPP_10X / (mpp * downsample) * 10.0


@dataclass
class ROIPayload:
    slide_name: str
    slide_dimensions: tuple[int, int]
    mpp: float | None
    magnification: float | None
    roi: dict  # {"kind":..., "points_level0": [...], "bbox_level0":[x0,y0,x1,y1]}
    patch_path: str  # absolute path to exported PNG of the ROI patch
    stats: dict  # output of roi_stats
    created_at: str  # ISO8601
    roi_id: int | None = None  # id in the rois table when persisted via a repository

    def to_json(self) -> str:
        d = {
            "slide_name": self.slide_name,
            "slide_dimensions": list(self.slide_dimensions),
            "mpp": self.mpp,
            "magnification": self.magnification,
            "roi": self.roi,
            "patch_path": self.patch_path,
            "stats": self.stats,
            "created_at": self.created_at,
            "roi_id": self.roi_id,
        }
        return json.dumps(d)

    @classmethod
    def from_json(cls, s: str) -> "ROIPayload":
        d = json.loads(s)
        return cls(
            slide_name=d["slide_name"],
            slide_dimensions=tuple(d["slide_dimensions"]),  # type: ignore[arg-type]
            mpp=d["mpp"],
            magnification=d["magnification"],
            roi=d["roi"],
            patch_path=d["patch_path"],
            stats=d["stats"],
            created_at=d["created_at"],
            roi_id=d.get("roi_id"),  # tolerate payloads written before roi_id existed
        )

    def to_agent_prompt(self) -> str:
        """Human+LLM readable summary block describing the selection, meant to
        be handed to a code agent (marimo AI / pair)."""
        x0, y0, x1, y1 = self.roi["bbox_level0"]
        lines = [
            "### HE-Scope ROI selection",
            f"- Slide: {self.slide_name} (level-0 dimensions: "
            f"{self.slide_dimensions[0]}x{self.slide_dimensions[1]} px)",
            f"- Microns-per-pixel: {self.mpp if self.mpp is not None else 'unknown'}",
            f"- Approx magnification: "
            f"{f'{self.magnification:.1f}x' if self.magnification is not None else 'unknown'}",
            f"- ROI kind: {self.roi['kind']}",
            f"- ROI bbox (level-0 px): x0={x0}, y0={y0}, x1={x1}, y1={y1} "
            f"({x1 - x0}x{y1 - y0} px)",
            f"- ROI points (level-0): {self.roi['points_level0']}",
            f"- Patch image: {self.patch_path} "
            f"({self.stats.get('width_px')}x{self.stats.get('height_px')} px)",
            f"- Mean RGB: {[round(v, 1) for v in self.stats.get('mean_rgb', [])]}",
            f"- Tissue fraction: {self.stats.get('tissue_fraction', 0):.3f}",
            f"- H&E deconvolution: hematoxylin_mean="
            f"{self.stats.get('he_deconvolution', {}).get('hematoxylin_mean', 0):.4f}, "
            f"eosin_mean="
            f"{self.stats.get('he_deconvolution', {}).get('eosin_mean', 0):.4f}",
            f"- Created at: {self.created_at}",
            "",
            "You may load the patch image at patch_path for further analysis.",
        ]
        return "\n".join(lines)


class AgentBridge:
    """Packages ROIs into payloads and records history for a code agent."""

    def __init__(
        self,
        out_dir: str | Path,
        repository: "ROIRepo | None" = None,
        slide_id: int | None = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._history_path = self.out_dir / "roi_history.jsonl"
        self.repository = repository
        self.slide_id = slide_id

    def submit(
        self,
        source: SlideSource,
        roi: ROI,
        magnification: float | None = None,
    ) -> ROIPayload:
        """Extract patch, save PNG under out_dir/patches/, compute stats, build
        payload, append payload JSON line to out_dir/roi_history.jsonl, return
        the payload.

        If a repository was given at construction time, the ROI is also
        persisted via ``repository.add`` (using ``self.slide_id``) and the
        payload's ``roi_id`` is set to the new row id. Raises ValueError if a
        repository is set but ``slide_id`` is None."""
        if self.repository is not None and self.slide_id is None:
            raise ValueError(
                "AgentBridge has a repository but no slide_id; "
                "pass slide_id to the constructor to persist ROIs"
            )
        patches_dir = self.out_dir / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)

        patch = extract_patch(source, roi)
        stats = roi_stats(patch, roi)
        x0, y0, x1, y1 = roi.bbox()
        now = datetime.now(timezone.utc)
        ts = re.sub(r"[^0-9A-Za-z]+", "", now.strftime("%Y%m%dT%H%M%S%f"))
        patch_path = patches_dir / f"{ts}_{roi.kind}_{x0}_{y0}_{x1}_{y1}.png"
        patch.save(patch_path)

        mag = magnification
        if mag is None and source.mpp:
            mag = magnification_for(source.mpp, 1.0)

        payload = ROIPayload(
            slide_name=source.name,
            slide_dimensions=source.dimensions,
            mpp=source.mpp,
            magnification=mag,
            roi={
                "kind": roi.kind,
                "points_level0": [list(p) for p in roi.points],
                "bbox_level0": [x0, y0, x1, y1],
            },
            patch_path=str(patch_path.resolve()),
            stats=stats,
            created_at=now.isoformat(),
        )
        if self.repository is not None:
            assert self.slide_id is not None  # checked at the top of submit()
            payload.roi_id = self.repository.add(
                self.slide_id,
                roi,
                patch_path=payload.patch_path,
                stats=stats,
                magnification=mag,
            )
        with open(self._history_path, "a", encoding="utf-8") as f:
            f.write(payload.to_json() + "\n")
        return payload

    def latest(self) -> ROIPayload | None:
        hist = self.history()
        return hist[-1] if hist else None

    def history(self) -> list[ROIPayload]:
        """Every submitted payload, oldest first.

        Unparseable lines are skipped rather than aborting the read: the
        history file is append-only, so a process killed mid-write leaves a
        truncated final line, and one bad line must not make the whole
        history (and `get_latest_selection`) permanently unreadable.
        """
        if not self._history_path.exists():
            return []
        out: list[ROIPayload] = []
        with open(self._history_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(ROIPayload.from_json(line))
                except Exception:
                    continue
        return out


def make_marimo_tool(bridge_getter: Callable[[], AgentBridge]) -> Callable[[], str]:
    """Return a zero-arg callable returning the latest payload JSON (or
    'NO_SELECTION'). Suitable to register as a marimo AI tool / expose to a
    code agent."""

    def get_latest_selection() -> str:
        # Never raises, like the other five tools: an unreadable history or
        # a bridge failure returns an "error" JSON object instead.
        try:
            payload = bridge_getter().latest()
            return payload.to_json() if payload is not None else "NO_SELECTION"
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    return get_latest_selection


# ---------------------------------------------------------------------------
# DB-backed agent tools: annotate_roi / query_annotations / get_slide_info.
# Same contract as make_marimo_tool / make_live_selection_tool: module-scope
# callables returning JSON strings (or a fixed sentinel), never raising.
# ---------------------------------------------------------------------------


def _record_interaction(db: object, **kwargs: object) -> None:
    """Best-effort interaction trace write; swallows everything."""
    try:
        engine = getattr(db, "engine", None)
        if engine is None:
            return
        from ..store.db import InteractionRepo

        InteractionRepo(engine).record(**kwargs)  # type: ignore[arg-type]
    except Exception:
        pass


def make_annotate_roi_tool(
    db_getter: Callable[[], object],
) -> Callable[..., str]:
    """Tool factory: ``annotate_roi(roi_id, label=None, notes=None) -> str``.

    Writes label/notes back to the rois table via
    ``ROIRepo.update_annotation`` and returns the updated row as JSON.
    Records an interactions row (kind=label_set). Never raises: DB-free mode
    or a missing row returns a JSON object with an "error" key.
    """

    def annotate_roi(
        roi_id: int, label: str | None = None, notes: str | None = None
    ) -> str:
        try:
            db = db_getter()
            if db is None or not getattr(db, "enabled", False):
                return json.dumps(
                    {"error": "database disabled (DB-free mode): cannot annotate"}
                )
            roi_repo = getattr(db, "roi_repo", None)
            if roi_repo is None:
                return json.dumps({"error": "roi repository unavailable"})
            try:
                rid = int(roi_id)
            except (TypeError, ValueError):
                return json.dumps({"error": f"invalid roi_id: {roi_id!r}"})
            if roi_repo.get(rid) is None:
                return json.dumps({"error": f"roi {rid} not found"})
            roi_repo.update_annotation(rid, label=label, notes=notes)
            row = roi_repo.get(rid)
            _record_interaction(
                db,
                kind="label_set",
                payload={"roi_id": rid, "label": label, "notes": notes},
                slide_id=row.get("slide_id") if row else None,
                roi_id=rid,
            )
            return json.dumps(row)
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    return annotate_roi


def make_query_annotations_tool(
    db_getter: Callable[[], object],
    slide_id_getter: Callable[[], int | None],
) -> Callable[..., str]:
    """Tool factory: ``query_annotations(label=None, limit=50) -> str``.

    Returns the current slide's annotation rows (ROIRepo row dicts) as a JSON
    list, optionally filtered by exact label. Records an interactions row
    (kind=tool_call). Never raises: DB-free mode returns an "error" JSON
    object; no open slide returns the empty list ``[]``.
    """

    def query_annotations(label: str | None = None, limit: int = 50) -> str:
        try:
            db = db_getter()
            if db is None or not getattr(db, "enabled", False):
                return json.dumps(
                    {"error": "database disabled (DB-free mode): no annotations"}
                )
            slide_id = slide_id_getter()
            if slide_id is None:
                return "[]"
            try:
                lim = max(1, int(limit))
            except (TypeError, ValueError):
                lim = 50
            rows = db.roi_repo.search(label=label, slide_id=slide_id)[:lim]
            _record_interaction(
                db,
                kind="tool_call",
                payload={
                    "tool": "query_annotations",
                    "label": label,
                    "limit": lim,
                    "n_results": len(rows),
                },
                slide_id=slide_id,
            )
            return json.dumps(rows)
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    return query_annotations


def make_slide_info_tool(
    source_getter: Callable[[], "SlideSource | None"],
    db_getter: Callable[[], object],
    slide_id_getter: Callable[[], int | None],
) -> Callable[[], str]:
    """Tool factory: ``get_slide_info() -> str``.

    Zero-arg tool returning metadata JSON for the currently open slide:
    name, dimensions, mpp, pyramid levels, DB row id and annotation count.
    Returns the exact string 'NO_SLIDE' when no slide is open. Never raises.
    """

    def get_slide_info() -> str:
        try:
            src = source_getter()
            if src is None:
                return "NO_SLIDE"
            slide_id = slide_id_getter()
            annotation_count: int | None = None
            db = db_getter()
            if (
                db is not None
                and getattr(db, "enabled", False)
                and slide_id is not None
            ):
                try:
                    annotation_count = len(db.roi_repo.for_slide(slide_id))
                except Exception:
                    annotation_count = None
            info = {
                "name": src.name,
                "dimensions": [int(src.dimensions[0]), int(src.dimensions[1])],
                "mpp": src.mpp,
                "levels": len(src.level_downsamples),
                "level_downsamples": [float(d) for d in src.level_downsamples],
                "db_id": slide_id,
                "annotation_count": annotation_count,
            }
            _record_interaction(
                db,
                kind="tool_call",
                payload={"tool": "get_slide_info", "slide": src.name},
                slide_id=slide_id,
            )
            return json.dumps(info)
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    return get_slide_info


# ---------------------------------------------------------------------------
# Zero-click live-selection tool (marimo-pair): the agent reads the figure
# selection the moment the user drags a box/lasso, without any "submit" click.
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _live_patch_dir() -> str:
    """Process-wide temp dir for live-selection patch PNGs (created lazily,
    once)."""
    return tempfile.mkdtemp(prefix="hescope_live_patches_")


def make_live_selection_tool(
    getter: Callable[[], dict | None],
    db_getter: Callable[[], object] | None = None,
    slide_id_getter: Callable[[], int | None] | None = None,
) -> Callable[[], str]:
    """Zero-arg tool for code agents. Returns compact JSON of the live
    selection dict, or the exact string 'NO_SELECTION'.

    This is the ZERO-CLICK companion of make_marimo_tool: make_marimo_tool
    returns the last SUBMITTED ROI payload (via AgentBridge, requiring the
    user to click "Send to code agent"), while this tool reports the raw
    live figure selection (box/lasso) as mapped by
    ``hescope.viewer.viewer.current_selection`` — no user click required.

    With ``db_getter`` the read is traced as an interactions row
    (kind=selection_view): an agent looking at what the user just drew is the
    "selection view" README/AGENTS.md promise the table records, and it is the
    one event that says the agent saw the human's choice before answering.
    Both extra arguments are optional so existing callers are unaffected.
    """

    def get_current_selection() -> str:
        # Never raises: a malformed figure selection or a mapping failure
        # returns an "error" JSON object instead of propagating.
        try:
            sel = getter()
            if db_getter is not None:
                _record_interaction(
                    db_getter(),
                    kind="selection_view",
                    payload={
                        "tool": "get_current_selection",
                        "kind": (sel or {}).get("kind"),
                        "bbox_level0": (sel or {}).get("bbox_level0"),
                    },
                    slide_id=(
                        slide_id_getter() if slide_id_getter is not None else None
                    ),
                )
            return json.dumps(sel) if sel is not None else "NO_SELECTION"
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    return get_current_selection


def selection_stats(
    source: SlideSource,
    selection: dict,
    max_size: int = 1024,
    out_dir: str | Path | None = None,
) -> dict:
    """Convenience: extract the patch for a live selection dict (as returned
    by ``hescope.viewer.viewer.current_selection``) and return ``roi_stats(patch)``
    merged with ``{"patch_path": <saved tmp png>}`` — the patch is saved
    under a temp dir so a multimodal agent can open the file.

    Reuses extract_patch/roi_stats from rois.py. ``out_dir`` defaults to a
    module-level lazily created temp dir (one per process).
    """
    roi = ROI(
        kind=selection["kind"],
        points=tuple(
            (float(p[0]), float(p[1])) for p in selection["points_level0"]
        ),
    )
    patch = extract_patch(source, roi, max_size=max_size)
    stats = roi_stats(patch, roi)
    target_dir = Path(out_dir) if out_dir is not None else Path(_live_patch_dir())
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = re.sub(
        r"[^0-9A-Za-z]+",
        "",
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"),
    )
    x0, y0, x1, y1 = roi.bbox()
    patch_path = target_dir / f"live_{ts}_{roi.kind}_{x0}_{y0}_{x1}_{y1}.png"
    patch.save(patch_path)
    return {**stats, "patch_path": str(patch_path.resolve())}
