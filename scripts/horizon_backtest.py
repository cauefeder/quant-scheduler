"""M2 — replay logged trend signals at 24/48/72h horizons.

Pure analysis. No production changes. Pulls every trend_direction
signal that already has a WIN/LOSS outcome from signal_tracker.db,
re-prices each at three horizons via a single wide-window yfinance
fetch per ticker, and writes a per-(ticker × horizon) comparison
table to docs/modeltelegra_horizon_backtest.md.

Usage:
  python scripts/horizon_backtest.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = Path(__file__).resolve()
PROJECTS_DIR = _HERE.parent.parent
sys.path.insert(0, str(PROJECTS_DIR))

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402

import signal_tracker as st  # noqa: E402

HORIZONS_HOURS = [24, 48, 72]
REPORT_PATH = PROJECTS_DIR / "docs" / "modeltelegra_horizon_backtest.md"


def _load_signals() -> pd.DataFrame:
    conn = sqlite3.connect(st.DB_PATH)
    try:
        df = pd.read_sql_query(
            """
            SELECT id, ticker, direction, market_price, kelly_bet,
                   created_at, raw_features
            FROM signals
            WHERE system = 'modeltelegra'
              AND signal_type = 'trend_direction'
              AND outcome IN ('WIN', 'LOSS')
            """,
            conn,
        )
    finally:
        conn.close()
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    return df


def _fetch_hist(ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
    """One wide-window hourly fetch per ticker, normalised to UTC."""
    pad_start = (start - timedelta(days=2)).strftime("%Y-%m-%d")
    pad_end = (end + timedelta(days=5)).strftime("%Y-%m-%d")
    hist = yf.Ticker(ticker).history(start=pad_start, end=pad_end, interval="1h")
    if hist.empty:
        return hist
    if hist.index.tz is None:
        hist.index = hist.index.tz_localize("UTC")
    else:
        hist.index = hist.index.tz_convert("UTC")
    return hist


def _nearest_close(hist: pd.DataFrame, target: pd.Timestamp) -> float | None:
    if hist.empty:
        return None
    idx = hist.index.get_indexer([target], method="nearest")[0]
    if idx == -1:
        return None
    bar_ts = hist.index[idx]
    # Reject if nearest bar is more than 12h away from target (data gap)
    if abs((bar_ts - target).total_seconds()) > 12 * 3600:
        return None
    return float(hist["Close"].iloc[idx])


def _row_pnl(signal: pd.Series, future_price: float) -> tuple[str, float, float]:
    """Mirror resolve_modeltelegra_signals' trend math."""
    change = (future_price - signal["market_price"]) / signal["market_price"]
    went_up = change > 0
    is_win = (
        (signal["direction"] == "LONG" and went_up)
        or (signal["direction"] == "SHORT" and not went_up)
    )
    bet = signal["kelly_bet"] or 0.0
    magnitude = bet * abs(change)
    outcome = "WIN" if is_win else "LOSS"
    pnl = magnitude if is_win else -magnitude
    return outcome, pnl, change


