"""One-shot backfill of unresolved Polymarket signals in signal_tracker.db.

Loops `signal_tracker.resolve_polymarket_signals()` until it returns 0 or
the iteration cap is hit, printing per-batch progress + running win-rate.

Usage:
  python scripts/backfill_polymarket.py [--max-iterations 200]
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
        n = conn.execute(
            "SELECT COUNT(*) FROM signals "
            "WHERE signal_type = 'polymarket' AND outcome IS NULL AND market_slug IS NOT NULL",
        ).fetchone()[0]
    finally:
        conn.close()
    return int(n)


def _running_winrate() -> tuple[int, float]:
    conn = sqlite3.connect(st.DB_PATH)
    try:
        n_resolved = conn.execute(
            "SELECT COUNT(*) FROM signals "
            "WHERE signal_type = 'polymarket' AND outcome IS NOT NULL",
        ).fetchone()[0]
        n_wins = conn.execute(
            "SELECT COUNT(*) FROM signals "
            "WHERE signal_type = 'polymarket' AND outcome = 'WIN'",
        ).fetchone()[0]
    finally:
        conn.close()
    return int(n_resolved), float(n_wins / n_resolved) if n_resolved else (0, 0.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-iterations", type=int, default=200)
    args = parser.parse_args()

    initial_unresolved = _unresolved_count()
    print(f"[backfill] Starting with {initial_unresolved:,} unresolved polymarket signals")

    iteration = 0
    grand_total = 0
    prev_unresolved = initial_unresolved
    while iteration < args.max_iterations:
        iteration += 1
        n = st.resolve_polymarket_signals()
        grand_total += n
        n_resolved, win_rate = _running_winrate()
        unresolved_now = _unresolved_count()
        delta = prev_unresolved - unresolved_now  # advanced signals (WIN/LOSS + NO_MATCH)
        print(
            f"[backfill] iter {iteration}: +{n} W/L, -{delta} unresolved, "
            f"+{grand_total} W/L this run, "
            f"{n_resolved:,} W/L total, running win rate {win_rate * 100:.1f}%, "
            f"{unresolved_now:,} still unresolved",
        )
        # Stop when *nothing* moved — neither resolution nor NO_MATCH marking.
        if delta == 0:
            print(f"[backfill] No advance in iteration {iteration} — stopping")
            break
        prev_unresolved = unresolved_now

    remaining = _unresolved_count()
    print(f"[backfill] Done. Resolved {grand_total:,} this run. {remaining:,} still unresolved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
