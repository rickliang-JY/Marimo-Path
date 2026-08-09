"""Tests for the parallel (ranged) fast download path in hescope.tcga.

ALL offline: the GDC /data endpoint is faked at the requests layer with a
range-aware stub serving a deterministic payload.
"""

from __future__ import annotations

import hashlib
import random
import re
import sqlite3
import threading

import pytest

from hescope.tcga import (
    GDCClient,
    SlideCatalog,
    SlideRecord,
    _record_from_hit,
    _resolve_workers,
)

# deterministic ~16MB payload: above _PARALLEL_MIN_BYTES so the parallel
# fast path is exercised, and not a multiple of chunk / worker count
PAYLOAD = random.Random(20240808).randbytes(16 * 1024 * 1024 + 123)
FILE_NAME = "fake-slide.svs"


# --------------------------------------------------------------------------
# fake ranged endpoint
# --------------------------------------------------------------------------


class FakeResp:
    def __init__(self, body: bytes, status: int, headers: dict):
        self._body = body
        self.status_code = status
        self.headers = headers

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests as _req

            raise _req.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=4096):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        pass


def make_ranged_get(payload: bytes = PAYLOAD, file_name: str = FILE_NAME):
    """requests.get stand-in honoring 'Range: bytes=a-b' (206 + Content-Range)
    and plain GET (200, whole body). Records every call."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None, stream=False, **kw):
        headers = headers or {}
        rng = headers.get("Range")
        calls.append({"url": url, "range": rng})
        base = {"Content-Disposition": f'attachment; filename="{file_name}"'}
        if rng:
            m = re.fullmatch(r"bytes=(\d+)-(\d+)", rng)
            assert m, f"unparseable Range header: {rng!r}"
            start, end = int(m.group(1)), int(m.group(2))
            body = payload[start : end + 1]
            h = dict(base)
            h["Content-Range"] = f"bytes {start}-{end}/{len(payload)}"
            h["Content-Length"] = str(len(body))
            return FakeResp(body, 206, h)
        h = dict(base)
        h["Content-Length"] = str(len(payload))
        return FakeResp(payload, 200, h)

    fake_get.calls = calls
    return fake_get


def _progress_sink():
    lock = threading.Lock()
    seen = []

    def cb(done, total):
        with lock:
            seen.append((done, total))

    return seen, cb


# --------------------------------------------------------------------------
# parallel fast path
# --------------------------------------------------------------------------


def test_parallel_download_exact_bytes(monkeypatch, tmp_path):
    fake = make_ranged_get()
    monkeypatch.setattr("hescope.tcga.requests.get", fake)
    client = GDCClient()
    seen, cb = _progress_sink()

    dest = client.download_slide("fid-fast", tmp_path, progress_cb=cb, workers=4)

    assert dest == tmp_path / FILE_NAME
    assert dest.read_bytes() == PAYLOAD  # byte-identical reassembly
    # progress: monotonically non-decreasing, ends at (total, total)
    dones = [d for d, _ in seen]
    assert dones == sorted(dones)
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert all(t == len(PAYLOAD) for _, t in seen)
    # .part cleaned up after success
    assert not (tmp_path / (FILE_NAME + ".part")).exists()
    # ranged path really used: probe + 4 range requests, no plain GET
    assert fake.calls[0]["range"] == "bytes=0-0"
    ranged = [c for c in fake.calls if c["range"]]
    assert len(ranged) == 5
    spans = []
    for c in ranged[1:]:
        m = re.fullmatch(r"bytes=(\d+)-(\d+)", c["range"])
        spans.append((int(m.group(1)), int(m.group(2))))
    spans.sort()
    assert spans[0][0] == 0 and spans[-1][1] == len(PAYLOAD) - 1
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert s2 == e1 + 1  # contiguous, disjoint coverage


def test_workers1_forces_single_stream(monkeypatch, tmp_path):
    fake = make_ranged_get()
    monkeypatch.setattr("hescope.tcga.requests.get", fake)
    client = GDCClient()
    seen, cb = _progress_sink()

    dest = client.download_slide("fid-one", tmp_path, progress_cb=cb, workers=1)

    assert dest.read_bytes() == PAYLOAD
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    # no Range header was ever sent: pure legacy single-stream
    assert all(c["range"] is None for c in fake.calls)
    assert len(fake.calls) == 1


def test_worker_failure_falls_back_to_single_stream(monkeypatch, tmp_path):
    fake = make_ranged_get()
    real_fake = fake

    def flaky_get(url, params=None, headers=None, timeout=None, stream=False, **kw):
        rng = (headers or {}).get("Range")
        if rng and rng != "bytes=0-0":
            import requests as _req

            raise _req.ConnectionError("boom mid-range")
        return real_fake(url, params=params, headers=headers, timeout=timeout)

    monkeypatch.setattr("hescope.tcga.requests.get", flaky_get)
    client = GDCClient()
    dest = client.download_slide("fid-flaky", tmp_path, workers=4)

    assert dest.read_bytes() == PAYLOAD  # legacy fallback produced the file
    assert not (tmp_path / (FILE_NAME + ".part")).exists()


def test_expected_md5_ok(monkeypatch, tmp_path):
    monkeypatch.setattr("hescope.tcga.requests.get", make_ranged_get())
    client = GDCClient()
    good = hashlib.md5(PAYLOAD).hexdigest()
    dest = client.download_slide("fid-md5", tmp_path, workers=4, expected_md5=good)
    assert dest.read_bytes() == PAYLOAD


def test_expected_md5_wrong_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("hescope.tcga.requests.get", make_ranged_get())
    client = GDCClient()
    bad = "0" * 32
    with pytest.raises(IOError, match="md5 mismatch"):
        client.download_slide("fid-md5bad", tmp_path, workers=4, expected_md5=bad)
    # nothing left behind: no final file, no .part
    assert not (tmp_path / FILE_NAME).exists()
    assert not (tmp_path / (FILE_NAME + ".part")).exists()


def test_expected_md5_wrong_raises_legacy(monkeypatch, tmp_path):
    monkeypatch.setattr("hescope.tcga.requests.get", make_ranged_get())
    client = GDCClient()
    with pytest.raises(IOError, match="md5 mismatch"):
        client.download_slide("fid-md5bad1", tmp_path, workers=1, expected_md5="f" * 32)


def test_legacy_skip_if_complete_still_works(monkeypatch, tmp_path):
    fake = make_ranged_get()
    monkeypatch.setattr("hescope.tcga.requests.get", fake)
    dest_dir = tmp_path / "fid-done"
    dest_dir.mkdir()
    (dest_dir / FILE_NAME).write_bytes(PAYLOAD)
    client = GDCClient()
    seen, cb = _progress_sink()

    # workers=4: probe happens (for the size check) but nothing is downloaded
    dest = client.download_slide("fid-done", dest_dir, progress_cb=cb, workers=4)
    assert dest.read_bytes() == PAYLOAD
    assert seen == [(len(PAYLOAD), len(PAYLOAD))]
    assert all(c["range"] == "bytes=0-0" for c in fake.calls)  # probe only
    assert not (dest_dir / (FILE_NAME + ".part")).exists()

    # workers=1: legacy skip via Content-Length
    fake.calls.clear()
    dest = client.download_slide("fid-done", dest_dir, workers=1)
    assert dest.read_bytes() == PAYLOAD
    assert all(c["range"] is None for c in fake.calls)


def test_stale_part_is_restarted_not_resumed(monkeypatch, tmp_path):
    fake = make_ranged_get()
    monkeypatch.setattr("hescope.tcga.requests.get", fake)
    (tmp_path / (FILE_NAME + ".part")).write_bytes(b"stale-garbage")
    client = GDCClient()
    dest = client.download_slide("fid-stale", tmp_path, workers=4)
    assert dest.read_bytes() == PAYLOAD
    assert not (tmp_path / (FILE_NAME + ".part")).exists()


# --------------------------------------------------------------------------
# worker count resolution (env var)
# --------------------------------------------------------------------------


def test_resolve_workers_env(monkeypatch):
    monkeypatch.setenv("HESCOPE_DL_WORKERS", "3")
    assert _resolve_workers(None) == 3
    monkeypatch.setenv("HESCOPE_DL_WORKERS", "garbage")
    assert _resolve_workers(None) == 8
    monkeypatch.setenv("HESCOPE_DL_WORKERS", "99")
    assert _resolve_workers(None) == 16  # clamped
    monkeypatch.setenv("HESCOPE_DL_WORKERS", "0")
    assert _resolve_workers(None) == 1  # clamped
    monkeypatch.delenv("HESCOPE_DL_WORKERS")
    assert _resolve_workers(None) == 8  # default
    assert _resolve_workers(5) == 5  # explicit wins over env


def test_env_workers_drive_parallel_download(monkeypatch, tmp_path):
    fake = make_ranged_get()
    monkeypatch.setattr("hescope.tcga.requests.get", fake)
    monkeypatch.setenv("HESCOPE_DL_WORKERS", "3")
    client = GDCClient()
    dest = client.download_slide("fid-env", tmp_path)
    assert dest.read_bytes() == PAYLOAD
    ranged = [c for c in fake.calls if c["range"]]
    assert len(ranged) == 1 + 3  # probe + 3 ranges


# --------------------------------------------------------------------------
# md5sum metadata: SlideRecord parsing + catalog column
# --------------------------------------------------------------------------


def test_record_from_hit_parses_md5sum():
    hit = {
        "file_id": "fid-x",
        "file_name": "x.svs",
        "file_size": 42,
        "md5sum": "abcdef0123456789abcdef0123456789",
        "cases": [
            {
                "submitter_id": "TCGA-XX",
                "project": {"project_id": "TCGA-BRCA", "primary_site": "Breast"},
                "samples": [{"sample_type": "Primary Tumor"}],
            }
        ],
    }
    rec = _record_from_hit(hit)
    assert rec.md5sum == "abcdef0123456789abcdef0123456789"
    # old payloads without md5sum still parse (additive field)
    hit.pop("md5sum")
    assert _record_from_hit(hit).md5sum is None


def test_search_slides_requests_and_parses_md5sum(monkeypatch):
    payload = {
        "data": {
            "hits": [
                {
                    "file_id": "fid-m",
                    "file_name": "m.svs",
                    "file_size": 7,
                    "md5sum": "1" * 32,
                    "cases": [],
                }
            ],
            "pagination": {"total": 1},
        }
    }
    calls = []

    class Resp:
        status_code = 200

        def json(self):
            return payload

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, timeout=None, **kw):
        calls.append(params)
        return Resp()

    monkeypatch.setattr("hescope.tcga.requests.get", fake_get)
    records, total = GDCClient().search_slides()
    assert total == 1
    assert records[0].md5sum == "1" * 32
    assert "md5sum" in calls[0]["fields"]


def _make_old_schema_db(path):
    """Create a catalog db with the pre-md5sum schema, by hand."""
    con = sqlite3.connect(str(path))
    con.execute(
        """
        CREATE TABLE tcga_slides (
            file_id TEXT PRIMARY KEY,
            file_name TEXT,
            file_size INTEGER,
            project_id TEXT,
            case_submitter_id TEXT,
            sample_type TEXT,
            primary_site TEXT,
            local_path TEXT,
            downloaded_at TEXT,
            first_seen_at TEXT
        )
        """
    )
    con.execute(
        "INSERT INTO tcga_slides VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("old-fid", "old.svs", 11, "TCGA-BRCA", "TCGA-OO", "Primary Tumor",
         "Breast", None, None, "2024-01-01T00:00:00+00:00"),
    )
    con.commit()
    con.close()


def test_catalog_alters_existing_db(tmp_path):
    db = tmp_path / "old_catalog.db"
    _make_old_schema_db(db)

    cat = SlideCatalog(db)  # must not raise on the old schema
    con = sqlite3.connect(str(db))
    cols = {row[1] for row in con.execute("PRAGMA table_info(tcga_slides)")}
    con.close()
    assert "md5sum" in cols

    # pre-existing rows read back with md5sum=None
    recs = cat.search()
    assert [r.file_id for r in recs] == ["old-fid"]
    assert recs[0].md5sum is None

    # new records round-trip their md5sum
    cat.upsert(
        [
            SlideRecord(
                "new-fid", "n.svs", 5, "TCGA-LUAD", "TCGA-NN",
                "Primary Tumor", "Lung", md5sum="2" * 32,
            )
        ]
    )
    by_id = {r.file_id: r for r in cat.search()}
    assert by_id["new-fid"].md5sum == "2" * 32
    assert by_id["old-fid"].md5sum is None


def test_catalog_md5sum_roundtrip_fresh_db(tmp_path):
    cat = SlideCatalog(tmp_path / "fresh.db")
    cat.upsert(
        [
            SlideRecord(
                "a", "a.svs", 1, "TCGA-BRCA", "TCGA-AA",
                "Primary Tumor", "Breast", md5sum="3" * 32,
            )
        ]
    )
    assert cat.search()[0].md5sum == "3" * 32


# --------------------------------------------------------------------------
# download retries (flaky upstream)
# --------------------------------------------------------------------------

import requests as _requests

from hescope.tcga import _PARALLEL_MIN_BYTES, _split_ranges
import hescope.tcga as _tcga_mod


class BrokenResp(FakeResp):
    """Streams the body, then dies mid-transfer like an IncompleteRead."""

    def iter_content(self, chunk_size=4096):
        yield from super().iter_content(chunk_size)
        raise _requests.exceptions.ChunkedEncodingError(
            "simulated IncompleteRead"
        )


def _no_sleep(monkeypatch):
    """Patch out backoff sleeps (keeps retry tests instant)."""
    monkeypatch.setattr(
        "hescope.tcga._retry_sleep", lambda attempt, retry_after=None: None
    )


def _parse_range(rng):
    m = re.fullmatch(r"bytes=(\d+)-(\d+)", rng)
    return int(m.group(1)), int(m.group(2))


def test_flaky_range_resumes_remaining_subrange(monkeypatch, tmp_path):
    """Every worker's first range attempt breaks mid-stream; the retry must
    re-request only the remaining sub-range and append at the right offset."""
    _no_sleep(monkeypatch)
    workers = 4
    cut = 300_000  # bytes served before the connection "breaks"
    orig_starts = {s for s, _ in _split_ranges(len(PAYLOAD), workers)}
    broken = set()
    state_lock = threading.Lock()
    base = make_ranged_get()

    def flaky_get(url, params=None, headers=None, timeout=None, stream=False, **kw):
        rng = (headers or {}).get("Range")
        if rng and rng != "bytes=0-0":
            start, end = _parse_range(rng)
            with state_lock:
                first = start in orig_starts and start not in broken
                if first:
                    broken.add(start)
            if first:
                resp = base(url, params=params, headers={
                    "Range": f"bytes={start}-{start + cut - 1}"}, timeout=timeout)
                return BrokenResp(resp._body, resp.status_code, resp.headers)
        return base(url, params=params, headers=headers, timeout=timeout)

    monkeypatch.setattr("hescope.tcga.requests.get", flaky_get)
    client = GDCClient()
    seen, cb = _progress_sink()

    dest = client.download_slide("fid-flaky-range", tmp_path, progress_cb=cb,
                                 workers=workers)

    assert dest.read_bytes() == PAYLOAD  # byte-exact despite mid-stream breaks
    # progress: monotonic, and the final total equals the file size exactly
    # (a retried sub-range must never report the same byte twice)
    dones = [d for d, _ in seen]
    assert dones == sorted(dones)
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    # every worker re-requested exactly its remaining sub-range
    requested = {_parse_range(c["range"]) for c in base.calls
                 if c["range"] and c["range"] != "bytes=0-0"}
    for s in orig_starts:
        assert (s + cut, max(e for ss, e in _split_ranges(len(PAYLOAD), workers)
                             if ss == s)) in requested


def test_range_retry_honors_retry_after_on_429(monkeypatch, tmp_path):
    """A 429 with Retry-After on the first range attempt is retried after
    (at least) the server-mandated wait, then succeeds."""
    real_retry_sleep = _tcga_mod._retry_sleep
    recorded = []
    monkeypatch.setattr(
        "hescope.tcga._retry_sleep",
        lambda attempt, retry_after=None: recorded.append((attempt, retry_after)),
    )
    base = make_ranged_get()
    throttled = set()
    state_lock = threading.Lock()

    def throttling_get(url, params=None, headers=None, timeout=None, stream=False, **kw):
        rng = (headers or {}).get("Range")
        if rng and rng != "bytes=0-0":
            start, _ = _parse_range(rng)
            with state_lock:
                first = start not in throttled
                if first:
                    throttled.add(start)
            if first:
                return FakeResp(b"", 429, {"Retry-After": "7"})
        return base(url, params=params, headers=headers, timeout=timeout)

    monkeypatch.setattr("hescope.tcga.requests.get", throttling_get)
    client = GDCClient()
    dest = client.download_slide("fid-429", tmp_path, workers=2)

    assert dest.read_bytes() == PAYLOAD
    # each worker's retry carried the Retry-After hint from the 429
    assert (0, 7.0) in recorded

    # the backoff computation itself: exponential, Retry-After can only raise
    sleeps = []
    monkeypatch.setattr(_tcga_mod.time, "sleep", sleeps.append)
    real_retry_sleep(0)
    real_retry_sleep(1)
    real_retry_sleep(2)
    real_retry_sleep(1, 5.0)   # hint above the computed wait wins
    real_retry_sleep(2, 0.1)   # hint below it does not lower the wait
    assert sleeps == [0.5, 1.0, 2.0, 5.0, 2.0]


def test_legacy_retries_chunked_encoding_errors(monkeypatch, tmp_path):
    """Legacy single-stream: two mid-stream breaks, third attempt succeeds;
    each attempt restarts the .part from scratch."""
    _no_sleep(monkeypatch)
    base = make_ranged_get()
    attempts = {"n": 0}

    def flaky_get(url, params=None, headers=None, timeout=None, stream=False, **kw):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            # dies after 2MB while claiming the full Content-Length
            resp = base(url, params=params, headers=headers, timeout=timeout)
            return BrokenResp(PAYLOAD[: 2 * 1024 * 1024], 200, resp.headers)
        return base(url, params=params, headers=headers, timeout=timeout)

    monkeypatch.setattr("hescope.tcga.requests.get", flaky_get)
    client = GDCClient()
    seen, cb = _progress_sink()

    dest = client.download_slide("fid-legacy-flaky", tmp_path, progress_cb=cb,
                                 workers=1)

    assert attempts["n"] == 3
    assert dest.read_bytes() == PAYLOAD  # byte-exact final attempt
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    # no double counting across attempts: every attempt reports its own
    # per-attempt progress, the last one ending exactly at the file size
    assert max(d for d, _ in seen) == len(PAYLOAD)
    assert not (tmp_path / (FILE_NAME + ".part")).exists()


def test_all_attempts_fail_raises_ioerror_and_cleans_part(monkeypatch, tmp_path):
    """Parallel workers exhaust their retries -> legacy fallback also
    exhausts its retries -> IOError; nothing is left behind."""
    _no_sleep(monkeypatch)
    base = make_ranged_get()

    def dead_get(url, params=None, headers=None, timeout=None, stream=False, **kw):
        rng = (headers or {}).get("Range")
        if rng is None:
            raise _requests.exceptions.ChunkedEncodingError("dead stream")
        if rng != "bytes=0-0":
            raise _requests.exceptions.ConnectionError("dead connection")
        return base(url, params=params, headers=headers, timeout=timeout)

    monkeypatch.setattr("hescope.tcga.requests.get", dead_get)
    client = GDCClient()

    with pytest.raises(IOError, match="failed after 3 attempts"):
        client.download_slide("fid-dead", tmp_path, workers=4)
    assert not (tmp_path / FILE_NAME).exists()
    assert not (tmp_path / (FILE_NAME + ".part")).exists()


def test_small_file_skips_parallel_path(monkeypatch, tmp_path):
    """Files below _PARALLEL_MIN_BYTES go straight to the legacy
    single-stream path even with workers=8 (probe still runs)."""
    small = random.Random(7).randbytes(6_500_000)  # ~6.5MB < 16MB
    assert len(small) < _PARALLEL_MIN_BYTES
    fake = make_ranged_get(payload=small)
    monkeypatch.setattr("hescope.tcga.requests.get", fake)
    client = GDCClient()
    seen, cb = _progress_sink()

    dest = client.download_slide("fid-small", tmp_path, progress_cb=cb, workers=8)

    assert dest.read_bytes() == small
    assert seen[-1] == (len(small), len(small))
    # exactly the 0-0 probe plus one plain GET: no parallel range fetches
    ranges = [c["range"] for c in fake.calls]
    assert ranges == ["bytes=0-0", None]
