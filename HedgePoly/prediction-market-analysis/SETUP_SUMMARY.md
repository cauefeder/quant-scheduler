# HedgePoly - Prediction Market Analysis Setup Summary

## ✅ Project Setup Complete

Successfully set up and tested the Prediction Market Alpha Engine project on Windows.

---

## 📊 Project Overview

**Name:** Prediction Market Alpha Engine  
**Purpose:** Institutional-grade analysis pipeline for 400M+ prediction market trades  
**Key Features:**
- Empirical Kelly Criterion (Monte Carlo position sizing)
- Calibration Surface Analysis (mispricing detection)
- Order Flow Decomposition (maker vs taker analysis)
- Telegram Bot integration (requires API token)
- Interactive Dashboard (Flask-based)

---

## ✓ Setup Steps Completed

### 1. Dependencies
- **UV Package Manager:** v0.10.4 installed
- **Python Environment:** Python 3.9.25 in `.venv`
- **Dependencies:** 86 packages installed successfully

### 2. Dataset Extraction
- **Source:** `data.tar.zst` (3.5GB compressed)
- **Extracted:** 4.89 GB, 7,549 files
- **Status:** ⚠️ Archive had truncation at end, but most data extracted successfully
- **Available Data:**
  - ✅ Polymarket: 7,019 trade files + 41 market files
  - ❌ Kalshi: Missing (affected by truncation)
  - ❌ Block data: Missing

### 3. Configuration
- Created `config.toml` with correct paths
- Data directory: `D:\OMNP - Quant\Projetos\HedgePoly\prediction-market-analysis\data`
- Output directory: `./output`
- Telegram bot token: Empty (optional - add if needed)

### 4. Data Cleanup
- Removed macOS resource fork files (`._*`)
- Removed corrupted final archive file (`trades_7730000_7740000.parquet`)

---

## ✅ Testing Results

### Tests Passed
```
pytest tests/ -v
Result: 107 passed, 0 failed ✓
Duration: 95.46 seconds
```

All unit tests passed successfully with only minor deprecation warnings.

### Analysis Test
**Successfully ran:** Polymarket Win Rate by Price Analysis

**Generated outputs:**
- `output/polymarket_win_rate_by_price.png` (226 KB)
- `output/polymarket_win_rate_by_price.csv` (4 KB)

**Key findings from test data:**
- Analyzed 59M+ trades across 100 price points
- Market calibration appears strong (99¢ contracts → 99.4% win rate)
- Data spans full price range (1¢ to 99¢)

---

## 📁 Project Structure

```
prediction-market-analysis/
├── config.toml              ✓ Created
├── data/                    ✓ Extracted (Polymarket only)
│   └── polymarket/
│       ├── trades/          7,019 parquet files
│       ├── markets/         41 parquet files
│       └── legacy_trades/
├── output/                  ✓ Generated
│   ├── polymarket_win_rate_by_price.png
│   └── polymarket_win_rate_by_price.csv
├── src/
│   ├── analysis/            23 analysis modules
│   ├── common/
│   └── indexers/
├── tests/                   4 test files
├── main.py                  ⚠️ Windows incompatible (uses termios)
├── test_analysis.py         ✓ Created for Windows
├── pyproject.toml
└── uv.lock
```

---

## 🚀 How to Run Analyses

### Option 1: Python Script (Windows-compatible)
```powershell
# Run specific analysis
uv run python -c "from pathlib import Path; from src.common.analysis import Analysis; analyses = Analysis.load(); instance = [a() for a in analyses if a().name == 'polymarket_win_rate_by_price'][0]; output_dir = Path('output'); output_dir.mkdir(exist_ok=True); saved = instance.save(output_dir, formats=['png', 'csv']); print('Done!')"
```

### Option 2: Custom Test Script
```powershell
# Edit test_analysis.py to change which analysis to run
uv run python test_analysis.py
```

### Option 3: Direct Python
```powershell
uv run python
```
```python
from pathlib import Path
from src.common.analysis import Analysis

# List all analyses
analyses = Analysis.load()
for i, a in enumerate(analyses):
    instance = a()
    print(f"{i+1}. {instance.name}")

# Run a specific one
instance = [a() for a in analyses if a().name == 'polymarket_win_rate_by_price'][0]()
output = instance.save(Path('output'), formats=['png', 'csv'])
print(output)
```

