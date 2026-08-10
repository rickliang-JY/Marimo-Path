"""The TCGA panel must say what happened, not which branch it was on.

Three things, all through app.py's OWN cells (``ast``-sliced and ``exec``'d,
the tests/test_annotation_jump.py technique), because the message strings are
composed inside those cells and nowhere else:

  * **R01-4 / R07-18** — "Downloaded and opened X" was written before anything
    was opened. The fix (a try/except around ``open_slide_path`` that reports
    "Downloaded X, but opening it failed: ...") has been in the tree since
    round 01 with **no regression test at all** — the only fix in six rounds
    carrying "test coverage: none". Round 01 recorded that faking a kernel to
    assert on a message string would only test the fake; running app.py's REAL
    cell source is what closes that, and it is what the two tests below do.
  * **R07-13** — the already-on-disk branch reached the same
    ``open_slide_path`` and therefore the same "Downloaded and opened" string,
    for a file nothing had fetched and nothing had md5-verified.
    ``SlideCatalog.local_file`` only checks that the path exists.
    "Downloaded" is an integrity claim in this app: ``download_slide``
    verifies ``expected_md5``, and R02-2 exists precisely because unverified
    downloads were a finding.
  * **R07-15 / R07-4** — "Browse local catalog" listed
    ``SlideCatalog.search()``'s default 200 rows with no message, directly
    under a status line counting every row, and wrote no message of its own,
    so a red "GDC search failed" from an earlier click stood over a result
    list that had just been produced successfully.
"""

from __future__ import annotations

import pathlib

import ast
import pathlib

import marimo as mo
import pytest

from hescope.tcga import SlideCatalog, SlideRecord

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _cell(marker: str):
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
            continue
        src = ast.get_source_segment(SOURCE, node)
        if src is None or marker not in src:
            continue
        ns: dict = {}
        exec(compile("\n" * (node.lineno - 1) + src, str(APP), "exec"), ns)
        return ns[node.name], [a.arg for a in node.args.args]
    raise AssertionError(f"no @app.cell in app.py contains {marker!r}")


def _record(file_id="abc-123", name="TCGA-XX-0001.svs", local=None):
    return SlideRecord(
        file_id=file_id,
        file_name=name,
        file_size=1234,
        project_id="TCGA-BRCA",
        case_submitter_id="TCGA-BH-A18H",
        sample_type="Primary Tumor",
        primary_site="Breast",
        local_path=local,
        md5sum="0" * 32,
    )


@pytest.fixture
def catalog(tmp_path):
    return SlideCatalog(tmp_path / "catalog.db")


def _new_dl():
    return {
        "thread": None,
        "progress": None,
        "msg": None,
        "open_path": None,
        "downloaded": False,
    }


class _PanelSpy:
    """The real ``hescope.tcga_panel``, keeping the widgets it builds.

    The cells hand their widgets straight into an ``mo.vstack``/``mo.hstack``,
    so this is how the test gets hold of the REAL table and the REAL button
    rather than digging through layout internals.
    """

    def __init__(self):
        import hescope.tcga_panel as panel

        self._panel = panel
        self.table = None
        self.switch = None

    def __getattr__(self, name):
        return getattr(self._panel, name)

    def make_results_table(self, records):
        self.table = self._panel.make_results_table(records)
        return self.table

    def make_filter_controls(self, on_search, on_browse):
        out = self._panel.make_filter_controls(on_search, on_browse)
        self.switch = out[4]
        return out


class _MO:
    """``mo`` with ``mo.ui.button`` recorded, so the test clicks the REAL one."""

    def __init__(self):
        self.buttons: dict = {}

        class _UI:
            def __getattr__(_s, name):
                return getattr(mo.ui, name)

            @staticmethod
            def button(**kw):
                el = mo.ui.button(**kw)
                self.buttons[kw.get("label")] = el
                return el

        self.ui = _UI()

    def __getattr__(self, name):
        return getattr(mo, name)


class _NoDb:
    """DB-free mode: the cell must take the no-database path."""

    enabled = False
    slide_repo = None


