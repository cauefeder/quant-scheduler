"""Build Telegram-ready alpha reports with live Polymarket data and smart-money signals.

Pipeline
--------
1. Load historical calibration map  (from output/polymarket_win_rate_by_price.csv)
2. Fetch live active markets         (Gamma API)
3. Score each market against calibration → list[BetIdea]
4. Fetch smart-money positions        (Polymarket Data API leaderboard)
5. Cross-reference: mark ideas where smart-money agrees with calibration
6. Rank and format → Telegram HTML report

Each opportunity is labelled BUY YES / BUY NO with a conviction tier:

  HIGH       calibration edge + smart money both confirm the same side
  MODERATE   calibration edge >= 2pp  OR  smart money alone (no cal. data)
  LOW        calibration edge < 2pp, no smart-money confirmation
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from smart_money import SmartMoneySignal, build_smart_money_signals
from utils import Config

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

# Minimum thresholds — tighten to cut noise
MIN_LIQUIDITY = 1_000    # $1 k minimum liquidity
MIN_VOLUME = 5_000       # $5 k minimum all-time volume
MIN_SAMPLE_SIZE = 500    # historical trades needed for calibration edge
MIN_EDGE_PP = 0.5        # minimum 0.5pp edge


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class BetIdea:
    title: str
    url: str
    side: str           # "YES" or "NO"
    entry_price: float
    edge: float
    ev: float
    kelly: float
    liquidity: float
    volume: float
    volume_24h: float
    sample_size: int
    days_to_resolution: float | None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _esc(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slug_from_url(url: str) -> str:
    """Extract market slug from a Polymarket event URL."""
    if "/event/" in url:
        return url.split("/event/")[-1]
    return ""


# ── Calibration loading ───────────────────────────────────────────────────────

def _load_calibration_map(output_dir: Path) -> dict[int, dict[str, float]]:
    """Map price_cents -> {q_yes, n} from historical calibration data."""
    csv_path = output_dir / "polymarket_win_rate_by_price.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path)
        mapping: dict[int, dict[str, float]] = {}
        for _, row in df.iterrows():
            cent = int(round(float(row["price"])))
            if cent < 1 or cent > 99:
                continue
            mapping[cent] = {
                "q_yes": float(row["win_rate"]) / 100.0,
                "n": int(row["total_trades"]),
            }
        return mapping

    # Fallback to pipeline JSON when CSV is missing
    pipeline_path = output_dir / "pipeline_results.json"
    if pipeline_path.exists():
        with open(pipeline_path, encoding="utf-8") as f:
            data = json.load(f)
        bins = data.get("calibration", {}).get("bins", [])
        mapping = {}
        for b in bins:
            cent = int(round(_safe_float(b.get("price"), 0.0) * 100))
            if cent < 1 or cent > 99:
                continue
            mapping[cent] = {
                "q_yes": _safe_float(b.get("empirical"), 0.0),
                "n": int(_safe_float(b.get("n"), 0)),
            }
        return mapping

    return {}


# ── Live market fetching ──────────────────────────────────────────────────────

def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _fetch_live_markets(limit: int = 300, max_pages: int = 3) -> list[dict[str, Any]]:
    """Fetch active Polymarket markets sorted by volume."""
    markets: list[dict[str, Any]] = []
    offset = 0
    with httpx.Client(timeout=25.0) as client:
        for _ in range(max_pages):
            params = {
                "limit": limit,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "archived": "false",
                "order": "volume",
                "ascending": "false",
            }
            try:
                resp = client.get(GAMMA_API_URL, params=params)
                resp.raise_for_status()
                page = resp.json()
            except Exception:
                break
            if not isinstance(page, list) or not page:
                break
            markets.extend(page)
            if len(page) < limit:
                break
            offset += len(page)
    return markets


def _parse_days_to_resolution(market: dict[str, Any]) -> float | None:
    now = datetime.now(timezone.utc)
    for key in ("endDate", "end_date_iso", "endDateIso", "end_date"):
        val = market.get(key)
        if val:
            try:
                dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                return max((dt - now).total_seconds() / 86400, 0.0)
            except (ValueError, TypeError):
                continue
    return None


# ── Market scoring ────────────────────────────────────────────────────────────

def _score_market(
    market: dict[str, Any],
    calibration: dict[int, dict[str, float]],
) -> BetIdea | None:
    """Score one live market against historical calibration. Returns None if no edge."""
    outcomes = _parse_json_list(market.get("outcomes"))
    prices_raw = _parse_json_list(market.get("outcomePrices"))
    if len(outcomes) != 2 or len(prices_raw) != 2:
        return None

    try:
        yes_idx = next(i for i, o in enumerate(outcomes) if str(o).strip().lower() == "yes")
        no_idx = next(i for i, o in enumerate(outcomes) if str(o).strip().lower() == "no")
    except StopIteration:
        return None

    p_yes = _safe_float(prices_raw[yes_idx], -1.0)
    p_no = _safe_float(prices_raw[no_idx], -1.0)
    if not (0.0 < p_yes < 1.0 and 0.0 < p_no < 1.0):
        return None

    cent = int(round(p_yes * 100))
    if cent < 1 or cent > 99 or cent not in calibration:
        return None

    q_yes = calibration[cent]["q_yes"]
    sample_size = int(calibration[cent]["n"])
    if sample_size < MIN_SAMPLE_SIZE:
        return None

    liquidity = _safe_float(market.get("liquidityClob") or market.get("liquidity"), 0.0)
    volume = _safe_float(market.get("volume"), 0.0)
    volume_24h = _safe_float(market.get("volume24hr") or market.get("volume24h"), 0.0)

    if liquidity < MIN_LIQUIDITY or volume < MIN_VOLUME:
        return None

    edge_yes = q_yes - p_yes
    edge_no = (1.0 - q_yes) - p_no

    if edge_yes >= edge_no:
        side, entry, q_side, edge = "YES", p_yes, q_yes, edge_yes
    else:
        side, entry, q_side, edge = "NO", p_no, 1.0 - q_yes, edge_no

    if edge * 100 < MIN_EDGE_PP or entry <= 0.01 or entry >= 0.99:
        return None

    b = (1.0 - entry) / entry
    ev = q_side * b - (1.0 - q_side)
    kelly = max(0.0, (b * q_side - (1.0 - q_side)) / b) if b > 0 else 0.0

    slug = market.get("slug") or ""
    url = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"

    return BetIdea(
        title=str(market.get("question") or "Untitled market"),
        url=url,
        side=side,
        entry_price=entry,
        edge=edge,
        ev=ev,
        kelly=kelly,
        liquidity=liquidity,
        volume=volume,
        volume_24h=volume_24h,
        sample_size=sample_size,
        days_to_resolution=_parse_days_to_resolution(market),
    )


def _composite_score(idea: BetIdea) -> float:
    """Composite ranking: balances edge, liquidity, 24h volume, and sample size."""
    liq_factor = max(idea.liquidity, 1.0) ** 0.20
    vol_factor = max(idea.volume_24h + 1.0, 1.0) ** 0.10
    sample_factor = max(idea.sample_size, 1) ** 0.10
    return idea.edge * liq_factor * vol_factor * sample_factor


# ── Smart money cross-reference ───────────────────────────────────────────────

def _fetch_sm_index() -> dict[str, SmartMoneySignal]:
    """
    Fetch smart-money signals and index them by market slug.
    Returns {} if the network is unavailable (graceful degradation).
    """
    try:
        signals = build_smart_money_signals()
        return {s.market_slug: s for s in signals}
    except Exception:
        return {}


def _conviction_label(idea: BetIdea, sm: SmartMoneySignal | None) -> str:
    """
    Determine conviction tier for one opportunity.

    HIGH     — calibration AND smart money agree on the same side
    MODERATE — strong calibration edge (≥ 2pp) with no smart-money data,
               OR smart-money confirms with any calibration edge
    LOW      — calibration edge only, below 2pp threshold
    """
    if sm is not None and sm.side == idea.side:
        return "HIGH"
    if idea.edge * 100 >= 2.0 or sm is not None:
        return "MODERATE"
    return "LOW"


# ── Report formatting helpers ─────────────────────────────────────────────────

_SEP = "─" * 34
_CONVICTION_MARKER = {"HIGH": "★", "MODERATE": "◆", "LOW": "▸"}


def _price_levels(entry: float, edge: float) -> tuple[str, str, str]:
    """
    Compute actionable entry / take-profit / stop-loss price levels.

    All three levels are for the recommended token (YES or NO):
      entry_range  — where to place a limit order (buy dip, don't chase)
      tp_range     — target exit when the market reprices toward true prob
      stop_loss    — cut the position to limit losses

    Returns strings formatted as cent values, e.g. "62c – 64c".
    """
    def _c(x: float) -> str:
        return f"{round(x * 100):.0f}c"

    # Buy the dip: 1–2c below current price up to the current ask
    dip = min(0.02, edge / 2)
    entry_lo = max(entry - dip, 0.01)

    # Exit when market has repriced 70–120% of the way to our true-prob estimate
    tp_lo = min(entry + edge * 0.70, 0.96)
    tp_hi = min(entry + edge * 1.20, 0.96)

    # Stop loss: 1.5× our edge against us, floored at 20% position loss
    sl = max(entry - edge * 1.5, entry * 0.80, 0.02)

    # &lt; is HTML-escaped "<" — required inside <code> tags in Telegram HTML mode
    return (
        f"{_c(entry_lo)} – {_c(entry)}",
        f"{_c(tp_lo)} – {_c(tp_hi)}",
        f"&lt; {_c(sl)}",
    )


def _timing_note(days: float | None) -> str:
    """Human-readable timing advice based on days to resolution."""
    if days is None:
        return "no end date — check market page"
    if days > 30:
        return f"{days:.0f}d out — plenty of time, set limit orders"
    if days > 14:
        return f"{days:.0f}d — use limit orders, check back daily"
    if days > 7:
        return f"{days:.0f}d — act soon, liquidity may thin"
    if days > 3:
        return f"{days:.0f}d — act this week, spreads widening"
    if days > 1:
        return f"{days:.0f}d — urgent, verify spread before entering"
    return "< 1d remaining — spreads likely wide, extreme caution"


def _format_pick(
    rank: int,
    idea: BetIdea,
    sm: SmartMoneySignal | None,
    kelly_fraction: float,
) -> list[str]:
    """
    Format one opportunity as Telegram HTML lines.

    Layout:
      Header  — conviction marker + BUY YES/NO decision
      Stats   — edge, EV, Kelly sizing
      Levels  — entry zone, take-profit, stop-loss
      Smart$  — smart-money confirmation or conflict (omitted if absent)
      Timing  — days-to-resolution with action advice
    """
    conviction = _conviction_label(idea, sm)
    marker = _CONVICTION_MARKER[conviction]
    adj_kelly = min(idea.kelly * kelly_fraction, 0.25) * 100
    vol24_str = f"${idea.volume_24h:,.0f}" if idea.volume_24h > 0 else "n/a"
    days = idea.days_to_resolution
    title_short = _esc(idea.title[:80]) + ("…" if len(idea.title) > 80 else "")
    entry_range, tp_range, sl_str = _price_levels(idea.entry_price, idea.edge)

    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(
        f"<b>{marker} {rank}.  BUY {idea.side} — {conviction} CONVICTION</b>"
    )
    lines.append(f'<a href="{idea.url}">{title_short}</a>')
    lines.append("")

    # ── Stats row ─────────────────────────────────────────────────────────────
    lines.append(
        f"  Edge <b>{idea.edge * 100:+.2f}pp</b>"
        f"  ·  EV {idea.ev * 100:+.2f}%"
        f"  ·  Kelly <b>{adj_kelly:.1f}%</b>"
    )
    lines.append(
        f"  Liq ${idea.liquidity:,.0f}"
        f"  ·  24h Vol {vol24_str}"
        f"  ·  HistN {idea.sample_size:,}"
    )
    lines.append("")

    # ── Price levels ──────────────────────────────────────────────────────────
    lines.append(f"  Buy zone:    <code>{entry_range}</code>   ← limit order here")
    lines.append(f"  Take profit: <code>{tp_range}</code>   ← exit as market corrects")
    lines.append(f"  Stop loss:   <code>{sl_str}</code>          ← cut if thesis breaks")

    # ── Smart money ───────────────────────────────────────────────────────────
    if sm is not None and sm.side == idea.side:
        pct = sm.confidence * 100
        lines.append("")
        lines.append(
            f"  Smart Money: <b>{sm.trader_count} traders agree</b>"
            f"  ·  ${sm.yes_value:,.0f} YES / ${sm.no_value:,.0f} NO"
            f"  ({pct:.0f}% {sm.side})"
        )
    elif sm is not None:
        lines.append("")
        lines.append(
            f"  Smart Money: CONFLICT — {sm.trader_count} traders favour {sm.side}"
        )

    # ── Timing ────────────────────────────────────────────────────────────────
    days_str = f"{days:.0f}d" if days is not None else "?"
    lines.append(f"  Timing: {_timing_note(days)}")

    lines.append(_SEP)
    lines.append("")
    return lines


# ── Main report builder ───────────────────────────────────────────────────────

def build_telegram_report(cfg: Config, top_n: int = 8) -> str:
    """
    Build an HTML-formatted alpha report combining:
      - Historical calibration (400M+ trade dataset)
      - Live Polymarket market prices
      - Smart-money positions from the Polymarket leaderboard

    Each opportunity is labelled BUY YES or BUY NO with a conviction tier
    (HIGH / MODERATE / LOW) so the reader can act immediately.

    Returns HTML string for Telegram (parse_mode='HTML').
    """
    output_dir = Path(cfg.output_dir)
    calibration = _load_calibration_map(output_dir)
    if not calibration:
        return (
            "<b>Alpha report unavailable</b>\n"
            "No calibration map found in output/.\n"
            "Run: <code>uv run pipeline.py</code> first."
        )

    markets = _fetch_live_markets(limit=300, max_pages=3)
    ideas: list[BetIdea] = []
    side_counter: Counter[str] = Counter()
    edges: list[float] = []

    for m in markets:
        idea = _score_market(m, calibration)
        if not idea:
            continue
        ideas.append(idea)
        side_counter[idea.side] += 1
        edges.append(idea.edge)

    ideas.sort(key=_composite_score, reverse=True)
    picks = ideas[:top_n]

    # Smart money — fetch once, index by slug
    sm_index = _fetch_sm_index()

    utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    avg_edge = (sum(edges) / len(edges) * 100) if edges else 0.0
    strong = sum(1 for e in edges if e >= 0.02)
    confirmed = sum(
        1 for p in picks
        if sm_index.get(_slug_from_url(p.url)) is not None
        and sm_index[_slug_from_url(p.url)].side == p.side
    )

    # ── Historical calibration bias ───────────────────────────────────────────
    longshot_bias_vals = [
        (cal["q_yes"] - cent / 100.0) * 100
        for cent, cal in calibration.items()
        if cent <= 10
    ]
    favorite_bias_vals = [
        (cal["q_yes"] - cent / 100.0) * 100
        for cent, cal in calibration.items()
        if cent >= 90
    ]

    lines: list[str] = []

    # ── Report header ─────────────────────────────────────────────────────────
    lines.append("<b>HedgePoly Alpha Report</b>")
    lines.append(f"<code>{utc_now}</code>")
    lines.append("")

    lines.append(
        f"<b>Scan:</b> {len(markets)} markets"
        f"  ·  {len(ideas)} with edge"
        f"  ·  {strong} strong (≥2pp)"
    )
    lines.append(
        f"<b>Avg edge:</b> {avg_edge:+.2f}pp"
        f"  ·  <b>Smart money:</b> {confirmed}/{len(picks)} confirmed"
    )
    lines.append(
        f"<b>Skew:</b> YES {side_counter['YES']} / NO {side_counter['NO']}"
    )

    if longshot_bias_vals and favorite_bias_vals:
        lb = sum(longshot_bias_vals) / len(longshot_bias_vals)
        fb = sum(favorite_bias_vals) / len(favorite_bias_vals)
        lb_note = "overpriced — fade them" if lb < -0.5 else "fair"
        lines.append(
            f"<b>Bias:</b> longshots {lb:+.2f}pp ({lb_note})"
            f"  ·  favorites {fb:+.2f}pp"
        )

    lines.append("")
    lines.append(
        "<i>★ HIGH = calibration + smart money agree"
        "  ·  ◆ MODERATE = one strong signal"
        "  ·  ▸ LOW = weak edge only</i>"
    )
    lines.append(_SEP)
    lines.append("")

    # ── Opportunities ─────────────────────────────────────────────────────────
    kelly_fraction = getattr(cfg, "kelly_fraction", 0.5)

    if not picks:
        lines.append("  No robust opportunities right now.")
        lines.append("  (Try later or lower MIN_LIQUIDITY threshold.)")
    else:
        for i, pick in enumerate(picks, 1):
            slug = _slug_from_url(pick.url)
            sm = sm_index.get(slug)
            lines.extend(_format_pick(i, pick, sm, kelly_fraction))

    lines.append(
        "<i>Live prices vs 400M-trade calibration + on-chain smart money. "
        "Not financial advice.</i>"
    )
    return "\n".join(lines)
