---
phase: quick-261120-npl
plan: 01
status: complete
files_modified:
  - src/model_prediction/cli/forecast.py
  - tests/test_nba_serving_plumbing.py
---

# NBA serving plumbing (B2, B3, B10, start-time compare)

All changes are in `cli/forecast.py`; `models/nba_v9.py` / `nba_v7.py` and the shared
`learned_forward._team_matches` are untouched. No probability, side, line or ask changes (pinned
by direct-engine comparison tests).

## What changed

- **B2 Over floor.** `_snapshot_season_games_min` reads `model_input_snapshot["features"]` (the key
  `build_snapshot` stores), preferring the new true count `current_season_games_played_min`, then the
  engine's lifetime `current_season_games_min`, then 15. It never raises (bad values fall through),
  because it runs above the Flat append inside a `try` that only catches `ValueError`.
- **B3 Clippers.** `_nba_team_matches` canonicalises exact `LA/L.A. Clippers|Lakers` strings to
  `los angeles ...` inside the comparison only, at the NBA spread and total call sites. The model
  still receives ESPN's name (history key). Moneyline already matched (descriptions are nicknames:
  "Clippers"/"Spurs").
- **B10 components.** New add-only snapshot `features` keys on every NBA spread/total row (Flat and
  Main share the snapshot): `expected_margin_pre_availability` / `expected_total_pre_availability`
  (engine rerun with availability zeroed; diagnostic only, never served),
  `availability_margin_component` / `availability_total_component`, `availability_points_gap_input`,
  `availability_total_loss_input`, `rest_days_{away,home}`, `b2b_{away,home}` (engine-mirrored),
  `current_season_games_{away,home}`, `current_season_games_played_min`,
  `decision_market_mid_{long,short,selection}`, `decision_market_quote_observed_at_utc`. Fail-soft:
  any error omits the keys and logs. No NBA spread/total path writes `production.db`
  (`cli_production.py` serves WNBA only).
- **Start time.** `_nba_same_start_minute` compares parsed UTC minutes, falling back to the old `[:16]`
  string compare. Stored `event_start_utc` values are not rewritten.

## Live check (read-only, 2026-11-20 slate, history from `E:/MP/data`)

True current-season minimum per game is 13-15 (engine lifetime value 225-230). The floor applies to
overs in 7 of 9 games; it goes dormant once both teams pass 15 games (days away). Today's 13 Main
over rows (CHA @ ATL, ORL @ BKN, TOR @ DET, all min 14), joined by event: the open TOR @ DET over
216.5 (edge 8.9%) and 11 of 12 superseded rows (edge 5.3%-8.9%) would be Flat-only; the 05:47Z
TOR @ DET row at 9.7% clears the floor and stays Main.

Replay of today's 21:16Z run through the fixed slate code (read-only: writes under the data roots
refused and none attempted; stub ESPN client built from the stored rows, `utc_now` = the run's
decision time): 9 spread + 9 total calls priced, selection, line and model probability identical to
the stored rows on all 18 (the ask differs only by the known integer-price round trip in
eligibility, e.g. 0.51 vs 0.509804); every stored engine key identical; every new key present and
finite. The ledger persists the whole `features` dict (in `model_input_snapshot_json` and, as
strings, in `feature_payload_json`).

## Verification

- `pytest tests/test_nba_serving_plumbing.py -o addopts='' -q`: 28 passed. At HEAD: 24 failed.
- Revert-and-watch, each fix reverted alone: B2 1 failure, B3 8, start-time 2, B10 6; restored: 28 passed.
- Existing targeted: `test_five_blind_spot_guards.py`, `test_nba_v9.py`,
  `test_forecast_runtime_regressions.py` 28 passed; `test_cli.py -k nba` 19 passed.
- `ruff check` / `ruff format --check` clean on both files.

## Found, not fixed (outside scope or files)

- Settlement CLV (`cli/settle.py` ~650) matches the spread snapshot's `team` with the shared
  `_team_matches`, so newly priced Clippers road spread rows will get blank closing CLV.
- `event_start_utc` format: ledger dedupe key excludes it (`ledger.py` `_market_duplicate_key`:
  event_id, market_type, line, sportsbook, model_version); `canonical_event_id` is a provider-id
  mapping (`event_identity.py`), not derived from the string. The auto-buyer bought-index keys on
  `event_start_utc[:10]` (`portfolio/auto_executor.py` ~1543, ~1567, ~1706, ~2336): ET date for ML rows,
  UTC date for spread/total, consistent within a market type, so rewriting stored values would break
  it. Dashboard identity fallbacks when `event_id` is empty concatenate the raw string
  (`dashboard/picks.py` ~297, ~317; `dashboard/orders.py` ~495). String ordering on mixed formats:
  `system_health.py` 81/91/102 (SQL `event_start_utc < ?`, up to 5 h off for ML rows),
  `dashboard/orders.py` 1200, `dashboard/status.py` 466, `portfolio/manual_bet_ledger.py` 227,
  `portfolio/polymarket_ledger.py` 170 (sorts). `polymarket_scanner._event_utc_date` already parses.
  Scan covered `src/` key builders and the portfolio modules, not the frozen `rebuild/` tree. WNBA/NFL
  slates keep the `[:16]` compare (all inputs are Z today).

## Commits

- B2 is its own commit (the only Main-behaviour change, revertible alone); B3, B10 and the
  start-time compare share one commit because they touch the same matching and snapshot code.
