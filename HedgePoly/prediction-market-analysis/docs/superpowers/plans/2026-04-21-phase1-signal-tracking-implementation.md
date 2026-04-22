# Phase 1: Signal Tracking, Transaction Costs & Market Context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a monorepo-wide signal tracking database, transaction-cost model, and enable the dormant `market_context.py` classifier so every quant subsystem records its signals, deducts costs before sizing, and adjusts edge by price structure.

**Architecture:** Two new standalone modules (`signal_tracker.py`, `cost_model.py`) live at the monorepo root next to `scheduler.py` and `bot.py`. SQLite is the storage layer (WAL mode for concurrent writers). Each of the six existing subsystems (HedgePoly, PolyTraders, ModelTelegra, AlphaFeed, Poly2, Poly) gets a thin integration: append `sys.path`, import the modules, call `log_signal()` after producing each opportunity, and (for systems that size positions) run the two-pass Kelly to fold cost into bet size. Resolution is passive and runs at the end of `scheduler.py` — Polymarket via Gamma API, ModelTelegra via yfinance.

**Tech Stack:** Python 3.12+, SQLite (stdlib `sqlite3`), `pytest`, `httpx` (already in AlphaFeed) or stdlib `urllib.request` for Polymarket API, `yfinance` for ModelTelegra resolution.

**Spec reference:** [`docs/superpowers/specs/2026-04-20-phase1-signal-tracking-design.md`](../specs/2026-04-20-phase1-signal-tracking-design.md)

---

## File Structure

### New files (all at monorepo root `d:/OMNP - Quant/Projetos/`)

| Path | Responsibility |
|------|----------------|
| `signal_tracker.py` | SQLite-backed signal log: `log_signal()`, resolvers, daily metrics, performance summary. ~400 LOC. |
| `cost_model.py` | Pure-function transaction cost estimator per market type. ~80 LOC. |
| `tests/__init__.py` | Empty marker so pytest discovers the package. |
| `tests/conftest.py` | Shared fixtures (temp DB factory, monorepo-root path). |
| `tests/test_signal_tracker.py` | Unit + integration tests for the tracker. |
| `tests/test_cost_model.py` | Unit tests for the cost model. |
| `tests/test_market_context_integration.py` | Verifies PolyTraders calls `classify_prediction_market`. |

### Modified files

| Path | Change |
|------|--------|
| `scheduler.py` | Append resolution + metrics call at end of `run_all()`. |
| `PolyTraders/kelly.py` | Wire `market_context`, integrate `cost_model` (two-pass Kelly), call `log_signal()`. |
| `PolyTraders/main.py` | Pass through context fields when emitting Telegram report. |
| `HedgePoly/prediction-market-analysis/reporting.py` | Call `log_signal()` for each scored market. |
| `ModelTelegra,/quant_desk/models/model3_risk.py` | Call `log_signal()` for the BTC straddle and each trend signal. |
| `AlphaFeed/backend/adapters/quant_report.py` | Call `log_signal()` for each scored opportunity. |
| `Poly2/polymarket_telegram_bot.py` | Call `log_signal()` after Kelly sizing in `_match_and_size()`. |
| `Poly/polymarket_scraper.py` | Call `log_signal()` after `find_opportunities()`. |
| `.gitignore` | Add `signal_tracker.db`, `signal_tracker.db-shm`, `signal_tracker.db-wal`. |

### File-size discipline

`signal_tracker.py` is the largest new file (~400 LOC). It is split internally by section comments (schema/migrations, connection helper, log_signal, resolvers, metrics) but kept in one file because all of these share state and helpers. If it grows past ~600 LOC during implementation, split resolvers into `signal_resolvers.py`.

---

## Phase Map

The plan is organised into four phases. Each phase produces working, committable software on its own.

- **Phase A — Foundation** (Tasks 1–7): `signal_tracker.py` and `cost_model.py` standalone with full unit tests. After Phase A, no production code uses the new modules yet.
- **Phase B — Market Context** (Task 8): Wire the dormant `market_context.py` into PolyTraders. Independent of Phase A — could ship alone.
- **Phase C — Integrations** (Tasks 9–15): Plumb `signal_tracker` and `cost_model` into all six subsystems plus the scheduler resolver.
- **Phase D — Verification** (Task 16): End-to-end run + DB inspection.

If the plan stalls or scope needs to shrink, Phase C can be split into a follow-up plan.

---

## Phase A — Foundation Modules

### Task 1: `tests/` scaffolding and `.gitignore`

**Files:**
- Create: `d:/OMNP - Quant/Projetos/tests/__init__.py`
- Create: `d:/OMNP - Quant/Projetos/tests/conftest.py`
- Modify: `d:/OMNP - Quant/Projetos/.gitignore` (create if absent)

- [ ] **Step 1: Create the empty `tests/__init__.py`**

```python
# tests/__init__.py
```

- [ ] **Step 2: Create `tests/conftest.py` with shared fixtures**

```python
# tests/conftest.py
"""Shared pytest fixtures for monorepo-level tests."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Iterator

import pytest

MONOREPO_ROOT = Path(__file__).resolve().parents[1]
if str(MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(MONOREPO_ROOT))


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Disposable SQLite path for a single test."""
    return tmp_path / "signal_tracker_test.db"


@pytest.fixture
def fresh_tracker(tmp_db_path: Path) -> Iterator[object]:
    """Import signal_tracker pointed at a temp DB; yields the module."""
    import importlib
    import signal_tracker  # noqa: WPS433 — dynamic monorepo import

    importlib.reload(signal_tracker)
    signal_tracker.DB_PATH = tmp_db_path
    signal_tracker.init_db()
    yield signal_tracker
```

- [ ] **Step 3: Add SQLite artifacts to `.gitignore`**

