# Quant Monorepo — Architecture, Data Flow & Refactor Plan

**Last audit:** 2026-05-25
**Scope:** AlphaFeed + Multi Factor + the 7 upstream signal projects + shared modules
**Status:** working production stack; refactor plan below

---

## 0. TL;DR — what this document is for

You're deploying this stack and need to understand:

1. **Where each project gets data** (8 external APIs, 4 internal state stores)
2. **Where projects step on each other** (4 duplicate fetches, 3 hardcoded paths)
3. **What the right cleanup order is** (the *Refactor Roadmap* below)
4. **What "deployment" actually requires** (the *Deployment Checklist* at the end)

If you read nothing else, read §1 (Refactor Roadmap) + §10 (Deployment Checklist).

---

## 1. Refactor Roadmap — execute in this order

Each row is one slice you'd brainstorm and ship independently. Each lands on its own branch + spec.

| # | Slice | Why first | Cost | Risk |
|---|---|---|---|---|
| **R1** | **Shared `lib/polymarket_client.py`** — wrap Gamma + CLOB + Leaderboard + Positions in one client with TTL caches | 3 of 4 duplicate-fetch issues solved at once; biggest network-cost reduction | 1–2 days | LOW (read-only API) |
| **R2** | **Fix Multi Factor hardcoded path** — `ROOT = Path("D:/Multi Factor")` becomes env-var driven | Blocks deployment on any non-Windows-D: machine | 30 min | LOW |
| **R3** | **Shared `lib/price_feed_cache.py`** — yfinance + Coinbase OHLC caches keyed by (symbol, date) | Eliminates the BTC-USD double-fetch; foundation for slice-2 of Multi Factor | 1 day | LOW |
| **R4** | **Centralize Telegram formatting** — Poly2 + PolyTraders adopt `lib/telegram_format` helpers (Poly Kelly + Multi Factor already do) | Visual consistency across all 9 channel messages | 1 day each (2 projects) | LOW |
| **R5** | **Normalize Python pin** — pick 3.11 OR 3.12 across the monorepo, drop the 3.9 outliers (Poly2) | Eliminates one class of subprocess startup overhead + drops 2 `uv --python` switches | 0.5 day | MED (Poly2 has PEP 604 quirk that may need fixing) |
| **R6** | **Drop committed .venv folders** — HedgePoly + Global Macro carry ~3GB of `.venv/` in git history | Repo size + clone time; should be in `.gitignore` only | 1 day (history rewrite optional) | MED (history rewrite has friction) |
| **R7** | **AlphaFeed model versioning** — move `xgboost_model.json`, `calibration_params.json` under `models/v{N}/` | Audit trail for model updates; supports A/B testing slice-2 model | 0.5 day | LOW |
| **R8** | **Consolidated rate-limit throttle** — currently sleep(0.2) / sleep(0.5) scattered; central `lib/rate_limit.py` | Marginal improvement; defer until R1 lands | 0.5 day | LOW |

**My recommendation:** R2 first (deployment-blocker, 30 min). Then R1 (biggest payoff). Defer R6 until you have a clear branch story to rewrite history.

---

## 2. Repo layout

