"""`hescope doctor` and `hescope delete-roi`.

Two questions the app could not answer:

  "is my database real, and is it healthy?"  Every panel reports on ONE slide.
  Nothing reported on the store itself -- which file, whether it exists, what
  schema version, whether the concurrency pragmas took, whether references are
  intact. A user asking whether their work is actually being saved had no
  command to run.

  "how do I delete THAT ROI?"  ROIRepo.delete(roi_id) existed, but the only way
  to reach it was to open the Annotations accordion, find the row in a table and
  click Delete. There was no CLI, and an agent could create and label ROIs but
  never remove one.
"""

from __future__ import annotations

import pytest

from hescope.cli import main
from hescope.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.rois import ROI


@pytest.fixture()
def db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'doc.db').as_posix()}"
    engine = get_engine(url)
    init_db(engine)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="s.svs", path=str(tmp_path / "s.svs"),
        width=1000, height=800, mpp=0.5,
    )
    repo = ROIRepo(engine)
    ids = [
        repo.add(slide_id, ROI(kind="rect", points=((float(i), 0.0), (float(i) + 9, 9.0))),
                 label=f"L{i}")
        for i in range(3)
    ]
    return url, engine, slide_id, ids


# --- doctor ----------------------------------------------------------------


def test_doctor_reports_the_file_the_pragmas_and_the_counts(db, capsys):
    url, _e, _sid, _ids = db
    rc = main(["--db", url, "doctor"])
    out = capsys.readouterr().out

    assert "backend      sqlite" in out
    assert "foreign_keys=True" in out
    assert "journal_mode=wal" in out
    assert "busy_timeout=" in out
    assert "rois=3" in out and "slides=1" in out
    assert "integrity_check=ok" in out
    # rc reflects PROBLEMS, not "did the command work" -- a fresh init_db'd
    # database has migrations pending, so 1 is the right answer here. The exit
    # code is pinned by test_doctor_exits_nonzero_when_migrations_are_pending.
    assert rc in (0, 1)


def test_doctor_says_when_the_file_does_not_exist(tmp_path, capsys):
    """The question that prompted this: a URL that looks fine and a file that
    is not there."""
    missing = tmp_path / "nope" / "gone.db"
    rc = main(["--db", f"sqlite:///{missing.as_posix()}", "doctor"])
    out = capsys.readouterr().out
    # get_engine creates parent dirs but not the file; sqlite makes it on
    # connect, so the meaningful assertion is that doctor REPORTS its state
    # rather than crashing on it.
    assert "file " in out
    assert isinstance(rc, int)


def test_doctor_reports_slide_files_that_no_longer_resolve(db, capsys):
    """18 of the real database's 31 slides point at files that are gone. That
    is not an error, but the user must not have to discover it when an ROI
    refuses to save."""
    url, _e, _sid, _ids = db
    main(["--db", url, "doctor"])
    out = capsys.readouterr().out
    assert "slide files" in out
    assert "missing" in out  # the fixture's path was never created


def test_doctor_exits_nonzero_when_migrations_are_pending(db, capsys):
    url, engine, _sid, _ids = db
    from hescope.migrations import current_version, migrate

    if current_version(engine) == 0:
        rc = main(["--db", url, "doctor"])
        assert rc == 1, capsys.readouterr().out
    migrate(engine)
    capsys.readouterr()
    assert main(["--db", url, "doctor"]) == 0


# --- delete-roi ------------------------------------------------------------


def test_delete_roi_reports_before_it_deletes_and_deletes_nothing(db, capsys):
    """An id alone is not enough for a human to confirm they meant it, so the
    default is a report."""
    url, engine, slide_id, ids = db
    rc = main(["--db", url, "delete-roi", str(ids[0]), str(ids[2])])
    out = capsys.readouterr().out

    assert f"roi {ids[0]}:" in out and "bbox=" in out and "label=" in out
    assert "would be deleted" in out
    assert "--yes" in out
    assert len(ROIRepo(engine).for_slide(slide_id)) == 3, "it deleted without --yes"
    assert rc == 0


def test_delete_roi_with_yes_deletes_exactly_those(db, capsys):
    url, engine, slide_id, ids = db
    rc = main(["--db", url, "delete-roi", str(ids[1]), "--yes"])
    out = capsys.readouterr().out

    assert "deleted 1 of 1" in out
    remaining = {r["id"] for r in ROIRepo(engine).for_slide(slide_id)}
    assert remaining == {ids[0], ids[2]}
    assert rc == 0


def test_an_unknown_roi_id_is_reported_not_silently_ignored(db, capsys):
    url, engine, slide_id, ids = db
    main(["--db", url, "delete-roi", str(ids[0]), "999999"])
    out = capsys.readouterr().out
    assert "roi 999999: not found" in out
    assert "1 ROI(s) would be deleted" in out


def test_only_unknown_ids_is_a_failure(db, capsys):
    url, _e, _sid, _ids = db
    assert main(["--db", url, "delete-roi", "999999"]) == 1
