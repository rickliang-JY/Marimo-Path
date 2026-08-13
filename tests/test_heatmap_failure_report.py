"""A sweep in which every tile failed must not be reported as a success.

R06-2. ``compute_grid`` writes ``NaN`` for a tile whose metric raised, which
is the same ``NaN`` a tile skipped by the tissue filter gets, and
``render_heatmap`` blends only non-NaN cells -- so a totally failed sweep is
byte-identical to the bare thumbnail. ``_on_run_heatmap._work`` then wrote its
success message unconditionally, and the ticker rendered it as a green
``mo.callout(kind="success")`` above a caption reading
``metric model_prob:tumor | grid 2x3``. The user saw a plain slide thumbnail
under a green success callout and concluded the model had found nothing.

The app-level half drives app.py's REAL "Run heatmap" button with a REAL
trained model whose meta names an encoder that cannot be loaded -- the shape
of the reachable trigger (an air-gapped workstation, a torch-hub/HF outage, a
models dir copied to another machine), reproduced without monkeypatching any
production code. ``make_prob_metric`` does not probe the encoder on the main
thread, so the failure happens once per tile inside ``compute_grid``.
"""

from __future__ import annotations

import ast
import json
import pathlib
import shutil

import marimo as mo
import numpy as np
import pytest
from PIL import Image, ImageDraw

from hescope.store.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.analysis.grid import tissue_fraction_proxy
from hescope.analysis.heatmap import compute_grid, render_heatmap
from hescope.analysis.ml import train_from_annotations
from hescope.core.rois import ROI
from hescope.wsi.slides import open_slide

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

TILE = 128
DS = 4.0
CELL = int(TILE * DS)


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


def _make_slide(tmp_path, size=(1024, 768)):
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for gx, gy in ((0, 0), (0, 1)):
        draw.rectangle(
            [gx * CELL + 16, gy * CELL + 16,
             (gx + 1) * CELL - 16, (gy + 1) * CELL - 16],
            fill=(120, 40, 140),
        )
    path = tmp_path / "slide.png"
    img.save(path)
    return open_slide(path)


# --- the mechanism, at the hescope level -------------------------------------


def test_compute_grid_reports_the_tiles_whose_metric_raised(tmp_path):
    src = _make_slide(tmp_path)
    seen = []

    def _boom(_tile):
        raise RuntimeError("encoder unavailable")

    grid = compute_grid(
        src, _boom, tile=TILE, downsample=DS, tissue_min=0.0,
        error_cb=lambda gx, gy, exc: seen.append((gx, gy, type(exc).__name__)),
    )
    assert not np.isfinite(grid).any(), "a failed tile must still be NaN"
    assert len(seen) == grid.size, (
        "compute_grid swallowed the failures, so a caller cannot tell a "
        f"broken metric from empty background: {seen}"
    )
    assert {name for _gx, _gy, name in seen} == {"RuntimeError"}


def test_a_skipped_tile_is_not_reported_as_a_failure(tmp_path):
    """The distinction the fix exists to make: skipped != failed."""
    src = _make_slide(tmp_path)
    seen = []
    grid = compute_grid(
        src, tissue_fraction_proxy, tile=TILE, downsample=DS,
        error_cb=lambda gx, gy, exc: seen.append((gx, gy)),
    )
    assert np.isnan(grid).any(), "the fixture must contain background tiles"
    assert seen == [], f"background tiles were reported as failures: {seen}"


def test_a_totally_failed_sweep_renders_as_the_bare_thumbnail(tmp_path):
    """Why the message is the only signal there is."""
    src = _make_slide(tmp_path)

    def _boom(_tile):
        raise RuntimeError("encoder unavailable")

    grid = compute_grid(src, _boom, tile=TILE, downsample=DS, tissue_min=0.0)
    thumb = src.get_thumbnail((256, 256)).convert("RGB")
    assert np.array_equal(
        np.asarray(render_heatmap(thumb, grid)), np.asarray(thumb)
    )


