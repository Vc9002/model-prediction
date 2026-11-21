# nba-player-v10 (Phase 3 player layer): pre-registration

Written 2026-11-21, before any validation outcome was joined to a player-layer prediction. This
file is committed before `scripts/nba_v10/player_fit.py walk` is allowed past 2025-11-02 and
before `player_fit.py g1` runs (both commands check `git log` for this file). After that commit the
spec below does not change in response to validation results. Plan reference:
`docs/NBA_REVIEW_AND_V10_PLAN_2026-11-20.md` sections 5, 6 (G1, Phase 3), 8, 9.

Research only: no serving, no ledgers, no `config/production.yaml`, no champions, no buyer, no
promotion. The artifact goes to `config/models/research/nba-player-v10.json`.

## 1. Data (read-only)

| input | use | PIT rule |
|---|---|---|
| `data/research/nba_v10/games/player_box.jsonl` (ESPN box 2016-17 .. 2023-24) and `data/availability/nba/espn_boxscores/*.json` (live ESPN captures 2024-01 onward; research rows win on overlap) | player minutes, box stats, plus team possessions | a game's box rows feed only decisions on later ET dates |
| `games/scoreboard_index.jsonl` | ET game date, tip time, teams, scores, season type | schedule fields only before tip |
| `injury_reports/rows/*.jsonl.gz` (official NBA injury report, 2021-22 onward) | availability statuses | only rows with `observed_at_utc` <= decision time |
| `harness/v9.jsonl`, `harness/v91_{spread,total}_final.jsonl` | v9 / v9.1 predictions and the validation closing lines | benchmark only |
| `closing_lines/nba_closing_lines_2007_2026.jsonl` | train-span closing lines (home line = -home_spread) | benchmark only, never an input |
| `espn_odds/espn_lines.jsonl` (`selected.open_*_books`, DraftKings; opens exist from 2023) | exploratory opening-line lambda | benchmark only, never an input |

Derived files: `data/research/nba_v10/player/{box_games,injury_index}.jsonl` built by
`scripts/nba_v10/player_build_data.py` with provenance files. Every loader stops at ET date
2026-02-22; the locked holdout (>= 2026-02-23) is never loaded. All-Star events (team id > 30)
are excluded. A box is accepted only if both teams' minutes sum to at least 215. The box score
of the game being priced is never an input; `did_not_play` of the priced game is never an
availability input; `dnp_reason` and `active` are never read.

## 2. Split

The replay harness's pinned split (`scripts/nba_v10/replay_harness.py`, `outputs/nba/v10_harness/G1.md`),
so the player layer is scored on the same validation games as v9 / v9.1 (n = 762):

- train: every game before 2025-11-03. Model rows start 2017-10-01; 2016-17 only warms up the
  ratings. All fits and all hyperparameter choices use train only.
- validation: 2025-11-03 .. 2026-02-22 (G1 only).
- locked holdout: >= 2026-02-23, never loaded, never scored.

This is not a fresh 60/20/20 over 2016-2026: on that split the locked 20% would contain the
harness validation window and v9 could not be compared. Train is "everything before 2025-11-03
back to 2017-18".

## 3. Model spec

Code: `src/model_prediction/features/nba_player_ratings.py` (features, PIT state) and
`src/model_prediction/models/nba_player_v10.py` (team model). State advances one ET date at a
time; features for date D use games on dates < D only.

### 3.1 Player ratings (as of each ET date)

Two ratings per player, in points per game for a full 48-minute share: `r` (margin) and `t`
(game efficiency, points per 100 possessions, both teams).

- Box rates `z`: exponentially decayed (half-life h days) per-48 rates of pts, fg3m, fga, fta,
  ast, tov, oreb, dreb, stl, blk, pf, shrunk toward fringe-player rates with 250 pseudo-minutes.
  Fringe rates = minute-weighted per-48 rates of train player-seasons with < 500 minutes.
- Box prior (fit on train games 2017-10-01 .. 2025-11-02, OLS, one fit per h):
  `margin = hca + sum_home(x z).gamma - sum_away(x z).gamma`,
  `100 total / poss - 2 eff_base = c + sum_both(x z).delta`, where `x = 5 * minutes / team
  minutes` (actual minutes of past games) and `z` the pre-game rates. Prior `p = z.gamma`,
  `q = z.delta`.
