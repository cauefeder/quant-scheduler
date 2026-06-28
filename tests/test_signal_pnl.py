"""Tests for lib.signal_pnl — per-system realised-P&L aggregation.

Builds a synthetic SQLite DB matching signal_tracker's schema and asserts
the aggregator returns the right numbers per system.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from lib.signal_pnl import realized_pnl_by_system


def _make_synthetic_db(path: Path, rows: list[dict]) -> None:
    """Build the minimal signals table matching production schema."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY,
            system TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            market_slug TEXT,
            ticker TEXT,
            direction TEXT NOT NULL,
            estimated_edge REAL,
            market_price REAL,
            entry_price REAL,
            kelly_bet REAL,
            raw_features TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            outcome TEXT,
            actual_pnl REAL
        )
        """,
    )
    for r in rows:
        cols = ",".join(r.keys())
        placeholders = ",".join("?" for _ in r)
        conn.execute(f"INSERT INTO signals ({cols}) VALUES ({placeholders})", tuple(r.values()))
    conn.commit()
    conn.close()


def _row(**kw) -> dict:
    base = {
        "system": "alphafeed",
        "signal_type": "polymarket",
        "direction": "YES",
        "estimated_edge": 0.05,
        "market_price": 0.40,
        "entry_price": 0.40,
        "kelly_bet": 10.0,
        "created_at": "2026-04-01T00:00:00+00:00",
        "resolved_at": "2026-04-15T00:00:00+00:00",
        "outcome": "WIN",
        "actual_pnl": 15.0,
    }
    base.update(kw)
    return base


def test_realized_pnl_groups_by_system(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [
        _row(system="alphafeed", outcome="WIN", actual_pnl=12.0),
        _row(system="alphafeed", outcome="LOSS", actual_pnl=-10.0),
        _row(system="poly", outcome="WIN", actual_pnl=8.0),
    ])
    df = realized_pnl_by_system(db)
    assert set(df["system"]) == {"alphafeed", "poly"}


def test_realized_pnl_win_rate_computation(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [
        _row(system="alphafeed", outcome="WIN"),
        _row(system="alphafeed", outcome="WIN"),
        _row(system="alphafeed", outcome="LOSS"),
        _row(system="alphafeed", outcome="LOSS"),
    ])
    df = realized_pnl_by_system(db)
    alphafeed = df[df["system"] == "alphafeed"].iloc[0]
    assert alphafeed["resolved"] == 4
    assert alphafeed["win_rate"] == pytest.approx(0.5)


def test_realized_pnl_total_and_avg_pnl(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [
        _row(system="poly", actual_pnl=20.0, outcome="WIN"),
        _row(system="poly", actual_pnl=-5.0, outcome="LOSS"),
        _row(system="poly", actual_pnl=15.0, outcome="WIN"),
    ])
    df = realized_pnl_by_system(db)
    row = df[df["system"] == "poly"].iloc[0]
    assert row["total_pnl"] == pytest.approx(30.0)
    assert row["avg_pnl_per_bet"] == pytest.approx(10.0)


def test_realized_pnl_excludes_unresolved(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [
        _row(system="alphafeed", outcome="WIN", actual_pnl=10.0),
        _row(system="alphafeed", outcome=None, actual_pnl=None),  # unresolved
    ])
    df = realized_pnl_by_system(db)
    af = df[df["system"] == "alphafeed"].iloc[0]
    assert af["resolved"] == 1


def test_realized_pnl_sharpe_zero_for_constant(tmp_path: Path) -> None:
    """All-equal P&L → std=0 → sharpe set to 0 (degenerate)."""
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [
        _row(system="poly", actual_pnl=5.0, outcome="WIN"),
        _row(system="poly", actual_pnl=5.0, outcome="WIN"),
    ])
    df = realized_pnl_by_system(db)
    assert df[df["system"] == "poly"].iloc[0]["sharpe_approx"] == 0.0


def test_realized_pnl_includes_total_signals_count(tmp_path: Path) -> None:
    """Should report both resolved and total (resolved+unresolved) per system."""
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [
        _row(system="alphafeed", outcome="WIN"),
        _row(system="alphafeed", outcome=None, actual_pnl=None),
        _row(system="alphafeed", outcome=None, actual_pnl=None),
    ])
    df = realized_pnl_by_system(db)
    af = df[df["system"] == "alphafeed"].iloc[0]
    assert af["total_signals"] == 3
    assert af["resolved"] == 1


def test_realized_pnl_empty_db_returns_empty_df(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [])
    df = realized_pnl_by_system(db)
    assert df.empty


def test_realized_pnl_excludes_no_match_outcomes(tmp_path: Path) -> None:
    """NO_MATCH markers (slug rot) must not inflate `resolved` or win rate."""
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [
        _row(system="alphafeed", outcome="WIN", actual_pnl=10.0),
        _row(system="alphafeed", outcome="NO_MATCH", actual_pnl=None),
        _row(system="alphafeed", outcome="NO_MATCH", actual_pnl=None),
    ])
    df = realized_pnl_by_system(db)
    af = df[df["system"] == "alphafeed"].iloc[0]
    assert af["total_signals"] == 3
    assert af["resolved"] == 1
    assert af["win_rate"] == pytest.approx(1.0)


# ── realized_pnl_by_segment ───────────────────────────────────────────────────


def test_segment_groups_by_signal_type_and_ticker(tmp_path: Path) -> None:
    from lib.signal_pnl import realized_pnl_by_segment
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [
        _row(system="modeltelegra", signal_type="trend_direction",
             ticker="BTC-USD", outcome="WIN", actual_pnl=5.0),
        _row(system="modeltelegra", signal_type="trend_direction",
             ticker="BTC-USD", outcome="LOSS", actual_pnl=-3.0),
        _row(system="modeltelegra", signal_type="trend_direction",
             ticker="GC=F", outcome="WIN", actual_pnl=8.0),
        _row(system="modeltelegra", signal_type="straddle",
             ticker="BTC-USD", outcome="LOSS", actual_pnl=-10.0),
    ])
    df = realized_pnl_by_segment(db, system="modeltelegra")
    assert len(df) == 3
    btc_trend = df[(df["signal_type"] == "trend_direction")
                   & (df["ticker"] == "BTC-USD")].iloc[0]
    assert btc_trend["resolved"] == 2
    assert btc_trend["win_rate"] == pytest.approx(0.5)
    assert btc_trend["total_pnl"] == pytest.approx(2.0)


def test_segment_extracts_timeframe_from_raw_features(tmp_path: Path) -> None:
    from lib.signal_pnl import realized_pnl_by_segment
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [
        {**_row(system="modeltelegra", signal_type="trend_direction",
                ticker="BTC-USD", outcome="WIN", actual_pnl=5.0),
         "raw_features": '{"timeframe": "1H"}'},
        {**_row(system="modeltelegra", signal_type="trend_direction",
                ticker="BTC-USD", outcome="LOSS", actual_pnl=-3.0),
         "raw_features": '{"timeframe": "1D"}'},
    ])
    df = realized_pnl_by_segment(
        db, system="modeltelegra",
        segment_keys=("signal_type", "ticker", "timeframe"),
    )
    assert set(df["timeframe"]) == {"1H", "1D"}
    h1 = df[df["timeframe"] == "1H"].iloc[0]
    assert h1["resolved"] == 1
    assert h1["win_rate"] == pytest.approx(1.0)


def test_segment_respects_min_resolved_filter(tmp_path: Path) -> None:
    from lib.signal_pnl import realized_pnl_by_segment
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [
        _row(system="modeltelegra", signal_type="trend_direction",
             ticker="BTC-USD", outcome="WIN", actual_pnl=5.0),
        _row(system="modeltelegra", signal_type="trend_direction",
             ticker="GC=F", outcome="WIN", actual_pnl=8.0),
        _row(system="modeltelegra", signal_type="trend_direction",
             ticker="GC=F", outcome="LOSS", actual_pnl=-3.0),
    ])
    df = realized_pnl_by_segment(db, system="modeltelegra", min_resolved=2)
    # BTC has only 1 resolved → filtered out
    assert set(df["ticker"]) == {"GC=F"}


def test_segment_empty_db_returns_empty(tmp_path: Path) -> None:
    from lib.signal_pnl import realized_pnl_by_segment
    db = tmp_path / "t.db"
    _make_synthetic_db(db, [])
    df = realized_pnl_by_segment(db, system="modeltelegra")
    assert df.empty
