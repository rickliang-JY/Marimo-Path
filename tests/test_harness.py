"""The four harness locks (design doc §15.3,
``docs/HE-Scope-设计文档-数据与Harness.md``).

Harness rules that live only in prose are, per §8.2, "散文" -- they do not
fail. These four locks are what turns AGENTS.md / SKILL.md / app.py's
partition line / the dependency floor from documentation into something
that goes red on its own:

  Lock 1  API contract   -- every entry point AGENTS.md/SKILL.md document
                             must really exist and really resolve.
  Lock 2  Skeleton        -- the skeleton region of app.py cannot silently
                             change shape (``skeleton.lock``).
  Lock 3  Granularity     -- cell count / longest-cell-length may not get
                             worse than the recorded baseline.
  Lock 4  Dependency      -- core keeps the numpy<2.6 headroom §3.3/§18.7
                             calls out, and never gains a banned adapter-
                             only dependency (VALIS, spatialdata, ...).

Every lock below is followed by a "does it actually fire" test (task rule:
"锁本身也要有测试:故意破坏一次,确认锁真的会红") that feeds the checking
function a deliberately broken input and asserts it flags the break --
without touching the real files on disk, since corrupting AGENTS.md/app.py
just to prove a point would leave the repo in exactly the broken state the
lock exists to prevent.

Lock 1 additionally carries a REAL regression it caught while this file was
being written (not a synthetic example): AGENTS.md §9's "Direct imports
also work" paragraph and SKILL.md §5 both still cited the flat
``hescope.nuclei`` / ``hescope.qc`` / ``hescope.stain`` / ``hescope.features``
/ ``hescope.grid`` / ``hescope.heatmap`` / ``hescope.ml`` / ``hescope.geojson``
/ ``hescope.db`` import paths from before e18c304 ("Split hescope/ into
eight subpackages by function") moved them under ``hescope.analysis.*`` /
``hescope.interop.*`` / ``hescope.store.*``. Watched fail before the fix:

    >>> import hescope.nuclei
    ModuleNotFoundError: No module named 'hescope.nuclei'
    >>> import hescope.geojson
    ModuleNotFoundError: No module named 'hescope.geojson'

Both docs were corrected (to ``hescope.analysis.nuclei``, ``hescope.store.db``,
etc.) as part of adding this lock; see ``test_flat_module_paths_from_before_
the_subpackage_split_stay_dead`` below for the regression pin.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness_support as hs  # noqa: E402

REPO_ROOT = hs.REPO_ROOT
APP_PY = REPO_ROOT / "app.py"
AGENTS_MD = hs.AGENTS_MD
SKILL_MD = hs.SKILL_MD
SKELETON_LOCK = REPO_ROOT / "skeleton.lock"

# Baseline recorded 2026-08-20 by running, from the repo root:
#   .venv/Scripts/python.exe -c "
#   import sys; sys.path.insert(0, 'tests'); import _harness_support as hs
#   cells = hs.skeleton_cells()
#   print(len(cells), max(c.n_lines for c in cells))"
# -> 51 292
# (51 = the pre-existing 50 cells + the new scratch-marker cell this same
# change adds; 292 = the pre-existing longest cell, unchanged by this
# change). Ratchet DOWN as §9.2's 2,404-line extraction happens; never
# raise these without also re-running the command above to confirm why.
BASELINE_CELL_COUNT = 51
BASELINE_MAX_CELL_LINES = 292


# ============================================================================
# Lock 1 -- API contract: AGENTS.md / SKILL.md vs the live app
# ============================================================================


def test_documented_kernel_globals_exist():
    """§15.3 lock one, the literal pseudocode: every name AGENTS.md §3's
    "Entry points" table and SKILL.md §2's "Tool list" table claim is a
    kernel global must actually be bound at module scope somewhere in
    ``app.py`` once the notebook runs."""
    claimed = hs.parse_documented_kernel_globals(AGENTS_MD, SKILL_MD)
    assert claimed, "parser found zero claimed entry points -- table format probably changed"
    live = hs.module_scope_names(APP_PY)
    missing = hs.undocumented_or_missing(claimed, live)
    assert not missing, (
        f"AGENTS.md/SKILL.md claim these are kernel globals but app.py never "
        f"binds them at module scope: {sorted(missing)}"
    )


def test_documented_kernel_globals_lock_fires_on_a_fake_entry_point():
    """Break it on purpose: a name no cell in app.py defines must be caught."""
    claimed = hs.parse_documented_kernel_globals(AGENTS_MD, SKILL_MD)
    live = hs.module_scope_names(APP_PY)
    corrupted = claimed | {"totally_fake_tool_xyz_not_in_app_py"}
    missing = hs.undocumented_or_missing(corrupted, live)
    assert missing == {"totally_fake_tool_xyz_not_in_app_py"}, (
        "the lock did not fire on a fabricated entry point"
    )


def test_documented_dotted_import_paths_resolve():
    """Every ``hescope.a.b.c`` path mentioned in AGENTS.md or SKILL.md must
    actually import/resolve. This is the check that caught the nine stale
    paths described in this module's docstring."""
    paths = hs.documented_dotted_paths(AGENTS_MD, SKILL_MD)
    assert len(paths) >= 15, f"parser found suspiciously few dotted paths: {paths}"
    broken = {}
    for p in sorted(paths):
        try:
            hs.resolve_dotted(p)
        except Exception as exc:  # noqa: BLE001 -- collecting ALL failures, not just the first
            broken[p] = f"{type(exc).__name__}: {exc}"
    assert not broken, f"documented import paths do not resolve: {broken}"


