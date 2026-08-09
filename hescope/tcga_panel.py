"""marimo UI building blocks for the TCGA / GDC browser panel.

Used by app.py. Kept separate so the panel layout logic is testable and
app.py stays declarative. Everything here degrades gracefully: network or
HTTP errors are surfaced via ``mo.callout`` by the caller, never raised
into the notebook.
"""

from __future__ import annotations

from dataclasses import replace as _dc_replace
from typing import Callable, Iterable

import marimo as mo

from .tcga import SlideRecord

PROJECT_OPTIONS = [
    "ALL",
    "TCGA-BRCA",
    "TCGA-LUAD",
    "TCGA-LUSC",
    "TCGA-COAD",
    "TCGA-KIRC",
    "TCGA-GBM",
    "TCGA-OV",
]


def human_size(nbytes: int | None) -> str:
    """Human-readable file size, e.g. '207.2 MB'."""
    if not nbytes:
        return "0 MB"
    mb = float(nbytes) / 1e6
    if mb >= 1000:
        return f"{mb / 1000:.2f} GB"
    return f"{mb:.1f} MB"


def merge_download_state(
    records: Iterable[SlideRecord], catalog_records: Iterable[SlideRecord]
) -> list[SlideRecord]:
    """Return ``records`` with local_path filled in from the catalog (so the
    'downloaded' column reflects what is already on disk)."""
    known = {r.file_id: r.local_path for r in catalog_records if r.local_path}
    return [
        _dc_replace(r, local_path=known.get(r.file_id, r.local_path))
        for r in records
    ]


def records_to_rows(records: Iterable[SlideRecord]) -> list[dict]:
    """Rows for mo.ui.table: file_name, project_id, case_submitter_id,
    sample_type, file_size (human-readable MB), downloaded (yes/no).
    file_id is included so a selected row can be mapped back to a record."""
    return [
        {
            "file_id": r.file_id,
            "file_name": r.file_name,
            "project_id": r.project_id or "",
            "case_submitter_id": r.case_submitter_id or "",
            "sample_type": r.sample_type or "",
            "file_size": human_size(r.file_size),
            "downloaded": "yes" if r.local_path else "no",
        }
        for r in records
    ]


def make_filter_controls(
    on_search: Callable,
    on_browse: Callable,
) -> tuple:
    """Build the filter row: project dropdown + sample-type text filter +
    'Search GDC' button + 'Browse local catalog' toggle.

    Returns (layout, project_dropdown, sample_type_input, search_button,
    browse_switch).
    """
    project_dropdown = mo.ui.dropdown(
        options=PROJECT_OPTIONS, value="ALL", label="Project"
    )
    sample_type_input = mo.ui.text(
        label="Sample type (optional, exact match)",
        placeholder="e.g. Primary Tumor",
    )
    search_button = mo.ui.button(label="Search GDC", on_click=on_search)
    browse_switch = mo.ui.switch(
        label="Browse local catalog", on_change=on_browse
    )
    layout = mo.hstack(
        [project_dropdown, sample_type_input, search_button, browse_switch],
        justify="start",
        gap=2,
        wrap=True,
    )
    return layout, project_dropdown, sample_type_input, search_button, browse_switch


def make_results_table(records: Iterable[SlideRecord]) -> mo.ui.table:
    """Paginated results table with single-row selection enabled."""
    return mo.ui.table(
        records_to_rows(records),
        pagination=True,
        page_size=10,
        selection="single",
        label="TCGA slides (select a row, then 'Download & Open')",
    )


def format_bytes_mb(n: int) -> str:
    """Byte count as megabytes with one decimal, e.g. '128.4 MB'."""
    return f"{float(n) / 1e6:.1f} MB"


def progress_percent(done: int, total: int | None) -> int | None:
    """Integer percent complete, clamped to 0..100; None when total unknown."""
    if not total:
        return None
    return max(0, min(100, round(100 * done / total)))


# Muted, low-saturation palette (project visual standards: no bright accents).
_BAR_TRACK_STYLE = (
    "width:100%;max-width:420px;height:14px;background:#e6e3dd;"
    "border-radius:7px;overflow:hidden;margin:2px 0;"
)
_BAR_COLOR = "#4a7c59"  # muted green
_BAR_COLOR_ALT = "#5f8a6c"  # slightly lighter stripe for indeterminate state


def progress_view(progress: tuple[int, int | None] | None) -> "mo.Html | None":
    """Download progress as a plain-HTML horizontal bar + status text.

    Determinate when the total size is known ("Downloading…  128.4 / 532.0 MB
    (24%)"); an animated striped bar plus "Downloading… 128.4 MB" when it is
    not. Returns None (no bar markup) when ``progress`` is None.
    """
    if progress is None:
        return None
    done, total = progress
    pct = progress_percent(done, total)
    if pct is not None:
        text = (
            f"Downloading\u2026  {float(done) / 1e6:.1f} / "
            f"{format_bytes_mb(total)} ({pct}%)"
        )
        bar = (
            f'<div style="{_BAR_TRACK_STYLE}">'
            f'<div style="height:100%;width:{pct}%;background:{_BAR_COLOR};'
            'transition:width 0.2s ease-out;"></div></div>'
        )
        style_tag = ""
    else:
        text = f"Downloading\u2026 {format_bytes_mb(done)}"
        stripes = (
            "repeating-linear-gradient(90deg,"
            f"{_BAR_COLOR} 0px,{_BAR_COLOR} 14px,"
            f"{_BAR_COLOR_ALT} 14px,{_BAR_COLOR_ALT} 28px)"
        )
        bar = (
            f'<div style="{_BAR_TRACK_STYLE}">'
            f'<div style="height:100%;width:100%;background:{stripes};'
            "animation:hescope-tcga-slide 0.9s linear infinite;\"></div></div>"
        )
        style_tag = (
            "<style>@keyframes hescope-tcga-slide{"
            "from{background-position:0 0;}"
            "to{background-position:28px 0;}}</style>"
        )
    return mo.Html(
        f'{style_tag}<div class="hescope-progress" '
        'style="display:flex;flex-direction:column;gap:4px;">'
        f"{bar}"
        f'<div style="font-size:0.85rem;color:#5a564e;">{text}</div>'
        "</div>"
    )


def status_view(
    message: tuple[str, str] | None,
    progress: tuple[int, int | None] | None,
    stats: dict,
) -> "mo.Html":
    """Catalog stats line + optional download progress + optional callout."""
    parts: list = [
        mo.md(
            f"catalog: **{stats.get('total', 0)}** slides, "
            f"**{stats.get('downloaded', 0)}** downloaded"
        )
    ]
    bar = progress_view(progress)
    if bar is not None:
        parts.append(bar)
    if message is not None:
        kind, text = message
        parts.append(mo.callout(text, kind=kind))
    return mo.vstack(parts)
