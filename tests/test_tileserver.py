"""Tests for hescope.tileserver.

Three groups:

* pure DZI geometry / planning (no I/O, no server),
* the DZI-grid-vs-real-pyramid bridge, exercised against a source whose
  ``level_downsamples`` are deliberately NOT powers of two,
* real HTTP against a server bound to an ephemeral loopback port.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from hescope.slides import best_level_for_downsample, open_slide
from hescope.tileserver import (
    HARD_MAX_READ_PIXELS,
    MAX_SLIDES,
    DisplayParams,
    DZILayout,
    SlideRefs,
    TileOutOfRange,
    TileServer,
    dzi_layout,
    encode_jpeg,
    ensure_server,
    plan_tile,
    render_tile,
    serve_slide,
    shutdown_server,
)
from hescope.tileserver import _source_edge  # the seam rule under test

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO = REPO_ROOT / "assets" / "demo_he.png"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


class _PyramidSource:
    """A SlideSource over an in-memory pyramid with arbitrary level widths.

    Mirrors PillowSource.read_region semantics (level-0 ``location``, clipped
    and white-padded to exactly ``size``) but lets a test choose level widths
    that do not halve, which is what TifffileSource produces from real slides.
    """

    def __init__(self, base: Image.Image, level_widths):
        self.name = "pyramid"
        self.dimensions = base.size
        w, h = base.size
        self._levels = [base]
        for lw in level_widths[1:]:
            lh = max(1, round(h * lw / w))
            self._levels.append(base.resize((lw, lh), Image.LANCZOS))
        self.level_count = len(self._levels)
        self.level_downsamples = tuple(
            round(w / lv.width, 4) for lv in self._levels
        )
        self.mpp = None

    def read_region(self, location, level, size):
        img = self._levels[level]
        d = self.level_downsamples[level]
        lx = int(round(location[0] / d))
        ly = int(round(location[1] / d))
        w, h = size
        out = Image.new("RGB", (w, h), (255, 255, 255))
        sx0, sy0 = max(0, lx), max(0, ly)
        sx1, sy1 = min(img.width, lx + w), min(img.height, ly + h)
        if sx1 > sx0 and sy1 > sy0:
            out.paste(img.crop((sx0, sy0, sx1, sy1)), (sx0 - lx, sy0 - ly))
        return out

    def get_thumbnail(self, size):
        t = self._levels[-1].copy()
        t.thumbnail(size, Image.LANCZOS)
        return t


def _smooth_image(w: int, h: int) -> Image.Image:
    """A smooth, non-repeating pattern: resampling it is well-defined, so a
    seam shows up as a real difference rather than as resampling noise."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = 128 + 100 * np.sin(xx / 97.0) * np.cos(yy / 61.0)
    g = 128 + 100 * np.sin((xx + yy) / 143.0)
    b = 128 + 100 * np.cos(xx / 211.0 + yy / 89.0)
    arr = np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


@pytest.fixture(scope="module")
def irregular_source():
    """3001x2003 with downsamples (1.0, 3.996, 15.9628) -- not powers of two."""
    src = _PyramidSource(_smooth_image(3001, 2003), (3001, 751, 188))
    assert src.level_downsamples == (1.0, 3.996, 15.9628)
    return src


@pytest.fixture(scope="module")
def demo_source():
    if not DEMO.exists():  # pragma: no cover - depends on the checkout
        pytest.skip(f"demo slide missing: {DEMO}")
    src = open_slide(DEMO)
    assert src.dimensions == (6000, 4000)
    assert src.level_downsamples == (1.0, 2.0, 4.0, 8.0)
    return src


@pytest.fixture(scope="module")
def server(demo_source):
    """A standalone server (not the process singleton) on an ephemeral port."""
    srv = TileServer()
    key = srv.register(demo_source, name="demo_he.png")
    try:
        yield srv, key
    finally:
        srv.shutdown()


def _request(srv: TileServer, path: str, *, method: str = "GET", headers=None):
    """Raw HTTP against the server. ``path`` is sent verbatim -- no client-side
    normalization -- so traversal attempts reach the routing layer intact."""
    conn = http.client.HTTPConnection(srv.host, srv.port, timeout=20)
    try:
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def _raw_request(srv: TileServer, request_line_path: str) -> bytes:
    """Send a request line over a bare socket.

    http.client is trusted not to rewrite paths, but for the traversal tests
    that trust is exactly what is under examination, so those go over a socket
    that cannot normalize anything.
    """
    import socket

    with socket.create_connection((srv.host, srv.port), timeout=20) as sock:
        sock.sendall(
            f"GET {request_line_path} HTTP/1.1\r\n"
            f"Host: {srv.host}:{srv.port}\r\nConnection: close\r\n\r\n".encode("utf-8")
        )
        chunks = []
        while True:
            b = sock.recv(65536)
            if not b:
                break
            chunks.append(b)
    return b"".join(chunks)


