#!/bin/bash
# model-prediction daily runner — launched by launchd every morning
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$HOME/Documents/Poly & Kalshi/model prediction" || exit 1

# Load .env for API keys
set -a
[ -f .env ] && source .env
set +a

DATE=$(date +%Y-%m-%d)
LOG="data/logs/daily_${DATE}.log"
mkdir -p data/logs

echo "=== model-prediction daily $DATE ===" >> "$LOG"
echo "Started: $(date)" >> "$LOG"

# Run the full daily pipeline
PYTHONPATH=src .venv/bin/python -m model_prediction.cli daily --date "$DATE" >> "$LOG" 2>&1

echo "Finished: $(date)" >> "$LOG"
echo "" >> "$LOG"

# Keep last 30 days of logs
find data/logs -name "daily_*.log" -mtime +30 -delete
