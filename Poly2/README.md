# Polymarket → Claude AI → Telegram Bot

Automated prediction market scanner that uses Claude AI to estimate probabilities, calculates Kelly Criterion bet sizes, and sends alerts to your Telegram group.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Polymarket  │────▶│  Pre-Filter  │────▶│  Claude AI   │────▶│    Kelly     │
│  Free APIs   │     │  300→10 mkts │     │  Haiku 4.5   │     │  Criterion   │
│  (no auth)   │     │  saves 90%   │     │  ~$0.01/scan │     │  + Risk Mgmt │
└──────────────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
                                                                       │
                                                                       ▼
                                                               ┌──────────────┐
                                                               │   Telegram   │
                                                               │   Group Bot  │
                                                               │   (free)     │
                                                               └──────────────┘
```

## Cost Breakdown

| Component | Cost | Notes |
|-----------|------|-------|
| Polymarket APIs | **Free** | No auth needed for reading |
| Claude Haiku 4.5 | **~$0.01/scan** | $1 in / $5 out per MTok |
| Claude Sonnet 4.5 | ~$0.02/scan | Better reasoning, 3x more |
| Telegram Bot API | **Free** | Unlimited messages |
| **Total (Haiku, 30min loop)** | **~$14/month** | 48 scans/day |
| **Total (Sonnet, 30min loop)** | ~$43/month | Better edge estimates |

## Quick Start

```bash
# 1. Install
pip install requests

# 2. Configure
cp .env.example .env
# Edit .env with your keys (see Setup Guide below)

# 3. Run once
python polymarket_telegram_bot.py --bankroll 1000

# 4. Run on loop (every 30 min)
python polymarket_telegram_bot.py --bankroll 1000 --loop --interval 30

# 5. Use better model for more precise estimates
python polymarket_telegram_bot.py --model claude-sonnet-4-5-20250929
```

## Setup Guide

### Step 1: Telegram Bot (2 minutes)

1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g., "Polymarket Kelly Bot")
4. Choose a username (e.g., "polymarket_kelly_bot")
5. **Save the token** BotFather gives you → put in `.env` as `TELEGRAM_BOT_TOKEN`
6. Create a **new Telegram group**
7. **Add your bot** to the group
8. Send any message in the group (e.g., "hello")
9. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
10. Find `"chat":{"id":-XXXXXXXXX}` → that number is your `TELEGRAM_CHAT_ID`

### Step 2: Anthropic API Key (2 minutes)

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Sign up (free) → add payment method (pay-as-you-go)
3. Go to **API Keys** → **Create Key**
4. Copy → put in `.env` as `ANTHROPIC_API_KEY`

### Step 3: Run

```bash
python polymarket_telegram_bot.py --bankroll 5000 --kelly-fraction 0.25
```

## CLI Options

```
--bankroll FLOAT       Bankroll in USD (default: 1000)
--kelly-fraction FLOAT Kelly multiplier 0-1 (default: 0.25 = quarter Kelly)
--max-days INT         Max days to resolution (default: 14)
--min-edge FLOAT       Minimum edge to bet (default: 0.05 = 5%)
--max-position FLOAT   Max single bet % (default: 0.10 = 10%)
--max-exposure FLOAT   Max total exposure % (default: 0.40 = 40%)
--max-bets INT         Max concurrent bets (default: 8)
--pages INT            Polymarket API pages (default: 2)
--model STR            Claude model choice
--loop                 Run continuously
--interval INT         Minutes between scans (default: 30)
```

## Token Optimization Details

The system is designed to minimize Claude API token usage:

1. **Pre-filtering (saves ~95% tokens)**: 300 raw markets → 10 candidates before Claude sees anything
2. **Batching**: 10 markets in 1 API call, not 10 separate calls
3. **Minimal system prompt**: ~150 tokens (cached after 1st call → 90% savings)
4. **Compact user prompt**: ~50 tokens per market (vs ~200 with full descriptions)
5. **JSON-only output**: No prose, no markdown → ~120 tokens per market output
6. **max_tokens cap**: 1500 tokens prevents runaway output
7. **Model choice**: Haiku at $1/$5 vs Opus at $5/$25

### Prompt sent to Claude (example ~650 tokens total):

```
System (150 tokens, cached):
  "You are a prediction market analyst..."

User (~500 tokens for 10 markets):
  Date: 2026-02-18. Analyze these prediction markets:
  - id:"cond_001abc" q:"Will Bitcoin exceed $100K by March?" p:0.62 spread:0.040 vol24h:$15000 resolves:10d cat:crypto
  - id:"cond_002def" q:"Will Fed cut rates in March?" p:0.45 spread:0.040 vol24h:$42000 resolves:5d cat:economics
  ...
```

### Claude response (~1000 tokens):

```json
[
  {"id":"cond_001abc","prob":0.68,"conf":"medium","reason":"BTC near ATH, momentum strong","factors":["price momentum","halving cycle"]},
  {"id":"cond_002def","prob":0.52,"conf":"medium","reason":"Mixed signals from recent data","factors":["inflation data","labor market"]}
]
```

## Example Telegram Output

```
🎯 POLYMARKET KELLY SCANNER
📅 2026-02-18 14:30 UTC
💰 Bankroll: $5,000 | Model: claude-haiku-4-5-20251001
💵 Scan cost: ~$0.0068

📊 3 opportunities found
📈 Total exposure: $850 (17.0%)
🎲 Expected profit: $127.50

───────────────────────────────────────
#1 🟡 MEDIUM
📌 Will the Fed cut rates at the March 2026 meeting?

  Market: 45% → Claude: 56%
  Edge: 11.0% | EV: 24.4% | Odds: 2.22x
  Kelly: 12.8% → Adj: 3.2%
  💰 BET: $160 (3.2% of bankroll)
  ⏰ Resolves: 5d | Vol24h: $42,000
  💡 Mixed data but labor market softening
  📎 inflation cooling · employment weak
  🔗 View on Polymarket
```

## Upgrading the Model

If you want more precise probability estimates, upgrade the Claude model:

| Model | Cost/scan | Quality | When to use |
|-------|-----------|---------|-------------|
| Haiku 4.5 | ~$0.01 | Good | Daily scanning, tight budget |
| Sonnet 4.5 | ~$0.03 | Better | When edges matter (higher bankroll) |
| Opus 4.5 | ~$0.05 | Best | High-stakes analysis |

```bash
# Switch model
python polymarket_telegram_bot.py --model claude-sonnet-4-5-20250929
```

## Running on Windows as a Service

### Option A: Windows Task Scheduler
```bash
# Create a .bat file:
@echo off
cd C:\path\to\polymarket_telegram
python polymarket_telegram_bot.py --bankroll 5000 --loop --interval 30
```
Add to Task Scheduler → trigger "At startup"

### Option B: Run as background process
```bash
pythonw polymarket_telegram_bot.py --loop --interval 30
```

### Option C: Use nssm (Non-Sucking Service Manager)
```bash
nssm install PolymarketBot "C:\Python312\python.exe" "C:\path\to\polymarket_telegram_bot.py --loop --interval 30"
nssm start PolymarketBot
```
