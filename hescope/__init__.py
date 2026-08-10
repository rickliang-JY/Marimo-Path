"""HE-Scope: marimo H&E pathology image viewer platform."""

from .agent_bridge import (
    AgentBridge,
    ROIPayload,
    magnification_for,
    make_live_selection_tool,
    make_marimo_tool,
    selection_stats,
)
from .rois import (
    ROI,
    ViewportState,
    extract_patch,
    patch_mpp,
    roi_stats,
    viewport_transform,
)
from .slides import (
    OpenSlideSource,
    PillowSource,
    SlideSource,
    TifffileSource,
    best_level_for_downsample,
    open_slide,
)
from .tcga import GDC_API, GDCClient, SlideCatalog, SlideRecord

# --- SPEC-ML analytics (Parts A + B): lazy-ish re-exports --------------------
# The analytics modules only need numpy/scipy/scikit-image (+ joblib /
# scikit-learn for ml); importing them here keeps ``hescope.detect_nuclei``
# etc. available to agents without an extra import step.
from .embeddings import (
    ENCODERS,
    EncoderSpec,
    default_encoder_name,
    embed_tiles,
    list_encoders,
    load_encoder,
)
from .features import FEATURE_DIM, FEATURE_NAMES, extract_embedding, extract_features
from .grid import grid_shape, iter_grid, tissue_fraction_proxy
from .heatmap import (
    compute_grid,
    get_colormap_lut,
    grid_bbox_to_level0,
    grid_coverage,
    render_heatmap,
)
from .ml import (
    ModelInfo,
    list_models,
    load_model,
    make_prob_metric,
    predict_patch,
    train_from_annotations,
)
from .nuclei import NucleiStats, detect_nuclei
from .qc import blur_score, qc_report, tissue_mask
from .stain import (
    STAIN_METHODS,
    fit_reference,
    macenko_normalize,
    normalize_stain,
    reinhard_normalize,
    ruifrok_normalize,
    vahadane_normalize,
)


def analysis_capabilities(models_dir: str = "data/models") -> dict:
    """JSON-serializable description of the available analysis features.

    Logic backing the app's zero-arg ``get_analysis_capabilities()`` tool
    (kept here so it is testable without running the marimo kernel). Never
    raises: any failure yields ``{"error": str(exc)}``.

    ``torch_embedding_available`` is probed via ``importlib.util.find_spec``
    only — torch/torchvision are NOT imported, so no model-weight download
    can be triggered by this call.
    """
    try:
        import importlib.util
        import json

        torch_ok = all(
            importlib.util.find_spec(pkg) is not None
            for pkg in ("torch", "torchvision")
        )
        result = {
            "analyses": [
                "selection_stats",      # hescope.agent_bridge.selection_stats
                "detect_nuclei",        # hescope.nuclei.detect_nuclei
                "qc_report",            # hescope.qc.qc_report
                "extract_features",     # hescope.features.extract_features
                "STAIN_METHODS",
    "macenko_normalize",
    "normalize_stain",
    "ruifrok_normalize",
    "vahadane_normalize",    # hescope.stain.macenko_normalize
                "ruifrok_normalize",    # hescope.stain.ruifrok_normalize
                "vahadane_normalize",   # hescope.stain.vahadane_normalize
                "reinhard_normalize",   # hescope.stain.reinhard_normalize
                "compute_grid",         # hescope.heatmap.compute_grid
                "render_heatmap",       # hescope.heatmap.render_heatmap
                "train_from_annotations",  # hescope.ml.train_from_annotations
                "predict_patch",        # hescope.ml.predict_patch
            ],
            "torch_embedding_available": bool(torch_ok),
            "available_encoders": {
                "default": default_encoder_name(),
                "torch_importable": bool(torch_ok),
                "encoders": list_encoders(),
            },
            "models": list_models(models_dir),
        }
        json.dumps(result)  # guarantee JSON-serializable for tool transport
        return result
    except Exception as exc:  # never raise from a capability probe
        return {"error": f"{type(exc).__name__}: {exc}"}


__all__ = [
    "AgentBridge",
    "ENCODERS",
    "EncoderSpec",
    "FEATURE_DIM",
    "FEATURE_NAMES",
    "GDC_API",
    "GDCClient",
    "ModelInfo",
    "NucleiStats",
    "OpenSlideSource",
    "PillowSource",
    "ROI",
    "ROIPayload",
    "SlideCatalog",
    "SlideRecord",
    "SlideSource",
    "TifffileSource",
    "ViewportState",
    "analysis_capabilities",
    "best_level_for_downsample",
    "blur_score",
    "compute_grid",
    "default_encoder_name",
    "detect_nuclei",
    "embed_tiles",
    "extract_embedding",
    "extract_features",
    "extract_patch",
    "fit_reference",
    "get_colormap_lut",
    "grid_bbox_to_level0",
    "grid_coverage",
    "grid_shape",
    "iter_grid",
    "list_encoders",
    "list_models",
    "load_encoder",
    "load_model",
    "macenko_normalize",
    "magnification_for",
    "make_live_selection_tool",
    "make_marimo_tool",
    "make_prob_metric",
    "open_slide",
    "patch_mpp",
    "predict_patch",
    "qc_report",
    "reinhard_normalize",
    "render_heatmap",
    "roi_stats",
    "selection_stats",
    "tissue_fraction_proxy",
    "tissue_mask",
    "train_from_annotations",
    "viewport_transform",
]
