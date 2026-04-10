"""
Telegram reporting module.

Formats Model outputs into clean, professional Telegram messages
and sends them via the Bot API.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from config.settings import telegram as tg_cfg, paths
from models.model1_volatility import Model1Result
from models.model2_trend import Model2Result, TrendState
from models.model3_risk import Model3Result, TradeDecision

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Emoji mappings
# ---------------------------------------------------------------------------

_DECISION_EMOJI = {
    TradeDecision.STRONG_BUY: "🟢",
    TradeDecision.SPECULATIVE_VOL_PLAY: "⚡",
    TradeDecision.TREND_CONTINUATION: "📈",
    TradeDecision.SHORT_SELL: "🔴",
    TradeDecision.POSITIVE_EV_STRADDLE: "🎯",
    TradeDecision.NO_TRADE: "⏸️",
    TradeDecision.CAPITAL_PRESERVATION: "🛡️",
}

_TREND_EMOJI = {
    TrendState.BULLISH: "🟢",
    TrendState.BEARISH: "🔴",
    TrendState.HOLD: "🔵",
}


def _confidence_bar(value: float) -> str:
    """ASCII confidence bar."""
    filled = int(value / 20)
    return "▓" * filled + "░" * (5 - filled)


def _risk_indicator(score: float) -> str:
    if score < 30:
        return "🟢 Low"
    elif score < 60:
        return "🟡 Moderate"
    else:
        return "🔴 High"


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_daily_report(
    m1: Model1Result,
    m2: Model2Result,
    m3: Model3Result,
) -> str:
    """
    Format the complete daily report for Telegram.

    Uses MarkdownV2 escaping for Telegram Bot API.
    Falls back to plain text for reliability.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    btc = m3.btc_signal
    regime = m1.regime
    straddle = m1.straddle
    gex = m1.gex

    # --- Header ---
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 QUANT DESK — DAILY REPORT",
        f"🕐 {now}",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # --- BTC Volatility Section ---
    lines.extend([
        f"🔶 BITCOIN — ${m1.spot:,.0f}",
        f"   Regime: {regime.regime.value}",
        f"   Day Type: {m1.day_type}",
        "",
        f"📐 Volatility:",
        f"   RV 1D: {regime.rv_1d:.1%} | 7D: {regime.rv_7d:.1%} | 30D: {regime.rv_30d:.1%}",
        f"   IV Est: {regime.iv_estimate:.1%} | Pctile: {regime.rv_percentile:.0f}th",
        f"   Vol Trend: {regime.vol_trend.upper()}",
        "",
        f"🎯 Expected Move (1D):",
        f"   ±${regime.expected_move_1d:,.0f} ({regime.expected_move_pct:.2%})",
        f"   Range: ${m1.spot - regime.expected_move_1d:,.0f} — ${m1.spot + regime.expected_move_1d:,.0f}",
        "",
    ])

    # --- MVRV proxy (price / SMA200) ---
    if regime.mvrv is not None:
        mvrv = regime.mvrv
        if mvrv < 0.85:
            mvrv_zone = "Undervalued — below long-term cost basis (accumulate)"
            mvrv_emoji = "🟢"
        elif mvrv < 1.25:
            mvrv_zone = "Fair value — near realized cost basis"
            mvrv_emoji = "🟡"
        elif mvrv < 2.0:
            mvrv_zone = "Elevated — premium to cost basis building"
            mvrv_emoji = "🟠"
        else:
            mvrv_zone = "Extreme — historical distribution / cycle-top territory"
            mvrv_emoji = "🔴"
        lines.extend([
            f"📐 MVRV (price/SMA200 proxy):",
            f"   {mvrv_emoji} {mvrv:.2f}x — {mvrv_zone}",
            "",
        ])

    # --- GEX Section ---
    lines.extend([
        f"🏗️ Options Structure (GEX):",
        f"   Call Wall: ${gex.call_wall:,.0f} (+{(gex.call_wall - m1.spot) / m1.spot:.2%})",
        f"   Put Wall:  ${gex.put_wall:,.0f} ({(gex.put_wall - m1.spot) / m1.spot:.2%})",
        f"   Max Pain:  ${gex.max_pain:,.0f}",
        f"   Net GEX:   {'Positive (Stabilizing)' if gex.net_gex > 0 else 'Negative (Destabilizing)'}",
        "",
    ])

    # --- Straddle Section ---
    edge_sign = "+" if straddle.expected_value > 0 else ""
    lines.extend([
        f"📦 1D Straddle Analysis:",
        f"   Best Strike: ${straddle.strike:,.0f}",
        f"   Cost: ${straddle.straddle_cost:,.2f}",
        f"   Breakevens: ${straddle.lower_breakeven:,.0f} — ${straddle.upper_breakeven:,.0f}",
        f"   Breakeven Range: {straddle.breakeven_range_pct:.2%}",
        f"   P(Profit): {straddle.prob_of_profit:.1%}",
        f"   R/R Ratio: {straddle.risk_reward_ratio:.2f}x",
        f"   EV: {edge_sign}${straddle.expected_value:,.2f}",
        "",
    ])

    # --- Decision Section ---
    decision_emoji = _DECISION_EMOJI.get(btc.decision, "❓")
    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"{decision_emoji} DECISION: {btc.decision.value.upper()}",
        f"   Signal Strength: {btc.signal_strength:.0f}/100 [{_confidence_bar(btc.signal_strength)}]",
        f"   Confidence: {btc.confidence:.0f}/100",
        f"   Asymmetry: {btc.asymmetry_ratio:.2f}x",
        f"   Risk: {_risk_indicator(btc.risk_score)} ({btc.risk_score:.0f}/100)",
        f"   Position Size: {btc.position_size_pct:.2f}% of capital",
        "",
        f"   Invalidation: ${btc.invalidation_level:,.0f}",
        f"   Target: ${btc.target_level:,.0f}",
        "",
        f"💬 {btc.reasoning}",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ])

    # --- Multi-Asset Trend Summary ---
    lines.append("📊 MULTI-ASSET TREND SCAN:")
    lines.append("")

    # Group by timeframe for compact display — includes RSI and EMA200 position
    for row in m2.summary:
        if row["timeframe"] == "1h":
            emoji = _TREND_EMOJI.get(TrendState(row["state"]), "⚪")
            alert = " ⚠️" if row.get("transition", False) else ""
            rsi_val = row.get("rsi")
            ema_pct = row.get("ema_vs_200_pct")
            rsi_str = f" | RSI:{rsi_val:.0f}" if rsi_val is not None else ""
            if ema_pct is not None:
                sign = "+" if ema_pct >= 0 else ""
                ema_str = f" | EMA200:{sign}{ema_pct:.1f}%"
            else:
                ema_str = ""
            lines.append(
                f"   {emoji} {row['name']}: {row['state']} "
                f"(Str:{row['strength']:.0f} | Pers:{row['persistence']:.0%}"
                f"{rsi_str}{ema_str}){alert}"
            )

    lines.append("")

    # --- Best Opportunities ---
    if m3.best_opportunities:
        lines.append("🏆 TOP OPPORTUNITIES:")
        for i, opp in enumerate(m3.best_opportunities[:5], 1):
            d_emoji = _DECISION_EMOJI.get(TradeDecision(opp["decision"]), "")
            rsi_str = f" | RSI:{opp['rsi']:.0f}" if opp.get("rsi") else ""
            ema_str = f" | EMA200:{opp['ema_pct']:+.1f}%" if opp.get("ema_pct") is not None else ""
            lines.append(
                f"   {i}. {d_emoji} {opp['name']} — {opp['decision']} "
                f"(Str:{opp['strength']:.0f} | Conf:{opp['confidence']:.0f}%"
                f"{rsi_str}{ema_str})"
            )
        lines.append("")

    # --- Risk Summary ---
    lines.extend([
        f"🛡️ Portfolio Risk Score: {m3.portfolio_risk:.0f}/100",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "Quant Desk v2.0 | RSI + EMA56/200 + MVRV | Not financial advice",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram sending
# ---------------------------------------------------------------------------

def send_telegram_message(
    text: str,
    parse_mode: str = "HTML",
    chat_id: Optional[str] = None,
    bot_token: Optional[str] = None,
) -> bool:
    """
    Send a message via Telegram Bot API.

    Parameters
    ----------
    text : str
        Message text.
    parse_mode : str
        "HTML" or "Markdown".
    chat_id : str, optional
        Override chat ID from config.
    bot_token : str, optional
        Override bot token from config.

    Returns
    -------
    bool
        True if sent successfully.
    """
    token = bot_token or tg_cfg.bot_token
    chat = chat_id or tg_cfg.chat_id

    if not token or not chat:
        logger.warning("Telegram not configured — message printed to console")
        print("\n" + "=" * 60)
        print("TELEGRAM MESSAGE (not sent — configure .env)")
        print("=" * 60)
        print(text)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Telegram has a 4096 char limit per message
    # Split if needed
    chunks = []
    if len(text) > 4000:
        lines = text.split("\n")
        current_chunk = []
        current_len = 0
        for line in lines:
            if current_len + len(line) + 1 > 4000:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
            else:
                current_chunk.append(line)
                current_len += len(line) + 1
        if current_chunk:
            chunks.append("\n".join(current_chunk))
    else:
        chunks = [text]

    success = True
    for i, chunk in enumerate(chunks):
        try:
            # Use plain text (no parse_mode) for reliability with special chars
            payload = {
                "chat_id": chat,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            result = resp.json()
            if not result.get("ok"):
                logger.error(f"Telegram API error: {result}")
                success = False
            else:
                logger.info(f"Telegram message sent ({i + 1}/{len(chunks)})")
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            success = False

    return success


def send_alert(
    title: str,
    body: str,
) -> bool:
    """Send a short alert message (for high-priority signals)."""
    msg = f"🚨 ALERT: {title}\n\n{body}"
    return send_telegram_message(msg)


def send_chart_image(
    image_path: Path,
    caption: str = "",
    chat_id: Optional[str] = None,
    bot_token: Optional[str] = None,
) -> bool:
    """Send a chart image to Telegram."""
    token = bot_token or tg_cfg.bot_token
    chat = chat_id or tg_cfg.chat_id

    if not token or not chat:
        logger.warning("Telegram not configured — chart not sent")
        return False

    url = f"https://api.telegram.org/bot{token}/sendPhoto"

    try:
        with open(image_path, "rb") as photo:
            payload = {"chat_id": chat, "caption": caption[:1024]}
            files = {"photo": photo}
            resp = requests.post(url, data=payload, files=files, timeout=60)
            resp.raise_for_status()
            logger.info(f"Chart sent: {image_path.name}")
            return True
    except Exception as e:
        logger.error(f"Failed to send chart: {e}")
        return False