Append to `d:/OMNP - Quant/Projetos/.gitignore` (create the file if it doesn't exist):

```gitignore
# Signal tracking database (Phase 1)
signal_tracker.db
signal_tracker.db-shm
signal_tracker.db-wal
```

- [ ] **Step 4: Sanity-check pytest discovers the new package**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/ --collect-only -q`

Expected: `no tests ran in 0.0Xs` (no test files yet — but no collection errors).

- [ ] **Step 5: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add tests/__init__.py tests/conftest.py .gitignore
git commit -m "chore: add monorepo tests/ scaffolding for Phase 1"
```

---

### Task 2: `signal_tracker.py` — schema, pragmas, init_db

**Files:**
- Create: `d:/OMNP - Quant/Projetos/signal_tracker.py`
- Test: `d:/OMNP - Quant/Projetos/tests/test_signal_tracker.py`

- [ ] **Step 1: Write failing test for `init_db()` and pragma application**

Create `tests/test_signal_tracker.py`:

```python
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
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_signal_tracker.py -v`

Expected: `ModuleNotFoundError: No module named 'signal_tracker'`.

- [ ] **Step 3: Create `signal_tracker.py` with schema and pragmas**

Create `d:/OMNP - Quant/Projetos/signal_tracker.py`:

```python
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_signal_tracker.py -v`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add signal_tracker.py tests/test_signal_tracker.py
git commit -m "feat(signal_tracker): add schema, WAL pragmas, migrations skeleton"
```

---

### Task 3: `log_signal()` with validation and direction-aware entry_price

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/signal_tracker.py`
- Modify: `d:/OMNP - Quant/Projetos/tests/test_signal_tracker.py`

- [ ] **Step 1: Write failing tests for `log_signal()`**

Append to `tests/test_signal_tracker.py`:

```python
import pytest


def test_log_signal_returns_id(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="polytraders",
        signal_type="polymarket",
        direction="YES",
        estimated_edge=0.05,
        market_price=0.40,
        market_slug="will-x-happen",
    )
    assert isinstance(sig_id, int) and sig_id > 0


def test_log_signal_yes_entry_price_equals_market_price(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="x",
    )
    row = fresh_tracker.get_signal(sig_id)
    assert row["entry_price"] == pytest.approx(0.40)


def test_log_signal_no_entry_price_is_complement(fresh_tracker):
    """Spec B2: NO bets have entry_price = 1 - market_price."""
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="NO",
        estimated_edge=0.05, market_price=0.30, market_slug="x",
    )
    row = fresh_tracker.get_signal(sig_id)
    assert row["entry_price"] == pytest.approx(0.70)


def test_log_signal_polymarket_requires_slug(fresh_tracker):
    """Spec B4: Polymarket signals MUST have market_slug for resolution."""
    with pytest.raises(ValueError, match="market_slug"):
        fresh_tracker.log_signal(
            system="poly", signal_type="polymarket", direction="YES",
            estimated_edge=0.05, market_price=0.40,
        )


def test_log_signal_straddle_requires_straddle_cost(fresh_tracker):
    """Spec W1: straddle resolver needs raw_features['straddle_cost']."""
    with pytest.raises(ValueError, match="straddle_cost"):
        fresh_tracker.log_signal(
            system="modeltelegra", signal_type="straddle", direction="STRADDLE",
            estimated_edge=0.02, market_price=70000.0, ticker="BTC-USD",
            raw_features={},
        )


def test_log_signal_trend_requires_timeframe(fresh_tracker):
    """Spec W2: trend resolver needs raw_features['timeframe']."""
    with pytest.raises(ValueError, match="timeframe"):
        fresh_tracker.log_signal(
            system="modeltelegra", signal_type="trend_direction", direction="LONG",
            estimated_edge=0.01, market_price=70000.0, ticker="BTC-USD",
            raw_features={},
        )


def test_log_signal_persists_raw_features_as_json(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="x",
        raw_features={"signal_strength": 0.8, "n_traders": 5},
    )
    row = fresh_tracker.get_signal(sig_id)
    import json
    parsed = json.loads(row["raw_features"])
    assert parsed["n_traders"] == 5


def test_log_signal_deduplication_within_run(fresh_tracker):
    common = dict(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="will-x-happen",
    )
    first = fresh_tracker.log_signal(**common)
    second = fresh_tracker.log_signal(**common)
    assert first == second  # second call returns the same ID, no duplicate row

    import sqlite3
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE market_slug='will-x-happen'"
    ).fetchone()[0]
    conn.close()
    assert count == 1
```

- [ ] **Step 2: Run tests — expect FAIL (`log_signal`/`get_signal` undefined)**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_signal_tracker.py -v`

Expected: 8 failures with `AttributeError: module 'signal_tracker' has no attribute 'log_signal'`.

- [ ] **Step 3: Implement `log_signal` and `get_signal`**

Append to `signal_tracker.py`:

```python
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_signal_tracker.py -v`

Expected: 12 passed (4 from Task 2 + 8 new).

- [ ] **Step 5: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add signal_tracker.py tests/test_signal_tracker.py
git commit -m "feat(signal_tracker): log_signal with validation and direction-aware entry_price"
```

---

### Task 4: Polymarket resolver — `resolve_polymarket_signals()`

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/signal_tracker.py`
- Modify: `d:/OMNP - Quant/Projetos/tests/test_signal_tracker.py`

- [ ] **Step 1: Write failing tests for the resolver (with mocked Gamma API)**

Append to `tests/test_signal_tracker.py`:

```python
from unittest.mock import patch


def _fake_gamma_response(closed: bool, yes_price: float = 1.0):
    """Mimic the shape returned by https://gamma-api.polymarket.com/markets?slug=…"""
    return [{
        "slug": "will-x-happen",
        "closed": closed,
        "resolutionSource": "https://example.com" if closed else "",
        "outcomePrices": json.dumps([str(yes_price), str(1.0 - yes_price)]),
    }]


def test_resolve_polymarket_yes_win(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="will-x-happen",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_market",
                      return_value=_fake_gamma_response(closed=True, yes_price=1.0)):
        resolved = fresh_tracker.resolve_polymarket_signals()
    assert resolved == 1
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"
    # entry_price=0.40, kelly_bet=10 → payout = 10 * (1/0.40 - 1) = 15
    assert row["actual_pnl"] == pytest.approx(15.0)


def test_resolve_polymarket_no_win(fresh_tracker):
    """NO bet at market_price=0.30 → entry_price=0.70 → win pays 10*(1/0.70 - 1) ≈ 4.29."""
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="NO",
        estimated_edge=0.05, market_price=0.30, market_slug="will-x-happen",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_market",
                      return_value=_fake_gamma_response(closed=True, yes_price=0.0)):
        fresh_tracker.resolve_polymarket_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"
    assert row["actual_pnl"] == pytest.approx(10.0 * (1.0 / 0.70 - 1.0))


def test_resolve_polymarket_loss(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="will-x-happen",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_market",
                      return_value=_fake_gamma_response(closed=True, yes_price=0.0)):
        fresh_tracker.resolve_polymarket_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "LOSS"
    assert row["actual_pnl"] == pytest.approx(-10.0)


