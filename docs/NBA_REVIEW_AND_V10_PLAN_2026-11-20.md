# NBA review and v10 overhaul plan (2026-11-20)

Status: PLAN. Nothing here changes serving. v9 spread/total and `nba-elo-trend-lr-v5` moneyline
keep serving and picking until a v10 component passes the gates below and is promoted through
`model_promotion promote`. Prices are implied probability (%) or cents; no American odds.

Reports and scripts from the four review workstreams are in `outputs/nba/review_2026-11-20/`:
`01_code_audit.md`, `02_loss_forensics.md`, `03_diagnostics.md` (+ `02_tables.md`),
`04_research.md`, and the replay/analysis scripts (`01_replay_v9.py`, `v9_retro*.py`, etc.).
Intermediate data files (pkl/jsonl) were not kept; rerun the scripts to regenerate them. The numbers below were spot-checked against the code
and the ledger where marked (verified).

## 1. Verdict

The NBA models have no information beyond the market line. Two independent replays of the
served v9 engine against real closing lines agree (availability adjustment off in both):
the diagnostics replay (2,811 games, 2024-01-29 to 2026-02-22, train + validation only) and the
code-audit replay (2,572 games, 2024-11 to 2026-06). The code-audit replay runs into the
2026-02-23+ NBA holdout, so its band hit rates and the B6 fatigue ablation include holdout games
and are exploratory.

| Check | Margin (spread) | Total |
|---|---|---|
| corr(model - close, actual - close) | -0.02 / -0.017 | +0.01 to -0.02 / -0.005 |
| Best shrink-to-market weight on model (fit on train) | -0.05 (i.e. 0) | +0.03 (i.e. 0) |
| Validation RMSE, model vs closing line | 14.59 vs 13.97 | 19.42 vs 18.18 |
| Cover/over Brier (coin flip = 0.2500) | 0.264-0.266 | 0.269 |
| Hit rate vs close inside the 5-10% Main band | 49.4% | 47.0% |

The model's gap to the line has an SD of about 4 points (margin) and 6-7 points (total). It is
pure noise, and the fixed sigmas (14 / 18) turn it into claimed 60-70% probabilities.
Moneyline v5 is calibrated (claimed 67.1% vs price 67.2%) but the best blend weight on it vs
the line-implied probability is 0.00 (Brier 0.2232 vs 0.2122 on validation).

This matches the literature: in Manner (2015) no team-level model beat even the opening spread
on squared error, and we found no public player-level model with a verifiable record against
NBA closing lines (`04_research.md`).

## 2. Live losses (quoted picks, ledgers.db, deduped Main over Flat)

480 of 512 deduped settled picks have a verified Polymarket quote; the 32 unquoted rows are all
moneyline and are excluded.

| Market | Record | ROI | Mean model prob | Mean price |
|---|---|---|---|---|
| Spread | 81-89 | -4.6% | 59.9% | 50.8% |
| Total | 79-93 | -8.0% | 57.8% | 51.2% |
| Moneyline | 92-46 | -2.5% | 67.1% | 67.2% |
| v9 spread / v9 total | 43-42 / 39-46 | +0.3% / -8.3% | | |
| v9 all markets incl. v9 moneyline | 92-89 | -2.3% | | |

- Brier, model minus market mid on the same picks, game-clustered bootstrap: pooled +0.0127
  [+0.0026, +0.0221], so the market is significantly better. v9 alone is indistinguishable
  from the market (spread +0.0099 [-0.013, +0.035], total -0.0023 [-0.020, +0.016]).
- Edge buckets are non-monotonic in every market. 12%+ spreads claimed 68.0% and hit 51.7%
  (30-28); 12%+ totals claimed 71.1% and hit 51.6%. Correlation of edge with (win - price):
  spreads +0.008, totals +0.094, ML +0.164.
- Edge-scaled sizing puts the largest stake on the largest fake edges: all top-12 losses were
  2U picks claiming 64-79% against 43-52% prices.
- v9 totals are 68% unders, and those unders went 25-33 (-15.8%). Games finished +3.9 above
  the v9 total (n=85, SE 1.85) and +3.7 above the Polymarket line (n=82). The diagnostics agent
  measured +3.0 above the line on its 85 served rows; either way about 2 SE, suggestive.
