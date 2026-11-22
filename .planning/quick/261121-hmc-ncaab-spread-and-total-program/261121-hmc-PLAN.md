---
phase: quick-261121-hmc
plan: 01
type: quick
follows: quick-261120-nip (protocol 261120, addendum v2, grader.py, confirmation tooling)
---

# 261121-hmc: NCAAB spread and total program

Operator ask (2026-11-21): "i want the spread and total worked on". A_ml_w0 (moneyline Elo weight 0)
passed confirmation and is a separate, pending operator decision: not touched here.

## Inventory (counts only, no outcome joined; 2026-11-21 17:45Z)

- Development lines (`espn_lines.jsonl`): closing spread/total 2023-2026 (~5.75k/season); opening
  spread/total only 2024-2026.
- Confirmation lines: ESPN backfill closing spread/total 2018 (4.4k), 2019 (5.6k), 2020 (5.3k), 2022
  (5.5k); no opens. CBBD cache: closing spread/total 2014-2026 (~4k/season pre-2019), opens only 2025-26.
  => any opening-line statistic is development-only (diagnostic, never confirmable on the backcast).
- `espn_boxscores/` is team-level components only (FGA, FTA, OREB, TOV, points), 2021-22 on.
  No player data anywhere => player candidates need a backfill first.

## Rules (binding)

- Protocol 261120 + addendum v2 stay unedited; `grader.py` stays byte-identical (the A_ml_w0 pin hashes
  the toolchain). New work goes through addendum v3 + a `grader_v3.py` that imports `grader.py`.
- Addendum v3 is pinned (sha256 recorded) BEFORE any new outcome join. K for each statistic = all prior
  reads in `read_log.jsonl` + the v3 grid. One logged read per grid name. Candidates come from
  mechanism, not from slicing residual tables (slicing counts as a read).
- PIT: a prediction for a game may use only games completed before its tip, ratings as of that date.
- No promotion, no `src/` or `config/` edits, no live state edits. Anything that passes is reported to
  the operator; promotion is a new id via `model_promotion` with operator approval.
- No shrink-to-market candidates (circular in backtest; leaves market correlation unchanged).
- Heavy compute never 05:50-06:45 ET or 00:15-01:15 ET (scheduler + host memory pressure).
- Shared checkout: stage explicit paths only; never touch other sessions' uncommitted files
  (`cli/commands.py`, `cli/forecast.py`, `tests/test_forecast_runtime_regressions.py`, STATE.md hunks).
  Files over 3 MB stay untracked.

## Lane T: totals (agent 1)

1. Addendum v3 (totals + spread opening-line diagnostic). Grid:
   - T0 diagnostics (reported, no verdict): served-total level offset vs close (as-of, not season mean);
     closing-total bias split regulation vs overtime; served v6 total cover/over lean.
   - T1_tempo_eff_total: opponent-adjusted offensive/defensive points per possession x projected
     possessions (both teams' adjusted tempo), as of game date; reuse `c_ppp_261120.py`.
   - T2_total_combiner: v7-style ridge combiner for totals (inputs fixed in the pin, e.g. v6 total,
     T1 total, sample size, early-season prior, neutral, PIT league scoring level); fit on train folds.
   - T3_total_scale: over/under distribution scale by expected possessions and sample; median target.
   - S0_open_line_diagnostic: v7 spread vs opening line, and corr(model - open, close - open),
     2024-2026 only; reported, no verdict.
   Primary statistics and DEV-ELIGIBLE rules pinned in v3 (total market-relative slope/corr and rule-1
   MSE delta vs served v6 total; over log-loss at the closing total for scale).
2. Implement, score each once on development (LOSO 2023-2026, espn_archive).
3. Confirmation read (once, 2018-2020 + 2022 ESPN backfill totals) for each DEV_ELIGIBLE candidate.

## Lane P: player data for spread (agent 2)

1. Backfill player-level box scores (ESPN game summary) for 2021-22..2025-26 D1 finals: resumable,
   single worker, polite pause, `observed_at_utc` provenance, small schema (event, team, athlete id,
   starter, minutes, points, FGA, FTA, OREB, DREB, AST, TOV). Runs in the background.
2. Build PIT player features (only games before tip): minutes-weighted availability of the team's
   top-rotation players in the previous game(s); roster continuity; prior-season returning minutes.
   No line or outcome scoring. Report data quality.
3. Spread candidates on player data get pinned in a later addendum (v4) before any outcome join.

## Done when

Addendum v3 pinned; T0/S0 diagnostics and every T candidate scored once; confirmation reads done for
eligible ones; player backfill + PIT feature table built and audited; summary + STATE row + handoff.
