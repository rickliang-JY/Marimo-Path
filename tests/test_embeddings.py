"""Tests for hescope.analysis.embeddings (encoder factory) and its ml.py integration.

Everything is mocked: no network, no model downloads, no GPU. Real torch
imports are only touched indirectly (torch IS installed in CI; timm and
huggingface_hub are not, and tests force-import-fail them anyway).
"""

from __future__ import annotations

import json
import subprocess
import sys
import types

import numpy as np
import pytest
from PIL import Image

import hescope
import hescope.analysis.embeddings as emb
from hescope.analysis.embeddings import (
    ENCODERS,
    EncoderSpec,
    default_encoder_name,
    embed_tiles,
    list_encoders,
    load_encoder,
)
from hescope.store.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.analysis.ml import (
    EMBEDDER_ENV_VAR,
    load_model,
    make_prob_metric,
    predict_patch,
    train_from_annotations,
)
from hescope.core.rois import ROI

# ---------------------------------------------------------------------------
# registry metadata
# ---------------------------------------------------------------------------


def test_registry_contains_all_encoders():
    assert set(ENCODERS) == {"gpfm", "uni2h", "hoptimus0", "resnet18"}
    for spec in ENCODERS.values():
        assert isinstance(spec, EncoderSpec)
        assert spec.name in ENCODERS
        assert spec.embedding_dim > 0
        assert spec.license
        assert isinstance(spec.commercial_ok, bool)
        assert isinstance(spec.gated, bool)
        assert spec.loader


def test_registry_metadata_values():
    gpfm = ENCODERS["gpfm"]
    assert gpfm.license == "MIT"
    assert gpfm.commercial_ok and not gpfm.gated
    assert gpfm.embedding_dim == 1024
    assert gpfm.hf_id == "majiabo/GPFM"  # per research/r2-foundation-models.md

    uni = ENCODERS["uni2h"]
    assert uni.hf_id == "mahmoodlab/UNI2-h"
    assert uni.license == "CC-BY-NC-ND-4.0"
    assert not uni.commercial_ok  # non-commercial: never a platform default
    assert uni.gated
    assert uni.embedding_dim == 1536

    h0 = ENCODERS["hoptimus0"]
    assert h0.hf_id == "bioptimus/H-optimus-0"
    assert h0.license == "Apache-2.0"
    assert h0.commercial_ok and not h0.gated
    assert h0.embedding_dim == 1536

    rn = ENCODERS["resnet18"]
    assert rn.embedding_dim == 512
    assert rn.commercial_ok and not rn.gated  # local fallback, no HF gating


def test_list_encoders_json_serializable():
    encs = list_encoders()
    assert len(encs) == 4
    assert all(isinstance(e, dict) for e in encs)
    json.dumps(encs)  # must survive tool transport
    required = {
        "name",
        "hf_id",
        "embedding_dim",
        "license",
        "commercial_ok",
        "gated",
        "loader",
    }
    assert all(required <= set(e) for e in encs)


# ---------------------------------------------------------------------------
# license red line for defaults
# ---------------------------------------------------------------------------


def test_default_encoder_is_license_safe():
    name = default_encoder_name()
    assert name == "gpfm"
    spec = ENCODERS[name]
    assert spec.commercial_ok and not spec.gated


def test_default_never_picks_noncommercial_or_gated(monkeypatch):
    # Remove every license-safe encoder except resnet18: uni2h (CC-BY-NC-ND,
    # gated) must never be selected as a default.
    restricted = {
        k: v for k, v in ENCODERS.items() if k in ("uni2h", "resnet18")
    }
    monkeypatch.setattr(emb, "ENCODERS", restricted)
    assert default_encoder_name() == "resnet18"

    only_nc = {"uni2h": ENCODERS["uni2h"]}
    monkeypatch.setattr(emb, "ENCODERS", only_nc)
    with pytest.raises(RuntimeError, match="license-safe"):
        default_encoder_name()


# ---------------------------------------------------------------------------
# lazy loading + failure message quality
# ---------------------------------------------------------------------------


def test_module_import_does_not_import_torch():
    """Importing hescope(.embeddings) must not pull in torch or network."""
    code = (
        "import sys; import hescope; import hescope.analysis.embeddings; "
        "assert 'torch' not in sys.modules, 'torch imported eagerly'; "
        "assert 'torchvision' not in sys.modules; "
        "assert 'timm' not in sys.modules; "
        "print('ok')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(__import__("pathlib").Path(hescope.__file__).parent.parent),
    )
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_load_encoder_unknown_name():
    with pytest.raises(ValueError, match="unknown encoder 'nope'.*gpfm"):
        load_encoder("nope")


