"""The viewer default is load-bearing: pin it.

A widget that constructs fine in Python can still fail to mount in the
browser, and nothing server-side can tell. When OpenSeadragon drives, the
plotly surface is suppressed -- so if the widget is dead the user gets an
empty viewing area, no ROI tool, no drag, and toolbar buttons that command a
widget which is not there. That is exactly what shipping it on by default
did the first time.

It is on by default again, but only because the gap is now closed by proof:
tests/browser/test_marimo_mount.py loads the real marimo page in headless
Chrome and asserts the widget mounts, OpenSeadragon initialises, tiles are
fetched from the loopback server and no JS error is raised. These tests pin
the switch itself; that one pins the thing the switch assumes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

_PROBE = textwrap.dedent(
    """
    import os

    def probe():
        if os.environ.get("HESCOPE_DISABLE_OSD", "").strip().lower() in (
            "1", "true", "yes", "on"
        ):
            return False, "disabled"
        import anywidget  # noqa: F401
        from hescope.viewer.osdviewer import build_esm

        build_esm()
        return True, None

    print(probe()[0])
    """
)


def _probe(env_overrides: dict[str, str]) -> bool:
    env = {k: v for k, v in os.environ.items()
           if k not in ("HESCOPE_ENABLE_OSD", "HESCOPE_DISABLE_OSD")}
    env.update(env_overrides)
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, env=env
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip() == "True"


def test_openseadragon_is_on_by_default():
    assert _probe({}) is True


def test_disable_flag_falls_back_to_plotly():
    assert _probe({"HESCOPE_DISABLE_OSD": "1"}) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_every_truthy_spelling_disables_it(value):
    assert _probe({"HESCOPE_DISABLE_OSD": value}) is False


@pytest.mark.parametrize("value", ["", "0", "no", "off", "false", "maybe"])
def test_non_truthy_values_leave_it_on(value):
    assert _probe({"HESCOPE_DISABLE_OSD": value}) is True


def test_app_source_keeps_the_probe_opt_in():
    """Guard against the flag being flipped back in app.py without a test."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "app.py"
    text = src.read_text(encoding="utf-8")
    assert "HESCOPE_DISABLE_OSD" in text, "the escape hatch disappeared from app.py"
    # the fallback must remain reachable without editing code
    assert "tests/browser/test_marimo_mount.py" in text, (
        "the comment pointing at the test that justifies this default is gone"
    )