def test_resolve_polymarket_skips_open_markets(fresh_tracker):
    fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, market_slug="will-x-happen",
        kelly_bet=10.0,
    )
    with patch.object(fresh_tracker, "_fetch_gamma_market",
                      return_value=_fake_gamma_response(closed=False)):
        resolved = fresh_tracker.resolve_polymarket_signals()
    assert resolved == 0
```

- [ ] **Step 2: Run tests — expect FAIL (resolver/`_fetch_gamma_market` undefined)**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_signal_tracker.py -v -k polymarket`

Expected: 4 failures.

- [ ] **Step 3: Implement the Polymarket resolver**

Append to `signal_tracker.py`:

```python
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_signal_tracker.py -v -k polymarket`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add signal_tracker.py tests/test_signal_tracker.py
git commit -m "feat(signal_tracker): polymarket resolver via Gamma API with direction-aware PnL"
```

---

### Task 5: ModelTelegra resolvers — straddle and trend

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/signal_tracker.py`
- Modify: `d:/OMNP - Quant/Projetos/tests/test_signal_tracker.py`

- [ ] **Step 1: Write failing tests for straddle + trend resolvers**

Append to `tests/test_signal_tracker.py`:

```python
def test_resolve_straddle_win(fresh_tracker):
    """move=8% > breakeven=5% (straddle_cost 4% + cost_estimate 1%) → WIN."""
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="straddle", direction="STRADDLE",
        estimated_edge=0.02, market_price=70000.0, ticker="BTC-USD",
        kelly_bet=100.0, cost_estimate=0.01,
        raw_features={"straddle_cost": 0.04},
    )
    with patch.object(fresh_tracker, "_fetch_btc_price_at",
                      return_value=70000.0 * 1.08):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"
    # move/breakeven = 0.08/0.05 = 1.6, pnl = 100 * (1.6 - 1) = 60
    assert row["actual_pnl"] == pytest.approx(60.0, rel=1e-3)


def test_resolve_straddle_loss_capped_at_premium(fresh_tracker):
    """move=0 → loss capped at -kelly_bet (premium outlay)."""
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="straddle", direction="STRADDLE",
        estimated_edge=0.02, market_price=70000.0, ticker="BTC-USD",
        kelly_bet=100.0, cost_estimate=0.01,
        raw_features={"straddle_cost": 0.04},
    )
    with patch.object(fresh_tracker, "_fetch_btc_price_at", return_value=70000.0):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "LOSS"
    assert row["actual_pnl"] == pytest.approx(-100.0)


def test_resolve_trend_long_win(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="trend_direction", direction="LONG",
        estimated_edge=0.01, market_price=70000.0, ticker="BTC-USD",
        kelly_bet=100.0,
        raw_features={"timeframe": "1D"},
    )
    with patch.object(fresh_tracker, "_fetch_btc_price_at", return_value=72100.0):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "WIN"
    # change = 2100/70000 = 0.03 → pnl = 100 * 0.03 = 3
    assert row["actual_pnl"] == pytest.approx(3.0, rel=1e-3)


def test_resolve_trend_short_loss(fresh_tracker):
    sig_id = fresh_tracker.log_signal(
        system="modeltelegra", signal_type="trend_direction", direction="SHORT",
        estimated_edge=0.01, market_price=70000.0, ticker="BTC-USD",
        kelly_bet=100.0,
        raw_features={"timeframe": "1H"},
    )
    with patch.object(fresh_tracker, "_fetch_btc_price_at", return_value=72100.0):
        fresh_tracker.resolve_modeltelegra_signals()
    row = fresh_tracker.get_signal(sig_id)
    assert row["outcome"] == "LOSS"
    assert row["actual_pnl"] == pytest.approx(-3.0, rel=1e-3)
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_signal_tracker.py -v -k modeltelegra or straddle or trend`

Expected: 4 failures.

- [ ] **Step 3: Implement the ModelTelegra resolvers**

Append to `signal_tracker.py`:

```python
# ── ModelTelegra resolution ──────────────────────────────────────────────────

# Bars-ahead by signal timeframe — how far in the future to look up the realised price.
_TREND_HORIZONS_HOURS = {"1H": 24, "1D": 24 * 5, "1W": 24 * 30}


def _fetch_btc_price_at(timestamp: datetime) -> float | None:
    """Fetch the BTC-USD spot price at (or just after) the given UTC timestamp via yfinance."""
    try:
        import yfinance as yf  # local import — yfinance is heavy
    except ImportError:
        log.warning("yfinance not installed — cannot resolve ModelTelegra signals")
        return None
    try:
        end = timestamp.replace(microsecond=0)
        # 24h window centered on target; yfinance returns hourly bars
        ticker = yf.Ticker("BTC-USD")
        hist = ticker.history(
            start=end.strftime("%Y-%m-%d"),
            end=(end.replace(hour=23, minute=59)).strftime("%Y-%m-%d"),
            interval="1h",
        )
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:  # noqa: BLE001
        log.debug("yfinance fetch failed: %s", exc)
        return None


def resolve_modeltelegra_signals() -> int:
    """Resolve all unresolved straddle + trend signals via yfinance. Returns count resolved."""
    from datetime import timedelta

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, signal_type, direction, market_price, kelly_bet,
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

        if row["signal_type"] == "straddle":
            target_time = created + timedelta(hours=24)
            future_price = _fetch_btc_price_at(target_time)
            if future_price is None or future_price <= 0:
                continue
            move = abs(future_price - row["market_price"]) / row["market_price"]
            breakeven = float(raw.get("straddle_cost", 0.04)) + (row["cost_estimate"] or 0.0)
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
            future_price = _fetch_btc_price_at(target_time)
            if future_price is None or future_price <= 0:
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_signal_tracker.py -v`

Expected: all 16 tests pass.

- [ ] **Step 5: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add signal_tracker.py tests/test_signal_tracker.py
git commit -m "feat(signal_tracker): straddle + trend resolvers using yfinance"
```

---

### Task 6: `daily_metrics` rollup and `performance_summary`

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/signal_tracker.py`
- Modify: `d:/OMNP - Quant/Projetos/tests/test_signal_tracker.py`

- [ ] **Step 1: Write failing tests for `update_daily_metrics()` and `get_performance_summary()`**

Append to `tests/test_signal_tracker.py`:

