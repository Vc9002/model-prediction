# cfb-structural-v7.2: NCAAF total-probability shrink on v7.1 (quick-261123-6q5 T1)

v7.2 = cfb-structural-v7.1 with only the total market's served P(over)/P(under) moved to `sigmoid(s * logit(p))` (intercept 0) on the no-push over share, the v5.2 construction. Moneyline and spread are v7.1's, bit for bit.

**Fitted s = 0.0** (train-only slope-only logistic, unclipped -0.040524, date-bootstrap 95% CI [-0.1867, 0.1090], 1000 resamples, seed 11).

## Gates

- **Gate 1 (walk sha256 pin): PASS.** Re-walked `walk_cfbd_portal_m0.5_hl45.jsonl` `4f757d56f43ed165a3553b5ef76e4ce80f2bc85a6ab39a831aed1c9902563e9c` vs the `cfb_v71_spec.json` pin `4f757d56f43ed165a3553b5ef76e4ce80f2bc85a6ab39a831aed1c9902563e9c`; holdout file `5260b2de400d504616cf354003fa26945da53ff13c693247c9da1f55fae17d75` vs the `cfb_v71_rule.json` pin `5260b2de400d504616cf354003fa26945da53ff13c693247c9da1f55fae17d75`.
- **Gate 2 (v7.1 stage-2 reproduction through `CFBStructuralV7Model.means`): PASS.**

| Window | n | Margin MAE | Total MAE | ML Brier | Published (n / margin / total / Brier) |
|---|---|---|---|---|---|
| validation | 1858 | 12.6314 | 12.8311 | 0.16174 | 1858 / 12.6314 / 12.8311 / 0.16174 |
| holdout | 1948 | 12.5057 | 12.8432 | 0.16409 | 1948 / 12.5057 / 12.8432 / 0.16409 |

## Split

`validation.chronological_split` fraction mode (60/20/20 on complete kickoff dates) over 9053 called, lined games (9384 called, 331 without an ESPN pinned closing total):

| Window | Dates | Lined | Pushes dropped | Scored |
|---|---|---|---|---|
| train | 2016-09-09 to 2022-11-26 | 5537 | 60 | 5477 |
| validation | 2022-11-27 to 2024-11-24 | 1731 | 11 | 1720 |
| locked_holdout | 2024-11-27 to 2026-11-14 | 1785 | 0 | 1785 |

## Raw v7.1 vs shrunk, per window (no-push over share vs over/under at the ESPN pinned closing total)

| Window | Over rate | Model lean vs line (pts) | Brier raw | Brier shrunk | Log loss raw | Log loss shrunk | Cal. slope raw | Slope-only raw | Mean P(over) raw | Mean P(over) shrunk | Max abs(P(over) - 50%) shrunk |
|---|---|---|---|---|---|---|---|---|---|---|---|
| train | 48.7% | +0.32 | 0.25972 | 0.25000 | 0.71444 | 0.69315 | -0.030 | -0.0405 | 50.9% | 50.0% | 0.00% |
| validation | 51.7% | +0.94 | 0.25519 | 0.25000 | 0.70450 | 0.69315 | +0.202 | +0.2281 | 52.3% | 50.0% | 0.00% |
| locked_holdout | 50.0% | +0.84 | 0.25996 | 0.25000 | 0.71462 | 0.69315 | -0.047 | -0.0445 | 52.0% | 50.0% | 0.00% |

The shrunk probability's calibration slope is the raw slope divided by s (a rescaling of logit), so it is not a separate check (it is in the JSON); Brier and log loss are. Validation's own slope-only fit is +0.2281 (date-bootstrap 95% CI [+0.0106, +0.4424]) against the train-fitted s; s was not adjusted after seeing it.

## Sensitivity: v7.1's own artifact split

Train <= 2022-11-11, validation <= 2024-11-14 (read from the v7.1 artifact; the split its stage 2, total calibration and total SDs were fitted on): train slope-only s = -0.0590 (n 5307); validation slope-only +0.2872; holdout slope-only -0.0522. Not used for the artifact.

## Locked holdout: games that would still clear the NCAAF Main total rule

