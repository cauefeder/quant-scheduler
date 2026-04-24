"""
cost_model.py — Transaction cost estimation per market type.

Returns round-trip cost as a fraction (e.g., 0.02 = 2% drag on PnL per round-trip).
Used by every system's Kelly sizing layer to deduct cost from gross edge before
sizing the bet.

Two-pass usage (see spec Component 2):
  1. Compute provisional bet from gross edge.
  2. estimate_cost(..., size_usd=provisional_bet) — gives a conservative upper bound.
  3. Deduct from edge, re-size Kelly with the net edge.
"""
from __future__ import annotations

# Hard cap on returned cost so a misconfigured caller can't filter every signal.
_COST_CAP = 0.50

# Polymarket constants — see spec for rationale.
_GAS_FEE_USD = 0.05      # ~$0.05 per Polygon L2 swap
_MIN_HALF_SPREAD = 0.005 # 0.5% half-spread floor when no spread data is available
_IMPACT_COEFF = 0.5      # linear impact: bet/liquidity * 0.5

# BTC straddle: flat estimate until Phase 3 wires Deribit order book.
_BTC_STRADDLE_FLAT_COST = 0.025


def estimate_cost(
    market_type: str,
    price: float,
    size_usd: float,
    liquidity: float = 0.0,
    spread: float = 0.0,
) -> float:
    """Estimate round-trip transaction cost as a fraction of position size.

    market_type : 'polymarket', 'btc_straddle', 'btc_spot', 'equity_spot'
    price       : current market price (0-1 for PM, USD for assets)
    size_usd    : provisional bet size in USD
    liquidity   : available liquidity in USD (PM order book depth, ignored elsewhere)
    spread      : bid-ask spread as fraction (0.02 = 2pp for PM)
    """
    if market_type == "polymarket":
        return _polymarket_cost(size_usd, liquidity, spread)
    if market_type == "btc_straddle":
        return _BTC_STRADDLE_FLAT_COST
    if market_type in ("btc_spot", "equity_spot"):
        return 0.0
    return 0.0  # unknown market type — fail open, don't filter


_MAX_ONE_WAY_IMPACT = 0.10  # cap per-leg price impact so missing/tiny liquidity
                            # data never silently caps every signal at _COST_CAP
_MAX_ONE_WAY_SPREAD = 0.10  # cap per-leg spread for the same reason: thin CLOB books
                            # often report ask-bid widths approaching 1.0, which
                            # would otherwise cap every signal


def _polymarket_cost(size_usd: float, liquidity: float, spread: float) -> float:
    half_spread = min(max(spread / 2.0, _MIN_HALF_SPREAD), _MAX_ONE_WAY_SPREAD)
    gas_pct = _GAS_FEE_USD / max(size_usd, 1.0)
    if liquidity > 0:
        impact = min((size_usd / liquidity) * _IMPACT_COEFF, _MAX_ONE_WAY_IMPACT)
    else:
        impact = 0.0  # unknown liquidity — skip impact rather than assume worst case
    one_way = half_spread + gas_pct + impact
    return min(2.0 * one_way, _COST_CAP - 1e-9)
