"""DICOM WSI backend.

Scope of what these prove, stated plainly: the adapter logic -- coordinate
conversion, clipping, padding, mpp, detection -- is pinned against a stub that
mimics wsidicom's API. **No real DICOM whole-slide image has been read**; there
is none in the repo and generating a conforming VL Whole Slide Microscopy
Image is a project of its own. The trap this backend exists to avoid is a
coordinate-space mismatch, which is exactly what a stub CAN pin, so the tests
are aimed there.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest
from PIL import Image

from hescope.wsi.dicom_source import DicomSource, is_dicom_slide


# --- detection -------------------------------------------------------------


def _write_dicom_magic(path: pathlib.Path) -> pathlib.Path:
    """A file carrying the DICM preamble magic at offset 128."""
    path.write_bytes(b"\x00" * 128 + b"DICM" + b"\x00" * 32)
    return path


def test_detects_dicom_by_content_not_extension(tmp_path):
    """Extensionless DICOM is common; judging by suffix alone misses it."""
    assert is_dicom_slide(_write_dicom_magic(tmp_path / "no_extension"))
    assert is_dicom_slide(_write_dicom_magic(tmp_path / "slide.dcm"))
    # a .dcm suffix is accepted on its own, since some writers omit the preamble
    (tmp_path / "bare.dcm").write_bytes(b"not really")
    assert is_dicom_slide(tmp_path / "bare.dcm")


def test_rejects_non_dicom(tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
    assert not is_dicom_slide(tmp_path / "a.png")
    assert not is_dicom_slide(tmp_path / "does-not-exist")
    (tmp_path / "short").write_bytes(b"tiny")
    assert not is_dicom_slide(tmp_path / "short")


def test_a_directory_of_instances_counts(tmp_path):
    """A DICOM WSI is normally a directory: one instance per level, plus
    label and overview."""
    study = tmp_path / "study"
    study.mkdir()
    assert not is_dicom_slide(study)
    _write_dicom_magic(study / "level0")
    assert is_dicom_slide(study)


# --- the adapter, against a stub of wsidicom's API -------------------------


class _Size:
    def __init__(self, w, h):
        self.width, self.height = w, h


class _Level:
    def __init__(self, w, h):
        self.size = _Size(w, h)
        self.pixel_spacing = None


class _StubWsiDicom:
    """Mimics the parts of wsidicom.WsiDicom the adapter touches, and records
    every read so the coordinate space can be asserted."""

    def __init__(self, w=4000, h=2000, n_levels=3, mpp=None):
        self.levels = [_Level(w >> i, h >> i) for i in range(n_levels)]
        self.mpp = _Size(mpp, mpp) if mpp else None
        self.calls: list[tuple] = []
        self.closed = False

    def read_region(self, location, level, size):
        self.calls.append((tuple(location), level, tuple(size)))
        arr = np.full((size[1], size[0], 3), 128, dtype=np.uint8)
        return Image.fromarray(arr, "RGB")

    def read_thumbnail(self, size):
        return Image.new("RGB", size, (200, 200, 200))

    def close(self):
        self.closed = True


@pytest.fixture()
def source(monkeypatch, tmp_path):
    stub = _StubWsiDicom()
    monkeypatch.setattr("hescope.wsi.dicom_source._HAS_WSIDICOM", True)
    monkeypatch.setattr(
        "hescope.wsi.dicom_source.WsiDicom",
        type("W", (), {"open": staticmethod(lambda p: stub)}),
    )
    src = DicomSource(_write_dicom_magic(tmp_path / "s.dcm"))
    src._stub = stub  # type: ignore[attr-defined]
    return src


def test_pyramid_metadata(source):
    assert source.dimensions == (4000, 2000)
    assert source.level_count == 3
    assert source.level_downsamples == (1.0, 2.0, 4.0)


def test_location_is_converted_from_level0_to_level_coordinates(source):
    """THE TRAP THIS BACKEND EXISTS FOR.

    Our contract puts `location` in level-0 pixels, as OpenSlide does.
    wsidicom scales the region UP from the level asked for, so its location is
    in THAT level's pixels. Forwarding ours unchanged reads the right-sized
    region from the wrong place at every level but 0, with the error growing
    with the downsample.
    """
    source.read_region((800, 400), 2, (100, 50))  # level 2 => downsample 4
    location, level, size = source._stub.calls[-1]
    assert location == (200, 100), "level-0 location was not divided by the downsample"
    assert level == 2 and size == (100, 50)

    source._stub.calls.clear()
    source.read_region((800, 400), 0, (10, 10))  # level 0 => identity
    assert source._stub.calls[-1][0] == (800, 400)


def test_region_is_clipped_and_padded_white_at_the_edge(source):
    """wsidicom raises on an out-of-bounds region; the viewport asks for one
    every time the user pans to the edge, and every other backend pads."""
    img = source.read_region((3900, 1900), 0, (200, 200))
    assert img.size == (200, 200)
    arr = np.asarray(img)
    assert (arr[:, -50:] == 255).all(), "outside the slide must be WHITE, not black"
    # only the in-slide part was actually requested
    location, _level, size = source._stub.calls[-1]
    assert location == (3900, 1900) and size == (100, 100)


def test_a_region_entirely_outside_returns_white_without_reading(source):
    source._stub.calls.clear()
    img = source.read_region((99999, 99999), 0, (32, 32))
    assert img.size == (32, 32)
    assert (np.asarray(img) == 255).all()
    assert source._stub.calls == [], "should not have asked wsidicom at all"


def test_a_failing_read_degrades_to_white_rather_than_breaking_the_viewport(source):
    def boom(*a, **k):
        raise RuntimeError("corrupt instance")

    source._stub.read_region = boom
    img = source.read_region((0, 0), 0, (16, 16))
    assert img.size == (16, 16) and (np.asarray(img) == 255).all()


def test_level_index_is_clamped(source):
    source.read_region((0, 0), 99, (8, 8))
    assert source._stub.calls[-1][1] == 2  # highest level that exists


def test_mpp_is_read_when_present(monkeypatch, tmp_path):
    stub = _StubWsiDicom(mpp=0.25)
    monkeypatch.setattr("hescope.wsi.dicom_source._HAS_WSIDICOM", True)
    monkeypatch.setattr(
        "hescope.wsi.dicom_source.WsiDicom",
        type("W", (), {"open": staticmethod(lambda p: stub)}),
    )
    assert DicomSource(_write_dicom_magic(tmp_path / "s.dcm")).mpp == pytest.approx(0.25)


def test_missing_mpp_is_none_not_zero(source):
    assert source.mpp is None


def test_thumbnail_and_close(source):
    assert source.get_thumbnail((64, 64)).size == (64, 64)
    source.close()
    assert source._stub.closed
    source.close()  # idempotent


def test_it_satisfies_the_slide_protocol(source):
    from hescope.wsi.slides import SlideSource

    assert isinstance(source, SlideSource)


def test_a_clear_error_when_wsidicom_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr("hescope.wsi.dicom_source._HAS_WSIDICOM", False)
    with pytest.raises(RuntimeError, match=r"\[dicom\]"):
        DicomSource(_write_dicom_magic(tmp_path / "s.dcm"))
