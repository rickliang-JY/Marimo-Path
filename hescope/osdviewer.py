"""OpenSeadragon-backed viewing surface for HE-Scope (anywidget).

Why this module exists
----------------------
The plotly surface in :mod:`hescope.viewer` renders ONE flat bitmap per
viewport change; marimo's plotly binding only reports ``points``/``range``/
``lasso`` back to Python, so wheel-zoom and pan are cosmetic -- they scale an
already-rendered bitmap and can never reveal new detail or anything beyond its
edge. OpenSeadragon fetches pyramid tiles itself, so pan/zoom are real, and an
anywidget comm carries the resulting viewport back to Python, which is what
keeps :class:`~hescope.rois.ViewportState` (navigator, click-to-jump, measure)
meaningful.

Layering
--------
Everything that decides a *coordinate* or builds an *agent payload* is a plain
function in this module, testable without a browser:

    OSD viewport rect  --viewport_from_osd-->   ViewportState
    ViewportState      --osd_bounds_from_viewport--> OSD viewport rect
    widget trait dict  --parse_osd_selection-->  {"kind", "points_level0"}
                       --osd_selection_to_roi--> ROI (level-0)
                       --osd_current_selection--> the 7-key agent dict

Only *rendering* and *input capture* live in JavaScript. The JS never invents a
coordinate system: it reports the raw OSD viewport rectangle plus the container
size and lets Python do the arithmetic, so there is exactly one implementation
of the mapping and it is the one the tests exercise.

Coordinate vocabulary (the two traps this module is built around)
----------------------------------------------------------------
1. OpenSeadragon normalizes BOTH viewport axes by the image WIDTH. See
   :func:`viewport_from_osd`.
2. :func:`parse_osd_selection` emits the FINAL ROI vocabulary ``rect`` /
   ``polygon``, and names its point key ``points_level0``. See its docstring.
"""

from __future__ import annotations

import functools
import math
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from .rois import ROI, ViewportState

__all__ = [
    "OSD_VERSION",
    "OSD_DIR",
    "osd_source_path",
    "osd_source_text",
    "vendored_osd_version",
    "build_esm",
    "viewport_from_osd",
    "osd_bounds_from_viewport",
    "viewport_state_from_report",
    "viewport_changed",
    "parse_osd_selection",
    "parse_osd_measure",
    "osd_selection_to_roi",
    "raw_osd_selection",
    "osd_current_selection",
    "parse_clicked_roi",
    "rois_to_payload",
    "HEScopeViewer",
    "make_viewer",
    "ROI_STROKE",
    "ROI_STROKE_SELECTED",
]

OSD_VERSION = "5.0.1"

#: Directory holding the vendored OpenSeadragon build (see THIRD_PARTY.md).
OSD_DIR = Path(__file__).resolve().parent / "static" / "vendor" / "openseadragon"

#: Overlay colours, kept byte-equal to hescope.overlay.draw_rois' RGB tuples
#: (255, 60, 60) / (60, 200, 60) so the client overlay and the server-rendered
#: export path cannot drift apart visually.
ROI_STROKE = "#ff3c3c"
ROI_STROKE_SELECTED = "#3cc83c"


# ---------------------------------------------------------------------------
# Vendored JavaScript
# ---------------------------------------------------------------------------


def osd_source_path() -> Path:
    """Absolute path of the vendored ``openseadragon.min.js``."""
    return OSD_DIR / "openseadragon.min.js"


def osd_source_text() -> str:
    """The vendored OpenSeadragon bundle with its ``sourceMappingURL`` comment
    removed.

    The comment must go for two independent reasons: the ``.map`` file is not
    vendored (so the browser would fire a 404 -- and this viewer promises zero
    external requests), and it is a trailing ``//`` line comment, which would
    comment out the ``; return OpenSeadragon;`` that :func:`build_esm` appends.
    """
    src = osd_source_path().read_text(encoding="utf-8")
    return "\n".join(
        ln
        for ln in src.splitlines()
        if not ln.lstrip().startswith("//# sourceMappingURL")
    )


def vendored_osd_version() -> str | None:
    """Version string parsed out of the vendored bundle's ``//!`` header.

    Used by the tests to catch a bundle swap that forgets to bump
    :data:`OSD_VERSION`.
    """
    head = osd_source_path().read_text(encoding="utf-8")[:400]
    m = re.search(r"^//!\s*openseadragon\s+([0-9][\w.\-]*)", head, re.MULTILINE)
    return m.group(1) if m else None


