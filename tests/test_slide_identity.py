"""Tests for hescope.identity (Phase 1: the SVS <-> ROI relationship).

Offline; tmp files only. Never touches data/hescope.db (R-1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hescope.identity import CONTENT_KEY_HEAD, content_key, slide_identity


# --- content_key -------------------------------------------------------


def test_content_key_is_stable_for_the_same_bytes(tmp_path):
    a = tmp_path / "a.svs"
    b = tmp_path / "b.svs"  # different path, SAME bytes
    payload = b"whole-slide-image-bytes" * 1000
    a.write_bytes(payload)
    b.write_bytes(payload)

    key_a = content_key(a)
    key_b = content_key(b)

    assert key_a is not None and key_b is not None
    assert key_a == key_b, "identical content at different paths must hash identically"
    digest_a, size_a = key_a
    assert size_a == len(payload)
    assert isinstance(digest_a, str) and len(digest_a) == 64  # sha256 hex


def test_content_key_changes_when_one_byte_changes(tmp_path):
    payload = bytearray(b"whole-slide-image-bytes" * 1000)
    a = tmp_path / "a.svs"
    a.write_bytes(bytes(payload))
    key_before = content_key(a)

    payload[len(payload) // 2] ^= 0xFF  # flip one byte in the middle
    a.write_bytes(bytes(payload))
    key_after = content_key(a)

    assert key_before is not None and key_after is not None
    assert key_before != key_after
    # size is unchanged, so the digest itself is what had to change
    assert key_before[1] == key_after[1]
    assert key_before[0] != key_after[0]


def test_content_key_changes_when_a_byte_beyond_the_head_window_changes(tmp_path):
    """The head/tail sampling (not a whole-file hash) must still catch a
    change past CONTENT_KEY_HEAD -- because the function also reads the
    TAIL of a file larger than the window."""
    size = CONTENT_KEY_HEAD * 3
    payload = bytearray(b"\x00" * size)
    p = tmp_path / "big.svs"
    p.write_bytes(bytes(payload))
    before = content_key(p)

    payload[-1] ^= 0xFF  # last byte: inside the TAIL window, not the head
    p.write_bytes(bytes(payload))
    after = content_key(p)

    assert before is not None and after is not None
    assert before != after


def test_content_key_none_for_a_missing_path(tmp_path):
    assert content_key(tmp_path / "does-not-exist.svs") is None


def test_content_key_none_for_a_directory(tmp_path):
    d = tmp_path / "a_directory"
    d.mkdir()
    assert content_key(d) is None


# --- slide_identity ------------------------------------------------------


def test_slide_identity_from_a_readable_path_is_sha256(tmp_path):
    p = tmp_path / "s.svs"
    p.write_bytes(b"content")
    scheme, key = slide_identity("local", path=p)
    assert scheme == "sha256"
    assert key == content_key(p)[0]


def test_slide_identity_returns_none_for_an_unreadable_path_the_path_fallback(
    tmp_path,
):
    """slide_identity() never manufactures a ('path', ...) identity itself --
    that fallback belongs to the caller (hescope.db.SlideRepo.register,
    which also needs normalize_slide_path -- see this module's docstring for
    why identity.py cannot depend on hescope.db). This is the boundary that
    fallback sits on: a path that cannot be hashed resolves to None here.
    """
    missing = tmp_path / "gone.svs"
    assert slide_identity("local", path=missing) is None
    assert slide_identity("local", path=None) is None


def test_slide_identity_dicomweb_round_trips_without_a_file():
    scheme, key = slide_identity(
        "dicomweb", study_uid="1.2.840.99999", series_uid="1.2.840.11111"
    )
    assert scheme == "dicomweb"
    assert key == "1.2.840.99999/1.2.840.11111"


def test_slide_identity_omero_round_trips_without_a_file():
    scheme, key = slide_identity("omero", image_id=4242)
    assert scheme == "omero"
    assert key == "4242"


def test_slide_identity_omero_image_id_zero_is_not_falsy(tmp_path):
    """image_id=0 is a legitimate OMERO id -- `is not None`, not truthiness,
    must gate this branch."""
    scheme, key = slide_identity("omero", image_id=0)
    assert scheme == "omero"
    assert key == "0"


@pytest.mark.parametrize("missing_kw", ["study_uid", "series_uid"])
def test_slide_identity_dicomweb_needs_both_uids(missing_kw, tmp_path):
    kw = {"study_uid": "1.2.3", "series_uid": "4.5.6"}
    kw[missing_kw] = ""  # falsy: incomplete pair must not produce a partial identity
    assert slide_identity("local", path=None, **kw) is None


def test_slide_identity_dicomweb_wins_over_a_path_supplied_alongside_it(tmp_path):
    """A caller that (mistakenly or not) supplies both a DICOMweb pair and a
    local path must get the DICOMweb identity -- a cached local copy of a
    DICOMweb slide must not silently become a path-hashed local slide."""
    p = tmp_path / "cached-copy.svs"
    p.write_bytes(b"content")
    scheme, key = slide_identity(
        "dicomweb", path=p, study_uid="1.2.3", series_uid="4.5.6"
    )
    assert (scheme, key) == ("dicomweb", "1.2.3/4.5.6")
