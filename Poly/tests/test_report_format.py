"""Golden-file regression for polymarket_scraper.render_report.

The first pytest run scaffolds the .txt files and fails with a hint to
inspect them. The second run compares byte-equal against the inspected
goldens. Spec §10.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Make Poly importable as if running from the Projetos root.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Poly"))

from polymarket_scraper import render_report  # noqa: E402

GOLDEN = Path(__file__).parent / "golden"


# ── minimal fixture types matching the real KellyBet / Market shape ──
@dataclass
class _MockMarket:
    question: str
    days_to_resolution: float
    volume_24h: float
    liquidity: float


@dataclass
class _MockOutcome:
    market_price: float


@dataclass
class _MockKellyBet:
    market: _MockMarket
    outcome: _MockOutcome
    bet_size_usd: float
    edge: float


def _mock_bet(*, q: str, days: float, vol: float, liq: float,
              price: float, size: float, edge: float) -> _MockKellyBet:
    return _MockKellyBet(
        market=_MockMarket(question=q, days_to_resolution=days,
                           volume_24h=vol, liquidity=liq),
        outcome=_MockOutcome(market_price=price),
        bet_size_usd=size,
        edge=edge,
    )


def _summary(total_exposure: float, avg_edge: float,
             expected_profit: float, worst_case: float,
             exposure_pct: float) -> dict:
    return {
        "total_exposure": total_exposure,
        "avg_edge": avg_edge,
        "expected_profit": expected_profit,
        "worst_case": worst_case,
        "exposure_pct": exposure_pct,
    }


_ASOF = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)


def _happy_fixture():
    return dict(
        bankroll=1000.0,
        settings="edge ≥3%, ≤30d",
        n_screened=10,
        approved_bets=[
            _mock_bet(q="Italy wins Eurovision 2026", days=6,
                      vol=63_000, liq=78_000, price=0.031,
                      size=15.31, edge=0.060),
            _mock_bet(q="Bitcoin dips to $60k in May", days=23,
                      vol=79_000, liq=121_000, price=0.026,
                      size=12.84, edge=0.050),
            _mock_bet(q="Aliens confirmed by May 31", days=22,
                      vol=905_000, liq=156_000, price=0.031,
                      size=12.91, edge=0.050),
        ] + [
            _mock_bet(q=f"Tail market #{i}", days=10 + i,
                      vol=50_000, liq=50_000, price=0.04,
                      size=10.0, edge=0.030 + i * 0.002)
            for i in range(7)
        ],
        summary=_summary(total_exposure=41.06, avg_edge=0.053,
                         expected_profit=8.20, worst_case=-41.06,
                         exposure_pct=4.1),
        asof=_ASOF,
    )


def _single_fixture():
    return dict(
        bankroll=1000.0,
        settings="edge ≥3%, ≤30d",
        n_screened=10,
        approved_bets=[
            _mock_bet(q="Italy wins Eurovision 2026", days=6,
                      vol=63_000, liq=78_000, price=0.031,
                      size=15.31, edge=0.060),
        ],
        summary=_summary(total_exposure=15.31, avg_edge=0.060,
                         expected_profit=2.50, worst_case=-15.31,
                         exposure_pct=1.5),
        asof=_ASOF,
    )


def _empty_fixture():
    return dict(
        bankroll=1000.0,
        settings="edge ≥3%, ≤30d",
        n_screened=300,
        approved_bets=[],
        summary=_summary(total_exposure=0.0, avg_edge=0.0,
                         expected_profit=0.0, worst_case=0.0,
                         exposure_pct=0.0),
        asof=_ASOF,
    )


# ── golden tests ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "fixture_factory,name",
    [
        (_happy_fixture, "happy_path.txt"),
        (_single_fixture, "single_bet.txt"),
        (_empty_fixture, "empty.txt"),
    ],
)
def test_golden_match(fixture_factory, name: str) -> None:
    text = render_report(**fixture_factory())
    path = GOLDEN / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        pytest.fail(f"Golden file {name} did not exist; wrote it. Inspect and rerun.")
    assert text == path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "fixture_factory",
    [_happy_fixture, _single_fixture, _empty_fixture],
)
def test_under_3500_chars(fixture_factory) -> None:
    text = render_report(**fixture_factory())
    assert len(text.encode("utf-8")) < 3500


@pytest.mark.parametrize(
    "fixture_factory",
    [_happy_fixture, _single_fixture, _empty_fixture],
)
def test_no_html_no_markdown(fixture_factory) -> None:
    text = render_report(**fixture_factory())
    assert "<" not in text
    assert "**" not in text
    assert "__" not in text
    # MarkdownV2 special chars that would need escaping if we used MV2:
    # `_`, `*`, `[`, `(`, `~`, `>`, `#`, `+`, `-`, `=`, `|`, `{`, `.`, `!`
    # We don't enforce all of these — only assert the markup-style ones that
    # would actually break under the wrong parse_mode.
