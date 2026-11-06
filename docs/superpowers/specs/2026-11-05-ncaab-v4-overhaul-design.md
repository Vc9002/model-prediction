# NCAAB Totals/Spread Model Overhaul: v3 → v4

Status: research candidate. Does **not** flip `active_production_version`
or `status` in `config/model.yaml` — promotion is a separate, explicit
decision gated on the walk-forward evidence this spec produces, per
`docs/ROADMAP.md`'s promotion rule.

## Motivation

`ncaab-tempo-v3` runs on the generic, shared `BasketballModel` class
(`src/model_prediction/models/basketball.py`), the same class NBA/WNBA/
Euroleague/NBL/Darts also run through, with almost none of the
guardrails NBA's dedicated v6/v7 models (`nba_spread_v6.py`,
`nba_total_v6.py`, `nba_v7.py`) already have. Confirmed live incident
(2026-11-05): NCAAB totals went 2-12 on `under` picks — walk-forward
backtest on the real 2025-26→2026-27 season boundary
(`data/historical/ncaab_games_all.jsonl`) showed the shared class's
`sample` variable for confidence-width used `games_played` (lifetime,
cross-season) instead of `current_season_games_played`, producing
near-max confidence (uncertainty ≈ 0.0355) on games where a team had
just 1 current-season game, vs. the correct ≈0.195 (near-min
confidence). Total-prediction MAE on those 10 games was 20.15 vs. 14.17
for in-season control — a 41% worse point estimate exactly where the
model was most falsely confident. That one-line fix is already applied
(`basketball.py:174-179`, uncommitted pending this broader overhaul).

NCAAB (≈360 D-I teams) also has structurally different dynamics than
NBA (30 teams) that the generic shared class was never tuned for:
larger pace/possession variance team-to-team, choppier non-conference
scheduling, much faster year-over-year roster turnover, and far more
frequent high-major/low-major mismatches.

## Scope

**In scope:** a dedicated NCAAB spread model and dedicated NCAAB total
model, mirroring NBA's v6 split, built on the same `TrendEngine`/Elo
primitives already shared across all basketball models (no new
feature-computation machinery).

**Out of scope (explicitly deferred to a future v5 candidate):**
conference-strength adjustment, travel-distance features, tournament
(postseason) specific handling beyond the existing `isolate_postseason`
flag, and any change to NBA/WNBA/Euroleague/NBL — this overhaul touches
only NCAAB-specific code paths.

**Priority:** calibration/overconfidence fixes over raw accuracy gains
where they trade off. This is live paper-trading with Kelly-edge sizing;
a miscalibrated-but-wrong-direction model burns units faster than a
slightly-less-accurate-but-honest one.

## Architecture

Two new files, following the NBA v6 pattern exactly:

- `src/model_prediction/models/ncaab_spread_v4.py` — `NCAABSpreadModelV4`
  (`predict_spreads`), mirrors `nba_spread_v6.py`.
- `src/model_prediction/models/ncaab_total_v4.py` — `NCAABTotalModelV4`
  (`predict_totals`), mirrors `nba_total_v6.py`.

Both take `history: Sequence[GameRecord]` and
`upcoming: Sequence[UpcomingGame]`, build a `TrendEngine` internally
(same as NBA v6), and return `GamePrediction` objects — no change to the
`GamePrediction`/`GamePredictionBase` contract or to how
`learned_forward.py`/`college_basketball_forward.py` consume predictions
beyond swapping which model class they instantiate for the v4 version
string (`ncaab-spread-v4`, `ncaab-total-v4`).

v3 (`ncaab_v3_model()` in `basketball_leagues.py`) is left untouched and
stays the live `active_production_version` until promotion.

## Calibration fixes (priority 1)

1. **Current-season sample gating, native.** Both v4 classes compute
   their own `sample = min(away.current_season_games_played,
   home.current_season_games_played)` — not inherited from the shared
   class's (now-fixed) `sample` line, so this can never regress silently
   if the shared class changes again.