```
D:/OMNP - Quant/Projetos/                  ← git root (feat/phase1-signal-tracking)
├── .env                                   ← root secrets (TELEGRAM_*, GEMINI_API_KEY)
├── scheduler.py                           ← cron orchestrator (12:00 + 22:00 UTC)
├── signal_tracker.py                      ← shared signal SQLite (signal_tracker.db)
├── signal_tracker.db                      ← WAL-mode SQLite, see §6.3
├── cost_model.py                          ← shared transaction-cost estimator
├── lib/                                   ← NEW — shared helpers (telegram_format today)
│   ├── __init__.py
│   ├── telegram_format.py
│   └── tests/
├── docs/                                  ← THIS DIR — architecture + style guides
│   ├── ARCHITECTURE.md                    ← this file
│   ├── TELEGRAM_STYLE_GUIDE.md
│   └── superpowers/{specs,plans}/         ← per-slice brainstorms + plans
│
├── HedgePoly/prediction-market-analysis/  ← Polymarket calibration alpha
│   ├── send_report.py                     ← entry: HedgePoly Alpha Report
│   ├── reporting.py                       ← build_telegram_report (HTML)
│   ├── notebooks/run_report.py            ← entry: Global Macro Quant Report
│   ├── notebooks/quant_helpers.py         ← 4-layer stack (regime+HAR+XGB+Kelly)
│   ├── smart_money.py                     ← Polymarket leaderboard+positions fetcher
│   └── .venv/                             ← ⚠ committed (~1.5GB)
│
├── Poly/                                  ← Poly Kelly Scraper
│   ├── polymarket_scraper.py              ← entry; uses lib.telegram_format
│   └── tests/                             ← golden-file regression (added 2026-05)
│
├── Poly2/                                 ← Macro intelligence reports
│   ├── polymarket_telegram_bot.py         ← entry: Poly2 Kelly Bot
│   ├── macro_report1.py                   ← entry: Poly2 Macro Report 1
│   └── macro_report2.py                   ← entry: Poly2 Macro Report 2
│
├── PolyTraders/                           ← Smart-money copy-trade
│   ├── main.py                            ← entry
│   ├── leaderboard.py                     ← Polymarket leaderboard fetcher
│   ├── positions.py                       ← Polymarket positions fetcher
│   └── kelly.py                           ← Kelly sizing
│
├── ModelTelegra,/quant_desk/              ← ⚠ comma in path; quote when scripting
│   ├── main.py                            ← entry: ModelTelegra Quant Desk
│   └── (charts.py, fetcher.py, model{1,2,3}*.py)
│
└── AlphaFeed/                             ← dashboard + 4 export adapters
    ├── backend/
    │   ├── server.py                      ← Flask, localhost:5000
    │   └── adapters/                      ← 4 export scripts
    │       ├── poly2_export.py            ← writes reports/poly2.json
    │       ├── polytraders_export.py      ← writes reports/polytraders.json
    │       ├── hedgepoly_export.py        ← writes reports/hedgepoly.json
    │       └── quant_report.py            ← reads above 2, writes reports/quant_report.json
    ├── models/                            ← XGBoost + calibration artifacts
    │   ├── xgboost_model.json
    │   ├── calibration_params.json
    │   └── training_metrics.json
    ├── frontend/                          ← React dashboard
    └── reports/                           ← JSON outputs (gitignored)

D:/Multi Factor/                           ← Multi Factor BTC Leverage (slice 1)
├── multifactor/                           ← package — see §9.1
│   ├── data/{deribit_public,price_feed}.py
│   ├── factors/{base,bs_edge,har_rv}.py
│   ├── engine/{orchestrator,scorer,risk}.py
│   ├── execution/paper.py
│   ├── delivery/{dashboard,telegram_bot}.py
│   ├── config/                            ← 3 YAMLs + pydantic Config
│   ├── state/                             ← snapshots, positions, HAR cache (gitignored)
│   └── refresh.py                         ← CLI entry: python -m multifactor.refresh
├── refresh_dashboard.py                   ← equity factor model (long-term + options swing)
├── dashboard.html                         ← combined 3-tab dashboard (regenerated)
└── docs/superpowers/{specs,plans}/        ← slice-1 design docs
```

**Two repos** — Projetos (orchestrator-side) and Multi Factor (BTC-side). Scheduler is in Projetos and shells out to Multi Factor via absolute path.

---

## 3. Project inventory (one-line health check)

| Project | Status | Delivery | Cron | Has tests? | Issues |
|---|---|---|---|---|---|
| HedgePoly Alpha Report | ✅ stable | own HTML POST | 2×/day | basic | committed .venv (R6) |
| Global Macro Quant Report | ✅ stable | own HTML POST | 2×/day | basic | committed .venv (R6) |
| Poly2 Kelly Bot | ✅ stable | own HTML POST | 2×/day | none | Python 3.9 (R5) |
| Poly2 Macro Report 1 | ⚠ over-long | own HTML POST | 2×/day | none | likely >3500 char (needs §9.4 audit) |
| Poly2 Macro Report 2 | ⚠ over-long | own HTML POST | 2×/day | none | likely >3500 char |
| ModelTelegra Quant Desk | ✅ stable | stdout-captured | 2×/day | none | comma in dir name (`ModelTelegra,`) |
| Poly Kelly Scraper | ✅ refactored 2026-05 | stdout-captured | 2×/day | golden files | none |
| PolyTraders Smart Money | ✅ stable | own HTML POST | 2×/day | none | duplicates HedgePoly fetches (R1) |
| Multi Factor BTC Leverage | ✅ slice 1 shipped 2026-05 | stdout-captured | 2×/day | 25 tests | hardcoded path (R2) |
| AlphaFeed adapters (×4) | ✅ stable | writes JSON | post-main | none | reads polytraders+poly2 — fragile if either fails |

