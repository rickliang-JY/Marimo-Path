"""Synthetic H&E-like demo slide generation for HE-Scope.

6000x4000 px, deterministic (seed=42): pink stroma blobs + purple nuclei
clusters built from low-resolution noise upsampled and blurred, then
thresholded. Fully vectorized (numpy + skimage), runs in a few seconds.

This lives INSIDE the hescope package (not tools/) so it ships with the
wheel: ``tools/make_demo_slide.py`` is not packaged, and a non-editable
install (pip/uvx) must still be able to generate the demo slide.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

W, H = 6000, 4000
SEED = 42


def _smooth_noise(rng: np.random.Generator, small: tuple[int, int], sigma: float) -> np.ndarray:
    """Low-res random field upsampled to (H, W) and gaussian-blurred, in [0,1]."""
    from skimage.filters import gaussian

    field = rng.random((small[1], small[0]), dtype=np.float32)
    up = np.asarray(
        Image.fromarray((field * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR),
        dtype=np.float32,
    ) / 255.0
    up = gaussian(up, sigma=sigma, preserve_range=True).astype(np.float32)
    lo, hi = float(up.min()), float(up.max())
    return (up - lo) / max(hi - lo, 1e-6)


def generate() -> np.ndarray:
    """Return the (H, W, 3) uint8 demo slide array (deterministic)."""
    from skimage.filters import gaussian

    rng = np.random.default_rng(SEED)

    # Pink stroma blobs: smooth large-scale density field.
    stroma_field = _smooth_noise(rng, (94, 63), sigma=4.0)
    stroma = stroma_field > 0.48

    # Nuclei clusters: a second smooth field defines cluster regions.
    cluster_field = _smooth_noise(rng, (300, 200), sigma=3.0)
    clusters = cluster_field > 0.58

    # Nuclei: dense random dots inside clusters, blurred into cell-sized blobs.
    idx = np.flatnonzero(clusters.ravel())
    chosen = idx[rng.random(idx.size) < 0.30] if idx.size else idx
    dots = np.zeros(H * W, dtype=np.float32)
    dots[chosen] = 1.0
    dots = dots.reshape(H, W)
    dots = gaussian(dots, sigma=2.0, preserve_range=True).astype(np.float32)
    nuclei = dots > 0.15
    # Darker nucleoli cores.
    cores = dots > 0.45

    # Fine-grain texture noise.
    grain = rng.normal(0.0, 5.0, (H, W, 1)).astype(np.float32)

    # Compose.
    img = np.zeros((H, W, 3), dtype=np.float32)
    img[:] = (246.0, 230.0, 236.0)  # light eosin background
    # Stroma: deeper pink with variation from the density field.
    depth = (stroma_field - 0.48).clip(0, 1)[:, :, None]
    stroma_color = np.array((228.0, 155.0, 190.0), dtype=np.float32)
    img = np.where(stroma[:, :, None], img * 0.30 + stroma_color * 0.70 - depth * 20.0, img)
    # Nuclei: purple (hematoxylin), darker cores.
    img[nuclei] = (110.0, 60.0, 150.0)
    img[cores] = (80.0, 40.0, 120.0)
    img += grain
    return np.clip(img, 0, 255).astype(np.uint8)


def generate_demo_slide(path: str | Path) -> Path:
    """Generate the demo slide PNG at ``path`` (parents auto-created)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    arr = generate()
    Image.fromarray(arr, "RGB").save(out, format="PNG")
    return out
