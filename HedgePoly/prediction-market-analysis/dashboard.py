"""
dashboard.py — Interactive Web Dashboard for Prediction Market Alpha.

Serves the HTML dashboard and provides API endpoints for live data.
"""

import json
import sys
from pathlib import Path
from flask import Flask, render_template, jsonify, send_from_directory

from utils import load_config, DataLoader, Config, log
from kelly import EmpiricalKellyEngine
from calibration import CalibrationEngine
from orderflow import OrderFlowEngine

import numpy as np


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


# ── App Factory ──────────────────────────────────────────────────────────────

def create_app(cfg: Config) -> Flask:
    app = Flask(__name__, template_folder="../templates")
    app.json_encoder = NumpyEncoder

    loader = DataLoader(cfg)
    kelly_eng = EmpiricalKellyEngine(cfg, loader)
    cal_eng = CalibrationEngine(cfg, loader)
    flow_eng = OrderFlowEngine(cfg, loader)

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/api/overview")
    def api_overview():
        """Load cached pipeline results."""
        results_path = Path(cfg.output_dir) / "pipeline_results.json"
        if results_path.exists():
            with open(results_path) as f:
                return jsonify(json.load(f))
        return jsonify({"error": "Run pipeline.py first"})

    @app.route("/api/kelly/<float:price>/<float:prob>")
    def api_kelly(price, prob):
        """On-demand Kelly calculation."""
        try:
            result = kelly_eng.calculate_empirical_kelly(
                entry_price=price,
                estimated_prob=prob,
                platform="kalshi",
                strategy_label=f"API ({price*100:.0f}¢, {prob*100:.0f}%)",
            )
            return jsonify(result.to_dict())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/calibration")
    def api_calibration():
        """1D calibration curve."""
        try:
            cal = cal_eng.build_1d_calibration("kalshi")
            return jsonify(cal.to_dict(orient="records"))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/surface")
    def api_surface():
        """2D calibration surface."""
        try:
            s = cal_eng.build_surface("kalshi")
            return jsonify({
                "price_bins": s.price_bins.tolist(),
                "time_bins": s.time_bins.tolist(),
                "surface": np.nan_to_num(s.surface_matrix, nan=0).tolist(),
                "counts": s.count_matrix.tolist(),
                "longshot_bias": s.overall_longshot_bias,
                "favorite_bias": s.overall_favorite_bias,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/orderflow")
    def api_orderflow():
        """Order flow stats."""
        try:
            stats = flow_eng.analyze("kalshi")
            return jsonify({
                "total_trades": stats.total_trades,
                "taker_excess": stats.taker_excess_return,
                "taker_negative_levels": stats.taker_negative_levels,
                "maker_yes_excess": stats.maker_buy_yes_excess,
                "maker_no_excess": stats.maker_buy_no_excess,
                "cohens_d": stats.maker_cohens_d,
                "price_levels": stats.price_level_stats.to_dict(orient="records"),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/longshots")
    def api_longshots():
        """Top opportunities."""
        try:
            opps = cal_eng.get_longshot_opportunities("kalshi", top_n=10)
            return jsonify([
                {
                    "price": b.price_bin_center,
                    "time": b.time_bin_center,
                    "implied": b.implied_prob,
                    "empirical": b.empirical_prob,
                    "mispricing": b.mispricing,
                    "n_trades": b.n_trades,
                }
                for b in opps
            ])
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


def main():
    config_path = "config/config.toml"
    for i, arg in enumerate(sys.argv):
        if arg == "--config" and i + 1 < len(sys.argv):
            config_path = sys.argv[i + 1]

    cfg = load_config(config_path)
    app = create_app(cfg)

    log.info(f"🌐 Dashboard starting on http://{cfg.host}:{cfg.port}")
    app.run(host=cfg.host, port=cfg.port, debug=cfg.debug)


if __name__ == "__main__":
    main()
