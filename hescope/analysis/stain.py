"""Stain normalization for H&E images (Part A.2).

Four methods, all deterministic and implemented here rather than pulled in:

* **Macenko** -- stain matrix from the extremes of the OD angular distribution.
* **Reinhard** -- moment matching in CIE-LAB space (no deconvolution).
* **Ruifrok & Johnston** -- the published fixed H&E matrix, estimated from
  nothing, so two runs are comparable and a mostly-blank patch cannot mislead it.
* **Vahadane** -- sparse non-negative factorisation of the OD cloud, which holds
  up where the stains co-localise and Macenko's extremes degrade.

Only Vahadane needs anything beyond numpy/scikit-image, and it uses
scikit-learn's ``DictionaryLearning`` -- already a dependency of the ``.[ml]``
extra -- falling back to Macenko when that is absent. This is the whole of what
TIAToolbox would have contributed on this front; adopting it for these four
would have cost 152 installs and 22 uninstalls in this environment, including
ipywidgets, which the OpenSeadragon viewer sits on (docs/ROADMAP-INTEROP.md).

Macenko, Ruifrok and Vahadane differ ONLY in how the stain matrix is estimated;
they share one reconstruction path (:func:`_deconvolve_normalize`) so a fix
there cannot apply to some and not others.

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


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """Unit-length stain vectors. A stain direction is a direction; leaving the
    rows unnormalised would fold their magnitude into the concentrations and
    change the reconstruction."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms < 1e-12, 1.0, norms)


def _order_he(matrix: np.ndarray) -> np.ndarray:
    """Hematoxylin first, by the larger red-channel OD component.

    Every estimator here has to agree on row order, or a reference fitted with
    one method and applied with another silently swaps the two stains.
    """
    if matrix[0][0] >= matrix[1][0]:
        return matrix
    return matrix[::-1].copy()


def _stain_matrix_vahadane(
    od_tissue: np.ndarray, *, random_state: int = 0
) -> np.ndarray:
    """Vahadane stain matrix: sparse non-negative matrix factorisation.

    Macenko takes the extremes of the OD angular distribution, which assumes
    the two stains are separable at the edges of the cloud. Vahadane instead
    asks for a sparse non-negative dictionary, which holds up better where the
    stains co-localise -- nuclei in dense tissue, where the two contribute to
    the same pixel.

    Implemented with scikit-learn's ``DictionaryLearning`` because it is
    already a dependency of the ``ml`` extra; there is no reason to pull a
    heavyweight toolbox in for one factorisation. When scikit-learn is absent,
    or the fit does not converge to something usable, this falls back to the
    Macenko estimate rather than raising -- stain normalisation is a display
    and preprocessing aid, and failing it should not take a slide down with it.
    """
    if od_tissue.shape[0] < _MIN_TISSUE_PIXELS:
        return np.array(STANDARD_STAIN_MATRIX, dtype=np.float64)
    try:
        from sklearn.decomposition import DictionaryLearning
    except Exception:  # scikit-learn not installed (it lives in .[ml])
        return _stain_matrix_from_od(od_tissue)
    try:
        # Learn 2 atoms over the OD pixels. transform_alpha controls the
        # sparsity of the concentrations; 0.1 is the value the Vahadane
        # reference implementations use.
        # Coordinate descent, not LARS: sklearn refuses positive_code with
        # 'lars' ("Positive constraint not supported for 'lars' coding
        # method"), and the non-negativity is the whole point -- a stain
        # cannot contribute a negative amount of absorbance.
        learner = DictionaryLearning(
            n_components=2,
            alpha=0.1,
            transform_alpha=0.1,
            fit_algorithm="cd",
            transform_algorithm="lasso_cd",
            positive_dict=True,
            positive_code=True,
            max_iter=20,
            random_state=random_state,
        )
        # rows are observations; the learned dictionary rows are stain vectors
        learner.fit(od_tissue)
        stains = np.asarray(learner.components_, dtype=np.float64)
    except Exception:
        return _stain_matrix_from_od(od_tissue)
    if stains.shape != (2, 3) or not np.isfinite(stains).all():
        return _stain_matrix_from_od(od_tissue)
    if np.linalg.norm(stains, axis=1).min() < 1e-9:
        return _stain_matrix_from_od(od_tissue)  # a degenerate atom
    return _order_he(_normalize_rows(stains))


def fit_reference(
    img: PIL.Image.Image,
    *,
    method: str = "macenko",
    luminance_threshold: float = 0.85,
) -> dict:
    """Fit deconvolution reference statistics on ``img``.

    Returns ``{"stain_matrix": [[..], [..]], "max_conc": [.., ..]}`` where
    ``max_conc`` holds the 99th-percentile concentration of each stain.

    ``method`` selects the estimator (``macenko``, ``ruifrok`` or
    ``vahadane``) and must match the method the reference is later applied
    with: the two share a row order (hematoxylin first) but not a basis, so a
    Vahadane reference pushed through Macenko rescales against vectors that
    were never fitted to it.
    """
    key = (method or "").strip().lower()
    if key not in _MATRIX_ESTIMATORS:
        raise ValueError(
            f"unknown stain method {method!r}; expected one of "
            f"{', '.join(sorted(_MATRIX_ESTIMATORS))}"
        )
    rgb = _to_rgb_array(img)
    od = _optical_density(rgb)
    he = _MATRIX_ESTIMATORS[key](_tissue_pixels(od, luminance_threshold))
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
    return _deconvolve_normalize(
        img, _macenko_matrix, reference=reference,
        luminance_threshold=luminance_threshold,
    )


