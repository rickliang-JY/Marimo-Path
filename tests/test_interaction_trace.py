"""The interactions table must record HUMAN actions, not just agent tools.

README.md and AGENTS.md both state that selection views, ROI submissions,
label write-backs and analysis runs land in the `interactions` table, and
`hescope/db.py` calls that table the input to "the data flywheel /
automation-bias research". R05-3 measured what actually reached it: every row
ever written in the shipped database was `kind='tool_call'`, from two agent
read tools. app.py never touched `InteractionRepo` at all, and four of the six
declared kinds had no writer anywhere in production code.

The sharpest consequence, and the one asserted below, is the asymmetry:
`label_set` was recorded when an AGENT wrote a label (`annotate_roi`) and NOT
when the human typed one into the Annotations panel -- exactly backwards for
the question the table exists to answer.

The notebook half is driven through `app.run()`'s real objects: the real
`ui_actions["send"]` and the real Annotations-panel buttons, against the real
DB-backed `db` the notebook bootstrapped.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from PIL import Image

from hescope.store.db import (
    INTERACTION_KINDS,
    InteractionRepo,
    ROIRepo,
    SlideRepo,
    get_engine,
    init_db,
)
from hescope.core.rois import ROI
from hescope.viewer.viewer import bootstrap_db


def _rect(x0=0.0, y0=0.0, x1=20.0, y1=20.0) -> ROI:
    return ROI(kind="rect", points=((x0, y0), (x1, y1)))


# --- DBContext.trace: the primitive the notebook handlers call ---------------


def test_trace_records_a_row_and_returns_its_id(tmp_path):
    ctx = bootstrap_db(f"sqlite:///{tmp_path / 'trace.db'}")
    sid = ctx.slide_repo.register(
        source_kind="local", name="s.png", path=str(tmp_path / "s.png"),
        width=10, height=10,
    )

    row_id = ctx.trace("roi_submit", payload={"actor": "human"}, slide_id=sid)

    assert row_id is not None
    rows = InteractionRepo(ctx.engine).recent(kind="roi_submit")
    assert len(rows) == 1
    assert rows[0]["slide_id"] == sid
    assert json.loads(rows[0]["payload"])["actor"] == "human"


def test_trace_is_a_noop_in_db_free_mode_and_never_raises():
    """A UI handler calls this with no try/except of its own: tracing must
    never be able to break the action it is tracing."""
    ctx = bootstrap_db("postgresql://invalid:5432/x")
    assert not ctx.enabled
    assert ctx.trace("roi_submit", payload={"actor": "human"}) is None

    ok = bootstrap_db("sqlite:///:memory:")
    # a bad keyword reaches InteractionRepo.record and must still be swallowed
    assert ok.trace("roi_submit", nonsense=1) is None


# --- the agent side: get_current_selection is a "selection view" -------------


def test_live_selection_tool_records_a_selection_view(tmp_path):
    from hescope.agent.agent_bridge import make_live_selection_tool

    ctx = bootstrap_db(f"sqlite:///{tmp_path / 'sel.db'}")
    sid = ctx.slide_repo.register(
        source_kind="local", name="s.png", path=str(tmp_path / "s.png"),
        width=10, height=10,
    )
    sel = {"kind": "rect", "points_level0": [[0, 0], [4, 4]],
           "bbox_level0": [0, 0, 4, 4]}

    tool = make_live_selection_tool(lambda: sel, lambda: ctx, lambda: sid)
    assert json.loads(tool())["kind"] == "rect"

    rows = InteractionRepo(ctx.engine).recent(kind="selection_view")
    assert len(rows) == 1 and rows[0]["slide_id"] == sid
    assert json.loads(rows[0]["payload"])["bbox_level0"] == [0, 0, 4, 4]


def test_live_selection_tool_without_a_db_is_unchanged():
    """The db arguments are optional; the AGENTS.md contract (JSON string or
    the exact sentinel, never raises) must not move."""
    from hescope.agent.agent_bridge import make_live_selection_tool

    assert make_live_selection_tool(lambda: None)() == "NO_SELECTION"

    def _boom():
        raise RuntimeError("viewer exploded")

    assert "error" in json.loads(make_live_selection_tool(_boom)())


def test_every_declared_interaction_kind_has_a_writer_except_the_reserved_one():
    """R05-3's headline number: four of the six declared kinds had no writer
    in production code. `human_gate` stays unwritten on purpose -- there is no
    human-gate UI -- and both READMEs now say so."""
    root = pathlib.Path(__file__).resolve().parents[1]
    # rglob, not glob: hescope/ is a tree of subpackages now, and a flat glob
    # silently stopped seeing every writer that moved into one -- reporting
    # "nothing records selection_view" about kinds that are recorded fine.
    sources = [root / "app.py"] + sorted((root / "hescope").rglob("*.py"))
    text = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    # a WRITE is `kind="..."` or `db.trace("...")`; the INTERACTION_KINDS
    # declaration itself matches neither, so it cannot vouch for itself
    written = set(re.findall(r'(?:kind=|\.trace\()\s*"([a-z_]+)"', text))

    unwritten = [k for k in INTERACTION_KINDS if k != "human_gate" and k not in written]
    assert unwritten == [], f"declared kinds nothing ever records: {unwritten}"
    assert "human_gate" not in written, (
        "a human-gate writer appeared: drop the 'reserved' note in README.md, "
        "README.zh-CN.md and AGENTS.md"
    )


# --- the notebook side, through app.run()'s real handlers -------------------


@pytest.fixture(scope="module")
def notebook():
    """``app.run()`` with two ROIs already saved for the slide the notebook
    auto-opens.

    Seeded BEFORE the run on purpose: ``app.run()`` executes each cell exactly
    once and performs no reactive re-running, so the Annotations panel only
    ever builds its ``mo.ui.table`` if rows exist at that moment.
    """
    import hescope.core.paths as paths_mod
    from hescope.wsi.demo import generate_demo_slide

    demo = paths_mod.resolve_runtime_dir(".") / "assets" / "demo_he.png"
    if not demo.is_file():  # normally the cached copy the conftest laid down
        generate_demo_slide(demo)
    with Image.open(demo) as img:
        width, height = img.size

    engine = get_engine()  # HESCOPE_DB_URL -> the session's tmp database
    init_db(engine)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name=demo.name, path=str(demo),
        width=width, height=height,
    )
    roi_ids = [ROIRepo(engine).add(slide_id, _rect()) for _ in range(2)]

    import app as appmod

    _outputs, defs = appmod.app.run()
    assert defs["db"].enabled, "the notebook came up in DB-free mode"
    assert defs["get_slide_id"]() == slide_id, (
        "the notebook did not auto-open the seeded slide; the Annotations "
        "panel is showing something else"
    )
    assert defs["annotation_table"] is not None, "the annotation table is empty"
    return defs, slide_id, roi_ids


def _rows(defs, slide_id, kind):
    return [
        r
        for r in InteractionRepo(defs["db"].engine).for_slide(slide_id)
        if r["kind"] == kind
    ]


def _all_kinds(defs, slide_id):
    return [r["kind"] for r in InteractionRepo(defs["db"].engine).for_slide(slide_id)]


def test_send_to_code_agent_records_a_roi_submit(notebook):
    defs, slide_id, _roi_ids = notebook
    defs["set_rois"]([ROI(kind="rect", points=((10.0, 10.0), (110.0, 110.0)))])

    defs["ui_actions"]["send"](None)

    kind, text = defs["get_db_msg"]()
    assert kind == "success", f"the submit itself failed: {text}"
    rows = _rows(defs, slide_id, "roi_submit")
    assert len(rows) == 1, (
        "a real ROI submission left no interactions row; the table README "
        f"describes only saw {_all_kinds(defs, slide_id)}"
    )
    payload = json.loads(rows[0]["payload"])
    assert payload["actor"] == "human"
    assert payload["bbox_level0"] == [10, 10, 110, 110]
    assert rows[0]["roi_id"] is not None


def test_a_human_label_edit_records_label_set_like_the_agent_does(notebook):
    """The asymmetry R05-3 named: `annotate_roi` (agent) recorded label_set,
    the Annotations panel (human) recorded nothing."""
    defs, slide_id, roi_ids = notebook
    defs["annotation_table"]._update(["0"])  # what a row click sends
    assert defs["annotation_table"].value[0]["id"] == roi_ids[0]
    defs["label_input"]._update("tumor")
    defs["notes_input"]._update("typed by a human")

    defs["save_ann_button"]._update(object())  # the real click

    rows = [r for r in _rows(defs, slide_id, "label_set") if r["roi_id"] == roi_ids[0]]
    assert len(rows) == 1, (
        f"the human label edit was not traced; saw {_all_kinds(defs, slide_id)}"
    )
    payload = json.loads(rows[0]["payload"])
    assert payload["actor"] == "human" and payload["label"] == "tumor"
    # and the edit itself still happened
    assert ROIRepo(defs["db"].engine).get(roi_ids[0])["label"] == "tumor"


def test_deleting_an_roi_is_traced(notebook):
    defs, slide_id, roi_ids = notebook
    defs["annotation_table"]._update(["1"])
    assert defs["annotation_table"].value[0]["id"] == roi_ids[1]

    defs["delete_ann_button"]._update(object())

    rows = [
        r
        for r in _rows(defs, slide_id, "roi_delete")
        if json.loads(r["payload"])["roi_id"] == roi_ids[1]
    ]
    assert len(rows) == 1, (
        f"the ROI deletion was not traced; saw {_all_kinds(defs, slide_id)}"
    )
    assert ROIRepo(defs["db"].engine).get(roi_ids[1]) is None


def test_analyze_current_selection_records_an_analysis_run(notebook):
    defs, slide_id, _roi_ids = notebook
    defs["set_payload"](None)  # force the "last session ROI" fallback
    defs["set_rois"]([ROI(kind="rect", points=((10.0, 10.0), (210.0, 210.0)))])

    defs["analyze_button"]._update(object())

    result = defs["get_analysis_result"]()
    assert result[0] == "ok", f"the analysis itself failed: {result}"
    rows = _rows(defs, slide_id, "analysis_run")
    assert len(rows) == 1, (
        f"the analysis run was not traced; saw {_all_kinds(defs, slide_id)}"
    )
    payload = json.loads(rows[0]["payload"])
    assert payload["actor"] == "human" and payload["analysis"] == "nuclei+qc"