def _tile_path(srv: TileServer, key: str, level: int, col: int, row: int, q: str = ""):
    return f"/t/{srv.token}/{key}_files/{level}/{col}_{row}.jpg{q}"


# ---------------------------------------------------------------------------
# DZI geometry
# ---------------------------------------------------------------------------


def test_layout_geometry_6000x4000():
    lay = dzi_layout(6000, 4000)
    # ceil(log2(6000)) == 13; the top level must be a single 1x1 tile.
    assert lay.max_level == 13
    assert lay.level_downsample(13) == 1.0
    assert lay.level_size(13) == (6000, 4000)
    assert lay.level_grid(13) == (24, 16)
    assert lay.level_size(0) == (1, 1)
    assert lay.level_grid(0) == (1, 1)
    assert lay.level_size(10) == (750, 500)
    assert lay.level_grid(10) == (3, 2)
    assert lay.level_size(9) == (375, 250)


@pytest.mark.parametrize(
    "size,expected",
    [((1, 1), 0), ((2, 1), 1), ((3, 3), 2), ((4096, 10), 12), ((4097, 10), 13)],
)
def test_max_level_is_exact_at_powers_of_two(size, expected):
    # math.log2 on an exact power of two is one ULP from an off-by-one, which
    # would shift the entire pyramid.
    assert dzi_layout(*size).max_level == expected


def test_tile_box_overlap_only_on_shared_edges():
    lay = dzi_layout(6000, 4000)
    lvl = 13  # 24x16 tiles
    assert lay.tile_box(lvl, 0, 0) == (0, 0, 257, 257)  # no left/top overlap
    assert lay.tile_box(lvl, 1, 1) == (255, 255, 513, 513)  # 258 px both ways
    x0, y0, x1, y1 = lay.tile_box(lvl, 23, 15)
    assert (x1 - x0, y1 - y0) == (6000 - 23 * 256 + 1, 4000 - 15 * 256 + 1)
    # a level that is one tile wide has no neighbours at all
    assert lay.tile_box(0, 0, 0) == (0, 0, 1, 1)


def test_core_boxes_tile_the_level_exactly():
    lay = dzi_layout(3001, 2003)
    for lvl in range(lay.max_level + 1):
        lw, lh = lay.level_size(lvl)
        cols, rows = lay.level_grid(lvl)
        assert lay.core_box(lvl, 0, 0)[:2] == (0, 0)
        assert lay.core_box(lvl, cols - 1, rows - 1)[2:] == (lw, lh)
        for c in range(cols - 1):
            assert lay.core_box(lvl, c, 0)[2] == lay.core_box(lvl, c + 1, 0)[0]


def test_validate_rejects_out_of_range():
    lay = dzi_layout(6000, 4000)
    cols, rows = lay.level_grid(13)
    lay.validate(13, cols - 1, rows - 1)  # in range: must not raise
    for bad in [
        (lay.max_level + 1, 0, 0),
        (-1, 0, 0),
        (13, cols, 0),
        (13, 0, rows),
        (13, -1, 0),
        (13, 0, -1),
    ]:
        with pytest.raises(TileOutOfRange):
            lay.validate(*bad)


def test_descriptors_round_trip():
    import xml.etree.ElementTree as ET

    lay = dzi_layout(6000, 4000)
    root = ET.fromstring(lay.to_xml())
    assert root.get("TileSize") == "256"
    assert root.get("Overlap") == "1"
    size = root[0]
    assert (size.get("Width"), size.get("Height")) == ("6000", "4000")
    d = lay.to_dict("http://x/y_files/")["Image"]
    assert d["Url"].endswith("/")  # OSD appends "{level}/{col}_{row}.jpg"
    assert d["Size"] == {"Width": 6000, "Height": 4000}
    assert d["Format"] == "jpg"


# ---------------------------------------------------------------------------
# the DZI grid -> real pyramid bridge (the irregular-downsample crux)
# ---------------------------------------------------------------------------


