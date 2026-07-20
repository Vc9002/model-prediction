# Independent roadmap feature effect audit

Generated: `2026-07-20T11:09:51.593147Z`

## Answer

Each addition was fit alone on top of the matched refit control. Across 24 sport-feature tests, no feature demonstrates a reliable positive effect after validation-direction checks, date-cluster randomization, and Holm correction. `schedule_available` is rejected structurally because it is constant or nearly constant in the evaluation cohorts.

A coefficient being nonzero does not establish an effect. The decision requires lower Brier on both validation and holdout plus multiplicity-safe evidence. Confidence-gate changes are reported but cannot override the probability-quality tests.

## Testing contract

- One added feature group at a time; no interactions or combinations.
- Same complete-date 60/20/20 cohorts and matched refit control as the factorial dossier.
- Holdout uncertainty uses a paired date-cluster sign-flip test with 5,000 randomizations.
- Holm correction is applied across all 24 isolated tests.
- Lower Brier/log loss is better; negative deltas favor the isolated feature.
- Flat `-110` results and selective hit rates are diagnostics, not economic evidence.

## Cross-sport feature consistency

| Feature | Validation improved | Holdout improved | Raw p≤0.05 | Holm p≤0.05 | Conclusion |
|---|---:|---:|---:|---:|---|
| `consistency` | 1/4 | 3/4 | 1/4 | 0/4 | No reliable independent effect |
| `hot_cold` | 1/4 | 3/4 | 0/4 | 0/4 | No reliable independent effect |
| `rest_disparity` | 1/4 | 3/4 | 1/4 | 0/4 | No reliable independent effect |
| `back_to_back` | 3/4 | 1/4 | 1/4 | 0/4 | No reliable independent effect |
| `schedule_density` | 2/4 | 2/4 | 0/4 | 0/4 | No reliable independent effect |
| `schedule_missingness` | 2/4 | 3/4 | 0/4 | 0/4 | Reject as a prediction feature |

# MLB

Control holdout Brier: `0.249058`. Control validation-selected gate: `0.569`; holdout calls/hit rate: `499` / `0.563`.

| Isolated feature | Coefficient | Holdout std / unique / zero | Validation Δ Brier | Holdout Δ Brier | Δ log loss | 95% CI Δ Brier | Raw p | Holm p | Gate | Calls | Hit | Effect decision |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `consistency` | -0.381296 | 0.0791 / 1177 / 0.0% | +0.000263 | +0.000085 | +0.000188 | [-0.000298, +0.000490] | 0.6571 | 1.0000 | 0.562 | 557 | 0.542 | DIRECTIONAL_HARM_NOT_DETECTED |
| `hot_cold` | +0.219278 | 0.5004 / 1192 / 0.1% | +0.000282 | +0.000100 | +0.000223 | [-0.000483, +0.000696] | 0.7391 | 1.0000 | 0.566 | 529 | 0.554 | DIRECTIONAL_HARM_NOT_DETECTED |
| `rest_disparity` | +0.070128 | 0.3908 / 5 / 88.1% | +0.000435 | -0.000361 | -0.000738 | [-0.000709, -0.000005] | 0.0378 | 0.8692 | 0.569 | 501 | 0.567 | NOMINAL_ONLY |
| `back_to_back` | +0.072199 | 0.3033 / 3 / 90.8% | -0.000087 | +0.000386 | +0.000790 | [+0.000112, +0.000689] | 0.0072 | 0.1728 | 0.569 | 503 | 0.555 | NOMINAL_ONLY |
| `schedule_density` | -0.007264 | 0.8330 / 6 / 52.7% | +0.000019 | +0.000027 | +0.000056 | [-0.000043, +0.000098] | 0.4625 | 1.0000 | 0.570 | 497 | 0.563 | DIRECTIONAL_HARM_NOT_DETECTED |
| `schedule_missingness` | +0.252714 | 0.0000 / 1 / 0.0% | +0.000018 | -0.000027 | -0.000055 | [-0.000116, +0.000054] | 0.5481 | 1.0000 | 0.569 | 500 | 0.566 | REJECT_DEGENERATE |

# NBA

Control holdout Brier: `0.194854`. Control validation-selected gate: `0.500`; holdout calls/hit rate: `662` / `0.702`.

| Isolated feature | Coefficient | Holdout std / unique / zero | Validation Δ Brier | Holdout Δ Brier | Δ log loss | 95% CI Δ Brier | Raw p | Holm p | Gate | Calls | Hit | Effect decision |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `consistency` | -0.370826 | 0.0264 / 658 / 0.5% | +0.000046 | -0.000139 | -0.000313 | [-0.000281, -0.000002] | 0.0472 | 1.0000 | 0.500 | 662 | 0.701 | NOMINAL_ONLY |
| `hot_cold` | +0.077237 | 0.5168 / 658 / 0.6% | +0.000074 | -0.000261 | -0.000578 | [-0.000547, +0.000003] | 0.0786 | 1.0000 | 0.500 | 662 | 0.698 | SPLIT_UNSTABLE |
| `rest_disparity` | +0.075388 | 1.1144 / 12 / 52.4% | +0.000475 | -0.000203 | -0.000562 | [-0.001282, +0.000852] | 0.7173 | 1.0000 | 0.500 | 661 | 0.699 | SPLIT_UNSTABLE |
| `back_to_back` | -0.243626 | 0.5916 / 3 / 64.8% | -0.000337 | -0.000183 | -0.000608 | [-0.002319, +0.001990] | 0.8684 | 1.0000 | 0.501 | 661 | 0.697 | DIRECTIONAL_POSITIVE_NOT_DETECTED |
| `schedule_density` | -0.108046 | 0.7817 / 6 / 56.3% | -0.000558 | -0.000219 | -0.000688 | [-0.001260, +0.000868] | 0.7189 | 1.0000 | 0.500 | 662 | 0.704 | DIRECTIONAL_POSITIVE_NOT_DETECTED |
| `schedule_missingness` | -0.829362 | 0.0672 / 2 / 0.5% | -0.000998 | -0.000564 | -0.001246 | [-0.001902, +0.000054] | 0.8922 | 1.0000 | 0.500 | 662 | 0.701 | REJECT_DEGENERATE |

