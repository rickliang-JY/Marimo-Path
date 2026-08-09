"""Slide sources and image pyramid handling for HE-Scope.

Openslide support is optional: if ``import openslide`` fails or the file is
not an openslide-compatible WSI, the Pillow backend is used. Nothing in this
module raises merely because openslide is missing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # pathology images are huge by design

try:  # optional dependency
    import openslide  # type: ignore

    _HAS_OPENSLIDE = True
except Exception:  # pragma: no cover - depends on environment
    openslide = None  # type: ignore
    _HAS_OPENSLIDE = False

try:  # optional dependency (SVS / pyramidal TIFF without openslide)
    import tifffile  # type: ignore

    _HAS_TIFFFILE = True
except Exception:  # pragma: no cover - depends on environment
    tifffile = None  # type: ignore
    _HAS_TIFFFILE = False

OPENSLIDE_EXTENSIONS = {".svs", ".ndpi", ".mrxs", ".tiff", ".tif"}
TIFFFILE_EXTENSIONS = {".svs", ".tiff", ".tif"}

_MPP_RE = re.compile(r"MPP\s*=\s*([0-9]*\.?[0-9]+)")


def _ensure_tifffile_zarr_compat() -> None:
    """Work around tifffile/zarr version skew.

    tifffile 2025.x does ``from zarr.core.chunk_grids import RegularChunkGrid``
    when ``tifffile.zarr`` is first imported (triggered by ``aszarr()``), but
    zarr >= 3.3 removed that symbol. tifffile only uses it inside
    ``tifffile.zarr.read_selection`` (which we never call), so injecting a
    placeholder is sufficient to make ``aszarr()`` + ``zarr.open`` work.
    """
    try:
        import zarr.core.chunk_grids as chunk_grids

        if not hasattr(chunk_grids, "RegularChunkGrid"):

            class RegularChunkGrid:  # pragma: no cover - shim placeholder
                def __init__(self, chunk_shape=None):
                    self.chunk_shape = chunk_shape

            chunk_grids.RegularChunkGrid = RegularChunkGrid  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive
        pass


@runtime_checkable
class SlideSource(Protocol):
    name: str
    dimensions: tuple[int, int]  # level-0 (w, h)
    level_count: int
    level_downsamples: tuple[float, ...]
    mpp: float | None  # microns-per-pixel if known else None

    def read_region(
        self, location: tuple[int, int], level: int, size: tuple[int, int]
    ) -> "Image.Image": ...

    def get_thumbnail(self, size: tuple[int, int]) -> "Image.Image": ...


class PillowSource:
    """Slide source backed by a single Pillow image.

    Builds an in-memory pyramid by 2x downsampling until min(dim) <= 512.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self.name = self._path.name
        base = Image.open(self._path).convert("RGB")
        base.load()
        self._levels: list[Image.Image] = [base]
        w, h = base.size
        while min(w, h) > 512:
            w = max(1, w // 2)
            h = max(1, h // 2)
            self._levels.append(self._levels[-1].resize((w, h), Image.LANCZOS))
        self.dimensions: tuple[int, int] = base.size
        self.level_count = len(self._levels)
        self.level_downsamples: tuple[float, ...] = tuple(
            float(2**i) for i in range(self.level_count)
        )
        self.mpp: float | None = None

    def read_region(
        self, location: tuple[int, int], level: int, size: tuple[int, int]
    ) -> "Image.Image":
        """Read a region of ``size`` at ``level``; ``location`` is in level-0
        coordinates. Clipped to slide bounds and padded with white so the
        result is always exactly ``size``."""
        img = self._levels[level]
        d = self.level_downsamples[level]
        x0, y0 = location
        w, h = size
        lx = int(round(x0 / d))
        ly = int(round(y0 / d))
        out = Image.new("RGB", (w, h), (255, 255, 255))
        sx0 = max(0, lx)
        sy0 = max(0, ly)
        sx1 = min(img.width, lx + w)
        sy1 = min(img.height, ly + h)
        if sx1 > sx0 and sy1 > sy0:
            crop = img.crop((sx0, sy0, sx1, sy1))
            out.paste(crop, (sx0 - lx, sy0 - ly))
        return out

    def get_thumbnail(self, size: tuple[int, int]) -> "Image.Image":
        thumb = self._levels[-1].copy()
        thumb.thumbnail(size, Image.LANCZOS)
        return thumb


class OpenSlideSource:
    """Slide source wrapping openslide.OpenSlide."""

    def __init__(self, path: str | Path) -> None:
        if not _HAS_OPENSLIDE:
            raise RuntimeError("openslide is not available")
        self._path = Path(path)
        self.name = self._path.name
        self._slide = openslide.OpenSlide(str(self._path))
        self.dimensions = tuple(self._slide.dimensions)  # type: ignore[assignment]
        self.level_count = int(self._slide.level_count)
        self.level_downsamples = tuple(float(d) for d in self._slide.level_downsamples)
        props = self._slide.properties
        try:
            mx = float(props.get(openslide.PROPERTY_NAME_MPP_X, "") or 0)
            my = float(props.get(openslide.PROPERTY_NAME_MPP_Y, "") or 0)
            self.mpp = (mx + my) / 2.0 if mx > 0 and my > 0 else None
        except (TypeError, ValueError):
            self.mpp = None

    def read_region(
        self, location: tuple[int, int], level: int, size: tuple[int, int]
    ) -> "Image.Image":
        d = self.level_downsamples[level]
        x0, y0 = location
        w, h = size
        img = self._slide.read_region((int(x0), int(y0)), level, (w, h)).convert("RGB")
        # openslide already pads; still clip/pad defensively to exact size
        out = Image.new("RGB", (w, h), (255, 255, 255))
        lw = max(1, int(round(self.dimensions[0] / d)))
        lh = max(1, int(round(self.dimensions[1] / d)))
        lx = int(round(x0 / d))
        ly = int(round(y0 / d))
        sx0 = max(0, lx)
        sy0 = max(0, ly)
        sx1 = min(lw, lx + w)
        sy1 = min(lh, ly + h)
        if sx1 > sx0 and sy1 > sy0:
            crop = img.crop((sx0 - lx, sy0 - ly, sx1 - lx + (sx1 - sx0), sy1 - ly + (sy1 - sy0)))
            out.paste(crop, (sx0 - lx, sy0 - ly))
        return out

    def get_thumbnail(self, size: tuple[int, int]) -> "Image.Image":
        return self._slide.get_thumbnail(size).convert("RGB")


class TifffileSource:
    """Slide source for Aperio SVS / tiled pyramidal TIFF via tifffile.

    Region reads go through tifffile's zarr store (``aszarr``) and zarr
    slicing, so reading a small region only decodes the tiles it overlaps;
    a whole level is never materialized in memory (memory-safe for ~1GB+
    slides).

    - levels: ``tf.series[0].levels`` (the pyramid); ``level_downsamples``
      are derived from level shapes relative to level 0.
    - mpp: parsed from ``MPP = <float>`` in the Aperio ImageDescription
      header if present.
    """

    def __init__(self, path: str | Path) -> None:
        if not _HAS_TIFFFILE:
            raise RuntimeError("tifffile is not available")
        _ensure_tifffile_zarr_compat()
        self._path = Path(path)
        self.name = self._path.name
        self._tf = tifffile.TiffFile(str(self._path))
        if not self._tf.series:
            raise ValueError(f"no image series in {self._path}")
        # pick the series that looks like a pyramid (most levels), else series 0
        series = self._tf.series[0]
        for s in self._tf.series:
            if len(getattr(s, "levels", [s])) > len(series.levels):
                series = s
        self._series = series
        self._levels = list(series.levels)
        self._zarr_arrays: list = [None] * len(self._levels)  # lazily opened
        self._zarr_stores: list = [None] * len(self._levels)

        axes = getattr(self._levels[0], "axes", "")
        self._y_idx = axes.index("Y") if "Y" in axes else max(0, len(self._levels[0].shape) - 3)
        self._x_idx = axes.index("X") if "X" in axes else max(1, len(self._levels[0].shape) - 2)
        shape0 = self._levels[0].shape
        w0 = int(shape0[self._x_idx])
        h0 = int(shape0[self._y_idx])
        self.dimensions: tuple[int, int] = (w0, h0)
        self.level_count = len(self._levels)
        self.level_downsamples: tuple[float, ...] = tuple(
            round(w0 / max(1, int(lv.shape[self._x_idx])), 4) for lv in self._levels
        )
        self.mpp = self._parse_mpp()

    def _parse_mpp(self) -> float | None:
        try:
            desc = self._tf.pages[0].description or ""
        except Exception:
            return None
        m = _MPP_RE.search(desc)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None

    def _zarr(self, level: int):
        """Return the zarr array for ``level`` (opened lazily, cached)."""
        if self._zarr_arrays[level] is None:
            import zarr

            store = self._series.aszarr(level=level)
            arr = zarr.open(store, mode="r")
            if not isinstance(arr, zarr.Array):  # defensive: group -> member "0"
                arr = arr["0"]
            self._zarr_stores[level] = store
            self._zarr_arrays[level] = arr
        return self._zarr_arrays[level]

    def read_region(
        self, location: tuple[int, int], level: int, size: tuple[int, int]
    ) -> "Image.Image":
        """Read a region of ``size`` at ``level``; ``location`` is in level-0
        coordinates. Clipped to slide bounds and padded with white so the
        result is always exactly ``size``. Only the overlapping tiles are
        decoded (zarr store slicing)."""
        d = self.level_downsamples[level]
        x0, y0 = location
        w, h = size
        lx = int(round(x0 / d))
        ly = int(round(y0 / d))
        arr = self._zarr(level)
        lw = int(arr.shape[self._x_idx])
        lh = int(arr.shape[self._y_idx])
        out = Image.new("RGB", (w, h), (255, 255, 255))
        sx0 = max(0, lx)
        sy0 = max(0, ly)
        sx1 = min(lw, lx + w)
        sy1 = min(lh, ly + h)
        if sx1 > sx0 and sy1 > sy0:
            # Slice through the SAME axis mapping the rest of this class uses
            # (_y_idx/_x_idx). Assuming axes 0 and 1 would silently read the
            # wrong axes for any series that does not lead with Y, X.
            slicer: list = [slice(None)] * arr.ndim
            slicer[self._y_idx] = slice(sy0, sy1)
            slicer[self._x_idx] = slice(sx0, sx1)
            block = np.asarray(arr[tuple(slicer)])
            if (self._y_idx, self._x_idx) != (0, 1) and block.ndim >= 2:
                rest = [
                    i for i in range(block.ndim)
                    if i not in (self._y_idx, self._x_idx)
                ]
                block = np.transpose(block, (self._y_idx, self._x_idx, *rest))
            if block.ndim > 3:  # collapse any trailing axes into channels
                block = block.reshape(block.shape[0], block.shape[1], -1)
            if block.ndim == 2:
                block = np.stack([block] * 3, axis=-1)
            if block.shape[-1] == 1:
                block = np.repeat(block, 3, axis=-1)
            if block.shape[-1] > 3:
                block = block[..., :3]
            crop = Image.fromarray(block.astype(np.uint8), "RGB")
            out.paste(crop, (sx0 - lx, sy0 - ly))
        return out

    def get_thumbnail(self, size: tuple[int, int]) -> "Image.Image":
        # prefer a separate small (thumbnail/label) series if one exists
        thumb_img = None
        for s in self._tf.series:
            if s is self._series:
                continue
            shape = s.shape
            n = 1
            for v in shape:
                n *= int(v)
            if n <= 16_000_000:  # small enough to decode fully
                try:
                    data = s.asarray()
                except Exception:
                    continue
                data = np.asarray(data)
                if data.ndim == 2:
                    data = np.stack([data] * 3, axis=-1)
                if data.ndim == 3 and data.shape[-1] > 3:
                    data = data[..., :3]
                if data.ndim == 3 and data.shape[-1] == 3:
                    thumb_img = Image.fromarray(data.astype(np.uint8), "RGB")
                    break
        if thumb_img is None:
            # smallest pyramid level via zarr (memory-safe)
            arr = self._zarr(self.level_count - 1)
            data = np.asarray(arr[...] if arr.ndim >= 3 else arr[:])
            if data.ndim == 2:
                data = np.stack([data] * 3, axis=-1)
            if data.shape[-1] > 3:
                data = data[..., :3]
            thumb_img = Image.fromarray(data.astype(np.uint8), "RGB")
        thumb_img.thumbnail(size, Image.LANCZOS)
        return thumb_img

    def close(self) -> None:
        for store in self._zarr_stores:
            if store is not None:
                try:
                    store.close()
                except Exception:
                    pass
        try:
            self._tf.close()
        except Exception:
            pass

    def __del__(self):  # pragma: no cover - best effort cleanup
        try:
            self.close()
        except Exception:
            pass


def open_slide(path: str | Path) -> SlideSource:
    """Open a slide. Priority: OpenSlideSource (if openslide is importable,
    the extension matches, and it opens) -> TifffileSource (if tifffile can
    open it; for .svs/.tiff/.tif) -> PillowSource. Never raises for a
    readable image file."""
    p = Path(path)
    ext = p.suffix.lower()
    if _HAS_OPENSLIDE and ext in OPENSLIDE_EXTENSIONS:
        try:
            return OpenSlideSource(p)
        except Exception:
            pass
    if _HAS_TIFFFILE and ext in TIFFFILE_EXTENSIONS:
        try:
            return TifffileSource(p)
        except Exception:
            pass
    return PillowSource(p)


def best_level_for_downsample(source: SlideSource, target_downsample: float) -> int:
    """Return the level with the largest downsample that is <= target
    (openslide semantics). Returns 0 if target_downsample < 1."""
    best = 0
    for i, d in enumerate(source.level_downsamples):
        if d <= target_downsample:
            best = i
        else:
            break
    return best