- CLV cannot be measured properly: quotes are only captured at forecast runs (last capture a
  median 65 min before tip), so on 246 of 461 picks the decision quote is also the last quote.
  On the 215 with a later capture, mean CLV is +0.58c. The market did not move against our picks;
  the losses are a calibration problem, not adverse selection.
- The 11-11..11-13 total rows whose stored probability was below 50% on the chosen side (4-10,
  -6.49u) were the cross-model calibrator, already fixed by `c5a6a5e9` (DEBUG 2026-11-12).

## 3. Bugs found

| # | Bug | Evidence | Effect |
|---|---|---|---|
| B1 | Early-season Over floor never fires: v9 counts lifetime `games_played`, not `current_season_games_played` (verified) | `models/nba_v9.py:275`; live rows read 220-229 | Overtime bump at full weight from opening night; 9% thin-sample Over floor disabled |
| B2 | Same floor also reads the wrong key: gate reads `model_input_snapshot["feature_basis"]`, snapshot stores `"features"` (verified) | `cli/forecast.py:2509` vs `scalar_artifact_replay.py:287` | Independently disables the floor |
| B3 | Clippers road spreads never priced: "LA Clippers" vs "Los Angeles Clippers" fails `_team_matches` | `cli/forecast.py:1769-1774`, `learned_forward.py:1096`; 11-10 and 11-19 had ML/total rows but no spread row | Missing picks |
| B4 | Latent sign error in the market-blend hook: `market_home_margin = -spread_away_line`, but the cover formula treats `spread_away_line` as the home-margin line (verified) | `models/nba_v9.py:268` vs `:345`; also `nba_v7.py:243` | Inert at weight 0; turning on shrink-to-market as written pushes away from the market |
| B5 | Totals league baseline is the unweighted mean of all history since 2024 (verified) | `features/trends.py:187` | Totals lag scoring drift: actual - model +2.67 (2024-25), +3.83 (2025-26), +6 to +8 in Oct-Dec |
| B6 | One-sided fatigue penalties (b2b, 3-in-4) at full weight on totals, but only through the 0.30 trend weight on margins | `03_diagnostics.md`, `01_code_audit.md` | Turning off fatigue + OT bump cuts the total gap to close from 2.0 to 0.5 pts. B5 and B6 are both measured causes of the low bias; ablate them together |
| B7 | Playoff totals priced like regular season | model too high by 13 pts (n=166), line off by 0.2 | Bad playoff totals |
| B8 | v9 qualification metrics cannot be reproduced: holdout spread Brier 0.1948 is unreachable against real lines (no sigma gets below ~0.251); no script fits v9 weights; no promotion record | `03_diagnostics.md`; `scripts/test_combo_features_v7.py` scores spreads at line 0 | v9 champion evidence is invalid |
| B9 | Availability adjustment is an unfitted heuristic: at least 1.1 pts per listed player, `return_date` ignored, exact-match tier names miss suffixes ("Jimmy Butler III"), full weight, no snapshot age limit (fails open) | `features/nba_player_availability.py`; 2026-11-19 tests: served adjustment raised live margin MAE 11.78 -> 12.51, fitted weight ~0.4 | Adds noise; also cuts ~1.5 pts off live totals |
| B10 | v9/v7 ledger rows store neither the availability component nor the pre-adjustment margin | `02_loss_forensics.md` §5 | Cannot attribute losses |
| B11 | ML v5 qualification ("73% hit, +236u") scored favourites as if they paid even money; holdout calibration slope 1.83 | `01_code_audit.md` | ML evidence overstated |
| minor | Fixed sigma (replay residual SD 14.6 / 19.0); Elo K=20 with an NFL-style MOV multiplier (effective K ~50 vs ~23 typical for NBA); mixed `-0500`/`Z` start times; fees not in edge; replay engine hash points at the wrong file | `01_code_audit.md` | |

Checked and OK: spread sign convention in the cover formula, over/under mapping, PIT cutoffs
(ET midnight; quotes and injuries filtered to decision time), ML train/serve parity, exhibition
filtering, home-court constant (served 1.48, regression 1.73, close overprices home by 0.2-0.6),
pushes, one settled row per event.

## 4. Root causes

1. **No market anchor.** Edge is raw model probability minus ask. The model is a weaker
   forecaster than the line, so every disagreement is mostly model error.
2. **Overconfident conversion.** A 4-point noise gap at sigma 14 becomes a 60%+ claim; sizing
   then scales stake with the noise.
