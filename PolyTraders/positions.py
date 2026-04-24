"""
positions.py — Fetch open positions for a list of Polymarket traders.

API: GET https://data-api.polymarket.com/positions
Required: user (proxy wallet address)
Optional: limit, offset, sortBy, sizeThreshold, ...
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import requests

POSITIONS_URL = "https://data-api.polymarket.com/positions"

HEADERS = {
    "User-Agent": "PolyTraders/1.0",
    "Accept": "application/json",
}

# Skip near-resolved markets (price ≤ 1c or ≥ 99c)
MIN_PRICE = 0.02
MAX_PRICE = 0.98

# Minimum current value to keep a position (USDC)
MIN_POSITION_VALUE = 10.0


@dataclass
class Position:
    proxy_wallet: str
    username: str
    trader_rank: int
    trader_pnl: float

    condition_id: str
    title: str
    outcome: str        # YES or NO
    outcome_index: int

    cur_price: float    # current market price for this outcome
    avg_price: float    # trader's average entry price
    size: float         # shares held
    current_value: float  # current_value = size * cur_price (approx)
    cash_pnl: float     # unrealized P&L in USDC
    percent_pnl: float  # unrealized P&L %

    end_date: str = ""
    slug: str = ""


def fetch_positions(
    proxy_wallet: str,
    limit: int = 50,
) -> list[Position]:
    """Fetch open positions for a single trader wallet address."""
    params = {
        "user": proxy_wallet,
        "limit": min(limit, 500),
        "offset": 0,
        "sizeThreshold": 0.01,
        "sortBy": "CURRENT",
        "sortDirection": "DESC",
    }
    try:
        resp = requests.get(POSITIONS_URL, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        # Network error or 4xx/5xx — skip this trader silently
        print(f"  [WARN] positions fetch failed for {proxy_wallet[:10]}...: {exc}")
        return []

    positions: list[Position] = []
    for item in data:
        cur_price = float(item.get("curPrice", 0) or 0)
        current_val = float(item.get("currentValue", 0) or 0)

        # Skip near-resolved markets and tiny positions
        if cur_price < MIN_PRICE or cur_price > MAX_PRICE:
            continue
        if current_val < MIN_POSITION_VALUE:
            continue

        avg_price = float(item.get("avgPrice", cur_price) or cur_price)

        positions.append(Position(
            proxy_wallet=proxy_wallet,
            username="",         # filled in by caller
            trader_rank=0,       # filled in by caller
            trader_pnl=0.0,      # filled in by caller
            condition_id=item.get("conditionId", ""),
            title=item.get("title", "Unknown market"),
            outcome=item.get("outcome", "YES"),
            outcome_index=int(item.get("outcomeIndex", 0) or 0),
            cur_price=cur_price,
            avg_price=avg_price,
            size=float(item.get("size", 0) or 0),
            current_value=current_val,
            cash_pnl=float(item.get("cashPnl", 0) or 0),
            percent_pnl=float(item.get("percentPnl", 0) or 0),
            end_date=item.get("endDate", "") or "",
            slug=item.get("slug", "") or "",
        ))

    return positions


def fetch_all_positions(
    traders,
    max_traders: int = 25,
    max_workers: int = 5,
) -> list[Position]:
    """
    Fetch open positions for up to max_traders concurrently.

    Parameters
    ----------
    traders : list[Trader]
        Trader objects from leaderboard.py
    max_traders : int
        Maximum number of traders to query.
    max_workers : int
        Thread pool size (keep ≤ 5 to stay within API rate limits).

    Returns
    -------
    list[Position]
        All positions across all traders, with username/rank/pnl filled in,
        sorted by trader rank (ascending).
    """
    cohort = [t for t in traders[:max_traders] if t.proxy_wallet]

    def _fetch_one(trader):
        positions = fetch_positions(trader.proxy_wallet)
        for p in positions:
            p.username = trader.username
            p.trader_rank = trader.rank
            p.trader_pnl = trader.pnl
        return trader, positions

    results: dict[str, list[Position]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in cohort}
        for future in as_completed(futures):
            trader, positions = future.result()
            results[trader.proxy_wallet] = positions
            print(
                f"  [{trader.rank}] {trader.username[:20]:<20}"
                f"  pnl=${trader.pnl:,.0f}  positions={len(positions)}"
            )

    # Reconstruct in rank order so logging/report is deterministic
    all_positions: list[Position] = []
    for trader in cohort:
        all_positions.extend(results.get(trader.proxy_wallet, []))

    return all_positions
