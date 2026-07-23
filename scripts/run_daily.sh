#!/bin/bash
# model-prediction daily runner — split pipeline
# Step 1: Settle ALL open picks from previous days (both ledgers)
# Step 2: Flat forecast — ALL model picks, no edge gate
# Step 3: Main forecast — edge-gated picks promoted to main ledger
# Re-running is safe: clears and replaces today's picks on each run.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
RUNNER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$RUNNER_DIR/.." || exit 1

RUN_DATE=$(TZ=America/New_York date +%Y-%m-%d)
LOG="data/logs/daily_${RUN_DATE}.log"
mkdir -p data/logs

set -a; [ -f .env ] && source .env; set +a

echo "=== model-prediction daily $RUN_DATE (America/New_York) ===" >> "$LOG"
echo "Started: $(TZ=America/New_York date)" >> "$LOG"

# ── Step 1: Settlement ──────────────────────────────────────────────
# Grade every open pick that has started (ESPN scoreboards for US sports,
# Polymarket resolution for esports). Idempotent on already-settled rows.
# Both main ledger and flat_picks.xlsx are settled by the command.
echo "--- Step 1: Settlement ---" >> "$LOG"
PYTHONPATH=src .venv/bin/python -m model_prediction.cli settle --all-unsettled >> "$LOG" 2>&1
SETTLE_EXIT=$?
echo "Settlement exit code: $SETTLE_EXIT" >> "$LOG"

# ── Step 2: Flat forecast ───────────────────────────────────────────
# ALL model candidates → flat_picks.xlsx (no edge/confidence gate).
# Clears and replaces today's flat picks on re-run.
echo "--- Step 2: Flat forecast (all picks) ---" >> "$LOG"
PYTHONPATH=src .venv/bin/python -m model_prediction.cli flat-forecast \
    --all --log --date "$RUN_DATE" \
    >> "$LOG" 2>&1
FLAT_EXIT=$?
echo "Flat forecast exit code: $FLAT_EXIT" >> "$LOG"

# ── Step 3: Main forecast ───────────────────────────────────────────
# Edge-gated picks → main ledger (only picks that pass confidence gate).
# Clears and replaces today's main picks on re-run.
echo "--- Step 3: Main forecast (edge-gated picks) ---" >> "$LOG"
PYTHONPATH=src .venv/bin/python -m model_prediction.cli forecast \
    --all --log --date "$RUN_DATE" --replace-today --model learned \
    >> "$LOG" 2>&1
MAIN_EXIT=$?
echo "Main forecast exit code: $MAIN_EXIT" >> "$LOG"

# ── Step 4: Esports forecast ─────────────────────────────────────────
# Esports picks → research.xlsx only (never main or flat ledger)
echo "--- Step 4: Esports forecast (research ledger) ---" >> "$LOG"
PYTHONPATH=src .venv/bin/python -m model_prediction.cli esports-forecast \
    --all --log --date "$RUN_DATE" \
    >> "$LOG" 2>&1
ESPORTS_EXIT=$?
echo "Esports forecast exit code: $ESPORTS_EXIT" >> "$LOG"

echo "Finished: $(TZ=America/New_York date)" >> "$LOG"
echo "Exit codes — settle: $SETTLE_EXIT, flat: $FLAT_EXIT, main: $MAIN_EXIT" >> "$LOG"

# Cleanup old logs
find data/logs -name "daily_*.log" -mtime +30 -delete
