---
phase: quick-261120-npc
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/model_prediction/devig.py
  - src/model_prediction/data_sources/nba_pretip_capture.py
  - src/model_prediction/nba_pretip_capture_worker.py
  - src/model_prediction/run_supervisor.py
  - scripts/register_windows_tasks.ps1
  - tests/test_nba_pretip_capture.py
  - tests/fixtures/nba_pretip/*
autonomous: true
requirements:
  - QUICK-261120-npc
must_haves:
  truths:
    - "For every NBA game on ESPN's scoreboard that tips inside the window, one ESPN odds record per run lands in <runtime>/odds/nba/<ET date>/espn_odds.jsonl with observed_at_utc taken after the response, event id, teams, event_start_utc, source, every provider in the odds list, and the matched Polymarket event (or a NO_CALL_* reason)."
    - "Sportsbook prices are converted to implied probabilities on read; no American price appears in any output, log, doc or record key."
    - "Polymarket quotes for the same games go through the existing capture_slate_snapshots into the canonical polymarket_snapshots.jsonl (bid, ask, midpoint, sizes, observed_at_utc), reusing its client, pacing and 429 budget."
    - "Each game fails closed on its own (no ESPN odds, no/ambiguous Polymarket event, detail fetch error); the capture never raises."
    - "Devig helper offers multiplicative (default, house convention) and power, documented with the measured difference."
    - "Worker nba-pretip-capture is in run_supervisor.WORKERS and in register_windows_tasks.ps1 (every 10 min 11:00-02:00 ET, S4U via the script's -LogonType), not registered."
    - "Tests run on recorded real ESPN and Polymarket payloads; ruff clean."
---

# quick-261120-npc: NBA pre-tip market capture (Phase 0.1, track T-B)

Why: NBA CLV cannot be measured. Polymarket quotes are captured only at forecast runs (last one a
median 65 minutes before tip) and no sportsbook consensus is captured live. The v10 main gate is CLV
against the devigged consensus at about T-10; track T-B compares that consensus with the Polymarket ask.

## Tasks

1. Record real payloads (ESPN NBA scoreboard with odds, ESPN scoreboard with final/live games, Polymarket
   NBA event list, one event detail, one /book) and trim them into `tests/fixtures/nba_pretip/`.
2. `devig.py` (multiplicative and power) and `data_sources/nba_pretip_capture.py` (ESPN parse, consensus,
   Polymarket match by tip time and both nicknames, capture through `capture_slate_snapshots`).
3. Worker module, `WORKERS` entry (300 s timeout, no daily lock), task definition in
   `register_windows_tasks.ps1` (parse-checked, not executed).
4. Tests (`-o addopts='' -q`), ruff, one hand run against the real slate with the runtime root.

Out of scope: forecast, models, config/models, production.yaml, auto-buyer state, any reader change.
