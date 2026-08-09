"""Stain normalization for H&E images (Part A.2).

Two methods, both deterministic and implemented with numpy/scikit-image only:

* Macenko stain normalization (from scratch, no external stain-norm package).
* Reinhard normalization in CIE-LAB space.

Constants
---------
STANDARD_STAIN_MATRIX
    Fallback 2x3 H&E stain matrix (rows: hematoxylin, eosin; columns: R, G, B
    optical-density vectors). Widely used reference values from the color
    deconvolution literature (Ruifrok & Johnston style H&E vectors).
REINHARD_REF_MEAN / REINHARD_REF_STD
    Default LAB target statistics used by :func:`reinhard_normalize` when no
    reference is given. Empirical H&E patch statistics (L in [0, 100], a/b in
    the skimage rgb2lab range); documented here so results are reproducible.
"""

from __future__ import annotations

import numpy as np
import PIL.Image
from skimage.color import lab2rgb, rgb2lab

# --- documented constants ----------------------------------------------------

#: Standard H&E stain vectors (rows: H, E) in optical-density space.
STANDARD_STAIN_MATRIX: list[list[float]] = [
    [0.650, 0.704, 0.286],
    [0.072, 0.990, 0.105],
]

#: Default Reinhard LAB target statistics (documented empirical H&E values).
REINHARD_REF_MEAN: tuple[float, float, float] = (62.0, 14.0, 12.0)
REINHARD_REF_STD: tuple[float, float, float] = (8.0, 5.0, 5.0)

#: Io background intensity used in the OD transform (255 = white).
_IO = 255.0

#: Minimum number of tissue pixels required to run the SVD reliably.
_MIN_TISSUE_PIXELS = 10


def _to_rgb_array(img: PIL.Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), dtype=np.uint8)


def _optical_density(rgb: np.ndarray) -> np.ndarray:
    """OD = -ln((I + 1) / 256), per pixel per channel; shape (n_pixels, 3)."""
    flat = rgb.reshape(-1, 3).astype(np.float64)
    return -np.log((flat + 1.0) / (_IO + 1.0))


def _tissue_pixels(od: np.ndarray, luminance_threshold: float) -> np.ndarray:
    """Select tissue pixels whose optical density exceeds -ln(threshold).

    A normalized luminance of ``luminance_threshold`` (default 0.85) corresponds
    to an optical density of ``-ln(0.85) ~= 0.163``; pixels darker (higher OD)
    than near-white background are kept for the SVD. This is the classic Macenko
    "beta" tissue-gating step expressed as a luminance threshold.
    """
    od_threshold = -np.log(float(luminance_threshold))
    mask = od.mean(axis=1) > od_threshold
    return od[mask]


def _stain_matrix_from_od(od_tissue: np.ndarray) -> np.ndarray:
    """Macenko stain matrix estimation via SVD + angular percentiles.

    SVD on the tissue OD pixels; project onto the plane of the first two right
    singular vectors; take the 1st/99th percentile angles as the extreme stain
    directions. Row 0 is the hematoxylin-like vector (larger red OD component,
    matching standard H&E vectors), row 1 the eosin-like vector.
    """
    if od_tissue.shape[0] < _MIN_TISSUE_PIXELS:
        return np.array(STANDARD_STAIN_MATRIX, dtype=np.float64)
    _, _, vt = np.linalg.svd(od_tissue, full_matrices=False)
    v1, v2 = vt[0], vt[1]
    angles = np.arctan2(od_tissue @ v2, od_tissue @ v1)
    phi_lo = np.percentile(angles, 1.0)
    phi_hi = np.percentile(angles, 99.0)
    v_lo = np.cos(phi_lo) * v1 + np.sin(phi_lo) * v2
    v_hi = np.cos(phi_hi) * v1 + np.sin(phi_hi) * v2
    # Hematoxylin first: it has the larger red-channel OD component.
    if v_lo[0] > v_hi[0]:
        he = np.array([v_lo, v_hi], dtype=np.float64)
    else:
        he = np.array([v_hi, v_lo], dtype=np.float64)
    return he


