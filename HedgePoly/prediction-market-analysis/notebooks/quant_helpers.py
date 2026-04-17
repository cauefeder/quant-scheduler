"""
quant_helpers.py — Analytical functions for the Global Macro Quant Research Notebook.

All functions are pure (no side effects) and take/return pandas DataFrames or
plain Python types. Import this module from the notebook.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf
import statsmodels.api as sm
from sklearn.metrics import mean_absolute_error
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

ASSETS = {
    "SPY":      {"exchange_type": "equity",  "min_rows": max(900, int(0.8 * 252 * 5))},
    "BTC-USD":  {"exchange_type": "crypto",  "min_rows": max(900, int(0.8 * 365 * 5))},
    "GC=F":     {"exchange_type": "futures", "min_rows": max(900, int(0.8 * 252 * 5))},
    "EURUSD=X": {"exchange_type": "fx",      "min_rows": max(900, int(0.8 * 261 * 5))},
}
FX_TICKERS = {"EURUSD=X"}

FEATURE_COLS = ["VR_rank", "candle_det_norm", "RV_1d", "RV_5d", "RV_22d", "mom_20", "mom_60"]


def clean_df(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Standardise, forward-fill, and validate a raw yfinance DataFrame."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    # Keep only OHLCV columns
    df = df[["open", "high", "low", "close", "volume"]]
    # Forward-fill (handles FX weekends, holiday gaps)
    df = df.ffill()
    # Drop leading NaNs (rows before first valid close)
    df = df.dropna(subset=["close"])
    df = df.loc[df["close"].first_valid_index():]
    # Row count assertion
    min_rows = ASSETS[ticker]["min_rows"]
    assert len(df) >= min_rows, (
        f"{ticker}: only {len(df)} rows after cleaning, need >= {min_rows}"
    )
    return df


def download_asset(ticker: str, period: str = "5y") -> pd.DataFrame:
    """Download 5-year daily OHLCV from yfinance and clean."""
    raw = yf.download(ticker, period=period, interval="1d",
                      auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.droplevel(1)
    return clean_df(raw, ticker)


def load_all_assets(period: str = "5y") -> dict:
    """Download and clean all four macro assets. Returns dict keyed by ticker."""
    return {ticker: download_asset(ticker, period) for ticker in ASSETS}


# ---------------------------------------------------------------------------
# TASK 3: Feature Engineering
# ---------------------------------------------------------------------------

def add_features(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    Compute all features and append as columns.
    Log-returns used throughout to handle GC=F roll gaps safely.
    """
    df = df.copy()

    # --- Log returns ---
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))

    # --- Volatility Ratio (Schwager / Wilder) ---
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["ATR_14"] = tr.ewm(span=14, adjust=False).mean()
    df["VR"] = df["ATR_14"] / df["close"]
    df["VR_rank"] = (
        df["VR"]
        .rolling(252)
        .apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    )

    # --- Candle Geometry — Determinant ---
    det = df["open"] * df["close"] - df["high"] * df["low"]
    df["candle_det_norm"] = det / df["close"] ** 2

    # --- HAR Volatility Lags (Corsi 2009) ---
    df["RV_1d"]  = df["log_ret"] ** 2
    df["RV_5d"]  = df["RV_1d"].rolling(5).mean()
    df["RV_22d"] = df["RV_1d"].rolling(22).mean()

    # --- Momentum (log-return based) ---
    df["mom_20"] = np.log(df["close"] / df["close"].shift(20))
    df["mom_60"] = np.log(df["close"] / df["close"].shift(60))

    return df


# ---------------------------------------------------------------------------
# TASK 4: Regime Detection
# ---------------------------------------------------------------------------

def classify_regime(vr_rank: float, candle_det_norm: float) -> str:
    """
    Classify current market regime using VR_rank with candle determinant tiebreaker.

    Border bands evaluated first (override primary rule in ±0.05 zone):
    - 0.65-0.75: candle_det_norm > 0 -> Trending, else Ranging
    - 0.25-0.35: candle_det_norm > 0 -> Ranging,  else Choppy
    Primary thresholds (outside border bands):
    - VR_rank > 0.7  -> Trending
    - VR_rank < 0.3  -> Choppy
    - otherwise      -> Ranging
    """
    if 0.65 <= vr_rank <= 0.75:
        return "Trending" if candle_det_norm > 0 else "Ranging"
    if 0.25 <= vr_rank <= 0.35:
        return "Ranging" if candle_det_norm > 0 else "Choppy"
    if vr_rank > 0.7:
        return "Trending"
    if vr_rank < 0.3:
        return "Choppy"
    return "Ranging"