# --- the shared deconvolution path -----------------------------------------
#
# Macenko, Ruifrok and Vahadane differ ONLY in how they estimate the stain
# matrix; the OD transform, concentration solve, percentile scaling and
# reconstruction are identical. Keeping one implementation means a fix to the
# reconstruction cannot apply to some methods and not others -- this codebase
# has already had four findings of the "a second place re-deriving what one
# owner decides" class (bugs/SUMMARY.md).


def _macenko_matrix(od_tissue: np.ndarray) -> np.ndarray:
    return _stain_matrix_from_od(od_tissue)


def _ruifrok_matrix(_od_tissue: np.ndarray) -> np.ndarray:
    """Ruifrok & Johnston: the published fixed H&E matrix, estimated from
    nothing. Deterministic and immune to a patch that holds too little tissue
    to estimate from -- which is exactly when the data-driven methods are least
    trustworthy."""
    return _order_he(_normalize_rows(np.array(STANDARD_STAIN_MATRIX, dtype=np.float64)))


def _deconvolve_normalize(
    img: PIL.Image.Image,
    matrix_fn,
    *,
    reference: dict | None = None,
    luminance_threshold: float = 0.85,
) -> PIL.Image.Image:
    """OD -> concentrations -> rescale to the reference -> back to RGB."""
    rgb = _to_rgb_array(img)
    h, w = rgb.shape[:2]
    od = _optical_density(rgb)
    src_he = matrix_fn(_tissue_pixels(od, luminance_threshold))
    conc = _concentrations(od, src_he)
    src_max = np.maximum(np.percentile(conc, 99.0, axis=1), 1e-6)

    if reference is None:
        ref_he, ref_max = src_he, src_max
    else:
        ref_he = np.asarray(reference["stain_matrix"], dtype=np.float64)
        ref_max = np.asarray(reference["max_conc"], dtype=np.float64)

    conc = conc * (ref_max / src_max)[:, None]
    od_out = ref_he.T @ conc  # (3, n_pixels)
    out = (_IO + 1.0) * np.exp(-od_out) - 1.0
    out = np.clip(out, 0.0, 255.0).T.reshape(h, w, 3).astype(np.uint8)
    return PIL.Image.fromarray(out, "RGB")


def ruifrok_normalize(
    img: PIL.Image.Image,
    *,
    reference: dict | None = None,
    luminance_threshold: float = 0.85,
) -> PIL.Image.Image:
    """Ruifrok & Johnston colour deconvolution with the fixed H&E matrix.

    The stain vectors are the published constants rather than an estimate, so
    the result depends only on the pixels and not on what else happened to be
    in the patch. That makes it the right choice when a patch is mostly
    background, where Macenko's SVD is fitting noise, and the right baseline
    when two runs must be comparable.
    """
    return _deconvolve_normalize(
        img, _ruifrok_matrix, reference=reference,
        luminance_threshold=luminance_threshold,
    )


def vahadane_normalize(
    img: PIL.Image.Image,
    *,
    reference: dict | None = None,
    luminance_threshold: float = 0.85,
) -> PIL.Image.Image:
    """Vahadane structure-preserving normalization (sparse NMF stain matrix).

    Falls back to the Macenko estimate when scikit-learn is unavailable or the
    factorisation does not converge to a usable pair of stain vectors; see
    :func:`_stain_matrix_vahadane`.
    """
    return _deconvolve_normalize(
        img, _stain_matrix_vahadane, reference=reference,
        luminance_threshold=luminance_threshold,
    )


#: The stain-matrix estimator behind each method name.
_MATRIX_ESTIMATORS = {
    "macenko": _macenko_matrix,
    "ruifrok": _ruifrok_matrix,
    "vahadane": _stain_matrix_vahadane,
}

#: Every normalization this module offers, by name -- what a UI dropdown or an
#: agent should enumerate rather than hardcoding a list that drifts.
STAIN_METHODS: tuple[str, ...] = ("macenko", "reinhard", "ruifrok", "vahadane")


def normalize_stain(
    img: PIL.Image.Image,
    method: str = "macenko",
    *,
    reference: dict | None = None,
    luminance_threshold: float = 0.85,
) -> PIL.Image.Image:
    """Apply a stain normalization by name. Raises ValueError on an unknown
    name, listing the ones that exist."""
    key = (method or "").strip().lower()
    if key == "reinhard":
        return reinhard_normalize(img)
    if key not in _MATRIX_ESTIMATORS:
        raise ValueError(
            f"unknown stain method {method!r}; expected one of "
            f"{', '.join(STAIN_METHODS)}"
        )
    return _deconvolve_normalize(
        img, _MATRIX_ESTIMATORS[key], reference=reference,
        luminance_threshold=luminance_threshold,
    )


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
