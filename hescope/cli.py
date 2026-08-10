"""HE-Scope command line interface.

Usage: ``hescope [--db URL] <command> ...`` (or ``python -m hescope.cli``).

Commands:
  app [--port N] [--host H]     launch the marimo viewer app
                                (``marimo edit app.py --no-token``); also the
                                default when ``hescope`` is run with no command
  init                          create database tables
  ingest PATH [--kind K] [--recursive]   register image files as slides
  list [--kind K]               list registered slides
  dedupe-slides [--dry-run]     merge slide rows that name the same file
  migrate-tcga-catalog [--dry-run]  import the flat TCGA catalog into the
                                TCGA-shaped tables (never moves files)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .db import (
    SlideRepo,
    get_engine,
    init_db,
    merge_duplicate_slide_paths,
    plan_duplicate_slide_merge,
)
from .slides import open_slide

IMAGE_EXTENSIONS = {".svs", ".tif", ".tiff", ".ndpi", ".png", ".jpg", ".jpeg"}


def find_app_py() -> Path:
    """Locate the marimo app (``app.py``) shipped with the project.

    Resolution rule: ``Path(__file__).resolve().parent.parent / "app.py"``,
    i.e. the directory that contains the installed ``hescope`` package. This
    works for both installation layouts:

    * repo checkout / ``pip install -e .`` — ``hescope/`` sits at the repo
      root next to ``app.py``;
    * wheel / sdist install — ``pyproject.toml`` registers ``app`` as a
      top-level py-module, so ``app.py`` lands in site-packages next to the
      ``hescope`` package.

    Raises ``FileNotFoundError`` with a clear message when the app cannot be
    found at the expected location.
    """
    app_path = Path(__file__).resolve().parent.parent / "app.py"
    if not app_path.is_file():
        raise FileNotFoundError(
            f"HE-Scope marimo app not found at {app_path}. Reinstall with "
            "`pip install -e .` from the repository root (or a distribution "
            "that ships app.py)."
        )
    return app_path


def build_marimo_cmd(
    app_path: Path, port: int | None = None, host: str | None = None
) -> list[str]:
    """Assemble the ``marimo edit`` command line for the viewer app."""
    cmd = ["marimo", "edit", str(app_path), "--no-token"]
    if port is not None:
        cmd += ["--port", str(port)]
    if host is not None:
        cmd += ["--host", host]
    return cmd


def _cmd_app(port: int | None, host: str | None) -> int:
    try:
        app_path = find_app_py()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    cmd = build_marimo_cmd(app_path, port=port, host=host)
    print(f"Starting HE-Scope viewer: {' '.join(cmd)}")
    print("Tip: once the browser opens, press Cmd/Ctrl+. to hide the code "
          "cells (app view).")
    try:
        os.execvp("marimo", cmd)  # replace this process with marimo
    except OSError as exc:
        print(f"error: could not launch marimo: {exc}", file=sys.stderr)
        return 1
    return 0  # pragma: no cover - only reached if execvp returns


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hescope",
        description="HE-Scope: H&E whole-slide viewer + agent platform. "
        "Run `hescope app` (or bare `hescope`) to launch the viewer; "
        "init/ingest/list manage the metadata database.",
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="URL",
        help="database URL (overrides HESCOPE_DB_URL and the default "
        "<project>/data/hescope.db)",
    )
    sub = parser.add_subparsers(dest="command")

    p_app = sub.add_parser(
        "app", help="launch the marimo viewer app (default command)"
    )
    p_app.add_argument(
        "--port", type=int, default=None, help="port for the marimo server"
    )
    p_app.add_argument(
        "--host", default=None, help="host for the marimo server"
    )

    sub.add_parser("init", help="create database tables")

    p_ingest = sub.add_parser(
        "ingest", help="register image files into the slides table"
    )
    p_ingest.add_argument("path", help="image file or directory of images")
    p_ingest.add_argument(
        "--kind",
        choices=["local", "tcga"],
        default="local",
        help="source_kind to record (default: local)",
    )
    p_ingest.add_argument(
        "--recursive",
        action="store_true",
        help="recurse into subdirectories when PATH is a directory",
    )

    p_dedupe = sub.add_parser(
        "dedupe-slides",
        help="merge slide rows that name the same file under different "
        "path spellings (one-off repair for databases written before "
        "paths were canonicalized)",
    )
    p_dedupe.add_argument(
        "--dry-run",
        action="store_true",
        help="print the merges, ROI moves and path rewrites that WOULD be "
        "made, and change nothing",
    )

    p_migrate = sub.add_parser(
        "migrate-tcga-catalog",
        help="import the flat data/tcga/catalog.db into the TCGA-shaped "
        "tables (project/case/sample/file) in the main database; files are "
        "never moved and the old catalog is left in place",
    )
    p_migrate.add_argument(
        "--catalog-db",
        default=None,
        metavar="PATH",
        help="the flat catalog to read (default: <runtime>/data/tcga/catalog.db)",
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="print what WOULD be imported and change nothing",
    )

    p_list = sub.add_parser("list", help="list registered slides")
    p_list.add_argument(
        "--kind",
        choices=["local", "tcga", "upload"],
        default=None,
        help="only show slides of this source_kind",
    )
    return parser


def _cmd_init(engine) -> int:
    init_db(engine)
    url = engine.url.render_as_string(hide_password=True)
    print(f"Initialized HE-Scope database at {url}")
    return 0


def _iter_image_files(root: Path, recursive: bool) -> list[Path]:
    if root.is_file():
        return [root]
    it = root.rglob("*") if recursive else root.glob("*")
    return sorted(
        f for f in it if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )


def _cmd_ingest(engine, path: str, kind: str, recursive: bool) -> int:
    root = Path(path)
    if not root.exists():
        print(f"error: no such file or directory: {path}", file=sys.stderr)
        return 1
    init_db(engine)
    repo = SlideRepo(engine)
    files = _iter_image_files(root, recursive)
    registered = 0
    skipped = 0
    for f in files:
        try:
            source = open_slide(f)
        except Exception as exc:  # unreadable file: warn + skip, keep going
            print(f"warning: skipping unreadable file {f}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        slide_id = repo.register(
            source_kind=kind,
            name=source.name,
            path=str(f.resolve()),
            width=source.dimensions[0],
            height=source.dimensions[1],
            mpp=source.mpp,
        )
        registered += 1
        print(f"registered slide id={slide_id} {source.name} "
              f"({source.dimensions[0]}x{source.dimensions[1]})")
    print(f"ingest complete: {registered} registered, {skipped} skipped")
    return 0


def _cmd_list(engine, kind: str | None) -> int:
    init_db(engine)
    repo = SlideRepo(engine)
    slides = repo.list(kind)
    if not slides:
        print("no slides registered")
        return 0
    print(f"{'id':>4}  {'kind':<6}  {'size':<12}  {'mpp':<8}  name (path)")
    for s in slides:
        size = f"{s['width']}x{s['height']}"
        mpp = f"{s['mpp']:.4g}" if s["mpp"] is not None else "-"
        print(f"{s['id']:>4}  {s['source_kind']:<6}  {size:<12}  {mpp:<8}  "
              f"{s['name']} ({s['path']})")
    print(f"{len(slides)} slide(s)")
    return 0


def _cmd_dedupe_slides(engine, dry_run: bool = False) -> int:
    init_db(engine)
    if dry_run:
        # This command rewrites the table a user's annotations hang off, in
        # one uncancellable transaction, and its input is the live database.
        # Showing the blast radius first is what makes running it a reviewable
        # decision instead of an unauthorized data change (R07-19).
        plan = plan_duplicate_slide_merge(engine)
        for dup_id, kept_id, canonical in plan["merges"]:
            print(f"would merge slide id={dup_id} into id={kept_id} ({canonical})")
        for roi_id, from_id, to_id in plan["moved_rois"]:
            print(f"would move roi id={roi_id} from slide {from_id} to {to_id}")
        for slide_id, old, new in plan["rewrites"]:
            print(f"would rewrite slide id={slide_id} path {old!r} -> {new!r}")
        print(
            f"dry run: {len(plan['merges'])} merge(s), "
            f"{len(plan['moved_rois'])} roi move(s), "
            f"{len(plan['rewrites'])} path rewrite(s); nothing was changed"
        )
        return 0
    merged = merge_duplicate_slide_paths(engine)
    if not merged:
        print("no duplicate slide paths found")
        return 0
    for dup_id, kept_id in merged:
        print(f"merged slide id={dup_id} into id={kept_id}")
    print(f"{len(merged)} duplicate slide row(s) merged")
    return 0


def _cmd_migrate_tcga_catalog(engine, catalog_db: str | None, dry_run: bool) -> int:
    """Carry the flat ``tcga_slides`` catalog into the TCGA-shaped tables.

    The old catalog is a separate SQLite file holding one denormalized row per
    file. This reads it, recovers the hierarchy each row belongs to (the
    barcode in the file name carries case and sample when the columns do not),
    and writes projects/cases/samples/files into the MAIN database, preserving
    download state. It never moves a file and never deletes the old catalog:
    an existing download keeps working from its recorded absolute path.
    """
    import sqlite3
    from pathlib import Path as _Path

    from .paths import resolve_runtime_dir
    from .tcga_schema import TcgaCatalog, parse_barcode

    src = _Path(catalog_db) if catalog_db else (
        resolve_runtime_dir(_Path(__file__).resolve().parent.parent)
        / "data" / "tcga" / "catalog.db"
    )
    if not src.is_file():
        print(f"no flat catalog at {src}; nothing to migrate")
        return 0

    con = sqlite3.connect(str(src))
    try:
        rows = con.execute(
            "SELECT file_id, file_name, file_size, project_id, "
            "case_submitter_id, sample_type, primary_site, local_path, md5sum "
            "FROM tcga_slides"
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"error: cannot read {src}: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()

    prepared, downloaded = [], []
    for (fid, fname, fsize, pid, case_id, stype, site, lpath, md5) in rows:
        bc = parse_barcode(fname)
        prepared.append(
            {
                "file_id": fid, "file_name": fname, "file_size": fsize,
                "md5sum": md5, "project_id": pid,
                "case_submitter_id": case_id or bc.case,
                # The flat table never stored a sample id. The barcode is the
                # only place the sample identity survives, so synthesise a
                # stable key from it rather than dropping the level entirely.
                "sample_id": (bc.sample or f"{fid}-sample"),
                "sample_submitter_id": bc.sample,
                "sample_type": stype or bc.sample_type,
                "tissue_type": None, "primary_site": site,
                "project_name": None, "disease_type": None,
                "program": bc.program, "case_uuid": None,
            }
        )
        if lpath:
            downloaded.append((fid, lpath))

    if dry_run:
        print(f"would import {len(prepared)} file row(s) from {src}")
        print(f"would preserve {len(downloaded)} recorded download(s)")
        print(f"  projects: {len({r['project_id'] for r in prepared if r['project_id']})}"
              f"  cases: {len({r['case_submitter_id'] for r in prepared if r['case_submitter_id']})}"
              f"  samples: {len({r['sample_id'] for r in prepared if r['sample_id']})}")
        missing = [p for _f, p in downloaded if not _Path(p).is_file()]
        if missing:
            print(f"  {len(missing)} recorded path(s) no longer exist and will "
                  "import without download state")
        print("dry run: nothing was changed")
        return 0

    init_db(engine)
    catalog = TcgaCatalog(engine)
    inserted = catalog.upsert_rows(prepared)
    kept = 0
    for fid, lpath in downloaded:
        if _Path(lpath).is_file():
            catalog.mark_downloaded(fid, lpath)
            kept += 1
    stats = catalog.stats()
    print(f"imported {inserted} new file row(s) from {src}")
    print(f"preserved {kept} download(s) (files were not moved)")
    print(
        f"catalog now: {stats['projects']} project(s), {stats['cases']} case(s), "
        f"{stats['samples']} sample(s), {stats['files']} file(s), "
        f"{stats['downloaded']} downloaded"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in (None, "app"):
        return _cmd_app(getattr(args, "port", None), getattr(args, "host", None))
    engine = get_engine(args.db)
    if args.command == "init":
        return _cmd_init(engine)
    if args.command == "ingest":
        return _cmd_ingest(engine, args.path, args.kind, args.recursive)
    if args.command == "list":
        return _cmd_list(engine, args.kind)
    if args.command == "dedupe-slides":
        return _cmd_dedupe_slides(engine, getattr(args, "dry_run", False))
    if args.command == "migrate-tcga-catalog":
        return _cmd_migrate_tcga_catalog(
            engine,
            getattr(args, "catalog_db", None),
            getattr(args, "dry_run", False),
        )
    return 1  # pragma: no cover - argparse enforces a valid command


if __name__ == "__main__":
    raise SystemExit(main())
