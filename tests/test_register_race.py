"""Tests for defect 1.2 (BUILD-PLAN-DB.md Phase 1): ``SlideRepo.register`` was
check-then-act (SELECT by path, then INSERT-or-UPDATE), which races under
concurrent registration of the SAME slide.

Measured evidence, gathered BEFORE writing the fix in this file, against a
BARE (deferred) ``BEGIN`` -- the transaction semantics this project used
before ``hescope.db.get_engine``'s ``BEGIN IMMEDIATE`` hook (see
``tests/test_db_concurrency.py``, which fixed the analogous DISTINCT-slides
race the same way) -- 8 threads registering ONE slide (identical path),
three separate process runs:

    [bare-BEGIN control] n_threads=8  errors=7  written_ok=1
    [bare-BEGIN control] n_threads=8  errors=3  written_ok=5
    [bare-BEGIN control] n_threads=8  errors=2  written_ok=6
    (all: sqlite3.IntegrityError: UNIQUE constraint failed: slides.path)

Against TODAY's ``get_engine`` (``BEGIN IMMEDIATE``, landed in the prior
round), the SAME scenario already measures 0 errors -- that hook alone
already serializes the old check-then-act body enough to avoid the
write-write race, on every run observed. What ``BEGIN IMMEDIATE`` does NOT
fix is defect 1.1 (the actual duplicate-row problem): a path-only unique key
cannot recognize that two DIFFERENT path strings name the same content, so
the same slide reached through two mount points still became two rows. The
``INSERT ... ON CONFLICT DO UPDATE`` rewrite in this phase fixes THAT (see
``test_same_content_at_different_paths_deduplicates_under_concurrency``
below) and is also the structurally correct implementation regardless of
which transaction mode is in effect -- it does not depend on every
connection taking a write lock up front to stay race-free.

``test_control_bare_begin_can_still_race_on_one_slide`` below re-attempts
that bare-BEGIN reproduction live, several independent times, and reports
whether THIS run of the suite happened to catch it -- thread interleaving on
a shared-lock SQLite file is inherently timing-dependent (unlike
``test_db_concurrency.py``'s reader-lock controls, there is no way to force
the SELECT/INSERT window open deterministically without instrumenting
``register`` itself), and on this machine it does not reproduce every time
even across many attempts. It is a SKIP, not a FAIL, when no attempt
reproduces it, so a quiet run reports "could not reproduce right now"
instead of a spurious red build for a fault this file is not asserting
still exists in today's code.
"""

from __future__ import annotations

import threading

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from hescope.db import Base, SlideFile, Slide, SlideRepo, get_engine, init_db


# --- the control: bare BEGIN really does race for ONE slide -----------------


def _bare_begin_engine(db_url: str) -> sa.Engine:
    """Same pragmas as ``hescope.db.get_engine``, but WITHOUT the
    ``isolation_level=None`` + ``BEGIN IMMEDIATE`` "begin" hook -- i.e.
    pysqlite's own legacy (deferred) transaction handling, the mode this
    project used before the prior round's fix. Mirrors
    ``tests/test_db_concurrency.py``'s own control engine for the analogous
    distinct-slides race.
    """
    engine = sa.create_engine(db_url, connect_args={"timeout": 10})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


def _register_shared_slide(engine_factory, db_url, n_threads: int, *, synchronize: bool = False):
    errors: list[Exception] = []
    written: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads) if synchronize else None

    def _go(i):
        eng = engine_factory(db_url)  # opened before the barrier: connection
        # setup (file open, pragmas) must not be what staggers the threads --
        # only register() itself should run inside the synchronized window.
        if barrier is not None:
            barrier.wait()
        try:
            sid = SlideRepo(eng).register(
                source_kind="local", name="shared.svs", path="shared.svs",
                width=4000, height=3000, mpp=0.25,
            )
            with lock:
                written.append(sid)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_go, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return errors, written


def test_control_bare_begin_can_still_race_on_one_slide(tmp_path):
    """Best-effort live reproduction of the pre-fix failure (see the module
    docstring for the numbers actually observed while developing this fix).
    Thread interleaving is inherently timing-dependent, so this retries
    several independent attempts and SKIPS (not fails) if none of them
    reproduce on this run of this machine -- it is not asserting anything
    about today's (already-fixed) `register`, only demonstrating, when it
    can, that the check-then-act SHAPE this replaces really was unsafe.
    """
    for attempt in range(10):
        db_url = f"sqlite:///{(tmp_path / f'control{attempt}.db').as_posix()}"
        setup = _bare_begin_engine(db_url)
        Base.metadata.create_all(setup)
        setup.dispose()

        errors, written = _register_shared_slide(_bare_begin_engine, db_url, 8)

        assert len(written) + len(errors) == 8
        if errors:
            assert all(isinstance(e, sa.exc.IntegrityError) for e in errors)
            return  # reproduced -- done
    pytest.skip(
        "the bare-BEGIN race did not reproduce in 10 attempts on this run; "
        "see the module docstring for the numbers observed while developing "
        "this fix (sqlite3.IntegrityError: UNIQUE constraint failed: "
        "slides.path, 2-7 of 8 threads, across separate process runs)"
    )


