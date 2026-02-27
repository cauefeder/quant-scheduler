#!/usr/bin/env python3
"""
Quant Desk — Local Quantitative Trading Analysis System
========================================================

Entry point. Run models, generate reports, send Telegram.

Usage:
    python main.py                     # Full pipeline
    python main.py --model volatility  # BTC vol model only
    python main.py --model trend       # Trend model only
    python main.py --model trend --ticker AAPL  # Custom ticker
    python main.py --model risk        # Full pipeline with risk
    python main.py --no-telegram       # Skip Telegram send
    python main.py --capital 50000     # Set capital size
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import paths


def setup_logging() -> None:
    """Configure logging to both file and console."""
    paths.ensure()

    log_file = paths.logs / f"quant_desk_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Suppress noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quant Desk — Local Quantitative Trading Analysis System"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="all",
        choices=["all", "volatility", "trend", "risk"],
        help="Which model to run (default: all)",
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Custom ticker to add to trend scan (Yahoo Finance symbol)",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Skip sending Telegram messages",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=10000.0,
        help="Trading capital in USD (default: 10000)",
    )
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()

    logger = logging.getLogger("quant_desk")
    logger.info(f"Starting Quant Desk — model={args.model}, ticker={args.ticker}")

    from scheduler.runner import run_full_pipeline, run_model_only

    if args.model == "all":
        run_full_pipeline(
            custom_ticker=args.ticker,
            capital=args.capital,
            send_telegram=not args.no_telegram,
        )
    else:
        run_model_only(model=args.model, ticker=args.ticker)

    logger.info("Done.")


if __name__ == "__main__":
    main()