- Ridge residual (game-level RAPM on our own box history): weighted ridge of the residual the
  prior leaves, `y = margin - hca - x.p` (signed x) and `y = 100 total/poss - 2 eff_base - c - x.q`,
  game weight `2^(-age/h)`, penalty lambda; players whose decayed design weight is below 0.05
  keep residual 0. Rating = prior + residual. A player never seen gets the fringe prior.
- Hyperparameters, chosen per market by train-only time-series CV: h in {120, 365, 730} days,
  lambda in {3, 10, 30, 100, infinity (box prior only)}. Metric: pooled walk-forward RMSE over
  train games 2018-10-01 .. 2025-11-02 of the margin (`hca + S_home - S_away`) and of the game
  efficiency (`c + 2 eff_base + U_home + U_away`), with ratings as of the game date and the
  game's actual minutes (isolates rating quality; train only). Lowest RMSE wins; ties go to the
  larger lambda. Result in `data/research/nba_v10/player/fits/cv.json`.

### 3.2 Minutes projection (PIT)

- Candidates: players whose most recent box row (any row, DNP rows of PAST games included) is
  with the team and inside the team's last 10 games; once the team has 3 current-season games,
  the row must be from the current season.
- Recent minutes: recency-weighted (0.85 per game) mean over the last 10 played games of
  blowout-adjusted minutes. Starter status: if the role of the last played game (starter or
  bench) has >= 3 games in the window, only those games are used.
- Blowout adjustment: each past game's minutes are divided by `1 + s * max(0, |final margin| - 10)`
  (clipped to [0.5, 2]); `s` fitted on train separately for starters and bench (minutes over
  the player-season mean, intercept-relative slope).
- Recent share: fraction of the team's last 5 games (since the player joined) that he played;
  bucketed zero / low (< 0.5) / mid (< 1) / full.
- Expected minutes `e = P(play | status, bucket) * recent minutes`. P(play) is a train-fitted
  table (cells with < 30 observations fall back to the status-only cell, then the bucket-only
  `unknown` cell). If the team sum is above 240, scale down to 240; if below, scale available
  players up by at most 1.25 and give the rest to a replacement-level player (rating of a
  player with fringe rates).
- Rest: not a minutes input. Rest enters the team model (b2b and 3-in-4 terms). Deviation from
  the plan's wording, named here.

### 3.3 Availability (variant `avail`)

- Decision time = tip - 60 minutes. Report = latest official report with `observed_at_utc` <=
  decision time that lists the game date. NO_CALL_INJURY_REPORT_MISSING if none or older than
  18 hours; NO_CALL_INJURY_GAME_ABSENT if that report does not list the matchup.
- Team status = the team's latest content at or before that report; `NOT YET SUBMITTED` falls
  back to the latest earlier submitted content for the same game date, else
  NO_CALL_INJURY_NOT_SUBMITTED. A listed game with no rows for the team = nobody listed.
- Names: "Last, First" with suffixes normalized against candidate display names (accents,
  punctuation, Jr/Sr/II/III/IV/V dropped), then first initial + last name. A report name that
  matches two candidates = NO_CALL_INJURY_NAME_AMBIGUOUS. Unmatched rows (players outside the
  candidate set) are ignored and counted.
- Status (Out / Doubtful / Questionable / Probable / Available; others -> Questionable) or
  `unlisted` selects the P(play) row. The ex-availability variant uses status `unknown`.
- Availability can only be fit and validated on 2021-22 onward. The P(play) status rows are
  fit on train report data available when the fit is run (re-run with the same procedure once
  the remaining months are parsed).

### 3.4 Team margin (step 4, OLS on train rows)

`margin = b0 + b1 (S_home - S_away) + b2 (b2b_away - b2b_home) + b3 (3in4_away - 3in4_home)`,
`S = sum(e/48 * r) + replacement minutes/48 * r_repl`. b2b = played the previous ET date;
3in4 = two games in the previous three dates and not b2b.

### 3.5 Totals (step 5, OLS on train rows)

