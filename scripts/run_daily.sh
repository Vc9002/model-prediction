#!/bin/bash
# model-prediction daily runner — split pipeline
# Step 1: Settle ALL open picks from previous days (both ledgers)
# Step 2: Unified slate + forecast:
#   - MLB/WNBA qualified calls -> main
#   - all learned US-sport candidates -> flat
#   - soccer/esports/KBO/NPB -> research, valid subset -> gated research
# The unified command computes each learned slate once and reuses it for flat,
# avoiding the old second full forecast process and duplicate upstream reads.
# Re-running is safe: clears and replaces today's picks on each run.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
RUNNER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$RUNNER_DIR/.." || exit 1

# Hold one non-blocking OS lock for the entire settlement/ingest/forecast
# workflow. Re-entry uses the inherited lock descriptor and skips this wrapper.
if [ "${MODEL_PREDICTION_DAILY_LOCK_HELD:-}" != "1" ]; then
    export MODEL_PREDICTION_DAILY_LOCK_HELD=1
    export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
    exec .venv/bin/python -m model_prediction.daily_lock \
        --lock data/locks/daily.lock -- bash "$0"
fi

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

# ── Step 1b: Historical game ingestion ──────────────────────────────
# Settlement above only grades ledger PICKS from ESPN scoreboards — it does
# NOT feed completed games back into data/historical/*_games_all.jsonl, the
# dataset every rolling feature (elo, trend, park, pitcher_era_gap) reads via
# FeatureStore.games_before(). Nothing else in this script (or on the
# schedule) ever called `ingest`, so that dataset silently stopped advancing
# entirely — found 2026-07-25 when it was still frozen as of 7/22-23. Ingests
# both yesterday and today (today usually reports 0 new games pregame; still
# safe/idempotent) so a single missed run auto-heals on the next one instead
# of silently compounding.
echo "--- Step 1b: Historical game ingestion ---" >> "$LOG"
YESTERDAY=$(TZ=America/New_York date -v-1d +%Y-%m-%d 2>/dev/null || TZ=America/New_York date -d yesterday +%Y-%m-%d)
INGEST_EXIT=0
for sport in mlb nba wnba nfl; do
    for d in "$YESTERDAY" "$RUN_DATE"; do
        PYTHONPATH=src .venv/bin/python -m model_prediction.cli ingest --sport "$sport" --date "$d" >> "$LOG" 2>&1
        code=$?
        if [ "$code" -ne 0 ]; then INGEST_EXIT=$code; fi
    done
done
echo "Ingestion exit code: $INGEST_EXIT" >> "$LOG"

# ── Step 2: Unified daily forecast ───────────────────────────────────
echo "--- Step 2: Unified slate + main/flat/research forecasts ---" >> "$LOG"
PYTHONPATH=src .venv/bin/python -m model_prediction.cli daily \
    --date "$RUN_DATE" --skip-settlement \
    >> "$LOG" 2>&1
DAILY_EXIT=$?
echo "Unified daily exit code: $DAILY_EXIT" >> "$LOG"

echo "Finished: $(TZ=America/New_York date)" >> "$LOG"
echo "Exit codes — settle: $SETTLE_EXIT, ingest: $INGEST_EXIT, daily: $DAILY_EXIT" >> "$LOG"

# Cleanup old logs
find data/logs -name "daily_*.log" -mtime +30 -delete

if [ "$SETTLE_EXIT" -ne 0 ] || [ "$INGEST_EXIT" -ne 0 ] || [ "$DAILY_EXIT" -ne 0 ]; then
    exit 1
fi
