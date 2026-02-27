"""
Polymarket Prediction Market Scraper with Kelly Criterion Position Sizing
=========================================================================
- Scrapes Polymarket's free public API (CLOB API + Gamma API)
- Calculates Kelly Criterion bet sizes
- Focuses on short-term markets (resolving within 30 days)
- Includes risk management with fractional Kelly
- Works on Windows with Python 3.10+

No API key required — Polymarket's public endpoints are free.

Usage:
    python polymarket_scraper.py
    python polymarket_scraper.py --bankroll 1000 --kelly-fraction 0.25 --max-days 14
"""

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

try:
    import requests
except ImportError:
    print("Missing dependency. Run: pip install requests")
    sys.exit(1)

# ─── Configuration ────────────────────────────────────────────────────────────

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

HEADERS = {
    "User-Agent": "PolymarketKellyBot/1.0",
    "Accept": "application/json",
}

# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class MarketOutcome:
    """A single outcome (YES/NO) in a market."""
    token_id: str
    outcome: str          # "Yes" or "No"
    market_price: float   # current market-implied probability (0-1)
    best_bid: float = 0.0
    best_ask: float = 0.0
    spread: float = 0.0
    volume_24h: float = 0.0

@dataclass
class Market:
    """A Polymarket prediction market."""
    condition_id: str
    question: str
    slug: str
    end_date: Optional[datetime] = None
    days_to_resolution: Optional[float] = None
    volume: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    outcomes: list[MarketOutcome] = field(default_factory=list)
    category: str = ""
    active: bool = True

@dataclass
class KellyBet:
    """A recommended bet from the Kelly Criterion."""
    market: Market
    outcome: MarketOutcome
    edge: float                # your edge = p_true - market_price
    kelly_fraction: float      # full Kelly % of bankroll
    adj_kelly_fraction: float  # fractional Kelly (risk-adjusted)
    bet_size_usd: float        # dollar amount to bet
    expected_value: float      # expected profit per dollar
    odds_decimal: float        # decimal odds
    confidence: str            # "LOW", "MEDIUM", "HIGH"
    score: float               # composite ranking score

# ─── API Layer ────────────────────────────────────────────────────────────────

class PolymarketAPI:
    """Handles all Polymarket API interactions (no auth required)."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _get(self, url: str, params: dict = None) -> dict | list | None:
        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            print(f"  [API Error] {e}")
            return None

    # ── Gamma API (market metadata) ───────────────────────────────────────

    def get_markets(self, limit: int = 100, offset: int = 0,
                    active: bool = True, closed: bool = False) -> list[dict]:
        """Fetch markets from the Gamma API."""
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": "volume24hr",
            "ascending": "false",
        }
        data = self._get(f"{GAMMA_API_BASE}/markets", params)
        return data if isinstance(data, list) else []

    def get_events(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Fetch events (groups of markets) from Gamma API."""
        params = {"limit": limit, "offset": offset, "active": "true",
                  "order": "volume24hr", "ascending": "false"}
        data = self._get(f"{GAMMA_API_BASE}/events", params)
        return data if isinstance(data, list) else []

    # ── CLOB API (order book & pricing) ───────────────────────────────────

    def get_orderbook(self, token_id: str) -> dict | None:
        """Get order book for a specific token (outcome)."""
        return self._get(f"{CLOB_API_BASE}/book", {"token_id": token_id})

    def get_midpoint(self, token_id: str) -> float | None:
        """Get midpoint price for a token."""
        data = self._get(f"{CLOB_API_BASE}/midpoint", {"token_id": token_id})
        if data and "mid" in data:
            try:
                return float(data["mid"])
            except (ValueError, TypeError):
                return None
        return None

    def get_price(self, token_id: str, side: str = "buy") -> float | None:
        """Get best price for a token."""
        data = self._get(f"{CLOB_API_BASE}/price",
                         {"token_id": token_id, "side": side})
        if data and "price" in data:
            try:
                return float(data["price"])
            except (ValueError, TypeError):
                return None
        return None

# ─── Kelly Criterion Engine ───────────────────────────────────────────────────

