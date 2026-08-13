"""Re-opening a slide whose content duplicates another must not detach it.

The user's real database has two duplicate-content groups covering five rows:
{3, 31} = demo_he.png and {17, 18, 32} = small_slide.png / slide_B_small.png.
Phase 1 gave slides a content identity with a UNIQUE index, and at commit
9036eba re-registering those rows raised:

    slide 18 -> IntegrityError: UNIQUE constraint failed: slides.identity_scheme,
                slides.identity_key
    3 of 5 raised

app.py catches that, calls set_slide_id(None) and shows "Slide registration
failed", so the slide still RENDERS while being completely disconnected from the
database -- no ROI save, no annotations. It needed no migration to trigger:
init_db alone, which viewer.py runs at every app start, was enough.

It is fixed on the current tree, but it was fixed BY ACCIDENT -- a side effect of
routing read-then-write repo methods through `write_session` for an unrelated
concurrency problem. An accidental fix with no test is one refactor away from
coming back, so this file pins the behaviour rather than the mechanism.

Verified both ways with a worktree at 9036eba: 3 of 5 raised there, 0 of 5 raise
on HEAD, same script and same input.
"""

from __future__ import annotations

import pytest

from hescope.store.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.core.rois import ROI


@pytest.fixture()
def engine(tmp_path):
    eng = get_engine(f"sqlite:///{(tmp_path / 'dup.db').as_posix()}")
    init_db(eng)
    return eng


def _twin_files(tmp_path, content=b"\x89PNG\r\n\x1a\n" + b"identical" * 500):
    a = tmp_path / "a" / "slide.png"
    b = tmp_path / "b" / "slide.png"
    for p in (a, b):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return a, b


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_registering_two_files_with_identical_content_never_raises(
    engine, tmp_path, order
):
    """Both orders: whichever is opened second must not be the one that breaks.

    The original defect was order-dependent, which is what made it dangerous --
    opening slide 31 before slide 3 detached slide 3, and slide 3 holds 2 of the
    database's 10 ROIs.
    """
    a, b = _twin_files(tmp_path)
    paths = {"a": a, "b": b}
    repo = SlideRepo(engine)

    ids = []
    for key in order:
        ids.append(
            repo.register(
                source_kind="local", name="slide.png", path=str(paths[key]),
                width=600, height=400, mpp=None,
            )
        )

    assert all(isinstance(i, int) and i > 0 for i in ids), ids


def test_identical_content_at_two_paths_is_ONE_slide_with_two_files(engine, tmp_path):
    """Phase 1's actual goal, stated as the assertion rather than assumed.

    Two paths, the same bytes: one slide row, two ``slide_files`` rows, and ROIs
    drawn through either path land on the same slide. That is the whole point of
    a content identity -- it is what stops `demo_he.png` from being slides 3 AND
    31 with the annotations only on one of them.

    (My first version of this test asserted two separate slides each holding one
    ROI. That was me asserting the OLD behaviour; the merge is the fix.)
    """
    import sqlalchemy as sa

    a, b = _twin_files(tmp_path)
    repo = SlideRepo(engine)
    first = repo.register(source_kind="local", name="slide.png", path=str(a),
                          width=600, height=400, mpp=None)
    ROIRepo(engine).add(first, ROI(kind="rect", points=((0.0, 0.0), (10.0, 10.0))))

    second = repo.register(source_kind="local", name="slide.png", path=str(b),
                           width=600, height=400, mpp=None)
    ROIRepo(engine).add(second, ROI(kind="rect", points=((5.0, 5.0), (20.0, 20.0))))

    assert first == second, "identical content produced two slide rows"
    assert len(ROIRepo(engine).for_slide(first)) == 2, (
        "an ROI drawn through the second path did not reach the slide"
    )
    with engine.connect() as conn:
        paths = {
            r[0] for r in conn.execute(
                sa.text("SELECT path FROM slide_files WHERE slide_id = :s"),
                {"s": first},
            )
        }
    assert len(paths) == 2, f"both locations must be recorded, got {paths}"


def test_re_registering_the_same_path_is_still_idempotent(engine, tmp_path):
    """Guard against the fix overreaching: one file, one row, stable id."""
    a, _b = _twin_files(tmp_path)
    repo = SlideRepo(engine)
    ids = {
        repo.register(source_kind="local", name="slide.png", path=str(a),
                      width=600, height=400, mpp=None)
        for _ in range(3)
    }
    assert len(ids) == 1
