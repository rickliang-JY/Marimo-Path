# Package layout — what belongs where

`hescope/` was 31 modules in one flat directory. It is now eight subpackages
grouped by function. **No module was renamed** — only moved — so a change is a
path change, not a rewrite.

```
hescope/
  __init__.py          the public API surface (57 names); unchanged by the split
  cli.py               the `hescope` entry point

  core/                domain types and anchors, no heavy dependencies
    rois.py            ROI geometry, ViewportState, extract_patch, roi_stats
    identity.py        content_key, slide_identity  -- how a slide is named
    paths.py           PACKAGE_ROOT / PROJECT_ROOT / resolve_runtime_dir
    measure.py         format_measurement

  wsi/                 reading whole-slide images
    slides.py          SlideSource, open_slide, OpenSlide/Pillow/Tifffile backends
    dicom_source.py    DICOM WSI via wsidicom
    demo.py            the generated demo slide

  store/               persistence
    db.py              engine, schema, repositories
    migrations.py      versioned, forward-only migration runner

  gdc/                 the Genomic Data Commons (TCGA, HCMI, ALCHEMIST, ...)
    tcga.py            GDCClient, SlideCatalog, the downloader
    tcga_schema.py     project/case/sample/file tables and TcgaCatalog
    tcga_panel.py      row shaping for the browser UI

  analysis/            measurement and ML
    nuclei.py qc.py stain.py features.py
    grid.py heatmap.py stats_table.py
    ml.py embeddings.py

  viewer/              rendering surfaces
    viewer.py          viewport state, navigator, DBContext
    osdviewer.py       the OpenSeadragon anywidget
    tileserver.py      the local DZI tile server
    overlay.py adjust.py

  interop/             getting annotations in and out
    geojson.py         QuPath-compatible export
    importers.py       QuPath GeoJSON and ASAP XML import

  agent/               the code-agent contract
    agent_bridge.py    AgentBridge, the module-scope tools

  static/              vendored OpenSeadragon (never moves -- see below)
```

## The rules that keep it honest

**Dependencies point inward.** `core/` depends on nothing else in the package.
`store/` may use `core/`. `analysis/`, `viewer/`, `interop/`, `gdc/` and
`agent/` may use `core/`, `wsi/` and `store/`. Nothing imports `cli.py`.

**Never count `.parent` to find a directory.** Two modules did, and both would
have broken silently in this split:

* `db.py` computed the project root as `Path(__file__).parent.parent`. Moving it
  into `store/` changed that from the repo root to `hescope/`, which would have
  pointed `DEFAULT_DB_URL` at a **different database file** — the user's data
  would have looked like it vanished.
* `osdviewer.py` located the vendored OpenSeadragon as
  `Path(__file__).parent / "static"`, which the move would have made
  `hescope/viewer/static/`.

Both now ask `hescope.core.paths` for `PROJECT_ROOT` / `PACKAGE_ROOT`. That
module is the **one place** that knows its own depth. If you need a directory,
import an anchor; do not count.

**Tests that scan the package must use `rglob`, not `glob`.** A flat
`(root / "hescope").glob("*.py")` silently stopped seeing every module that
moved into a subpackage — `test_interaction_trace` reported "nothing records
selection_view" about kinds that are recorded perfectly well.

**A stub injected for a lazy import goes on the module's new holder.** Two test
fixtures patched `hescope.features`, but `analysis/ml.py` does
`from . import features`, which resolves through `hescope.analysis`. The patch
silently missed, and the tests ran against the real 56-dimension features while
believing they had a 16-dimension stub — and still passed, on the wrong thing.

## What did NOT change

* `hescope/__init__.py` still exports the same 57 names, so
  `from hescope import ROI, open_slide, ...` is unaffected.
* `app.py`'s agent contract is unaffected: the nine module-scope tools are
  defined in the notebook, not in the package.
* `hescope/static/` stays at the package root; it is addressed through
  `PACKAGE_ROOT`.

`AGENTS.md` documents two submodule paths (`hescope.agent_bridge`,
`hescope.rois`); they are now `hescope.agent.agent_bridge` and
`hescope.core.rois`, and that document has been updated.