# The widget half of the ESM bundle. Written against `OSD`, the local binding
# build_esm() introduces; it never touches window.OpenSeadragon (there is no
# guarantee a marimo cell's shadow root shares a global with anything else).
_WIDGET_JS = r"""
const NS = "http://www.w3.org/2000/svg";
const ROI_STROKE = "#ff3c3c";
const ROI_STROKE_SELECTED = "#3cc83c";
// Screen-pixel budgets for freehand capture. A vertex every 2 screen px keeps
// the polygon bounded by how far the mouse TRAVELLED rather than by how often
// the browser happened to fire mousemove; 1 screen px of Douglas-Peucker
// tolerance then removes what the user cannot see anyway.
const LASSO_MIN_STEP_PX = 2;
const LASSO_SIMPLIFY_PX = 1;

function num(v, dflt) {
  const x = Number(v);
  return Number.isFinite(x) ? x : dflt;
}

/* Douglas-Peucker. `eps` is in the SAME units as the points (level-0 px); the
   caller converts a screen-pixel budget into level-0 px at the current zoom. */
function simplify(pts, eps) {
  if (pts.length < 3) return pts.slice();
  const keep = new Array(pts.length).fill(false);
  keep[0] = true;
  keep[pts.length - 1] = true;
  const stack = [[0, pts.length - 1]];
  while (stack.length) {
    const seg = stack.pop();
    const i = seg[0], j = seg[1];
    const ax = pts[i][0], ay = pts[i][1];
    const dx = pts[j][0] - ax, dy = pts[j][1] - ay;
    const len = Math.hypot(dx, dy) || 1e-9;
    let best = -1, bestd = eps;
    for (let k = i + 1; k < j; k++) {
      const d = Math.abs((pts[k][0] - ax) * dy - (pts[k][1] - ay) * dx) / len;
      if (d > bestd) { bestd = d; best = k; }
    }
    if (best >= 0) { keep[best] = true; stack.push([i, best], [best, j]); }
  }
  return pts.filter(function (_, i) { return keep[i]; });
}

export default {
  render({ model, el }) {
    const uid = "hescope-" + Math.random().toString(36).slice(2, 10);

    /* ---------------------------- DOM ---------------------------------- */
    const root = document.createElement("div");
    root.setAttribute("data-hescope-osd", uid);
    root.style.cssText =
      "position:relative;width:100%;user-select:none;-webkit-user-select:none;";

    const host = document.createElement("div");
    host.setAttribute("data-hescope-osd-host", "1");
    host.style.cssText =
      "width:100%;height:" + num(model.get("height"), 640) + "px;" +
      "background:#0b0b0d;position:relative;overflow:hidden;";
    root.appendChild(host);

    /* Gamma cannot be expressed in plain CSS, so keep an SVG filter around and
       only reference it when gamma != 1 (a url() filter that fails to resolve
       would blank the canvas, and shadow-DOM url() resolution is browser
       dependent). */
    const defs = document.createElementNS(NS, "svg");
    defs.setAttribute("width", "0");
    defs.setAttribute("height", "0");
    defs.style.cssText = "position:absolute;width:0;height:0;overflow:hidden;";
    defs.innerHTML =
      '<defs><filter id="' + uid + '-gamma" color-interpolation-filters="sRGB">' +
      '<feComponentTransfer>' +
      '<feFuncR type="gamma" exponent="1"/>' +
      '<feFuncG type="gamma" exponent="1"/>' +
      '<feFuncB type="gamma" exponent="1"/>' +
      '</feComponentTransfer></filter></defs>';
    root.appendChild(defs);

    /* One SVG overlay, absolutely positioned over the OSD canvas. It is a
       SIBLING of `host`, never a parent of it: the display CSS filter is
       applied to the OSD canvas container only, so it can never tint the ROI
       outlines or the scale bar. */
    const svg = document.createElementNS(NS, "svg");
    svg.style.cssText =
      "position:absolute;left:0;top:0;width:100%;height:100%;" +
      "pointer-events:none;overflow:hidden;";
    const gRoot = document.createElementNS(NS, "g");
    const gRois = document.createElementNS(NS, "g");
    const gDraft = document.createElementNS(NS, "g");
    gRoot.appendChild(gRois);
    gRoot.appendChild(gDraft);
    svg.appendChild(gRoot);
    root.appendChild(svg);

    const hud = document.createElement("div");
    hud.setAttribute("data-hescope-hud", "1");
    hud.style.cssText =
      "position:absolute;left:8px;bottom:8px;font:11px/1.5 ui-monospace,monospace;" +
      "color:#fff;text-shadow:0 0 3px #000;pointer-events:none;";
    root.appendChild(hud);

    const bar = document.createElement("div");
    bar.setAttribute("data-hescope-scalebar", "1");
    bar.style.cssText =
      "position:absolute;right:10px;bottom:10px;pointer-events:none;" +
      "font:11px/1.4 ui-monospace,monospace;color:#111;background:#fff;" +
      "padding:3px 5px;border-radius:2px;display:none;text-align:right;";
    root.appendChild(bar);

    const note = document.createElement("div");
    note.style.cssText =
      "position:absolute;left:0;top:0;width:100%;height:100%;display:flex;" +
      "align-items:center;justify-content:center;color:#8a8a92;" +
      "font:13px ui-sans-serif,system-ui;pointer-events:none;";
    note.textContent = "No slide open.";
    root.appendChild(note);

    el.appendChild(root);

    /* --------------------------- state --------------------------------- */
    let viewer = null;
    let imageW = 1, imageH = 1;
    let opened = false;
    let draft = null;          // in-progress drawing
    let selSeq = 0;            // monotonic, so an identical repeat drag still fires
    let clickSeq = 0;
    let ackSeq = 0;            // last command_seq seen from Python
    let lastReport = null;     // {cx, cy, ds} of the last report we sent
    let reportTimer = null;
    let spaceDown = false;
    let hovering = false;
    let disposed = false;

    /* ------------------------ coordinate helpers ----------------------- */
    /* Level-0 image px per SCREEN px at the current zoom. OSD's zoom is
       "viewport units per container width", and the image spans exactly 1.0
       viewport unit horizontally, hence imageW / (zoom * containerWidth). */
    function level0PerScreenPx() {
      if (!viewer) return 1;
      const zoom = viewer.viewport.getZoom(true);
      const cw = viewer.viewport.getContainerSize().x;
      return imageW / Math.max(1e-9, zoom * cw);
    }

    function toImage(position) {
      const vp = viewer.viewport.pointFromPixel(position, true);
      const p = viewer.viewport.viewportToImageCoordinates(vp);
      return [p.x, p.y];
    }

    /* --------------------------- reporting ----------------------------- */
    function reportPayload(why) {
      const b = viewer.viewport.getBounds(true);
      const cs = viewer.viewport.getContainerSize();
      return {
        bounds: { x: b.x, y: b.y, width: b.width, height: b.height },
        container: [Math.round(cs.x), Math.round(cs.y)],
        image: [imageW, imageH],
        ack_seq: ackSeq,
        why: why,
      };
    }

    /* Report ONLY on animation-finish / resize / open / goto_bbox, debounced,
       and only when the view actually moved: every save_changes() re-runs the
       downstream marimo cells, so a per-frame report would hammer the kernel
       with ~60 thumbnail re-renders per second. */
    function report(why, force) {
      if (!viewer || !opened || disposed) return;
      const p = reportPayload(why);
      const W0 = imageW;
      const cx = (p.bounds.x + p.bounds.width / 2) * W0;
      const cy = (p.bounds.y + p.bounds.height / 2) * W0;
      const ds = (p.bounds.width * W0) / Math.max(1, p.container[0]);
      if (!force && lastReport) {
        const movedPx = Math.hypot(cx - lastReport.cx, cy - lastReport.cy) / Math.max(1e-9, ds);
        const zoomed = Math.abs(ds / Math.max(1e-9, lastReport.ds) - 1);
        if (movedPx < 0.5 && zoomed < 0.01) return;
      }
      lastReport = { cx: cx, cy: cy, ds: ds };
      model.set("viewport", p);
      model.save_changes();
      updateHud(cx, cy, ds);
      updateScaleBar(ds);
    }

    function scheduleReport(why) {
      if (reportTimer) clearTimeout(reportTimer);
      reportTimer = setTimeout(function () {
        reportTimer = null;
        report(why, false);
      }, 120);
    }

    function updateHud(cx, cy, ds) {
      hud.textContent =
        "x " + Math.round(cx) + "  y " + Math.round(cy) + "  ds " + ds.toFixed(2);
    }

    function updateScaleBar(ds) {
      const mpp = num(model.get("mpp"), 0);
      if (!(mpp > 0) || !model.get("show_scale_bar")) { bar.style.display = "none"; return; }
      const umPerScreenPx = mpp * ds;
      const target = Math.max(40, viewer.viewport.getContainerSize().x / 5);
      const cands = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];
      let best = cands[0], bestErr = Infinity;
      for (const c of cands) {
        const err = Math.abs(c / umPerScreenPx - target);
        if (err < bestErr) { bestErr = err; best = c; }
      }
      bar.style.display = "block";
      bar.innerHTML =
        '<div>' + best + ' µm</div>' +
        '<div style="height:3px;background:#111;width:' +
        Math.max(1, Math.round(best / umPerScreenPx)) + 'px;margin-left:auto;"></div>';
    }

    /* --------------------------- overlay ------------------------------- */
    /* One <g> carries the level-0 -> screen transform; every child is written
       in RAW LEVEL-0 COORDINATES and never re-projected. That is deliberate:
       the overlay then lives in exactly the same number space as the agent
       contract, so there is no second place a coordinate can be mangled. */
    function syncOverlay() {
      if (!viewer || !opened) return;
      const p = viewer.viewport.pixelFromPoint(new OSD.Point(0, 0), true);
      const zoom = viewer.viewport.getZoom(true);
      const cw = viewer.viewport.getContainerSize().x;
      const scale = (zoom * cw) / Math.max(1e-9, imageW);
      gRoot.setAttribute(
        "transform", "translate(" + p.x + "," + p.y + ") scale(" + scale + ")");
    }

    function shapeFor(item, stroke, dashed) {
      const kind = item.kind;
      const pts = item.points || [];
      let node = null;
      if (kind === "rect" && pts.length >= 2) {
        const x0 = Math.min(pts[0][0], pts[1][0]), x1 = Math.max(pts[0][0], pts[1][0]);
        const y0 = Math.min(pts[0][1], pts[1][1]), y1 = Math.max(pts[0][1], pts[1][1]);
        node = document.createElementNS(NS, "rect");
        node.setAttribute("x", x0); node.setAttribute("y", y0);
        node.setAttribute("width", Math.max(0, x1 - x0));
        node.setAttribute("height", Math.max(0, y1 - y0));
      } else if (kind === "circle" && pts.length >= 2) {
        const r = Math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]);
        node = document.createElementNS(NS, "circle");
        node.setAttribute("cx", pts[0][0]); node.setAttribute("cy", pts[0][1]);
        node.setAttribute("r", r);
      } else if (pts.length >= 2) {
        node = document.createElementNS(NS, "polygon");
        node.setAttribute("points", pts.map(function (q) { return q[0] + "," + q[1]; }).join(" "));
      }
      if (!node) return null;
      node.setAttribute("fill", "none");
      node.setAttribute("stroke", stroke);
      node.setAttribute("stroke-width", "2");
      /* Without this the outline would scale with the image and become a
         20-px-thick slab at 40x, hiding the nuclei it is meant to frame. */
      node.setAttribute("vector-effect", "non-scaling-stroke");
      if (dashed) node.setAttribute("stroke-dasharray", "6 4");
      return node;
    }

    function renderRois() {
      while (gRois.firstChild) gRois.removeChild(gRois.firstChild);
      if (!model.get("overlay_visible")) return;
      const items = model.get("rois") || [];
      items.forEach(function (item, idx) {
        const node = shapeFor(item, item.selected ? ROI_STROKE_SELECTED : ROI_STROKE, false);
        if (!node) return;
        /* Clicking an outline is the inverse of the annotation browser's
           click-to-jump; it costs one attribute because the overlay already
           lives in level-0 space. */
        node.style.pointerEvents = "stroke";
        node.style.cursor = "pointer";
        node.addEventListener("click", function (ev) {
          ev.stopPropagation();
          clickSeq += 1;
          model.set("clicked_roi", String(idx) + "#" + clickSeq);
          model.save_changes();
        });
        gRois.appendChild(node);
      });
      svg.style.pointerEvents = (model.get("rois") || []).length ? "none" : "none";
      gRois.style.pointerEvents = "auto";
    }

    function renderDraft() {
      while (gDraft.firstChild) gDraft.removeChild(gDraft.firstChild);
      if (!draft) return;
      let item;
      if (draft.tool === "lasso") {
        item = { kind: "polygon", points: draft.pts };
      } else if (draft.tool === "circle") {
        const cx = (draft.start[0] + draft.cur[0]) / 2;
        const cy = (draft.start[1] + draft.cur[1]) / 2;
        const r = Math.min(Math.abs(draft.cur[0] - draft.start[0]),
                           Math.abs(draft.cur[1] - draft.start[1])) / 2;
        item = { kind: "circle", points: [[cx, cy], [cx + r, cy]] };
      } else {
        item = { kind: "rect", points: [draft.start, draft.cur] };
      }
      const node = shapeFor(item, ROI_STROKE, true);
      if (node) gDraft.appendChild(node);
    }

    /* --------------------------- drawing ------------------------------- */
    function drawingTool() {
      const t = model.get("tool") || "pan";
      return t === "pan" ? null : t;
    }

    function applyTool() {
      const t = drawingTool();
      const pan = !t || spaceDown;
      if (viewer) {
        viewer.gestureSettingsMouse.dragToPan = pan;
        viewer.gestureSettingsTouch.dragToPan = pan;
      }
      host.style.cursor = t && !spaceDown ? "crosshair" : "grab";
    }

    function onPress(ev) {
      if (!opened) return;
      const t = drawingTool();
      if (!t || spaceDown) return;
      const p = toImage(ev.position);
      draft = { tool: t, pts: [p], start: p, cur: p };
      renderDraft();
    }

    function onDrag(ev) {
      if (!draft) return;
      ev.preventDefaultAction = true;
      const p = toImage(ev.position);
      draft.cur = p;
      if (draft.tool === "lasso") {
        const last = draft.pts[draft.pts.length - 1];
        const step = LASSO_MIN_STEP_PX * level0PerScreenPx();
        if (Math.hypot(p[0] - last[0], p[1] - last[1]) >= step) draft.pts.push(p);
      }
      renderDraft();
    }

    function onDragEnd() {
      if (!draft) return;
      const d = draft;
      draft = null;
      renderDraft();
      if (d.tool === "lasso") {
        let pts = d.pts;
        if (pts.length < 3) return;
        pts = simplify(pts, LASSO_SIMPLIFY_PX * level0PerScreenPx());
        if (pts.length < 3) return;
        emit("polygon", pts);
        return;
      }
      const x0 = Math.min(d.start[0], d.cur[0]), x1 = Math.max(d.start[0], d.cur[0]);
      const y0 = Math.min(d.start[1], d.cur[1]), y1 = Math.max(d.start[1], d.cur[1]);
      /* A press with no movement is a click, not a zero-area ROI. */
      if (!(x1 - x0 > 1e-9) || !(y1 - y0 > 1e-9)) return;
      /* NOTE: the circle tool reports a RECT. Circle-ness is a Python-side
         flag (osd_selection_to_roi(..., as_circle=True)) so that the widget
         emits exactly one geometry vocabulary and the agent contract keeps a
         single conversion path. */
      emit(d.tool === "measure" ? "measure" : "rect", [[x0, y0], [x1, y1]]);
    }

    function emit(kind, pts) {
      selSeq += 1;
      model.set("selection", {
        kind: kind,
        points_level0: pts.map(function (p) { return [p[0], p[1]]; }),
        seq: selSeq,
      });
      model.save_changes();
    }

    /* --------------------------- viewer -------------------------------- */
    function ensureViewer() {
      if (viewer) return viewer;
      viewer = OSD({
        element: host,
        prefixUrl: "",                 // no sprite PNGs -> no network
        showNavigationControl: false,
        showNavigator: false,
        crossOriginPolicy: "Anonymous", // keep the canvas untainted (CORS tiles)
        ajaxWithCredentials: false,
        /* A pathologist counting nuclei needs to see real pixels above 1:1,
           not a browser-smoothed guess at them. */
        imageSmoothingEnabled: false,
        maxZoomPixelRatio: 8,
        visibilityRatio: 0.5,
        constrainDuringPan: false,
        animationTime: 0.6,
        springStiffness: 8,
        gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true },
      });

      viewer.addHandler("open", function () {
        const item = viewer.world.getItemAt(0);
        if (item) {
          const cs = item.getContentSize();
          imageW = cs.x || 1;
          imageH = cs.y || 1;
        }
        opened = true;
        note.style.display = "none";
        root.setAttribute("data-hescope-open", "yes");
        applyTool();
        applyDisplay();
        syncOverlay();
        renderRois();
        lastReport = null;
        report("open", true);
      });
      viewer.addHandler("open-failed", function (e) {
        opened = false;
        note.style.display = "flex";
        note.textContent = "Tile source failed to open" + (e && e.message ? ": " + e.message : ".");
        root.setAttribute("data-hescope-open", "failed");
      });
      viewer.addHandler("update-viewport", syncOverlay);
      viewer.addHandler("animation-finish", function () { scheduleReport("animation-finish"); });
      viewer.addHandler("resize", function () { syncOverlay(); scheduleReport("resize"); });
      viewer.addHandler("canvas-press", onPress);
      viewer.addHandler("canvas-drag", onDrag);
      viewer.addHandler("canvas-drag-end", onDragEnd);
      return viewer;
    }

    function openTileSource() {
      const ts = model.get("tile_source");
      if (!ts || !Object.keys(ts).length) {
        opened = false;
        note.style.display = "flex";
        note.textContent = "No slide open.";
        if (viewer) viewer.close();
        return;
      }
      ensureViewer();
      /* Display-parameter changes (channel view, stain normalization) arrive
         as a NEW tile source for the same slide. Re-opening would reset the
         view to home, so remember where the user was and restore it. */
      let keep = null;
      if (opened && num(ts.width, 0) === imageW && num(ts.height, 0) === imageH) {
        keep = viewer.viewport.getBounds(true);
      }
      opened = false;
      viewer.open(ts);
      if (keep) {
        const once = function () {
          viewer.removeHandler("open", once);
          viewer.viewport.fitBounds(keep, true);
        };
        viewer.addHandler("open", once);
      }
    }

    function applyDisplay() {
      if (!viewer || !viewer.canvas) return;
      const d = model.get("display") || {};
      const b = num(d.brightness, 1), c = num(d.contrast, 1), g = num(d.gamma, 1);
      const parts = [];
      if (Math.abs(b - 1) > 1e-6) parts.push("brightness(" + b + ")");
      if (Math.abs(c - 1) > 1e-6) parts.push("contrast(" + c + ")");
      if (Math.abs(g - 1) > 1e-6) {
        const fs = defs.querySelectorAll("feFuncR,feFuncG,feFuncB");
        for (const f of fs) f.setAttribute("exponent", String(1 / g));
        parts.push("url(#" + uid + "-gamma)");
      }
      /* Applied to the OSD canvas container ONLY -- the SVG overlay is a
         sibling, so ROI outlines and the scale bar keep their true colours. */
      viewer.canvas.style.filter = parts.join(" ");
    }

    /* ------------------------- python -> js ---------------------------- */
    model.on("change:tile_source", openTileSource);
    model.on("change:tool", applyTool);
    model.on("change:rois", renderRois);
    model.on("change:overlay_visible", renderRois);
    model.on("change:display", applyDisplay);
    model.on("change:command_seq", function () { ackSeq = num(model.get("command_seq"), 0); });
    model.on("change:mpp", function () { if (lastReport) updateScaleBar(lastReport.ds); });
    model.on("change:show_scale_bar", function () { if (lastReport) updateScaleBar(lastReport.ds); });
    model.on("change:height", function () {
      host.style.height = num(model.get("height"), 640) + "px";
      if (viewer) viewer.viewport.resize(viewer.viewport.getContainerSize(), false);
    });
    model.on("change:goto_bbox", function () {
      const bb = model.get("goto_bbox");
      if (!viewer || !opened || !bb || bb.length !== 4) return;
      ackSeq = num(model.get("command_seq"), ackSeq);
      const r = new OSD.Rect(
        num(bb[0], 0), num(bb[1], 0),
        Math.max(1, num(bb[2], 1) - num(bb[0], 0)),
        Math.max(1, num(bb[3], 1) - num(bb[1], 0)));
      viewer.viewport.fitBounds(viewer.viewport.imageToViewportRectangle(r), true);
      syncOverlay();
      report("goto_bbox", true);
    });

    /* --------------------------- keyboard ------------------------------ */
    function onKeyDown(ev) {
      if (!hovering) return;
      if (ev.key === "Escape" && draft) { draft = null; renderDraft(); }
      if (ev.code === "Space" && !spaceDown) {
        spaceDown = true;
        applyTool();
        ev.preventDefault();
      }
    }
    function onKeyUp(ev) {
      if (ev.code === "Space" && spaceDown) { spaceDown = false; applyTool(); }
    }
    root.addEventListener("mouseenter", function () { hovering = true; });
    root.addEventListener("mouseleave", function () { hovering = false; });
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);

    /* --------------------------- boot ---------------------------------- */
    openTileSource();

    /* Test hooks. Real mouse input goes through OSD's own MouseTracker; these
       exist only so the headless-Chrome test can assert the Python round trip
       deterministically without simulating pixel-perfect drags. */
    root.__hescope = {
      zoomBy: function (f) { viewer.viewport.zoomBy(f); viewer.viewport.applyConstraints(); },
      panToImage: function (x, y) {
        viewer.viewport.panTo(viewer.viewport.imageToViewportCoordinates(new OSD.Point(x, y)));
        viewer.viewport.applyConstraints();
      },
      drawRect: function (x0, y0, x1, y1) { emit("rect", [[x0, y0], [x1, y1]]); },
      state: function () {
        return { open: opened, image: [imageW, imageH], tool: model.get("tool") };
      },
    };

    return function cleanup() {
      disposed = true;
      if (reportTimer) clearTimeout(reportTimer);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("keyup", onKeyUp);
      if (viewer) { try { viewer.destroy(); } catch (e) { /* already gone */ } }
      viewer = null;
    };
  }
};
"""


