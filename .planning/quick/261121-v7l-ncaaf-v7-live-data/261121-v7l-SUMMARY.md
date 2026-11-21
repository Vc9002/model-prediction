---
phase: quick-261121-v7l
plan: 01
status: complete
branch: ncaaf-v7live-261121 (worktree E:\MP-wt\ncaaf-v7live)
commits: 75627175, debcef98, 3190aca1, (this follow-up)
---

# quick-261121-v7l summary: NCAAF v7 live data pipeline

## Result

cfb-structural-v7 can now be priced point in time from runtime-root data. With the seeded store and one
incremental update, `price_v7_slate` priced 91 of 103 pre-kickoff NCAAF games, decision = now (2026-11-21
~21:54Z). The slate was today's not-yet-started games plus 11-24 to 11-29; 11-22 has no games. All 12 no-calls
are `NO_CALL_V7_NO_RATING_HISTORY` for ESPN placeholder opponents: team `-2` "TBD" (4 games) and `112351`/`112350`
"TBA" (8 FCS playoff slots). There were 0 `PBP_MISSING`, 0 `NO_CLASS` and 0 `NO_CFBD`. Slate pricing took 2.5 s
including the load.

## Built

- `src/model_prediction/data_sources/cfb_pbp_live.py` covers the following.
  - Scoreboard discovery reads ESPN groups 80 and 81 per date, so FCS-vs-FCS games are discovered. A per-date
    scan ledger rescans open dates only.
  - Summaries are fetched for games final on the scoreboard once kickoff + 5 h has passed. A "no plays" result
    counts as terminal only when fetched 24 h or more after kickoff.
  - Incremental rows go to `pbp/<season>.jsonl`; only raw files missing from `features_index.jsonl` are processed.
    Every row carries `observed_at_utc` (the fetch time) and `kickoff_utc`.
  - It also handles season FBS lists, the one-time seed, and the `update` and `status` CLI.
  - Runs are paced at 1 req/s with a 180 s default budget, a non-blocking lock, and fail-soft status dicts.
- `src/model_prediction/features/cfb_pbp_rows.py` is the research row builder, moved out of
  `scripts/cfb_pbp_features.py`. That script is now a re-export shim, and the research tests still pass.
- `RuntimePaths.ncaaf_v7_root` points to `<runtime>/research/ncaaf_v7`, and `ncaaf_v7_lock` to
  `<runtime>/locks/ncaaf_v7_pbp.lock`.
- `models/cfb_structural_v7.py` gains `load_v7_serving_data` and `price_v7_slate(data_root, decision_time,
  games)`.
  - It fits the state once per slate and is tested equal to `price_game`.
  - The row spine is the fit script's: pbp rows plus score-only rows for final games without pbp. Rows observed
    after T are dropped.
  - Two conditions no-call the whole slate: a scan gap (`NO_CALL_V7_PBP_MISSING`) or a missing class list for S
    or S-1 (`NO_CALL_V7_NO_CLASS_LABEL`).
  - Per team, an admitted schedule or live-history game without pbp gives `NO_CALL_V7_PBP_MISSING`, unless ESPN
    has none for it.
- `cli/daily.py`: `step1o_ncaaf_v7_pbp` runs in the capture pool, which is joined before forecasting. It is gated
  by the NCAAF daily switch and is not material.

## Verification

- Tests: `tests/test_cfb_pbp_live.py` has 16 tests and `tests/test_cfb_structural_v7.py` has 8; all 24 pass.
  - The live tests cover incremental runs, FCS-vs-FCS discovery, the fetch buffer, no-plays retry, the time budget,
    the lock and unseeded root, network fail-soft, daily gating, `price_game` equality, PIT entry and observation
    rules, missing pbp, the live-history witness, the scan gap, a missing class list and `sync-cfbd`.
  - Two more cases are covered. A game still marked scheduled after kickoff + 5 h is demanded (no-call); a
    postponed game is not.
  - Fetch and scan time stamps are taken after the response. This is tested with a clock that advances one minute
    per request.
  - I mutated each of 16 guards and each mutation failed at least one test.
