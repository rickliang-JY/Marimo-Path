"""The heatmap sweep must not run inside the click handler.

marimo resolves state updates only AFTER the runner finishes
(``runtime.py``: ``await runner.run_all()`` and then
``runner.resolve_state_updates(...)``), so while ``_on_run_heatmap`` was on
the stack no cell could re-render. Two things that looked functional were
therefore impossible (R04-6):

  * the progress bar. ``set_hm_progress`` was driven from ``compute_grid``'s
    ``progress_cb`` and reset to ``None`` in a ``finally``, so by the time any
    cell could read it the coalesced value was always ``None`` and the
    ``if _prog is not None:`` block never rendered;
  * the in-flight guard. ``get_hm_progress() is not None`` could never be
    true, so a second click re-ran the entire sweep. Measured on the 6000x4000
    demo slide before the fix: the click blocked 0.4 s for tissue_fraction and
    9.2 s for nuclei_density, with the kernel frozen and nothing on screen. A
    real WSI plans hundreds of cells.

The fix is the shape the TCGA download already uses and documents: a worker
thread, a plain (non-reactive) dict, and a ticker cell that renders progress
and publishes the result on the main thread.

Driven through ``app.run()``'s real "Run heatmap" button; the ticker cell is
executed from app.py's own source, the ``tests/test_annotation_jump.py``
technique.
"""

from __future__ import annotations

import ast
import pathlib
import threading
import time

import marimo as mo
import pytest
from PIL import Image

from hescope.demo import generate_demo_slide

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
def notebook_defs(tmp_path_factory):
    import app as appmod

    _outputs, defs = appmod.app.run()
    # A PRIVATE copy, not assets/demo_he.png itself: slide identity is now
    # content, not path (BUILD-PLAN-DB.md Phase 1), so reusing the shared
    # demo verbatim would fold this module's "Run heatmap" clicks into the
    # SAME database row the canonical demo slide uses over the suite's
    # shared session database -- which tests/test_interaction_trace.py
    # asserts an exact interaction count against. One pixel is perturbed
    # after generation so the content identity differs; every statistic a
    # heatmap sweep measures is smoothed over thousands of pixels and
    # unaffected by it.
    demo_path = tmp_path_factory.mktemp("hm_job") / "demo_he.png"
    generate_demo_slide(demo_path)
    with Image.open(demo_path) as img:
        img = img.convert("RGB")
        px = img.load()
        r, g, b = px[0, 0]
        px[0, 0] = ((r + 1) % 256, g, b)
        img.save(demo_path)
    defs["open_slide_path"](str(demo_path))
    return defs


@pytest.fixture(autouse=True)
def _idle(notebook_defs):
    """Leave no worker running behind a test."""
    yield
    t = notebook_defs["hm_job"]["thread"]
    if t is not None:
        t.join(timeout=300)


