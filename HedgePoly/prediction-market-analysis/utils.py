"""
utils.py — Shared utilities for the Prediction Market Alpha Engine.
Handles config parsing, data loading via DuckDB, and common helpers.
"""

import os
import sys
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import duckdb
import pandas as pd
import numpy as np

# ── Logging ──────────────────────────────────────────────────────────────────
# Fix emoji encoding errors on Windows by forcing UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("alpha")


# ── Configuration ────────────────────────────────────────────────────────────
@dataclass
class Config:
    # Paths
    data_dir: str = "./data"
    output_dir: str = "./output"

    # Telegram
    bot_token: str = ""
    allowed_chat_ids: list = field(default_factory=list)
    report_interval_hours: int = 24

    # Analysis
    monte_carlo_runs: int = 10_000
    drawdown_confidence: float = 0.95
    min_sample_size: int = 30
    kelly_fraction: float = 0.5

    # Calibration
    price_bins: int = 20
    time_bins: int = 10
    signal_threshold: float = 5.0

    # Orderflow
    min_market_volume: int = 100
    large_order_percentile: float = 95

    # Dashboard
    host: str = "0.0.0.0"
    port: int = 5050
    debug: bool = False


def load_config(path: str = "config/config.toml") -> Config:
    """Load configuration from TOML file."""
    cfg = Config()
    p = Path(path)
    if not p.exists():
        log.warning(f"Config file not found at {path}, using defaults.")
        return cfg

    with open(p, "rb") as f:
        raw = tomllib.load(f)

    # Paths
    paths = raw.get("paths", {})
    cfg.data_dir = paths.get("data_dir", cfg.data_dir)
    cfg.output_dir = paths.get("output_dir", cfg.output_dir)

    # Telegram
    tg = raw.get("telegram", {})
    cfg.bot_token = tg.get("bot_token", cfg.bot_token)
    ids_str = tg.get("allowed_chat_ids", "")
    cfg.allowed_chat_ids = [int(x.strip()) for x in ids_str.split(",") if x.strip()]
    cfg.report_interval_hours = tg.get("report_interval_hours", cfg.report_interval_hours)

    # Analysis
    an = raw.get("analysis", {})
    cfg.monte_carlo_runs = an.get("monte_carlo_runs", cfg.monte_carlo_runs)
    cfg.drawdown_confidence = an.get("drawdown_confidence", cfg.drawdown_confidence)
    cfg.min_sample_size = an.get("min_sample_size", cfg.min_sample_size)
    cfg.kelly_fraction = an.get("kelly_fraction", cfg.kelly_fraction)

    # Calibration
    cal = raw.get("calibration", {})
    cfg.price_bins = cal.get("price_bins", cfg.price_bins)
    cfg.time_bins = cal.get("time_bins", cfg.time_bins)
    cfg.signal_threshold = cal.get("signal_threshold", cfg.signal_threshold)

    # Orderflow
    of = raw.get("orderflow", {})
    cfg.min_market_volume = of.get("min_market_volume", cfg.min_market_volume)
    cfg.large_order_percentile = of.get("large_order_percentile", cfg.large_order_percentile)

    # Dashboard
    dash = raw.get("dashboard", {})
    cfg.host = dash.get("host", cfg.host)
    cfg.port = dash.get("port", cfg.port)
    cfg.debug = dash.get("debug", cfg.debug)

    os.makedirs(cfg.output_dir, exist_ok=True)
    log.info(f"Config loaded from {path}")
    return cfg


# ── DuckDB Data Loading ─────────────────────────────────────────────────────
class DataLoader:
    """
    Loads Parquet trade/market data via DuckDB for fast columnar queries.
    Handles both Polymarket and Kalshi datasets.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.con = duckdb.connect(":memory:")
        self._register_views()

    def _register_views(self):
        """Register Parquet files as DuckDB views for fast querying."""
        for platform in ["polymarket", "kalshi"]:
            trades_path = Path(self.cfg.data_dir) / platform / "trades" / "*.parquet"
            markets_path = Path(self.cfg.data_dir) / platform / "markets" / "*.parquet"

            if Path(self.cfg.data_dir, platform, "trades").exists():
                self.con.execute(f"""
                    CREATE OR REPLACE VIEW {platform}_trades AS
                    SELECT * FROM read_parquet('{trades_path}')
                """)
                log.info(f"Registered {platform}_trades view")

            if Path(self.cfg.data_dir, platform, "markets").exists():
                self.con.execute(f"""
                    CREATE OR REPLACE VIEW {platform}_markets AS
                    SELECT * FROM read_parquet('{markets_path}')
                """)
                log.info(f"Registered {platform}_markets view")

    def query(self, sql: str) -> pd.DataFrame:
        """Execute SQL and return a Pandas DataFrame."""
        return self.con.execute(sql).fetchdf()

    def query_arrow(self, sql: str):
        """Execute SQL and return an Arrow table (for large results)."""
        return self.con.execute(sql).fetch_arrow_table()

    def get_trade_count(self, platform: str = "polymarket") -> int:
        """Get total trade count for a platform."""
        try:
            result = self.con.execute(
                f"SELECT COUNT(*) AS cnt FROM {platform}_trades"
            ).fetchone()
            return result[0] if result else 0
        except Exception:
            return 0

    def get_trades_with_resolution(self, platform: str = "polymarket") -> pd.DataFrame:
        """
        Join trades with market resolution outcomes.
        This is the core dataset for calibration and orderflow analysis.
        """
        sql = f"""
        SELECT
            t.*,
            m.title,
            m.status,
            m.close_time,
            m.result AS market_result
        FROM {platform}_trades t
        JOIN {platform}_markets m ON t.market_id = m.id
        WHERE m.status = 'resolved'
        """
        return self.query(sql)

    def get_price_distribution(self, platform: str = "polymarket") -> pd.DataFrame:
        """Get trade count distribution across price levels."""
        sql = f"""
        SELECT
            ROUND(price * 100) AS price_cent,
            COUNT(*) AS trade_count,
            SUM(volume) AS total_volume
        FROM {platform}_trades
        GROUP BY price_cent
        ORDER BY price_cent
        """
        return self.query(sql)

    def get_schema(self, platform: str = "polymarket", table: str = "trades"):
        """Inspect the schema of a registered view."""
        try:
            return self.con.execute(
                f"DESCRIBE {platform}_{table}"
            ).fetchdf()
        except Exception as e:
            log.warning(f"Could not describe {platform}_{table}: {e}")
            return pd.DataFrame()


# ── Helpers ──────────────────────────────────────────────────────────────────

def format_pct(value: float, decimals: int = 2) -> str:
    """Format a decimal as a percentage string."""
    return f"{value * 100:.{decimals}f}%"


def format_currency(value: float) -> str:
    """Format as USD."""
    return f"${value:,.2f}"


def safe_div(a, b, default=0.0):
    """Safe division, returns default if denominator is zero."""
    return a / b if b != 0 else default


def percentile_label(p: float) -> str:
    """Convert 0.95 → '95th percentile'."""
    return f"{int(p * 100)}th percentile"
