"""Loopback DeepZoom (DZI) tile server for the OpenSeadragon viewer.

The viewer needs pixels faster than the marimo websocket can carry them: a
single 1024x768 frame is a ~2.4 MB figure payload, while the same viewport as
256x256 JPEG tiles is ~240 KB *and* caches in the browser. So tiles travel
over plain HTTP on a loopback socket and only *control* travels over the
kernel comm.

Design constraints that are not obvious from the code:

* **Nothing on disk is reachable.** Requests are matched with ``re.fullmatch``
  against a character class that cannot express a path separator or a dot
  segment, and no handler ever calls ``open()``. The only bytes this server
  emits are JPEGs it just encoded and descriptors it just formatted.
* **The pyramid mismatch is real.** DZI assumes a power-of-two pyramid;
  :class:`~hescope.wsi.slides.TifffileSource` derives ``level_downsamples`` from
  the actual level shapes, so they are things like ``15.9744``. The DZI grid
  is therefore *virtual*: every DZI level is synthesized by reading the
  largest real level whose downsample is <= the DZI level's, then downscaling.
  See :func:`plan_tile` for the edge-rounding rule that keeps that seamless.
* **Display maths never enters ``read_region``.** Adjustments are applied to
  the encoded tile only. Anything agent-facing (``extract_patch``,
  ``roi_stats``) must keep seeing raw slide pixels.

Public entry point for app.py is :func:`serve_slide`.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import re
import secrets
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from ..wsi.slides import SlideSource, best_level_for_downsample

__all__ = [
    "TILE_SIZE",
    "TILE_OVERLAP",
    "JPEG_QUALITY",
    "OVERVIEW_MAX",
    "MAX_READ_PIXELS",
    "HARD_MAX_READ_PIXELS",
    "MAX_SLIDES",
    "TileOutOfRange",
    "DZILayout",
    "dzi_layout",
    "DisplayParams",
    "SlideRefs",
    "TilePlan",
    "plan_tile",
    "render_tile",
    "encode_jpeg",
    "SlideEntry",
    "TileServer",
    "ensure_server",
    "shutdown_server",
    "serve_slide",
]

TILE_SIZE = 256
TILE_OVERLAP = 1
JPEG_QUALITY = 85
#: Longest edge of the cached whole-slide overview used by the coarse tier.
OVERVIEW_MAX = 2048
#: Above this many source pixels for one tile, prefer the overview tier.
MAX_READ_PIXELS = 4_194_304  # 2048^2
#: Above this, with no overview available, refuse rather than risk an OOM.
HARD_MAX_READ_PIXELS = 67_108_864  # 8192^2
#: Slides kept registered at once (LRU). The viewer only ever shows one.
MAX_SLIDES = 3
#: Encoded tiles cached per slide, per display setting.
TILE_CACHE_MAX = 512

#: The singleton lives on ``sys`` rather than in a module global on purpose:
#: marimo's autoreload re-executes this module, which would reset a module
#: global and leak the previous server *and its port*, one per reload. ``sys``
#: is never reloaded.
_SINGLETON_ATTR = "_hescope_tileserver"

_DZI_XMLNS = "http://schemas.microsoft.com/deepzoom/2008"

_CHANNELS = ("rgb", "r", "g", "b", "hematoxylin", "eosin")


class TileOutOfRange(ValueError):
    """Requested tile does not exist, or would cost more than we will spend.

    Both cases become the same opaque 404 so the server is not enumerable.
    """


# ---------------------------------------------------------------------------
# DZI geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DZILayout:
    """The *virtual* DeepZoom pyramid over a slide of ``width`` x ``height``.

    Virtual because it is derived from the level-0 dimensions alone and never
    from the slide's real ``level_downsamples``: OpenSeadragon requires a
    power-of-two pyramid down to a 1x1 top level, which real WSI pyramids do
    not provide. :func:`plan_tile` bridges the two.
    """

    width: int
    height: int
    tile_size: int
    overlap: int
    max_level: int

    # -- level geometry ----------------------------------------------------

    def level_downsample(self, level: int) -> float:
        """Level-0 pixels per pixel at DZI ``level`` (always a power of two)."""
        return float(1 << (self.max_level - level))

    def level_size(self, level: int) -> tuple[int, int]:
        d = self.level_downsample(level)
        return (
            max(1, int(math.ceil(self.width / d))),
            max(1, int(math.ceil(self.height / d))),
        )

    def level_grid(self, level: int) -> tuple[int, int]:
        lw, lh = self.level_size(level)
        return (
            int(math.ceil(lw / self.tile_size)),
            int(math.ceil(lh / self.tile_size)),
        )

    # -- tile geometry -----------------------------------------------------

    def core_box(self, level: int, col: int, row: int) -> tuple[int, int, int, int]:
        """Tile box in DZI-level pixels *without* overlap.

        The cores tile the level exactly; the overlapped boxes do not. Seam
        checks must use this, not :meth:`tile_box`.
        """
        lw, lh = self.level_size(level)
        x0 = col * self.tile_size
        y0 = row * self.tile_size
        return (x0, y0, min(x0 + self.tile_size, lw), min(y0 + self.tile_size, lh))

    def tile_box(self, level: int, col: int, row: int) -> tuple[int, int, int, int]:
        """Tile box in DZI-level pixels *with* overlap (DeepZoom convention:
        overlap is added only on edges shared with a neighbour)."""
        cols, rows = self.level_grid(level)
        x0, y0, x1, y1 = self.core_box(level, col, row)
        ov = self.overlap
        return (
            x0 - (ov if col > 0 else 0),
            y0 - (ov if row > 0 else 0),
            x1 + (ov if col < cols - 1 else 0),
            y1 + (ov if row < rows - 1 else 0),
        )

    def level0_box(
        self, level: int, col: int, row: int, *, core: bool = False
    ) -> tuple[float, float, float, float]:
        """The tile's box in level-0 coordinates (exact, unrounded)."""
        d = self.level_downsample(level)
        x0, y0, x1, y1 = (
            self.core_box(level, col, row) if core else self.tile_box(level, col, row)
        )
        return (x0 * d, y0 * d, x1 * d, y1 * d)

    def validate(self, level: int, col: int, row: int) -> None:
        if not (0 <= level <= self.max_level):
            raise TileOutOfRange(f"level {level} outside 0..{self.max_level}")
        cols, rows = self.level_grid(level)
        if not (0 <= col < cols) or not (0 <= row < rows):
            raise TileOutOfRange(f"tile {col}_{row} outside {cols}x{rows}")

    # -- descriptors -------------------------------------------------------

    def to_dict(self, tiles_url: str) -> dict:
        """The DZI-JSON descriptor OpenSeadragon accepts as a tile source."""
        return {
            "Image": {
                "xmlns": _DZI_XMLNS,
                "Url": tiles_url,
                "Format": "jpg",
                "Overlap": self.overlap,
                "TileSize": self.tile_size,
                "Size": {"Width": self.width, "Height": self.height},
            }
        }

    def to_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Image xmlns="{_DZI_XMLNS}" Format="jpg" '
            f'Overlap="{self.overlap}" TileSize="{self.tile_size}">'
            f'<Size Width="{self.width}" Height="{self.height}"/>'
            "</Image>"
        )


