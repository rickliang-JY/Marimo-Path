"""Every UI action must read the selection through live_selection().

app.py keeps two viewing surfaces alive: OpenSeadragon (which reports level-0
coordinates directly) and the legacy plotly figure (viewport pixels, which
must go through viewport_transform). `live_selection()` is the one place that
picks between them, and its own comment says nothing else in the notebook may
re-derive it.

Three handlers did anyway -- "Add ROI", "Send to code agent" and "Analyze
current selection" all read `roi_plot`, which is None whenever OpenSeadragon
drives. They therefore did nothing at all, silently: the user drew a box, got
no feedback, and clicking Add ROI added nothing. The agent tool kept working
the whole time because it already used live_selection(), which is what made
the failure so confusing -- the agent could read a selection the UI insisted
did not exist.
"""

from __future__ import annotations

import pathlib
import re

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")


def _cells() -> list[str]:
    return re.split(r"^@app\.cell", SOURCE, flags=re.M)


def test_only_live_selection_consumes_roi_plot():
    """roi_plot may be read by the cell that builds it and by live_selection,
    and nowhere else."""
    offenders = []
    for cell in _cells():
        if "roi_plot" not in cell:
            continue
        builds_it = "roi_plot = " in cell
        is_router = "def live_selection" in cell
        if builds_it or is_router:
            continue
        # ignore prose: only flag real reads
        code = "\n".join(
            line for line in cell.splitlines() if not line.strip().startswith("#")
        )
        if "roi_plot" in code:
            head = next(
                (l.strip() for l in code.splitlines() if "roi_plot" in l), "?"
            )
            offenders.append(head)
    assert not offenders, (
        "these read roi_plot directly and will silently do nothing when "
        f"OpenSeadragon is driving: {offenders}"
    )


@pytest.mark.parametrize(
    "handler", ["_on_add_roi", "_on_send", "_on_analyze"]
)
def test_selection_handlers_use_the_router(handler):
    cell = next((c for c in _cells() if f"def {handler}" in c), None)
    assert cell is not None, f"{handler} disappeared from app.py"
    assert "live_selection" in cell, (
        f"{handler} does not go through live_selection(); it will ignore "
        "anything drawn on the OpenSeadragon surface"
    )


def test_live_selection_still_branches_on_both_surfaces():
    cell = next((c for c in _cells() if "def live_selection" in c), None)
    assert cell is not None
    assert "osd_viewer" in cell and "roi_plot" in cell, (
        "live_selection must still handle BOTH surfaces"
    )
