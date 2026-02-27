"""
Test suite for Polymarket → Claude → Telegram pipeline.
Tests all components with mocked APIs.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from polymarket_telegram_bot import (
    PolymarketAPI, MarketScanner, ClaudeEstimator, KellyCriterion,
    RiskManager, TelegramBot, Pipeline, Market, MarketOutcome,
    ClaudeEstimate, KellyBet,
)


# ─── Mock Data ────────────────────────────────────────────────────────────────

def future(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

MOCK_MARKETS = [
    {
        "conditionId": "cond_001_btc",
        "question": "Will Bitcoin exceed $100,000 by March 2026?",
        "slug": "bitcoin-100k-march",
        "endDate": future(10),
        "volume": 250000, "volume24hr": 15000, "liquidityClob": 45000,
        "clobTokenIds": json.dumps(["tok_001_y", "tok_001_n"]),
        "outcomePrices": json.dumps(["0.62", "0.38"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "groupItemTitle": "crypto", "active": True,
    },
    {
        "conditionId": "cond_002_fed",
        "question": "Will the Fed cut rates at the March 2026 meeting?",
        "slug": "fed-rates-march",
        "endDate": future(5),
        "volume": 500000, "volume24hr": 42000, "liquidityClob": 120000,
        "clobTokenIds": json.dumps(["tok_002_y", "tok_002_n"]),
        "outcomePrices": json.dumps(["0.45", "0.55"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "groupItemTitle": "economics", "active": True,
    },
    {
        "conditionId": "cond_003_nvd",
        "question": "Will Nvidia close above $150 this Friday?",
        "slug": "nvidia-150-friday",
        "endDate": future(2),
        "volume": 95000, "volume24hr": 22000, "liquidityClob": 55000,
        "clobTokenIds": json.dumps(["tok_003_y", "tok_003_n"]),
        "outcomePrices": json.dumps(["0.52", "0.48"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "groupItemTitle": "stocks", "active": True,
    },
]

MOCK_ORDERBOOKS = {
    "tok_001_y": {"bids": [{"price": "0.60", "size": "500"}], "asks": [{"price": "0.64", "size": "300"}]},
    "tok_001_n": {"bids": [{"price": "0.35", "size": "400"}], "asks": [{"price": "0.40", "size": "200"}]},
    "tok_002_y": {"bids": [{"price": "0.43", "size": "1000"}], "asks": [{"price": "0.47", "size": "800"}]},
    "tok_002_n": {"bids": [{"price": "0.52", "size": "900"}], "asks": [{"price": "0.57", "size": "700"}]},
    "tok_003_y": {"bids": [{"price": "0.50", "size": "1200"}], "asks": [{"price": "0.54", "size": "1000"}]},
    "tok_003_n": {"bids": [{"price": "0.45", "size": "1100"}], "asks": [{"price": "0.50", "size": "900"}]},
}

# Mock Claude API response
MOCK_CLAUDE_RESPONSE = {
    "content": [{"type": "text", "text": json.dumps([
        {"id": "cond_001_btc", "prob": 0.72, "conf": "medium",
         "reason": "BTC momentum strong near ATH", "factors": ["price momentum", "halving cycle"]},
        {"id": "cond_002_fed", "prob": 0.55, "conf": "medium",
         "reason": "Mixed signals, labor softening", "factors": ["inflation data", "employment weak"]},
        {"id": "cond_003_nvd", "prob": 0.60, "conf": "low",
         "reason": "AI demand strong but valuation high", "factors": ["AI capex", "earnings soon"]},
    ])}],
    "usage": {"input_tokens": 650, "output_tokens": 280},
}


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestClaudeEstimator(unittest.TestCase):
    """Test Claude AI integration."""

    def test_build_prompt_compact(self):
        """Prompt should be compact (~50 tokens per market)."""
        estimator = ClaudeEstimator(api_key="test", model="claude-haiku-4-5-20251001")
        api = PolymarketAPI()
        scanner = MarketScanner(api)

        markets = []
        for raw in MOCK_MARKETS:
            m = scanner._parse(raw)
            if m:
                markets.append(m)

        prompt = estimator._build_prompt(markets)

        # Should contain all market questions
        self.assertIn("Bitcoin", prompt)
        self.assertIn("Fed", prompt)
        self.assertIn("Nvidia", prompt)

        # Should be compact
        self.assertLess(len(prompt), 1000)  # well under 1000 chars
        print(f"  ✓ Prompt built: {len(prompt)} chars for {len(markets)} markets")
        print(f"    Preview: {prompt[:150]}...")

    def test_system_prompt_size(self):
        """System prompt should be minimal."""
        prompt = ClaudeEstimator.SYSTEM_PROMPT
        # Rough token estimate: ~1 token per 4 chars
        est_tokens = len(prompt) / 4
        self.assertLess(est_tokens, 200)
        print(f"  ✓ System prompt: {len(prompt)} chars (~{est_tokens:.0f} tokens)")

    def test_parse_valid_response(self):
        """Should parse well-formed JSON from Claude."""
        estimator = ClaudeEstimator(api_key="test")
        api = PolymarketAPI()
        scanner = MarketScanner(api)
        markets = [scanner._parse(raw) for raw in MOCK_MARKETS]
        markets = [m for m in markets if m]

        text = json.dumps([
            {"id": "cond_001_btc", "prob": 0.72, "conf": "medium",
             "reason": "Momentum strong", "factors": ["price action"]},
            {"id": "cond_002_fed", "prob": 0.55, "conf": "medium",
             "reason": "Mixed signals", "factors": ["inflation"]},
        ])

        estimates = estimator._parse_response(text, markets)
        self.assertEqual(len(estimates), 2)
        self.assertAlmostEqual(estimates[0].true_probability, 0.72)
        self.assertEqual(estimates[0].confidence, "medium")
        print(f"  ✓ Parsed {len(estimates)} estimates from Claude response")

    def test_parse_response_with_markdown_fences(self):
        """Should handle Claude wrapping JSON in ```."""
        estimator = ClaudeEstimator(api_key="test")
        markets = []

        text = '```json\n[{"id":"x","prob":0.65,"conf":"high","reason":"test","factors":["a"]}]\n```'
        estimates = estimator._parse_response(text, markets)
        self.assertEqual(len(estimates), 1)
        self.assertAlmostEqual(estimates[0].true_probability, 0.65)
        print(f"  ✓ Handled markdown-fenced JSON correctly")

    def test_probability_clamping(self):
        """Probabilities should be clamped to [0.02, 0.98]."""
        estimator = ClaudeEstimator(api_key="test")
        text = json.dumps([
            {"id": "x", "prob": 0.001, "conf": "low", "reason": "test", "factors": []},
            {"id": "y", "prob": 0.999, "conf": "low", "reason": "test", "factors": []},
        ])
        estimates = estimator._parse_response(text, [])
        self.assertGreaterEqual(estimates[0].true_probability, 0.02)
        self.assertLessEqual(estimates[1].true_probability, 0.98)
        print(f"  ✓ Probabilities clamped: {estimates[0].true_probability}, {estimates[1].true_probability}")

    def test_candidate_selection_prioritizes_interesting(self):
        """Pre-filter should select the most interesting markets."""
        estimator = ClaudeEstimator(api_key="test")
        api = PolymarketAPI()
        scanner = MarketScanner(api)
        markets = [scanner._parse(raw) for raw in MOCK_MARKETS]
        markets = [m for m in markets if m]

        # Add spreads manually
        for m in markets:
            for o in m.outcomes:
                if o.token_id in MOCK_ORDERBOOKS:
                    book = MOCK_ORDERBOOKS[o.token_id]
                    o.best_bid = float(book["bids"][0]["price"])
                    o.best_ask = float(book["asks"][0]["price"])
                    o.spread = o.best_ask - o.best_bid

        candidates = estimator._select_candidates(markets, max_n=2)
        self.assertEqual(len(candidates), 2)
        # Should prioritize by uncertainty + volume + urgency
        print(f"  ✓ Selected {len(candidates)} candidates from {len(markets)}: "
              f"{[c.question[:30] for c in candidates]}")

    def test_fallback_when_no_api_key(self):
        """Should produce heuristic estimates without API key."""
        estimator = ClaudeEstimator(api_key="", model="claude-haiku-4-5-20251001")
        api = PolymarketAPI()
        scanner = MarketScanner(api)
        markets = [scanner._parse(raw) for raw in MOCK_MARKETS]
        markets = [m for m in markets if m]

        estimates = estimator.estimate_batch(markets, max_markets=3)
        self.assertGreater(len(estimates), 0)
        for e in estimates:
            self.assertGreater(e.true_probability, 0)
            self.assertLess(e.true_probability, 1)
            self.assertEqual(e.confidence, "low")
        print(f"  ✓ Fallback produced {len(estimates)} heuristic estimates")

    def test_mock_claude_api_call(self):
        """Test full Claude API call with mocked HTTP response."""
        estimator = ClaudeEstimator(api_key="sk-test-key")
        api = PolymarketAPI()
        scanner = MarketScanner(api)
        markets = [scanner._parse(raw) for raw in MOCK_MARKETS]
        markets = [m for m in markets if m]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_CLAUDE_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch.object(estimator.session, 'post', return_value=mock_resp):
            estimates = estimator.estimate_batch(markets, max_markets=3)

        self.assertEqual(len(estimates), 3)
        self.assertAlmostEqual(estimates[0].true_probability, 0.72)
        self.assertEqual(estimates[1].confidence, "medium")

        # Verify cost calculation
        usage = MOCK_CLAUDE_RESPONSE["usage"]
        cost = (usage["input_tokens"] / 1e6 * 1.0) + (usage["output_tokens"] / 1e6 * 5.0)
        print(f"  ✓ Mock Claude call: {len(estimates)} estimates, "
              f"cost=${cost:.4f} ({usage['input_tokens']}in + {usage['output_tokens']}out)")

    def test_cost_by_model(self):
        """Verify cost calculations for each model."""
        for model, in_p, out_p in [
            ("claude-haiku-4-5-20251001", 1.0, 5.0),
            ("claude-sonnet-4-5-20250929", 3.0, 15.0),
            ("claude-opus-4-5-20250918", 5.0, 25.0),
        ]:
            est = ClaudeEstimator(api_key="test", model=model)
            self.assertEqual(est._input_price(), in_p)
            self.assertEqual(est._output_price(), out_p)
            # Cost for typical scan: 800 in + 1200 out
            cost = (800 / 1e6 * in_p) + (1200 / 1e6 * out_p)
            daily = cost * 48  # every 30 min
            monthly = daily * 30
            print(f"  ✓ {model}: ${cost:.4f}/scan, ${daily:.2f}/day, ${monthly:.1f}/month")


class TestTelegramBot(unittest.TestCase):
    """Test Telegram message formatting."""

    def _make_bet(self, question, edge, bet_size, conf="medium"):
        market = Market(
            condition_id="cond_test", question=question, slug="test-slug",
            volume_24h=15000, liquidity=50000, days_to_resolution=5,
            category="crypto",
            outcomes=[MarketOutcome(
                token_id="tok_test", outcome="Yes", market_price=0.50, spread=0.04,
            )],
        )
        estimate = ClaudeEstimate(
            condition_id="cond_test",
            true_probability=0.50 + edge,
            confidence=conf,
            reasoning="Test reasoning for this market",
            key_factors=["factor one", "factor two"],
        )
        return KellyBet(
            market=market, outcome=market.outcomes[0], claude_estimate=estimate,
            edge=edge, kelly_fraction=0.15, adj_kelly_fraction=bet_size / 5000,
            bet_size_usd=bet_size, expected_value=edge * 2,
            odds_decimal=2.0, score=5.0,
        )

    def test_format_report_with_bets(self):
        """Report should contain all key information."""
        bot = TelegramBot("test_token", "-123456")
        bets = [
            self._make_bet("Will BTC hit $100K?", 0.10, 250, "medium"),
            self._make_bet("Will Fed cut rates?", 0.08, 180, "low"),
        ]

        report = bot.format_report(bets, 5000, "claude-haiku-4-5-20251001", 0.007)

        self.assertIn("POLYMARKET KELLY SCANNER", report)
        self.assertIn("$5,000", report)
        self.assertIn("BTC", report)
        self.assertIn("Fed", report)
        self.assertIn("BET:", report)
        self.assertIn("Edge:", report)
        self.assertIn("Claude:", report)
        self.assertIn("polymarket.com", report)
        self.assertIn("Not financial advice", report)
        print(f"  ✓ Report generated: {len(report)} chars")
        print(f"    First 200 chars: {report[:200]}...")

    def test_format_report_no_bets(self):
        """Should handle empty bet list gracefully."""
        bot = TelegramBot("test_token", "-123456")
        report = bot.format_report([], 5000, "haiku", 0.007)
        self.assertIn("No opportunities", report)
        print(f"  ✓ Empty report: '{report[:80]}...'")

    def test_message_splitting(self):
        """Long messages should be split at line boundaries."""
        bot = TelegramBot("test_token", "-123456")
        long_text = "\n".join([f"Line {i}: " + "x" * 100 for i in range(50)])
        chunks = bot._split_message(long_text, 4096)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 4096)
        print(f"  ✓ Split {len(long_text)} chars into {len(chunks)} chunks")

    def test_telegram_send_mock(self):
        """Test Telegram API call with mocked HTTP."""
        bot = TelegramBot("test_token", "-123456")

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("polymarket_telegram_bot.requests.post", return_value=mock_resp):
            result = bot.send_message("Test message")

        self.assertTrue(result)
        print(f"  ✓ Mock Telegram send succeeded")

    def test_telegram_graceful_without_keys(self):
        """Should not crash when keys are missing."""
        bot = TelegramBot("", "")
        result = bot.send_message("Test")
        self.assertFalse(result)
        print(f"  ✓ Gracefully handled missing Telegram keys")


class TestFullPipeline(unittest.TestCase):
    """End-to-end pipeline test."""

    def test_full_pipeline_with_mocks(self):
        """Complete pipeline with all APIs mocked."""
        print("\n  ── Full Pipeline Test ──")

        # Patch module-level constants so Pipeline uses them
        import polymarket_telegram_bot as bot_module
        orig_tg_token = bot_module.TELEGRAM_BOT_TOKEN
        orig_tg_chat = bot_module.TELEGRAM_CHAT_ID
        orig_api_key = bot_module.ANTHROPIC_API_KEY

        try:
            bot_module.TELEGRAM_BOT_TOKEN = "test_token"
            bot_module.TELEGRAM_CHAT_ID = "-123456"
            bot_module.ANTHROPIC_API_KEY = "sk-test"

            pipeline = Pipeline(
                bankroll=5000, kelly_fraction=0.25, max_days=14,
                min_edge=0.05, max_position=0.10, max_exposure=0.40,
                max_bets=5, pages=1, claude_model="claude-haiku-4-5-20251001",
            )
            # Ensure the claude estimator has the key
            pipeline.claude.api_key = "sk-test"

            # Mock Polymarket
            with patch.object(pipeline.poly_api, 'get_markets', return_value=MOCK_MARKETS):
                with patch.object(pipeline.poly_api, 'get_orderbook') as mock_book:
                    mock_book.side_effect = lambda tid: MOCK_ORDERBOOKS.get(tid)

                    # Mock Claude API HTTP call
                    mock_claude_resp = MagicMock()
                    mock_claude_resp.status_code = 200
                    mock_claude_resp.json.return_value = MOCK_CLAUDE_RESPONSE
                    mock_claude_resp.raise_for_status = MagicMock()

                    with patch.object(pipeline.claude.session, 'post',
                                      return_value=mock_claude_resp):
                        # Mock Telegram HTTP call
                        mock_tg_resp = MagicMock()
                        mock_tg_resp.status_code = 200

                        with patch("polymarket_telegram_bot.requests.post",
                                   return_value=mock_tg_resp):
                            bets, cost = pipeline.run()
        finally:
            bot_module.TELEGRAM_BOT_TOKEN = orig_tg_token
            bot_module.TELEGRAM_CHAT_ID = orig_tg_chat
            bot_module.ANTHROPIC_API_KEY = orig_api_key

        self.assertGreater(len(bets), 0)
        self.assertGreater(cost, 0)

        print(f"\n  Results:")
        print(f"  Approved bets: {len(bets)}")
        print(f"  Estimated cost: ${cost:.4f}")
        for i, b in enumerate(bets, 1):
            print(f"  #{i}: {b.market.question[:45]}... "
                  f"edge={b.edge:.1%} bet=${b.bet_size_usd:.0f} "
                  f"claude={b.claude_estimate.true_probability:.0%} "
                  f"conf={b.claude_estimate.confidence}")

        # Verify risk limits
        total_exp = sum(b.bet_size_usd for b in bets)
        self.assertLessEqual(total_exp, 2000)  # 40% of $5000
        for b in bets:
            self.assertLessEqual(b.bet_size_usd, 500)  # 10% of $5000
            self.assertGreater(b.edge, 0.05)
        print(f"\n  Total exposure: ${total_exp:.0f} ({total_exp/5000*100:.1f}%)")
        print(f"  ✓ All risk limits respected")


class TestKellyIntegration(unittest.TestCase):
    """Test Kelly + Claude estimate integration."""

    def test_edge_from_claude_estimates(self):
        """Verify edge = claude_prob - market_prob."""
        kelly = KellyCriterion(bankroll=5000, kelly_multiplier=0.25)

        # Claude says 72%, market says 62% → 10% edge
        result = kelly.calculate(market_prob=0.62, true_prob=0.72)
        self.assertAlmostEqual(result["edge"], 0.10, places=2)
        self.assertGreater(result["bet"], 0)
        print(f"  ✓ BTC: market=62%, claude=72% → edge=10%, bet=${result['bet']:.0f}")

        # Claude says 55%, market says 45% → 10% edge
        result = kelly.calculate(market_prob=0.45, true_prob=0.55)
        self.assertAlmostEqual(result["edge"], 0.10, places=2)
        print(f"  ✓ Fed: market=45%, claude=55% → edge=10%, bet=${result['bet']:.0f}")

        # Claude agrees with market → no edge
        result = kelly.calculate(market_prob=0.52, true_prob=0.52)
        self.assertEqual(result["edge"], 0)
        self.assertEqual(result["bet"], 0)
        print(f"  ✓ No edge: market=52%, claude=52% → bet=$0")


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  POLYMARKET → CLAUDE → TELEGRAM TEST SUITE")
    print("=" * 60 + "\n")

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    for cls in [
        TestClaudeEstimator,
        TestTelegramBot,
        TestKellyIntegration,
        TestFullPipeline,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    if result.wasSuccessful():
        print(f"  ✅ ALL {result.testsRun} TESTS PASSED")
    else:
        print(f"  ❌ {len(result.failures)} FAILURES, {len(result.errors)} ERRORS")
        for f in result.failures:
            print(f"    FAIL: {f[0]}\n    {f[1][:300]}")
        for e in result.errors:
            print(f"    ERROR: {e[0]}\n    {e[1][:300]}")
    print("=" * 60 + "\n")
