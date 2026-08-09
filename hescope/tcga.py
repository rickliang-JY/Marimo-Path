"""TCGA / GDC integration for HE-Scope.

Search the NCI GDC Data Portal for open-access TCGA whole-slide images
("Slide Image" data type), cache the metadata in a local SQLite catalog,
and download selected slides. No GDC token is required: all data used here
is open access.

GDC API docs: https://docs.gdc.cancer.gov/API/Users_Guide/Getting_Started/
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

GDC_API = "https://api.gdc.cancer.gov"

_DL_CHUNK = 1024 * 1024  # 1 MiB streaming chunk
_DL_WORKERS_DEFAULT = 8
_DL_WORKERS_MAX = 16

# Files smaller than this are fetched with the legacy single-stream path
# even when workers > 1: parallel range fetches pay setup overhead without
# any measurable speedup below ~16MB.
_PARALLEL_MIN_BYTES = 16 * 1024 * 1024

_RANGE_ATTEMPTS = 4  # attempts per range worker before giving up
_LEGACY_ATTEMPTS = 3  # whole-file attempts in the single-stream path
_BACKOFF_BASE_S = 0.5  # attempt i waits _BACKOFF_BASE_S * 2**i: 0.5, 1, 2, 4

# transient network failures worth retrying (mid-stream breaks included)
_RETRYABLE_EXC = (
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.Timeout,
)


class _RetryableStatusError(Exception):
    """Internal: HTTP 429/5xx response, eligible for a retry."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"HTTP {status}")


def _retry_after_seconds(headers) -> float | None:
    """Parse a numeric Retry-After header (HTTP-date forms are ignored)."""
    raw = headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except (ValueError, AttributeError):
        return None


def _retry_sleep(attempt: int, retry_after: float | None = None) -> None:
    """Backoff before retry `attempt` (0-based): 0.5s, 1s, 2s, 4s, ...
    A Retry-After hint from a 429/503 response raises (never lowers) the
    wait. Kept as a module function so tests can patch it out."""
    delay = _BACKOFF_BASE_S * (2 ** attempt)
    if retry_after is not None:
        delay = max(delay, retry_after)
    time.sleep(delay)


# full retryable set: transient network errors + transient HTTP statuses
_RETRYABLE = _RETRYABLE_EXC + (_RetryableStatusError,)


@dataclass(frozen=True)
class SlideRecord:
    file_id: str
    file_name: str
    file_size: int
    project_id: str | None  # e.g. "TCGA-BRCA"
    case_submitter_id: str | None  # e.g. "TCGA-BH-A18H"
    sample_type: str | None  # e.g. "Primary Tumor"
    primary_site: str | None
    local_path: str | None = None  # set when downloaded
    md5sum: str | None = None  # GDC-reported content md5 (hex)


def _record_from_hit(hit: dict) -> SlideRecord:
    """Build a SlideRecord from one GDC /files hit expanded with
    cases.project and cases.samples (plus cases.submitter_id field)."""
    cases = hit.get("cases") or []
    case = cases[0] if cases else {}
    project = case.get("project") or {}
    samples = case.get("samples") or []
    sample = samples[0] if samples else {}
    return SlideRecord(
        file_id=hit.get("file_id") or hit.get("id") or "",
        file_name=hit.get("file_name") or "",
        file_size=int(hit.get("file_size") or 0),
        project_id=project.get("project_id"),
        case_submitter_id=case.get("submitter_id"),
        sample_type=sample.get("sample_type"),
        primary_site=project.get("primary_site") or case.get("primary_site"),
        md5sum=hit.get("md5sum"),
    )


