"""Reset outcome=NO_MATCH on Polymarket signals that have a condition_id,
then run the standard backfill loop. Uses the condition_id-first resolver
(commit N1) to recover signals whose slug rotted.

Only touches poly / poly2 / polytraders — alphafeed's 1,199 NO_MATCH
signals have condition_id=NULL and stay untouched.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
PROJECTS_DIR = _HERE.parent.parent
sys.path.insert(0, str(PROJECTS_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import signal_tracker as st  # noqa: E402


def _reset_recoverable() -> int:
    """Set outcome=NULL on NO_MATCH signals with a non-empty condition_id."""
    conn = sqlite3.connect(st.DB_PATH)
    try:
        cur = conn.execute(
            """
            UPDATE signals
            SET outcome = NULL,
                actual_pnl = NULL,
                resolved_at = NULL,
                resolution_data = NULL
            WHERE outcome = 'NO_MATCH'
              AND signal_type = 'polymarket'
              AND condition_id IS NOT NULL
              AND condition_id != ''
            """,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _unresolved_count() -> int:
    conn = sqlite3.connect(st.DB_PATH)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM signals "
            "WHERE signal_type = 'polymarket' AND outcome IS NULL "
            "AND market_slug IS NOT NULL",
        ).fetchone()[0]
    finally:
        conn.close()
    return int(n)


def main() -> int:
    n_reset = _reset_recoverable()
    print(f"[recover] Reset {n_reset} NO_MATCH signals with condition_id")

    prev_unresolved = _unresolved_count()
    print(f"[recover] Unresolved before backfill: {prev_unresolved:,}")

    iteration = 0
    grand_total = 0
    while iteration < 200:
        iteration += 1
        n = st.resolve_polymarket_signals()
        grand_total += n
        unresolved_now = _unresolved_count()
        delta = prev_unresolved - unresolved_now
        print(
            f"[recover] iter {iteration}: +{n} W/L, -{delta} unresolved, "
            f"{unresolved_now:,} still unresolved",
        )
        if delta == 0:
            print(f"[recover] No advance in iteration {iteration} — stopping")
            break
        prev_unresolved = unresolved_now

    remaining = _unresolved_count()
    print(f"[recover] Done. Resolved {grand_total:,} this run. {remaining:,} still unresolved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
