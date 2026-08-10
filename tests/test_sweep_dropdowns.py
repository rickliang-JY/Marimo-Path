"""A successful train must not un-pick the user's heatmap model or metric.

R04-4's class, on the two controls round 04 neither fixed nor justified.
``set_models_version(object())`` fires on the train-success path; that re-runs
the model-dropdown cell (deliberately -- refreshing the model LIST is the
point of the token) and, with it, the metric-dropdown cell downstream. marimo
stamps a re-constructed ``mo.ui`` element with a fresh token so it comes back
at its DEFAULT, so both selections were discarded — on precisely the click
after which a user wants a ``model_prob:<label>`` sweep, which AGENTS.md 9
documents as the canonical train -> load_model -> make_prob_metric ->
compute_grid workflow (R07-8).

Two things this test has to do that the existing reactivity guard cannot:

  * assert on VALUES, not on ``.refs``. ``hm_metric_dropdown``'s cell does not
    reference ``get_models_version``; it is a transitive descendant of the
    cell that does. Adding it to
    ``test_sweep_controls_are_not_rebuilt_by_a_training_run``'s parametrize
    list passes on the broken code — a false green, and this project nearly
    shipped one.
  * re-run app.py's OWN cells the way marimo does, since the fix lives in how
    they construct their elements.
"""

from __future__ import annotations

import ast
import json
import pathlib

import marimo as mo
import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _cell(marker: str):
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


@pytest.fixture
def models_dir(tmp_path):
    """Two model directories, as ``hescope.ml`` writes them."""
    for name, labels in (("tumour_v1", ["tumour", "stroma"]),):
        d = tmp_path / name
        d.mkdir()
        (d / "meta.json").write_text(
            json.dumps({"name": name, "labels": labels}), encoding="utf-8"
        )
    return tmp_path


def _add_model(models_dir, name, labels):
    d = models_dir / name
    d.mkdir()
    (d / "meta.json").write_text(
        json.dumps({"name": name, "labels": labels}), encoding="utf-8"
    )


def _run_model_cell(models_dir, hm_choice):
    from hescope.ml import list_models

    cell, params = _cell("# Heatmap controls (Analysis accordion).")
    deps = {
        "MODELS_DIR": models_dir,
        "get_models_version": lambda: None,
        "hm_choice": hm_choice,
        "list_models": list_models,
        "mo": mo,
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the model-dropdown cell grew new dependencies: {missing}"
    return cell(**{p: deps[p] for p in params})  # (dropdown, models)


def _run_metric_cell(model_dd, models, hm_choice):
    cell, params = _cell("# Metric dropdown, built dynamically")
    deps = {
        "hm_choice": hm_choice,
        "hm_model_dropdown": model_dd,
        "hm_models": models,
        "mo": mo,
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the metric-dropdown cell grew new dependencies: {missing}"
    (metric_dd,) = cell(**{p: deps[p] for p in params})
    return metric_dd


def _rebuild(models_dir, hm_choice):
    """Both cells, the way ``set_models_version`` re-runs them."""
    model_dd, models = _run_model_cell(models_dir, hm_choice)
    return model_dd, _run_metric_cell(model_dd, models, hm_choice)


def test_a_training_run_does_not_reset_the_model_and_metric(models_dir):
    hm_choice = {"model": None, "metric": "tissue_fraction"}
    # Startup: both cells run once.
    model_dd, models = _run_model_cell(models_dir, hm_choice)
    metric_dd = _run_metric_cell(model_dd, models, hm_choice)

    # The user picks a model. Only the METRIC cell re-runs -- it refs the model
    # dropdown; the model cell does not ref itself.
    model_dd._update(["tumour_v1"])
    metric_dd = _run_metric_cell(model_dd, models, hm_choice)
    assert "model_prob:tumour" in metric_dd.options
    metric_dd._update(["model_prob:tumour"])

    # ...then a successful "Train from annotations" bumps get_models_version,
    # which re-runs the model cell and, downstream, the metric cell.
    _add_model(models_dir, "tumour_v2", ["tumour", "stroma"])
    model_dd, metric_dd = _rebuild(models_dir, hm_choice)

    assert sorted(model_dd.options) == ["tumour_v1", "tumour_v2"], (
        "the model LIST must still refresh -- that is what the token is for"
    )
    assert model_dd.value == "tumour_v1", (
        "training discarded the user's model selection; the model_prob metrics "
        "vanish with it, so the sweep they were about to run is two clicks "
        f"away again ({model_dd.value!r})"
    )
    assert metric_dd.value == "model_prob:tumour", (
        "training reset the heatmap metric to the hardcoded default "
        f"'tissue_fraction' ({metric_dd.value!r})"
    )


def test_a_choice_that_is_no_longer_offered_falls_back(models_dir):
    """A remembered value that is not in the new option list raises out of the
    cell, so it must fall back rather than be honoured blindly."""
    hm_choice = {"model": "deleted_model", "metric": "model_prob:gone"}

    model_dd, metric_dd = _rebuild(models_dir, hm_choice)

    assert model_dd.value is None
    assert metric_dd.value == "tissue_fraction"


def test_the_defaults_on_a_first_run_are_unchanged(models_dir):
    """Guard against the fix overreaching: a fresh session starts where it
    always did."""
    model_dd, metric_dd = _rebuild(models_dir, {"model": None, "metric": "tissue_fraction"})

    assert model_dd.value is None
    assert metric_dd.value == "tissue_fraction"
    assert sorted(metric_dd.options) == ["nuclei_density", "tissue_fraction"]
