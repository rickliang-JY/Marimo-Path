"""Toolbar click handlers must actually be callable.

`python app.py` executes every cell but never CLICKS anything, so a handler
that raises only on click sailed through every check we had: the four arrow
buttons were dead in the browser with

    NameError: name '_cell_ROlb_pan' is not defined

marimo rewrites cell-private names (`_pan` -> `_cell_<id>_pan`). A handler
stored as `lambda _: _pan(...)` defers that lookup to click time, when the
mangled name is no longer in scope. Storing the function object -- directly,
or captured in a default argument -- binds it while it still exists.

IMPORTANT about coverage: `app.run()` does NOT apply that mangling, so the
handler-invocation tests below pass on the broken code too -- they were
verified to do so. They still earn their place (they catch an unregistered or
genuinely raising handler, and that panning moves the viewport), but the bug
above is pinned by the two tests at the bottom of this file: one reproduces
marimo's mangle-then-discard sequence directly, the other guards the source.
"""

from __future__ import annotations

import ast

import pytest


@pytest.fixture(scope="module")
def notebook_defs():
    import app as appmod

    _outputs, defs = appmod.app.run()
    return defs


@pytest.fixture(scope="module")
def ui_actions(notebook_defs):
    actions = notebook_defs["ui_actions"]
    assert actions, "the toolbar handler registry is empty"
    return actions


@pytest.mark.parametrize(
    "name", ["pan_w", "pan_e", "pan_n", "pan_s", "zoom_fit", "zoom", "add_roi", "send"]
)
def test_every_registered_action_is_present(ui_actions, name):
    assert name in ui_actions, f"toolbar action {name!r} was never registered"
    assert callable(ui_actions[name])


@pytest.mark.parametrize("name", ["pan_w", "pan_e", "pan_n", "pan_s", "zoom_fit"])
def test_camera_actions_do_not_raise_when_clicked(ui_actions, name):
    """The regression: these raised NameError on click with no slide open."""
    ui_actions[name](None)


def test_zoom_action_does_not_raise_when_clicked(ui_actions):
    ui_actions["zoom"](4.0)


@pytest.mark.parametrize("name", ["add_roi", "send"])
def test_roi_actions_do_not_raise_when_clicked(ui_actions, name):
    ui_actions[name](None)


def test_pan_moves_the_viewport_when_a_slide_is_open():
    """Not just 'does not raise' -- the arrows must actually pan.

    Takes its OWN snapshot rather than the module-scoped fixture. `app` is a
    module-level singleton, and several test modules now call `app.run()`, so
    a fixture captured once per module can be left holding getters whose state
    another module has since reset -- which made this assertion fail
    intermittently in the full suite while passing in isolation.
    """
    import app as appmod

    from hescope.demo import generate_demo_slide

    _outputs, defs = appmod.app.run()
    open_slide_path = defs["open_slide_path"]
    get_vp = defs["get_vp"]
    actions = defs["ui_actions"]

    open_slide_path(str(generate_demo_slide("assets/demo_he.png")))
    before = get_vp().center
    actions["pan_e"](None)
    after_east = get_vp().center
    assert after_east[0] > before[0], "pan east did not move the viewport"
    assert after_east[1] == before[1]

    actions["pan_s"](None)
    assert get_vp().center[1] > after_east[1], "pan south did not move the viewport"


# --- the two tests that actually pin the mangling bug ----------------------


def test_late_bound_cell_private_reference_dies_after_the_cell_finishes():
    """Reproduce marimo's sequence: mangle cell-private names, run the cell,
    then discard them. A handler that names one in its BODY is dead by click
    time; one that captured the object in a default argument still works."""
    from marimo._ast.variables import if_local_then_mangle

    mangled = if_local_then_mangle("_pan", "ROlb")
    assert mangled == "_cell_ROlb_pan"

    registry: dict = {}
    ns: dict = {"registry": registry}
    exec(f"def {mangled}(dx, dy):\n    return (dx, dy)\n", ns)
    exec(f"registry['late'] = lambda _v: {mangled}(1, 0)", ns)
    exec(f"registry['bound'] = lambda _v, fn={mangled}: fn(1, 0)", ns)

    del ns[mangled]  # marimo drops cell-private names when the cell ends

    with pytest.raises(NameError, match="_cell_ROlb_pan"):
        registry["late"](None)
    assert registry["bound"](None) == (1, 0)


