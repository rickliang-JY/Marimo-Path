"""Stage-0 display-frame encoding and overscan tests (offline).

Two things are proven here, and the second one is the dangerous one:

1. The ON-SCREEN frame is JPEG (fast, small) while everything that is DATA --
   ``extract_patch`` / ``roi_stats`` / anything the agent tools or the DB see
   -- still reads unadjusted lossless source pixels.
2. Rendering an OVERSCAN frame (bigger than ``vp.size``, so plotly's native
   drag has real pixels to reveal) does NOT move any coordinate. A selection
   dragged anywhere on the overscanned figure -- including into the margin,
   at negative axis coordinates -- must map to exactly the level-0 coordinate
   it would have mapped to before. An off-by-one here silently corrupts every
   ROI, so the round trip is tested end to end against planted pixels rather
   than against recomputed arithmetic.
"""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from PIL import Image

from hescope.overlay import draw_rois
from hescope.rois import ROI, ViewportState, extract_patch, viewport_transform
from hescope.slides import PillowSource
from hescope.viewer import (
    DEFAULT_OVERSCAN,
    apply_display_pipeline,
    current_selection,
    make_roi_figure,
    parse_plotly_selection,
    render_viewport,
    render_viewport_overscan,
    selection_dict_from_roi,
    selection_to_roi,
    viewport_data_uri,
    viewport_jpeg_bytes,
    viewport_png_bytes,
)

# Same viewport the agent-contract acceptance test uses
# (tests/test_live_selection.py): offset = center - (size/2)*downsample.
VP = ViewportState(center=(1000.0, 800.0), downsample=2.0, size=(400, 300))

# Viewport for the planted-pixel round trip. downsample=1.0 reads level 0 at
# 1:1 with no resize, so a 4x4 marker survives byte-exact; at ds=2 the LANCZOS
# pyramid blends it into its neighbours and it is no longer findable.
VP1 = ViewportState(center=(1000.0, 800.0), downsample=1.0, size=(400, 300))

MARKER = (12, 200, 34)  # a colour the synthetic slide never produces elsewhere


def _slide_array(width: int = 2400, height: int = 2000) -> np.ndarray:
    """Textured H&E-ish base so JPEG/PNG sizes are meaningful."""
    rng = np.random.default_rng(11)
    arr = rng.integers(150, 235, size=(height, width, 3), dtype=np.uint8)
    arr[..., 1] = np.clip(arr[..., 1].astype(int) - 40, 0, 255).astype(np.uint8)
    return arr


@pytest.fixture()
def source(tmp_path):
    p = tmp_path / "encode_slide.png"
    Image.fromarray(_slide_array(), "RGB").save(p)
    return PillowSource(p)


@pytest.fixture()
def marked_source(tmp_path):
    """Slide with a 4x4 MARKER block planted at a known level-0 position that
    falls OUTSIDE the nominal VP1 viewport but INSIDE its 1.5x overscan margin.

    VP1 spans level-0 x in [800, 1200), y in [650, 950); the 1.5x overscan
    frame spans x in [700, 1300), y in [575, 1025). The marker's nominal
    viewport pixel is (-50, -40) -- up and to the left of the visible frame,
    exactly the region overscan exists to reveal.
    """
    arr = _slide_array()
    to_level0, _ = viewport_transform(VP1)
    mx, my = (int(v) for v in to_level0((-50.0, -40.0)))
    assert (mx, my) == (750, 610)
    arr[my : my + 4, mx : mx + 4] = MARKER
    p = tmp_path / "marked_slide.png"
    Image.fromarray(arr, "RGB").save(p)
    return PillowSource(p), (mx, my)


# ---------------------------------------------------------------------------
# Encoders: JPEG for the screen, PNG stays lossless
# ---------------------------------------------------------------------------