---

## 4. External APIs — what hits what

| API | Endpoints | Auth | Callers | Frequency | Notes |
|---|---|---|---|---|---|
| **Polymarket Gamma** | `gamma-api.polymarket.com/markets`, `/events`, `/markets?slug=` | none | Poly Kelly, AlphaFeed/poly2_export, signal_tracker (resolution) | 2×/day per caller | ~800 markets × 2 fetchers daily = wasteful; **R1** target |
| **Polymarket CLOB** | `clob.polymarket.com/book`, `/midpoint`, `/price` | none | Poly Kelly only | 2×/day | per-token order book; needed for edge calc |
| **Polymarket Leaderboard** | `data-api.polymarket.com/v1/leaderboard` | none | PolyTraders, HedgePoly/smart_money, AlphaFeed/polytraders_export (via PolyTraders) | 2×/day per caller | **3 independent fetches** of same data — **R1** target |
| **Polymarket Positions** | `data-api.polymarket.com/positions?user=…` | none | PolyTraders, HedgePoly/smart_money | 2×/day per caller, ~100 traders × parallel-5-workers | **2 independent fetches** — **R1** target |
| **Gemini AI** | `generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent` | `GEMINI_API_KEY` | Poly2 (×3 scripts), ModelTelegra | 2×/day per script | FREE tier 250 req/day; risk of saturation |
| **Deribit Public** | `api.exchange.deribit.com/api/v2/public/get_book_summary_by_currency`, `/get_index_price` | none | Multi Factor (data/deribit_public.py) | 2×/day | exponential backoff retry on transport errors |
| **Coinbase Exchange** | `api.exchange.coinbase.com/products/BTC-USD/candles` | none | Multi Factor (data/price_feed.py) | 2×/day | 730-day OHLC, paginated 300/req, cached |
| **yfinance** (→ Yahoo Finance internally) | implicit | none | HedgePoly/Global Macro, signal_tracker (BTC-USD resolution) | 1×/day each | **BTC-USD fetched 2× same day** — **R3** target |
| **Telegram Bot** | `api.telegram.org/bot{token}/sendMessage` | `TELEGRAM_BOT_TOKEN` | scheduler._tg_send + every project's own POST | once per project completion | 4000-char chunking; plain text via scheduler, HTML via own POSTs |

---

## 5. Internal state — what writes where

| Store | Location | Producer(s) | Consumer(s) | Format | Atomic? |
|---|---|---|---|---|---|
| `AlphaFeed/reports/poly2.json` | Projetos | poly2_export | quant_report, dashboard frontend | JSON (~80 categorized markets) | yes (.tmp + rename) |
| `AlphaFeed/reports/polytraders.json` | Projetos | polytraders_export | quant_report, dashboard | JSON | yes |
| `AlphaFeed/reports/hedgepoly.json` | Projetos | hedgepoly_export | dashboard | JSON | yes |
| `AlphaFeed/reports/quant_report.json` | Projetos | quant_report (reads above 2) | dashboard | JSON | yes |
| `signal_tracker.db` | Projetos | every project that logs signals | scheduler.resolve_*, daily metrics | SQLite WAL | dedup via UNIQUE constraint |
| `Poly/results.json` | Projetos | Poly Kelly scraper | dashboard (not consumed downstream; informational) | JSON | yes |
| `Multi Factor/multifactor/state/leverage_snapshot.json` | Multi Factor | engine/orchestrator | delivery/dashboard | JSON | yes (.json.tmp + replace) |
| `Multi Factor/multifactor/state/positions.json` | Multi Factor | execution/paper | engine/orchestrator next tick | JSON | yes |
| `Multi Factor/multifactor/state/har_rv_coeffs.json` | Multi Factor | factors/har_rv | factors/bs_edge next tick | JSON (cache, refit every 30d) | yes |
| `Multi Factor/multifactor/state/factor_history.jsonl` | Multi Factor | engine/orchestrator | scripts/residual_audit (manual) | JSONL append | append-only |
| `Multi Factor/multifactor/state/shadow_signals.jsonl` | Multi Factor | engine/orchestrator | (slice-2 calibration replay) | JSONL append | append-only |
| `Multi Factor/dashboard_data.json` | Multi Factor | refresh_dashboard.py | delivery/dashboard.render_combined | JSON | yes |
| `Multi Factor/dashboard.html` | Multi Factor | delivery/dashboard | user browser | HTML | yes |
| `Multi Factor/multifactor/outbox/telegram/*.md` | Multi Factor | delivery/telegram_bot | audit / replay | Markdown | append-only directory |

