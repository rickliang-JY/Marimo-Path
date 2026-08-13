"""The test suite must not write into the developer's own runtime data.

The suite drives the real notebook and the real bootstrap, and both resolve
their storage the way a user's session does. With nothing redirecting them,
`pytest` wrote straight into `<repo>/data/hescope.db` and `<repo>/agent_out/`:
by the time R05-4 was filed the shipped database held eleven junk slide rows
(one per pytest tmpdir, never deduplicated because the tmp path is unique per
run), an ROI row and an `agent_runs` row from a run that really did drive the
toolbar's submit, and `agent_out/patches/` held six PNGs of that ROI plus a
line per submit in `roi_history.jsonl`.

`conftest.py` redirects both for the session. These tests assert the redirect
is in force, so the isolation cannot be dropped silently -- with the fixture
removed they name the developer's real paths and go red.
"""

from __future__ import annotations

from pathlib import Path

from hescope.store.db import DEFAULT_DB_URL, get_engine
from hescope.viewer.viewer import bootstrap_db

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_the_default_database_is_redirected_away_from_the_repo():
    """`get_engine()` with no argument is what `bootstrap_db()` -- and so
    `app.py` -- uses."""
    url = str(get_engine().url)
    assert url != DEFAULT_DB_URL, (
        "the suite is about to write into the developer's own database at "
        f"{DEFAULT_DB_URL}"
    )
    assert str(_REPO_ROOT) not in url


def test_bootstrapping_the_app_database_stays_out_of_the_repo():
    ctx = bootstrap_db()
    assert ctx.enabled
    assert str(_REPO_ROOT) not in str(ctx.engine.url)


def test_the_runtime_root_is_redirected_away_from_the_repo():
    """`resolve_runtime_dir` is where app.py gets OUT_DIR (agent_out/, which
    receives ROI patch PNGs and roi_history.jsonl), MODELS_DIR and
    TCGA_DATA_DIR. Imported inside the test on purpose: the module attribute
    is what app.py's cells look up, and it is what conftest patches."""
    from hescope.core.paths import resolve_runtime_dir

    runtime = resolve_runtime_dir(_REPO_ROOT)
    assert runtime != _REPO_ROOT, (
        "the suite is about to write patches and model files into the repo"
    )
    assert _REPO_ROOT not in runtime.resolve().parents


def test_the_notebooks_own_out_dir_is_inside_the_session_tmp_dir():
    """The end of the chain: what app.py actually computed, not what the
    helper returns when a test calls it."""
    import app as appmod

    _outputs, defs = appmod.app.run()
    out_dir = Path(defs["OUT_DIR"]).resolve()
    assert _REPO_ROOT not in out_dir.parents and out_dir != _REPO_ROOT / "agent_out", (
        f"the notebook writes ROI patches to {out_dir}"
    )
    assert str(defs["db"].engine.url).find(str(_REPO_ROOT)) == -1
