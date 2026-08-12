"""Tests for hescope.tcga and the TifffileSource backend.

NO network access: all requests to the GDC API are mocked. The canned
/files response in fixtures/gdc_files_response.json is a real GDC API
payload (TCGA-BRCA, 3 hits).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image

from hescope import slides as slides_mod
from hescope.slides import SlideSource, TifffileSource, open_slide
from hescope.tcga import GDCClient, SlideCatalog, SlideRecord

FIXTURE = Path(__file__).parent / "fixtures" / "gdc_files_response.json"


# --------------------------------------------------------------------------
# GDC API mocking helpers
# --------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, payload=None, content: bytes = b"", headers=None):
        self._payload = payload
        self._content = content
        self.headers = headers or {}
        self.status_code = 200
        self.raise_calls = 0

    def json(self):
        return self._payload

    def raise_for_status(self):
        self.raise_calls += 1

    def iter_content(self, chunk_size=4096):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]

    def close(self):
        pass


@pytest.fixture()
def gdc_payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture()
def mock_search(monkeypatch, gdc_payload):
    """Monkeypatch requests.get so search_slides returns the fixture."""
    calls = []

    def fake_get(url, params=None, timeout=None, stream=False):
        calls.append({"url": url, "params": params})
        return FakeResponse(payload=gdc_payload)

    monkeypatch.setattr("hescope.tcga.requests.get", fake_get)
    return calls


# --------------------------------------------------------------------------
# GDCClient.search_slides
# --------------------------------------------------------------------------


def test_search_slides_parses_fixture(mock_search):
    client = GDCClient()
    records, total = client.search_slides(project_id="TCGA-BRCA", size=3)
    assert total == 3112  # pagination.total from the real response
    assert len(records) == 3

    r0 = records[0]
    assert isinstance(r0, SlideRecord)
    assert r0.file_id == "495ab2ae-0286-4d87-8c7b-4d4af7eded01"
    assert r0.file_name.endswith(".svs")
    assert r0.file_size == 217303029
    assert r0.project_id == "TCGA-BRCA"
    assert r0.case_submitter_id == "TCGA-BH-A18H"
    assert r0.sample_type == "Primary Tumor"
    assert r0.primary_site == "Breast"
    assert r0.local_path is None

    # the request went to the /files endpoint with a Slide Image filter
    call = mock_search[0]
    assert call["url"].endswith("/files")
    filters = json.loads(call["params"]["filters"])
    fields = {
        c["content"]["field"]
        for c in filters["content"]
        if c["op"] == "="
    }
    assert "files.data_type" in fields
    assert "cases.project.project_id" in fields  # project filter applied


def test_search_slides_no_filters(mock_search):
    client = GDCClient()
    records, total = client.search_slides()
    filters = json.loads(mock_search[0]["params"]["filters"])
    assert len(filters["content"]) == 1  # only the data_type clause
    assert total == 3112


def test_search_slides_sample_type_filter(mock_search):
    client = GDCClient()
    client.search_slides(sample_type="Primary Tumor")
    filters = json.loads(mock_search[0]["params"]["filters"])
    fields = {c["content"]["field"] for c in filters["content"]}
    assert "cases.samples.sample_type" in fields


# --------------------------------------------------------------------------
# GDCClient.download_slide
# --------------------------------------------------------------------------


def _download_response(data: bytes, name: str) -> FakeResponse:
    return FakeResponse(
        content=data,
        headers={
            "Content-Length": str(len(data)),
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


def test_download_slide_writes_file(monkeypatch, tmp_path):
    payload = b"fake-svs-bytes" * 100
    monkeypatch.setattr(
        "hescope.tcga.requests.get",
        lambda *a, **k: _download_response(payload, "slide.svs"),
    )
    client = GDCClient()
    progress = []
    dest = client.download_slide(
        "fid-1", tmp_path, progress_cb=lambda d, t: progress.append((d, t))
    )
    assert dest == tmp_path / "slide.svs"
    assert dest.read_bytes() == payload
    assert progress  # progress_cb was called
    assert progress[-1] == (len(payload), len(payload))


def test_download_slide_skips_when_complete(monkeypatch, tmp_path):
    payload = b"already-here" * 50
    dest_dir = tmp_path / "fid-2"
    dest_dir.mkdir()
    (dest_dir / "slide.svs").write_bytes(payload)

    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return _download_response(payload, "slide.svs")

    monkeypatch.setattr("hescope.tcga.requests.get", fake_get)
    client = GDCClient()
    dest = client.download_slide("fid-2", dest_dir)
    assert dest.read_bytes() == payload
    # server was still consulted for headers, but no .part file remains and
    # the file was not rewritten
    assert not (dest_dir / "slide.svs.part").exists()


def test_download_slide_http_error_raises(monkeypatch, tmp_path):
    import requests as _requests

    class BoomResponse(FakeResponse):
        def raise_for_status(self):
            raise _requests.HTTPError("404")

    monkeypatch.setattr(
        "hescope.tcga.requests.get", lambda *a, **k: BoomResponse()
    )
    client = GDCClient()
    with pytest.raises(_requests.HTTPError):
        client.download_slide("missing", tmp_path)


# --------------------------------------------------------------------------
# SlideCatalog
# --------------------------------------------------------------------------


def _records() -> list[SlideRecord]:
    return [
        SlideRecord("a", "a.svs", 100, "TCGA-BRCA", "TCGA-AA", "Primary Tumor", "Breast"),
        SlideRecord("b", "b.svs", 200, "TCGA-BRCA", "TCGA-BB", "Solid Tissue Normal", "Breast"),
        SlideRecord("c", "c.svs", 300, "TCGA-LUAD", "TCGA-CC", "Primary Tumor", "Lung"),
    ]


def test_catalog_upsert_dedup(tmp_path):
    cat = SlideCatalog(tmp_path / "cat.db")
    assert cat.upsert(_records()) == 3
    assert cat.upsert(_records()) == 0  # duplicates ignored
    assert cat.stats()["total"] == 3


def test_catalog_mark_downloaded_and_search(tmp_path):
    cat = SlideCatalog(tmp_path / "cat.db")
    cat.upsert(_records())
    cat.mark_downloaded("a", "/data/tcga/a/a.svs")

    downloaded = cat.search(downloaded_only=True)
    assert [r.file_id for r in downloaded] == ["a"]
    assert downloaded[0].local_path == "/data/tcga/a/a.svs"

    brca = cat.search(project_id="TCGA-BRCA")
    assert {r.file_id for r in brca} == {"a", "b"}

    # upserting again must not clobber the download state
    cat.upsert(_records())
    assert cat.search(downloaded_only=True)[0].local_path == "/data/tcga/a/a.svs"


def test_catalog_stats(tmp_path):
    cat = SlideCatalog(tmp_path / "cat.db")
    cat.upsert(_records())
    cat.mark_downloaded("c", "/data/tcga/c/c.svs")
    stats = cat.stats()
    assert stats["total"] == 3
    assert stats["downloaded"] == 1
    assert stats["projects"] == {"TCGA-BRCA": 2, "TCGA-LUAD": 1}


# --------------------------------------------------------------------------
# TifffileSource
# --------------------------------------------------------------------------

APERIO_DESC = (
    "Aperio Image Library v12.0.16\n"
    "123456x78901 [0,0 512x512] (128x128) RGB|AppMag = 20|MPP = 0.4942"
)


@pytest.fixture()
def svs_path(tmp_path):
    """Synthetic Aperio-style tiled pyramidal TIFF (SVS layout):
    page0 baseline 512x512 tiled, page1 thumbnail, pages 2-3 pyramid
    levels 256x256 / 128x128 (tiled, uncompressed)."""
    rng = np.random.default_rng(7)
    base = rng.integers(0, 255, size=(512, 512, 3), dtype=np.uint8)
    base[0:100, 0:100] = (255, 0, 0)  # red marker block, level-0 coords
    p = tmp_path / "synthetic.svs"
    with tifffile.TiffWriter(p, bigtiff=True) as tif:
        tif.write(
            base,
            tile=(128, 128),
            photometric="rgb",
            description=APERIO_DESC,
            metadata=None,
        )
        tif.write(base[::4, ::4], photometric="rgb", subfiletype=1, metadata=None)
        tif.write(base[::2, ::2], tile=(128, 128), photometric="rgb", metadata=None)
        tif.write(base[::4, ::4], tile=(128, 128), photometric="rgb", metadata=None)
    return p, base


def test_tifffilesource_pyramid(svs_path):
    path, _ = svs_path
    src = TifffileSource(path)
    assert src.name == "synthetic.svs"
    assert src.dimensions == (512, 512)
    assert src.level_count == 3
    assert src.level_downsamples == (1.0, 2.0, 4.0)
    assert src.mpp == pytest.approx(0.4942)
    assert isinstance(src, SlideSource)
    src.close()


def test_tifffilesource_region_reads(svs_path):
    path, base = svs_path
    src = TifffileSource(path)
    img = src.read_region((0, 0), 0, (100, 100))
    assert img.size == (100, 100)
    assert (np.asarray(img) == (255, 0, 0)).all()  # inside red marker

    # arbitrary region matches the source array exactly
    img2 = src.read_region((200, 150), 0, (64, 48))
    assert (np.asarray(img2) == base[150:198, 200:264]).all()

    # level-1 read: location given in level-0 coordinates
    img3 = src.read_region((0, 0), 1, (50, 50))  # 50 level-1 px = 100 level-0 px
    assert (np.asarray(img3) == base[0:100:1, 0:100:1][::2, ::2][0:50, 0:50]).all()
    src.close()


def test_tifffilesource_clip_and_pad(svs_path):
    path, base = svs_path
    src = TifffileSource(path)
    # region extends past the bottom-right edge -> white padding
    img = src.read_region((500, 500), 0, (50, 50))
    assert img.size == (50, 50)
    arr = np.asarray(img)
    assert (arr[:12, :12] == base[500:512, 500:512]).all()
    assert (arr[12:, :] == 255).all()
    assert (arr[:, 12:] == 255).all()
    # negative location -> white padding on the top/left
    img2 = src.read_region((-50, -50), 0, (100, 100))
    arr2 = np.asarray(img2)
    assert (arr2[10, 10] == (255, 255, 255)).all()
    assert (arr2[60, 60] == base[10, 10]).all()
    src.close()


def test_tifffilesource_thumbnail(svs_path):
    path, _ = svs_path
    src = TifffileSource(path)
    thumb = src.get_thumbnail((128, 128))
    assert thumb.size[0] <= 128 and thumb.size[1] <= 128
    assert thumb.mode == "RGB"
    src.close()


def test_tifffilesource_no_mpp(tmp_path):
    p = tmp_path / "plain.svs"
    arr = np.zeros((256, 256, 3), dtype=np.uint8)
    with tifffile.TiffWriter(p) as tif:
        tif.write(arr, tile=(128, 128), photometric="rgb", metadata=None)
    src = TifffileSource(p)
    assert src.mpp is None
    src.close()


def test_open_slide_routes_svs_to_tifffile(svs_path, monkeypatch):
    monkeypatch.setattr(slides_mod, "_HAS_OPENSLIDE", False)
    path, _ = svs_path
    src = open_slide(path)
    assert isinstance(src, TifffileSource)
    src.close()


# --- an already-downloaded slide must open offline -------------------------


def test_local_file_finds_a_downloaded_slide(tmp_path):
    """The catalog already records where the slide landed; asking it must not
    require the network.

    download_slide cannot answer this question: it probes the server with a
    Range request before it can decide the local file is complete, so with no
    network it exhausts its retry budget and raises over a slide sitting on
    disk. That made "Download & Open" -- the only route to a TCGA slide in the
    UI -- fail offline for a slide already downloaded.
    """
    from hescope.tcga import SlideCatalog, SlideRecord

    cat = SlideCatalog(tmp_path / "catalog.db")
    cat.upsert([
        SlideRecord(
            file_id="fid-1", file_name="a.svs", file_size=10,
            project_id="TCGA-BRCA", case_submitter_id="C1",
            sample_type="Primary Tumor", primary_site="Breast",
            local_path=None, md5sum=None,
        )
    ])
    assert cat.local_file("fid-1") is None          # known, never downloaded
    assert cat.local_file("no-such-id") is None     # unknown id

    slide = tmp_path / "a.svs"
    slide.write_bytes(b"x" * 10)
    cat.mark_downloaded("fid-1", str(slide))
    assert cat.local_file("fid-1") == slide


def test_local_file_ignores_a_stale_path(tmp_path):
    """A recorded path whose file was moved or deleted must not be reported as
    openable -- the caller would hand it to open_slide and get an error it
    cannot explain."""
    from hescope.tcga import SlideCatalog, SlideRecord

    cat = SlideCatalog(tmp_path / "catalog.db")
    cat.upsert([
        SlideRecord(
            file_id="fid-2", file_name="b.svs", file_size=10,
            project_id="TCGA-LUAD", case_submitter_id="C2",
            sample_type=None, primary_site=None,
            local_path=None, md5sum=None,
        )
    ])
    gone = tmp_path / "moved-away.svs"
    gone.write_bytes(b"y" * 10)
    cat.mark_downloaded("fid-2", str(gone))
    assert cat.local_file("fid-2") == gone
    gone.unlink()
    assert cat.local_file("fid-2") is None

    # a directory is not an openable slide either
    d = tmp_path / "a-directory"
    d.mkdir()
    cat.mark_downloaded("fid-2", str(d))
    assert cat.local_file("fid-2") is None


def test_opening_a_downloaded_slide_needs_no_network(tmp_path):
    """End to end: with the client pointed at an unroutable host, the catalog
    path still resolves instantly while download_slide fails."""
    import time

    import pytest as _pytest

    from hescope.tcga import GDCClient, SlideCatalog, SlideRecord

    cat = SlideCatalog(tmp_path / "catalog.db")
    cat.upsert([
        SlideRecord(
            file_id="fid-3", file_name="c.svs", file_size=4,
            project_id="TCGA-BRCA", case_submitter_id="C3",
            sample_type=None, primary_site=None, local_path=None, md5sum=None,
        )
    ])
    slide = tmp_path / "c.svs"
    slide.write_bytes(b"data")
    cat.mark_downloaded("fid-3", str(slide))

    started = time.perf_counter()
    assert cat.local_file("fid-3") == slide
    assert time.perf_counter() - started < 1.0, "the catalog lookup hit the network"

    offline = GDCClient(api_base="http://127.0.0.1:9", timeout=1)
    with _pytest.raises(Exception):
        offline.download_slide("fid-3", tmp_path)


# --------------------------------------------------------------------------
# hescope.tcga_panel.records_to_rows (BUILD-PLAN-DB.md Phase 2, defect 2.3)
# --------------------------------------------------------------------------


def test_records_to_rows_carries_md5sum():
    """records_to_rows used to omit md5sum entirely, so app.py's
    ``_sel[0].get("md5sum") or <catalog scan>`` had a permanently dead first
    branch -- every download's integrity check went through the O(n) scan
    fallback, never the table row itself."""
    from hescope.tcga_panel import records_to_rows

    rec = SlideRecord(
        file_id="a", file_name="a.svs", file_size=1, project_id="TCGA-BRCA",
        case_submitter_id="TCGA-AA", sample_type="Primary Tumor",
        primary_site="Breast", md5sum="d" * 32,
    )
    rows = records_to_rows([rec])
    assert rows[0]["md5sum"] == "d" * 32
