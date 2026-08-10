"""Clicking a row in the annotation browser must move the viewer.

Two defects sat on this one path, and only the first was visible:

  * the table renders the bbox as text (``"bbox": str(_r["bbox"])``) and the
    handler read that same cell back as coordinates, so
    ``jump_viewport_for_bbox`` unpacked the string ``"[1000, 2000, ...]"``
    character by character and raised ValueError out of ``mo.ui.table``'s
    ``on_change`` -- on every row click, always (R03-2);
  * behind it, the handler wrote ``set_vp``, which only updates
    ``ViewportState`` (navigator, header, the agent contract's
    ``viewport_downsample``). The default viewing surface is OpenSeadragon and
    it moves only via ``set_cam``, i.e. ``move_camera``. Repairing the bbox
    alone would have shipped the quieter failure: readouts jump, the slide
    does not, no error (R03-3).

These are marimo cell bodies, so they cannot be imported -- and a test that
re-implements the row composition tests the copy (see R01-4). Instead this
module EXECUTES app.py's own source text: the annotation-browser cell, the
camera-actions cell, ``move_camera`` and the camera-consumer cell are sliced
out with ``ast`` and run against stubs, and the click goes through the REAL
``mo.ui.table`` selection path. Editing any of those back turns this red.

The jump now travels one hop further -- the browser cell looks
``ui_actions["jump_bbox"]`` up at click time instead of reading the viewport
itself (R04-1), so this wiring runs the camera cell too. That indirection is
what keeps the Annotations panel out of ``get_vp``'s dependency set;
``tests/test_notebook_reactivity.py`` is what pins it there.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import textwrap

import marimo as mo
import pytest

from hescope.rois import ViewportState
from hescope.viewer import jump_viewport_for_bbox

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _cell(marker: str):
    """The @app.cell function whose source contains ``marker``, as a callable.

    ``ast`` reports a decorated function's lineno at the ``def``, so the
    segment is the plain function -- exec it and the cell becomes an ordinary
    callable taking its dependencies as keyword arguments.
    """
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
            continue
        src = ast.get_source_segment(SOURCE, node)
        if src is None or marker not in src:
            continue
        ns: dict = {}
        # pad so a traceback out of the cell names its real app.py line
        exec(compile("\n" * (node.lineno - 1) + src, str(APP), "exec"), ns)
        return ns[node.name], [a.arg for a in node.args.args]
    raise AssertionError(f"no @app.cell in app.py contains {marker!r}")


def _nested_def(name: str, globals_: dict):
    """Exec app.py's own source for a helper defined inside a cell."""
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            src = textwrap.dedent(ast.get_source_segment(SOURCE, node))
            exec(
                compile("\n" * (node.lineno - 1) + src, str(APP), "exec"), globals_
            )
            return globals_[name]
    raise AssertionError(f"{name}() disappeared from app.py")


class _RecordingViewer:
    """Stands in for the anywidget; records what the camera channel commands."""

    def __init__(self):
        self.gotos: list[tuple[float, ...]] = []

    def goto(self, bbox):
        self.gotos.append(tuple(float(v) for v in bbox))


class _Src:
    dimensions = (4000, 3000)
    level_downsamples = (1.0, 4.0, 16.0)


class _Db:
    enabled = True


ROW = {
    "id": 7,
    "slide_id": 1,
    "kind": "rect",
    "bbox": [1000, 2000, 1600, 2600],
    "label": "tumour",
    "notes": "",
    "created_at": "2026-08-09T10:00:00",
}


def _wire():
    """Build the notebook slice: viewport state, camera channel, the cells."""
    vp_box = {
        "vp": ViewportState(center=(0.0, 0.0), downsample=8.0, size=(1024, 768))
    }
    cam_box = {"cam": None}
    msgs: list = []
    env = {
        "set_vp": lambda v: vp_box.__setitem__("vp", v),
        "set_cam": lambda c: cam_box.__setitem__("cam", c),
    }
    move_camera = _nested_def("move_camera", env)

    # Everything that reads the LIVE viewport lives in the camera cell, and
    # the annotation jump is one of those things (R04-1). Run it first so the
    # named action is registered before the row click looks it up.
    ui_actions: dict = {}
    common = {
        "ViewportState": ViewportState,
        "dc_replace": dataclasses.replace,
        "get_source": lambda: _Src(),
        "get_vp": lambda: vp_box["vp"],
        "jump_viewport_for_bbox": jump_viewport_for_bbox,
        "mo": mo,
        "move_camera": move_camera,
        "ui_actions": ui_actions,
        # provided so the PRE-FIX shape of the browser cell also constructs;
        # the assertion that goes red then is "the camera never moved", not a
        # TypeError about a missing argument.
        "set_vp": env["set_vp"],
        # Every camera action reports its own failure now: marimo swallows an
        # exception raised inside an on_click callback, so an unguarded one is
        # a click that does nothing and says nothing (R07-5).
        "set_db_msg": lambda v: msgs.append(v),
    }
    cam_cell, cam_params = _cell("Camera actions for the toolbar buttons")
    missing = [p for p in cam_params if p not in common]
    assert not missing, f"camera cell grew new dependencies: {missing}"
    cam_cell(**{p: common[p] for p in cam_params})

    cell, params = _cell("Annotation browser (inside the Annotations accordion)")
    deps = dict(
        common,
        db=_Db(),
        db_roi_error=None,
        db_roi_rows=[ROW],
        get_slide_id=lambda: 1,
    )
    missing = [p for p in params if p not in deps]
    assert not missing, f"annotation-browser cell grew new dependencies: {missing}"
    _view, table = cell(**{p: deps[p] for p in params})
    return table, vp_box, cam_box


def test_annotation_row_click_moves_the_openseadragon_camera():
    table, vp_box, cam_box = _wire()
    assert table is not None, "the annotation table was not built"

    # the real selection path: this is what the browser sends on a row click
    table._update(["0"])

    # 600x600 bbox in a 1024x768 viewport at fill=0.8 -> downsample clamps to
    # 1.0, centre is the bbox centre
    vp = vp_box["vp"]
    assert vp.center == pytest.approx((1300.0, 2300.0))
    assert vp.downsample == pytest.approx(1.0)

    # ...and the command must actually reach the widget, through the same
    # consumer cell app.py uses.
    viewer = _RecordingViewer()
    consumer, params = _cell("Programmatic camera moves (pan buttons")
    assert set(params) == {"get_cam", "osd_viewer", "viewer_bus"}
    bus = {"cam_token": None, "vp": None, "sel_seq": None}
    consumer(get_cam=lambda: cam_box["cam"], osd_viewer=viewer, viewer_bus=bus)

    assert viewer.gotos == [(788.0, 1916.0, 1812.0, 2684.0)], (
        "the annotation jump did not reach the OpenSeadragon surface; it only "
        "moved ViewportState, so the navigator jumps and the slide does not"
    )


def test_annotation_table_still_shows_the_bbox_as_text():
    """The display/data split is the point: the cell stays human-readable and
    the jump uses the numeric bbox, looked up by row id."""
    table, _vp_box, _cam_box = _wire()
    table._update(["0"])
    shown = table.value[0]["bbox"]
    assert isinstance(shown, str) and shown.startswith("["), (
        "the table column is no longer text -- if it is numeric now, the "
        "id lookup can be dropped, but check the jump first"
    )
