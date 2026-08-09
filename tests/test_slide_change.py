"""Opening a slide must invalidate everything derived from the previous one.

``_open_slide_path`` already knew it was at a slide boundary -- it cleared the
measurement readout and the widget de-dup bookkeeping -- but it left the
session ROI list, the last submitted agent payload, the analysis result and
the heatmap grid alone. All four are bare level-0 geometry or level-0 pixel
statistics with no record of which slide they came from, so they were silently
re-attributed to the new one (R04-2, R04-3):

  * "Send to code agent" with nothing drawn falls back to ``get_rois()[-1]``,
    so it resubmitted slide A's rectangle against slide B, wrote it into the
    ``rois`` table under slide B's id, saved a patch PNG, appended to
    ``roi_history.jsonl`` and reported success in green;
  * "Analyze current selection" falls back to the stale payload and reported
    origin "last submitted ROI" while extracting from the new slide;
  * the navigator blended slide A's metric grid over slide B's thumbnail. The
    ``except Exception: pass`` guard there never fires, because a grid from
    another slide does not raise -- ``render_heatmap`` simply resizes it.

Driven through ``app.run()``'s real objects: the real ``open_slide_path``
(the same function the sidebar, the upload box and the TCGA panel call) and
the real ``ui_actions["send"]`` toolbar action.
"""

from __future__ import annotations

import pytest
from PIL import Image

from hescope.demo import generate_demo_slide
from hescope.rois import ROI


@pytest.fixture(scope="module")
def notebook_defs():
    import app as appmod

    _outputs, defs = appmod.app.run()
    return defs


@pytest.fixture(scope="module")
def slide_b(tmp_path_factory):
    """A second slide, deliberately far smaller than the demo one."""
    path = tmp_path_factory.mktemp("slide_b") / "other_slide.png"
    Image.new("RGB", (600, 400), (250, 240, 245)).save(path)
    return str(path)


#: Slide A geometry, comfortably outside slide B (600x400).
STALE_ROI = ROI(kind="rect", points=((1000.0, 800.0), (1400.0, 1200.0)))


def _open_a_then_stale_state_then_b(defs, slide_b):
    defs["open_slide_path"](str(generate_demo_slide("assets/demo_he.png")))
    assert defs["get_source"]().dimensions == (6000, 4000)
    defs["set_rois"]([STALE_ROI])
    defs["set_payload"]("PAYLOAD-FROM-SLIDE-A")
    defs["set_analysis_result"](("ok", "last submitted ROI", {"bbox": [1000, 800]}))
    defs["set_analysis_msg"](("success", "Heatmap 'tissue_fraction': grid 16x24"))
    defs["set_hm_result"]({"grid": "GRID-FROM-SLIDE-A", "params": {}, "png": b""})
    defs["open_slide_path"](slide_b)
    assert defs["get_source"]().dimensions == (600, 400)


def test_a_new_slide_does_not_inherit_the_previous_slides_session_state(
    notebook_defs, slide_b
):
    _open_a_then_stale_state_then_b(notebook_defs, slide_b)
    assert notebook_defs["get_rois"]() == [], (
        "slide A's ROIs are still in the session list, so they are drawn over "
        "slide B and are what 'Send to code agent' falls back to"
    )
    assert notebook_defs["get_payload"]() is None
    assert notebook_defs["get_analysis_result"]() is None
    assert notebook_defs["get_hm_result"]() is None, (
        "slide A's heatmap grid survived the slide change; with 'show heatmap "
        "on navigator' on it is blended onto slide B's thumbnail, rescaled by "
        "grid_coverage into a registration that means nothing"
    )
    assert notebook_defs["get_analysis_msg"]() is None


def test_send_to_agent_after_a_slide_change_does_not_resubmit_the_old_roi(
    notebook_defs, slide_b
):
    """The user-visible half: the green success button must not lie.

    Nothing is drawn on slide B, so the only honest answer is "nothing to
    send". Before the fix this persisted slide A's bbox as a row of slide B's
    annotations and reported success.
    """
    _open_a_then_stale_state_then_b(notebook_defs, slide_b)
    notebook_defs["set_db_msg"](None)
    notebook_defs["ui_actions"]["send"](None)

    msg = notebook_defs["get_db_msg"]()
    assert msg is not None
    kind, text = msg
    assert kind == "warn" and "Nothing to send" in text, (
        f"'Send to code agent' answered {msg!r} on a freshly opened slide "
        "with nothing drawn -- it submitted the previous slide's ROI"
    )
    assert notebook_defs["get_payload"]() is None