# WNBA

Control holdout Brier: `0.218558`. Control validation-selected gate: `0.505`; holdout calls/hit rate: `173` / `0.688`.

| Isolated feature | Coefficient | Holdout std / unique / zero | Validation Δ Brier | Holdout Δ Brier | Δ log loss | 95% CI Δ Brier | Raw p | Holm p | Gate | Calls | Hit | Effect decision |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `consistency` | +0.036297 | 0.0291 / 175 / 0.0% | +0.000174 | -0.000019 | -0.000044 | [-0.000052, +0.000017] | 0.3009 | 1.0000 | 0.505 | 174 | 0.690 | SPLIT_UNSTABLE |
| `hot_cold` | +0.268313 | 0.4449 / 175 / 0.0% | +0.000773 | -0.001025 | -0.002137 | [-0.002267, +0.000289] | 0.1212 | 1.0000 | 0.501 | 174 | 0.690 | SPLIT_UNSTABLE |
| `rest_disparity` | -0.017964 | 1.7452 / 12 / 30.9% | -0.000028 | +0.000192 | +0.000469 | [-0.000915, +0.001301] | 0.7441 | 1.0000 | 0.503 | 173 | 0.688 | SPLIT_UNSTABLE |
| `back_to_back` | +0.048891 | 0.5237 / 3 / 72.6% | -0.000254 | +0.000340 | +0.000758 | [-0.000604, +0.001295] | 0.4931 | 1.0000 | 0.504 | 174 | 0.684 | SPLIT_UNSTABLE |
| `schedule_density` | -0.131804 | 0.9430 / 6 / 41.1% | +0.000143 | +0.002088 | +0.004379 | [-0.001752, +0.006096] | 0.3041 | 1.0000 | 0.502 | 175 | 0.674 | DIRECTIONAL_HARM_NOT_DETECTED |
| `schedule_missingness` | -0.148985 | 0.0000 / 1 / 0.0% | -0.000293 | -0.000021 | -0.000053 | [-0.000107, +0.000066] | 0.6267 | 1.0000 | 0.506 | 172 | 0.686 | REJECT_DEGENERATE |

# NFL

Control holdout Brier: `0.221420`. Control validation-selected gate: `0.500`; holdout calls/hit rate: `110` / `0.627`.

| Isolated feature | Coefficient | Holdout std / unique / zero | Validation Δ Brier | Holdout Δ Brier | Δ log loss | 95% CI Δ Brier | Raw p | Holm p | Gate | Calls | Hit | Effect decision |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `consistency` | -0.060959 | 0.0425 / 110 / 0.0% | -0.000046 | -0.000060 | -0.000114 | [-0.000161, +0.000039] | 0.2743 | 1.0000 | 0.500 | 110 | 0.627 | DIRECTIONAL_POSITIVE_NOT_DETECTED |
| `hot_cold` | +0.800645 | 0.4223 / 110 / 0.9% | -0.001633 | -0.002468 | -0.005540 | [-0.005364, +0.000381] | 0.1006 | 1.0000 | 0.501 | 110 | 0.636 | DIRECTIONAL_POSITIVE_NOT_DETECTED |
| `rest_disparity` | -0.026620 | 0.6669 / 5 / 66.4% | +0.000183 | -0.000238 | -0.000573 | [-0.000704, +0.000429] | 0.4491 | 1.0000 | 0.501 | 108 | 0.620 | SPLIT_UNSTABLE |
| `back_to_back` | +0.000000 | 0.0000 / 1 / 100.0% | +0.000000 | +0.000000 | +0.000000 | [-0.000000, +0.000000] | 0.1874 | 1.0000 | 0.500 | 110 | 0.627 | NO_VARIANCE |
| `schedule_density` | -0.169751 | 0.4565 / 3 / 79.1% | -0.001071 | -0.000041 | -0.000197 | [-0.002939, +0.002851] | 0.9808 | 1.0000 | 0.502 | 109 | 0.633 | DIRECTIONAL_POSITIVE_NOT_DETECTED |
| `schedule_missingness` | -0.000008 | 0.0000 / 1 / 0.0% | +0.000022 | +0.000020 | +0.000046 | [-0.000014, +0.000053] | 0.2547 | 1.0000 | 0.500 | 110 | 0.627 | REJECT_DEGENERATE |

# Final independent-feature decision

- Keep in production: **none**.
- Collect prospectively: MLB `rest_disparity`; NFL `hot_cold` and `schedule_density`; NBA schedule-load features. These are hypotheses, not validated additions.
- Reject as modeled: `schedule_missingness`; NFL `back_to_back` has no variance; MLB `back_to_back` shows holdout harm.
- Do not tune confidence gates around these features until a fresh timestamp-valid cohort confirms an all-prediction Brier improvement.

