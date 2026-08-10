"""``density_per_mm2`` must describe the ROI, not the thumbnail of it.

``extract_patch`` reads from a downsampled pyramid level and then thumbnails
whatever is still wider than ``max_size`` (1024 px), so a patch pixel is
generally not a level-0 pixel. ``detect_nuclei`` computes its area from the
mpp it is handed times the PATCH's own pixel dimensions
(``nuclei.py``: ``area_mm2 = (h * mpp) * (w * mpp) / 1e6``). app.py handed it
``_src.mpp``, the LEVEL-0 value, so the area it divided by was smaller than
the real ROI by the extraction downsample squared and ``density_per_mm2`` came
out that many times too large — 4x at a 2048 px ROI, 16x at 4096, 64x at 8192,
under a green "Analyzed ..." callout that prints the true patch size right
beside it (R07-2).

Any ROI up to 1024 level-0 px on its long axis is unaffected, which is why
this survived: it only appears once the user drags a bigger box.

AGENTS.md 9's worked recipe carried the same two calls, so an agent following
the contract reproduced the error independently; that recipe now uses
``patch_mpp`` too.
"""

from __future__ import annotations

import ast
import pathlib

import marimo as mo
import pytest
from PIL import Image

from hescope.rois import ROI, extract_patch, patch_mpp
from hescope.slides import open_slide

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

MPP = 0.25


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """A slide big enough that a realistic ROI is downsampled by extract_patch."""
    path = tmp_path_factory.mktemp("density") / "slide.png"
    # Deterministic blobs so detect_nuclei finds something at EVERY raster the
    # test exercises: 24 px at level 0 is still 6 px (36 px area, above the
    # 20 px min_size) after the 4x downsample a 4096 px ROI gets. The count
    # itself is irrelevant here -- only the area it is divided by.
    img = Image.new("RGB", (6000, 4000), (235, 220, 235))
    px = img.load()
    for y in range(0, 4000 - 96, 96):
        for x in range(0, 6000 - 96, 96):
            for dy in range(24):
                for dx in range(24):
                    px[x + dx, y + dy] = (90, 40, 120)
    img.save(path)
    src = open_slide(path)
    src.mpp = MPP  # PillowSource has no metadata to parse one from
    return src


def _analyze_cell():
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
            continue
        src = ast.get_source_segment(SOURCE, node)
        if src is None or '"Analyze current selection" (Analysis accordion)' not in src:
            continue
        ns: dict = {}
        exec(compile("\n" * (node.lineno - 1) + src, str(APP), "exec"), ns)
        return ns[node.name], [a.arg for a in node.args.args]
    raise AssertionError("no @app.cell in app.py holds 'Analyze current selection'")


def _click_analyze(source, roi):
    """Drive app.py's OWN analyze cell with ``roi`` as the live selection."""
    import hescope
    from hescope.db import ROIRepo  # noqa: F401  (import symmetry with app.py)

    published: dict = {}

    class _NoDB:
        enabled = False

        def trace(self, *a, **k):
            return None

    cell, params = _analyze_cell()
    deps = {
        "ROI": ROI,
        "db": _NoDB(),
        "detect_nuclei": hescope.detect_nuclei,
        "extract_patch": extract_patch,
        "get_payload": lambda: None,
        "get_rois": lambda: [],
        "get_slide_id": lambda: None,
        "get_source": lambda: source,
        "live_selection": lambda: {
            "kind": roi.kind,
            "points_level0": [list(p) for p in roi.points],
        },
        "mo": mo,
        "patch_mpp": patch_mpp,
        "qc_report": hescope.qc_report,
        "set_analysis_result": lambda v: published.__setitem__("result", v),
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the analyze cell grew new dependencies: {missing}"
    out = cell(**{p: deps[p] for p in params})
    # the cell returns (analyze_button,)
    out[0]._update(object())  # the real click
    return published["result"]


def _true_density(roi, count):
    x0, y0, x1, y1 = roi.bbox()
    area_mm2 = ((x1 - x0) * MPP) * ((y1 - y0) * MPP) / 1e6
    return count / area_mm2


@pytest.mark.parametrize("side", [800, 2048, 4096])
def test_the_reported_density_matches_the_roi_it_names(source, side):
    roi = ROI(kind="rect", points=((1000.0, 1000.0), (1000.0 + side, 1000.0 + side)))

    kind, origin, data = _click_analyze(source, roi)

    assert kind == "ok", (kind, origin)
    # Otherwise detect_nuclei short-circuits to _empty_stats(), whose density
    # is None whatever mpp says, and this test would pass on nothing.
    assert data["nuclei"]["count"] > 0, data["nuclei"]
    reported = data["nuclei"]["density_per_mm2"]
    expected = _true_density(roi, data["nuclei"]["count"])
    assert reported == pytest.approx(expected, rel=0.02), (
        f"a {side}x{side} level-0 ROI was thumbnailed to "
        f"{data['patch_size']} px and the density was computed against the "
        f"THUMBNAIL's area: reported {reported:.1f}/mm^2 where the ROI holds "
        f"{expected:.1f}/mm^2 -- overstated {reported / expected:.2f}x, i.e. "
        "the extraction downsample squared. The callout above it is green and "
        f"prints the true patch size {data['patch_size']}."
    )


def test_patch_mpp_is_the_extraction_downsample(source):
    """The unit behind it, so a failure says which half is wrong."""
    roi = ROI(kind="rect", points=((0.0, 0.0), (4096.0, 4096.0)))
    patch = extract_patch(source, roi, max_size=1024)

    assert max(patch.size) == 1024
    assert patch_mpp(source, roi, patch) == pytest.approx(MPP * 4.0)


def test_a_patch_that_was_not_downsampled_keeps_the_level_0_mpp(source):
    roi = ROI(kind="rect", points=((0.0, 0.0), (800.0, 600.0)))
    patch = extract_patch(source, roi, max_size=1024)

    assert patch.size == (800, 600)
    assert patch_mpp(source, roi, patch) == pytest.approx(MPP)


def test_patch_mpp_is_none_when_the_slide_has_no_mpp(tmp_path):
    path = tmp_path / "nompp.png"
    Image.new("RGB", (256, 256), (240, 230, 240)).save(path)
    src = open_slide(path)
    roi = ROI(kind="rect", points=((0.0, 0.0), (128.0, 128.0)))

    assert src.mpp is None
    assert patch_mpp(src, roi, extract_patch(src, roi)) is None