def dzi_layout(
    width: int,
    height: int,
    *,
    tile_size: int = TILE_SIZE,
    overlap: int = TILE_OVERLAP,
) -> DZILayout:
    """Build the DZI layout for a slide of ``width`` x ``height``.

    ``max_level`` is ``ceil(log2(max(w, h)))`` computed with integer bit
    arithmetic -- ``math.log2`` on an exact power of two is one ULP away from
    being off by one, and being off by one shifts the whole pyramid.
    """
    w = max(1, int(width))
    h = max(1, int(height))
    max_level = (max(w, h) - 1).bit_length()  # == ceil(log2(max(w, h))), exactly
    return DZILayout(
        width=w,
        height=h,
        tile_size=int(tile_size),
        overlap=int(overlap),
        max_level=int(max_level),
    )


# ---------------------------------------------------------------------------
# Display parameters (baked into the tile; see the Tier-2 rules in the plan)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisplayParams:
    """Per-tile display settings that must be baked server-side.

    Only the *discrete* controls live here. Brightness/contrast/gamma are
    continuous sliders and are applied client-side as a CSS filter, so that
    dragging a slider does not invalidate the browser's whole tile cache.
    """

    channel: str = "rgb"
    stain_norm: bool = False
    rev: int = 0

    def cache_key(self) -> str:
        """Short stable hash used as the ``?v=`` cache-buster and in the ETag."""
        raw = f"{self.channel}|{int(self.stain_norm)}|{int(self.rev)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]

    @property
    def is_identity(self) -> bool:
        return self.channel == "rgb" and not self.stain_norm

    def query(self) -> str:
        return f"c={self.channel}&s={int(self.stain_norm)}&rev={int(self.rev)}"

    @classmethod
    def from_query(cls, qs: dict | None) -> "DisplayParams":
        """Parse a ``parse_qs``-style mapping. Never raises: an unparseable or
        unknown value falls back to the identity setting, because a malformed
        tile URL must render *something* rather than 404 the whole viewer."""

        def one(name: str) -> str | None:
            if not isinstance(qs, dict):
                return None
            v = qs.get(name)
            if isinstance(v, (list, tuple)):
                v = v[0] if v else None
            return v if isinstance(v, str) else None

        channel = (one("c") or "rgb").lower()
        if channel not in _CHANNELS:
            channel = "rgb"
        stain = (one("s") or "0").strip().lower() in ("1", "true", "yes", "on")
        try:
            rev = int(one("rev") or 0)
        except (TypeError, ValueError):
            rev = 0
        return cls(channel=channel, stain_norm=stain, rev=rev)


