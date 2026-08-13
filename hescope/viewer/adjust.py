"""Image adjustments (brightness/contrast/gamma) and H&E channel views.

Pure functions: the input image is never mutated.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from PIL import Image, ImageEnhance


def apply_adjustments(
    img: Image.Image,
    *,
    brightness: float = 1.0,
    contrast: float = 1.0,
    gamma: float = 1.0,
) -> Image.Image:
    """Apply gamma -> brightness -> contrast and return a new image.

    brightness/contrast use PIL ImageEnhance (1.0 = identity). gamma uses a
    numpy lookup table: out = 255 * (in / 255) ** (1 / gamma); gamma <= 0 is
    treated as 1.0.
    """
    out = img.copy()

    if gamma is None or gamma <= 0:
        gamma = 1.0
    if gamma != 1.0:
        arr = np.asarray(out.convert("RGB"), dtype=np.float64) / 255.0
        arr = np.clip(np.power(arr, 1.0 / gamma) * 255.0, 0, 255).astype(np.uint8)
        out = Image.fromarray(arr, "RGB")

    if brightness != 1.0:
        out = ImageEnhance.Brightness(out).enhance(brightness)
    if contrast != 1.0:
        out = ImageEnhance.Contrast(out).enhance(contrast)
    return out


_HED_INDEX = {"hematoxylin": 0, "eosin": 1}


def _hed_channel(img: Image.Image, index: int) -> np.ndarray:
    """Raw (un-normalized) rgb2hed channel as a float array."""
    from skimage.color import rgb2hed

    arr = np.asarray(img.convert("RGB"), dtype=np.float64) / 255.0
    return rgb2hed(arr)[..., index]


def _hed_channel_gray(
    img: Image.Image,
    index: int,
    lo: float | None = None,
    hi: float | None = None,
) -> Image.Image:
    """rgb2hed channel inverted-normalized to 0..255 (stain-dense = dark).

    ``lo``/``hi`` pin the normalization range. When they are None the range is
    this image's own min/max — correct for a whole viewport, WRONG for a tile:
    min-max normalizing a blank background tile stretches sensor noise across
    the full 0..255 range and renders empty slide as convincing fake structure
    (measured on synthetic background: std 31.4 alone vs 1.1 in context, up to
    246/255 per-pixel difference). Any tiled renderer must pass a slide-level
    range from :func:`fit_channel_reference`.
    """
    hed = _hed_channel(img, index)
    if lo is None:
        lo = float(hed.min())
    if hi is None:
        hi = float(hed.max())
    lo, hi = float(lo), float(hi)
    if hi > lo:
        norm = np.clip((hed - lo) / (hi - lo), 0.0, 1.0)
    else:
        norm = np.zeros_like(hed)
    gray = ((1.0 - norm) * 255.0).astype(np.uint8)
    return Image.fromarray(gray, "L")


def fit_channel_reference(
    img: Image.Image,
    channel: Literal["hematoxylin", "eosin"],
) -> tuple[float, float]:
    """Fit a pinned ``(lo, hi)`` normalization range for an H/E channel view.

    Intended to be called ONCE per slide on a thumbnail, so that every tile of
    that slide is normalized against the same range (see
    :func:`_hed_channel_gray` for why per-tile ranges are unsafe). Returns the
    raw rgb2hed channel min/max, i.e. exactly the range the un-pinned code path
    would have derived from the whole image.
    """
    if channel not in _HED_INDEX:
        raise ValueError(f"channel has no fitted reference: {channel!r}")
    hed = _hed_channel(img, _HED_INDEX[channel])
    return float(hed.min()), float(hed.max())


def channel_view(
    img: Image.Image,
    channel: Literal["rgb", "r", "g", "b", "hematoxylin", "eosin"],
    *,
    lo: float | None = None,
    hi: float | None = None,
) -> Image.Image:
    """Return a channel view of ``img``.

    'rgb' = passthrough copy. 'r'/'g'/'b' = single channel as grayscale
    (mode 'L'). 'hematoxylin'/'eosin' = skimage rgb2hed channel 0/1,
    inverted-normalized so stain-dense tissue is dark, mode 'L'.

    ``lo``/``hi`` pin the hematoxylin/eosin normalization range (see
    :func:`fit_channel_reference`); they are ignored by the other channels,
    which are pure per-pixel selections and therefore already tile-safe. With
    both None the behaviour is exactly the historical per-image min/max.
    """
    if channel == "rgb":
        return img.convert("RGB").copy()
    if channel in ("r", "g", "b"):
        idx = {"r": 0, "g": 1, "b": 2}[channel]
        return img.convert("RGB").split()[idx].copy()
    if channel in _HED_INDEX:
        return _hed_channel_gray(img, _HED_INDEX[channel], lo, hi)
    raise ValueError(f"unknown channel: {channel!r}")
