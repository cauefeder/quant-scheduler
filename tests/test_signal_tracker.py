"""Tests for signal_tracker.py — schema, pragmas, and migration setup."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def test_init_db_creates_signals_table(fresh_tracker):
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    conn.close()
    assert "signals" in tables
    assert "daily_metrics" in tables
    assert "schema_version" in tables


def test_init_db_sets_wal_mode(fresh_tracker):
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_signals_table_has_entry_price_column(fresh_tracker):
    """Spec B3: entry_price must exist for direction-aware PnL."""
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    conn.close()
    expected = {
        "id", "system", "signal_type", "market_slug", "ticker", "condition_id",
        "direction", "estimated_edge", "estimated_prob", "market_price",
        "entry_price", "kelly_bet", "kelly_fraction", "signal_tier",
        "raw_features", "cost_estimate", "net_edge", "created_at",
        "resolved_at", "outcome", "actual_pnl", "resolution_data",
    }
    assert expected.issubset(cols), f"Missing columns: {expected - cols}"


def test_schema_version_starts_at_1(fresh_tracker):
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    conn.close()
    assert version == 1


def test_log_signal_returns_id(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="polytraders",
        signal_type="polymarket",
        direction="YES",
        estimated_edge=0.05,
        market_price=0.40,
        market_slug="will-x-happen",
    )
    assert isinstance(sig_id, int) and sig_id > 0


def test_log_signal_yes_entry_price_equals_market_price(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="x",
    )
    row = fresh_tracker.get_signal(sig_id)
    assert row["entry_price"] == pytest.approx(0.40)


def test_log_signal_no_entry_price_is_complement(fresh_tracker):
    """Spec B2: NO bets have entry_price = 1 - market_price."""
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="NO",
        estimated_edge=0.05, market_price=0.30, market_slug="x",
    )
    row = fresh_tracker.get_signal(sig_id)
    assert row["entry_price"] == pytest.approx(0.70)


def test_log_signal_polymarket_requires_slug(fresh_tracker):
    """Spec B4: Polymarket signals MUST have market_slug for resolution."""
    with pytest.raises(ValueError, match="market_slug"):
        fresh_tracker.log_signal(
            system="poly", signal_type="polymarket", direction="YES",
            estimated_edge=0.05, market_price=0.40,
        )


def test_log_signal_straddle_requires_straddle_cost(fresh_tracker):
    """Spec W1: straddle resolver needs raw_features['straddle_cost']."""
    with pytest.raises(ValueError, match="straddle_cost"):
        fresh_tracker.log_signal(
            system="modeltelegra", signal_type="straddle", direction="STRADDLE",
            estimated_edge=0.02, market_price=70000.0, ticker="BTC-USD",
            raw_features={},
        )


def test_log_signal_trend_requires_timeframe(fresh_tracker):
    """Spec W2: trend resolver needs raw_features['timeframe']."""
    with pytest.raises(ValueError, match="timeframe"):
        fresh_tracker.log_signal(
            system="modeltelegra", signal_type="trend_direction", direction="LONG",
            estimated_edge=0.01, market_price=70000.0, ticker="BTC-USD",
            raw_features={},
        )


def test_log_signal_persists_raw_features_as_json(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="x",
        raw_features={"signal_strength": 0.8, "n_traders": 5},
    )
    row = fresh_tracker.get_signal(sig_id)
    import json
    parsed = json.loads(row["raw_features"])
    assert parsed["n_traders"] == 5


def test_log_signal_deduplication_within_run(fresh_tracker):
    common = dict(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="will-x-happen",
    )
    first = fresh_tracker.log_signal(**common)
    second = fresh_tracker.log_signal(**common)
    assert first == second  # second call returns the same ID, no duplicate row

    import sqlite3
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE market_slug='will-x-happen'"
    ).fetchone()[0]
    conn.close()
    assert count == 1