@dataclass
class SlideRefs:
    """Slide-level references pinned once, so per-tile maths stays tile-safe.

    Fitting the H/E channel normalization or the Macenko source matrix *per
    tile* is the obvious implementation and it is a diagnostic-safety defect:
    a blank background tile min-max normalizes its own sensor noise into
    convincing fake structure, and per-tile Macenko drives blank tiles to
    black. Pinning these to the whole slide removes the tile from the maths.
    """

    channel_lohi: dict[str, tuple[float, float]] = field(default_factory=dict)
    stain_source: dict | None = None
    stain_target: dict | None = None

    @classmethod
    def fit(cls, source: SlideSource, *, max_size: int = 512) -> "SlideRefs":
        """Fit what can be fit from a slide thumbnail. Never raises.

        ``stain_target`` is deliberately left None: normalizing a slide toward
        its own statistics is a no-op, so a *target* is a product decision the
        caller has to make. Without one, ``stain_norm`` is skipped.
        """
        from . import adjust as _adjust
        from ..analysis import stain as _stain

        channel_lohi: dict[str, tuple[float, float]] = {}
        stain_source: dict | None = None
        try:
            thumb = source.get_thumbnail((max_size, max_size)).convert("RGB")
        except Exception:
            return cls()
        fit_channel = getattr(_adjust, "fit_channel_reference", None)
        if callable(fit_channel):
            for ch in ("hematoxylin", "eosin"):
                try:
                    lo, hi = fit_channel(thumb, ch)
                    channel_lohi[ch] = (float(lo), float(hi))
                except Exception:
                    pass
        try:
            stain_source = _stain.fit_reference(thumb)
        except Exception:
            stain_source = None
        return cls(channel_lohi=channel_lohi, stain_source=stain_source)


@lru_cache(maxsize=32)
def _accepts(func: Callable, name: str) -> bool:
    """Whether ``func`` takes a keyword argument ``name``.

    The pinned-reference keywords (``lo``/``hi`` on ``channel_view``,
    ``source_reference`` on ``macenko_normalize``) are owned by another
    module and land on their own schedule; degrade to the unpinned call
    rather than crash a tile request.
    """
    import inspect

    try:
        return name in inspect.signature(func).parameters
    except (TypeError, ValueError):  # builtins / C functions
        return False


def _apply_display(
    img: "Image.Image", display: DisplayParams, refs: SlideRefs | None
) -> "Image.Image":
    """Bake the discrete display settings into a rendered tile."""
    from .adjust import channel_view
    from ..analysis.stain import macenko_normalize

    out = img
    if display.stain_norm and refs is not None and refs.stain_target is not None:
        kwargs: dict[str, Any] = {"reference": refs.stain_target}
        if refs.stain_source is not None and _accepts(macenko_normalize, "source_reference"):
            kwargs["source_reference"] = refs.stain_source
        out = macenko_normalize(out.convert("RGB"), **kwargs)
    if display.channel != "rgb":
        kwargs = {}
        lohi = (refs.channel_lohi if refs else {}).get(display.channel)
        if lohi is not None and _accepts(channel_view, "lo"):
            kwargs["lo"], kwargs["hi"] = lohi
        out = channel_view(out, display.channel, **kwargs)  # type: ignore[arg-type]
    return out


# ---------------------------------------------------------------------------
# Tile planning: the DZI grid -> real pyramid bridge
# ---------------------------------------------------------------------------


def _source_edge(level0: float, downsample: float) -> int:
    """Round a level-0 coordinate to a source-level pixel edge.

    THE seam rule. Each tile edge is rounded *independently* from its own
    level-0 coordinate. Tile N's right level-0 edge is bit-identical to tile
    N+1's left level-0 edge, so identical rounding forces the source boxes to
    abut exactly. Deriving the box from a rounded *width* instead
    (``round(out_w * dz / d)``) accumulates sub-pixel drift at fractional
    ratios -- DZI level 9 against ``level_downsamples`` of ``15.9744`` gives a
    ratio of 1.0016, which is a visible seam every ~600 tiles and a half-pixel
    misalignment long before that.
    """
    return int(round(level0 / downsample))


