"""Tests for cost_model.py — transaction cost estimation per market type."""
from __future__ import annotations

import pytest


def test_polymarket_liquid_market_low_cost():
    from cost_model import estimate_cost
    cost = estimate_cost("polymarket", price=0.50, size_usd=100, liquidity=100_000, spread=0.01)
    assert 0 < cost < 0.05  # < 5% on a deep liquid market


def test_polymarket_illiquid_high_cost():
    from cost_model import estimate_cost
    cost = estimate_cost("polymarket", price=0.50, size_usd=100, liquidity=1_000, spread=0.04)
    assert cost > 0.10  # > 10% on a thin market


def test_polymarket_small_bet_gas_dominated():
    from cost_model import estimate_cost
    big = estimate_cost("polymarket", price=0.50, size_usd=100, liquidity=10_000, spread=0.01)
    tiny = estimate_cost("polymarket", price=0.50, size_usd=1, liquidity=10_000, spread=0.01)
    assert tiny > big  # gas eats a small bet


def test_btc_straddle_flat():
    from cost_model import estimate_cost
    assert estimate_cost("btc_straddle", price=70000, size_usd=500) == pytest.approx(0.025)


def test_spot_signals_zero_cost():
    from cost_model import estimate_cost
    assert estimate_cost("btc_spot", price=70000, size_usd=100) == 0.0
    assert estimate_cost("equity_spot", price=400, size_usd=100) == 0.0


def test_cost_always_non_negative():
    from cost_model import estimate_cost
    cost = estimate_cost("polymarket", price=0.50, size_usd=0, liquidity=0, spread=0)
    assert cost >= 0


def test_cost_bounded_below_50pct():
    from cost_model import estimate_cost
    cost = estimate_cost("polymarket", price=0.50, size_usd=10_000, liquidity=100, spread=0.10)
    assert cost < 0.50


def test_unknown_market_type_returns_zero():
    from cost_model import estimate_cost
    assert estimate_cost("unknown_thing", price=1, size_usd=1) == 0.0


def test_polymarket_unknown_liquidity_does_not_trigger_cost_cap():
    """Regression: Polymarket API sometimes returns liquidity=0/null. Earlier formula
    divided by max(liquidity, 1.0), which made impact explode and capped every signal
    at the _COST_CAP. Unknown liquidity must fall back to spread+gas only."""
    from cost_model import estimate_cost
    cost = estimate_cost("polymarket", price=0.5, size_usd=40, liquidity=0.0, spread=0.0)
    # half_spread floor 0.005 + gas_pct 0.05/40 ≈ 0.00125 → one_way ≈ 0.00625 → rt ≈ 0.0125
    assert cost < 0.05, f"Unknown-liquidity cost should be small, got {cost}"


def test_polymarket_pathological_spread_does_not_trigger_cost_cap():
    """Regression: thin CLOB books often report ask-bid widths approaching 1.0 when
    there are no real counterparties. Earlier half_spread was uncapped, so every
    signal on a thin book hit the _COST_CAP. Spread must be capped per leg too."""
    from cost_model import estimate_cost
    cost = estimate_cost("polymarket", price=0.5, size_usd=40, liquidity=5_000, spread=1.0)
    # Capped: half_spread ≤ 0.10, impact ≤ 0.10 → one_way ≤ 0.20 + small gas → rt ≤ 0.40
    assert cost < 0.45, f"Pathological-spread cost should not hit cap, got {cost}"
