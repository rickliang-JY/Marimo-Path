"""A write must not fail because something else is reading.

The app's normal shape is two readers around one writer: the notebook holds the
database open, an agent reads the same file over marimo-pair, and a click saves
an ROI. Under SQLite's default ``journal_mode=delete`` that save raises
``OperationalError: database is locked`` — measured at 5.5 s to surface, which
is long enough to look like a hang and short enough that nobody suspects the
journal mode.

``get_engine`` now sets three pragmas on every sqlite connection:

  foreign_keys=ON      (already there since the initial commit)
  busy_timeout=10000   wait for a lock instead of failing instantly
  journal_mode=WAL     readers and the writer proceed concurrently

The first test below is the behavioural one and is the reason this file exists;
the pragma assertions after it are there so a future engine cannot lose one of
the three silently.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest
import sqlalchemy as sa

from hescope.db import (
    SQLITE_BUSY_TIMEOUT_MS,
    Base,
    ROIRepo,
    SlideRepo,
    get_engine,
    init_db,
    sqlite_pragma_report,
)
from hescope.rois import ROI


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{(tmp_path / 'concurrent.db').as_posix()}"


def _slide(engine):
    return SlideRepo(engine).register(
        source_kind="local", name="s.svs", path="s.svs",
        width=4000, height=3000, mpp=0.25,
    )


# --- the failure this exists to prevent ------------------------------------


def _hold_shared_lock(db_file):
    """A reader that genuinely holds SQLite's SHARED lock.

    This detail is the whole test. A SQLAlchemy connection running a SELECT
    does NOT hold the lock across the call, so an earlier version of this test
    passed against the un-fixed engine — a false green of exactly the kind
    bugs/SUMMARY.md exists to catch. A raw ``sqlite3`` connection with an
    explicit ``BEGIN`` and an executed SELECT does hold it, and that is what
    makes a concurrent writer block at COMMIT.
    """
    conn = sqlite3.connect(str(db_file), timeout=30)
    conn.execute("BEGIN")
    conn.execute("SELECT count(*) FROM rois").fetchone()
    return conn


def test_an_roi_saves_while_another_connection_holds_a_read_lock(tmp_path, db_url):
    """The measured failure, reproduced and fixed.

    Control: the same scenario on a plain ``create_engine`` (what get_engine was
    before) is asserted to FAIL in the test below, so this pair proves the fix
    rather than asserting that today's behaviour is today's behaviour.
    """
    db_file = tmp_path / "concurrent.db"
    engine = get_engine(db_url)
    init_db(engine)
    slide_id = _slide(engine)

    reader = _hold_shared_lock(db_file)
    try:
        started = time.perf_counter()
        roi_id = ROIRepo(get_engine(db_url)).add(
            slide_id, ROI(kind="rect", points=((0.0, 0.0), (10.0, 10.0)))
        )
        elapsed = time.perf_counter() - started
    finally:
        reader.rollback()
        reader.close()

    assert roi_id, "the ROI was not written"
    assert elapsed < 2.0, (
        f"the save took {elapsed:.1f}s with a reader holding SHARED — it is "
        "queueing, which is what journal_mode=WAL is here to stop"
    )
    assert len(ROIRepo(engine).for_slide(slide_id)) == 1


def test_the_control_the_engine_used_to_be_does_fail(tmp_path):
    """The other half of the pair: a plain engine on a `delete`-journal file
    raises 'database is locked' in the same scenario.

    Measured at 5.54 s with sqlite's default 5 s wait; the timeout here is cut
    to 0.5 s so the proof costs half a second instead of six.
    """
    db_file = tmp_path / "plain.db"
    url = f"sqlite:///{db_file.as_posix()}"
    plain = lambda: sa.create_engine(url, connect_args={"timeout": 0.5})  # noqa: E731

    Base.metadata.create_all(plain())
    with plain().connect() as conn:
        assert conn.execute(sa.text("PRAGMA journal_mode")).scalar() == "delete"
    slide_id = _slide(plain())

    reader = _hold_shared_lock(db_file)
    try:
        with pytest.raises(sa.exc.OperationalError, match="database is locked"):
            ROIRepo(plain()).add(
                slide_id, ROI(kind="rect", points=((0.0, 0.0), (10.0, 10.0)))
            )
    finally:
        reader.rollback()
        reader.close()


def test_two_writers_serialise_rather_than_fail(db_url):
    """WAL still allows only one writer. What must not happen is an immediate
    'database is locked' — busy_timeout makes the second writer wait."""
    engine = get_engine(db_url)
    init_db(engine)
    slide_id = _slide(engine)

    errors: list = []
    written: list = []

    def _write(i):
        try:
            eng = get_engine(db_url)
            written.append(
                ROIRepo(eng).add(
                    slide_id,
                    ROI(kind="rect", points=((float(i), 0.0), (float(i) + 5, 5.0))),
                )
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"concurrent writes failed: {errors[:3]}"
    assert len(written) == 8
    assert len(ROIRepo(engine).for_slide(slide_id)) == 8


# --- the three pragmas -----------------------------------------------------


def test_a_file_backed_engine_sets_all_three_pragmas(db_url):
    report = sqlite_pragma_report(get_engine(db_url))
    assert report["foreign_keys"] is True
    assert report["journal_mode"] == "wal"
    assert report["busy_timeout"] == SQLITE_BUSY_TIMEOUT_MS


def test_an_in_memory_engine_keeps_foreign_keys_and_the_timeout():
    """WAL is meaningless for :memory: and sqlite reports 'memory'. The other
    two still have to be set — the test suite runs on in-memory databases."""
    report = sqlite_pragma_report(get_engine("sqlite:///:memory:"))
    assert report["foreign_keys"] is True
    assert report["busy_timeout"] == SQLITE_BUSY_TIMEOUT_MS
    assert report["journal_mode"] == "memory"


def test_the_report_is_a_diagnostic_and_never_raises(tmp_path):
    """It is meant for a status panel; a diagnostic must not be what breaks."""
    engine = get_engine(f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    engine.dispose()
    assert isinstance(sqlite_pragma_report(engine), dict)


def test_wal_persists_across_engines(db_url):
    """journal_mode is a property of the FILE, so a second engine (a CLI call,
    a second notebook) inherits it rather than racing to set it."""
    init_db(get_engine(db_url))
    assert sqlite_pragma_report(get_engine(db_url))["journal_mode"] == "wal"
