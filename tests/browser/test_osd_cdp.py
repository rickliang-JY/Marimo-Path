"""Real-browser proof for the OpenSeadragon widget. SKIPPED BY DEFAULT.

Run with::

    HESCOPE_BROWSER_TESTS=1 pytest -q tests/browser/test_osd_cdp.py

It needs Google Chrome on the machine and is therefore not part of the default
suite; ``tests/test_osdviewer.py`` covers everything that can be proven without
one. What only a browser can prove, and what this file exists for:

* the UMD wrapper in :func:`hescope.osdviewer.build_esm` is enough to make
  ``openseadragon.min.js`` evaluate as an ES module (the failure mode is a
  ``TypeError`` on ``this`` and a SILENT blank widget);
* OpenSeadragon boots, tiles are drawn, and the ``open`` event fires;
* the SVG overlay receives a level-0 -> screen transform;
* Python -> JS ``goto_bbox`` lands on the requested rectangle and the viewport
  report that comes back parses through ``viewport_state_from_report``;
* JS -> Python selection payloads satisfy ``parse_osd_selection``;
* the whole thing runs with **every non-loopback host unresolvable**, i.e. it
  is genuinely offline.

Chrome is driven with ``--dump-dom`` rather than the DevTools protocol so the
test needs nothing beyond the standard library; the page reports its findings
as JSON inside a ``<pre>``.

NOTE for anyone extending this into a full marimo round trip: this repo's
``~/.config/marimo/marimo.toml`` sets ``auto_instantiate = false``, so cells
sit in ``needs-run`` and the widget looks dead. Drop a scratch ``.marimo.toml``
with ``auto_instantiate = true`` next to the notebook first.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import pathlib
import re
import shutil
import socketserver
import subprocess
import tempfile
import threading

import pytest

from hescope.osdviewer import build_esm, parse_osd_selection, viewport_state_from_report

pytestmark = pytest.mark.skipif(
    os.environ.get("HESCOPE_BROWSER_TESTS", "") not in ("1", "true", "yes"),
    reason="browser test: set HESCOPE_BROWSER_TESTS=1 (needs Chrome)",
)

_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

# Slide is 4000x1000 on purpose: OSD normalizes BOTH viewport axes by image
# WIDTH, and only a non-square image makes a W0/H0 mix-up visible.
IMAGE_W, IMAGE_H = 4000, 1000
GOTO_BBOX = [2000, 300, 2400, 600]
DRAW_RECT = [2100, 350, 2300, 500]

PAGE = r"""<!doctype html>
<meta charset="utf-8">
<body>
<div id="mount"></div>
<pre id="result">PENDING</pre>
<script type="module">
import widget from "./widget.mjs";

const out = {errors: []};
window.addEventListener("error", e => out.errors.push(String(e.message)));
window.addEventListener("unhandledrejection", e => out.errors.push("rej:" + String(e.reason)));

/* Minimal stand-in for the anywidget model: get/set/on/save_changes is the
   whole surface the widget uses, so the JS under test is the real one. */
function makeModel(state) {
  const listeners = {};
  return {
    _state: state,
    get(k) { return this._state[k]; },
    set(k, v) { this._state[k] = v; },
    save_changes() { out.saves = (out.saves || 0) + 1; },
    on(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); },
    off(evt, fn) { const a = listeners[evt] || []; const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); },
    _fire(evt) { (listeners[evt] || []).forEach(f => f()); },
    send() {},
  };
}

const W = __IMAGE_W__, H = __IMAGE_H__, TS = 256;
/* Tiles are generated client-side as data: URLs -- zero network, so a failure
   here cannot be a flaky fetch. */
