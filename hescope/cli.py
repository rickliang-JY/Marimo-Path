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
    return 1  # pragma: no cover - argparse enforces a valid command


if __name__ == "__main__":
    raise SystemExit(main())
