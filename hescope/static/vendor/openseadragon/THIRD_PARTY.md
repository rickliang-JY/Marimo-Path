# Vendored third-party code

## OpenSeadragon 5.0.1

- Files: `openseadragon.min.js` (277,226 bytes), `LICENSE.txt`
- Upstream: <https://openseadragon.github.io/> — npm tarball `openseadragon-5.0.1.tgz`,
  file `package/build/openseadragon/openseadragon.min.js`
- Build stamp (from the file header): `Built on 2024-12-09`, git commit
  `v5.0.1-0-480de92d`
- License: BSD-3-Clause (see `LICENSE.txt`, reproduced verbatim from the tarball)

### Why it is vendored rather than fetched from a CDN

HE-Scope must run fully offline: pathology slides are frequently handled on
air-gapped or restricted workstations, and a viewer that silently degrades when
the network is unavailable is not usable in that setting. The library is inlined
into the anywidget `_esm` bundle by `hescope.osdviewer.build_esm()`, so the
browser makes **zero** external requests to render the viewer.

The `.map` file and the ~40 navigation-button sprite PNGs from the upstream
tarball are intentionally NOT vendored: the widget sets `prefixUrl: ""`,
`showNavigationControl: false` and `showNavigator: false`, so no sprite is ever
requested, and a source map that is never served would only add 337 KB.

### Updating

1. Download the npm tarball for the new version.
2. Copy `package/build/openseadragon/openseadragon.min.js` and
   `package/LICENSE.txt` here.
3. Bump `OSD_VERSION` in `hescope/osdviewer.py`.
4. Run `pytest tests/test_osdviewer.py` — `test_vendored_osd_version_matches`
   checks that the constant and the file header agree, and
   `test_build_esm_wrapper_is_present` re-checks the UMD-wrapper assumptions
   (`build_esm()` relies on the bundle ending in a UMD footer that calls
   `this`, which is `undefined` in an ES module).
