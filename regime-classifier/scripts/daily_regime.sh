#!/bin/bash
# Daily regime scoring — run after market close data is fetched.
# Called by the pipeline after bhavcopy + FII/DII + IX data is available.
#
# Usage:
#   ./scripts/daily_regime.sh [YYYY-MM-DD]
#   If no date given, uses today.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLASSIFIER_DIR="$(dirname "$SCRIPT_DIR")"
cd "$CLASSIFIER_DIR"

DATE="${1:-$(date +%Y-%m-%d)}"

echo "=== Daily Regime Score: $DATE ==="

export PGPASSWORD=algotrix

# Step 1: Score today and store in regime_daily
echo "Scoring regime for $DATE..."
python3 cli.py regime daily --date "$DATE"

echo ""
echo "=== Done ==="
