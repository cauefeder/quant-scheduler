"""
smart_money.py — Smart Money Scanner for Polymarket.

Tracks the top Polymarket traders (by weekly PnL on the public leaderboard)
and aggregates their open positions into per-market directional signals.

Used by reporting.py to cross-reference with calibration-based BetIdeas,
surfacing markets where both the crowd wisdom (calibration) and informed
capital (smart money) agree on the same side.

Public API
----------
build_smart_money_signals(...)  → list[SmartMoneySignal]
aggregate_signals(...)          → list[SmartMoneySignal]   (pure, testable)
SmartMoneySignal                → dataclass
TraderPosition                  → dataclass
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import httpx

_LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
_POSITIONS_URL = "https://data-api.polymarket.com/positions"

# Module-level defaults (can be overridden per call)
_TOP_N_TRADERS = 25
_MIN_POSITION_VALUE = 200.0  # USD current market value
_MIN_TRADER_AGREEMENT = 2    # at least N distinct top-traders on the same market
_MAX_WORKERS = 5             # concurrent position fetches (stay below rate limit)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class TraderPosition:
    """One trader's open position in a single Polymarket market."""
    market_slug: str
    question: str
    outcome: str          # "Yes" or "No" (raw string from API)
    avg_price: float      # 0.0–1.0 entry price
    current_value: float  # USD value at current price


@dataclass
class SmartMoneySignal:
    """
    Aggregated smart money signal for one market.

    Produced by combining positions from multiple top-ranked traders.
    ``side`` is the majority direction by USD value.
    """
    market_slug: str
    question: str
    side: str          # "YES" or "NO"
    yes_value: float   # total USD held in YES positions
    no_value: float    # total USD held in NO positions
    trader_count: int  # distinct top-traders with any position here
    url: str

    @property
    def total_value(self) -> float:
        return self.yes_value + self.no_value

    @property
    def confidence(self) -> float:
        """
        Fraction of total smart-money value on the majority side.

        0.5 = perfectly split, 1.0 = unanimous.
        Returns 0.5 when there is no value (degenerate case).
        """
        total = self.yes_value + self.no_value
        if total <= 0:
            return 0.5
        majority_value = self.yes_value if self.side == "YES" else self.no_value
        return majority_value / total

    @property
    def is_yes(self) -> bool:
        return self.side == "YES"


# ── Network helpers (isolated for easy mocking in tests) ─────────────────────

def _fetch_leaderboard(
    window: str = "week",
    limit: int = 50,
    timeout: float = 15.0,
) -> list[str]:
    """
    Fetch top-trader proxy wallet addresses from the Polymarket leaderboard.

    Returns a list of address strings.  On any network or parse error, returns
    an empty list so callers can degrade gracefully.
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                _LEADERBOARD_URL,
                params={
                    "timePeriod": window.upper(),  # API accepts "WEEK", "ALL" etc.
                    "orderBy": "PNL",
                    "limit": limit,
                    "category": "OVERALL",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    addresses: list[str] = []
    for item in data:
        addr = item.get("proxyWallet") or item.get("address") or ""
        if addr:
            addresses.append(str(addr))
    return addresses


def _fetch_trader_positions(
    address: str,
    min_value: float = _MIN_POSITION_VALUE,
    timeout: float = 10.0,
) -> list[TraderPosition]:
    """
    Fetch open positions for a single trader.

    Filters out positions whose current USD value is below *min_value*.
    Returns an empty list on any error.
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                _POSITIONS_URL,
                params={"user": address, "sizeThreshold": 0.01},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    out: list[TraderPosition] = []
    for item in data:
        slug = item.get("slug") or item.get("marketSlug") or ""
        question = item.get("title") or item.get("question") or ""
        outcome = str(item.get("outcome") or "Yes")
        try:
            avg_price = float(item.get("avgPrice") or 0.0)
            current_value = float(item.get("currentValue") or 0.0)
        except (TypeError, ValueError):
            continue
        if not slug or current_value < min_value:
            continue
        out.append(TraderPosition(
            market_slug=slug,
            question=question,
            outcome=outcome,
            avg_price=avg_price,
            current_value=current_value,
        ))
    return out


# ── Core aggregation (pure function — no I/O) ─────────────────────────────────

def aggregate_signals(
    positions_by_trader: list[list[TraderPosition]],
    min_traders: int = _MIN_TRADER_AGREEMENT,
) -> list[SmartMoneySignal]:
    """
    Aggregate positions from multiple traders into per-market signals.

    Parameters
    ----------
    positions_by_trader:
        One inner list per trader, each containing that trader's positions.
    min_traders:
        Minimum number of distinct top-traders required for a market signal
        to be emitted.  Filters out thin, single-trader signals.

    Returns
    -------
    list[SmartMoneySignal] sorted by total_value descending.
    """
    yes_val: dict[str, float] = defaultdict(float)
    no_val: dict[str, float] = defaultdict(float)
    questions: dict[str, str] = {}
    trader_sets: dict[str, set[int]] = defaultdict(set)

    for idx, positions in enumerate(positions_by_trader):
        for pos in positions:
            slug = pos.market_slug
            questions[slug] = pos.question
            trader_sets[slug].add(idx)
            if pos.outcome.lower().startswith("y"):
                yes_val[slug] += pos.current_value
            else:
                no_val[slug] += pos.current_value

    signals: list[SmartMoneySignal] = []
    for slug, question in questions.items():
        n_traders = len(trader_sets[slug])
        if n_traders < min_traders:
            continue
        yes = yes_val[slug]
        no = no_val[slug]
        if yes + no <= 0:
            continue
        side = "YES" if yes >= no else "NO"
        signals.append(SmartMoneySignal(
            market_slug=slug,
            question=question,
            side=side,
            yes_value=yes,
            no_value=no,
            trader_count=n_traders,
            url=f"https://polymarket.com/event/{slug}",
        ))

    signals.sort(key=lambda s: s.total_value, reverse=True)
    return signals


# ── Public entry point ────────────────────────────────────────────────────────

def build_smart_money_signals(
    top_n_traders: int = _TOP_N_TRADERS,
    min_position_value: float = _MIN_POSITION_VALUE,
    min_traders: int = _MIN_TRADER_AGREEMENT,
    leaderboard_window: str = "week",
) -> list[SmartMoneySignal]:
    """
    Full smart-money pipeline: leaderboard → positions → aggregate.

    1. Fetch top *top_n_traders* addresses from the Polymarket leaderboard.
    2. For each address, fetch open positions (filtered by *min_position_value*).
    3. Aggregate into per-market signals requiring at least *min_traders*.

    Returns an empty list on network failure (caller should degrade gracefully).
    """
    addresses = _fetch_leaderboard(window=leaderboard_window, limit=top_n_traders * 2)
    addresses = addresses[:top_n_traders]
    if not addresses:
        return []

    # Fetch positions concurrently — ~5x faster than sequential
    positions_by_trader: list[list[TraderPosition]] = [[] for _ in addresses]
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_trader_positions, addr, min_value=min_position_value): idx
            for idx, addr in enumerate(addresses)
        }
        for future in as_completed(futures):
            positions_by_trader[futures[future]] = future.result()

    return aggregate_signals(positions_by_trader, min_traders=min_traders)
