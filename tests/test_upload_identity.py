"""R08-1: uploading one file twice must open one slide, not two.

A slide's identity is its path — ``SlideRepo.register`` is idempotent on
``UNIQUE(path)``. The upload handler used to write into
``tempfile.mkdtemp(prefix="hescope_upload_")``, which returns a **fresh
directory on every call**, so the second upload of ``biopsy.svs`` became a
second, unrelated slide whose panel reported no annotations — and whose row
pointed into an OS-managed temp directory free to be swept, leaving the row
naming a file that no longer exists.

The live database shows what that fate looks like: 26 of 31 rows sit on temp
paths and 18 no longer resolve. Those came from tests and probes, not from this
handler (``source_kind='upload'`` has zero rows — nobody has pressed the
button), so the defect was latent. These tests make it stay fixed.

Driven through app.py's own loader cell.
"""

from __future__ import annotations

import pathlib

import pytest


class _Uploaded:
    """The shape ``mo.ui.file`` hands its on_change handler."""

    def __init__(self, name: str, contents: bytes):
        self.name = name
        self.contents = contents


def _run_loader(tmp_path):
    """Exec app.py's loader cell; returns (on_upload, opened_paths, messages)."""
    import app as appmod

    appmod.app._maybe_initialize()
    cell = appmod.app._graph.cells["SFPL"]

    opened: list[str] = []
    messages: list = []
    handlers: dict = {}

    class _Source:
        dimensions = (100, 80)
        mpp = None
        level_downsamples = [1.0]
        level_count = 1
        name = "x"

        def get_thumbnail(self, size):
            raise AssertionError("not needed")

    class _UI:
        @staticmethod
        def text(**kw):
            return object()

        @staticmethod
        def button(**kw):
            return object()

        @staticmethod
        def file(**kw):
            handlers["upload"] = kw["on_change"]
            return object()

    class _MO:
        ui = _UI()

        @staticmethod
        def md(*a, **kw):
            return object()

        @staticmethod
        def callout(*a, **kw):
            return object()

        @staticmethod
        def vstack(*a, **kw):
            return object()

        @staticmethod
        def hstack(*a, **kw):
            return object()

    class _DB:
        enabled = False
        error = None

    def _open_slide(path):
        opened.append(str(path))
        return _Source()

    ns: dict = {
        "OSD_AVAILABLE": False,
        "Path": pathlib.Path,
        "SlideRefs": lambda **kw: object(),
        "UPLOAD_DIR": tmp_path / "uploads",
        "ViewportState": lambda **kw: object(),
        "db": _DB(),
        "ensure_demo_slide": lambda: tmp_path / "demo.png",
        "mo": _MO(),
        "open_slide": _open_slide,
        "os": __import__("os"),
        "serve_slide": lambda *a, **kw: None,
        "set_analysis_msg": lambda v: None,
        "set_analysis_result": lambda v: None,
        "set_db_msg": messages.append,
        "set_hm_result": lambda v: None,
        "set_measure_msg": lambda v: None,
        "set_payload": lambda v: None,
        "set_rois": lambda v: None,
        "set_slide_id": lambda v: None,
        "set_source": lambda v: None,
        "set_tiles": lambda v: None,
        "set_vp": lambda v: None,
        "viewer_bus": {},
    }
    exec(cell.body, ns)
    assert "upload" in handlers, "the loader cell no longer builds a file input"
    # The cell auto-opens the demo slide on first run; that is the feature under
    # a different test. Clear it so `opened` records only what the click did.
    opened.clear()
    messages.clear()
    return handlers["upload"], opened, messages


PNG = b"\x89PNG\r\n\x1a\n" + b"slide-bytes" * 40


def test_uploading_the_same_file_twice_opens_one_slide(tmp_path):
    on_upload, opened, _msgs = _run_loader(tmp_path)

    on_upload([_Uploaded("biopsy.svs", PNG)])
    on_upload([_Uploaded("biopsy.svs", PNG)])

    assert len(opened) == 2, "both uploads should have opened something"
    assert opened[0] == opened[1], (
        "the same bytes produced two different paths, so SlideRepo.register's "
        "UNIQUE(path) makes them two unrelated slides and the annotations on "
        f"the first are invisible from the second: {opened}"
    )


def test_the_upload_lands_in_the_stable_directory_not_a_temp_dir(tmp_path):
    on_upload, opened, _msgs = _run_loader(tmp_path)
    on_upload([_Uploaded("biopsy.svs", PNG)])

    dest = pathlib.Path(opened[0])
    assert dest.parent == tmp_path / "uploads", (
        f"upload written outside the managed directory: {dest}"
    )
    assert dest.exists() and dest.read_bytes() == PNG
    assert "mkdtemp" not in str(dest) and "hescope_upload_" not in str(dest)


def test_different_content_under_one_name_stays_two_slides(tmp_path):
    """Two different biopsies both called `slide.svs` must not collide."""
    on_upload, opened, _msgs = _run_loader(tmp_path)

    on_upload([_Uploaded("slide.svs", PNG)])
    on_upload([_Uploaded("slide.svs", PNG + b"different")])

    assert opened[0] != opened[1], "two different slides were merged into one"
    assert pathlib.Path(opened[0]).name.endswith("slide.svs")


def test_the_original_filename_is_still_recognisable(tmp_path):
    on_upload, opened, _msgs = _run_loader(tmp_path)
    on_upload([_Uploaded("TCGA-BH-A18H.svs", PNG)])
    assert pathlib.Path(opened[0]).name.endswith("TCGA-BH-A18H.svs"), (
        "the user has to be able to recognise their file in the slide list"
    )


def test_a_directory_traversal_name_cannot_escape_the_upload_directory(tmp_path):
    on_upload, opened, _msgs = _run_loader(tmp_path)
    on_upload([_Uploaded("../../evil.svs", PNG)])
    if not opened:
        return  # refused outright is also acceptable
    assert pathlib.Path(opened[0]).parent == tmp_path / "uploads"


def test_a_failed_upload_reports_instead_of_doing_nothing(tmp_path):
    on_upload, opened, messages = _run_loader(tmp_path)

    class _Boom:
        name = "x.svs"

        @property
        def contents(self):
            raise OSError("read failed")

    on_upload([_Boom()])
    assert not opened
    assert messages and messages[-1][0] == "danger", (
        "a failed upload changed nothing on screen (R07-5's class)"
    )


@pytest.mark.parametrize("files", [[], None])
def test_an_empty_upload_is_a_no_op(tmp_path, files):
    on_upload, opened, messages = _run_loader(tmp_path)
    on_upload(files)
    assert not opened and not messages
