# NCAAB research protocol 261120 (pre-registration)

Machine-readable pin: `protocol_261120.json`. Canonical hash (sha256 of
`json.dumps(obj, sort_keys=True, separators=(",", ":"))`):

**`72a8a9ff216c4f84cc80acd9dda6fcdac14b6cafb9131bb05fcb210ce3677813`** (fixed 2026-11-21T04:05:41Z,
2026-11-20 23:05 ET), before any outcome was joined for any candidate of quick 261120-nip. `grader.py`
hard-codes this hash and refuses to run if the JSON changes. The JSON is binding; this page summarises it.
If they disagree, the JSON wins.

**Addendum v2** (`protocol_261120_addendum_v2.json`, sha256
`75241bc7885378f266ba66ce383f3b50fe58869e9b699ae90f0ddd9c8ead6317`) was written after the baseline read and
before any candidate, confirmation-window or CollegeBasketballData (CBBD) line was joined.

- **Confirmation window, widened:** season years 2015-2020 plus 2022, touched once. 2014 is burn-in and 2021 (COVID) is history only.
- **Line sources, now pluggable:** `espn_archive` serves development. CBBD becomes usable for confirmation once
  its rule is pinned, with the ESPN backfill as fallback.
- **D2_massey_pit:** withdrawn, with K unchanged.

The v1 table below shows the original confirmation seasons; the addendum supersedes them.

## Windows

| window | season years | status | use |
|---|---|---|---|
| development | 2023, 2024, 2025, 2026 | already read, contaminated (labels below) | leave-one-season-out; a pass only means ELIGIBLE FOR CONFIRMATION |
| confirmation (backcast) | 2018, 2019, 2020, 2022 | never scored; SEALED pending archive | one read per eligible candidate, plain 95% bound |
| history only | 2016, 2017, 2021 (COVID) | - | rating burn-in, never scored |
| forward final | 2027 (2026-27) | LOCKED | not before 2027-04-15, once, operator go-ahead |

Why this design: the current archive has no never-read season.

- **2024-25** was read for 12 spread, 11 total and 5 win-probability models.
- **2025-26** shaped v7's design (1m7). Its line-joined numbers were never produced; this lane's baseline is the first such read.
- **2022-23 and 2023-24** are v7's fit seasons.

Pooling all four seasons leave-one-season-out gives the largest n for a tiny market-relative signal. It charges
every earlier read through K, and it cannot by itself justify "beats v7".

A probe of ESPN's odds endpoint found multi-provider closing lines back to at least 2013-14, so a clean backcast
window is buildable. `espn_backfill_261120.py` fetches it: resumable, single worker, new files only. On top of
that, the 2026-27 season stays a sealed forward check.

Lock: season year >= 2027 or Eastern game date >= 2026-07-01 is refused before any score or line is loaded.

## Multiplicity

Grid size 9 (below). K = prior reads + 9:

| statistic | K |
|---|---|
| spread market-relative | 21 |
| total market-relative | 20 |
| moneyline | 14 |
| cover calibration | 14 |

Adjusted intervals use alpha = 0.025 / K per tail, as in ncaab-residual-rule-1. Each score call is logged in
`outputs/ncaab/research_261120/read_log.jsonl`. Only the first read of a grid name carries a verdict unless an
amendment registered the change.

## Candidate grid (implemented by other agents)

| name | family | what changes | primary vs v7 |
|---|---|---|---|
| A_ml_w0 | moneyline | p_home = pure margin distribution (Elo weight 0); w = 0.5 is the served v7 baseline | moneyline log-loss delta |
| B1_scale_linear_mu | scale | t8 scale = a + b * abs(mu) | cover log-loss delta at the closing line |
| B2_target_cap | mean | v7 combiner refit on margin clipped to +-25 | spread correlation delta |
| B3_early_scale_floor | scale | scale = max(v7 bucket scale, c0 * (1 + c1 * max(0, 1 - s/8))) | cover log-loss delta |
| C1_ppp_adjusted | mean | opponent-adjusted per-possession O/D x projected possessions | spread correlation delta |
| C2_ppp_into_v7 | mean | C1 margin as a third v7 combiner input | spread correlation delta |
| D1_prior_regression | mean | prior-season rating regressed to conference mean replaces tier prior | spread correlation delta |
| D2_massey_pit | mean | Kaggle Massey ordinal composite (RankingDayNum before game day) as combiner input | spread correlation delta |
| D3_torvik_pit | mean | Torvik adjusted efficiency (cbbdata, dated) as combiner input | spread correlation delta |

Returning minutes is not in the grid, because the archive has no player data. Adding it needs an amendment.

## Acceptance

- **Market-relative primary:** pooled over the held-out seasons.
  - Plain statistics: the OLS slope and the correlation of (actual - closing line) on (model - closing line), with day-cluster bootstrap CIs (4000 resamples, seed 17).
  - The ncaab-residual-rule-1 ridge machinery runs leave-one-season-out under this hash: alpha 1000, shrink 0.5, the same `window_stats`.
  - EDGE-ELIGIBLE needs both rule-1 K-adjusted lower bounds above 0 (slope and MSE delta).
- **"Beats v7" in development (DEV-ELIGIBLE):** every test is paired against served v7 on identical games.
  - Mean candidates: the correlation delta has its K-adjusted lower bound above 0, the RMSE delta is at most 0, and the 2025+2026 folds favour the candidate.
  - Scale candidates: the cover log-loss delta has its K-adjusted upper bound below 0, and moneyline Brier is not worse.
  - Moneyline candidates: the moneyline log-loss delta has its K-adjusted upper bound below 0.
  - In every case there is no leak flag and coverage is at least 99% of the cohort in each season.
  - "Beats v7" is only claimed after the confirmation window also passes, read once.
- **Secondary (reported, not gating unless named above):**
  - RMSE and bias by games played (0, 1-2, 3-7, 8+) and by abs(closing line) (<5, 5-10, 10-20, 20+).
  - Cover Brier and log loss at the closing line, with reliability bins.
  - Moneyline Brier and log loss, against the closing-spread-implied p_line = 1 - Phi((0.5 - L) / sigma). sigma is the SD of (margin - L) on the fold's training seasons.
  - Totals when submitted.
- **Leak flags:** RMSE below the closing line's on a held-out season; abs(market correlation) > 0.15; fit seasons that include the predicted season.

## Baselines

- **v7 served** (D1-vs-D1):
  - Constants from `models/ncaab_v7_serving.py`; leave-one-season-out predictions on 2023 and 2024.
  - Moneyline is the served 0.5 Elo blend.
  - Total is the served v6 total.
- **v6 as-of:** the v6 replay from the component archive.
- **Closing line.**

Reproduction gate: v7 on 2025 must reproduce the m5r numbers before the baseline is read.
