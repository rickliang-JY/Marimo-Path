"""Nuclei detection for H&E patches (Part A.3).

Pipeline (deterministic): RGB -> color deconvolution (skimage ``rgb2hed``) ->
hematoxylin channel -> threshold (Otsu by default) -> morphology cleanup
(remove small objects, fill holes) -> distance-transform watershed to split
touching nuclei -> labeled mask + :class:`NucleiStats` via ``regionprops``.

Degenerate inputs (blank/white/flat images) yield an empty label mask and a
zero-count stats object; nothing raises.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import PIL.Image
from scipy import ndimage as ndi
from skimage.color import rgb2hed
from skimage.feature import peak_local_max
from skimage.filters import threshold_otsu
from skimage.measure import label, regionprops
from skimage.segmentation import watershed


def remove_small_objects_strict(binary: "np.ndarray", min_size: int) -> "np.ndarray":
    """Drop connected components smaller than ``min_size`` pixels.

    Replaces ``skimage.morphology.remove_small_objects(ar, min_size=...)``,
    whose ``min_size`` parameter is deprecated in scikit-image 0.26 and
    removed in 2.0. The successor is not a rename: ``max_size`` drops
    components *smaller than or equal to* the threshold (and defaults to 64),
    where ``min_size`` dropped only the strictly smaller ones. Doing this
    explicitly keeps the original strict ``area < min_size`` boundary, so
    nuclei counts do not shift under us.

    Uses ``ndi.label``'s default cross structure, which is the same
    4-connectivity as skimage's ``connectivity=1`` default.
    """
    if min_size <= 1:
        return binary
    lab, n = ndi.label(binary)
    if n == 0:
        return binary
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_size
    keep[0] = False  # background label
    return keep[lab]


@dataclass(frozen=True)
class NucleiStats:
    count: int
    density_per_mm2: float | None  # None when mpp unknown
    mean_area_px: float
    mean_intensity_h: float  # mean hematoxylin channel over nuclei
    mask_coverage: float  # fraction of patch covered by nuclei


def _empty_stats() -> NucleiStats:
    return NucleiStats(
        count=0,
        density_per_mm2=None,
        mean_area_px=0.0,
        mean_intensity_h=0.0,
        mask_coverage=0.0,
    )


def detect_nuclei(
    img: PIL.Image.Image,
    *,
    mpp: float | None = None,
    min_size_px: int = 20,
    h_threshold: float | None = None,
) -> tuple[np.ndarray, NucleiStats]:
    """Detect nuclei in an H&E patch.

    Returns ``(labels, stats)`` where ``labels`` is an int32 array the size of
    the input image (0 = background, 1..N = individual nuclei, touching blobs
    separated by watershed) and ``stats`` a :class:`NucleiStats`.

    ``mpp`` (microns per pixel) enables ``density_per_mm2``; when None the
    density is reported as None. ``h_threshold`` overrides the default Otsu
    threshold on the hematoxylin channel.
    """
    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    h_channel = rgb2hed(rgb)[..., 0]

    thr = float(h_threshold) if h_threshold is not None else None
    if thr is None:
        try:
            thr = float(threshold_otsu(h_channel))
        except ValueError:  # constant image
            thr = float("inf")
    binary = h_channel > thr

    if not binary.any():
        return np.zeros((h, w), dtype=np.int32), _empty_stats()

    # Morphology cleanup: drop specks, fill holes.
    clean = remove_small_objects_strict(binary, int(min_size_px))
    clean = ndi.binary_fill_holes(clean)
    if not clean.any():
        return np.zeros((h, w), dtype=np.int32), _empty_stats()

    # Watershed separation of touching nuclei.
    dist = ndi.distance_transform_edt(clean)
    min_dist = max(3, int(round(np.sqrt(float(min_size_px)))))
    coords = peak_local_max(dist, min_distance=min_dist, labels=clean)
    markers = np.zeros(dist.shape, dtype=np.int32)
    if coords.size:
        markers[tuple(coords.T)] = np.arange(1, len(coords) + 1)
        markers = ndi.label(markers > 0)[0]
        labels = watershed(-dist, markers, mask=clean)
    else:
        labels = label(clean)
    labels = labels.astype(np.int32)

    # Stats over watershed regions, keeping only regions >= min_size_px.
    props = [p for p in regionprops(labels, intensity_image=h_channel) if p.area >= min_size_px]
    count = len(props)
    final_mask = labels > 0
    coverage = float(final_mask.sum()) / float(h * w)
    if count:
        areas = np.array([p.area for p in props], dtype=np.float64)
        mean_area = float(areas.mean())
        mean_intensity = float(h_channel[final_mask].mean())
    else:
        mean_area = 0.0
        mean_intensity = 0.0

    density = None
    if mpp is not None:
        area_mm2 = (h * mpp) * (w * mpp) / 1e6
        density = float(count) / area_mm2 if area_mm2 > 0 else 0.0

    stats = NucleiStats(
        count=count,
        density_per_mm2=density,
        mean_area_px=mean_area,
        mean_intensity_h=mean_intensity,
        mask_coverage=coverage,
    )
    return labels, stats