def main() -> int:
    print(f"[horizon] Loading trend signals from {st.DB_PATH}")
    signals = _load_signals()
    print(f"[horizon] {len(signals)} resolved trend signals across {signals['ticker'].nunique()} tickers")

    # Per-ticker wide-window hourly fetch
    hist_by_ticker: dict[str, pd.DataFrame] = {}
    for ticker, grp in signals.groupby("ticker"):
        start = grp["created_at"].min().to_pydatetime()
        end = grp["created_at"].max().to_pydatetime() + timedelta(hours=max(HORIZONS_HOURS))
        print(f"[horizon] Fetching {ticker} {start.date()} → {end.date()}")
        hist_by_ticker[ticker] = _fetch_hist(ticker, start, end)
        print(f"[horizon]   {len(hist_by_ticker[ticker]):,} bars")

    # Replay each signal at each horizon
    rows: list[dict] = []
    no_match_count = 0
    for _, sig in signals.iterrows():
        hist = hist_by_ticker.get(sig["ticker"])
        if hist is None or hist.empty:
            continue
        for h in HORIZONS_HOURS:
            target = pd.Timestamp(sig["created_at"]) + pd.Timedelta(hours=h)
            future_price = _nearest_close(hist, target)
            if future_price is None:
                no_match_count += 1
                continue
            outcome, pnl, change = _row_pnl(sig, future_price)
            rows.append({
                "ticker": sig["ticker"],
                "horizon_h": h,
                "outcome": outcome,
                "pnl": pnl,
                "abs_change": abs(change),
            })

    if no_match_count:
        print(f"[horizon] {no_match_count} (signal × horizon) pairs had no nearby bar — skipped")

    bt = pd.DataFrame(rows)
    if bt.empty:
        print("[horizon] No replay results — aborting report")
        return 1

    # Aggregate per (ticker, horizon)
    agg = (
        bt.groupby(["ticker", "horizon_h"])
          .agg(n=("outcome", "size"),
               wins=("outcome", lambda s: int((s == "WIN").sum())),
               total_pnl=("pnl", "sum"),
               avg_pnl=("pnl", "mean"))
          .reset_index()
    )
    agg["win_rate"] = agg["wins"] / agg["n"]

    # Sharpe per (ticker, horizon)
    def _sharpe(g: pd.DataFrame) -> float:
        if len(g) < 2:
            return 0.0
        std = g["pnl"].std(ddof=1)
        if std <= 1e-12:
            return 0.0
        return float(g["pnl"].mean() / std)

    sharpe_map = bt.groupby(["ticker", "horizon_h"]).apply(_sharpe, include_groups=False)
    agg["sharpe"] = agg.apply(lambda r: sharpe_map.loc[(r["ticker"], r["horizon_h"])], axis=1)

    # Per-horizon totals (cross-ticker)
    totals = (
        bt.groupby("horizon_h")
          .agg(n=("outcome", "size"),
               wins=("outcome", lambda s: int((s == "WIN").sum())),
               total_pnl=("pnl", "sum"),
               avg_pnl=("pnl", "mean"))
          .reset_index()
    )
    totals["win_rate"] = totals["wins"] / totals["n"]

    # Best horizon per ticker
    best_per_ticker = (
        agg.sort_values("total_pnl", ascending=False)
           .groupby("ticker")
           .first()
           .reset_index()[["ticker", "horizon_h", "total_pnl", "win_rate"]]
    )

    _write_report(agg=agg, totals=totals, best=best_per_ticker, n_signals=len(signals))
    print(f"[horizon] Wrote {REPORT_PATH}")
    return 0


