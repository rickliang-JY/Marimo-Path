"""Quality-control helpers for H&E patches (Part A.4).

* :func:`tissue_mask` — Otsu on the HSV saturation channel + morphology cleanup.
* :func:`blur_score` — variance of the Laplacian (higher = sharper).
* :func:`qc_report` — small dict summary with a documented blur threshold.

Blur threshold calibration
--------------------------
``BLUR_THRESHOLD = 100.0`` was calibrated on the synthetic 256x256 test
fixtures (pink background + hard-edged purple blobs): sharp fixtures score in
the thousands, while the same fixtures blurred with a Gaussian sigma of 3 drop
well below 100. It is intended for ~256 px patches.
"""

from __future__ import annotations

import numpy as np
import PIL.Image
from scipy import ndimage as ndi
from skimage.color import rgb2hsv
from skimage.filters import threshold_otsu
from skimage.morphology import binary_closing, disk, remove_small_objects

#: Blur threshold: blur_score below this on a ~256 px patch => is_blurry.
BLUR_THRESHOLD: float = 100.0


def tissue_mask(img: PIL.Image.Image) -> np.ndarray:
    """Boolean tissue mask: Otsu on the HSV saturation channel + cleanup.

    Saturated (non-white) pixels are tissue; the mask is closed with a disk(2)
    footprint, holes are filled, and objects smaller than 64 px are removed.
    Returns a bool array the size of the input image.
    """
    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    sat = rgb2hsv(rgb)[..., 1]
    try:
        thr = threshold_otsu(sat)
    except ValueError:  # constant image
        return np.zeros(sat.shape, dtype=bool)
    mask = sat > thr
    if not mask.any():
        return np.zeros(sat.shape, dtype=bool)
    mask = binary_closing(mask, footprint=disk(2))
    mask = ndi.binary_fill_holes(mask)
    mask = remove_small_objects(mask, min_size=64)
    return mask.astype(bool)


def blur_score(img: PIL.Image.Image) -> float:
    """Variance of the Laplacian of the grayscale image (higher = sharper).

    Computed with ``scipy.ndimage.laplace`` on a float grayscale image in
    [0, 255]. See :data:`BLUR_THRESHOLD` for the calibrated QC threshold.
    """
    gray = np.asarray(img.convert("L"), dtype=np.float64)
    return float(ndi.laplace(gray).var())


def qc_report(img: PIL.Image.Image, *, mpp: float | None = None) -> dict:
    """Small QC summary dict.

    Keys: ``tissue_fraction`` (from :func:`tissue_mask`), ``blur_score``
    (Laplacian variance), ``is_blurry`` (``blur_score < BLUR_THRESHOLD``;
    threshold calibrated on synthetic 256 px fixtures, see module docstring),
    ``brightness_mean`` (mean luminance in [0, 255]). ``mpp`` is accepted for
    API symmetry but currently unused.
    """
    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    mask = tissue_mask(img)
    score = blur_score(img)
    return {
        "tissue_fraction": float(mask.mean()),
        "blur_score": score,
        "is_blurry": bool(score < BLUR_THRESHOLD),
        "brightness_mean": float(rgb.mean()),
    }
