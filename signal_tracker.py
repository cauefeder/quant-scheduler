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


# ── log_signal ───────────────────────────────────────────────────────────────

_VALID_DIRECTIONS = {"YES", "NO", "LONG", "SHORT", "STRADDLE"}
_VALID_TIMEFRAMES = {"1H", "1D", "1W"}


def _compute_entry_price(direction: str, market_price: float) -> float:
    """Direction-aware entry cost. PM NO pays (1 - market_price); everything else pays market_price."""
    if direction == "NO":
        return 1.0 - market_price
    return market_price


def log_signal(
    system: str,
    signal_type: str,
    direction: str,
    estimated_edge: float,
    market_price: float,
    *,
    market_slug: str | None = None,
    ticker: str | None = None,
    condition_id: str | None = None,
    estimated_prob: float | None = None,
    kelly_bet: float | None = None,
    kelly_fraction: float | None = None,
    signal_tier: str | None = None,
    raw_features: dict | None = None,
    cost_estimate: float = 0.0,
) -> int:
    """Log a signal. Returns the signal ID (or the existing ID on dedup hit)."""
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {_VALID_DIRECTIONS}, got {direction!r}")

    if signal_type == "polymarket" and not market_slug:
        raise ValueError("polymarket signals require market_slug")

    if signal_type == "straddle":
        if not raw_features or "straddle_cost" not in raw_features:
            raise ValueError("straddle signals require raw_features['straddle_cost']")

    if signal_type == "trend_direction":
        if not raw_features or "timeframe" not in raw_features:
            raise ValueError("trend_direction signals require raw_features['timeframe']")
        if raw_features["timeframe"] not in _VALID_TIMEFRAMES:
            raise ValueError(
                f"raw_features['timeframe'] must be one of {_VALID_TIMEFRAMES}"
            )

    entry_price = _compute_entry_price(direction, market_price)
    net_edge = max(0.0, estimated_edge - cost_estimate)
    created_at = datetime.now(timezone.utc).isoformat()
    raw_json = json.dumps(raw_features) if raw_features else None

    with _connect() as conn:
        # Deduplication: same system + slug/ticker + direction on the same UTC date
        dedup_key = market_slug or ticker or ""
        existing = conn.execute(
            """
            SELECT id FROM signals
            WHERE system = ?
              AND COALESCE(market_slug, ticker, '') = ?
              AND direction = ?
              AND date(created_at) = date(?)
            LIMIT 1
            """,
            (system, dedup_key, direction, created_at),
        ).fetchone()
        if existing:
            return int(existing[0])

        cur = conn.execute(
            """
            INSERT INTO signals (
                system, signal_type, market_slug, ticker, condition_id,
                direction, estimated_edge, estimated_prob, market_price,
                entry_price, kelly_bet, kelly_fraction, signal_tier,
                raw_features, cost_estimate, net_edge, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system, signal_type, market_slug, ticker, condition_id,
                direction, estimated_edge, estimated_prob, market_price,
                entry_price, kelly_bet, kelly_fraction, signal_tier,
                raw_json, cost_estimate, net_edge, created_at,
            ),
        )
        return int(cur.lastrowid)


def get_signal(signal_id: int) -> dict[str, Any] | None:
    """Fetch a single signal row as a dict (for tests and audit)."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        return dict(row) if row else None


# ── Polymarket resolution ────────────────────────────────────────────────────

import urllib.request
import urllib.parse

GAMMA_API = "https://gamma-api.polymarket.com/markets"
_RESOLVE_BATCH_LIMIT = 50  # max markets queried per resolver run


def _fetch_gamma_market(slug: str) -> list[dict] | None:
    """Query Gamma for a single market by slug. Returns the JSON list, or None on failure."""
    qs = urllib.parse.urlencode({"slug": slug})
    req = urllib.request.Request(
        f"{GAMMA_API}?{qs}",
        headers={"User-Agent": "signal_tracker/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001 — log and skip this market
        log.debug("Gamma fetch failed for %s: %s", slug, exc)
        return None


def _parse_resolution(market: dict) -> str | None:
    """Return 'YES', 'NO', or None if the market isn't resolved yet."""
    if not market.get("closed"):
        return None
    if not market.get("resolutionSource"):
        return None
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
        yes_price = float(prices[0])
    except (ValueError, IndexError, TypeError):
        return None
    if yes_price >= 0.99:
        return "YES"
    if yes_price <= 0.01:
        return "NO"
    return None  # ambiguous — leave unresolved


def resolve_polymarket_signals() -> int:
    """Resolve all unresolved Polymarket signals via Gamma API. Returns count resolved."""
    import time

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, market_slug, direction, market_price, entry_price, kelly_bet
            FROM signals
            WHERE outcome IS NULL
              AND signal_type = 'polymarket'
              AND market_slug IS NOT NULL
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (_RESOLVE_BATCH_LIMIT,),
        ).fetchall()

    resolved_count = 0
    for row in rows:
        market_data = _fetch_gamma_market(row["market_slug"])
        if not market_data:
            time.sleep(0.2)
            continue
        market = market_data[0] if isinstance(market_data, list) and market_data else None
        if not market:
            time.sleep(0.2)
            continue

        resolution = _parse_resolution(market)
        if not resolution:
            time.sleep(0.2)
            continue

        is_win = resolution == row["direction"]
        entry = row["entry_price"] or row["market_price"] or 0.5
        bet = row["kelly_bet"] or 0.0
        if is_win:
            outcome = "WIN"
            pnl = bet * (1.0 / entry - 1.0)
        else:
            outcome = "LOSS"
            pnl = -bet

        with _connect() as conn:
            conn.execute(
                """
                UPDATE signals
                SET outcome = ?, actual_pnl = ?, resolved_at = ?, resolution_data = ?
                WHERE id = ?
                """,
                (
                    outcome,
                    pnl,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps({"yes_price": resolution, "source": "gamma-api"}),
                    row["id"],
                ),
            )
        resolved_count += 1
        time.sleep(0.2)

    return resolved_count
