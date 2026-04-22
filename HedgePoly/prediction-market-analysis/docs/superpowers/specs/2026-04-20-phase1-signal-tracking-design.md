# Phase 1: Signal Tracking, Transaction Costs & Market Context

**Date**: 2026-04-20
**Scope**: Cross-system foundation for all five QuantDesk subsystems
**Depends on**: Nothing (foundational)
**Enables**: Phase 2 (validation), Phase 3 (model fixes), Phase 4 (portfolio layer)

---

## Problem Statement

Five quantitative subsystems (HedgePoly, PolyTraders, ModelTelegra, AlphaFeed, Poly2/Poly) generate trading signals daily. None of them track whether predictions are correct after resolution. All compute Kelly sizing on gross edge without deducting transaction costs. PolyTraders has a sophisticated market-context module that is imported but never called.

Without outcome tracking, the system cannot distinguish alpha from noise. Without cost deduction, Kelly oversizes on negative-EV opportunities. Without market context, edge estimates ignore price structure.

## Goals

1. **Signal tracking database**: Record every signal from every system with enough detail to replay/audit later. Automatically resolve outcomes and compute performance metrics.
2. **Transaction cost model**: Estimate round-trip costs per market type. Deduct costs before Kelly sizing so only positive net-edge signals generate bets.
3. **Enable market_context.py**: Wire up the existing PolyTraders price-structure classifier so edge estimates reflect compression, exhaustion, pullback, and breakout regimes.

## Non-Goals

- No UI dashboard for signal tracking (Phase 2+)
- No portfolio-level correlation or VaR (Phase 4)
- No model retraining or parameter recalibration (Phase 2)
- No Deribit API integration (Phase 3)

---

## Component 1: Signal Tracking Database

### Location

`d:/OMNP - Quant/Projetos/signal_tracker.py` — single module in the monorepo root, alongside `scheduler.py` and `bot.py`.

`d:/OMNP - Quant/Projetos/signal_tracker.db` — SQLite database file (gitignored).

### Schema

```sql
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

CREATE INDEX IF NOT EXISTS idx_signals_system ON signals(system);
CREATE INDEX IF NOT EXISTS idx_signals_slug ON signals(market_slug);
CREATE INDEX IF NOT EXISTS idx_signals_unresolved ON signals(outcome) WHERE outcome IS NULL;
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (1, datetime('now'));
```

### Connection Pragmas

Every connection opened by `signal_tracker.py` MUST set these pragmas immediately after `sqlite3.connect()` and before any DDL/DML:

```python
conn.execute("PRAGMA journal_mode=WAL")        # concurrent readers + 1 writer
conn.execute("PRAGMA busy_timeout=5000")       # wait up to 5s on locked DB
conn.execute("PRAGMA synchronous=NORMAL")      # safe with WAL, faster than FULL
conn.execute("PRAGMA foreign_keys=ON")
```

WAL mode is critical: the scheduler runs subsystems in parallel, so multiple Python processes may write concurrently. Without WAL, writers block readers and trigger `database is locked` errors.

### Schema Migrations

The `schema_version` table tracks the current schema generation. On import, `signal_tracker.py` checks `SELECT MAX(version) FROM schema_version` and applies pending migrations from a `_MIGRATIONS` list. Phase 1 ships at version 1. Future schema changes (Phase 2+) bump the version and append to the migration list.

### Column Semantics