def _run_ticker_cell(defs):
    """app.py's own ticker cell, against the live hm_job/train_job dicts."""
    published = {}
    cell, params = _cell("1s ticker (created in the analysis-state cell)")
    deps = {
        "hm_job": defs["hm_job"],
        "hm_ticker": mo.ui.refresh(options=["1s"], default_interval="1s"),
        "mo": mo,
        # The ticker compares the sweep's slide against the open one before it
        # publishes anything (R07-1); see tests/test_heatmap_provenance.py.
        "get_source": defs["get_source"],
        "set_analysis_msg": lambda v: published.__setitem__("msg", v),
        "set_hm_result": lambda v: published.__setitem__("result", v),
        # The same ticker drains the background TRAINING run (R06-7).
        "train_job": defs["train_job"],
        "set_models_version": lambda v: published.__setitem__("models", v),
        "set_train_info": lambda v: published.__setitem__("train_info", v),
        "set_train_msg": lambda v: published.__setitem__("train_msg", v),
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the heatmap ticker cell grew new dependencies: {missing}"
    cell(**{p: deps[p] for p in params})
    return published


def test_the_click_returns_before_the_sweep_does(notebook_defs):
    """The whole point: the kernel stays free while the grid is computed."""
    defs = notebook_defs
    defs["hm_metric_dropdown"]._update(["nuclei_density"])

    t0 = time.perf_counter()
    defs["hm_run_button"]._update(object())  # the real click
    click = time.perf_counter() - t0

    # Self-calibrating, so it needs no hardcoded budget: if the sweep runs
    # inline the join is free and `click` IS the total.
    worker = defs["hm_job"]["thread"]
    if worker is not None:
        worker.join(timeout=300)
        assert not worker.is_alive(), "the heatmap worker never finished"
    total = time.perf_counter() - t0

    assert click < total / 2, (
        f"the click blocked {click:.2f}s of a {total:.2f}s sweep -- the grid "
        "is still being computed inside the handler, so no cell can re-render "
        "while it runs, the progress bar can never show a value and the "
        "in-flight guard can never be true"
    )
    assert isinstance(worker, threading.Thread)
    # ...and the result reaches mo.state only through the main-thread ticker.
    assert defs["hm_job"]["result"] is not None
    published = _run_ticker_cell(defs)
    assert published["result"]["params"]["metric"] == "nuclei_density"
    assert published["msg"][0] == "success"
    assert defs["hm_job"]["result"] is None, "the ticker must consume the result"


def test_progress_is_observable_while_the_sweep_runs(notebook_defs):
    """``hm_job['progress']`` has to move, otherwise the bar has nothing to show."""
    defs = notebook_defs
    defs["hm_metric_dropdown"]._update(["nuclei_density"])
    defs["hm_run_button"]._update(object())

    seen = set()
    worker = defs["hm_job"]["thread"]
    if worker is not None:  # inline sweeps leave nothing to watch, and fail below
        deadline = time.perf_counter() + 300
        while worker.is_alive() and time.perf_counter() < deadline:
            p = defs["hm_job"]["progress"]
            if p is not None:
                seen.add(p)
            time.sleep(0.01)
        worker.join(timeout=300)

    assert len(seen) > 1, (
        "progress never changed while a sweep was in flight, so the progress "
        f"bar still has nothing to render: {sorted(seen)}"
    )
    assert defs["hm_job"]["progress"] is None, "the worker must clear progress"


def test_a_second_click_is_refused_while_a_sweep_is_in_flight(notebook_defs):
    """The in-flight guard, made deterministic with a stand-in worker."""
    defs = notebook_defs
    release = threading.Event()
    stand_in = threading.Thread(target=release.wait, daemon=True)
    stand_in.start()
    defs["hm_job"]["thread"] = stand_in
    defs["set_analysis_msg"](None)
    try:
        defs["hm_run_button"]._update(object())
        assert defs["get_analysis_msg"]() == ("warn", "Heatmap already running."), (
            "a second 'Run heatmap' click started another full sweep while "
            "one was still in flight"
        )
        assert defs["hm_job"]["thread"] is stand_in
    finally:
        release.set()
        stand_in.join(timeout=30)


def test_a_control_error_is_still_answered_immediately(notebook_defs):
    """Guard against the fix overreaching: user errors about the controls must
    not be deferred to a tick, and must not start a worker."""
    defs = notebook_defs
    defs["hm_job"]["thread"] = None
    defs["hm_metric_dropdown"]._update(["tissue_fraction"])
    # "a model_prob metric with no model selected" is the reachable control
    # error. The dropdown only offers model_prob options once a model exists,
    # and no model is trained here, so the value is set directly rather than
    # through _update (which validates against the option list).
    defs["hm_metric_dropdown"]._value = "model_prob:tumour"
    defs["set_analysis_msg"](None)

    defs["hm_run_button"]._update(object())

    kind, text = defs["get_analysis_msg"]()
    assert kind == "danger" and "Select a model" in text
    assert defs["hm_job"]["thread"] is None, "no worker should have started"
