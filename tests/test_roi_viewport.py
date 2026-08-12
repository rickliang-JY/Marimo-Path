"""Tests for ``ROIRepo.in_viewport`` (BUILD-PLAN-DB.md Phase 1, defect 1.4).

Before this phase, ``bbox_json`` was TEXT and ``ROIRepo`` had no
viewport-capable query at all -- ``for_slide`` was the only option, returning
every ROI on the slide regardless of what was actually on screen. Measured
against the pre-fix code (``hescope/db.py`` reverted to its committed state
before this phase, R-2):

    has in_viewport: False
    for_slide returned 2000 of 2000 rois (ALL of them, regardless of any
    viewport) in 68.7 ms, 632.8 KB
    in_viewport call FAILED as expected: 'ROIRepo' object has no attribute
    'in_viewport'

Offline; tmp sqlite files. Never touches data/hescope.db (R-1).
"""

from __future__ import annotations

import time

from hescope.db import ROIRepo, SlideRepo, get_engine, init_db
from hescope.rois import ROI


def _engine(tmp_path):
    eng = get_engine(f"sqlite:///{tmp_path}/viewport.db")
    init_db(eng)
    return eng


def _populate(repo: ROIRepo, slide_id: int, n: int) -> None:
    for i in range(n):
        x = float((i * 137) % 100_000)
        y = float((i * 251) % 100_000)
        repo.add(slide_id, ROI(kind="rect", points=((x, y), (x + 50.0, y + 50.0))))


def test_in_viewport_returns_only_intersecting_rois(tmp_path):
    engine = _engine(tmp_path)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="big.svs", path="big.svs",
        width=100_000, height=100_000,
    )
    repo = ROIRepo(engine)
    _populate(repo, slide_id, 2000)

    x0, y0, x1, y1 = 0.0, 0.0, 10_000.0, 10_000.0

    all_rows = repo.for_slide(slide_id)
    assert len(all_rows) == 2000  # for_slide is still unfiltered by default

    def _intersects(bbox) -> bool:
        bx0, by0, bx1, by1 = bbox
        return not (bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1)

    expected_ids = {r["id"] for r in all_rows if _intersects(r["bbox"])}
    assert 0 < len(expected_ids) < 2000, "the viewport must be a real subset for this to test anything"

    actual = repo.in_viewport(slide_id, x0, y0, x1, y1)
    actual_ids = {r["id"] for r in actual}

    assert actual_ids == expected_ids
    # every returned row really does carry queryable bbox columns, not just
    # the JSON blob -- that IS the fix (defect 1.4)
    assert all(r["bbox_x0"] is not None for r in actual)


def test_in_viewport_excludes_rois_entirely_outside_the_rectangle(tmp_path):
    engine = _engine(tmp_path)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="s.svs", path="s.svs", width=1000, height=1000,
    )
    repo = ROIRepo(engine)
    inside = repo.add(slide_id, ROI(kind="rect", points=((10.0, 10.0), (20.0, 20.0))))
    outside = repo.add(slide_id, ROI(kind="rect", points=((900.0, 900.0), (950.0, 950.0))))

    rows = repo.in_viewport(slide_id, 0.0, 0.0, 100.0, 100.0)

    ids = {r["id"] for r in rows}
    assert inside in ids
    assert outside not in ids


def test_in_viewport_only_returns_rois_for_the_given_slide(tmp_path):
    engine = _engine(tmp_path)
    repo_slides = SlideRepo(engine)
    a = repo_slides.register(source_kind="local", name="a.svs", path="a.svs", width=100, height=100)
    b = repo_slides.register(source_kind="local", name="b.svs", path="b.svs", width=100, height=100)
    repo = ROIRepo(engine)
    a_roi = repo.add(a, ROI(kind="rect", points=((0.0, 0.0), (10.0, 10.0))))
    repo.add(b, ROI(kind="rect", points=((0.0, 0.0), (10.0, 10.0))))  # same coords, other slide

    rows = repo.in_viewport(a, 0.0, 0.0, 100.0, 100.0)

    assert {r["id"] for r in rows} == {a_roi}


def test_in_viewport_respects_limit(tmp_path):
    engine = _engine(tmp_path)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="s.svs", path="s.svs", width=1000, height=1000,
    )
    repo = ROIRepo(engine)
    for _ in range(10):
        repo.add(slide_id, ROI(kind="rect", points=((0.0, 0.0), (10.0, 10.0))))

    assert len(repo.in_viewport(slide_id, 0.0, 0.0, 100.0, 100.0, limit=3)) == 3
    assert len(repo.in_viewport(slide_id, 0.0, 0.0, 100.0, 100.0)) == 10


def test_in_viewport_is_faster_than_for_slide_plus_python_filter(tmp_path):
    """Not just correct -- the point of pushing the predicate into SQL.
    Measured on this machine (2000 ROIs, a viewport matching ~2% of them):

        for_slide+python-filter: 74.45 ms over 2000 rows
        in_viewport (SQL):        3.42 ms over 46 rows

    The exact numbers vary by machine; the assertion only requires
    ``in_viewport`` to win, with a generous margin for run-to-run noise.
    """
    engine = _engine(tmp_path)
    slide_id = SlideRepo(engine).register(
        source_kind="local", name="big.svs", path="big.svs",
        width=100_000, height=100_000,
    )
    repo = ROIRepo(engine)
    _populate(repo, slide_id, 2000)
    x0, y0, x1, y1 = 0.0, 0.0, 10_000.0, 10_000.0

    started = time.perf_counter()
    all_rows = repo.for_slide(slide_id)
    filtered = [
        r for r in all_rows
        if not (r["bbox"][2] < x0 or r["bbox"][0] > x1 or r["bbox"][3] < y0 or r["bbox"][1] > y1)
    ]
    full_scan_s = time.perf_counter() - started

    started = time.perf_counter()
    viewport_rows = repo.in_viewport(slide_id, x0, y0, x1, y1)
    viewport_s = time.perf_counter() - started

    assert {r["id"] for r in filtered} == {r["id"] for r in viewport_rows}
    assert viewport_s < full_scan_s, (
        f"in_viewport ({viewport_s*1000:.2f} ms) was not faster than "
        f"for_slide+python-filter ({full_scan_s*1000:.2f} ms)"
    )