**Key insight:** the only SQL in the entire monorepo is `signal_tracker.db`. Everything else is JSON-on-disk (atomic writes via `.tmp` + rename). That's a **deliberate simplicity choice** — easy to inspect with `cat`, easy to backup, no DB server. Don't be tempted to "upgrade" it unless you have a real reason.

### 5.1 `signal_tracker.db` schema

Two tables:

```sql
-- signals: every logged signal across all projects
CREATE TABLE signals (
    id INTEGER PRIMARY KEY,
    system TEXT,              -- "poly"|"polytraders"|"hedgepoly"|"alphafeed"|"modeltelegra"
    signal_type TEXT,         -- "polymarket"|"trend_direction"|"straddle"
    market_slug TEXT,
    ticker TEXT,
    condition_id TEXT,
    direction TEXT,           -- "YES"|"NO"|"LONG"|"SHORT"|"STRADDLE"
    estimated_edge REAL,
    estimated_prob REAL,
    market_price REAL,
    entry_price REAL,
    kelly_bet REAL,
    kelly_fraction REAL,
    signal_tier TEXT,         -- "A"|"B"|"C"
    raw_features TEXT,        -- JSON
    cost_estimate REAL,
    net_edge REAL,
    created_at TEXT,
    resolved_at TEXT,
    outcome TEXT,             -- "WIN"|"LOSS"|NULL
    actual_pnl REAL,
    resolution_data TEXT      -- JSON
);

-- daily_metrics: aggregate per-day per-system
CREATE TABLE daily_metrics (
    id INTEGER PRIMARY KEY,
    date TEXT,
    system TEXT,
    signals_logged INTEGER,
    signals_resolved INTEGER,
    win_rate REAL,
    avg_edge REAL,
    avg_net_edge REAL,
    total_pnl REAL,
    sharpe_approx REAL,
    UNIQUE(date, system)
);
```

Indexes: `idx_signals_system`, `idx_signals_slug`, `idx_signals_unresolved`, `idx_signals_created`.

Dedup logic: `(system, COALESCE(market_slug, ticker, ''), direction, date(created_at))` is the natural unique key — if a project logs the same signal twice in a UTC day, the second call returns the existing id.

### 5.2 Multi Factor's slice-1 state additions

Slice 1 added 4 new state files. They are append-only logs (`*.jsonl`) or atomic-rewrite caches (`*.json`). All are gitignored. The `state/audits/` directory is reserved for the end-of-week-1 residual audit (`multifactor/scripts/residual_audit.py`).

---

## 6. Data flow — end-to-end picture

### 6.1 Cron tick lifecycle (one scheduler run)

```
12:00 or 22:00 UTC
  │
  ▼
scheduler.run_all()
  │
  ├─► MAIN PROJECTS (in order, sequential):
  │     1. HedgePoly Alpha            ──► Polymarket Gamma  ─┐
  │     2. Global Macro               ──► yfinance ─────────┐│
  │     3. Poly2 Kelly Bot            ──► Polymarket Gamma  ││──► Gemini AI
  │     4. Poly2 Macro Report 1       ──► Polymarket Gamma  │└──► Telegram
  │     5. Poly2 Macro Report 2       ──► Polymarket Gamma  │
  │     6. ModelTelegra Quant Desk    ──► Deribit + Coinbase + yfinance
  │     7. Poly Kelly Scraper         ──► Polymarket Gamma+CLOB ──► signal_tracker.db
  │     8. PolyTraders Smart Money    ──► Polymarket Leaderboard+Positions
  │     9. Multi Factor BTC Leverage  ──► Deribit + Coinbase + state/*.json
  │
  ├─► SIGNAL RESOLUTION (best-effort):
  │     signal_tracker.resolve_polymarket_signals()    ──► Polymarket Gamma
  │     signal_tracker.resolve_modeltelegra_signals()  ──► yfinance
  │     signal_tracker.update_daily_metrics()           ──► daily_metrics table
  │
  └─► ALPHAFEED ADAPTERS (in order — last reads first 2):
        1. poly2_export                ──► reports/poly2.json
        2. polytraders_export          ──► reports/polytraders.json
        3. hedgepoly_export            ──► reports/hedgepoly.json
        4. quant_report  reads 1+2     ──► reports/quant_report.json
                                       └─► logs to signal_tracker.db
```