def _concentrations(od: np.ndarray, stain_matrix: np.ndarray) -> np.ndarray:
    """Least-squares stain concentrations; returns (2, n_pixels), non-negative."""
    conc, *_ = np.linalg.lstsq(stain_matrix.T, od.T, rcond=None)
    return np.clip(conc, 0.0, None)


def fit_reference(img: PIL.Image.Image, *, luminance_threshold: float = 0.85) -> dict:
    """Fit Macenko reference statistics on ``img``.

    Returns ``{"stain_matrix": [[..], [..]], "max_conc": [.., ..]}`` where
    ``max_conc`` holds the 99th-percentile concentration of each stain.
    """
    rgb = _to_rgb_array(img)
    od = _optical_density(rgb)
    he = _stain_matrix_from_od(_tissue_pixels(od, luminance_threshold))
    conc = _concentrations(od, he)
    max_conc = np.percentile(conc, 99.0, axis=1)
    max_conc = np.maximum(max_conc, 1e-6)  # guard flat/blank images
    return {
        "stain_matrix": [[float(v) for v in row] for row in he],
        "max_conc": [float(v) for v in max_conc],
    }


def macenko_normalize(
    img: PIL.Image.Image,
    *,
    reference: dict | None = None,
    luminance_threshold: float = 0.85,
) -> PIL.Image.Image:
    """Macenko stain normalization.

    OD transform -> SVD on tissue pixels (OD above ``luminance_threshold``) ->
    1st/99th angular percentile stain vectors -> concentration 99th-percentile
    scaling. When ``reference`` is None, the reference statistics are fit from
    this image itself (see :func:`fit_reference`) and the image is normalized
    against its own statistics; otherwise the given reference dict
    (``{"stain_matrix", "max_conc"}``) is applied.
    """
    rgb = _to_rgb_array(img)
    h, w = rgb.shape[:2]
    od = _optical_density(rgb)
    src_he = _stain_matrix_from_od(_tissue_pixels(od, luminance_threshold))
    conc = _concentrations(od, src_he)
    src_max = np.maximum(np.percentile(conc, 99.0, axis=1), 1e-6)

    if reference is None:
        ref_he, ref_max = src_he, src_max
    else:
        ref_he = np.asarray(reference["stain_matrix"], dtype=np.float64)
        ref_max = np.asarray(reference["max_conc"], dtype=np.float64)

    conc *= (ref_max / src_max)[:, None]
    od_out = ref_he.T @ conc  # (3, n_pixels)
    out = (_IO + 1.0) * np.exp(-od_out) - 1.0
    out = np.clip(out, 0.0, 255.0).T.reshape(h, w, 3).astype(np.uint8)
    return PIL.Image.fromarray(out, "RGB")


def reinhard_normalize(
    img: PIL.Image.Image,
    *,
    ref_mean: tuple[float, float, float] | None = None,
    ref_std: tuple[float, float, float] | None = None,
) -> PIL.Image.Image:
    """Reinhard normalization in LAB space (skimage ``rgb2lab``).

    Each LAB channel is standardized to zero mean / unit variance and rescaled
    to the reference statistics. Defaults are the documented H&E constants
    :data:`REINHARD_REF_MEAN` / :data:`REINHARD_REF_STD`.
    """
    rgb = _to_rgb_array(img)
    lab = rgb2lab(rgb)
    mean = lab.reshape(-1, 3).mean(axis=0)
    std = lab.reshape(-1, 3).std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)  # flat-channel guard
    tgt_mean = np.asarray(ref_mean if ref_mean is not None else REINHARD_REF_MEAN)
    tgt_std = np.asarray(ref_std if ref_std is not None else REINHARD_REF_STD)
    out = (lab - mean) / std * tgt_std + tgt_mean
    out_rgb = np.clip(lab2rgb(out), 0.0, 1.0)
    return PIL.Image.fromarray((out_rgb * 255.0).round().astype(np.uint8), "RGB")
