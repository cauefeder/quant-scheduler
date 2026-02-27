"""
Unit tests for Quant Desk models.

Run with: pytest tests/test_models.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.regime import VolRegime, compute_realized_vol, detect_regime
from data.options_proxy import (
    bs_d1,
    bs_gamma,
    bs_price,
    calculate_gex,
    generate_synthetic_chain,
)
from models.model2_trend import TrendState, classify_trend
from models.model3_risk import TradeDecision, compute_risk_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_price_df(n: int = 500, base: float = 100000.0, vol: float = 0.02) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(42)
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="1h")
    returns = np.random.normal(0.0001, vol, n)
    prices = base * (1 + returns).cumprod()

    df = pd.DataFrame({
        "open": prices * (1 - np.random.uniform(0, 0.003, n)),
        "high": prices * (1 + np.random.uniform(0, 0.005, n)),
        "low": prices * (1 - np.random.uniform(0, 0.005, n)),
        "close": prices,
        "volume": np.random.uniform(1000, 5000, n),
    }, index=dates)

    return df


# ---------------------------------------------------------------------------
# Test Black-Scholes
# ---------------------------------------------------------------------------

class TestBlackScholes:
    def test_atm_call_price_positive(self):
        px = bs_price(100000, 100000, 1/365, 0.045, 0.50, is_call=True)
        assert px > 0

    def test_call_put_parity(self):
        S, K, T, r, sigma = 100000, 100000, 1/365, 0.045, 0.50
        call = bs_price(S, K, T, r, sigma, is_call=True)
        put = bs_price(S, K, T, r, sigma, is_call=False)
        # C - P = S - K*exp(-rT)
        parity_diff = call - put - (S - K * np.exp(-r * T))
        assert abs(parity_diff) < 1.0  # within $1

    def test_gamma_positive(self):
        g = bs_gamma(100000, 100000, 1/365, 0.045, 0.50)
        assert g > 0

    def test_deep_otm_call_near_zero(self):
        px = bs_price(100000, 200000, 1/365, 0.045, 0.50, is_call=True)
        assert px < 1.0


# ---------------------------------------------------------------------------
# Test Options Chain & GEX
# ---------------------------------------------------------------------------

class TestOptionsProxy:
    def test_chain_generation(self):
        chain = generate_synthetic_chain(spot=100000, atm_iv=0.50)
        assert len(chain.chain) > 5
        assert chain.spot == 100000
        assert "strike" in chain.chain.columns
        assert "call_gamma" in chain.chain.columns

    def test_gex_calculation(self):
        chain = generate_synthetic_chain(spot=100000, atm_iv=0.50)
        gex = calculate_gex(chain)
        assert gex.call_wall > 0
        assert gex.put_wall > 0
        assert gex.call_wall >= gex.put_wall
        assert gex.max_pain > 0
        assert len(gex.pin_candidates) <= 5


# ---------------------------------------------------------------------------
# Test Regime Detection
# ---------------------------------------------------------------------------

class TestRegimeDetection:
    def test_realized_vol(self):
        df = make_price_df(500)
        rv = compute_realized_vol(df["close"], window=24)
        assert not rv.dropna().empty
        assert rv.dropna().iloc[-1] > 0

    def test_detect_regime_returns_result(self):
        df = make_price_df(500)
        result = detect_regime(df)
        assert isinstance(result.regime, VolRegime)
        assert 0 <= result.rv_percentile <= 100
        assert result.expected_move_1d > 0


# ---------------------------------------------------------------------------
# Test Trend Classification
# ---------------------------------------------------------------------------

class TestTrendClassification:
    def test_classify_returns_signal_column(self):
        df = make_price_df(200, base=100, vol=0.01)
        result = classify_trend(df)
        assert "signal" in result.columns
        assert "strength" in result.columns
        valid_states = {s.value for s in TrendState}
        assert set(result["signal"].unique()).issubset(valid_states)

    def test_no_future_leak(self):
        """Signals should use shifted data (no repainting)."""
        df = make_price_df(200, base=100, vol=0.01)
        result = classify_trend(df)
        # First signal should be NaN-filled to Hold
        assert result["signal"].iloc[0] == TrendState.HOLD.value

    def test_strength_bounded(self):
        df = make_price_df(200, base=100, vol=0.01)
        result = classify_trend(df)
        assert result["strength"].min() >= 0
        assert result["strength"].max() <= 100


# ---------------------------------------------------------------------------
# Test Risk Engine
# ---------------------------------------------------------------------------

class TestRiskEngine:
    def test_positive_ev(self):
        rm = compute_risk_metrics(win_prob=0.6, avg_win=200, avg_loss=100)
        assert rm.expected_value > 0

    def test_negative_ev(self):
        rm = compute_risk_metrics(win_prob=0.3, avg_win=100, avg_loss=200)
        assert rm.expected_value < 0

    def test_kelly_positive_for_edge(self):
        rm = compute_risk_metrics(win_prob=0.6, avg_win=200, avg_loss=100)
        assert rm.kelly_fraction > 0

    def test_kelly_zero_for_no_edge(self):
        rm = compute_risk_metrics(win_prob=0.3, avg_win=100, avg_loss=200)
        assert rm.kelly_fraction == 0

    def test_position_size_bounded(self):
        rm = compute_risk_metrics(win_prob=0.9, avg_win=1000, avg_loss=10)
        assert rm.suggested_position_pct <= 0.02  # max 2%


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
