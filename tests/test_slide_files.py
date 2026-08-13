"""Tests for hescope.store.db's slide_files table (BUILD-PLAN-DB.md Phase 1):
one slide, many locations -- defect 1.1's fix.

Offline; tmp sqlite files and tmp images. Never touches data/hescope.db (R-1).
"""

from __future__ import annotations

import sqlalchemy as sa

from hescope.store.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.core.rois import ROI


def _rect() -> ROI:
    return ROI(kind="rect", points=((10.0, 10.0), (20.0, 20.0)))


def _engine(tmp_path):
    eng = get_engine(f"sqlite:///{tmp_path}/slide_files.db")
    init_db(eng)
    return eng


def test_one_file_at_three_paths_is_one_slide_with_three_slide_files_rows(tmp_path):
    """The same content registered from three DIFFERENT path strings (three
    separate real files with identical bytes -- mimicking three mount points
    of one network share) must be ONE slide row with THREE slide_files rows,
    not three slide rows (defect 1.1)."""
    engine = _engine(tmp_path)
    repo = SlideRepo(engine)

    payload = b"identical whole-slide bytes" * 500
    paths = []
    for name in ("mount_a/x.svs", "mount_b/x.svs", "backup/x.svs"):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)
        paths.append(str(p.resolve()))

    ids = [
        repo.register(
            source_kind="local", name="x.svs", path=p, width=100, height=100
        )
        for p in paths
    ]

    assert len(set(ids)) == 1, f"one file at 3 paths became {len(set(ids))} slide(s)"
    slide_id = ids[0]
    assert len(repo.list()) == 1

    files = repo.files_for(slide_id)
    assert len(files) == 3
    assert {f["path"] for f in files} == set(paths)
    assert all(f["source_kind"] == "local" for f in files)
    assert all(f["missing_since"] is None for f in files)  # all 3 files exist

    # `slides.path` (the cache) reflects the MOST RECENTLY registered path
    row = repo.get(slide_id)
    assert row["path"] == paths[-1]
    assert row["identity_scheme"] == "sha256"


def test_a_path_that_disappears_gets_missing_since_and_rois_stay_reachable(tmp_path):
    engine = _engine(tmp_path)
    repo = SlideRepo(engine)
    roi_repo = ROIRepo(engine)

    real_file = tmp_path / "vanishing.svs"
    real_file.write_bytes(b"here today")
    path = str(real_file.resolve())

    slide_id = repo.register(
        source_kind="local", name="vanishing.svs", path=path, width=8, height=8
    )
    identity = (repo.get(slide_id)["identity_scheme"], repo.get(slide_id)["identity_key"])
    roi_id = roi_repo.add(slide_id, _rect(), label="tumor")

    files = repo.files_for(slide_id)
    assert len(files) == 1 and files[0]["missing_since"] is None

    real_file.unlink()  # the file is gone; a later re-registration must
    # notice, using the identity the FIRST registration already computed
    # (a deleted file cannot be re-hashed) -- exactly how a caller that
    # persisted the identity (e.g. TCGA's stored slide_id) would notice.
    same_id = repo.register(
        source_kind="local", name="vanishing.svs", path=path, width=8, height=8,
        identity=identity,
    )
    assert same_id == slide_id, "the same identity must resolve to the same row"

    files_after = repo.files_for(slide_id)
    assert len(files_after) == 1  # still one location, not a new row
    assert files_after[0]["missing_since"] is not None

    # the ROI is unaffected: it hangs off slide_id, never off a path
    rois = roi_repo.for_slide(slide_id)
    assert len(rois) == 1 and rois[0]["id"] == roi_id and rois[0]["label"] == "tumor"


def test_re_registering_the_same_path_refreshes_last_seen_and_clears_missing(tmp_path):
    engine = _engine(tmp_path)
    repo = SlideRepo(engine)

    real_file = tmp_path / "comeback.svs"
    real_file.write_bytes(b"content")
    path = str(real_file.resolve())

    slide_id = repo.register(
        source_kind="local", name="comeback.svs", path=path, width=8, height=8
    )
    identity = (repo.get(slide_id)["identity_scheme"], repo.get(slide_id)["identity_key"])
    first_seen_before = repo.files_for(slide_id)[0]["first_seen_at"]

    real_file.unlink()
    repo.register(
        source_kind="local", name="comeback.svs", path=path, width=8, height=8,
        identity=identity,
    )
    missing = repo.files_for(slide_id)[0]
    assert missing["missing_since"] is not None
    assert missing["first_seen_at"] == first_seen_before, (
        "re-registering must never rewrite first_seen_at (R-3)"
    )

    real_file.write_bytes(b"content")  # the file comes back
    repo.register(
        source_kind="local", name="comeback.svs", path=path, width=8, height=8,
        identity=identity,
    )
    recovered = repo.files_for(slide_id)[0]
    assert recovered["missing_since"] is None
    assert recovered["first_seen_at"] == first_seen_before


def test_slide_files_cascade_deletes_with_the_slide(tmp_path):
    engine = _engine(tmp_path)
    repo = SlideRepo(engine)
    real_file = tmp_path / "s.svs"
    real_file.write_bytes(b"content")
    slide_id = repo.register(
        source_kind="local", name="s.svs", path=str(real_file.resolve()),
        width=8, height=8,
    )
    assert len(repo.files_for(slide_id)) == 1

    repo.delete(slide_id)

    with sa.orm.Session(engine) as s:
        from hescope.store.db import SlideFile
        remaining = s.execute(
            sa.select(sa.func.count()).select_from(SlideFile)
        ).scalar_one()
    assert remaining == 0
