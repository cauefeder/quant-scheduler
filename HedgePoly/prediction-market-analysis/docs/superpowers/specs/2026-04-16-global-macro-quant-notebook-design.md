# Global Macro Quant Research Notebook — Design Spec
**Date:** 2026-04-16  
**Author:** Senior Quantitative Researcher (Claude Code)  
**Status:** Approved — v3 (post spec-review iteration 2 fixes)

---

## 1. Overview

A standalone Jupyter notebook that applies a four-layer quantitative signal stack to four globally liquid macro assets. The notebook is self-contained, fully documented, and produces both in-notebook output (charts, tables, printed summaries) and a Telegram HTML report compatible with the existing project infrastructure.

**Output file:** `notebooks/global_macro_quant_report.ipynb`
> The `notebooks/` directory must be created at the project root if it does not exist.

---

## 2. Asset Universe

| Asset | yfinance Ticker | Asset Type | Rationale |
|-------|----------------|------------|-----------|
| US Equities | `SPY` | Equity ETF | Most liquid global equity ETF (~$500B AUM) |
| Bitcoin | `BTC-USD` | Crypto | 24/7 global liquidity, ~$1T market cap |
| Gold | `GC=F` | Futures | Universal safe haven, deepest commodity market |
| EUR/USD | `EURUSD=X` | FX | Most liquid FX pair (~$1.2T daily volume) |

---

## 3. Data Layer

- **Source:** `yfinance` — `yf.download(ticker, period="5y", interval="1d", auto_adjust=True)`
- **Period:** 5 years of daily adjusted OHLCV bars
- **Output:** `data: dict[str, pd.DataFrame]` — one DataFrame per asset with columns `[open, high, low, close, volume]`

### 3.1 Cleaning Rules
- Forward-fill missing close prices (handles FX weekends and public holidays)
- Drop leading NaN rows (before any valid data begins)
- Minimum row assertion: `len(df) > max(900, 0.8 × expected_rows)` where:
  - SPY / GC=F: expected = 252 × 5 = 1260 rows
  - EURUSD=X: expected = 261 × 5 = 1305 rows
  - BTC-USD: expected = 365 × 5 = 1825 rows
- **EURUSD=X volume:** yfinance returns `volume = 0` for spot FX. This is expected. All volume-based features or quality checks must be skipped for FX assets.
- **GC=F roll gaps:** `auto_adjust=True` does not fully handle futures roll discontinuities. All price-level features (momentum, returns) must be computed as log-returns so that any roll-date spike only affects a single observation rather than creating a persistent level shift.

### 3.2 Notebook Output
Summary table per asset: ticker, date range, row count, number of forward-filled days.

---

## 4. Feature Engineering

All features computed per asset and appended as columns. Features are z-score standardised using a `StandardScaler` fit **on the training set only** and then applied to the test set — never fit on the full dataset to avoid lookahead contamination.

### 4.1 Volatility Ratio (Schwager / Wilder)
- `ATR_14` = 14-period Average True Range
- `VR = ATR_14 / close` — price-normalised range
- `VR_rank` = percentile rank of VR over trailing 252-day window ∈ [0, 1]
- **Industry standard:** ✅ Ubiquitous regime filter on systematic desks

### 4.2 Candle Geometry — Determinant
- `det = open * close − high * low` (2×2 OHLC matrix determinant)
- `candle_det_norm = det / close²` — dimensionless shape score
- Positive → bullish body dominance; negative → wick-heavy / bearish structure
- **Industry standard:** ⚠️ Novel/educational — not standard at institutional level

### 4.3 HAR Volatility Lags (Corsi 2009)
- `log_ret = log(close / close.shift(1))`
- `RV_1d = log_ret²` — daily realised variance proxy (known at end of day t)
- `RV_5d = RV_1d.rolling(5).mean()` — weekly HAR component
- `RV_22d = RV_1d.rolling(22).mean()` — monthly HAR component
- **Industry standard:** ✅ Among the most cited vol models in academic and practitioner literature