def _write_report(*, agg: pd.DataFrame, totals: pd.DataFrame,
                  best: pd.DataFrame, n_signals: int) -> None:
    def _fmt_pnl(v: float) -> str:
        return f"${v:+.2f}"

    # Pivot per-ticker × horizon for the headline table
    pivot_pnl = agg.pivot(index="ticker", columns="horizon_h", values="total_pnl").fillna(0.0)
    pivot_wr = agg.pivot(index="ticker", columns="horizon_h", values="win_rate").fillna(0.0)
    pivot_sharpe = agg.pivot(index="ticker", columns="horizon_h", values="sharpe").fillna(0.0)

    # Sort by 24h baseline P&L for stable ordering
    pivot_pnl = pivot_pnl.sort_values(24, ascending=False)

    headline_rows: list[str] = []
    for ticker in pivot_pnl.index:
        cells = []
        for h in [24, 48, 72]:
            p = pivot_pnl.loc[ticker, h]
            wr = pivot_wr.loc[ticker, h]
            sh = pivot_sharpe.loc[ticker, h]
            cells.append(f"{_fmt_pnl(p)} ({wr*100:.0f}% WR, Sh {sh:+.2f})")
        headline_rows.append(f"| **{ticker}** | {cells[0]} | {cells[1]} | {cells[2]} |")

    total_rows = []
    for _, r in totals.iterrows():
        total_rows.append(
            f"| **{r['horizon_h']}h** | {r['n']} | {r['win_rate']*100:.1f}% | "
            f"{_fmt_pnl(r['total_pnl'])} | {_fmt_pnl(r['avg_pnl'])} |",
        )

    best_rows = []
    baseline_24 = pivot_pnl[24]
    for _, r in best.iterrows():
        baseline = baseline_24.get(r["ticker"], 0.0)
        delta = r["total_pnl"] - baseline
        marker = "→ **switch**" if r["horizon_h"] != 24 and delta > 0 else "→ keep 24h"
        best_rows.append(
            f"| {r['ticker']} | **{r['horizon_h']}h** | "
            f"{_fmt_pnl(r['total_pnl'])} | {r['win_rate']*100:.1f}% | "
            f"{_fmt_pnl(delta)} vs 24h | {marker} |",
        )

    # Verdict
    best_total_horizon = int(totals.loc[totals["total_pnl"].idxmax(), "horizon_h"])
    verdict_pnl = float(totals.loc[totals["horizon_h"] == best_total_horizon, "total_pnl"].iloc[0])
    baseline_pnl = float(totals.loc[totals["horizon_h"] == 24, "total_pnl"].iloc[0])

    if best_total_horizon == 24:
        verdict = (
            f"**Keep 24h.** The current horizon ({_fmt_pnl(baseline_pnl)}) "
            f"is the most profitable in this sample. Longer horizons add P&L "
            f"noise without adding edge."
        )
    elif verdict_pnl - baseline_pnl > 5.0:
        verdict = (
            f"**Switch to {best_total_horizon}h.** Cross-ticker total at "
            f"{best_total_horizon}h is {_fmt_pnl(verdict_pnl)} vs "
            f"{_fmt_pnl(baseline_pnl)} at 24h "
            f"(+{_fmt_pnl(verdict_pnl - baseline_pnl)} improvement)."
        )
    else:
        verdict = (
            f"**Marginal.** {best_total_horizon}h totals "
            f"{_fmt_pnl(verdict_pnl)} vs 24h {_fmt_pnl(baseline_pnl)} — "
            f"the difference ({_fmt_pnl(verdict_pnl - baseline_pnl)}) is "
            f"within the noise of a {n_signals}-signal sample. Keep 24h."
        )

    report = f"""# ModelTelegra trend horizon backtest

Replay of {n_signals} resolved trend_direction signals at horizons of
**24h** (current production), **48h**, and **72h**. Same WIN/LOSS +
magnitude math as the live resolver — only the timestamp at which the
future price is sampled changes.

Generated by `scripts/horizon_backtest.py`. Data: `signal_tracker.db`.

## Verdict

{verdict}

## Cross-ticker totals per horizon

| Horizon | n | Win rate | Total P&L | Avg / bet |
|---|---|---|---|---|
{chr(10).join(total_rows)}

## Per-ticker × horizon

Each cell is `total P&L (win rate, per-bet Sharpe)`.

| Ticker | 24h (current) | 48h | 72h |
|---|---|---|---|
{chr(10).join(headline_rows)}

## Best horizon per ticker

| Ticker | Best horizon | P&L at best | Win rate | Δ vs 24h | Recommendation |
|---|---|---|---|---|---|
{chr(10).join(best_rows)}

## Method

For each trend signal we have on disk:
1. Look up the ticker's hourly close price at `created_at + h` for
   `h ∈ [24, 48, 72]` using the same yfinance source the resolver uses.
2. Re-compute the WIN/LOSS + magnitude P&L using the live resolver's
   formula (`magnitude = kelly_bet × |change|`, signed by direction).
3. Aggregate per (ticker, horizon).

Signals where no hourly bar exists within 12 hours of the target
timestamp (weekend/holiday gaps on futures, yfinance outages) are
skipped rather than NO_MATCH-stamped — this is read-only analysis.

## Caveats

- **Same-sample evaluation.** The signals used here are the same 132
  the model produced during 64 days of live operation. We are
  evaluating "what if we had held longer?" on those exact signals, not
  re-running the model to generate fresh signals tuned to a longer
  horizon. The model's entry rule may itself be tuned to the 24h
  horizon; longer-horizon P&L from the same entries is an upper-bound
  estimate of what longer horizons could add.
- **No re-sized bets.** `kelly_bet` is held constant across horizons
  even though half-Kelly sizing for a 72h horizon would arguably be
  smaller (more volatility per bet → smaller fraction). Real
  deployment would re-size; results here may overstate 72h P&L
  proportionally.
- **No transaction-cost adjustment.** Same as the headline
  modeltelegra report — gross P&L only.
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
