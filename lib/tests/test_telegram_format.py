"""Unit tests for lib/telegram_format helpers. Pure functions, no IO."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lib.telegram_format import (
    footer,
    header,
    signal_block,
    stat_line,
    summary_panel,
    tail_summary,
    truncate_smart,
)


# ── header ──────────────────────────────────────────────────────────────────
def test_header_format() -> None:
    ts = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    got = header("Polymarket Kelly", ts)
    assert got == "📊 Polymarket Kelly  ·  2026-05-09  ·  12:00 UTC"


def test_header_custom_emoji() -> None:
    ts = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    got = header("BTC Leverage", ts, emoji="🟢")
    assert got.startswith("🟢 BTC Leverage")


# ── stat_line ───────────────────────────────────────────────────────────────
def test_stat_line_pairs() -> None:
    got = stat_line(("Bankroll", "$1,000"), ("Bets", "3"), ("Staked", "$43"))
    assert got == "Bankroll $1,000  ·  Bets 3  ·  Staked $43"


def test_stat_line_empty_returns_empty() -> None:
    assert stat_line() == ""


# ── signal_block ────────────────────────────────────────────────────────────
def test_signal_block_shape() -> None:
    got = signal_block(
        rank=1,
        headline="$15.31 · edge +6.0%",
        title="Italy wins Eurovision 2026",
        details=[
            "Yes @ 3.1%  ·  resolves 6d",
            "vol 24h $63k  ·  liq $78k",
        ],
    )
    expected = (
        "● #1  $15.31 · edge +6.0%\n"
        "  Italy wins Eurovision 2026\n"
        "  Yes @ 3.1%  ·  resolves 6d\n"
        "  vol 24h $63k  ·  liq $78k"
    )
    assert got == expected


def test_signal_block_no_details() -> None:
    got = signal_block(rank=2, headline="$5 · edge +3%", title="Some market", details=[])
    assert got == "● #2  $5 · edge +3%\n  Some market"


# ── tail_summary ────────────────────────────────────────────────────────────
def test_tail_summary_with_range() -> None:
    got = tail_summary(7, (0.03, 0.05), suffix="on polymarket.com")
    assert got == "+ 7 more (edge 3-5%) on polymarket.com"


def test_tail_summary_zero_returns_empty() -> None:
    assert tail_summary(0, (0.03, 0.05)) == ""


def test_tail_summary_no_range() -> None:
    got = tail_summary(3, None, suffix="on Polymarket")
    assert got == "+ 3 more on Polymarket"


# ── summary_panel ───────────────────────────────────────────────────────────
def test_summary_panel_two_columns() -> None:
    got = summary_panel(
        [
            ("Bankroll", "$1,000", "Open", "$43.06"),
            ("Edge avg", "+5.3%", "EV", "+$8.20"),
            ("Exposure", "4.1%", "Worst", "-$41"),
        ]
    )
    expected = (
        "│ Bankroll $1,000     Open  $43.06\n"
        "│ Edge avg +5.3%      EV    +$8.20\n"
        "│ Exposure 4.1%       Worst -$41"
    )
    assert got == expected


# ── footer ──────────────────────────────────────────────────────────────────
def test_footer_default_disclaimer() -> None:
    assert footer() == "\n— Not financial advice."


def test_footer_custom_text() -> None:
    assert footer("Educational only.") == "\n— Educational only."


# ── truncate_smart ──────────────────────────────────────────────────────────
def test_truncate_smart_under_limit() -> None:
    text = "short content"
    assert truncate_smart(text, limit=100) == text


def test_truncate_smart_at_limit() -> None:
    # exactly limit chars (with trailing newline = 100 chars total)
    text = "a" * 99 + "\n"
    assert truncate_smart(text, limit=100) == text


def test_truncate_smart_cuts_at_last_newline() -> None:
    lines = ["line one", "line two", "line three", "line four"]
    text = "\n".join(lines)
    # Force cut: limit small enough to drop "line four"
    got = truncate_smart(text, limit=len("line one\nline two\nline three") + 2)
    assert got.endswith("…")
    assert "line four" not in got
    assert "line three" in got


def test_truncate_smart_no_newline_in_window() -> None:
    text = "x" * 5000  # single huge line
    got = truncate_smart(text, limit=100)
    assert got.endswith("…")
    assert len(got) <= 100
