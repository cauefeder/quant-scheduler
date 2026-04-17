# Global Macro Quant Research Notebook — Design Spec
**Date:** 2026-04-16  
**Author:** Senior Quantitative Researcher (Claude Code)  
**Status:** Approved

---

## 1. Overview

A standalone Jupyter notebook that applies a four-layer quantitative signal stack to four globally liquid macro assets. The notebook is self-contained, fully documented, and produces both in-notebook output (charts, tables, printed summaries) and a Telegram HTML report compatible with the existing project infrastructure.

**Output file:**
`notebooks/global_macro_quant_report.ipynb`

---

## 2. Asset Universe

| Asset | yfinance Ticker | Rationale |
|-------|----------------|-----------|
| US Equities | `SPY` | Most liquid global equity ETF (~$500B AUM) |
| Bitcoin | `BTC-USD` | 24/7 global crypto, ~$1T market cap |
| Gold | `GC=F` | Universal safe haven, deepest commodity market |
| EUR/USD | `EURUSD=X` | Most liquid FX pair (~$1.2T daily volume) |

---

## 3. Data Layer

- **Source:** `yfinance` — `yf.download(ticker, period="5y", interval="1d", auto_adjust=True)`
- **Period:** 5 years of daily adjusted OHLCV bars
- **Output:** `data: dict[str, pd.DataFrame]` — one DataFrame per asset with columns `[open, high, low, close, volume]`
- **Cleaning:** forward-fill FX weekend gaps, drop leading NaNs, assert minimum 900 trading days per asset
- **Notebook output:** summary table (asset, date range, row count, missing days)

---

## 4. Feature Engineering

All features are computed per asset and appended as columns. Features are z-score standardised before model ingestion.

### 4.1 Volatility Ratio (Schwager / Wilder)
- `ATR_14` = 14-period Average True Range
- `VR = ATR_14 / close` — price-normalised range
- `VR_rank` = percentile rank of VR over trailing 252-day window ∈ [0, 1]
- **Industry standard:** ✅ Ubiquitous regime filter on systematic desks

### 4.2 Candle Geometry — Determinant
- `det = open * close − high * low` (2×2 matrix determinant of OHLC)
- `candle_det_norm = det / close²` — dimensionless shape score
- Positive → bullish body dominance; negative → wick-heavy / bearish structure
- **Industry standard:** ⚠️ Novel/educational; not standard at institutional level

### 4.3 HAR Volatility Lags (Corsi 2009)
- `log_ret = log(close / close.shift(1))`
- `RV_1d = log_ret²` — daily realised variance proxy
- `RV_5d = RV_1d.rolling(5).mean()` — weekly HAR component
- `RV_22d = RV_1d.rolling(22).mean()` — monthly HAR component
- **Industry standard:** ✅ One of the most cited vol models in academic and practitioner literature

### 4.4 Momentum
- `mom_20 = close.pct_change(20)` — 1-month momentum
- `mom_60 = close.pct_change(60)` — 3-month momentum

---

## 5. Model Stack

### Layer 1 — Regime Detection (Rule-Based)
- **Model:** VR_rank thresholds + candle_det_norm confirmation
- **Rules:**
  - `VR_rank > 0.7` → **Trending**
  - `VR_rank < 0.3` → **Choppy**
  - `0.3 ≤ VR_rank ≤ 0.7` → **Ranging**
  - Candle determinant used as tiebreaker when VR_rank is near thresholds (±0.05 band)
- **Output:** `regime` column per asset + 30-day rolling regime history chart
- **Industry standard:** ✅ Vol regime filtering is standard practice

### Layer 2 — Volatility Forecasting (HAR-OLS)
- **Model:** OLS regression `RV_1d_fwd ~ RV_1d + RV_5d + RV_22d`
- **Target:** `RV_1d` shifted −1 (next-day realised variance)
- **Evaluation:** chronological train (first 4y) / test (last 1y), walk-forward
- **Output:** `vol_forecast` per asset, `vol_regime` tag (Expanding / Contracting), printed R², MAE, coefficients
- **Industry standard:** ✅ HAR-RV (Corsi 2009) is a daily production model at many vol desks