Conditions applied (cli/forecast.py `_forecast_cfb_sport`, eligibility): model probability >= 50%, 2% <= edge <= 8% (config min_edge, the copied v5.2 gates' max_edge), FCS blocked, model probability <= 62.0%, no under at a line >= 56.5, |projected total - line| >= 1.5 points (v7 contracts carry the calibrated mean as `projected_total`). **Not evaluable historically, and decisive in serving: `pregame_weather_unverified` rejects every v7-family total in Main (v7 contracts carry no verified weather; OD-5 keeps the gate), so no v7.2 total reaches Main whatever these counts say.** Also not evaluated: exposure caps and the market-disagreement check. No historical asks exist, so the ask is assumed: 50% (no-vig at the closing line) and 48%.

| Assumed ask | Raw v7.1 games (over / under sides) | v7.2 games (over / under sides) |
|---|---|---|
| 50.0% | 442 (337 / 105) | 0 (0 / 0) |
| 48.0% | 259 (190 / 69) | 1006 (1006 / 808) |

No shrunk holdout probability is more than 0.00% from 50%, so a side clears the 2% minimum edge on price alone only at an ask at or below about 48.0%.

**s = 0: every total on a half-point line is served at exactly 50% over and 50% under** (an integer line keeps its push mass, so both sides sit below 50% and fail the 50% eligibility floor). At a 48% ask the edge is exactly the 2% minimum, a boundary that floating point can tip either way in serving, and both sides of one game can clear together (the 48% row counts over and under sides separately; games where only the over clears are the under-line rule's). At a 50% ask nothing clears. In serving the weather gate rejects all of these anyway (OD-5).

## Gates block copied into the artifact (OD-1, from cfb-structural-v5.2, verbatim; none validated on v7)

Edge band 2% to 8%, moneyline ask floor 33.3%, max spread line 21.5 points, FCS blocked: True, sizing `dynamic_edge_scaled`.

## Disclosures

- This is the THIRD read of the holdout window (v7 2026-11-21, v7.1 2026-11-22, this fit), and every ESPN lined window 2017-2025 was read before (docs/ROADMAP.md NCAAF: v5, v6, v6.1, v6.2, v7, v7.1; v5.2's shrink fit). Market-relative numbers here are design-informed, not a clean test.
- v7.1's total calibration and total SD buckets were fitted on its train window (<= 2022-11-11), inside this script's 60/20/20 train window, so train-window v7.1 probabilities are in-sample for those layers.
- v7.1 vs v7 was NOT_CONFIRMED; v7.1 has no market skill on totals (total corr with the close +0.009, cfb_v71_report.md); CFBD inputs are current-revised values.
- Closing lines are ESPN's pinned median (espn-lines-rule-1), not decision-time Polymarket asks.

## Provenance

| Input | sha256 |
|---|---|
| walk_cfbd_portal_m0.5_hl45.jsonl (E:/MP/data/research/ncaaf_v71_work) | `4f757d56f43ed165a3553b5ef76e4ce80f2bc85a6ab39a831aed1c9902563e9c` |
| walk_cfbd_portal_m0.5_hl45.holdout.jsonl (E:/MP/data/research/ncaaf_v71_work) | `5260b2de400d504616cf354003fa26945da53ff13c693247c9da1f55fae17d75` |
| espn_lines (E:/MP/data/research/ncaaf_v6/espn_lines.jsonl) | `2b19a2545fb2e5f9df4c55a1b57ee9f88b11eab371828a4a7ed88e8bdfb7fa54` |
| config/models/cfb-structural-v7.1.json | `9aeebe2e931722da7b1b600d2ac2c3c78985940f13cc92fec9db99aa490d63d2` |
| config/models/cfb-structural-v5.2.json | `08232608222a6d8698ba44739cb538a04b9b01e1207e58f78ba3d4377ed7c2f7` |
| outputs/ncaaf/cfb_v71_spec.json | `ad9c8689b92e4e47326ac728d0f7ad95e0e5db7c3d947af0f6b8beecd2851edc` |
| outputs/ncaaf/cfb_v71_rule.json | `3a6e833eae6e2e3a1d801f35db2d348109eb4932854470f887dfb840359477a1` |
| outputs/ncaaf/cfb_v71_report.json | `fc1d7a727c8b5c165502e3f01f915d74d0c465611810cdffddc60f10c93d5162` |
| scripts/fit_cfb_v52_total_shrink.py | `d7844a8a0b51fb1b7c20a71654460cf61f80caa7155731773c787bf28da11164` |
| scripts/fit_cfb_v72_total_shrink.py | `5d1bc9889a395e78a60fe38fc3375e779a9e860bc305019d5d062457b26144cb` |
