# Quant Desk — Local Quantitative Trading Analysis System

## Architecture Overview

```
quant_desk/
├── config/
│   ├── settings.py          # Central configuration
│   └── .env.example         # Environment variables template
├── data/
│   ├── fetcher.py           # Price data from Yahoo Finance / Binance
│   └── options_proxy.py     # Synthetic options chain (Deribit-ready)
├── models/
│   ├── model1_volatility.py # BTC Volatility & GEX Model
│   ├── model2_trend.py      # Multi-Asset Trend Classification
│   └── model3_risk.py       # Probability & Risk Engine
├── analytics/
│   └── regime.py            # Volatility regime detection
├── reporting/
│   ├── telegram_bot.py      # Telegram message formatting & sending
│   └── charts.py            # Chart generation (HTML + PNG for Telegram)
├── scheduler/
│   └── runner.py            # Main orchestrator
├── tests/
│   └── test_models.py       # Unit tests
├── logs/                    # Auto-created log files
├── output/                  # Generated reports & charts
├── main.py                  # Entry point
├── requirements.txt         # Dependencies
└── README.md                # This file
```

## Quick Start

### 1. Install Python 3.10+ and create environment
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/WSL2:
source .venv/bin/activate
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp config/.env.example .env
# Edit .env with your Telegram bot token and chat ID
```

### 4. Run manually
```bash
python main.py
```

### 5. Run specific model or ticker
```bash
python main.py --model trend --ticker AAPL
python main.py --model volatility
python main.py --model all
```

## Scheduling

### Windows Task Scheduler
```
Action: Start a program
Program: C:\path\to\.venv\Scripts\python.exe
Arguments: C:\path\to\quant_desk\main.py
Start in: C:\path\to\quant_desk
Trigger: Daily at 07:00 AM
```

### Linux / WSL2 Cron
```bash
crontab -e
# Add:
0 7 * * * cd /path/to/quant_desk && /path/to/.venv/bin/python main.py >> logs/cron.log 2>&1
```

## Telegram Setup

1. Message @BotFather on Telegram, create bot, get token
2. Create a channel/group, add bot as admin
3. Get chat ID (send message to bot, visit `https://api.telegram.org/bot<TOKEN>/getUpdates`)
4. Set in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

## Models Summary

### Model 1 — BTC Volatility & Options Structure
- Estimates 1D expected move from realized volatility
- Builds synthetic GEX profile using Black-Scholes gamma
- Identifies Call Wall, Put Wall, max pain
- Classifies volatility regime (expansion/compression/mean-reversion/breakout)
- Suggests optimal straddle strike and breakeven range
- Ready to plug real Deribit/LNMarkets options data

### Model 2 — Multi-Asset Trend Classification
- Fetches 1H data from Yahoo Finance for BTC, ETH, BNB, SOL, SPY, QQQ, GLD, SLV, USO
- Uses HMM-inspired regime detection with volatility-adjusted signals
- Classifies each bar as Bullish/Bearish/Hold
- Computes trend strength score (0-100) and persistence probability
- Generates colored price charts (green/red/blue segments)
- Supports custom ticker input

### Model 3 — Signal Integration & Risk Engine
- Combines Model 1 + Model 2 outputs
- Computes probability-weighted trade score
- Estimates win probability, expected value, risk of ruin
- Outputs: Strong Buy / Speculative Vol Play / Trend Continuation / No Trade / Capital Preservation
- Only alerts when asymmetry + strength + risk thresholds are all met

## Future Improvements
- Plug real Deribit options chain API
- Add LNMarkets trade execution via API
- Add OMQS signal integration
- Backtest framework with walk-forward optimization
- Multi-strategy portfolio allocation (Kelly criterion)
- Docker containerization for dedicated Linux box
