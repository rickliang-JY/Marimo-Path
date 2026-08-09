"""Tests for hescope.cli. Offline; tmp sqlite files; images made with PIL."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from PIL import Image

from hescope.cli import main
from hescope.db import SlideRepo, get_engine


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{tmp_path}/cli.db"


@pytest.fixture()
def image_dir(tmp_path):
    """A folder with 2 tiny valid PNGs and 1 bogus .png text file."""
    d = tmp_path / "images"
    d.mkdir()
    for i, color in enumerate([(220, 140, 180), (150, 120, 200)]):
        arr = np.zeros((64, 96, 3), dtype=np.uint8)
        arr[..., 0], arr[..., 1], arr[..., 2] = color
        Image.fromarray(arr, "RGB").save(d / f"slide_{i}.png")
    (d / "bogus.png").write_text("this is not an image at all")
    return d


def test_init_creates_tables(db_url, capsys):
    assert main(["--db", db_url, "init"]) == 0
    out = capsys.readouterr().out
    assert "Initialized" in out
    engine = get_engine(db_url)
    tables = set(sa.inspect(engine).get_table_names())
    assert {"slides", "rois", "agent_runs"} <= tables
    engine.dispose()


def test_ingest_and_list(db_url, image_dir, capsys):
    assert main(["--db", db_url, "init"]) == 0
    capsys.readouterr()
    assert main(["--db", db_url, "ingest", str(image_dir)]) == 0
    captured = capsys.readouterr()
    assert "2 registered" in captured.out
    assert "1 skipped" in captured.out
    assert "bogus.png" in captured.err  # warned about the unreadable file

    assert main(["--db", db_url, "list"]) == 0
    out = capsys.readouterr().out
    assert "slide_0.png" in out
    assert "slide_1.png" in out
    assert "bogus" not in out
    assert "2 slide(s)" in out

    engine = get_engine(db_url)
    slides = SlideRepo(engine).list()
    assert len(slides) == 2
    assert {(s["width"], s["height"]) for s in slides} == {(96, 64)}
    engine.dispose()


def test_ingest_idempotent(db_url, image_dir, capsys):
    main(["--db", db_url, "init"])
    main(["--db", db_url, "ingest", str(image_dir)])
    capsys.readouterr()
    # re-ingest the same folder: no duplicates
    assert main(["--db", db_url, "ingest", str(image_dir)]) == 0
    capsys.readouterr()
    engine = get_engine(db_url)
    slides = SlideRepo(engine).list()
    assert len(slides) == 2
    engine.dispose()
    assert main(["--db", db_url, "list"]) == 0
    assert "2 slide(s)" in capsys.readouterr().out


def test_ingest_kind_and_list_filter(db_url, image_dir, capsys):
    main(["--db", db_url, "init"])
    main(["--db", db_url, "ingest", str(image_dir), "--kind", "tcga"])
    capsys.readouterr()
    assert main(["--db", db_url, "list", "--kind", "tcga"]) == 0
    assert "2 slide(s)" in capsys.readouterr().out
    assert main(["--db", db_url, "list", "--kind", "local"]) == 0
    assert "no slides registered" in capsys.readouterr().out


def test_ingest_missing_path_errors(db_url, capsys):
    main(["--db", db_url, "init"])
    assert main(["--db", db_url, "ingest", "/does/not/exist"]) == 1
    assert "no such file" in capsys.readouterr().err


def test_ingest_single_file_and_recursive(db_url, tmp_path, capsys):
    arr = np.zeros((32, 32, 3), dtype=np.uint8)
    Image.fromarray(arr, "RGB").save(tmp_path / "one.png")
    sub = tmp_path / "sub"
    sub.mkdir()
    Image.fromarray(arr, "RGB").save(sub / "two.png")

    main(["--db", db_url, "init"])
    # single file ingest
    assert main(["--db", db_url, "ingest", str(tmp_path / "one.png")]) == 0
    # non-recursive dir ingest finds nothing new at top level (one.png dup)
    assert main(["--db", db_url, "ingest", str(tmp_path)]) == 0
    # recursive picks up sub/two.png
    assert main(["--db", db_url, "ingest", str(tmp_path), "--recursive"]) == 0
    capsys.readouterr()
    engine = get_engine(db_url)
    names = {s["name"] for s in SlideRepo(engine).list()}
    assert names == {"one.png", "two.png"}
    engine.dispose()


def test_db_option_overrides_env(tmp_path, monkeypatch, capsys):
    env_url = f"sqlite:///{tmp_path}/env.db"
    cli_url = f"sqlite:///{tmp_path}/cli.db"
    monkeypatch.setenv("HESCOPE_DB_URL", env_url)
    assert main(["--db", cli_url, "init"]) == 0
    engine = get_engine(cli_url)
    assert "slides" in sa.inspect(engine).get_table_names()
    engine.dispose()
    env_engine = get_engine(env_url)
    assert sa.inspect(env_engine).get_table_names() == []
    env_engine.dispose()


def test_dedupe_slides_merges_rows_that_name_one_file(tmp_path, capsys):
    """R05-2 repair path: a database filled in before paths were canonicalized
    already holds several rows per file, so the fix needs a way to reunite
    them (the shipped data/hescope.db had two rows for the demo slide, one ROI
    hanging off each)."""
    from hescope.db import ROIRepo, Slide
    from hescope.rois import ROI

    db_url = f"sqlite:///{tmp_path}/dedupe.db"
    slide = tmp_path / "dup.png"
    Image.fromarray(
        np.full((8, 8, 3), 240, dtype=np.uint8), "RGB"
    ).save(slide)
    main(["--db", db_url, "init"])
    engine = get_engine(db_url)
    with sa.orm.Session(engine) as s:  # pre-fix rows: bypass register()
        for path in (str(slide.resolve()), str(slide).replace("\\", "/")):
            s.add(Slide(source_kind="local", name="dup.png", path=path,
                        width=8, height=8, extra_json="{}"))
        s.commit()
    ids = [row["id"] for row in SlideRepo(engine).list()]
    assert len(ids) == 2
    for sid in ids:
        ROIRepo(engine).add(sid, ROI(kind="rect", points=((0.0, 0.0), (4.0, 4.0))))

    assert main(["--db", db_url, "dedupe-slides"]) == 0

    out = capsys.readouterr().out
    assert f"merged slide id={ids[1]} into id={ids[0]}" in out
    assert len(SlideRepo(engine).list()) == 1
    assert len(ROIRepo(engine).for_slide(ids[0])) == 2, "annotations were lost"
    assert main(["--db", db_url, "dedupe-slides"]) == 0  # idempotent
    assert "no duplicate slide paths found" in capsys.readouterr().out
    engine.dispose()


# --- app launcher subcommand -------------------------------------------------


def test_find_app_py_locates_repo_app():
    from hescope.cli import find_app_py

    app_path = find_app_py()
    assert app_path.is_file()
    assert app_path.name == "app.py"


def test_build_marimo_cmd_defaults_and_passthrough():
    from hescope.cli import build_marimo_cmd

    app_py = Path("/x/app.py")
    # str(Path(...)) is platform-dependent ("\x\app.py" on Windows), so build
    # the expectation the same way build_marimo_cmd does.
    app_arg = str(app_py)
    cmd = build_marimo_cmd(app_py)
    assert cmd == ["marimo", "edit", app_arg, "--no-token"]
    cmd = build_marimo_cmd(app_py, port=8888, host="0.0.0.0")
    assert cmd == [
        "marimo", "edit", app_arg, "--no-token",
        "--port", "8888", "--host", "0.0.0.0",
    ]


@pytest.mark.parametrize("argv", [["app"], ["app", "--port", "9999"], []])
def test_app_command_execs_marimo(argv, monkeypatch, capsys):
    """`hescope app` and bare `hescope` replace the process with marimo."""
    calls = []
    monkeypatch.setattr(
        "hescope.cli.os.execvp",
        lambda exe, cmd: calls.append((exe, cmd)),
    )
    assert main(argv) == 0
    assert len(calls) == 1
    exe, cmd = calls[0]
    assert exe == "marimo"
    assert cmd[:2] == ["marimo", "edit"]
    assert cmd[2].endswith("app.py") and Path(cmd[2]).is_file()
    assert "--no-token" in cmd
    if "--port" in argv:
        assert cmd[cmd.index("--port") + 1] == "9999"
    out = capsys.readouterr().out
    assert "Cmd/Ctrl+." in out  # app-view hint is printed


def test_app_command_missing_marimo_binary(monkeypatch, capsys):
    def _raise(exe, cmd):
        raise OSError("No such file or directory")

    monkeypatch.setattr("hescope.cli.os.execvp", _raise)
    assert main(["app"]) == 1
    assert "could not launch marimo" in capsys.readouterr().err
