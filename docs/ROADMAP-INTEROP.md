# Interoperability and capability roadmap

**English** · [简体中文](ROADMAP-INTEROP.zh-CN.md)

Written 2026-08-09, on branch `feature/interop-and-hardening`.

The thesis this plan starts from: the cheapest way to absorb the existing
ecosystem is not to import other people's algorithms, it is to let other
people's *artifacts* move in and out. That thesis survives contact with the
dependency resolver in tier 1 and does not survive it in tier 2, so tier 2 is
restructured below rather than adopted as stated.

Everything here was checked against the code and the resolver before being
written down; the numbers are measured, not estimated.

---

## What we already have, precisely

| Capability | Where | State |
| --- | --- | --- |
| GeoJSON export | `hescope/geojson.py`, 82 lines | Present but **lossy** (see below) |
| GeoJSON import | — | Missing |
| ASAP / QuPath XML | — | Missing |
| WSI reading | `hescope/slides.py` | OpenSlide + tifffile/zarr + Pillow. No DICOM |
| Stain normalization | `hescope/stain.py`, 173 lines | Macenko, Reinhard |
| Nuclei | `hescope/nuclei.py`, 122 lines | H&E deconvolution → Otsu → watershed |
| QC | `hescope/qc.py`, 77 lines | tissue fraction, blur score, brightness |
| Features | `hescope/features.py`, 200 lines | 56 hand-crafted |
| FM encoders | `hescope/embeddings.py` | GPFM / H-optimus-0 / UNI2-h / ResNet18 |

### The GeoJSON export is lossy, and the data to fix it is already stored

`rois_to_geojson` builds its geometry from `bbox` only. A lasso becomes its
bounding box:

```
stored points  : [[10,10],[90,20],[50,80]]      # a triangle
exported ring  : [[10,10],[90,10],[90,80],[10,80],[10,10]]   # a rectangle
shape preserved: False
```

This is not a data limitation. The `rois` table already stores
`points_json` alongside `bbox_json`, and `ROIRepo` already returns it in
every row dict. The export simply ignores it. Fixing this is a handful of
lines and it is a prerequisite for any honest round trip — there is no point
importing QuPath polygons into a system that flattens them on the way out.

---

## Tier 1 — interoperability (cheapest, highest value, do first)

Confirmed cheap. `wsidicom` resolves to **7 new packages, 0 uninstalls, 0
downgrades**:

```
+ cachetools, dicomweb-client, marshmallow, pydicom, retrying,
  universal-pathlib, wsidicom
```

| # | Item | Effort | Why |
| --- | --- | --- | --- |
| 1.1 | **Make GeoJSON export shape-faithful** | 0.5 d | Prerequisite for a round trip; data already stored |
| 1.2 | **GeoJSON import** | 1–2 d | Every QuPath user's existing annotations become usable with no algorithm written |
| 1.3 | **ASAP / QuPath XML import** | 1 d | The other format real annotation sets arrive in |
| 1.4 | **DICOM WSI reading** via `wsidicom` | 1–2 d | A `SlideSource` implementation; clinical scanners increasingly emit DICOM |
| 1.5 | Round-trip test corpus | 0.5 d | Import → export → import must be identity on geometry and class |

**Design constraints for the importer.** Import must go through the same
`ROI` vocabulary the rest of the system uses (`rect` / `polygon` / `circle`)
and land in the `rois` table with `points_json` populated, so imported
annotations are indistinguishable from drawn ones — they must be trainable
on, exportable, and visible to `query_annotations()`. QuPath writes
`classification: {"name": ...}`; that maps to our `label`. Coordinates are
level-0 pixels on both sides, which is what makes this cheap.

**The trap to test for:** QuPath GeoJSON in the wild contains `MultiPolygon`,
holes (interior rings), and `Point`/`LineString` features. Decide explicitly
what happens to each — flatten, skip with a count, or reject the file — and
test it. Silently importing the outer ring of a donut is worse than refusing
it.

---

## Tier 2 — TIAToolbox: **do not adopt as a direct dependency**

The premise was "one adapter buys four or five capabilities, and the adapter
can be thin because TIAToolbox accepts standard PyTorch modules". The first
half is right about the API. The second half is not true of the
*dependencies*, and that is what decides this.

Measured with the resolver against this exact environment:

```
tiatoolbox:  198 resolved, 107 downloaded, 152 installed, 22 UNINSTALLED
```

The 22 it would remove or downgrade include:

```
numpy 2.4.6, torch 2.13.0, torchvision 0.28.0, timm 1.0.28,
scikit-learn 1.9.0, scipy 1.17.1, pillow 12.3.0, tifffile 2026.3.3,
imagecodecs, openslide-python 1.4.6, openslide-bin 4.0.1.2,
huggingface-hub 1.27.0, ipywidgets 8.1.8, joblib, requests, pyyaml, ...
```

Three of those are load-bearing here:

- **`ipywidgets`** — `anywidget` sits on it, and `anywidget` is the
  OpenSeadragon viewer. This is the main viewing surface.
- **`openslide-python` / `openslide-bin`** — how we read real WSIs.
- **`torch` / `torchvision` / `timm`** — the FM encoder factory is built and
  pinned around these.

So adopting TIAToolbox into the main environment does not add a thin adapter;
it rewrites the foundation the viewer and the encoder stack stand on, and any
future TIAToolbox release re-litigates all of it. The capability argument is
sound; the packaging is the problem.

