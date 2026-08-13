"""A heatmap sweep may only be published onto the slide it measured.

R04-3 closed "slide A's heatmap blended onto slide B's thumbnail" by clearing
``hm_result`` inside ``_open_slide_path``. R04-6 then moved the sweep onto a
worker thread — and a worker writes ``hm_job["result"]``/``hm_job["msg"]``
*after* that clear, so the defect came straight back with a green success
callout on top of it (R07-1):

    slide A open, "Run heatmap" clicked, sweep still running
    -> user opens slide B (hm_result cleared, R04-3's fix fires)
    -> slide A's worker finishes and writes its grid + "96 cells measured"
    -> the 1s ticker publishes both, unconditionally
    -> the Navigator blends slide A's 8x12 grid over slide B's thumbnail,
       rescaled by grid_coverage from slide B's dimensions

Nothing raises anywhere: the Navigator's ``except Exception: pass  #
stale/mismatched grid`` cannot fire, because a grid from another slide does
not raise — ``render_heatmap`` simply resizes it. And R04-3's own regression
test stays green throughout, because it pins the synchronous path only.

The durable fix is provenance carried WITH the result and checked at PUBLISH
time, not another clear at OPEN time — which is precisely what the worker
thread walked around. So the tests below drive the real button and then run
app.py's own ticker cell, which is where the check has to live.
"""

from __future__ import annotations

import ast
import pathlib

import marimo as mo
import pytest
from PIL import Image

from hescope.wsi.demo import generate_demo_slide

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _cell(marker: str):
    """The @app.cell function whose source contains ``marker``, as a callable."""
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
            continue
        src = ast.get_source_segment(SOURCE, node)
        if src is None or marker not in src:
            continue
        ns: dict = {}
        exec(compile("\n" * (node.lineno - 1) + src, str(APP), "exec"), ns)
        return ns[node.name], [a.arg for a in node.args.args]
    raise AssertionError(f"no @app.cell in app.py contains {marker!r}")


@pytest.fixture(scope="module")
def notebook_defs():
    import app as appmod

    _outputs, defs = appmod.app.run()
    return defs


def _private_demo_slide(path) -> str:
    """Write a demo-shaped slide at ``path`` that is content-DISTINCT from
    the shared ``assets/demo_he.png`` (BUILD-PLAN-DB.md Phase 1: slide
    identity is now content, not path -- reusing ``generate_demo_slide``'s
    deterministic output verbatim would silently fold this file's "Run
    heatmap" sweeps into the SAME database row the canonical demo slide
    uses, over the suite's shared session database, which
    ``tests/test_interaction_trace.py`` asserts an exact interaction count
    against). One pixel is perturbed after generation so the bytes -- and so
    the content identity -- differ, while every statistic a heatmap sweep
    measures (smoothed over thousands of pixels) is unaffected."""
    generate_demo_slide(path)
    with Image.open(path) as img:
        img = img.convert("RGB")
        px = img.load()
        r, g, b = px[0, 0]
        px[0, 0] = ((r + 1) % 256, g, b)
        img.save(path)
    return str(path)


@pytest.fixture(scope="module")
def demo_path(tmp_path_factory):
    """One private, content-distinct copy of the demo slide for this whole
    module -- see :func:`_private_demo_slide`."""
    return _private_demo_slide(tmp_path_factory.mktemp("hm_provenance") / "demo_he.png")


@pytest.fixture(scope="module")
def slide_b(tmp_path_factory):
    """A second slide, deliberately far smaller than the demo one."""
    path = tmp_path_factory.mktemp("hm_slide_b") / "slide_b.png"
    Image.new("RGB", (600, 400), (250, 240, 245)).save(path)
    return str(path)


@pytest.fixture(autouse=True)
def _idle(notebook_defs):
    yield
    t = notebook_defs["hm_job"]["thread"]
    if t is not None:
        t.join(timeout=300)


