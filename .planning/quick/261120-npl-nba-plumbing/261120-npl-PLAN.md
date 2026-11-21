---
phase: quick-261120-npl
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/model_prediction/cli/forecast.py
  - tests/test_nba_serving_plumbing.py
autonomous: true
requirements:
  - QUICK-261120-npl
must_haves:
  truths:
    - "B2: the NBA totals thin-sample Over floor reads model_input_snapshot['features'] (the key build_snapshot stores), preferring a true current-season games count (TrendEngine.current_season_games_played with the season-opener override) over the engine's lifetime current_season_games_min; default 15. The gate never raises (bad values coerce to 15), so no Flat row can be lost."
    - "B3: NBA spread and total market matching canonicalise 'LA Clippers'/'Los Angeles Clippers' and 'LA Lakers'/'Los Angeles Lakers' at the NBA call sites only; the shared learned_forward._team_matches is unchanged, and the names passed to the model stay ESPN's (history keys)."
    - "B10: every NBA spread/total snapshot['features'] additionally carries pre-availability expected margin/total (engine rerun with availability zeroed), the availability inputs and component, rest days and b2b per team (engine-mirrored), current-season games per team, and the decision-time Polymarket mid. Existing keys are never renamed or removed; probability, side, line and ask are unchanged."
    - "Start time: NBA spread/total quote-to-event matching compares parsed UTC minutes, falling back to the old [:16] string compare; stored event_start_utc values are never rewritten."
    - "Flat: a test pins that every priced NBA spread/total contract reaches the Flat ledger, including Main-gated overs."
---

<objective>
Serving-plumbing fixes B2, B3, B10 and start-time compare from docs/NBA_REVIEW_AND_V10_PLAN_2026-11-20.md section 3, in cli/forecast.py only. models/nba_v9.py and nba_v7.py are not touched (their file hash is part of replay provenance). B1, B4-B9 are out of scope.
</objective>

<tasks>
1. Write tests/test_nba_serving_plumbing.py first; run it at HEAD and record the failures.
2. Implement in cli/forecast.py: _nba_team_matches alias helper, _nba_same_start_minute, schedule/current-season/mid component helpers, pre-availability rerun, gate key fix.
3. Re-run the tests; revert each fix in turn to watch its test fail, restore.
4. ruff check/format on touched files; targeted existing NBA tests (test_five_blind_spot_guards.py, test_nba_v9.py, test_forecast_runtime_regressions.py).
5. Read-only live check of today's slate: which games the Over floor now applies to.
</tasks>

<verification>
`pytest tests/test_nba_serving_plumbing.py -o addopts='' -q` passes; each fix proven by revert; ruff clean.
</verification>
