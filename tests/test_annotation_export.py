"""The Annotations panel must report what it did, not what it attempted.

Three findings, one class (bugs/SUMMARY.md class 1), all driven through
app.py's OWN "Annotation editor + export" cell:

  * **R07-6** — a failed export was delivered as a successful download.
    ``_export`` returned ``f"export failed: {exc}"`` and ``mo.download`` handed
    that string over as ``rois.json`` / ``rois.csv`` / ``rois.geojson``, with
    the right filename and the right mimetype, so the browser performed an
    ordinary download and nothing was written to any message channel. The user
    walks away believing the annotations are exported; the failure surfaces
    later and elsewhere, as QuPath rejecting the GeoJSON or a script's
    ``json.loads`` raising, with nothing pointing back at the click.
  * **R07-7** — with no slide open, GeoJSON downloaded an empty
    FeatureCollection while the two buttons beside it downloaded every ROI in
    the database, under one comment promising all three do the same thing.
    An empty-but-valid FeatureCollection is worse than an error: QuPath opens
    it and shows nothing, which reads as "this slide has no annotations".
  * **R07-14** — "Saved annotation for ROI N." / "Deleted ROI N." were written
    on the sole condition that nothing raised, over two repository methods
    documented to no-op when the row is gone.
"""

from __future__ import annotations

import ast
import csv
import io
import json
import pathlib

import marimo as mo
import pytest

from hescope.db import ROIRepo, SlideRepo, export_rois, get_engine, init_db
from hescope.geojson import slide_geojson_text
from hescope.rois import ROI

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _editor_cell():
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
            continue
        src = ast.get_source_segment(SOURCE, node)
        if src is None or "# Annotation editor + export" not in src:
            continue
        ns: dict = {}
        exec(compile("\n" * (node.lineno - 1) + src, str(APP), "exec"), ns)
        return ns[node.name], [a.arg for a in node.args.args]
    raise AssertionError("no @app.cell in app.py holds the annotation editor")


class _DB:
    """The DBContext surface this cell uses."""

    def __init__(self, engine):
        self.engine = engine
        self.enabled = True
        self.roi_repo = ROIRepo(engine)
        self.traces: list = []

    def trace(self, kind, **kw):
        self.traces.append((kind, kw))


@pytest.fixture
def engine(tmp_path):
    eng = get_engine(f"sqlite:///{(tmp_path / 'h.db').as_posix()}")
    init_db(eng)
    return eng


@pytest.fixture
def slide_id(engine):
    return SlideRepo(engine).register(
        source_kind="local",
        name="s.png",
        path=str("s.png"),
        width=1000,
        height=800,
        mpp=0.25,
    )


def _rect(x=10.0, y=20.0):
    return ROI(kind="rect", points=((x, y), (x + 100.0, y + 80.0)))


def _run_editor(engine, *, slide_id, table_value=None, db=None):
    """app.py's own editor cell; returns (db, published, downloads, buttons)."""
    published: dict = {}
    downloads: dict = {}
    buttons: dict = {}
    db = db if db is not None else _DB(engine)

    class _Table:
        value = table_value

    def _spy_download(*, data, filename, mimetype, label):
        downloads[label] = {
            "data": data,
            "filename": filename,
            "mimetype": mimetype,
        }
        return mo.md(label)

    class _MO:
        def __getattr__(self, name):
            return getattr(mo, name)

        @staticmethod
        def download(**kw):
            return _spy_download(**kw)

        class ui:  # noqa: N801 - mirrors mo.ui
            @staticmethod
            def text(**kw):
                return mo.ui.text(**kw)

            @staticmethod
            def text_area(**kw):
                return mo.ui.text_area(**kw)

            @staticmethod
            def button(**kw):
                el = mo.ui.button(**kw)
                buttons[kw.get("label")] = el
                return el

    cell, params = _editor_cell()
    deps = {
        "annotation_table": _Table() if table_value is not None else None,
        "db": db,
        "db_roi_rows": ROIRepo(engine).for_slide(slide_id) if slide_id else [],
        "export_rois": export_rois,
        "get_slide_id": lambda: slide_id,
        "mo": _MO(),
        "set_ann_version": lambda v: published.__setitem__("ann_version", v),
        "set_db_msg": lambda v: published.__setitem__("msg", v),
        "slide_geojson_text": slide_geojson_text,
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the annotation editor cell grew new dependencies: {missing}"
    cell(**{p: deps[p] for p in params})
    return db, published, downloads, buttons


def _download(downloads, label):
    """Resolve one download the way ``mo.download._load`` does: filename first,
    then data (``marimo/_plugins/ui/_impl/input.py``)."""
    spec = downloads[label]
    name = spec["filename"]() if callable(spec["filename"]) else spec["filename"]
    body = spec["data"]() if callable(spec["data"]) else spec["data"]
    return name, body


# --- R07-7 -----------------------------------------------------------------


def test_with_no_slide_open_all_three_exports_agree(engine, slide_id):
    repo = ROIRepo(engine)
    ids = [repo.add(slide_id, _rect(x)) for x in (10.0, 200.0, 400.0)]

    _db, _pub, downloads, _b = _run_editor(engine, slide_id=None)

    _n, js = _download(downloads, "Export ROIs (JSON)")
    _n, cs = _download(downloads, "Export ROIs (CSV)")
    _n, gj = _download(downloads, "Export ROIs (GeoJSON, QuPath)")

    assert [r["id"] for r in json.loads(js)] == ids
    assert len(list(csv.DictReader(io.StringIO(cs)))) == len(ids)
    assert [f["properties"]["roi_id"] for f in json.loads(gj)["features"]] == ids, (
        "the GeoJSON button downloaded an empty FeatureCollection while the "
        "two buttons beside it downloaded every ROI in the database. QuPath "
        "opens an empty collection happily and shows nothing, which reads as "
        "'this slide has no annotations'"
    )


# --- R07-6 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "label, good",
    [
        ("Export ROIs (JSON)", "rois.json"),
        ("Export ROIs (CSV)", "rois.csv"),
        ("Export ROIs (GeoJSON, QuPath)", "rois.geojson"),
    ],
)
def test_a_failed_export_is_not_delivered_as_the_data_file(
    engine, slide_id, tmp_path, label, good
):
    """The database is unreachable when the button is clicked.

    A directory that does not exist is what sqlite reports as
    ``OperationalError: unable to open database file`` -- the state a database
    on a network share, a removed volume or a deleted runtime directory
    reaches. ``db_roi_rows`` still comes from the good engine, so the cell
    renders exactly as it does in the app and only the export fails.
    """
    broken = get_engine(
        f"sqlite:///{(tmp_path / 'gone' / 'h.db').as_posix()}"
    )
    _db, _pub, downloads, _b = _run_editor(
        engine, slide_id=slide_id, db=_DB(broken)
    )

    name, body = _download(downloads, label)

    assert "export failed" in body, "the export unexpectedly succeeded"
    assert name != good, (
        f"a failed export was delivered as {good} -- right name, right "
        "mimetype, 'export failed: ...' as the body -- so the browser "
        "performs an ordinary successful download and the failure only "
        "surfaces later, in whatever tried to read the file"
    )
    assert name.endswith(".txt") and good in name, name


