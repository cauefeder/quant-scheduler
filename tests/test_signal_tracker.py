"""Tests for signal_tracker.py — schema, pragmas, and migration setup."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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


def _fake_gamma_response(closed: bool, yes_price: float = 1.0):
    """Mimic the shape returned by https://gamma-api.polymarket.com/markets?slug=…"""
    return [{
        "slug": "will-x-happen",
        "closed": closed,
        "resolutionSource": "https://example.com" if closed else "",
        "outcomePrices": json.dumps([str(yes_price), str(1.0 - yes_price)]),
    }]


def test_resolve_polymarket_yes_win(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="will-x-happen",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_market",
                      return_value=_fake_gamma_response(closed=True, yes_price=1.0)):
        resolved = fresh_tracker.resolve_polymarket_signals()
    assert resolved == 1
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"
    # entry_price=0.40, kelly_bet=10 → payout = 10 * (1/0.40 - 1) = 15
    assert row["actual_pnl"] == pytest.approx(15.0)


def test_resolve_polymarket_no_win(fresh_tracker):
    """NO bet at market_price=0.30 → entry_price=0.70 → win pays 10*(1/0.70 - 1) ≈ 4.29."""
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="NO",
        estimated_edge=0.05, market_price=0.30, market_slug="will-x-happen",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_market",
                      return_value=_fake_gamma_response(closed=True, yes_price=0.0)):
        fresh_tracker.resolve_polymarket_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"
    assert row["actual_pnl"] == pytest.approx(10.0 * (1.0 / 0.70 - 1.0))


def test_resolve_polymarket_loss(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="will-x-happen",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_market",
                      return_value=_fake_gamma_response(closed=True, yes_price=0.0)):
        fresh_tracker.resolve_polymarket_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "LOSS"
    assert row["actual_pnl"] == pytest.approx(-10.0)


def test_resolve_polymarket_skips_open_markets(fresh_tracker):
    fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="will-x-happen",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_market",
                      return_value=_fake_gamma_response(closed=False)):
        resolved = fresh_tracker.resolve_polymarket_signals()
    assert resolved == 0


def test_resolve_straddle_win(fresh_tracker):
    """move=8% > breakeven=5% (straddle_cost 4% + cost_estimate 1%) → WIN."""
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="straddle", direction="STRADDLE",
        estimated_edge=0.02, market_price=70000.0, ticker="BTC-USD",
        kelly_bet=100.0, cost_estimate=0.01,
        raw_features={"straddle_cost": 0.04},
    )
    with patch.object(fresh_tracker, "_fetch_btc_price_at",
                      return_value=70000.0 * 1.08):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"
    # move/breakeven = 0.08/0.05 = 1.6, pnl = 100 * (1.6 - 1) = 60
    assert row["actual_pnl"] == pytest.approx(60.0, rel=1e-3)


def test_resolve_straddle_loss_capped_at_premium(fresh_tracker):
    """move=0 → loss capped at -kelly_bet (premium outlay)."""
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="straddle", direction="STRADDLE",
        estimated_edge=0.02, market_price=70000.0, ticker="BTC-USD",
        kelly_bet=100.0, cost_estimate=0.01,
        raw_features={"straddle_cost": 0.04},
    )
    with patch.object(fresh_tracker, "_fetch_btc_price_at", return_value=70000.0):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "LOSS"
    assert row["actual_pnl"] == pytest.approx(-100.0)


def test_resolve_trend_long_win(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="trend_direction", direction="LONG",
        estimated_edge=0.01, market_price=70000.0, ticker="BTC-USD",
        kelly_bet=100.0,
        raw_features={"timeframe": "1D"},
    )
    with patch.object(fresh_tracker, "_fetch_btc_price_at", return_value=72100.0):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"
    # change = 2100/70000 = 0.03 → pnl = 100 * 0.03 = 3
    assert row["actual_pnl"] == pytest.approx(3.0, rel=1e-3)


def test_resolve_trend_short_loss(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="trend_direction", direction="SHORT",
        estimated_edge=0.01, market_price=70000.0, ticker="BTC-USD",
        kelly_bet=100.0,
        raw_features={"timeframe": "1H"},
    )
    with patch.object(fresh_tracker, "_fetch_btc_price_at", return_value=72100.0):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "LOSS"
    assert row["actual_pnl"] == pytest.approx(-3.0, rel=1e-3)


def test_update_daily_metrics_aggregates_per_system(fresh_tracker):
    # Two PolyTraders signals, one WIN one LOSS, both resolved today
    fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, kelly_bet=10.0,
        market_slug="market-1", cost_estimate=0.02,
    )
    fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.06, market_price=0.50, kelly_bet=10.0,
        market_slug="market-2", cost_estimate=0.02,
    )
    # Manually mark them resolved
    import sqlite3
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    conn.execute("UPDATE signals SET outcome='WIN', actual_pnl=15.0, resolved_at=? WHERE market_slug='market-1'",
                 (datetime.now(timezone.utc).isoformat(),))
    conn.execute("UPDATE signals SET outcome='LOSS', actual_pnl=-10.0, resolved_at=? WHERE market_slug='market-2'",
                 (datetime.now(timezone.utc).isoformat(),))
    conn.commit()
    conn.close()

    fresh_tracker.update_daily_metrics()

    summary = fresh_tracker.get_performance_summary(system="polytraders", days=30)
    assert summary["signals_resolved"] == 2
    assert summary["win_rate"] == pytest.approx(0.5)
    assert summary["total_pnl"] == pytest.approx(5.0)


def test_log_signal_auto_initializes_schema_on_fresh_db(tmp_db_path, monkeypatch):
    """Regression: log_signal must create schema when called against a DB that was
    never explicitly init_db'd. Subsystems rely on this — otherwise their
    try/except swallows 'no such table' errors and every signal is silently lost."""
    import importlib
    import signal_tracker
    importlib.reload(signal_tracker)
    monkeypatch.setattr(signal_tracker, "DB_PATH", tmp_db_path)
    assert not tmp_db_path.exists()

    sig_id = signal_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="auto-init-test",
    )
    assert sig_id > 0

    conn = sqlite3.connect(tmp_db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "signals" in tables
    assert "schema_version" in tables
