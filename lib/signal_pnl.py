"""Per-system realised-P&L aggregation over signal_tracker.db.

Reads the signals table and produces a per-system summary:
  total_signals, resolved, win_rate, total_pnl, avg_pnl_per_bet,
  sharpe_approx.

Sharpe is a per-bet approximation (mean / std) — not annualised. Useful
for ranking systems against each other, not for absolute risk-adjusted
return claims.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd


def realized_pnl_by_system(db_path: Path) -> pd.DataFrame:
    """Return one row per `system` with realised P&L aggregates.

    Columns: system, total_signals, resolved, win_rate, total_pnl,
    avg_pnl_per_bet, sharpe_approx.

    Empty DB → empty DataFrame with the same columns.
    """
    cols = [
        "system", "total_signals", "resolved",
        "win_rate", "total_pnl", "avg_pnl_per_bet", "sharpe_approx",
    ]
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT system, outcome, actual_pnl FROM signals",
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame(columns=cols)

    rows: list[dict[str, Any]] = []
    for system, grp in df.groupby("system"):
        # `resolved` here means "Gamma returned a clean win/loss". NO_MATCH
        # (slug rot) and NULL (untried) are excluded — they don't carry
        # P&L information.
        resolved = grp[grp["outcome"].isin(["WIN", "LOSS"])]
        total_signals = int(len(grp))
        n_resolved = int(len(resolved))

        if n_resolved == 0:
            rows.append({
                "system": system,
                "total_signals": total_signals,
                "resolved": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl_per_bet": 0.0,
                "sharpe_approx": 0.0,
            })
            continue

        wins = int((resolved["outcome"] == "WIN").sum())
        pnls = resolved["actual_pnl"].dropna()
        total_pnl = float(pnls.sum()) if not pnls.empty else 0.0
        avg_pnl = float(pnls.mean()) if not pnls.empty else 0.0

        if len(pnls) > 1 and pnls.std(ddof=1) > 1e-12:
            sharpe = float(pnls.mean() / pnls.std(ddof=1))
        else:
            sharpe = 0.0

        rows.append({
            "system": system,
            "total_signals": total_signals,
            "resolved": n_resolved,
            "win_rate": wins / n_resolved if n_resolved else 0.0,
            "total_pnl": total_pnl,
            "avg_pnl_per_bet": avg_pnl,
            "sharpe_approx": sharpe,
        })

    return pd.DataFrame(rows, columns=cols).sort_values("total_pnl", ascending=False)


def realized_pnl_by_segment(
    db_path: Path,
    *,
    system: str,
    segment_keys: tuple[str, ...] = ("signal_type", "ticker"),
    min_resolved: int = 1,
) -> pd.DataFrame:
    """Per-segment P&L for one system, grouping by the named SQL columns.

    Args:
        db_path: signal_tracker.db path.
        system: 'modeltelegra' / 'alphafeed' / etc.
        segment_keys: tuple of column names to group by. For modeltelegra:
            ('signal_type', 'ticker') gives the basic split; add 'timeframe'
            (lifted from raw_features) for the finest grain.
        min_resolved: filter out segments with fewer than this many WIN/LOSS
            outcomes — defaults to 1 so the caller can see everything.

    Returns one row per non-empty segment with columns:
        segment_keys + total_signals, resolved, win_rate, total_pnl,
        avg_pnl_per_bet, sharpe_approx.
    """
    out_cols = list(segment_keys) + [
        "total_signals", "resolved",
        "win_rate", "total_pnl", "avg_pnl_per_bet", "sharpe_approx",
    ]
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT signal_type, ticker, raw_features, outcome, actual_pnl "
            "FROM signals WHERE system = ?",
            conn, params=(system,),
        )
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame(columns=out_cols)

    # Lift timeframe out of raw_features if the caller asked for it.
    if "timeframe" in segment_keys:
        df["timeframe"] = df["raw_features"].apply(_extract_timeframe)

    rows: list[dict[str, Any]] = []
    for keys, grp in df.groupby(list(segment_keys), dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        resolved = grp[grp["outcome"].isin(["WIN", "LOSS"])]
        if len(resolved) < min_resolved:
            continue

        wins = int((resolved["outcome"] == "WIN").sum())
        pnls = resolved["actual_pnl"].dropna()
        total_pnl = float(pnls.sum()) if not pnls.empty else 0.0
        avg_pnl = float(pnls.mean()) if not pnls.empty else 0.0
        sharpe = (
            float(pnls.mean() / pnls.std(ddof=1))
            if len(pnls) > 1 and pnls.std(ddof=1) > 1e-12 else 0.0
        )

        row: dict[str, Any] = dict(zip(segment_keys, keys))
        row.update({
            "total_signals":    int(len(grp)),
            "resolved":         int(len(resolved)),
            "win_rate":         wins / len(resolved) if len(resolved) else 0.0,
            "total_pnl":        total_pnl,
            "avg_pnl_per_bet":  avg_pnl,
            "sharpe_approx":    sharpe,
        })
        rows.append(row)

    return pd.DataFrame(rows, columns=out_cols).sort_values(
        "total_pnl", ascending=False,
    )


def _extract_timeframe(raw_features_json: str | None) -> str | None:
    """Pull `timeframe` out of the raw_features JSON column. Returns None
    when the field is absent (typical for straddle signals)."""
    if not raw_features_json:
        return None
    try:
        import json
        return json.loads(raw_features_json).get("timeframe")
    except Exception:
        return None