2. **Dynamic volatility widens the actual distribution, not just
   metadata.** When `sample` is thin, widen `margin_sd`/`total_sd`
   themselves (same mechanism as NBA v6's `dynamic_volatility`) so
   `eligibility.py`'s edge/confidence gate sees a genuinely wider
   normal distribution on thin-sample games — not just a cosmetically
   higher `uncertainty` field that nothing except dashboard display
   reads.
3. **NCAAB-specific offseason retention.** NBA v6 uses
   `offseason_gap_days=60, offseason_retention=0.40`. NCAAB rosters turn
   over far more per year (one-and-dones, grad transfers, portal
   movement) than NBA's — retained prior-season signal should decay
   faster. Walk-forward-fit a new `offseason_retention` for NCAAB rather
   than reusing NBA's number; starting hypothesis is lower retention
   (less carried-over weight) and/or a shorter effective half-life
   before full current-season weight dominates.

## Accuracy features (priority 2, layered after calibration holds)

4. `dynamic_matchup_pace` + `possession_model`: NCAAB possession counts
   vary more team-to-team than NBA's (shot-clock/style mismatches between
   high-major and low-major programs are far starker than any NBA
   mismatch).
5. Rest/fatigue effects recalibrated for college scheduling (not NBA's
   B2B numbers reused verbatim) — NCAAB's non-conference neutral-site
   and multi-team-event scheduling produces different fatigue patterns
   than NBA's back-to-backs.
6. `use_logistic_moneyline` + keep `coherent_moneyline` (already on in
   v3) with a fit logistic scale, mirroring `nba_v7.py`'s approach.

## Validation gate

Per `docs/ARCHITECTURE.md`'s validation contract and this repo's
promotion rules: 60/20/20 chronological split over
`data/historical/ncaab_games_all.jsonl` using
`validation.py::build_walk_forward_rows`/`chronological_split` (reused,
not reimplemented). Compare v4 against incumbent v3 on:

- Brier score and calibration (both totals and spreads)
- Locked-holdout accuracy (the 20% never touched until the final check)
- Specifically: the early-season bucket (current-season games ≤3,
  lifetime games ≥15) identified in this session's walk-forward, to
  confirm the calibration fix actually narrows the MAE/overconfidence
  gap found there (20.15 vs 14.17 MAE, 0.0355 vs 0.195 uncertainty)

Report real numbers, not just "it correlates." `config/model.yaml`'s
`NCAAB.active_research_version` can point at `ncaab-tempo-v4` once it
exists; `active_production_version` stays `ncaab-tempo-v3` until a
separate, explicit promotion decision reviews this evidence.

### 2026-11-05 — walk-forward comparison result (`scripts/ncaab_v4_walkforward.py`)

Day-by-day walk-forward over the full `data/historical/ncaab_games_all.jsonl`
history (6,538 completed games), grading every game on or after
`GRADE_FROM = 2026-09-01` with the TrendEngine rebuilt from all strictly-prior
history at each day. Printed output, verbatim:

```
total completed games: 6538

graded games: 238
v3: total MAE=14.42  moneyline Brier=0.1358
v4: total MAE=14.58  moneyline Brier=0.1233
```

v4 (`NCAABSpreadModelV4` + `NCAABTotalModelV4`) shows a small moneyline Brier
improvement over incumbent v3 (0.1233 vs 0.1358) but a slightly worse total
MAE (14.58 vs 14.42) on this 238-game graded sample. This is the raw
v3-vs-v4 comparison only, not the full 60/20/20 chronological-split /
locked-holdout gate described above — that remains a separate, more
rigorous validation step before any promotion decision. No config changes
were made as part of recording this result.

Note: `GRADE_FROM = 2026-09-01` does not grade a true in-season control
group — the historical data file has a gap from April to November, so the
238 graded games above are all from early November 2026-27 season-start
(2026-11-01 through 2026-11-05). That window is exactly the thin-current-
season-sample regime C1 (season-opener confidence bug) addresses.

### 2026-11-05 (later) — re-run after the C1 season-opener fix