class GDCClient:
    """Thin client for the open-access parts of the GDC API."""

    def __init__(self, api_base: str = GDC_API, timeout: int = 30) -> None:
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def search_slides(
        self,
        project_id: str | None = None,
        sample_type: str | None = None,
        size: int = 25,
        offset: int = 0,
    ) -> tuple[list[SlideRecord], int]:
        """Query /files for data_type='Slide Image' (optionally filtered by
        project / sample type). Returns (records, total_count)."""
        clauses = [
            {
                "op": "=",
                "content": {"field": "files.data_type", "value": "Slide Image"},
            }
        ]
        if project_id:
            clauses.append(
                {
                    "op": "=",
                    "content": {
                        "field": "cases.project.project_id",
                        "value": project_id,
                    },
                }
            )
        if sample_type:
            clauses.append(
                {
                    "op": "=",
                    "content": {
                        "field": "cases.samples.sample_type",
                        "value": sample_type,
                    },
                }
            )
        params = {
            "filters": json.dumps({"op": "and", "content": clauses}),
            "fields": "file_id,file_name,file_size,md5sum,cases.submitter_id",
            "expand": "cases.project,cases.samples",
            "size": int(size),
            "from": int(offset),
        }
        resp = requests.get(
            f"{self.api_base}/files", params=params, timeout=self.timeout
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data") or {}
        hits = data.get("hits") or []
        total = int((data.get("pagination") or {}).get("total") or 0)
        return [_record_from_hit(h) for h in hits], total

    def download_slide(
        self,
        file_id: str,
        dest_dir: str | Path,
        progress_cb: Callable[[int, int | None], None] | None = None,
        *,
        workers: int | None = None,
        expected_md5: str | None = None,
    ) -> Path:
        """Download GET /data/{file_id} to dest_dir/<file_name>.

        If the file already exists and its size matches the server-reported
        total, the download is skipped (resume-safe). With workers > 1 the
        file is fetched as parallel HTTP Range requests (when the server
        supports them); any parallel-mode failure falls back to the legacy
        single-stream download (also used directly for files smaller than
        _PARALLEL_MIN_BYTES). Transient network failures are retried with
        exponential backoff in both paths. Partial downloads write to
        <file_name>.part and are only renamed to the final name after the
        size (and, if given, md5) checks pass. Raises on HTTP error; raises
        IOError on size/md5 mismatch or when every retry is exhausted.
        """
        # Contained once, at the top, so the URL, both `f"{file_id}.svs"`
        # fallbacks and the error messages below all get the safe form.
        file_id = safe_file_id(file_id)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        url = f"{self.api_base}/data/{file_id}"
        n_workers = _resolve_workers(workers)
        if n_workers > 1:
            probe = self._probe_range(url)
            if probe is not None and probe[0] < _PARALLEL_MIN_BYTES:
                # small file: parallel ranges only add overhead -> legacy
                return self._download_legacy(
                    url, file_id, dest_dir, progress_cb, expected_md5
                )
            if probe is not None:
                total, file_name = probe
                dest = dest_dir / (file_name or f"{file_id}.svs")
                if dest.exists() and dest.stat().st_size == total:
                    if progress_cb is not None:
                        progress_cb(total, total)
                    return dest
                try:
                    part = self._download_parallel(
                        url, dest, total, n_workers, progress_cb
                    )
                except Exception:
                    # parallel mode failed: drop the partial file and retry
                    # with the legacy single-stream path
                    dest.with_name(dest.name + ".part").unlink(missing_ok=True)
                else:
                    return _finalize_part(part, dest, total, expected_md5)
        return self._download_legacy(url, file_id, dest_dir, progress_cb, expected_md5)

    def _probe_range(self, url: str) -> tuple[int, str | None] | None:
        """GET with 'Range: bytes=0-0' (HEAD is not supported by GDC).
        Returns (total_size, file_name) when the server answers 206 with a
        parseable Content-Range, else None."""
        try:
            resp = requests.get(
                url,
                headers={"Range": "bytes=0-0"},
                stream=True,
                timeout=self.timeout,
            )
        except Exception:
            return None
        try:
            if resp.status_code != 206:
                return None
            m = re.match(
                r"bytes\s+\d+-\d+/(\d+)", resp.headers.get("Content-Range") or ""
            )
            if not m:
                return None
            return int(m.group(1)), _filename_from_headers(resp.headers)
        finally:
            resp.close()

    def _download_parallel(
        self,
        url: str,
        dest: Path,
        total: int,
        workers: int,
        progress_cb: Callable[[int, int | None], None] | None,
    ) -> Path:
        """Fetch [0, total) as `workers` contiguous ranges into a single
        pre-allocated .part file. Each worker writes only inside its own
        disjoint range via its own file handle, so no write locking is
        needed; progress aggregation is protected by a lock. Each range is
        retried up to _RANGE_ATTEMPTS times on transient failures
        (connection drops, chunked-encoding breaks, timeouts, HTTP
        429/5xx): a retry re-requests only the not-yet-written remainder
        of the range, with exponential backoff (a Retry-After header on
        429/503 raises the wait). If every attempt fails the worker
        raises (the caller deletes the .part and falls back)."""
        part = dest.with_name(dest.name + ".part")
        part.unlink(missing_ok=True)  # stale partials are restarted, not resumed
        with open(part, "wb") as fh:
            fh.truncate(total)
        done = 0
        lock = threading.Lock()

        def _fetch(start: int, end: int) -> None:
            nonlocal done
            pos = start
            last_err: Exception | None = None
            delay_hint: float | None = None
            for attempt in range(_RANGE_ATTEMPTS):
                if attempt:
                    _retry_sleep(attempt - 1, delay_hint)
                    delay_hint = None
                resp = None
                try:
                    # after a mid-stream break only the REMAINING sub-range
                    # is re-requested; bytes already written are kept
                    resp = requests.get(
                        url,
                        headers={"Range": f"bytes={pos}-{end}"},
                        stream=True,
                        # (connect, read): tolerate slow-link stalls
                        timeout=(self.timeout, max(self.timeout, 300)),
                    )
                    if resp.status_code == 429 or resp.status_code >= 500:
                        if resp.status_code in (429, 503):
                            delay_hint = _retry_after_seconds(resp.headers)
                        raise _RetryableStatusError(resp.status_code)
                    resp.raise_for_status()
                    if resp.status_code != 206:
                        raise IOError(
                            f"range {start}-{end} not honored "
                            f"(HTTP {resp.status_code})"
                        )
                    with open(part, "r+b") as fh:
                        fh.seek(pos)
                        for chunk in resp.iter_content(chunk_size=_DL_CHUNK):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            pos += len(chunk)
                            # only newly-written bytes are aggregated, so a
                            # retried sub-range never double-counts progress
                            with lock:
                                done += len(chunk)
                                if progress_cb is not None:
                                    progress_cb(done, total)
                    if pos == end + 1:
                        return
                    # clean but short read: same remedy as a broken stream
                    last_err = IOError(
                        f"range {start}-{end} short read: "
                        f"{pos - start} of {end - start + 1} bytes"
                    )
                except _RETRYABLE as e:
                    last_err = e
                finally:
                    if resp is not None:
                        resp.close()
            raise last_err if last_err is not None else IOError(
                f"range {start}-{end} failed after {_RANGE_ATTEMPTS} attempts"
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_fetch, start, end)
                for start, end in _split_ranges(total, workers)
            ]
            for fut in futures:
                fut.result()  # re-raises the first worker failure
        return part

    def _download_legacy(
        self,
        url: str,
        file_id: str,
        dest_dir: Path,
        progress_cb: Callable[[int, int | None], None] | None,
        expected_md5: str | None,
    ) -> Path:
        """Single-stream download (original semantics): skip if the file
        exists with matching size, stream to .part, rename on success.
        Transient failures (connection drops, chunked-encoding breaks,
        timeouts, HTTP 429/5xx) are retried up to _LEGACY_ATTEMPTS times;
        every attempt restarts the .part from scratch, and progress is
        reported per attempt so no byte is ever counted twice."""
        tmp: Path | None = None
        last_err: Exception | None = None
        delay_hint: float | None = None
        for attempt in range(_LEGACY_ATTEMPTS):
            if attempt:
                _retry_sleep(attempt - 1, delay_hint)
                delay_hint = None
            resp = None
            try:
                resp = requests.get(
                    url,
                    stream=True,
                    # (connect, read): large slides over slow links can
                    # stall well beyond the default between reads.
                    timeout=(self.timeout, max(self.timeout, 300)),
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if resp.status_code in (429, 503):
                        delay_hint = _retry_after_seconds(resp.headers)
                    raise _RetryableStatusError(resp.status_code)
                resp.raise_for_status()
                cl = resp.headers.get("Content-Length")
                total = int(cl) if cl and cl.isdigit() else None
                file_name = _filename_from_headers(resp.headers) or f"{file_id}.svs"
                dest = dest_dir / file_name
                if (
                    dest.exists()
                    and total is not None
                    and dest.stat().st_size == total
                ):
                    if progress_cb is not None:
                        progress_cb(total, total)
                    return dest
                tmp = dest.with_name(dest.name + ".part")
                done = 0
                with open(tmp, "wb") as fh:  # "wb" truncates: fresh attempt
                    for chunk in resp.iter_content(chunk_size=_DL_CHUNK):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        if progress_cb is not None:
                            progress_cb(done, total)
                return _finalize_part(tmp, dest, total, expected_md5)
            except _RETRYABLE as e:
                last_err = e
            finally:
                if resp is not None:
                    resp.close()
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise IOError(
            f"download of {file_id} failed after {_LEGACY_ATTEMPTS} "
            f"attempts: {last_err}"
        ) from last_err


def _contained_name(name: str) -> str | None:
    """Reduce a server-supplied file name to a bare, contained one.

    The name arrives on Content-Disposition and is joined onto dest_dir,
    and the result is what gets written to disk, stored in the catalog as
    the slide's location and later opened. A "../" segment, an absolute
    path or a Windows drive letter would all place it outside the
    download directory, so keep the last path component only. Returns
    None when nothing usable is left (the callers fall back to file_id).
    """
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    # "C:evil.svs" is drive-relative on Windows and joins as an absolute
    # path; a colon also introduces an NTFS alternate data stream.
    name = name.rsplit(":", 1)[-1]
    # leaves "." and ".." empty, keeps ordinary names untouched
    return name.strip().strip(".") or None


def safe_file_id(file_id: str) -> str:
    """Reduce a GDC-supplied file_id to something safe to join onto a path.

    ``file_id`` reaches us from the same untrusted place as the
    Content-Disposition name (``_record_from_hit``), and it is joined onto a
    path twice: app.py builds the per-slide download directory as
    ``TCGA_DATA_DIR / file_id``, and ``download_slide`` falls back to
    ``f"{file_id}.svs"`` when the server sends no file name. A "../" segment
    therefore escaped the download root twice over, and the escaped path was
    persisted to ``tcga_slides.local_path``. Same containment rule (and the
    same low severity — the real GDC sends a UUID) as R02-3's
    ``_contained_name`` on the header file name.

    A real file_id is a UUID and passes through unchanged.
    """
    return _contained_name(str(file_id)) or "unknown_file_id"


def _filename_from_headers(headers) -> str | None:
    cd = headers.get("Content-Disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
    # sanitised here rather than at the two join sites so no future caller
    # can reintroduce the escape by forgetting one
    return _contained_name(m.group(1).strip()) if m else None


def _resolve_workers(workers: int | None) -> int:
    """workers=None -> env HESCOPE_DL_WORKERS (default 8); clamp to 1..16."""
    if workers is None:
        raw = os.environ.get("HESCOPE_DL_WORKERS", "")
        try:
            workers = int(raw) if raw.strip() else _DL_WORKERS_DEFAULT
        except ValueError:
            workers = _DL_WORKERS_DEFAULT
    return max(1, min(_DL_WORKERS_MAX, int(workers)))


def _split_ranges(total: int, workers: int) -> list[tuple[int, int]]:
    """Split [0, total) into `workers` contiguous inclusive (start, end)
    ranges. Extra bytes go to the leading ranges."""
    if total <= 0:
        return []
    workers = max(1, min(workers, total))
    base, rem = divmod(total, workers)
    ranges = []
    start = 0
    for i in range(workers):
        size = base + (1 if i < rem else 0)
        ranges.append((start, start + size - 1))
        start += size
    return ranges


def _md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finalize_part(
    part: Path, dest: Path, total: int | None, expected_md5: str | None
) -> Path:
    """Verify the completed .part (size, then md5 if given) and atomically
    rename it to the final name. Raises IOError on mismatch."""
    if total is not None:
        size = part.stat().st_size
        if size != total:
            part.unlink(missing_ok=True)
            raise IOError(
                f"incomplete download of {dest.name}: "
                f"expected {total} bytes, got {size}"
            )
    if expected_md5:
        digest = _md5_of(part)
        if digest.lower() != expected_md5.lower():
            part.unlink(missing_ok=True)
            raise IOError(
                f"md5 mismatch for {dest.name}: "
                f"expected {expected_md5}, got {digest}"
            )
    part.replace(dest)
    return dest


class SlideCatalog:
    """SQLite-backed local catalog of TCGA slide metadata.

    Table tcga_slides(file_id PK, file_name, file_size, project_id,
    case_submitter_id, sample_type, primary_site, local_path,
    downloaded_at, first_seen_at, md5sum). Existing databases without the
    md5sum column are upgraded in place via ALTER TABLE.
    """

    _COLUMNS = (
        "file_id",
        "file_name",
        "file_size",
        "project_id",
        "case_submitter_id",
        "sample_type",
        "primary_site",
        "local_path",
        "downloaded_at",
        "first_seen_at",
        "md5sum",
    )

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS tcga_slides (
                    file_id TEXT PRIMARY KEY,
                    file_name TEXT,
                    file_size INTEGER,
                    project_id TEXT,
                    case_submitter_id TEXT,
                    sample_type TEXT,
                    primary_site TEXT,
                    local_path TEXT,
                    downloaded_at TEXT,
                    first_seen_at TEXT,
                    md5sum TEXT
                )
                """
            )
            # catalogs created before the md5sum column existed: add it in
            # place (no migration framework, additive only)
            cols = {
                row[1] for row in con.execute("PRAGMA table_info(tcga_slides)")
            }
            if "md5sum" not in cols:
                con.execute("ALTER TABLE tcga_slides ADD COLUMN md5sum TEXT")

    def _connect(self) -> sqlite3.Connection:
        # short-lived connections: safe across marimo kernel threads
        return sqlite3.connect(str(self.db_path))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert(self, records: list[SlideRecord]) -> int:
        """Insert new records (existing file_ids are ignored, local download
        state is preserved). A missing md5sum on an existing row is the one
        field that is backfilled. Returns the number of newly inserted rows."""
        now = self._now()
        inserted = 0
        with self._connect() as con:
            for r in records:
                cur = con.execute(
                    """
                    INSERT OR IGNORE INTO tcga_slides
                    (file_id, file_name, file_size, project_id,
                     case_submitter_id, sample_type, primary_site,
                     local_path, downloaded_at, first_seen_at, md5sum)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r.file_id,
                        r.file_name,
                        r.file_size,
                        r.project_id,
                        r.case_submitter_id,
                        r.sample_type,
                        r.primary_site,
                        r.local_path,
                        None,
                        now,
                        r.md5sum,
                    ),
                )
                inserted += cur.rowcount
                # md5sum is the only content check a download gets. A row
                # that predates it -- carried over by the ALTER TABLE
                # upgrade above, or first seen when GDC reported none --
                # would otherwise keep md5sum NULL forever, so every later
                # download of that slide runs with expected_md5=None and is
                # silently accepted unverified. Fill it in, but never
                # overwrite a value that is already recorded.
                if cur.rowcount == 0 and r.md5sum:
                    con.execute(
                        "UPDATE tcga_slides SET md5sum = ? "
                        "WHERE file_id = ? AND md5sum IS NULL",
                        (r.md5sum, r.file_id),
                    )
        return inserted

    def mark_downloaded(self, file_id: str, local_path: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE tcga_slides SET local_path = ?, downloaded_at = ? "
                "WHERE file_id = ?",
                (str(local_path), self._now(), file_id),
            )

    def search(
        self,
        project_id: str | None = None,
        downloaded_only: bool = False,
        limit: int = 200,
    ) -> list[SlideRecord]:
        sql = (
            "SELECT file_id, file_name, file_size, project_id, "
            "case_submitter_id, sample_type, primary_site, local_path, md5sum "
            "FROM tcga_slides"
        )
        clauses: list[str] = []
        args: list = []
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        if downloaded_only:
            clauses.append("local_path IS NOT NULL")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY project_id, file_name LIMIT ?"
        args.append(int(limit))
        with self._connect() as con:
            rows = con.execute(sql, args).fetchall()
        return [
            SlideRecord(
                file_id=row[0],
                file_name=row[1],
                file_size=int(row[2] or 0),
                project_id=row[3],
                case_submitter_id=row[4],
                sample_type=row[5],
                primary_site=row[6],
                local_path=row[7],
                md5sum=row[8],
            )
            for row in rows
        ]

    def stats(self) -> dict:
        """Return {"total": n, "downloaded": m, "projects": {pid: count}}."""
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) FROM tcga_slides").fetchone()[0]
            downloaded = con.execute(
                "SELECT COUNT(*) FROM tcga_slides WHERE local_path IS NOT NULL"
            ).fetchone()[0]
            proj_rows = con.execute(
                "SELECT COALESCE(project_id, 'unknown'), COUNT(*) "
                "FROM tcga_slides GROUP BY project_id"
            ).fetchall()
        return {
            "total": int(total),
            "downloaded": int(downloaded),
            "projects": {pid: int(n) for pid, n in proj_rows},
        }