def _run_ticker(defs):
    """app.py's own ticker cell against the live hm_job/train_job dicts."""
    published: dict = {}
    cell, params = _cell("1s ticker (created in the analysis-state cell)")
    deps = {
        "get_source": defs["get_source"],
        "hm_job": defs["hm_job"],
        "hm_ticker": mo.ui.refresh(options=["1s"], default_interval="1s"),
        "mo": mo,
        "set_analysis_msg": lambda v: published.__setitem__("msg", v),
        "set_hm_result": lambda v: published.__setitem__("result", v),
        "train_job": defs["train_job"],
        "set_models_version": lambda v: published.__setitem__("models", v),
        "set_train_info": lambda v: published.__setitem__("train_info", v),
        "set_train_msg": lambda v: published.__setitem__("train_msg", v),
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the heatmap ticker cell grew new dependencies: {missing}"
    cell(**{p: deps[p] for p in params})
    return published


def _sweep_slide_a_then_open(defs, next_slide, demo_path):
    """Run a real sweep on the demo slide, then open ``next_slide`` first."""
    defs["open_slide_path"](demo_path)
    slide_a = defs["get_source"]()
    defs["hm_metric_dropdown"]._update(["tissue_fraction"])
    defs["hm_run_button"]._update(object())  # the real click
    worker = defs["hm_job"]["thread"]
    assert worker is not None, "no sweep started"
    worker.join(timeout=300)
    assert not worker.is_alive(), "the heatmap worker never finished"
    assert defs["hm_job"]["result"] is not None, "the sweep produced no grid"
    defs["open_slide_path"](next_slide)
    return slide_a


def test_a_sweep_that_outlives_its_slide_is_not_published_onto_the_next_one(
    notebook_defs, slide_b, demo_path
):
    defs = notebook_defs
    slide_a = _sweep_slide_a_then_open(defs, slide_b, demo_path)
    assert defs["get_source"]() is not slide_a

    published = _run_ticker(defs)

    assert "result" not in published, (
        "slide A's metric grid was published while slide B is open. With "
        "'show heatmap on navigator' ticked it is blended onto slide B's "
        "thumbnail and rescaled by grid_coverage into a registration that "
        f"means nothing: {published.get('result', {}).get('params')}"
    )
    kind, text = published["msg"]
    assert kind == "warn", (
        "a sweep of another slide was reported as a measurement of the slide "
        f"on screen: {published['msg']!r}"
    )
    assert "changed" in text and "not shown" in text, published["msg"]
    assert defs["hm_job"]["result"] is None, "the ticker must consume the result"
    assert defs["hm_job"]["msg"] is None


def test_a_sweep_of_the_open_slide_is_still_published(notebook_defs, demo_path):
    """Guard against the fix overreaching: the normal path must be untouched."""
    defs = notebook_defs
    defs["open_slide_path"](demo_path)
    defs["hm_metric_dropdown"]._update(["tissue_fraction"])
    defs["hm_run_button"]._update(object())
    worker = defs["hm_job"]["thread"]
    worker.join(timeout=300)

    published = _run_ticker(defs)

    assert published["result"]["params"]["metric"] == "tissue_fraction"
    assert published["msg"][0] == "success"


def test_reopening_the_same_file_is_a_different_slide(notebook_defs, demo_path):
    """The token is the SlideSource, not the path.

    Re-opening the same file builds a new SlideSource, and everything derived
    from the old one is discarded by ``_open_slide_path`` at that moment, so
    the honest answer is still "this grid does not describe what is on screen".
    Asserted so a later round does not "improve" the check into a path
    comparison and quietly reintroduce a weaker rule.
    """
    defs = notebook_defs
    _sweep_slide_a_then_open(defs, demo_path, demo_path)

    published = _run_ticker(defs)

    assert "result" not in published
    assert published["msg"][0] == "warn"


def test_a_finished_but_undrained_sweep_is_not_discarded_by_the_next_click(
    notebook_defs, demo_path,
):
    """R07-17: the click resets the same three unsynchronised slots the worker
    just wrote, so a sweep that finished within the last second vanished.

    The grid is the expensive artefact, so the click hands it over before it
    resets; the old message is deliberately dropped, because "sweep started"
    supersedes it on the very next line.
    """
    defs = notebook_defs
    defs["open_slide_path"](demo_path)
    defs["hm_metric_dropdown"]._update(["tissue_fraction"])
    defs["hm_run_button"]._update(object())
    defs["hm_job"]["thread"].join(timeout=300)
    finished = defs["hm_job"]["result"]
    assert finished is not None

    # ...the user clicks again BEFORE the 1s ticker has fired.
    defs["set_hm_result"](None)
    defs["hm_run_button"]._update(object())

    assert defs["get_hm_result"]() is finished, (
        "the first sweep's grid was discarded by the second click without "
        "ever reaching the UI"
    )
    defs["hm_job"]["thread"].join(timeout=300)