def classify_regimes_series(df: pd.DataFrame) -> pd.Series:
    """Apply classify_regime row-wise to a featured DataFrame."""
    return df.apply(
        lambda row: classify_regime(row["VR_rank"], row["candle_det_norm"])
        if pd.notna(row.get("VR_rank")) and pd.notna(row.get("candle_det_norm"))
        else None,
        axis=1,
    ).rename("regime")


# ---------------------------------------------------------------------------
# TASK 5: HAR-OLS Volatility Forecasting
# ---------------------------------------------------------------------------

def fit_har_model(df: pd.DataFrame, return_metrics: bool = False):
    """
    Fit HAR-OLS volatility model (Corsi 2009).
    Target: next-day RV (RV_1d.shift(-1)) — no lookahead.
    Train: first 80%, Test: last 20%.

    Returns df with vol_forecast and vol_regime columns appended.
    If return_metrics=True, also returns dict with r2, mae, coefficients.
    """
    df = df.copy()

    # Build target: shift by -1 so row t predicts day t+1
    df["RV_1d_target"] = df["RV_1d"].shift(-1)
    df = df.dropna(subset=["RV_1d", "RV_5d", "RV_22d", "RV_1d_target"])

    split = int(len(df) * 0.8)
    train = df.iloc[:split]
    test  = df.iloc[split:]

    feature_cols = ["RV_1d", "RV_5d", "RV_22d"]
    X_train = sm.add_constant(train[feature_cols])
    X_test  = sm.add_constant(test[feature_cols])

    model = sm.OLS(train["RV_1d_target"], X_train).fit()
    preds = model.predict(X_test)

    # Only populate forecast for test rows — train rows stay NaN
    df["vol_forecast"] = np.nan
    df.loc[test.index, "vol_forecast"] = preds.values

    # vol_regime: forecast vs recent average RV
    rv22 = df["RV_22d"]
    df["vol_regime"] = df.apply(
        lambda row: "Expanding" if pd.notna(row["vol_forecast"]) and row["vol_forecast"] > rv22[row.name]
        else ("Contracting" if pd.notna(row["vol_forecast"]) else None),
        axis=1,
    )

    if return_metrics:
        metrics = {
            "r2":           model.rsquared,
            "mae":          mean_absolute_error(test["RV_1d_target"], preds),
            "coefficients": model.params.to_dict(),
        }
        return df, metrics

    return df


# ---------------------------------------------------------------------------
# TASK 6: XGBoost Direction Signal
# ---------------------------------------------------------------------------

def fit_xgb_signal(df: pd.DataFrame, return_meta: bool = False):
    """
    Fit XGBoost 3-class direction model (Bearish=-1, Neutral=0, Bullish=+1).
    No lookahead: tercile boundaries from training set only, scaler fit on train.
    Target: 20-day forward log-return terciles.
    """
    df = df.copy()

    # Forward return — rolling sum then shift (no lookahead)
    df["fwd_ret"] = df["log_ret"].rolling(20).sum().shift(-20)
    # Drop last 20 rows to eliminate boundary target contamination
    df = df.iloc[:-20]
    df = df.dropna(subset=FEATURE_COLS + ["fwd_ret"])

    split = int(len(df) * 0.8)
    train = df.iloc[:split]
    test  = df.iloc[split:]

    # Tercile boundaries — training set ONLY
    q33 = train["fwd_ret"].quantile(0.33)
    q67 = train["fwd_ret"].quantile(0.67)

    def label(x):
        if x < q33: return -1
        if x > q67: return  1
        return 0

    train_labels = train["fwd_ret"].map(label)

    # Scaler — fit on training set only
    scaler = StandardScaler()
    X_train = scaler.fit_transform(train[FEATURE_COLS])
    X_test  = scaler.transform(test[FEATURE_COLS])

    # XGBoost needs labels 0,1,2 — remap from -1,0,1
    label_map = {-1: 0, 0: 1, 1: 2}
    inv_map   = {0: -1, 1: 0, 2: 1}

    model = XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, eval_metric="mlogloss",
        random_state=42,
    )
    model.fit(X_train, train_labels.map(label_map))

    proba = model.predict_proba(X_test)
    preds = model.predict(X_test)

    df["xgb_signal"]  = np.nan
    df["p_bearish"]   = np.nan
    df["p_neutral"]   = np.nan
    df["p_bullish"]   = np.nan

    df.loc[test.index, "xgb_signal"]  = [inv_map[p] for p in preds]
    df.loc[test.index, "p_bearish"]   = proba[:, 0]
    df.loc[test.index, "p_neutral"]   = proba[:, 1]
    df.loc[test.index, "p_bullish"]   = proba[:, 2]

    df.attrs["xgb_model"]        = model
    df.attrs["xgb_feature_cols"] = FEATURE_COLS

    if return_meta:
        meta = {"split_idx": split, "q33": q33, "q67": q67, "model": model}
        return df, meta

    return df


