"""
Model 3 — Signal Strength, Probability & Risk Engine

Integrates outputs from Model 1 (Volatility) and Model 2 (Trend) to:
1. Compute probability-weighted trade score
2. Estimate win probability and expected value
3. Assess risk of ruin and tail risk
4. Generate final trade decision with position sizing

Decision outputs:
- Strong Buy
- Speculative Vol Play
- Trend Continuation
- No Trade
- Capital Preservation Mode
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

import numpy as np

from analytics.regime import VolRegime
from config.settings import risk as risk_cfg
from models.model1_volatility import Model1Result
from models.model2_trend import Model2Result, TrendState

logger = logging.getLogger(__name__)


class TradeDecision(str, Enum):
    STRONG_BUY = "Strong Buy"
    SPECULATIVE_VOL_PLAY = "Speculative Vol Play"
    TREND_CONTINUATION = "Trend Continuation"
    NO_TRADE = "No Trade"
    CAPITAL_PRESERVATION = "Capital Preservation Mode"


@dataclass
class RiskMetrics:
    """Risk analysis output."""
    win_probability: float        # 0-1
    expected_value: float         # USD
    risk_reward_ratio: float
    risk_of_ruin: float           # 0-1
    tail_risk_exposure: float     # 0-100
    max_drawdown_estimate: float  # %
    kelly_fraction: float         # optimal bet size
    suggested_position_pct: float # dampened Kelly


@dataclass
class TradeSignal:
    """Final integrated signal."""
    decision: TradeDecision
    signal_strength: float        # 0-100
    confidence: float             # 0-100
    asymmetry_ratio: float
    risk_score: float             # 0-100 (lower = safer)
    position_size_pct: float      # % of capital
    invalidation_level: float     # price level
    target_level: float           # price level
    stop_loss: float
    risk_metrics: RiskMetrics
    reasoning: str
    alert_worthy: bool


@dataclass
class Model3Result:
    """Complete output of Model 3."""
    btc_signal: TradeSignal
    trend_signals: Dict[str, TradeSignal]  # per-ticker signals
    portfolio_risk: float         # aggregate risk score
    best_opportunities: List[Dict]
    summary: str


# ---------------------------------------------------------------------------
# Risk calculations
# ---------------------------------------------------------------------------

def compute_risk_metrics(
    win_prob: float,
    avg_win: float,
    avg_loss: float,
    capital: float = 10000.0,
) -> RiskMetrics:
    """
    Compute comprehensive risk metrics.

    Parameters
    ----------
    win_prob : float
        Estimated win probability (0-1).
    avg_win : float
        Average winning trade (USD).
    avg_loss : float
        Average losing trade (USD, positive number).
    capital : float
        Total capital for risk of ruin calculation.
    """
    # Expected value
    ev = win_prob * avg_win - (1 - win_prob) * avg_loss

    # Risk/reward
    rr = avg_win / avg_loss if avg_loss > 0 else 0

    # Kelly criterion
    if avg_loss > 0 and avg_win > 0:
        b = avg_win / avg_loss  # odds
        kelly = (win_prob * b - (1 - win_prob)) / b
        kelly = max(0, kelly)
    else:
        kelly = 0

    # Dampened Kelly (quarter-Kelly for safety)
    suggested_pct = kelly * risk_cfg.kelly_fraction

    # Risk of ruin (simplified Gambler's ruin)
    if win_prob > 0 and win_prob < 1 and avg_loss > 0:
        # Using the formula: RoR = ((1-p)/p)^(units)
        # where units = capital / avg_loss
        units = capital / avg_loss if avg_loss > 0 else 100
        ratio = (1 - win_prob) / win_prob if win_prob > 0.01 else 100
        if ratio < 1:
            ror = ratio ** min(units, 100)
        else:
            ror = min(1.0, ratio ** min(units, 10))
    else:
        ror = 0.5

    # Tail risk: probability of losing > 2x avg_loss
    # Simplified: based on vol regime
    tail_risk = (1 - win_prob) * 50 + (1 / max(rr, 0.1)) * 25

    # Max drawdown estimate (empirical: ~2x max consecutive loss)
    max_consecutive = risk_cfg.max_consecutive_losses
    max_dd = (avg_loss * max_consecutive / capital) * 100 if capital > 0 else 100

    return RiskMetrics(
        win_probability=win_prob,
        expected_value=ev,
        risk_reward_ratio=rr,
        risk_of_ruin=min(1, ror),
        tail_risk_exposure=min(100, tail_risk),
        max_drawdown_estimate=min(100, max_dd),
        kelly_fraction=kelly,
        suggested_position_pct=min(suggested_pct, risk_cfg.max_position_pct),
    )


# ---------------------------------------------------------------------------
# Signal integration
# ---------------------------------------------------------------------------

def _score_vol_opportunity(m1: Model1Result) -> float:
    """Score the volatility/straddle opportunity (0-100)."""
    score = 0.0

    # Edge score: is expected move > straddle cost?
    if m1.regime.expected_move_1d > m1.straddle.straddle_cost:
        edge_ratio = m1.regime.expected_move_1d / m1.straddle.straddle_cost
        score += min(40, edge_ratio * 20)

    # Regime bonus
    regime_scores = {
        VolRegime.HIGH_VOL_BREAKOUT: 25,
        VolRegime.EXPANSION: 20,
        VolRegime.COMPRESSION: 15,  # cheap but need catalyst
        VolRegime.NORMAL: 5,
        VolRegime.MEAN_REVERSION: -10,
        VolRegime.TRAP: -5,
    }
    score += regime_scores.get(m1.regime.regime, 0)

    # Probability of profit bonus
    score += m1.straddle.prob_of_profit * 30

    # R/R bonus
    if m1.straddle.risk_reward_ratio > 2:
        score += 10
    elif m1.straddle.risk_reward_ratio > 1.5:
        score += 5

    return max(0, min(100, score))


def _score_trend_signal(trend_result) -> float:
    """Score a trend signal (0-100) from Model 2."""
    score = float(trend_result.strength)

    # Persistence bonus
    if trend_result.persistence_prob > 0.7:
        score += 10
    elif trend_result.persistence_prob < 0.3:
        score -= 10

    # Regime duration bonus (established trends)
    if trend_result.bars_in_regime > 10:
        score += 5

    # Transition penalty (just switched — less reliable)
    if trend_result.transition_detected:
        score -= 15

    # EMA 200 long-term confirmation:
    # Price above EMA200 on a bullish signal = trend aligned with long-term structure (+8)
    # Price below EMA200 on a bearish signal = same (+8)
    # Price against EMA200 = penalty (-8) — fade caution
    try:
        ema_vs_200 = float(trend_result.signal_history["ema_vs_200_pct"].iloc[-1])
        state = trend_result.current_state.value
        if (state == "Bullish" and ema_vs_200 > 0.0) or (state == "Bearish" and ema_vs_200 < 0.0):
            score += 8   # aligned with long-term structure
        elif (state == "Bullish" and ema_vs_200 < -0.03) or (state == "Bearish" and ema_vs_200 > 0.03):
            score -= 8   # >3% against EMA200 — counter-trend, reduce confidence
    except (KeyError, IndexError):
        pass

    # RSI extremes reduce reliability (overbought bull / oversold bear = less room to run)
    try:
        rsi_val = float(trend_result.signal_history["rsi"].iloc[-1])
        if trend_result.current_state.value == "Bullish" and rsi_val > 75:
            score -= 10  # overbought — don't chase
        elif trend_result.current_state.value == "Bearish" and rsi_val < 25:
            score -= 10  # oversold — bounce risk

    except (KeyError, IndexError):
        pass

    return max(0, min(100, score))


def integrate_signals(
    m1: Model1Result,
    m2: Model2Result,
    capital: float = 10000.0,
) -> Model3Result:
    """
    Integrate Model 1 and Model 2 into actionable trade decisions.

    Parameters
    ----------
    m1 : Model1Result
        Volatility model output.
    m2 : Model2Result
        Trend model output.
    capital : float
        Total trading capital (USD).

    Returns
    -------
    Model3Result
    """
    logger.info("=" * 60)
    logger.info("MODEL 3 — Signal Integration & Risk Engine")
    logger.info("=" * 60)

    # --- BTC Straddle Signal ---
    vol_score = _score_vol_opportunity(m1)

    # Get BTC trend from Model 2 for confirmation
    btc_trend_1h = m2.results.get("BTC-USD", {}).get("1h", None)
    btc_trend_score = _score_trend_signal(btc_trend_1h) if btc_trend_1h else 50

    # Combined BTC signal
    btc_combined = vol_score * 0.6 + btc_trend_score * 0.4

    # Risk metrics for straddle
    straddle_risk = compute_risk_metrics(
        win_prob=m1.straddle.prob_of_profit,
        avg_win=m1.regime.expected_move_1d * 0.7,  # conservative: capture 70% of move
        avg_loss=m1.straddle.straddle_cost,
        capital=capital,
    )

    # Asymmetry
    asymmetry = straddle_risk.risk_reward_ratio

    # Risk score (0-100, lower = safer)
    risk_score = (
        (1 - m1.straddle.prob_of_profit) * 40 +
        straddle_risk.risk_of_ruin * 30 +
        straddle_risk.tail_risk_exposure * 0.3
    )

    # Decision logic for BTC
    if (
        btc_combined > risk_cfg.min_signal_strength and
        asymmetry > risk_cfg.min_asymmetry_ratio and
        risk_score < risk_cfg.max_risk_score
    ):
        if vol_score > 70 and m1.regime.regime in (VolRegime.HIGH_VOL_BREAKOUT, VolRegime.EXPANSION):
            decision = TradeDecision.SPECULATIVE_VOL_PLAY
            reasoning = (
                f"Vol regime ({m1.regime.regime.value}) favors straddle. "
                f"Expected move > cost. Score={btc_combined:.0f}."
            )
        elif btc_trend_1h and btc_trend_1h.current_state == TrendState.BULLISH and btc_trend_score > 60:
            decision = TradeDecision.STRONG_BUY
            reasoning = (
                f"Strong bullish trend + vol opportunity aligned. "
                f"Trend strength={btc_trend_score:.0f}, Vol score={vol_score:.0f}."
            )
        else:
            decision = TradeDecision.TREND_CONTINUATION
            reasoning = f"Moderate opportunity. Combined score={btc_combined:.0f}."
    elif risk_score > 80:
        decision = TradeDecision.CAPITAL_PRESERVATION
        reasoning = f"Risk too high ({risk_score:.0f}). Preserve capital."
    else:
        decision = TradeDecision.NO_TRADE
        reasoning = (
            f"Thresholds not met. Signal={btc_combined:.0f} "
            f"(need>{risk_cfg.min_signal_strength:.0f}), "
            f"Asymmetry={asymmetry:.2f} (need>{risk_cfg.min_asymmetry_ratio:.1f})."
        )

    alert_worthy = decision in (
        TradeDecision.STRONG_BUY,
        TradeDecision.SPECULATIVE_VOL_PLAY,
    )

    btc_signal = TradeSignal(
        decision=decision,
        signal_strength=btc_combined,
        confidence=min(100, btc_combined * (1 + straddle_risk.win_probability) / 2),
        asymmetry_ratio=asymmetry,
        risk_score=risk_score,
        position_size_pct=straddle_risk.suggested_position_pct * 100,
        invalidation_level=m1.gex.put_wall,
        target_level=m1.gex.call_wall,
        stop_loss=m1.straddle.lower_breakeven,
        risk_metrics=straddle_risk,
        reasoning=reasoning,
        alert_worthy=alert_worthy,
    )

    # --- Per-ticker trend signals ---
    trend_signals: Dict[str, TradeSignal] = {}
    best_opportunities: List[Dict] = []

    for ticker, tf_results in m2.results.items():
        if "1h" not in tf_results:
            continue
        tr = tf_results["1h"]
        t_score = _score_trend_signal(tr)

        # Simple risk metrics for trend trades
        atr_stop = tr.atr * 2  # 2x ATR stop loss
        avg_win_est = tr.atr * 3  # 3:1 target
        t_risk = compute_risk_metrics(
            win_prob=tr.persistence_prob * 0.7 + 0.15,  # base + persistence
            avg_win=avg_win_est,
            avg_loss=atr_stop,
            capital=capital,
        )

        t_asymmetry = t_risk.risk_reward_ratio
        t_risk_score = (1 - t_risk.win_probability) * 50 + t_risk.tail_risk_exposure * 0.5

        if t_score > risk_cfg.min_signal_strength and t_asymmetry > 1.2:
            if tr.current_state == TrendState.BULLISH:
                t_decision = TradeDecision.TREND_CONTINUATION
            elif tr.current_state == TrendState.BEARISH:
                t_decision = TradeDecision.CAPITAL_PRESERVATION
            else:
                t_decision = TradeDecision.NO_TRADE
        else:
            t_decision = TradeDecision.NO_TRADE

        t_signal = TradeSignal(
            decision=t_decision,
            signal_strength=t_score,
            confidence=min(100, t_score * 0.8),
            asymmetry_ratio=t_asymmetry,
            risk_score=t_risk_score,
            position_size_pct=t_risk.suggested_position_pct * 100,
            invalidation_level=tr.support,
            target_level=tr.resistance,
            stop_loss=tr.last_price - atr_stop if tr.current_state == TrendState.BULLISH else tr.last_price + atr_stop,
            risk_metrics=t_risk,
            reasoning=f"{tr.current_state.value} trend, strength={tr.strength:.0f}, persistence={tr.persistence_prob:.0%}",
            alert_worthy=(t_decision != TradeDecision.NO_TRADE and t_score > 70),
        )

        trend_signals[ticker] = t_signal

        if t_signal.alert_worthy:
            best_opportunities.append({
                "ticker": ticker,
                "name": tr.name,
                "decision": t_decision.value,
                "strength": t_score,
                "confidence": t_signal.confidence,
                "state": tr.current_state.value,
            })

    # Sort opportunities by strength
    best_opportunities.sort(key=lambda x: x["strength"], reverse=True)

    # Portfolio risk
    active_signals = [s for s in trend_signals.values() if s.decision != TradeDecision.NO_TRADE]
    portfolio_risk = (
        sum(s.risk_score for s in active_signals) / max(len(active_signals), 1)
    )

    # Summary text
    summary_lines = [
        f"BTC: {btc_signal.decision.value} (Score: {btc_signal.signal_strength:.0f})",
    ]
    for opp in best_opportunities[:5]:
        summary_lines.append(
            f"  {opp['ticker']}: {opp['decision']} — {opp['state']} "
            f"(Strength: {opp['strength']:.0f})"
        )
    if not best_opportunities:
        summary_lines.append("  No high-conviction trend opportunities detected.")

    result = Model3Result(
        btc_signal=btc_signal,
        trend_signals=trend_signals,
        portfolio_risk=portfolio_risk,
        best_opportunities=best_opportunities,
        summary="\n".join(summary_lines),
    )

    logger.info(f"BTC Decision: {btc_signal.decision.value}")
    logger.info(f"Alert-worthy opportunities: {len(best_opportunities)}")

    return result
