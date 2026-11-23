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

| Window | Over rate | Model lean vs line (pts) | Brier raw | Brier shrunk | Log loss raw | Log loss shrunk | Cal. slope raw | Slope-only raw | Mean P(over) raw | Mean P(over) shrunk | Max abs(P(over) - 50%) shrunk |
|---|---|---|---|---|---|---|---|---|---|---|---|
| train | 48.4% | +0.10 | 0.26376 | 0.25000 | 0.72285 | 0.69314 | +0.013 | +0.0095 | 50.4% | 50.0% | 0.48% |
| validation | 51.8% | +1.84 | 0.25647 | 0.24995 | 0.70730 | 0.69305 | +0.179 | +0.2136 | 54.0% | 50.0% | 0.32% |
| locked_holdout | 50.4% | +1.32 | 0.25865 | 0.24999 | 0.71141 | 0.69313 | +0.028 | +0.0366 | 52.9% | 50.0% | 0.30% |

The shrunk probability's calibration slope is the raw slope divided by s (a rescaling of logit), so it is not a separate check (it is in the JSON); Brier and log loss are. Validation's own slope-only fit is +0.2136 (date-bootstrap 95% CI [-0.0243, +0.4346]) against the train-fitted s; s was not adjusted after seeing it.

## Sensitivity: v5's own artifact split

Train <= 2022-11-11, validation <= 2024-11-14 (the split v5's layers were fitted on): train slope-only s = 0.0080 (n 5429); validation slope-only +0.2261; holdout slope-only +0.0196. Not used for the artifact.

## Locked holdout: games that would still clear the NCAAF Main total rule

Conditions applied (cli/forecast.py `_forecast_cfb_sport`): 2% <= edge <= 8% (config min_edge, artifact max_edge), FCS blocked, model probability <= 0.62, no under at a line >= 56.5, |raw projection - line| >= 1.5 points. Not evaluable historically: `weather_inputs_verified`, the eligibility engine's exposure caps and market-disagreement check. No historical asks exist, so the ask is assumed at the closing line: 50% (no-vig) and 52.4% (vigged).

| Assumed ask | Raw v5 games (over / under sides) | v5.2 games (over / under sides) |
|---|---|---|
| 50.0% | 382 (306 / 76) | 0 (0 / 0) |
| 52.4% | 446 (345 / 101) | 0 (0 / 0) |

The zero holds only for asks at or above 50%: no shrunk holdout probability is more than 0.30% from 50%, so a side whose ask is below about 48.3% would still clear on price alone. Raw v5 clears more games at 52.4% than at 50% because the 8% max_edge cap rejects many raw v5 edges at 50%.

## Reproduction gate

PASS=True: v5's calibrated total matches `outputs/ncaaf/cfb_v7_v5_repro.json` on 1899 holdout events (max abs diff 0.00e+00), the joined ESPN line matches exactly. The brief's join (per_event + cfb_v5_base_rows actual totals, pushes dropped) gives n 1511 (the brief's count), calibration slope +0.0116 against the brief's +0.008 (close, not identical; the brief's exact probability construction is not recorded).

## Disclosures

- Every ESPN lined window 2017-2025 and this locked holdout were read before (docs/ROADMAP.md NCAAF section: v5, v6, v6.1, v6.2, v7, v7.1), and this task's exploration read the holdout slope under both splits before the fit was run. Market-relative numbers here are design-informed, not a clean test.
- v5's total_calibration and total_sd were fitted on 2016-08-27..2022-11-11, inside this train window, so train-window v5 probabilities are in-sample for those layers (train lean vs the line is near 0, validation and holdout lean +1.3 to +1.8 points). The train-fitted s is still about 0.
- Closing lines are ESPN's pinned median (espn-lines-rule-1), not decision-time Polymarket asks.

## Provenance

| Input | sha256 |
|---|---|
| v5_replay_rows (E:/MP/data/research/ncaaf_v7_work/v5_replay_rows.jsonl) | `68caa21f0fc0f4e39ac2fa9139f3adbc8d332a3836d11e9cb9f910a3313ce74f` |
| espn_lines (E:/MP/data/research/ncaaf_v6/espn_lines.jsonl) | `2b19a2545fb2e5f9df4c55a1b57ee9f88b11eab371828a4a7ed88e8bdfb7fa54` |
| config/models/cfb-structural-v5.json | `fbff68717f8c9aff28e227c41b650b5b854404f46d1f4ca2d09ec8fc7cd05f38` |
| outputs/ncaaf/cfb_v7_v5_repro.json | `2b2cdc254bed0a97035c71da3d5892bc6c9c2c78c774604e6ebf1a816545b8af` |
| outputs/ncaaf/cfb_v5_base_rows.jsonl | `742a7dfdcaa70da4a194e9bc9f4221699e5e294a98e7b4133f2cab37d0e1a9c3` |
| scripts/fit_cfb_v52_total_shrink.py | `d7844a8a0b51fb1b7c20a71654460cf61f80caa7155731773c787bf28da11164` |
