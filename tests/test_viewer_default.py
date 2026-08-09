"""The viewer default is load-bearing: pin it.

A widget that constructs fine in Python can still fail to mount in the
browser, and nothing server-side can tell. When OpenSeadragon drives, the
plotly surface is suppressed -- so if the widget is dead the user gets an
empty viewing area, no ROI tool, no drag, and toolbar buttons that command a
widget which is not there. That is exactly what shipping it on by default
did. Until an automated test proves the widget mounts in a real marimo page,
the default must stay on the surface we can verify from Python.
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
        flag = os.environ.get("HESCOPE_ENABLE_OSD", "").strip().lower()
        if flag not in ("1", "true", "yes", "on"):
            return False, "not enabled"
        if os.environ.get("HESCOPE_DISABLE_OSD", "").strip().lower() in (
            "1", "true", "yes", "on"
        ):
            return False, "disabled"
        import anywidget  # noqa: F401
        from hescope.osdviewer import build_esm

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


def test_opensedragon_is_off_by_default():
    assert _probe({}) is False


def test_opensedragon_is_opt_in():
    assert _probe({"HESCOPE_ENABLE_OSD": "1"}) is True


def test_disable_overrides_enable():
    assert _probe({"HESCOPE_ENABLE_OSD": "1", "HESCOPE_DISABLE_OSD": "1"}) is False


@pytest.mark.parametrize("value", ["", "0", "no", "off", "false", "maybe"])
def test_only_truthy_values_enable_it(value):
    assert _probe({"HESCOPE_ENABLE_OSD": value}) is False


def test_app_source_keeps_the_probe_opt_in():
    """Guard against the flag being flipped back in app.py without a test."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "app.py"
    text = src.read_text(encoding="utf-8")
    assert "HESCOPE_ENABLE_OSD" in text, "the opt-in flag disappeared from app.py"
    # the probe must return False when the flag is absent, i.e. the check is
    # 'not in truthy set -> return False', not 'in disable set -> return False'
    assert 'if _flag not in ("1", "true", "yes", "on"):' in text