def test_resnet18_missing_torch_error_guidance(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)  # import torch -> ImportError
    with pytest.raises(RuntimeError, match=r"pip install.*torch"):
        load_encoder("resnet18")


def test_timm_encoder_missing_dependency_guidance(monkeypatch):
    monkeypatch.setitem(sys.modules, "timm", None)  # force ImportError
    with pytest.raises(RuntimeError) as excinfo:
        load_encoder("gpfm")
    msg = str(excinfo.value)
    assert "pip install" in msg
    assert "timm" in msg and "huggingface_hub" in msg
    assert "majiabo/GPFM" in msg


def test_gated_encoder_failure_mentions_gating_and_license(monkeypatch):
    fake_timm = types.ModuleType("timm")

    def _boom(*args, **kwargs):
        raise OSError("403 Client Error")

    fake_timm.create_model = _boom
    monkeypatch.setitem(sys.modules, "timm", fake_timm)
    with pytest.raises(RuntimeError) as excinfo:
        load_encoder("uni2h")
    msg = str(excinfo.value).lower()
    assert "gated" in msg
    assert "huggingface.co/mahmoodlab/uni2-h" in msg
    assert "huggingface-cli login" in msg or "hf_token" in msg
    assert "cc-by-nc-nd" in msg


def test_ungated_encoder_failure_mentions_network_and_license(monkeypatch):
    fake_timm = types.ModuleType("timm")

    def _boom(*args, **kwargs):
        raise OSError("connection refused")

    fake_timm.create_model = _boom
    monkeypatch.setitem(sys.modules, "timm", fake_timm)
    with pytest.raises(RuntimeError) as excinfo:
        load_encoder("hoptimus0")
    msg = str(excinfo.value)
    assert "network" in msg
    assert "Apache-2.0" in msg


# ---------------------------------------------------------------------------
# embed_tiles with a fake encoder
# ---------------------------------------------------------------------------


class StubEncoder:
    """Deterministic fake encoder recording batch sizes."""

    def __init__(self, dim=8):
        self.embedding_dim = dim
        self.batch_sizes: list[int] = []

    def embed_batch(self, images):
        imgs = list(images)
        self.batch_sizes.append(len(imgs))
        rows = []
        for im in imgs:
            arr = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
            means = arr.mean(axis=(0, 1))
            stds = arr.std(axis=(0, 1))
            rows.append(
                np.concatenate([means, stds, np.zeros(self.embedding_dim - 6)])
            )
        return np.stack(rows).astype(np.float32)


def test_embed_tiles_batching_and_shape(monkeypatch):
    stub = StubEncoder(dim=8)
    monkeypatch.setattr(emb, "load_encoder", lambda name: stub)
    encoder = emb.load_encoder("gpfm")  # returns the stub

    images = [Image.new("RGB", (16, 16), (i * 20, 40, 200)) for i in range(7)]
    out = embed_tiles(encoder, images, batch_size=3)
    assert out.shape == (7, 8)
    assert out.dtype == np.float32
    assert stub.batch_sizes == [3, 3, 1]
    # order preserved: first column is the red mean, increasing with i
    assert np.all(np.diff(out[:, 0]) > 0)


def test_embed_tiles_empty_and_bad_batch_size():
    stub = StubEncoder(dim=5)
    out = embed_tiles(stub, [])
    assert out.shape == (0, 5)
    with pytest.raises(ValueError, match="batch_size"):
        embed_tiles(stub, [Image.new("RGB", (8, 8))], batch_size=0)


# ---------------------------------------------------------------------------
# ml.py integration (HESCOPE_EMBEDDER)
# ---------------------------------------------------------------------------

STUB_FEATURE_DIM = 16
EMBED_DIM = 8