3. **Thin information set.** Team-level Elo + points trend + rest from 2.8 seasons of history
   (NBA history starts 2024-01-01). Nothing the market does not already have, and less.
4. **Totals structural bias.** All-history baseline (B5) plus one-sided fatigue (B6) make the
   model low; it bets unders into a rising scoring environment.
5. **No measurement of what matters.** No pre-tip close capture, no stored adjustment
   components, qualification metrics not computed against real lines.

## 5. What a realistic v10 can and cannot do

Public evidence says NBA closing sides are efficient and no player model has a documented
record against them. The realistic targets, in order of credibility:

- **A. Stop paying for noise.** Anchor every price to the market and only deviate by a fitted
  weight. If the fitted weight is zero, the model correctly says it has no edge.
- **B. Polymarket vs sharp consensus.** Polymarket's NBA book is thin (median pre-game bid-ask
  spread about 3.9c). Where the devigged sportsbook consensus disagrees with the Polymarket ask
  by more than the cost of crossing the spread, that is an edge that needs no forecasting skill.
- **C. Windows with documented inefficiency.** Early-season totals vs scoring drift (Baryla
  2007: 56.7% against early closing totals; 2026-27 scoring is ~113.7 per team vs 102.7 in
  2015-16); injury-news latency between an official injury report publication and the
  Polymarket move.
- **D. Player-level availability.** Minutes-weighted player ratings with minutes
  redistribution and replacement level (about -2.0 per 100 possessions) instead of flat tiers.
  This is how books price stars (1.5-5.5 pts depending on depth). Expect it to make the model
  less wrong, not to beat the close on its own.

Break-even math: a pick at a 51-52c ask needs more than about 2-3 points of probability edge
over the mid just to clear the spread. Proving a 2-point edge from W/L takes about 3,900 bets;
CLV can show a 1c mean in about 60-100 bets (per-bet CLV SD 3-4c, to be measured).

## 6. Phases and gates

Each phase is its own GSD task with a new version id; nothing is edited in place. The
shadow-feature pattern, literal train/serve parity and `observed_at_utc` provenance apply
throughout.

### Phase 0: measurement and data (first, blocks everything else)

1. **Pre-tip and consensus capture.** Capture Polymarket NBA quotes at T-10 min, and the
   sportsbook consensus (ESPN odds) at every forecast run and at T-10 min, into the quote store
   with observed times; keep the post-game ESPN close too. This gives real CLV against a liquid
   benchmark for every pick from that date on, decision-time anchors for Phase 2, and the live
   consensus feed track T-B needs.
2. **Store components on every NBA row.** Pre-adjustment margin/total, availability component,
   rest/b2b flags, market mid at decision time (fixes B10).
3. **Historical closing lines 2016-2024.** Extend `data/odds/nba/historical_closing_lines/`
   (today 2024-2026, 3,481 games) via the ESPN core odds API (checked: a 2017 game returns 13
   books with spread and total). Use for benchmarking and anchoring only; these closes have no
   capture timestamps, so they are never decision inputs.
4. **Box score / play-by-play history 2016+.** Backfill via `nba_api` or ESPN so player ratings
   can be fit as-of each date (current history starts 2024-01-01).
5. **PIT injury history.** Archive the official NBA injury report PDFs
   (`ak-static.cms.nba.com/referee/injury/Injury-Report_<date>_<time>.pdf`, back to 2021-22;
   the filename time is the observed time). Box-score-inferred absences are hindsight and cannot
   validate a player model, so the player layer can only be validated point-in-time on
   2021-22 onward, not on the full 2016-2026 window.

Gate: datasets exist with provenance files, and a row-count / date-coverage check passes.
Backfills write under `data/` and need operator go-ahead.

### Phase 1: v9 plumbing fixes (can run in parallel with Phase 0)

- B2 (key), B3 (Clippers matching), B4 (blend sign), start-time normalisation: serving
  plumbing, fixed with regression tests that fail before the fix.
- B1, B5, B6, B7 change model output, so they go into a challenger `nba-*-margin-v9.1`
  measured against v9 on the replay harness, not edited into v9.
- Rebuild the replay harness (`01_replay_v9.py` from the scratchpad) as a repo script that
  scores spread/total qualification against real lines, so B8 cannot recur.

Expectation: v9.1 removes the totals bias (validation bias +3.34 -> -1.25 with a
season-to-date baseline) but RMSE barely moves (19.42 -> 19.10) and correlation stays at 0.
Bias fixes alone do not create edge.

