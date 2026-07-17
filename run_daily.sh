#!/bin/bash
# model-prediction daily runner
# Skips if already ran today. Runs once per day max.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$HOME/Documents/Poly & Kalshi/model prediction" || exit 1

DATE=$(date +%Y-%m-%d)
LOG="data/logs/daily_${DATE}.log"
mkdir -p data/logs

# Skip if already ran today
if [ -f "$LOG" ]; then
    exit 0
fi

set -a; [ -f .env ] && source .env; set +a

echo "=== model-prediction daily $DATE ===" >> "$LOG"
echo "Started: $(date)" >> "$LOG"
PYTHONPATH=src .venv/bin/python -m model_prediction.cli daily --date "$DATE" >> "$LOG" 2>&1
echo "Finished: $(date)" >> "$LOG"

find data/logs -name "daily_*.log" -mtime +30 -delete
