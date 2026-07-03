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


# ── Bug-fix regression tests ──────────────────────────────────────────────────
# Real Gamma responses for closed markets have resolutionSource='' (empty string),
# and the /markets endpoint defaults to active-only — closed markets aren't
# returned without an explicit closed=true filter. Both broke the resolver in
# prod (4,632 unresolved signals over weeks).


def test_resolve_succeeds_when_resolution_source_is_empty(fresh_tracker):
    """Closed market with resolutionSource='' must still resolve.

    This is the actual production case — Gamma sets resolutionSource='' for
    resolved markets, not None. The old resolver's `if not resolutionSource`
    check rejected every real market and reported 0 resolved.
    """
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="will-x-happen",
        kelly_bet=10.0,
    )
    fake = [{
        "slug": "will-x-happen",
        "closed": True,
        "resolutionSource": "",  # production reality, not the synthetic URL
        "outcomePrices": json.dumps(["1", "0"]),  # YES won
    }]
    with patch.object(fresh_tracker, "_fetch_gamma_market", return_value=fake):
        resolved = fresh_tracker.resolve_polymarket_signals()
    assert resolved == 1
    assert fresh_tracker.get_signal(sig_id)["outcome"] == "WIN"


def test_fetch_gamma_market_requests_closed_true(monkeypatch):
    """_fetch_gamma_market must pass closed=true so the /markets endpoint
    returns closed markets (it defaults to active-only otherwise)."""
    import signal_tracker as st
    seen_urls: list[str] = []

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"[]"

    def fake_urlopen(req, timeout=None):
        seen_urls.append(req.full_url)
        return FakeResponse()

    monkeypatch.setattr(st.urllib.request, "urlopen", fake_urlopen)
    st._fetch_gamma_market("will-x-happen")
    assert seen_urls, "urlopen should have been called"
    assert "closed=true" in seen_urls[0]
    assert "slug=will-x-happen" in seen_urls[0]


def test_resolve_skips_unsettled_market(fresh_tracker):
    """outcomePrices not at boundary (0/1) means market isn't fully settled.
    Treat as unresolved — don't write a fake outcome."""
    fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="will-x-happen",
        kelly_bet=10.0,
    )
    fake = [{
        "slug": "will-x-happen",
        "closed": True,
        "resolutionSource": "",
        "outcomePrices": json.dumps(["0.7", "0.3"]),  # not at boundary
    }]
    with patch.object(fresh_tracker, "_fetch_gamma_market", return_value=fake):
        resolved = fresh_tracker.resolve_polymarket_signals()
    assert resolved == 0


def test_resolve_handles_empty_gamma_response(fresh_tracker):
    """Slug returns no markets (slug stale / wrong) — skip cleanly."""
    fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="ghost-market",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_market", return_value=None):
        resolved = fresh_tracker.resolve_polymarket_signals()
    assert resolved == 0


def test_resolve_marks_slug_not_found_as_no_match(fresh_tracker):
    """Empty Gamma response → outcome='NO_MATCH' so resolver doesn't loop
    forever on the same dead slugs."""
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="dead-slug",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_market", return_value=[]):
        fresh_tracker.resolve_polymarket_signals()

    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "NO_MATCH"
    assert row["resolved_at"] is not None
    # Next call should NOT re-attempt this signal (already marked).
    with patch.object(fresh_tracker, "_fetch_gamma_market") as mock:
        fresh_tracker.resolve_polymarket_signals()
    mock.assert_not_called()


# ── ModelTelegra resolver bug-fix regression tests ───────────────────────────


def test_fetch_price_at_takes_ticker_argument(fresh_tracker):
    """The resolver used to hardcode yf.Ticker('BTC-USD'), so gold (GC=F)
    and silver (SI=F) signals were being resolved against BTC prices.
    The fetcher must accept the ticker explicitly."""
    import inspect
    sig = inspect.signature(fresh_tracker._fetch_price_at)
    assert "ticker" in sig.parameters
    assert "timestamp" in sig.parameters