```python
def test_update_daily_metrics_aggregates_per_system(fresh_tracker):
    # Two PolyTraders signals, one WIN one LOSS, both resolved today
    fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.05, market_price=0.40, kelly_bet=10.0,
        market_slug="market-1", cost_estimate=0.02,
    )
    fresh_tracker.log_signal(
        system="polytraders", signal_type="polymarket", direction="YES",
        estimated_edge=0.06, market_price=0.50, kelly_bet=10.0,
        market_slug="market-2", cost_estimate=0.02,
    )
    # Manually mark them resolved
    import sqlite3
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    conn.execute("UPDATE signals SET outcome='WIN', actual_pnl=15.0, resolved_at=? WHERE market_slug='market-1'",
                 (datetime.now(timezone.utc).isoformat(),))
    conn.execute("UPDATE signals SET outcome='LOSS', actual_pnl=-10.0, resolved_at=? WHERE market_slug='market-2'",
                 (datetime.now(timezone.utc).isoformat(),))
    conn.commit()
    conn.close()

    fresh_tracker.update_daily_metrics()

    summary = fresh_tracker.get_performance_summary(system="polytraders", days=30)
    assert summary["signals_resolved"] == 2
    assert summary["win_rate"] == pytest.approx(0.5)
    assert summary["total_pnl"] == pytest.approx(5.0)
```