@pytest.fixture
def stub_features(monkeypatch):
    """Inject a deterministic stub hescope.analysis.features module (as in test_ml)."""
    mod = types.ModuleType("hescope.analysis.features")
    mod.FEATURE_DIM = STUB_FEATURE_DIM

    def extract_features(img):
        arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
        means = arr.mean(axis=(0, 1))
        stds = arr.std(axis=(0, 1))
        seed = int(round(float(arr.sum()) * 1000)) % (2**32)
        rng = np.random.default_rng(seed)
        rest = rng.random(STUB_FEATURE_DIM - 6).astype(np.float32)
        return np.concatenate([means, stds, rest]).astype(np.float32)

    mod.extract_features = extract_features
    monkeypatch.setitem(sys.modules, "hescope.analysis.features", mod)
    # The attribute to patch moved with the module: hescope/analysis/ml.py now
    # does `from . import features`, which resolves through hescope.analysis,
    # not hescope. Patching the old holder left the REAL 56-dim features in
    # play while the test believed it had a 16-dim stub.
    import hescope.analysis

    monkeypatch.setattr(hescope.analysis, "features", mod, raising=False)
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
    return slide_id


def test_train_default_path_unchanged(stub_features, tmp_path, monkeypatch):
    monkeypatch.delenv(EMBEDDER_ENV_VAR, raising=False)
    engine = _engine(tmp_path)
    _seed_db(tmp_path, engine)
    info = train_from_annotations(
        engine, name="m_plain", models_dir=tmp_path / "models"
    )
    assert info.feature_dim == STUB_FEATURE_DIM
    assert info.encoder is None
    assert info.warning is None
    _, meta = load_model("m_plain", tmp_path / "models")
    assert meta.get("encoder") is None
    assert meta.get("warning") is None


def test_train_embedder_failure_falls_back_with_warning(
    stub_features, tmp_path, monkeypatch
):
    monkeypatch.setenv(EMBEDDER_ENV_VAR, "gpfm")

    def _raise(name):
        raise RuntimeError(
            "encoder 'gpfm' requires timm and huggingface_hub; "
            "install them with: pip install timm huggingface_hub"
        )

    monkeypatch.setattr(emb, "load_encoder", _raise)
    engine = _engine(tmp_path)
    _seed_db(tmp_path, engine)
    info = train_from_annotations(
        engine, name="m_fallback", models_dir=tmp_path / "models"
    )
    # fell back to the handcrafted stub path
    assert info.feature_dim == STUB_FEATURE_DIM
    assert info.encoder is None
    assert info.warning is not None
    assert "HESCOPE_EMBEDDER" in info.warning
    assert "gpfm" in info.warning
    assert "fell back" in info.warning

    _, meta = load_model("m_fallback", tmp_path / "models")
    assert meta["warning"] == info.warning
    assert meta.get("encoder") is None

    # predictions still work on the fallback model
    red = Image.new("RGB", (48, 48), (215, 45, 45))
    probs = predict_patch(_, meta, red)
    assert set(probs) == {"a", "b"}


def test_train_with_stub_encoder_records_encoder_and_dim(
    stub_features, tmp_path, monkeypatch
):
    monkeypatch.setenv(EMBEDDER_ENV_VAR, "gpfm")
    stub = StubEncoder(dim=EMBED_DIM)
    monkeypatch.setattr(emb, "load_encoder", lambda name: stub)

    engine = _engine(tmp_path)
    _seed_db(tmp_path, engine)
    info = train_from_annotations(
        engine, name="m_embed", models_dir=tmp_path / "models"
    )
    assert info.feature_dim == EMBED_DIM  # embedding dim, not handcrafted 16
    assert info.encoder == "gpfm"
    assert info.warning is None
    assert info.n_samples == 6

    _, meta = load_model("m_embed", tmp_path / "models")
    assert meta["encoder"] == "gpfm"
    assert meta["feature_dim"] == EMBED_DIM

    # predict path (and the heatmap model_prob metric built on it) re-embeds
    # via the recorded encoder instead of handcrafted features
    red = Image.new("RGB", (48, 48), (215, 45, 45))
    probs = predict_patch(_, meta, red)
    assert set(probs) == {"a", "b"}
    assert next(iter(probs)) == "a"  # red patch -> class a

    metric = make_prob_metric(_, meta, "a")
    value = metric(red)
    assert 0.0 <= value <= 1.0
    assert value > 0.5


# ---------------------------------------------------------------------------
# analysis_capabilities
# ---------------------------------------------------------------------------


def test_analysis_capabilities_reports_encoders():
    caps = hescope.analysis_capabilities()
    assert "error" not in caps
    avail = caps["available_encoders"]
    assert avail["default"] == "gpfm"
    assert isinstance(avail["torch_importable"], bool)
    assert {e["name"] for e in avail["encoders"]} == {
        "gpfm",
        "uni2h",
        "hoptimus0",
        "resnet18",
    }
    json.dumps(caps)  # whole payload stays JSON-serializable
