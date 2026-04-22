"""
signal_tracker.py — Cross-system signal logging and resolution.

Design: single SQLite database at the monorepo root. WAL mode lets the
scheduler's parallel subprocesses write concurrently. Resolution is passive —
runs from scheduler.py at end of run_all().

See docs/superpowers/specs/2026-04-20-phase1-signal-tracking-design.md for the
full design rationale.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("signal_tracker")

DB_PATH: Path = Path(__file__).resolve().parent / "signal_tracker.db"

# ── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system          TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    market_slug     TEXT,
    ticker          TEXT,
    condition_id    TEXT,
    direction       TEXT NOT NULL,
    estimated_edge  REAL NOT NULL,
    estimated_prob  REAL,
    market_price    REAL,
    entry_price     REAL,
    kelly_bet       REAL,
    kelly_fraction  REAL,
    signal_tier     TEXT,
    raw_features    TEXT,
    cost_estimate   REAL,
    net_edge        REAL,
    created_at      TEXT NOT NULL,
    resolved_at     TEXT,
    outcome         TEXT,
    actual_pnl      REAL,
    resolution_data TEXT
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    system          TEXT NOT NULL,
    signals_logged  INTEGER DEFAULT 0,
    signals_resolved INTEGER DEFAULT 0,
    win_rate        REAL,
    avg_edge        REAL,
    avg_net_edge    REAL,
    total_pnl       REAL,
    sharpe_approx   REAL,
    UNIQUE(date, system)
);

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_system ON signals(system);
CREATE INDEX IF NOT EXISTS idx_signals_slug ON signals(market_slug);
CREATE INDEX IF NOT EXISTS idx_signals_unresolved ON signals(outcome) WHERE outcome IS NULL;
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
"""

_MIGRATIONS: list[tuple[int, str]] = [
    # (version, ddl) — append future migrations here
]


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    _apply_pragmas(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables (idempotent), apply pragmas, run pending migrations."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA_DDL)
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (1, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        cur = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        current = cur.fetchone()[0]
        for version, ddl in _MIGRATIONS:
            if version > current:
                conn.executescript(ddl)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
                log.info("Applied schema migration v%d", version)