@functools.lru_cache(maxsize=1)
def build_esm() -> str:
    """Return the complete anywidget ``_esm`` module: OpenSeadragon + widget.

    The wrapper is mandatory and non-obvious. ``openseadragon.min.js`` is not
    clean UMD -- it ends with ``!function(e,t){...}(this, function(){return
    OpenSeadragon})``, and ``this`` at the top level of an ES module is
    ``undefined``, so pasting the bundle in raw throws ``TypeError`` during
    evaluation and the widget renders as a silent blank box. Calling the
    wrapper with ``.call(globalThis)`` restores the ``this`` the UMD footer
    expects while still keeping ``OpenSeadragon`` a module-local binding (the
    bundle uses no ``window.OpenSeadragon``, no ``arguments.callee`` and no
    ``with``, so ES-module strict mode is safe).

    The result is served by marimo as a virtual file over plain HTTP, so its
    ~283 KB is a one-off cost the browser then caches.
    """
    return (
        f"// vendored OpenSeadragon {OSD_VERSION} (BSD-3-Clause); "
        "see hescope/static/vendor/openseadragon/THIRD_PARTY.md\n"
        "// Bundled offline on purpose: HE-Scope makes zero external requests.\n"
        "const OSD = (function () {\n"
        + osd_source_text()
        + "\n; return OpenSeadragon;\n}).call(globalThis);\n"
        + _WIDGET_JS
    )