**Why this order matters:**

- AlphaFeed runs *after* main projects so its data is consistent with what just shipped to Telegram.
- `quant_report.py` runs LAST in the AlphaFeed batch because it joins `polytraders.json` × `poly2.json` for enrichment.
- Failures in AlphaFeed don't block the main run — best-effort by design.

### 6.2 The AlphaFeed XGBoost stage (where the model lives)

`AlphaFeed/backend/adapters/quant_report.py` does the heavy lifting:

```
polytraders.json  (one entry per smart-money opportunity)
       │
       ▼
join on slug → poly2.json (Gamma metadata: volume, liquidity, days_left, category)
       │
       ▼
quant_features.compute_features(opp)
       │
       ▼  ← features: log_volume_24h, log_liquidity, info_ratio, days_left_sqrt,
       │            price_cents, market_efficiency, crowd_uncertainty, smart_money_edge…
       │
       ▼
XGBoost.predict_proba()      ← AlphaFeed/models/xgboost_model.json
       │
       ▼
Platt calibration             ← AlphaFeed/models/calibration_params.json
       │
       ▼
tier = "A" if quantScore≥0.65 else "B" if ≥0.40 else "C"
convergentScore = quantScore × countSignal     ← rewards: crowd uncertain AND traders agree
contraryFlag = (crowd certain BUT traders disagree)  ← rewards: contrarian smart-money
       │
       ▼
reports/quant_report.json
       │
       ▼
signal_tracker.log_signal(system="alphafeed", ..., raw_features={...})
```

**Model versioning gap:** `AlphaFeed/models/xgboost_model.json` is a single file with no version suffix. When you retrain, you overwrite. **R7** moves these under `models/v{N}/` with a pointer in code.

### 6.3 signal_tracker.db lifecycle

Every project that wants signal-quality tracking calls `signal_tracker.log_signal(...)`. The post-run resolver looks up each unresolved signal:

- **Polymarket signals**: Gamma API tells you if market closed and what YES price was. Resolver marks `WIN` if direction matches actual outcome (≥0.99 = YES resolved, ≤0.01 = NO resolved). Indeterminate stays unresolved.
- **ModelTelegra/Multi Factor signals (trend/straddle)**: yfinance gives BTC-USD price at target timestamp; resolver computes realized move vs strike or breakeven, marks WIN/LOSS + actual_pnl.

Daily metrics aggregator computes per-system win_rate, sharpe_approx, etc. **This is where the slice-2 calibration data lives for Multi Factor** — once we have ≥30 closed trades.

---

## 7. Duplicate fetches — the concrete refactor savings

The audit found 4 duplicated API call patterns. Concrete refactor:

### 7.1 Polymarket Leaderboard — 3 fetches → 1

| Caller | File | Behavior |
|---|---|---|
| PolyTraders | `PolyTraders/leaderboard.py` (37–99) | direct fetch w/ pagination |
| HedgePoly | `HedgePoly/.../smart_money.py` (90–125) | direct fetch w/ pagination |
| AlphaFeed | `AlphaFeed/backend/adapters/polytraders_export.py` | imports PolyTraders modules → re-runs leaderboard fetch |

**Refactor:** create `Projetos/lib/polymarket_client.py` with:

```python
class PolymarketClient:
    def __init__(self, *, ttl_seconds: int = 3600):
        self._lb_cache: dict[tuple[str,str,int], tuple[float, list[Trader]]] = {}

    def get_leaderboard(self, *, time_period: str = "WEEK",
                        category: str = "OVERALL", limit: int = 50) -> list[Trader]:
        key = (time_period, category, limit)
        now = time.time()
        if key in self._lb_cache:
            cached_at, data = self._lb_cache[key]
            if now - cached_at < self.ttl:
                return data
        data = self._fetch_leaderboard_uncached(time_period, category, limit)
        self._lb_cache[key] = (now, data)
        return data
```

