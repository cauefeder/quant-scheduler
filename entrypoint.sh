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

exec python bot.py
