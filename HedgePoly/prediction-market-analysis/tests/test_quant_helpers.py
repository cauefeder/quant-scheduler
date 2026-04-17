"""Tests for notebooks/quant_helpers.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "notebooks"))

import numpy as np
import pandas as pd
import pytest
from quant_helpers import (
    clean_df, ASSETS, FX_TICKERS,
    add_features,
    classify_regime, classify_regimes_series,
    fit_har_model,
    fit_xgb_signal,
    compute_kelly,
    build_summary_row, build_telegram_html, build_summary_table_text,
)


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


# ---------------------------------------------------------------------------
# TASK 2: Data layer tests
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# TASK 3: Feature Engineering tests
# ---------------------------------------------------------------------------

def test_vr_rank_bounded():
    df = _make_ohlcv(n=1300)
    result = add_features(df, "SPY")
    rank = result["VR_rank"].dropna()
    assert rank.between(0, 1).all(), "VR_rank must be in [0, 1]"


def test_candle_det_formula():
    df = _make_ohlcv(n=1300)
    result = add_features(df, "SPY")
    expected_det = (df["open"] * df["close"] - df["high"] * df["low"]) / df["close"] ** 2
    pd.testing.assert_series_equal(
        result["candle_det_norm"].dropna(),
        expected_det.dropna().rename("candle_det_norm"),
        check_names=False,
    )


def test_har_lags_not_null_after_warmup():
    df = _make_ohlcv(n=1300)
    result = add_features(df, "SPY")
    tail = result.iloc[30:]
    assert tail[["RV_1d", "RV_5d", "RV_22d"]].isna().sum().sum() == 0


def test_momentum_log_returns():
    df = _make_ohlcv(n=1300)
    result = add_features(df, "SPY")
    expected_mom20 = np.log(df["close"] / df["close"].shift(20))
    pd.testing.assert_series_equal(
        result["mom_20"].dropna(),
        expected_mom20.dropna().rename("mom_20"),
        check_names=False,
    )


def test_fx_no_volume_features():
    df = _make_ohlcv(n=1100)
    df["volume"] = 0.0
    result = add_features(df, "EURUSD=X")
    assert "VR_rank" in result.columns


# ---------------------------------------------------------------------------
# TASK 4: Regime Detection tests
# ---------------------------------------------------------------------------

def test_regime_trending_high_vr():
    assert classify_regime(vr_rank=0.80, candle_det_norm=0.1) == "Trending"


def test_regime_choppy_low_vr():
    assert classify_regime(vr_rank=0.20, candle_det_norm=-0.1) == "Choppy"


def test_regime_ranging_mid_vr():
    assert classify_regime(vr_rank=0.50, candle_det_norm=0.0) == "Ranging"


def test_regime_tiebreaker_upper_bull():
    assert classify_regime(vr_rank=0.70, candle_det_norm=0.05) == "Trending"


def test_regime_tiebreaker_upper_bear():
    assert classify_regime(vr_rank=0.70, candle_det_norm=-0.05) == "Ranging"


def test_regime_tiebreaker_lower_bull():
    assert classify_regime(vr_rank=0.30, candle_det_norm=0.05) == "Ranging"


def test_regime_tiebreaker_lower_bear():
    assert classify_regime(vr_rank=0.30, candle_det_norm=-0.05) == "Choppy"


def test_classify_regimes_series():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    regimes = classify_regimes_series(df)
    assert set(regimes.dropna().unique()).issubset({"Trending", "Ranging", "Choppy"})


# ---------------------------------------------------------------------------
# TASK 5: HAR-OLS Volatility Forecasting tests
# ---------------------------------------------------------------------------

def test_har_returns_forecast_series():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_har_model(df)
    assert "vol_forecast" in result.columns
    assert "vol_regime" in result.columns


def test_har_no_lookahead_in_target():
    """vol_forecast must be NaN for the training portion (first 80%)."""
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_har_model(df)
    n_train = int(len(result.dropna(subset=["RV_1d"])) * 0.8)
    train_forecasts = result["vol_forecast"].iloc[:n_train]
    assert train_forecasts.isna().all(), (
        "vol_forecast must be NaN in the training period"
    )


def test_har_vol_regime_values():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_har_model(df)
    valid = result["vol_regime"].dropna()
    assert set(valid.unique()).issubset({"Expanding", "Contracting"})


def test_har_metrics_returned():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    _, metrics = fit_har_model(df, return_metrics=True)
    assert "r2" in metrics and "mae" in metrics and "coefficients" in metrics


# ---------------------------------------------------------------------------
# TASK 6: XGBoost Direction Signal tests
# ---------------------------------------------------------------------------

def test_xgb_returns_probabilities():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_xgb_signal(df)
    for col in ["xgb_signal", "p_bearish", "p_neutral", "p_bullish"]:
        assert col in result.columns, f"Missing column: {col}"


def test_xgb_probs_sum_to_one():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_xgb_signal(df)
    probs = result[["p_bearish", "p_neutral", "p_bullish"]].dropna()
    sums = probs.sum(axis=1)
    assert (sums.round(4) == 1.0).all(), "Probabilities must sum to 1"


def test_xgb_no_shuffle_chronological():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result, meta = fit_xgb_signal(df, return_meta=True)
    split_idx = meta["split_idx"]
    assert split_idx > 0 and split_idx < len(df), "Split index must be valid"
    assert meta["q33"] < meta["q67"], "Tercile bounds must be ordered"


def test_xgb_signal_values():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_xgb_signal(df)
    valid = result["xgb_signal"].dropna()
    assert set(valid.unique()).issubset({-1, 0, 1})


# ---------------------------------------------------------------------------
# TASK 7: Half-Kelly Position Sizing tests
# ---------------------------------------------------------------------------

def test_kelly_bullish():
    result = compute_kelly(p_bullish=0.67, p_neutral=0.20, p_bearish=0.13)
    assert result["side"] == "LONG"
    assert abs(result["kelly_pct"] - 10.0) < 0.01  # capped at 10%


def test_kelly_bearish():
    result = compute_kelly(p_bullish=0.10, p_neutral=0.20, p_bearish=0.70)
    assert result["side"] == "SHORT"
    assert result["kelly_pct"] > 0


def test_kelly_neutral_no_edge():
    result = compute_kelly(p_bullish=0.35, p_neutral=0.40, p_bearish=0.25)
    assert result["side"] == "FLAT"
    assert result["kelly_pct"] == 0.0


def test_kelly_p_win_always_shown():
    result = compute_kelly(p_bullish=0.35, p_neutral=0.40, p_bearish=0.25)
    assert result["p_win"] == pytest.approx(0.35)


def test_kelly_capped_at_10_pct():
    result = compute_kelly(p_bullish=0.95, p_neutral=0.03, p_bearish=0.02)
    assert result["kelly_pct"] == pytest.approx(10.0)


def test_kelly_formula_exact():
    result = compute_kelly(p_bullish=0.60, p_neutral=0.20, p_bearish=0.20)
    full_kelly = 2 * 0.60 - 1
    expected = min(full_kelly * 0.5 * 100, 10.0)
    assert result["kelly_pct"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# TASK 8: Report Builder tests
# ---------------------------------------------------------------------------

def test_xgb_no_lookahead_in_train():
    """xgb_signal must be NaN for the training partition (first 80%)."""
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result, meta = fit_xgb_signal(df, return_meta=True)
    n_train = meta["split_idx"]
    for col in ["xgb_signal", "p_bearish", "p_neutral", "p_bullish"]:
        assert result[col].iloc[:n_train].isna().all(), (
            f"{col} must be NaN in the training partition"
        )


def test_build_summary_row_keys():
    row = build_summary_row("SPY", "Trending", "Expanding", "LONG", 0.67, 8.5)
    for key in ["ticker", "regime", "vol_regime", "side", "signal_label", "p_win", "kelly_pct"]:
        assert key in row


def test_telegram_html_contains_all_tickers():
    signals = [
        build_summary_row("SPY",      "Trending",  "Expanding",   "LONG",  0.67, 8.5),
        build_summary_row("BTC-USD",  "Choppy",    "Contracting", "FLAT",  0.51, 0.0),
        build_summary_row("GC=F",     "Ranging",   "Expanding",   "LONG",  0.61, 5.5),
        build_summary_row("EURUSD=X", "Ranging",   "Contracting", "SHORT", 0.58, 4.0),
    ]
    html = build_telegram_html(signals, date_str="2026-04-16")
    for ticker in ["SPY", "BTC-USD", "GC=F", "EURUSD=X"]:
        assert ticker in html


def test_telegram_html_is_valid_html():
    signals = [build_summary_row("SPY", "Trending", "Expanding", "LONG", 0.67, 8.5)]
    html = build_telegram_html(signals, date_str="2026-04-16")
    assert html.startswith("<")
    assert "<b>" in html or "<code>" in html


def test_summary_table_text_contains_all_tickers():
    signals = [
        build_summary_row("SPY",      "Trending",  "Expanding",   "LONG",  0.67, 8.5),
        build_summary_row("BTC-USD",  "Choppy",    "Contracting", "FLAT",  0.51, 0.0),
        build_summary_row("GC=F",     "Ranging",   "Expanding",   "LONG",  0.61, 5.5),
        build_summary_row("EURUSD=X", "Ranging",   "Contracting", "SHORT", 0.58, 4.0),
    ]
    text = build_summary_table_text(signals, date_str="2026-04-17")
    for ticker in ["SPY", "BTC-USD", "GC=F", "EURUSD=X"]:
        assert ticker in text
    assert "2026-04-17" in text
