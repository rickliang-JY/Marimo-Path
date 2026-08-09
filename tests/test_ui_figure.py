"""Unified-viewer figure contract tests (offline).

The single plotly figure is the app's one interactive surface: it must be
pannable/zoomable (no fixedrange, scrollZoom config), honor the requested
dragmode, and keep axis extents exactly equal to the image size so selection
coordinates stay in viewport-pixel coordinates (selection mapping is
unaffected by client-side zoom).
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from hescope.viewer import make_roi_figure


def _img(width: int = 64, height: int = 48) -> Image.Image:
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[..., 0] = 200
    arr[..., 1] = 120
    arr[..., 2] = 160
    return Image.fromarray(arr, "RGB")


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
