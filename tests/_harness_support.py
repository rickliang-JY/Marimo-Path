"""Parsing helpers for tests/test_harness.py -- the four locks of design doc
§15.3 (``docs/HE-Scope-设计文档-数据与Harness.md``).

Per §15.0 this module lives in ``tests/`` on purpose, not in ``hescope/``:
the AST walkers and markdown-table parsers below exist only to check the
harness, they are not product code, and putting them in ``hescope/`` would
leak test-only surface into the package namespace.

This file is NOT a test module (its name does not match ``test_*.py`` /
``*_test.py``), so pytest does not collect it directly -- only
``test_harness.py`` (and anything else that imports it) exercises it.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python >= 3.11 stdlib
except ModuleNotFoundError:  # pragma: no cover -- 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent

# The literal line marimo will keep verbatim inside the marker cell's body
# (see app.py's last cell before ``if __name__ == "__main__":``). Cell
# comments survive marimo's save/regenerate cycle; a bare top-level comment
# between two ``@app.cell`` blocks does NOT (marimo regenerates app.py from
# its cell IR on every save -- see marimo/_ast/codegen.py:generate_filecontents
# -- so anything not inside a cell's body is silently dropped on the next
# save). That is why the marker lives inside a cell instead of as a free
# comment between cells.
SCRATCH_MARKER = "▼▼▼ SCRATCH ▼▼▼"

_CELL_DECORATOR_RE = re.compile(r"^@app\.cell\b")
_FOOTER_RE = re.compile(r'^if __name__ == "__main__":')


# --------------------------------------------------------------------------
# Lock two/three: app.py cell parsing (skeleton lock, granularity lock)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    index: int
    start_line: int  # 0-based line index of the ``@app.cell`` decorator
    lines: tuple[str, ...]  # source lines, trailing blank lines trimmed

    @property
    def n_lines(self) -> int:
        return len(self.lines)

    @property
    def source(self) -> str:
        return "\n".join(self.lines)


def find_marker_line(lines: list[str]) -> int | None:
    """Index of the line containing the scratch marker, or None if absent.

    Substring search, not exact-line match: the marker lives inside a cell
    body, so the line is indented and prefixed with ``#``.
    """
    for i, line in enumerate(lines):
        if SCRATCH_MARKER in line:
            return i
    return None


def parse_app_cells(path: str | Path = REPO_ROOT / "app.py") -> list[Cell]:
    """Every ``@app.cell`` block in ``app.py``, in file order.

    A cell's span runs from its ``@app.cell`` decorator line to the line
    before the next decorator (or the ``if __name__ ==`` footer for the
    last cell), with trailing blank separator lines trimmed.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if _CELL_DECORATOR_RE.match(line)]
    if not starts:
        return []
    footer = next((i for i, line in enumerate(lines) if _FOOTER_RE.match(line)), len(lines))

    cells: list[Cell] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else footer
        span = lines[start:end]
        while span and span[-1].strip() == "":
            span.pop()
        cells.append(Cell(index=idx, start_line=start, lines=tuple(span)))
    return cells


