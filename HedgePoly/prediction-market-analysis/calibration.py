"""
calibration.py — Calibration Surface Analysis Across Price and Time Dimensions.

Builds C(p, t) calibration function:
  - p = contract price (0 to 100 cents)
  - t = time remaining until resolution (days)
  - C(p, t) = empirical probability of outcome occurring

Detects:
  - Longshot bias (1¢ contracts winning only 0.43% vs 1% implied)
  - Time-varying calibration shifts
  - Mispricing surfaces M(p, t) = C(p, t) - p/100
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

from utils import Config, DataLoader, log, format_pct, safe_div


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class CalibrationBin:
    """Single bin in the calibration surface."""
    price_bin_center: float  # e.g., 0.05 for 5¢
    time_bin_center: float   # e.g., 15.0 for 15 days remaining
    implied_prob: float      # = price
    empirical_prob: float    # actual win rate
    mispricing: float        # empirical - implied (positive = underpriced)
    n_trades: int
    total_volume: float


@dataclass
class CalibrationSurface:
    """Complete calibration analysis results."""
    bins: list  # list of CalibrationBin
    price_bins: np.ndarray
    time_bins: np.ndarray
    surface_matrix: np.ndarray  # shape (n_price_bins, n_time_bins) of mispricing
    count_matrix: np.ndarray    # shape (n_price_bins, n_time_bins) of sample sizes

    # Aggregate metrics
    overall_longshot_bias: float  # avg mispricing at p < 0.10
    overall_favorite_bias: float  # avg mispricing at p > 0.90
    worst_mispricing_bin: Optional[CalibrationBin] = None
    best_opportunity_bin: Optional[CalibrationBin] = None

    def summary(self) -> str:
        """Human-readable summary for Telegram/dashboard."""
        lines = [
            f"📐 CALIBRATION SURFACE ANALYSIS",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Grid: {len(self.price_bins)} price bins × {len(self.time_bins)} time bins",
            f"Total bins analyzed: {len(self.bins):,}",
            f"",
            f"🎰 Longshot Bias (p < 10¢):",
            f"   Avg mispricing: {self.overall_longshot_bias:+.2f}%",
            f"   (negative = overpriced = retail overpays)",
            f"",
            f"⭐ Favorite Bias (p > 90¢):",
            f"   Avg mispricing: {self.overall_favorite_bias:+.2f}%",
            f"",
        ]

        if self.worst_mispricing_bin:
            b = self.worst_mispricing_bin
            lines.extend([
                f"🔴 Worst Mispricing:",
                f"   Price: {b.price_bin_center*100:.0f}¢ | "
                f"Time: {b.time_bin_center:.0f}d remaining",
                f"   Implied: {format_pct(b.implied_prob)} | "
                f"Actual: {format_pct(b.empirical_prob)}",
                f"   Mispricing: {b.mispricing:+.2f}% ({b.n_trades:,} trades)",
                f"",
            ])

        if self.best_opportunity_bin:
            b = self.best_opportunity_bin
            lines.extend([
                f"🟢 Best Opportunity (underpriced):",
                f"   Price: {b.price_bin_center*100:.0f}¢ | "
                f"Time: {b.time_bin_center:.0f}d remaining",
                f"   Implied: {format_pct(b.implied_prob)} | "
                f"Actual: {format_pct(b.empirical_prob)}",
                f"   Mispricing: {b.mispricing:+.2f}% ({b.n_trades:,} trades)",
            ])

        return "\n".join(lines)

    def get_signal(self, price: float, days_remaining: float, threshold: float = 5.0) -> str:
        """
        Get trading signal for a specific price and time.
        Returns: 'LONG', 'SHORT', or 'FLAT'
        """
        # Find nearest bins
        p_idx = np.argmin(np.abs(self.price_bins - price))
        t_idx = np.argmin(np.abs(self.time_bins - days_remaining))

        mispricing = self.surface_matrix[p_idx, t_idx]
        count = self.count_matrix[p_idx, t_idx]

        if count < 30:
            return "FLAT (insufficient data)"

        if mispricing > threshold:
            return f"LONG (+{mispricing:.1f}% underpriced, n={int(count)})"
        elif mispricing < -threshold:
            return f"SHORT ({mispricing:.1f}% overpriced, n={int(count)})"
        else:
            return f"FLAT ({mispricing:+.1f}%, within threshold)"


# ── Core Engine ──────────────────────────────────────────────────────────────

class CalibrationEngine:
    """
    Builds calibration surface C(p, t) from historical trades.
    """

    def __init__(self, cfg: Config, loader: DataLoader):
        self.cfg = cfg
        self.loader = loader

    def _load_resolved_trades(self, platform: str = "polymarket") -> pd.DataFrame:
        """Load trades joined with market outcomes and timing info."""
        if platform == "polymarket":
            sql_poly = """
            WITH resolved_markets AS (
                SELECT
                    json_extract_string(clob_token_ids, '$[0]') AS token0,
                    json_extract_string(clob_token_ids, '$[1]') AS token1,
                    end_date AS close_time,
                    CASE
                        WHEN CAST(json_extract_string(outcome_prices, '$[0]') AS DOUBLE) > 0.99
                         AND CAST(json_extract_string(outcome_prices, '$[1]') AS DOUBLE) < 0.01 THEN 0
                        WHEN CAST(json_extract_string(outcome_prices, '$[0]') AS DOUBLE) < 0.01
                         AND CAST(json_extract_string(outcome_prices, '$[1]') AS DOUBLE) > 0.99 THEN 1
                        ELSE NULL
                    END AS winning_outcome
                FROM polymarket_markets
                WHERE closed = true
            ),
            token_resolution AS (
                SELECT token0 AS token_id, true AS won, close_time
                FROM resolved_markets
                WHERE winning_outcome = 0 AND token0 IS NOT NULL
                UNION ALL
                SELECT token1 AS token_id, false AS won, close_time
                FROM resolved_markets
                WHERE winning_outcome = 0 AND token1 IS NOT NULL
                UNION ALL
                SELECT token0 AS token_id, false AS won, close_time
                FROM resolved_markets
                WHERE winning_outcome = 1 AND token0 IS NOT NULL
                UNION ALL
                SELECT token1 AS token_id, true AS won, close_time
                FROM resolved_markets
                WHERE winning_outcome = 1 AND token1 IS NOT NULL
            )
            SELECT
                CASE
                    WHEN t.maker_asset_id = '0'
                        THEN CAST(t.maker_amount AS DOUBLE) / NULLIF(CAST(t.taker_amount AS DOUBLE), 0)
                    ELSE CAST(t.taker_amount AS DOUBLE) / NULLIF(CAST(t.maker_amount AS DOUBLE), 0)
                END AS price,
                CASE
                    WHEN t.maker_asset_id = '0' THEN CAST(t.maker_amount AS DOUBLE) / 1e6
                    ELSE CAST(t.taker_amount AS DOUBLE) / 1e6
                END AS volume,
                t._fetched_at AS trade_time,
                tr.close_time,
                tr.won AS market_result
            FROM polymarket_trades t
            INNER JOIN token_resolution tr
                ON (CASE WHEN t.maker_asset_id = '0' THEN t.taker_asset_id ELSE t.maker_asset_id END) = tr.token_id
            WHERE t.taker_amount > 0
              AND t.maker_amount > 0
            """
            df = self.loader.query(sql_poly)
        else:
            sql = f"""
            SELECT
                t.price,
                t.volume,
                t.timestamp AS trade_time,
                m.close_time,
                m.result AS market_result,
                m.title
            FROM {platform}_trades t
            JOIN {platform}_markets m ON t.market_id = m.id
            WHERE m.status = 'resolved'
              AND t.price > 0
              AND t.price < 1.0
            """
            try:
                df = self.loader.query(sql)
            except Exception:
                sql_alt = f"""
                SELECT
                    t.price,
                    t.amount AS volume,
                    t.side AS taker_side,
                    t.timestamp AS trade_time,
                    m.end_date AS close_time,
                    m.outcome AS market_result,
                    m.title
                FROM {platform}_trades t
                JOIN {platform}_markets m ON t.market_id = m.id
                WHERE m.outcome IS NOT NULL
                  AND t.price > 0
                  AND t.price < 1.0
                """
                df = self.loader.query(sql_alt)

        if df.empty:
            return df

        # Parse outcome
        df["won"] = df["market_result"].astype(str).str.lower().isin(
            ["yes", "1", "true", "y"]
        )

        # Calculate days to resolution — convert both to UTC then strip tz-info
        # so subtraction always works (avoids tz-naive vs tz-aware errors)
        df["trade_time"] = (
            pd.to_datetime(df["trade_time"], errors="coerce", utc=True).dt.tz_convert(None)
        )
        df["close_time"] = (
            pd.to_datetime(df["close_time"], errors="coerce", utc=True).dt.tz_convert(None)
        )
        df["days_to_resolution"] = (
            (df["close_time"] - df["trade_time"]).dt.total_seconds() / 86400
        ).clip(lower=0)

        log.info(f"Loaded {len(df):,} resolved trades for calibration")
        return df

    def build_1d_calibration(self, platform: str = "polymarket") -> pd.DataFrame:
        """
        Build 1D calibration: price → empirical win rate.
        This replicates Becker's core finding.
        """
        df = self._load_resolved_trades(platform)
        if df.empty:
            return pd.DataFrame()

        # Bin by price (1-cent resolution)
        df["price_cent"] = (df["price"] * 100).round().astype(int).clip(1, 99)

        cal = df.groupby("price_cent").agg(
            n_trades=("won", "count"),
            empirical_prob=("won", "mean"),
            total_volume=("volume", "sum"),
        ).reset_index()

        cal["implied_prob"] = cal["price_cent"] / 100.0
        cal["mispricing_pct"] = (cal["empirical_prob"] - cal["implied_prob"]) * 100
        cal["taker_excess_return"] = cal["mispricing_pct"]  # for taker perspective

        log.info(
            f"1D Calibration: {len(cal)} price levels, "
            f"longshot bias at 1¢ = {cal[cal['price_cent']==1]['mispricing_pct'].values}"
        )
        return cal

    def build_surface(self, platform: str = "polymarket") -> CalibrationSurface:
        """
        Build 2D calibration surface C(p, t).
        This extends Becker's analysis with the time dimension.
        """
        df = self._load_resolved_trades(platform)
        if df.empty:
            log.warning("No data for surface construction")
            return self._empty_surface()

        # Define bins
        n_price = self.cfg.price_bins
        n_time = self.cfg.time_bins

        price_edges = np.linspace(0, 1, n_price + 1)
        price_centers = (price_edges[:-1] + price_edges[1:]) / 2

        # Time bins: use quantiles for better coverage
        valid_times = df["days_to_resolution"].dropna()
        if len(valid_times) == 0:
            return self._empty_surface()

        time_edges = np.quantile(valid_times, np.linspace(0, 1, n_time + 1))
        time_edges = np.unique(time_edges)  # remove duplicates
        n_time = len(time_edges) - 1
        time_centers = (time_edges[:-1] + time_edges[1:]) / 2

        # Assign bins
        df["p_bin"] = np.digitize(df["price"], price_edges) - 1
        df["t_bin"] = np.digitize(df["days_to_resolution"], time_edges) - 1

        # Clip to valid range
        df["p_bin"] = df["p_bin"].clip(0, n_price - 1)
        df["t_bin"] = df["t_bin"].clip(0, n_time - 1)

        # Build matrices
        surface = np.full((n_price, n_time), np.nan)
        counts = np.zeros((n_price, n_time))
        all_bins = []

        for pi in range(n_price):
            for ti in range(n_time):
                mask = (df["p_bin"] == pi) & (df["t_bin"] == ti)
                subset = df[mask]

                if len(subset) < self.cfg.min_sample_size:
                    continue

                emp_prob = subset["won"].mean()
                impl_prob = price_centers[pi]
                mispricing = (emp_prob - impl_prob) * 100  # percentage points

                surface[pi, ti] = mispricing
                counts[pi, ti] = len(subset)

                b = CalibrationBin(
                    price_bin_center=price_centers[pi],
                    time_bin_center=time_centers[ti],
                    implied_prob=impl_prob,
                    empirical_prob=emp_prob,
                    mispricing=mispricing,
                    n_trades=len(subset),
                    total_volume=subset["volume"].sum() if "volume" in subset else 0,
                )
                all_bins.append(b)

        # Aggregate metrics
        longshot_bins = [b for b in all_bins if b.price_bin_center < 0.10]
        favorite_bins = [b for b in all_bins if b.price_bin_center > 0.90]

        longshot_bias = np.mean([b.mispricing for b in longshot_bins]) if longshot_bins else 0
        favorite_bias = np.mean([b.mispricing for b in favorite_bins]) if favorite_bins else 0

        # Find extreme bins
        valid_bins = [b for b in all_bins if b.n_trades >= self.cfg.min_sample_size]
        worst = min(valid_bins, key=lambda b: b.mispricing) if valid_bins else None
        best = max(valid_bins, key=lambda b: b.mispricing) if valid_bins else None

        result = CalibrationSurface(
            bins=all_bins,
            price_bins=price_centers,
            time_bins=time_centers,
            surface_matrix=surface,
            count_matrix=counts,
            overall_longshot_bias=longshot_bias,
            overall_favorite_bias=favorite_bias,
            worst_mispricing_bin=worst,
            best_opportunity_bin=best,
        )

        log.info(
            f"Surface built: {n_price}×{n_time} grid, "
            f"{len(all_bins)} bins with data, "
            f"longshot bias={longshot_bias:+.2f}%"
        )
        return result

    def _empty_surface(self) -> CalibrationSurface:
        return CalibrationSurface(
            bins=[],
            price_bins=np.array([]),
            time_bins=np.array([]),
            surface_matrix=np.array([[]]),
            count_matrix=np.array([[]]),
            overall_longshot_bias=0,
            overall_favorite_bias=0,
        )

    def get_longshot_opportunities(
        self, platform: str = "polymarket", top_n: int = 10
    ) -> list[CalibrationBin]:
        """Find the top underpriced bins (biggest positive mispricing)."""
        surface = self.build_surface(platform)
        valid = [b for b in surface.bins if b.n_trades >= self.cfg.min_sample_size]
        # Sort by mispricing descending (most underpriced first)
        valid.sort(key=lambda b: b.mispricing, reverse=True)
        return valid[:top_n]


# ── Standalone ───────────────────────────────────────────────────────────────

def run_calibration_analysis(cfg: Config, loader: DataLoader) -> CalibrationSurface:
    """Run full calibration analysis and print results."""
    engine = CalibrationEngine(cfg, loader)

    # 1D calibration (Becker replication)
    log.info("Building 1D calibration curve...")
    cal_1d = engine.build_1d_calibration("polymarket")

    # 2D surface
    log.info("\nBuilding 2D calibration surface...")
    surface = engine.build_surface("polymarket")

    # Top opportunities
    log.info("\nFinding top longshot opportunities...")
    opps = engine.get_longshot_opportunities("polymarket", top_n=5)

    return surface


if __name__ == "__main__":
    import sys
    from utils import load_config
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/config.toml"
    cfg = load_config(config_path)
    loader = DataLoader(cfg)
    run_calibration_analysis(cfg, loader)
