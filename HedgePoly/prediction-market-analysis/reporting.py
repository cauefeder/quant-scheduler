"""Build Telegram-ready alpha reports with live Polymarket comparison."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from utils import Config

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

# Minimum filters to avoid noisy markets
MIN_LIQUIDITY = 1_000        # $1k minimum liquidity
MIN_VOLUME = 5_000           # $5k minimum all-time volume
MIN_SAMPLE_SIZE = 500        # historical trades needed for calibration edge
MIN_EDGE_PP = 0.5            # minimum 0.5pp edge to show an opportunity


@dataclass
class BetIdea:
    title: str
    url: str
    side: str
    entry_price: float
    edge: float
    ev: float
    kelly: float
    liquidity: float
    volume: float
    volume_24h: float
    sample_size: int
    days_to_resolution: float | None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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

    # Fallback to pipeline output if CSV is missing.
    pipeline_path = output_dir / "pipeline_results.json"
    if pipeline_path.exists():
        with open(pipeline_path, "r", encoding="utf-8") as f:
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
    """Fetch active markets sorted by volume."""
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
    """Parse days until market resolution from various date fields."""
    now = datetime.now(timezone.utc)
    for key in ("endDate", "end_date_iso", "endDateIso", "end_date"):
        val = market.get(key)
        if val:
            try:
                dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                days = (dt - now).total_seconds() / 86400
                return max(days, 0.0)
            except (ValueError, TypeError):
                continue
    return None


def _score_market(market: dict[str, Any], calibration: dict[int, dict[str, float]]) -> BetIdea | None:
    """Score a single market against historical calibration. Returns None if no edge."""
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
        side = "YES"
        entry = p_yes
        q_side = q_yes
        edge = edge_yes
    else:
        side = "NO"
        entry = p_no
        q_side = 1.0 - q_yes
        edge = edge_no

    if edge * 100 < MIN_EDGE_PP:
        return None

    if entry <= 0.01 or entry >= 0.99:
        return None

    b = (1.0 - entry) / entry
    ev = q_side * b - (1.0 - q_side)
    kelly = max(0.0, (b * q_side - (1.0 - q_side)) / b) if b > 0 else 0.0

    slug = market.get("slug") or ""
    url = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"
    days_left = _parse_days_to_resolution(market)

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
        days_to_resolution=days_left,
    )


def _composite_score(idea: BetIdea) -> float:
    """Composite ranking: balances edge, liquidity, volume, and sample credibility."""
    liq_factor = max(idea.liquidity, 1.0) ** 0.20
    vol_factor = max(idea.volume_24h + 1.0, 1.0) ** 0.10
    sample_factor = max(idea.sample_size, 1) ** 0.10
    return idea.edge * liq_factor * vol_factor * sample_factor


def _esc(text: str) -> str:
    """Escape HTML special characters for Telegram HTML mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_telegram_report(cfg: Config, top_n: int = 8) -> str:
    """
    Build an HTML-formatted alpha report combining:
      - Historical calibration data (400M+ trade dataset)
      - Live Polymarket market prices
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

    utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    avg_edge = (sum(edges) / len(edges) * 100) if edges else 0.0
    strong = sum(1 for e in edges if e >= 0.02)

    lines: list[str] = []
    lines.append("<b>HedgePoly Alpha Report</b>")
    lines.append(f"<code>{utc_now}</code>")
    lines.append("")

    lines.append("<b>Live Market Scan</b>")
    lines.append(f"  Markets scanned: {len(markets)}")
    lines.append(f"  Positive-edge candidates: {len(ideas)}")
    lines.append(f"  Strong edge (>=2pp): {strong}")
    lines.append(f"  Avg edge: {avg_edge:+.2f}pp")
    lines.append(f"  Side skew: YES {side_counter['YES']} | NO {side_counter['NO']}")
    lines.append("")

    # Historical calibration context
    longshot_bias = []
    favorite_bias = []
    for cent, data in calibration.items():
        mispricing_pp = (data["q_yes"] - cent / 100.0) * 100.0
        if cent <= 10:
            longshot_bias.append(mispricing_pp)
        if cent >= 90:
            favorite_bias.append(mispricing_pp)
    if longshot_bias and favorite_bias:
        lb = sum(longshot_bias) / len(longshot_bias)
        fb = sum(favorite_bias) / len(favorite_bias)
        lines.append("<b>Historical Calibration Bias</b>")
        lines.append(f"  Longshots (10c and below): {lb:+.2f}pp (overpriced = short bias)")
        lines.append(f"  Favorites (90c and above): {fb:+.2f}pp (underpriced = long bias)")
        lines.append("")

    lines.append(f"<b>Top {len(picks)} Opportunities</b>")
    lines.append("")

    kelly_fraction = getattr(cfg, "kelly_fraction", 0.5)

    if not picks:
        lines.append("  No robust opportunities found right now.")
        lines.append("  (Try again later or lower MIN_LIQUIDITY threshold)")
    else:
        for i, p in enumerate(picks, 1):
            days_str = f"{p.days_to_resolution:.0f}d" if p.days_to_resolution is not None else "?"
            vol24_str = f"${p.volume_24h:,.0f}" if p.volume_24h > 0 else "n/a"
            title_short = _esc(p.title[:80]) + ("..." if len(p.title) > 80 else "")
            # Apply configured kelly_fraction and cap for display
            adj_kelly = min(p.kelly * kelly_fraction, 0.25)

            lines.append(
                f"<b>{i}. {p.side} @ {p.entry_price*100:.1f}c</b>"
                f" | Edge: <b>{p.edge*100:+.2f}pp</b>"
                f" | EV: {p.ev*100:+.2f}%"
            )
            lines.append(
                f"   Adj.Kelly: {adj_kelly*100:.2f}%"
                f" | Liq: ${p.liquidity:,.0f}"
                f" | Vol24h: {vol24_str}"
                f" | Resolves: {days_str}"
            )
            lines.append(f"   HistN={p.sample_size:,}")
            lines.append(f'   <a href="{p.url}">{title_short}</a>')
            lines.append("")

    lines.append("<i>Method: live prices vs 400M-trade historical calibration by price bucket.</i>")
    lines.append("<i>Not financial advice. Research tool only.</i>")
    return "\n".join(lines)
