"""
Chart generation for reports and Telegram.

Generates:
- Interactive HTML reports (for local viewing)
- Static PNG images (for Telegram)
- GEX profile charts
- Trend overview dashboard
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config.settings import paths
from data.options_proxy import GEXResult
from models.model1_volatility import Model1Result
from models.model2_trend import Model2Result, TrendState

logger = logging.getLogger(__name__)


def save_chart(fig: go.Figure, name: str, as_png: bool = True) -> Path:
    """Save a Plotly figure as HTML and optionally PNG."""
    paths.ensure()
    html_path = paths.output / f"{name}.html"
    fig.write_html(str(html_path), include_plotlyjs="cdn")
    logger.info(f"Chart saved: {html_path}")

    png_path = None
    if as_png:
        try:
            png_path = paths.output / f"{name}.png"
            fig.write_image(str(png_path), width=1200, height=800, scale=2)
            logger.info(f"PNG saved: {png_path}")
        except Exception as e:
            logger.warning(f"PNG export failed (install kaleido): {e}")
            png_path = None

    return png_path or html_path


def create_gex_chart(m1: Model1Result) -> go.Figure:
    """Create GEX profile chart with call/put walls."""
    gex = m1.gex
    df = gex.gex_by_strike

    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=("Gamma Exposure by Strike", "Open Interest Distribution"),
        vertical_spacing=0.12,
        row_heights=[0.6, 0.4],
    )

    # Total GEX bars
    colors = ["#00C853" if x > 0 else "#FF1744" for x in df["total_gex"]]
    fig.add_trace(
        go.Bar(x=df["strike"], y=df["total_gex"], marker_color=colors, name="Net GEX"),
        row=1, col=1,
    )

    # OI distribution
    fig.add_trace(
        go.Bar(x=df["strike"], y=df["call_oi"], name="Call OI", marker_color="#81C784"),
        row=2, col=1,
    )
    fig.add_trace(
        go.Bar(x=df["strike"], y=-df["put_oi"], name="Put OI", marker_color="#E57373"),
        row=2, col=1,
    )

    # Reference lines
    for row in [1, 2]:
        fig.add_vline(x=m1.spot, line_dash="solid", line_color="#2196F3", line_width=2, row=row, col=1)
        fig.add_vline(x=gex.call_wall, line_dash="dash", line_color="#00C853", line_width=2, row=row, col=1)
        fig.add_vline(x=gex.put_wall, line_dash="dash", line_color="#FF1744", line_width=2, row=row, col=1)
        fig.add_vline(x=gex.max_pain, line_dash="dot", line_color="#FFC107", line_width=1.5, row=row, col=1)

    fig.update_layout(
        title=f"BTC GEX Profile — Spot: ${m1.spot:,.0f} | {m1.day_type}",
        height=700,
        template="plotly_dark",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    return fig


def create_trend_dashboard(m2: Model2Result) -> go.Figure:
    """Create a compact multi-asset trend overview."""
    summary = [r for r in m2.summary if r["timeframe"] == "1h"]
    if not summary:
        return go.Figure()

    tickers = [r["name"] for r in summary]
    strengths = [r["strength"] for r in summary]
    states = [r["state"] for r in summary]

    colors = [
        "#00C853" if s == "Bullish"
        else "#FF1744" if s == "Bearish"
        else "#2196F3"
        for s in states
    ]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=tickers,
        y=strengths,
        marker_color=colors,
        text=[f"{s}\n{st:.0f}" for s, st in zip(states, strengths)],
        textposition="outside",
    ))

    fig.add_hline(y=65, line_dash="dash", line_color="gray", annotation_text="Strong")
    fig.add_hline(y=35, line_dash="dash", line_color="gray", annotation_text="Weak")

    fig.update_layout(
        title="Multi-Asset Trend Strength (1H)",
        yaxis_title="Trend Strength (0-100)",
        height=500,
        template="plotly_dark",
    )

    return fig


def generate_all_charts(
    m1: Model1Result,
    m2: Model2Result,
) -> Dict[str, Path]:
    """Generate all charts and return paths."""
    chart_paths: Dict[str, Path] = {}

    # GEX chart
    gex_fig = create_gex_chart(m1)
    chart_paths["gex"] = save_chart(gex_fig, "btc_gex_profile")

    # Trend dashboard
    trend_fig = create_trend_dashboard(m2)
    chart_paths["trend_dashboard"] = save_chart(trend_fig, "trend_dashboard")

    # Individual trend charts
    for ticker, html_content in m2.charts_html.items():
        safe_name = ticker.replace("=", "_").replace("-", "_")
        html_path = paths.output / f"trend_{safe_name}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        chart_paths[f"trend_{ticker}"] = html_path
        logger.info(f"Trend chart saved: {html_path}")

    return chart_paths
