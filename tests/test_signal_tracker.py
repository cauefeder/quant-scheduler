"""Tests for signal_tracker.py — schema, pragmas, and migration setup."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def test_init_db_creates_signals_table(fresh_tracker):
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    conn.close()
    assert "signals" in tables
    assert "daily_metrics" in tables
    assert "schema_version" in tables


def test_init_db_sets_wal_mode(fresh_tracker):
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_signals_table_has_entry_price_column(fresh_tracker):
    """Spec B3: entry_price must exist for direction-aware PnL."""
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    conn.close()
    expected = {
        "id", "system", "signal_type", "market_slug", "ticker", "condition_id",
        "direction", "estimated_edge", "estimated_prob", "market_price",
        "entry_price", "kelly_bet", "kelly_fraction", "signal_tier",
        "raw_features", "cost_estimate", "net_edge", "created_at",
        "resolved_at", "outcome", "actual_pnl", "resolution_data",
    }
    assert expected.issubset(cols), f"Missing columns: {expected - cols}"


def test_schema_version_starts_at_1(fresh_tracker):
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    conn.close()
    assert version == 1