`total = a0 + a1 T + a2 (b2b_home + b2b_away) + a3 (3in4_home + 3in4_away) + a4 playoff`,
`T = poss_hat * (2 eff_base + c + U_home + U_away) / 100`, `U = sum(e/48 * t) + replacement`.
`eff_base` / `pace_base` = league points per 100 possessions per team and possessions per game
over the current season's games before the date, padded with the most recent games to at least
150. `poss_hat = pace_base + d_home + d_away`, `d` = team pace deviation, recency-weighted
(0.9 per game) over its last 20 games, shrunk with 5 pseudo-games. Fatigue is symmetric (one
coefficient per tired team, either side); playoff and play-in games are the playoff regime.

### 3.6 Fail-closed reasons (either team -> the game is a NO_CALL for that variant)

NO_CALL_HISTORY_SHORT (team has < 5 box games in history), NO_CALL_BOX_GAP (more than 1 of the
team's last 5 current-season games without a box), NO_CALL_ROSTER_THIN (< 8 candidates with
minutes), plus the availability reasons above.

## 4. Variants (gated, at most 3 per market; here 2)

| id | market | spec | span with data |
|---|---|---|---|
| `exavail` | spread, total | sections 3.1-3.5 with status `unknown` for everyone | full span |
| `avail` | spread, total | `exavail` plus section 3.3; its own step 4/5 coefficients fit on train rows that have an `avail` row (2021-22 onward) | 2021-22 onward |

Each variant is judged on its own G1 result. No best-of selection across variants or markets;
every result is reported. If the injury reports for the validation window are not parsed when
G1 runs, `avail` is reported as not evaluated and run later with this exact spec.

## 5. Gate G1 (copied from the plan, section 6)

> Gate G1 (validation only, 60/20/20 on 2016-2026 with the new data): lambda > 0 with 95% CI
> excluding 0, and corr(model - close, actual - close) > 0 with CI excluding 0 on at least 700
> validation games. G1 against closing lines is a benchmark test only: the closes have no capture
> time and are never decision inputs. The serving lambda is fitted on decision-time lines (ESPN
> opening lines historically, forward Phase 0.1 captures going forward), and it must also come
> out above 0. A component that fails G1 ships with lambda = 0 and is not re-tuned on the same
> data.

Operational rule, identical to `outputs/nba/v10_harness/G1.md` and computed with
`nba_replay_harness.gate_g1` (date-cluster bootstrap, 2000 draws, seed 20261120): PASS iff
lambda fit on train has 95% CI > 0, lambda refit on validation has 95% CI > 0, corr on
validation has 95% CI > 0, and n validation >= 700, per market (spread, total).

- Gating `lambda_train`: all train rows of the variant with a closing line (2017-10-01 ..
  2025-11-02 for `exavail`; 2021-22 onward for `avail`), closes from the 2007-2026 archive.
  Also reported, not gating: lambda on the v9-matched train window (>= 2024-01-29).
- Validation closes: the harness's own `line_margin` / `line_total` from `harness/v9.jsonl`
  (the file v9 and v9.1 were scored on).
- A FAIL ships lambda = 0 in the artifact. Nothing is re-tuned on validation.

## 6. Non-gating outputs that touch validation (all listed here in advance)

1. Same validation games, player layer vs v9 / v9.1 / close: n, RMSE vs actual, bias
   (actual - model), close RMSE and bias, corr and lambda with CIs.
2. Exploratory, labelled as such: corr and lambda against the ESPN opening line (DraftKings,
   `selected.open_*_books`) on the validation games that have one, for the player layer, v9,
   v9.1 and the close itself.
3. Hindsight diagnostic (not a model, never a decision input): the same ratings with the priced
   game's actual minutes (`oracle`), its own step 4/5 coefficients fit on train, same metrics.

## 7. Known limitations at registration

- Ratings are game-level APM on box minutes, not stint-level RAPM (no lineup data used).
- No box scores for the 2024-25 and 2025-26 postseasons in the live captures; those games are
  NO_CALLs and their minutes never enter the state.
- Season openers use last season's rosters until players appear in a box (offseason moves are
  invisible to a box-only roster).
- ESPN opening lines are one book (DraftKings) and only from 2023.
- Injury rows were parsed through 2025-01 when this was written; the validation window needs
  2025-11 .. 2026-02.