@dataclass(frozen=True)
class TilePlan:
    """Everything :func:`render_tile` needs, with no I/O performed yet."""

    level: int
    col: int
    row: int
    out_size: tuple[int, int]
    source_level: int  # -1 when reading from the overview tier
    source_downsample: float
    location_level0: tuple[int, int]
    read_size: tuple[int, int]
    source_box: tuple[int, int, int, int]  # (x0, y0, x1, y1) in source-level px
    use_overview: bool
    level0_box: tuple[float, float, float, float]


def plan_tile(
    source: SlideSource,
    layout: DZILayout,
    level: int,
    col: int,
    row: int,
    *,
    max_read_pixels: int = MAX_READ_PIXELS,
    overview_downsample: float | None = None,
) -> TilePlan:
    """Resolve one DZI tile onto the slide's real pyramid.

    ``best_level_for_downsample`` returns the largest real downsample that is
    <= the DZI level's, so ``dz / d >= 1`` always and we only ever *down*
    scale -- never upsample, which would invent detail.
    """
    layout.validate(level, col, row)
    zx0, zy0, zx1, zy1 = layout.tile_box(level, col, row)
    out_size = (max(1, zx1 - zx0), max(1, zy1 - zy0))
    dz = layout.level_downsample(level)
    l0 = layout.level0_box(level, col, row)

    src_level = best_level_for_downsample(source, dz)
    def source_box(downsample: float) -> tuple[int, int, int, int]:
        return (
            _source_edge(l0[0], downsample),
            _source_edge(l0[1], downsample),
            _source_edge(l0[2], downsample),
            _source_edge(l0[3], downsample),
        )

    d = float(source.level_downsamples[src_level])
    box = source_box(d)
    read_w = max(1, box[2] - box[0])
    read_h = max(1, box[3] - box[1])

    use_overview = False
    if (
        read_w * read_h > max_read_pixels
        and overview_downsample
        and dz >= float(overview_downsample)
    ):
        # A source with a shallow (or absent) pyramid makes the coarse DZI
        # levels enormous: one 1x1 tile over a whole slide is a full-slide
        # read. OpenSeadragon asks for ~10 such levels the moment it opens.
        use_overview = True
        src_level = -1
        d = float(overview_downsample)
        box = source_box(d)
        read_w = max(1, box[2] - box[0])
        read_h = max(1, box[3] - box[1])
    if not use_overview and read_w * read_h > HARD_MAX_READ_PIXELS:
        raise TileOutOfRange(
            f"tile {level}/{col}_{row} needs {read_w}x{read_h} source pixels"
        )

    return TilePlan(
        level=level,
        col=col,
        row=row,
        out_size=out_size,
        source_level=src_level,
        source_downsample=d,
        # read_region takes level-0 coordinates and re-divides by d; for d >= 1
        # this round-trips back to box[0]/box[1] exactly.
        location_level0=(int(round(box[0] * d)), int(round(box[1] * d))),
        read_size=(read_w, read_h),
        source_box=box,
        use_overview=use_overview,
        level0_box=l0,
    )


def render_tile(
    source: SlideSource,
    plan: TilePlan,
    *,
    overview: "Image.Image | None" = None,
    display: DisplayParams | None = None,
    refs: SlideRefs | None = None,
) -> "Image.Image":
    """Produce the tile image described by ``plan`` (RGB, exactly out_size)."""
    if plan.use_overview:
        if overview is None:
            raise TileOutOfRange("overview tier requested but no overview exists")
        sx0, sy0, sx1, sy1 = plan.source_box
        img = Image.new("RGB", (max(1, sx1 - sx0), max(1, sy1 - sy0)), (255, 255, 255))
        cx0, cy0 = max(0, sx0), max(0, sy0)
        cx1, cy1 = min(overview.width, sx1), min(overview.height, sy1)
        if cx1 > cx0 and cy1 > cy0:
            img.paste(overview.crop((cx0, cy0, cx1, cy1)), (cx0 - sx0, cy0 - sy0))
    else:
        img = source.read_region(
            plan.location_level0, plan.source_level, plan.read_size
        )
    if img.size != plan.out_size:
        img = img.resize(plan.out_size, Image.LANCZOS)
    if display is not None and not display.is_identity:
        img = _apply_display(img, display, refs)
    return img.convert("RGB")


