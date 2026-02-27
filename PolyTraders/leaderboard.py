"""
leaderboard.py — Fetch top traders from Polymarket leaderboard.

API: GET https://data-api.polymarket.com/v1/leaderboard
Parameters:
  timePeriod  : DAY | WEEK | MONTH | ALL
  orderBy     : PNL | VOL
  limit       : 1-50 (max per request)
  offset      : 0-1000
  category    : OVERALL | POLITICS | SPORTS | CRYPTO | ...
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"

HEADERS = {
    "User-Agent": "PolyTraders/1.0",
    "Accept": "application/json",
}


@dataclass
class Trader:
    rank: int
    proxy_wallet: str
    username: str
    pnl: float       # profit/loss in USDC
    vol: float       # trading volume in USDC
    verified: bool
    x_username: str = ""


def fetch_top_traders(
    time_period: str = "WEEK",
    order_by: str = "PNL",
    limit: int = 30,
    category: str = "OVERALL",
) -> list[Trader]:
    """
    Fetch the top `limit` traders from the Polymarket leaderboard.

    Parameters
    ----------
    time_period : str
        DAY, WEEK, MONTH, or ALL
    order_by : str
        PNL (profit/loss) or VOL (volume)
    limit : int
        Number of traders to fetch (max 50 per request).
        If > 50, multiple requests are made.
    category : str
        Market category filter (default OVERALL).

    Returns
    -------
    list[Trader]
        Traders sorted by rank ascending (best first).
    """
    traders: list[Trader] = []
    per_page = 50
    offset = 0

    while len(traders) < limit:
        batch = min(per_page, limit - len(traders))
        params = {
            "timePeriod": time_period,
            "orderBy": order_by,
            "limit": batch,
            "offset": offset,
            "category": category,
        }
        resp = requests.get(LEADERBOARD_URL, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            break

        for item in data:
            traders.append(Trader(
                rank=item.get("rank", len(traders) + 1),
                proxy_wallet=item.get("proxyWallet", ""),
                username=item.get("userName") or (item.get("proxyWallet", "")[:8] + "..."),
                pnl=float(item.get("pnl", 0)),
                vol=float(item.get("vol", 0)),
                verified=bool(item.get("verifiedBadge", False)),
                x_username=item.get("xUsername", "") or "",
            ))

        if len(data) < batch:
            break  # No more pages

        offset += batch

    return traders[:limit]
