"""
analytics/context.py — Market structure classification layer.

Based on Al Brooks' price action framework: before acting on any indicator
signal, classify WHAT the market is doing. The same EMA reading in a tight
range vs an established trend has completely different probability implications.

Structure states
----------------
  TIGHT_RANGE       price contained < 1 ATR over 20 bars → fade extremes
  BREAKOUT          first 1-2 bars outside prior range → wait for confirmation
  PULLBACK          counter-trend correction then resumption → highest-prob entry
  ESTABLISHED_TREND 6-14 bars same side, healthy → trend-follow acceptable
  EXHAUSTION        strength > 80 + bars > 15 + large spread → reversal risk
  UNCLEAR           mixed signals

Cross-signal rules (trend structure × vol regime)
---------------------------------------------------
  HIGH_VOL_BREAKOUT + trend transition  → late entry / chasing (-30%)
  HIGH_VOL_BREAKOUT + exhaustion        → climax reversal warning  (-60%)
  HIGH_VOL_BREAKOUT + pullback          → momentum continuation   (+20%)
  COMPRESSION       + established trend → coiling for continuation (+30%)
  COMPRESSION       + tight range       → direction unknown, wait  (-50%)
  TRAP              + any               → fakeout risk elevated    (-40%)
  EXPANSION         + pullback          → momentum building         (+40%)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class MarketStructure(str, Enum):
    TIGHT_RANGE       = "Tight Range"
    BREAKOUT          = "Breakout"
    PULLBACK          = "Pullback"
    ESTABLISHED_TREND = "Established Trend"
    EXHAUSTION        = "Exhaustion"
    UNCLEAR           = "Unclear"


@dataclass
class ContextResult:
    structure:              MarketStructure
    probability_adjustment: float   # multiply raw signal confidence by this
    entry_quality:          str     # "ideal" | "acceptable" | "avoid"
    note:                   str     # plain-English summary for the report


def classify_market_structure(df: pd.DataFrame) -> ContextResult:
    """
    Classify market structure from the output of classify_trend().

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns produced by classify_trend():
        signal, strength, regime_bars, atr, high, low, close,
        vol_adj_spread, transition.

    Returns
    -------
    ContextResult
    """
    if len(df) < 20:
        return ContextResult(MarketStructure.UNCLEAR, 1.0, "avoid",
                             "Insufficient history for structure analysis.")

    last         = df.iloc[-1]
    recent       = df.iloc[-20:]
    current_sig  = last["signal"]
    regime_bars  = int(last["regime_bars"])
    strength     = float(last["strength"])
    atr          = float(last["atr"])
    vol_adj_sprd = float(last["vol_adj_spread"])

    if atr == 0:
        return ContextResult(MarketStructure.UNCLEAR, 1.0, "avoid", "ATR is zero.")

    # ── Exhaustion: climax bar at end of extended move ─────────────────────────
    if strength > 80 and regime_bars > 15 and abs(vol_adj_sprd) > 3.0:
        return ContextResult(
            structure=MarketStructure.EXHAUSTION,
            probability_adjustment=0.40,
            entry_quality="avoid",
            note=(
                f"Exhaustion: strength={strength:.0f}, {regime_bars} bars in regime, "
                f"spread={vol_adj_sprd:.2f}× ATR. "
                "Brooks: large climax bar after extended move = high reversal risk. "
                "Reduce or avoid new entries."
            ),
        )

    # ── Tight range: price coiling within 1 ATR over last 20 bars ─────────────
    recent_range = float(recent["high"].max() - recent["low"].min())
    if recent_range < atr * 1.0:
        return ContextResult(
            structure=MarketStructure.TIGHT_RANGE,
            probability_adjustment=0.60,
            entry_quality="avoid",
            note=(
                f"Tight range: 20-bar range={recent_range:.4f} < 1 ATR={atr:.4f}. "
                "Market coiling — breakout probability low right now. "
                "Wait for range expansion before entering."
            ),
        )

    # ── Two-legged pullback: counter-trend correction then resumption ──────────
    is_pullback = False
    if 1 <= regime_bars <= 3 and len(df) >= 10:
        prev = df["signal"].iloc[-10:-1].values
        if current_sig == "Bullish":
            opposite = sum(1 for s in prev[-6:] if s in ("Bearish", "Hold"))
            is_pullback = 2 <= opposite <= 5
        elif current_sig == "Bearish":
            opposite = sum(1 for s in prev[-6:] if s in ("Bullish", "Hold"))
            is_pullback = 2 <= opposite <= 5

    if is_pullback:
        return ContextResult(
            structure=MarketStructure.PULLBACK,
            probability_adjustment=1.50,
            entry_quality="ideal",
            note=(
                f"Two-legged pullback resuming {current_sig} trend "
                f"(bar {regime_bars} after correction). "
                "Brooks' highest-probability setup — trend continuation after "
                "counter-trend correction. Ideal entry zone."
            ),
        )

    # ── Breakout: regime just started ─────────────────────────────────────────
    if bool(last["transition"]) or regime_bars <= 2:
        return ContextResult(
            structure=MarketStructure.BREAKOUT,
            probability_adjustment=0.80,
            entry_quality="acceptable",
            note=(
                f"Breakout: bar {regime_bars} of new {current_sig} regime. "
                "Brooks: 80% of breakouts fail — wait for second bar close "
                "confirmation before full-size entry."
            ),
        )

    # ── Established trend ─────────────────────────────────────────────────────
    if regime_bars >= 6:
        chase_warning = regime_bars > 14
        return ContextResult(
            structure=MarketStructure.ESTABLISHED_TREND,
            probability_adjustment=1.10 if not chase_warning else 0.90,
            entry_quality="acceptable",
            note=(
                f"Established {current_sig} trend: {regime_bars} bars, "
                f"strength={strength:.0f}. "
                + ("Chase risk increasing — consider waiting for pullback."
                   if chase_warning else
                   "Trend-follow entry acceptable. Look for first pullback.")
            ),
        )

    return ContextResult(
        structure=MarketStructure.UNCLEAR,
        probability_adjustment=0.90,
        entry_quality="acceptable",
        note="Mixed structure — no dominant pattern detected.",
    )


def cross_signal_adjustment(
    structure: MarketStructure,
    vol_regime: str,
) -> tuple[float, str]:
    """
    Combine trend structure with vol regime for a final probability multiplier.

    Parameters
    ----------
    structure : MarketStructure
        Output of classify_market_structure().
    vol_regime : str
        VolRegime.value from Model 1 (e.g. "High Vol Breakout").

    Returns
    -------
    (multiplier, explanation)
    """
    m     = 1.0
    notes: list[str] = []

    if vol_regime == "High Vol Breakout":
        if structure in (MarketStructure.BREAKOUT, MarketStructure.ESTABLISHED_TREND):
            m *= 0.70
            notes.append("High vol + trend entry = likely chasing move. Reduce size.")
        elif structure == MarketStructure.EXHAUSTION:
            m *= 0.40
            notes.append("High vol + exhaustion = climax reversal. Avoid or fade.")
        elif structure == MarketStructure.PULLBACK:
            m *= 1.20
            notes.append("High vol pullback = momentum continuation likely. Good setup.")

    elif vol_regime == "Compression":
        if structure == MarketStructure.ESTABLISHED_TREND:
            m *= 1.30
            notes.append("Compression + trend = coiling for explosive continuation.")
        elif structure == MarketStructure.TIGHT_RANGE:
            m *= 0.50
            notes.append("Double compression: vol + price both coiling. Wait for direction.")

    elif vol_regime == "Trap Day":
        m *= 0.60
        notes.append("Trap day: fakeout risk elevated across all structures.")

    elif vol_regime == "Expansion":
        if structure == MarketStructure.PULLBACK:
            m *= 1.40
            notes.append("Vol expansion + pullback = momentum building. Strong setup.")
        elif structure == MarketStructure.EXHAUSTION:
            m *= 0.50
            notes.append("Vol expansion + exhaustion = blow-off top/bottom risk.")

    explanation = " | ".join(notes) if notes else "No cross-signal conflict."
    return m, explanation
