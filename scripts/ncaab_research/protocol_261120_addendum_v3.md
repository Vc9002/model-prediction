# Addendum v3 to NCAAB research protocol 261120: totals (quick 261121-hmc, lane T)

Machine-readable pin: `protocol_261120_addendum_v3.json`. Canonical sha256 (as `grader.canonical_sha256`):

**`d098238e340f192eb61339a9f936a96e84f49fc00b5e944b9c312dc323b1b337`**

Written 2026-11-21 ~18:00Z (13:00 ET), before any total, margin or line was joined to an outcome by this lane.
`grader_v3.py` imports `grader.py` (unchanged, byte-identical) and hard-codes this hash. The JSON is binding;
this page summarises it.

## Why an addendum

Protocol v1 and addendum v2 grade margins against served v7. Totals were only a secondary block. v3 registers a
totals grid with its own verdict against the **served v6 total**, and one opening-line diagnostic.

Totals were not untouched before v3:

- 11 total models were read before protocol 261120.
- The development baseline read the served v6 total.
- `C1_ppp_adjusted` submitted a per-possession total and was read: slope -0.110, corr -0.026.

T1 reuses C1's machinery with three changes chosen from mechanism after that read. Those changes are disclosed
in the JSON, and T1 is charged as a new read.

## Grid (K = 26 for every totals statistic, 23 for the S0 spread statistic)

| name | family | what | primary |
|---|---|---|---|
| T0_total_diagnostics | diagnostic | as-of level offset of v6 vs close; closing bias regulation vs overtime (CBBD period scores); v6 over lean | none |
| T1_tempo_eff_total | mean | C1 per-possession ridge; tempo per 40 minutes; explicit overtime expectation; variant chosen on total RMSE | total corr delta vs v6 |
| T2_total_combiner | mean | ridge of [v6, T1, early x v6, early x T1, neutral, PIT league level], fit on training folds | total corr delta vs v6 |
| T3_total_scale | scale | v6 + training median residual; normal SD by possessions and sample (MLE) | over log-loss delta vs v6 (and vs ln 2) |
| S0_open_line_diagnostic | diagnostic | v7 spread / v6 total vs the opening line, 2024-2026 | none |

K counting rule: the v1 K of the family, plus 1 for the uncharged baseline read, plus every v3 entry that produces
a statistic of that family. The diagnostics count, since they are logged reads.

## Acceptance

- **Mean candidates.** DEV_ELIGIBLE needs all of:
  - the total corr delta vs v6 has its K-adjusted lower bound above 0;
  - the RMSE delta is at most 0;
  - the 2025+2026 folds favour the candidate;
  - no totals leak flag;
  - coverage is at least 99% in each season.
- **Scale candidates.** DEV_ELIGIBLE needs all of:
  - the over log-loss delta vs v6 has its K-adjusted upper bound below 0;
  - the over log loss minus ln 2 has its K-adjusted upper bound below 0, because served v6 (0.7110) is worse than a coin;
  - the late folds favour the candidate;
  - the over Brier delta is at most 0;
  - no leak flag and coverage met.
- **Confirmation.** Each eligible candidate gets one read on season years 2018-2020 and 2022 (ESPN backfill
  closing totals). The pass rule is the primary statistic with a plain one-sided 95% bound. The window is narrower than v2's,
  because closing totals exist only there and CBBD lines are still refused.
- **Box scores for the confirmation history.** 2014-2021 come from CBBD `games__teams`, with at least 99.3%
  D1-vs-D1 coverage each season. 2022 comes from ESPN box components. No season has both, so the two sources
  are not cross-checked.
- **Reproduction gate.** Before any candidate is scored, `grader_v3` must reproduce the baseline card's served-v6
  totals block at v1's K (20).

## Use

```bash
OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/grader_v3.py repro
OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/grader_v3.py t0
OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/grader_v3.py s0
OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/grader_v3.py score --pred P --meta M --shuffle-seed 5   # plumbing, not logged
OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/grader_v3.py score --pred P --meta M                   # the one logged read
```
