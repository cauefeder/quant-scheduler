"""
Comprehensive test suite for Polymarket Kelly Criterion Scanner.
Tests all components with mocked API data since we can't reach the network.
"""

import json
import sys
import os
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from io import StringIO

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from polymarket_scraper import (
    PolymarketAPI,
    MarketScanner,
    KellyCriterion,
    EdgeEstimator,
    OpportunityRanker,
    RiskManager,
    Market,
    MarketOutcome,
    KellyBet,
    print_bets,
    print_portfolio_summary,
    save_results,
)


# ─── Mock Data ────────────────────────────────────────────────────────────────

def make_future_date(days_ahead: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    return dt.isoformat()

MOCK_MARKETS_RAW = [
    {
        "conditionId": "cond_001",
        "question": "Will Bitcoin exceed $100,000 by March 2026?",
        "slug": "will-bitcoin-exceed-100k-march-2026",
        "endDate": make_future_date(10),
        "volume": 250000,
        "volume24hr": 15000,
        "liquidityClob": 45000,
        "clobTokenIds": json.dumps(["token_001_yes", "token_001_no"]),
        "outcomePrices": json.dumps(["0.62", "0.38"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "groupItemTitle": "crypto",
        "active": True,
    },
    {
        "conditionId": "cond_002",
        "question": "Will the Fed cut rates at the March 2026 meeting?",
        "slug": "fed-cut-rates-march-2026",
        "endDate": make_future_date(5),
        "volume": 500000,
        "volume24hr": 42000,
        "liquidityClob": 120000,
        "clobTokenIds": json.dumps(["token_002_yes", "token_002_no"]),
        "outcomePrices": json.dumps(["0.45", "0.55"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "groupItemTitle": "economics",
        "active": True,
    },
    {
        "conditionId": "cond_003",
        "question": "Will SpaceX Starship complete orbital flight by March 2026?",
        "slug": "spacex-starship-orbital",
        "endDate": make_future_date(20),
        "volume": 180000,
        "volume24hr": 8000,
        "liquidityClob": 30000,
        "clobTokenIds": json.dumps(["token_003_yes", "token_003_no"]),
        "outcomePrices": json.dumps(["0.78", "0.22"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "groupItemTitle": "science",
        "active": True,
    },
    {
        "conditionId": "cond_004",
        "question": "Will there be a government shutdown in Feb 2026?",
        "slug": "government-shutdown-feb-2026",
        "endDate": make_future_date(3),
        "volume": 320000,
        "volume24hr": 28000,
        "liquidityClob": 75000,
        "clobTokenIds": json.dumps(["token_004_yes", "token_004_no"]),
        "outcomePrices": json.dumps(["0.30", "0.70"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "groupItemTitle": "politics",
        "active": True,
    },
    {
        "conditionId": "cond_005",
        "question": "Will Nvidia stock close above $150 this Friday?",
        "slug": "nvidia-above-150-friday",
        "endDate": make_future_date(2),
        "volume": 95000,
        "volume24hr": 22000,
        "liquidityClob": 55000,
        "clobTokenIds": json.dumps(["token_005_yes", "token_005_no"]),
        "outcomePrices": json.dumps(["0.52", "0.48"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "groupItemTitle": "stocks",
        "active": True,
    },
    # ── Markets that should be FILTERED OUT ──
    {
        "conditionId": "cond_006_expired",
        "question": "Already resolved market",
        "slug": "already-resolved",
        "endDate": make_future_date(-5),  # in the past
        "volume": 100000,
        "volume24hr": 500,
        "liquidityClob": 10000,
        "clobTokenIds": json.dumps(["token_006_yes", "token_006_no"]),
        "outcomePrices": json.dumps(["0.99", "0.01"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "active": True,
    },
    {
        "conditionId": "cond_007_long",
        "question": "Will AI achieve AGI by 2030?",
        "slug": "agi-by-2030",
        "endDate": make_future_date(365),  # too far out
        "volume": 800000,
        "volume24hr": 50000,
        "liquidityClob": 200000,
        "clobTokenIds": json.dumps(["token_007_yes", "token_007_no"]),
        "outcomePrices": json.dumps(["0.15", "0.85"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "active": True,
    },
    {
        "conditionId": "cond_008_lowvol",
        "question": "Low volume market nobody cares about",
        "slug": "low-volume",
        "endDate": make_future_date(7),
        "volume": 500,
        "volume24hr": 10,  # below threshold
        "liquidityClob": 200,
        "clobTokenIds": json.dumps(["token_008_yes", "token_008_no"]),
        "outcomePrices": json.dumps(["0.50", "0.50"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "active": True,
    },
    {
        "conditionId": "cond_009_inactive",
        "question": "Inactive market",
        "slug": "inactive",
        "endDate": make_future_date(7),
        "volume": 100000,
        "volume24hr": 5000,
        "liquidityClob": 50000,
        "clobTokenIds": json.dumps(["token_009_yes", "token_009_no"]),
        "outcomePrices": json.dumps(["0.50", "0.50"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "active": False,  # inactive
    },
]

MOCK_ORDERBOOKS = {
    "token_001_yes": {"bids": [{"price": "0.60", "size": "500"}], "asks": [{"price": "0.64", "size": "300"}]},
    "token_001_no":  {"bids": [{"price": "0.35", "size": "400"}], "asks": [{"price": "0.40", "size": "200"}]},
    "token_002_yes": {"bids": [{"price": "0.43", "size": "1000"}], "asks": [{"price": "0.47", "size": "800"}]},
    "token_002_no":  {"bids": [{"price": "0.52", "size": "900"}], "asks": [{"price": "0.57", "size": "700"}]},
    "token_003_yes": {"bids": [{"price": "0.76", "size": "600"}], "asks": [{"price": "0.80", "size": "500"}]},
    "token_003_no":  {"bids": [{"price": "0.19", "size": "300"}], "asks": [{"price": "0.24", "size": "200"}]},
    "token_004_yes": {"bids": [{"price": "0.28", "size": "800"}], "asks": [{"price": "0.32", "size": "600"}]},
    "token_004_no":  {"bids": [{"price": "0.67", "size": "700"}], "asks": [{"price": "0.72", "size": "500"}]},
    "token_005_yes": {"bids": [{"price": "0.50", "size": "1200"}], "asks": [{"price": "0.54", "size": "1000"}]},
    "token_005_no":  {"bids": [{"price": "0.45", "size": "1100"}], "asks": [{"price": "0.50", "size": "900"}]},
}


# ─── Test Cases ───────────────────────────────────────────────────────────────

class TestKellyCriterion(unittest.TestCase):
    """Test the Kelly Criterion math engine."""

    def setUp(self):
        self.kelly = KellyCriterion(
            bankroll=10000,
            kelly_multiplier=0.25,
            max_position_pct=0.10,
            min_edge=0.03,
        )

    def test_positive_edge(self):
        """When you have a genuine edge, Kelly should recommend a bet."""
        result = self.kelly.calculate(market_prob=0.50, true_prob=0.60)
        self.assertGreater(result["kelly_fraction"], 0)
        self.assertGreater(result["adj_kelly_fraction"], 0)
        self.assertGreater(result["bet_size"], 0)
        self.assertGreater(result["edge"], 0)
        self.assertGreater(result["ev"], 0)
        print(f"  ✓ Positive edge: market=50%, true=60% → Kelly={result['kelly_fraction']:.2%}, "
              f"Adj={result['adj_kelly_fraction']:.2%}, Bet=${result['bet_size']:.2f}")

    def test_no_edge(self):
        """When market is correctly priced, Kelly should recommend zero."""
        result = self.kelly.calculate(market_prob=0.60, true_prob=0.60)
        self.assertEqual(result["kelly_fraction"], 0)
        self.assertEqual(result["bet_size"], 0)
        print(f"  ✓ No edge: market=60%, true=60% → Kelly=0 (correct)")

    def test_negative_edge(self):
        """When market is overpriced vs your estimate, no bet."""
        result = self.kelly.calculate(market_prob=0.70, true_prob=0.55)
        self.assertEqual(result["kelly_fraction"], 0)
        self.assertEqual(result["bet_size"], 0)
        print(f"  ✓ Negative edge: market=70%, true=55% → Kelly=0 (don't bet)")

    def test_fractional_kelly_reduces_size(self):
        """Fractional Kelly should always be less than full Kelly."""
        result = self.kelly.calculate(market_prob=0.40, true_prob=0.55)
        self.assertLess(result["adj_kelly_fraction"], result["kelly_fraction"])
        self.assertAlmostEqual(
            result["adj_kelly_fraction"],
            result["kelly_fraction"] * 0.25,
            places=6,
        )
        print(f"  ✓ Fractional Kelly: full={result['kelly_fraction']:.4f}, "
              f"adj={result['adj_kelly_fraction']:.4f} (25% of full)")

    def test_max_position_cap(self):
        """Even with huge edge, position should be capped."""
        result = self.kelly.calculate(market_prob=0.10, true_prob=0.80)
        # Full kelly would be enormous, but adj should be capped at 10%
        self.assertLessEqual(result["adj_kelly_fraction"], 0.10)
        self.assertLessEqual(result["bet_size"], 1000)  # 10% of $10k
        print(f"  ✓ Position cap: huge edge but adj={result['adj_kelly_fraction']:.2%} "
              f"(capped at 10%), bet=${result['bet_size']:.2f}")

    def test_edge_boundary_values(self):
        """Test boundary probabilities."""
        # Near zero
        result = self.kelly.calculate(market_prob=0.01, true_prob=0.05)
        self.assertGreaterEqual(result["kelly_fraction"], 0)

        # Near one
        result = self.kelly.calculate(market_prob=0.99, true_prob=0.995)
        self.assertGreaterEqual(result["kelly_fraction"], 0)

        # Invalid
        result = self.kelly.calculate(market_prob=0, true_prob=0.5)
        self.assertEqual(result["kelly_fraction"], 0)

        result = self.kelly.calculate(market_prob=1, true_prob=0.5)
        self.assertEqual(result["kelly_fraction"], 0)
        print(f"  ✓ Boundary values handled correctly")

    def test_decimal_odds_calculation(self):
        """Verify decimal odds = 1 / market_prob."""
        result = self.kelly.calculate(market_prob=0.40, true_prob=0.55)
        self.assertAlmostEqual(result["decimal_odds"], 2.5, places=2)
        print(f"  ✓ Decimal odds: buy at $0.40 → odds={result['decimal_odds']:.2f}x")

    def test_ev_calculation(self):
        """Verify expected value calculation."""
        result = self.kelly.calculate(market_prob=0.50, true_prob=0.65)
        # EV = p*b - q = 0.65*1.0 - 0.35 = 0.30
        expected_ev = 0.65 * (1/0.50 - 1) - 0.35
        self.assertAlmostEqual(result["ev"], expected_ev, places=4)
        print(f"  ✓ EV calculation: {result['ev']:.4f} (expected {expected_ev:.4f})")


class TestMarketParsing(unittest.TestCase):
    """Test market data parsing and filtering."""

    def setUp(self):
        self.api = PolymarketAPI()
        self.scanner = MarketScanner(
            self.api, max_days=30, min_volume_24h=100, min_liquidity=500
        )

    def test_parse_valid_market(self):
        """Parse a well-formed market correctly."""
        market = self.scanner._parse_market(MOCK_MARKETS_RAW[0])
        self.assertIsNotNone(market)
        self.assertEqual(market.condition_id, "cond_001")
        self.assertIn("Bitcoin", market.question)
        self.assertEqual(len(market.outcomes), 2)
        self.assertAlmostEqual(market.outcomes[0].market_price, 0.62, places=2)
        self.assertAlmostEqual(market.outcomes[1].market_price, 0.38, places=2)
        self.assertEqual(market.volume_24h, 15000)
        self.assertEqual(market.category, "crypto")
        self.assertIsNotNone(market.days_to_resolution)
        self.assertGreater(market.days_to_resolution, 0)
        print(f"  ✓ Parsed: '{market.question[:50]}...' | "
              f"YES={market.outcomes[0].market_price:.0%} | "
              f"days={market.days_to_resolution:.1f}")

    def test_parse_all_mock_markets(self):
        """All mock markets should parse without errors."""
        for raw in MOCK_MARKETS_RAW:
            market = self.scanner._parse_market(raw)
            self.assertIsNotNone(market, f"Failed to parse: {raw.get('question')}")
        print(f"  ✓ All {len(MOCK_MARKETS_RAW)} mock markets parsed successfully")

    def test_filter_accepts_valid(self):
        """Valid short-term markets should pass the filter."""
        accepted = []
        for raw in MOCK_MARKETS_RAW:
            market = self.scanner._parse_market(raw)
            if market and self.scanner._passes_filter(market):
                accepted.append(market)

        # Should accept: cond_001 (10d), cond_002 (5d), cond_003 (20d),
        #                cond_004 (3d), cond_005 (2d)
        accepted_ids = [m.condition_id for m in accepted]
        self.assertIn("cond_001", accepted_ids)
        self.assertIn("cond_002", accepted_ids)
        self.assertIn("cond_003", accepted_ids)
        self.assertIn("cond_004", accepted_ids)
        self.assertIn("cond_005", accepted_ids)
        print(f"  ✓ Filter accepted {len(accepted)} valid markets: "
              f"{[m.condition_id for m in accepted]}")

    def test_filter_rejects_expired(self):
        """Expired markets should be filtered out."""
        raw = MOCK_MARKETS_RAW[5]  # cond_006_expired
        market = self.scanner._parse_market(raw)
        self.assertFalse(self.scanner._passes_filter(market))
        print(f"  ✓ Filtered out expired market (days={market.days_to_resolution:.1f})")

    def test_filter_rejects_long_term(self):
        """Markets too far out should be filtered."""
        raw = MOCK_MARKETS_RAW[6]  # cond_007_long (365 days)
        market = self.scanner._parse_market(raw)
        self.assertFalse(self.scanner._passes_filter(market))
        print(f"  ✓ Filtered out long-term market (days={market.days_to_resolution:.1f})")

    def test_filter_rejects_low_volume(self):
        """Low volume markets should be filtered."""
        raw = MOCK_MARKETS_RAW[7]  # cond_008_lowvol
        market = self.scanner._parse_market(raw)
        self.assertFalse(self.scanner._passes_filter(market))
        print(f"  ✓ Filtered out low volume market (vol24h={market.volume_24h})")

    def test_filter_rejects_inactive(self):
        """Inactive markets should be filtered."""
        raw = MOCK_MARKETS_RAW[8]  # cond_009_inactive
        market = self.scanner._parse_market(raw)
        self.assertFalse(self.scanner._passes_filter(market))
        print(f"  ✓ Filtered out inactive market")


class TestOrderbookEnrichment(unittest.TestCase):
    """Test orderbook data enrichment."""

    def test_enrichment_with_mock_orderbook(self):
        """Verify bid/ask/spread are populated from orderbook data."""
        api = PolymarketAPI()
        scanner = MarketScanner(api, max_days=30)

        market = scanner._parse_market(MOCK_MARKETS_RAW[0])

        # Mock the API call
        with patch.object(api, 'get_orderbook') as mock_book:
            def side_effect(token_id):
                return MOCK_ORDERBOOKS.get(token_id)
            mock_book.side_effect = side_effect

            scanner._enrich_with_orderbook(market)

        yes_outcome = market.outcomes[0]
        self.assertAlmostEqual(yes_outcome.best_bid, 0.60, places=2)
        self.assertAlmostEqual(yes_outcome.best_ask, 0.64, places=2)
        self.assertAlmostEqual(yes_outcome.spread, 0.04, places=2)
        print(f"  ✓ Enriched: bid={yes_outcome.best_bid}, ask={yes_outcome.best_ask}, "
              f"spread={yes_outcome.spread}")


class TestEdgeEstimator(unittest.TestCase):
    """Test edge estimation heuristics."""

    def test_wide_spread_signal(self):
        """Wide spreads should generate a positive spread signal."""
        market = Market(
            condition_id="test", question="Test", slug="test",
            volume_24h=10000, liquidity=50000,
            days_to_resolution=5,
            outcomes=[MarketOutcome(
                token_id="t1", outcome="Yes", market_price=0.50,
                spread=0.08, best_bid=0.46, best_ask=0.54,
            )],
        )
        signals = EdgeEstimator.estimate_edge_signals(market)
        self.assertGreater(signals["spread_signal"], 0)
        print(f"  ✓ Wide spread (0.08) → signal={signals['spread_signal']:.4f}")

    def test_narrow_spread_no_signal(self):
        """Tight spreads should not generate a spread signal."""
        market = Market(
            condition_id="test", question="Test", slug="test",
            volume_24h=50000, liquidity=100000,
            days_to_resolution=10,
            outcomes=[MarketOutcome(
                token_id="t1", outcome="Yes", market_price=0.60,
                spread=0.02, best_bid=0.59, best_ask=0.61,
            )],
        )
        signals = EdgeEstimator.estimate_edge_signals(market)
        self.assertEqual(signals["spread_signal"], 0)
        print(f"  ✓ Tight spread (0.02) → signal=0 (no mispricing signal)")

    def test_low_volume_uncertain_market(self):
        """Low volume 50/50 markets should have volume signal."""
        market = Market(
            condition_id="test", question="Test", slug="test",
            volume_24h=2000, liquidity=10000,
            days_to_resolution=7,
            outcomes=[MarketOutcome(
                token_id="t1", outcome="Yes", market_price=0.48,
                spread=0.03,
            )],
        )
        signals = EdgeEstimator.estimate_edge_signals(market)
        self.assertGreater(signals["volume_signal"], 0)
        print(f"  ✓ Low vol uncertain market → volume_signal={signals['volume_signal']:.4f}")

    def test_short_time_signal(self):
        """Markets resolving soon should have time signal."""
        market = Market(
            condition_id="test", question="Test", slug="test",
            volume_24h=20000, liquidity=50000,
            days_to_resolution=3,
            outcomes=[MarketOutcome(
                token_id="t1", outcome="Yes", market_price=0.50,
                spread=0.06,
            )],
        )
        signals = EdgeEstimator.estimate_edge_signals(market)
        self.assertGreater(signals["time_signal"], 0)
        print(f"  ✓ Short-term (3d) → time_signal={signals['time_signal']:.4f}")


class TestOpportunityRanker(unittest.TestCase):
    """Test opportunity ranking with user-provided estimates."""

    def setUp(self):
        self.kelly = KellyCriterion(bankroll=10000, kelly_multiplier=0.25)
        self.ranker = OpportunityRanker(self.kelly, min_edge=0.03)

    def _make_markets(self) -> list[Market]:
        api = PolymarketAPI()
        scanner = MarketScanner(api, max_days=30)
        markets = []
        for raw in MOCK_MARKETS_RAW[:5]:
            m = scanner._parse_market(raw)
            if m:
                # Manually set spreads for testing
                for outcome in m.outcomes:
                    token_id = outcome.token_id
                    if token_id in MOCK_ORDERBOOKS:
                        book = MOCK_ORDERBOOKS[token_id]
                        outcome.best_bid = float(book["bids"][0]["price"])
                        outcome.best_ask = float(book["asks"][0]["price"])
                        outcome.spread = outcome.best_ask - outcome.best_bid
                markets.append(m)
        return markets

    def test_user_estimates_create_opportunities(self):
        """User-provided probability estimates should create bet opportunities."""
        markets = self._make_markets()

        user_estimates = {
            "cond_001": 0.72,  # market says 62%, you think 72% → 10% edge
            "cond_002": 0.55,  # market says 45%, you think 55% → 10% edge
            "cond_004": 0.42,  # market says 30%, you think 42% → 12% edge
        }

        opportunities = self.ranker.find_opportunities(markets, user_estimates)
        self.assertGreater(len(opportunities), 0)

        for opp in opportunities:
            self.assertGreater(opp.edge, 0.03)
            self.assertGreater(opp.bet_size_usd, 0)
            print(f"  ✓ Opportunity: '{opp.market.question[:45]}...' "
                  f"edge={opp.edge:.1%} bet=${opp.bet_size_usd:.2f} "
                  f"conf={opp.confidence}")

    def test_no_edge_no_opportunity(self):
        """Markets without edge should not generate opportunities."""
        markets = self._make_markets()

        # Set estimates equal to market prices → no edge
        user_estimates = {
            "cond_001": 0.62,
            "cond_002": 0.45,
            "cond_003": 0.78,
            "cond_004": 0.30,
            "cond_005": 0.52,
        }

        opportunities = self.ranker.find_opportunities(markets, user_estimates)
        self.assertEqual(len(opportunities), 0)
        print(f"  ✓ No edge → 0 opportunities (correct)")

    def test_opportunities_sorted_by_score(self):
        """Opportunities should be sorted by composite score descending."""
        markets = self._make_markets()
        user_estimates = {
            "cond_001": 0.72,
            "cond_002": 0.55,
            "cond_004": 0.45,
            "cond_005": 0.62,
        }
        opportunities = self.ranker.find_opportunities(markets, user_estimates)
        scores = [o.score for o in opportunities]
        self.assertEqual(scores, sorted(scores, reverse=True))
        print(f"  ✓ Opportunities sorted by score: {[f'{s:.2f}' for s in scores]}")

    def test_extreme_prices_filtered(self):
        """Markets with prices near 0 or 1 should be skipped."""
        market = Market(
            condition_id="extreme", question="Near-certain", slug="extreme",
            volume_24h=10000, liquidity=50000, days_to_resolution=5,
            outcomes=[MarketOutcome(
                token_id="t_ext", outcome="Yes", market_price=0.99,
                spread=0.01,
            )],
        )
        user_estimates = {"extreme": 0.995}
        opportunities = self.ranker.find_opportunities([market], user_estimates)
        self.assertEqual(len(opportunities), 0)
        print(f"  ✓ Extreme price (99%) filtered out correctly")


class TestRiskManager(unittest.TestCase):
    """Test portfolio risk management."""

    def setUp(self):
        self.risk_mgr = RiskManager(
            bankroll=10000,
            max_single_pct=0.10,
            max_total_pct=0.40,
            max_correlated_pct=0.20,
            max_bets=5,
        )

    def _make_bet(self, cid: str, question: str, bet_size: float,
                  category: str = "general", edge: float = 0.10) -> KellyBet:
        market = Market(
            condition_id=cid, question=question, slug=cid,
            volume_24h=10000, liquidity=50000, days_to_resolution=5,
            category=category,
            outcomes=[MarketOutcome(
                token_id=f"t_{cid}", outcome="Yes", market_price=0.50, spread=0.03,
            )],
        )
        return KellyBet(
            market=market,
            outcome=market.outcomes[0],
            edge=edge,
            kelly_fraction=0.20,
            adj_kelly_fraction=bet_size / 10000,
            bet_size_usd=bet_size,
            expected_value=0.15,
            odds_decimal=2.0,
            confidence="MEDIUM",
            score=5.0,
        )

    def test_single_position_cap(self):
        """Single bet should be capped at max_single_pct."""
        bets = [self._make_bet("big", "Big bet", 2000)]  # $2k > 10% of $10k
        approved = self.risk_mgr.apply_limits(bets)
        self.assertEqual(len(approved), 1)
        self.assertLessEqual(approved[0].bet_size_usd, 1000)  # 10% of $10k
        print(f"  ✓ Single position capped: ${2000} → ${approved[0].bet_size_usd:.0f}")

    def test_total_exposure_cap(self):
        """Total exposure should not exceed max_total_pct."""
        bets = [
            self._make_bet(f"bet_{i}", f"Bet {i}", 1000, f"cat_{i}")
            for i in range(6)  # 6 × $1000 = $6000 > 40% of $10k
        ]
        approved = self.risk_mgr.apply_limits(bets)
        total = sum(b.bet_size_usd for b in approved)
        self.assertLessEqual(total, 4000)  # 40% of $10k
        print(f"  ✓ Total exposure capped: 6×$1000 → {len(approved)} bets, "
              f"total=${total:.0f} (max $4000)")

    def test_correlated_exposure_cap(self):
        """Bets in same category should be limited."""
        bets = [
            self._make_bet(f"crypto_{i}", f"Crypto bet {i}", 800, "crypto")
            for i in range(5)  # 5 × $800 = $4000 in crypto > 20% of $10k
        ]
        approved = self.risk_mgr.apply_limits(bets)
        crypto_total = sum(b.bet_size_usd for b in approved
                          if b.market.category == "crypto")
        self.assertLessEqual(crypto_total, 2000)  # 20% of $10k
        print(f"  ✓ Correlated exposure capped: crypto total=${crypto_total:.0f} "
              f"(max $2000)")

    def test_max_bets_limit(self):
        """Should not exceed max number of bets."""
        bets = [
            self._make_bet(f"bet_{i}", f"Bet {i}", 200, f"cat_{i}")
            for i in range(10)
        ]
        approved = self.risk_mgr.apply_limits(bets)
        self.assertLessEqual(len(approved), 5)
        print(f"  ✓ Max bets capped: 10 bets → {len(approved)} approved (max 5)")

    def test_portfolio_summary(self):
        """Portfolio summary should calculate correctly."""
        bets = [
            self._make_bet("a", "Bet A", 500, "crypto"),
            self._make_bet("b", "Bet B", 300, "politics"),
        ]
        summary = self.risk_mgr.portfolio_summary(bets)
        self.assertEqual(summary["total_bets"], 2)
        self.assertAlmostEqual(summary["total_exposure"], 800)
        self.assertAlmostEqual(summary["exposure_pct"], 8.0)
        self.assertIn("crypto", summary["category_breakdown"])
        self.assertIn("politics", summary["category_breakdown"])
        print(f"  ✓ Portfolio summary: {summary['total_bets']} bets, "
              f"${summary['total_exposure']:.0f} exposure ({summary['exposure_pct']:.1f}%), "
              f"EV=${summary['expected_profit']:.2f}")

    def test_empty_portfolio(self):
        """Empty bet list should produce zero summary."""
        summary = self.risk_mgr.portfolio_summary([])
        self.assertEqual(summary["total_bets"], 0)
        print(f"  ✓ Empty portfolio handled correctly")


class TestEndToEnd(unittest.TestCase):
    """Full pipeline test with mocked API."""

    def test_full_pipeline(self):
        """Run the complete pipeline with mock data and user estimates."""
        print("\n  ── Full Pipeline Test ──")

        # Setup
        api = PolymarketAPI()
        scanner = MarketScanner(api, max_days=30, min_volume_24h=100)
        kelly = KellyCriterion(bankroll=5000, kelly_multiplier=0.25, max_position_pct=0.10)
        ranker = OpportunityRanker(kelly, min_edge=0.03)
        risk_mgr = RiskManager(bankroll=5000, max_single_pct=0.10,
                               max_total_pct=0.40, max_bets=5)

        # Mock API calls
        with patch.object(api, 'get_markets', return_value=MOCK_MARKETS_RAW):
            with patch.object(api, 'get_orderbook') as mock_book:
                mock_book.side_effect = lambda tid: MOCK_ORDERBOOKS.get(tid)

                # Step 1: Scan
                markets = scanner.scan(num_pages=1)
                print(f"  Step 1: Scanned → {len(markets)} markets pass filter")
                self.assertEqual(len(markets), 5)

                # Step 2: Find opportunities with user estimates
                user_estimates = {
                    "cond_001": 0.75,  # BTC: market 62%, you 75% → 13% edge
                    "cond_002": 0.56,  # Fed: market 45%, you 56% → 11% edge
                    "cond_004": 0.42,  # Shutdown: market 30%, you 42% → 12% edge
                    "cond_005": 0.60,  # NVDA: market 52%, you 60% → 8% edge
                }
                opportunities = ranker.find_opportunities(markets, user_estimates)
                print(f"  Step 2: Found → {len(opportunities)} opportunities")
                self.assertGreater(len(opportunities), 0)

                # Step 3: Risk management
                approved = risk_mgr.apply_limits(opportunities)
                print(f"  Step 3: Approved → {len(approved)} bets after risk limits")
                self.assertGreater(len(approved), 0)

                # Step 4: Verify all risk limits
                total_exposure = sum(b.bet_size_usd for b in approved)
                self.assertLessEqual(total_exposure, 2000)  # 40% of $5000
                for b in approved:
                    self.assertLessEqual(b.bet_size_usd, 500)  # 10% of $5000
                    self.assertGreater(b.edge, 0.03)

                # Step 5: Portfolio summary
                summary = risk_mgr.portfolio_summary(approved)
                print(f"  Step 4: Portfolio → {summary['total_bets']} bets, "
                      f"${summary['total_exposure']:.0f} exposure, "
                      f"EV=${summary['expected_profit']:.2f}")

                # Step 6: Display (capture stdout)
                captured = StringIO()
                sys.stdout = captured
                print_bets(approved, 5000)
                print_portfolio_summary(summary, 5000)
                sys.stdout = sys.__stdout__
                output = captured.getvalue()
                self.assertIn("BET SIZE", output)
                self.assertIn("PORTFOLIO RISK SUMMARY", output)
                print(f"  Step 5: Report generated successfully ({len(output)} chars)")

                # Step 7: Save to JSON
                save_results(approved, summary, "/tmp/test_results.json")
                with open("/tmp/test_results.json") as f:
                    saved = json.load(f)
                self.assertEqual(len(saved["bets"]), len(approved))
                self.assertIn("portfolio_summary", saved)
                print(f"  Step 6: JSON saved with {len(saved['bets'])} bets")

                # Print final output for visual verification
                sys.stdout = sys.__stdout__
                print(f"\n  ── Approved Bets ──")
                for i, b in enumerate(approved, 1):
                    print(f"  #{i}: {b.market.question[:50]}... "
                          f"| edge={b.edge:.1%} | bet=${b.bet_size_usd:.2f} "
                          f"| EV={b.expected_value:.1%} | {b.confidence}")

                print(f"\n  ── Portfolio ──")
                print(f"  Total exposure: ${summary['total_exposure']:.2f} "
                      f"({summary['exposure_pct']:.1f}% of $5000)")
                print(f"  Expected profit: ${summary['expected_profit']:.2f}")
                print(f"  Worst case: ${summary['worst_case']:.2f}")
                print(f"  Best case: ${summary['best_case']:.2f}")


class TestSaveResults(unittest.TestCase):
    """Test JSON output."""

    def test_json_output_structure(self):
        """Verify JSON output has correct structure."""
        market = Market(
            condition_id="test", question="Test question", slug="test-slug",
            volume_24h=10000, liquidity=50000, days_to_resolution=5,
            category="test_cat",
            outcomes=[MarketOutcome(
                token_id="t_test", outcome="Yes", market_price=0.60, spread=0.03,
            )],
        )
        bet = KellyBet(
            market=market, outcome=market.outcomes[0],
            edge=0.10, kelly_fraction=0.20, adj_kelly_fraction=0.05,
            bet_size_usd=250, expected_value=0.15, odds_decimal=1.67,
            confidence="HIGH", score=8.5,
        )
        summary = {"total_bets": 1, "total_exposure": 250}

        save_results([bet], summary, "/tmp/test_structure.json")
        with open("/tmp/test_structure.json") as f:
            data = json.load(f)

        self.assertIn("generated_at", data)
        self.assertIn("portfolio_summary", data)
        self.assertIn("bets", data)
        self.assertEqual(len(data["bets"]), 1)

        b = data["bets"][0]
        self.assertEqual(b["rank"], 1)
        self.assertEqual(b["question"], "Test question")
        self.assertAlmostEqual(b["edge"], 0.10)
        self.assertAlmostEqual(b["bet_size_usd"], 250)
        self.assertIn("polymarket_url", b)
        print(f"  ✓ JSON structure validated with all required fields")


# ─── Run Tests ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  POLYMARKET KELLY CRITERION - COMPREHENSIVE TEST SUITE")
    print("=" * 70 + "\n")

    # Custom test runner for prettier output
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestKellyCriterion,
        TestMarketParsing,
        TestOrderbookEnrichment,
        TestEdgeEstimator,
        TestOpportunityRanker,
        TestRiskManager,
        TestEndToEnd,
        TestSaveResults,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    if result.wasSuccessful():
        print(f"  ✅ ALL {result.testsRun} TESTS PASSED")
    else:
        print(f"  ❌ {len(result.failures)} FAILURES, {len(result.errors)} ERRORS")
        for fail in result.failures:
            print(f"    FAIL: {fail[0]}")
            print(f"    {fail[1][:200]}")
        for err in result.errors:
            print(f"    ERROR: {err[0]}")
            print(f"    {err[1][:200]}")
    print("=" * 70 + "\n")