- Discovery completeness: every 2026 event in the seeded v6 snapshot (1,384) also appears in a scoreboard-sourced
  record. None is seed-only.
- Production path: `data_root` = `Path(ledger_path(config)).parent` = `<repo>/data`. `v7_paths` maps it to
  `<runtime>/research/ncaaf_v7` (checked with the env var set). An update result of `not_seeded` in
  `step1o_ncaaf_v7_pbp` would mean the path did not resolve.
  - Also pass: `tests/research/test_cfb_pbp_features.py` (29), the daily wiring and day-ahead tests, and
    `test_runtime_paths`.
- Parity on the seeded runtime store (`verify_seed.py`, scratch):
  - Serving spine 2025-26 against `cfb_v7_fit.load_team_games`: of 6,204 research rows, 0 are missing and 0 differ.
    The 106 runtime-only rows are post-11-14 score-only rows.
  - 2026 rows rebuilt from the copied raw files with the moved builder: 2,830 of 2,830 are identical.
  - CFBD 2026 covariates equal `load_cfbd_covariates()[2026]` (220 teams).
  - ESPN group-80 lists agree with the research `season_classes` on every team that played in 2025 and 2026, with
    0 disagreements.
- Seed (`E:\model-prediction-runtime\research\ncaaf_v7`, new directory only):
  - Copied: pbp rows for 2016-2026 (31,066), raw and manifest for 2025-26 (3,102 files, 16,404 manifest records),
    16,378 snapshot schedule records and 3,067 CFBD rows.
  - Fetched: the 2025 and 2026 FBS lists (146 and 148 ids), then swept 100 dates of 2026 (202 requests). The sweep
    found 8 events to catch up: 7 FCS-FCS and 1 FBS-FBS.
- Update: the first run took 11.6 s with 12 requests, fetched 8 and added 16 rows. The second run took 3.5 s with 4
  requests and fetched nothing.

## Interface for the routing session

`price_v7_slate(data_root, decision_time, games)` takes mappings or objects with `event_id`, `home_team_id`,
`away_team_id`, `is_neutral_site`, and optionally `spread_home_line` and `total_line`. It returns
`(results, summary)`. Each result has `status` ok or no_call, `reason`, `detail`, `mu_margin`, `mu_total`,
`margin_sd`, `total_sd` and `probabilities`. Pass the daily `data_root`: it is rerouted to the runtime root for the
v7 store, and the live-history witness is read from `data_root/processed/ncaaf/games.jsonl`.

## Caveats

- The seeded raw files cover only 2025-26, so earlier seasons exist only as rows.
- There is a decision-time floor at the seed time, 2026-11-21 about 21:50Z. The class lists and scan ledger are
  stamped then, so any earlier decision no-calls the whole slate. The store serves live decisions, not replays;
  refits and replays use the research copies.
- `price_v7_slate` never raises `V7NoCall`, but a corrupt store file can raise. The routing hook must catch
  exceptions and fall back.
- Pool latency: the step usually takes about 4-12 s. A backlog can take up to the 180 s budget plus one 15 s
  timeout, which delays the start of every forecast, but not its results.
- Runtime writes beyond the seed: two `update` runs added 8 raw files, 16 rows and `locks/ncaaf_v7_pbp.lock`.
- One ESPN play type, `fumble`, is unknown to the builder and is excluded and counted, as in research.
- 2027 needs CFBD rows for that season; without them v7 returns NO_CALL_V7_NO_CFBD_PRIOR. Fetch them with
  `scripts/cfbd_backfill.py`, then run `python -m model_prediction.data_sources.cfb_pbp_live sync-cfbd`, which
  appends whole new seasons only. The FBS lists are fetched automatically.