def test_resolve_modeltelegra_uses_signal_ticker(fresh_tracker):
    """Resolver must fetch the signal's own ticker, not a hardcoded BTC."""
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="trend_direction",
        direction="LONG", ticker="GC=F", estimated_edge=0.02,
        market_price=4000.0, kelly_bet=5.0,
        raw_features={"timeframe": "1H"},
    )
    seen_tickers: list[str] = []

    def fake_fetch(*, ticker, timestamp):
        seen_tickers.append(ticker)
        return 4100.0  # gold went up

    with patch.object(fresh_tracker, "_fetch_price_at", side_effect=fake_fetch):
        fresh_tracker.resolve_modeltelegra_signals()

    assert "GC=F" in seen_tickers, f"expected GC=F to be queried, saw {seen_tickers}"
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"  # LONG + price up = WIN


def test_resolve_modeltelegra_marks_no_match_when_price_unavailable(fresh_tracker):
    """yfinance returning None (weekend, holiday, delisted) → NO_MATCH so
    the resolver doesn't keep re-fetching the same dead signal."""
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="trend_direction",
        direction="LONG", ticker="GC=F", estimated_edge=0.02,
        market_price=4000.0, kelly_bet=5.0,
        raw_features={"timeframe": "1H"},
    )
    with patch.object(fresh_tracker, "_fetch_price_at", return_value=None):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "NO_MATCH"
    assert row["resolved_at"] is not None
    # Next call should NOT re-attempt this signal
    with patch.object(fresh_tracker, "_fetch_price_at") as mock:
        fresh_tracker.resolve_modeltelegra_signals()
    mock.assert_not_called()


def test_resolve_trend_short_winning(fresh_tracker):
    """SHORT + price went down = WIN."""
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="trend_direction",
        direction="SHORT", ticker="BTC-USD", estimated_edge=0.02,
        market_price=60000.0, kelly_bet=10.0,
        raw_features={"timeframe": "1H"},
    )
    with patch.object(fresh_tracker, "_fetch_price_at", return_value=58000.0):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"
    # change = (58000 - 60000) / 60000 = -0.0333
    # magnitude = 10 * 0.0333 = 0.333
    # WIN → pnl = +0.333
    assert row["actual_pnl"] == pytest.approx(10.0 * (2000 / 60000), abs=1e-3)


def test_resolve_straddle_volatility_win(fresh_tracker):
    """Straddle wins when realised move exceeds straddle cost (relative)."""
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="straddle",
        direction="STRADDLE", ticker="BTC-USD", estimated_edge=0.10,
        market_price=60000.0, kelly_bet=10.0,
        raw_features={"straddle_cost": 0.04},  # 4% breakeven move
    )
    # Price moved 8% — twice the breakeven → big win
    with patch.object(fresh_tracker, "_fetch_price_at", return_value=64800.0):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"
    assert row["actual_pnl"] > 0


def test_resolve_straddle_quiet_loss(fresh_tracker):
    """Straddle loses when realised move stays below breakeven."""
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="straddle",
        direction="STRADDLE", ticker="BTC-USD", estimated_edge=0.10,
        market_price=60000.0, kelly_bet=10.0,
        raw_features={"straddle_cost": 0.04},
    )
    # 1% move — well under 4% breakeven
    with patch.object(fresh_tracker, "_fetch_price_at", return_value=60600.0):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "LOSS"
    assert row["actual_pnl"] < 0


def test_fetch_gamma_by_condition_id_sends_correct_query(monkeypatch):
    """The condition_id fetcher must include closed=true so Gamma returns
    resolved markets (same defensive default as the slug fetcher)."""
    import signal_tracker as st
    seen_urls: list[str] = []

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"[]"

    def fake_urlopen(req, timeout=None):
        seen_urls.append(req.full_url)
        return FakeResponse()

    monkeypatch.setattr(st.urllib.request, "urlopen", fake_urlopen)
    st._fetch_gamma_by_condition_id("0xdeadbeef")
    assert seen_urls, "urlopen should have been called"
    assert "condition_ids=0xdeadbeef" in seen_urls[0]
    assert "closed=true" in seen_urls[0]


def test_resolve_prefers_condition_id_over_slug(fresh_tracker):
    """When a signal has both slug and condition_id, the resolver must
    consult condition_id first. This is the whole point of N1 — condition_id
    is stable across market re-slugs."""
    sig_id = fresh_tracker.log_signal(
        system="poly", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40,
        market_slug="rotted-slug", condition_id="0xstable",
        kelly_bet=10.0,
    )

    def fake_slug_fetch(slug):
        # Old slug returns empty (rotted)
        return []

    fake_cid_market = [{
        "slug": "current-slug",
        "closed": True,
        "resolutionSource": "",
        "outcomePrices": json.dumps(["1", "0"]),
    }]

    with patch.object(fresh_tracker, "_fetch_gamma_market",
                      side_effect=fake_slug_fetch), \
         patch.object(fresh_tracker, "_fetch_gamma_by_condition_id",
                      return_value=fake_cid_market) as cid_mock:
        resolved = fresh_tracker.resolve_polymarket_signals()

    assert resolved == 1
    assert fresh_tracker.get_signal(sig_id)["outcome"] == "WIN"
    cid_mock.assert_called_with("0xstable")


