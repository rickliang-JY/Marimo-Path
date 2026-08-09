"""Features the docs promise must actually be reachable from app.py.

Two of round 05's findings were about wiring, not logic, and neither was
visible to any test we had:

  * R05-8 -- README lists "GeoJSON export. One click turns annotations into a
    QuPath-compatible FeatureCollection" on the user-facing feature list, but
    `export_rois_geojson` had no caller anywhere in app.py. A repo-wide search
    for "geojson" matched 14 files; app.py was not among them. The one interop
    claim a user could act on was an agent-only API.
  * R05-1 -- the GDC-supplied `file_id` was joined onto `TCGA_DATA_DIR` raw,
    so a "../" in it walked the download out of the data directory (twice
    over, since the fallback file name is `f"{file_id}.svs"`).

Asserted over marimo's OWN graph -- the refs and cell bodies it computed from
app.py -- rather than by grepping the file, so a rename of the helper cannot
leave the guard passing on unwired code.
"""

from __future__ import annotations

import re

import pytest


@pytest.fixture(scope="module")
def graph():
    import app as appmod

    appmod.app._maybe_initialize()
    return appmod.app._graph


def _cell_defining(graph, name: str):
    owners = [cell for cell in graph.cells.values() if name in cell.defs]
    assert len(owners) == 1, f"expected exactly one cell defining {name}: {owners}"
    return owners[0]


def test_the_annotations_panel_offers_a_geojson_download(graph):
    """R05-8: the export must hang off the same cell as the JSON/CSV pair."""
    cell = _cell_defining(graph, "ann_edit_view")
    assert "slide_geojson_text" in cell.refs, (
        "the Annotations panel does not call the GeoJSON exporter, so the "
        "'one click' README advertises does not exist for a user"
    )
    assert "rois.geojson" in cell.code, "no GeoJSON download filename in the panel"
    # ... and it is a real download button, beside the two that already work
    assert cell.code.count("mo.download(") == 3


def test_the_tcga_download_contains_the_server_supplied_file_id(graph):
    """R05-1: every join of TCGA_DATA_DIR must go through safe_file_id."""
    cell = _cell_defining(graph, "tcga_results_table")
    assert "safe_file_id" in cell.refs
    joins = re.findall(r"TCGA_DATA_DIR\s*/\s*([A-Za-z_][\w.]*)", cell.code)
    assert joins, "the download handler no longer joins onto TCGA_DATA_DIR"
    assert set(joins) == {"safe_file_id"}, (
        "a server-supplied name is joined onto the download root unsanitized: "
        f"TCGA_DATA_DIR / {joins}"
    )