| Column | Type | Description |
|--------|------|-------------|
| `system` | TEXT | One of: `hedgepoly`, `polytraders`, `modeltelegra`, `alphafeed`, `poly2`, `poly` |
| `signal_type` | TEXT | `polymarket` for prediction market signals, `straddle` for BTC vol plays, `trend_direction` for directional calls |
| `market_slug` | TEXT | Polymarket slug for PM signals (used for resolution lookup). NULL for non-PM. |
| `ticker` | TEXT | Asset ticker for ModelTelegra signals (`BTC-USD`, `SPY`, etc.). NULL for PM. |
| `condition_id` | TEXT | Polymarket condition_id (used for API resolution queries). NULL for non-PM. |
| `direction` | TEXT | `YES`, `NO` for PM; `LONG`, `SHORT` for directional; `STRADDLE` for vol plays |
| `estimated_edge` | REAL | Gross edge before transaction costs |
| `estimated_prob` | REAL | System's probability estimate (e.g., AlphaFeed quantScore, Poly2 Gemini prob) |
| `market_price` | REAL | YES-side market price at signal time (always in [0, 1] for PM, USD for assets). Direction-independent. |
| `entry_price` | REAL | Effective entry price for the chosen direction. For PM: `market_price` if YES, `1 - market_price` if NO. For assets: same as `market_price`. Used by resolver to compute PnL correctly for both sides. |
| `kelly_bet` | REAL | Recommended bet in USD |
| `kelly_fraction` | REAL | Kelly fraction (after multiplier, before bankroll) |
| `signal_tier` | TEXT | `A`, `B`, `C` for AlphaFeed; `HIGH`, `MEDIUM`, `LOW` for others; NULL if not tiered |
| `raw_features` | TEXT | JSON blob of all inputs that produced this signal (for replay/audit) |
| `cost_estimate` | REAL | Estimated round-trip transaction cost (fraction, e.g., 0.02 = 2%) |
| `net_edge` | REAL | `estimated_edge - cost_estimate` |
| `created_at` | TEXT | ISO-8601 UTC timestamp |
| `resolved_at` | TEXT | ISO-8601 UTC when outcome was determined. NULL until resolved. |
| `outcome` | TEXT | `WIN`, `LOSS`, or NULL. For straddles: WIN if abs(move) > cost. |
| `actual_pnl` | REAL | Hypothetical P&L based on kelly_bet and outcome. NULL until resolved. |
| `resolution_data` | TEXT | JSON blob: closing price, resolution source, actual probability, etc. |

### Public API

```python
# signal_tracker.py

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
    """Log a signal. Returns the signal ID.

    Validation:
    - signal_type='polymarket' REQUIRES market_slug (resolver needs it to query Gamma API).
      Raises ValueError if missing. condition_id is optional (slug is the primary key).
    - signal_type='straddle' REQUIRES raw_features['straddle_cost'] (premium paid as fraction
      of underlying — used by resolver to compute breakeven and PnL). Raises ValueError if missing.
    - signal_type='trend_direction' REQUIRES raw_features['timeframe'] in {'1H', '1D', '1W'}
      so the resolver knows how far ahead to look up the realized price.

    entry_price is computed automatically from (market_price, direction):
    - PM YES: entry_price = market_price
    - PM NO:  entry_price = 1 - market_price
    - Assets: entry_price = market_price (LONG/SHORT use market_price as cost basis)
    """

def resolve_polymarket_signals() -> int:
    """Check all unresolved Polymarket signals against Gamma API.
    Returns count of newly resolved signals."""

def resolve_modeltelegra_signals() -> int:
    """Check ModelTelegra straddle/trend signals against price data.
    Returns count of newly resolved signals."""

def update_daily_metrics(date: str | None = None) -> None:
    """Recompute daily_metrics for the given date (default: today)."""

def get_performance_summary(system: str | None = None, days: int = 30) -> dict:
    """Return win rate, avg edge, Sharpe, total PnL for recent signals."""
```

### Resolution Logic

**Polymarket signals** (`signal_type = 'polymarket'`):
- Query `GET https://gamma-api.polymarket.com/markets?slug={slug}`
- If market has `closed=true` and `resolutionSource` is set:
  - Parse `outcomePrices` — if YES outcome settled to 1.0, resolution is YES, else NO
  - Compare against `direction`: if direction matches resolution → WIN, else LOSS
  - **PnL formula uses `entry_price`, NOT `market_price`** (handles YES and NO symmetrically):
    - WIN: `actual_pnl = kelly_bet * (1.0 / entry_price - 1.0)`
    - LOSS: `actual_pnl = -kelly_bet`
  - Worked example (NO bet at market_price=0.30):
    - `entry_price = 1 - 0.30 = 0.70` (cost of NO share)
    - If market resolves NO → WIN, payout = $1 per share, gain = `(1/0.70 - 1) ≈ 43%`
    - If market resolves YES → LOSS, lose entire `kelly_bet`

**ModelTelegra straddle signals** (`signal_type = 'straddle'`):
- `raw_features['straddle_cost']` MUST be present — it is the premium paid as a fraction of the
  underlying (e.g., 0.04 = 4% premium for an at-the-money straddle). This is the breakeven move.