### 4.4 Momentum (log-return based)
- `mom_20 = log(close / close.shift(20))` — 1-month momentum
- `mom_60 = log(close / close.shift(60))` — 3-month momentum
> Using log-returns for GC=F prevents roll-date spikes from distorting multi-period momentum.

---

## 5. Model Stack

### Layer 1 — Regime Detection (Rule-Based)

**Primary rule — VR_rank thresholds:**
- `VR_rank > 0.7` → **Trending**
- `VR_rank < 0.3` → **Choppy**
- `0.3 ≤ VR_rank ≤ 0.7` → **Ranging**

**Tiebreaker — candle_det_norm (applied only in the ±0.05 border bands):**

| VR_rank range | candle_det_norm | Final Regime |
|---------------|-----------------|--------------|
| 0.65 – 0.75   | > 0             | Trending     |
| 0.65 – 0.75   | ≤ 0             | Ranging      |
| 0.25 – 0.35   | > 0             | Ranging      |
| 0.25 – 0.35   | ≤ 0             | Choppy       |

**Output:** `regime` column per asset + 30-day rolling regime history bar chart.
**Industry standard:** ✅

---

### Layer 2 — Volatility Forecasting (HAR-OLS)

**Model:** OLS regression
```
RV_1d_target ~ RV_1d + RV_5d + RV_22d
```

**Target construction (no lookahead):**
```python
df["RV_1d_target"] = df["RV_1d"].shift(-1)  # next-day RV
# Row at index t predicts day t+1 using only information available at close of day t
# Drop the final row (NaN target)
```

**Train/test split:** chronological 80/20 (first 4 years train, last 1 year test).

**vol_regime tag rule:**
- `vol_regime = "Expanding"` if `vol_forecast[t] > RV_1d.rolling(22).mean()[t]` (forecast exceeds recent average)
- `vol_regime = "Contracting"` otherwise

**Output:** `vol_forecast` per asset, `vol_regime` tag, printed R², MAE, OLS coefficients table.
**Industry standard:** ✅ HAR-RV (Corsi 2009) — daily production model on vol desks.

---

### Layer 3 — Direction Signal (XGBoost Classifier)

**Model:** `XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.05, subsample=0.8, use_label_encoder=False, eval_metric="mlogloss")`

**Target construction (no lookahead):**
```python
# Correct form: compute rolling sum first, then shift to place result at the start row.
# This means row t holds the sum of log-returns from t+1 through t+20.
fwd_ret = df["log_ret"].rolling(20).sum().shift(-20)
# Drop the last 20 rows (NaN target at the tail) BEFORE defining the train/test split,
# so that no boundary row has a target that draws from the other partition.
df = df.iloc[:-20]
# Tercile boundaries computed on TRAINING SET ONLY:
q33_train = fwd_ret_train.quantile(0.33)
q67_train = fwd_ret_train.quantile(0.67)
# Apply to both sets:
signal = pd.cut(fwd_ret, bins=[-np.inf, q33_train, q67_train, np.inf], labels=[-1, 0, 1])
```
> Dropping the last 20 rows before splitting ensures no train-set target value draws from test-period data (eliminates boundary lookahead).

**Features:** `[VR_rank, candle_det_norm, RV_1d, RV_5d, RV_22d, mom_20, mom_60]` (z-scored with scaler fit on training set).

**Split:** 80/20 chronological, no shuffling. One model per asset.

**P(Neutral) behaviour:** If `P(Neutral)` is the dominant class (e.g., 0.80), then `p_win = max(P(Bullish), P(Bearish))` will be low, `edge` will be negative or near-zero, and the Kelly floor will suppress sizing to 0%. This is the correct and intended behaviour — high neutral probability means no position. The summary table always displays `P(Win) = max(P(Bullish), P(Bearish))` regardless of whether the Kelly floor fires; when Kelly = 0 the `Signal` column shows `NEUTRAL` and `Kelly%` shows `0.0%`.

**Output:** current signal, `P(Bearish)`, `P(Neutral)`, `P(Bullish)`, feature importance bar chart per asset.
**Industry standard:** ✅

