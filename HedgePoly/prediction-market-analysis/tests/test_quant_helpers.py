"""Tests for notebooks/quant_helpers.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "notebooks"))

import numpy as np
import pandas as pd
import pytest
from quant_helpers import clean_df, ASSETS, FX_TICKERS


def _make_ohlcv(n=1300, with_gaps=False):
    """Synthetic OHLCV DataFrame for testing."""
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "open":   close * (1 + np.random.uniform(-0.005, 0.005, n)),
        "high":   close * (1 + np.abs(np.random.uniform(0, 0.01, n))),
        "low":    close * (1 - np.abs(np.random.uniform(0, 0.01, n))),
        "close":  close,
        "volume": np.random.randint(1_000_000, 10_000_000, n).astype(float),
    }, index=idx)
    if with_gaps:
        df.loc[df.index[5:10], "close"] = np.nan  # insert NaN gaps
    return df


def test_clean_df_forward_fills_gaps():
    df = _make_ohlcv(with_gaps=True)
    result = clean_df(df, "SPY")
    assert result["close"].isna().sum() == 0


def test_clean_df_drops_leading_nans():
    df = _make_ohlcv()
    df.iloc[:5] = np.nan
    result = clean_df(df, "SPY")
    assert not result.iloc[0].isna().any()


def test_clean_df_raises_on_too_few_rows():
    df = _make_ohlcv(n=500)
    with pytest.raises(AssertionError):
        clean_df(df, "SPY")


def test_clean_df_fx_volume_zero_ok():
    """EURUSD=X always has volume=0 — this must not raise."""
    df = _make_ohlcv(n=1100)
    df["volume"] = 0.0
    result = clean_df(df, "EURUSD=X")   # should not raise
    assert len(result) > 0