### What to do instead, in order of preference

**2a. Take the two missing stain normalizers directly (recommended).**
Of the four TIAToolbox offers, we already have Macenko and Reinhard. Ruifrok
is fixed-matrix deconvolution — `skimage.color.rgb2hed` is already that, so it
is a thin wrapper over code we import today. Vahadane needs sparse NMF, which
is `sklearn.decomposition.DictionaryLearning`; scikit-learn is already a
dependency. Cost: roughly a day, zero new packages, no downgrades. This
captures most of the stain-normalization value with none of the packaging
cost.

**2b. Run TIAToolbox out-of-process for HoVer-Net, if and when nuclei quality
actually blocks someone.** A separate uv-managed environment plus a
subprocess boundary that exchanges a patch and a JSON of instances (bbox,
centroid, contour, class probabilities). The dependency conflict disappears
because the environments never meet. Cost is real (an install, a process
boundary, a schema) but it is bounded and reversible, and it is the only way
to have both HoVer-Net and our current torch pin.

**2c. Do not vendor TIAToolbox source.** Licence and maintenance both argue
against it.

**Explicitly agreed:** skip PathML (limited model choice, unclear extension
path) and HistomicsTK (centre of gravity has moved to the Digital Slide
Archive ecosystem).

---

## Tier 3 — point capabilities, on demand only

Build these when something is actually blocked, not before.

| Item | Trigger to build it |
| --- | --- |
| InstanSeg / CellViT instance segmentation | Watershed nuclei visibly failing on real slides |
| HistoQC / GrandQC | Someone is misled by a slide our 77-line `qc.py` passed |
| Spatial morphometrics (SPARK-style) | A concrete question needs "lymphocytes within N µm of tumour" |

`qc.py` at 77 lines is thin for a real pain point, and it is the most likely
of the three to be needed first — pen marks, out-of-focus regions, tissue
folds and bubbles are all things it cannot currently see.

---

## UI

What the current interface gets wrong, from using it:

1. **The toolbar mixes two eras.** With OpenSeadragon driving, the arrow
   buttons and the downsample slider are vestigial — pan and zoom are the
   mouse now. They should become a compact secondary group (or a keyboard
   shortcut), not the widest thing in the bar.
2. **The zoom readout is raw.** It renders `8.001340482573728`. It should
   read as magnification (`5.0×`) or a rounded downsample; that number is a
   number nobody can use.
3. **Selection state is invisible outside the canvas.** The dashed outline
   now persists (fixed today), but nothing says "1 selection, not yet added"
   in text, and "Add ROI" gives no confirmation beyond a row appearing in a
   sidebar list the user may not be looking at.
4. **Everything interesting is collapsed.** Annotations, Agent console,
   Analysis and TCGA are all accordions. A first-time user sees a viewer and
   a toolbar and cannot tell the app has an analysis stack at all.
5. **No slide-level orientation.** The navigator shows where you are, but
   there is no thumbnail-level view of *what has been annotated* across the
   whole slide.

Proposed, in value order:

- **A status strip under the viewer**: current selection (kind + size in µm),
  ROI count, last agent action. One line, always visible, replacing the
  guesswork.
- **Reorganise the toolbar**: tool (pan / box / lasso / measure) on the left
  as a segmented control, view controls (fit, zoom readout, arrows) collapsed
  to the right, actions (Add ROI, Send) pinned right. Fewer, larger targets.
- **Promote Analysis out of the accordion** when a slide is open, even if
  only as a one-line "Analyze selection" affordance next to the status strip.
- **ROI overlay on the navigator**, so annotated regions are visible at slide
  level.

---

## Statistics

`roi_stats` today returns mean RGB, H&E deconvolution means and tissue
fraction for one ROI. That is per-ROI and point-in-time; there is no way to
compare ROIs, see a distribution, or characterise a slide.

Worth building, cheapest first:

1. **A comparison table across ROIs.** Every ROI in the current slide as a
   row, with its stats and label. This is a query and a table — no new maths,
   and it turns single measurements into evidence.
2. **Export stats as CSV/JSON.** Already true for annotations; extend it to
   the computed statistics so results can leave the tool.
3. **Slide-level summary.** Tissue area in mm², ROI count by label, nuclei
   density distribution across the grid we already compute for heatmaps.
4. **Per-label aggregates with dispersion.** Mean ± SD of each statistic by
   label, which is what makes "tumour vs stroma differ on H density" a claim
   rather than an impression.
5. **Uncertainty on the model probability heatmap.** We show probability but
   nothing about confidence, and that is exactly where a pathologist should
   be sceptical.

Items 1–3 are days, not weeks, and all reuse data already in the database.

---

## Sequence

1. Bug rounds 02–06 land and the tree is green. *(in progress)*
2. Tier 1.1 — shape-faithful export. Small, unblocks the rest.
3. Tier 1.2 / 1.3 — GeoJSON and ASAP XML import, with the round-trip corpus.
4. Tier 1.4 — DICOM `SlideSource`.
5. Statistics 1–2 — comparison table and export.
6. UI — status strip and toolbar reorganisation.
7. Tier 2a — Ruifrok and Vahadane, in-tree.
8. Re-evaluate 2b and tier 3 against real usage, not speculation.
