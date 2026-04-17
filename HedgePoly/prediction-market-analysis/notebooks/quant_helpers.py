"""
quant_helpers.py — Analytical functions for the Global Macro Quant Research Notebook.

All functions are pure (no side effects) and take/return pandas DataFrames or
plain Python types. Import this module from the notebook.
"""
import numpy as np
import pandas as pd
import yfinance as yf

ASSETS = {
    "SPY":      {"exchange_type": "equity",  "min_rows": max(900, int(0.8 * 252 * 5))},
    "BTC-USD":  {"exchange_type": "crypto",  "min_rows": max(900, int(0.8 * 365 * 5))},
    "GC=F":     {"exchange_type": "futures", "min_rows": max(900, int(0.8 * 252 * 5))},
    "EURUSD=X": {"exchange_type": "fx",      "min_rows": max(900, int(0.8 * 261 * 5))},
}
FX_TICKERS = {"EURUSD=X"}


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