def test_clicking_add_roi_after_the_cell_ends_adds_an_roi():
    """The user-reported failure, driven through marimo's OWN mangled bytecode.

    ``Add ROI failed: name '_cell_ZBYS_add_roi_or_measure' is not defined``.
    marimo compiles a cell with its private names rewritten and discards them
    once the cell finishes, so this execs the real compiled body, deletes every
    ``_cell_*`` name exactly as marimo does, and only THEN clicks -- which is
    the sequence a source guard can only approximate.
    """
    import app as appmod

    from hescope.rois import ROI

    appmod.app._maybe_initialize()
    cell = appmod.app._graph.cells["ZBYS"]

    added: list = []
    published: list = []

    class _Check:
        value = False

    ns: dict = {
        "ROI": ROI,
        "circle_checkbox": _Check(),
        "measure_checkbox": _Check(),
        "format_measurement": lambda m: "measured",
        "get_rois": lambda: list(added),
        "get_source": lambda: None,
        "live_measure": lambda: None,
        "live_selection": lambda: {
            "kind": "rect",
            "points_level0": ((10.0, 20.0), (110.0, 100.0)),
        },
        "measure_box": lambda a, b, mpp: {},
        "set_measure_msg": published.append,
        "set_rois": lambda rois: added.__setitem__(slice(None), rois),
        "ui_actions": (actions := {}),
    }
    exec(cell.body, ns)

    assert any(k.startswith("_cell_ZBYS_") for k in ns), "cell defined no privates"
    for key in [k for k in ns if k.startswith("_cell_")]:
        del ns[key]  # marimo drops cell-private names when the cell finishes

    actions["add_roi"](object())

    # A successful add ends with set_measure_msg(None), clearing the strip.
    failures = [t for k, t in (m for m in published if m) if k == "danger"]
    assert not failures, f"the click raised instead of adding an ROI: {failures}"
    assert len(added) == 1 and added[0].kind == "rect"
    assert added[0].points == ((10.0, 20.0), (110.0, 100.0))


def _bound_names(fn: ast.AST) -> set[str]:
    """Every name ``fn`` binds itself: parameters, assignments, loop and with
    and except targets, comprehension variables, imports, nested defs."""
    bound: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
    return bound


def _deferred_private_refs(fn: ast.AST, cell_private: dict) -> set[str]:
    """Cell-private names ``fn`` will look up WHEN CALLED rather than now.

    A default argument is evaluated at ``def`` time, so it is safe -- and it is
    also how a handler legitimately reaches a helper. Everything else in the
    body is a lookup deferred to click time, which is exactly when marimo has
    already discarded the name.
    """
    safe = _bound_names(fn)
    defaults = [d for d in getattr(fn.args, "defaults", [])] + [
        d for d in getattr(fn.args, "kw_defaults", []) or [] if d is not None
    ]
    default_nodes = {id(n) for d in defaults for n in ast.walk(d)}
    return {
        node.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in cell_private
        and node.id not in safe
        and id(node) not in default_nodes
    }


def _helpers_bound_as_defaults(fn: ast.AST, cell_private: dict) -> set[str]:
    """Cell-private functions this handler captured in a default argument.

    Safe for this handler, but their OWN bodies still run at click time, so the
    check has to follow them (R07-5's ``_on_add_roi`` -> ``_add_roi_or_measure``
    was one hop past where the old line-based guard looked)."""
    defaults = [d for d in getattr(fn.args, "defaults", [])] + [
        d for d in getattr(fn.args, "kw_defaults", []) or [] if d is not None
    ]
    return {
        n.id
        for d in defaults
        for n in ast.walk(d)
        if isinstance(n, ast.Name) and n.id in cell_private
    }


def test_no_toolbar_action_defers_a_cell_private_lookup_to_click_time():
    """Source guard over app.py's own AST.

    The arrow buttons died as ``ui_actions[k] = lambda _v: _pan(...)``. The Add
    ROI button died later as ``ui_actions["add_roi"] = _on_add_roi``, whose
    BODY called a second cell-private helper -- a shape the old line-based
    ``= lambda`` regex could not see. Both are the same defect, so the guard is
    now structural: walk every callable reachable from ``ui_actions``, through
    default-argument captures, and flag any cell-private name it resolves when
    clicked instead of when defined.
    """
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text(
        encoding="utf-8"
    )
    offenders: list[str] = []

    for cell in [
        n
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.decorator_list
    ]:
        # Names this cell owns privately -- mangled by marimo, then discarded
        # when the cell finishes. Single underscore only; __dunder__ is not.
        cell_private = {
            node.name: node
            for node in cell.body
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("_")
            and not node.name.startswith("__")
        }

        queue: list[tuple[str, ast.AST]] = []
        for node in ast.walk(cell):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(t, ast.Subscript)
                and isinstance(t.value, ast.Name)
                and t.value.id == "ui_actions"
                for t in node.targets
            ):
                continue
            key = ast.unparse(node.targets[0])
            if isinstance(node.value, ast.Lambda):
                queue.append((key, node.value))
            elif isinstance(node.value, ast.Name) and node.value.id in cell_private:
                queue.append((key, cell_private[node.value.id]))

        seen: set[int] = set()
        while queue:
            key, fn = queue.pop()
            if id(fn) in seen:
                continue
            seen.add(id(fn))
            for name in sorted(_deferred_private_refs(fn, cell_private)):
                offenders.append(f"{key} -> {name}")
            for name in sorted(_helpers_bound_as_defaults(fn, cell_private)):
                queue.append((f"{key} -> {name}", cell_private[name]))

    assert not offenders, (
        "these toolbar handlers look a cell-private name up when the button is "
        "clicked, by which time marimo has discarded it, so the click raises "
        f"NameError in the browser instead of doing anything: {offenders}"
    )
