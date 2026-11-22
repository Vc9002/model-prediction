# Addendum v4 to NCAAB research protocol 261120: player features for the spread (quick 261121-hmc, lane P)

Machine-readable pin: `protocol_261120_addendum_v4.json`. Canonical sha256 (as `grader.canonical_sha256`):

**`ccb107fc6f0b5b525337f8e48e7c9717b27afe824b78f7ea6c354f354618490e`**

Written 2026-11-21 19:10 ET, before any player feature was joined to a margin, line or outcome. `grader_v4.py`
imports `grader.py` (unchanged) and hard-codes this hash. The JSON is binding; this page summarises it.

## Why

v1's D1 entry left returning minutes out for lack of player data and asked for an amendment if a lane supplied
it. Lane P built 62,078 point-in-time feature rows from ESPN player boxes (2022-2026, 100% fetched, a box-quality
gate against broken boxes). This addendum registers two spread candidates on them.

## Grid (K spread = 25, cover = 16, moneyline = 16)

| name | form | free parameters |
|---|---|---|
| P1_player_availability | v7 mean + b1 x (home - away share of rotation minutes that played the previous game) + b2 x (home - away rotation minutes share out 3+ games) | b1, b2 (OLS, training folds) |
| P2_returning_production_early | v7 mean + max(0, 1 - games/10) x (c1 x returning game-score share diff + c2 x prior-season game score of the current roster diff) | c1, c2 (OLS, training folds) |

Both keep v7's scale. A null input gives adjustment 0, so the game is predicted by v7 and coverage holds. The
verdict is on the full cohort; the subset with a non-zero adjustment is reported without a verdict.

## Rules worth knowing

- Season 2022 is a sealed confirmation season: its rows are never fit or scored in development.
- The feature table, its builder and the backfill script are pinned by sha256; a rebuilt table is a new addendum.
- Transfers are joined on ESPN athlete id only; about 7% get a new id and count as newcomers (disclosed).
- Acceptance is v1's mean-family rule at K = 25.
- Confirmation (only if DEV_ELIGIBLE): fetch 2014-2021 boxes with the pinned scripts, keep seasons where at least
  85% of D1 team-games pass the box gate (decided before the read), coefficients frozen from 2023-2026, one read.
  Fewer than 3 usable seasons means CONFIRMATION_UNAVAILABLE and forward-only judgement.
- Live moneyline is now v7m; candidates still submit the v7 blend moneyline so the secondaries compare like with
  like against the pinned baseline.
- Reproduction gate (v7 spread block at K 21), then a shuffled plumbing run, then one logged read per name.