(Add `from datetime import datetime, timezone` to the test file's imports if not already present.)

- [ ] **Step 2: Run test — expect FAIL**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_signal_tracker.py -v -k metrics`

Expected: 1 failure (`update_daily_metrics` undefined).

- [ ] **Step 3: Implement metrics aggregation**

Append to `signal_tracker.py`:

```python
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
                   AVG(CASE WHEN outcome = 'WIN' THEN 1.0 ELSE 0.0 END)
                       FILTER (WHERE outcome IS NOT NULL) AS win_rate,
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
                   AVG(CASE WHEN outcome = 'WIN' THEN 1.0 ELSE 0.0 END)
                       FILTER (WHERE outcome IS NOT NULL) AS win_rate,
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_signal_tracker.py -v`

Expected: 17 passed.

- [ ] **Step 5: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add signal_tracker.py tests/test_signal_tracker.py
git commit -m "feat(signal_tracker): daily metrics rollup and performance summary"
```

---

### Task 7: `cost_model.py`

**Files:**
- Create: `d:/OMNP - Quant/Projetos/cost_model.py`
- Create: `d:/OMNP - Quant/Projetos/tests/test_cost_model.py`

- [ ] **Step 1: Write failing tests for `estimate_cost()`**

Create `tests/test_cost_model.py`:

```python
"""Tests for cost_model.py — transaction cost estimation per market type."""
from __future__ import annotations

import pytest


def test_polymarket_liquid_market_low_cost():
    from cost_model import estimate_cost
    cost = estimate_cost("polymarket", price=0.50, size_usd=100, liquidity=100_000, spread=0.01)
    assert 0 < cost < 0.05  # < 5% on a deep liquid market


def test_polymarket_illiquid_high_cost():
    from cost_model import estimate_cost
    cost = estimate_cost("polymarket", price=0.50, size_usd=100, liquidity=1_000, spread=0.04)
    assert cost > 0.10  # > 10% on a thin market


def test_polymarket_small_bet_gas_dominated():
    from cost_model import estimate_cost
    big = estimate_cost("polymarket", price=0.50, size_usd=100, liquidity=10_000, spread=0.01)
    tiny = estimate_cost("polymarket", price=0.50, size_usd=1, liquidity=10_000, spread=0.01)
    assert tiny > big  # gas eats a small bet


def test_btc_straddle_flat():
    from cost_model import estimate_cost
    assert estimate_cost("btc_straddle", price=70000, size_usd=500) == pytest.approx(0.025)


def test_spot_signals_zero_cost():
    from cost_model import estimate_cost
    assert estimate_cost("btc_spot", price=70000, size_usd=100) == 0.0
    assert estimate_cost("equity_spot", price=400, size_usd=100) == 0.0


def test_cost_always_non_negative():
    from cost_model import estimate_cost
    cost = estimate_cost("polymarket", price=0.50, size_usd=0, liquidity=0, spread=0)
    assert cost >= 0


def test_cost_bounded_below_50pct():
    from cost_model import estimate_cost
    cost = estimate_cost("polymarket", price=0.50, size_usd=10_000, liquidity=100, spread=0.10)
    assert cost < 0.50


def test_unknown_market_type_returns_zero():
    from cost_model import estimate_cost
    assert estimate_cost("unknown_thing", price=1, size_usd=1) == 0.0
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_cost_model.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement `cost_model.py`**

Create `d:/OMNP - Quant/Projetos/cost_model.py`:

```python
"""
cost_model.py — Transaction cost estimation per market type.

Returns round-trip cost as a fraction (e.g., 0.02 = 2% drag on PnL per round-trip).
Used by every system's Kelly sizing layer to deduct cost from gross edge before
sizing the bet.

Two-pass usage (see spec Component 2):
  1. Compute provisional bet from gross edge.
  2. estimate_cost(..., size_usd=provisional_bet) — gives a conservative upper bound.
  3. Deduct from edge, re-size Kelly with the net edge.
"""
from __future__ import annotations

# Hard cap on returned cost so a misconfigured caller can't filter every signal.
_COST_CAP = 0.50

# Polymarket constants — see spec for rationale.
_GAS_FEE_USD = 0.05      # ~$0.05 per Polygon L2 swap
_MIN_HALF_SPREAD = 0.005 # 0.5% half-spread floor when no spread data is available
_IMPACT_COEFF = 0.5      # linear impact: bet/liquidity * 0.5

# BTC straddle: flat estimate until Phase 3 wires Deribit order book.
_BTC_STRADDLE_FLAT_COST = 0.025


def estimate_cost(
    market_type: str,
    price: float,
    size_usd: float,
    liquidity: float = 0.0,
    spread: float = 0.0,
) -> float:
    """Estimate round-trip transaction cost as a fraction of position size.

    market_type : 'polymarket', 'btc_straddle', 'btc_spot', 'equity_spot'
    price       : current market price (0-1 for PM, USD for assets)
    size_usd    : provisional bet size in USD
    liquidity   : available liquidity in USD (PM order book depth, ignored elsewhere)
    spread      : bid-ask spread as fraction (0.02 = 2pp for PM)
    """
    if market_type == "polymarket":
        return _polymarket_cost(size_usd, liquidity, spread)
    if market_type == "btc_straddle":
        return _BTC_STRADDLE_FLAT_COST
    if market_type in ("btc_spot", "equity_spot"):
        return 0.0
    return 0.0  # unknown market type — fail open, don't filter


def _polymarket_cost(size_usd: float, liquidity: float, spread: float) -> float:
    half_spread = max(spread / 2.0, _MIN_HALF_SPREAD)
    gas_pct = _GAS_FEE_USD / max(size_usd, 1.0)
    impact = (size_usd / max(liquidity, 1.0)) * _IMPACT_COEFF
    one_way = half_spread + gas_pct + impact
    return min(2.0 * one_way, _COST_CAP)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_cost_model.py -v`

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add cost_model.py tests/test_cost_model.py
git commit -m "feat(cost_model): per-market-type round-trip transaction cost estimator"
```

---

## Phase B — Wire `market_context.py` in PolyTraders

### Task 8: Call `classify_prediction_market` from `kelly.py`

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/PolyTraders/kelly.py`
- Modify: `d:/OMNP - Quant/Projetos/PolyTraders/main.py` (Telegram report only — minor)
- Create: `d:/OMNP - Quant/Projetos/tests/test_market_context_integration.py`

**Background:** [`PolyTraders/kelly.py:38`](PolyTraders/kelly.py#L38) imports `classify_prediction_market` but never calls it. The `Opportunity` dataclass at [`PolyTraders/kelly.py:94-117`](PolyTraders/kelly.py#L94-L117) already has `market_structure`, `context_quality`, `context_note` fields with default values. Wire the classifier in.

- [ ] **Step 1: Write failing test for `score_opportunities` populating context fields**

Create `tests/test_market_context_integration.py`:

```python
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


def _fake_position(condition_id="abc", outcome="YES", cur_price=0.40, avg_price=0.30,
                   current_value=200.0, trader_rank=1, proxy_wallet="0xAAA",
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
```

- [ ] **Step 2: Run test — expect FAIL (mock not called)**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_market_context_integration.py -v`

Expected: 1 failure (`assert mock.called`).

- [ ] **Step 3: Wire `classify_prediction_market` into `score_opportunities`**

Read `kelly.py` around line 230-260 (after `estimated_edge` is computed, before Kelly formula). Insert this block immediately after the `estimated_edge = raw_edge * entry_discount` line (~line 229) and before the `if estimated_edge < min_net_edge:` check:

```python
        # ── Market structure context (Phase 1) ───────────────────────────────
        try:
            ctx = classify_prediction_market(
                condition_id=condition_id,
                cur_price=cur_price,
                wav_entry=wav_entry,
                total_exposure=total_val,
            )
            estimated_edge *= ctx.edge_mult
            market_structure = ctx.structure.value if hasattr(ctx.structure, "value") else str(ctx.structure)
            context_quality = ctx.quality
            context_note = ctx.note
        except Exception as exc:
            log.debug("market_context classify failed for %s: %s", condition_id, exc)
            market_structure = "Unknown"
            context_quality = "acceptable"
            context_note = ""
```

Also ensure `log` is defined near the top of `kelly.py` (add `import logging; log = logging.getLogger("kelly")` if not present).

Then in the `Opportunity(...)` constructor call further down in the same function, pass these fields:

```python
opportunities.append(Opportunity(
    # ... existing fields ...
    market_structure=market_structure,
    context_quality=context_quality,
    context_note=context_note,
))
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_market_context_integration.py -v`

Expected: 2 passed.

- [ ] **Step 5: Update Telegram output in `PolyTraders/main.py`**

Find the per-opportunity formatter and append a line showing market structure. Look for the f-string that formats one opportunity (likely contains `opp.title` and `opp.kelly_bet`). Add after the existing line that mentions edge or kelly:

```python
            f"Structure: {opp.market_structure} ({opp.context_quality}) — "
            f"{opp.context_note}\n"
```

(The exact f-string location depends on `main.py`'s current output template — adapt verbatim from existing style.)

- [ ] **Step 6: Smoke-test PolyTraders end-to-end**

Run: `cd "d:/OMNP - Quant/Projetos/PolyTraders" && python main.py --top 25 --bankroll 100 --period WEEK 2>&1 | head -50`

Expected: opportunities print with non-default `Structure:` lines (or "Unknown" if API fails — that's fine).

- [ ] **Step 7: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add PolyTraders/kelly.py PolyTraders/main.py tests/test_market_context_integration.py
git commit -m "feat(polytraders): wire market_context classifier into edge calculation"
```

---

## Phase C — Cross-System Integrations

> **Pattern for every Phase C task:** at the top of the file being modified, add the monorepo-root `sys.path` shim and import `signal_tracker` (and `cost_model` if the system sizes positions) inside a `try/except ImportError` so failure to import never crashes the main pipeline. The depth of `parents[N]` differs per file — see the table at line 270 of the spec.

### Task 9: Integrate `signal_tracker` + cost_model + two-pass Kelly into PolyTraders

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/PolyTraders/kelly.py`
- Modify: `d:/OMNP - Quant/Projetos/tests/test_market_context_integration.py` (add cost-flow assertion)

- [ ] **Step 1: Write failing test that asserts a row is written and cost is non-zero**

Append to `tests/test_market_context_integration.py`:

```python
def test_score_opportunities_logs_signal_with_cost(fresh_tracker, monkeypatch):
    from PolyTraders import kelly
    from PolyTraders.market_context import MarketContext, PriceStructure

    positions = [
        _fake_position(proxy_wallet=f"0x{i:03}", trader_rank=i + 1, slug="will-x")
        for i in range(5)
    ]
    fake_ctx = MarketContext(
        structure=PriceStructure.COMPRESSION, edge_mult=1.30,
        quality="ideal", note="test",
    )
    monkeypatch.setattr(kelly, "classify_prediction_market", lambda **k: fake_ctx)

    opps = kelly.score_opportunities(positions, total_traders_checked=25, bankroll=100.0)
    assert opps

    import sqlite3
    conn = sqlite3.connect(fresh_tracker.DB_PATH)
    rows = conn.execute(
        "SELECT cost_estimate, net_edge, market_slug FROM signals WHERE system='polytraders'"
    ).fetchall()
    conn.close()
    assert rows, "expected signal_tracker to receive at least one row"
    cost, net_edge, slug = rows[0]
    assert slug == "will-x"
    assert cost > 0
    assert net_edge < opps[0].estimated_edge + 1e-9  # net = gross - cost
```

(The `fresh_tracker` fixture is from `conftest.py` — it points `signal_tracker.DB_PATH` to a temp DB.)

- [ ] **Step 2: Run test — expect FAIL**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_market_context_integration.py::test_score_opportunities_logs_signal_with_cost -v`

Expected: failure (no rows in DB).

- [ ] **Step 3: Add the sys.path shim and try/except imports at the top of `kelly.py`**

Insert near the top of `kelly.py`, right after the existing `from __future__ import annotations`:

```python
import sys
from pathlib import Path

_MONOREPO_ROOT = Path(__file__).resolve().parents[1]
if str(_MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MONOREPO_ROOT))

try:
    from signal_tracker import log_signal as _log_signal
    from cost_model import estimate_cost as _estimate_cost
except ImportError:
    def _log_signal(*args, **kwargs):  # type: ignore[no-redef]
        return -1
    def _estimate_cost(*args, **kwargs):  # type: ignore[no-redef]
        return 0.0
```

- [ ] **Step 4: Replace single-pass Kelly with two-pass + cost deduction + log_signal**

In `score_opportunities()`, after computing `estimated_edge` (post-context) but BEFORE the existing Kelly block, insert the two-pass logic:

```python
        # ── Two-pass Kelly with cost deduction (Phase 1) ────────────────────
        # Pass 1: provisional bet using gross edge → upper-bound input to cost model
        # NOTE: `cur_price` here is already the side-specific price (PolyTraders groups
        # positions by (condition_id, outcome), so cur_price = the price of the side
        # smart money is on — that's the side we're copying).
        side_price = cur_price
        b = (1.0 - side_price) / side_price
        p_gross = min(side_price + estimated_edge, 0.99)
        kelly_gross_frac = max(0.0, (b * p_gross - (1.0 - p_gross)) / b)
        provisional_bet = kelly_gross_frac * bankroll * kelly_fraction

        # Liquidity / spread aren't currently exposed by the position objects;
        # use conservative defaults until PolyTraders pulls order book data.
        liquidity = float(getattr(representative, "liquidity", 0.0) or 0.0)
        spread = float(getattr(representative, "spread", 0.0) or 0.0)
        cost = _estimate_cost(
            "polymarket", side_price, provisional_bet,
            liquidity=liquidity, spread=spread,
        )
        net_edge = max(0.0, estimated_edge - cost)
        if net_edge < min_net_edge:
            continue  # skip — negative EV after costs

        # Pass 2: re-size with net edge
        p_est = min(side_price + net_edge, 0.99)
```

Then remove the old `p_est = min(cur_price + estimated_edge, 0.99)` line below (it is replaced by `p_est = min(side_price + net_edge, 0.99)`).

After computing `kelly_bet` (the final bet), call `log_signal`:

```python
        try:
            _log_signal(
                system="polytraders",
                signal_type="polymarket",
                direction=outcome,  # 'YES' or 'NO' — destructured from the (condition_id, outcome) group key
                estimated_edge=estimated_edge,
                market_price=cur_price,
                market_slug=getattr(representative, "slug", None),
                condition_id=condition_id,
                kelly_bet=kelly_bet,
                kelly_fraction=kelly_fraction,
                cost_estimate=cost,
                raw_features={
                    "signal_strength": signal_strength,
                    "count_signal": count_signal,
                    "size_signal": size_signal,
                    "n_smart_traders": n,
                    "wav_entry": wav_entry,
                    "market_structure": market_structure,
                    "context_quality": context_quality,
                },
            )
        except Exception as exc:
            log.debug("log_signal failed: %s", exc)
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/test_market_context_integration.py -v`

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add PolyTraders/kelly.py tests/test_market_context_integration.py
git commit -m "feat(polytraders): two-pass Kelly with cost_model + log signals"
```

---

### Task 10: Integrate `signal_tracker` into HedgePoly `reporting.py`

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/HedgePoly/prediction-market-analysis/reporting.py`

- [ ] **Step 1: Add the sys.path shim near the top of `reporting.py`**

```python
import sys
from pathlib import Path

_MONOREPO_ROOT = Path(__file__).resolve().parents[2]  # reporting.py → prediction-market-analysis → HedgePoly → Projetos
if str(_MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MONOREPO_ROOT))

try:
    from signal_tracker import log_signal as _log_signal
except ImportError:
    def _log_signal(*args, **kwargs):  # type: ignore[no-redef]
        return -1
```

- [ ] **Step 2: Locate where opportunities are emitted and add `log_signal` calls**

Find the function that produces the final scored opportunity list (likely after `_score_market()` is called per market). For each opportunity, call:

```python
try:
    _log_signal(
        system="hedgepoly",
        signal_type="polymarket",
        direction=opp.direction if hasattr(opp, "direction") else "YES",
        estimated_edge=opp.calibrated_edge,  # adapt to actual field name
        market_price=opp.market_price,
        market_slug=opp.slug,
        condition_id=getattr(opp, "condition_id", None),
        signal_tier=opp.tier if hasattr(opp, "tier") else None,
        raw_features={"score": opp.score, "calibration": opp.calibration_method},
    )
except Exception as exc:
    log.debug("log_signal failed: %s", exc)
```

(Field names depend on the HedgePoly opportunity struct — adapt to what actually exists.)

- [ ] **Step 3: Run HedgePoly tests to confirm nothing regresses**

Run: `cd "d:/OMNP - Quant/Projetos/HedgePoly/prediction-market-analysis" && python -m pytest tests/ -v`

Expected: existing tests still pass.

- [ ] **Step 4: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add HedgePoly/prediction-market-analysis/reporting.py
git commit -m "feat(hedgepoly): log scored markets to signal_tracker"
```

---

### Task 11: Integrate `signal_tracker` into AlphaFeed `quant_report.py`

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/AlphaFeed/backend/adapters/quant_report.py`

- [ ] **Step 1: Add the sys.path shim**

```python
import sys
from pathlib import Path

_MONOREPO_ROOT = Path(__file__).resolve().parents[3]  # quant_report.py → adapters → backend → AlphaFeed → Projetos
if str(_MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MONOREPO_ROOT))

try:
    from signal_tracker import log_signal as _log_signal
except ImportError:
    def _log_signal(*args, **kwargs):  # type: ignore[no-redef]
        return -1
```

- [ ] **Step 2: After `score_opportunity()` produces each opportunity, call `log_signal`**

Find the loop that iterates scored opportunities (look for `quantScore` and `signalTier`). Add:

```python
try:
    _log_signal(
        system="alphafeed",
        signal_type="polymarket",
        direction=opp.get("direction", "YES"),
        estimated_edge=float(opp.get("quantScore", 0)),
        estimated_prob=float(opp.get("quantScore", 0)),
        market_price=float(opp.get("curPrice", 0.5)),
        market_slug=opp.get("slug"),
        condition_id=opp.get("conditionId"),
        signal_tier=opp.get("signalTier"),
        raw_features={k: v for k, v in opp.items()
                      if k in ("countSignal", "sizeSignal", "info_ratio", "volume_24h",
                               "liquidity", "days_left", "contraryFlag")},
    )
except Exception as exc:
    log.debug("log_signal failed: %s", exc)
```

(Note: `quantScore` is in [0, 1] — it's used as `estimated_edge` here as a placeholder. The spec calls this "system's probability estimate". If AlphaFeed has a separate edge field, use that instead. Don't overthink — this can be tuned later.)

- [ ] **Step 3: Run AlphaFeed adapter end-to-end**

Run: `cd "d:/OMNP - Quant/Projetos/AlphaFeed" && python backend/adapters/quant_report.py`

Expected: completes without error; check the temp DB or `signal_tracker.db` afterward to see rows.

- [ ] **Step 4: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add AlphaFeed/backend/adapters/quant_report.py
git commit -m "feat(alphafeed): log scored opportunities to signal_tracker"
```

---

### Task 12: Integrate `signal_tracker` into Poly2 `polymarket_telegram_bot.py`

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/Poly2/polymarket_telegram_bot.py`

- [ ] **Step 1: Add the sys.path shim**

```python
import sys
from pathlib import Path

_MONOREPO_ROOT = Path(__file__).resolve().parents[1]  # bot.py → Poly2 → Projetos
if str(_MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MONOREPO_ROOT))