const tileSource = {
  width: W, height: H, tileSize: TS, tileOverlap: 0,
  minLevel: 0, maxLevel: Math.ceil(Math.log2(Math.max(W, H))),
  getTileUrl(level, x, y) {
    const c = document.createElement("canvas");
    c.width = TS; c.height = TS;
    const g = c.getContext("2d");
    g.fillStyle = "hsl(" + ((level * 47 + x * 13 + y * 29) % 360) + ",60%,60%)";
    g.fillRect(0, 0, TS, TS);
    return c.toDataURL("image/png");
  },
};

const model = makeModel({
  tile_source: {}, tool: "pan", rois: [], overlay_visible: true,
  display: {}, goto_bbox: [], mpp: 0.25, show_scale_bar: true,
  height: 400, command_seq: 0, viewport: {}, selection: {}, clicked_roi: "",
});

const el = document.getElementById("mount");
try { widget.render({ model, el }); out.rendered = true; }
catch (e) { out.errors.push("render:" + (e && e.stack ? e.stack : String(e))); }

const root = el.querySelector("[data-hescope-osd]");
out.root_found = !!root;
const wait = ms => new Promise(r => setTimeout(r, ms));

function finish() {
  document.getElementById("result").textContent = JSON.stringify(out);
  document.title = "DONE";
}

