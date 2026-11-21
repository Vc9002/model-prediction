---
phase: quick-261121-dac
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/model_prediction/cli/commands.py
  - src/model_prediction/cli/daily.py
  - src/model_prediction/models/college_football.py
  - tests/test_ncaaf_day_ahead_capture.py
autonomous: true
requirements:
  - QUICK-261121-dac (NCAAF overhaul plan, step 2 follow-up: day-ahead Polymarket capture)
---

# Quick 261121-dac: NCAAF day-ahead Polymarket capture

No champion change, no ledger mutation, no scheduled-task change.

## Evidence gathered before editing

- Capture runs only inside the daily job, for today's ET slate
  (`cli/daily.py` `slate_args`, `_serialized_polymarket_captures`). The NFL
  week-ahead (`commands.capture_nfl_polymarket_week_ahead`) already files
  future games under each game's own ET date; NCAAF has no equivalent.
- `build_cfb_slate` (the only NCAAF decision reader) reads only
  `odds/ncaaf/<game date>/`, keeps rows with `observed_at_utc <= decision`,
  takes the latest row per slug, and `_match_cfb_spread` / `_match_cfb_total`
  pick the most balanced ask across slugs with no age bound. So a rung quoted
  the day before and missing from today's shortlist would beat today's rung
  and be stamped `decision_time_executable_quote` at a day-old price.
- Runtime archive (read-only): 168,975 NCAAF rows; 11,241 were observed before
  their file date's ET midnight, all from manual/off-schedule runs
  (2026-09-05 10,700 at 09-04T07Z; 10-22 170; 10-23 364; 11-05 7). Scheduled
  runs write only today's date.
- Saturday size: 94 events (11-14), 115 (11-21). NCAAF is /book-only
  (`EVENT_DETAIL_EXCLUDED_LEAGUES`); `slate()` hydrates each event (one detail
  GET each, unpaced, 4 workers).
- Settlement `_closing_probability_for_moneyline_pick` tries the event's ET
  date first; closes are per slug and the decision quote is game-day, so a
  day-ahead row is never later than a game-day decision's own slug quote.

## Tasks

1. `commands.capture_ncaaf_polymarket_day_ahead(config, start_date, days=2,
   max_seconds=180)`: for D+1, D+2 call `slate("NCAAF", date)` then
   `capture_slate_snapshots(..., game_date=<event ET date>, horizon_hours=None,
   ladder_keep={"NCAAF": 1}, priority_leagues={"NCAAF"},
   max_capture_seconds=<remaining of one shared deadline>)`. The deadline
   starts before the first slate fetch; a date is skipped once it is spent.
   Same writer, same row schema, filed under the event's own date.
2. `daily._run_ncaaf_day_ahead_capture`: gated on
   `is_sport_daily_enabled(config, "NCAAF")`, any exception ->
   `{"status": "error"}`; result key `step1n_ncaaf_day_ahead_quotes`, not in
   `_material_daily_errors`. Runs last in `_serialized_polymarket_captures`
   (after the slate and the NFL week-ahead); the serializer takes
   `*later_captures` and isolates each one.
3. Decision guard (own commit): `build_cfb_slate` loads only rows observed at
   or after 00:00 ET of the game date and at or before the decision time.
   Status quo for scheduled runs (they only write today's date); day-ahead
   rows feed line movement and CLV, never a decision.
4. Tests (`tests/test_ncaaf_day_ahead_capture.py`): switch off -> skipped;
   error -> fail-soft and not material; serializer order and isolation;
   budget (shared deadline passed through, later date skipped when spent);
   filing under the event date; PIT (post-decision row ignored, newest
   in-window row chosen); stale-rung guard. Mutation-prove each guard.
   Run with `-o addopts='' -q`, no MODEL_PREDICTION_* env, plus
   `tests/test_nfl_week_ahead_capture.py`, `tests/test_daily_cfb_pregame_capture_wiring.py`,
   the existing `build_cfb_slate` tests, and `tests/test_daily_cycle_smoke.py`. ruff clean.
