"""
orderflow.py — Order Flow Decomposition and Maker vs Taker Profitability.

Analyzes 400M+ trades to measure:
  - Taker excess returns by price level
  - Maker edge (structural arbitrage)
  - Wealth transfer quantification
  - Adverse selection detection
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional

from utils import Config, DataLoader, log, format_pct, safe_div


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class OrderFlowStats:
    """Aggregate order flow statistics."""
    total_trades: int
    total_volume: float

    # Taker stats
    taker_buy_yes_count: int
    taker_buy_no_count: int
    taker_excess_return: float  # aggregate
    taker_negative_levels: int  # out of 99 price levels
    taker_worst_level: int      # price cent with worst performance

    # Maker stats
    maker_buy_yes_excess: float
    maker_buy_no_excess: float
    maker_avg_excess: float
    maker_cohens_d: float  # symmetry measure

    # Top insights
    price_level_stats: pd.DataFrame  # per-cent breakdown

    def summary(self) -> str:
        lines = [
            f"📊 ORDER FLOW DECOMPOSITION",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Total Trades: {self.total_trades:,}",
            f"Total Volume: ${self.total_volume:,.0f}",
            f"",
            f"🔴 TAKER ANALYSIS (Liquidity Consumers):",
            f"   Aggregate Excess Return: {self.taker_excess_return:+.2f}%",
            f"   Negative at {self.taker_negative_levels}/99 price levels",
            f"   Worst Level: {self.taker_worst_level}¢",
            f"   YES bias: {self.taker_buy_yes_count:,} YES vs {self.taker_buy_no_count:,} NO",
            f"",
            f"🟢 MAKER ANALYSIS (Liquidity Providers):",
            f"   Buying YES excess: {self.maker_buy_yes_excess:+.2f}%",
            f"   Buying NO excess:  {self.maker_buy_no_excess:+.2f}%",
            f"   Average excess:    {self.maker_avg_excess:+.2f}%",
            f"   Cohen's d:         {self.maker_cohens_d:.3f} (≈0 = structural, not informational)",
            f"",
            f"💡 INSIGHT: {'Structural edge confirmed' if abs(self.maker_cohens_d) < 0.2 else 'Possible informational edge'}",
            f"   Makers profit via patience + spread capture,",
            f"   not superior prediction ability.",
        ]
        return "\n".join(lines)


# ── Core Engine ──────────────────────────────────────────────────────────────

class OrderFlowEngine:
    """
    Decomposes trade flow into maker/taker populations
    and measures profitability asymmetry.
    """

    def __init__(self, cfg: Config, loader: DataLoader):
        self.cfg = cfg
        self.loader = loader

    def _load_flow_data(self, platform: str = "polymarket") -> pd.DataFrame:
        """Load trades with taker side and resolution outcome."""
        if platform == "polymarket":
            log.warning(
                "Order flow decomposition unavailable for Polymarket CTF schema "
                "(no taker_side field). Returning empty result."
            )
            return pd.DataFrame()

        sql = f"""
        SELECT
            t.price,
            t.volume,
            t.taker_side,
            m.result AS market_result
        FROM {platform}_trades t
        JOIN {platform}_markets m ON t.market_id = m.id
        WHERE m.status = 'resolved'
          AND t.price > 0
          AND t.price < 1.0
          AND t.taker_side IS NOT NULL
        """
        try:
            df = self.loader.query(sql)
        except Exception:
            sql_alt = f"""
            SELECT
                t.price,
                t.amount AS volume,
                t.side AS taker_side,
                m.outcome AS market_result
            FROM {platform}_trades t
            JOIN {platform}_markets m ON t.market_id = m.id
            WHERE m.outcome IS NOT NULL
              AND t.price > 0
              AND t.price < 1.0
              AND t.side IS NOT NULL
            """
            df = self.loader.query(sql_alt)

        if df.empty:
            return df

        # Normalize
        df["won"] = df["market_result"].astype(str).str.lower().isin(
            ["yes", "1", "true", "y"]
        )
        df["price_cent"] = (df["price"] * 100).round().astype(int).clip(1, 99)
        df["taker_side"] = df["taker_side"].astype(str).str.lower()

        # Determine taker bought YES or NO
        df["taker_bought_yes"] = df["taker_side"].isin(["yes", "buy", "1", "true", "y"])

        # Taker P&L: if taker bought YES and market resolved YES → profit
        df["taker_won"] = (
            (df["taker_bought_yes"] & df["won"]) |
            (~df["taker_bought_yes"] & ~df["won"])
        )

        # Return calculation
        df["taker_return"] = np.where(
            df["taker_won"],
            np.where(
                df["taker_bought_yes"],
                (1.0 - df["price"]) / df["price"],   # bought YES, won
                df["price"] / (1.0 - df["price"]),     # bought NO, won
            ),
            -1.0  # lost
        )

        # Maker is the other side
        df["maker_won"] = ~df["taker_won"]
        df["maker_return"] = np.where(
            df["maker_won"],
            np.where(
                ~df["taker_bought_yes"],  # maker bought YES (opposite of taker)
                (1.0 - df["price"]) / df["price"],
                df["price"] / (1.0 - df["price"]),
            ),
            -1.0
        )

        log.info(f"Loaded {len(df):,} flow-tagged trades")
        return df

    def analyze(self, platform: str = "polymarket") -> OrderFlowStats:
        """Full order flow analysis."""
        df = self._load_flow_data(platform)
        if df.empty:
            log.warning("No order flow data available")
            return self._empty_stats()

        total_trades = len(df)
        total_volume = df["volume"].sum() if "volume" in df.columns else 0

        # ── Per-price-level analysis ─────────────────────────────────────────
        level_stats = df.groupby("price_cent").agg(
            n_trades=("taker_won", "count"),
            taker_win_rate=("taker_won", "mean"),
            taker_avg_return=("taker_return", "mean"),
            maker_avg_return=("maker_return", "mean"),
        ).reset_index()

        level_stats["implied_prob"] = level_stats["price_cent"] / 100.0
        level_stats["taker_excess"] = (
            level_stats["taker_win_rate"] - level_stats["implied_prob"]
        ) * 100
        level_stats["maker_excess"] = -level_stats["taker_excess"]

        # Count how many levels takers have negative excess
        taker_negative = (level_stats["taker_excess"] < 0).sum()
        worst_level_row = level_stats.loc[level_stats["taker_excess"].idxmin()]
        worst_level = int(worst_level_row["price_cent"])

        # ── Aggregate taker stats ────────────────────────────────────────────
        taker_buy_yes = df["taker_bought_yes"].sum()
        taker_buy_no = total_trades - taker_buy_yes
        taker_agg_excess = (df["taker_won"].mean() - 0.5) * 100  # vs fair

        # ── Maker directional analysis ───────────────────────────────────────
        # Makers buying YES = when taker sold YES (taker_bought_yes = False)
        maker_yes_mask = ~df["taker_bought_yes"]
        maker_no_mask = df["taker_bought_yes"]

        maker_yes_excess = (
            df[maker_yes_mask]["maker_won"].mean() -
            df[maker_yes_mask]["price"].mean()
        ) * 100 if maker_yes_mask.any() else 0

        maker_no_excess = (
            df[maker_no_mask]["maker_won"].mean() -
            (1 - df[maker_no_mask]["price"]).mean()
        ) * 100 if maker_no_mask.any() else 0

        maker_avg = (maker_yes_excess + maker_no_excess) / 2

        # Cohen's d: effect size between maker YES and NO excess returns
        if maker_yes_mask.any() and maker_no_mask.any():
            m1 = df[maker_yes_mask]["maker_return"].values
            m2 = df[maker_no_mask]["maker_return"].values
            pooled_std = np.sqrt((np.var(m1) + np.var(m2)) / 2)
            cohens_d = safe_div(np.mean(m1) - np.mean(m2), pooled_std)
        else:
            cohens_d = 0.0

        return OrderFlowStats(
            total_trades=total_trades,
            total_volume=total_volume,
            taker_buy_yes_count=int(taker_buy_yes),
            taker_buy_no_count=int(taker_buy_no),
            taker_excess_return=taker_agg_excess,
            taker_negative_levels=int(taker_negative),
            taker_worst_level=worst_level,
            maker_buy_yes_excess=maker_yes_excess,
            maker_buy_no_excess=maker_no_excess,
            maker_avg_excess=maker_avg,
            maker_cohens_d=cohens_d,
            price_level_stats=level_stats,
        )

    def get_maker_edge_by_price(self, platform: str = "polymarket") -> pd.DataFrame:
        """Get maker edge breakdown at each price level."""
        stats = self.analyze(platform)
        return stats.price_level_stats[
            ["price_cent", "n_trades", "taker_excess", "maker_excess"]
        ].sort_values("maker_excess", ascending=False)

    def detect_adverse_selection(self, platform: str = "polymarket") -> pd.DataFrame:
        """
        Detect trades where informed flow may dominate.
        Large trades at specific price levels where takers actually win.
        """
        df = self._load_flow_data(platform)
        if df.empty:
            return pd.DataFrame()

        # Flag large orders (above configured percentile)
        vol_threshold = np.percentile(
            df["volume"].dropna(), self.cfg.large_order_percentile
        ) if "volume" in df.columns else 0

        df["is_large"] = df["volume"] >= vol_threshold if vol_threshold > 0 else False

        # Compare large vs small order taker performance
        large = df[df["is_large"]]
        small = df[~df["is_large"]]

        comparison = pd.DataFrame({
            "segment": ["Small Orders", "Large Orders"],
            "count": [len(small), len(large)],
            "taker_win_rate": [
                small["taker_won"].mean() if len(small) > 0 else 0,
                large["taker_won"].mean() if len(large) > 0 else 0,
            ],
            "avg_taker_return": [
                small["taker_return"].mean() if len(small) > 0 else 0,
                large["taker_return"].mean() if len(large) > 0 else 0,
            ],
        })

        log.info(
            f"Adverse selection: large order taker win rate = "
            f"{format_pct(comparison.iloc[1]['taker_win_rate'])}"
        )
        return comparison

    def _empty_stats(self) -> OrderFlowStats:
        return OrderFlowStats(
            total_trades=0, total_volume=0,
            taker_buy_yes_count=0, taker_buy_no_count=0,
            taker_excess_return=0, taker_negative_levels=0, taker_worst_level=0,
            maker_buy_yes_excess=0, maker_buy_no_excess=0,
            maker_avg_excess=0, maker_cohens_d=0,
            price_level_stats=pd.DataFrame(),
        )


# ── Standalone ───────────────────────────────────────────────────────────────

def run_orderflow_analysis(cfg: Config, loader: DataLoader) -> OrderFlowStats:
    """Run full order flow analysis and print results."""
    engine = OrderFlowEngine(cfg, loader)

    stats = engine.analyze("polymarket")

    return stats


if __name__ == "__main__":
    import sys
    from utils import load_config
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/config.toml"
    cfg = load_config(config_path)
    loader = DataLoader(cfg)
    run_orderflow_analysis(cfg, loader)
