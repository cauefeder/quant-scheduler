"""
Model 1 — Bitcoin Volatility & Options Structure Model

Purpose: Identify optimal 1-day long straddle opportunities on LNMarkets.

Pipeline:
1. Fetch BTC price data (1H candles)
2. Compute realized volatility at multiple horizons
3. Detect volatility regime
4. Build synthetic GEX profile
5. Find Call/Put walls, max pain, pin levels
6. Suggest optimal straddle strike
7. Estimate breakeven, probability of touch, expected value
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.stats import norm

from analytics.regime import RegimeResult, VolRegime, detect_regime
from data.fetcher import fetch_btc_price, fetch_mvrv_ratio
from data.options_proxy import (
    OptionsChain,
    GEXResult,
    bs_price,
    calculate_gex,
    generate_synthetic_chain,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Straddle analysis
# ---------------------------------------------------------------------------

@dataclass
class StraddleAnalysis:
    """Complete straddle trade analysis."""
    strike: float
    call_price: float
    put_price: float
    straddle_cost: float
    upper_breakeven: float
    lower_breakeven: float
    breakeven_range_pct: float
    prob_of_touch_upper: float
    prob_of_touch_lower: float
    prob_of_profit: float
    expected_value: float
    risk_reward_ratio: float
    max_loss: float


def analyze_straddle(
    spot: float,
    strike: float,
    iv: float,
    T: float = 1 / 365,
    r: float = 0.045,
) -> StraddleAnalysis:
    """
    Analyze a 1-day long straddle at the given strike.

    Parameters
    ----------
    spot : float
        Current BTC price.
    strike : float
        Strike price for straddle.
    iv : float
        Implied volatility (annualized, decimal).
    T : float
        Time to expiry in years.
    r : float
        Risk-free rate.

    Returns
    -------
    StraddleAnalysis
    """
    call_px = bs_price(spot, strike, T, r, iv, is_call=True)
    put_px = bs_price(spot, strike, T, r, iv, is_call=False)
    straddle_cost = call_px + put_px

    upper_be = strike + straddle_cost
    lower_be = strike - straddle_cost
    be_range_pct = (straddle_cost * 2) / spot

    # Probability of touching breakevens (using GBM)
    sigma_1d = iv * np.sqrt(T)
    if sigma_1d > 0:
        z_upper = (np.log(upper_be / spot)) / sigma_1d
        z_lower = (np.log(lower_be / spot)) / sigma_1d
        prob_touch_upper = 2 * (1 - norm.cdf(abs(z_upper)))  # reflection principle
        prob_touch_lower = 2 * (1 - norm.cdf(abs(z_lower)))
    else:
        prob_touch_upper = 0.0
        prob_touch_lower = 0.0

    # Approximate probability of profit
    # Price must move beyond breakevens in either direction
    if sigma_1d > 0:
        prob_above_upper = 1 - norm.cdf(z_upper)
        prob_below_lower = norm.cdf(z_lower)
        prob_profit = prob_above_upper + prob_below_lower
    else:
        prob_profit = 0.0

    # Expected value (Monte Carlo-lite using normal approximation)
    # E[max(0, |S_T - K| - cost)] under GBM
    n_sims = 10000
    np.random.seed(42)
    z = np.random.standard_normal(n_sims)
    S_T = spot * np.exp((r - 0.5 * iv**2) * T + iv * np.sqrt(T) * z)
    payoffs = np.maximum(0, np.abs(S_T - strike) - straddle_cost)
    expected_profit = float(np.mean(payoffs))
    expected_value = expected_profit - straddle_cost  # net EV after premium
    # Actually: EV = E[payoff] - cost, but payoff already subtracts cost
    # Recalculate: gross payoff - cost
    gross_payoffs = np.abs(S_T - strike)
    ev = float(np.mean(gross_payoffs)) - straddle_cost

    # Risk/reward: max gain (unbounded, but use 2σ move) vs max loss
    expected_2sigma_move = spot * sigma_1d * 2
    potential_gain_2sigma = expected_2sigma_move - straddle_cost
    rr_ratio = potential_gain_2sigma / straddle_cost if straddle_cost > 0 else 0

    return StraddleAnalysis(
        strike=strike,
        call_price=call_px,
        put_price=put_px,
        straddle_cost=straddle_cost,
        upper_breakeven=upper_be,
        lower_breakeven=lower_be,
        breakeven_range_pct=be_range_pct,
        prob_of_touch_upper=prob_touch_upper,
        prob_of_touch_lower=prob_touch_lower,
        prob_of_profit=prob_profit,
        expected_value=ev,
        risk_reward_ratio=rr_ratio,
        max_loss=straddle_cost,
    )


# ---------------------------------------------------------------------------
# Full Model 1 output
# ---------------------------------------------------------------------------

@dataclass
class Model1Result:
    """Complete output of Model 1."""
    spot: float
    regime: RegimeResult
    gex: GEXResult
    straddle: StraddleAnalysis
    best_strike: float
    day_type: str
    recommendation: str


def run_model1() -> Model1Result:
    """
    Execute the full Model 1 pipeline.

    Returns
    -------
    Model1Result
    """
    logger.info("=" * 60)
    logger.info("MODEL 1 — BTC Volatility & Options Structure")
    logger.info("=" * 60)

    # 1. Fetch price data
    price_df = fetch_btc_price(interval="1h", limit=500)
    spot = float(price_df["close"].iloc[-1])
    logger.info(f"Spot price: ${spot:,.2f}")

    # 2. Detect volatility regime
    regime = detect_regime(price_df, spot=spot)

    # 2b. Inject on-chain MVRV (non-blocking — None if API unavailable)
    regime.mvrv = fetch_mvrv_ratio()

    # 3. Build synthetic options chain
    chain = generate_synthetic_chain(
        spot=spot,
        atm_iv=regime.iv_estimate,
        T=1 / 365,
        r=0.045,
    )

    # 4. Calculate GEX
    gex = calculate_gex(chain)

    # 5. Select best strike for straddle
    # Prefer ATM or nearest round strike to max pain
    atm_strike = round(spot / 1000) * 1000
    # If max pain is within 2% of spot, use it (natural magnet)
    if abs(gex.max_pain - spot) / spot < 0.02:
        best_strike = gex.max_pain
    else:
        best_strike = atm_strike

    # 6. Analyze straddle
    straddle = analyze_straddle(
        spot=spot,
        strike=best_strike,
        iv=regime.iv_estimate,
    )

    # 7. Classify day type
    if regime.regime == VolRegime.HIGH_VOL_BREAKOUT:
        day_type = "HIGH VOL BREAKOUT DAY"
        recommendation = (
            "Straddle is expensive but breakout probability is high. "
            "Consider if RV > IV → straddle is underpriced. "
            "Aggressive entry possible with tight time management."
        )
    elif regime.regime == VolRegime.COMPRESSION:
        day_type = "COMPRESSION DAY"
        recommendation = (
            "Volatility compressed — cheap straddles. "
            "WAIT for catalyst before entry. "
            "Set alerts on breakout of GEX range."
        )
    elif regime.regime == VolRegime.EXPANSION:
        day_type = "EXPANSION DAY"
        recommendation = (
            "Vol expanding — if IV hasn't caught up to RV, straddle has edge. "
            "Check if straddle cost < expected 1D move. "
            "Good entry if RV/IV ratio > 1."
        )
    elif regime.regime == VolRegime.MEAN_REVERSION:
        day_type = "MEAN REVERSION DAY"
        recommendation = (
            "Vol mean-reverting — straddle likely to lose theta. "
            "AVOID straddle. Consider iron butterfly or stay flat."
        )
    elif regime.regime == VolRegime.TRAP:
        day_type = "TRAP DAY"
        recommendation = (
            "Volatility elevated but fading — fake breakouts likely. "
            "CAUTION with straddles. Wait for clearer regime."
        )
    else:
        day_type = "NORMAL DAY"
        recommendation = (
            "Normal vol environment. Straddle at standard sizing. "
            "Only enter if expected move > straddle cost."
        )

    # Check edge: is expected move > straddle cost?
    edge = regime.expected_move_1d - straddle.straddle_cost
    if edge > 0:
        recommendation += f"\n→ EDGE DETECTED: Expected move (${regime.expected_move_1d:,.0f}) > Straddle cost (${straddle.straddle_cost:,.0f}). Positive EV."
    else:
        recommendation += f"\n→ NO EDGE: Expected move (${regime.expected_move_1d:,.0f}) < Straddle cost (${straddle.straddle_cost:,.0f}). Negative EV — consider waiting."

    result = Model1Result(
        spot=spot,
        regime=regime,
        gex=gex,
        straddle=straddle,
        best_strike=best_strike,
        day_type=day_type,
        recommendation=recommendation,
    )

    logger.info(f"Day Type: {day_type}")
    logger.info(f"Best Strike: ${best_strike:,.0f}")
    logger.info(f"Straddle Cost: ${straddle.straddle_cost:,.2f}")

    return result
