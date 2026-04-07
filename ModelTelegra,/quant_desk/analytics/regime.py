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
    mvrv: Optional[float] = None  # Market Value / Realized Value (on-chain)


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

    Uses multiple realized vol windows, vol-of-vol, actual 24h price change,
    and term structure analysis to classify the regime.

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
    rv_1d  = compute_realized_vol(close, window=24,  annualize=annualize)
    rv_7d  = compute_realized_vol(close, window=168, annualize=annualize)
    rv_30d = compute_realized_vol(close, window=720, annualize=annualize)

    current_rv_1d  = float(rv_1d.iloc[-1])           if not rv_1d.isna().iloc[-1]          else 0.50
    current_rv_7d  = float(rv_7d.iloc[-1])           if not rv_7d.isna().iloc[-1]          else 0.50
    current_rv_30d = float(rv_30d.dropna().iloc[-1]) if len(rv_30d.dropna()) > 0           else 0.50

    # --- IV estimate (7D RV + 10% premium, proxy for ATM IV) ---
    iv_premium  = 1.10
    iv_estimate = current_rv_7d * iv_premium

    # --- Percentile ranking of current 7D RV vs its own history ---
    rv_history = rv_7d.dropna()
    if len(rv_history) > 50:
        rv_percentile = float((rv_history < current_rv_7d).sum() / len(rv_history) * 100)
    else:
        rv_percentile = 50.0

    # --- Vol of vol ---
    vol_of_vol = float(rv_7d.dropna().pct_change().std() * 100) if len(rv_7d.dropna()) > 10 else 0.0

    # --- Actual 24H price change (catches large moves instantly) ---
    # The MA-based vol_trend can take hours to react; this fires immediately.
    price_24h_pct = 0.0
    if len(close) >= 25:
        price_24h_pct = abs(float(close.iloc[-1]) / float(close.iloc[-25]) - 1.0)

    # --- Key vol ratios ---
    rv_ratio        = current_rv_1d / current_rv_30d if current_rv_30d > 0 else 1.0  # vs baseline
    rv_ratio_1d_7d  = current_rv_1d / current_rv_7d  if current_rv_7d  > 0 else 1.0  # vs recent avg

    # --- Vol trend (slow MA crossover signal) ---
    vol_trend = "flat"
    if len(rv_7d.dropna()) >= 48:
        vol_ma_short = rv_7d.dropna().rolling(24).mean()
        vol_ma_long  = rv_7d.dropna().rolling(72).mean()
        if len(vol_ma_short.dropna()) > 0 and len(vol_ma_long.dropna()) > 0:
            if vol_ma_short.iloc[-1] > vol_ma_long.iloc[-1] * 1.05:
                vol_trend = "rising"
            elif vol_ma_short.iloc[-1] < vol_ma_long.iloc[-1] * 0.95:
                vol_trend = "falling"

    # --- Fast vol trend override ---
    # If 1D RV is 1.6x the 7D RV, vol is spiking right now — don't wait for the MA crossover.
    if rv_ratio_1d_7d >= 1.6:
        vol_trend = "rising"
    elif rv_ratio_1d_7d <= 0.55 and vol_trend != "falling":
        vol_trend = "falling"

    # --- Expected move ---
    expected_move_pct = iv_estimate / np.sqrt(365.25)
    expected_move_1d  = spot * expected_move_pct

    # --- Regime classification ---
    # Extreme-event flag: large actual price move + elevated short-term vol.
    # Catches "BTC down/up 10% today" cases that slow percentile metrics miss.
    extreme_event = price_24h_pct >= 0.04 and rv_ratio >= 1.20

    if (
        (rv_percentile > 80 and vol_trend == "rising")   # classic high-vol expansion
        or rv_percentile > 92                             # extreme vol by any measure
        or extreme_event                                  # large price move right now
    ):
        regime = VolRegime.HIGH_VOL_BREAKOUT
        desc = (
            f"Extreme volatility ({rv_percentile:.0f}th pctile). "
            f"24H move: {price_24h_pct:.1%}. "
            f"Vol expanding (1D RV={current_rv_1d:.0%} vs 30D={current_rv_30d:.0%}). "
            f"Large directional moves likely. Straddle premium elevated but breakout potential high."
        )

    elif (
        (rv_percentile > 65 and rv_ratio > 1.2)          # vol above average and spiking
        or (rv_percentile > 50 and rv_ratio_1d_7d > 1.8) # strong 1D spike vs recent
    ):
        regime = VolRegime.EXPANSION
        desc = (
            f"Volatility expanding (1D RV={current_rv_1d:.0%} vs 7D={current_rv_7d:.0%}). "
            f"Short-term vol spike at {rv_percentile:.0f}th pctile. "
            f"Good for straddles if IV hasn't caught up yet."
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

    elif rv_percentile > 55 and vol_trend == "falling" and rv_ratio > 1.0:
        regime = VolRegime.TRAP
        desc = (
            f"Vol elevated ({rv_percentile:.0f}th pctile) but fading. "
            f"Potential trap — breakout fakeouts likely. Careful with directional bets."
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

    logger.info(
        f"Regime: {regime.value} | RV1D={current_rv_1d:.0%} | RV7D={current_rv_7d:.0%} "
        f"| Pctile={rv_percentile:.0f} | 24H move={price_24h_pct:.1%} | Vol={vol_trend}"
    )
    return result