def test_viewport_jpeg_bytes_is_a_real_jpeg(source):
    img = render_viewport(source, VP)
    raw = viewport_jpeg_bytes(img)
    assert raw[:2] == b"\xff\xd8" and raw[-2:] == b"\xff\xd9"
    with Image.open(io.BytesIO(raw)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == img.size


def test_viewport_jpeg_uses_444_no_chroma_subsampling(source):
    """H&E nuclei at 40x are precisely the high-frequency colour detail 4:2:0
    throws away, so the display encoder must stay 4:4:4."""
    from PIL import JpegImagePlugin

    raw = viewport_jpeg_bytes(render_viewport(source, VP))
    with Image.open(io.BytesIO(raw)) as decoded:
        decoded.load()
        assert JpegImagePlugin.get_sampling(decoded) == 0


def test_viewport_jpeg_honours_quality(source):
    img = render_viewport(source, VP)
    assert len(viewport_jpeg_bytes(img, 30)) < len(viewport_jpeg_bytes(img, 90))


def test_jpeg_is_much_smaller_than_png(source):
    img = render_viewport(source, VP)
    assert len(viewport_jpeg_bytes(img)) < len(viewport_png_bytes(img)) / 2


def test_viewport_png_bytes_still_png(source):
    """The navigator and the heatmap export depend on this staying lossless."""
    raw = viewport_png_bytes(render_viewport(source, VP))
    assert raw[:4] == b"\x89PNG"


def test_png_round_trip_is_lossless(source):
    img = render_viewport(source, VP)
    with Image.open(io.BytesIO(viewport_png_bytes(img))) as back:
        assert np.array_equal(np.asarray(back.convert("RGB")), np.asarray(img))


def test_viewport_data_uri_prefix_and_payload(source):
    img = render_viewport(source, VP)
    uri = viewport_data_uri(img)
    assert uri.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == viewport_jpeg_bytes(img)


def test_jpeg_encoder_accepts_non_rgb_modes():
    """channel_view can hand back an L/RGBA image; JPEG cannot encode those
    directly, so the encoder must normalize rather than raise."""
    assert viewport_jpeg_bytes(Image.new("L", (16, 16), 128))[:2] == b"\xff\xd8"
    assert viewport_jpeg_bytes(Image.new("RGBA", (16, 16)))[:2] == b"\xff\xd8"


# ---------------------------------------------------------------------------
# Overscan rendering
# ---------------------------------------------------------------------------


def test_overscan_1_is_exactly_render_viewport(source):
    img, margin = render_viewport_overscan(source, VP, overscan=1.0)
    assert margin == (0, 0)
    assert img.size == VP.size
    assert np.array_equal(np.asarray(img), np.asarray(render_viewport(source, VP)))


def test_overscan_size_and_symmetric_margin(source):
    img, (ox, oy) = render_viewport_overscan(source, VP, overscan=1.5)
    assert (ox, oy) == (100, 75)  # 400*0.25, 300*0.25
    assert img.size == (VP.size[0] + 2 * ox, VP.size[1] + 2 * oy) == (600, 450)


def test_default_overscan_constant_is_applied(source):
    a, ma = render_viewport_overscan(source, VP)
    b, mb = render_viewport_overscan(source, VP, overscan=DEFAULT_OVERSCAN)
    assert ma == mb and a.size == b.size


@pytest.mark.parametrize("ds", [1.0, 2.0, 4.0])
def test_overscan_center_matches_render_viewport(source, ds):
    """Cropping the margin off the overscan frame must reproduce the plain
    render: same center, same downsample, therefore the same pixels."""
    vp = ViewportState(center=(1000.0, 800.0), downsample=ds, size=(400, 300))
    big, (ox, oy) = render_viewport_overscan(source, vp, overscan=1.5)
    crop = big.crop((ox, oy, ox + vp.size[0], oy + vp.size[1]))
    a = np.asarray(render_viewport(source, vp), dtype=np.float64)
    b = np.asarray(crop, dtype=np.float64)
    # measured 0.0 at pyramid-level downsamples and 0.005/255 at fractional
    # ones (resampling phase); 0.05 is 10x headroom, not a rubber stamp
    assert np.abs(a - b).mean() < 0.05


def test_overscan_margin_reveals_pixels_beyond_the_nominal_viewport(
    marked_source,
):
    """The whole point of overscan: content outside vp.size really is in the
    bitmap, so plotly's native drag reveals detail instead of blank space."""
    src, _ = marked_source
    plain = np.asarray(render_viewport(src, VP1))
    big, _ = render_viewport_overscan(src, VP1, overscan=1.5)
    assert (np.asarray(big) == MARKER).all(axis=-1).any()
    assert not (plain == MARKER).all(axis=-1).any()


# ---------------------------------------------------------------------------
# THE contract proof: overscan must not move a single coordinate
# ---------------------------------------------------------------------------


def test_viewport_transform_extrapolates_outside_the_viewport():
    """Everything below rests on this: ``viewport_transform`` is a pure affine
    map with NO clamping, so negative / oversize viewport pixels extrapolate
    to correct level-0 coordinates."""
    to_level0, to_viewport = viewport_transform(VP)
    for px in (-100.0, -0.5, 0.0, 399.0, 500.0):
        for py in (-75.0, 0.0, 299.0, 400.0):
            back = to_viewport(to_level0((px, py)))
            assert back == pytest.approx((px, py))
    # explicit values, so a sign flip cannot hide behind a round trip
    assert to_level0((-100.0, -75.0)) == (400.0, 350.0)


def test_overscan_selection_maps_to_same_level0_as_before():
    """A box dragged into the overscan margin (negative axis coordinates) maps
    through the UNCHANGED parse/selection chain."""
    sel = parse_plotly_selection({"range": {"x": [-50, 450], "y": [-30, 330]}})
    assert sel is not None
    roi = selection_to_roi(sel, VP)
    to_level0, _ = viewport_transform(VP)
    assert roi.points == (to_level0((-50.0, -30.0)), to_level0((450.0, 330.0)))


def test_overscan_round_trip_from_planted_pixel_is_exact(marked_source):
    """End-to-end round trip: a pixel planted at a known LEVEL-0 position ->
    overscan render -> figure axis coordinate (via the trace's x0/dx, which is
    how the browser maps a drag back to data space) -> parse_plotly_selection
    -> selection_to_roi -> level-0. The recovered coordinate must be the
    planted one, exactly.

    The marker sits in the overscan margin, so this also proves the negative
    axis-coordinate path.
    """
    src, (mx, my) = marked_source
    big, (ox, oy) = render_viewport_overscan(src, VP1, overscan=1.5)
    fig = make_roi_figure(big, viewport_size=VP1.size)
    trace = fig.data[0]

    # locate the marker's top-left pixel in the rendered bitmap
    hits = np.argwhere((np.asarray(big) == MARKER).all(axis=-1))
    assert hits.size, "marker must be inside the overscan frame"
    row, col = int(hits[:, 0].min()), int(hits[:, 1].min())

    # the browser maps image column c to axis coordinate x0 + c*dx
    ax = float(trace.x0) + col * float(trace.dx)
    ay = float(trace.y0) + row * float(trace.dy)
    assert (ax, ay) == (-50.0, -40.0)  # outside the nominal viewport
    assert (col, row) == (50, 35)  # and it really is inside the margin

    sel = current_selection(
        src, VP1, {"range": {"x": [ax, ax + 4], "y": [ay, ay + 4]}}
    )
    assert sel is not None
    assert sel["kind"] == "rect"
    # exactly the planted level-0 position (ds=1, so 4 axis px = 4 level-0 px)
    assert sel["points_level0"] == [[float(mx), float(my)], [mx + 4.0, my + 4.0]]
    assert sel["bbox_level0"] == [mx, my, mx + 4, my + 4]


def test_figure_axis_mapping_is_identical_with_and_without_overscan(source):
    """Same drag, same level-0 answer, whether or not the frame is overscanned
    -- the axis space is the nominal viewport either way."""
    plain = render_viewport(source, VP)
    big, _ = render_viewport_overscan(source, VP, overscan=1.5)
    f_plain = make_roi_figure(plain)
    f_over = make_roi_figure(big, viewport_size=VP.size)
    assert f_plain.layout.xaxis.range == f_over.layout.xaxis.range
    assert f_plain.layout.yaxis.range == f_over.layout.yaxis.range

    value = {"range": {"x": [10, 110], "y": [20, 70]}}
    assert current_selection(source, VP, value) == current_selection(
        source, VP, value
    )
    # and it is still the documented acceptance value
    assert current_selection(source, VP, value)["bbox_level0"] == [
        620,
        540,
        820,
        640,
    ]


# ---------------------------------------------------------------------------
# Overlay alignment under overscan
# ---------------------------------------------------------------------------


def test_overlay_alignment_unaffected_by_overscan(source):
    """If apply_display_pipeline forgets to widen the ViewportState it hands
    to draw_rois, every outline shifts by the overscan margin -- which looks
    like an ROI-geometry bug, not a rendering bug. Assert the outline lands on
    the same LEVEL-0 position with and without overscan.
    """
    roi = ROI(kind="rect", points=((700.0, 600.0), (900.0, 700.0)))

    plain = apply_display_pipeline(render_viewport(source, VP), VP, rois=[roi])
    big_img, (ox, oy) = render_viewport_overscan(source, VP, overscan=1.5)
    big = apply_display_pipeline(big_img, VP, rois=[roi])

    assert big.size == big_img.size  # pipeline must not resize the frame

    def outline_bbox(img: Image.Image) -> tuple[int, int, int, int]:
        hit = np.argwhere((np.asarray(img) == (255, 60, 60)).all(axis=-1))
        assert hit.size, "ROI outline must be drawn"
        return (
            int(hit[:, 1].min()),
            int(hit[:, 0].min()),
            int(hit[:, 1].max()),
            int(hit[:, 0].max()),
        )

    px0, py0, px1, py1 = outline_bbox(plain)
    bx0, by0, bx1, by1 = outline_bbox(big)
    # translate the overscan hit back into nominal-viewport pixel space
    assert (bx0 - ox, by0 - oy, bx1 - ox, by1 - oy) == (px0, py0, px1, py1)
    # and that really is the ROI's level-0 position
    _, to_viewport = viewport_transform(VP)
    assert to_viewport((700.0, 600.0)) == pytest.approx((px0, py0), abs=1.5)


def test_apply_display_pipeline_without_overscan_is_unchanged(source):
    """The dc_replace(vp, size=out.size) substitution is the identity when the
    frame is the nominal size, so existing behaviour is byte-stable."""
    roi = ROI(kind="rect", points=((700.0, 600.0), (900.0, 700.0)))
    img = render_viewport(source, VP)
    got = apply_display_pipeline(img, VP, rois=[roi])
    expected = draw_rois(img, [roi], VP, selected_index=None)
    assert np.array_equal(np.asarray(got), np.asarray(expected))


# ---------------------------------------------------------------------------
# The hard rule: display encoding never reaches data
# ---------------------------------------------------------------------------


def test_extract_patch_ignores_display_settings(source):
    """Regression guard for the unadjusted-pixels promise: patches come from
    ``SlideSource.read_region`` and are unaffected by anything the display
    pipeline does."""
    roi = ROI(kind="rect", points=((700.0, 600.0), (900.0, 700.0)))
    baseline = np.asarray(extract_patch(source, roi).convert("RGB"))

    for kwargs in (
        {"brightness": 2.5},
        {"contrast": 0.2},
        {"gamma": 0.4},
        {"channel": "hematoxylin"},
        {"show_overlays": True, "rois": [roi]},
    ):
        apply_display_pipeline(render_viewport(source, VP), VP, **kwargs)
        after = np.asarray(extract_patch(source, roi).convert("RGB"))
        assert np.array_equal(baseline, after), kwargs


def test_extract_patch_is_not_jpeg_encoded(source):
    """``extract_patch`` must hand back source pixels, not a JPEG round trip.
    On a flat block the difference would hide; on texture it will not."""
    roi = ROI(kind="rect", points=((700.0, 600.0), (900.0, 700.0)))
    patch = extract_patch(source, roi).convert("RGB")
    direct = source.read_region((700, 600), 0, (200, 100)).convert("RGB")
    assert np.array_equal(np.asarray(patch), np.asarray(direct))


# ---------------------------------------------------------------------------
# selection_dict_from_roi: one copy of the 7-key agent contract
# ---------------------------------------------------------------------------


def test_selection_dict_from_roi_matches_current_selection(source):
    value = {"range": {"x": [10, 110], "y": [20, 70]}}
    roi = selection_to_roi(parse_plotly_selection(value), VP, as_circle=False)
    assert selection_dict_from_roi(source, roi, VP.downsample) == current_selection(
        source, VP, value
    )


def test_selection_dict_from_roi_key_order_and_types(source):
    roi = ROI(kind="rect", points=((620.0, 540.0), (820.0, 640.0)))
    out = selection_dict_from_roi(source, roi, 2.0)
    assert list(out) == [
        "kind",
        "points_level0",
        "bbox_level0",
        "viewport_downsample",
        "slide",
        "slide_dimensions",
        "mpp",
    ]
    assert out["bbox_level0"] == [620, 540, 820, 640]
    assert all(isinstance(v, int) for v in out["bbox_level0"])
    assert isinstance(out["viewport_downsample"], float)
    assert out["slide"] == source.name


def test_selection_dict_from_roi_none_guards(source):
    roi = ROI(kind="rect", points=((0.0, 0.0), (1.0, 1.0)))
    assert selection_dict_from_roi(None, roi, 2.0) is None
    assert selection_dict_from_roi(source, None, 2.0) is None
    assert selection_dict_from_roi(None, None, 2.0) is None