# ---------------------------------------------------------------------------
# Coordinates: OSD viewport rectangle <-> ViewportState
# ---------------------------------------------------------------------------


def viewport_from_osd(
    bounds: dict,
    container_px: tuple[int, int],
    slide_dimensions: tuple[int, int],
) -> ViewportState:
    """Map an OpenSeadragon viewport rectangle to a :class:`ViewportState`.

    ``bounds`` is OSD's ``viewport.getBounds()`` -- ``{"x", "y", "width",
    "height"}`` in OSD *viewport* units. ``container_px`` is the OSD container
    size in CSS pixels, which becomes ``ViewportState.size``.

    *** OpenSeadragon normalizes BOTH viewport axes by the image WIDTH. ***
    The image occupies x in [0, 1] and y in [0, H0/W0], so::

        cx = (x + width / 2)  * W0
        cy = (y + height / 2) * W0      <-- W0, NOT H0

    Using ``H0`` for ``cy`` produces a viewport that drifts vertically in
    proportion to the slide's aspect ratio. Nothing raises: the resulting
    ViewportState is well-formed, so every level-0 coordinate
    ``get_current_selection()`` reports is wrong-but-plausible, passes shape
    validation, and lands in the rois table and in trained models. This is the
    single highest-consequence line in the module.

    ``downsample`` is level-0 pixels per container pixel, i.e. the same
    quantity ``render_viewport`` uses to pick a pyramid level.
    """
    w0 = float(slide_dimensions[0])
    bx = float(bounds.get("x", 0.0))
    by = float(bounds.get("y", 0.0))
    bw = float(bounds.get("width", 1.0))
    bh = float(bounds.get("height", 1.0))
    cw = max(1, int(round(float(container_px[0]))))
    ch = max(1, int(round(float(container_px[1]))))
    cx = (bx + bw / 2.0) * w0
    cy = (by + bh / 2.0) * w0
    downsample = (bw * w0) / float(cw)
    return ViewportState(
        center=(cx, cy),
        downsample=downsample if downsample > 0 else 1.0,
        size=(cw, ch),
    )