(async () => {
  model.set("tile_source", tileSource); model._fire("change:tile_source");
  for (let i = 0; i < 100 && root.getAttribute("data-hescope-open") !== "yes"; i++) await wait(100);
  out.opened = root.getAttribute("data-hescope-open");
  out.canvas_count = root.querySelectorAll("canvas").length;
  out.viewport_after_open = JSON.parse(JSON.stringify(model.get("viewport")));

  model.set("rois", [
    { kind: "rect", points: [[100, 100], [900, 400]], selected: false },
    { kind: "polygon", points: [[1200, 200], [1600, 250], [1400, 600]], selected: true },
    { kind: "circle", points: [[3000, 500], [3200, 500]], selected: false },
  ]);
  model._fire("change:rois");
  await wait(200);
  const shapes = root.querySelectorAll("svg g g *");
  out.overlay_shape_count = shapes.length;
  out.overlay_kinds = Array.from(shapes).map(n => n.tagName.toLowerCase());
  out.overlay_stroke_effect = shapes.length ? shapes[0].getAttribute("vector-effect") : null;
  const g = root.querySelector("svg g");
  out.overlay_transform = g ? g.getAttribute("transform") : null;

  model.set("command_seq", 7); model._fire("change:command_seq");
  model.set("goto_bbox", __GOTO__); model._fire("change:goto_bbox");
  await wait(1500);
  out.viewport_after_goto = JSON.parse(JSON.stringify(model.get("viewport")));

  model.set("tool", "rect"); model._fire("change:tool");
  root.__hescope.drawRect.apply(null, __DRAW__);
  await wait(200);
  out.selection = JSON.parse(JSON.stringify(model.get("selection")));

  model.set("display", { brightness: 1.2, contrast: 0.9, gamma: 2.0 });
  model._fire("change:display");
  await wait(100);
  const cv = root.querySelector(".openseadragon-canvas");
  out.canvas_filter = cv ? cv.style.filter : "no-canvas-div";
  out.overlay_filter = root.querySelector("svg").style.filter || "";

  finish();
})().catch(e => { out.errors.push("driver:" + String((e && e.stack) || e)); finish(); });
</script>
</body>
"""


def _chrome() -> str:
    for c in _CHROME_CANDIDATES:
        if pathlib.Path(c).exists():
            return c
    exe = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")
    if exe:
        return exe
    pytest.skip("Google Chrome not found")


@pytest.fixture(scope="module")
def browser_result() -> dict:
    """Render the widget once in headless Chrome and return its JSON report."""
    chrome = _chrome()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="hescope_osd_"))
    page = (
        PAGE.replace("__IMAGE_W__", str(IMAGE_W))
        .replace("__IMAGE_H__", str(IMAGE_H))
        .replace("__GOTO__", json.dumps(GOTO_BBOX))
        .replace("__DRAW__", json.dumps(DRAW_RECT))
    )
    (tmp / "widget.mjs").write_text(build_esm(), encoding="utf-8")
    (tmp / "index.html").write_text(page, encoding="utf-8")

    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D401 - keep pytest output clean
            pass

    class _Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = _Server(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=str(tmp))
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    profile = tempfile.mkdtemp(prefix="hescope_chrome_")
    try:
        proc = subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1000,700",
                "--virtual-time-budget=20000",
                # THE OFFLINE PROOF: every host but loopback fails to resolve,
                # so a CDN dependency could not possibly load.
                "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
                "--dump-dom",
                f"http://127.0.0.1:{port}/index.html",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        srv.shutdown()
        shutil.rmtree(profile, ignore_errors=True)

    m = re.search(r'<pre id="result">(.*?)</pre>', proc.stdout, re.S)
    assert m, f"no result element; chrome stderr tail:\n{proc.stderr[-2000:]}"
    payload = m.group(1)
    assert payload != "PENDING", "the page never finished driving the widget"
    payload = payload.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<")
    result = json.loads(payload)
    shutil.rmtree(tmp, ignore_errors=True)
    return result


def test_widget_renders_without_javascript_errors(browser_result):
    assert browser_result["errors"] == []
    assert browser_result["rendered"] is True
    assert browser_result["root_found"] is True


def test_openseadragon_opens_and_draws(browser_result):
    # If the UMD wrapper were wrong this would be "null" with a TypeError.
    assert browser_result["opened"] == "yes"
    assert browser_result["canvas_count"] >= 1


def test_open_report_places_centre_using_image_width(browser_result):
    """The whole-image view of a 4:1 slide must report centre y = H0/2."""
    vp = viewport_state_from_report(
        browser_result["viewport_after_open"], (IMAGE_W, IMAGE_H)
    )
    assert vp is not None
    assert vp.center[0] == pytest.approx(IMAGE_W / 2, abs=1.0)
    assert vp.center[1] == pytest.approx(IMAGE_H / 2, abs=1.0)


def test_goto_bbox_lands_on_the_requested_rectangle(browser_result):
    report = browser_result["viewport_after_goto"]
    assert report["ack_seq"] == 7
    vp = viewport_state_from_report(report, (IMAGE_W, IMAGE_H))
    assert vp is not None
    assert vp.center[0] == pytest.approx((GOTO_BBOX[0] + GOTO_BBOX[2]) / 2, abs=1.0)
    assert vp.center[1] == pytest.approx((GOTO_BBOX[1] + GOTO_BBOX[3]) / 2, abs=1.0)
    # the bbox must fit inside the view, i.e. the view is at least as wide
    span = vp.downsample * vp.size[0]
    assert span >= (GOTO_BBOX[2] - GOTO_BBOX[0]) - 1e-6


def test_selection_payload_parses_to_the_contract(browser_result):
    sel = parse_osd_selection(browser_result["selection"])
    assert sel == {
        "kind": "rect",
        "points_level0": [
            (float(DRAW_RECT[0]), float(DRAW_RECT[1])),
            (float(DRAW_RECT[2]), float(DRAW_RECT[3])),
        ],
    }


def test_overlay_is_drawn_in_level0_space(browser_result):
    assert browser_result["overlay_shape_count"] == 3
    assert browser_result["overlay_kinds"] == ["rect", "polygon", "circle"]
    # non-scaling-stroke is what keeps an outline 2 px wide at 40x
    assert browser_result["overlay_stroke_effect"] == "non-scaling-stroke"
    assert browser_result["overlay_transform"].startswith("translate(")
    assert "scale(" in browser_result["overlay_transform"]


def test_display_filter_touches_the_canvas_only(browser_result):
    f = browser_result["canvas_filter"]
    assert "brightness(1.2)" in f and "contrast(0.9)" in f and "gamma" in f
    # tinting the overlay would recolour the ROI outlines and the scale bar
    assert browser_result["overlay_filter"] == ""