Finding C1 (final whole-branch review) found that `current_season_games_played`
can't see the gap between a team's last historical game and the game being
predicted now, so true season openers (0 games played this season) were
getting near-MAXIMUM confidence instead of near-minimum. Both v4 models were
fixed to override the sample to 0 in that case (see `ncaab_spread_v4.py`'s
and `ncaab_total_v4.py`'s `_effective_current_season_games` helper). Re-ran
`scripts/ncaab_v4_walkforward.py` verbatim after the fix:

```
total completed games: 6538

graded games: 238
v3: total MAE=14.42  moneyline Brier=0.1358
v4: total MAE=14.58  moneyline Brier=0.1233
```

Numbers are unchanged from the pre-fix run above — this script's two graded
metrics (`expected_total`'s MAE and moneyline Brier, which uses the logistic
moneyline path) don't depend on `effective_margin_sd`/`effective_total_sd` or
`uncertainty`, so C1's fix (which only widens those) doesn't move them. The
fix is nonetheless real and load-bearing for anything downstream that reads
`uncertainty` or the spread/total `sd` (e.g. `eligibility.py`'s edge/
confidence gate) on a season opener — see the new regression tests
(`test_ncaab_spread_v4_season_opener_gets_max_uncertainty`,
`test_ncaab_total_v4_season_opener_gets_max_uncertainty`) in
`tests/test_ncaab_v4_walkforward.py`.

### 2026-11-06 — full-season walk-forward (`scripts/ncaab_v4_full_season_walkforward.py`)

The two runs above only graded 238 games (the early-November 2026-27
season-start window) — not a statistically meaningful sample. Reran over
every settled game in `data/historical/ncaab_games_all.jsonl`
(2025-11-03 → 2026-11-05) with only a 14-day warmup, point-in-time
correct (each game predicted from strictly-prior history, history
extended after grading). Printed output, verbatim:

```
total completed games: 6538
grading from 2025-11-17 onward (14-day warmup)

graded games: 5712 (took 413s)
v3: total MAE=14.55  moneyline Brier=0.1945
v4: total MAE=14.11  moneyline Brier=0.1928

By current-season sample bucket (total MAE only):
  early_season (n=611): v3 MAE=14.44  v4 MAE=14.44
  in_season (n=5101): v3 MAE=14.56  v4 MAE=14.07
```

v4 beats incumbent v3 on both total MAE (14.11 vs 14.55) and moneyline
Brier (0.1928 vs 0.1945) across the full season — a much larger, more
credible sample than the earlier 238-game window. The early-season bucket
is identical between v3 and v4 (14.44 vs 14.44), exactly as expected: the
calibration fix only widens `uncertainty`/`sd`, it never touches the point
estimate (`expected_total`), so it cannot and does not move MAE in that
bucket. The real gain is entirely in the in-season bucket (14.56 → 14.07,
n=5101) — attributable to v4's other tuning (NCAAB-specific rest/schedule-
density thresholds, logistic moneyline, `elo_home_advantage=55.0`), not the
sample-size/calibration fix itself.

This is still not the full 60/20/20 chronological-split + locked-holdout
gate described above (no train/validation/holdout partitioning, no
calibration-specific metric beyond MAE/Brier, spread market untested since
the walk-forward script fixes `total_line=150.0` and never passes a
spread line). It is, however, a real, large-sample, point-in-time-correct
result in v4's favor on both markets measured — a meaningfully stronger
signal than the earlier window. Promotion to `active_production_version`
is still a separate, explicit decision this result alone does not make;
the full gate (spread market, proper chronological split, calibration
curves) remains the recommended next step before that decision.

## Testing

- Unit tests mirroring `test_nba_v6_walkforward.py`'s structure:
  artifact-hash integrity (if/when a JSON artifact is produced),
  standalone spread/total prediction sanity checks, and a dedicated
  regression test pinning the current-season-games-played gating
  behavior (the bug this whole overhaul started from) so it can never
  silently regress again.
- `ruff check` clean; no monolithic `pytest tests/` run required for
  this (per this project's fast-iteration directive) — run only the new
  NCAAB v4 test file plus the existing basketball/NCAAB test files
  touched.
