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


def _hed_channel_gray(img: Image.Image, index: int) -> Image.Image:
    """rgb2hed channel inverted-normalized to 0..255 (stain-dense = dark)."""
    from skimage.color import rgb2hed

    arr = np.asarray(img.convert("RGB"), dtype=np.float64) / 255.0
    hed = rgb2hed(arr)[..., index]
    lo = float(hed.min())
    hi = float(hed.max())
    if hi > lo:
        norm = (hed - lo) / (hi - lo)
    else:
        norm = np.zeros_like(hed)
    gray = ((1.0 - norm) * 255.0).astype(np.uint8)
    return Image.fromarray(gray, "L")


def channel_view(
    img: Image.Image,
    channel: Literal["rgb", "r", "g", "b", "hematoxylin", "eosin"],
) -> Image.Image:
    """Return a channel view of ``img``.

    'rgb' = passthrough copy. 'r'/'g'/'b' = single channel as grayscale
    (mode 'L'). 'hematoxylin'/'eosin' = skimage rgb2hed channel 0/1,
    inverted-normalized so stain-dense tissue is dark, mode 'L'.
    """
    if channel == "rgb":
        return img.convert("RGB").copy()
    if channel in ("r", "g", "b"):
        idx = {"r": 0, "g": 1, "b": 2}[channel]
        return img.convert("RGB").split()[idx].copy()
    if channel == "hematoxylin":
        return _hed_channel_gray(img, 0)
    if channel == "eosin":
        return _hed_channel_gray(img, 1)
    raise ValueError(f"unknown channel: {channel!r}")