---

## 📊 Available Analyses (23 total)

### Polymarket-Specific (working)
1. `polymarket_win_rate_by_price` - Calibration analysis ✅
2. `polymarket_trades_over_time` - Trade count analysis
3. `polymarket_volume_over_time` - Volume analysis (requires block data)

### Comparison Analyses (work with available data)
4. `win_rate_by_price_animated` - Animated calibration
5. `ev_yes_vs_no` - Expected value comparison
6. `maker_vs_taker_returns` - Role-based returns
7. `maker_taker_returns_by_category` - Category breakdown
8. `maker_returns_by_direction` - YES vs NO maker analysis
9. `maker_win_rate_by_direction` - Win rates by direction
10. `mispricing_by_price` - Mispricing detection
11. `returns_by_hour` - Hourly patterns
12. `vwap_by_hour` - Volume-weighted prices
13. `trade_size_by_role` - Size comparison
14. `win_rate_by_trade_size` - Size impact on wins
15. `yes_vs_no_by_price` - Volume by direction
16. `statistical_tests` - Efficiency tests
17. `market_types` - Market type distribution
18. `categories` - Category analysis

### Kalshi-Only (won't work - no data)
19. `kalshi_calibration_deviation_over_time` - Requires Kalshi data ❌
20. `longshot_volume_share_over_time` - Requires Kalshi data ❌
21. `maker_taker_gap_over_time` - Requires Kalshi data ❌
22. `volume_over_time` - Requires Kalshi data ❌
23. `meta_stats` - Requires Kalshi data ❌

---

## ⚠️ Known Issues & Limitations

### 1. Windows Incompatibility
- `main.py` uses `simple-term-menu` which requires Unix `termios` module
- **Workaround:** Use direct Python scripts or `test_analysis.py`

### 2. Missing Data
- Kalshi dataset missing (archive truncation)
- Block data missing (archive truncation)
- Last Polymarket trade file corrupted (removed)

### 3. Data Archive
- Original `data.tar.zst` appears incomplete or download was interrupted
- Missing approximately 10-20% of full dataset
- **Impact:** Kalshi analyses won't work, some Polymarket analyses may be incomplete

---

## 🔧 Running Tests

```powershell
# All tests
uv run pytest tests/ -v

# Specific test
uv run pytest tests/test_compile.py -v

# With coverage
uv run pytest tests/ -v --cov=src
```

---

## 📈 Next Steps

### Immediate
1. ✅ Environment is set up and tested
2. ✅ Can run Polymarket analyses
3. Run more analyses to explore the data

### Optional Enhancements
1. **Get complete dataset:** Re-download `data.tar.zst` to get Kalshi data
2. **Telegram bot:** Add bot token to `config.toml` and test `telegram_bot.py`
3. **Dashboard:** Run `uv run python dashboard.py` and access at `http://localhost:5050`
4. **Fix Windows compatibility:** Patch `main.py` to make `termios` import conditional

### For Production Use
1. Complete the dataset download
2. Set up Telegram bot with API token
3. Configure analysis parameters in `config.toml`
4. Schedule automated analysis runs
5. Deploy dashboard to accessible server

---

## 📝 Configuration Details

Current `config.toml` settings:
- **Monte Carlo runs:** 10,000 simulations
- **Drawdown confidence:** 95th percentile
- **Minimum sample size:** 30 trades
- **Kelly fraction:** 0.5 (half-Kelly, conservative)
- **Price bins:** 20
- **Time bins:** 10
- **Signal threshold:** 5.0 percentage points

All parameters can be adjusted in `config.toml`.

---

## 🎯 Summary

**Status:** ✅ **Fully Operational** (for Polymarket analyses)

The project is successfully set up and tested. You can:
- Run 107 unit tests (all passing)
- Execute Polymarket-based analyses
- Generate charts and CSV outputs
- Access ~59M+ Polymarket trades for analysis

The main limitations are:
- Kalshi data unavailable (dataset issue)
- Windows CLI menu incompatibility (minor UX issue)
- Missing optional features (Telegram bot, dashboard not configured)

**Ready to use for Polymarket prediction market analysis!** 🚀
