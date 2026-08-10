# Field reports — bugs found by using the app, not by reviewing it

The seven numbered rounds are reviews: a lens is chosen, code is read, findings
are filed. This file is for the other source — a defect the user hit in the
running notebook and reported from the status strip. They are worth separating
because they measure something the rounds cannot: **which defects survive the
review process**, and why the guard written for the last one failed to see the
next.

---

## F01 — Add ROI raised `NameError` on click

**Reported.** From the running app on port 2718:

```
selection: rect 2525x2117 px (638x535 um) — not added yet | 0 ROI(s) this session
Measurement: Add ROI failed: name '_cell_ZBYS_add_roi_or_measure' is not defined
```

**What was wrong.** `app.py`'s ROI cell (marimo cell `ZBYS`) registered its
button handler as a named function whose *body* called a second cell-private
helper:

```python
def _on_add_roi(_):
    try:
        _add_roi_or_measure()          # <- resolved when clicked, not when defined
    except Exception as _exc:
        set_measure_msg(("danger", f"Add ROI failed: {_exc}"))

ui_actions["add_roi"] = _on_add_roi
```

marimo renames a cell-private name (`_add_roi_or_measure` ->
`_cell_ZBYS_add_roi_or_measure`) and **discards it when the cell finishes**. The
handler object outlives the cell; the name does not. So every click raised, and
the button did nothing for the whole session.

**Fix.** Capture the function object in a default argument, which is evaluated
at `def` time — the same correction that fixed the arrow buttons:

```python
def _on_add_roi(_, _run=_add_roi_or_measure):
    ...
```

**This is class 1's mirror image, and it is why the report exists at all.**
Round 07's R07-5 wrapped this handler in `try/except` so a failure would be
*written somewhere* instead of vanishing into marimo's swallowed traceback.
That hardening is what turned an invisible dead button into the message quoted
above. The message names the mangled symbol, which names the cell, which names
the defect — the entire diagnosis was in the report.

---

## Why the existing guard missed it

`tests/test_toolbar_actions.py` already carried a guard written for the arrow
buttons. It scanned `app.py` line by line for

```
ui_actions[...] = lambda ...: _helper(...)
```

That is one *syntactic* shape of the defect. `_on_add_roi` is the same defect in
a different shape — a named function, registered by reference, with the deferred
lookup one level down in its body — so the guard read the line
`ui_actions["add_roi"] = _on_add_roi`, found no `lambda`, and passed.

**The lesson is about how the first guard was scoped:** it was written against
the *reproduction* rather than against the *rule*. The rule is "nothing reachable
from `ui_actions` may resolve a cell-private name at click time"; the guard
encoded "no `ui_actions` line contains a lambda that calls an underscore name".

The replacement, `test_no_toolbar_action_defers_a_cell_private_lookup_to_click_time`,
works over `app.py`'s AST: for every cell, it collects the cell-private
functions, walks every callable registered into `ui_actions` — lambda or named —
and follows default-argument captures transitively, since a helper safely bound
as a default still runs its own body at click time. Anything it can reach that
loads a cell-private name it does not itself bind is an offender.

Alongside it, `test_clicking_add_roi_after_the_cell_ends_adds_an_roi` drives
**marimo's own mangled bytecode**: it execs `app._graph.cells["ZBYS"].body`,
deletes every `_cell_*` name exactly as marimo does when a cell ends, and only
then clicks. A source guard approximates the failure; this one reproduces it.

Both were confirmed to fail against the pre-fix `app.py` (`git stash push
app.py`, run, pop) before the fix landed — the check that the earlier
`app.run()`-based toolbar test skipped, which is how it came to pass on broken
code.

**Coverage this still does not have:** factory-produced handlers
(`on_click=_make_view(_i)`, still open item 7) are registered by call, not by
name, and remain outside both guards.