class KellyCriterion:
    """
    Kelly Criterion calculator for binary prediction markets.

    Full Kelly: f* = (bp - q) / b
      where:
        b = net odds received (decimal odds - 1)
        p = your estimated true probability of winning
        q = 1 - p
        f* = fraction of bankroll to bet

    We use FRACTIONAL Kelly to reduce variance:
        f_adj = kelly_multiplier * f*
    """

    def __init__(self, bankroll: float, kelly_multiplier: float = 0.25,
                 max_position_pct: float = 0.10, min_edge: float = 0.05):
        self.bankroll = bankroll
        self.kelly_multiplier = kelly_multiplier   # 0.25 = quarter Kelly
        self.max_position_pct = max_position_pct   # max 10% of bankroll per bet
        self.min_edge = min_edge                    # minimum 5% edge required

    def calculate(self, market_prob: float, true_prob: float) -> dict:
        """
        Calculate Kelly bet for a YES position.

        Args:
            market_prob: current market price (implied probability)
            true_prob:   your estimated true probability

        Returns dict with kelly details.
        """
        if market_prob <= 0 or market_prob >= 1 or true_prob <= 0 or true_prob >= 1:
            return {"kelly_fraction": 0, "edge": 0, "ev": 0}

        # Decimal odds for buying YES at market_prob
        # If you buy YES at $0.60, you get $1 if correct → decimal odds = 1/0.60
        decimal_odds = 1.0 / market_prob
        b = decimal_odds - 1.0   # net odds (profit per $1 wagered)

        p = true_prob
        q = 1.0 - p
        edge = p - market_prob

        # Full Kelly fraction
        kelly_f = (b * p - q) / b if b > 0 else 0

        # Clamp to [0, 1]
        kelly_f = max(0, min(1, kelly_f))

        # Fractional Kelly
        adj_f = kelly_f * self.kelly_multiplier

        # Cap at max position size
        adj_f = min(adj_f, self.max_position_pct)

        # Expected value per dollar
        ev = (p * b) - q  # profit if win * prob_win - loss_if_lose * prob_lose

        return {
            "kelly_fraction": kelly_f,
            "adj_kelly_fraction": adj_f,
            "bet_size": adj_f * self.bankroll,
            "edge": edge,
            "ev": ev,
            "decimal_odds": decimal_odds,
        }


# ─── Edge Estimation Heuristics ──────────────────────────────────────────────

class EdgeEstimator:
    """
    Heuristic edge estimation when you don't have your own model.

    IMPORTANT: In real trading, you should replace these heuristics with
    your own research-based probability estimates. These are EXAMPLES of
    signals you might combine.

    The tool identifies markets where structural inefficiencies may exist:
    - Wide bid-ask spreads (illiquid = possibly mispriced)
    - Prices near 0.50 with low volume (uncertain = opportunity)
    - Rapid recent price movement (overreaction potential)
    """

    @staticmethod
    def estimate_edge_signals(market: Market) -> dict:
        """
        Returns edge signals (not a final probability estimate).
        You should use these as INPUTS to your own judgment.
        """
        signals = {
            "spread_signal": 0.0,
            "volume_signal": 0.0,
            "time_signal": 0.0,
            "composite_adjustment": 0.0,
        }

        for outcome in market.outcomes:
            if outcome.outcome.lower() == "yes":
                # Wide spreads may indicate mispricing
                if outcome.spread > 0.05:
                    signals["spread_signal"] = min(outcome.spread * 0.3, 0.05)

                # Low volume near 50/50 = uncertain market
                if 0.35 < outcome.market_price < 0.65 and market.volume_24h < 5000:
                    signals["volume_signal"] = 0.02

                # Short time to resolution increases urgency
                if market.days_to_resolution and market.days_to_resolution < 7:
                    signals["time_signal"] = 0.01

        signals["composite_adjustment"] = (
            signals["spread_signal"] +
            signals["volume_signal"] +
            signals["time_signal"]
        )

        return signals


# ─── Market Scanner ───────────────────────────────────────────────────────────