Same for `get_positions(wallet)`, `get_markets(filters)`, `get_orderbook(token_id)`.

**Estimated savings:** ~100 HTTP requests/day across the 3 callers; ~60s wall time.

### 7.2 Polymarket Positions — 2 fetches → 1

Same pattern. PolyTraders + HedgePoly both walk the same ~100 trader wallets parallel-fetching positions. Shared client with positions cache (TTL=1h) collapses to one fetch.

**Estimated savings:** ~100 HTTP requests/day, ~60s wall time.

### 7.3 Polymarket Gamma Markets — 2 fetches → 1

Poly Kelly Scraper (3 pages × 100 = 300 markets) + AlphaFeed poly2_export (8 pages = 800 markets). Different page counts but overlapping market sets. Shared client with markets cache (TTL=10m) means whichever runs second sees a warm cache.

**Estimated savings:** ~800 HTTP requests/day, ~120s wall time.

### 7.4 yfinance BTC-USD — 2 fetches → 1

Global Macro fetches BTC-USD for the macro stack. signal_tracker resolves ModelTelegra signals via the same source. Different timestamps requested but same underlying API. Shared cache keyed by `(symbol, date)`.

**Estimated savings:** marginal (2 requests/day) but a precedent for R3.

**Aggregate savings if R1 + R3 land: ~3–5 minutes per cron run, fewer rate-limit collisions on Polymarket public endpoints.**

---

## 8. Configuration — where settings actually live

### 8.1 Environment variables (`.env` at Projetos root)

| Key | Used by | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | scheduler._tg_send, every project's own POST | propagated via `os.environ.copy()` to subprocesses |
| `TELEGRAM_CHAT_ID` | same | usually a group ID (negative integer) |
| `GEMINI_API_KEY` | Poly2 (×3), ModelTelegra | FREE-tier 250 req/day; saturation risk if reports balloon |
| `GEMINI_MODEL` | same | default `gemini-2.5-flash` |
| `POLYTRADERS_BANKROLL` | PolyTraders, AlphaFeed polytraders_export | default 100 USDC |
| `POLYTRADERS_TOP_N` | same | default 25 |
| `POLYTRADERS_TIME_PERIOD` | same | `WEEK` default; `DAY`/`MONTH`/`ALL` valid |

**Loading**: `scheduler._load_dotenv()` reads root `.env`, then subprocess inherits via `env=os.environ.copy()`. No project should re-parse `.env` (some still do — fine but redundant).

### 8.2 YAML configs (Multi Factor only)

```
Multi Factor/multifactor/config/
├── weights.yaml      — factor weights, bs_edge_kappa, gate thresholds
├── risk.yaml         — account equity, sizing, stops, vetoes, execution
└── universe.yaml     — Deribit settings, filter bands, cadence, HAR-RV params
```

All loaded via `multifactor.config.load_all() → Config` (frozen pydantic v2, fail-closed on validation).

### 8.3 Hardcoded paths that need fixing (R2)

| File | Line | Path | Fix |
|---|---|---|---|
| `Multi Factor/multifactor/refresh.py` | 27 | `ROOT = Path("D:/Multi Factor")` | read from `MULTIFACTOR_ROOT` env var, fallback to `Path(__file__).resolve().parent.parent` |
| `Projetos/scheduler.py` | 53–60 | `PROJECTS_DIR = Path(__file__).resolve().parent` etc. | already correct (computed from `__file__`) |
| `Projetos/AlphaFeed/backend/adapters/*.py` | varies | `_HERE.parent.parent.parent` paths | works because adapters live 3 levels deep; brittle to dir moves |

R2 is just the Multi Factor one.

---

## 9. Per-project deep dives (read as needed)

### 9.1 Multi Factor — slice-1 module map

