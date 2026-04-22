"""
market_context.py — Price action context for Polymarket prediction markets.

Applies Al Brooks' market structure framework to prediction market price
histories. A market at 55c in a tight range behaves differently from a
market at 55c that just broke out of 40c — same price, different edge.

API
---
Polymarket CLOB price history:
  GET https://clob.polymarket.com/prices-history
  Params: market (token_id), startTs, endTs, fidelity (minutes)

Token ID resolution:
  GET https://gamma-api.polymarket.com/markets?conditionId=<id>
  Returns clobTokenIds list — we use the first token (YES outcome).

Structure states
----------------
  COMPRESSION   price range < 0.04 (4pp) over last 7 days — coiling
  BREAKOUT      price moved > 0.08 in last 24h — momentum
  PULLBACK      trend established then reversed partially — ideal entry
  TREND         directional move, smart money entered lower/higher
  EXHAUSTION    large move + smart money entry far from current price
  UNKNOWN       not enough history or API unavailable
"""
from __future__ import annotations

import time
import urllib.request
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

log = logging.getLogger("market_context")

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
CLOB_URL  = "https://clob.polymarket.com/prices-history"

HEADERS = {"User-Agent": "PolyTraders/1.0", "Accept": "application/json"}


class PriceStructure(str, Enum):
    COMPRESSION = "Compression"
    BREAKOUT    = "Breakout"
    PULLBACK    = "Pullback"
    TREND       = "Trend"
    EXHAUSTION  = "Exhaustion"
    UNKNOWN     = "Unknown"


@dataclass
class MarketContext:
    structure:    PriceStructure
    edge_mult:    float   # multiply estimated_edge by this (0.5 – 1.6)
    quality:      str     # "ideal" | "acceptable" | "avoid"
    note:         str     # one-line plain-English reason


# ── API helpers ───────────────────────────────────────────────────────────────

