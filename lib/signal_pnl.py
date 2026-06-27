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
