"""Tests for the TCGA download progress bar (hescope.tcga_panel).

Offline: pure HTML-string assertions, no marimo runtime or network needed.
"""

from __future__ import annotations

def _html(obj) -> str:
    return obj.text


from hescope.tcga_panel import (
    format_bytes_mb,
    progress_percent,
    progress_view,
    status_view,
)

_STATS = {"total": 3, "downloaded": 1}


def test_format_bytes_mb():
    assert format_bytes_mb(0) == "0.0 MB"
    assert format_bytes_mb(128_400_000) == "128.4 MB"
    assert format_bytes_mb(532_000_000) == "532.0 MB"


def test_progress_percent_known_total():
    assert progress_percent(0, 1000) == 0
    assert progress_percent(500, 1000) == 50
    assert progress_percent(1000, 1000) == 100


def test_progress_percent_total_none_returns_none():
    assert progress_percent(123, None) is None
    assert progress_percent(123, 0) is None


def test_progress_percent_clamped():
    assert progress_percent(-50, 1000) == 0
    assert progress_percent(5000, 1000) == 100


def test_progress_view_determinate_has_bar_percent_and_mb():
    html = _html(progress_view((128_400_000, 532_000_000)))
    assert "hescope-progress" in html
    assert "width:24%" in html
    assert "(24%)" in html
    assert "128.4 / 532.0 MB" in html
    assert "Downloading" in html
    assert "#4a7c59" in html  # muted accent, low saturation


def test_progress_view_indeterminate_when_total_none():
    html = _html(progress_view((128_400_000, None)))
    assert "hescope-progress" in html
    assert "128.4 MB" in html
    assert "%)" not in html  # no percentage when total unknown
    assert "@keyframes" in html  # animated striped bar


def test_progress_view_none_returns_none():
    assert progress_view(None) is None


def test_status_view_shows_bar_when_progress_set():
    html = _html(status_view(None, (50, 200), _STATS))
    assert "hescope-progress" in html
    assert "(25%)" in html
    assert "MB" in html


def test_status_view_no_bar_markup_when_progress_none():
    html = _html(status_view(None, None, _STATS))
    assert "hescope-progress" not in html
    assert "Downloading" not in html
