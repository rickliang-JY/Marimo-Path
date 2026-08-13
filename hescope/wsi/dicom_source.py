"""DICOM whole-slide images as a :class:`~hescope.wsi.slides.SlideSource`.

DICOM VL Whole Slide Microscopy Image is what clinical scanners increasingly
emit, and it is the one format neither OpenSlide nor tifffile covers here. The
adapter is thin because `wsidicom`_ already exposes a pyramid; almost all of
the code below is about the one place the two disagree.

THE COORDINATE TRAP
-------------------
``SlideSource.read_region`` takes ``location`` in **level-0** pixels. That is
the OpenSlide convention, it is what :mod:`hescope.core.rois` and the tile server
both assume, and both existing backends implement it.

``wsidicom`` does the opposite. Its ``read_region`` scales the region *up* from
the level you asked for::

    scale_factor = wsi_level.calculate_scale(level)
    scaled_region = Region(position=location, size=size) * scale_factor

so its ``location`` is in the **requested level's** pixels. Forwarding ours
unchanged reads the right-sized region from the wrong place at every level
except 0, and the error grows with the downsample -- silent, plausible-looking,
and exactly the class of bug that made an off-slide read count as tissue
(bugs/2026-08-09-round-03.md, R03-1). :meth:`DicomSource.read_region` converts,
and a test pins the two against each other.

.. _wsidicom: https://github.com/imi-bigpicture/wsidicom
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

__all__ = ["DICOM_EXTENSIONS", "is_dicom_slide", "DicomSource"]

try:  # optional dependency: pip install -e ".[dicom]"
    from wsidicom import WsiDicom  # type: ignore

    _HAS_WSIDICOM = True
except Exception:  # pragma: no cover - depends on environment
    WsiDicom = None  # type: ignore
    _HAS_WSIDICOM = False

#: A DICOM WSI is often a *directory* of instances, and single files are
#: usually .dcm, sometimes extensionless.
DICOM_EXTENSIONS = {".dcm", ".dicom"}

#: "DICM" at offset 128 -- the preamble magic every conforming file carries.
_DICM_MAGIC_OFFSET = 128


def is_dicom_slide(path: str | Path) -> bool:
    """Whether ``path`` looks like a DICOM WSI, by content rather than name.

    Extensionless DICOM is common, so a file is probed for the ``DICM`` magic
    at offset 128 instead of being judged by its suffix. A directory counts
    when it holds at least one such file, because a DICOM WSI is normally a
    directory of instances (one per level, plus label and overview).
    """
    p = Path(path)
    try:
        if p.is_dir():
            return any(is_dicom_slide(child) for child in p.iterdir() if child.is_file())
        if not p.is_file():
            return False
        if p.suffix.lower() in DICOM_EXTENSIONS:
            return True
        with open(p, "rb") as fh:
            fh.seek(_DICM_MAGIC_OFFSET)
            return fh.read(4) == b"DICM"
    except OSError:
        return False


class DicomSource:
    """A DICOM WSI exposed through the HE-Scope slide protocol.

    ``path`` may be a directory of DICOM instances or a single file. Levels,
    downsamples and mpp come from the pyramid ``wsidicom`` reports; regions are
    requested in level-0 coordinates, clipped to the slide and padded white, so
    this behaves like every other backend.
    """

    def __init__(self, path: str | Path) -> None:
        if not _HAS_WSIDICOM:
            raise RuntimeError(
                "wsidicom is not available; install it with: pip install -e \".[dicom]\""
            )
        self._path = Path(path)
        self.name = self._path.name
        self._slide = WsiDicom.open(str(self._path))

        levels = list(self._slide.levels)
        if not levels:
            raise ValueError(f"no image levels in {self._path}")
        base = levels[0]
        w0, h0 = int(base.size.width), int(base.size.height)
        self.dimensions: tuple[int, int] = (w0, h0)
        self.level_count = len(levels)
        self.level_downsamples: tuple[float, ...] = tuple(
            round(w0 / max(1, int(lv.size.width)), 4) for lv in levels
        )
        self.mpp = self._read_mpp()

    def _read_mpp(self) -> float | None:
        """Microns per pixel, averaged over x and y.

        DICOM stores pixel spacing in MILLIMETRES; wsidicom's ``mpp`` is
        already microns, so prefer it and fall back to converting the spacing.
        """
        try:
            mpp = self._slide.mpp
            if mpp is not None:
                x, y = float(mpp.width), float(mpp.height)
                if x > 0 and y > 0:
                    return (x + y) / 2.0
        except Exception:
            pass
        try:
            spacing = self._slide.levels[0].pixel_spacing
            if spacing is not None:
                x, y = float(spacing.width) * 1000.0, float(spacing.height) * 1000.0
                if x > 0 and y > 0:
                    return (x + y) / 2.0
        except Exception:
            pass
        return None

    def read_region(
        self, location: tuple[int, int], level: int, size: tuple[int, int]
    ) -> "Image.Image":
        """Read ``size`` pixels at ``level``; ``location`` is LEVEL-0.

        Converted to level coordinates before the call, because wsidicom reads
        in the requested level's space (see the module docstring). Clipped to
        the slide and padded white so the result is always exactly ``size`` --
        wsidicom raises on an out-of-bounds region rather than padding, and the
        viewport routinely asks for one at the slide edge.
        """
        level = max(0, min(int(level), self.level_count - 1))
        d = self.level_downsamples[level]
        w, h = int(size[0]), int(size[1])
        lx = int(round(location[0] / d))
        ly = int(round(location[1] / d))
        lw = max(1, int(round(self.dimensions[0] / d)))
        lh = max(1, int(round(self.dimensions[1] / d)))

        out = Image.new("RGB", (w, h), (255, 255, 255))
        sx0, sy0 = max(0, lx), max(0, ly)
        sx1, sy1 = min(lw, lx + w), min(lh, ly + h)
        if sx1 <= sx0 or sy1 <= sy0:
            return out  # entirely outside the slide
        try:
            region = self._slide.read_region(
                (sx0, sy0), level, (sx1 - sx0, sy1 - sy0)
            )
        except Exception:
            return out  # unreadable region must not break the viewport
        out.paste(_as_rgb(region), (sx0 - lx, sy0 - ly))
        return out

    def get_thumbnail(self, size: tuple[int, int]) -> "Image.Image":
        try:
            return _as_rgb(self._slide.read_thumbnail(size))
        except Exception:
            # fall back to the smallest level, which always exists
            level = self.level_count - 1
            d = self.level_downsamples[level]
            w = max(1, int(round(self.dimensions[0] / d)))
            h = max(1, int(round(self.dimensions[1] / d)))
            img = self.read_region((0, 0), level, (w, h))
            img.thumbnail(size, Image.LANCZOS)
            return img

    def close(self) -> None:
        slide = getattr(self, "_slide", None)
        if slide is not None:
            try:
                slide.close()
            except Exception:
                pass
            self._slide = None  # type: ignore[assignment]

    def __del__(self):  # pragma: no cover - best effort cleanup
        try:
            self.close()
        except Exception:
            pass


def _as_rgb(img: Any) -> "Image.Image":
    """Whatever wsidicom returned, as an RGB PIL image."""
    if isinstance(img, Image.Image):
        return img.convert("RGB")
    arr = np.asarray(img)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    if arr.ndim == 3 and arr.shape[-1] > 3:
        arr = arr[..., :3]
    return Image.fromarray(arr.astype(np.uint8), "RGB")