def skeleton_cells(path: str | Path = REPO_ROOT / "app.py") -> list[Cell]:
    """Cells at or before the marker cell (design doc §9.3's "骨架区").

    The marker cell itself counts as skeleton: it is human-placed and fixed
    overhead, not a place for an agent to grow logic. If no marker exists
    yet, every cell is skeleton (matches the pre-Phase-H1 state where the
    whole file is unpartitioned).
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    marker = find_marker_line(lines)
    cells = parse_app_cells(path)
    if marker is None:
        return cells
    return [c for c in cells if c.start_line <= marker]


def scratch_cells(path: str | Path = REPO_ROOT / "app.py") -> list[Cell]:
    """Cells strictly after the marker cell (design doc §9.3's "草稿区")."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    marker = find_marker_line(lines)
    if marker is None:
        return []
    return [c for c in parse_app_cells(path) if c.start_line > marker]


def skeleton_hashes(path: str | Path = REPO_ROOT / "app.py") -> dict:
    """Structural fingerprint of the skeleton region for ``skeleton.lock``.

    One sha256 per skeleton cell (order matters), plus the count, so a
    mismatch can name which cell index moved/changed instead of just
    saying "something changed".
    """
    cells = skeleton_cells(path)
    return {
        "cell_count": len(cells),
        "hashes": [hashlib.sha256(c.source.encode("utf-8")).hexdigest() for c in cells],
    }


def granularity_violations(
    cells: list[Cell], *, max_count: int, max_lines: int
) -> dict[str, tuple[int, int]]:
    """§15.3 lock three: cell count and longest-cell-length ceilings.

    Empty dict = within budget. Otherwise ``{limit_name: (observed,
    ceiling)}`` for every limit that was exceeded.
    """
    violations: dict[str, tuple[int, int]] = {}
    if len(cells) > max_count:
        violations["cell_count"] = (len(cells), max_count)
    longest = max((c.n_lines for c in cells), default=0)
    if longest > max_lines:
        violations["max_cell_lines"] = (longest, max_lines)
    return violations


def load_lock(path: str | Path = REPO_ROOT / "skeleton.lock") -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def diff_lock(current: dict, locked: dict) -> str:
    """Human-readable summary of where ``current`` and ``locked`` disagree."""
    if current["cell_count"] != locked["cell_count"]:
        return (
            f"skeleton cell count changed: locked={locked['cell_count']} "
            f"live={current['cell_count']}"
        )
    changed = [
        i
        for i, (a, b) in enumerate(zip(current["hashes"], locked["hashes"]))
        if a != b
    ]
    return f"skeleton cell(s) changed at index/indices {changed}"


# --------------------------------------------------------------------------
# Lock one: API contract (AGENTS.md / SKILL.md vs live app.py + hescope/)
# --------------------------------------------------------------------------

AGENTS_MD = REPO_ROOT / "AGENTS.md"
SKILL_MD = REPO_ROOT / "skills" / "he-scope" / "SKILL.md"


def module_scope_names(path: str | Path = REPO_ROOT / "app.py") -> set[str]:
    """Names that land in marimo's kernel globals when ``app.py`` runs.

    marimo executes each ``@app.cell`` function's body directly in the
    kernel's shared globals (the ``def _(a, b): ... return (x,)`` shape in
    the file on disk is a static DAG-ordering representation, not what
    actually runs): every top-level binding in a cell becomes a kernel
    global UNLESS its name starts with ``_`` (marimo's convention for
    cell-local names -- confirmed by many ``_vp`` / ``_center`` locals in
    this file that are never referenced from other cells). So this walks
    each cell function's top-level statements (recursing through
    if/for/while/try/with, but NOT into nested def/class/lambda bodies --
    those are separate scopes) and collects bound names, dropping any that
    start with ``_``.
    """
    import ast

    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()

    def bind(target: "ast.expr") -> None:
        if isinstance(target, ast.Name):
            if not target.id.startswith("_"):
                names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                bind(elt)
        # ast.Attribute / ast.Subscript / ast.Starred targets do not bind a
        # new module-scope NAME (``x.y = 1`` mutates an existing object).

    def walk_stmts(stmts: list["ast.stmt"]) -> None:
        for node in stmts:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    bind(t)
            elif isinstance(node, ast.AnnAssign):
                bind(node.target)
            elif isinstance(node, ast.AugAssign):
                bind(node.target)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    names.add(node.name)
                # do NOT recurse into the def/class body: separate scope.
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    if not bound.startswith("_"):
                        names.add(bound)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if bound != "*" and not bound.startswith("_"):
                        names.add(bound)
            elif isinstance(node, (ast.If, ast.While)):
                walk_stmts(node.body)
                walk_stmts(node.orelse)
            elif isinstance(node, ast.For):
                bind(node.target)
                walk_stmts(node.body)
                walk_stmts(node.orelse)
            elif isinstance(node, ast.Try):
                walk_stmts(node.body)
                for h in node.handlers:
                    walk_stmts(h.body)
                walk_stmts(node.orelse)
                walk_stmts(node.finalbody)
            elif isinstance(node, ast.With):
                for item in node.items:
                    if item.optional_vars is not None:
                        bind(item.optional_vars)
                walk_stmts(node.body)
            # Expr / Return / Pass / bare calls etc. bind nothing.

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and any(
            (isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "cell")
            or (isinstance(d, ast.Attribute) and d.attr == "cell")
            for d in node.decorator_list
        ):
            walk_stmts(node.body)

    return names


_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_BACKTICK_RE = re.compile(r"`([^`]+)`")


def _bare_names(cell_text: str) -> list[str]:
    """Extract bare callable/attribute names from a markdown table cell.

    Handles ```` `get_vp()` / `get_source()` ```` (multiple backtick spans)
    and strips ``(...)`` argument lists, keeping just the identifier a code
    agent would look up in kernel globals.
    """
    out = []
    for span in _BACKTICK_RE.findall(cell_text):
        name = span.split("(", 1)[0].strip()
        if name:
            out.append(name)
    return out


def _parse_table_first_column(text: str, heading_re: str) -> set[str]:
    """Names in the first column of the markdown table under a heading.

    Scans from the first line matching ``heading_re`` to the next ``## ``
    heading (or EOF), and pulls bare names (see ``_bare_names``) out of
    every table row's first column, skipping the header and the
    ``| --- | --- |`` separator row.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(heading_re, line):
            start = i
            break
    if start is None:
        return set()
    names: set[str] = set()
    seen_header = False
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        m = _TABLE_ROW_RE.match(line)
        if not m:
            continue
        first_col = m.group(1).split("|", 1)[0]
        if not seen_header:
            # header row itself (e.g. "Name" / "Tool"): skip it and the
            # following separator row.
            seen_header = True
            continue
        if re.fullmatch(r"[\s:-]*", first_col):
            continue  # "| --- | --- |" separator row
        names.update(_bare_names(first_col))
    return names


def parse_documented_kernel_globals(
    agents_md: str | Path = AGENTS_MD, skill_md: str | Path = SKILL_MD
) -> set[str]:
    """Kernel-global names AGENTS.md §3 and SKILL.md §2 claim exist.

    Table-driven on purpose (not "every backtick in the file"): AGENTS.md's
    prose elsewhere legitimately mentions names -- e.g. the "Countertop"
    section's forward-looking ``current_slide`` / ``available_layers`` --
    that are explicitly NOT live yet and must not be treated as a claim.
    """
    claimed = _parse_table_first_column(
        Path(agents_md).read_text(encoding="utf-8"), r"^## 3\. Entry points"
    )
    claimed |= _parse_table_first_column(
        Path(skill_md).read_text(encoding="utf-8"), r"^## 2\. Tool list"
    )
    return claimed


def undocumented_or_missing(claimed: set[str], live: set[str]) -> set[str]:
    """Names AGENTS.md/SKILL.md claim exist that are not in ``live``.

    Empty return = the contract holds (``claimed <= live``).
    """
    return claimed - live


_HESCOPE_DOTTED_RE = re.compile(r"\bhescope\.[A-Za-z_][A-Za-z0-9_.]*")


def find_hescope_dotted_paths(text: str) -> set[str]:
    """Every ``hescope.a.b.c``-shaped token mentioned in ``text``.

    Trailing sentence punctuation (a period ending the sentence) is
    stripped -- no legitimate Python dotted path ends in ``.``.
    """
    return {m.rstrip(".") for m in _HESCOPE_DOTTED_RE.findall(text)}


def resolve_dotted(dotted: str) -> object:
    """Import/attribute-resolve a dotted path like a Python REPL would.

    Tries the longest importable module prefix first, then walks the
    remaining parts as attribute access -- so both ``hescope.detect_nuclei``
    (module ``hescope``, attribute ``detect_nuclei``) and
    ``hescope.interop.geojson.export_rois_geojson`` (module
    ``hescope.interop.geojson``, attribute ``export_rois_geojson``)
    resolve correctly. Raises ModuleNotFoundError/AttributeError if the
    path does not resolve to anything real.
    """
    parts = dotted.split(".")
    last_error: Exception | None = None
    for i in range(len(parts), 0, -1):
        mod_name = ".".join(parts[:i])
        try:
            obj = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:
            last_error = exc
            continue
        for attr in parts[i:]:
            obj = getattr(obj, attr)  # AttributeError propagates, on purpose
        return obj
    raise ModuleNotFoundError(dotted) from last_error


def documented_dotted_paths(
    agents_md: str | Path = AGENTS_MD, skill_md: str | Path = SKILL_MD
) -> set[str]:
    text = Path(agents_md).read_text(encoding="utf-8") + "\n" + Path(skill_md).read_text(
        encoding="utf-8"
    )
    return find_hescope_dotted_paths(text)


# --------------------------------------------------------------------------
# Lock four: dependency headroom (§3.3 / §18.7's numpy<2.6 numba ceiling)
# --------------------------------------------------------------------------

PYPROJECT = REPO_ROOT / "pyproject.toml"

# §17's decision table: none of these may EVER be a declared dependency of
# this package (core or any optional extra) -- that is precisely the
# import-graph integration §3 rejects. valis-wsi/spatialdata/scanpy all
# pull in numba (§3.3/DESIGN-DOC-DELTA §4.2); valis-wsi additionally forces
# a numpy 2.x -> 1.26 downgrade (§3.2). umap-learn is the one dimensionality
# reduction backend in §12.4's comparison table that carries numba, so it
# stays out of core even though it is fine in an adapter env.
BANNED_ADAPTER_PACKAGES = frozenset({"valis-wsi", "spatialdata", "scanpy", "umap-learn"})


def _dep_name(spec: str) -> str:
    """PEP 508 requirement string -> bare distribution name, lowercased."""
    return re.split(r"[<>=!~\[\s;]", spec, maxsplit=1)[0].strip().lower()


def all_declared_dependency_specs(pyproject_path: str | Path = PYPROJECT) -> list[str]:
    data = tomllib.loads(Path(pyproject_path).read_text(encoding="utf-8"))
    specs = list(data["project"]["dependencies"])
    for group in data["project"].get("optional-dependencies", {}).values():
        specs.extend(group)
    return specs


def find_banned_packages(specs: list[str]) -> set[str]:
    return {name for spec in specs if (name := _dep_name(spec)) in BANNED_ADAPTER_PACKAGES}


def numpy_declared_floor(specs: list[str]) -> tuple[int, int] | None:
    """The ``>=X.Y`` floor pyproject.toml pins for numpy, if any."""
    for spec in specs:
        if _dep_name(spec) != "numpy":
            continue
        m = re.search(r">=\s*(\d+)\.(\d+)", spec)
        if m:
            return (int(m.group(1)), int(m.group(2)))
    return None


def numpy_headroom_ok(version: tuple[int, int, int]) -> bool:
    """True iff ``version`` is on the 2.x line and below the numba ceiling.

    Two facts measured in ``docs/DESIGN-DOC-DELTA.md`` §4.2 motivate the two
    halves of this check: ``valis-wsi`` forces numpy 2.5.2 -> 1.26.4 (a
    major-version downgrade off the 2.x line -- guarded by ``major == 2``),
    and ``numba==0.67.0`` (pulled in by both ``scanpy`` and ``spatialdata``)
    declares ``numpy<2.6,>=1.22`` on PyPI (guarded by ``< (2, 6)``).
    """
    major, minor = version[0], version[1]
    return major == 2 and (major, minor) < (2, 6)


def installed_version_tuple(pkg: str) -> tuple[int, int, int]:
    import importlib.metadata as metadata

    raw = metadata.version(pkg)
    nums = []
    for part in raw.split(".")[:3]:
        m = re.match(r"\d+", part)
        nums.append(int(m.group()) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]
