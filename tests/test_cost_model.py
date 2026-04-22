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