### Phase 2: market-anchored scaffold (`nba-anchor-v10`)

- Anchor: devigged sportsbook consensus where available, else the Polymarket mid.
- Margin and total space: `pred = line + lambda * (model - line)`; lambda per market is fitted on
  validation with a bootstrap CI and clipped at 0. Probability from a sigma fitted on residuals
  around the line, refit each season.
- Edge = blended probability minus executable ask, net of fees. Size on the blended edge.
- With lambda = 0 this reproduces the market and emits almost no picks. That is the honest
  output, not a gate. Whether v9 keeps picking alongside it is an operator decision (section 8).

Gate G1 (validation only, 60/20/20 on 2016-2026 with the new data): lambda > 0 with 95% CI
excluding 0, and corr(model - close, actual - close) > 0 with CI excluding 0 on at least 700
validation games. G1 against closing lines is a benchmark test only: the closes have no capture
time and are never decision inputs. The serving lambda is fitted on decision-time lines (ESPN
opening lines historically, forward Phase 0.1 captures going forward), and it must also come out
above 0. A component that fails G1 ships with lambda = 0 and is not re-tuned on the same data.
Note that every NBA game before 2026-11-20 has already been looked at in some form, so the last
20% of a 2016-2026 split is not a clean holdout; the clean test is Phase 5.

### Phase 3: player layer (`nba-player-v10`, shadow feature)

1. Player ratings as of each game date: box-score prior plus a ridge RAPM fit on our own
   play-by-play/box history (no ratings fetched today, since those include later games).
2. Minutes projection: recent minutes, starter status, rest, blowout adjustment.
3. Availability through minutes redistribution: an out player's minutes go to the rotation at
   their ratings, with replacement level for depth. Statuses come from the official report (PIT).
4. Team strength = sum of minutes x rating, plus home court and rest, fitted on train.
5. Totals: possessions x efficiency with a current-season rolling league baseline as of the
   decision date, symmetric fitted fatigue, and a playoff regime.

Its output feeds Phase 2 as `model`; it only influences prices through a lambda that passed G1.

### Phase 4: targeted edge tracks (each pre-registered separately)

- **T-B, Polymarket vs consensus:** pick when devigged consensus minus the Polymarket ask is
  greater than a threshold fixed on validation. Needs live consensus capture (Phase 0.1).
- **T-C1, early-season totals:** first N games of the season, rolling-baseline total vs line.
- **T-C2, injury latency:** decisions inside X minutes after an official report changes a
  rated player's status, before the Polymarket mid moves.

### Phase 5: forward shadow and promotion

- Fresh forward-only holdout: every game after the v10 freeze date. The existing 2026-02-23+
  NBA holdout has been touched twice (availability ablations) and is exploratory only.
- Gate G2 (pre-registered, games clustered across markets): at least 150 forward shadow picks,
  mean CLV against the devigged sportsbook consensus at T-10 min of at least +1.0c with 95% CI
  excluding 0, CLV above v9's on the same games, and Brier no worse than the Polymarket mid on the
  same picks. Polymarket-to-Polymarket CLV is not enough: v9 picks already average +0.58c
  (totals +1.10c) against later Polymarket quotes while losing, so that benchmark passes a
  no-skill model.
- Kill: if G2 fails at 300 picks, stop that component; do not extend the window to fish for
  significance.
- Promotion only through `python -m model_prediction.model_promotion promote` with the
  evidence attached. Champion change per market, rollback to v9 recorded.

## 7. Suggested order

1. Phase 0.1-0.2 (CLV capture, stored components): days, and they make every later
   measurement possible.
2. Phase 1 plumbing fixes (B2, B3, B4, timestamps).
3. Phase 0.3-0.5 backfills (with go-ahead), then v9.1 challenger.
4. Phase 2 scaffold and G1 on the existing team-level model, which should come out at
   lambda near 0 and confirms the harness.
5. Track T-B (cheapest real edge candidate), then Phase 3 player layer, then T-C tracks.
6. Phase 5 forward shadow from the freeze date.

## 8. Operator decisions (answered 2026-11-20)

1. v9.1: build as a challenger with a new version id; v9's engine file stays untouched
   (its source hash is verified at replay).
