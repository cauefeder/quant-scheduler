# Quant Poly Alpha

**Automated prediction market alpha — Polymarket signals, BTC volatility, multi-asset trends, and smart-money copy-trading delivered twice daily to Telegram.**

> Live signals channel: [HedgePoly Alpha](https://t.me/+YOUR_INVITE_LINK_HERE) *(add your Telegram group invite link here when you make the repo public)*

---

## What This Is

A suite of 7 quantitative modules that run automatically at **6 AM and 4 PM Saskatchewan time (UTC-6)**, sending actionable reports to a private Telegram group. Each module targets a different edge:

| Module | Edge Source | Output |
|--------|-------------|--------|
| **HedgePoly** | 400M-trade historical calibration on Polymarket — finds markets where price ≠ empirical win rate | Top 8 mispriced YES/NO opportunities with Kelly bet sizes |
| **Poly2 Kelly** | Gemini AI probability estimation vs market-implied probability | Kelly-sized opportunities with AI reasoning |
| **Poly2 Macro 1 & 2** | Macroeconomic context (Fed, geopolitics, crypto) applied to active markets | Macro-informed market views |
| **ModelTelegra** | BTC realized vol vs implied vol, multi-asset trend classification (EMA/ATR/ROC), risk scoring | Daily BTC straddle recommendation + 9-asset trend scan |
| **Poly Scraper** | Kelly criterion on short-term Polymarket positions (≤30 days) | Top bets by composite edge score |
| **PolyTraders** | Smart-money copy-trading: top 25 Polymarket traders by 7-day PnL, consensus open positions | Kelly-sized copy-trade signals |

---

## Architecture

```
quant-poly-alpha/
├── scheduler.py              # Daemon: runs all 7 modules twice daily
├── setup_windows_tasks.bat   # Windows: creates scheduled tasks (run as Admin)
├── .env.example              # Credential template
│
├── HedgePoly/
│   └── prediction-market-analysis/   # Calibration alpha engine
│       ├── calibration.py            # Historical win-rate surface
│       ├── kelly.py                  # Monte Carlo Kelly sizing
│       ├── orderflow.py              # Maker/taker flow analysis
│       ├── reporting.py              # HTML Telegram report builder
│       ├── send_report.py            # One-shot sender (used by scheduler)
│       ├── pipeline.py               # Full analysis pipeline
│       └── telegram_bot.py           # PTB bot with /report command
│
├── Poly2/
│   ├── polymarket_telegram_bot.py    # Gemini AI + Kelly opportunities
│   ├── macro_report1.py              # Macro intelligence report
│   └── macro_report2.py              # Deep macro report
│
├── Poly/
│   └── polymarket_scraper.py         # Short-term Kelly position scanner
│
├── PolyTraders/
│   ├── leaderboard.py                # Top traders by PnL
│   ├── positions.py                  # Open positions per trader
│   ├── kelly.py                      # Consensus signal + Kelly sizing
│   └── main.py                       # Orchestrator + Telegram sender
│
└── ModelTelegra,/
    └── quant_desk/
        ├── models/
        │   ├── model1_volatility.py  # BTC vol regime + straddle analysis
        │   ├── model2_trend.py       # Multi-asset trend classification
        │   └── model3_risk.py        # Signal integration + risk scoring
        ├── analytics/
        │   └── regime.py             # Vol regime detection (fixed 2026-02)
        ├── data/fetcher.py           # Binance + Yahoo Finance fetcher
        └── reporting/telegram_bot.py # Telegram report formatter
```

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/cauefeder/quant-poly-alpha.git
cd quant-poly-alpha
cp .env.example .env
# Edit .env with your Telegram bot token and chat ID
```

### 2. Configure HedgePoly

```bash
cd HedgePoly/prediction-market-analysis
cp config_example.toml config.toml
# Edit config.toml: add your Telegram token and data_dir path
# Download the dataset: see HedgePoly/prediction-market-analysis/README.md
```

### 3. Run a single module manually

```bash
# HedgePoly — send one report now
cd HedgePoly/prediction-market-analysis
uv run python send_report.py

# PolyTraders — preview smart-money signals
cd PolyTraders
uv run --no-project --python 3.11 --with "requests,python-dotenv" python main.py --preview

# Full scheduler test (dry run, no API calls)
python scheduler.py --test

# Run everything once now
python scheduler.py --once
```

### 4. Start the daemon (recommended)

```bash
python scheduler.py
# Checks every 30 seconds, fires at 12:00 UTC and 22:00 UTC
# Catches up missed runs within 4 hours after sleep/restart
```

### 5. Windows scheduled tasks (optional — auto-starts on boot)

```
Right-click setup_windows_tasks.bat → Run as Administrator
```

This creates three Windows Scheduled Tasks:
- `QuantScheduler_Morning` — 6 AM SK daily
- `QuantScheduler_Afternoon` — 4 PM SK daily
- `QuantScheduler_Daemon` — starts on login (persistent daemon)

All tasks use `StartWhenAvailable=true`, so they catch up after the laptop wakes from sleep.

---

## Dependencies

All modules use [`uv`](https://docs.astral.sh/uv/) for zero-install dependency management — no `pip install` or virtual environment setup required.

| Module | Python | Key Dependencies |
|--------|--------|-----------------|
| HedgePoly | 3.11+ (via uv) | httpx, python-telegram-bot, duckdb, pandas, toml |
| Poly2 | 3.9+ (via uv) | requests, google-generativeai |
| Poly | 3.11+ (via uv) | requests |
| PolyTraders | 3.11+ (via uv) | requests, python-dotenv |
| ModelTelegra | 3.11+ (via uv) | yfinance, python-telegram-bot, plotly, numpy, scipy |
| Scheduler | 3.8+ (system) | stdlib only |

Install uv (one-time):
```bash
# Windows (PowerShell)
irm https://astral.sh/uv/install.ps1 | iex
```

---

## Configuration

### Root `.env` (controls the scheduler and PolyTraders)

```env
TELEGRAM_BOT_TOKEN=...    # from @BotFather
TELEGRAM_CHAT_ID=...      # your group chat ID (negative number)
GEMINI_API_KEY=...        # from https://aistudio.google.com (free)
GEMINI_MODEL=gemini-2.5-flash
POLYTRADERS_BANKROLL=100  # your bankroll in USDC
POLYTRADERS_TOP_N=25      # number of top traders to track
POLYTRADERS_TIME_PERIOD=WEEK
```

### HedgePoly `config.toml`

```toml
[telegram]
bot_token = "..."
allowed_chat_ids = "..."

[paths]
data_dir = "/path/to/data"  # download from HedgePoly README
```

### Poly2 `.env`

Same Telegram token + Gemini API key (see `Poly2/.env.example`).

### ModelTelegra `.env`

Same Telegram token + Gemini API key (see `ModelTelegra,/quant_desk/.env.example`).

---

## Module Details

### HedgePoly — Calibration Alpha

Loads a 400M-trade historical dataset mapping market price buckets to empirical win rates. Compares current live market prices against this surface to find statistically significant mispricing.

**Edge formula:**
```
edge = empirical_win_rate(price_bucket) - current_market_price
composite_score = edge × liquidity^0.20 × volume_24h^0.10 × sample_size^0.10
kelly_bet = kelly_fraction × composite_score / (1 - price)
```

### PolyTraders — Smart Money Copy Trading

Fetches top 25 traders by 7-day PnL from the Polymarket leaderboard API, then pulls their current open positions. Markets where ≥2 top traders share a position generate a consensus signal.

**Signal formula:**
```
signal_strength = n_smart_traders / total_checked
estimated_edge  = signal_strength × 0.15  (capped at 20%)
kelly_bet       = quarter-Kelly × estimated_edge / (1 - market_price) × bankroll
```

### ModelTelegra — BTC Volatility Regime

Classifies the current volatility environment using realized vol at 1D/7D/30D horizons, vol-of-vol, and a 24H price change override that catches large moves immediately (the slow MA-based signal was fixed Feb 2026).

Regimes: `HIGH_VOL_BREAKOUT` · `EXPANSION` · `COMPRESSION` · `MEAN_REVERSION` · `TRAP` · `NORMAL`

---

## Scheduler

`scheduler.py` runs all 7 modules in sequence using `uv run` for isolated, dependency-managed execution.

**Sleep/wake handling:** The daemon checks every 30 seconds. If the laptop was asleep at a scheduled time, it catches up any run missed within the last 4 hours. The Windows tasks also have `StartWhenAvailable=true` as a secondary guarantee.

**Timezone:** Saskatchewan (SK), Canada is always UTC-6 with no daylight saving time.

---

## Disclaimer

This is a personal research and automation project. Nothing here is financial advice. Prediction markets carry significant risk. All Kelly sizing is fractional (quarter-Kelly) to limit exposure. Always verify signals before acting on them.

---

*Built by [@cauefeder](https://github.com/cauefeder)*