def test_resolve_falls_back_to_slug_when_no_condition_id(fresh_tracker):
    """Signals without condition_id (e.g., alphafeed history) go through
    the slug path unchanged."""
    sig_id = fresh_tracker.log_signal(
        system="alphafeed", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40,
        market_slug="only-slug", condition_id=None,
        kelly_bet=10.0,
    )
    fake_market = [{
        "slug": "only-slug",
        "closed": True,
        "resolutionSource": "",
        "outcomePrices": json.dumps(["1", "0"]),
    }]
    with patch.object(fresh_tracker, "_fetch_gamma_market",
                      return_value=fake_market):
        resolved = fresh_tracker.resolve_polymarket_signals()
    assert resolved == 1
    assert fresh_tracker.get_signal(sig_id)["outcome"] == "WIN"


def test_resolve_no_match_when_both_lookups_fail(fresh_tracker):
    """condition_id and slug both return nothing → NO_MATCH (unchanged
    behavior), so the resolver doesn't loop forever on truly-dead signals."""
    fresh_tracker.log_signal(
        system="poly", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40,
        market_slug="dead-slug", condition_id="0xdead",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_by_condition_id",
                      return_value=[]), \
         patch.object(fresh_tracker, "_fetch_gamma_market", return_value=[]):
        fresh_tracker.resolve_polymarket_signals()

    row = fresh_tracker.get_signal(1)
    assert row["outcome"] == "NO_MATCH"


def test_trend_horizons_pinned_for_audit(fresh_tracker):
    """Regression: 1H trend horizon must stay 72 after the M2 backtest
    decision (docs/modeltelegra_horizon_backtest.md). Reverting to 24 is
    a real money policy change that should require updating this test."""
    assert fresh_tracker._TREND_HORIZONS_HOURS["1H"] == 72
    # 1D / 1W are untested by backtest yet — left at original values.
    assert fresh_tracker._TREND_HORIZONS_HOURS["1D"] == 24 * 5
    assert fresh_tracker._TREND_HORIZONS_HOURS["1W"] == 24 * 30


def test_resolve_straddle_dollar_premium_normalised(fresh_tracker):
    """ModelTelegra logs straddle_cost as a dollar premium. The resolver
    must normalise to a fractional move so the breakeven comparison
    works. Regression for the 0% WR + -$50k catastrophe on BTC straddles.
    """
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="straddle",
        direction="STRADDLE", ticker="BTC-USD", estimated_edge=0.10,
        market_price=60000.0, kelly_bet=100.0,
        # 1734.80 is a dollar premium (raw_cost > 1.0). Resolver should
        # treat it as 1734.80 / 60000 = 2.89% breakeven.
        raw_features={"straddle_cost": 1734.80},
    )
    # 5% move (3000 / 60000) — well above the 2.89% breakeven → WIN
    with patch.object(fresh_tracker, "_fetch_price_at", return_value=63000.0):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN", (
        f"Expected WIN with 5% move vs 2.89% breakeven, got {row['outcome']}. "
        "The resolver is probably treating the dollar premium as a fraction."
    )
    assert row["actual_pnl"] > 0


def test_resolve_straddle_win(fresh_tracker):
    """move=8% > breakeven=5% (straddle_cost 4% + cost_estimate 1%) → WIN."""
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="straddle", direction="STRADDLE",
        estimated_edge=0.02, market_price=70000.0, ticker="BTC-USD",
        kelly_bet=100.0, cost_estimate=0.01,
        raw_features={"straddle_cost": 0.04},
    )
    with patch.object(fresh_tracker, "_fetch_price_at",
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
    with patch.object(fresh_tracker, "_fetch_price_at", return_value=70000.0):
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
    with patch.object(fresh_tracker, "_fetch_price_at", return_value=72100.0):
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
    with patch.object(fresh_tracker, "_fetch_price_at", return_value=72100.0):
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