def encode_jpeg(img: "Image.Image", quality: int = JPEG_QUALITY) -> bytes:
    """Encode a tile as JPEG.

    ``subsampling=0`` (4:4:4) because nuclei at 40x are exactly the
    high-frequency content chroma subsampling destroys, and this is a viewer a
    pathologist counts nuclei in.
    """
    buf = io.BytesIO()
    img.convert("RGB").save(
        buf, format="JPEG", quality=int(quality), subsampling=0, optimize=False
    )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class SlideEntry:
    key: str
    name: str
    source: SlideSource
    layout: DZILayout
    refs: SlideRefs | None = None
    overview: "Image.Image | None" = None
    overview_downsample: float | None = None
    retired: bool = False
    # Serializes read_region per slide. TifffileSource._zarr() mutates
    # per-level caches without synchronization, so two cold threads can build
    # two zarr stores for the same level and orphan one (leaking a tifffile
    # file handle). register() pre-warms every level to close that window, and
    # this lock closes it for anything the pre-warm could not reach. JPEG
    # encoding stays outside the lock.
    read_lock: threading.Lock = field(default_factory=threading.Lock)
    cache_lock: threading.Lock = field(default_factory=threading.Lock)
    cache: "OrderedDict[tuple, bytes]" = field(default_factory=OrderedDict)


def _parallel_reads_enabled() -> bool:
    return os.environ.get("HESCOPE_TILE_PARALLEL_READ", "").strip() in ("1", "true", "yes")


