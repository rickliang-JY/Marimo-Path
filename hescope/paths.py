"""Runtime data-directory resolution for HE-Scope.

app.py computes paths (agent_out/, data/, assets/demo_he.png) relative to
its own file location. That is correct for a repo checkout or an editable
install, but in a non-editable install (pip / uvx) app.py lives in
site-packages — a read-only, shared, virtualenv-internal location that must
never receive user data. ``resolve_runtime_dir`` picks the writable root:

- repo / editable layout (app.py sits next to the hescope/ package dir AND
  that location is writable) -> the app dir itself (unchanged behavior);
- anything that looks like a non-editable install (app_dir inside
  site-packages / dist-packages — even when writable, as with pip --user
  or a venv) -> ``Path.cwd() / "hescope_runtime"``;
- otherwise (missing package dir, read-only app dir) ->
  ``Path.cwd() / "hescope_runtime"``.

CWD is preferred over ``~/.hescope`` so that two projects run from
different working directories keep separate outputs (matching how users
already separate data by repo today), and so the written location is
obvious to the user.
"""

from __future__ import annotations

import os
from pathlib import Path

RUNTIME_DIR_NAME = "hescope_runtime"
# Non-editable installs (pip/uvx) place app.py in one of these; an
# editable install keeps app.py at the repo root, outside them.
_PACKAGE_DIRS = frozenset(("site-packages", "dist-packages"))


def _looks_like_installed_package(path: Path) -> bool:
    """True if ``path`` lives inside a site-packages / dist-packages dir."""
    return any(part in _PACKAGE_DIRS for part in path.parts)


def _is_writable_dir(path: Path) -> bool:
    """True if ``path`` is an existing directory we can create files in."""
    if not path.is_dir():
        return False
    if not os.access(path, os.W_OK | os.X_OK):
        return False
    # os.access can be optimistic when running as root; probe for real.
    try:
        probe = path / f".hescope_write_probe_{os.getpid()}"
        probe.touch(exist_ok=False)
        probe.unlink()
        return True
    except OSError:
        return False


def resolve_runtime_dir(app_dir: str | Path) -> Path:
    """Return the writable root for runtime data (created if needed).

    ``app_dir`` is the directory containing app.py. If it looks like the
    repo / editable-install layout (a ``hescope/`` package directory right
    next to app.py, outside site-packages) and is writable, it is returned
    unchanged; otherwise ``<cwd>/hescope_runtime`` is used (see module
    docstring for why CWD wins over a home directory).
    """
    app_dir = Path(app_dir)
    if (
        not _looks_like_installed_package(app_dir)
        and (app_dir / "hescope").is_dir()
        and _is_writable_dir(app_dir)
    ):
        runtime = app_dir
    else:
        runtime = Path.cwd() / RUNTIME_DIR_NAME
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime
