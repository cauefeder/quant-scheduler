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


# Lazy-init state — every public entry opens via _connect(), so this guarantees
# the schema exists on first use even when a subsystem never called init_db.
_db_initialized: bool = False
_in_init: bool = False


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    global _db_initialized, _in_init
    if not _db_initialized and not _in_init:
        _in_init = True
        try:
            init_db()
        finally:
            _in_init = False
        _db_initialized = True
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
    """Query Gamma for a single market by slug. Returns the JSON list, or None on failure.

    `closed=true` is required: the /markets endpoint defaults to active markets
    only, so without it Gamma returns an empty list for any resolved market.
    """
    qs = urllib.parse.urlencode({"slug": slug, "closed": "true"})
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
    """Return 'YES', 'NO', or None if the market isn't resolved yet.

    Note: we used to also require `resolutionSource` to be truthy, but Gamma
    populates that field with the empty string for resolved markets — so the
    check was rejecting every real settlement. Trust `closed=True` plus
    outcomePrices-at-boundary instead.
    """
    if not market.get("closed"):
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
        market = market_data[0] if isinstance(market_data, list) and market_data else None

        if market is None:
            # Gamma found nothing for this slug — slug rot / archival / wrong
            # slug at log time. Mark as NO_MATCH so we don't keep re-fetching
            # it every backfill batch; aggregator filters NO_MATCH out of P&L.
            with _connect() as conn:
                conn.execute(
                    "UPDATE signals SET outcome = 'NO_MATCH', resolved_at = ?, "
                    "resolution_data = ? WHERE id = ?",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        json.dumps({"source": "gamma-api", "reason": "slug_not_found"}),
                        row["id"],
                    ),
                )
            time.sleep(0.2)
            continue

        resolution = _parse_resolution(market)
        if not resolution:
            # Market exists but not yet at boundary settlement — leave open.
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


# ── ModelTelegra resolution ──────────────────────────────────────────────────

# Bars-ahead by signal timeframe — how far in the future to look up the realised price.
_TREND_HORIZONS_HOURS = {"1H": 24, "1D": 24 * 5, "1W": 24 * 30}


def _fetch_price_at(*, ticker: str, timestamp: datetime) -> float | None:
    """Fetch the close price of `ticker` at (or nearest to) the given UTC timestamp.

    Uses yfinance hourly bars over a ±2 day window so weekend / holiday gaps
    on futures (GC=F, SI=F) and 24/7 instruments (BTC-USD) are both handled.
    Returns None when no bar exists in the window — caller treats this as
    NO_MATCH so the resolver doesn't loop on dead requests.
    """
    try:
        import pandas as pd
        import yfinance as yf
    except ImportError:
        log.warning("yfinance/pandas not installed — cannot resolve ModelTelegra signals")
        return None
    try:
        from datetime import timedelta

        start = (timestamp - timedelta(days=2)).strftime("%Y-%m-%d")
        end = (timestamp + timedelta(days=2)).strftime("%Y-%m-%d")
        hist = yf.Ticker(ticker).history(start=start, end=end, interval="1h")
        if hist.empty:
            return None
        # Normalise to UTC and pick the bar nearest to the target timestamp
        if hist.index.tz is None:
            hist.index = hist.index.tz_localize("UTC")
        else:
            hist.index = hist.index.tz_convert("UTC")
        target = pd.Timestamp(timestamp).tz_convert("UTC") if pd.Timestamp(timestamp).tzinfo else pd.Timestamp(timestamp).tz_localize("UTC")
        idx = hist.index.get_indexer([target], method="nearest")[0]
        if idx == -1:
            return None
        return float(hist["Close"].iloc[idx])
    except Exception as exc:  # noqa: BLE001
        log.debug("yfinance fetch failed for %s @ %s: %s", ticker, timestamp, exc)
        return None


# Back-compat shim — the old function name is referenced in older runbooks
# and may still be imported by ad-hoc scripts. Routes through the generic helper.
def _fetch_btc_price_at(timestamp: datetime) -> float | None:
    return _fetch_price_at(ticker="BTC-USD", timestamp=timestamp)


