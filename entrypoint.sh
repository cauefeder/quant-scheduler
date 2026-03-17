#!/bin/bash
set -e

# Clone sibling repos at container startup so /run commands work.
# Uses GH_PAT + GITHUB_USER secrets set via: flyctl secrets set GH_PAT=... GITHUB_USER=...
if [ -n "$GH_PAT" ] && [ -n "$GITHUB_USER" ]; then
    echo "[entrypoint] Cloning project repos..."

    clone_if_missing() {
        local repo=$1 dest=$2
        if [ ! -d "$dest" ]; then
            git clone --depth 1 \
                "https://${GITHUB_USER}:${GH_PAT}@github.com/${GITHUB_USER}/${repo}.git" \
                "$dest" 2>&1 \
                && echo "[entrypoint] Cloned $repo → $dest" \
                || echo "[entrypoint] SKIP $repo (not found or no access)"
        else
            echo "[entrypoint] $dest already present — skipping clone"
        fi
    }

    clone_if_missing "HedgePoly"   "HedgePoly"
    clone_if_missing "PolyTraders" "PolyTraders"
    # Uncomment as you push more repos to GitHub:
    # clone_if_missing "Poly2"        "Poly2"
    # clone_if_missing "ModelTelegra" "ModelTelegra,"
    # clone_if_missing "Poly"         "Poly"
else
    echo "[entrypoint] GH_PAT or GITHUB_USER not set — skipping repo clone"
    echo "[entrypoint] /run commands will skip missing project directories"
fi

# Generate HedgePoly config.toml from env vars (file is gitignored)
HEDGEPOLY_CONFIG="/app/HedgePoly/prediction-market-analysis/config.toml"
if [ ! -f "$HEDGEPOLY_CONFIG" ] && [ -n "$TELEGRAM_BOT_TOKEN" ]; then
    echo "[entrypoint] Generating HedgePoly config.toml from env vars..."
    cat > "$HEDGEPOLY_CONFIG" <<TOML
[paths]
data_dir   = "/app/HedgePoly/prediction-market-analysis/data"
output_dir = "/app/HedgePoly/prediction-market-analysis/output"

[telegram]
bot_token              = "${TELEGRAM_BOT_TOKEN}"
allowed_chat_ids       = "${TELEGRAM_CHAT_ID}"
report_interval_hours  = 24

[analysis]
monte_carlo_runs    = 10000
drawdown_confidence = 0.95
min_sample_size     = 30
kelly_fraction      = 0.5

[calibration]
price_bins       = 20
time_bins        = 10
signal_threshold = 5.0

[orderflow]
min_market_volume     = 100
large_order_percentile = 95

[dashboard]
host  = "0.0.0.0"
port  = 5050
debug = false
TOML
    echo "[entrypoint] config.toml written."
fi

exec python bot.py