# --- the same thing through app.py's real "Run heatmap" button ---------------


@pytest.fixture(scope="module")
def notebook_defs(tmp_path_factory):
    import app as appmod

    _outputs, defs = appmod.app.run()
    # A small synthetic slide of its OWN, not the shared demo: clicking "Run
    # heatmap" writes an `analysis_run` interaction against whatever slide is
    # open, and the session database is shared with the rest of the suite
    # (tests/test_interaction_trace.py counts those rows for the demo slide).
    slide = tmp_path_factory.mktemp("hm_fail") / "tissue.png"
    img = Image.new("RGB", (1024, 768), (150, 70, 160))  # tissue everywhere
    ImageDraw.Draw(img).rectangle([0, 0, 64, 64], fill=(255, 255, 255))
    img.save(slide)
    defs["open_slide_path"](str(slide))
    return defs


@pytest.fixture(autouse=True)
def _idle(notebook_defs):
    yield
    t = notebook_defs["hm_job"]["thread"]
    if t is not None:
        t.join(timeout=300)


def _model_with_an_unloadable_encoder(tmp_path, models_dir, name):
    """A REAL trained model whose meta names an encoder that cannot load.

    Trained through the real ``train_from_annotations``; only the recorded
    encoder name is then rewritten, which is exactly the state a model dir
    reaches when it is copied to a machine that cannot fetch the weights.
    """
    engine = get_engine(f"sqlite:///{tmp_path / 'broken.db'}")
    init_db(engine)
    slide_id = SlideRepo(engine).register(
        source_kind="pillow", name="synthetic",
        path=str(tmp_path / "s.png"), width=512, height=512,
    )
    repo = ROIRepo(engine)
    roi = ROI(kind="rect", points=((0.0, 0.0), (64.0, 64.0)))
    for i in range(2):
        for label, color in (("tumor", (210, 40, 40)), ("stroma", (40, 40, 210))):
            p = tmp_path / f"{label}{i}.png"
            Image.new("RGB", (64, 64), tuple(c + i for c in color)).save(p)
            repo.add(slide_id, roi, label=label, patch_path=str(p))
    train_from_annotations(engine, name=name, models_dir=str(models_dir))
    meta_path = pathlib.Path(models_dir) / name / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["encoder"] = "no_such_encoder_r06"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return name


def _run_ticker_cell(defs):
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


def test_the_button_reports_a_totally_failed_sweep_as_a_failure(
    notebook_defs, tmp_path
):
    defs = notebook_defs
    models_dir = pathlib.Path(str(defs["MODELS_DIR"]))
    name = _model_with_an_unloadable_encoder(tmp_path, models_dir, "r06_broken")
    try:
        defs["hm_job"]["thread"] = None
        defs["hm_model_dropdown"]._value = name
        # Set directly rather than through _update: the metric dropdown only
        # offers model_prob options once the model list has refreshed.
        defs["hm_metric_dropdown"]._value = "model_prob:tumor"
        defs["hm_tile_slider"]._update(256)
        defs["set_analysis_msg"](None)

        defs["hm_run_button"]._update(object())  # the real click
        worker = defs["hm_job"]["thread"]
        assert worker is not None, "the sweep never started"
        worker.join(timeout=300)

        published = _run_ticker_cell(defs)
        kind, text = published["msg"]
        grid = published["result"]["grid"]
        assert not np.isfinite(grid).any(), (
            "the fixture is wrong: some tile scored, so this is not the "
            "total-failure case"
        )
        assert kind == "danger", (
            "a sweep in which every tile failed was reported to the user as "
            f"{kind!r}: {text!r}. The image below it is the bare thumbnail, "
            "so a broken model is indistinguishable from empty background."
        )
        assert "failed" in text and "no_such_encoder_r06" in text, (
            f"the message names neither the failure nor its cause: {text!r}"
        )
    finally:
        shutil.rmtree(models_dir / name, ignore_errors=True)
