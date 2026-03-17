"""
kelly.py — Smart-money signal scoring + Kelly Criterion sizing.

Strategy
--------
We treat the concentration of top-PnL traders in a position as a
copy-trade alpha signal.  The stronger the consensus, the more we
shade our probability estimate above the market price, which then
generates a positive expected-value Kelly bet.

Formula
-------
  count_signal    = Σ RANK_DECAY^(rank-1) for each smart trader / Σ RANK_DECAY^(rank-1) for all traders
  size_signal     = min(mean_exposure / SIZE_ANCHOR, 1.0)
  signal_strength = COUNT_WEIGHT * count_signal + SIZE_WEIGHT * size_signal
  raw_edge        = min(signal_strength * SIGNAL_MULTIPLIER, MAX_EDGE)
  entry_discount  = max(0, 1 - (cur_price - wav_entry) / ENTRY_DISCOUNT_CAP)
  estimated_edge  = raw_edge * entry_discount
  p_est           = cur_price + estimated_edge   (our estimate of true prob)
  b               = (1 - cur_price) / cur_price  (payout odds per $1 bet)
  kelly_full      = max(0, (b * p_est - (1 - p_est)) / b)
  kelly_bet       = min(kelly_full * KELLY_FRACTION * bankroll, MAX_BET)

Accuracy improvements vs original:
  1. Rank-weighted count_signal  — rank 1 counts ~2.5x more than rank 25
  2. Entry-price discount        — edge shrinks if you're buying above smart money's entry
  3. MIN_DAYS_TO_RESOLVE filter  — skip markets resolving in < 1 day (near-certain outcomes)

Tune SIGNAL_MULTIPLIER and KELLY_FRACTION for your risk tolerance.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ── Configurable knobs ────────────────────────────────────────────────────────

# How many top traders must share a position to qualify
MIN_SIGNAL_TRADERS: int = 2

# Minimum current_value (USDC) each smart trader must have in the position
MIN_INDIVIDUAL_EXPOSURE: float = 10.0

# Edge added per smart trader as a fraction of total traders checked.
# E.g. 5/20 traders hold a position → signal = 0.25 → edge = 0.25 * 0.15 = 3.75%
SIGNAL_MULTIPLIER: float = 0.15

# Blended signal: 60% count-based, 40% size-based (USD conviction).
COUNT_WEIGHT: float = 0.60
SIZE_WEIGHT: float = 0.40

# Mean per-trader exposure that maps to size_signal = 1.0.
# $500 mean exposure → full size weight; below → linear scale.
SIZE_ANCHOR: float = 500.0

# Cap estimated edge at this level (prevents overconfidence)
MAX_EDGE: float = 0.20

# Minimum edge required to even consider a bet
MIN_NET_EDGE: float = 0.025   # 2.5 percentage points

# Kelly fraction: use quarter-Kelly for safety with a small bankroll
KELLY_FRACTION: float = 0.25

# Hard cap: never bet more than this fraction of bankroll on one position
MAX_BET_PCT: float = 0.10

# Skip bets smaller than this (not worth the transaction cost)
MIN_BET_USDC: float = 0.50

# ── Accuracy improvements ─────────────────────────────────────────────────────

# Rank weighting: top traders get more weight in count_signal.
# rank_weight(rank) = RANK_DECAY ^ (rank - 1)
# rank 1 → 1.0, rank 5 → ~0.66, rank 10 → ~0.39, rank 25 → ~0.08
RANK_DECAY: float = 0.90

# Entry-price discount: if the current price is already above smart money's
# average entry, we discount the edge linearly.
# 0% discount when cur_price == wav_entry
# 100% discount when cur_price >= wav_entry + ENTRY_DISCOUNT_CAP
ENTRY_DISCOUNT_CAP: float = 0.10   # 10pp overshoot → edge fully discounted

# Skip markets resolving in fewer than this many days (near-certain / dead alpha)
MIN_DAYS_TO_RESOLVE: float = 1.0


# ── Output dataclass ──────────────────────────────────────────────────────────

@dataclass
class Opportunity:
    condition_id: str
    title: str
    outcome: str
    slug: str                   # market slug for URL construction
    cur_price: float            # current market-implied probability
    n_smart_traders: int        # how many top traders hold this
    total_traders_checked: int
    smart_trader_names: list[str]
    signal_strength: float      # blended count+size signal
    count_signal: float         # n_smart / total_traders
    size_signal: float          # mean_exposure / SIZE_ANCHOR (capped at 1)
    estimated_edge: float       # probability edge above market price
    p_est: float                # p_market + edge
    kelly_full: float           # full Kelly fraction (of bankroll)
    kelly_bet: float            # recommended bet in USDC (fractioned + capped)
    weighted_avg_entry: float   # weighted avg entry price (by position value)
    total_exposure: float       # total USDC all smart traders have in this
    positions: list = field(default_factory=list, repr=False)

    @property
    def url(self) -> str:
        return f"https://polymarket.com/event/{self.slug}" if self.slug else "https://polymarket.com"


# ── Main scoring function ─────────────────────────────────────────────────────

def score_opportunities(
    positions,
    total_traders_checked: int,
    bankroll: float = 100.0,
    kelly_fraction: float = KELLY_FRACTION,
    min_signal_traders: int = MIN_SIGNAL_TRADERS,
    min_net_edge: float = MIN_NET_EDGE,
) -> list[Opportunity]:
    """
    Aggregate positions by market+outcome, apply smart-money scoring,
    and return Kelly-ranked opportunities.

    Parameters
    ----------
    positions : list[Position]
        All open positions across all traders (from positions.py).
    total_traders_checked : int
        Total number of traders fetched from leaderboard.
    bankroll : float
        Your total bankroll in USDC.
    kelly_fraction : float
        Fraction of full Kelly to bet (0.25 = quarter-Kelly).
    min_signal_traders : int
        Minimum distinct top traders required to generate a signal.
    min_net_edge : float
        Minimum estimated edge to include an opportunity.

    Returns
    -------
    list[Opportunity]
        Opportunities sorted by kelly_bet descending (best first).
    """
    # Group positions by (condition_id, outcome) — unique market side
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for p in positions:
        key = (p.condition_id, p.outcome)
        groups[key].append(p)

    opportunities: list[Opportunity] = []

    for (condition_id, outcome), pos_list in groups.items():
        # Filter: each trader must have meaningful exposure
        sig = [p for p in pos_list if p.current_value >= MIN_INDIVIDUAL_EXPOSURE]

        if len(sig) < min_signal_traders:
            continue

        # Deduplicate: one entry per trader (take the largest position if duplicated)
        seen_wallets: dict[str, object] = {}
        for p in sig:
            if p.proxy_wallet not in seen_wallets or p.current_value > seen_wallets[p.proxy_wallet].current_value:
                seen_wallets[p.proxy_wallet] = p
        sig = list(seen_wallets.values())

        if len(sig) < min_signal_traders:
            continue

        # Market data from the position with the largest exposure
        sig_sorted = sorted(sig, key=lambda p: p.current_value, reverse=True)
        representative = sig_sorted[0]
        cur_price = representative.cur_price
        title = representative.title

        # Sanity check price
        if cur_price <= 0.01 or cur_price >= 0.99:
            continue

        # Improvement 3: skip markets resolving too soon (< MIN_DAYS_TO_RESOLVE)
        end_date = representative.end_date
        if end_date:
            try:
                dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                days_left = (dt - datetime.now(timezone.utc)).total_seconds() / 86400
                if days_left < MIN_DAYS_TO_RESOLVE:
                    continue
            except Exception:
                pass  # unparseable end_date — allow through

        # Weighted average entry price (weighted by current position value)
        total_val = sum(p.current_value for p in sig)
        if total_val <= 0:
            continue
        wav_entry = sum(p.avg_price * p.current_value for p in sig) / total_val

        # ── Signal & edge ────────────────────────────────────────────────────
        n = len(sig)
        mean_exposure = total_val / n

        # Improvement 1: rank-weighted count signal
        # Higher-ranked traders (lower rank number) contribute more weight
        total_possible_weight = sum(
            RANK_DECAY ** (r - 1) for r in range(1, total_traders_checked + 1)
        )
        smart_weight = sum(RANK_DECAY ** max((p.trader_rank or 1) - 1, 0) for p in sig)
        count_signal = smart_weight / max(total_possible_weight, 1e-9)

        size_signal = min(mean_exposure / SIZE_ANCHOR, 1.0)
        signal_strength = COUNT_WEIGHT * count_signal + SIZE_WEIGHT * size_signal
        raw_edge = min(signal_strength * SIGNAL_MULTIPLIER, MAX_EDGE)

        # Improvement 2: discount edge if current price already above smart money entry
        overshoot = max(0.0, cur_price - wav_entry)
        entry_discount = max(0.0, 1.0 - overshoot / ENTRY_DISCOUNT_CAP)
        estimated_edge = raw_edge * entry_discount

        if estimated_edge < min_net_edge:
            continue

        # ── Kelly Criterion ──────────────────────────────────────────────────
        p_est = min(cur_price + estimated_edge, 0.99)
        q_est = 1.0 - p_est
        b = (1.0 - cur_price) / cur_price   # net odds: win this much per $1 risked

        if b <= 0:
            continue

        kelly_full = max(0.0, (b * p_est - q_est) / b)

        if kelly_full <= 0:
            continue

        kelly_bet = min(
            kelly_full * kelly_fraction * bankroll,
            MAX_BET_PCT * bankroll,
        )

        if kelly_bet < MIN_BET_USDC:
            continue

        slug = representative.slug or ""
        opportunities.append(Opportunity(
            condition_id=condition_id,
            title=title,
            outcome=outcome,
            slug=slug,
            cur_price=cur_price,
            n_smart_traders=n,
            total_traders_checked=total_traders_checked,
            smart_trader_names=[p.username for p in sig_sorted],
            signal_strength=signal_strength,
            count_signal=count_signal,
            size_signal=size_signal,
            estimated_edge=estimated_edge,
            p_est=p_est,
            kelly_full=kelly_full,
            kelly_bet=kelly_bet,
            weighted_avg_entry=wav_entry,
            total_exposure=total_val,
            positions=sig_sorted,
        ))

    # Best opportunities first (largest Kelly bet)
    opportunities.sort(key=lambda o: o.kelly_bet, reverse=True)
    return opportunities
