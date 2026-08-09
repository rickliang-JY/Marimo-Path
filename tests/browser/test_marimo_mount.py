"""Does the viewer widget actually mount inside a REAL marimo page?

This is the test whose absence caused a shipped-broken viewer. Everything
else passed -- the suite, `python app.py`, even 7 browser tests -- because
they all exercised the widget on a standalone HTML page or through
`app.run()`. None of them loaded the marimo SPA, and a widget that
constructs fine in Python can still fail to mount in the browser with no
server-side signal at all. With OpenSeadragon driving, the plotly fallback is
suppressed, so that failure presents as a dead viewing area.

So this drives the real app in headless Chrome over CDP and asserts the
things that were assumed before:

  * the anywidget custom element is in the page,
  * OpenSeadragon initialised (window.OpenSeadragon is a function),
  * a canvas exists,
  * tiles were actually fetched from the loopback tile server,
  * no JS error was raised.

Run with:  HESCOPE_BROWSER_TESTS=1 pytest -q tests/browser/test_marimo_mount.py

Two environment facts this test depends on, both deliberate:
  * `marimo run` executes every cell on load, which sidesteps a user's
    `auto_instantiate = false` config;
  * app.py auto-opens the demo slide, so a slide exists without anyone
    clicking. Without a slide there is no tile source and no widget, and
    marimo keeps its DOM behind closed shadow roots -- `querySelectorAll('*')`
    returns 3 elements against ~2.5k characters of rendered text -- so a
    script cannot reach in and click the button itself.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("HESCOPE_BROWSER_TESTS", "") not in ("1", "true", "yes"),
    reason="browser test: set HESCOPE_BROWSER_TESTS=1 (needs Chrome)",
)

REPO = pathlib.Path(__file__).resolve().parents[2]
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _chrome() -> str:
    for candidate in _CHROME_CANDIDATES:
        if pathlib.Path(candidate).exists():
            return candidate
    exe = shutil.which("chrome") or shutil.which("google-chrome")
    if not exe:
        pytest.skip("Google Chrome not found")
    return exe


@pytest.fixture(scope="module")
def page_report() -> dict:
    """Load the real app in headless Chrome; return what the page did."""
    import websockets.sync.client as wsc

    chrome = _chrome()
    app_port, dbg_port = _free_port(), _free_port()
    env = dict(os.environ)
    env.pop("HESCOPE_DISABLE_OSD", None)
    env["PATH"] = str(REPO / ".venv" / "Scripts") + os.pathsep + env["PATH"]

    marimo_exe = REPO / ".venv" / "Scripts" / "marimo.exe"
    if not marimo_exe.exists():
        pytest.skip("marimo executable not found in .venv")

    app = subprocess.Popen(
        [str(marimo_exe), "run", "app.py", "--port", str(app_port),
         "--host", "127.0.0.1", "--headless"],
        cwd=str(REPO), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    profile = tempfile.mkdtemp(prefix="hescope_mount_")
    browser = None
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{app_port}/", timeout=1)
                break
            except Exception:
                time.sleep(1)
        else:
            pytest.fail("the marimo app never started")

        browser = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", f"--user-data-dir={profile}",
             "--no-first-run", "--no-default-browser-check", "--window-size=1400,900",
             f"--remote-debugging-port={dbg_port}", f"http://127.0.0.1:{app_port}/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        target = None
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{dbg_port}/json", timeout=1))
                target = next((t for t in tabs if t.get("type") == "page"), None)
                if target:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not target:
            pytest.fail("Chrome exposed no CDP target")

        ws = wsc.connect(target["webSocketDebuggerUrl"], max_size=None)
        counter = 0

        def send(method: str, params: dict | None = None) -> int:
            nonlocal counter
            counter += 1
            ws.send(json.dumps({"id": counter, "method": method,
                                "params": params or {}}))
            return counter

        for domain in ("Runtime", "Log", "Network", "Page"):
            send(f"{domain}.enable")

        errors: list[str] = []
        tiles: list[str] = []
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                msg = json.loads(ws.recv(timeout=2))
            except TimeoutError:
                continue
            except Exception:
                break
            method, params = msg.get("method", ""), msg.get("params", {})
            if method == "Runtime.exceptionThrown":
                errors.append(
                    str(params.get("exceptionDetails", {}).get("text"))[:200])
            elif (method == "Log.entryAdded"
                  and params.get("entry", {}).get("level") == "error"):
                errors.append(str(params["entry"].get("text"))[:200])
            elif method == "Network.requestWillBeSent":
                url = params.get("request", {}).get("url", "")
                if "_files/" in url or url.endswith(".dzi"):
                    tiles.append(url)

        probe_id = send("Runtime.evaluate", {
            "expression": """(() => {
                const walk = (root, out) => {
                  root.querySelectorAll('*').forEach(el => {
                    out.push(el.tagName.toLowerCase());
                    if (el.shadowRoot) walk(el.shadowRoot, out);
                  });
                  return out;
                };
                const tags = walk(document, []);
                return JSON.stringify({
                  anywidget: tags.filter(t => t.includes('anywidget')).length,
                  canvas: tags.filter(t => t === 'canvas').length,
                  osd: typeof window.OpenSeadragon,
                  text: document.body.innerText.slice(0, 400),
                });
            })()""", "returnByValue": True})
        dom = None
        stop = time.time() + 10
        while time.time() < stop:
            try:
                msg = json.loads(ws.recv(timeout=2))
            except TimeoutError:
                continue
            except Exception:
                break
            if msg.get("id") == probe_id and "result" in msg:
                dom = json.loads(msg["result"]["result"]["value"])
                break
        if dom is None:
            pytest.fail("the page never answered the DOM probe")

        return {"dom": dom, "tiles": tiles, "errors": errors}
    finally:
        for proc in (browser, app):
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass


def test_a_slide_is_open_without_anyone_clicking(page_report):
    assert "demo_he.png" in page_report["dom"]["text"], (
        "the app did not auto-open a slide, so no viewer could be built"
    )


def test_the_widget_element_mounted(page_report):
    assert page_report["dom"]["anywidget"] >= 1, (
        "no anywidget custom element in the page: the widget never mounted, "
        "which is exactly the failure that has no server-side signal"
    )


def test_openseadragon_initialised(page_report):
    assert page_report["dom"]["osd"] == "function", (
        f"window.OpenSeadragon is {page_report['dom']['osd']!r}; the vendored "
        "bundle did not evaluate (check the UMD-as-ES-module wrapper)"
    )
    assert page_report["dom"]["canvas"] >= 1, "OpenSeadragon drew no canvas"


def test_tiles_were_actually_fetched(page_report):
    assert len(page_report["tiles"]) >= 4, (
        f"only {len(page_report['tiles'])} tile requests; the viewer mounted "
        "but is not pulling image data from the loopback tile server"
    )


def test_no_javascript_errors(page_report):
    assert not page_report["errors"], page_report["errors"][:5]
