"""
Volatility regime detection and classification.

Uses a combination of realized volatility percentile ranking,
rate of change of vol, and term structure slope to classify
the current environment into actionable regimes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class VolRegime(str, Enum):
    """Volatility regime classification."""
    HIGH_VOL_BREAKOUT = "High Vol Breakout"
    MEAN_REVERSION = "Mean Reversion"
    COMPRESSION = "Compression"
    EXPANSION = "Expansion"
    TRAP = "Trap Day"
    NORMAL = "Normal"


@dataclass
class RegimeResult:
    """Result of regime detection."""
    regime: VolRegime
    rv_1d: float          # 1-day realized vol (annualized)
    rv_7d: float          # 7-day realized vol (annualized)
    rv_30d: float         # 30-day realized vol (annualized)
    iv_estimate: float    # Estimated implied vol
    rv_percentile: float  # Percentile of current vol vs history (0-100)
    vol_of_vol: float     # Volatility of volatility
    vol_trend: str        # "rising", "falling", "flat"
    expected_move_1d: float  # Expected 1-day move in USD
    expected_move_pct: float # Expected 1-day move in %
    description: str


def compute_realized_vol(
    prices: pd.Series,
    window: int,
    annualize: float = 365.25 * 24,
) -> pd.Series:
    """
    Compute annualized realized volatility from log returns.

    Parameters
    ----------
    prices : pd.Series
        Close prices.
    window : int
        Rolling window (in bars).
    annualize : float
        Annualization factor (365.25*24 for hourly, 252 for daily).

    Returns
    -------
    pd.Series
        Annualized realized volatility.
    """
    log_returns = np.log(prices / prices.shift(1))
    return log_returns.rolling(window=window).std() * np.sqrt(annualize)


def detect_regime(
    price_df: pd.DataFrame,
    spot: Optional[float] = None,
    annualize: float = 365.25 * 24,
) -> RegimeResult:
    """
    Detect the current volatility regime.

    Uses multiple realized vol windows, vol-of-vol, and
    term structure analysis to classify the regime.

    Parameters
    ----------
    price_df : pd.DataFrame
        OHLCV with 'close' column. Assumed hourly data.
    spot : float, optional
        Current price (defaults to last close).
    annualize : float
        Annualization factor.

    Returns
    -------
    RegimeResult
    """
    close = price_df["close"]
    if spot is None:
        spot = float(close.iloc[-1])

    # --- Realized volatilities ---
    rv_1d = compute_realized_vol(close, window=24, annualize=annualize)
    rv_7d = compute_realized_vol(close, window=168, annualize=annualize)
    rv_30d = compute_realized_vol(close, window=720, annualize=annualize)

    current_rv_1d = float(rv_1d.iloc[-1]) if not rv_1d.isna().iloc[-1] else 0.50
    current_rv_7d = float(rv_7d.iloc[-1]) if not rv_7d.isna().iloc[-1] else 0.50
    current_rv_30d = float(rv_30d.dropna().iloc[-1]) if len(rv_30d.dropna()) > 0 else 0.50

    # --- IV estimate (simple proxy: short-term RV with premium) ---
    # In practice, this would come from Deribit ATM IV
    iv_premium = 1.10  # IV typically trades at ~10% premium to RV
    iv_estimate = current_rv_7d * iv_premium

    # --- Percentile ranking ---
    rv_history = rv_7d.dropna()
    if len(rv_history) > 50:
        rv_percentile = float((rv_history < current_rv_7d).sum() / len(rv_history) * 100)
    else:
        rv_percentile = 50.0

    # --- Vol of vol ---
    vol_of_vol = float(rv_7d.dropna().pct_change().std() * 100) if len(rv_7d.dropna()) > 10 else 0.0

    # --- Vol trend (is vol rising or falling?) ---
    if len(rv_7d.dropna()) >= 48:
        vol_ma_short = rv_7d.dropna().rolling(24).mean()
        vol_ma_long = rv_7d.dropna().rolling(72).mean()
        if len(vol_ma_short.dropna()) > 0 and len(vol_ma_long.dropna()) > 0:
            if vol_ma_short.iloc[-1] > vol_ma_long.iloc[-1] * 1.05:
                vol_trend = "rising"
            elif vol_ma_short.iloc[-1] < vol_ma_long.iloc[-1] * 0.95:
                vol_trend = "falling"
            else:
                vol_trend = "flat"
        else:
            vol_trend = "flat"
    else:
        vol_trend = "flat"

    # --- Expected move ---
    expected_move_pct = iv_estimate / np.sqrt(365.25)  # 1-day expected move
    expected_move_1d = spot * expected_move_pct

    # --- Regime classification ---
    rv_ratio = current_rv_1d / current_rv_30d if current_rv_30d > 0 else 1.0

    if rv_percentile > 85 and vol_trend == "rising":
        regime = VolRegime.HIGH_VOL_BREAKOUT
        desc = (
            f"Extreme volatility ({rv_percentile:.0f}th pctile). "
            f"Vol expanding. Large directional moves likely. "
            f"Straddle premium expensive but breakout potential high."
        )
    elif rv_percentile > 70 and rv_ratio > 1.3:
        regime = VolRegime.EXPANSION
        desc = (
            f"Volatility expanding (1D RV >> 30D RV). "
            f"Short-term vol spike. Good for straddles if IV hasn't caught up."
        )
    elif rv_percentile < 25 and vol_trend == "falling":
        regime = VolRegime.COMPRESSION
        desc = (
            f"Volatility compressed ({rv_percentile:.0f}th pctile). "
            f"Coiling for a move. Cheap straddles — wait for catalyst."
        )
    elif rv_ratio < 0.7 and rv_percentile > 40:
        regime = VolRegime.MEAN_REVERSION
        desc = (
            f"Short-term vol dropping toward long-term mean. "
            f"Range-bound trading likely. Sell vol or wait."
        )
    elif rv_percentile > 60 and vol_trend == "falling" and rv_ratio > 1.1:
        regime = VolRegime.TRAP
        desc = (
            f"Vol elevated but fading. Potential trap — breakout fakeouts likely. "
            f"Careful with directional bets."
        )
    else:
        regime = VolRegime.NORMAL
        desc = (
            f"Normal volatility environment ({rv_percentile:.0f}th pctile). "
            f"Standard position sizing applies."
        )

    result = RegimeResult(
        regime=regime,
        rv_1d=current_rv_1d,
        rv_7d=current_rv_7d,
        rv_30d=current_rv_30d,
        iv_estimate=iv_estimate,
        rv_percentile=rv_percentile,
        vol_of_vol=vol_of_vol,
        vol_trend=vol_trend,
        expected_move_1d=expected_move_1d,
        expected_move_pct=expected_move_pct,
        description=desc,
    )

    logger.info(f"Regime: {regime.value} | RV7D={current_rv_7d:.1%} | Pctile={rv_percentile:.0f}")
    return result