class MarketScanner:
    """Scans and filters Polymarket markets for short-term opportunities."""

    def __init__(self, api: PolymarketAPI, max_days: int = 30,
                 min_volume_24h: float = 100, min_liquidity: float = 500):
        self.api = api
        self.max_days = max_days
        self.min_volume_24h = min_volume_24h
        self.min_liquidity = min_liquidity

    def scan(self, num_pages: int = 3) -> list[Market]:
        """Scan markets and return filtered, enriched Market objects."""
        raw_markets = []
        for page in range(num_pages):
            print(f"  Fetching page {page + 1}...")
            batch = self.api.get_markets(limit=100, offset=page * 100)
            if not batch:
                break
            raw_markets.extend(batch)
            time.sleep(0.5)  # polite rate limiting

        print(f"  Fetched {len(raw_markets)} raw markets. Filtering...")
        markets = []
        for raw in raw_markets:
            market = self._parse_market(raw)
            if market and self._passes_filter(market):
                markets.append(market)

        print(f"  {len(markets)} markets pass filters. Enriching with orderbook data...")
        enriched = []
        for i, market in enumerate(markets):
            self._enrich_with_orderbook(market)
            enriched.append(market)
            if (i + 1) % 10 == 0:
                print(f"    Enriched {i + 1}/{len(markets)}...")
                time.sleep(0.3)

        return enriched

    def _parse_market(self, raw: dict) -> Optional[Market]:
        """Parse raw API data into a Market object."""
        try:
            # Parse end date
            end_date = None
            days_to_res = None
            end_str = raw.get("endDate") or raw.get("end_date_iso")
            if end_str:
                try:
                    end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    days_to_res = (end_date - now).total_seconds() / 86400
                except (ValueError, TypeError):
                    pass

            # Parse outcomes/tokens
            outcomes = []
            clob_tokens = raw.get("clobTokenIds")
            outcome_prices = raw.get("outcomePrices")
            outcome_names = raw.get("outcomes")

            if clob_tokens and outcome_prices and outcome_names:
                try:
                    token_ids = json.loads(clob_tokens) if isinstance(clob_tokens, str) else clob_tokens
                    prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                    names = json.loads(outcome_names) if isinstance(outcome_names, str) else outcome_names
                except (json.JSONDecodeError, TypeError):
                    token_ids, prices, names = [], [], []

                for tid, price, name in zip(token_ids, prices, names):
                    try:
                        p = float(price)
                    except (ValueError, TypeError):
                        p = 0.5
                    outcomes.append(MarketOutcome(
                        token_id=str(tid),
                        outcome=str(name),
                        market_price=p,
                    ))

            market = Market(
                condition_id=raw.get("conditionId", raw.get("condition_id", "")),
                question=raw.get("question", "Unknown"),
                slug=raw.get("slug", ""),
                end_date=end_date,
                days_to_resolution=days_to_res,
                volume=float(raw.get("volume", 0) or 0),
                volume_24h=float(raw.get("volume24hr", 0) or 0),
                liquidity=float(raw.get("liquidityClob", 0) or raw.get("liquidity", 0) or 0),
                outcomes=outcomes,
                category=raw.get("groupItemTitle", "") or raw.get("category", "") or "",
                active=raw.get("active", True),
            )
            return market

        except Exception as e:
            return None

    def _passes_filter(self, m: Market) -> bool:
        """Filter for short-term, liquid, active markets."""
        if not m.active:
            return False
        if m.days_to_resolution is not None:
            if m.days_to_resolution < 0 or m.days_to_resolution > self.max_days:
                return False
        else:
            # No end date → skip for short-term focus
            return False
        if m.volume_24h < self.min_volume_24h:
            return False
        if len(m.outcomes) < 2:
            return False
        return True

    def _enrich_with_orderbook(self, market: Market):
        """Add bid/ask/spread data from the CLOB API."""
        for outcome in market.outcomes:
            if not outcome.token_id:
                continue
            try:
                book = self.api.get_orderbook(outcome.token_id)
                if book:
                    bids = book.get("bids", [])
                    asks = book.get("asks", [])
                    if bids:
                        outcome.best_bid = float(bids[0].get("price", 0))
                    if asks:
                        outcome.best_ask = float(asks[0].get("price", 0))
                    if outcome.best_bid and outcome.best_ask:
                        outcome.spread = outcome.best_ask - outcome.best_bid
                time.sleep(0.15)  # rate limit
            except Exception:
                pass


# ─── Opportunity Ranker ───────────────────────────────────────────────────────

