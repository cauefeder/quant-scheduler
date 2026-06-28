"""Backfill ModelTelegra signals via the yfinance-based resolver.

Mirrors backfill_polymarket.py: loops resolve_modeltelegra_signals()
with a delta-based stop condition until no more unresolved signals
make progress.

Usage:
  python scripts/backfill_modeltelegra.py [--max-iterations 200]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
PROJECTS_DIR = _HERE.parent.parent
sys.path.insert(0, str(PROJECTS_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import signal_tracker as st  # noqa: E402


def _unresolved_count() -> int:
    conn = sqlite3.connect(st.DB_PATH)
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM signals "
            "WHERE outcome IS NULL "
            "AND signal_type IN ('straddle', 'trend_direction')",
        ).fetchone()[0])
    finally:
        conn.close()


def _running_winrate() -> tuple[int, float]:
    conn = sqlite3.connect(st.DB_PATH)
    try:
        n_resolved = conn.execute(
            "SELECT COUNT(*) FROM signals "
            "WHERE outcome IN ('WIN', 'LOSS') "
            "AND signal_type IN ('straddle', 'trend_direction')",
        ).fetchone()[0]
        n_wins = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE outcome = 'WIN' "
            "AND signal_type IN ('straddle', 'trend_direction')",
        ).fetchone()[0]
    finally:
        conn.close()
    return int(n_resolved), float(n_wins / n_resolved) if n_resolved else (0, 0.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-iterations", type=int, default=200)
    args = parser.parse_args()

    initial = _unresolved_count()
    print(f"[backfill] Starting with {initial} unresolved modeltelegra signals")

    iteration = 0
    grand_total = 0
    prev_unresolved = initial
    while iteration < args.max_iterations:
        iteration += 1
        n = st.resolve_modeltelegra_signals()
        grand_total += n
        n_resolved, win_rate = _running_winrate()
        unresolved_now = _unresolved_count()
        delta = prev_unresolved - unresolved_now
        print(
            f"[backfill] iter {iteration}: +{n} W/L, -{delta} unresolved, "
            f"{n_resolved} W/L total, win rate {win_rate * 100:.1f}%, "
            f"{unresolved_now} pending",
        )
        if delta == 0:
            print(f"[backfill] No advance in iteration {iteration} — stopping")
            break
        prev_unresolved = unresolved_now

    print(f"[backfill] Done. Resolved {grand_total} this run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