class TileServer:
    """A running loopback HTTP server that serves DZI tiles for open slides.

    Constructing it starts the listening thread; call :meth:`shutdown` to stop
    it. Prefer :func:`ensure_server`, which keeps one per process.
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int = 0,
        allowed_origin: str | None = None,
        quality: int = JPEG_QUALITY,
    ) -> None:
        self.token = secrets.token_urlsafe(24)  # 192 bits, in the URL PATH
        self.allowed_origin = allowed_origin
        self.quality = int(quality)
        #: Instance attribute rather than the module constant so a test (or a
        #: memory-constrained deployment) can move the overview threshold
        #: without monkeypatching a default argument.
        self.max_read_pixels = MAX_READ_PIXELS
        self._entries: "OrderedDict[str, SlideEntry]" = OrderedDict()
        self._registry_lock = threading.Lock()
        self._shutdown = False

        bind_host = host or os.environ.get("HESCOPE_TILE_HOST") or "127.0.0.1"
        self._httpd = _Server((bind_host, int(port)), _Handler)
        self._httpd.tileserver = self
        self.host, self.port = self._httpd.server_address[0], self._httpd.server_address[1]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="hescope-tileserver",
            daemon=True,
        )
        self._thread.start()

    # -- addressing --------------------------------------------------------

    @property
    def base_url(self) -> str:
        """Origin the *browser* should use.

        The browser, not the kernel, fetches these URLs, so a loopback address
        is only correct while the two share a machine. ``HESCOPE_TILE_BASE_URL``
        is the escape hatch for SSH-tunnelled or containerized sessions.
        """
        override = os.environ.get("HESCOPE_TILE_BASE_URL", "").strip()
        if override:
            return override.rstrip("/")
        host = self.host
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        if ":" in host:  # IPv6 literal
            host = f"[{host}]"
        return f"http://{host}:{self.port}"

    def dzi_url(self, key: str) -> str:
        return f"{self.base_url}/t/{self.token}/{key}.dzi"

    def tiles_url(self, key: str) -> str:
        # Must end with "/": OpenSeadragon appends "{level}/{col}_{row}.jpg".
        return f"{self.base_url}/t/{self.token}/{key}_files/"

    def tile_source_dict(self, key: str, *, display: DisplayParams | None = None) -> dict:
        entry = self.get(key)
        if entry is None:
            raise KeyError(key)
        d = entry.layout.to_dict(self.tiles_url(key))
        if display is not None and not display.is_identity:
            # OpenSeadragon >= 3 appends `queryParams` verbatim to tile URLs.
            d["queryParams"] = "?" + display.query() + f"&v={display.cache_key()}"
        return d

    # -- registry ----------------------------------------------------------

    def register(
        self,
        source: SlideSource,
        *,
        name: str | None = None,
        refs: SlideRefs | None = None,
    ) -> str:
        if self._shutdown:
            # A shut-down server accepted registrations and handed back a
            # perfectly well-formed tile_source dict on a port nothing is
            # listening on. app.py's `except Exception -> fall back to the
            # legacy plotly viewer` guard cannot fire on a success, so the
            # user got a permanently blank OpenSeadragon canvas and no message
            # (R07-12). Refusing here is what makes that guard reachable.
            raise RuntimeError(
                "tile server has been shut down; call ensure_server() for a "
                "fresh one"
            )
        downsamples = [float(d) for d in source.level_downsamples]
        if downsamples != sorted(downsamples):
            # best_level_for_downsample walks the list and breaks on the first
            # level above the target; unsorted downsamples make it silently
            # return the wrong level rather than fail.
            raise ValueError(f"level_downsamples must be ascending: {downsamples}")

        layout = dzi_layout(int(source.dimensions[0]), int(source.dimensions[1]))
        entry = SlideEntry(
            key=secrets.token_urlsafe(12),
            name=name or getattr(source, "name", "slide"),
            source=source,
            layout=layout,
            refs=refs,
        )

        # Pre-warm in the CALLING thread: a lazily-built per-level cache races
        # when six tile threads hit a cold slide at once.
        for lvl in range(len(downsamples)):
            try:
                source.read_region((0, 0), lvl, (1, 1))
            except Exception:
                pass
        self._build_overview(entry)

        evicted: list[SlideEntry] = []
        with self._registry_lock:
            self._entries[entry.key] = entry
            while len(self._entries) > MAX_SLIDES:
                _, old = self._entries.popitem(last=False)
                evicted.append(old)
        for old in evicted:  # drop pixels, never close the source (see retire)
            old.retired = True
            old.overview = None
            with old.cache_lock:
                old.cache.clear()
        return entry.key

    def _build_overview(self, entry: SlideEntry) -> None:
        """Cache a whole-slide overview for the coarse tier. Never raises."""
        try:
            thumb = entry.source.get_thumbnail((OVERVIEW_MAX, OVERVIEW_MAX))
            thumb = thumb.convert("RGB")
        except Exception:
            return
        if thumb.width <= 0 or thumb.height <= 0:
            return
        entry.overview = thumb
        # Derive the downsample from the image we actually got: get_thumbnail
        # never upscales, so a slide whose smallest level is below OVERVIEW_MAX
        # yields a coarser overview than requested.
        entry.overview_downsample = float(entry.layout.width) / float(thumb.width)

    def retire(self, key: str) -> None:
        """Stop serving ``key``. The SlideSource is NOT closed -- app.py owns
        its lifetime, and a closed TifffileSource raises for everyone."""
        with self._registry_lock:
            entry = self._entries.pop(key, None)
        if entry is not None:
            entry.retired = True
            entry.overview = None
            with entry.cache_lock:
                entry.cache.clear()

    def get(self, key: str) -> SlideEntry | None:
        with self._registry_lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
        return entry

    def keys(self) -> list[str]:
        with self._registry_lock:
            return list(self._entries)

    # -- rendering ---------------------------------------------------------

    def tile_bytes(
        self, entry: SlideEntry, level: int, col: int, row: int, display: DisplayParams
    ) -> bytes:
        """Encoded JPEG for one tile. Raises TileOutOfRange for bad coords."""
        ckey = (display.cache_key(), level, col, row)
        with entry.cache_lock:
            hit = entry.cache.get(ckey)
            if hit is not None:
                entry.cache.move_to_end(ckey)
                return hit

        plan = plan_tile(
            entry.source,
            entry.layout,
            level,
            col,
            row,
            max_read_pixels=self.max_read_pixels,
            overview_downsample=entry.overview_downsample,
        )
        if plan.use_overview or _parallel_reads_enabled():
            img = render_tile(
                entry.source, plan, overview=entry.overview, display=None
            )
        else:
            with entry.read_lock:
                img = render_tile(
                    entry.source, plan, overview=entry.overview, display=None
                )
        # Display maths and JPEG encoding stay outside the read lock.
        if not display.is_identity:
            img = _apply_display(img, display, entry.refs)
        data = encode_jpeg(img.convert("RGB"), self.quality)

        with entry.cache_lock:
            entry.cache[ckey] = data
            while len(entry.cache) > TILE_CACHE_MAX:
                entry.cache.popitem(last=False)
        return data

    # -- lifecycle ---------------------------------------------------------

    def is_alive(self) -> bool:
        return not self._shutdown and self._thread.is_alive()

    def shutdown(self) -> None:
        """Stop the listener and release the port. Idempotent."""
        if self._shutdown:
            return
        self._shutdown = True
        try:
            self._httpd.shutdown()
        except Exception:
            pass
        try:
            self._httpd.server_close()
        except Exception:
            pass
        try:
            self._thread.join(timeout=5.0)
        except Exception:
            pass
        with self._registry_lock:
            self._entries.clear()


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

# A key or token can only be URL-safe base64; neither "/" nor "." can appear,
# so no request line can express a path segment, a dot segment, or a filename.
_TOKEN = r"[A-Za-z0-9_\-]{8,128}"
_KEY = r"[A-Za-z0-9_\-]{1,64}"
_TILE_RE = re.compile(
    rf"/t/(?P<token>{_TOKEN})/(?P<key>{_KEY})_files"
    r"/(?P<level>\d{1,3})/(?P<col>\d{1,7})_(?P<row>\d{1,7})\.jpe?g"
)
_DZI_RE = re.compile(
    rf"/t/(?P<token>{_TOKEN})/(?P<key>{_KEY})\.dzi(?P<json>\.json)?"
)

_NOT_FOUND_BODY = b"not found"  # fixed, opaque: never echo the request

#: Tiles are immutable for a given (key, display) pair -- a slide swap mints a
#: new key and a display change mints a new ``v``.
_TILE_CACHE_CONTROL = "private, max-age=31536000, immutable"


class _Server(ThreadingHTTPServer):
    # Windows honours SO_REUSEADDR literally: with it on, another local
    # process can bind the same 127.0.0.1:port and take over our traffic.
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 128
    tileserver: "TileServer"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30  # do not leak a thread on a half-open socket

    # -- helpers -----------------------------------------------------------

    def version_string(self) -> str:  # noqa: D102 - the default leaks versions
        return "hescope"

    def log_message(self, fmt: str, *args) -> None:  # noqa: D102
        if os.environ.get("HESCOPE_TILE_LOG", "").strip() in ("1", "true", "yes"):
            sys.stderr.write("[tileserver] " + (fmt % args) + "\n")

    @property
    def _srv(self) -> TileServer:
        return self.server.tileserver  # type: ignore[attr-defined]

    def _acao(self) -> str:
        return self._srv.allowed_origin or "*"

    def _send(
        self,
        code: int,
        body: bytes,
        content_type: str,
        *,
        extra: dict | None = None,
        with_body: bool = True,
    ) -> None:
        try:
            self.send_response(code)
            # 204 and 304 are bodiless by definition; framing headers on them
            # confuse caches and add nothing.
            if code not in (204, 304):
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            # Required: without CORS the OSD canvas is tainted and rendering
            # breaks. Never Access-Control-Allow-Credentials -- the token in
            # the path is the only access control there is.
            self.send_header("Access-Control-Allow-Origin", self._acao())
            for k, v in (extra or {}).items():
                self.send_header(k, str(v))
            self.end_headers()
            if with_body and body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Fast pans abandon in-flight tiles constantly; that is not news.
            self.close_connection = True

    def _not_found(self, with_body: bool = True) -> None:
        self._send(404, _NOT_FOUND_BODY, "text/plain; charset=utf-8", with_body=with_body)

    # -- verbs -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(with_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(with_body=False)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(
            204,
            b"",
            "text/plain",
            extra={
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "If-None-Match",
                "Access-Control-Max-Age": "86400",
            },
            with_body=False,
        )

    def _method_not_allowed(self) -> None:
        self._send(
            405,
            b"method not allowed",
            "text/plain; charset=utf-8",
            extra={"Allow": "GET, HEAD, OPTIONS"},
        )

    do_POST = _method_not_allowed  # type: ignore[assignment]
    do_PUT = _method_not_allowed  # type: ignore[assignment]
    do_DELETE = _method_not_allowed  # type: ignore[assignment]
    do_PATCH = _method_not_allowed  # type: ignore[assignment]

    # -- routing -----------------------------------------------------------

    def _dispatch(self, *, with_body: bool) -> None:
        """Route a request. This must never raise: an escaping exception kills
        the connection with no status line at all."""
        try:
            parts = urlsplit(self.path)
            # Match the RAW path: unquoting first would let "%2e%2e%2f" become
            # a dot segment. There is no filesystem behind this server, but the
            # invariant is cheap and its violation is catastrophic.
            path = parts.path
            m = _TILE_RE.fullmatch(path)
            if m is not None:
                self._serve_tile(m, parts.query, with_body=with_body)
                return
            m = _DZI_RE.fullmatch(path)
            if m is not None:
                self._serve_descriptor(m, with_body=with_body)
                return
            self._not_found(with_body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:  # pragma: no cover - defensive
            try:
                self._send(
                    500, b"error", "text/plain; charset=utf-8", with_body=with_body
                )
            except Exception:
                self.close_connection = True

    def _resolve(self, m: "re.Match") -> SlideEntry | None:
        srv = self._srv
        if not secrets.compare_digest(m.group("token"), srv.token):
            return None
        return srv.get(m.group("key"))

    def _serve_descriptor(self, m: "re.Match", *, with_body: bool) -> None:
        entry = self._resolve(m)
        if entry is None:
            self._not_found(with_body)
            return
        srv = self._srv
        if m.group("json"):
            import json

            try:
                body = json.dumps(srv.tile_source_dict(entry.key)).encode("utf-8")
            except KeyError:  # retired between _resolve and here
                self._not_found(with_body)
                return
            ctype = "application/json"
        else:
            body = entry.layout.to_xml().encode("utf-8")
            ctype = "application/xml"
        self._send(
            200,
            body,
            ctype,
            # The descriptor changes when the slide is swapped, and a swap
            # mints a new key, so a short cache is safe but pointless.
            extra={"Cache-Control": "no-store"},
            with_body=with_body,
        )

    def _serve_tile(self, m: "re.Match", query: str, *, with_body: bool) -> None:
        entry = self._resolve(m)
        if entry is None:
            self._not_found(with_body)
            return
        try:
            level = int(m.group("level"))
            col = int(m.group("col"))
            row = int(m.group("row"))
        except ValueError:
            self._not_found(with_body)
            return

        # Derive the display key from the parsed parameters, never from the
        # client's "v": a client-chosen cache key is a cache-poisoning knob.
        display = DisplayParams.from_query(parse_qs(query))
        v = display.cache_key()
        etag = f'"{entry.key}-{v}-{level}-{col}-{row}"'
        inm = self.headers.get("If-None-Match")
        if inm and any(t.strip() == etag for t in inm.split(",")):
            self._send(
                304,
                b"",
                "image/jpeg",
                extra={"ETag": etag, "Cache-Control": _TILE_CACHE_CONTROL},
                with_body=False,
            )
            return

        try:
            data = self._srv.tile_bytes(entry, level, col, row, display)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
            return
        except Exception:
            # TileOutOfRange, a slide closed underneath us (TifffileSource.close
            # leaves zarr raising AssertionError), a decode failure -- from the
            # viewer's side these are all "this tile will not paint", and a 500
            # storm during a fast pan helps nobody. Opaque 404, never a stack
            # trace in the body.
            self.log_message("tile %s/%s_%s failed", level, col, row)
            self._not_found(with_body)
            return
        self._send(
            200,
            data,
            "image/jpeg",
            extra={"ETag": etag, "Cache-Control": _TILE_CACHE_CONTROL},
            with_body=with_body,
        )


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------


def ensure_server(*, allowed_origin: str | None = None) -> TileServer:
    """Return the process-wide tile server, starting it if needed."""
    srv = getattr(sys, _SINGLETON_ATTR, None)
    if isinstance(srv, TileServer) and srv.is_alive():
        return srv
    if isinstance(srv, TileServer):
        # Replacing a dead server: shut the old one down rather than drop the
        # reference. is_alive() is False as soon as the listener THREAD dies --
        # an exception escaping serve_forever does that without running
        # shutdown() -- and the socket stays bound, so the port leaks, one per
        # recovery. Leaking "the previous server *and its port*" is exactly
        # what this module's singleton exists to prevent (R07-12).
        srv.shutdown()
    srv = TileServer(allowed_origin=allowed_origin)
    setattr(sys, _SINGLETON_ATTR, srv)
    return srv


def shutdown_server() -> None:
    """Stop the process-wide tile server if one is running. Idempotent."""
    srv = getattr(sys, _SINGLETON_ATTR, None)
    if isinstance(srv, TileServer):
        srv.shutdown()
    try:
        delattr(sys, _SINGLETON_ATTR)
    except AttributeError:
        pass


def serve_slide(
    source: SlideSource,
    *,
    name: str | None = None,
    refs: SlideRefs | None = None,
    allowed_origin: str | None = None,
) -> dict:
    """Register ``source`` with the running tile server and describe it.

    Returns everything the viewer needs to open the slide::

        {"key", "base_url", "dzi_url", "tiles_url", "tile_source",
         "width", "height", "max_level", "tile_size", "overlap"}
    """
    srv = ensure_server(allowed_origin=allowed_origin)
    key = srv.register(source, name=name, refs=refs)
    entry = srv.get(key)
    assert entry is not None  # just registered
    layout = entry.layout
    return {
        "key": key,
        "base_url": srv.base_url,
        "dzi_url": srv.dzi_url(key),
        "tiles_url": srv.tiles_url(key),
        "tile_source": srv.tile_source_dict(key),
        "width": layout.width,
        "height": layout.height,
        "max_level": layout.max_level,
        "tile_size": layout.tile_size,
        "overlap": layout.overlap,
    }
