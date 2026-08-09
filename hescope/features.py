"""Per-tile feature extraction contract (Part A.5) for the ML/heatmap agents.

``extract_features`` returns a deterministic float32 vector of shape
``(FEATURE_DIM,)`` built from:

* per-HED-channel stats (mean/std/p10/p50/p90)            -> 15 features
* RGB 8-bin normalized histograms                          -> 24 features
* tissue_fraction, blur_score                              ->  2 features
* nuclei count / coverage / mean area (fast path, no
  watershed — plain connected components)                  ->  3 features
* GLCM contrast/homogeneity/correlation on a 64x64
  downsampled grayscale image quantized to 16 gray levels,
  fixed offsets distances=(1, 2) x angles=(0, pi/2)        -> 12 features

Total FEATURE_DIM = 56.

``extract_embedding`` is an OPTIONAL deep embedding: it lazily imports
torch/torchvision, builds a ResNet18 with ImageNet weights and returns the
512-d avgpool output (fc replaced by Identity). The first successful call may
download weights; the model is cached at module level. Any exception (missing
torch, missing weights, download failure, ...) yields ``None`` — it never
raises.
"""

from __future__ import annotations

import numpy as np
import PIL.Image
from scipy import ndimage as ndi
from skimage.color import rgb2gray, rgb2hed
from skimage.filters import threshold_otsu
from skimage.measure import label, regionprops
from skimage.morphology import remove_small_objects
from skimage.transform import resize

from .qc import blur_score, tissue_mask

# --- feature-name contract ----------------------------------------------------

_HED_CHANNELS = ("h", "e", "d")
_HED_STATS = ("mean", "std", "p10", "p50", "p90")
_GLCM_PROPS = ("contrast", "homogeneity", "correlation")
_GLCM_DISTANCES = (1, 2)
_GLCM_ANGLES = (0.0, float(np.pi) / 2.0)
_GLCM_ANGLE_TAGS = ("a0", "a90")
_GLCM_LEVELS = 16
_GLCM_SIZE = 64
_NUCLEI_MIN_SIZE_PX = 20

FEATURE_NAMES: list[str] = (
    [f"hed_{ch}_{stat}" for ch in _HED_CHANNELS for stat in _HED_STATS]
    + [f"hist_{ch}_{i}" for ch in ("r", "g", "b") for i in range(8)]
    + ["tissue_fraction", "blur_score"]
    + ["nuclei_count", "nuclei_coverage", "nuclei_mean_area_px"]
    + [
        f"glcm_{prop}_d{d}_{tag}"
        for prop in _GLCM_PROPS
        for d in _GLCM_DISTANCES
        for tag in _GLCM_ANGLE_TAGS
    ]
)
FEATURE_DIM: int = len(FEATURE_NAMES)


def _hed_features(rgb: np.ndarray) -> list[float]:
    hed = rgb2hed(rgb)
    flat = hed.reshape(-1, 3)
    out: list[float] = []
    for ch in range(3):
        col = flat[:, ch]
        out.extend(
            [
                float(col.mean()),
                float(col.std()),
                float(np.percentile(col, 10)),
                float(np.percentile(col, 50)),
                float(np.percentile(col, 90)),
            ]
        )
    return out


def _hist_features(rgb: np.ndarray) -> list[float]:
    out: list[float] = []
    for ch in range(3):
        hist, _ = np.histogram(rgb[..., ch], bins=8, range=(0, 256))
        total = hist.sum()
        out.extend((hist / total).astype(np.float64).tolist() if total else [0.0] * 8)
    return out


