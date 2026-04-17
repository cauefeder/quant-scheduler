"""
run_report.py — Headless runner for the Global Macro Quant Report.

Executes the full four-layer signal stack and sends the result to Telegram.
Designed to be called by the project scheduler (no Jupyter required):

    uv run python notebooks/run_report.py

Output: printed summary table + Telegram HTML message to all chat IDs in config.toml.
"""
from __future__ import annotations

import sys
import warnings
from datetime import date
from pathlib import Path

import httpx

# ── Paths ──────────────────────────────────────────────────────────────────────
NOTEBOOK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = NOTEBOOK_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))   # for utils.py
sys.path.insert(0, str(NOTEBOOK_DIR))   # for quant_helpers.py

from utils import load_config
import quant_helpers as qh

warnings.filterwarnings("ignore")

# ── Config ─────────────────────────────────────────────────────────────────────
cfg   = load_config(str(PROJECT_ROOT / "config.toml"))
TODAY = date.today().isoformat()


def main() -> None:
    print(f"[Global Macro Quant] Starting report for {TODAY}")

    # ── Layer 0: Data ──────────────────────────────────────────────────────────
    print("  Downloading market data (5y daily)...")
    data = qh.load_all_assets(period="5y")

    # ── Layer 1: Features ──────────────────────────────────────────────────────
    print("  Computing features...")
    for ticker in data:
        data[ticker] = qh.add_features(data[ticker], ticker)

    # ── Layer 2: Regime detection ──────────────────────────────────────────────
    print("  Classifying regimes...")
    for ticker in data:
        data[ticker]["regime"] = qh.classify_regimes_series(data[ticker])

    # ── Layer 3: HAR-OLS vol forecast ──────────────────────────────────────────
    print("  Fitting HAR-OLS models...")
    for ticker in data:
        data[ticker], _ = qh.fit_har_model(data[ticker], return_metrics=True)

    # Snapshot regime/vol_regime BEFORE XGBoost truncates the DataFrame by 20 rows
    regime_snapshots     = {t: data[t]["regime"].dropna().iloc[-1]     for t in data}
    vol_regime_snapshots = {t: data[t]["vol_regime"].dropna().iloc[-1] for t in data}

    # ── Layer 4: XGBoost direction signal ──────────────────────────────────────
    print("  Fitting XGBoost direction models...")
    for ticker in data:
        data[ticker], _ = qh.fit_xgb_signal(data[ticker], return_meta=True)

    # ── Layer 5: Half-Kelly position sizing ────────────────────────────────────
    sizing = {}
    for ticker in data:
        last = data[ticker][["p_bearish", "p_neutral", "p_bullish"]].dropna().iloc[-1]
        sizing[ticker] = qh.compute_kelly(last.p_bullish, last.p_neutral, last.p_bearish)

    # ── Report ─────────────────────────────────────────────────────────────────
    signals = []
    for ticker in data:
        sz  = sizing[ticker]
        row = qh.build_summary_row(
            ticker     = ticker,
            regime     = regime_snapshots[ticker],
            vol_regime = vol_regime_snapshots[ticker],
            side       = sz["side"],
            p_win      = sz["p_win"],
            kelly_pct  = sz["kelly_pct"],
        )
        signals.append(row)

    print(qh.build_summary_table_text(signals, date_str=TODAY))

    # ── Telegram send ──────────────────────────────────────────────────────────
    html_report = qh.build_telegram_html(signals, date_str=TODAY)

    if not (cfg.bot_token and cfg.allowed_chat_ids):
        print("  [SKIP] Telegram not configured.")
        return

    url    = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    chunks: list[str] = []
    current = ""
    for line in html_report.split("\n"):
        if len(current) + len(line) + 1 > 4000:
            if current:
                chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        chunks.append(current)

    with httpx.Client(timeout=15.0) as client:
        for chat_id in cfg.allowed_chat_ids:
            for i, chunk in enumerate(chunks, 1):
                resp = client.post(url, json={
                    "chat_id":                  chat_id,
                    "text":                     chunk,
                    "parse_mode":               "HTML",
                    "disable_web_page_preview": True,
                })
                status = "OK" if resp.status_code == 200 else f"ERROR {resp.status_code}"
                print(f"  Telegram chat_id={chat_id} chunk={i}/{len(chunks)} -> {status}")

    print(f"[Global Macro Quant] Done.")


if __name__ == "__main__":
    main()