def test_documented_dotted_import_paths_lock_fires_on_a_fake_path():
    """Break it on purpose: a module that was never real must be caught.
    (Either exception is a legitimate "does not resolve" signal:
    ModuleNotFoundError when no prefix imports at all, AttributeError when
    a real prefix -- here bare ``hescope`` -- imports but lacks the rest of
    the chain; ``test_documented_dotted_import_paths_resolve`` above treats
    both as failures via a bare ``except Exception``.)"""
    with pytest.raises((ModuleNotFoundError, AttributeError)):
        hs.resolve_dotted("hescope.totally_fake_module_xyz.not_a_real_thing")


STALE_FLAT_MODULE_PATHS = (
    "hescope.nuclei",
    "hescope.qc",
    "hescope.stain",
    "hescope.features",
    "hescope.grid",
    "hescope.heatmap",
    "hescope.ml",
    "hescope.geojson",
    "hescope.db",
)


def test_flat_module_paths_from_before_the_subpackage_split_stay_dead():
    """Regression pin for the real bug this lock caught (see module
    docstring): these nine flat-module paths existed before e18c304 split
    ``hescope/`` into subpackages, and must never quietly become importable
    again in a way that lets stale documentation drift back to "true"."""
    for stale in STALE_FLAT_MODULE_PATHS:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(stale)


def test_docs_no_longer_cite_the_stale_flat_module_paths():
    """The other half of the regression pin: AGENTS.md/SKILL.md must not
    have drifted back to citing a path from ``STALE_FLAT_MODULE_PATHS``."""
    text = AGENTS_MD.read_text(encoding="utf-8") + "\n" + SKILL_MD.read_text(encoding="utf-8")
    mentioned = hs.find_hescope_dotted_paths(text)
    stale_mentions = {
        p
        for p in mentioned
        if any(p == s or p.startswith(s + ".") for s in STALE_FLAT_MODULE_PATHS)
    }
    assert not stale_mentions, f"docs cite dead flat-module paths again: {stale_mentions}"


# ============================================================================
# Lock 2 -- skeleton lock: app.py's skeleton region cannot silently change
# ============================================================================


def test_scratch_marker_exists_exactly_once():
    """The partition line (design doc §9.3, AGENTS.md "Partitioning") must
    exist, and exist exactly once, or "skeleton" / "scratch" is ambiguous."""
    lines = APP_PY.read_text(encoding="utf-8").splitlines()
    hits = [i for i, line in enumerate(lines) if hs.SCRATCH_MARKER in line]
    assert len(hits) == 1, f"expected exactly one scratch-marker line, found {len(hits)}: {hits}"


def test_skeleton_unchanged():
    """§15.3 lock two, the literal pseudocode: the skeleton region's
    structure must match ``skeleton.lock`` byte-for-byte. A human changing
    the skeleton on purpose regenerates the lock file as an explicit,
    reviewable action (see the file's own ``note`` field); an agent (or an
    accidental edit) changing it makes this test fail instead."""
    current = hs.skeleton_hashes(APP_PY)
    locked = hs.load_lock(SKELETON_LOCK)
    assert current["cell_count"] == locked["cell_count"] and current["hashes"] == locked["hashes"], (
        hs.diff_lock(current, locked)
    )


def test_skeleton_lock_fires_on_a_skeleton_edit(tmp_path):
    """Break it on purpose: copy app.py, mutate one skeleton cell's body,
    and confirm the resulting hash set differs from the checked-in one --
    i.e. the exact failure a real out-of-band skeleton edit would produce."""
    original_text = APP_PY.read_text(encoding="utf-8")
    lines = original_text.splitlines()
    marker_idx = hs.find_marker_line(lines)
    assert marker_idx is not None

    # Mutate the marker cell itself (guaranteed to be a skeleton cell, and
    # touching it does not require knowing any other cell's exact shape).
    lines[marker_idx] += "  # agent edit that must never pass silently"
    mutated = tmp_path / "app.py"
    mutated.write_text("\n".join(lines) + "\n", encoding="utf-8")

    real_hashes = hs.skeleton_hashes(APP_PY)
    mutated_hashes = hs.skeleton_hashes(mutated)
    assert mutated_hashes["cell_count"] == real_hashes["cell_count"]
    assert mutated_hashes["hashes"] != real_hashes["hashes"], (
        "mutating a skeleton cell's source did not change its hash -- the lock has no teeth"
    )
    locked = hs.load_lock(SKELETON_LOCK)
    assert mutated_hashes["hashes"] != locked["hashes"], (
        "the mutated copy still matches skeleton.lock -- test_skeleton_unchanged would not fire"
    )