---

### Layer 4 — Position Sizing (Half-Kelly)

Standard Kelly for a directional bet where win pays +1 and loss pays −1 (`b = 1`):
```
f* = 2 × p_win − 1        (full Kelly, b=1 payoff)
kelly_f = f* × 0.5         (half-Kelly)
```

Where `p_win = max(P(Bullish), P(Bearish))`.

- Floor: `kelly_f = 0` if `p_win < 0.52` (no edge)
- Cap: `kelly_f = min(kelly_f, 0.10)` (10% max per asset)

**Output:** position size % per asset, side (LONG / SHORT / FLAT).
**Industry standard:** ✅

---

## 6. Report Layer

### 6.1 Notebook Print Output
Formatted summary table printed at the end of the notebook:

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

- Config must be loaded by calling `load_config()` from `utils.py` — do NOT read `config.toml` directly via `tomllib.load()`. The `allowed_chat_ids` field is stored as a plain string (e.g. `"-1001234567890"`) in `config.toml`, not a TOML array. `load_config()` handles the comma-split and returns `cfg.allowed_chat_ids` as `list[int]`. Accessing the TOML directly would yield a string and the send loop would silently iterate over characters.
- Iterate over `cfg.allowed_chat_ids` (list of int) — not a single `chat_id`
- `utils.py` uses `tomllib` (Python ≥3.11 stdlib) with a `tomli` fallback. The project `.venv` runs Python 3.9, so `tomli` is the active path. Do NOT add the `toml` package — it is not used elsewhere in the project.
- Send via `httpx` using the same chunk-at-4000-chars pattern as `send_report.py`
- Format: HTML (matching existing `reporting.py` style)
- Include: header with date, per-asset signal block, disclaimer footer

---

## 7. Notebook Structure

```
Section 0  — Config & Imports
Section 1  — Data Download & Quality Check
Section 2  — Feature Engineering
Section 3  — Layer 1: Regime Detection
Section 4  — Layer 2: Volatility Forecasting (HAR-OLS)
Section 5  — Layer 3: Direction Signal (XGBoost)
Section 6  — Layer 4: Position Sizing (Half-Kelly)
Section 7  — Report: Notebook Print + Telegram Send
```

Each section contains:
1. Markdown header with model description and literature reference
2. "Real-world adoption" callout (✅ standard / ⚠️ novel)
3. Implementation code (well-commented)
4. Output cell (chart or table)

---

## 8. Dependencies

Use the project's existing `.venv` (managed by `uv`, see `pyproject.toml`). The `.venv` runs Python 3.9 (`requires-python = ">=3.9"`).

**Additional packages to install via `uv add`:**
```
yfinance
xgboost
statsmodels
scikit-learn
jupyter
ipykernel
```

**Already present in the `.venv`:**
```
pandas, numpy, plotly, httpx, tomli, scipy
```

> `jupyter` and `ipykernel` are required to open and execute the notebook. Neither is present in the current `.venv`.  
> `tomllib` is NOT available on Python 3.9 (stdlib only from 3.11); `tomli` is the active fallback and is already installed.

---

## 9. What is NOT included

- Gamma Exposure (requires options chain Excel files, not available via yfinance for global macro)
- Plotly animations (visual infrastructure only, not an analytical model)
- Live intraday data (daily bars only)
- Backtesting engine (signal generation only, not a full strategy backtest)
- Volume-based features for EURUSD=X (yfinance returns volume=0 for FX spot)

---

## 10. Success Criteria

- [ ] `notebooks/` directory exists at project root
- [ ] Notebook runs end-to-end without errors using the project `.venv`
- [ ] All 4 assets produce a signal + size at the final cell
- [ ] No lookahead bias: scaler fit on train only, tercile boundaries from train only, target correctly shifted
- [ ] Report prints clearly inside the notebook
- [ ] Telegram message sends successfully to all chat IDs in `allowed_chat_ids`
- [ ] Each section is self-explanatory to a reader without prior context