- `cost_estimate` (slippage/spread) is NOT the breakeven — keep these distinct.
- Fetch BTC price 24h after `created_at` via yfinance
- `move = abs(price_24h_later - price_at_signal) / price_at_signal`
- `breakeven = raw_features['straddle_cost'] + cost_estimate` (premium + execution drag)
- If `move > breakeven`: WIN. Else: LOSS.
- **Convention**: `kelly_bet` for straddles is the **premium outlay** (max loss), not underlying
  notional. So a "lose everything" outcome (move = 0) yields `actual_pnl = -kelly_bet`.
- `actual_pnl = kelly_bet * (move / breakeven - 1)` for WIN
- `actual_pnl = -kelly_bet * (1 - move / breakeven)` for LOSS (clamped to `-kelly_bet` when move = 0)

**ModelTelegra trend signals** (`signal_type = 'trend_direction'`):
- `raw_features['timeframe']` MUST be present — one of `'1H'`, `'1D'`, `'1W'`.
- Lookup horizon: 1H signal → 1D later, 1D signal → 5D later, 1W signal → 30D later.
- Fetch price N bars later via yfinance using the mapped horizon.
- If direction is LONG and price went up → WIN. LONG and price went down → LOSS. Vice versa for SHORT.
- `actual_pnl = kelly_bet * abs(price_change / market_price)` for WIN, negative same magnitude for LOSS.

### Integration with Scheduler

At the end of `run_all()` in `scheduler.py`, add:

```python
from signal_tracker import resolve_polymarket_signals, resolve_modeltelegra_signals, update_daily_metrics

# After all projects run:
resolved = resolve_polymarket_signals() + resolve_modeltelegra_signals()
update_daily_metrics()
log.info(f"Signal tracker: {resolved} signals resolved")
```

### Integration with Each System

Each system calls `log_signal()` after generating an opportunity but before sending the Telegram report. Integration points:

| System | File | Location | What to log | Required keys |
|--------|------|----------|-------------|---------------|
| HedgePoly | `reporting.py` | After `_score_market()` produces an opportunity | Each scored market with calibration edge | `market_slug=market.slug`, `condition_id` if available |
| PolyTraders | `kelly.py` | After constructing each `Opportunity` | Each opportunity with count/size signals, edge, Kelly bet | `market_slug=opp.slug`, `condition_id=opp.condition_id` |
| ModelTelegra | `models/model3_risk.py` | After `generate_trade_decision()` | BTC straddle + each trend signal above threshold | `ticker='BTC-USD'`, `raw_features['straddle_cost']` for straddles, `raw_features['timeframe']` for trend |
| AlphaFeed | `backend/adapters/quant_report.py` | After `score_opportunity()` | Each scored opportunity with quantScore and features | `market_slug=opp['slug']`, `condition_id=opp.get('condition_id')` |
| Poly2 | `polymarket_telegram_bot.py` | After Kelly sizing in `_match_and_size()` | Each bet with Gemini prob, edge, Kelly size | `market_slug=market['slug']`, `condition_id=market['conditionId']` |
| Poly | `polymarket_scraper.py` | After `find_opportunities()` | Each opportunity with heuristic edge | `market_slug=market.slug` (the scraper already exposes a `slug` field — pass it explicitly, do NOT derive from URL) |

### Importing signal_tracker from Subprojects

The five subsystems live in sibling directories under `d:/OMNP - Quant/Projetos/`. They are not
installed as packages, so a plain `from signal_tracker import log_signal` will fail. Each
integration point MUST add the monorepo root to `sys.path` before importing:

```python
# At the top of reporting.py / kelly.py / model3_risk.py / quant_report.py /
# polymarket_telegram_bot.py / polymarket_scraper.py
import sys
from pathlib import Path

_MONOREPO_ROOT = Path(__file__).resolve().parents[2]   # adjust depth per file
if str(_MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MONOREPO_ROOT))

from signal_tracker import log_signal
from cost_model import estimate_cost
```

Per-file `parents[N]` depth:

| File | Depth | Reason |
|------|-------|--------|
| `HedgePoly/prediction-market-analysis/reporting.py` | `parents[2]` | reporting.py → prediction-market-analysis → HedgePoly → Projetos |
| `PolyTraders/kelly.py` | `parents[1]` | kelly.py → PolyTraders → Projetos |
| `ModelTelegra,/quant_desk/models/model3_risk.py` | `parents[3]` | model3_risk.py → models → quant_desk → ModelTelegra, → Projetos |
| `AlphaFeed/backend/adapters/quant_report.py` | `parents[3]` | quant_report.py → adapters → backend → AlphaFeed → Projetos |
| `Poly2/polymarket_telegram_bot.py` | `parents[1]` | bot.py → Poly2 → Projetos |
| `Poly/polymarket_scraper.py` | `parents[1]` | scraper.py → Poly → Projetos |