def test_a_successful_export_keeps_its_plain_filename(engine, slide_id):
    ROIRepo(engine).add(slide_id, _rect())
    _db, _pub, downloads, _b = _run_editor(engine, slide_id=slide_id)

    for label, good in (
        ("Export ROIs (JSON)", "rois.json"),
        ("Export ROIs (CSV)", "rois.csv"),
        ("Export ROIs (GeoJSON, QuPath)", "rois.geojson"),
    ):
        name, body = _download(downloads, label)
        assert name == good, (label, name)
        assert "export failed" not in body


def test_each_click_re_reads_the_database(engine, slide_id):
    """Guard against the fix overreaching: the per-click cache must not serve
    a stale body to the NEXT click."""
    repo = ROIRepo(engine)
    repo.add(slide_id, _rect())
    _db, _pub, downloads, _b = _run_editor(engine, slide_id=slide_id)

    _n, first = _download(downloads, "Export ROIs (JSON)")
    repo.add(slide_id, _rect(500.0))
    _n, second = _download(downloads, "Export ROIs (JSON)")

    assert len(json.loads(first)) == 1
    assert len(json.loads(second)) == 2, "the second click served the first's bytes"


# --- R07-14 ----------------------------------------------------------------


def test_saving_an_roi_that_is_gone_is_not_reported_as_saved(engine, slide_id):
    """A second marimo session (or `hescope dedupe-slides` on live data) is
    the reachable second writer; within one session ``set_ann_version`` clears
    the table selection."""
    repo = ROIRepo(engine)
    rid = repo.add(slide_id, _rect(), label="tumour")
    _db, published, _d, buttons = _run_editor(
        engine, slide_id=slide_id, table_value=[{"id": rid, "kind": "rect", "label": "tumour"}]
    )
    repo.delete(rid)  # out of band

    buttons["Save annotation"]._update(object())

    kind, text = published["msg"]
    assert kind != "success", (
        f"a green {published['msg'][1]!r} over a repository method documented "
        "to no-op when the row is gone"
    )
    assert kind == "warn" and str(rid) in text


def test_deleting_an_roi_that_is_gone_is_not_reported_as_deleted(engine, slide_id):
    repo = ROIRepo(engine)
    rid = repo.add(slide_id, _rect(), label="tumour")
    _db, published, _d, buttons = _run_editor(
        engine, slide_id=slide_id, table_value=[{"id": rid, "kind": "rect", "label": "tumour"}]
    )
    repo.delete(rid)

    buttons["Delete ROI"]._update(object())

    kind, text = published["msg"]
    assert kind == "warn" and str(rid) in text, published["msg"]


def test_the_normal_save_and_delete_still_report_success(engine, slide_id):
    repo = ROIRepo(engine)
    rid = repo.add(slide_id, _rect(), label="")
    db, published, _d, buttons = _run_editor(
        engine, slide_id=slide_id, table_value=[{"id": rid, "kind": "rect", "label": "tumour"}]
    )

    buttons["Save annotation"]._update(object())
    assert published["msg"][0] == "success"
    assert [k for k, _ in db.traces] == ["label_set"]

    buttons["Delete ROI"]._update(object())
    assert published["msg"] == ("success", f"Deleted ROI {rid}.")
    assert repo.get(rid) is None
