---
phase: quick-261120-npc
plan: 01
subsystem: market-capture
tags: [nba, clv, polymarket, espn, devig, supervisor]
status: complete
requirements: [QUICK-261120-npc]
key-files:
  created:
    - src/model_prediction/devig.py
    - src/model_prediction/data_sources/nba_pretip_capture.py
    - src/model_prediction/nba_pretip_capture_worker.py
    - tests/test_nba_pretip_capture.py
    - tests/fixtures/nba_pretip/ (5 trimmed real payloads)
  modified:
    - src/model_prediction/run_supervisor.py
    - scripts/register_windows_tasks.ps1
commits: 5
plan_head_before: a3cbdac7
---

# quick-261120-npc: NBA pre-tip market capture

Worker `nba-pretip-capture` captures, for every NBA game tipping inside the next 25 minutes, the ESPN
sportsbook odds (as probabilities, devigged, with a cross-book consensus) and the Polymarket quotes.
Scheduled every 10 minutes from 11:00 to 02:00 ET, each game gets captures about 20 and 10 minutes before tip.

## Commits

- `2db631de` feat: devig helper, capture module, worker entry point, tests and fixtures
- `c89e7adc` feat: `WORKERS` entry (300 s timeout) and the task definition in `register_windows_tasks.ps1`
- `85af3746` docs: this plan and summary
- test commit: pin `polymarket_us.utc_now` to the test clock in the end-to-end test (it failed once real
  time passed the fixture's tip; proved by forcing utc_now to 2026-12-15, failing before, passing after)
- docs commit: this summary update

## Storage

- ESPN: `<runtime>/odds/nba/<ET date>/espn_odds.jsonl`, one hashed record per game per run. Fields:
  `observed_at_utc` (stamped after the response), `event_id`, teams, `event_start_utc`, `minutes_to_tip`,
  `source=espn_scoreboard_odds`, `providers[]` (moneyline, spread, total: current and open lines, raw implied
  probabilities, overround, fair probabilities), `consensus` (`method`, mean fair probability at the modal
  line, `n_books`), `polymarket` (matched event slug and id, or a `NO_CALL_*` reason).
- Polymarket: the existing canonical `polymarket_snapshots.jsonl` via `capture_slate_snapshots`, unchanged.
  One event-detail request per game quotes bid/ask on every rung; paced /book adds sizes for the moneyline,
  the most balanced spread and total rung, the rungs at the ESPN lines and open NBA picks' rungs.
  Consequence: the existing closing-snapshot and CLV readers now see quotes about 10 minutes before tip,
  so CLV on NBA picks settled after this goes live is measured nearer the close than before.

## Devig

Multiplicative by default (the house convention, `pricing.normalize_no_vig`), power available. Measured
on DraftKings' MIL @ PHI 2026-11-21 moneyline (86.7% / 17.4% implied): 83.3% vs 85.4% for Philadelphia.
Equal-priced spread/total pairs are identical under both.

## Verification

- `pytest tests/test_nba_pretip_capture.py -o addopts='' -q`: 12 passed. `ruff check src/ tests/` clean.
- Open NBA pick slug lookup against the runtime root returns 27 slugs, no warning in the hand run.
- `tests/test_run_supervisor.py tests/test_system_health.py`: 35 passed, 1 failed
  (`test_worker_registry_commands_exist_on_disk`): the worktree has no `.venv`, so the existing production
  worker's interpreter path does not exist here. Not caused by this change (it only checks
  daily/production/rebuild-shadow).
- Hand run 2026-11-20 23:38 ET against the real runtime root with `--window-minutes 1400` (a wide-window
  validation; nothing tips within 25 minutes at that hour, so these are not T-10 rows): 6 games, 6 ESPN
  records, 6/6 Polymarket events matched, 440 snapshot rows (385 event-detail, 46 /book, 9 /bbo), 0
  failures, one 429 pause, 152 s. That run used 3 rungs per ladder (55 /book calls); `NBA_LADDER_KEEP`
  was then cut to 1 so a busy T-10 slot stays well inside 10 minutes; the committed value of 1 has run
  only under tests so far.

## API limitations

- ESPN exposes one sportsbook for NBA (DraftKings, provider 100) on the scoreboard, the core odds endpoint
  and the summary pickcenter, so "consensus" is a single-book devig today (`n_books=1`). The code keeps
  every provider ESPN lists, so more books are captured automatically if ESPN adds them.
- ESPN strips the odds block once a game goes live or final; the last pregame record is the close.
- Polymarket event-detail quotes carry no sizes and no transact time; sizes come only from /book or /bbo,
  which share an IP-wide 429 limit with the daily capture.

## Not done (operator)

- The scheduled task is defined but not registered. Until it runs once, `system_health` reports
  "worker 'nba-pretip-capture' has never run" (after merge).
- A failed last run of the night (about 02:00) leaves `system_health` down until the 11:00 run.