def osd_bounds_from_viewport(
    vp: ViewportState, slide_dimensions: tuple[int, int]
) -> dict:
    """Exact inverse of :func:`viewport_from_osd` (same W0-for-both-axes rule).

    Used to drive OSD from Python -- e.g. restoring a stored viewport, or
    turning ``jump_viewport_for_bbox``'s answer into an OSD rectangle.
    """
    w0 = float(slide_dimensions[0])
    vw = float(vp.size[0])
    vh = float(vp.size[1])
    bw = (vw * vp.downsample) / w0
    bh = (vh * vp.downsample) / w0
    return {
        "x": vp.center[0] / w0 - bw / 2.0,
        "y": vp.center[1] / w0 - bh / 2.0,
        "width": bw,
        "height": bh,
    }


def viewport_state_from_report(
    report: dict | None, slide_dimensions: tuple[int, int]
) -> ViewportState | None:
    """Turn the widget's ``viewport`` trait into a :class:`ViewportState`.

    Never raises: a missing, empty, malformed or non-finite report yields
    ``None``, which the caller reads as "no update, keep the current state".
    This is what a marimo consumer cell calls on ``osd_viewer.value["viewport"]``.

    Two payload shapes are accepted so the Python side is not coupled to a
    particular JS revision: the raw form this module's JS emits
    (``{"bounds": {...}, "container": [w, h]}``) and a pre-derived flat form
    (``{"cx", "cy", "ds", "w", "h"}``).
    """
    if not isinstance(report, dict) or not report:
        return None
    try:
        bounds = report.get("bounds")
        if isinstance(bounds, dict) and bounds:
            width = float(bounds.get("width", 0.0))
            # A zero/negative-width rectangle is a report from a container
            # that has not been laid out yet. viewport_from_osd sanitizes it
            # into downsample 1.0 so no caller divides by zero; here, at the
            # "is this a real update?" boundary, it must read as "no update".
            if not (math.isfinite(width) and width > 0.0):
                return None
            container = report.get("container") or (0, 0)
            vp = viewport_from_osd(
                bounds,
                (int(container[0]), int(container[1])),
                slide_dimensions,
            )
        else:
            vp = ViewportState(
                center=(float(report["cx"]), float(report["cy"])),
                downsample=float(report["ds"]),
                size=(max(1, int(report["w"])), max(1, int(report["h"]))),
            )
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if not all(
        math.isfinite(v) for v in (vp.center[0], vp.center[1], vp.downsample)
    ):
        return None
    if vp.downsample <= 0:
        return None
    return vp