class OpportunityRanker:
    """Ranks and scores betting opportunities."""

    def __init__(self, kelly: KellyCriterion, min_edge: float = 0.03):
        self.kelly = kelly
        self.min_edge = min_edge

    def find_opportunities(self, markets: list[Market],
                           user_estimates: dict[str, float] = None
                           ) -> list[KellyBet]:
        """
        Find and rank betting opportunities.

        Args:
            markets: list of enriched Market objects
            user_estimates: optional dict of {condition_id: your_true_prob}
                           If not provided, uses heuristic signals as a DEMO.
        """
        bets = []

        for market in markets:
            for outcome in market.outcomes:
                if outcome.outcome.lower() != "yes":
                    continue

                market_prob = outcome.market_price
                if market_prob <= 0.02 or market_prob >= 0.98:
                    continue  # skip extremes

                # Determine true probability estimate
                if user_estimates and market.condition_id in user_estimates:
                    true_prob = user_estimates[market.condition_id]
                else:
                    # DEMO: use heuristic edge signals
                    signals = EdgeEstimator.estimate_edge_signals(market)
                    adj = signals["composite_adjustment"]
                    # Slight contrarian: if market says 0.60, we estimate ~0.60 + adj
                    # This is a PLACEHOLDER — replace with your own model!
                    true_prob = min(0.95, max(0.05, market_prob + adj))

                edge = true_prob - market_prob
                if edge < self.min_edge:
                    continue

                k = self.kelly.calculate(market_prob, true_prob)
                if k["adj_kelly_fraction"] <= 0:
                    continue

                # Confidence level
                if edge > 0.15:
                    confidence = "HIGH"
                elif edge > 0.08:
                    confidence = "MEDIUM"
                else:
                    confidence = "LOW"

                # Composite score for ranking
                score = (
                    edge * 40 +
                    k["ev"] * 30 +
                    min(market.volume_24h / 50000, 1) * 15 +
                    min(market.liquidity / 100000, 1) * 10 +
                    (1 / max(market.days_to_resolution, 0.5)) * 5
                )

                bet = KellyBet(
                    market=market,
                    outcome=outcome,
                    edge=edge,
                    kelly_fraction=k["kelly_fraction"],
                    adj_kelly_fraction=k["adj_kelly_fraction"],
                    bet_size_usd=k["bet_size"],
                    expected_value=k["ev"],
                    odds_decimal=k["decimal_odds"],
                    confidence=confidence,
                    score=score,
                )
                bets.append(bet)

        bets.sort(key=lambda b: b.score, reverse=True)
        return bets


# ─── Risk Management ─────────────────────────────────────────────────────────

class RiskManager:
    """
    Portfolio-level risk management for prediction market bets.

    Rules:
    1. Max single bet: X% of bankroll (default 10%)
    2. Max total exposure: Y% of bankroll (default 40%)
    3. Max correlated exposure: limit bets on similar events
    4. Minimum edge threshold
    5. Diversification: spread across categories
    """

    def __init__(self, bankroll: float, max_single_pct: float = 0.10,
                 max_total_pct: float = 0.40, max_correlated_pct: float = 0.20,
                 max_bets: int = 10):
        self.bankroll = bankroll
        self.max_single = bankroll * max_single_pct
        self.max_total = bankroll * max_total_pct
        self.max_correlated = bankroll * max_correlated_pct
        self.max_bets = max_bets

    def apply_limits(self, bets: list[KellyBet]) -> list[KellyBet]:
        """Apply risk limits to a ranked list of bets."""
        approved = []
        total_exposure = 0.0
        category_exposure: dict[str, float] = {}

        for bet in bets:
            if len(approved) >= self.max_bets:
                break

            # Cap single bet
            bet.bet_size_usd = min(bet.bet_size_usd, self.max_single)

            # Check total exposure
            if total_exposure + bet.bet_size_usd > self.max_total:
                remaining = self.max_total - total_exposure
                if remaining < 5:  # minimum $5 bet
                    continue
                bet.bet_size_usd = remaining

            # Check correlated exposure (same category)
            cat = bet.market.category or "general"
            cat_exp = category_exposure.get(cat, 0)
            if cat_exp + bet.bet_size_usd > self.max_correlated:
                remaining = self.max_correlated - cat_exp
                if remaining < 5:
                    continue
                bet.bet_size_usd = remaining

            # Recalculate adjusted kelly based on final bet size
            bet.adj_kelly_fraction = bet.bet_size_usd / self.bankroll

            approved.append(bet)
            total_exposure += bet.bet_size_usd
            category_exposure[cat] = cat_exp + bet.bet_size_usd

        return approved

    def portfolio_summary(self, bets: list[KellyBet]) -> dict:
        """Generate portfolio risk summary."""
        if not bets:
            return {"total_bets": 0}

        total_bet = sum(b.bet_size_usd for b in bets)
        avg_edge = sum(b.edge for b in bets) / len(bets)
        avg_ev = sum(b.expected_value for b in bets) / len(bets)
        weighted_ev = sum(b.expected_value * b.bet_size_usd for b in bets) / total_bet if total_bet else 0

        # Expected profit
        expected_profit = sum(b.expected_value * b.bet_size_usd for b in bets)

        # Worst case (all bets lose)
        worst_case = -total_bet

        # Best case (all bets win)
        best_case = sum(b.bet_size_usd * (b.odds_decimal - 1) for b in bets)

        # Category breakdown
        by_cat: dict[str, float] = {}
        for b in bets:
            cat = b.market.category or "general"
            by_cat[cat] = by_cat.get(cat, 0) + b.bet_size_usd

        return {
            "total_bets": len(bets),
            "total_exposure": total_bet,
            "exposure_pct": total_bet / self.bankroll * 100,
            "avg_edge": avg_edge,
            "avg_ev": avg_ev,
            "weighted_ev": weighted_ev,
            "expected_profit": expected_profit,
            "worst_case": worst_case,
            "best_case": best_case,
            "category_breakdown": by_cat,
        }


