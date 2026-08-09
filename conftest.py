"""Session-wide isolation of the developer's runtime data from the test suite.

The suite drives the REAL objects on purpose -- `app.run()` executes every
notebook cell, `bootstrap_db()` opens a real engine, `ui_actions["send"]` runs
a real submit -- and every one of them resolves its storage exactly the way a
user's session does: the database from `HESCOPE_DB_URL` or
`<repo>/data/hescope.db`, and `agent_out/` + `data/` + `data/tcga/` from
`hescope.paths.resolve_runtime_dir`. Nothing overrode either, so a test run
wrote into the artefact the app exists to protect. By the time this was filed
the shipped `data/hescope.db` held eleven junk slide rows -- one per pytest
tmpdir, never deduplicated because the tmp path is unique per run -- plus an
ROI row and an `agent_runs` row from a past run that really did drive the
toolbar's submit, and `agent_out/patches/` held six PNGs of that ROI.

Two levers cover all of it:

* `HESCOPE_DB_URL` -> a per-session sqlite file. This is the documented
  override (`hescope.db.get_engine`), so nothing in the package has to know.
* `hescope.paths.resolve_runtime_dir` -> a per-session directory. There is no
  env override for the runtime root by design (it is derived from where app.py
  lives), so this is patched instead of configured.

Both are applied from a session fixture rather than at import time, so that
`hescope.db.DEFAULT_DB_URL` -- computed once, at import, from the real repo
root -- keeps meaning what `tests/test_db.py` asserts it means. For the same
reason `tests/test_paths.py` is unaffected: it binds `resolve_runtime_dir` at
import, before the patch, so it still exercises the real function.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent


@pytest.fixture(scope="session", autouse=True)
def _isolate_runtime_data(tmp_path_factory):
    """Point the database and the runtime root at a per-session tmp dir."""
    import hescope.paths as paths_mod

    session_root = tmp_path_factory.mktemp("hescope_session")
    runtime_root = session_root / "runtime"
    (runtime_root / "assets").mkdir(parents=True, exist_ok=True)

    # Reuse the checked-in demo slide instead of re-synthesising it (~15 s)
    # the first time a notebook cell calls ensure_demo_slide().
    cached_demo = _REPO_ROOT / "assets" / "demo_he.png"
    if cached_demo.is_file():
        shutil.copy2(cached_demo, runtime_root / "assets" / "demo_he.png")

    def _session_runtime_dir(app_dir):  # same signature; app_dir is ignored
        runtime_root.mkdir(parents=True, exist_ok=True)
        return runtime_root

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HESCOPE_DB_URL", f"sqlite:///{session_root / 'hescope.db'}")
        mp.setattr(paths_mod, "resolve_runtime_dir", _session_runtime_dir)
        yield session_root
