"""Verify PolyTraders kelly.py invokes market_context.classify_prediction_market."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "PolyTraders") not in sys.path:
    sys.path.insert(0, str(ROOT / "PolyTraders"))


def _fake_position(condition_id="abc", outcome="YES", cur_price=0.35, avg_price=0.30,
                   current_value=500.0, trader_rank=1, proxy_wallet="0xAAA",
                   slug="market-x", title="Market X", end_date=None):
    p = MagicMock()
    p.condition_id = condition_id
    p.outcome = outcome
    p.cur_price = cur_price
    p.avg_price = avg_price
    p.current_value = current_value
    p.trader_rank = trader_rank
    p.proxy_wallet = proxy_wallet
    p.slug = slug
    p.title = title
    p.end_date = end_date
    p.username = f"trader_{trader_rank}"
    return p


def test_score_opportunities_calls_classify_prediction_market():
    from PolyTraders import kelly
    from PolyTraders.market_context import MarketContext, PriceStructure

    positions = [
        _fake_position(proxy_wallet=f"0x{i:03}", trader_rank=i + 1)
        for i in range(5)
    ]
    fake_ctx = MarketContext(
        structure=PriceStructure.PULLBACK, edge_mult=1.55,
        quality="ideal", note="test",
    )
    with patch.object(kelly, "classify_prediction_market", return_value=fake_ctx) as mock:
        opps = kelly.score_opportunities(positions, total_traders_checked=25, bankroll=100.0)
        assert mock.called
    assert opps and opps[0].market_structure.startswith("Pullback")
    assert opps[0].context_quality == "ideal"


def test_score_opportunities_handles_classifier_exception():
    from PolyTraders import kelly

    positions = [
        _fake_position(proxy_wallet=f"0x{i:03}", trader_rank=i + 1)
        for i in range(5)
    ]
    with patch.object(kelly, "classify_prediction_market", side_effect=Exception("network")):
        opps = kelly.score_opportunities(positions, total_traders_checked=25, bankroll=100.0)
    # Should not crash; uses default context
    assert opps
    assert opps[0].context_quality == "acceptable"