# ─── Display / Reports ───────────────────────────────────────────────────────

def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║       POLYMARKET KELLY CRITERION POSITION SIZER                ║
║       Short-Term Prediction Market Opportunity Scanner         ║
╚══════════════════════════════════════════════════════════════════╝
    """)


def print_bets(bets: list[KellyBet], bankroll: float):
    """Pretty-print the recommended bets."""
    if not bets:
        print("\n  ⚠  No opportunities found matching your criteria.")
        return

    print(f"\n{'='*80}")
    print(f"  TOP OPPORTUNITIES (Bankroll: ${bankroll:,.2f})")
    print(f"{'='*80}\n")

    for i, bet in enumerate(bets, 1):
        days = bet.market.days_to_resolution
        days_str = f"{days:.1f}d" if days else "N/A"

        print(f"  #{i} | {bet.confidence} confidence | Score: {bet.score:.2f}")
        print(f"  ┌─ Market: {bet.market.question[:70]}")
        print(f"  ├─ Resolves in: {days_str} | 24h Vol: ${bet.market.volume_24h:,.0f} | Liq: ${bet.market.liquidity:,.0f}")
        print(f"  ├─ Market Price (YES): {bet.outcome.market_price:.1%}  |  Spread: {bet.outcome.spread:.3f}")
        print(f"  ├─ Your Edge: {bet.edge:.1%}  |  EV: {bet.expected_value:.1%}  |  Odds: {bet.odds_decimal:.2f}x")
        print(f"  ├─ Full Kelly: {bet.kelly_fraction:.2%}  →  Adj Kelly: {bet.adj_kelly_fraction:.2%}")
        print(f"  └─ 💰 BET SIZE: ${bet.bet_size_usd:.2f} ({bet.adj_kelly_fraction:.2%} of bankroll)")
        print()


def print_portfolio_summary(summary: dict, bankroll: float):
    """Print portfolio risk summary."""
    if summary["total_bets"] == 0:
        return

    print(f"{'='*80}")
    print(f"  PORTFOLIO RISK SUMMARY")
    print(f"{'='*80}")
    print(f"  Bankroll:           ${bankroll:,.2f}")
    print(f"  Total Bets:         {summary['total_bets']}")
    print(f"  Total Exposure:     ${summary['total_exposure']:,.2f} ({summary['exposure_pct']:.1f}%)")
    print(f"  Avg Edge:           {summary['avg_edge']:.2%}")
    print(f"  Weighted EV:        {summary['weighted_ev']:.2%}")
    print(f"  Expected Profit:    ${summary['expected_profit']:,.2f}")
    print(f"  Worst Case:         ${summary['worst_case']:,.2f}")
    print(f"  Best Case:          ${summary['best_case']:,.2f}")
    print(f"\n  Category Breakdown:")
    for cat, amt in summary["category_breakdown"].items():
        print(f"    {cat[:30]:30s}  ${amt:,.2f}")
    print()


# ─── Save Results to JSON ────────────────────────────────────────────────────

def save_results(bets: list[KellyBet], summary: dict, filename: str = "results.json"):
    """Save results to a JSON file for further analysis."""
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portfolio_summary": summary,
        "bets": [
            {
                "rank": i + 1,
                "question": b.market.question,
                "slug": b.market.slug,
                "condition_id": b.market.condition_id,
                "days_to_resolution": b.market.days_to_resolution,
                "market_price": b.outcome.market_price,
                "spread": b.outcome.spread,
                "edge": b.edge,
                "ev": b.expected_value,
                "kelly_full": b.kelly_fraction,
                "kelly_adj": b.adj_kelly_fraction,
                "bet_size_usd": b.bet_size_usd,
                "odds_decimal": b.odds_decimal,
                "confidence": b.confidence,
                "score": b.score,
                "volume_24h": b.market.volume_24h,
                "liquidity": b.market.liquidity,
                "category": b.market.category,
                "polymarket_url": f"https://polymarket.com/event/{b.market.slug}" if b.market.slug else "",
            }
            for i, b in enumerate(bets)
        ],
    }
    with open(filename, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"  📁 Results saved to {filename}")


# ─── Main Entry Point ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Polymarket Kelly Criterion Scanner")
    parser.add_argument("--bankroll", type=float, default=1000.0,
                        help="Your total bankroll in USD (default: 1000)")
    parser.add_argument("--kelly-fraction", type=float, default=0.25,
                        help="Kelly fraction multiplier 0-1 (default: 0.25 = quarter Kelly)")
    parser.add_argument("--max-days", type=int, default=30,
                        help="Max days to resolution (default: 30)")
    parser.add_argument("--min-edge", type=float, default=0.03,
                        help="Minimum edge to consider (default: 0.03 = 3%%)")
    parser.add_argument("--max-position", type=float, default=0.10,
                        help="Max single position as %% of bankroll (default: 0.10)")
    parser.add_argument("--max-exposure", type=float, default=0.40,
                        help="Max total exposure as %% of bankroll (default: 0.40)")
    parser.add_argument("--max-bets", type=int, default=10,
                        help="Max number of bets (default: 10)")
    parser.add_argument("--pages", type=int, default=3,
                        help="Number of API pages to scan (default: 3)")
    parser.add_argument("--output", type=str, default="results.json",
                        help="Output JSON file (default: results.json)")
    args = parser.parse_args()

    print_banner()
    print(f"  Settings: bankroll=${args.bankroll:,.0f} | kelly={args.kelly_fraction} | "
          f"max_days={args.max_days} | min_edge={args.min_edge:.0%}")
    print()

    # Initialize components
    api = PolymarketAPI()
    kelly = KellyCriterion(
        bankroll=args.bankroll,
        kelly_multiplier=args.kelly_fraction,
        max_position_pct=args.max_position,
        min_edge=args.min_edge,
    )
    scanner = MarketScanner(api, max_days=args.max_days)
    ranker = OpportunityRanker(kelly, min_edge=args.min_edge)
    risk_mgr = RiskManager(
        bankroll=args.bankroll,
        max_single_pct=args.max_position,
        max_total_pct=args.max_exposure,
        max_bets=args.max_bets,
    )

    # Step 1: Scan markets
    print("  [1/4] Scanning Polymarket for short-term markets...")
    markets = scanner.scan(num_pages=args.pages)
    print(f"  Found {len(markets)} qualifying markets.\n")

    if not markets:
        print("  No markets found. Try adjusting filters (--max-days, --min-volume).")
        return

    # Step 2: Find opportunities
    print("  [2/4] Analyzing edges with Kelly Criterion...")
    opportunities = ranker.find_opportunities(markets)
    print(f"  Found {len(opportunities)} opportunities with edge > {args.min_edge:.0%}.\n")

    # Step 3: Apply risk management
    print("  [3/4] Applying risk management limits...")
    approved_bets = risk_mgr.apply_limits(opportunities)
    print(f"  Approved {len(approved_bets)} bets after risk limits.\n")

    # Step 4: Display & save
    print("  [4/4] Generating report...\n")
    print_bets(approved_bets, args.bankroll)

    summary = risk_mgr.portfolio_summary(approved_bets)
    print_portfolio_summary(summary, args.bankroll)

    save_results(approved_bets, summary, args.output)

    print("\n  ⚠  DISCLAIMER: This is a research tool, NOT financial advice.")
    print("  Edge estimates are heuristic demos. Replace with your own research.")
    print("  Always verify markets on https://polymarket.com before betting.\n")


if __name__ == "__main__":
    main()
