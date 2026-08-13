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

from hescope.store.db import (
    SQLITE_BUSY_TIMEOUT_MS,
    Base,
    ROIRepo,
    SlideRepo,
    get_engine,
    init_db,
    sqlite_pragma_report,
)
from hescope.core.rois import ROI


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


def test_concurrent_annotation_saves_on_different_rois_do_not_fail(db_url):
    """Read-then-write in one transaction (``s.get`` then mutate then commit)
    is the app's Save-annotation button's actual shape, and it is different
    from the bare-INSERT shape ``test_two_writers_serialise_rather_than_fail``
    covers above: a bare (deferred) ``BEGIN`` lets the SELECT pin a WAL read
    snapshot that the later UPDATE then has to upgrade, which fails
    immediately with "database is locked" and does NOT honour
    ``busy_timeout`` (that pragma governs waiting for a lock, not upgrading a
    snapshot). Measured against the un-fixed engine (bare ``BEGIN`` in
    ``get_engine``'s "begin" hook): 6 of 8 threads raised
    ``sqlite3.OperationalError: database is locked``. Fixed by issuing
    ``BEGIN IMMEDIATE`` instead, which takes the write lock up front and so
    queues (honouring busy_timeout) rather than snapshot-conflicting.
    """
    engine = get_engine(db_url)
    init_db(engine)
    slide_id = _slide(engine)
    repo = ROIRepo(engine)
    roi_ids = [
        repo.add(slide_id, ROI(kind="rect", points=((float(i), 0.0), (float(i) + 5, 5.0))))
        for i in range(8)
    ]

    errors: list = []
    saved: list = []

    def _save(roi_id, i):
        try:
            ok = ROIRepo(get_engine(db_url)).update_annotation(roi_id, label=f"label-{i}")
            if ok:
                saved.append(roi_id)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_save, args=(rid, i)) for i, rid in enumerate(roi_ids)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"concurrent annotation saves failed: {errors[:3]}"
    assert len(saved) == 8


def test_concurrent_slide_registration_of_distinct_slides_does_not_fail(db_url):
    """``SlideRepo.register`` is SELECT-by-path then (insert-or-update) then
    commit -- the same read-then-write shape as ``update_annotation`` above,
    hit every time a slide is opened. Measured against the un-fixed engine:
    16 threads registering 16 DISTINCT slides -> 13 errors, 4 rows written
    (expected 16). Fixed by the same ``BEGIN IMMEDIATE`` change."""
    engine = get_engine(db_url)
    init_db(engine)

    errors: list = []
    written: list = []

    def _register(i):
        try:
            eng = get_engine(db_url)
            written.append(
                SlideRepo(eng).register(
                    source_kind="local",
                    name=f"s{i}.svs",
                    path=f"s{i}.svs",
                    width=4000,
                    height=3000,
                    mpp=0.25,
                )
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_register, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"concurrent registrations failed: {errors[:3]}"
    assert len(written) == 16
    assert len(SlideRepo(engine).list()) == 16


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


# --- the two scenarios that pull against each other ------------------------
#
# A global `BEGIN IMMEDIATE` fixes the second of these and breaks the first; a
# global deferred `BEGIN` does the reverse. Both are asserted here so neither
# can be "fixed" again by moving the setting back.


def test_two_sqlalchemy_readers_do_not_block_each_other(db_url):
    """The app's normal shape: the notebook holds the database open while an
    agent reads it over marimo-pair.

    Measured against a global BEGIN IMMEDIATE: the second reader FAILED after
    2266 ms with "database is locked", because IMMEDIATE takes the write lock
    for every transaction including read-only ones. This test is what stops
    that from being reintroduced.
    """
    engine = get_engine(db_url)
    init_db(engine)
    slide_id = _slide(engine)

    reading = threading.Event()
    release = threading.Event()
    failures: list = []

    def _first_reader():
        try:
            with get_engine(db_url).connect() as conn:
                conn.execute(sa.text("SELECT count(*) FROM rois")).scalar()
                reading.set()
                release.wait(timeout=20)
        except Exception as exc:  # pragma: no cover - surfaced by the assert
            failures.append(exc)
            reading.set()

    t = threading.Thread(target=_first_reader, daemon=True)
    t.start()
    assert reading.wait(timeout=10)

    try:
        started = time.perf_counter()
        with get_engine(db_url).connect() as conn:
            count = conn.execute(sa.text("SELECT count(*) FROM rois")).scalar()
        elapsed = time.perf_counter() - started
    finally:
        release.set()
        t.join(timeout=10)

    assert not failures, failures
    assert count == 0
    assert elapsed < 1.0, (
        f"a second reader took {elapsed:.2f}s while another reader held a "
        "transaction — reads are taking the write lock"
    )


def test_concurrent_read_then_write_calls_all_succeed(db_url):
    """The other side: ``update_annotation`` reads the row and then writes it.

    Under a deferred BEGIN the read pins a WAL snapshot and the write has to
    upgrade it, which fails instantly with SQLITE_BUSY and does NOT honour
    busy_timeout. Measured before ``write_session``: 6 of 8 threads raised.
    """
    engine = get_engine(db_url)
    init_db(engine)
    slide_id = _slide(engine)
    repo = ROIRepo(engine)
    roi_ids = [
        repo.add(slide_id, ROI(kind="rect", points=((float(i), 0.0), (float(i) + 4, 4.0))))
        for i in range(8)
    ]

    errors: list = []

    def _label(roi_id, i):
        try:
            ROIRepo(get_engine(db_url)).update_annotation(roi_id, label=f"L{i}")
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_label, args=(rid, i))
        for i, rid in enumerate(roi_ids)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"{len(errors)} of 8 read-then-write calls failed: {errors[:2]}"
    labels = {r["label"] for r in repo.for_slide(slide_id)}
    assert labels == {f"L{i}" for i in range(8)}


def test_a_write_session_flags_its_connection_and_clears_it_afterwards(db_url):
    """The mechanism, pinned directly — including the release.

    ``Connection.info`` lives on the POOLED dbapi connection. Without an
    explicit clear the flag survives return-to-pool, and the next reader handed
    that connection silently gets BEGIN IMMEDIATE. Observed while writing this
    test: ``assert True is None`` on the second block, i.e. a read connection
    still carrying the write flag.
    """
    from hescope.store.db import write_session

    engine = get_engine(db_url)
    init_db(engine)
    with write_session(engine) as session:
        assert session.connection().info.get("hescope_write") is True
    for _ in range(3):  # exercise pool reuse
        with engine.connect() as conn:
            assert conn.info.get("hescope_write") is None, (
                "the write flag leaked back to a reader through the pool"
            )
