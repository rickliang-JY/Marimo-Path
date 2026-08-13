"""Training must not run inside the click handler, and must report itself.

Two findings, one path -- app.py's real "Train from annotations" button:

  * R06-7. Training ran inline. marimo resolves state updates only after the
    runner finishes (``runtime.py``: ``await runner.run_all()`` then
    ``resolve_state_updates``), so while ``_on_train`` was on the stack no
    cell could re-render: the kernel simply froze, with no progress and no
    message. Measured before the fix on 20 labelled ROIs of the size
    ``extract_patch`` produces: **19.73 s**, i.e. ~1 s per ROI, so a realistic
    annotation set freezes the notebook for a minute or more. This is the
    exact shape round 04 moved the heatmap sweep off (R04-6); training was
    left behind.

  * R06-4. ``ModelInfo.warning`` -- the note that says a ``HESCOPE_EMBEDDER``
    could not be loaded and the model fell back to 56 handcrafted numbers --
    was computed, persisted to meta.json, and never displayed.
    ``set_train_info`` was built from a fixed key list that included neither
    ``warning`` nor ``encoder``, and the message was an unconditional
    ``("success", "Model '<name>' trained.")``. The platform's own documented
    default encoder (``gpfm``) 404s on Hugging Face today, so following
    docs/STRATEGY.md and setting it produced a handcrafted model under a green
    success callout.

Driven through ``app.run()``'s real button; the ticker cell is executed from
app.py's own source, the ``tests/test_heatmap_job.py`` technique.
"""

from __future__ import annotations

import ast
import pathlib
import shutil
import threading
import time

import marimo as mo
import pytest
from PIL import Image

from hescope.store.db import ROIRepo, SlideRepo
from hescope.analysis.ml import EMBEDDER_ENV_VAR
from hescope.core.rois import ROI

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
    return defs  # training reads the DB, not the slide: none is opened


@pytest.fixture
def labelled_rois(notebook_defs, tmp_path):
    """Labelled ROIs in the notebook's OWN database, removed afterwards.

    Patches are 512 px, the size ``extract_patch`` writes for a real ROI, so
    the run costs what a user's run costs.
    """
    engine = notebook_defs["db"].engine
    slide_id = SlideRepo(engine).register(
        source_kind="pillow", name="r06_train_job",
        path=str(tmp_path / "slide.png"), width=4096, height=4096,
    )
    roi = ROI(kind="rect", points=((0.0, 0.0), (512.0, 512.0)))
    repo = ROIRepo(engine)
    roi_ids = []
    for i in range(6):
        for label, color in (("tumor", (200, 60, 60)), ("stroma", (60, 60, 200))):
            p = tmp_path / f"{label}{i}.png"
            Image.new("RGB", (512, 512), tuple(c + i for c in color)).save(p)
            roi_ids.append(
                repo.add(slide_id, roi, label=label, patch_path=str(p))
            )
    try:
        yield slide_id
    finally:
        for roi_id in roi_ids:
            repo.delete(roi_id)
        SlideRepo(engine).delete(slide_id)


@pytest.fixture(autouse=True)
def _idle(notebook_defs):
    """Leave no worker running behind a test, and no model on disk."""
    yield
    t = notebook_defs["train_job"]["thread"]
    if t is not None:
        t.join(timeout=300)
    for name in ("r06_block", "r06_warn", "r06_plain"):
        shutil.rmtree(
            pathlib.Path(str(notebook_defs["MODELS_DIR"])) / name,
            ignore_errors=True,
        )