def resolve_modeltelegra_signals() -> int:
    """Resolve all unresolved straddle + trend signals via yfinance. Returns count resolved."""
    from datetime import timedelta

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, signal_type, direction, ticker, market_price, kelly_bet,
                   cost_estimate, raw_features, created_at
            FROM signals
            WHERE outcome IS NULL
              AND signal_type IN ('straddle', 'trend_direction')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (_RESOLVE_BATCH_LIMIT,),
        ).fetchall()

    resolved_count = 0
    for row in rows:
        created = datetime.fromisoformat(row["created_at"])
        raw = json.loads(row["raw_features"]) if row["raw_features"] else {}
        ticker = row["ticker"] or "BTC-USD"  # legacy signals defaulted to BTC

        def _mark_no_match(signal_id: int, reason: str) -> None:
            """Stamp NO_MATCH so we don't keep re-fetching dead targets."""
            with _connect() as nm_conn:
                nm_conn.execute(
                    "UPDATE signals SET outcome = 'NO_MATCH', resolved_at = ?, "
                    "resolution_data = ? WHERE id = ?",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        json.dumps({"source": "yfinance", "reason": reason}),
                        signal_id,
                    ),
                )

        if row["signal_type"] == "straddle":
            target_time = created + timedelta(hours=24)
            future_price = _fetch_price_at(ticker=ticker, timestamp=target_time)
            if future_price is None or future_price <= 0:
                _mark_no_match(row["id"], "no_price_at_target")
                continue
            move = abs(future_price - row["market_price"]) / row["market_price"]
            # straddle_cost is logged inconsistently across ModelTelegra versions:
            # some emit a fractional move (e.g. 0.04 = 4%), others emit the
            # dollar premium ($1,734.80 on a $60k BTC). Normalise to a fraction
            # by dividing by market_price when the value is clearly absolute.
            raw_cost = float(raw.get("straddle_cost", 0.04))
            if raw_cost > 1.0 and row["market_price"] > 0:
                raw_cost = raw_cost / row["market_price"]
            breakeven = raw_cost + (row["cost_estimate"] or 0.0)
            if breakeven <= 0:
                continue
            bet = row["kelly_bet"] or 0.0
            if move > breakeven:
                outcome = "WIN"
                pnl = bet * (move / breakeven - 1.0)
            else:
                outcome = "LOSS"
                pnl = max(-bet, -bet * (1.0 - move / breakeven))
            resolution_data = {"future_price": future_price, "move": move, "breakeven": breakeven}

        else:  # trend_direction
            tf = raw.get("timeframe", "1D")
            hours_ahead = _TREND_HORIZONS_HOURS.get(tf, 24)
            target_time = created + timedelta(hours=hours_ahead)
            future_price = _fetch_price_at(ticker=ticker, timestamp=target_time)
            if future_price is None or future_price <= 0:
                _mark_no_match(row["id"], "no_price_at_target")
                continue
            change = (future_price - row["market_price"]) / row["market_price"]
            went_up = change > 0
            is_win = (row["direction"] == "LONG" and went_up) or (row["direction"] == "SHORT" and not went_up)
            bet = row["kelly_bet"] or 0.0
            magnitude = bet * abs(change)
            outcome = "WIN" if is_win else "LOSS"
            pnl = magnitude if is_win else -magnitude
            resolution_data = {"future_price": future_price, "change": change, "timeframe": tf}

        with _connect() as conn:
            conn.execute(
                """
                UPDATE signals
                SET outcome = ?, actual_pnl = ?, resolved_at = ?, resolution_data = ?
                WHERE id = ?
                """,
                (
                    outcome, pnl,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(resolution_data),
                    row["id"],
                ),
            )
        resolved_count += 1

    return resolved_count


# ── Metrics & reporting ──────────────────────────────────────────────────────

def update_daily_metrics(date: str | None = None) -> None:
    """Recompute daily_metrics for the given UTC date (default: today)."""
    target = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT system,
                   COUNT(*) AS signals_logged,
                   SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) AS signals_resolved,
                   AVG(CASE WHEN outcome = 'WIN' THEN 1.0
                            WHEN outcome IS NOT NULL THEN 0.0 END) AS win_rate,
                   AVG(estimated_edge) AS avg_edge,
                   AVG(net_edge) AS avg_net_edge,
                   SUM(actual_pnl) AS total_pnl
            FROM signals
            WHERE date(created_at) = ?
            GROUP BY system
            """,
            (target,),
        ).fetchall()

        for r in rows:
            conn.execute(
                """
                INSERT INTO daily_metrics
                    (date, system, signals_logged, signals_resolved,
                     win_rate, avg_edge, avg_net_edge, total_pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, system) DO UPDATE SET
                    signals_logged = excluded.signals_logged,
                    signals_resolved = excluded.signals_resolved,
                    win_rate = excluded.win_rate,
                    avg_edge = excluded.avg_edge,
                    avg_net_edge = excluded.avg_net_edge,
                    total_pnl = excluded.total_pnl
                """,
                (
                    target, r["system"],
                    r["signals_logged"] or 0,
                    r["signals_resolved"] or 0,
                    r["win_rate"], r["avg_edge"], r["avg_net_edge"], r["total_pnl"],
                ),
            )


def get_performance_summary(system: str | None = None, days: int = 30) -> dict[str, Any]:
    """Return aggregated win rate, edge, PnL for the trailing `days` days."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        sql = """
            SELECT COUNT(*) AS signals_logged,
                   SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) AS signals_resolved,
                   AVG(CASE WHEN outcome = 'WIN' THEN 1.0
                            WHEN outcome IS NOT NULL THEN 0.0 END) AS win_rate,
                   AVG(estimated_edge) AS avg_edge,
                   AVG(net_edge) AS avg_net_edge,
                   SUM(actual_pnl) AS total_pnl
            FROM signals
            WHERE created_at >= ?
        """
        params: list[Any] = [cutoff]
        if system:
            sql += " AND system = ?"
            params.append(system)
        row = conn.execute(sql, params).fetchone()
    return {
        "system": system or "ALL",
        "days": days,
        "signals_logged": row["signals_logged"] or 0,
        "signals_resolved": row["signals_resolved"] or 0,
        "win_rate": row["win_rate"],
        "avg_edge": row["avg_edge"],
        "avg_net_edge": row["avg_net_edge"],
        "total_pnl": row["total_pnl"] or 0.0,
    }