try:
    from signal_tracker import log_signal as _log_signal
except ImportError:
    def _log_signal(*args, **kwargs):  # type: ignore[no-redef]
        return -1
```

- [ ] **Step 2: After the Kelly bet is computed in `_match_and_size()`, log the signal**

Locate `_match_and_size()` (or similar — the function that produces the final bet dict). After the bet is sized:

```python
try:
    _log_signal(
        system="poly2",
        signal_type="polymarket",
        direction=bet["direction"],  # adapt to actual key
        estimated_edge=bet["edge"],
        estimated_prob=bet["gemini_prob"],
        market_price=market["price"],
        market_slug=market["slug"],
        condition_id=market.get("conditionId"),
        kelly_bet=bet["size_usd"],
        kelly_fraction=bet.get("kelly_fraction"),
        raw_features={"category": market.get("category"), "gemini_prob": bet["gemini_prob"]},
    )
except Exception as exc:
    logging.debug("log_signal failed: %s", exc)
```

(Field names depend on Poly2's bet dict. Adapt to the actual structure.)

- [ ] **Step 3: Smoke test**

Run: `cd "d:/OMNP - Quant/Projetos/Poly2" && python polymarket_telegram_bot.py --dry-run 2>&1 | tail -20`

Expected: completes without error.

- [ ] **Step 4: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add Poly2/polymarket_telegram_bot.py
git commit -m "feat(poly2): log Kelly-sized bets to signal_tracker"
```