```
multifactor/refresh.py             ← CLI entry
   │
   ▼ calls
orchestrator.run(cfg, state_dir, now)
   │
   ├─ data/deribit_public.py:DeribitClient(cfg).fetch_chain()
   │    └─ /public/get_book_summary_by_currency?currency=BTC&kind=option
   ├─ data/deribit_public.py:fetch_index_price()
   │    └─ /public/get_index_price?index_name=btc_usd
   ├─ data/price_feed.py:PriceFeed(cfg).fetch_history(days=730)
   │    └─ /products/BTC-USD/candles?granularity=86400 (paginated)
   │
   ├─ factors/har_rv.py:yang_zhang_rv(ohlc, window=30) → RV series
   ├─ factors/har_rv.py:load_coeffs() OR _fit_har_log_rv(rv) → HARCoeffs
   │    └─ writes har_rv_coeffs.json if refit
   ├─ factors/har_rv.py:forecast_one_step(rv, coeffs)
   ├─ factors/har_rv.py:annualised_sigma(rv_one_step) → σ̂_T
   │
   ├─ factors/bs_edge.py:compute_bs_edge(chain, σ̂, spot, cfg) → FactorOutput
   │    └─ universe filter → BS reprice each contract → vega-weighted score
   │
   ├─ engine/scorer.py:composite([bs_edge_output], cfg)
   ├─ engine/risk.py:gate_and_size(score, ranked, positions, account, cfg)
   │    └─ 6 vetoes + Kelly sizing + tier multipliers
   │
   ├─ execution/paper.py:apply(ticket, book, chain_marks, cfg, now)
   │    └─ MtM, exits, idempotent order_id; writes positions.json + trades.jsonl
   │
   ├─ delivery/dashboard.py:render_combined() → dashboard.html (3 tabs)
   └─ delivery/telegram_bot.py:emit(snap, outbox_dir) → stdout + outbox file
```

### 9.2 AlphaFeed adapters — exact reads & writes

| Adapter | Reads | Writes | Timeout | Logs signals? |
|---|---|---|---|---|
| `poly2_export.py` | Polymarket Gamma `/markets` (8 pages) | `reports/poly2.json` | 180s | no |
| `polytraders_export.py` | `sys.path.insert PolyTraders/` then imports its modules; calls leaderboard + positions APIs | `reports/polytraders.json` | 240s | indirectly via PolyTraders.kelly.score_opportunities → signal_tracker |
| `hedgepoly_export.py` | `sys.path.insert HedgePoly/`, then `smart_money.build_smart_money_signals()` | `reports/hedgepoly.json` | 240s | no |
| `quant_report.py` | `reports/polytraders.json` + `reports/poly2.json` + `models/xgboost_model.json` + `models/calibration_params.json` + Gamma API (for missing slugs) | `reports/quant_report.json` | 300s | yes, system="alphafeed" |

### 9.3 ModelTelegra Quant Desk — what it actually does

Multi-model BTC volatility / trend / risk stack. Outputs:
- Plotly chart rendered to PNG via kaleido
- Telegram message + chart (via python-telegram-bot, not stdlib)
- Signals logged to signal_tracker.db (types: `straddle`, `trend_direction`)

**Notable**: this is the only project that sends `sendPhoto` (image) to Telegram. The plain-text style guide does NOT apply. Treat as a separate design conversation.

### 9.4 Poly2 — the over-long Macro reports

`macro_report1.py` + `macro_report2.py` use Gemini AI to write structured reports. AI-generated text tends toward Telegram's 4096-char hard cap. Both:
- Hit Polymarket Gamma /markets to gather context
- Call Gemini once per market batch (50–80 markets)
- Render an HTML report with `parse_mode=HTML`
- Chunk to multiple Telegram messages if >4000 chars

**R4 (when prioritized):** add TL;DR header (3 bullets) + clamp each AI section length, then optionally adopt `lib/telegram_format` for the header + footer skeleton.

---

## 10. Deployment checklist

### 10.1 What "deploy" means today

