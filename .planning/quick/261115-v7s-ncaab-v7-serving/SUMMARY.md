# 261115-v7s summary: NCAAB v7 serving path (built, OFF)

## What shipped
- `data/archive/ncaab_v7_prior_seasons.jsonl.gz` (1.75 MB, 24,739 games, seasons 2021-22..2024-25, conference
  maps for 2022..2027 season_year). Build is byte-deterministic. The 2026-27 map is a snapshot of
  2026-11-15 (365 D1 teams); re-run the script to refresh. Teams absent from the map count as non-D1 and fall
  back to v6.
- `src/model_prediction/models/ncaab_v7_serving.py` (`NCAABV7Serving`, `combine_f1`, `load_archive`).
- Hook in `src/model_prediction/models/ncaab_spread_v6.py` (`margin_engine`, `_v7_margins`) and the switch wiring in
  `src/model_prediction/college_basketball_forward.py`.
- `tests/test_ncaab_v7_serving.py` (9 tests).

## Behaviour when on
- D1-vs-D1 games (as-of conference map): expected margin = 0.9593 * ridge(rg_c010_l02_k14) + 0.1611 * tail(v6 as-of
  margin), Student-t (df 8) scale from the current-season sample bucket (11.27, 10.87, 10.71, 9.97).
- v7 path turns OFF v6's rest/density terms and availability points (v7 was fitted without them). Elo/trend blend
  weights, moneyline mixing, eligibility, caps, divergence gate and unquoted rows are unchanged; moneyline and
  spread cover probabilities are derived from v7's mean/scale.
- Non-D1, unknown-team and failed-ridge games: v6. Missing/corrupt archive or any v7 exception: whole slate is v6
  with a warning log; never raises, never guesses.
- The research non-D1 branch (alpha 0.5645, beta 19.96, scale 16.46) is deliberately not used.

## Evidence
- Serving vs research combiner output on 2024-25 days (2024-11-09, 2024-12-19, 2025-01-29, 2025-03-10): max abs
  diff 3.6e-15 on mean and scale.
- Live smoke (history to 2026-11-15, model call only, no ledger): v7 rows served for D1 pairs, an unknown team fell back to v6.
- Targeted suites pass: test_ncaab_v7_serving, test_college_basketball_forward, test_ncaab_tier1_guardrails,
  test_ncaab_v6, test_ncaab_v6_review_fixes, test_ncaab_v7_multiseason. ruff clean on touched files.

## Caveats
- The combiner math is duplicated (few lines) from `scripts/ncaab_v7_multiseason.py` because src must not import scripts;
  tests pin it to that script's `apply_config` and to the frozen JSON.
- Live neutral/rank stats for 2025-26+ come from `data/raw/ncaab`; the research replay used a rebuilt root, so tiny
  differences are possible where the live archive lacks a game.
- Turning it on is an operator decision (v7 has no demonstrated market edge; see the m5r report). STATE.md quick-task
  row skipped: the file carries other sessions' uncommitted edits.