---

### Task 13: Integrate `signal_tracker` into Poly `polymarket_scraper.py`

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/Poly/polymarket_scraper.py`

- [ ] **Step 1: Add the sys.path shim**

```python
import sys
from pathlib import Path

_MONOREPO_ROOT = Path(__file__).resolve().parents[1]  # scraper.py → Poly → Projetos
if str(_MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MONOREPO_ROOT))

try:
    from signal_tracker import log_signal as _log_signal
except ImportError:
    def _log_signal(*args, **kwargs):  # type: ignore[no-redef]
        return -1
```

- [ ] **Step 2: After `find_opportunities()` produces each entry, log it**

Adapt to the scraper's actual opportunity shape. Critically: pass `market.slug` directly (do not derive from URL — see spec B4):

```python
for opp in opportunities:
    try:
        _log_signal(
            system="poly",
            signal_type="polymarket",
            direction=opp.direction or "YES",
            estimated_edge=opp.heuristic_edge,
            market_price=opp.market_price,
            market_slug=opp.market.slug,  # explicit — never URL-derived
            condition_id=getattr(opp.market, "condition_id", None),
            raw_features={"heuristic": opp.heuristic_name},
        )
    except Exception as exc:
        logging.debug("log_signal failed: %s", exc)
```

- [ ] **Step 3: Smoke test**

Run: `cd "d:/OMNP - Quant/Projetos/Poly" && python polymarket_scraper.py 2>&1 | tail -10`

Expected: completes without error.

- [ ] **Step 4: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add Poly/polymarket_scraper.py
git commit -m "feat(poly): log scraper opportunities to signal_tracker (explicit slug)"
```

---

### Task 14: Integrate `signal_tracker` into ModelTelegra `model3_risk.py`

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/ModelTelegra,/quant_desk/models/model3_risk.py`

- [ ] **Step 1: Add the sys.path shim (parents[3] for this depth)**

```python
import sys
from pathlib import Path

_MONOREPO_ROOT = Path(__file__).resolve().parents[3]  # model3_risk.py → models → quant_desk → ModelTelegra, → Projetos
if str(_MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MONOREPO_ROOT))

try:
    from signal_tracker import log_signal as _log_signal
except ImportError:
    def _log_signal(*args, **kwargs):  # type: ignore[no-redef]
        return -1
```

- [ ] **Step 2: After `generate_trade_decision()` produces the BTC straddle decision, log it**

```python
# After the straddle decision is computed
try:
    _log_signal(
        system="modeltelegra",
        signal_type="straddle",
        direction="STRADDLE",
        estimated_edge=decision["expected_edge"],     # adapt to actual key
        market_price=spot_price,
        ticker="BTC-USD",
        kelly_bet=decision["premium_usd"],            # premium outlay = max loss
        cost_estimate=0.005,                          # ~0.5% slippage
        raw_features={
            "straddle_cost": decision["straddle_cost_pct"],  # premium / spot
            "iv": decision.get("iv"),
            "gex": decision.get("gex"),
        },
    )
except Exception as exc:
    log.debug("log_signal failed: %s", exc)
```

- [ ] **Step 3: For each trend signal above threshold, log a `trend_direction` row**

```python
for trend in trend_signals:  # adapt to actual iterable
    try:
        _log_signal(
            system="modeltelegra",
            signal_type="trend_direction",
            direction="LONG" if trend["bullish"] else "SHORT",
            estimated_edge=trend["confidence"],
            market_price=trend["spot_price"],
            ticker=trend["ticker"],  # 'BTC-USD', 'SPY', etc.
            kelly_bet=trend.get("size_usd", 0.0),
            raw_features={"timeframe": trend["timeframe"]},  # MUST be 1H, 1D, or 1W
        )
    except Exception as exc:
        log.debug("log_signal failed: %s", exc)
