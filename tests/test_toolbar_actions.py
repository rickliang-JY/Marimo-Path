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


def test_pan_moves_the_viewport_when_a_slide_is_open(notebook_defs):
    """Not just 'does not raise' -- the arrows must actually pan."""
    from hescope.demo import generate_demo_slide

    open_slide_path = notebook_defs["open_slide_path"]
    get_vp = notebook_defs["get_vp"]
    actions = notebook_defs["ui_actions"]

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


def test_no_toolbar_action_names_a_cell_private_helper_in_a_lambda_body():
    """Source guard: ``ui_actions[...] = lambda ...: _helper(...)`` is the
    shape that broke the arrow buttons. Capturing via a default argument, or
    storing the function object directly, is fine."""
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text(
        encoding="utf-8"
    )
    offenders = []
    for line in src.splitlines():
        stripped = line.strip()
        if not re.match(r"ui_actions\[.+\]\s*=\s*lambda", stripped):
            continue
        head, _, body = stripped.partition("lambda")
        params, _, expr = body.partition(":")
        for ref in re.findall(r"\b_[A-Za-z]\w*", expr):
            if f"={ref}" not in params.replace(" ", ""):
                offenders.append(stripped)
                break
    assert not offenders, (
        "these toolbar handlers defer a cell-private lookup to click time and "
        f"will raise NameError in the browser: {offenders}"
    )
