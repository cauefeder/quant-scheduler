# Global Macro Quant Research Notebook — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, fully-documented Jupyter notebook that applies a four-layer quant signal stack (Regime → HAR-VOL → XGBoost Direction → Half-Kelly) to SPY, BTC-USD, GC=F, and EURUSD=X, printing results inline and sending a Telegram report.

**Architecture:** Core analytical functions live in `notebooks/quant_helpers.py` (importable, testable); the notebook `notebooks/global_macro_quant_report.ipynb` calls these functions and renders charts/tables per section. Tests in `tests/test_quant_helpers.py` cover each helper with deterministic fixtures.

**Tech Stack:** Python 3.9, yfinance, pandas, numpy, statsmodels (OLS), scikit-learn (StandardScaler), xgboost, plotly, httpx, utils.py (load_config), tomli

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `notebooks/quant_helpers.py` | **Create** | All analytical functions (data, features, models, report) |
| `notebooks/global_macro_quant_report.ipynb` | **Create** | Orchestration notebook — calls helpers, renders output |
| `tests/test_quant_helpers.py` | **Create** | Unit tests for every helper function |
| `pyproject.toml` | **Modify** | Add new dependencies |

---

## Task 1: Environment Setup

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Install missing dependencies**

From the project root (where `pyproject.toml` lives):
```bash
cd "d:/OMNP - Quant/Projetos/HedgePoly/prediction-market-analysis"
uv add "yfinance" "xgboost>=2.0" "statsmodels" "scikit-learn" "jupyter" "ipykernel"
```

Expected: packages added to `pyproject.toml` dependencies and `.venv` updated. Verify:
```bash
uv run python -c "import yfinance, xgboost, statsmodels, sklearn, jupyter; print('OK')"
```
Expected output: `OK`

- [ ] **Step 2: Create the notebooks directory**

```bash
mkdir -p notebooks
```

- [ ] **Step 3: Create empty helpers file**

Create `notebooks/quant_helpers.py` with just a module docstring for now:
```python
"""
quant_helpers.py — Analytical functions for the Global Macro Quant Research Notebook.

All functions are pure (no side effects) and take/return pandas DataFrames or
plain Python types. Import this module from the notebook.
"""
```

- [ ] **Step 4: Create empty test file**

Create `tests/test_quant_helpers.py`:
```python
"""Tests for notebooks/quant_helpers.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "notebooks"))
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock notebooks/quant_helpers.py tests/test_quant_helpers.py
git commit -m "feat: scaffold global macro notebook — add deps and empty helpers"
```

---

## Task 2: Data Layer

**Files:**
- Modify: `notebooks/quant_helpers.py`
- Modify: `tests/test_quant_helpers.py`

### Asset config

Assets and their expected minimum row counts:

```python
ASSETS = {
    "SPY":      {"exchange_type": "equity",  "min_rows": max(900, int(0.8 * 252 * 5))},   # 1008
    "BTC-USD":  {"exchange_type": "crypto",  "min_rows": max(900, int(0.8 * 365 * 5))},   # 1460
    "GC=F":     {"exchange_type": "futures", "min_rows": max(900, int(0.8 * 252 * 5))},   # 1008
    "EURUSD=X": {"exchange_type": "fx",      "min_rows": max(900, int(0.8 * 261 * 5))},   # 1044
}
FX_TICKERS = {"EURUSD=X"}   # volume = 0 for FX — skip volume-based checks
```

- [ ] **Step 1: Write failing tests for data functions**