2. v9 keeps edge-based sizing until v10 replaces it.
3. Free data sources only (no paid data was authorized).
4. Backfills approved. They write only under `data/research/nba_v10/`, never into the live
   history files v9 reads (its baseline and Elo are computed from all loaded history).
5. Fewer Main picks are acceptable if they win: v10 may replace v9 in Main once it passes G2.
   **The Flat ledger keeps calling everything**: v10 always emits a side and probability for
   every priced game, and only Main eligibility depends on the blended edge.
6. Track T-B (Polymarket vs sportsbook consensus) is in scope.

## 9. Pinned test parameters (fixed before any test runs)

- **T-B:** a pick fires when the devigged sportsbook consensus probability for a side minus the
  Polymarket ask for that side is at least 3.0 percentage points (about a 2c half-spread plus a
  1-point margin). Evaluated forward only, from the first captured slate.
- **T-C1 early-season totals:** games where either team has played fewer than 15 current-season
  games.
- **T-C2 injury latency:** decisions within 30 minutes after an official injury report changes
  the status of a player averaging at least 20 minutes, measured against the Polymarket mid
  before and after.
- **G1 / G2:** as written in section 6; not re-tuned after results are seen.
- **G2 scoring rules (pinned 2026-11-21, before any v10 pick settled;
  `src/model_prediction/nba_v10_g2.py`, `scripts/nba_v10/g2_report.py`):**
  - G2 pick: while `nba-anchor-v10`'s `main_min_edge` is null (lambda = 0), a pick is a
    consensus-anchored row where consensus probability minus Polymarket ask is at least 3.0 points
    (the T-B rule above). Mid-anchored rows never count. If `main_min_edge` is ever set, the rule
    becomes net edge at or above it.
  - T-10 benchmark: the latest ESPN capture with `decision_time < observed_at <= tip - 10 min`.
    It is excluded as stale if older than tip - 40 min.
  - Scoring: picks are scored only after tip. v9 pairs only with a row from the same run (decisions
    at most 15 min apart).
  - Criteria 3 and 4: criterion 3 passes on a mean paired v10 - v9 CLV above 0. Criterion 4 uses
    the benchmark-scored picks.
  - Verdicts and confidence intervals: PENDING below 150 picks; FAIL at 150-299; KILL at 300+.
    CIs are a cluster bootstrap by game.

## 10. Progress (2026-11-21)

- **Phase 0.1 capture (`quick-261120-npc`, `b1e009a8`):** the `nba-pretip-capture` worker writes
  ESPN odds for the next 12 hours of slate and Polymarket quotes inside a 25-minute window. ESPN
  exposes one NBA book (DraftKings), so "consensus" is a single-book devig. The Windows task is
  defined in `scripts/register_windows_tasks.ps1` but not registered yet (S4U needs an elevated
  shell).
- **Plumbing (`quick-261120-npl`, `quick-261121-nw1`):** B2 Over-floor key (the 9% floor now
  fires early-season), B3 Clippers alias in forecast and settlement CLV, B10 stored components,
  start times compared as instants (forecast, system_health). v9 rows unchanged on replay.
- **Harness, v9.1, anchor (`quick-261120-nh1/nv9/na0`):** `scripts/nba_v10/replay_harness.py`
  reproduces the review numbers and replaces v9's unreproducible qualification metrics. v9.1
  keeps B5 (300-game rolling baseline), B6 for totals only, the B7 playoff offset and the B4 sign
  fix; B1 was dropped. Total bias +3.34 -> -0.34, RMSE 19.42 -> 19.03; correlation with the line
  still about 0.
- **Gate G1: FAIL** for v9 and v9.1 in both markets (validation n=762; lambda CIs straddle 0,
  corr -0.015 to -0.030). `nba-anchor-v10` ships lambda = 0: its price is the anchor, and its only
  possible edge is T-B (consensus minus Polymarket ask >= 3.0 points).
- **Wiring (`quick-261121-nw1`):** `nba-anchor-v10` logs one Flat row per NBA spread and total
  per game after v9; not on Main or any buyer whitelist. The consensus anchor must be at most 90
  minutes old, otherwise the Polymarket mid of a two-sided book no wider than 10 points is used.
  Moneyline is not priced by the anchor module.
- **In flight:** Phase 0.3-0.5 backfills into `data/research/nba_v10/`. The Phase 3 player layer
  starts on that data and reaches serving only through a lambda that passes G1.
- **G2 clock:** starts once the capture task is registered; at least 150 forward v10 picks.