def test_abutment_invariant_on_irregular_pyramid(irregular_source):
    """Adjacent tiles must resolve to abutting source-pixel boxes.

    This is the regression test for the seam rule: each tile edge is rounded
    from its own level-0 coordinate, so tile N's right edge and tile N+1's
    left edge -- which are the same level-0 number -- round identically.
    """
    src = irregular_source
    lay = dzi_layout(*src.dimensions)
    mismatches = []
    boundaries = 0
    for lvl in range(lay.max_level + 1):
        dz = lay.level_downsample(lvl)
        d = src.level_downsamples[best_level_for_downsample(src, dz)]
        cols, rows = lay.level_grid(lvl)
        for c in range(cols - 1):
            a = lay.level0_box(lvl, c, 0, core=True)[2]
            b = lay.level0_box(lvl, c + 1, 0, core=True)[0]
            boundaries += 1
            if _source_edge(a, d) != _source_edge(b, d):
                mismatches.append((lvl, "col", c))
        for r in range(rows - 1):
            a = lay.level0_box(lvl, 0, r, core=True)[3]
            b = lay.level0_box(lvl, 0, r + 1, core=True)[1]
            boundaries += 1
            if _source_edge(a, d) != _source_edge(b, d):
                mismatches.append((lvl, "row", r))
    assert boundaries > 20  # the fixture must actually exercise multi-tile levels
    assert mismatches == []


def test_naive_width_formula_would_drift(irregular_source):
    """Guard the guard: prove the fixture can distinguish the two formulas.

    If someone weakens the fixture into a power-of-two pyramid, the abutment
    test above becomes vacuous. Here the *rejected* implementation --
    ``round(out_w * dz / d)`` accumulated per tile -- must visibly drift.
    """
    src = irregular_source
    lay = dzi_layout(*src.dimensions)
    drifted = False
    for lvl in range(lay.max_level + 1):
        dz = lay.level_downsample(lvl)
        d = src.level_downsamples[best_level_for_downsample(src, dz)]
        cols, _ = lay.level_grid(lvl)
        naive = 0
        for c in range(cols):
            x0, _, x1, _ = lay.core_box(lvl, c, 0)
            exact = _source_edge(lay.level0_box(lvl, c, 0, core=True)[0], d)
            if naive != exact:
                drifted = True
            naive += int(round((x1 - x0) * dz / d))
    assert drifted, "fixture no longer discriminates the width-based formula"


def test_plan_tile_uses_per_edge_rounding(irregular_source):
    """Pin the read box to the edge rule, not to a rounded width."""
    src = irregular_source
    lay = dzi_layout(*src.dimensions)
    for lvl in range(lay.max_level + 1):
        cols, rows = lay.level_grid(lvl)
        for col in range(cols):
            for row in range(rows):
                plan = plan_tile(src, lay, lvl, col, row)
                d = plan.source_downsample
                l0 = lay.level0_box(lvl, col, row)
                sx0, sy0 = _source_edge(l0[0], d), _source_edge(l0[1], d)
                sx1, sy1 = _source_edge(l0[2], d), _source_edge(l0[3], d)
                assert plan.source_box == (sx0, sy0, sx1, sy1)
                assert plan.read_size == (max(1, sx1 - sx0), max(1, sy1 - sy0))
                # read_region re-divides by d; that must land back on sx0/sy0
                assert int(round(plan.location_level0[0] / d)) == sx0
                assert int(round(plan.location_level0[1] / d)) == sy0


def test_plan_never_upsamples(irregular_source, demo_source):
    for src in (irregular_source, demo_source):
        lay = dzi_layout(*src.dimensions)
        for lvl in range(lay.max_level + 1):
            dz = lay.level_downsample(lvl)
            cols, rows = lay.level_grid(lvl)
            for col in (0, cols - 1):
                for row in (0, rows - 1):
                    plan = plan_tile(src, lay, lvl, col, row)
                    assert plan.source_downsample <= dz
                    assert plan.read_size[0] >= plan.out_size[0]
                    assert plan.read_size[1] >= plan.out_size[1]


def test_plan_rejects_out_of_range(demo_source):
    lay = dzi_layout(*demo_source.dimensions)
    cols, rows = lay.level_grid(13)
    with pytest.raises(TileOutOfRange):
        plan_tile(demo_source, lay, lay.max_level + 1, 0, 0)
    with pytest.raises(TileOutOfRange):
        plan_tile(demo_source, lay, 13, cols, 0)
    with pytest.raises(TileOutOfRange):
        plan_tile(demo_source, lay, 13, 0, rows)


