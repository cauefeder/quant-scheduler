"""
Model 2 — Multi-Asset Trend Classification Engine

Uses a volatility-adjusted regime detection system (NOT simple MA crossovers)
to classify each bar as Bullish / Bearish / Hold.

Core logic:
- EMA momentum with volatility-adjusted thresholds
- ATR-normalized trend strength
- Rate of change confirmation
- Volume regime filter
- Trend persistence scoring

Non-repainting: all signals use completed bars only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data.fetcher import fetch_yf
from config.settings import trend as trend_cfg
from analytics.context import (
    ContextResult, MarketStructure,
    classify_market_structure, cross_signal_adjustment,
)

logger = logging.getLogger(__name__)


class TrendState(str, Enum):
    BULLISH = "Bullish"
    BEARISH = "Bearish"
    HOLD = "Hold"


@dataclass
class TrendResult:
    """Trend classification for a single asset."""
    ticker: str
    name: str
    timeframe: str
    current_state: TrendState
    strength: float          # 0-100
    persistence_prob: float  # probability trend continues (0-1)
    bars_in_regime: int
    support: float
    resistance: float
    atr: float
    atr_pct: float
    last_price: float
    signal_history: pd.DataFrame  # full classified data
    transition_detected: bool
    context: ContextResult         # market structure classification
    cross_adjustment: float        # vol×structure multiplier
    cross_note: str                # plain-English cross-signal explanation


@dataclass
class Model2Result:
    """Complete output of Model 2."""
    results: Dict[str, Dict[str, TrendResult]]  # {ticker: {timeframe: TrendResult}}
    summary: List[Dict]  # Flat summary for reporting
    charts_html: Dict[str, str]  # {ticker: html_path}


# ---------------------------------------------------------------------------
# Trend classification engine
# ---------------------------------------------------------------------------

def _compute_ema(series: pd.Series, span: int) -> pd.Series:
    """EMA that doesn't repaint — uses closed bars only."""
    return series.ewm(span=span, adjust=False).mean()


def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def classify_trend(
    df: pd.DataFrame,
    fast_ema: int = 12,
    slow_ema: int = 26,
    signal_ema: int = 9,
    atr_period: int = 14,
    vol_lookback: int = 20,
) -> pd.DataFrame:
    """
    Classify each bar as Bullish / Bearish / Hold.

    Algorithm:
    1. Compute fast/slow EMA spread (MACD-style but volatility-adjusted)
    2. Normalize spread by ATR → removes false signals in low-vol
    3. Apply signal EMA as confirmation
    4. Score trend strength (0-100)
    5. Track regime duration for persistence

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data (must have 'close', 'high', 'low', 'volume').

    Returns
    -------
    pd.DataFrame
        Original data plus: signal, strength, regime_bars, support, resistance
    """
    out = df.copy()
    close = out["close"]

    # --- Core trend indicators ---
    ema_fast = _compute_ema(close, fast_ema)
    ema_slow = _compute_ema(close, slow_ema)
    spread = ema_fast - ema_slow

    # ATR for volatility normalization
    atr = _compute_atr(out, atr_period)
    out["atr"] = atr
    out["atr_pct"] = atr / close

    # Volatility-adjusted spread
    # Dividing by ATR means we need a MEANINGFUL move relative to volatility
    vol_adj_spread = spread / atr.replace(0, np.nan)
    signal_line = _compute_ema(vol_adj_spread, signal_ema)

    # --- Rate of Change confirmation ---
    roc_10 = close.pct_change(10)
    roc_20 = close.pct_change(20)

    # --- Volume regime (relative volume) ---
    vol_sma = out["volume"].rolling(vol_lookback).mean()
    rel_volume = out["volume"] / vol_sma.replace(0, np.nan)

    # --- Classification ---
    # Bull: vol_adj_spread > signal AND positive ROC AND spread > threshold
    # Bear: vol_adj_spread < signal AND negative ROC
    # Hold: ambiguous or transitioning
    threshold = 0.5  # Minimum ATR-normalized spread for conviction

    signals = pd.Series(TrendState.HOLD.value, index=out.index)

    bull_mask = (
        (vol_adj_spread > signal_line) &
        (vol_adj_spread > threshold) &
        (roc_10 > 0)
    )
    bear_mask = (
        (vol_adj_spread < signal_line) &
        (vol_adj_spread < -threshold) &
        (roc_10 < 0)
    )

    signals[bull_mask] = TrendState.BULLISH.value
    signals[bear_mask] = TrendState.BEARISH.value

    # --- Anti-repaint: use shifted signals (closed bars only) ---
    out["signal"] = signals.shift(1).fillna(TrendState.HOLD.value)

    # --- Trend strength (0-100) ---
    # Based on: |spread|, ROC magnitude, volume confirmation
    abs_spread_norm = vol_adj_spread.abs().clip(0, 5) / 5 * 40  # 0-40 points
    roc_norm = roc_10.abs().clip(0, 0.10) / 0.10 * 30  # 0-30 points
    vol_norm = rel_volume.clip(0, 3) / 3 * 20  # 0-20 points
    roc20_bonus = (roc_20.abs() > roc_10.abs()).astype(float) * 10  # momentum building

    out["strength"] = (abs_spread_norm + roc_norm + vol_norm + roc20_bonus).clip(0, 100)

    # --- Regime duration (consecutive bars in same state) ---
    regime_bars = pd.Series(0, index=out.index)
    count = 0
    prev_state = None
    for i, state in enumerate(out["signal"]):
        if state == prev_state:
            count += 1
        else:
            count = 1
        regime_bars.iloc[i] = count
        prev_state = state
    out["regime_bars"] = regime_bars

    # --- Support / Resistance (rolling extremes within regime) ---
    out["support"] = out["low"].rolling(window=20).min()
    out["resistance"] = out["high"].rolling(window=20).max()

    # --- Transition detection ---
    out["transition"] = out["signal"] != out["signal"].shift(1)

    # Store intermediate values for debugging
    out["ema_fast"] = ema_fast
    out["ema_slow"] = ema_slow
    out["vol_adj_spread"] = vol_adj_spread
    out["signal_line"] = signal_line

    return out


