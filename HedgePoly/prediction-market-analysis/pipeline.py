"""
pipeline.py — Main Analysis Pipeline.

Runs all three institutional methods sequentially:
  1. Empirical Kelly Criterion with Monte Carlo
  2. Calibration Surface Analysis
  3. Order Flow Decomposition

Caches results as JSON for the Telegram bot and dashboard.
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

from utils import load_config, DataLoader, log, Config
from kelly import EmpiricalKellyEngine, run_kelly_analysis
from calibration import CalibrationEngine, run_calibration_analysis
from orderflow import OrderFlowEngine, run_orderflow_analysis


class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient="records")
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def run_pipeline(cfg: Config):
    """Execute the full analysis pipeline."""
    start = time.time()
    output = Path(cfg.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    log.info("=" * 70)
    log.info("PREDICTION MARKET ALPHA ENGINE — Full Pipeline")
    log.info(f"Data: {cfg.data_dir}")
    log.info(f"Output: {cfg.output_dir}")
    log.info("=" * 70)

    # Initialize data loader
    loader = DataLoader(cfg)

    # Check data availability
    for platform in ["polymarket"]:
        count = loader.get_trade_count(platform)
        log.info(f"{platform}: {count:,} trades loaded")

    results = {
        "timestamp": datetime.now().isoformat(),
        "kelly": [],
        "calibration": {},
        "orderflow": {},
    }

    # ── Method 1: Empirical Kelly ────────────────────────────────────────────
    log.info("\n" + "=" * 70)
    log.info("METHOD 1: EMPIRICAL KELLY CRITERION")
    log.info("=" * 70)

    try:
        kelly_results = run_kelly_analysis(cfg, loader)
        results["kelly"] = [r.to_dict() for r in kelly_results]
        log.info(f"Kelly analysis complete: {len(kelly_results)} strategies analyzed")
    except Exception as e:
        log.error(f"Kelly analysis failed: {e}")
        results["kelly_error"] = str(e)

    # ── Method 2: Calibration Surface ────────────────────────────────────────
    log.info("\n" + "=" * 70)
    log.info("METHOD 2: CALIBRATION SURFACE ANALYSIS")
    log.info("=" * 70)

    try:
        surface = run_calibration_analysis(cfg, loader)
        results["calibration"] = {
            "longshot_bias": surface.overall_longshot_bias,
            "favorite_bias": surface.overall_favorite_bias,
            "n_bins": len(surface.bins),
            "surface_matrix": surface.surface_matrix,
            "count_matrix": surface.count_matrix,
            "price_bins": surface.price_bins,
            "time_bins": surface.time_bins,
            "bins": [
                {
                    "price": b.price_bin_center,
                    "time": b.time_bin_center,
                    "implied": b.implied_prob,
                    "empirical": b.empirical_prob,
                    "mispricing": b.mispricing,
                    "n": b.n_trades,
                }
                for b in surface.bins
            ],
        }
        log.info("Calibration analysis complete")
    except Exception as e:
        log.error(f"Calibration analysis failed: {e}")
        results["calibration_error"] = str(e)

    # ── Method 3: Order Flow ─────────────────────────────────────────────────
    log.info("\n" + "=" * 70)
    log.info("METHOD 3: ORDER FLOW DECOMPOSITION")
    log.info("=" * 70)

    try:
        flow_stats = run_orderflow_analysis(cfg, loader)
        results["orderflow"] = {
            "total_trades": flow_stats.total_trades,
            "total_volume": flow_stats.total_volume,
            "taker_excess_return": flow_stats.taker_excess_return,
            "taker_negative_levels": flow_stats.taker_negative_levels,
            "maker_buy_yes_excess": flow_stats.maker_buy_yes_excess,
            "maker_buy_no_excess": flow_stats.maker_buy_no_excess,
            "maker_avg_excess": flow_stats.maker_avg_excess,
            "maker_cohens_d": flow_stats.maker_cohens_d,
            "price_level_stats": flow_stats.price_level_stats,
        }
        log.info("Order flow analysis complete")
    except Exception as e:
        log.error(f"Order flow analysis failed: {e}")
        results["orderflow_error"] = str(e)

    # ── Save Results ─────────────────────────────────────────────────────────
    results_path = output / "pipeline_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, cls=NumpyEncoder, indent=2)

    elapsed = time.time() - start
    log.info(f"\n{'='*70}")
    log.info(f"Pipeline complete in {elapsed:.1f}s")
    log.info(f"Results saved to {results_path}")
    log.info(f"{'='*70}")

    return results


if __name__ == "__main__":
    config_path = "config.toml"
    # Support --config flag or positional arg
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--config" and i + 1 < len(sys.argv):
            config_path = sys.argv[i + 1]
        elif not arg.startswith("--"):
            config_path = arg

    cfg = load_config(config_path)
    run_pipeline(cfg)