# ============================================================================
# Lock 3 -- granularity lock: cell count / longest-cell-length ceiling
# ============================================================================


def test_cell_granularity_within_recorded_baseline():
    """§15.3 lock three. Scratch cells are exempt by construction
    (``skeleton_cells`` only returns cells at/above the marker; design doc
    §9.3/§10.2: scratch "不计入 cell 预算")."""
    cells = hs.skeleton_cells(APP_PY)
    violations = hs.granularity_violations(
        cells, max_count=BASELINE_CELL_COUNT, max_lines=BASELINE_MAX_CELL_LINES
    )
    assert not violations, f"cell granularity regressed past the recorded baseline: {violations}"


def test_granularity_lock_fires_on_a_synthetic_regression():
    """Break it on purpose, twice: a cell longer than the ceiling, and one
    cell too many -- both must be flagged, with the observed/ceiling pair
    a human can read straight out of the assertion."""
    one_cell_too_long = [
        hs.Cell(index=0, start_line=0, lines=tuple(f"line {i}" for i in range(BASELINE_MAX_CELL_LINES + 1)))
    ]
    v1 = hs.granularity_violations(
        one_cell_too_long, max_count=BASELINE_CELL_COUNT, max_lines=BASELINE_MAX_CELL_LINES
    )
    assert v1.get("max_cell_lines") == (BASELINE_MAX_CELL_LINES + 1, BASELINE_MAX_CELL_LINES)

    one_cell_too_many = [
        hs.Cell(index=i, start_line=i, lines=("pass",)) for i in range(BASELINE_CELL_COUNT + 1)
    ]
    v2 = hs.granularity_violations(
        one_cell_too_many, max_count=BASELINE_CELL_COUNT, max_lines=BASELINE_MAX_CELL_LINES
    )
    assert v2.get("cell_count") == (BASELINE_CELL_COUNT + 1, BASELINE_CELL_COUNT)


# ============================================================================
# Lock 4 -- dependency lock: the numpy<2.6 headroom (§3.3 / §18.7)
# ============================================================================


def test_installed_numpy_keeps_numba_headroom():
    """The environment actually running this suite right now must not have
    drifted onto numpy 1.x (a VALIS-style downgrade) or past 2.6 (the
    ceiling numba==0.67.0 declares -- see docs/DESIGN-DOC-DELTA.md §4.2)."""
    version = hs.installed_version_tuple("numpy")
    assert hs.numpy_headroom_ok(version), (
        f"installed numpy {version} has lost the numba<2.6 headroom "
        f"(§3.3/§18.7) -- either a major-version downgrade or past the ceiling"
    )


def test_core_pyproject_excludes_banned_adapter_packages():
    """§17's decision table: valis-wsi/spatialdata/scanpy/umap-learn must
    never be a declared dependency of this package, core or any optional
    extra -- that IS the import-graph integration §3 rejects."""
    specs = hs.all_declared_dependency_specs()
    banned = hs.find_banned_packages(specs)
    assert not banned, f"a banned adapter-only package is declared in pyproject.toml: {banned}"


def test_core_pyproject_numpy_floor_leaves_numba_headroom():
    """Defensive/declarative half of the same check: even before anything
    is installed, pyproject.toml's own numpy specifier must not raise the
    floor to or past 2.6."""
    specs = hs.all_declared_dependency_specs()
    floor = hs.numpy_declared_floor(specs)
    if floor is not None:
        assert floor < (2, 6), f"pyproject.toml pins numpy>={floor[0]}.{floor[1]}, past the numba ceiling"


def test_dependency_lock_fires_on_a_banned_package():
    """Break it on purpose: a pyproject.toml that declared valis-wsi must
    be caught, whatever version spec it carries."""
    banned = hs.find_banned_packages(["numpy", "pillow", "valis-wsi>=2.0.1"])
    assert banned == {"valis-wsi"}


def test_dependency_lock_fires_on_a_numpy_floor_past_the_ceiling():
    """Break it on purpose: a pyproject.toml that raised numpy's floor to
    2.6 must be caught."""
    floor = hs.numpy_declared_floor(["numpy>=2.6", "scipy"])
    assert floor == (2, 6)
    assert not (floor < (2, 6))


def test_dependency_lock_fires_on_the_exact_valis_regression():
    """The specific, measured regression §3.2/DESIGN-DOC-DELTA §4.2 pins:
    valis-wsi forces numpy 2.5.2 -> 1.26.4. That exact downgraded version
    must fail the headroom check."""
    assert not hs.numpy_headroom_ok((1, 26, 4))


def test_dependency_lock_fires_on_crossing_the_numba_ceiling():
    """numba==0.67.0 declares ``numpy<2.6,>=1.22`` on PyPI (verified live
    in DESIGN-DOC-DELTA.md §4.2); numpy 2.6.0 itself must fail the check."""
    assert not hs.numpy_headroom_ok((2, 6, 0))