def _nuclei_features(rgb: np.ndarray) -> list[float]:
    """Fast nuclei summary: Otsu on the H channel + connected components.

    Deliberately avoids the watershed used by ``nuclei.detect_nuclei`` to keep
    per-tile feature extraction cheap.
    """
    h_channel = rgb2hed(rgb)[..., 0]
    try:
        thr = threshold_otsu(h_channel)
    except ValueError:
        return [0.0, 0.0, 0.0]
    binary = h_channel > thr
    if not binary.any():
        return [0.0, 0.0, 0.0]
    binary = remove_small_objects(binary, min_size=_NUCLEI_MIN_SIZE_PX)
    if not binary.any():
        return [0.0, 0.0, 0.0]
    labels = label(binary)
    areas = np.array([p.area for p in regionprops(labels)], dtype=np.float64)
    count = float(len(areas))
    coverage = float(binary.sum()) / float(binary.size)
    mean_area = float(areas.mean()) if areas.size else 0.0
    return [count, coverage, mean_area]


def _glcm_features(rgb: np.ndarray) -> list[float]:
    from skimage.feature import graycomatrix, graycoprops

    gray = rgb2gray(rgb)  # float [0, 1]
    small = resize(gray, (_GLCM_SIZE, _GLCM_SIZE), anti_aliasing=True, preserve_range=True)
    levels = np.clip((small * _GLCM_LEVELS).astype(np.uint8), 0, _GLCM_LEVELS - 1)
    glcm = graycomatrix(
        levels,
        distances=list(_GLCM_DISTANCES),
        angles=list(_GLCM_ANGLES),
        levels=_GLCM_LEVELS,
        symmetric=True,
        normed=True,
    )
    out: list[float] = []
    for prop in _GLCM_PROPS:
        vals = graycoprops(glcm, prop)  # shape (len(distances), len(angles))
        out.extend(float(v) for v in vals.ravel())
    return out


def extract_features(img: PIL.Image.Image) -> np.ndarray:
    """Deterministic handcrafted feature vector, shape ``(FEATURE_DIM,)`` float32.

    Designed to be fast (<0.5 s on a 256 px patch) since it runs per tile.
    """
    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    feats = (
        _hed_features(rgb)
        + _hist_features(rgb)
        + [float(tissue_mask(img).mean()), blur_score(img)]
        + _nuclei_features(rgb)
        + _glcm_features(rgb)
    )
    vec = np.asarray(feats, dtype=np.float32)
    if vec.shape[0] != FEATURE_DIM:  # pragma: no cover - contract guard
        raise RuntimeError(f"feature dim mismatch: {vec.shape[0]} != {FEATURE_DIM}")
    return vec


# --- optional deep embedding --------------------------------------------------

_EMBED_MODEL = None  # cached (model, device) tuple once loaded
_EMBED_FAILED = False  # fail fast after the first unsuccessful load attempt

_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


def extract_embedding(img: PIL.Image.Image) -> "np.ndarray | None":
    """Optional ResNet18 (ImageNet) avgpool embedding, 512-d float32.

    Lazily imports torch/torchvision and loads ImageNet weights (the first
    successful call downloads them; the model is cached at module level).
    Returns None on ANY exception (torch missing, weights unavailable, offline,
    ...) — this function never raises.
    """
    global _EMBED_MODEL, _EMBED_FAILED
    try:
        if _EMBED_MODEL is None:
            if _EMBED_FAILED:
                return None
            import torch
            import torchvision

            weights = torchvision.models.ResNet18_Weights.IMAGENET1K_V1
            model = torchvision.models.resnet18(weights=weights)
            model.fc = torch.nn.Identity()
            model.eval()
            _EMBED_MODEL = (model, torch)
        model, torch = _EMBED_MODEL

        rgb = img.convert("RGB").resize((224, 224), PIL.Image.BILINEAR)
        arr = np.asarray(rgb, dtype=np.float32) / 255.0
        arr = (arr - np.asarray(_IMAGENET_MEAN, dtype=np.float32)) / np.asarray(
            _IMAGENET_STD, dtype=np.float32
        )
        tensor = torch.from_numpy(arr.transpose(2, 0, 1)).unsqueeze(0)
        with torch.no_grad():
            emb = model(tensor).squeeze(0).cpu().numpy().astype(np.float32)
        return emb
    except Exception:
        _EMBED_FAILED = True
        return None
