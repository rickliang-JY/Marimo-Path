"""Every action must say what happened, and no message may outlive its subject.

Two rules over `app.py`, both aimed at the class six rounds keep re-finding
(bugs/SUMMARY.md class 1, "failure rendered as success"), stated structurally
so a NEW handler or a NEW channel is caught rather than only the instances
found so far:

  * **R07-4 — a slide boundary resets every message channel.**
    ``_open_slide_path`` already cleared five of six and simply missed
    ``set_db_msg``, which is the channel carrying every success string in the
    app. Slide A's green "Sent ROI to agent: rect bbox=[1000, 800, 1600, 1300]
    — the agent reads it with get_latest_selection()" stood, verbatim, over
    slide B. The text is actionable and true of the jsonl history, so an agent
    that follows the callout analyses slide A's region while the user looks at
    slide B.

  * **R07-5 — a click that can fail must have an error path.**
    marimo swallows an exception raised inside an ``on_click`` callback (it
    logs to the KERNEL's stderr — the terminal running ``marimo edit``, not
    the browser — and returns normally; ``marimo/_plugins/ui/_impl/input.py``,
    "on_click handler for button ... raised an Exception"). So a handler with
    no try/except is a click that does nothing and says nothing: with the
    previous slide still rendered, that is byte-identical to successfully
    re-opening the same slide. ``_on_open_clicked`` / ``_on_demo_clicked`` /
    ``_on_upload`` were the only three in the file without one, on the panel
    whose own comment claims to be hardened.

The lint below is deliberately a lint and not three patches: this class has
now produced five findings across five rounds, and the cheap way to stop the
sixth is to make an unguarded handler fail the suite.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from PIL import Image

from hescope.wsi.demo import generate_demo_slide

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

#: Handlers that provably cannot raise, each with the reason. A handler earns
#: a place here only by calling nothing but marimo state setters/getters --
#: anything that touches a file, the database, the network or geometry does
#: not qualify. Keeping the list short is the point.
_CANNOT_FAIL = {
    "_on_clear_rois": "two mo.state setters and nothing else",
}


def _handlers() -> dict[str, ast.FunctionDef]:
    """Every click/change handler defined in app.py.

    Two ways in, because app.py uses both: passed by name to ``on_click=`` /
    ``on_change=`` (including through ``tcga_panel.make_filter_controls``,
    which is why the ``_on_*`` naming convention is also honoured).

    Known gap, stated rather than papered over: closures produced by a factory
    (``on_click=_make_view(_i)``) are not reachable by name, so the per-ROI
    View/Delete buttons are outside this rule.
    """
    wanted: set[str] = set()
    for node in ast.walk(TREE):
        if (
            isinstance(node, ast.keyword)
            and node.arg in ("on_click", "on_change")
            and isinstance(node.value, ast.Name)
        ):
            wanted.add(node.value.id)
    out: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and (
            node.name in wanted or node.name.startswith("_on_")
        ):
            out[node.name] = node
    return out


def test_every_click_handler_has_an_error_path():
    unguarded = sorted(
        name
        for name, fn in _handlers().items()
        if name not in _CANNOT_FAIL
        and not any(isinstance(n, ast.Try) for n in ast.walk(fn))
    )
    assert unguarded == [], (
        "these on_click/on_change handlers can raise, and marimo swallows the "
        "exception into the kernel's stderr rather than the browser -- so the "
        "click changes nothing on screen and says nothing, which is "
        "indistinguishable from success. Wrap the body and write the failure "
        f"to a message channel (set_db_msg/set_tcga_msg/...): {unguarded}"
    )


def test_the_handlers_we_expect_are_actually_being_checked():
    """Guard the guard: a rename that empties `_handlers()` must not read as
    a pass."""
    found = _handlers()
    for expected in (
        "_on_open_clicked",
        "_on_demo_clicked",
        "_on_upload",
        "_on_browse_catalog",
        "_on_run_heatmap",
        "_on_save",
    ):
        assert expected in found, f"{expected} is no longer being linted"


# --- R07-4: no message survives the slide it describes ----------------------


def _loader_cell() -> tuple[ast.FunctionDef, str]:
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
            continue
        src = ast.get_source_segment(SOURCE, node)
        if src is not None and "def _open_slide_path(" in src:
            return node, src
    raise AssertionError("no @app.cell in app.py defines _open_slide_path")


def test_opening_a_slide_resets_every_message_channel():
    """Structural, so a channel added LATER is caught too.

    Enumerated from the loader cell's own signature rather than hardcoded:
    marimo passes each referenced setter in as a parameter, so `set_*_msg` in
    the parameter list IS the set of message channels this function can write,
    and every one of them has to be reset at a slide boundary.
    """
    cell, src = _loader_cell()
    channels = sorted(
        a.arg
        for a in cell.args.args
        if a.arg.startswith("set_") and a.arg.endswith("_msg")
    )
    assert channels, "the loader cell no longer takes any message channel"
    missing = [c for c in channels if f"{c}(None)" not in src]
    assert missing == [], (
        "_open_slide_path writes to these message channels but does not reset "
        "them when the slide changes, so a message about slide A stands over "
        f"slide B: {missing}"
    )


@pytest.fixture(scope="module")
def notebook_defs():
    import app as appmod

    _outputs, defs = appmod.app.run()
    return defs


@pytest.fixture(scope="module")
def slide_b(tmp_path_factory):
    path = tmp_path_factory.mktemp("msg_slide_b") / "other_slide.png"
    Image.new("RGB", (600, 400), (250, 240, 245)).save(path)
    return str(path)


def test_opening_a_path_that_does_not_exist_says_so(notebook_defs, tmp_path):
    """R07-5, behaviourally, through app.py's REAL "Open" button.

    A typo in a path box is an everyday input. Before the fix the click
    returned normally, ``button.value`` was None, no message channel moved and
    the previous slide was still on screen -- while the FileNotFoundError went
    to the kernel's stderr, i.e. the terminal running ``marimo edit``, which
    the user is not looking at.
    """
    defs = notebook_defs
    defs["open_slide_path"](str(generate_demo_slide("assets/demo_he.png")))
    _before = defs["get_source"]()
    defs["set_db_msg"](None)
    _missing = str(tmp_path / "slides" / "biopsy_2026_03.svs")
    defs["path_input"]._update(_missing)

    defs["open_button"]._update(object())  # the real click

    assert defs["get_source"]() is _before, "the failed open changed the slide"
    msg = defs["get_db_msg"]()
    assert msg is not None, (
        "an 'Open' that failed wrote nothing to the UI at all: no callout, no "
        "message, and the previous slide still rendered -- byte-identical to "
        "successfully re-opening the same slide"
    )
    kind, text = msg
    assert kind == "danger" and "biopsy_2026_03.svs" in text, msg


def test_a_stale_db_message_does_not_survive_a_slide_change(notebook_defs, slide_b):
    """The behavioural half, through app.py's REAL ``open_slide_path``.

    ``set_db_msg`` carries "Sent ROI to agent: ...", "Saved annotation for ROI
    N.", "Deleted ROI [i]", "Viewer centered on ROI [i]." and the two failure
    notes ``_open_slide_path`` itself writes. R04-2 cleared the ROI and the
    payload a stale message describes and left the message standing, so the
    user-visible half of R04-2 was still there.
    """
    defs = notebook_defs
    defs["open_slide_path"](str(generate_demo_slide("assets/demo_he.png")))
    defs["set_db_msg"](
        (
            "success",
            "Sent ROI to agent: rect bbox=[1000, 800, 1600, 1300] — the agent "
            "reads it with get_latest_selection().",
        )
    )

    defs["open_slide_path"](slide_b)

    assert defs["get_source"]().dimensions == (600, 400)
    assert defs["get_db_msg"]() is None, (
        "slide A's message is still on screen over slide B. It names slide A's "
        "rectangle and tells the reader how to fetch it, so an agent that "
        "follows the callout analyses the wrong slide: "
        f"{defs['get_db_msg']()!r}"
    )
