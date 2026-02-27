# 🎯 Prediction Market Alpha Engine

**Institutional-grade analysis pipeline for 400M+ prediction market trades.**

Implements the three core hedge fund methodologies:
1. **Empirical Kelly Criterion** — Monte Carlo uncertainty-adjusted position sizing
2. **Calibration Surface Analysis** — Price × Time mispricing detection
3. **Order Flow Decomposition** — Maker vs Taker profitability analysis

Plus: **Telegram Bot** for real-time insight alerts and an **Interactive Dashboard**.

---

## Quick Start

### 1. Prerequisites
- Python 3.9+
- 40GB free disk space (for the full dataset)
- A Telegram Bot Token ([create one here](https://t.me/BotFather))

### 2. Install Dependencies
```bash
pip install duckdb pandas numpy matplotlib seaborn scipy python-telegram-bot flask plotly pyarrow --break-system-packages
```

### 3. Download the Dataset
```bash
# Clone the dataset repo
git clone https://github.com/Jon-Becker/prediction-market-analysis
cd prediction-market-analysis
make setup
# This downloads ~36GB compressed and extracts to data/
```

### 4. Configure
```bash
cp config/config_example.toml config/config.toml
# Edit config.toml with your Telegram bot token and data path
```

### 5. Run the Analysis Pipeline
```bash
python src/pipeline.py --config config/config.toml
```

### 6. Start the Telegram Bot
```bash
python src/telegram_bot.py --config config/config.toml
```

### 7. Launch the Dashboard
```bash
python src/dashboard.py --config config/config.toml
# Open http://localhost:5050 in your browser
```

---

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and overview |
| `/kelly <price> <estimated_prob>` | Empirical Kelly position sizing |
| `/calibration <price>` | Calibration bias at price level |
| `/surface <price> <days_to_resolution>` | Full surface mispricing lookup |
| `/orderflow` | Latest maker vs taker stats |
| `/longshots` | Top longshot bias opportunities |
| `/report` | Full daily insight report |
| `/help` | List all commands |

---

## Project Structure
```
prediction-market-alpha/
├── config/
│   ├── config_example.toml    # Example configuration
│   └── config.toml            # Your local config (git-ignored)
├── src/
│   ├── pipeline.py            # Main analysis pipeline
│   ├── kelly.py               # Empirical Kelly + Monte Carlo
│   ├── calibration.py         # Calibration surface analysis
│   ├── orderflow.py           # Maker vs Taker decomposition
│   ├── telegram_bot.py        # Telegram bot for alerts
│   ├── dashboard.py           # Flask dashboard server
│   └── utils.py               # Shared utilities
├── templates/
│   └── dashboard.html         # Interactive dashboard UI
├── data/                      # Dataset location (after download)
└── README.md
```

---

## How It Works

### Method 1: Empirical Kelly with Monte Carlo
- Extracts historical trade patterns matching your criteria
- Builds empirical return distributions (not Gaussian assumptions)
- Runs 10,000 Monte Carlo path simulations
- Calculates drawdown distributions at 50th/95th/99th percentiles
- Outputs uncertainty-adjusted position sizes

### Method 2: Calibration Surface
- Builds C(p, t) calibration function across price × time
- Detects longshot bias (-57% at 1¢ contracts per Becker's research)
- Maps mispricing M(p, t) = C(p, t) - p/100 across full surface
- Identifies time-varying entry/exit signals

### Method 3: Order Flow Decomposition
- Separates maker vs taker populations using dataset tags
- Measures excess returns by price level and direction
- Quantifies systematic wealth transfer (takers lose at 80/99 levels)
- Identifies optimal liquidity provision zones

---

## License
MIT — Built on Jon Becker's open-source prediction market dataset.
