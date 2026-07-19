#!/bin/bash
# model-prediction daily runner
# Skips if already ran on the current US-Eastern date. Runs once per ET day max.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
RUNNER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$RUNNER_DIR" || exit 1

RUN_DATE=$(TZ=America/New_York date +%Y-%m-%d)
LOG="data/logs/daily_${RUN_DATE}.log"
mkdir -p data/logs

# Always re-run — replaces today's picks with fresh forecast
set -a; [ -f .env ] && source .env; set +a

echo "=== model-prediction daily $RUN_DATE (America/New_York) ===" >> "$LOG"
echo "Started: $(TZ=America/New_York date)" >> "$LOG"
PYTHONPATH=src .venv/bin/python -m model_prediction.cli daily --date "$RUN_DATE" >> "$LOG" 2>&1
echo "Finished: $(TZ=America/New_York date)" >> "$LOG"

find data/logs -name "daily_*.log" -mtime +30 -delete
