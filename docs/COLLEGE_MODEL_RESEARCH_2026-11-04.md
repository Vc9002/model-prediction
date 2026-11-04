# College model audit and settled-slate replay — 2026-11-04

## Decision

Do not promote either probability challenger. The NCAAB margin calibration fails its
chronological validation and holdout checks. The NCAAF normal-variance replacement
worsens total Brier score on replayed settled contracts. The live models remain the
same versions; football price integrity and artifact lineage were corrected.

## External research considered

- [hoopR's college basketball ratings](https://github.com/sportsdataverse/hoopR-mbb-data/blob/main/docs/models/ratings.md)
  use opponent-adjusted offense, defense, and tempo. This supports investigating
  possession and schedule adjustment, but no unverified external rating was joined
  retrospectively into settled calls.
- [Walsh and Joshi's calibration study](https://arxiv.org/abs/2303.06021)
  argues that calibration matters more than classification accuracy for wagering.
  Accordingly this audit evaluates Brier and log loss alongside units.
- A [CFB practitioner thread](https://www.reddit.com/r/algobetting/comments/1ncrr6n/building_a_college_football_betting_model/)
  describes PPA, success rate, explosiveness, tempo, and market differences. The
  author later acknowledged a wrong target variable; the discussion is a warning
  to verify outcomes and prices, not evidence of a profitable transferable model.
- [CBB-predictor](https://github.com/coreyw7/CBB-predictor) combines Elo with
  adjusted efficiency and tempo and compares projections to closing lines. It is
  an implementation idea, not a source of point-in-time features for our ledger.

## Reproducible evidence

`scripts/replay_college_challengers.py` and
`scripts/compare_college_settled_full_slate.py` produced the paired replay and
full-slate report. The canonical SQLite ledger was queried read-only. The
full-slate policy considers every settled row and retains a recorded contract
and its original stake only if the research probability exceeds its recorded
ask. Rows without a reproducible probability retain the incumbent probability
and are counted as fallback. No opposite side or missing historical price is
invented.

| Sport / tier | Settled | Replayed | Fallback | Incumbent P&L | Research policy calls | Research policy P&L |
|---|---:|---:|---:|---:|---:|---:|
| NCAAB Flat | 418 | 418 | 0 | -29.5550u | 281 | -18.6772u |
| NCAAB Main | 367 | 367 | 0 | -26.0668u | 264 | -19.2154u |
| NCAAF Flat | 395 | 371 | 24 | +8.0296u | 233 | +15.4269u |
| NCAAF Main | 1,014 | 1,011 | 3 | -58.4141u | 800 | -66.2204u |

This is a **retrospective decision-layer diagnostic**, not a newly deployed
model's realized P&L. The policy abstains on some incumbent calls; the columns
are therefore not the same set of wagers. Flat and Main overlap. NCAAF Flat's
24 earliest rows have no pregame inference snapshot; the Main ledger contains
snapshots for all but three of its rows. Counts and market-level results are in
`outputs/research/college_full_slate_2026-11-04.json`.

The NCAAB research calibration improves Brier on the 418 settled Flat calls,
but margin MAE worsens on chronological validation (8.86 to 10.69) and holdout
(9.03 to 13.52). This is a failed generalization check. The NCAAF candidate
total Brier worsens from 0.26429 to 0.27258 across 117 replayed Flat totals.
Its apparent Flat units gain therefore does not justify serving it. All NCAAF
snapshot records mark historical source observation times unverified.

A second ridge ablation using Elo margin, trend margin, and their combination
also failed to beat the incumbent's later-date margin MAE (9.03). The best
trend-only candidate tested here reached 10.94 on those dates. Early-season
fit does not transfer cleanly into later conference/tournament play; a larger
coefficient or an Elo-only substitution is not a defensible repair.

## Serving repairs and next research priority

Football ESPN fallback markets now require an actual quote on both sides. A
missing spread, total, or moneyline side produces no priced contract. The
football logger records the exact serving artifact hash instead of a digest
of the model version string. The prior version remains in Git for rollback.

The next research priority is NCAAB moneyline and spread projection with
opponent-adjusted efficiency and a verified pregame feature store. NCAAB v3
already uses Elo, blending Elo win and margin signals with recent trends; Elo
alone is not the missing fix. NCAAF needs complete quote and feature snapshots
before an all-row challenger can be claimed. Any new fit must beat market
baseline calibration and loss on chronological data before promotion.
