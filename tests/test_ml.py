"""Tests for hescope.ml (SPEC-ML Part B.4).

hescope.features is stubbed via sys.modules injection so these tests are
fast and pass even while the real features module does not exist yet.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest
from PIL import Image

from hescope.db import AgentRunRepo, ROIRepo, SlideRepo, get_engine, init_db
from hescope.ml import (
    list_models,
    load_model,
    make_prob_metric,
    predict_patch,
    train_from_annotations,
)
from hescope.rois import ROI

FEATURE_DIM = 16


@pytest.fixture(autouse=True)
def stub_features(monkeypatch):
    """Inject a deterministic stub hescope.features module."""
    mod = types.ModuleType("hescope.features")
    mod.FEATURE_DIM = FEATURE_DIM

    def extract_features(img):
        arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
        means = arr.mean(axis=(0, 1))  # (3,) class-separating in these tests
        stds = arr.std(axis=(0, 1))  # (3,)
        seed = int(round(float(arr.sum()) * 1000)) % (2**32)
        rng = np.random.default_rng(seed)
        rest = rng.random(FEATURE_DIM - 6).astype(np.float32)
        return np.concatenate([means, stds, rest]).astype(np.float32)

    mod.extract_features = extract_features
    monkeypatch.setitem(sys.modules, "hescope.features", mod)
    # If the real hescope.features was already imported earlier in the session,
    # the `hescope` package attribute still points to it; override both so
    # `from hescope import features` inside ml.py resolves to the stub.
    import hescope

    monkeypatch.setattr(hescope, "features", mod, raising=False)
    return mod


def _patch(path, color, jitter=0):
    img = Image.new("RGB", (48, 48), tuple(int(c) + jitter for c in color))
    img.save(path)
    return str(path)


def _engine(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    return engine


def _seed_db(tmp_path, engine, per_class=3):
    """Two labeled classes ('a' red, 'b' blue) with real patch PNGs, plus an
    unlabeled ROI and one ROI whose patch file is missing (both ignored)."""
    slide_id = SlideRepo(engine).register(
        source_kind="pillow", name="synthetic",
        path=str(tmp_path / "slide.png"), width=1024, height=768,
    )
    repo = ROIRepo(engine)
    roi = ROI(kind="rect", points=((0.0, 0.0), (48.0, 48.0)))
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir(exist_ok=True)
    for i in range(per_class):
        repo.add(slide_id, roi, label="a",
                 patch_path=_patch(patch_dir / f"a{i}.png", (210, 40, 40), i))
        repo.add(slide_id, roi, label="b",
                 patch_path=_patch(patch_dir / f"b{i}.png", (40, 40, 210), i))
    # unlabeled ROI: must be ignored
    repo.add(slide_id, roi, label="",
             patch_path=_patch(patch_dir / "unlabeled.png", (0, 200, 0)))
    # labeled ROI with missing patch file: must be skipped
    repo.add(slide_id, roi, label="b",
             patch_path=str(patch_dir / "does_not_exist.png"))
    return slide_id


def test_train_from_annotations(tmp_path):
    engine = _engine(tmp_path)
    _seed_db(tmp_path, engine)
    info = train_from_annotations(
        engine, name="m1", models_dir=tmp_path / "models", seed=7
    )
    assert info.name == "m1"
    assert info.labels == ["a", "b"]
    assert info.feature_dim == FEATURE_DIM
    assert info.n_samples == 6  # unlabeled + missing-patch rows excluded
    assert info.cv_accuracy is not None
    assert 0.0 <= info.cv_accuracy <= 1.0
    assert info.cv_accuracy >= 0.5  # classes are trivially separable
    assert (tmp_path / "models" / "m1" / "model.joblib").is_file()
    assert (tmp_path / "models" / "m1" / "meta.json").is_file()

    # agent_runs row recorded
    runs = [r for r in AgentRunRepo(engine).recent() if r["tool"] == "train_model"]
    assert len(runs) == 1
    assert "m1" in runs[0]["input_json"]
    assert runs[0]["status"] == "ok"


def test_train_requires_two_labels(tmp_path):
    engine = _engine(tmp_path)
    slide_id = SlideRepo(engine).register(
        source_kind="pillow", name="s", path=str(tmp_path / "s.png"),
        width=64, height=64,
    )
    repo = ROIRepo(engine)
    roi = ROI(kind="rect", points=((0.0, 0.0), (48.0, 48.0)))
    for i in range(3):
        repo.add(slide_id, roi, label="only",
                 patch_path=_patch(tmp_path / f"p{i}.png", (200, 50, 50), i))
    with pytest.raises(ValueError, match="at least 2"):
        train_from_annotations(engine, models_dir=tmp_path / "models")


def test_train_requires_min_per_class(tmp_path):
    engine = _engine(tmp_path)
    slide_id = SlideRepo(engine).register(
        source_kind="pillow", name="s", path=str(tmp_path / "s.png"),
        width=64, height=64,
    )
    repo = ROIRepo(engine)
    roi = ROI(kind="rect", points=((0.0, 0.0), (48.0, 48.0)))
    for i in range(3):
        repo.add(slide_id, roi, label="a",
                 patch_path=_patch(tmp_path / f"a{i}.png", (210, 40, 40), i))
    repo.add(slide_id, roi, label="b",
             patch_path=_patch(tmp_path / "b0.png", (40, 40, 210)))
    with pytest.raises(ValueError, match="min_per_class|at least 2 samples|'b'"):
        train_from_annotations(
            engine, models_dir=tmp_path / "models", min_per_class=2
        )


def test_load_predict_and_metric(tmp_path):
    engine = _engine(tmp_path)
    _seed_db(tmp_path, engine)
    train_from_annotations(engine, name="m1", models_dir=tmp_path / "models")

    model, meta = load_model("m1", tmp_path / "models")
    assert meta["name"] == "m1"
    assert meta["labels"] == ["a", "b"]

    red = Image.new("RGB", (48, 48), (215, 45, 45))
    probs = predict_patch(model, meta, red)
    assert set(probs) == {"a", "b"}
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-5)
    assert all(0.0 <= p <= 1.0 for p in probs.values())
    # sorted by probability, descending
    values = list(probs.values())
    assert values == sorted(values, reverse=True)
    # red patch is class 'a'
    assert next(iter(probs)) == "a"

    metric = make_prob_metric(model, meta, "a")
    value = metric(red)
    assert isinstance(value, float)
    assert 0.0 <= value <= 1.0
    assert value > 0.5

    with pytest.raises(ValueError, match="not in model classes"):
        make_prob_metric(model, meta, "nope")


def test_list_models_and_missing_model(tmp_path):
    engine = _engine(tmp_path)
    _seed_db(tmp_path, engine)
    models_dir = tmp_path / "models"
    assert list_models(models_dir) == []  # missing dir is fine

    train_from_annotations(engine, name="zeta", models_dir=models_dir)
    train_from_annotations(engine, name="alpha", models_dir=models_dir)
    models = list_models(models_dir)
    assert [m["name"] for m in models] == ["alpha", "zeta"]
    assert all(m["n_samples"] == 6 for m in models)

    with pytest.raises(FileNotFoundError, match="not found"):
        load_model("ghost", models_dir)
