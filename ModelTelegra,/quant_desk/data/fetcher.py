"""
Data fetching layer.
Pulls price data from Yahoo Finance (equities, ETFs, commodities, crypto)
and Binance (BTC intraday) with retry logic and caching.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Yahoo Finance fetcher
# ---------------------------------------------------------------------------
def fetch_yf(
    ticker: str,
    period: str = "60d",
    interval: str = "1h",
    retries: int = 3,
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV data from Yahoo Finance.

    Parameters
    ----------
    ticker : str
        Yahoo Finance ticker symbol (e.g. 'BTC-USD', 'SPY').
    period : str
        Data period ('60d', '2y', etc.).
    interval : str
        Candle interval ('1h', '1d', '1wk').
    retries : int
        Number of retry attempts.

    Returns
    -------
    pd.DataFrame or None
        Columns: open, high, low, close, volume (lowercase).
    """
    for attempt in range(1, retries + 1):
        try:
            df = yf.download(
                ticker,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=True,
            )
            if df is None or df.empty:
                logger.warning(f"[{ticker}] Empty result from Yahoo Finance (attempt {attempt})")
                time.sleep(2 * attempt)
                continue

            # Flatten multi-level columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df.columns = [c.lower().replace(" ", "_") for c in df.columns]

            # Ensure required columns exist
            required = {"open", "high", "low", "close", "volume"}
            if not required.issubset(set(df.columns)):
                logger.warning(f"[{ticker}] Missing columns: {required - set(df.columns)}")
                return None

            df = df[["open", "high", "low", "close", "volume"]].dropna()
            df.index = pd.to_datetime(df.index)

            # Remove timezone info for consistency
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

            logger.info(f"[{ticker}] Fetched {len(df)} bars ({interval}) from Yahoo Finance")
            return df

        except Exception as e:
            logger.error(f"[{ticker}] Yahoo Finance error (attempt {attempt}): {e}")
            time.sleep(2 * attempt)

    return None


# ---------------------------------------------------------------------------
# Binance fetcher (BTC only, no API key needed)
# ---------------------------------------------------------------------------
def fetch_binance_btc(
    interval: str = "1h",
    limit: int = 500,
    retries: int = 3,
) -> Optional[pd.DataFrame]:
    """
    Fetch BTC/USDT OHLCV from Binance public API.

    Parameters
    ----------
    interval : str
        Candle interval ('1m', '5m', '15m', '1h', '4h', '1d').
    limit : int
        Number of candles (max 1000).
    retries : int
        Number of retry attempts.

    Returns
    -------
    pd.DataFrame or None
    """
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": "BTCUSDT", "interval": interval, "limit": min(limit, 1000)}

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            raw = resp.json()

            df = pd.DataFrame(raw, columns=[
                "timestamp", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades", "taker_buy_base",
                "taker_buy_quote", "ignore",
            ])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)

            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)

            df = df[["open", "high", "low", "close", "volume"]]
            logger.info(f"[BTC/Binance] Fetched {len(df)} candles ({interval})")
            return df

        except Exception as e:
            logger.error(f"[BTC/Binance] Error (attempt {attempt}): {e}")
            time.sleep(2 * attempt)

    return None


# ---------------------------------------------------------------------------
# Convenience: fetch BTC with fallback
# ---------------------------------------------------------------------------
def fetch_btc_price(
    interval: str = "1h",
    limit: int = 500,
) -> pd.DataFrame:
    """Try Binance first, fall back to Yahoo Finance."""
    df = fetch_binance_btc(interval=interval, limit=limit)
    if df is not None and not df.empty:
        return df

    logger.info("[BTC] Falling back to Yahoo Finance")
    period_map = {"1h": "60d", "4h": "60d", "1d": "2y"}
    period = period_map.get(interval, "60d")
    df = fetch_yf("BTC-USD", period=period, interval=interval)
    if df is not None:
        return df

    raise RuntimeError("Could not fetch BTC price data from any source")


# ---------------------------------------------------------------------------
# MVRV Ratio (on-chain valuation) — CoinMetrics community API, no auth
# ---------------------------------------------------------------------------
def fetch_mvrv_ratio() -> Optional[float]:
    """
    Compute BTC MVRV proxy: current price / 200-day SMA.

    Uses 2 years of daily Yahoo Finance data — no API key required.
    The 200-day SMA serves as a proxy for BTC's on-chain realized price
    (the average cost basis of all coins last moved), which is what true
    MVRV measures. Directionally equivalent for regime identification.

    Zones (calibrated to BTC price/SMA200 historical distribution):
      < 0.85 : Undervalued — price below long-term trend (historically strong buy)
      0.85-1.25: Fair value — trading near realized cost basis
      1.25-2.0 : Elevated — premium to realized value building
      > 2.0  : Extreme — historical distribution / cycle top territory

    Returns None on data failure.
    """
    try:
        df = fetch_yf("BTC-USD", period="2y", interval="1d")
        if df is None or len(df) < 200:
            logger.warning("[MVRV] Insufficient daily data for SMA200")
            return None
        sma_200 = float(df["close"].rolling(200).mean().dropna().iloc[-1])
        current = float(df["close"].iloc[-1])
        if sma_200 > 0:
            proxy = round(current / sma_200, 3)
            logger.info(f"[MVRV proxy] price={current:.0f} / SMA200={sma_200:.0f} = {proxy:.3f}")
            return proxy
    except Exception as e:
        logger.warning(f"[MVRV] Could not compute: {e}")
    return None


# ---------------------------------------------------------------------------
# Fetch multiple tickers
# ---------------------------------------------------------------------------
def fetch_multi(
    tickers: Dict[str, str],
    period: str = "60d",
    interval: str = "1h",
) -> Dict[str, pd.DataFrame]:
    """
    Fetch data for multiple tickers.

    Returns dict of {ticker: DataFrame}, skipping failures.
    """
    results: Dict[str, pd.DataFrame] = {}
    for ticker, name in tickers.items():
        df = fetch_yf(ticker, period=period, interval=interval)
        if df is not None and not df.empty:
            results[ticker] = df
        else:
            logger.warning(f"[{ticker}/{name}] Skipped — no data")
    return results
