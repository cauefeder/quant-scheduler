"""Calibration analysis for prediction-model probability outputs.

Two pure helpers plus a verdict-writer. Consumers pass in numpy arrays
of predicted probabilities and 0/1 outcomes; helpers return per-bin
reliability data and Brier score.

A well-calibrated model has observed win rates that hug the diagonal
predicted=observed line across probability bins. Systematic
above-diagonal → underconfident; below-diagonal → overconfident.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def bin_by_probability(
    predictions: "np.ndarray | pd.Series",
    outcomes: "np.ndarray | pd.Series",
    *,
    n_bins: int = 10,
) -> pd.DataFrame:
    """Bin predictions into equal-width buckets and compute observed win rate.

    Args:
        predictions: 1-D array of predicted probabilities in [0, 1].
        outcomes: 1-D array of 0/1 labels (WIN = 1, LOSS = 0), same length.
        n_bins: number of equal-width buckets (default 10 deciles).

    Returns:
        DataFrame with rows sorted by ascending bin_center. Columns:
            bin_center: midpoint of the probability bucket.
            predicted:  mean predicted probability inside the bucket.
            observed:   fraction of outcomes that were 1 in the bucket.
            count:      number of observations in the bucket.
        Empty buckets are omitted so downstream plots don't clutter.
    """
    p = np.asarray(predictions, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    if p.shape != y.shape:
        raise ValueError(f"predictions and outcomes shape mismatch: {p.shape} vs {y.shape}")

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    # Clip predictions to [0, 1] and use np.searchsorted with right='right' so
    # that a prediction of exactly 1.0 falls into the last bin rather than
    # overflowing to bin index n_bins.
    p_clipped = np.clip(p, 0.0, 1.0)
    bin_idx = np.searchsorted(edges, p_clipped, side="right") - 1
    bin_idx = np.clip(bin_idx, 0, n_bins - 1)

    rows: list[dict[str, float]] = []
    for k in range(n_bins):
        mask = bin_idx == k
        n = int(mask.sum())
        if n == 0:
            continue
        rows.append({
            "bin_center": (edges[k] + edges[k + 1]) / 2.0,
            "predicted":  float(p[mask].mean()),
            "observed":   float(y[mask].mean()),
            "count":      n,
        })

    return pd.DataFrame(rows).sort_values("bin_center").reset_index(drop=True)


def brier_score(
    predictions: "np.ndarray | pd.Series",
    outcomes: "np.ndarray | pd.Series",
) -> float:
    """Mean squared error between predicted probabilities and observed 0/1
    outcomes. Ranges from 0 (perfect) to 1 (perfectly wrong). Uninformed
    50/50 predictions on a balanced set score exactly 0.25.
    """
    p = np.asarray(predictions, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    if p.size == 0:
        return 0.0
    return float(np.mean((p - y) ** 2))


def calibration_verdict(reliability: pd.DataFrame, *, brier: float) -> str:
    """Plain-language verdict for a calibration table.

    Combines Brier score with per-bin bias to distinguish:
      - well-calibrated (Brier low + bins near diagonal)
      - overconfident (observed systematically below predicted)
      - underconfident (observed systematically above predicted)
      - noisy (Brier high but no consistent bias)
    """
    if reliability.empty:
        return "No calibration data — sample is empty."

    residuals = (reliability["observed"] - reliability["predicted"]).to_numpy()
    weights = reliability["count"].to_numpy(dtype=float)
    weighted_mean = float(np.average(residuals, weights=weights))
    all_negative = bool(np.all(residuals < 0))
    all_positive = bool(np.all(residuals > 0))

    if brier <= 0.20 and abs(weighted_mean) < 0.05:
        return (
            f"Well-calibrated. Brier score {brier:.3f} (low) and observed "
            f"win rates track close to the diagonal — the model's stated "
            f"probabilities can be trusted at face value."
        )
    if all_negative and weighted_mean < -0.05:
        return (
            f"Overconfident. Brier {brier:.3f}; observed win rate is lower "
            f"than predicted probability in every bucket (weighted gap "
            f"{weighted_mean:+.2f}). The model is too confident about the "
            f"side it picks. A shrinkage step (blend calibrated output with "
            f"a prior of 0.5) would tighten this."
        )
    if all_positive and weighted_mean > 0.05:
        return (
            f"Underconfident. Brier {brier:.3f}; observed win rate is higher "
            f"than predicted probability in every bucket (weighted gap "
            f"{weighted_mean:+.2f}). The model is producing signal but "
            f"discounting its own strength — a sharpening step or re-fit "
            f"Platt scaling would recover it."
        )
    return (
        f"Noisy calibration. Brier {brier:.3f}. Per-bin bias is inconsistent "
        f"(weighted gap {weighted_mean:+.2f}), so a simple linear correction "
        f"won't fix it — needs a re-trained model or a richer calibrator "
        f"(isotonic regression)."
    )
