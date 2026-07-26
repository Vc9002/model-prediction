#!/bin/bash
# model-prediction daily runner — split pipeline
# Step 1: Settle ALL open picks from previous days (both ledgers)
# Step 2: Main forecast — edge-gated picks promoted to main ledger
# Step 3: Flat forecast — ALL model picks, no edge gate
# Main runs BEFORE flat so that when flat snapshots main's exposure to size
# its own picks (exposure_ledger=main, always), it sees today's real main
# calls already logged rather than a stale pre-run picture — otherwise flat's
# unit sizes for shared games could drift from main's own (main sizes each
# pick against exposure accumulated so far within its own run; flat, running
# first, would size against an exposure state from before any of today's
# main calls existed).
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

# ── Step 2: Polymarket slate capture ──────────────────────────────────
# Capture executable BBO snapshots for every Polymarket sport contract.
# Forecast steps read these snapshots to match picks against real prices.
echo "--- Step 2: Polymarket slate snapshot ---" >> "$LOG"
PYTHONPATH=src .venv/bin/python -m model_prediction.cli polymarket-slate \
    --all --date "$RUN_DATE" \
    >> "$LOG" 2>&1
SLATE_EXIT=$?
echo "Polymarket slate exit code: $SLATE_EXIT" >> "$LOG"

# ── Step 3: Main forecast ───────────────────────────────────────────
# Edge-gated picks → main ledger (MLB/WNBA); esports + soccer/nba/nfl route
# to research.xlsx / gated_research.xlsx internally (see _forecast_learned_sport
# and _log_esports_forecast, both invoked per-title/sport by --all below).
# There is no separate "esports-forecast" step: it was a fully redundant
# second invocation of the same esports logging this step already does,
# and running both produced near-simultaneous duplicate research rows for
# the same contract. Clears and replaces today's main picks on re-run.
echo "--- Step 3: Main forecast (edge-gated + research-routed picks) ---" >> "$LOG"
PYTHONPATH=src .venv/bin/python -m model_prediction.cli forecast \
    --all --log --date "$RUN_DATE" --replace-today --model learned \
    >> "$LOG" 2>&1
MAIN_EXIT=$?
echo "Main forecast exit code: $MAIN_EXIT" >> "$LOG"

# ── Step 4: Flat forecast ───────────────────────────────────────────
# ALL model candidates → flat_picks.xlsx (no edge/confidence gate). Runs
# after main so its exposure snapshot of the main ledger (see comment above)
# reflects today's real calls. Clears and replaces today's flat picks on re-run.
echo "--- Step 4: Flat forecast (all picks) ---" >> "$LOG"
PYTHONPATH=src .venv/bin/python -m model_prediction.cli flat-forecast \
    --all --log --date "$RUN_DATE" \
    >> "$LOG" 2>&1
FLAT_EXIT=$?
echo "Flat forecast exit code: $FLAT_EXIT" >> "$LOG"

echo "Finished: $(TZ=America/New_York date)" >> "$LOG"
echo "Exit codes — settle: $SETTLE_EXIT, ingest: $INGEST_EXIT, slate: $SLATE_EXIT, main: $MAIN_EXIT, flat: $FLAT_EXIT" >> "$LOG"

# Cleanup old logs
find data/logs -name "daily_*.log" -mtime +30 -delete
