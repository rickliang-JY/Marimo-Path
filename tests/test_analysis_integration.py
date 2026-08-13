"""SPEC-ML Part C integration tests (offline, tmp dirs only).

Acceptance item 3 (E2E): synthetic slide -> register slide + 4 ROIs with 2
labels (SlideRepo/ROIRepo + real patch PNGs) -> ml.train_from_annotations ->
ModelInfo with 2 labels -> compute_grid with the tissue metric on the
synthetic slide -> shape == grid_shape -> render_heatmap onto a thumbnail ->
size matches. Plus the analysis-capabilities tool logic (hescope-level:
find_spec probe + list_models) returns valid JSON with an "analyses" key.

Uses the REAL hescope.analysis.features.extract_features (no stubbing): the patches
are tiny, so the whole module stays well under the 5s budget.
"""

from __future__ import annotations

import json

import numpy as np
from PIL import Image, ImageDraw

import hescope
from hescope.store.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.analysis.grid import grid_shape, tissue_fraction_proxy
from hescope.analysis.heatmap import compute_grid, render_heatmap
from hescope.analysis.ml import train_from_annotations
from hescope.core.rois import ROI
from hescope.wsi.slides import open_slide

TILE = 128
DS = 2.0
CELL = int(TILE * DS)  # 256 level-0 px per grid cell


def _make_slide(tmp_path, size=(512, 384)):
    """White slide with a dark 'tissue' rectangle in grid cell (0, 0)."""
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([16, 16, CELL - 16, CELL - 16], fill=(120, 40, 140))
    path = tmp_path / "slide.png"
    img.save(path)
    return path


def _patch(path, color, jitter=0):
    img = Image.new("RGB", (48, 48), tuple(int(c) + jitter for c in color))
    img.save(path)
    return str(path)


def _seed_db(tmp_path, engine):
    """Register the synthetic slide + 4 labeled ROIs (2 labels x 2 patches)."""
    slide_path = _make_slide(tmp_path)
    slide_id = SlideRepo(engine).register(
        source_kind="pillow",
        name="synthetic",
        path=str(slide_path),
        width=512,
        height=384,
    )
    repo = ROIRepo(engine)
    roi = ROI(kind="rect", points=((16.0, 16.0), (240.0, 240.0)))
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir(exist_ok=True)
    for i in range(2):
        repo.add(
            slide_id, roi, label="tumor",
            patch_path=_patch(patch_dir / f"tumor{i}.png", (210, 40, 40), i),
        )
        repo.add(
            slide_id, roi, label="stroma",
            patch_path=_patch(patch_dir / f"stroma{i}.png", (40, 40, 210), i),
        )
    return slide_path, slide_id


def test_e2e_train_and_heatmap(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    slide_path, _slide_id = _seed_db(tmp_path, engine)

    # 1) Train a classifier from the labeled ROIs (real feature extraction).
    models_dir = tmp_path / "models"
    info = train_from_annotations(engine, name="e2e", models_dir=models_dir)
    assert sorted(info.labels) == ["stroma", "tumor"]
    assert info.n_samples == 4
    assert info.feature_dim == hescope.FEATURE_DIM
    assert info.cv_accuracy is None or 0.0 <= info.cv_accuracy <= 1.0

    # 2) Tissue-fraction heatmap grid over the synthetic slide.
    src = open_slide(slide_path)
    expected_shape = grid_shape(src, tile=TILE, downsample=DS)
    assert expected_shape == (2, 2)
    progress_calls = []
    grid = compute_grid(
        src,
        tissue_fraction_proxy,
        tile=TILE,
        downsample=DS,
        progress_cb=lambda d, t: progress_calls.append((d, t)),
    )
    assert grid.shape == expected_shape
    assert progress_calls and progress_calls[-1] == (4, 4)
    # The tissue cell (0, 0) scored; the blank cells were skipped (NaN).
    assert not np.isnan(grid[0, 0]) and grid[0, 0] > 0.5
    assert np.isnan(grid[1, 1])

    # 3) Render the grid onto a thumbnail: size must match the base image.
    thumb = src.get_thumbnail((200, 150)).convert("RGB")
    blended = render_heatmap(thumb, grid)
    assert blended.size == thumb.size
    assert blended.mode == "RGB"

    # 4) Capabilities tool logic reflects the freshly trained model.
    caps = hescope.analysis_capabilities(str(models_dir))
    assert "analyses" in caps and "error" not in caps
    assert "train_from_annotations" in caps["analyses"]
    assert isinstance(caps["torch_embedding_available"], bool)
    assert [m["name"] for m in caps["models"]] == ["e2e"]
    assert sorted(caps["models"][0]["labels"]) == ["stroma", "tumor"]


def test_analysis_capabilities_empty_and_serializable(tmp_path):
    """Zero-arg-tool logic: never raises, valid JSON, 'analyses' key."""
    caps = hescope.analysis_capabilities(str(tmp_path / "no_such_dir"))
    assert isinstance(caps, dict)
    assert "analyses" in caps and isinstance(caps["analyses"], list)
    assert caps["models"] == []
    assert isinstance(caps["torch_embedding_available"], bool)
    parsed = json.loads(json.dumps(caps))  # round-trips as JSON
    assert parsed["analyses"] == caps["analyses"]


def test_analytics_reexports_present():
    """hescope/__init__ re-exports the Part A/B public API (SPEC-ML C.1)."""
    for name in (
        # stain
        "macenko_normalize", "fit_reference", "reinhard_normalize",
        # nuclei / qc
        "detect_nuclei", "NucleiStats", "qc_report", "tissue_mask",
        "blur_score",
        # features
        "extract_features", "extract_embedding", "FEATURE_NAMES",
        "FEATURE_DIM",
        # grid / heatmap
        "iter_grid", "grid_shape", "tissue_fraction_proxy", "compute_grid",
        "render_heatmap", "grid_bbox_to_level0",
        # ml
        "ModelInfo", "train_from_annotations", "load_model", "predict_patch",
        "make_prob_metric", "list_models",
        # capabilities helper
        "analysis_capabilities",
    ):
        assert hasattr(hescope, name), name
        assert name in hescope.__all__, name