def _run_ticker_cell(defs):
    """app.py's own ticker cell, against the live train_job dict."""
    published = {}
    cell, params = _cell("1s ticker (created in the analysis-state cell)")
    deps = {
        "hm_job": defs["hm_job"],
        "hm_ticker": mo.ui.refresh(options=["1s"], default_interval="1s"),
        "mo": mo,
        # The ticker checks the sweep's slide against the open one before it
        # publishes anything (R07-1); see tests/test_heatmap_provenance.py.
        "get_source": defs["get_source"],
        "set_analysis_msg": lambda v: published.__setitem__("msg", v),
        "set_hm_result": lambda v: published.__setitem__("result", v),
        "train_job": defs["train_job"],
        "set_models_version": lambda v: published.__setitem__("models", v),
        "set_train_info": lambda v: published.__setitem__("train_info", v),
        "set_train_msg": lambda v: published.__setitem__("train_msg", v),
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the ticker cell grew new dependencies: {missing}"
    cell(**{p: deps[p] for p in params})
    return published


def _click_and_wait(defs, name):
    defs["train_job"]["thread"] = None
    defs["train_name_input"]._update(name)
    defs["train_button"]._update(object())  # the real click
    worker = defs["train_job"]["thread"]
    assert worker is not None, "training never started"
    worker.join(timeout=300)
    return _run_ticker_cell(defs)


def test_the_click_returns_before_the_training_does(notebook_defs, labelled_rois):
    """R06-7: the kernel stays free while the patches are featurized."""
    defs = notebook_defs
    defs["train_job"]["thread"] = None
    defs["train_name_input"]._update("r06_block")

    t0 = time.perf_counter()
    defs["train_button"]._update(object())  # the real click
    click = time.perf_counter() - t0

    # Self-calibrating, so it needs no hardcoded budget: if training runs
    # inline the join is free and `click` IS the total.
    worker = defs["train_job"]["thread"]
    assert isinstance(worker, threading.Thread), (
        "no worker was started, so train_from_annotations ran on the stack "
        "and the whole notebook was frozen for the duration"
    )
    worker.join(timeout=300)
    total = time.perf_counter() - t0

    assert click < total / 2, (
        f"the click blocked {click:.2f}s of a {total:.2f}s training run -- "
        "the kernel is frozen while it runs, so no cell can re-render and "
        "the user sees nothing at all until it finishes"
    )
    published = _run_ticker_cell(defs)
    # Asserted on content, not on the callout kind: the session database is
    # shared with the rest of the suite and other modules leave labelled rows
    # with no patch behind, which legitimately downgrades this to a warn.
    assert "Model 'r06_block' trained" in published["train_msg"][1]
    assert published["train_info"]["name"] == "r06_block"
    assert defs["train_job"]["result"] is None, "the ticker must consume it"


def test_a_second_click_is_refused_while_training_is_in_flight(notebook_defs):
    """The in-flight guard, made deterministic with a stand-in worker."""
    defs = notebook_defs
    release = threading.Event()
    stand_in = threading.Thread(target=release.wait, daemon=True)
    stand_in.start()
    defs["train_job"]["thread"] = stand_in
    defs["set_train_msg"](None)
    try:
        defs["train_button"]._update(object())
        assert defs["get_train_msg"]() == ("warn", "Training already running."), (
            "a second 'Train from annotations' click started another run "
            "while one was still in flight"
        )
        assert defs["train_job"]["thread"] is stand_in
    finally:
        release.set()
        stand_in.join(timeout=30)
        defs["train_job"]["thread"] = None


def test_a_failed_embedder_is_reported_instead_of_a_green_success(
    notebook_defs, labelled_rois, monkeypatch
):
    """R06-4: the fallback note must reach the user, not just meta.json."""
    monkeypatch.setenv(EMBEDDER_ENV_VAR, "gpfm_typo_r06")
    published = _click_and_wait(notebook_defs, "r06_warn")

    kind, text = published["train_msg"]
    assert kind == "warn", (
        "HESCOPE_EMBEDDER could not be loaded and training silently fell "
        f"back to handcrafted features, but the user was told {kind!r}: "
        f"{text!r}"
    )
    assert "fell back to handcrafted features" in text
    assert "gpfm_typo_r06" in text
    assert published["train_info"]["encoder"] == "handcrafted", (
        "the info table does not say which feature space the model lives in, "
        "so a failed embedder is invisible in the UI"
    )


def test_the_info_table_names_the_feature_space_on_the_normal_path(
    notebook_defs, labelled_rois, monkeypatch
):
    """The same key must be there when nothing went wrong, or it reads as an
    error marker rather than as a fact about the model."""
    monkeypatch.delenv(EMBEDDER_ENV_VAR, raising=False)
    published = _click_and_wait(notebook_defs, "r06_plain")

    assert "fell back to handcrafted features" not in published["train_msg"][1]
    assert published["train_info"]["encoder"] == "handcrafted"
    assert published["train_info"]["labels"] == "stroma, tumor"
