"""
kelly.py — Empirical Kelly Criterion with Monte Carlo Uncertainty Quantification.

Implements institutional position sizing:
  1. Historical pattern extraction from 400M+ trades
  2. Empirical return distribution construction
  3. Monte Carlo resampling (10,000 paths)
  4. Drawdown distribution analysis (50th/95th/99th percentiles)
  5. Uncertainty-adjusted position sizing
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional

from utils import Config, DataLoader, log, format_pct


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class KellyResult:
    """Output of an Empirical Kelly analysis."""
    # Inputs
    entry_price: float
    estimated_prob: float
    strategy_label: str

    # Standard Kelly
    textbook_kelly: float
    textbook_edge: float

    # Empirical metrics
    n_historical_analogs: int
    empirical_win_rate: float
    empirical_avg_return: float
    empirical_std_return: float
    cv_edge: float  # coefficient of variation

    # Monte Carlo results
    mc_median_drawdown: float
    mc_p95_drawdown: float
    mc_p99_drawdown: float
    mc_median_final_return: float

    # Final sizing
    empirical_kelly: float
    recommended_size: float  # after fraction multiplier

    def to_dict(self) -> dict:
        return {k: round(v, 4) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}

    def summary(self) -> str:
        """Human-readable summary for Telegram/dashboard."""
        lines = [
            f"📊 EMPIRICAL KELLY ANALYSIS",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Strategy: {self.strategy_label}",
            f"Entry Price: {self.entry_price:.2f}¢ | Est. Prob: {format_pct(self.estimated_prob)}",
            f"",
            f"🎯 Standard Kelly:     {format_pct(self.textbook_kelly)}",
            f"   Edge (textbook):    {format_pct(self.textbook_edge)}",
            f"",
            f"📈 Historical Analogs: {self.n_historical_analogs:,}",
            f"   Empirical Win Rate: {format_pct(self.empirical_win_rate)}",
            f"   Avg Return:         {format_pct(self.empirical_avg_return)}",
            f"   Std Return:         {format_pct(self.empirical_std_return)}",
            f"   CV (uncertainty):   {self.cv_edge:.3f}",
            f"",
            f"🎲 Monte Carlo ({self.n_historical_analogs:,} analogs × 10K paths):",
            f"   Median Drawdown:    {format_pct(self.mc_median_drawdown)}",
            f"   95th %ile Drawdown: {format_pct(self.mc_p95_drawdown)}",
            f"   99th %ile Drawdown: {format_pct(self.mc_p99_drawdown)}",
            f"   Median Final Return:{format_pct(self.mc_median_final_return)}",
            f"",
            f"✅ Empirical Kelly:    {format_pct(self.empirical_kelly)}",
            f"🎯 Recommended Size:   {format_pct(self.recommended_size)}",
            f"   (half-Kelly conservative deployment)",
        ]
        return "\n".join(lines)


# ── Core Engine ──────────────────────────────────────────────────────────────

class EmpiricalKellyEngine:
    """
    Implements the full Empirical Kelly pipeline:
    pattern extraction → return distribution → Monte Carlo → sizing.
    """

    def __init__(self, cfg: Config, loader: DataLoader):
        self.cfg = cfg
        self.loader = loader

    # ── Phase 1: Historical Pattern Extraction ───────────────────────────────
    def extract_historical_analogs(
        self,
        platform: str,
        max_price: float,
        min_estimated_prob: float,
    ) -> pd.DataFrame:
        """
        Find historical trades matching criteria:
        - Entry price below max_price
        - In markets that have resolved (so we know the outcome)

        Returns DataFrame with columns: price, market_result, return_pct
        """
        if platform == "polymarket":
            sql_poly = f"""
            WITH resolved_markets AS (
                SELECT
                    json_extract_string(clob_token_ids, '$[0]') AS token0,
                    json_extract_string(clob_token_ids, '$[1]') AS token1,
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
                SELECT token0 AS token_id, true AS won
                FROM resolved_markets
                WHERE winning_outcome = 0 AND token0 IS NOT NULL
                UNION ALL
                SELECT token1 AS token_id, false AS won
                FROM resolved_markets
                WHERE winning_outcome = 0 AND token1 IS NOT NULL
                UNION ALL
                SELECT token0 AS token_id, false AS won
                FROM resolved_markets
                WHERE winning_outcome = 1 AND token0 IS NOT NULL
                UNION ALL
                SELECT token1 AS token_id, true AS won
                FROM resolved_markets
                WHERE winning_outcome = 1 AND token1 IS NOT NULL
            ),
            trade_positions AS (
                SELECT
                    CASE
                        WHEN t.maker_asset_id = '0'
                            THEN CAST(t.maker_amount AS DOUBLE) / NULLIF(CAST(t.taker_amount AS DOUBLE), 0)
                        ELSE CAST(t.taker_amount AS DOUBLE) / NULLIF(CAST(t.maker_amount AS DOUBLE), 0)
                    END AS price,
                    tr.won AS market_result,
                    t._fetched_at AS timestamp
                FROM polymarket_trades t
                INNER JOIN token_resolution tr
                    ON (CASE WHEN t.maker_asset_id = '0' THEN t.taker_asset_id ELSE t.maker_asset_id END) = tr.token_id
                WHERE t.taker_amount > 0 AND t.maker_amount > 0
            )
            SELECT price, market_result, timestamp
            FROM trade_positions
            WHERE price <= {max_price} AND price > 0
            ORDER BY timestamp
            """
            df = self.loader.query(sql_poly)
        else:
            # Query trades at low prices in resolved markets
            # Adapt column names based on platform schema
            sql = f"""
            SELECT
                t.price,
                t.timestamp,
                m.result AS market_result
            FROM {platform}_trades t
            JOIN {platform}_markets m ON t.market_id = m.id
            WHERE m.status = 'resolved'
              AND t.price <= {max_price}
              AND t.price > 0
            ORDER BY t.timestamp
            """
            try:
                df = self.loader.query(sql)
            except Exception as e:
                log.warning(f"Query failed, attempting with alternative schema: {e}")
                # Fallback for different column naming conventions
                sql_alt = f"""
                SELECT
                    t.price,
                    t.timestamp,
                    m.outcome AS market_result
                FROM {platform}_trades t
                JOIN {platform}_markets m ON t.market_id = m.id
                WHERE m.outcome IS NOT NULL
                  AND t.price <= {max_price}
                  AND t.price > 0
                ORDER BY t.timestamp
                """
                df = self.loader.query(sql_alt)

        if df.empty:
            log.warning("No historical analogs found for criteria.")
            return df

        # Calculate returns: if market resolved YES and you bought YES at price p,
        # return = (1 - p) / p. If resolved NO, return = -1 (total loss).
        df["won"] = df["market_result"].astype(str).str.lower().isin(
            ["yes", "1", "true", "y"]
        )
        df["return_pct"] = np.where(
            df["won"],
            (1.0 - df["price"]) / df["price"],  # win: payout is $1
            -1.0  # loss: lose entire stake
        )
        log.info(
            f"Extracted {len(df):,} historical analogs "
            f"(win rate: {format_pct(df['won'].mean())})"
        )
        return df

    # ── Phase 2: Return Distribution ─────────────────────────────────────────
    def build_return_distribution(self, analogs: pd.DataFrame) -> dict:
        """
        Build empirical return distribution statistics.
        Returns dict with mean, std, skew, kurtosis, percentiles.
        """
        returns = analogs["return_pct"].values
        dist = {
            "mean": float(np.mean(returns)),
            "std": float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0,
            "skew": float(pd.Series(returns).skew()),
            "kurtosis": float(pd.Series(returns).kurtosis()),
            "min": float(np.min(returns)),
            "max": float(np.max(returns)),
            "p5": float(np.percentile(returns, 5)),
            "p25": float(np.percentile(returns, 25)),
            "p50": float(np.percentile(returns, 50)),
            "p75": float(np.percentile(returns, 75)),
            "p95": float(np.percentile(returns, 95)),
            "n": len(returns),
            "win_rate": float(np.mean(returns > 0)),
            "returns": returns,
        }
        log.info(
            f"Return distribution: mean={format_pct(dist['mean'])}, "
            f"std={format_pct(dist['std'])}, skew={dist['skew']:.2f}, "
            f"kurtosis={dist['kurtosis']:.2f}"
        )
        return dist

    # ── Phase 3: Monte Carlo Resampling ──────────────────────────────────────
    def monte_carlo_resample(
        self,
        returns: np.ndarray,
        n_simulations: int = 10_000,
        path_length: Optional[int] = None,
    ) -> dict:
        """
        Generate n_simulations alternative equity paths by randomly
        reordering the same historical returns.

        Returns dict with drawdown distribution and final return distribution.
        """
        max_path_length = 2_000
        max_simulations = 2_000

        if path_length is None:
            path_length = min(len(returns), max_path_length)
        if len(returns) > 200_000 and n_simulations > max_simulations:
            log.warning(
                "Large dataset detected; capping Monte Carlo simulations "
                f"from {n_simulations:,} to {max_simulations:,} for runtime safety."
            )
            n_simulations = max_simulations

        n_sims = n_simulations
        rng = np.random.default_rng(42)

        max_drawdowns = np.zeros(n_sims)
        final_returns = np.zeros(n_sims)

        for i in range(n_sims):
            # Resample with replacement for bootstrap
            path = rng.choice(returns, size=path_length, replace=True)

            # Build equity curve (starting at 1.0)
            # Bound growth factors to keep long-path simulations numerically stable.
            growth = np.clip(1.0 + path, 1e-9, 1e6)
            equity = np.cumprod(growth)

            # Track running maximum and drawdown
            running_max = np.maximum.accumulate(equity)
            drawdowns = (running_max - equity) / running_max

            max_drawdowns[i] = np.max(drawdowns)
            final_returns[i] = equity[-1] - 1.0  # total return

        result = {
            "max_drawdowns": max_drawdowns,
            "final_returns": final_returns,
            "dd_median": float(np.median(max_drawdowns)),
            "dd_p95": float(np.percentile(max_drawdowns, 95)),
            "dd_p99": float(np.percentile(max_drawdowns, 99)),
            "dd_mean": float(np.mean(max_drawdowns)),
            "ret_median": float(np.median(final_returns)),
            "ret_p5": float(np.percentile(final_returns, 5)),
            "ret_p95": float(np.percentile(final_returns, 95)),
        }

        log.info(
            f"Monte Carlo ({n_sims:,} paths): "
            f"Median DD={format_pct(result['dd_median'])}, "
            f"95th DD={format_pct(result['dd_p95'])}, "
            f"99th DD={format_pct(result['dd_p99'])}"
        )
        return result

    # ── Phase 4 & 5: Kelly Calculation with Uncertainty ──────────────────────
    def calculate_empirical_kelly(
        self,
        entry_price: float,
        estimated_prob: float,
        platform: str = "polymarket",
        strategy_label: str = "Longshot Value",
    ) -> KellyResult:
        """
        Full pipeline: extract → distribution → Monte Carlo → sizing.

        Args:
            entry_price: Price threshold (e.g., 0.15 for 15¢ contracts)
            estimated_prob: Your estimated true probability (e.g., 0.25)
            platform: 'polymarket' or 'kalshi'
            strategy_label: Human-readable name for the strategy

        Returns:
            KellyResult with all metrics and recommended size.
        """
        # Textbook Kelly
        b = (1.0 - entry_price) / entry_price  # odds
        q = 1.0 - estimated_prob
        textbook_edge = estimated_prob * b - q
        textbook_kelly = max(0, textbook_edge / b) if b > 0 else 0

        # Phase 1: Extract analogs
        analogs = self.extract_historical_analogs(platform, entry_price, estimated_prob)

        if len(analogs) < self.cfg.min_sample_size:
            log.warning(
                f"Only {len(analogs)} analogs found "
                f"(min: {self.cfg.min_sample_size}). Using textbook Kelly."
            )
            return KellyResult(
                entry_price=entry_price,
                estimated_prob=estimated_prob,
                strategy_label=strategy_label,
                textbook_kelly=textbook_kelly,
                textbook_edge=textbook_edge,
                n_historical_analogs=len(analogs),
                empirical_win_rate=0,
                empirical_avg_return=0,
                empirical_std_return=0,
                cv_edge=1.0,
                mc_median_drawdown=0,
                mc_p95_drawdown=0,
                mc_p99_drawdown=0,
                mc_median_final_return=0,
                empirical_kelly=0,
                recommended_size=0,
            )

        # Phase 2: Distribution
        dist = self.build_return_distribution(analogs)

        # Phase 3: Monte Carlo
        mc = self.monte_carlo_resample(
            dist["returns"],
            n_simulations=self.cfg.monte_carlo_runs,
        )

        # Phase 5: Uncertainty-adjusted sizing
        mean_ret = dist["mean"]
        std_ret = dist["std"]
        cv_edge = std_ret / abs(mean_ret) if mean_ret != 0 else 1.0

        # Empirical Kelly: f_empirical = f_kelly * (1 - CV_edge)
        # Clamp CV to [0, 1] to avoid negative sizing
        cv_clamped = min(max(cv_edge, 0), 1.0)
        empirical_kelly = max(0, textbook_kelly * (1 - cv_clamped))

        # Apply conservative fraction (half-Kelly default)
        recommended = empirical_kelly * self.cfg.kelly_fraction

        return KellyResult(
            entry_price=entry_price,
            estimated_prob=estimated_prob,
            strategy_label=strategy_label,
            textbook_kelly=textbook_kelly,
            textbook_edge=textbook_edge,
            n_historical_analogs=len(analogs),
            empirical_win_rate=dist["win_rate"],
            empirical_avg_return=dist["mean"],
            empirical_std_return=dist["std"],
            cv_edge=cv_edge,
            mc_median_drawdown=mc["dd_median"],
            mc_p95_drawdown=mc["dd_p95"],
            mc_p99_drawdown=mc["dd_p99"],
            mc_median_final_return=mc["ret_median"],
            empirical_kelly=empirical_kelly,
            recommended_size=recommended,
        )


# ── Standalone Quick-Run ─────────────────────────────────────────────────────

def run_kelly_analysis(cfg: Config, loader: DataLoader) -> list[KellyResult]:
    """Run Kelly analysis across a grid of entry prices and probabilities."""
    engine = EmpiricalKellyEngine(cfg, loader)
    results = []

    # Grid of strategies to analyze
    strategies = [
        (0.05, 0.15, "Deep Longshots (5¢, est. 15%)"),
        (0.10, 0.20, "Longshots (10¢, est. 20%)"),
        (0.15, 0.25, "Value Contracts (15¢, est. 25%)"),
        (0.20, 0.30, "Moderate Value (20¢, est. 30%)"),
        (0.30, 0.40, "Mild Underpricing (30¢, est. 40%)"),
    ]

    for price, prob, label in strategies:
        log.info(f"\n{'='*60}\nAnalyzing: {label}\n{'='*60}")
        result = engine.calculate_empirical_kelly(
            entry_price=price,
            estimated_prob=prob,
            platform="polymarket",
            strategy_label=label,
        )
        results.append(result)

    return results


if __name__ == "__main__":
    import sys
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/config.toml"
    cfg = load_config(config_path)
    loader = DataLoader(cfg)
    run_kelly_analysis(cfg, loader)
