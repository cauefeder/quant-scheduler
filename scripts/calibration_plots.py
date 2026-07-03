"""Build per-system reliability diagrams for alphafeed + poly2.

For each system:
 1. Pull resolved (WIN/LOSS) signals with estimated_prob populated.
 2. Bin into 10 deciles, compute observed win rate + Brier score.
 3. Write docs/calibration/<system>.png — reliability diagram with
    diagonal reference line and points sized by bin count.
 4. Emit a per-system stanza for docs/calibration.md.

alphafeed's estimated_prob column stores `quantScore` = P(crowd_wrong).
Since we're checking calibration of the CONFIDENCE number vs whether
the model was ACTUALLY right, we compare directly against
(outcome == 'WIN') — that mapping was the point of the label
definition in train_model.py.

poly2 stores estimated_prob as the model's belief about the bet-side
outcome, so same direct comparison applies.
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

import numpy as np  # noqa: E402

import signal_tracker as st  # noqa: E402
from lib.calibration import bin_by_probability, brier_score, calibration_verdict  # noqa: E402

OUT_DIR = PROJECTS_DIR / "docs" / "calibration"
REPORT_PATH = PROJECTS_DIR / "docs" / "calibration.md"
SYSTEMS = ["alphafeed", "poly2"]


def _load(system: str) -> tuple[np.ndarray, np.ndarray]:
    conn = sqlite3.connect(st.DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT estimated_prob, outcome
            FROM signals
            WHERE system = ?
              AND outcome IN ('WIN', 'LOSS')
              AND estimated_prob IS NOT NULL
              AND estimated_prob > 0
            """,
            (system,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return np.array([]), np.array([])
    probs = np.array([r[0] for r in rows], dtype=float)
    outcomes = np.array([1 if r[1] == "WIN" else 0 for r in rows], dtype=int)
    return probs, outcomes


def _plot(system: str, probs, outcomes, brier: float) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = bin_by_probability(probs, outcomes, n_bins=10)

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], ls="--", color="gray", lw=0.8, label="Perfect calibration")
    ax.scatter(
        df["predicted"], df["observed"],
        s=np.sqrt(df["count"]) * 12, alpha=0.7, color="#1f77b4",
        edgecolors="black", linewidths=0.5,
        label=f"Observed (size ~ count, n={int(df['count'].sum())})",
    )
    for _, r in df.iterrows():
        ax.annotate(
            f"n={int(r['count'])}",
            xy=(r["predicted"], r["observed"]),
            xytext=(4, 4), textcoords="offset points",
            fontsize=7, color="#555",
        )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Predicted probability")
    ax.set_ylabel("Observed win rate")
    ax.set_title(f"Reliability diagram — {system}\nBrier score: {brier:.4f}")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out = OUT_DIR / f"{system}.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def _stanza(system: str, probs, outcomes) -> str:
    df = bin_by_probability(probs, outcomes, n_bins=10)
    brier = brier_score(probs, outcomes)
    verdict = calibration_verdict(df, brier=brier)

    rows = []
    for _, r in df.iterrows():
        rows.append(
            f"| {r['bin_center']:.2f} | {int(r['count'])} | "
            f"{r['predicted']:.3f} | {r['observed']:.3f} | "
            f"{(r['observed'] - r['predicted']):+.3f} |",
        )

    return f"""## {system}

- **N:** {len(probs):,} resolved signals with `estimated_prob` populated
- **Brier score:** **{brier:.4f}** (0 = perfect, 0.25 = uninformed 50/50, 1 = perfectly wrong)

**Verdict:** {verdict}

![Reliability diagram — {system}](calibration/{system}.png)

| Bin | Count | Mean predicted | Observed win rate | Gap (obs − pred) |
|---|---|---|---|---|
{chr(10).join(rows)}
"""


def main() -> int:
    stanzas: list[str] = []
    for system in SYSTEMS:
        probs, outcomes = _load(system)
        if probs.size == 0:
            print(f"[calibration] {system}: no data — skipping")
            continue
        brier = brier_score(probs, outcomes)
        path = _plot(system, probs, outcomes, brier)
        stanzas.append(_stanza(system, probs, outcomes))
        print(f"[calibration] {system}: n={len(probs)}, Brier={brier:.4f} → {path}")

    report = f"""# Calibration report — alphafeed + poly2

Do the models' stated probabilities match observed win rates? Reads
`signal_tracker.db`, bins resolved signals by predicted probability
into 10 deciles, and compares each bin's observed win rate against
what the model claimed.

A well-calibrated model has bins that hug the diagonal in the plots
below — predicting 0.65 means the market actually wins 65% of the
time. Deviations tell you whether the model is overconfident,
underconfident, or noisy.

The Brier score summarises the whole distribution in one number:
0 = perfect, 0.25 = uninformed coin flip, 1 = perfectly wrong.
Anything below 0.20 combined with bins near the diagonal is
production-usable.

{chr(10).join(stanzas)}

## Systems not covered

- **poly**: signals have `estimated_prob = NULL` — its logger never
  populated the field. Needs a fix at the poly logger site before
  calibration can be measured.
- **modeltelegra** per-ticker segments: all currently under 30
  resolved bets — sample too small for a decile-level reliability
  diagram.
"""
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"[calibration] Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