Wrap the import itself in `try/except ImportError` and degrade gracefully — if `signal_tracker`
fails to import (e.g., AlphaFeed running in GitHub Actions without the monorepo root),
log a warning and define a no-op `log_signal` shim so the main pipeline still runs.

### Deduplication

The same Polymarket market may appear in multiple systems (HedgePoly, PolyTraders, AlphaFeed, Poly2). Each system logs independently — this is intentional. The `system` column distinguishes them. Cross-system comparison ("did PolyTraders and AlphaFeed agree on this market?") is an analysis query, not a logging concern.

Within a single system run, deduplicate by `(system, market_slug, direction, date(created_at))` — don't log the same signal twice in the same run.

---

## Component 2: Transaction Cost Model

### Location

`d:/OMNP - Quant/Projetos/cost_model.py` — shared module in monorepo root.

### Cost Estimation

```python
# cost_model.py

def estimate_cost(
    market_type: str,
    price: float,
    size_usd: float,
    liquidity: float = 0.0,
    spread: float = 0.0,
) -> float:
    """Estimate round-trip transaction cost as a fraction.

    Returns a value like 0.02 meaning 2% round-trip cost.

    market_type: 'polymarket', 'btc_straddle', 'btc_spot', 'equity_spot'
    price: current market price (0-1 for PM, USD for assets)
    size_usd: position size in USD
    liquidity: available liquidity in USD (PM order book depth)
    spread: bid-ask spread as fraction (0.02 = 2pp for PM)
    """
```

### Cost Components by Market Type

**Polymarket** (`market_type = 'polymarket'`):
```
base_spread = max(spread / 2, 0.005)       # half-spread, min 0.5%
gas_fee = 0.05 / max(size_usd, 1)          # ~$0.05 gas as % of bet
impact = size_usd / max(liquidity, 1) * 0.5 # linear price impact model
cost = 2 * (base_spread + gas_fee + impact) # round-trip (entry + exit)
```

Rationale:
- Half-spread: you cross the spread on entry. Minimum 0.5% for markets with no spread data.
- Gas: Polygon L2 transaction, ~$0.05 fixed. Material for small bets.
- Impact: Linear model — betting $100 in a $1000 liquidity pool moves price ~5%.
- Round-trip: multiply by 2 (enter and exit).

**BTC Straddle** (`market_type = 'btc_straddle'`):
```
cost = 0.025  # 2.5% flat estimate (1% spread per leg + 0.5% slippage)
```

Flat estimate until Phase 3 integrates real Deribit order book data. Conservative — actual costs may be lower on liquid strikes.

**Spot signals** (`market_type in ('btc_spot', 'equity_spot')`):
```
cost = 0.0  # informational signals only, not traded
```

ModelTelegra trend signals are informational — no direct execution. Cost stays 0 for tracking purposes, but signals are still logged for accuracy measurement.

### Units: Probability Space vs Return Space

Edge and cost are in **different units** and must be reconciled before subtracting:

- `estimated_edge` is in **probability space** for prediction markets (e.g., 0.10 means
  "I think the true probability is 10pp higher than the market implies").
- `estimate_cost` returns a fraction in **return space** (e.g., 0.02 means "2% drag on PnL
  per round-trip, measured as a fraction of bet size").

For Polymarket, the conversion is direct because a 1pp change in probability ≈ 1pp change in
expected payout per dollar staked at prices near 0.5. At extreme prices the conversion is
non-linear (a 1pp prob edge at p=0.95 is worth less than at p=0.50 because the payout
multiple `1/p - 1` shrinks). For Phase 1 we use the linear approximation `net_edge = edge - cost`
and document the bias: cost deduction is slightly too generous at price extremes. Phase 2
will refine to `net_edge = edge - cost * (1 / p)` once tracked outcomes confirm the magnitude.

For BTC straddles, both `estimated_edge` and `cost_estimate` are already in return-space
fractions of the underlying — no conversion needed.

For ModelTelegra trend signals, `cost_estimate = 0` (informational), so the units question
is moot.

### Resolving the Cost ↔ Kelly Circular Dependency

The cost model's `impact` term needs `size_usd` to estimate slippage. But `size_usd` (the
Kelly bet) depends on `net_edge`, which depends on `cost`. This is circular.

