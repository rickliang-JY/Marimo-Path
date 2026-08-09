"""The handcrafted feature vector must be scored at the raster it was fit at.

R06-1. ``features.extract_features`` is raster-dependent by construction --
``nuclei_count``, ``nuclei_mean_area_px`` and ``blur_score`` are pixel-geometry
quantities -- and nothing tied the training raster to the raster
``heatmap.compute_grid`` feeds the model. Training patches come from
``rois.extract_patch``, which caps at 1024 px; heatmap tiles are
``hm_tile_slider`` px at a downsample derived from the slide size, so on any
real WSI the two rasters differ by construction. Measured before the fix, on
IDENTICAL pixels resampled to 1024/512/256/128 px:

    nuclei_count              555.0  391.0  189.0   49.0
    nuclei_mean_area_px       628.6  216.0   96.4   51.3
    blur_score               2444.5 4393.3 7966.7 17795.5

-- the same tissue at two rasters landed ~97x further apart in feature space
than two genuinely different tissue regions did at one raster, and the
predicted class flipped on 8 of 16 of a model's own training patches from
resampling alone.

These tests use the REAL hescope.features (no stub): the point is the real
function's raster dependence, which a stub cannot express.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from hescope.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.features import FEATURE_NAMES, extract_features
from hescope.ml import (
    FEATURE_RASTER,
    _feature_vector_for_model,
    load_model,
    make_prob_metric,
    train_from_annotations,
)
from hescope.rois import ROI


def he_image(seed=0, size=512, n_blobs=10, bg=(235, 190, 205)):
    """Synthetic H&E-like image: pink background + purple blobs.

    Same generator as tests/test_features.py, with the blob radius scaled to
    the image size so that ``he_image(size=n)`` is the SAME picture at every
    n -- otherwise a raster comparison would also be a content comparison.
    """
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(img)
    for _ in range(n_blobs):
        x, y = rng.integers(size // 10, size - size // 10, 2)
        r = size // 32
        d.ellipse([x - r, y - r, x + r, y + r], fill=(130, 60, 160))
    arr = np.asarray(img).astype(np.float64) + rng.normal(0, 2.0, (size, size, 3))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def _seed_two_classes(tmp_path, engine, patch_px=512, per_class=3):
    """'dense' (many nuclei) vs 'sparse' (few), as real patch PNGs."""
    slide_id = SlideRepo(engine).register(
        source_kind="pillow", name="synthetic",
        path=str(tmp_path / "slide.png"), width=4096, height=4096,
    )
    repo = ROIRepo(engine)
    roi = ROI(kind="rect", points=((0.0, 0.0), (float(patch_px), float(patch_px))))
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir(exist_ok=True)
    for i in range(per_class):
        for label, blobs in (("dense", 60), ("sparse", 4)):
            p = patch_dir / f"{label}{i}.png"
            he_image(seed=i, size=patch_px, n_blobs=blobs).save(p)
            repo.add(slide_id, roi, label=label, patch_path=str(p))
    return slide_id


def _engine(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'raster.db'}")
    init_db(engine)
    return engine


def test_pixel_geometry_features_no_longer_track_the_raster(tmp_path):
    """The mechanism, isolated: identical pixels, only the raster differs.

    Asserted on ``nuclei_mean_area_px`` because it is pure pixel geometry --
    a nucleus of the same tissue covers 4x the pixels at twice the raster --
    which is exactly the dependence that made a heatmap tile unscoreable by a
    model fit on ROI patches. Measured over the four rasters below:

        before: 4973.7  1263.2   325.5    80.7   (62x)
        after:   324.8   325.0   325.5   318.5   (1.02x)

    ``blur_score`` is deliberately NOT asserted on: a tile that was really
    read at 128 px IS blurrier, and no amount of resampling can put the
    detail back. That is information loss, not a raster artifact.
    """
    engine = _engine(tmp_path)
    _seed_two_classes(tmp_path, engine)
    train_from_annotations(engine, name="raster", models_dir=tmp_path / "models")
    _model, meta = load_model("raster", tmp_path / "models")

    big = he_image(seed=99, size=1024, n_blobs=60)
    idx = FEATURE_NAMES.index("nuclei_mean_area_px")
    values = {
        n: float(
            _feature_vector_for_model(meta, big.resize((n, n), Image.LANCZOS))[idx]
        )
        for n in (1024, 512, 256, 128)
    }
    spread = max(values.values()) / min(values.values())
    assert spread < 1.25, (
        "nuclei_mean_area_px still scales with the raster the tile happens "
        f"to arrive at ({values}); a model fit on ROI patches is being asked "
        "to score heatmap tiles from a different distribution"
    )


def test_the_training_raster_is_recorded_in_the_meta(tmp_path):
    """Nothing could even DETECT the mismatch: the meta never named a raster."""
    engine = _engine(tmp_path)
    _seed_two_classes(tmp_path, engine)
    info = train_from_annotations(
        engine, name="raster", models_dir=tmp_path / "models"
    )
    assert info.feature_raster == FEATURE_RASTER
    meta = json.loads(
        Path(tmp_path / "models" / "raster" / "meta.json").read_text("utf-8")
    )
    assert meta["feature_raster"] == FEATURE_RASTER


def test_a_meta_without_a_raster_is_scored_exactly_as_before(tmp_path):
    """Backwards compatibility: models trained before the key are untouched.

    Those were fit at whatever raster their patches happened to have, so
    resampling THEM here would introduce the very mismatch this guards
    against.
    """
    img = he_image(seed=3, size=192, n_blobs=12)
    assert np.array_equal(
        _feature_vector_for_model({}, img), extract_features(img.convert("RGB"))
    )


@pytest.fixture(scope="module")
def metric_trained_at_1024(tmp_path_factory):
    """P(dense) from a model trained at ``extract_patch``'s 1024 px cap.

    1024 px is what ``rois.extract_patch`` produces for any ROI at least that
    big, i.e. what "Send to code agent" writes into ``agent_out/patches/``.
    """
    tmp_path = tmp_path_factory.mktemp("raster_e2e")
    engine = _engine(tmp_path)
    _seed_two_classes(tmp_path, engine, patch_px=1024)
    train_from_annotations(engine, name="raster", models_dir=tmp_path / "models")
    model, meta = load_model("raster", tmp_path / "models")
    return make_prob_metric(model, meta, "dense")


@pytest.mark.parametrize("tile_px", [512, 256, 128])
@pytest.mark.parametrize("label,blobs", [("dense", 60), ("sparse", 4)])
def test_the_class_decision_does_not_depend_on_tile_size(
    metric_trained_at_1024, tile_px, label, blobs
):
    """End to end: the same pixels, scored as an ROI patch and as a tile.

    ``compute_grid`` hands ``make_prob_metric`` whatever ``tile`` px the user
    picked, so this is exactly the path ``model_prob:<label>`` takes. Measured
    before the fix, P(dense) on a patch that IS sparse: 0.0165 at the 1024 px
    training raster, 0.2942 at 512, 0.5559 at 256, 0.5812 at 128 -- the class
    flips on pure resampling, so the heatmap is decided by tile geometry.
    """
    metric = metric_trained_at_1024
    img = he_image(seed=7, size=1024, n_blobs=blobs)
    as_a_patch = metric(img)
    as_a_tile = metric(img.resize((tile_px, tile_px), Image.LANCZOS))
    expected = label == "dense"

    assert (as_a_patch > 0.5) is expected, "the held-out patch is not learned"
    assert (as_a_tile > 0.5) is expected, (
        f"the same {label} tissue read as a {tile_px} px tile is classified "
        f"as the OTHER class (P(dense)={as_a_tile:.4f} vs {as_a_patch:.4f} at "
        "the training raster)"
    )
    assert abs(as_a_tile - as_a_patch) < 0.15, (
        f"P(dense) moved {abs(as_a_tile - as_a_patch):.4f} from resampling "
        f"alone ({as_a_patch:.4f} -> {as_a_tile:.4f})"
    )