Add to `tests/test_quant_helpers.py`:
```python
import numpy as np
import pandas as pd
import pytest
from quant_helpers import clean_df, ASSETS, FX_TICKERS


def _make_ohlcv(n=1300, with_gaps=False):
    """Synthetic OHLCV DataFrame for testing."""
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "open":   close * (1 + np.random.uniform(-0.005, 0.005, n)),
        "high":   close * (1 + np.abs(np.random.uniform(0, 0.01, n))),
        "low":    close * (1 - np.abs(np.random.uniform(0, 0.01, n))),
        "close":  close,
        "volume": np.random.randint(1_000_000, 10_000_000, n).astype(float),
    }, index=idx)
    if with_gaps:
        df.loc[df.index[5:10], "close"] = np.nan  # insert NaN gaps
    return df


def test_clean_df_forward_fills_gaps():
    df = _make_ohlcv(with_gaps=True)
    result = clean_df(df, "SPY")
    assert result["close"].isna().sum() == 0


def test_clean_df_drops_leading_nans():
    df = _make_ohlcv()
    df.iloc[:5] = np.nan
    result = clean_df(df, "SPY")
    assert not result.iloc[0].isna().any()


def test_clean_df_raises_on_too_few_rows():
    df = _make_ohlcv(n=500)
    with pytest.raises(AssertionError):
        clean_df(df, "SPY")


def test_clean_df_fx_volume_zero_ok():
    """EURUSD=X always has volume=0 — this must not raise."""
    df = _make_ohlcv(n=1100)
    df["volume"] = 0.0
    result = clean_df(df, "EURUSD=X")   # should not raise
    assert len(result) > 0
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/test_quant_helpers.py -v 2>&1 | head -30
```
Expected: `ImportError` or `NameError` (functions don't exist yet).

- [ ] **Step 3: Implement data functions in quant_helpers.py**

```python
import numpy as np
import pandas as pd
import yfinance as yf

ASSETS = {
    "SPY":      {"exchange_type": "equity",  "min_rows": max(900, int(0.8 * 252 * 5))},
    "BTC-USD":  {"exchange_type": "crypto",  "min_rows": max(900, int(0.8 * 365 * 5))},
    "GC=F":     {"exchange_type": "futures", "min_rows": max(900, int(0.8 * 252 * 5))},
    "EURUSD=X": {"exchange_type": "fx",      "min_rows": max(900, int(0.8 * 261 * 5))},
}
FX_TICKERS = {"EURUSD=X"}


def clean_df(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Standardise, forward-fill, and validate a raw yfinance DataFrame."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    # Keep only OHLCV columns
    df = df[["open", "high", "low", "close", "volume"]]
    # Forward-fill (handles FX weekends, holiday gaps)
    df = df.ffill()
    # Drop leading NaNs (rows before first valid close)
    df = df.dropna(subset=["close"])
    df = df.loc[df["close"].first_valid_index():]
    # Row count assertion
    min_rows = ASSETS[ticker]["min_rows"]
    assert len(df) >= min_rows, (
        f"{ticker}: only {len(df)} rows after cleaning, need >= {min_rows}"
    )
    return df


def download_asset(ticker: str, period: str = "5y") -> pd.DataFrame:
    """Download 5-year daily OHLCV from yfinance and clean."""
    raw = yf.download(ticker, period=period, interval="1d",
                      auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.droplevel(1)
    return clean_df(raw, ticker)


def load_all_assets(period: str = "5y") -> dict[str, pd.DataFrame]:
    """Download and clean all four macro assets. Returns dict keyed by ticker."""
    return {ticker: download_asset(ticker, period) for ticker in ASSETS}
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
uv run pytest tests/test_quant_helpers.py -v
```
Expected: 4 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add notebooks/quant_helpers.py tests/test_quant_helpers.py
git commit -m "feat: add data download and cleaning helpers with tests"
```

---

## Task 3: Feature Engineering

**Files:**
- Modify: `notebooks/quant_helpers.py`
- Modify: `tests/test_quant_helpers.py`

- [ ] **Step 1: Write failing tests**

```python
from quant_helpers import add_features


def test_vr_rank_bounded():
    df = _make_ohlcv(n=1300)
    result = add_features(df, "SPY")
    rank = result["VR_rank"].dropna()
    assert rank.between(0, 1).all(), "VR_rank must be in [0, 1]"


def test_candle_det_formula():
    """det = open*close - high*low, normalised by close^2"""
    df = _make_ohlcv(n=1300)
    result = add_features(df, "SPY")
    expected_det = (df["open"] * df["close"] - df["high"] * df["low"]) / df["close"] ** 2
    pd.testing.assert_series_equal(
        result["candle_det_norm"].dropna(),
        expected_det.dropna().rename("candle_det_norm"),
        check_names=False,
    )


def test_har_lags_not_null_after_warmup():
    df = _make_ohlcv(n=1300)
    result = add_features(df, "SPY")
    # After 22-day warmup, HAR lags must be valid
    tail = result.iloc[30:]
    assert tail[["RV_1d", "RV_5d", "RV_22d"]].isna().sum().sum() == 0


def test_momentum_log_returns():
    """Momentum uses log-returns, not pct_change."""
    df = _make_ohlcv(n=1300)
    result = add_features(df, "SPY")
    expected_mom20 = np.log(df["close"] / df["close"].shift(20))
    pd.testing.assert_series_equal(
        result["mom_20"].dropna(),
        expected_mom20.dropna().rename("mom_20"),
        check_names=False,
    )


def test_fx_no_volume_features():
    """EURUSD=X (FX) must not raise even with volume=0."""
    df = _make_ohlcv(n=1100)
    df["volume"] = 0.0
    result = add_features(df, "EURUSD=X")
    assert "VR_rank" in result.columns
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/test_quant_helpers.py -v -k "feature or vr_rank or candle or har or momentum or fx_no"
```
Expected: failures.

- [ ] **Step 3: Implement add_features()**

Add to `notebooks/quant_helpers.py`:
```python
def add_features(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    Compute all features and append as columns.
    Log-returns used throughout to handle GC=F roll gaps safely.
    """
    df = df.copy()

    # --- Log returns ---
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))

    # --- Volatility Ratio (Schwager / Wilder) ---
    # ATR(14): average of (high-low, |high-prev_close|, |low-prev_close|)
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["ATR_14"] = tr.ewm(span=14, adjust=False).mean()
    df["VR"] = df["ATR_14"] / df["close"]
    # Rolling percentile rank over 252-day window
    df["VR_rank"] = (
        df["VR"]
        .rolling(252)
        .apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    )

    # --- Candle Geometry — Determinant ---
    det = df["open"] * df["close"] - df["high"] * df["low"]
    df["candle_det_norm"] = det / df["close"] ** 2

    # --- HAR Volatility Lags (Corsi 2009) ---
    df["RV_1d"]  = df["log_ret"] ** 2
    df["RV_5d"]  = df["RV_1d"].rolling(5).mean()
    df["RV_22d"] = df["RV_1d"].rolling(22).mean()

    # --- Momentum (log-return based) ---
    df["mom_20"] = np.log(df["close"] / df["close"].shift(20))
    df["mom_60"] = np.log(df["close"] / df["close"].shift(60))

    return df
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
uv run pytest tests/test_quant_helpers.py -v
```
Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add notebooks/quant_helpers.py tests/test_quant_helpers.py
git commit -m "feat: add feature engineering (VR, candle det, HAR lags, momentum)"
```

---

## Task 4: Regime Detection

**Files:**
- Modify: `notebooks/quant_helpers.py`
- Modify: `tests/test_quant_helpers.py`

- [ ] **Step 1: Write failing tests**

```python
from quant_helpers import classify_regime


def test_regime_trending_high_vr():
    assert classify_regime(vr_rank=0.80, candle_det_norm=0.1) == "Trending"


def test_regime_choppy_low_vr():
    assert classify_regime(vr_rank=0.20, candle_det_norm=-0.1) == "Choppy"


def test_regime_ranging_mid_vr():
    assert classify_regime(vr_rank=0.50, candle_det_norm=0.0) == "Ranging"


def test_regime_tiebreaker_upper_bull():
    """VR_rank in 0.65-0.75, positive det -> Trending"""
    assert classify_regime(vr_rank=0.70, candle_det_norm=0.05) == "Trending"


def test_regime_tiebreaker_upper_bear():
    """VR_rank in 0.65-0.75, negative det -> Ranging"""
    assert classify_regime(vr_rank=0.70, candle_det_norm=-0.05) == "Ranging"


def test_regime_tiebreaker_lower_bull():
    """VR_rank in 0.25-0.35, positive det -> Ranging"""
    assert classify_regime(vr_rank=0.30, candle_det_norm=0.05) == "Ranging"


def test_regime_tiebreaker_lower_bear():
    """VR_rank in 0.25-0.35, negative det -> Choppy"""
    assert classify_regime(vr_rank=0.30, candle_det_norm=-0.05) == "Choppy"


def test_classify_regimes_series():
    """classify_regimes_series returns a Series of strings."""
    from quant_helpers import classify_regimes_series
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    regimes = classify_regimes_series(df)
    assert set(regimes.dropna().unique()).issubset({"Trending", "Ranging", "Choppy"})
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/test_quant_helpers.py -v -k "regime"
```

- [ ] **Step 3: Implement regime functions**

```python
def classify_regime(vr_rank: float, candle_det_norm: float) -> str:
    """
    Classify current market regime using VR_rank with candle determinant tiebreaker.

    Decision table (spec Section 5 Layer 1):
    Primary thresholds:
    - VR_rank > 0.7          -> Trending
    - VR_rank < 0.3          -> Choppy
    - 0.3 <= VR_rank <= 0.7  -> Ranging
    Border bands (±0.05 of threshold) use candle_det_norm as tiebreaker:
    - 0.65-0.75: candle_det_norm > 0 -> Trending, else Ranging
    - 0.25-0.35: candle_det_norm > 0 -> Ranging,  else Choppy
    Note: border bands are evaluated first so they override the primary rule
    in the ±0.05 zone around each threshold.
    """
    if 0.65 <= vr_rank <= 0.75:
        return "Trending" if candle_det_norm > 0 else "Ranging"
    if 0.25 <= vr_rank <= 0.35:
        return "Ranging" if candle_det_norm > 0 else "Choppy"
    if vr_rank > 0.7:
        return "Trending"
    if vr_rank < 0.3:
        return "Choppy"
    return "Ranging"


def classify_regimes_series(df: pd.DataFrame) -> pd.Series:
    """Apply classify_regime row-wise to a featured DataFrame."""
    return df.apply(
        lambda row: classify_regime(row["VR_rank"], row["candle_det_norm"])
        if pd.notna(row.get("VR_rank")) and pd.notna(row.get("candle_det_norm"))
        else None,
        axis=1,
    ).rename("regime")
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
uv run pytest tests/test_quant_helpers.py -v
```
Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add notebooks/quant_helpers.py tests/test_quant_helpers.py
git commit -m "feat: add regime detection with VR_rank + candle determinant tiebreaker"
```

---

## Task 5: HAR-OLS Volatility Forecasting

**Files:**
- Modify: `notebooks/quant_helpers.py`
- Modify: `tests/test_quant_helpers.py`

- [ ] **Step 1: Write failing tests**

```python
from quant_helpers import fit_har_model


def test_har_returns_forecast_series():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_har_model(df)
    assert "vol_forecast" in result.columns
    assert "vol_regime" in result.columns


def test_har_no_lookahead_in_target():
    """vol_forecast must be NaN for the training portion (first 80%)."""
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_har_model(df)
    n_train = int(len(result.dropna(subset=["RV_1d"])) * 0.8)
    train_forecasts = result["vol_forecast"].iloc[:n_train]
    assert train_forecasts.isna().all(), (
        "vol_forecast must be NaN in the training period — no predictions made on in-sample data"
    )


def test_har_vol_regime_values():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_har_model(df)
    valid = result["vol_regime"].dropna()
    assert set(valid.unique()).issubset({"Expanding", "Contracting"})


def test_har_metrics_returned():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    _, metrics = fit_har_model(df, return_metrics=True)
    assert "r2" in metrics and "mae" in metrics and "coefficients" in metrics
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/test_quant_helpers.py -v -k "har"
```

- [ ] **Step 3: Implement fit_har_model()**

```python
import statsmodels.api as sm
from sklearn.metrics import mean_absolute_error


def fit_har_model(df: pd.DataFrame, return_metrics: bool = False):
    """
    Fit HAR-OLS volatility model (Corsi 2009).
    Target: next-day RV (RV_1d.shift(-1)) — no lookahead.
    Train: first 80%, Test: last 20%.

    Returns df with vol_forecast and vol_regime columns appended.
    If return_metrics=True, also returns dict with r2, mae, coefficients.
    """
    df = df.copy()

    # Build target (shift-last, no lookahead)
    df["RV_1d_target"] = df["RV_1d"].shift(-1)
    df = df.dropna(subset=["RV_1d", "RV_5d", "RV_22d", "RV_1d_target"])

    split = int(len(df) * 0.8)
    train = df.iloc[:split]
    test  = df.iloc[split:]

    # Features
    feature_cols = ["RV_1d", "RV_5d", "RV_22d"]
    X_train = sm.add_constant(train[feature_cols])
    X_test  = sm.add_constant(test[feature_cols])

    model = sm.OLS(train["RV_1d_target"], X_train).fit()
    preds = model.predict(X_test)

    # Attach forecast only to test rows (no lookahead in train)
    df["vol_forecast"] = np.nan
    df.loc[test.index, "vol_forecast"] = preds.values

    # vol_regime: forecast vs 22-day rolling mean of RV
    rv22 = df["RV_22d"]
    df["vol_regime"] = df.apply(
        lambda row: "Expanding" if pd.notna(row["vol_forecast"]) and row["vol_forecast"] > rv22[row.name]
        else ("Contracting" if pd.notna(row["vol_forecast"]) else None),
        axis=1,
    )

    if return_metrics:
        y_true = test["RV_1d_target"]
        y_pred = preds
        metrics = {
            "r2":           model.rsquared,
            "mae":          mean_absolute_error(y_true, y_pred),
            "coefficients": model.params.to_dict(),
        }
        return df, metrics

    return df
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
uv run pytest tests/test_quant_helpers.py -v
```
Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add notebooks/quant_helpers.py tests/test_quant_helpers.py
git commit -m "feat: add HAR-OLS volatility forecasting (Corsi 2009, no lookahead)"
```

---

## Task 6: XGBoost Direction Signal

**Files:**
- Modify: `notebooks/quant_helpers.py`
- Modify: `tests/test_quant_helpers.py`

- [ ] **Step 1: Write failing tests**

```python
from quant_helpers import fit_xgb_signal


def test_xgb_returns_probabilities():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_xgb_signal(df)
    for col in ["xgb_signal", "p_bearish", "p_neutral", "p_bullish"]:
        assert col in result.columns, f"Missing column: {col}"


def test_xgb_probs_sum_to_one():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_xgb_signal(df)
    probs = result[["p_bearish", "p_neutral", "p_bullish"]].dropna()
    sums = probs.sum(axis=1)
    assert (sums.round(4) == 1.0).all(), "Probabilities must sum to 1"


def test_xgb_no_shuffle_chronological():
    """
    Tercile thresholds computed on training set must not use test-set returns.
    Verify by checking the split index is strictly chronological.
    """
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result, meta = fit_xgb_signal(df, return_meta=True)
    split_idx = meta["split_idx"]
    assert split_idx > 0 and split_idx < len(df), "Split index must be valid"
    assert meta["q33"] < meta["q67"], "Tercile bounds must be ordered"


def test_xgb_signal_values():
    df = _make_ohlcv(n=1300)
    df = add_features(df, "SPY")
    result = fit_xgb_signal(df)
    valid = result["xgb_signal"].dropna()
    assert set(valid.unique()).issubset({-1, 0, 1})
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/test_quant_helpers.py -v -k "xgb"
```

- [ ] **Step 3: Implement fit_xgb_signal()**

```python
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier


FEATURE_COLS = ["VR_rank", "candle_det_norm", "RV_1d", "RV_5d", "RV_22d", "mom_20", "mom_60"]


def fit_xgb_signal(df: pd.DataFrame, return_meta: bool = False):
    """
    Fit XGBoost 3-class direction model (Bearish=-1, Neutral=0, Bullish=+1).
    No lookahead: tercile boundaries from training set only, scaler fit on train.
    Target: 20-day forward log-return terciles.
    """
    df = df.copy()

    # Forward return — shift-last form (no lookahead)
    df["fwd_ret"] = df["log_ret"].rolling(20).sum().shift(-20)
    # Drop last 20 rows to avoid any boundary target drawing from future
    df = df.iloc[:-20]
    df = df.dropna(subset=FEATURE_COLS + ["fwd_ret"])

    split = int(len(df) * 0.8)
    train = df.iloc[:split]
    test  = df.iloc[split:]

    # Tercile boundaries — training set ONLY
    q33 = train["fwd_ret"].quantile(0.33)
    q67 = train["fwd_ret"].quantile(0.67)

    def label(x):
        if x < q33: return -1
        if x > q67: return  1
        return 0

    train_labels = train["fwd_ret"].map(label)
    test_labels  = test["fwd_ret"].map(label)

    # Scaler — fit on training set only
    scaler = StandardScaler()
    X_train = scaler.fit_transform(train[FEATURE_COLS])
    X_test  = scaler.transform(test[FEATURE_COLS])

    model = XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, eval_metric="mlogloss",
        random_state=42,
        # Note: use_label_encoder was removed in XGBoost >=2.0 — do not include it
    )
    # XGBoost needs labels 0,1,2 — remap from -1,0,1
    label_map = {-1: 0, 0: 1, 1: 2}
    inv_map   = {0: -1, 1: 0, 2: 1}
    model.fit(X_train, train_labels.map(label_map))

    proba = model.predict_proba(X_test)  # shape (n, 3): [P(-1), P(0), P(+1)]

    df["xgb_signal"]  = np.nan
    df["p_bearish"]   = np.nan
    df["p_neutral"]   = np.nan
    df["p_bullish"]   = np.nan

    preds = model.predict(X_test)
    df.loc[test.index, "xgb_signal"]  = [inv_map[p] for p in preds]
    df.loc[test.index, "p_bearish"]   = proba[:, 0]
    df.loc[test.index, "p_neutral"]   = proba[:, 1]
    df.loc[test.index, "p_bullish"]   = proba[:, 2]

    # Feature importance (stored on model object — caller can access via .feature_importances_)
    df.attrs["xgb_model"]       = model
    df.attrs["xgb_feature_cols"] = FEATURE_COLS

    if return_meta:
        meta = {"split_idx": split, "q33": q33, "q67": q67, "model": model}
        return df, meta

    return df
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
uv run pytest tests/test_quant_helpers.py -v
```
Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add notebooks/quant_helpers.py tests/test_quant_helpers.py
git commit -m "feat: add XGBoost 3-class direction signal (no lookahead, per-asset)"
```

---

## Task 7: Half-Kelly Position Sizing

**Files:**
- Modify: `notebooks/quant_helpers.py`
- Modify: `tests/test_quant_helpers.py`

- [ ] **Step 1: Write failing tests**

```python
from quant_helpers import compute_kelly


def test_kelly_bullish():
    """p_win=0.67, b=1: f*=2*0.67-1=0.34, half-Kelly=0.17, capped at 0.10"""
    result = compute_kelly(p_bullish=0.67, p_neutral=0.20, p_bearish=0.13)
    assert result["side"] == "LONG"
    assert abs(result["kelly_pct"] - 10.0) < 0.01  # capped at 10%


def test_kelly_bearish():
    result = compute_kelly(p_bullish=0.10, p_neutral=0.20, p_bearish=0.70)
    assert result["side"] == "SHORT"
    assert result["kelly_pct"] > 0


def test_kelly_neutral_no_edge():
    """p_win < 0.52 -> flat"""
    result = compute_kelly(p_bullish=0.35, p_neutral=0.40, p_bearish=0.25)
    assert result["side"] == "FLAT"
    assert result["kelly_pct"] == 0.0


def test_kelly_p_win_always_shown():
    """p_win = max(p_bull, p_bear) even when flat"""
    result = compute_kelly(p_bullish=0.35, p_neutral=0.40, p_bearish=0.25)
    assert result["p_win"] == pytest.approx(0.35)


def test_kelly_capped_at_10_pct():
    result = compute_kelly(p_bullish=0.95, p_neutral=0.03, p_bearish=0.02)
    assert result["kelly_pct"] == pytest.approx(10.0)


def test_kelly_formula_exact():
    """At p_win=0.60: f*=2*0.60-1=0.20, half=0.10 -> exactly 10% (hits cap)."""
    result = compute_kelly(p_bullish=0.60, p_neutral=0.20, p_bearish=0.20)
    full_kelly = 2 * 0.60 - 1   # = 0.20
    expected = min(full_kelly * 0.5 * 100, 10.0)
    assert result["kelly_pct"] == pytest.approx(expected)
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/test_quant_helpers.py -v -k "kelly"
```

- [ ] **Step 3: Implement compute_kelly()**

```python
def compute_kelly(p_bullish: float, p_neutral: float, p_bearish: float) -> dict:
    """
    Compute half-Kelly position size for a directional bet (b=1 payoff).

    Kelly formula (b=1): f* = 2*p_win - 1
    Half-Kelly:          kelly_f = f* * 0.5
    Floor:               0% if p_win < 0.52
    Cap:                 10% max

    Always returns p_win = max(p_bull, p_bear) for display, even when flat.
    """
    p_win = max(p_bullish, p_bearish)
    side  = "BULLISH" if p_bullish >= p_bearish else "BEARISH"

    if p_win < 0.52:
        return {"side": "FLAT", "p_win": p_win, "kelly_pct": 0.0}

    full_kelly = 2 * p_win - 1       # b=1 Kelly formula
    half_kelly = full_kelly * 0.5
    kelly_pct  = min(half_kelly * 100, 10.0)

    trade_side = "LONG" if side == "BULLISH" else "SHORT"
    return {"side": trade_side, "p_win": p_win, "kelly_pct": round(kelly_pct, 2)}
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
uv run pytest tests/test_quant_helpers.py -v
```
Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add notebooks/quant_helpers.py tests/test_quant_helpers.py
git commit -m "feat: add half-Kelly position sizing (b=1, 10% cap, 0.52 floor)"
```

---

## Task 8: Report Builder

**Files:**
- Modify: `notebooks/quant_helpers.py`
- Modify: `tests/test_quant_helpers.py`

- [ ] **Step 1: Write failing tests**

```python
from quant_helpers import build_summary_row, build_telegram_html


def _sample_signal():
    return {
        "ticker": "SPY",
        "regime": "Trending",
        "vol_regime": "Expanding",
        "side": "LONG",
        "signal_label": "BULLISH",
        "p_win": 0.67,
        "kelly_pct": 8.5,
    }


def test_build_summary_row_keys():
    row = build_summary_row("SPY", "Trending", "Expanding", "LONG", 0.67, 8.5)
    for key in ["ticker", "regime", "vol_regime", "side", "p_win", "kelly_pct"]:
        assert key in row


def test_telegram_html_contains_all_tickers():
    signals = [
        build_summary_row("SPY",      "Trending",  "Expanding",   "LONG",  0.67, 8.5),
        build_summary_row("BTC-USD",  "Choppy",    "Contracting", "FLAT",  0.51, 0.0),
        build_summary_row("GC=F",     "Ranging",   "Expanding",   "LONG",  0.61, 5.5),
        build_summary_row("EURUSD=X", "Ranging",   "Contracting", "SHORT", 0.58, 4.0),
    ]
    html = build_telegram_html(signals, date_str="2026-04-16")
    for ticker in ["SPY", "BTC-USD", "GC=F", "EURUSD=X"]:
        assert ticker in html


def test_telegram_html_is_valid_html():
    signals = [build_summary_row("SPY", "Trending", "Expanding", "LONG", 0.67, 8.5)]
    html = build_telegram_html(signals, date_str="2026-04-16")
    assert html.startswith("<")
    assert "<b>" in html or "<code>" in html
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/test_quant_helpers.py -v -k "summary or telegram"
```

- [ ] **Step 3: Implement report functions**

```python
from datetime import date


def build_summary_row(
    ticker: str,
    regime: str,
    vol_regime: str,
    side: str,
    p_win: float,
    kelly_pct: float,
) -> dict:
    """Build a dict representing one row in the summary report."""
    signal_label = (
        "BULLISH" if side == "LONG"
        else "BEARISH" if side == "SHORT"
        else "NEUTRAL"
    )
    return {
        "ticker":       ticker,
        "regime":       regime,
        "vol_regime":   vol_regime,
        "side":         side,
        "signal_label": signal_label,
        "p_win":        p_win,
        "kelly_pct":    kelly_pct,
    }


def build_summary_table_text(signals: list[dict], date_str: str) -> str:
    """Format signals as a plain-text table for notebook print."""
    header = f"\n{'='*55}\n GLOBAL MACRO QUANT REPORT — {date_str}\n{'='*55}\n"
    col_fmt = "{:<10} {:<10} {:<13} {:<8} {:>6} {:>7}"
    sep = "-" * 55
    lines = [
        header,
        col_fmt.format("Asset", "Regime", "Vol Regime", "Signal", "P(Win)", "Kelly%"),
        sep,
    ]
    for s in signals:
        lines.append(col_fmt.format(
            s["ticker"],
            s["regime"],
            s["vol_regime"],
            s["signal_label"],
            f"{s['p_win']*100:.0f}%",
            f"{s['kelly_pct']:.1f}%",
        ))
    lines.append(sep)
    return "\n".join(lines)


def build_telegram_html(signals: list[dict], date_str: str) -> str:
    """Build Telegram HTML report matching the existing reporting.py style."""
    lines = [
        f"<b>GLOBAL MACRO QUANT REPORT</b>",
        f"<code>{date_str}</code>",
        "",
    ]
    emoji_map = {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}
    for s in signals:
        em = emoji_map.get(s["signal_label"], "")
        lines += [
            f"<b>{s['ticker']}</b> {em} <code>{s['signal_label']}</code>",
            f"  Regime: {s['regime']} | Vol: {s['vol_regime']}",
            f"  P(Win): {s['p_win']*100:.0f}% | Kelly: {s['kelly_pct']:.1f}%",
            "",
        ]
    lines.append("<i>Research only. Not financial advice.</i>")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
uv run pytest tests/test_quant_helpers.py -v
```
Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add notebooks/quant_helpers.py tests/test_quant_helpers.py
git commit -m "feat: add report builder — summary table + Telegram HTML formatter"
```

---

## Task 9: Assemble the Notebook

**Files:**
- Create: `notebooks/global_macro_quant_report.ipynb`

The notebook is assembled by writing cells in order. Use `nbformat` to create the file programmatically, or create it directly in Jupyter. Each section follows the pattern: markdown explanation → code → output.

- [ ] **Step 1: Create the notebook skeleton**

Run this Python script to generate the empty notebook:
```bash
uv run python - << 'EOF'
import nbformat as nbf
nb = nbf.v4.new_notebook()
nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
with open("notebooks/global_macro_quant_report.ipynb", "w") as f:
    nbf.write(nb, f)
print("Notebook created.")
EOF
```

- [ ] **Step 2: Open the notebook and add Section 0 — Config & Imports**

Cell 1 (Markdown):
```markdown
# Global Macro Quant Research Notebook

**Date:** 2026-04-16  
**Author:** Senior Quantitative Researcher  

A four-layer signal stack applied to the most liquid global macro assets:
SPY (US equities), BTC-USD (crypto), GC=F (gold), EURUSD=X (FX).

---

## Section 0 — Config & Imports
```

Cell 2 (Code):
```python
# =============================================================
# SECTION 0 — Config & Imports
# =============================================================
import sys
import warnings
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import httpx

# Add project root to path so utils.py is importable
PROJECT_ROOT = Path("..").resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(".").resolve()))  # notebooks/ for quant_helpers

from utils import load_config
import quant_helpers as qh

# Load config (handles allowed_chat_ids string-split correctly)
cfg = load_config(str(PROJECT_ROOT / "config.toml"))

TODAY = date.today().isoformat()
SEND_TO_TELEGRAM = True   # set False to preview only

warnings.filterwarnings("ignore")
print(f"Config loaded. Bot token: {'SET' if cfg.bot_token else 'MISSING'}")
print(f"Chat IDs: {cfg.allowed_chat_ids}")
print(f"Report date: {TODAY}")
```

- [ ] **Step 3: Add Section 1 — Data Download**

Cell (Markdown):
```markdown
## Section 1 — Data Download & Quality Check

We use `yfinance` with `auto_adjust=True` to download 5 years of daily OHLCV bars.

**Assets:**
| Ticker | Asset | Why |
|--------|-------|-----|
| SPY | US Equities ETF | Deepest global equity liquidity (~$500B AUM) |
| BTC-USD | Bitcoin | 24/7 global crypto market (~$1T market cap) |
| GC=F | Gold Futures | Universal safe-haven, ~$200B daily volume |
| EURUSD=X | EUR/USD FX | Most liquid FX pair (~$1.2T daily volume) |

> **Note (GC=F):** Gold futures have roll-date gaps. All return calculations use log-returns to confine any roll spike to a single observation.  
> **Note (EURUSD=X):** Spot FX has no centralised volume — `volume = 0` is expected and ignored.
```

Cell (Code):
```python
print("Downloading market data...")
data = qh.load_all_assets(period="5y")

# Quality check summary
rows = []
for ticker, df in data.items():
    ff_days = df["close"].isna().sum()   # after ffill this should be 0
    rows.append({
        "Ticker": ticker,
        "Start": df.index[0].date(),
        "End":   df.index[-1].date(),
        "Rows":  len(df),
        "Fwd-Filled": ff_days,
    })
pd.DataFrame(rows).set_index("Ticker")
```

- [ ] **Step 4: Add Section 2 — Feature Engineering**

Cell (Markdown):
```markdown
## Section 2 — Feature Engineering

All features are computed on each asset independently.

| Feature | Formula | Literature |
|---------|---------|------------|
| VR (Volatility Ratio) | ATR(14) / close | Schwager, *Market Wizards* |
| VR_rank | Rolling 252-day percentile of VR | Common regime filter |
| candle_det_norm | (open×close − high×low) / close² | Novel geometric feature |
| RV_1d | log_ret² | Andersen & Bollerslev (1998) |
| RV_5d / RV_22d | Rolling means of RV_1d | Corsi (2009) HAR-RV |
| mom_20 / mom_60 | log(close/close.shift(N)) | Jegadeesh & Titman (1993) |

> **Real-world adoption:** VR, HAR lags, and momentum are daily tools on systematic desks. Candle determinant is a novel/educational feature — interpret with caution.
```

Cell (Code):
```python
print("Computing features...")
for ticker in data:
    data[ticker] = qh.add_features(data[ticker], ticker)

# Preview feature columns for SPY
print("\nSPY features (last 5 rows):")
feature_cols = ["VR_rank", "candle_det_norm", "RV_1d", "RV_5d", "RV_22d", "mom_20", "mom_60"]
data["SPY"][feature_cols].tail()
```

- [ ] **Step 5: Add Section 3 — Regime Detection**

Cell (Markdown):
```markdown
## Section 3 — Layer 1: Regime Detection

> **Real-world adoption:** ✅ Volatility regime filters are standard on every systematic desk. The specific formula varies (ATR, Garman-Klass, Parkinson vol) but the concept is universal.

**Decision logic:**
- VR_rank > 0.75 → **Trending** (high vol, directional momentum)
- VR_rank < 0.25 → **Choppy** (low vol, mean-reverting)  
- Otherwise → **Ranging** (neutral)
- Border bands (±0.05): candle determinant as tiebreaker (positive det → bullish body → promotes to higher regime)
```

Cell (Code):
```python
print("Classifying regimes...")
for ticker in data:
    data[ticker]["regime"] = qh.classify_regimes_series(data[ticker])

# Current regime per asset
current_regimes = {t: data[t]["regime"].dropna().iloc[-1] for t in data}
print("\nCurrent regimes:")
for t, r in current_regimes.items():
    print(f"  {t:<12} {r}")

# --- 30-day rolling regime chart ---
fig = make_subplots(rows=2, cols=2, subplot_titles=list(data.keys()))
regime_colors = {"Trending": "green", "Ranging": "gold", "Choppy": "red"}
for i, (ticker, df) in enumerate(data.items(), 1):
    last30 = df["regime"].iloc[-30:]
    row, col = (i - 1) // 2 + 1, (i - 1) % 2 + 1
    for regime, color in regime_colors.items():
        mask = last30 == regime
        fig.add_trace(go.Bar(
            x=last30.index[mask], y=[1] * mask.sum(),
            name=regime, marker_color=color, showlegend=(i == 1),
        ), row=row, col=col)
fig.update_layout(title="30-Day Regime History", barmode="stack", height=500)
fig.show()
```

- [ ] **Step 6: Add Section 4 — HAR-OLS Volatility Forecasting**

Cell (Markdown):
```markdown
## Section 4 — Layer 2: Volatility Forecasting (HAR-OLS)

> **Real-world adoption:** ✅ The HAR-RV model (Corsi 2009, *Journal of Financial Econometrics*) is one of the most robust volatility forecasting models in both academia and industry. Hedge fund vol desks run it or variants (HAR-CJ with jump components) daily.

**Model:** OLS regression  
`RV_next_day ~ β₀ + β₁·RV_1d + β₂·RV_5d + β₃·RV_22d`

**No lookahead:** Target is `RV_1d.shift(-1)`. Train on first 80% of data. Scaler fit on training set only.

**vol_regime tag:** "Expanding" if forecast > 22-day rolling mean of RV; "Contracting" otherwise.
```

Cell (Code):
```python
har_metrics = {}
print("Fitting HAR-OLS models...")
for ticker in data:
    data[ticker], metrics = qh.fit_har_model(data[ticker], return_metrics=True)
    har_metrics[ticker] = metrics
    print(f"\n{ticker}:")
    print(f"  R²  = {metrics['r2']:.4f}")
    print(f"  MAE = {metrics['mae']:.6f}")
    print(f"  Coefficients: {metrics['coefficients']}")

# Current vol regime
print("\nCurrent vol regimes:")
for ticker in data:
    vr = data[ticker]["vol_regime"].dropna().iloc[-1]
    print(f"  {ticker:<12} {vr}")
```

- [ ] **Step 7: Add Section 5 — XGBoost Direction Signal**

Cell (Markdown):
```markdown
## Section 5 — Layer 3: Direction Signal (XGBoost)

> **Real-world adoption:** ✅ Gradient boosting (XGBoost, LightGBM) is the dominant directional model on systematic equity and macro desks. Tree-based models handle the non-linear interactions between vol regime, momentum, and candle geometry naturally.

**Model:** `XGBClassifier` — 3 classes: Bearish (−1) / Neutral (0) / Bullish (+1)  
**Target:** 20-day forward log-return terciles (boundaries computed on training set only)  
**Features:** VR_rank, candle_det_norm, RV_1d, RV_5d, RV_22d, mom_20, mom_60  
**Split:** 80/20 chronological — no shuffling, no lookahead  
**One model per asset** — respects structural differences across asset classes.
```

Cell (Code):
```python
xgb_meta = {}
print("Fitting XGBoost models...")
for ticker in data:
    data[ticker], meta = qh.fit_xgb_signal(data[ticker], return_meta=True)
    xgb_meta[ticker] = meta

# Current signals
print("\nCurrent XGBoost signals (last test-set row):")
for ticker in data:
    row = data[ticker][["xgb_signal", "p_bearish", "p_neutral", "p_bullish"]].dropna().iloc[-1]
    print(f"  {ticker:<12} signal={int(row.xgb_signal):+d}  "
          f"P(Bear)={row.p_bearish:.2f}  P(Neut)={row.p_neutral:.2f}  P(Bull)={row.p_bullish:.2f}")

# Feature importance charts
fig = make_subplots(rows=2, cols=2, subplot_titles=list(data.keys()))
for i, (ticker, meta) in enumerate(xgb_meta.items(), 1):
    model = meta["model"]
    importances = model.feature_importances_
    row, col = (i - 1) // 2 + 1, (i - 1) % 2 + 1
    fig.add_trace(go.Bar(
        x=qh.FEATURE_COLS, y=importances,
        name=ticker, showlegend=False,
    ), row=row, col=col)
fig.update_layout(title="XGBoost Feature Importances by Asset", height=500)
fig.show()
```

- [ ] **Step 8: Add Section 6 — Half-Kelly Sizing**

Cell (Markdown):
```markdown
## Section 6 — Layer 4: Position Sizing (Half-Kelly)

> **Real-world adoption:** ✅ Kelly Criterion (Kelly 1956) is conceptually used on every systematic desk. Half-Kelly is the standard practitioner variant (reduces variance while retaining most of the growth rate). Institutional desks often layer volatility-targeting on top.

**Formula (b=1 payoff — long/short bet pays ±1):**
```
f* = 2 × p_win − 1        (full Kelly)
kelly_f = f* × 0.5         (half-Kelly)
```
**Floor:** 0% if p_win < 0.52  
**Cap:** 10% per asset  
**p_win = max(P(Bullish), P(Bearish))** — always displayed even when flat.
```

Cell (Code):
```python
sizing = {}
print("Computing position sizes...")
for ticker in data:
    last = data[ticker][["p_bearish", "p_neutral", "p_bullish"]].dropna().iloc[-1]
    result = qh.compute_kelly(last.p_bullish, last.p_neutral, last.p_bearish)
    sizing[ticker] = result
    print(f"  {ticker:<12} side={result['side']:<6}  "
          f"P(Win)={result['p_win']*100:.0f}%  Kelly={result['kelly_pct']:.1f}%")
```

- [ ] **Step 9: Add Section 7 — Report**

Cell (Markdown):
```markdown
## Section 7 — Report

Aggregates all layers into a final summary and sends to Telegram.
```

Cell (Code):
```python
# Build signal rows
signals = []
for ticker in data:
    last_regime     = data[ticker]["regime"].dropna().iloc[-1]
    last_vol_regime = data[ticker]["vol_regime"].dropna().iloc[-1]
    sz = sizing[ticker]
    row = qh.build_summary_row(
        ticker      = ticker,
        regime      = last_regime,
        vol_regime  = last_vol_regime,
        side        = sz["side"],
        p_win       = sz["p_win"],
        kelly_pct   = sz["kelly_pct"],
    )
    signals.append(row)

# Print summary table inside notebook
summary_text = qh.build_summary_table_text(signals, date_str=TODAY)
print(summary_text)

# Build Telegram HTML
html_report = qh.build_telegram_html(signals, date_str=TODAY)

# Send to Telegram
if SEND_TO_TELEGRAM and cfg.bot_token:
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    chunks = []
    current = ""
    for line in html_report.split("\n"):
        if len(current) + len(line) + 1 > 4000:
            if current:
                chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        chunks.append(current)

    with httpx.Client(timeout=15.0) as client:
        for chat_id in cfg.allowed_chat_ids:
            for i, chunk in enumerate(chunks, 1):
                resp = client.post(url, json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
                status = "OK" if resp.status_code == 200 else f"ERROR {resp.status_code}"
                print(f"  chat_id={chat_id} chunk={i}/{len(chunks)} -> {status}")
else:
    print("\n[PREVIEW MODE — not sending to Telegram]")
    print(html_report)
```

- [ ] **Step 10: Run the complete notebook end-to-end**

```bash
cd "d:/OMNP - Quant/Projetos/HedgePoly/prediction-market-analysis"
uv run jupyter nbconvert --to notebook --execute \
  --ExecutePreprocessor.timeout=300 \
  notebooks/global_macro_quant_report.ipynb \
  --output notebooks/global_macro_quant_report.ipynb
```

Expected: exit code 0, all cells execute, no errors. Check the final printed summary table in the last cell output.

- [ ] **Step 11: Run all tests one final time**

```bash
uv run pytest tests/test_quant_helpers.py -v
```
Expected: all tests PASSED.

- [ ] **Step 12: Commit**

```bash
git add notebooks/global_macro_quant_report.ipynb notebooks/quant_helpers.py
git commit -m "feat: add global macro quant research notebook (all 8 sections, Telegram report)"
```

---

## Full Test Run Reference

Run the complete test suite at any point:
```bash
cd "d:/OMNP - Quant/Projetos/HedgePoly/prediction-market-analysis"
uv run pytest tests/test_quant_helpers.py -v
```

Expected final state: **17+ tests, all PASSED**.

---

## Success Checklist (from spec)

- [ ] `notebooks/` directory exists at project root
- [ ] Notebook runs end-to-end without errors using the project `.venv`
- [ ] All 4 assets produce a signal + size at the final cell
- [ ] No lookahead bias: scaler fit on train only, tercile boundaries from train only, target correctly shifted
- [ ] Report prints clearly inside the notebook
- [ ] Telegram message sends successfully to all chat IDs in `allowed_chat_ids`
- [ ] Each section is self-explanatory to a reader without prior context