```

(Adapt field names to ModelTelegra's actual trend signal struct.)

- [ ] **Step 4: Smoke test**

Run: `cd "d:/OMNP - Quant/Projetos/ModelTelegra,/quant_desk" && python scheduler/runner.py --once 2>&1 | tail -20`

Expected: completes without error.

- [ ] **Step 5: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add "ModelTelegra,/quant_desk/models/model3_risk.py"
git commit -m "feat(modeltelegra): log straddle + trend signals to signal_tracker"
```

---

### Task 15: Wire scheduler resolver

**Files:**
- Modify: `d:/OMNP - Quant/Projetos/scheduler.py`

- [ ] **Step 1: Add resolution + metrics call at the end of `run_all()`**

Read `scheduler.py` to find the `run_all()` (or equivalent main orchestration) function. After every subsystem subprocess completes, append:

```python
    # ── Phase 1: signal tracker resolution + daily metrics ───────────────────
    try:
        from signal_tracker import (
            resolve_polymarket_signals,
            resolve_modeltelegra_signals,
            update_daily_metrics,
        )
        resolved = resolve_polymarket_signals() + resolve_modeltelegra_signals()
        update_daily_metrics()
        log.info("signal_tracker: resolved %d signals", resolved)
    except Exception as exc:
        log.warning("signal_tracker resolution failed: %s", exc)
```

- [ ] **Step 2: Run scheduler in dry-run / once mode**

Run: `cd "d:/OMNP - Quant/Projetos" && python scheduler.py --once 2>&1 | tail -30`

Expected: completes; the `signal_tracker:` log line appears at the end. Resolution count may be 0 if no markets have closed yet — that's fine.

- [ ] **Step 3: Commit**

```bash
cd "d:/OMNP - Quant/Projetos"
git add scheduler.py
git commit -m "feat(scheduler): call signal_tracker resolvers at end of run_all"
```

---

## Phase D — End-to-end Verification

### Task 16: Integration validation against a real `signal_tracker.db`

**Files:** none modified — pure validation step.

- [ ] **Step 1: Delete any stale dev DB so we observe a fresh run**

```bash
rm -f "d:/OMNP - Quant/Projetos/signal_tracker.db"*
```

- [ ] **Step 2: Run the full scheduler once**

Run: `cd "d:/OMNP - Quant/Projetos" && python scheduler.py --once 2>&1 | tee /tmp/phase1_smoke.log`

Expected: completes; log mentions `signal_tracker:` line.

- [ ] **Step 3: Inspect the resulting database**

```bash
cd "d:/OMNP - Quant/Projetos"
sqlite3 signal_tracker.db <<'SQL'
SELECT system, COUNT(*) AS n,
       AVG(estimated_edge) AS avg_edge,
       AVG(cost_estimate) AS avg_cost,
       AVG(net_edge) AS avg_net
FROM signals
GROUP BY system;
SQL
```

Verify:
- All running systems have at least one row.
- `avg_cost > 0` for `polytraders`, `hedgepoly`, `alphafeed`, `poly2`, `poly`.
- `avg_cost = 0` for `modeltelegra` trend signals (informational only); straddle signals have `cost_estimate ≈ 0.005`.
- `avg_net < avg_edge` for every Polymarket-style system.

- [ ] **Step 4: Verify market structure populated for PolyTraders**

```bash
sqlite3 signal_tracker.db "SELECT json_extract(raw_features, '$.market_structure') AS struct, COUNT(*) FROM signals WHERE system='polytraders' GROUP BY struct;"
```

Expected: a mix of structures — should NOT be 100% "Unknown" (a few "Unknown" is fine — those failed CLOB API).

- [ ] **Step 5: Run all tests one last time**

Run: `cd "d:/OMNP - Quant/Projetos" && python -m pytest tests/ -v`

Expected: all green.

- [ ] **Step 6: Commit a manual run-log snapshot (optional)**

If you want a record of the verification run, save the relevant DB stats to a file the user can review:

```bash
cd "d:/OMNP - Quant/Projetos"
sqlite3 signal_tracker.db ".schema signals" > docs/superpowers/plans/2026-04-21-phase1-verification.txt
sqlite3 signal_tracker.db "SELECT system, COUNT(*), AVG(estimated_edge), AVG(net_edge) FROM signals GROUP BY system;" >> docs/superpowers/plans/2026-04-21-phase1-verification.txt
git add docs/superpowers/plans/2026-04-21-phase1-verification.txt
git commit -m "chore: capture Phase 1 verification snapshot"
```

---

## Success Criteria (recap from spec)

After completing every task:

1. `signal_tracker.db` exists at the monorepo root with schema_version=1 and WAL mode active.
2. After one scheduler run, `signals` table has rows from all six systems.
3. `cost_estimate > 0` for every Polymarket signal; `net_edge < estimated_edge` strictly.
4. PolyTraders Telegram report shows non-default `Structure:` lines.
5. Some signals are filtered out by the cost deduction (would have triggered before).
6. Unit + integration tests all pass: `pytest tests/ -v` is green.
7. The scheduler resolver runs at end of every cycle and prints the resolution count.

---

## Risks & Mitigations (from spec)

| Risk | Mitigation |
|------|------------|
| Subsystem fails to import `signal_tracker` (depth wrong, env mismatch) | Every integration uses `try/except ImportError` with a no-op shim. |
| Yfinance rate limits when resolving ModelTelegra | Resolver caps at 50 signals/run with implicit per-call delays. |
| Gamma API throttles Polymarket resolver | 0.2s sleep between calls, 50/run cap. |
| Cost model filters too aggressively | Phase 2 will tune from tracked outcomes; for now it's intentionally conservative. |
| Concurrent writes from parallel subsystems | WAL + busy_timeout=5000 lets multiple writers queue safely. |
| Tests pollute production DB | `fresh_tracker` fixture rebinds `DB_PATH` to `tmp_path`; never touches the real file. |

---

## Notes for the Implementer

- The integration calls in Phase C use field names that **may not exactly match** each subsystem's data structures. Read the file you're editing first; adapt the field names to what actually exists. Do NOT add new fields to existing dataclasses — pull from what's already there.
- Every `_log_signal` call lives inside a `try/except` that logs at debug level. **Tracking failures must never crash the main pipeline.** That's the contract.
- If a smoke test fails because of unrelated network/data issues (not Phase 1 code), document it in the commit message and move on. Phase 1 is structural — outcome quality is Phase 2.
- Don't try to be clever about field name guessing across systems. If a field doesn't exist, leave it out (e.g., `condition_id=None`); only `system`, `signal_type`, `direction`, `estimated_edge`, `market_price` are mandatory.
