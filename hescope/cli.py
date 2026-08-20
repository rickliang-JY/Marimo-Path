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
  migrate [--dry-run]           apply pending schema migrations (versioned,
                                additive-only; see hescope.store.migrations)
  migrate-tcga-catalog [--dry-run]  import the flat TCGA catalog into the
                                TCGA-shaped tables (never moves files)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .store.db import (
    SlideRepo,
    get_engine,
    init_db,
    merge_duplicate_slide_paths,
    plan_duplicate_slide_merge,
    plan_init_db,
)
from .wsi.slides import open_slide

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

    p_migrate_schema = sub.add_parser(
        "migrate",
        help="apply pending schema migrations (versioned, additive-only)",
    )
    p_migrate_schema.add_argument(
        "--dry-run",
        action="store_true",
        help="print what WOULD be applied and change nothing",
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

    p_doctor = sub.add_parser(
        "doctor",
        help="check the database: which file, schema version, pragmas, row "
             "counts, dangling references, integrity, missing slide files",
    )
    p_doctor.add_argument("--verbose", action="store_true",
                          help="also list the slides whose file is missing")

    p_del = sub.add_parser(
        "delete-roi", help="delete specific ROIs by id (prints them first)")
    p_del.add_argument("roi_id", type=int, nargs="+")
    p_del.add_argument("--yes", action="store_true",
                       help="actually delete; without it this only reports")

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
    registered = 0  # files that produced a brand-new slide row
    merged = 0  # files whose content already matched a known slide (a
    # second location for that slide, not a second slide -- see
    # BUILD-PLAN-DB.md Phase 1 debugger round 2, finding 3: this used to
    # count every registration call as "registered" even when the content
    # matched an existing row, so ingesting a directory with two
    # byte-identical images printed "2 registered" while only 1 slide row
    # ever existed)
    skipped = 0
    for f in files:
        try:
            source = open_slide(f)
        except Exception as exc:  # unreadable file: warn + skip, keep going
            print(f"warning: skipping unreadable file {f}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        result = repo.register(
            source_kind=kind,
            name=source.name,
            path=str(f.resolve()),
            width=source.dimensions[0],
            height=source.dimensions[1],
            mpp=source.mpp,
            report=True,
        )
        dims = f"({source.dimensions[0]}x{source.dimensions[1]})"
        if result.inserted:
            registered += 1
            print(f"registered slide id={result.id} {source.name} {dims}")
        else:
            merged += 1
            print(
                f"{f} matches existing slide id={result.id} {source.name} {dims} "
                "-- location recorded, no new slide"
            )
    print(
        f"ingest complete: {len(files)} file(s) seen, {registered} registered, "
        f"{merged} merged into an existing slide (additional location recorded), "
        f"{skipped} skipped"
    )
    return 0


def _cmd_doctor(engine, *, verbose: bool = False) -> int:
    """Answer "is my database real, and is it healthy" without a GUI.

    Every panel in the app reports on ONE slide. Nothing reported on the
    database itself: which file is in use, whether it exists, what schema
    version it is at, whether the concurrency pragmas took, whether the
    references are intact, or how many rows there are. A user asking "is this
    thing actually storing my work" had no command to run.
    """
    import sqlalchemy as _sa

    from .store.db import sqlite_pragma_report
    from .store.migrations import SCHEMA_VERSION, current_version, pending

    url = engine.url
    print(f"url          {url.render_as_string(hide_password=True)}")
    print(f"backend      {url.get_backend_name()}")

    problems: list[str] = []

    if url.get_backend_name() == "sqlite" and url.database not in (None, ":memory:"):
        f = Path(url.database)
        if f.exists():
            size = f.stat().st_size
            print(f"file         {f}  ({size:,} bytes)")
            for side in ("-wal", "-shm"):
                sc = f.with_name(f.name + side)
                if sc.exists():
                    print(f"             {sc.name}  ({sc.stat().st_size:,} bytes)")
        else:
            print(f"file         {f}  *** DOES NOT EXIST ***")
            problems.append("the database file does not exist")

        pr = sqlite_pragma_report(engine)
        ok_fk = pr.get("foreign_keys") is True
        ok_wal = pr.get("journal_mode") == "wal"
        print(f"pragmas      foreign_keys={pr.get('foreign_keys')} "
              f"journal_mode={pr.get('journal_mode')} "
              f"busy_timeout={pr.get('busy_timeout')}")
        if not ok_fk:
            problems.append("foreign keys are OFF: deletes will not cascade")
        if not ok_wal:
            problems.append(
                f"journal_mode is {pr.get('journal_mode')!r}, not WAL: a write "
                "can fail with 'database is locked' while anything is reading"
            )

    try:
        at = current_version(engine)
        todo = pending(engine)
        print(f"schema       version {at} of {SCHEMA_VERSION}"
              + (f"  ({len(todo)} pending: "
                 + ", ".join(str(m.version) for m in todo) + ")" if todo else "  (up to date)"))
        if todo:
            problems.append(
                f"{len(todo)} migration(s) pending - run `hescope migrate --dry-run` first"
            )
    except Exception as exc:
        print(f"schema       could not be read: {exc}")
        problems.append(f"schema version unreadable: {exc}")

    counts: dict[str, int] = {}
    with engine.connect() as conn:
        names = [
            r[0] for r in conn.execute(_sa.text(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
        ] if url.get_backend_name() == "sqlite" else []
        for t in names:
            try:
                counts[t] = conn.execute(_sa.text(f"SELECT count(*) FROM {t}")).scalar() or 0
            except Exception:
                counts[t] = -1
        print("rows         " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))

        # The two references that carry no FK, checked by hand.
        dangling = {}
        unchecked: list[str] = []
        for label, q in (
            ("interactions.roi_id", "SELECT count(*) FROM interactions i LEFT JOIN rois r "
                                    "ON r.id=i.roi_id WHERE i.roi_id IS NOT NULL AND r.id IS NULL"),
            ("rois.slide_id", "SELECT count(*) FROM rois r LEFT JOIN slides s "
                              "ON s.id=r.slide_id WHERE s.id IS NULL"),
        ):
            try:
                n = conn.execute(_sa.text(q)).scalar() or 0
            except Exception as exc:
                # A doctor tool that quietly drops a check it could not run
                # defeats the point of running `doctor` at all -- say so
                # instead of just omitting the label from the report.
                unchecked.append(f"{label} ({exc})")
                continue
            dangling[label] = n
            if n:
                problems.append(f"{n} dangling {label}")
        print("references   " + ", ".join(f"{k}={v}" for k, v in dangling.items()))
        if unchecked:
            print("             could not check: " + "; ".join(unchecked))
            problems.append(f"{len(unchecked)} reference check(s) could not run")

        if url.get_backend_name() == "sqlite":
            try:
                fk = list(conn.execute(_sa.text("PRAGMA foreign_key_check")))
                integ = conn.execute(_sa.text("PRAGMA integrity_check")).scalar()
                print(f"integrity    integrity_check={integ}, foreign_key_check={len(fk)} violation(s)")
                if integ != "ok":
                    problems.append(f"integrity_check says {integ!r}")
                if fk:
                    problems.append(f"{len(fk)} foreign key violation(s)")
            except Exception as exc:
                print(f"integrity    could not be checked: {exc}")

        # Slides whose file is gone: expected after machines change, but the
        # user should be told rather than discover it when an ROI will not save.
        try:
            rows = list(conn.execute(_sa.text("SELECT id, name, path FROM slides")))
        except Exception:
            rows = []
        missing = [r for r in rows if not Path(r[2]).exists()]
        if rows:
            print(f"slide files  {len(rows) - len(missing)} of {len(rows)} resolve"
                  + (f", {len(missing)} missing" if missing else ""))
            if verbose and missing:
                for sid, name, path in missing[:20]:
                    print(f"             missing  id={sid}  {name}  {path}")

    print()
    if problems:
        print(f"{len(problems)} problem(s):")
        for p_ in problems:
            print(f"  - {p_}")
        return 1
    print("no problems found")
    return 0


def _cmd_delete_roi(engine, roi_ids: list[int], *, yes: bool) -> int:
    """Delete specific ROIs by id. Prints what each one WAS before deleting it,
    because an id alone is not enough for a human to confirm they meant it."""
    from .store.db import ROIRepo

    init_db(engine)
    repo = ROIRepo(engine)
    rows = []
    for rid in roi_ids:
        row = repo.get(rid)
        if row is None:
            print(f"roi {rid}: not found")
            continue
        rows.append(row)
        print(f"roi {rid}: slide={row['slide_id']} kind={row['kind']} "
              f"bbox={row.get('bbox')} label={row.get('label')!r}")
    if not rows:
        return 1
    if not yes:
        print(f"\n{len(rows)} ROI(s) would be deleted. Re-run with --yes to do it.")
        return 0
    deleted = sum(1 for row in rows if repo.delete(row["id"]))
    print(f"\ndeleted {deleted} of {len(rows)} ROI(s)")
    return 0 if deleted == len(rows) else 1


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


def _cmd_migrate(engine, dry_run: bool) -> int:
    """Apply pending schema migrations, or (``--dry-run``) report what would
    run without opening a write transaction or touching the database."""
    from .store.migrations import (
        migrate,
        plan_migration_2,
        plan_migration_3,
        plan_migration_4,
    )
    from .gdc.tcga_schema import plan_init_tcga_schema

    if dry_run:
        # A plain get_engine(url) flips journal_mode to WAL (a persistent
        # file-header change) the moment ANYTHING connects through it, even
        # a read -- read_only=True skips just that one pragma, so inspecting
        # this database cannot be the thing that violates R-1. Rebuilt from
        # the resolved URL rather than reusing `engine` because `engine` was
        # already created (in main()) through the writing get_engine() and
        # may already have flipped the file by the time we get here.
        ro_engine = get_engine(engine.url.render_as_string(hide_password=False), read_only=True)

        # The real command runs init_db(engine) BEFORE migrate(engine) (see
        # below) -- a dry run that only asks migrate() what it would do
        # silently omits init_db's effect. Measured on a narrow (old-schema)
        # database: this under-report was 2 tables, 8 indexes and 3 columns
        # that init_db actually adds. plan_init_db is the same computation
        # init_db itself runs (see its docstring), so this cannot drift from
        # what `migrate` (no --dry-run) actually does.
        init_plan = plan_init_db(ro_engine)
        # Same gap, TCGA side: migrate() also self-containedly calls
        # init_tcga_schema (migration 3's self-containment, mirroring
        # init_db's) -- see plan_init_tcga_schema's docstring for the
        # measured under-report this closes.
        tcga_init_plan = plan_init_tcga_schema(ro_engine)
        report = migrate(ro_engine, dry_run=True)

        init_db_changes = False
        for table_name in init_plan["new_tables"]:
            indexes = init_plan["new_table_indexes"].get(table_name, [])
            suffix = f" (with index(es): {', '.join(indexes)})" if indexes else ""
            print(f"would create table: {table_name}{suffix}")
            init_db_changes = True
        for stmt in init_plan["alter_statements"]:
            print(f"would run: {stmt}")
            init_db_changes = True
        for stmt in init_plan["create_index_statements"]:
            print(f"would run: {stmt}")
            init_db_changes = True
        for table_name in tcga_init_plan["new_tables"]:
            indexes = tcga_init_plan["new_table_indexes"].get(table_name, [])
            suffix = f" (with index(es): {', '.join(indexes)})" if indexes else ""
            print(f"would create table: {table_name}{suffix}")
            init_db_changes = True

        if not report.skipped and not init_db_changes:
            print(f"dry run: already at version {report.from_version}; nothing to do")
            return 0
        for item in report.skipped:
            print(f"would apply migration {item}")
            # Migration 2's backfill counts (BUILD-PLAN-DB.md Phase 1's
            # "done when" report) -- migrate(dry_run=True) never calls a
            # migration's apply(), so this preview is computed the same way
            # plan_init_db previews init_db: a read-only pass over the SAME
            # rows/files apply() would touch, sharing its computation (see
            # hescope.store.migrations.plan_migration_2) so the two cannot drift.
            if item.startswith("2:"):
                with ro_engine.connect() as ro_conn:
                    mig2 = plan_migration_2(ro_conn)
                print(
                    f"  would write {mig2['slide_files']} slide_files row(s) "
                    f"({mig2['missing']} marked missing), "
                    f"{mig2['distinct_identities']} distinct identity(ies), "
                    f"{mig2['duplicate_content_rows']} duplicate-content row(s) skipped, "
                    f"{mig2['rois_backfilled']} roi(s) with bbox backfilled"
                )
            # Migration 3's FK + backfill counts (BUILD-PLAN-DB.md Phase 2's
            # "done when" report), same shared-computation guarantee as
            # migration 2's block above (see hescope.store.migrations.plan_migration_3).
            if item.startswith("3:"):
                with ro_engine.connect() as ro_conn:
                    mig3 = plan_migration_3(ro_conn)
                fk_note = (
                    "already present" if mig3["fk_present"]
                    else "would be added" if mig3["tcga_files_exists"]
                    else "n/a (tcga_files does not exist yet)"
                )
                print(
                    f"  tcga_files.slide_id foreign key: {fk_note}; "
                    f"of {mig3['with_local_path']} downloaded file(s), "
                    f"{mig3['already_linked']} already linked, "
                    f"would link {mig3['would_link']}, "
                    f"{mig3['could_not_link']} could not be linked"
                )
                # round 3 finding 2: a slide_id that already names no row in
                # `slides` (e.g. dedupe-slides deleted it) would abort the FK
                # rebuild outright; migration 3 now clears and re-resolves it
                # instead (folded into would_link/could_not_link above), but
                # a reviewer still needs to see that this database has one.
                if mig3["dangling_slide_ids"]:
                    print(
                        f"  {mig3['dangling_slide_ids']} tcga_files row(s) "
                        "have a slide_id pointing at a slide that no longer "
                        "exists -- would be cleared and re-linked from "
                        "local_path"
                    )

            # Migration 4's backfill counts. plan_migration_4 existed and was
            # correct from the start, and NOTHING CALLED IT: `hescope migrate
            # --dry-run` printed migration 4's title and no detail line, so the
            # duplicate (slide_id, geom_key) groups -- the one thing
            # docs/DATABASE-DESIGN.md §5 step 5 says must be REPORTED rather
            # than silently dropped -- were invisible in the only place a user
            # would look. Third time this branch has paid for a preview that
            # cannot match its run (ba84d17, 4b93f58); the fix each time is to
            # make the command ask the planner, not to reason about it.
            if item.startswith("4:"):
                with ro_engine.connect() as ro_conn:
                    mig4 = plan_migration_4(ro_conn)
                print(
                    f"  would backfill geom_key on {mig4['rois']} roi(s) and "
                    f"created_by on {mig4['created_by_backfilled']}"
                )
                if mig4["duplicate_geom_key_groups"]:
                    print(
                        f"  {mig4['duplicate_geom_key_groups']} duplicate "
                        f"(slide_id, geom_key) group(s) covering "
                        f"{mig4['duplicate_geom_key_rois']} roi(s) -- reported, "
                        "not merged; geometry-identical ROIs stay as separate "
                        "rows until you decide"
                    )
        total_new_tables = len(init_plan["new_tables"]) + len(tcga_init_plan["new_tables"])
        print(
            f"dry run: would go from version {report.from_version} to "
            f"version {report.to_version} ({len(report.skipped)} pending migration(s), "
            f"{total_new_tables} new table(s), "
            f"{len(init_plan['alter_statements'])} new column(s), "
            f"{len(init_plan['create_index_statements'])} new index(es) from init_db); "
            "nothing was changed"
        )
        return 0

    # An empty database needs its tables before version 1 can be stamped;
    # a database that already has them (every shipped data/hescope.db) is
    # untouched by this call -- see hescope.store.migrations' module docstring.
    init_db(engine)
    report = migrate(engine)
    for item in report.applied:
        print(f"applied migration {item}")
    if report.error is not None:
        print(f"error applying migration: {report.error}", file=sys.stderr)
        print(
            f"migration stopped: version {report.from_version} -> "
            f"{report.to_version} ({len(report.applied)} applied)",
            file=sys.stderr,
        )
        return 1
    if not report.applied:
        print(f"already at version {report.to_version}; nothing to do")
    else:
        print(
            f"migrated: version {report.from_version} -> {report.to_version} "
            f"({len(report.applied)} applied)"
        )
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

    from .core.paths import resolve_runtime_dir
    from .gdc.tcga_schema import TcgaCatalog, parse_barcode

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
            "case_submitter_id, sample_type, primary_site, local_path, md5sum, "
            "first_seen_at, downloaded_at "
            "FROM tcga_slides"
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"error: cannot read {src}: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()

    prepared, downloaded = [], []
    for (fid, fname, fsize, pid, case_id, stype, site, lpath, md5, first_seen,
         downloaded_at) in rows:
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
                # The flat catalog's OWN discovery time -- carried through so
                # TcgaCatalog.upsert_rows can use it instead of defaulting to
                # "now" (R-3: this is the exact defect that reset every
                # imported row's first_seen_at and lost 25 hours of
                # provenance across all 50 rows of the real database).
                "first_seen_at": first_seen,
            }
        )
        if lpath:
            # downloaded_at travels with (fid, lpath): the identical R-3
            # provenance fix as first_seen_at above, one column over (round
            # 3 finding 4) -- mark_downloaded below uses it instead of
            # defaulting to "now".
            downloaded.append((fid, lpath, downloaded_at))

    if dry_run:
        print(f"would import {len(prepared)} file row(s) from {src}")
        print(f"would preserve {len(downloaded)} recorded download(s)")
        print(f"  projects: {len({r['project_id'] for r in prepared if r['project_id']})}"
              f"  cases: {len({r['case_submitter_id'] for r in prepared if r['case_submitter_id']})}"
              f"  samples: {len({r['sample_id'] for r in prepared if r['sample_id']})}")
        missing = [p for _f, p, _d in downloaded if not _Path(p).is_file()]
        if missing:
            print(f"  {len(missing)} recorded path(s) no longer exist and will "
                  "import without download state")
        print("dry run: nothing was changed")
        return 0

    init_db(engine)
    catalog = TcgaCatalog(engine)
    inserted = catalog.upsert_rows(prepared)
    kept = 0
    unmatched = 0
    for fid, lpath, downloaded_at in downloaded:
        if _Path(lpath).is_file():
            # round 3 finding 5: mark_downloaded returns False (and writes
            # nothing) for a file_id upsert_rows refused to create a row for
            # (e.g. an empty file_id) -- act on that instead of counting it
            # as preserved regardless.
            if catalog.mark_downloaded(fid, lpath, downloaded_at=downloaded_at):
                kept += 1
            else:
                unmatched += 1
    stats = catalog.stats()
    print(f"imported {inserted} new file row(s) from {src}")
    print(f"preserved {kept} download(s) (files were not moved)")
    if unmatched:
        print(
            f"{unmatched} recorded download(s) named no catalog row and "
            "were skipped"
        )
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
    if args.command == "doctor":
        return _cmd_doctor(engine, verbose=args.verbose)
    if args.command == "delete-roi":
        return _cmd_delete_roi(engine, args.roi_id, yes=args.yes)
    if args.command == "list":
        return _cmd_list(engine, args.kind)
    if args.command == "dedupe-slides":
        return _cmd_dedupe_slides(engine, getattr(args, "dry_run", False))
    if args.command == "migrate":
        return _cmd_migrate(engine, getattr(args, "dry_run", False))
    if args.command == "migrate-tcga-catalog":
        return _cmd_migrate_tcga_catalog(
            engine,
            getattr(args, "catalog_db", None),
            getattr(args, "dry_run", False),
        )
    return 1  # pragma: no cover - argparse enforces a valid command


if __name__ == "__main__":
    raise SystemExit(main())