**Two-pass resolution** — every system uses this pattern:

```python
from cost_model import estimate_cost

# Direction-aware entry math: side_price is what you actually pay per share.
# YES side: pay cur_price, win 1.0 if YES resolves
# NO  side: pay (1 - cur_price), win 1.0 if NO resolves
side_price = cur_price if direction == 'YES' else (1.0 - cur_price)
b = (1.0 - side_price) / side_price          # payout odds for the side held
p_gross = min(side_price + estimated_edge, 0.99)  # `estimated_edge` is on the side held

# PASS 1 — provisional Kelly using GROSS edge (overestimates bet, that's OK)
kelly_gross_frac = max(0, (b * p_gross - (1 - p_gross)) / b)
provisional_bet = kelly_gross_frac * bankroll * KELLY_MULTIPLIER

# Cost estimated against the provisional bet (the realistic upper bound)
cost = estimate_cost(
    'polymarket',
    cur_price,
    provisional_bet,
    liquidity=liquidity,
    spread=spread,
)

# PASS 2 — re-size with NET edge (uses the same side_price/b as pass 1)
net_edge = max(0.0, estimated_edge - cost)
if net_edge < MIN_NET_EDGE:
    continue  # skip — negative EV after costs

p_net = min(side_price + net_edge, 0.99)
kelly_net_frac = max(0, (b * p_net - (1 - p_net)) / b)
kelly_bet = kelly_net_frac * bankroll * KELLY_MULTIPLIER
```

Why this converges in one pass: the provisional bet is always ≥ the final bet (gross_edge ≥
net_edge ⇒ gross_kelly ≥ net_kelly). Using the larger bet for the impact estimate is
**conservative** — we slightly overestimate cost and slightly undersize the position. That
bias is acceptable for Phase 1; iterating to a fixed point would buy us at most a few percent
on bet size and isn't worth the complexity.

Both `cost_estimate` and `net_edge` are stored in the signal tracker for analysis. The
provisional bet is NOT stored.

---

## Component 3: Enable market_context.py in PolyTraders

### Current State

`kelly.py` line 1:
```python
from market_context import classify_prediction_market
```

The import exists. The function is never called. The `Opportunity` dataclass has fields for `market_structure`, `context_quality`, `context_note` that are always default values.

### Change

In `kelly.py`, inside `score_opportunities()`, after computing `estimated_edge` and before the Kelly formula:

```python
# Classify market structure and adjust edge
try:
    context = classify_prediction_market(
        condition_id=condition_id,
        cur_price=cur_price,
        wav_entry=wav_entry,
        total_exposure=total_val,
    )
    estimated_edge *= context.edge_mult
    market_structure = context.structure
    context_quality = context.quality
    context_note = context.note
except Exception:
    market_structure = "Unknown"
    context_quality = "acceptable"
    context_note = ""
```

### Edge Multipliers (from existing market_context.py)

| Structure | edge_mult | Quality | When |
|-----------|-----------|---------|------|
| EXHAUSTION | 0.45 | avoid | 24h move >12pp AND >8pp above smart money entry |
| COMPRESSION | 1.30 | ideal | 7d range <5pp AND ATR 1h <0.5pp |
| BREAKOUT (confirmed) | 1.10 | acceptable | 24h move >8pp, still near smart money entry |
| BREAKOUT (late) | 0.75 | avoid | 24h move >8pp, buying far above smart money |
| PULLBACK | 1.55 | ideal | 7d trend >6pp, 24h flat (\|move\| ≤ 1pp), price within 1pp of smart money entry (entry_overshoot threshold) |
| TREND (good entry) | 1.10 | acceptable | 7d move >4pp, <3pp above smart money |
| TREND (late entry) | 0.80 | acceptable | 7d move >4pp, >3pp above smart money |
| UNKNOWN | 1.00 | acceptable | No pattern matched |

### Telegram Report Update

Add market structure to the PolyTraders Telegram report for each opportunity:

```
Structure: PULLBACK (ideal) — edge boosted 55%
```

or:

```
Structure: EXHAUSTION (avoid) — edge reduced 55%
```

### Fallback Behavior

If the CLOB API call fails (network error, rate limit), the fallback heuristic in `_classify_from_positions()` is used. This only needs `cur_price` and `wav_entry` — data already available. No API call required for fallback.

---

## Testing Strategy

