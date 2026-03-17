"""
Options data proxy layer.

Since LNMarkets does not expose a full options chain and Deribit requires
an API key, this module builds a *synthetic* options surface using
Black-Scholes math calibrated to BTC realized volatility.

When real Deribit data becomes available, swap `generate_synthetic_chain()`
for `fetch_deribit_chain()` — the rest of the pipeline stays identical.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy.stats import norm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Black-Scholes helpers
# ---------------------------------------------------------------------------

def bs_d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes d1."""
    if T <= 0 or sigma <= 0:
        return 0.0
    return (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes gamma (same for calls and puts)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = bs_d1(S, K, T, r, sigma)
    return norm.pdf(d1) / (S * sigma * np.sqrt(T))


def bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Black-Scholes option price."""
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = d1 - sigma * np.sqrt(T)
    if is_call:
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Black-Scholes delta."""
    if T <= 0 or sigma <= 0:
        return (1.0 if is_call else -1.0) if S > K else 0.0
    d1 = bs_d1(S, K, T, r, sigma)
    return norm.cdf(d1) if is_call else norm.cdf(d1) - 1.0


# ---------------------------------------------------------------------------
# Synthetic options chain generation
# ---------------------------------------------------------------------------

@dataclass
class OptionsChain:
    """Container for options chain data."""
    spot: float
    chain: pd.DataFrame  # columns: strike, call_oi, put_oi, call_iv, put_iv, call_gamma, put_gamma, ...


def generate_synthetic_chain(
    spot: float,
    atm_iv: float,
    T: float = 1 / 365,
    r: float = 0.045,
    strike_range_pct: float = 0.10,
    strike_step: int = 1000,
) -> OptionsChain:
    """
    Build a synthetic options chain around `spot`.

    The IV surface is modeled with a simple skew: puts get higher IV
    (consistent with BTC's historical put skew), and OI is modeled
    with realistic clustering around ATM.

    Parameters
    ----------
    spot : float
        Current BTC price.
    atm_iv : float
        At-the-money implied volatility (annualized, decimal).
    T : float
        Time to expiry in years (default 1 day).
    r : float
        Risk-free rate.
    strike_range_pct : float
        Range around spot to generate strikes (±%).
    strike_step : int
        Dollar step between strikes.

    Returns
    -------
    OptionsChain
    """
    low_strike = int(spot * (1 - strike_range_pct) / strike_step) * strike_step
    high_strike = int(spot * (1 + strike_range_pct) / strike_step) * strike_step + strike_step
    strikes = np.arange(low_strike, high_strike + 1, strike_step, dtype=float)

    np.random.seed(int(spot) % 10000)  # deterministic per price level

    rows = []
    for K in strikes:
        moneyness = K / spot

        # --- IV surface (skew model) ---
        # Put skew: OTM puts get progressively higher IV
        # Call wing: slight upward tilt for far OTM calls
        skew_factor = -0.15 * (moneyness - 1.0)  # negative = puts get more IV
        wing_factor = 0.05 * max(0, abs(moneyness - 1.0) - 0.03)  # smile wings
        call_iv = max(0.15, atm_iv + skew_factor * 0.5 + wing_factor)
        put_iv = max(0.15, atm_iv - skew_factor * 0.5 + wing_factor)

        # --- OI distribution (peaks near ATM, decays outward) ---
        atm_distance = abs(moneyness - 1.0)
        base_oi = max(50, int(500 * np.exp(-atm_distance * 30)))
        # Round numbers attract more OI
        round_bonus = 1.3 if K % 5000 == 0 else (1.15 if K % 2000 == 0 else 1.0)

        call_oi = int(base_oi * round_bonus * np.random.uniform(0.8, 1.3))
        put_oi = int(base_oi * round_bonus * np.random.uniform(0.9, 1.4))  # puts slightly higher OI

        # --- Greeks ---
        call_gamma = bs_gamma(spot, K, T, r, call_iv)
        put_gamma = bs_gamma(spot, K, T, r, put_iv)
        call_delta = bs_delta(spot, K, T, r, call_iv, is_call=True)
        put_delta = bs_delta(spot, K, T, r, put_iv, is_call=False)
        call_price = bs_price(spot, K, T, r, call_iv, is_call=True)
        put_price = bs_price(spot, K, T, r, put_iv, is_call=False)

        rows.append({
            "strike": K,
            "moneyness": moneyness,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "call_iv": call_iv,
            "put_iv": put_iv,
            "call_gamma": call_gamma,
            "put_gamma": put_gamma,
            "call_delta": call_delta,
            "put_delta": put_delta,
            "call_price": call_price,
            "put_price": put_price,
        })

    chain = pd.DataFrame(rows)
    logger.info(f"Generated synthetic chain: {len(chain)} strikes around ${spot:,.0f}")
    return OptionsChain(spot=spot, chain=chain)


# ---------------------------------------------------------------------------
# GEX calculation
# ---------------------------------------------------------------------------

@dataclass
class GEXResult:
    """Container for GEX analysis results."""
    gex_by_strike: pd.DataFrame
    call_wall: float
    call_wall_gex: float
    put_wall: float
    put_wall_gex: float
    net_gex: float
    max_pain: float
    pin_candidates: List[Tuple[float, float]]


def calculate_gex(chain: OptionsChain) -> GEXResult:
    """
    Calculate Gamma Exposure (GEX) by strike.

    GEX = Gamma × OI × Spot² × ContractMultiplier
    Call GEX is positive (dealers long gamma → stabilizing).
    Put GEX is negative (dealers short gamma → destabilizing).
    """
    df = chain.chain.copy()
    S = chain.spot

    df["call_gex"] = df["call_gamma"] * df["call_oi"] * S * S
    df["put_gex"] = -df["put_gamma"] * df["put_oi"] * S * S  # negative by convention
    df["total_gex"] = df["call_gex"] + df["put_gex"]
    df["abs_gex"] = df["total_gex"].abs()

    # Call wall = strike with highest positive GEX (resistance)
    call_wall_idx = df["total_gex"].idxmax()
    call_wall = df.loc[call_wall_idx, "strike"]
    call_wall_gex = df.loc[call_wall_idx, "total_gex"]

    # Put wall = strike with most negative GEX (support)
    put_wall_idx = df["total_gex"].idxmin()
    put_wall = df.loc[put_wall_idx, "strike"]
    put_wall_gex = df.loc[put_wall_idx, "total_gex"]

    # Net GEX
    net_gex = df["total_gex"].sum()

    # Max pain = strike that minimizes total option holder value
    total_pain = []
    for _, row in df.iterrows():
        K = row["strike"]
        call_pain = max(0, S - K) * row["call_oi"]
        put_pain = max(0, K - S) * row["put_oi"]
        total_pain.append({"strike": K, "pain": call_pain + put_pain})
    pain_df = pd.DataFrame(total_pain)
    max_pain = pain_df.loc[pain_df["pain"].idxmin(), "strike"]

    # Pin candidates (top 5 by absolute GEX)
    top_pins = df.nlargest(5, "abs_gex")[["strike", "total_gex"]].values.tolist()

    logger.info(
        f"GEX: Call Wall=${call_wall:,.0f} | Put Wall=${put_wall:,.0f} | "
        f"Max Pain=${max_pain:,.0f} | Net GEX={net_gex:,.0f}"
    )

    return GEXResult(
        gex_by_strike=df,
        call_wall=call_wall,
        call_wall_gex=call_wall_gex,
        put_wall=put_wall,
        put_wall_gex=put_wall_gex,
        net_gex=net_gex,
        max_pain=max_pain,
        pin_candidates=top_pins,
    )