# ---------------------------------------------------------------------------
# TASK 7: Half-Kelly Position Sizing
# ---------------------------------------------------------------------------

def compute_kelly(p_bullish: float, p_neutral: float, p_bearish: float) -> dict:
    """
    Compute half-Kelly position size for a directional bet (b=1 payoff).

    Kelly formula (b=1): f* = 2*p_win - 1
    Half-Kelly:          kelly_f = f* * 0.5
    Floor:               0% if p_win < 0.52
    Cap:                 10% max

    Always returns p_win = max(p_bull, p_bear) for display, even when flat.
    """
    p_win = max(p_bullish, p_bearish)
    side  = "BULLISH" if p_bullish >= p_bearish else "BEARISH"

    if p_win < 0.52:
        return {"side": "FLAT", "p_win": p_win, "kelly_pct": 0.0}

    full_kelly = 2 * p_win - 1
    half_kelly = full_kelly * 0.5
    kelly_pct  = min(half_kelly * 100, 10.0)

    trade_side = "LONG" if side == "BULLISH" else "SHORT"
    return {"side": trade_side, "p_win": p_win, "kelly_pct": round(kelly_pct, 2)}


# ---------------------------------------------------------------------------
# TASK 8: Report Builder
# ---------------------------------------------------------------------------

def build_summary_row(
    ticker: str,
    regime: str,
    vol_regime: str,
    side: str,
    p_win: float,
    kelly_pct: float,
) -> dict:
    """Build a dict representing one row in the summary report."""
    signal_label = (
        "BULLISH" if side == "LONG"
        else "BEARISH" if side == "SHORT"
        else "NEUTRAL"
    )
    return {
        "ticker":       ticker,
        "regime":       regime,
        "vol_regime":   vol_regime,
        "side":         side,
        "signal_label": signal_label,
        "p_win":        p_win,
        "kelly_pct":    kelly_pct,
    }


def build_summary_table_text(signals: list, date_str: str) -> str:
    """Format signals as a plain-text table for notebook print."""
    header = f"\n{'='*55}\n GLOBAL MACRO QUANT REPORT — {date_str}\n{'='*55}\n"
    col_fmt = "{:<10} {:<10} {:<13} {:<8} {:>6} {:>7}"
    sep = "-" * 55
    lines = [
        header,
        col_fmt.format("Asset", "Regime", "Vol Regime", "Signal", "P(Win)", "Kelly%"),
        sep,
    ]
    for s in signals:
        lines.append(col_fmt.format(
            s["ticker"],
            s["regime"],
            s["vol_regime"],
            s["signal_label"],
            f"{s['p_win']*100:.0f}%",
            f"{s['kelly_pct']:.1f}%",
        ))
    lines.append(sep)
    return "\n".join(lines)


def build_telegram_html(signals: list, date_str: str) -> str:
    """Build Telegram HTML report matching the existing reporting.py style."""
    lines = [
        "<b>GLOBAL MACRO QUANT REPORT</b>",
        f"<code>{date_str}</code>",
        "",
    ]
    emoji_map = {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}
    for s in signals:
        em = emoji_map.get(s["signal_label"], "")
        lines += [
            f"<b>{s['ticker']}</b> {em} <code>{s['signal_label']}</code>",
            f"  Regime: {s['regime']} | Vol: {s['vol_regime']}",
            f"  P(Win): {s['p_win']*100:.0f}% | Kelly: {s['kelly_pct']:.1f}%",
            "",
        ]
    lines.append("<i>Research only. Not financial advice.</i>")
    return "\n".join(lines)
