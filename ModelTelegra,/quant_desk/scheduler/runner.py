"""
Main orchestrator — runs all models, generates reports, sends Telegram.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from config.settings import paths
from models.model1_volatility import Model1Result, run_model1
from models.model2_trend import Model2Result, run_model2
from models.model3_risk import Model3Result, integrate_signals
from reporting.charts import generate_all_charts
from reporting.telegram_bot import (
    format_daily_report,
    send_alert,
    send_chart_image,
    send_telegram_message,
)

logger = logging.getLogger(__name__)


def run_full_pipeline(
    custom_ticker: Optional[str] = None,
    capital: float = 10000.0,
    send_telegram: bool = True,
) -> None:
    """
    Execute the complete daily analysis pipeline.

    1. Run Model 1 (BTC Volatility & GEX)
    2. Run Model 2 (Multi-Asset Trend Classification)
    3. Run Model 3 (Signal Integration & Risk)
    4. Generate charts
    5. Format and send Telegram report

    Parameters
    ----------
    custom_ticker : str, optional
        Additional ticker to analyze.
    capital : float
        Total trading capital for position sizing.
    send_telegram : bool
        Whether to send Telegram messages.
    """
    start_time = time.time()
    logger.info("=" * 70)
    logger.info(f"QUANT DESK — Daily Analysis Pipeline")
    logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 70)

    # --- Model 1: BTC Volatility ---
    try:
        m1: Model1Result = run_model1()
        logger.info(f"Model 1 complete: {m1.day_type}")
    except Exception as e:
        logger.error(f"Model 1 FAILED: {e}", exc_info=True)
        if send_telegram:
            send_alert("Model 1 Failed", f"BTC Volatility model error: {e}")
        return

    # --- Model 2: Multi-Asset Trends ---
    try:
        m2: Model2Result = run_model2(
            custom_ticker=custom_ticker,
            vol_regime=m1.regime.regime.value,  # cross-signal: vol context informs trend confidence
        )
        logger.info(f"Model 2 complete: {len(m2.summary)} signals generated")
    except Exception as e:
        logger.error(f"Model 2 FAILED: {e}", exc_info=True)
        if send_telegram:
            send_alert("Model 2 Failed", f"Trend model error: {e}")
        return

    # --- Model 3: Signal Integration ---
    try:
        m3: Model3Result = integrate_signals(m1, m2, capital=capital)
        logger.info(f"Model 3 complete: BTC={m3.btc_signal.decision.value}")
    except Exception as e:
        logger.error(f"Model 3 FAILED: {e}", exc_info=True)
        if send_telegram:
            send_alert("Model 3 Failed", f"Risk engine error: {e}")
        return

    # --- Generate Charts ---
    try:
        chart_paths = generate_all_charts(m1, m2)
        logger.info(f"Charts generated: {len(chart_paths)} files")
    except Exception as e:
        logger.warning(f"Chart generation partially failed: {e}")
        chart_paths = {}

    # --- Format Report ---
    report = format_daily_report(m1, m2, m3)

    # --- Save report locally ---
    paths.ensure()
    report_path = paths.output / f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"Report saved: {report_path}")

    # --- Print to console ---
    print("\n" + report + "\n")

    # --- Send Telegram ---
    if send_telegram:
        success = send_telegram_message(report)
        if success:
            logger.info("Telegram report sent successfully")
        else:
            logger.warning("Telegram report not sent (check config)")

        # Send charts as images
        for chart_name, chart_path in chart_paths.items():
            if chart_path.suffix == ".png":
                send_chart_image(chart_path, caption=chart_name)

        # Send alert for high-priority signals
        if m3.btc_signal.alert_worthy:
            send_alert(
                f"BTC: {m3.btc_signal.decision.value}",
                f"Signal Strength: {m3.btc_signal.signal_strength:.0f}/100\n"
                f"Confidence: {m3.btc_signal.confidence:.0f}%\n"
                f"Asymmetry: {m3.btc_signal.asymmetry_ratio:.2f}x\n"
                f"{m3.btc_signal.reasoning}",
            )

    elapsed = time.time() - start_time
    logger.info(f"Pipeline complete in {elapsed:.1f}s")


def run_model_only(
    model: str,
    ticker: Optional[str] = None,
) -> None:
    """Run a single model for debugging/testing."""
    if model == "volatility":
        result = run_model1()
        print(f"\nDay Type: {result.day_type}")
        print(f"Spot: ${result.spot:,.2f}")
        print(f"Regime: {result.regime.regime.value}")
        print(f"Expected Move: ±${result.regime.expected_move_1d:,.0f}")
        print(f"Best Strike: ${result.best_strike:,.0f}")
        print(f"Straddle Cost: ${result.straddle.straddle_cost:,.2f}")
        print(f"P(Profit): {result.straddle.prob_of_profit:.1%}")
        print(f"R/R: {result.straddle.risk_reward_ratio:.2f}x")
        print(f"\n{result.recommendation}")

    elif model == "trend":
        result = run_model2(custom_ticker=ticker)
        for row in result.summary:
            if row["timeframe"] == "1h":
                print(
                    f"  {row['name']}: {row['state']} | "
                    f"Strength={row['strength']:.0f} | "
                    f"Persistence={row['persistence']:.0%}"
                )

    elif model == "risk":
        m1 = run_model1()
        m2 = run_model2(custom_ticker=ticker)
        m3 = integrate_signals(m1, m2)
        print(f"\nBTC Decision: {m3.btc_signal.decision.value}")
        print(m3.summary)

    else:
        print(f"Unknown model: {model}. Use: volatility, trend, risk, all")
