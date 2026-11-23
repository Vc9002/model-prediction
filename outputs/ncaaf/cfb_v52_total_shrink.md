# cfb-structural-v5.2: NCAAF total-probability shrink (quick-261123-2w2)

v5.2 = cfb-structural-v5 with only the total market's served P(over)/P(under) moved to `sigmoid(s * logit(p))` (intercept 0) on the no-push over share. Moneyline and spread are v5's, bit for bit.

**Fitted s = 0.0095** (train-only slope-only logistic, unclipped 0.009471, date-bootstrap 95% CI [-0.1014, 0.1186], 1000 resamples). A slope this close to 0 serves every total at about 50%.

## Split

`validation.chronological_split` fraction mode (60/20/20 on complete kickoff dates) over 9177 lined FBS-involved games:

| Window | Dates | Lined | Pushes dropped | Scored |
|---|---|---|---|---|
| train | 2016-08-27 to 2022-11-20 | 5610 | 60 | 5550 |
| validation | 2022-11-23 to 2024-11-22 | 1723 | 13 | 1710 |
| locked_holdout | 2024-11-23 to 2026-11-14 | 1844 | 0 | 1844 |

## Raw v5 vs shrunk, per window (no-push over share vs over/under at the ESPN pinned closing total)

| Window | Over rate | Model lean vs line (pts) | Brier raw | Brier shrunk | Log loss raw | Log loss shrunk | Cal. slope raw | Cal. slope shrunk | Slope-only raw | Mean P(over) raw | Mean P(over) shrunk |
|---|---|---|---|---|---|---|---|---|---|---|---|
| train | 48.4% | +0.10 | 0.26376 | 0.25000 | 0.72285 | 0.69314 | +0.013 | +1.370 | +0.0095 | 50.4% | 50.0% |
| validation | 51.8% | +1.84 | 0.25647 | 0.24995 | 0.70730 | 0.69305 | +0.179 | +18.827 | +0.2136 | 54.0% | 50.0% |
| locked_holdout | 50.4% | +1.32 | 0.25865 | 0.24999 | 0.71141 | 0.69313 | +0.028 | +2.914 | +0.0366 | 52.9% | 50.0% |

Calibration slope of the shrunk probability equals the raw slope divided by s (a rescaling of logit), so it is not a separate check; Brier and log loss are.

## Sensitivity: v5's own artifact split

Train <= 2022-11-11, validation <= 2024-11-14 (the split v5's layers were fitted on): train slope-only s = 0.0080 (n 5429); validation slope-only +0.2261; holdout slope-only +0.0196. Not used for the artifact.

## Locked holdout: games that would still clear the NCAAF Main total rule

Conditions applied (cli/forecast.py `_forecast_cfb_sport`): 2% <= edge <= 8% (config min_edge, artifact max_edge), FCS blocked, model probability <= 0.62, no under at a line >= 56.5, |raw projection - line| >= 1.5 points. Not evaluable historically: `weather_inputs_verified`, the eligibility engine's exposure caps and market-disagreement check. No historical asks exist, so the ask is assumed at the closing line: 50% (no-vig) and 52.4% (vigged).

| Assumed ask | Raw v5 games (over / under sides) | v5.2 games (over / under sides) |
|---|---|---|
| 50.0% | 382 (306 / 76) | 0 (0 / 0) |
| 52.4% | 446 (345 / 101) | 0 (0 / 0) |

## Reproduction gate

PASS=True: v5's calibrated total matches `outputs/ncaaf/cfb_v7_v5_repro.json` on 1899 holdout events (max abs diff 0.00e+00), the joined ESPN line matches exactly. The brief's join (per_event + cfb_v5_base_rows actual totals, pushes dropped) gives n 1511, calibration slope +0.0116.

## Disclosures

- Every ESPN lined window 2017-2025 and this locked holdout were read before (docs/ROADMAP.md NCAAF section: v5, v6, v6.1, v6.2, v7, v7.1), and this task's exploration read the holdout slope under both splits before the fit was run. Market-relative numbers here are design-informed, not a clean test.
- v5's total_calibration and total_sd were fitted on 2016-08-27..2022-11-11, inside this train window, so train-window v5 probabilities are in-sample for those layers (train lean vs the line is near 0, validation and holdout lean +1.3 to +1.8 points). The train-fitted s is still about 0.
- Closing lines are ESPN's pinned median (espn-lines-rule-1), not decision-time Polymarket asks.

## Provenance

| Input | sha256 |
|---|---|
| v5_replay_rows (E:/MP/data/research/ncaaf_v7_work/v5_replay_rows.jsonl) | `68caa21f0fc0f4e39ac2fa9139f3adbc8d332a3836d11e9cb9f910a3313ce74f` |
| espn_lines (E:/MP/data/research/ncaaf_v6/espn_lines.jsonl) | `2b19a2545fb2e5f9df4c55a1b57ee9f88b11eab371828a4a7ed88e8bdfb7fa54` |
| config/models/cfb-structural-v5.json | `12f7a970fe2cc796ac417da03106a9e709c5643c0b9d4489b5277703c57c6192` |
| outputs/ncaaf/cfb_v7_v5_repro.json | `76b1801ed58d180390526caa6d15227e49a2a8b348121a37c56220f5f96a04d1` |
| outputs/ncaaf/cfb_v5_base_rows.jsonl | `39c1cbd61e63b7dc1c029ad39b7c5b759aa1921296957d9070cb977bab2d187e` |
| scripts/fit_cfb_v52_total_shrink.py | `112b2e12f67574b820aca872e8110defc28aa947d070701ee7b9c3f965aa672a` |