def _get(url: str, params: dict) -> Optional[list | dict]:
    qs  = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(
        f"{url}?{qs}", headers=HEADERS
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        log.debug("API call failed: %s", exc)
        return None


def _get_token_id(condition_id: str) -> Optional[str]:
    """Resolve condition_id → YES token_id via gamma-api."""
    data = _get(GAMMA_URL, {"conditionId": condition_id})
    if not data or not isinstance(data, list) or not data[0]:
        return None
    try:
        tokens = json.loads(data[0].get("clobTokenIds", "[]"))
        return str(tokens[0]) if tokens else None
    except Exception:
        return None


def _fetch_price_history(token_id: str, days: int = 7) -> list[float]:
    """Fetch hourly close prices for last `days` days."""
    now      = int(time.time())
    start_ts = now - days * 86400
    data = _get(CLOB_URL, {
        "market":   token_id,
        "startTs":  start_ts,
        "endTs":    now,
        "fidelity": 60,   # 60-minute bars
    })
    if not data or not isinstance(data, dict):
        return []
    history = data.get("history", [])
    return [float(p["p"]) for p in history if "p" in p]


# ── Structure classification ───────────────────────────────────────────────────

def classify_prediction_market(
    condition_id:  str,
    cur_price:     float,
    wav_entry:     float,    # smart money weighted avg entry
    total_exposure: float,
) -> MarketContext:
    """
    Classify the price action context of a Polymarket prediction market.

    Falls back gracefully to entry-based heuristics if API is unavailable.

    Parameters
    ----------
    condition_id  : Polymarket condition ID for the market.
    cur_price     : Current market-implied probability (0-1).
    wav_entry     : Smart money weighted avg entry price (0-1).
    total_exposure: Total USDC smart traders have in this market.

    Returns
    -------
    MarketContext
    """
    # ── Try to get full price history ────────────────────────────────────────
    token_id = _get_token_id(condition_id)
    prices   = _fetch_price_history(token_id) if token_id else []

    if len(prices) >= 24:
        return _classify_from_history(prices, cur_price, wav_entry)

    # ── Fallback: heuristics from available position data ────────────────────
    return _classify_from_positions(cur_price, wav_entry, total_exposure)


def _classify_from_history(
    prices:     list[float],
    cur_price:  float,
    wav_entry:  float,
) -> MarketContext:
    """Full classification using OHLCV-style price history."""
    p   = prices          # list of hourly closes, oldest first
    n   = len(p)

    recent_24h = p[-24:]  if n >= 24  else p
    recent_7d  = p[-168:] if n >= 168 else p

    price_range_7d  = max(recent_7d)  - min(recent_7d)
    price_move_24h  = abs(p[-1] - p[-24]) if n >= 24 else 0.0
    price_move_7d   = p[-1] - p[0]

    # Simple ATR proxy: mean absolute hourly change
    changes = [abs(p[i] - p[i-1]) for i in range(1, len(p))]
    atr_1h  = sum(changes) / len(changes) if changes else 0.01

    entry_overshoot = cur_price - wav_entry   # positive = bought above smart money

    # ── Exhaustion: large move, smart money entry far below ──────────────────
    if price_move_24h > 0.12 and entry_overshoot > 0.08:
        return MarketContext(
            structure=PriceStructure.EXHAUSTION,
            edge_mult=0.45,
            quality="avoid",
            note=(
                f"Exhaustion: +{price_move_24h:.0%} in 24h, "
                f"buying {entry_overshoot:.0%} above smart money entry. "
                "Climax move — high reversal risk."
            ),
        )

    # ── Compression: price coiling in tight range ─────────────────────────────
    if price_range_7d < 0.05 and atr_1h < 0.005:
        return MarketContext(
            structure=PriceStructure.COMPRESSION,
            edge_mult=1.30,
            quality="ideal",
            note=(
                f"Compression: 7-day range={price_range_7d:.0%}, ATR={atr_1h:.2%}/hr. "
                "Market coiling — smart money positioning before expected catalyst. "
                "Edge is real if resolution is near."
            ),
        )

    # ── Breakout: large recent move ───────────────────────────────────────────
    if price_move_24h > 0.08:
        # First bar of breakout (smart money ahead of us) or second bar confirmation?
        confirmed = entry_overshoot < 0.04  # still close to smart money entry
        return MarketContext(
            structure=PriceStructure.BREAKOUT,
            edge_mult=1.10 if confirmed else 0.75,
            quality="acceptable" if confirmed else "avoid",
            note=(
                f"Breakout: +{price_move_24h:.0%} in 24h. "
                + (f"Still near smart money entry ({entry_overshoot:.0%} above) — entry acceptable."
                   if confirmed else
                   f"Buying {entry_overshoot:.0%} above smart money — chasing. Wait for pullback.")
            ),
        )

    # ── Pullback: trend established, then partial reversal ───────────────────
    if price_move_7d > 0.06 and price_move_24h < 0.02 and entry_overshoot <= 0.01:
        return MarketContext(
            structure=PriceStructure.PULLBACK,
            edge_mult=1.55,
            quality="ideal",
            note=(
                f"Pullback: 7-day trend +{price_move_7d:.0%}, "
                f"last 24h consolidating ({price_move_24h:.0%}). "
                "Smart money at similar price. Highest-probability continuation entry."
            ),
        )

    # ── Established trend ─────────────────────────────────────────────────────
    if abs(price_move_7d) > 0.04:
        direction = "up" if price_move_7d > 0 else "down"
        mult = 1.10 if entry_overshoot < 0.03 else 0.80
        return MarketContext(
            structure=PriceStructure.TREND,
            edge_mult=mult,
            quality="acceptable",
            note=(
                f"Trend: {direction} {abs(price_move_7d):.0%} over 7 days. "
                + (f"Entry near smart money level — acceptable."
                   if entry_overshoot < 0.03 else
                   f"Buying {entry_overshoot:.0%} above smart money — trend-follow with caution.")
            ),
        )

    return MarketContext(
        structure=PriceStructure.UNKNOWN,
        edge_mult=1.0,
        quality="acceptable",
        note="No dominant price structure detected — neutral adjustment.",
    )


def _classify_from_positions(
    cur_price:      float,
    wav_entry:      float,
    total_exposure: float,
) -> MarketContext:
    """
    Fallback when price history is unavailable.
    Uses smart money entry vs current price as a proxy for structure.
    """
    overshoot = cur_price - wav_entry

    if overshoot > 0.12:
        return MarketContext(
            structure=PriceStructure.EXHAUSTION,
            edge_mult=0.50,
            quality="avoid",
            note=(
                f"Buying {overshoot:.0%} above smart money avg entry ({wav_entry:.0%}). "
                "Most alpha already captured — exhaustion risk. (No price history available.)"
            ),
        )
    if overshoot > 0.06:
        return MarketContext(
            structure=PriceStructure.TREND,
            edge_mult=0.80,
            quality="acceptable",
            note=(
                f"{overshoot:.0%} above smart money entry. "
                "Trend running — entry possible but momentum may be late. "
                "(No price history available.)"
            ),
        )
    if abs(overshoot) <= 0.02:
        return MarketContext(
            structure=PriceStructure.PULLBACK,
            edge_mult=1.40,
            quality="ideal",
            note=(
                f"Current price ({cur_price:.0%}) near smart money avg entry ({wav_entry:.0%}). "
                "Entering alongside smart money — ideal timing. "
                "(No price history available.)"
            ),
        )

    return MarketContext(
        structure=PriceStructure.UNKNOWN,
        edge_mult=1.0,
        quality="acceptable",
        note="Entry within normal range of smart money. Neutral. (No price history available.)",
    )