def viewport_changed(
    old: ViewportState | None,
    new: ViewportState,
    *,
    center_eps_px: float = 0.5,
    ds_rel_eps: float = 0.01,
) -> bool:
    """True when ``new`` differs from ``old`` enough to be worth acting on.

    The widget already suppresses sub-pixel reports client-side; this is the
    Python-side twin, so that a report that survives the network still cannot
    trigger a thumbnail re-render (``navigator_image`` re-reads the slide) for
    a half-pixel drift. ``center_eps_px`` is in *viewport* pixels, measured at
    the new downsample.
    """
    if old is None:
        return True
    if old.size != new.size:
        return True
    ds = new.downsample if new.downsample > 0 else 1.0
    moved = math.hypot(
        new.center[0] - old.center[0], new.center[1] - old.center[1]
    ) / ds
    zoomed = abs(ds / (old.downsample or 1.0) - 1.0)
    return moved >= center_eps_px or zoomed >= ds_rel_eps


# ---------------------------------------------------------------------------
# Selections
# ---------------------------------------------------------------------------

#: The only ``kind`` values the agent contract (AGENTS.md 4.1) allows.
_ROI_KINDS = ("rect", "polygon")


def _finite_points(raw: Any) -> list[tuple[float, float]] | None:
    """Coerce ``[[x, y], ...]`` to finite float pairs, or None.

    Mirrors the R01-3 hardening in :func:`hescope.viewer.parse_plotly_selection`
    and adds a finiteness check: ``float("nan")`` does not raise, and a NaN
    corner would silently produce an ROI whose bbox is garbage.
    """
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        return None
    out: list[tuple[float, float]] = []
    try:
        for p in raw:
            # "12" would otherwise parse as the point (1.0, 2.0); every other
            # malformed shape is caught by the except clause below.
            if isinstance(p, (str, bytes)):
                return None
            x, y = float(p[0]), float(p[1])
            if not (math.isfinite(x) and math.isfinite(y)):
                return None
            out.append((x, y))
    except (TypeError, ValueError, IndexError):
        return None
    return out