### Layer 3 — Direction Signal (XGBoost Classifier)
- **Model:** `XGBClassifier` — 3-class: Bearish (−1) / Neutral (0) / Bullish (+1)
- **Target:** 20-day forward return terciles (bottom 33% = Bearish, middle = Neutral, top = Bullish)
- **Features:** `[VR_rank, candle_det_norm, RV_1d, RV_5d, RV_22d, mom_20, mom_60]` (z-scored)
- **Hyperparameters:** `n_estimators=200, max_depth=4, learning_rate=0.05, subsample=0.8`
- **Split:** 80/20 chronological — no lookahead, no shuffling
- **One model per asset** — respects structural differences across asset classes
- **Output:** current signal + `P(Bearish)`, `P(Neutral)`, `P(Bullish)` + feature importance chart
- **Industry standard:** ✅ Gradient boosting is the dominant direction model on systematic desks

### Layer 4 — Position Sizing (Half-Kelly)
- `p_win = max(P(Bullish), P(Bearish))` — probability of the dominant directional side
- `edge = p_win − 0.5`
- `kelly_f = edge / (1 − edge) × 0.5` — half-Kelly fraction
- Capped: `max(kelly_f, 0)`, floored at 0% if `p_win < 0.52`, capped at 10% per asset
- **Output:** position size % per asset
- **Industry standard:** ✅ Half-Kelly is standard; institutional desks add vol-targeting on top

---

## 6. Report Layer

### 6.1 Notebook Output (print inside notebook)
A formatted ASCII / markdown summary table printed at the end of the notebook:

```
=== GLOBAL MACRO QUANT REPORT — 2026-04-16 ===

Asset      Regime     Vol Regime   Signal    P(Win)  Kelly%
---------  ---------  -----------  --------  ------  ------
SPY        Trending   Expanding    BULLISH   67%     8.5%
BTC-USD    Choppy     Contracting  NEUTRAL   54%     2.0%
GC=F       Ranging    Expanding    BULLISH   61%     5.5%
EURUSD=X   Ranging    Contracting  BEARISH   58%     4.0%
```

### 6.2 Telegram Report
- Format: HTML (matches existing `reporting.py` style)
- Uses existing `config.toml` for `bot_token` and `allowed_chat_ids`
- Sent via the same `httpx` pattern as `send_report.py`
- Includes: header, per-asset signal block, disclaimer footer
- Character limit: chunked at 4000 chars (matches existing implementation)

---

## 7. Notebook Structure

```
Section 0  — ⚙️ Config & Imports
Section 1  — 📥 Data Download & Quality Check
Section 2  — 🔧 Feature Engineering
Section 3  — 🗺️ Layer 1: Regime Detection
Section 4  — 📊 Layer 2: Volatility Forecasting (HAR-OLS)
Section 5  — 🎯 Layer 3: Direction Signal (XGBoost)
Section 6  — 💰 Layer 4: Position Sizing (Half-Kelly)
Section 7  — 📤 Report: Notebook Print + Telegram Send
```

Each section contains:
1. Markdown header with model description and literature reference
2. "Real-world adoption" callout (✅ / ⚠️)
3. Implementation code (well-commented)
4. Output cell (chart or table)

---

## 8. Dependencies

```
yfinance
pandas
numpy
statsmodels
xgboost
scikit-learn
plotly
httpx
toml
```

---

## 9. What is NOT included

- Gamma Exposure (requires options chain Excel files from B3, not available via yfinance for global macro)
- Plotly animations (visual infrastructure only, not an analytical model)
- Live intraday data (daily bars only)
- Backtesting engine (signal generation only, not a full strategy backtest)

---

## 10. Success Criteria

- [ ] Notebook runs end-to-end without errors on a fresh Python environment
- [ ] All 4 assets produce a signal + size at the final cell
- [ ] Report prints clearly inside the notebook
- [ ] Telegram message sends successfully to the configured chat
- [ ] Each section is self-explanatory to a reader without prior context