### Unit Tests

**signal_tracker tests** (`tests/test_signal_tracker.py`):
- `test_log_signal_returns_id` — basic insert + ID return
- `test_deduplication` — same signal in same run doesn't duplicate
- `test_resolve_polymarket_win` — mock Gamma API response, verify WIN outcome
- `test_resolve_polymarket_loss` — verify LOSS outcome
- `test_resolve_straddle_win` — BTC moved > cost → WIN
- `test_resolve_straddle_loss` — BTC moved < cost → LOSS
- `test_daily_metrics_computation` — verify win rate, avg edge calculations
- `test_performance_summary` — verify 30-day rolling metrics
- `test_concurrent_writes` — two threads writing simultaneously don't corrupt

**cost_model tests** (`tests/test_cost_model.py`):
- `test_polymarket_liquid` — low cost on $100K liquidity market
- `test_polymarket_illiquid` — high cost on $1K liquidity market
- `test_polymarket_small_bet` — gas fee dominates on $1 bet
- `test_btc_straddle_flat` — returns 0.025
- `test_spot_zero` — informational signals have zero cost
- `test_cost_always_positive` — cost >= 0 for all inputs
- `test_cost_reasonable_range` — cost < 0.50 (50%) for any realistic input

**market_context integration tests** (`tests/test_market_context_integration.py`):
- `test_kelly_calls_classify` — verify classify_prediction_market is called
- `test_exhaustion_reduces_edge` — edge × 0.45 when exhaustion detected
- `test_compression_boosts_edge` — edge × 1.30 when compression detected
- `test_api_failure_uses_fallback` — network error → fallback heuristic
- `test_context_in_opportunity` — Opportunity dataclass populated correctly

### Integration Test

Run `scheduler.py --once` locally. Verify:
1. Each system logs signals to `signal_tracker.db`
2. `signals` table has rows for each system
3. `cost_estimate` and `net_edge` are populated
4. PolyTraders opportunities have non-default `market_structure` values
5. Resolution runs without error (may resolve 0 signals if none are closed yet)

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `signal_tracker.py` | NEW | Signal tracking module with SQLite backend |
| `cost_model.py` | NEW | Transaction cost estimation |
| `tests/test_signal_tracker.py` | NEW | Signal tracker unit tests |
| `tests/test_cost_model.py` | NEW | Cost model unit tests |
| `tests/test_market_context_integration.py` | NEW | Market context wiring tests |
| `scheduler.py` | EDIT | Add resolution + metrics update after run_all() |
| `PolyTraders/kelly.py` | EDIT | Wire up market_context, integrate cost_model, call log_signal |
| `PolyTraders/main.py` | EDIT | Pass cost_model to kelly pipeline |
| `HedgePoly/prediction-market-analysis/reporting.py` | EDIT | Call log_signal for each scored market |
| `ModelTelegra,/quant_desk/models/model3_risk.py` | EDIT | Call log_signal for trade decisions |
| `AlphaFeed/backend/adapters/quant_report.py` | EDIT | Call log_signal for scored opportunities |
| `Poly2/polymarket_telegram_bot.py` | EDIT | Call log_signal after Kelly sizing |
| `Poly/polymarket_scraper.py` | EDIT | Call log_signal after find_opportunities |
| `.gitignore` | EDIT | Add `signal_tracker.db` |

---

## Success Criteria

1. After one full scheduler run, `signal_tracker.db` contains signals from all active systems
2. Each signal has a non-zero `cost_estimate` (except ModelTelegra spot signals)
3. `net_edge` is strictly less than `estimated_edge` for all Polymarket signals
4. PolyTraders opportunities show market structure classifications in Telegram reports
5. Some signals that previously passed the edge threshold are now filtered out by cost deduction
6. After markets resolve (days/weeks later), the resolver backfills outcomes automatically
7. `get_performance_summary()` returns meaningful win rate and PnL data once enough signals resolve

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| SQLite file corruption on crash | WAL mode enabled by default; atomic writes |
| Gamma API rate limit during resolution | Batch queries with 0.2s sleep; max 50 per run |
| market_context API calls slow down PolyTraders | 12s timeout per market; fallback heuristic if timeout |
| Cost model too aggressive (filters all signals) | Start conservative (lower bound estimates); tune based on tracked outcomes |
| Systems fail to import signal_tracker | Wrap all log_signal calls in try/except; never let tracking crash the main pipeline |