def parse_osd_selection(value: dict | None) -> dict | None:
    """Normalize the widget's ``selection`` trait to level-0 ROI geometry.

    Returns ``{"kind": "rect"|"polygon", "points_level0": [(x, y), ...]}`` or
    ``None``. Never raises.

    *** VOCABULARY: this emits the FINAL kinds. ***
    :func:`hescope.viewer.parse_plotly_selection` emits the INTERMEDIATE kinds
    ``box``/``lasso``, which ``selection_to_roi`` renames to ``rect``/
    ``polygon``. There is no rename step on this path, so copying the plotly
    branch verbatim would make ``get_current_selection()`` report
    ``{"kind": "box"}`` -- an AGENTS.md 4.1 violation that raises nowhere.

    *** The output key is ``points_level0``, NOT ``points``. ***
    OSD hands back level-0 coordinates directly, so this geometry must NOT go
    through ``viewport_transform`` again. Naming the key differently from what
    :func:`hescope.viewer.selection_to_roi` reads means an accidental
    ``selection_to_roi(parse_osd_selection(v), vp)`` raises ``KeyError``
    instead of silently double-transforming every coordinate into a
    plausible-looking wrong answer.

    ``kind == "measure"`` returns None on purpose: a measurement is a UI
    readout, never an ROI, and it must not reach the agent contract. Use
    :func:`parse_osd_measure` for it.
    """
    if not isinstance(value, dict) or not value:
        return None
    kind = value.get("kind")
    if kind not in _ROI_KINDS:
        return None
    pts = _finite_points(value.get("points_level0"))
    if pts is None:
        return None
    if kind == "rect":
        if len(pts) < 2:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        corners = [(min(xs), min(ys)), (max(xs), max(ys))]
        return {"kind": "rect", "points_level0": corners}
    if len(pts) < 3:
        return None
    return {"kind": "polygon", "points_level0": pts}