The scheduler.py daemon runs on **a single machine** (the user's Windows laptop) at 12:00 + 22:00 UTC. There is no cloud component except the external APIs. Deployment ≈ "set it up on a new box."

### 10.2 Minimum requirements

- [ ] Python ≥ 3.11 (Multi Factor needs 3.12; Poly2 still on 3.9 — see R5)
- [ ] `uv` installed (`winget install --id=astral-sh.uv` on Windows; `curl -LsSf https://astral.sh/uv/install.sh | sh` on POSIX)
- [ ] `git clone` both repos to known absolute paths (or fix R2 first)
- [ ] `.env` at `Projetos/` root with all keys from §8.1
- [ ] `signal_tracker.db` will be created on first run (no migration needed)

### 10.3 Smoke test (in order)

```bash
# 1. dry-run scheduler — should print every project's command, no execution
cd "Projetos"
python scheduler.py --test

# 2. run Multi Factor standalone
cd "Multi Factor"
uv run python -m multifactor.refresh
# expect: ⚪ or 🟢 banner + Telegram outbox file written

# 3. one-shot live cycle — runs ALL projects, sends Telegram messages
cd "Projetos"
python scheduler.py --once
# expect: all 9 projects + 4 AlphaFeed adapters complete in ~10–15 min

# 4. inspect signal_tracker
python -c "import sqlite3; print(sqlite3.connect('signal_tracker.db').execute('SELECT system, COUNT(*) FROM signals GROUP BY system').fetchall())"

# 5. inspect AlphaFeed reports
ls -la AlphaFeed/reports/
# expect: 4 JSON files, all mtime within last 5 min
```

### 10.4 To run as a real daemon

```bash
cd "Projetos"
nohup python scheduler.py > /dev/null 2>&1 &
# or on Windows: pythonw scheduler.py
```

Daemon checks every 30s, fires at 12:00 + 22:00 UTC (with 4h catch-up if laptop was asleep).

### 10.5 What happens when things break

| Failure | Behavior | User-visible signal |
|---|---|---|
| One project fails (non-zero exit) | scheduler logs error, continues with next project, **sends Telegram failure summary** at end of cycle | "Scheduler run at TS — FAILURES (N/9): …" |
| All projects fail | same as above, summary lists everyone | same |
| Telegram API down | scheduler catches, logs to scheduler.log, run completes (no notification reaches you) | no Telegram message that cycle |
| signal_tracker.db locked / corrupt | best-effort try/except in scheduler:361–372; doesn't block run | scheduler.log: "signal_tracker resolution failed" |
| AlphaFeed adapter fails | run_project returns False; next adapter still runs | reports/X.json stays stale; dashboard shows previous run's data |
| Network outage mid-cycle | each project times out (240s typical), returns False | failure summary lists affected projects |

**There is no automatic retry.** The next scheduled tick (12 hours later) runs everything fresh.

### 10.6 Operational nice-to-haves (post-R2)

- `scheduler.log` rotation (currently grows forever)
- AlphaFeed `reports/*.json` to S3 or similar for off-machine backup
- signal_tracker.db nightly backup (it's the only stateful piece)

---

## 11. Open questions / decisions deferred

1. **ModelTelegra rename** — the `,` in `ModelTelegra,/` directory name causes quoting issues in scripts. Worth a rename + update scheduler.py + commit, but breaks any external references. Defer.
2. **lib/ ownership** — currently lives at Projetos root. Multi Factor lives in a separate repo and imports via... wait, does Multi Factor import from Projetos/lib? **It doesn't today.** If we want to share `lib/telegram_format` between Multi Factor and the Projetos projects, we either (a) symlink, (b) duplicate, (c) make Multi Factor a subdir of Projetos, or (d) publish lib/ as a wheel. Decide before R4 expands.
3. **scheduler.py daemonization** — currently relies on the user keeping the laptop awake or running `nohup`. Productionizing this means a service unit (Windows: scheduled task / Linux: systemd). Out of scope for this audit but on the deferred list.
4. **CLARITY Act / Polymarket regulatory risk** — if Polymarket is geo-blocked or shut down in the US, 5 of 9 projects break overnight. There is no backup data source. Deferred (not actionable).

---

## 12. Where to find things

- **This document:** `Projetos/docs/ARCHITECTURE.md` (you are here)
- **Telegram style guide:** `Projetos/docs/TELEGRAM_STYLE_GUIDE.md`
- **Per-slice specs/plans:** `Projetos/docs/superpowers/{specs,plans}/`
- **Multi Factor slice-1 spec:** `Multi Factor/docs/superpowers/specs/2026-04-28-btc-leverage-slice1-design.md`
- **Schedule of runs:** `Projetos/scheduler.log` (mtime tells you last cycle)
- **All signal history:** `Projetos/signal_tracker.db` (open with any SQLite browser)

If you change anything load-bearing, brainstorm + spec + plan it through the superpowers flow that landed slice-1 and the Telegram redesign — it works, and the audit trail is worth more than the velocity.
