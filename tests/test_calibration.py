"""TDD coverage for lib.calibration — probability binning + Brier score.

Both helpers are pure numpy/pandas so tests use synthetic arrays.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lib.calibration import bin_by_probability, brier_score, calibration_verdict


# ---------- bin_by_probability ----------


def test_binning_10_deciles_evenly_spaced() -> None:
    probs = np.linspace(0.0, 1.0, 100, endpoint=False) + 0.005
    outcomes = np.zeros(100, dtype=int)
    df = bin_by_probability(probs, outcomes, n_bins=10)
    assert len(df) == 10
    # Bin midpoints at 0.05, 0.15, ..., 0.95
    np.testing.assert_allclose(df["bin_center"].values, np.arange(0.05, 1.0, 0.10))


def test_binning_computes_observed_win_rate() -> None:
    """Bin 0.6–0.7 has 4 signals, 3 wins → observed=0.75."""
    probs = np.array([0.61, 0.62, 0.63, 0.68])
    outcomes = np.array([1, 1, 1, 0])
    df = bin_by_probability(probs, outcomes, n_bins=10)
    # All 4 land in the same bin — only one row returned
    assert len(df) == 1
    assert df["bin_center"].iloc[0] == pytest.approx(0.65)
    assert df["count"].iloc[0] == 4
    assert df["observed"].iloc[0] == pytest.approx(0.75)


def test_binning_returns_predicted_mean_per_bin() -> None:
    """The 'predicted' column reports the average predicted prob within
    each bin (useful when signals cluster at one end of the bin)."""
    probs = np.array([0.61, 0.62, 0.63, 0.68])
    outcomes = np.array([1, 0, 1, 0])
    df = bin_by_probability(probs, outcomes, n_bins=10)
    assert df["predicted"].iloc[0] == pytest.approx(np.mean(probs))


def test_binning_empty_bins_omitted() -> None:
    """Bins with zero observations don't clutter the output."""
    probs = np.array([0.05, 0.55, 0.95])
    outcomes = np.array([0, 1, 1])
    df = bin_by_probability(probs, outcomes, n_bins=10)
    # 3 signals in 3 different bins → 3 rows
    assert len(df) == 3


def test_binning_edge_case_prob_at_one() -> None:
    """A predicted probability of exactly 1.0 falls into the last bin."""
    probs = np.array([1.0, 1.0])
    outcomes = np.array([1, 1])
    df = bin_by_probability(probs, outcomes, n_bins=10)
    assert len(df) == 1
    assert df["bin_center"].iloc[0] == pytest.approx(0.95)


# ---------- brier_score ----------


def test_brier_score_zero_for_perfect_predictions() -> None:
    probs = np.array([0.99, 0.01, 1.0, 0.0])
    outcomes = np.array([1, 0, 1, 0])
    # (0.01)^2 × 2 + 0 × 2, then divided by 4 → 5e-5
    assert brier_score(probs, outcomes) == pytest.approx(5e-5, abs=1e-6)


def test_brier_score_025_for_uninformed_50_50() -> None:
    """Predicting 0.5 always on a balanced set gives Brier 0.25 exactly."""
    probs = np.full(100, 0.5)
    outcomes = np.tile([0, 1], 50)
    assert brier_score(probs, outcomes) == pytest.approx(0.25, abs=1e-9)


def test_brier_score_one_for_perfectly_wrong() -> None:
    probs = np.array([1.0, 0.0])
    outcomes = np.array([0, 1])
    assert brier_score(probs, outcomes) == pytest.approx(1.0)


# ---------- calibration_verdict ----------


def test_verdict_reasonably_calibrated() -> None:
    """Brier below 0.20 with mostly-on-diagonal bins → 'well-calibrated'."""
    # Synthetic well-calibrated 4-bin frame
    df = pd.DataFrame([
        {"bin_center": 0.15, "predicted": 0.14, "observed": 0.13, "count": 20},
        {"bin_center": 0.35, "predicted": 0.36, "observed": 0.38, "count": 20},
        {"bin_center": 0.55, "predicted": 0.56, "observed": 0.55, "count": 20},
        {"bin_center": 0.85, "predicted": 0.84, "observed": 0.82, "count": 20},
    ])
    text = calibration_verdict(df, brier=0.15)
    assert "well-calibrated" in text.lower() or "close to the diagonal" in text.lower()


def test_verdict_flags_systematic_bias() -> None:
    """When observed consistently below predicted → 'overconfident'."""
    df = pd.DataFrame([
        {"bin_center": 0.35, "predicted": 0.35, "observed": 0.15, "count": 20},
        {"bin_center": 0.55, "predicted": 0.55, "observed": 0.30, "count": 20},
        {"bin_center": 0.75, "predicted": 0.75, "observed": 0.45, "count": 20},
    ])
    text = calibration_verdict(df, brier=0.30)
    assert "overconfident" in text.lower() or "too confident" in text.lower()