def parse_osd_measure(
    value: dict | None,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Level-0 corners of a ``measure`` drag, or None.

    Kept separate from :func:`parse_osd_selection` precisely because measure
    geometry must never become an ROI. Feed the result straight to
    :func:`hescope.measure.measure_box`.
    """
    if not isinstance(value, dict) or value.get("kind") != "measure":
        return None
    pts = _finite_points(value.get("points_level0"))
    if pts is None or len(pts) < 2:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return ((min(xs), min(ys)), (max(xs), max(ys)))


def osd_selection_to_roi(selection: dict, as_circle: bool = False) -> ROI:
    """Parsed OSD selection -> :class:`ROI`.

    Twin of :func:`hescope.viewer.selection_to_roi` MINUS the
    ``viewport_transform`` call: the points are already level-0.

    ``as_circle`` mirrors the plotly path (radius = half the shorter box side,
    stored as centre + a point on the edge) so the "draw a box, get a circle"
    checkbox behaves identically on both surfaces.
    """
    pts = [(float(x), float(y)) for x, y in selection["points_level0"]]
    if selection["kind"] == "rect":
        (x0, y0), (x1, y1) = pts[0], pts[1]
        if as_circle:
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            r = min(x1 - x0, y1 - y0) / 2.0
            return ROI(kind="circle", points=((cx, cy), (cx + r, cy)))
        return ROI(kind="rect", points=((x0, y0), (x1, y1)))
    return ROI(kind="polygon", points=tuple(pts))


def raw_osd_selection(value: Any) -> dict | None:
    """Dig the ``selection`` payload out of whatever the caller has.

    Accepts the widget's value dict (``mo.ui.anywidget(...).value``), the
    ``mo.ui.anywidget`` element itself, a bare :class:`HEScopeViewer`, or the
    selection dict directly. Returns None when there is nothing usable.

    (The plotly path needs :func:`hescope.viewer.raw_plotly_selection` for the
    same reason: what an app cell holds is an element, not a payload.)
    """
    if value is None:
        return None
    if isinstance(value, dict):
        sel = value.get("selection")
        if isinstance(sel, dict) and sel:
            return sel
        if "kind" in value and "points_level0" in value:
            return value
        return None
    sel = getattr(value, "selection", None)
    if isinstance(sel, dict) and sel:
        return sel
    inner = getattr(value, "value", None)
    if isinstance(inner, dict):
        return raw_osd_selection(inner)
    return None


def _selection_dict_from_roi(
    source: Any, roi: ROI | None, downsample: float
) -> dict | None:
    """The 7-key agent contract dict (AGENTS.md 4.1) for ``roi``.

    Delegates to ``hescope.viewer.selection_dict_from_roi`` when that exists,
    so there is ONE copy of the contract shape in the codebase. The inline
    fallback reproduces the pre-extraction body of
    :func:`hescope.viewer.current_selection` verbatim and exists only so this
    module keeps working against a viewer.py that has not been refactored yet
    -- ``test_osd_selection_matches_plotly_contract`` compares the two outputs
    byte-for-byte, so the fallback cannot drift unnoticed.
    """
    if source is None or roi is None:
        return None
    try:
        from .viewer import selection_dict_from_roi as _impl  # type: ignore[attr-defined]
    except ImportError:  # pragma: no cover - viewer.py always imports
        _impl = None
    if _impl is not None:
        return _impl(source, roi, downsample)
    x0, y0, x1, y1 = roi.bbox()
    return {
        "kind": roi.kind,
        "points_level0": [[float(x), float(y)] for x, y in roi.points],
        "bbox_level0": [x0, y0, x1, y1],
        "viewport_downsample": float(downsample),
        "slide": source.name,
        "slide_dimensions": [int(source.dimensions[0]), int(source.dimensions[1])],
        "mpp": source.mpp,
    }


def osd_current_selection(
    source: Any, vp: ViewportState, widget_value: Any
) -> dict | None:
    """OSD twin of :func:`hescope.viewer.current_selection`.

    Same signature, same return shape, same ``None`` semantics -- so the
    ``get_current_selection`` tool's getter can be swapped from one to the
    other without the tool factory, the sentinel or AGENTS.md changing.

    ``vp`` supplies ``viewport_downsample`` only; the geometry comes from the
    widget already in level-0 coordinates.
    """
    # `vp` is guarded alongside `source`: without a viewport there is no
    # selection to report, and the plotly counterpart returns None rather
    # than raising here. The tool factory wraps this, but the documented
    # promise above is "same None semantics", so honour it directly.
    if source is None or vp is None:
        return None
    sel = parse_osd_selection(raw_osd_selection(widget_value))
    if sel is None:
        return None
    return _selection_dict_from_roi(
        source, osd_selection_to_roi(sel), vp.downsample
    )


def parse_clicked_roi(value: Any) -> int | None:
    """Index encoded in the widget's ``clicked_roi`` trait, or None.

    The trait carries ``"<index>#<seq>"``: without the sequence suffix,
    clicking the same outline twice would not change the trait and the second
    click would never reach Python.
    """
    if not isinstance(value, str) or not value:
        return None
    head = value.split("#", 1)[0]
    try:
        idx = int(head)
    except ValueError:
        return None
    return idx if idx >= 0 else None


def rois_to_payload(
    rois: Iterable[ROI], selected_index: int | None = None
) -> list[dict]:
    """ROIs -> the widget's ``rois`` trait (level-0 coordinates, JSON-safe).

    No projection happens here: the SVG overlay draws in level-0 space and OSD
    applies the transform, which is why the outlines stay 2 px crisp at 40x.
    """
    out: list[dict] = []
    for idx, roi in enumerate(rois):
        out.append(
            {
                "kind": roi.kind,
                "points": [[float(x), float(y)] for x, y in roi.points],
                "selected": selected_index is not None and idx == selected_index,
            }
        )
    return out


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _viewer_class() -> type:
    """Build the anywidget subclass on first use.

    anywidget is imported lazily so that every pure function above stays
    importable (and testable) in an environment that only has the declared
    dependencies -- the class is the only part that actually needs a comm.
    """
    import anywidget
    import traitlets

    class HEScopeViewer(anywidget.AnyWidget):
        """OpenSeadragon viewing surface with a level-0 ROI overlay.

        Traits split cleanly in two. Python -> JS traits are COMMANDS applied
        to an already-running widget; they must never cause the widget to be
        rebuilt, because both marimo teardown paths destroy the comm silently
        when the defining cell re-runs (symptom: "my zoom keeps resetting").
        JS -> Python traits are REPORTS.
        """

        _esm = build_esm()

        # ---- Python -> JS (commands) ----
        tile_source = traitlets.Dict({}).tag(sync=True)
        tool = traitlets.Unicode("pan").tag(sync=True)  # pan|rect|lasso|circle|measure
        rois = traitlets.List([]).tag(sync=True)
        overlay_visible = traitlets.Bool(True).tag(sync=True)
        display = traitlets.Dict({}).tag(sync=True)  # brightness/contrast/gamma
        goto_bbox = traitlets.List([]).tag(sync=True)  # [x0,y0,x1,y1] level-0, or []
        mpp = traitlets.Float(0.0).tag(sync=True)  # 0.0 == unknown
        show_scale_bar = traitlets.Bool(True).tag(sync=True)
        height = traitlets.Int(640).tag(sync=True)
        command_seq = traitlets.Int(0).tag(sync=True)

        # ---- JS -> Python (reports) ----
        viewport = traitlets.Dict({}).tag(sync=True)
        selection = traitlets.Dict({}).tag(sync=True)
        clicked_roi = traitlets.Unicode("").tag(sync=True)

        # -- convenience, so app.py never re-implements the mapping ----------
        def viewport_state(
            self, slide_dimensions: tuple[int, int]
        ) -> ViewportState | None:
            """Current viewport as a :class:`ViewportState`, or None."""
            return viewport_state_from_report(self.viewport, slide_dimensions)

        def goto(self, bbox: Sequence[float]) -> None:
            """Move the camera to ``bbox`` (level-0 x0, y0, x1, y1).

            Bumps ``command_seq`` so the report that comes back carries an
            ``ack_seq`` the consumer can use to tell its own commanded move
            from a user gesture (otherwise the two race and the view fights
            the user).
            """
            self.command_seq = int(self.command_seq) + 1
            self.goto_bbox = [float(v) for v in bbox]

        def set_rois(
            self, roi_list: Iterable[ROI], selected_index: int | None = None
        ) -> None:
            """Replace the overlay contents (a command, not a rebuild)."""
            self.rois = rois_to_payload(roi_list, selected_index)

    return HEScopeViewer


def __getattr__(name: str):  # PEP 562
    """Expose ``HEScopeViewer`` without importing anywidget at module import."""
    if name == "HEScopeViewer":
        return _viewer_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def make_viewer(
    source: Any,
    tile_source: dict,
    *,
    tool: str = "pan",
    height: int = 640,
    rois: Iterable[ROI] = (),
    selected_index: int | None = None,
) -> Any:
    """Construct a viewer for ``source`` showing ``tile_source``.

    ``tile_source`` is the OSD descriptor produced by the tile server
    (``hescope.tileserver.serve_slide(...)["tile_source"]``); an empty dict
    renders the "no slide open" placeholder rather than failing.

    The caller is expected to wrap the result in ``mo.ui.anywidget`` and to
    keep the constructing cell dependent on slide identity ONLY -- every other
    control must be a trait set on the live widget.
    """
    cls = _viewer_class()
    return cls(
        tile_source=dict(tile_source or {}),
        tool=tool,
        height=int(height),
        rois=rois_to_payload(rois, selected_index),
        mpp=float(getattr(source, "mpp", None) or 0.0),
    )
