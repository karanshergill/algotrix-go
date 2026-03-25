#!/bin/bash
# Pre-open launcher: refresh Fyers token then start collector
# Runs via cron at 8:50 AM IST on trading days

set -e
LOG="/tmp/preopen_collector_$(date +%Y%m%d).log"

echo "=== Pre-open launcher $(date) ===" >> "$LOG"

# Step 1: Refresh token
echo "Refreshing Fyers token..." >> "$LOG"
bash /home/me/projects/algotrix-go/engine/refresh_token.sh >> "$LOG" 2>&1

if [ $? -ne 0 ]; then
    echo "ERROR: Token refresh failed!" >> "$LOG"
    # Try to notify via openclaw
    openclaw system event --text "⚠️ Fyers token refresh FAILED — pre-open collector won't run. Need manual login." --mode now 2>/dev/null || true
    exit 1
fi

# Step 2: Check if collector is already running
if pgrep -f "preopen_collector.py" > /dev/null; then
    echo "Collector already running, skipping." >> "$LOG"
    exit 0
fi

# Step 3: Start collector
echo "Starting pre-open collector..." >> "$LOG"
cd /home/me/projects/algotrix-go
nohup python3 -u regime-classifier/src/preopen_collector.py >> "$LOG" 2>&1 &
COLLECTOR_PID=$!
echo "Collector started (PID: $COLLECTOR_PID)" >> "$LOG"

# Notify
openclaw system event --text "📊 Pre-open collector started (PID: $COLLECTOR_PID) — capturing 9:00–15:30 data" --mode now 2>/dev/null || true
