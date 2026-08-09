"""Tests for hescope.paths.resolve_runtime_dir."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hescope.paths import RUNTIME_DIR_NAME, resolve_runtime_dir


def _make_repo_layout(root: Path) -> Path:
    """Fake repo/editable layout: app.py next to a hescope/ package dir."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").touch()
    (root / "hescope").mkdir()
    return root


def test_repo_layout_returns_app_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # prove CWD is not used in this branch
    app_dir = _make_repo_layout(tmp_path / "repo")
    assert resolve_runtime_dir(app_dir) == app_dir


def test_repo_layout_dir_is_created_for_site_packages(tmp_path, monkeypatch):
    # site-packages layout: app.py present but NO hescope/ dir next to it.
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    (site_packages / "app.py").touch()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    resolved = resolve_runtime_dir(site_packages)
    assert resolved == work / RUNTIME_DIR_NAME
    assert resolved.is_dir()


def test_writable_site_packages_with_package_still_uses_cwd(tmp_path, monkeypatch):
    # Non-editable install shape: app.py AND hescope/ inside site-packages,
    # and the directory is writable (pip --user / venv). Must still NOT be
    # used for runtime data.
    site_packages = _make_repo_layout(tmp_path / "venv" / "lib" / "site-packages")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    resolved = resolve_runtime_dir(site_packages)
    assert resolved == work / RUNTIME_DIR_NAME
    assert resolved.is_dir()


@pytest.mark.skipif(
    not hasattr(os, "geteuid"),
    reason="POSIX permission bits; os.chmod cannot make a dir unwritable on Windows",
)
def test_readonly_repo_layout_falls_back_to_cwd(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission bits; probe write succeeds")
    app_dir = _make_repo_layout(tmp_path / "repo")
    os.chmod(app_dir, stat.S_IRUSR | stat.S_IXUSR)  # read+execute only
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    try:
        assert resolve_runtime_dir(app_dir) == work / RUNTIME_DIR_NAME
    finally:
        os.chmod(app_dir, stat.S_IRWXU)


def test_missing_app_dir_falls_back_to_cwd(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    resolved = resolve_runtime_dir(tmp_path / "does-not-exist")
    assert resolved == work / RUNTIME_DIR_NAME
    assert resolved.is_dir()


def test_existing_runtime_dir_is_reused(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    (work / RUNTIME_DIR_NAME).mkdir()
    assert resolve_runtime_dir(tmp_path / "nope") == work / RUNTIME_DIR_NAME


def test_accepts_str_app_dir(tmp_path):
    app_dir = _make_repo_layout(tmp_path / "repo")
    assert resolve_runtime_dir(str(app_dir)) == app_dir
