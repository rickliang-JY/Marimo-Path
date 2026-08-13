"""Unified-viewer figure contract tests (offline).

The single plotly figure is the app's one interactive surface: it must be
pannable/zoomable (no fixedrange, scrollZoom config), honor the requested
dragmode, and keep the axis coordinate system equal to VIEWPORT PIXELS so
selection coordinates map 1:1 through ``viewport_transform`` (selection
mapping is unaffected by client-side zoom).

Stage 0 changes two things and both are guarded here:

* the frame travels as a self-encoded JPEG data URI on a ``go.Image`` trace
  instead of ``px.imshow``'s base64 PNG (measured 1.4-2.2 MB -> ~0.4 MB), and
* the image may be an OVERSCAN frame larger than the nominal viewport, in
  which case the trace is offset by ``-margin`` while the axis range stays
  pinned to the nominal viewport.
"""

from __future__ import annotations

import base64

import numpy as np
from PIL import Image

from hescope.viewer.viewer import make_roi_figure


def _img(width: int = 64, height: int = 48) -> Image.Image:
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[..., 0] = 200
    arr[..., 1] = 120
    arr[..., 2] = 160
    return Image.fromarray(arr, "RGB")


def _tissue_like(width: int, height: int) -> Image.Image:
    """Low-frequency structure plus mild grain.

    A flat-colour image would flatter both encoders, and pure white noise
    would unfairly punish JPEG (it is the one signal DCT cannot compact).
    Real H&E is neither: mostly smooth stain gradients with fine texture.
    """
    rng = np.random.default_rng(7)
    coarse = rng.integers(140, 235, size=(height // 8, width // 8, 3), dtype=np.uint8)
    arr = np.asarray(
        Image.fromarray(coarse, "RGB").resize((width, height), Image.BICUBIC)
    ).astype(int)
    arr = np.clip(arr + rng.integers(-8, 9, size=arr.shape), 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


# ---------------------------------------------------------------------------
# Unchanged contract: dragmode, config, axis extents, zoomability
# ---------------------------------------------------------------------------


def test_dragmode_pan_sets_layout_and_scrollzoom_config():
    fig = make_roi_figure(_img(), dragmode="pan")
    assert fig.layout.dragmode == "pan"
    assert fig._config["scrollZoom"] is True


def test_default_dragmode_preserved():
    fig = make_roi_figure(_img())
    assert fig.layout.dragmode == "select"


def test_lasso_dragmode():
    fig = make_roi_figure(_img(), dragmode="lasso")
    assert fig.layout.dragmode == "lasso"


def test_figure_config_contract():
    cfg = make_roi_figure(_img())._config
    assert cfg["scrollZoom"] is True
    assert cfg["displaylogo"] is False
    assert cfg["modeBarButtonsToRemove"] == ["autoScale2d", "resetScale2d"]


def test_figure_extents_match_image_size():
    width, height = 96, 50
    fig = make_roi_figure(_img(width, height))
    x0, x1 = (float(v) for v in fig.layout.xaxis.range)
    y0, y1 = (float(v) for v in fig.layout.yaxis.range)
    assert x1 - x0 == width
    assert abs(y1 - y0) == height
    # y axis stays top-down like the previous autorange-reversed figure
    assert y0 > y1


def test_axes_are_not_fixed_and_uirevision_default():
    fig = make_roi_figure(_img())
    assert not fig.layout.xaxis.fixedrange
    assert not fig.layout.yaxis.fixedrange
    assert fig.layout.uirevision == "constant"


# ---------------------------------------------------------------------------
# Stage 0: JPEG data URI instead of px.imshow's base64 PNG
# ---------------------------------------------------------------------------


def test_figure_source_is_jpeg():
    fig = make_roi_figure(_img())
    trace = fig.data[0]
    assert trace.type == "image"
    assert trace.source.startswith("data:image/jpeg;base64,")
    # and it is a real decodable JPEG, not just a prefix
    raw = base64.b64decode(trace.source.split(",", 1)[1])
    assert raw[:2] == b"\xff\xd8" and raw[-2:] == b"\xff\xd9"


def test_figure_carries_no_z_array():
    """``source`` replaces ``z``; a leftover z would put the whole raw frame
    back into the figure JSON and undo the entire saving."""
    assert make_roi_figure(_img()).data[0].z is None


def test_figure_payload_shrinks():
    """A 1024x768 frame used to serialize to 1.4-2.2 MB of figure JSON via
    px.imshow's base64 PNG (measured on assets/demo_he.png)."""
    fig = make_roi_figure(_tissue_like(1024, 768))
    assert len(fig.to_json()) < 900_000


def test_figure_payload_beats_the_px_imshow_baseline():
    """Content-independent form of the above: the same frame, both ways.

    This is the regression guard that survives someone changing the test
    image -- it compares against the exact path Stage 0 replaced.
    """
    import plotly.express as px

    img = _tissue_like(512, 384)
    arr = np.asarray(img)
    ours = len(make_roi_figure(img).to_json())
    baseline = len(px.imshow(arr).to_json())
    assert ours < baseline / 2


def test_quality_parameter_reaches_the_encoder():
    """Guards the plotly trap: ``px.imshow(binary_format="jpg")`` silently
    pins quality at PIL's default 75 because ``_plotly_utils.data_utils``
    passes ``compress_level`` (a PNG kwarg) to the JPEG saver. Our own
    encoder must actually honour ``quality``."""
    img = _tissue_like(256, 192)
    lo = len(make_roi_figure(img, quality=40).data[0].source)
    hi = len(make_roi_figure(img, quality=95).data[0].source)
    assert hi > lo * 1.5


# ---------------------------------------------------------------------------
# Stage 0: overscan offsets the IMAGE, never the AXES
# ---------------------------------------------------------------------------


def test_no_overscan_is_identity():
    fig = make_roi_figure(_img(64, 48))
    trace = fig.data[0]
    assert (trace.x0, trace.y0) == (0, 0)
    assert (trace.dx, trace.dy) == (1, 1)
    assert tuple(float(v) for v in fig.layout.xaxis.range) == (-0.5, 63.5)
    assert tuple(float(v) for v in fig.layout.yaxis.range) == (47.5, -0.5)


def test_explicit_viewport_size_equal_to_image_is_identity():
    a = make_roi_figure(_img(64, 48))
    b = make_roi_figure(_img(64, 48), viewport_size=(64, 48))
    assert (a.data[0].x0, a.data[0].y0) == (b.data[0].x0, b.data[0].y0)
    assert a.layout.xaxis.range == b.layout.xaxis.range
    assert a.layout.yaxis.range == b.layout.yaxis.range


def test_overscan_offsets_image_not_axes():
    fig = make_roi_figure(_img(1536, 1152), viewport_size=(1024, 768))
    trace = fig.data[0]
    assert (trace.x0, trace.y0) == (-256, -192)
    assert (trace.dx, trace.dy) == (1, 1)
    # axes stay pinned to the NOMINAL viewport: the axis coordinate system is
    # still "viewport pixels with (0,0) at the nominal viewport's top-left"
    assert tuple(float(v) for v in fig.layout.xaxis.range) == (-0.5, 1023.5)
    assert tuple(float(v) for v in fig.layout.yaxis.range) == (767.5, -0.5)


def test_overscan_pixel_index_maps_through_x0_dx():
    """The client turns an axis coordinate a into image column
    ``(a - x0) / dx``. Column 0 must therefore be axis -ox, and the nominal
    viewport's top-left (axis 0) must be image column ox."""
    ox, oy = 256, 192
    fig = make_roi_figure(_img(1536, 1152), viewport_size=(1024, 768))
    t = fig.data[0]
    assert (0 - t.x0) / t.dx == -float(t.x0) == ox
    assert (0 - t.y0) / t.dy == -float(t.y0) == oy
    # the far edge of the nominal viewport is still inside the bitmap
    assert (1023 - t.x0) / t.dx == 1023 + ox < 1536
