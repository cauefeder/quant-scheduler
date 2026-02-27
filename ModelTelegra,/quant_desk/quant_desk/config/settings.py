"""
Central configuration for the Quant Desk system.
All tuneable parameters live here — no magic numbers in model code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from project root
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Paths:
    root: Path = _PROJECT_ROOT
    logs: Path = _PROJECT_ROOT / "logs"
    output: Path = _PROJECT_ROOT / "output"

    def ensure(self) -> None:
        self.logs.mkdir(exist_ok=True)
        self.output.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


# ---------------------------------------------------------------------------
# Model 1 — Volatility & GEX
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VolatilityConfig:
    # Realized vol lookback windows (in hours for 1H data)
    rv_windows: List[int] = field(default_factory=lambda: [24, 72, 168])  # 1D, 3D, 7D
    # Annualization factor for hourly data
    annualization_factor: float = 365.25 * 24
    # Strike generation range around spot (±%)
    strike_range_pct: float = 0.10
    # Strike step (USD)
    strike_step: int = 1000
    # Regime thresholds (IV percentile)
    vol_expansion_threshold: float = 0.70
    vol_compression_threshold: float = 0.30
    # Straddle horizon (days)
    straddle_horizon_days: float = 1.0
    # Risk-free rate proxy
    risk_free_rate: float = 0.045
    # GEX scaling
    contract_multiplier: float = 1.0  # BTC options = 1 BTC notional


# ---------------------------------------------------------------------------
# Model 2 — Trend Classification
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TrendConfig:
    # Default tickers to scan
    default_tickers: Dict[str, str] = field(default_factory=lambda: {
        "BTC-USD": "Bitcoin",
        "ETH-USD": "Ethereum",
        "BNB-USD": "BNB",
        "SOL-USD": "Solana",
        "SPY": "S&P 500 ETF",
        "QQQ": "Nasdaq 100 ETF",
        "GC=F": "Gold Futures",
        "SI=F": "Silver Futures",
        "CL=F": "Crude Oil Futures",
    })
    # Timeframes to fetch
    timeframes: Dict[str, Dict] = field(default_factory=lambda: {
        "1h": {"period": "60d", "interval": "1h"},
        "1d": {"period": "2y", "interval": "1d"},
        "1wk": {"period": "10y", "interval": "1wk"},
    })
    # Regime detection parameters
    fast_ema: int = 12
    slow_ema: int = 26
    signal_ema: int = 9
    atr_period: int = 14
    vol_lookback: int = 20
    # Trend strength thresholds
    strong_trend_threshold: float = 65.0
    weak_trend_threshold: float = 35.0
    # Minimum bars for regime persistence
    min_regime_bars: int = 3


# ---------------------------------------------------------------------------
# Model 3 — Risk Engine
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RiskConfig:
    # Signal thresholds to trigger alert
    min_signal_strength: float = 60.0
    min_asymmetry_ratio: float = 1.5
    max_risk_score: float = 70.0  # above this = too risky
    # Position sizing (fraction of capital)
    max_position_pct: float = 0.02  # 2% max risk per trade
    # Kelly criterion dampening factor
    kelly_fraction: float = 0.25  # quarter-Kelly
    # Risk of ruin parameters
    max_consecutive_losses: int = 10
    # Confidence thresholds
    high_confidence: float = 75.0
    medium_confidence: float = 50.0


# ---------------------------------------------------------------------------
# Singleton config
# ---------------------------------------------------------------------------
paths = Paths()
telegram = TelegramConfig()
volatility = VolatilityConfig()
trend = TrendConfig()
risk = RiskConfig()
