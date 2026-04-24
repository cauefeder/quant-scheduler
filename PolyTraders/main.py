"""
PolyTraders — Polymarket smart-money copy-trading signal generator.

Strategy
--------
1. Fetch top N traders by 7-day PnL from the Polymarket leaderboard.
2. Pull their current open positions.
3. Find markets where ≥2 top traders share a position (consensus signal).
4. Apply Kelly Criterion sizing for a small bankroll.
5. Send an HTML-formatted Telegram report with the best opportunities.

Usage
-----
  # Run with uv (auto-installs dependencies):
  uv run --no-project --python 3.11 --with requests,python-dotenv python main.py

  # Preview report in console (no Telegram send):
  uv run --no-project --python 3.11 --with requests,python-dotenv python main.py --preview

  # Dry-run (no API calls):
  uv run --no-project --python 3.11 --with requests,python-dotenv python main.py --test

Environment variables (.env)
----------------------------
  TELEGRAM_BOT_TOKEN      Telegram bot token
  TELEGRAM_CHAT_ID        Telegram chat/group ID
  POLYTRADERS_BANKROLL    Bankroll in USDC (default 100)
  POLYTRADERS_TOP_N       Number of top traders to fetch (default 25)
  POLYTRADERS_TIME_PERIOD Leaderboard period: DAY|WEEK|MONTH|ALL (default WEEK)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Windows UTF-8 fix (must be before any print statements) ──────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Load .env ─────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv not available; rely on env vars set externally

TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BANKROLL_USDC = float(os.getenv("POLYTRADERS_BANKROLL", "100"))
TOP_N_TRADERS = int(os.getenv("POLYTRADERS_TOP_N", "25"))
TIME_PERIOD   = os.getenv("POLYTRADERS_TIME_PERIOD", "WEEK")
MAX_OPPS      = 12  # opportunities shown in report


# ── Telegram (stdlib only — no external deps) ─────────────────────────────────

def _esc(s: str) -> str:
    """Escape text for Telegram HTML parse_mode."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tg_send(text: str) -> bool:
    """Send HTML message to Telegram (chunks at 4 000 chars)."""
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[WARN] Telegram credentials not set — skipping send.")
        return False

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    # Split at newlines to avoid cutting mid-tag
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > 4000:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)

    ok = True
    for chunk in chunks:
        payload = json.dumps({
            "chat_id": TG_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    print(f"[WARN] Telegram returned HTTP {resp.status}")
                    ok = False
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            print(f"[ERROR] Telegram HTTP {exc.code}: {body[:500]}")
            ok = False
        except Exception as exc:
            print(f"[ERROR] Telegram send failed: {exc}")
            ok = False

    return ok


# ── Report formatting helpers ─────────────────────────────────────────────────

_SEP = "─" * 34


def _price_levels_opp(cur_price: float, edge: float) -> tuple[str, str, str]:
    """
    Return (entry_range, take_profit_range, stop_loss) for one opportunity.

    entry_range  — where to set a limit order (buy dip up to current price)
    tp_range     — target exit when market reprices toward estimated true prob
    stop_loss    — cut the position if the market moves against you
    """
    def _c(x: float) -> str:
        return f"{round(x * 100):.0f}c"

    dip = min(0.02, edge / 2)
    entry_lo = max(cur_price - dip, 0.01)

    tp_lo = min(cur_price + edge * 0.70, 0.96)
    tp_hi = min(cur_price + edge * 1.20, 0.96)

    sl = max(cur_price - edge * 1.5, cur_price * 0.80, 0.02)

    # &lt; is HTML-escaped "<" — required inside <code> tags in Telegram HTML mode
    return (
        f"{_c(entry_lo)} – {_c(cur_price)}",
        f"{_c(tp_lo)} – {_c(tp_hi)}",
        f"&lt; {_c(sl)}",
    )


def _entry_timing(cur_price: float, avg_entry: float, edge: float) -> str:
    """Comment on whether you're entering before, with, or after smart money."""
    delta = cur_price - avg_entry
    if delta < -0.02:
        return f"price {abs(delta)*100:.1f}pp BELOW smart money entry — buy carefully"
    if delta <= 0.01:
        return "at smart money entry — good timing"
    if delta <= 0.03:
        return f"~{delta*100:.1f}pp above their entry — still reasonable"
    return f"{delta*100:.1f}pp above their entry — consider waiting for dip"


# ── Report formatter ──────────────────────────────────────────────────────────

def _format_report(
    traders,
    opportunities,
    n_positions: int,
    time_period: str,
    bankroll: float,
) -> str:
    now    = datetime.now(timezone.utc)
    ts_utc = now.strftime("%Y-%m-%d %H:%M UTC")
    sk_h   = (now.hour - 6) % 24
    ts_sk  = f"{sk_h:02d}:{now.minute:02d} SK"

    # ── Header ────────────────────────────────────────────────────────────────
    lines = [
        "<b>PolyTraders  |  Smart Money Signals</b>",
        f"<code>{ts_utc}  ({ts_sk})</code>",
        "",
        f"<b>Leaderboard:</b> {len(traders)} traders  ·  {time_period.title()} PnL",
        f"<b>Positions scanned:</b> {n_positions}  ·  <b>Bankroll:</b> ${bankroll:.0f} USDC",
    ]

    if not opportunities:
        lines += [
            "",
            "<i>No consensus opportunities this run.</i>",
            "<i>(Need ≥2 top traders in the same market/outcome.)</i>",
            "",
            "<i>Signal = smart-money consensus. Not financial advice.</i>",
        ]
        return "\n".join(lines)

    n_shown = min(len(opportunities), MAX_OPPS)
    lines += [
        f"<b>Signals found:</b> {len(opportunities)}  ·  showing top {n_shown}",
        "",
        _SEP,
        "",
    ]

    # ── Opportunities ─────────────────────────────────────────────────────────
    for i, opp in enumerate(opportunities[:MAX_OPPS], 1):
        price_c   = opp.cur_price * 100
        edge_pp   = opp.estimated_edge * 100
        entry_c   = opp.weighted_avg_entry * 100
        mean_exp  = opp.total_exposure / max(opp.n_smart_traders, 1)

        entry_range, tp_range, sl_str = _price_levels_opp(opp.cur_price, opp.estimated_edge)
        timing_note = _entry_timing(opp.cur_price, opp.weighted_avg_entry, opp.estimated_edge)

        holders = ", ".join(_esc(n) for n in opp.smart_trader_names[:5])
        if len(opp.smart_trader_names) > 5:
            holders += f" +{len(opp.smart_trader_names)-5} more"

        title_esc = _esc(opp.title[:75]) + ("…" if len(opp.title) > 75 else "")

        # Rank marker: ★ for top 3, ◆ for rest
        marker = "★" if i <= 3 else "◆"

        lines += [
            # Header
            f"<b>{marker} {i}.  BUY {opp.outcome}</b>  ·  {opp.n_smart_traders}/{opp.total_traders_checked} smart traders",
            f'<a href="{opp.url}">{title_esc}</a>',
            "",
            # Stats
            f"  Price <code>{price_c:.1f}c</code>"
            f"  ·  Edge <b>+{edge_pp:.1f}pp</b>"
            f"  ·  Kelly bet <b>${opp.kelly_bet:.2f}</b>"
            f"  (full {opp.kelly_full*100:.1f}%)",
            f"  Signal: count <code>{opp.count_signal*100:.0f}%</code>"
            f" · size <code>{opp.size_signal*100:.0f}%</code>"
            f"  ·  avg exposure <code>${mean_exp:,.0f}</code>",
            f"  Total exposure: <code>${opp.total_exposure:,.0f}</code>"
            f"  ·  smart entry avg: <code>{entry_c:.1f}c</code>",
            "",
            # Price levels
            f"  Buy zone:    <code>{entry_range}</code>   ← {timing_note}",
            f"  Take profit: <code>{tp_range}</code>   ← exit as market corrects",
            f"  Stop loss:   <code>{sl_str}</code>          ← cut if market disagrees",
            "",
            f"  Holders: {holders}",
            _SEP,
            "",
        ]

    lines += [
        "<i>Signal = smart-money consensus. Kelly = quarter-Kelly sizing.</i>",
        "<i>Not financial advice. Bet responsibly.</i>",
    ]

    return "\n".join(lines)


def _format_report_plain(report_html: str) -> str:
    """Strip HTML tags for console preview."""
    import re
    text = re.sub(r"<[^>]+>", "", report_html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    preview = "--preview" in args
    test    = "--test" in args

    print("=" * 60)
    print(" PolyTraders — Smart Money Signal Generator")
    print("=" * 60)
    print(f" Bankroll  : ${BANKROLL_USDC:.0f} USDC")
    print(f" Top N     : {TOP_N_TRADERS} traders")
    print(f" Period    : {TIME_PERIOD}")
    print(f" Mode      : {'TEST (dry-run)' if test else 'PREVIEW' if preview else 'LIVE'}")
    print("=" * 60)

    if test:
        print("[TEST] Dry-run mode — no API calls made.")
        print("[TEST] Would send report to Telegram when done.")
        return

    # ── Step 1: Leaderboard ───────────────────────────────────────────────────
    from leaderboard import fetch_top_traders
    print(f"\n[1/4] Fetching top {TOP_N_TRADERS} traders ({TIME_PERIOD} PnL)...")
    try:
        traders = fetch_top_traders(time_period=TIME_PERIOD, limit=TOP_N_TRADERS)
    except Exception as exc:
        print(f"[ERROR] Leaderboard fetch failed: {exc}")
        return

    if not traders:
        print("[ERROR] No traders returned from leaderboard — aborting.")
        return

    print(f"      Found {len(traders)} traders")
    print(f"      Top PnL: {traders[0].username} = ${traders[0].pnl:,.0f}")

    # ── Step 2: Positions ─────────────────────────────────────────────────────
    from positions import fetch_all_positions
    print(f"\n[2/4] Fetching open positions (up to {TOP_N_TRADERS} traders)...")
    all_positions = fetch_all_positions(traders, max_traders=TOP_N_TRADERS)
    print(f"      {len(all_positions)} qualifying positions collected")

    if not all_positions:
        print("[WARN] No positions found — nothing to report.")
        traders_for_report = traders
        opportunities = []
    else:
        # ── Step 3: Kelly scoring ─────────────────────────────────────────────
        from kelly import score_opportunities
        print(f"\n[3/4] Scoring opportunities (bankroll=${BANKROLL_USDC:.0f})...")
        opportunities = score_opportunities(
            all_positions,
            total_traders_checked=len(traders),
            bankroll=BANKROLL_USDC,
        )
        print(f"      {len(opportunities)} opportunities qualify")
        traders_for_report = traders

    # ── Step 4: Report ────────────────────────────────────────────────────────
    print("\n[4/4] Formatting report...")
    report_html = _format_report(
        traders_for_report,
        opportunities if all_positions else [],
        n_positions=len(all_positions),
        time_period=TIME_PERIOD,
        bankroll=BANKROLL_USDC,
    )

    # Always print to console
    print("\n" + "=" * 60)
    print(_format_report_plain(report_html))
    print("=" * 60 + "\n")

    if preview:
        print("[PREVIEW] Not sending to Telegram (--preview flag).")
        return

    print("Sending to Telegram...")
    ok = _tg_send(report_html)
    if ok:
        print("[OK] Report sent successfully.")
    else:
        print("[WARN] Telegram send failed (check token/chat_id in .env).")


if __name__ == "__main__":
    main()
