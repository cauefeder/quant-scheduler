# Polymarket Kelly Criterion Position Sizer

## Quick Start (Windows)

```bash
# 1. Install Python 3.10+ from python.org (check "Add to PATH")
# 2. Install dependency
pip install requests

# 3. Run with defaults ($1000 bankroll, quarter Kelly)
python polymarket_scraper.py

# 4. Run with custom settings
python polymarket_scraper.py --bankroll 5000 --kelly-fraction 0.25 --max-days 14 --min-edge 0.05

# 5. Results saved to results.json
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    MAIN PIPELINE                     │
├─────────────────────────────────────────────────────┤
│  1. PolymarketAPI    → Fetch markets + orderbooks   │
│  2. MarketScanner    → Filter short-term + liquid   │
│  3. EdgeEstimator    → Heuristic edge signals       │
│  4. KellyCriterion   → Calculate optimal bet sizes  │
│  5. OpportunityRanker→ Score & rank opportunities   │
│  6. RiskManager      → Portfolio-level limits       │
│  7. Report + JSON    → Display & save results       │
└─────────────────────────────────────────────────────┘
```

## APIs Used (All Free, No Auth)

| API | Base URL | Purpose |
|-----|----------|---------|
| Gamma API | `gamma-api.polymarket.com` | Market metadata, questions, volumes, end dates |
| CLOB API | `clob.polymarket.com` | Order books, bid/ask prices, spreads |

No API keys needed. These are Polymarket's public endpoints.

## Kelly Criterion Explained

The Kelly Criterion tells you the mathematically optimal fraction of your bankroll to bet:

```
f* = (b × p - q) / b

Where:
  b = net decimal odds (decimal_odds - 1)
  p = your true probability estimate
  q = 1 - p
  f* = fraction of bankroll to wager
```

**Why Fractional Kelly?** Full Kelly is mathematically optimal but has huge variance.
Quarter Kelly (0.25) achieves ~75% of the growth rate with massively reduced drawdowns.

| Kelly Fraction | Growth Rate | Max Drawdown Risk |
|---------------|-------------|-------------------|
| Full (1.0)    | 100%        | Very High         |
| Half (0.5)    | 75%         | High              |
| Quarter (0.25)| 50%         | Moderate          |
| Eighth (0.125)| 30%         | Low               |

## Risk Management Rules

1. **Max single bet**: 10% of bankroll (configurable)
2. **Max total exposure**: 40% of bankroll
3. **Max correlated exposure**: 20% per category
4. **Maximum bet count**: 10 concurrent bets
5. **Minimum edge**: 3% (skip low-edge markets)
6. **Skip extremes**: Ignore prices < 2% or > 98%

## Command Line Options

```
--bankroll FLOAT       Total bankroll in USD (default: 1000)
--kelly-fraction FLOAT Kelly multiplier 0-1 (default: 0.25)
--max-days INT         Max days to resolution (default: 30)
--min-edge FLOAT       Minimum edge threshold (default: 0.03)
--max-position FLOAT   Max single bet % (default: 0.10)
--max-exposure FLOAT   Max total exposure % (default: 0.40)
--max-bets INT         Max concurrent bets (default: 10)
--pages INT            API pages to scan (default: 3)
--output STR           Output JSON file (default: results.json)
```

---

## Hints & Solutions for Building This Properly

### 1. Edge Estimation (The Hard Part)

The built-in `EdgeEstimator` uses simple heuristics as a **demo**. For real profitability:

**Option A: Manual Research Override**
```python
# Create a file my_estimates.json:
{
    "0xabc123...": 0.72,  # condition_id: your_true_probability
    "0xdef456...": 0.35
}

# Load and pass to the ranker:
import json
with open("my_estimates.json") as f:
    estimates = json.load(f)
opportunities = ranker.find_opportunities(markets, user_estimates=estimates)
```

**Option B: News Sentiment via Free APIs**
```python
# Use NewsAPI.org (free tier: 100 req/day)
# or GNews API (free tier: 100 req/day)
import requests
resp = requests.get("https://gnews.io/api/v4/search", params={
    "q": market.question,
    "token": "YOUR_FREE_KEY",
    "lang": "en",
    "max": 5
})
articles = resp.json().get("articles", [])
# Analyze sentiment to adjust probability estimates
```

**Option C: Use a Local LLM**
```python
# Use Ollama (free, local) to estimate probabilities
# Install: https://ollama.ai
import requests
resp = requests.post("http://localhost:11434/api/generate", json={
    "model": "llama3",
    "prompt": f"What is the probability (0-1) that: {market.question}? "
              f"Current market price: {market_price}. "
              f"Respond with ONLY a number between 0 and 1.",
})
```

### 2. Short-Term Focus Strategy

Best signals for short-term markets (< 14 days):
- **Binary event markets** (election results, earnings, sports) → clearest resolution
- **High-volume markets** → better liquidity, tighter spreads
- **Recent price movement** → compare current price to 24h ago via the Gamma API's `price_change` field
- **Upcoming catalyst** → event date is known and imminent

### 3. Improving the Scraper

**Add Historical Price Tracking:**
```python
# The CLOB API has a timeseries endpoint:
# GET https://clob.polymarket.com/prices-history?market=TOKEN_ID&interval=1d
# Use this to detect momentum and mean reversion
```

**Add Telegram/Discord Alerts:**
```python
# Telegram Bot API is free:
import requests
def send_alert(message):
    bot_token = "YOUR_BOT_TOKEN"
    chat_id = "YOUR_CHAT_ID"
    requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                  json={"chat_id": chat_id, "text": message})
```

**Schedule Automated Runs (Windows):**
```bash
# Use Windows Task Scheduler or:
pip install schedule
```
```python
import schedule, time
schedule.every(30).minutes.do(main)
while True:
    schedule.run_pending()
    time.sleep(1)
```

### 4. Common Pitfalls

| Pitfall | Solution |
|---------|----------|
| Overconfidence in edge estimates | Use fractional Kelly (0.25 or less) |
| Ignoring liquidity | Only bet markets with > $500 liquidity |
| Correlated bets | Limit exposure per category to 20% |
| Bid-ask spread eating profits | Only bet when spread < 5 cents |
| API rate limits | Add `time.sleep(0.3)` between calls |
| Ruin risk | Never exceed 40% total exposure |
| Chasing losses | Stick to systematic criteria |

### 5. Legal & Compliance Notes

- Polymarket operates on Polygon (crypto). You need a wallet + USDC to trade.
- Check your jurisdiction's laws regarding prediction markets.
- This tool is for **research and education** only.
- Polymarket's API terms may change — check their docs.

### 6. Extending to Other Markets

The architecture is modular. To add other platforms:
```python
class ManifoldAPI(PolymarketAPI):
    """Manifold Markets (completely free, play money + Mana)."""
    BASE = "https://api.manifold.markets/v0"
    
    def get_markets(self, **kwargs):
        return self._get(f"{self.BASE}/markets", kwargs)
```

Other free prediction market APIs:
- **Manifold Markets**: `api.manifold.markets/v0` (free, no auth needed)
- **Metaculus**: `metaculus.com/api2` (free, auth optional)
- **Kalshi**: Has a public API but requires registration

### 7. Backtesting Framework

```python
# Save daily snapshots, then backtest:
# 1. Record market prices daily
# 2. Record your edge estimates  
# 3. After resolution, check if your edges were real
# 4. Calibration: if you say 70%, do events happen 70% of the time?

# Key metric: Brier Score
def brier_score(predictions, outcomes):
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / len(predictions)
```