def test_irregular_level_just_above_dz_is_not_used(irregular_source):
    """A level with downsample 15.9628 IS usable at dz=16 (15.96 <= 16); a
    level at 16.024 would not be. Openslide semantics: never upsample."""
    src = irregular_source
    lay = dzi_layout(*src.dimensions)
    lvl16 = lay.max_level - 4  # dz == 16
    assert lay.level_downsample(lvl16) == 16.0
    plan = plan_tile(src, lay, lvl16, 0, 0)
    assert plan.source_level == 2
    assert plan.source_downsample == pytest.approx(15.9628)


def test_tiles_reconstruct_render_viewport(demo_source):
    """GOLDEN DIFF: the tile path and the server-render path must agree.

    This is the only automated proof that the tile reader picks the right
    level *and* the right origin. Viewport geometry is chosen so the tiles
    involved lie fully inside every level (no edge padding to explain away).
    """
    from hescope.rois import ViewportState
    from hescope.viewer import render_viewport

    lay = dzi_layout(*demo_source.dimensions)
    vw, vh = 512, 256
    for ds in (1, 2, 4, 8):
        lvl = lay.max_level - int(round(np.log2(ds)))
        assert lay.level_downsample(lvl) == float(ds)
        vp = ViewportState(
            center=(vw / 2 * ds, vh / 2 * ds), downsample=float(ds), size=(vw, vh)
        )
        ref = render_viewport(demo_source, vp).convert("RGB")
        stitched = Image.new("RGB", (vw, vh))
        for col in range(vw // 256):
            for row in range(vh // 256):
                tile = render_tile(demo_source, plan_tile(demo_source, lay, lvl, col, row))
                ox = lay.overlap if col > 0 else 0
                oy = lay.overlap if row > 0 else 0
                stitched.paste(tile.crop((ox, oy, ox + 256, oy + 256)), (col * 256, row * 256))
        diff = np.abs(
            np.asarray(ref, dtype=np.float64) - np.asarray(stitched, dtype=np.float64)
        )
        assert diff.mean() < 1.0, f"ds={ds} MAE={diff.mean()}"


def test_stitched_irregular_level_has_no_seam(irregular_source):
    """On a fractional level, a stitched DZI level must match a direct read of
    the same region. A drifting read box shows up here as a step at x=256."""
    src = irregular_source
    lay = dzi_layout(*src.dimensions)
    lvl = lay.max_level - 2  # dz == 4 against a real downsample of 3.996
    cols, rows = lay.level_grid(lvl)
    assert cols >= 3
    stitched = Image.new("RGB", (3 * 256, 256))
    for col in range(3):
        tile = render_tile(src, plan_tile(src, lay, lvl, col, 0))
        ox = lay.overlap if col > 0 else 0
        stitched.paste(tile.crop((ox, 0, ox + 256, 256)), (col * 256, 0))
    arr = np.asarray(stitched, dtype=np.float64)
    # Column-to-column change across each seam must look like the change
    # anywhere else in the image; a misaligned read box makes it jump.
    steps = np.abs(np.diff(arr, axis=1)).mean(axis=(0, 2))
    typical = float(np.median(steps))
    for seam in (255, 511):
        assert steps[seam] < typical + 1.0, (
            f"seam at x={seam}: {steps[seam]} vs typical {typical}"
        )


# ---------------------------------------------------------------------------
# overview tier / cost ceilings
# ---------------------------------------------------------------------------


class _ShallowSource:
    """A huge slide with a single level -- the shape ``open_slide`` really
    produces for a pyramidal TIFF it cannot read levels from. Reads are
    synthesized, so planning against it costs nothing."""

    def __init__(self, w=10000, h=8000, thumb_w=2048, thumb_fails=False):
        self.name = "shallow"
        self.dimensions = (w, h)
        self.level_count = 1
        self.level_downsamples = (1.0,)
        self.mpp = None
        self._thumb_w = thumb_w
        self._thumb_fails = thumb_fails
        self.reads: list[tuple] = []

    def read_region(self, location, level, size):
        self.reads.append((location, level, size))
        return Image.new("RGB", size, (10, 20, 30))

    def get_thumbnail(self, size):
        if self._thumb_fails:
            raise RuntimeError("no thumbnail")
        w = min(self._thumb_w, self.dimensions[0])
        h = max(1, round(self.dimensions[1] * w / self.dimensions[0]))
        return Image.new("RGB", (w, h), (200, 100, 50))


def test_overview_tier_engages_and_never_upsamples():
    src = _ShallowSource()
    lay = dzi_layout(*src.dimensions)
    ods = src.dimensions[0] / 2048.0
    plan = plan_tile(src, lay, 0, 0, 0, overview_downsample=ods)
    assert plan.use_overview and plan.source_level == -1
    assert plan.source_downsample == pytest.approx(ods)
    assert plan.source_downsample <= lay.level_downsample(0)
    overview = Image.new("RGB", (2048, 1638), (7, 8, 9))
    img = render_tile(src, plan, overview=overview)
    assert img.size == plan.out_size
    assert src.reads == []  # the source was never touched


def test_overview_uses_the_same_edge_rounding():
    src = _ShallowSource()
    lay = dzi_layout(*src.dimensions)
    ods = 4.8828125
    lvl = 5
    d_boxes = [
        plan_tile(src, lay, lvl, c, 0, max_read_pixels=1, overview_downsample=ods).source_box
        for c in range(lay.level_grid(lvl)[0])
    ]
    for a, b in zip(d_boxes, d_boxes[1:]):
        # overlapped boxes must still be derived edge-wise: the right edge of
        # tile N minus its right overlap equals tile N+1's core left edge.
        assert a[2] >= b[0]
    exact = [_source_edge(lay.level0_box(lvl, c, 0)[0], ods) for c in range(len(d_boxes))]
    assert [b[0] for b in d_boxes] == exact


def test_hard_limit_refused_when_no_overview():
    src = _ShallowSource()
    lay = dzi_layout(*src.dimensions)
    # dz == 16384 over a single-level slide: 16384^2 source pixels.
    plan_would_read = (16384 // 1) ** 2
    assert plan_would_read > HARD_MAX_READ_PIXELS
    with pytest.raises(TileOutOfRange):
        plan_tile(src, lay, 0, 0, 0, overview_downsample=None)


def test_shallow_source_served_over_http_uses_overview():
    src = _ShallowSource()
    srv = TileServer()
    try:
        key = srv.register(src)
        entry = srv.get(key)
        assert entry is not None and entry.overview is not None
        assert entry.overview_downsample == pytest.approx(10000 / 2048)
        status, headers, body = _request(srv, _tile_path(srv, key, 0, 0, 0))
        assert status == 200
        assert headers["Content-Type"] == "image/jpeg"
        assert Image.open(io.BytesIO(body)).size == (1, 1)
        assert src.reads == [((0, 0), 0, (1, 1))]  # only the register() pre-warm
    finally:
        srv.shutdown()


def test_registration_without_thumbnail_still_serves_fine_levels():
    src = _ShallowSource(thumb_fails=True)
    srv = TileServer()
    try:
        key = srv.register(src)
        entry = srv.get(key)
        assert entry is not None and entry.overview is None
        # coarse level cannot be built without an overview -> opaque 404
        assert _request(srv, _tile_path(srv, key, 0, 0, 0))[0] == 404
        # fine level is a normal small read -> fine
        assert _request(srv, _tile_path(srv, key, dzi_layout(10000, 8000).max_level, 0, 0))[0] == 200
    finally:
        srv.shutdown()


def test_register_rejects_unsorted_downsamples():
    src = _ShallowSource()
    src.level_downsamples = (1.0, 8.0, 4.0)  # type: ignore[assignment]
    srv = TileServer()
    try:
        with pytest.raises(ValueError):
            srv.register(src)
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# display parameters
# ---------------------------------------------------------------------------


def test_display_params_from_query_is_total():
    assert DisplayParams.from_query(None) == DisplayParams()
    assert DisplayParams.from_query({}) == DisplayParams()
    assert DisplayParams.from_query({"c": ["nonsense"]}).channel == "rgb"
    assert DisplayParams.from_query({"rev": ["not-an-int"]}).rev == 0
    assert DisplayParams.from_query({"c": [None]}).channel == "rgb"  # type: ignore[list-item]
    p = DisplayParams.from_query({"c": ["hematoxylin"], "s": ["1"], "rev": ["3"]})
    assert (p.channel, p.stain_norm, p.rev) == ("hematoxylin", True, 3)
    assert not p.is_identity and DisplayParams().is_identity


def test_display_cache_key_is_stable_and_discriminating():
    a = DisplayParams(channel="eosin", rev=2)
    assert a.cache_key() == DisplayParams(channel="eosin", rev=2).cache_key()
    assert a.cache_key() != DisplayParams(channel="eosin", rev=3).cache_key()
    assert a.cache_key() != DisplayParams(channel="rgb", rev=2).cache_key()
    assert len(a.cache_key()) == 10


def test_channel_view_is_baked_into_the_tile(demo_source):
    lay = dzi_layout(*demo_source.dimensions)
    plan = plan_tile(demo_source, lay, lay.max_level, 10, 8)
    rgb = render_tile(demo_source, plan)
    hema = render_tile(
        demo_source, plan, display=DisplayParams(channel="hematoxylin"), refs=SlideRefs()
    )
    assert hema.size == rgb.size and hema.mode == "RGB"
    assert np.asarray(hema).std() > 0
    assert not np.array_equal(np.asarray(hema), np.asarray(rgb))


def test_stain_norm_without_a_target_is_a_noop(demo_source):
    # Normalizing a slide toward its own statistics is the identity, so
    # SlideRefs.fit deliberately leaves stain_target unset and the tile
    # encoder skips the transform rather than inventing a target.
    refs = SlideRefs.fit(demo_source)
    assert refs.stain_target is None
    lay = dzi_layout(*demo_source.dimensions)
    plan = plan_tile(demo_source, lay, lay.max_level, 10, 8)
    plain = render_tile(demo_source, plan)
    normed = render_tile(
        demo_source, plan, display=DisplayParams(stain_norm=True), refs=refs
    )
    assert np.array_equal(np.asarray(plain), np.asarray(normed))


def test_encode_jpeg_is_a_jpeg(demo_source):
    lay = dzi_layout(*demo_source.dimensions)
    img = render_tile(demo_source, plan_tile(demo_source, lay, lay.max_level, 5, 5))
    data = encode_jpeg(img)
    assert data[:2] == b"\xff\xd8"  # SOI
    assert Image.open(io.BytesIO(data)).size == img.size


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def test_tile_response_headers_and_pixels(server):
    srv, key = server
    status, headers, body = _request(srv, _tile_path(srv, key, 13, 5, 5))
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert int(headers["Content-Length"]) == len(body)
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "Access-Control-Allow-Credentials" not in headers
    assert headers["Cache-Control"] == "private, max-age=31536000, immutable"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Server"] == "hescope"  # never leak Python/BaseHTTP versions
    assert headers["ETag"] == f'"{key}-{DisplayParams().cache_key()}-13-5-5"'
    assert Image.open(io.BytesIO(body)).size == (258, 258)  # 256 + overlap on both sides


def test_conditional_request_gets_304(server):
    srv, key = server
    path = _tile_path(srv, key, 12, 3, 3)
    _, headers, _ = _request(srv, path)
    etag = headers["ETag"]
    status, h2, body = _request(srv, path, headers={"If-None-Match": etag})
    assert status == 304
    assert body == b""
    assert h2["ETag"] == etag
    assert "Content-Length" not in h2  # 304 is bodiless by definition
    # a different display setting must not be satisfied by the same ETag
    status2, _, _ = _request(
        srv, path + "?c=eosin", headers={"If-None-Match": etag}
    )
    assert status2 == 200


def test_head_returns_headers_without_a_body(server):
    srv, key = server
    status, headers, body = _request(srv, _tile_path(srv, key, 13, 1, 1), method="HEAD")
    assert status == 200
    assert body == b""
    assert int(headers["Content-Length"]) > 0


def test_descriptors_over_http(server):
    srv, key = server
    status, headers, body = _request(srv, f"/t/{srv.token}/{key}.dzi")
    assert status == 200 and headers["Content-Type"] == "application/xml"
    assert b'TileSize="256"' in body and b'Width="6000"' in body
    status, headers, body = _request(srv, f"/t/{srv.token}/{key}.dzi.json")
    assert status == 200 and headers["Content-Type"] == "application/json"
    assert json.loads(body) == srv.tile_source_dict(key)


def test_no_slide_open_is_404():
    srv = TileServer()
    try:
        assert srv.keys() == []
        assert _request(srv, f"/t/{srv.token}/anykey.dzi")[0] == 404
        assert _request(srv, _tile_path(srv, "anykey", 5, 0, 0))[0] == 404
    finally:
        srv.shutdown()


def test_bad_token_and_unknown_key_are_404(server):
    srv, key = server
    forged = "A" * len(srv.token)
    assert _request(srv, f"/t/{forged}/{key}_files/13/5_5.jpg")[0] == 404
    assert _request(srv, f"/t/{forged}/{key}.dzi")[0] == 404
    assert _request(srv, _tile_path(srv, "nosuchkey", 13, 5, 5))[0] == 404


def test_out_of_range_tile_is_404(server):
    srv, key = server
    assert _request(srv, _tile_path(srv, key, 13, 24, 0))[0] == 404  # cols == 24
    assert _request(srv, _tile_path(srv, key, 13, 0, 16))[0] == 404  # rows == 16
    assert _request(srv, _tile_path(srv, key, 14, 0, 0))[0] == 404  # max_level == 13
    assert _request(srv, _tile_path(srv, key, 0, 0, 0))[0] == 200  # the top tile exists


@pytest.mark.parametrize(
    "path",
    [
        "/pyproject.toml",
        "/../pyproject.toml",
        "/../../../../etc/passwd",
        "/t/{tok}/../pyproject.toml",
        "/t/{tok}/../../pyproject.toml",
        "/t/{tok}/%2e%2e%2fpyproject.toml",
        "/t/{tok}/..%2f..%2fetc%2fpasswd",
        "/t/{tok}/{key}_files/../../../../pyproject.toml",
        "/t/{tok}/{key}_files/13/../../../../pyproject.toml",
        "/t/{tok}/{key}_files/13/5_5.jpg/../../../pyproject.toml",
        "/t/{tok}/pyproject.toml",
        "/t/{tok}/{key}.dzi/../../../pyproject.toml",
        "/t/{tok}/C:%5Cwindows%5Cwin.ini",
        "/t/{tok}/{key}_files/13/5_5.png",
    ],
)
def test_no_file_off_disk_is_ever_served(server, path):
    """The router's character class cannot express a separator or a dot
    segment, and no handler opens a file. Both belts, both braces.

    Sent over a bare socket so nothing between the test and the router can
    normalize the path away and make this pass for the wrong reason.
    """
    srv, key = server
    target = path.format(tok=srv.token, key=key)
    assert (REPO_ROOT / "pyproject.toml").exists()  # the file really is there
    raw = _raw_request(srv, target)
    assert raw.startswith(b"HTTP/1.1 404 "), raw[:80]
    assert raw.endswith(b"not found")  # fixed opaque body: nothing is echoed
    assert b"[project]" not in raw and b"[build-system]" not in raw
    # and again through http.client, which is what a browser-ish client does
    status, _, body = _request(srv, target)
    assert status == 404 and body == b"not found"


@pytest.mark.parametrize(
    "exc",
    [AssertionError("zarr store closed"), OSError("truncated"), RuntimeError("boom")],
)
def test_a_source_that_blows_up_becomes_an_opaque_404(exc):
    """A slide closed underneath the server (TifffileSource.close leaves zarr
    raising AssertionError) must not 500, and must not leak a stack trace."""

    class _Broken(_ShallowSource):
        def read_region(self, location, level, size):
            raise exc

    srv = TileServer()
    try:
        src = _Broken(w=4096, h=4096)
        key = srv.register(src)  # pre-warm swallows the failure
        lvl = dzi_layout(4096, 4096).max_level
        status, _, body = _request(srv, _tile_path(srv, key, lvl, 0, 0))
        assert status == 404
        assert body == b"not found"
        # the server survives and still routes
        assert _request(srv, f"/t/{srv.token}/{key}.dzi")[0] == 200
    finally:
        srv.shutdown()


def test_write_methods_are_405(server):
    srv, key = server
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, headers, _ = _request(
            srv, _tile_path(srv, key, 13, 5, 5), method=method
        )
        assert status == 405, method
        assert headers["Allow"] == "GET, HEAD, OPTIONS"


def test_options_preflight(server):
    srv, key = server
    status, headers, body = _request(
        srv, _tile_path(srv, key, 13, 5, 5), method="OPTIONS"
    )
    assert status == 204
    assert body == b""
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "GET" in headers["Access-Control-Allow-Methods"]


def test_garbage_requests_never_take_the_server_down(server):
    srv, key = server
    junk = [
        "/",
        "/t/",
        "/t//",
        "/t/" + srv.token,
        f"/t/{srv.token}/{key}_files/13/notanumber_5.jpg",
        f"/t/{srv.token}/{key}_files/999999/0_0.jpg",
        f"/t/{srv.token}/{key}_files/13/99999999999_0.jpg",
        "/t/" + "A" * 500 + "/k_files/1/0_0.jpg",
        "/%00",
        "/t/%FF%FE/k.dzi",
    ]
    for p in junk:
        status, _, _ = _request(srv, p)
        assert status in (404, 405), p
    # A junk QUERY on a valid tile route must still paint: DisplayParams.from_query
    # is total, and 404-ing the viewer over an unparseable slider value would be
    # a worse failure than rendering the default view.
    for q in ("?", "?c=" + "x" * 4000, "?c=&s=&rev=", "?s=maybe&rev=1e9"):
        assert _request(srv, _tile_path(srv, key, 13, 5, 5, q))[0] == 200, q
    # still serving afterwards
    assert _request(srv, _tile_path(srv, key, 13, 5, 5))[0] == 200


def test_concurrent_requests_are_identical(server):
    srv, key = server
    path = _tile_path(srv, key, 13, 7, 4)
    results: list[tuple[int, str]] = []
    lock = threading.Lock()

    def fetch():
        status, _, body = _request(srv, path)
        with lock:
            results.append((status, hashlib.md5(body).hexdigest()))

    threads = [threading.Thread(target=fetch) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(results) == 8
    assert {s for s, _ in results} == {200}
    assert len({d for _, d in results}) == 1


def test_slide_swap_at_runtime(server, irregular_source):
    """Opening a second slide must not disturb the first, and retiring a slide
    must stop serving it immediately (a stale tile would be another slide's
    pixels under the same URL)."""
    srv, key = server
    key2 = srv.register(irregular_source, name="irregular")
    assert key2 != key
    assert _request(srv, _tile_path(srv, key, 13, 5, 5))[0] == 200
    assert _request(srv, _tile_path(srv, key2, 12, 1, 1))[0] == 200
    assert _request(srv, f"/t/{srv.token}/{key2}.dzi")[1]["Content-Type"] == "application/xml"

    srv.retire(key2)
    assert _request(srv, _tile_path(srv, key2, 12, 1, 1))[0] == 404
    assert _request(srv, f"/t/{srv.token}/{key2}.dzi")[0] == 404
    assert _request(srv, _tile_path(srv, key, 13, 5, 5))[0] == 200  # untouched


def test_retire_does_not_close_the_source(demo_source):
    srv = TileServer()
    try:
        key = srv.register(demo_source)
        srv.retire(key)
        # the source must still be usable: app.py owns its lifetime
        assert demo_source.read_region((0, 0), 0, (4, 4)).size == (4, 4)
        srv.retire(key)  # idempotent
    finally:
        srv.shutdown()


def test_registry_is_bounded():
    srv = TileServer()
    try:
        keys = [srv.register(_ShallowSource(w=512, h=512)) for _ in range(MAX_SLIDES + 2)]
        assert len(srv.keys()) == MAX_SLIDES
        assert _request(srv, f"/t/{srv.token}/{keys[0]}.dzi")[0] == 404
        assert _request(srv, f"/t/{srv.token}/{keys[-1]}.dzi")[0] == 200
    finally:
        srv.shutdown()


def test_shutdown_is_idempotent_and_frees_the_port(demo_source):
    srv = TileServer()
    srv.register(demo_source)
    host, port = srv.host, srv.port
    assert srv.is_alive()
    srv.shutdown()
    srv.shutdown()  # must not raise
    assert not srv.is_alive()
    with pytest.raises(OSError):
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/")
        conn.getresponse()


def test_allow_reuse_address_is_off():
    # On Windows SO_REUSEADDR lets another local process bind the same
    # 127.0.0.1:port and take over our traffic.
    from hescope.tileserver import _Server

    assert _Server.allow_reuse_address is False


def test_ensure_server_is_a_singleton_and_serve_slide_describes_it(demo_source):
    try:
        a = ensure_server()
        b = ensure_server()
        assert a is b and a.is_alive()
        info = serve_slide(demo_source, name="demo_he.png")
        assert set(info) == {
            "key",
            "base_url",
            "dzi_url",
            "tiles_url",
            "tile_source",
            "width",
            "height",
            "max_level",
            "tile_size",
            "overlap",
        }
        assert info["tiles_url"].endswith("/")
        assert info["dzi_url"].startswith(info["base_url"])
        assert (info["width"], info["height"], info["max_level"]) == (6000, 4000, 13)
        assert info["tile_source"]["Image"]["Url"] == info["tiles_url"]
        status, _, body = _request(
            a, _tile_path(a, info["key"], 11, 2, 2)
        )
        assert status == 200 and Image.open(io.BytesIO(body)).size == (258, 258)
    finally:
        shutdown_server()
        shutdown_server()  # idempotent


def test_base_url_honours_the_remote_override(monkeypatch, demo_source):
    srv = TileServer()
    try:
        assert srv.base_url == f"http://127.0.0.1:{srv.port}"
        monkeypatch.setenv("HESCOPE_TILE_BASE_URL", "https://tunnel.example/tiles/")
        assert srv.base_url == "https://tunnel.example/tiles"
        assert srv.dzi_url("k").startswith("https://tunnel.example/tiles/t/")
    finally:
        srv.shutdown()


def test_layout_is_immutable():
    import dataclasses

    lay = dzi_layout(100, 50)
    assert isinstance(lay, DZILayout)
    with pytest.raises(dataclasses.FrozenInstanceError):
        lay.width = 1  # type: ignore[misc]
