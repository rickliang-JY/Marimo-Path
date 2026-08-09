"""Which cells a state change re-runs, asserted over marimo's OWN graph.

app.py states this rule twice in prose -- "THIS CELL MUST NOT REFERENCE
get_vp ... a re-constructed mo.ui element comes back at its default value"
(toolbar) and "*** THIS CELL'S DEPENDENCY SET IS LOAD-BEARING ***" (the
OpenSeadragon cell) -- and enforced it nowhere. It leaked twice:

  * R04-1: the annotation browser took ``get_vp`` into its signature (added,
    ironically, by R03-3's own fix) so that its row-click handler could read
    the viewport. That put ``mo.ui.table`` and, through its value, the
    label/notes boxes of the annotation editor inside the viewport's
    dependency set: every pan and zoom rebuilt them, and a rebuilt element
    comes back at its DEFAULT value on purpose (marimo stamps each one with a
    fresh token so a re-run resets it -- ``ui_element.py``). Row selection,
    ROI highlight and half-typed label all died on a mouse drag, on the one
    panel that holds user-TYPED data.
  * R04-4: the tile-size slider and the "show heatmap on navigator" checkbox
    were parked in the cell that reads ``get_models_version()`` to refresh the
    model dropdown, so a successful "Train from annotations" reset both.

``app.run()`` cannot see either: it runs every cell exactly once and performs
no reactive re-running. So these tests ask marimo's own ``DirectedGraph``
instead -- the refs it computed from app.py's real cell bodies -- and apply
its own re-run rule (``cell_runner.resolve_state_updates`` condition 5: run a
cell when the state object is among its refs), plus the descendants those
re-runs drag along.
"""

from __future__ import annotations

import ast

import pytest

#: mo.ui constructors that hold state the USER put there and that a rebuild
#: therefore destroys. ``mo.ui.button`` and ``mo.download`` are excluded:
#: they are stateless triggers, and rebuilding one is invisible.
_USER_INPUT = frozenset(
    {
        "text", "text_area", "code_editor", "number", "date", "datetime",
        "slider", "range_slider", "checkbox", "switch", "radio", "dropdown",
        "multiselect", "file", "file_browser", "form",
    }
)


@pytest.fixture(scope="module")
def graph():
    import app as appmod

    appmod.app._maybe_initialize()
    return appmod.app._graph


def _user_input_elements(code: str) -> list[str]:
    """``mo.ui.<name>(...)`` calls in ``code`` that carry user state.

    ``mo.ui.table`` counts only when it is given ``selection=`` or
    ``on_change=``. Without those it is a read-only display of a dict (the
    Selection-analysis panel builds three of them) and rebuilding it loses
    nothing; with them it is an input, and a rebuild drops the selected row.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Attribute)
            and fn.value.attr == "ui"
            and isinstance(fn.value.value, ast.Name)
            and fn.value.value.id == "mo"
        ):
            continue
        kwargs = {kw.arg for kw in node.keywords}
        if fn.attr in _USER_INPUT:
            found.append(fn.attr)
        elif fn.attr == "table" and kwargs & {"selection", "on_change"}:
            found.append("table(selection=...)")
    return found


def _rerun_closure(graph, state_getter: str) -> set[str]:
    """Cells marimo re-runs when ``state_getter``'s setter fires, transitively.

    Condition 5 of ``resolve_state_updates`` picks the roots (the state object
    among a cell's refs); everything downstream of a root re-runs because its
    inputs changed. The runtime also skips the setter's own cell and anything
    already scheduled after it -- both make the real set SMALLER, so ignoring
    them keeps this guard conservative rather than lenient.
    """
    roots = {cid for cid, cell in graph.cells.items() if state_getter in cell.refs}
    closure = set(roots)
    for cid in roots:
        closure |= set(graph.descendants(cid))
    return closure


def _label(graph, cid: str) -> str:
    return f"{cid}({', '.join(sorted(graph.cells[cid].defs)[:3]) or 'no defs'})"


def test_no_cell_rebuilt_by_a_viewport_change_holds_user_input(graph):
    """A pan or zoom must not reset a control the user typed in or selected.

    ``set_vp`` fires on every real mouse gesture -- the widget reports at a
    0.5 viewport-px / 1% zoom threshold -- so anything in this closure is
    rebuilt constantly.
    """
    offenders = {
        _label(graph, cid): elements
        for cid in sorted(_rerun_closure(graph, "get_vp"))
        if (elements := _user_input_elements(graph.cells[cid].code))
    }
    assert offenders == {}, (
        "these cells are re-run by every pan and zoom and construct elements "
        "holding user input, which marimo resets on a re-run -- move whatever "
        "needs the live viewport into a named ui_actions handler, the way the "
        f"toolbar and the annotation browser do: {offenders}"
    )


def test_the_annotation_panel_depends_on_the_slide_and_the_rows_only(graph):
    """The specific leak R04-1 found, named rather than inferred."""
    browser = [
        cid
        for cid, cell in graph.cells.items()
        if "annotation_table" in cell.defs
    ]
    assert len(browser) == 1, f"expected one annotation-browser cell, got {browser}"
    refs = graph.cells[browser[0]].refs
    assert "get_vp" not in refs, (
        "the annotation browser reads the live viewport again, so every pan "
        "rebuilds mo.ui.table and the label/notes boxes below it; the jump "
        "belongs in ui_actions['jump_bbox']"
    )
    editor = [cid for cid, cell in graph.cells.items() if "label_input" in cell.defs]
    assert len(editor) == 1, f"expected one annotation-editor cell, got {editor}"
    assert "get_vp" not in graph.cells[editor[0]].refs


@pytest.mark.parametrize("element", ["hm_tile_slider", "hm_nav_checkbox"])
def test_sweep_controls_are_not_rebuilt_by_a_training_run(graph, element):
    """R04-4: training refreshes the MODEL LIST, nothing else.

    ``hm_model_dropdown`` is legitimately in ``get_models_version``'s closure
    -- refreshing it is the whole point -- so this is stated per element
    rather than as a blanket rule over the closure.
    """
    owners = [cid for cid, cell in graph.cells.items() if element in cell.defs]
    assert len(owners) == 1, f"expected one cell defining {element}, got {owners}"
    assert "get_models_version" not in graph.cells[owners[0]].refs, (
        f"{element} is built in a cell that re-runs on every successful "
        "'Train from annotations', so the user's choice is silently reset by "
        "a click that has nothing to do with it"
    )