def compute_persistence_probability(df: pd.DataFrame) -> float:
    """
    Estimate probability that the current trend continues.

    Based on historical regime duration distribution.
    """
    signals = df["signal"]
    current_regime = signals.iloc[-1]
    regime_bars = df["regime_bars"].iloc[-1]

    # Count all historical regime durations for this state
    durations = []
    count = 0
    prev = None
    for s in signals:
        if s == prev:
            count += 1
        else:
            if prev == current_regime and count > 0:
                durations.append(count)
            count = 1
        prev = s

    if len(durations) < 3:
        return 0.5  # Not enough data

    # P(continue) = P(duration > current_bars | duration >= current_bars)
    arr = np.array(durations)
    survived = (arr >= regime_bars).sum()
    continued = (arr > regime_bars).sum()

    if survived == 0:
        return 0.5

    return float(continued / survived)


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------

def create_trend_chart(
    df: pd.DataFrame,
    ticker: str,
    name: str,
    timeframe: str,
) -> go.Figure:
    """
    Create a colored price chart with trend segments.

    Green = Bullish, Red = Bearish, Blue = Hold.
    """
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=(
            f"{name} ({ticker}) — {timeframe}",
            "Trend Strength",
            "Volume",
        ),
    )

    # --- Price with colored segments ---
    color_map = {
        TrendState.BULLISH.value: "rgba(0, 200, 83, 0.8)",
        TrendState.BEARISH.value: "rgba(255, 68, 68, 0.8)",
        TrendState.HOLD.value: "rgba(66, 133, 244, 0.6)",
    }

    # Plot segments as colored areas
    for state, color in color_map.items():
        mask = df["signal"] == state
        if mask.any():
            segment_df = df[mask]
            fig.add_trace(
                go.Scatter(
                    x=segment_df.index,
                    y=segment_df["close"],
                    mode="markers",
                    marker=dict(size=3, color=color),
                    name=state,
                    showlegend=True,
                ),
                row=1, col=1,
            )

    # Price line (thin, for continuity)
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["close"],
            mode="lines",
            line=dict(color="rgba(100,100,100,0.3)", width=1),
            name="Price",
            showlegend=False,
        ),
        row=1, col=1,
    )

    # EMAs
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["ema_fast"],
            mode="lines", line=dict(color="orange", width=1, dash="dot"),
            name=f"EMA {trend_cfg.fast_ema}", showlegend=True,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["ema_slow"],
            mode="lines", line=dict(color="purple", width=1, dash="dot"),
            name=f"EMA {trend_cfg.slow_ema}", showlegend=True,
        ),
        row=1, col=1,
    )

    # Support / Resistance
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["support"],
            mode="lines", line=dict(color="red", width=1, dash="dash"),
            name="Support", showlegend=True,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["resistance"],
            mode="lines", line=dict(color="green", width=1, dash="dash"),
            name="Resistance", showlegend=True,
        ),
        row=1, col=1,
    )

    # --- Trend strength ---
    strength_colors = [
        "green" if s == TrendState.BULLISH.value
        else "red" if s == TrendState.BEARISH.value
        else "blue"
        for s in df["signal"]
    ]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["strength"],
            marker_color=strength_colors,
            name="Strength",
            showlegend=False,
        ),
        row=2, col=1,
    )
    fig.add_hline(y=65, line_dash="dash", line_color="gray", row=2, col=1)
    fig.add_hline(y=35, line_dash="dash", line_color="gray", row=2, col=1)

    # --- Volume ---
    vol_colors = ["green" if c >= o else "red" for c, o in zip(df["close"], df["open"])]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["volume"],
            marker_color=vol_colors,
            opacity=0.5,
            name="Volume",
            showlegend=False,
        ),
        row=3, col=1,
    )

    # Mark transitions
    transitions = df[df["transition"]]
    if not transitions.empty:
        fig.add_trace(
            go.Scatter(
                x=transitions.index,
                y=transitions["close"],
                mode="markers",
                marker=dict(symbol="diamond", size=8, color="white", line=dict(width=2, color="black")),
                name="Regime Change",
            ),
            row=1, col=1,
        )

    fig.update_layout(
        height=800,
        template="plotly_dark",
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return fig


# ---------------------------------------------------------------------------
# Full Model 2 pipeline
# ---------------------------------------------------------------------------

def run_model2(
    tickers: Optional[Dict[str, str]] = None,
    timeframes: Optional[Dict[str, Dict]] = None,
    custom_ticker: Optional[str] = None,
    vol_regime: Optional[str] = None,   # VolRegime.value from Model 1 for cross-signal
) -> Model2Result:
    """
    Execute Model 2 for all configured assets and timeframes.

    Parameters
    ----------
    tickers : dict, optional
        Override default tickers.
    timeframes : dict, optional
        Override default timeframes.
    custom_ticker : str, optional
        Add a custom ticker (Yahoo Finance symbol).

    Returns
    -------
    Model2Result
    """
    logger.info("=" * 60)
    logger.info("MODEL 2 — Multi-Asset Trend Classification")
    logger.info("=" * 60)

    if tickers is None:
        tickers = dict(trend_cfg.default_tickers)
    if timeframes is None:
        timeframes = dict(trend_cfg.timeframes)

    # Add custom ticker if specified
    if custom_ticker:
        tickers[custom_ticker] = custom_ticker

    all_results: Dict[str, Dict[str, TrendResult]] = {}
    summary: List[Dict] = []
    charts_html: Dict[str, str] = {}

    for ticker, name in tickers.items():
        all_results[ticker] = {}

        for tf_name, tf_params in timeframes.items():
            logger.info(f"Processing {ticker} ({tf_name})...")

            df = fetch_yf(ticker, period=tf_params["period"], interval=tf_params["interval"])
            if df is None or len(df) < 50:
                logger.warning(f"[{ticker}/{tf_name}] Insufficient data, skipping")
                continue

            # Classify
            classified = classify_trend(
                df,
                fast_ema=trend_cfg.fast_ema,
                slow_ema=trend_cfg.slow_ema,
                signal_ema=trend_cfg.signal_ema,
                atr_period=trend_cfg.atr_period,
                vol_lookback=trend_cfg.vol_lookback,
            )

            current_state = TrendState(classified["signal"].iloc[-1])
            strength = float(classified["strength"].iloc[-1])
            regime_bars = int(classified["regime_bars"].iloc[-1])
            persistence = compute_persistence_probability(classified)
            last_price = float(classified["close"].iloc[-1])
            atr_val = float(classified["atr"].iloc[-1])
            atr_pct_val = float(classified["atr_pct"].iloc[-1])
            support = float(classified["support"].iloc[-1])
            resistance = float(classified["resistance"].iloc[-1])
            transition = bool(classified["transition"].iloc[-1])

            # ── Context layer ──────────────────────────────────────────────
            ctx = classify_market_structure(classified)
            cross_mult, cross_note = cross_signal_adjustment(
                ctx.structure, vol_regime or "Normal"
            )

            result = TrendResult(
                ticker=ticker,
                name=name,
                timeframe=tf_name,
                current_state=current_state,
                strength=strength,
                persistence_prob=persistence,
                bars_in_regime=regime_bars,
                support=support,
                resistance=resistance,
                atr=atr_val,
                atr_pct=atr_pct_val,
                last_price=last_price,
                signal_history=classified,
                transition_detected=transition,
                context=ctx,
                cross_adjustment=cross_mult,
                cross_note=cross_note,
            )

            all_results[ticker][tf_name] = result

            # Summary row
            summary.append({
                "ticker": ticker,
                "name": name,
                "timeframe": tf_name,
                "state": current_state.value,
                "strength": strength,
                "persistence": persistence,
                "bars_in_regime": regime_bars,
                "price": last_price,
                "atr_pct": atr_pct_val,
                "transition": transition,
                "structure": ctx.structure.value,
                "entry_quality": ctx.entry_quality,
                "prob_adjustment": round(ctx.probability_adjustment * cross_mult, 2),
                "context_note": ctx.note,
                "cross_note": cross_note,
            })

            logger.info(
                f"  [{ticker}/{tf_name}] {current_state.value} | "
                f"Strength={strength:.0f} | Bars={regime_bars} | "
                f"Persistence={persistence:.0%}"
            )

        # Generate chart for primary timeframe (1h)
        if "1h" in all_results.get(ticker, {}):
            primary = all_results[ticker]["1h"]
            fig = create_trend_chart(
                primary.signal_history, ticker, name, "1H"
            )
            charts_html[ticker] = fig.to_html(include_plotlyjs="cdn")

    return Model2Result(
        results=all_results,
        summary=summary,
        charts_html=charts_html,
    )