def _results_cell(catalog, tcga_dl, rows, tmp_path, client):
    published: dict = {}
    panel = _PanelSpy()
    momod = _MO()
    cell, params = _cell("def _on_download_open(_):")
    deps = {
        "TCGA_DATA_DIR": tmp_path,
        "get_tcga_records": lambda: rows,
        "mo": momod,
        "safe_file_id": lambda v: v,
        "set_tcga_msg": lambda v: published.__setitem__("msg", v),
        "tcga_catalog": catalog,
        "tcga_client": client,
        "tcga_dl": tcga_dl,
        "tcga_panel": panel,
        # The cell now also registers a finished download into the main
        # database and lays it out under <project>/<case>/ (tier 1 wiring).
        "db": _NoDb(),
        "open_slide": lambda p: (_ for _ in ()).throw(
            AssertionError("open_slide must not run on the click path")
        ),
        "tcga_db": None,
        "tcga_storage_relpath": lambda fid, name=None, proj=None, case=None: (
            pathlib.Path(proj or "unknown-project")
            / (case or "unknown-case")
            / (name or f"{fid}.svs")
        ),
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the TCGA results cell grew new dependencies: {missing}"
    cell(**{p: deps[p] for p in params})
    assert list(momod.buttons) == ["Open slide (downloads if needed)"], momod.buttons
    return published, panel, momod.buttons["Open slide (downloads if needed)"]


def _click_open(catalog, tcga_dl, rows, tmp_path):
    """app.py's own TCGA results cell: real mo.ui.table selection, real button."""

    class _NoNetwork:
        def download_slide(self, *a, **k):  # pragma: no cover - must not run
            raise AssertionError("the network was touched for a local file")

    published, panel, button = _results_cell(
        catalog, tcga_dl, rows, tmp_path, _NoNetwork()
    )
    panel.table._update([0])  # select the first row
    button._update(object())  # the real click
    return published


def _run_status_cell(tcga_dl, catalog, *, open_raises=False):
    """app.py's own TCGA status/ticker cell -- where the message is composed."""
    published: dict = {}
    opened: list = []

    def _open_slide_path(p, source_kind="local"):
        opened.append((p, source_kind))
        if open_raises:
            raise ValueError("no backend handles this SVS variant")

    cell, params = _cell("# 1s ticker (created in the state cell)")
    deps = {
        "Path": pathlib.Path,
        "get_tcga_msg": lambda: published.get("msg"),
        "get_tcga_records": lambda: [],
        "mo": mo,
        "open_slide_path": _open_slide_path,
        "set_tcga_msg": lambda v: published.__setitem__("msg", v),
        "set_tcga_records": lambda v: published.__setitem__("records", v),
        "tcga_catalog": catalog,
        "tcga_dl": tcga_dl,
        "tcga_panel": __import__("hescope.tcga_panel", fromlist=["x"]),
        "tcga_ticker": mo.ui.refresh(options=["1s"], default_interval="1s"),
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the TCGA status cell grew new dependencies: {missing}"
    cell(**{p: deps[p] for p in params})
    return published, opened


# --- R07-13 ----------------------------------------------------------------


def test_an_already_downloaded_slide_is_not_reported_as_downloaded(
    catalog, tmp_path
):
    local = tmp_path / "TCGA-XX-0001.svs"
    local.write_bytes(b"not the md5 the catalog recorded")
    catalog.upsert([_record()])
    catalog.mark_downloaded("abc-123", str(local))
    rows = catalog.search(limit=100000)
    tcga_dl = _new_dl()

    _click_open(catalog, tcga_dl, rows, tmp_path)

    assert tcga_dl["open_path"] == str(local)
    assert tcga_dl["thread"] is None, "a worker ran for a file already on disk"
    assert tcga_dl["downloaded"] is False

    published, opened = _run_status_cell(tcga_dl, catalog)

    assert opened == [(str(local), "tcga")]
    kind, text = published["msg"]
    assert kind == "success"
    assert "Downloaded and opened" not in text, (
        "nothing was fetched and no md5 was checked on this path, but the "
        f"panel claimed a download: {text!r}"
    )
    assert "already downloaded" in text and "TCGA-XX-0001.svs" in text


def test_a_real_download_is_still_reported_as_downloaded(catalog, tmp_path):
    """Guard against the fix overreaching."""
    tcga_dl = _new_dl()
    tcga_dl.update(open_path=str(tmp_path / "TCGA-YY-0002.svs"), downloaded=True)

    published, opened = _run_status_cell(tcga_dl, catalog)

    assert published["msg"] == ("success", "Downloaded and opened TCGA-YY-0002.svs")


# --- R01-4 / R07-18 --------------------------------------------------------


@pytest.mark.parametrize("downloaded", [True, False])
def test_a_slide_that_cannot_be_opened_is_never_reported_as_opened(
    catalog, tmp_path, downloaded
):
    """R01-4's fix, finally pinned.

    A verified download can still be unopenable -- no backend handles that SVS
    variant -- and the message must not claim otherwise. Both entry points
    reach the same ``open_slide_path`` call, so both are exercised.
    """
    tcga_dl = _new_dl()
    tcga_dl.update(
        open_path=str(tmp_path / "TCGA-ZZ-0003.svs"), downloaded=downloaded
    )

    published, opened = _run_status_cell(tcga_dl, catalog, open_raises=True)

    assert opened, "open_slide_path was never called"
    kind, text = published["msg"]
    assert kind == "danger", (
        "opening the slide raised, and the panel still reported success: "
        f"{published['msg']!r}"
    )
    assert "TCGA-ZZ-0003.svs" in text and "opening it failed" in text
    assert "no backend handles this SVS variant" in text


def test_the_open_path_handoff_is_consumed_exactly_once(catalog, tmp_path):
    tcga_dl = _new_dl()
    tcga_dl.update(open_path=str(tmp_path / "s.svs"), downloaded=True)

    _p1, opened1 = _run_status_cell(tcga_dl, catalog)
    _p2, opened2 = _run_status_cell(tcga_dl, catalog)

    assert len(opened1) == 1 and opened2 == []


# --- R07-17 (TCGA leg) -----------------------------------------------------


def test_starting_a_download_does_not_discard_an_undrained_open_path(
    catalog, tmp_path
):
    """A completed, md5-verified download would otherwise sit on disk, never
    opened and never mentioned."""
    done = tmp_path / "finished.svs"
    done.write_bytes(b"x")
    catalog.upsert([_record(file_id="new-1", name="not-yet.svs")])
    rows = catalog.search(limit=100000)
    tcga_dl = _new_dl()
    tcga_dl.update(
        open_path=str(done), downloaded=True, msg=("success", "Downloaded finished.svs")
    )

    class _Client:
        def download_slide(self, *a, **k):
            raise RuntimeError("network down")

    _published, panel, button = _results_cell(
        catalog, tcga_dl, rows, tmp_path, _Client()
    )
    panel.table._update([0])
    button._update(object())

    assert tcga_dl["open_path"] == str(done), (
        "the click reset the same slots the worker writes, so a finished "
        "download's open_path was discarded before the 1s ticker ever saw it"
    )
    if tcga_dl["thread"] is not None:
        tcga_dl["thread"].join(timeout=30)


# --- R07-15 / R07-4 --------------------------------------------------------


def _click_browse(catalog):
    published: dict = {}
    panel = _PanelSpy()
    cell, params = _cell("def _on_browse_catalog(_v):")
    deps = {
        "mo": mo,
        "set_tcga_msg": lambda v: published.__setitem__("msg", v),
        "set_tcga_records": lambda v: published.__setitem__("records", v),
        "tcga_catalog": catalog,
        "tcga_client": object(),
        # the search half of this cell now also seeds the TCGA hierarchy
        "tcga_db": None,
        "tcga_hits_to_rows": lambda hits: [],
        "tcga_panel": panel,
    }
    missing = [p for p in params if p not in deps]
    assert not missing, f"the TCGA filter cell grew new dependencies: {missing}"
    cell(**{p: deps[p] for p in params})
    panel.switch._update(True)  # the real 'Browse local catalog' toggle
    return published


def test_browsing_the_catalog_lists_all_of_it_and_says_so(catalog):
    catalog.upsert([_record(file_id=f"id-{i}", name=f"s{i}.svs") for i in range(250)])
    assert catalog.stats()["total"] == 250

    published = _click_browse(catalog)

    assert len(published["records"]) == 250, (
        "'Browse local catalog' showed SlideCatalog.search's default 200 rows "
        "while the status line right above it reads 'catalog: 250 slides' -- a "
        "user who catalogued more than 200 concludes an early download is gone"
    )
    kind, text = published["msg"]
    assert kind == "info" and "250" in text, (
        "the browse wrote no message of its own, so a red 'GDC search failed' "
        f"from an earlier click stands over a successful listing: {published}"
    )