# --- the fix: 16 concurrent registrations of ONE slide, 0 errors, 1 row -----


def test_sixteen_concurrent_registrations_of_one_slide_race_free(tmp_path):
    """The acceptance criterion from BUILD-PLAN-DB.md Phase 1: 16 threads
    registering the SAME slide (identical path) via today's ``get_engine`` +
    the identity-aware ``register`` -> 0 errors, exactly 1 row."""
    db_url = f"sqlite:///{(tmp_path / 'race.db').as_posix()}"
    setup = get_engine(db_url)
    init_db(setup)
    setup.dispose()

    errors, written = _register_shared_slide(get_engine, db_url, 16)

    assert not errors, f"concurrent registration of one slide failed: {errors[:3]}"
    assert len(written) == 16
    assert len(set(written)) == 1, f"expected exactly 1 row, ids were {set(written)}"

    engine = get_engine(db_url)
    assert len(SlideRepo(engine).list()) == 1
    with sa.orm.Session(engine) as s:
        file_count = s.execute(
            sa.select(sa.func.count()).select_from(SlideFile)
        ).scalar_one()
    assert file_count == 1, "one path was registered 16 times -> one slide_files row"


def test_same_content_at_different_paths_deduplicates_under_concurrency(tmp_path):
    """Defect 1.1 under concurrency: the SAME identity reached through 16
    DIFFERENT path strings (mount points / spellings that do not textually
    normalize to one another) must still collapse to ONE slide row, with one
    slide_files row per distinct path. An explicit ``identity`` is used so
    this does not depend on 16 real files sharing content -- ``register``'s
    resolution order puts a supplied identity first regardless."""
    db_url = f"sqlite:///{(tmp_path / 'dedup.db').as_posix()}"
    setup = get_engine(db_url)
    init_db(setup)
    setup.dispose()

    errors: list[Exception] = []
    written: list[int] = []
    lock = threading.Lock()

    def _go(i):
        try:
            sid = SlideRepo(get_engine(db_url)).register(
                source_kind="local",
                name="mounted.svs",
                path=f"mount-{i}/mounted.svs",  # 16 DISTINCT path strings
                width=4000, height=3000,
                identity=("sha256", "one-real-slide"),
            )
            with lock:
                written.append(sid)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_go, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"concurrent same-identity registration failed: {errors[:3]}"
    assert len(set(written)) == 1
    engine = get_engine(db_url)
    slide = SlideRepo(engine).get(written[0])
    assert slide["identity_scheme"] == "sha256"
    assert slide["identity_key"] == "one-real-slide"
    with sa.orm.Session(engine) as s:
        file_count = s.execute(
            sa.select(sa.func.count()).select_from(SlideFile)
            .where(SlideFile.slide_id == written[0])
        ).scalar_one()
    assert file_count == 16, "16 distinct paths for the SAME identity -> 16 slide_files rows"


# --- the legacy-path fallback (not a race test: single-threaded) -----------


def test_registering_a_known_path_under_a_new_identity_enriches_the_legacy_row(
    tmp_path,
):
    """A row written before identity existed (identity_scheme IS NULL) sits
    at a path. Registering that SAME path again, now with a real content
    identity, must enrich that row (fold into it via the ``path`` conflict
    target) rather than raising IntegrityError on the ``path`` UNIQUE
    constraint -- the identity-keyed upsert alone has nothing to conflict
    with (a NULL identity_scheme never participates in the partial unique
    index), so without this fallback the plain INSERT underneath it would
    hit the path constraint and raise.
    """
    db_url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    engine = get_engine(db_url)
    init_db(engine)

    real_file = tmp_path / "legacy.svs"
    real_file.write_bytes(b"legacy slide bytes")
    path = str(real_file.resolve())

    with sa.orm.Session(engine) as s:  # pre-Phase-1 style row: bypass register()
        legacy = Slide(
            source_kind="local", name="legacy.svs", path=path,
            width=10, height=10, extra_json="{}",
        )
        s.add(legacy)
        s.commit()
        legacy_id = legacy.id

    new_id = SlideRepo(engine).register(
        source_kind="local", name="legacy.svs", path=path, width=10, height=10,
    )

    assert new_id == legacy_id, "must enrich the existing row, not create a new one"
    assert len(SlideRepo(engine).list()) == 1
    row = SlideRepo(engine).get(new_id)
    assert row["identity_scheme"] == "sha256"
    assert row["identity_key"] is not None
