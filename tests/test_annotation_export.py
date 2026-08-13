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

from hescope.store.db import ROIRepo, SlideRepo, export_rois, get_engine, init_db
from hescope.interop.geojson import slide_geojson_text
from hescope.interop.importers import (
    import_annotations,
    parse_asap_xml,
    parse_geojson_annotations,
)
from hescope.core.rois import ROI

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
    handlers: dict = {}
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

            @staticmethod
            def file(**kw):
                # The annotation IMPORT control (R10-2). Captured by label so a
                # test can hand it a file the way the browser would.
                handlers[kw.get("label")] = kw["on_change"]
                return mo.md(str(kw.get("label")))

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
        # R10-2 wired the annotation IMPORT beside the exports.
        "import_annotations": import_annotations,
        "parse_asap_xml": parse_asap_xml,
        "parse_geojson_annotations": parse_geojson_annotations,
        # The editor now also exports the MEASUREMENTS, not just the
        # annotations, which needs the slide (for mpp) and the stats reshape.
        "get_source": lambda: None,
        "roi_stats_rows": lambda engine, sid, mpp=None: [],
        "rows_to_csv": lambda rows: "",
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the annotation editor cell grew new dependencies: {missing}"
    cell(**{p: deps[p] for p in params})
    return db, published, downloads, buttons, handlers


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

    _db, _pub, downloads, _b, _h = _run_editor(engine, slide_id=None)

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
    _db, _pub, downloads, _b, _h = _run_editor(
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
    _db, _pub, downloads, _b, _h = _run_editor(engine, slide_id=slide_id)

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
    _db, _pub, downloads, _b, _h = _run_editor(engine, slide_id=slide_id)

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
    _db, published, _d, buttons, _h = _run_editor(
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
    _db, published, _d, buttons, _h = _run_editor(
        engine, slide_id=slide_id, table_value=[{"id": rid, "kind": "rect", "label": "tumour"}]
    )
    repo.delete(rid)

    buttons["Delete ROI"]._update(object())

    kind, text = published["msg"]
    assert kind == "warn" and str(rid) in text, published["msg"]


def test_the_normal_save_and_delete_still_report_success(engine, slide_id):
    repo = ROIRepo(engine)
    rid = repo.add(slide_id, _rect(), label="")
    db, published, _d, buttons, _h = _run_editor(
        engine, slide_id=slide_id, table_value=[{"id": rid, "kind": "rect", "label": "tumour"}]
    )

    buttons["Save annotation"]._update(object())
    assert published["msg"][0] == "success"
    assert [k for k, _ in db.traces] == ["label_set"]

    buttons["Delete ROI"]._update(object())
    assert published["msg"] == ("success", f"Deleted ROI {rid}.")
    assert repo.get(rid) is None


# --- R10-2: the import door ------------------------------------------------

IMPORT_LABEL = "Import annotations (QuPath GeoJSON / ASAP XML)"


class _Uploaded:
    def __init__(self, name, text):
        self.name = name
        self.contents = text.encode("utf-8")


def _import(engine, slide_id, name, text, *, db=None):
    _db, published, _d, _b, handlers = _run_editor(
        engine, slide_id=slide_id, db=db
    )
    assert IMPORT_LABEL in handlers, (
        "the Annotations panel has three export buttons and no way to import: "
        "hescope/importers.py is complete, tested, and reachable from nothing"
    )
    handlers[IMPORT_LABEL]([_Uploaded(name, text)])
    return published


def _fc(*features):
    return json.dumps({"type": "FeatureCollection", "features": list(features)})


def _poly(coords, label="tumour"):
    return {
        "type": "Feature",
        "properties": {"classification": {"name": label}},
        "geometry": {"type": "Polygon", "coordinates": [coords]},
    }


SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]


def test_a_qupath_geojson_becomes_rois_on_this_slide(engine, slide_id):
    published = _import(
        engine, slide_id, "annotations.geojson", _fc(_poly(SQUARE))
    )

    rows = ROIRepo(engine).for_slide(slide_id)
    assert len(rows) == 1
    assert rows[0]["label"] == "tumour"
    assert rows[0]["bbox"] == [0, 0, 100, 100]
    assert published["msg"][0] == "success"
    assert "Imported 1 annotation(s)" in published["msg"][1]
    assert "ann_version" in published, "the panels were never told to refresh"


def test_what_the_parser_could_not_represent_is_reported(engine, slide_id):
    """An import that keeps some features and reports plain success is this
    project's signature failure. Points and LineStrings are skipped on purpose;
    the message has to say so, and must not be a green tick."""
    point = {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [5, 5]}}
    published = _import(
        engine, slide_id, "mixed.geojson", _fc(_poly(SQUARE), point)
    )

    kind, text = published["msg"]
    assert len(ROIRepo(engine).for_slide(slide_id)) == 1
    assert kind == "warn", f"one of two features was dropped under {kind!r}: {text!r}"
    assert "Skipped" in text and "1" in text


def test_an_asap_xml_is_routed_by_extension(engine, slide_id):
    xml = (
        '<ASAP_Annotations><Annotations>'
        '<Annotation Name="A" PartOfGroup="tumour" Type="Polygon"><Coordinates>'
        '<Coordinate Order="0" X="0" Y="0"/><Coordinate Order="1" X="50" Y="0"/>'
        '<Coordinate Order="2" X="50" Y="40"/>'
        '</Coordinates></Annotation></Annotations></ASAP_Annotations>'
    )
    published = _import(engine, slide_id, "camelyon.xml", xml)

    rows = ROIRepo(engine).for_slide(slide_id)
    assert len(rows) == 1 and rows[0]["kind"] == "polygon"
    assert rows[0]["label"] == "tumour"
    assert published["msg"][0] == "success"


def test_a_malformed_file_reports_instead_of_writing_nothing_silently(
    engine, slide_id
):
    """``parse_geojson_annotations`` is deliberately tolerant: it does not
    raise on bad JSON, it records ``skipped={'unreadable GeoJSON
    (JSONDecodeError)': 1}``. So the correct outcome is not `danger` (nothing
    crashed) but a `warn` that NAMES the reason — what must never happen is a
    green tick, or silence, over a file that contributed nothing."""
    published = _import(engine, slide_id, "broken.geojson", "{not json")
    kind, text = published["msg"]
    assert kind != "success"
    assert "broken.geojson" in text
    assert "unreadable GeoJSON" in text, f"the reason was not reported: {text!r}"
    assert ROIRepo(engine).for_slide(slide_id) == []


def test_a_valid_but_empty_file_is_not_a_green_tick(engine, slide_id):
    published = _import(engine, slide_id, "empty.geojson", _fc())
    assert published["msg"][0] == "warn"
    assert "Imported 0 annotation(s)" in published["msg"][1]


def test_importing_with_no_slide_open_says_what_to_do(engine):
    published = _import(engine, None, "a.geojson", _fc(_poly(SQUARE)))
    kind, text = published["msg"]
    assert kind == "warn" and "Open a slide" in text


def test_an_empty_upload_is_a_no_op(engine, slide_id):
    _db, published, _d, _b, handlers = _run_editor(engine, slide_id=slide_id)
    handlers[IMPORT_LABEL]([])
    assert "msg" not in published
