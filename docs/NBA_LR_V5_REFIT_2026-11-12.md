# NBA moneyline refit — `nba-elo-trend-lr-v5` (2026-11-12, quick-261112-kj3)

Operator directive "refit nba", after `quick-261112-11j` removed the NBA-only Elo/Trend
serving overrides (MODEL_REVIEW_2026-11-11 #6) so `learned_forward.py` rebuilds Elo and
trends exactly as `validation.py` fits them. This refit was produced by the project's own
contract, not by hand: `LEARNED_ARTIFACT_VERSIONS["nba"]` bumped to v5, then

```
python -m model_prediction.cli validate-models --sports nba \
  --output outputs/nba/nba_lr_v5_validation.json --write-artifacts
```

Walk-forward rows (`build_walk_forward_rows`, point-in-time, 3,596 rows over 3,859 games
2024-01-01 → 2026-11-12), `chronological_split` 60/20/20 on complete dates, LR fit on train,
threshold learned on validation only, locked holdout graded once. The writer refuses to
overwrite an existing versioned file, so `nba-elo-trend-lr-v4.json` is untouched.

## Result

| | v4 (2026-07) | v5 (2026-11-12) |
|---|---|---|
| Features | elo_probability, trend_gap, defensive_trend_gap | same |
| Coefficients | 3.5641, −0.0036, −0.0131; intercept −1.9072 | 3.6046, −0.0018, −0.0115; intercept −1.9376 |
| Confidence threshold | 54.44% | 52.54% |
| Coefficient fit | 2024-01-08 → 2025-05-27 (2,171) | 2024-01-08 → 2025-06-22 (2,104) |
| Threshold selection | 2025-05-28 → 2026-01-23 (753) | 2025-10-21 → 2026-02-22 (856) |
| Locked holdout | 2026-01-24 → 2026-06-13 (654) | 2026-02-23 → 2026-11-11 (636) |
| Holdout calls / hit | 577 / 73.7% | 598 / 73.1% |
| Brier on calls | 0.1854 | 0.1849 |
| Qualified (project gate) | yes | yes; every qualifying month positive at −110 |

Variant comparison inside the same audit (all 636 holdout rows): `elo_only` Brier 0.1886,
`elo_trend` 0.1886, `elo_trend_defense` 0.1889. The trend and defensive-trend terms carry no
information beyond Elo in this cohort; `elo_trend_defense` is the production variant by
construction (`build_production_artifact`), not by evidence.

## Same-holdout comparison (identical rows, v5's locked window)

| Model | Brier (all 636) | Log loss | Accuracy | Threshold | Calls | Hit | Units at −110 |
|---|---|---|---|---|---|---|---|
| v4 coefficients, v4 threshold | 0.1892 | 0.5627 | 71.5% | 54.44% | 569 | 74.5% | +240.5 |
| v5 coefficients, v5 threshold | 0.1889 | 0.5619 | 71.9% | 52.54% | 598 | 73.1% | +236.3 |
| v4 coefficients, v5 threshold | 0.1892 | 0.5627 | 71.5% | 52.54% | 594 | 73.4% | +238.4 |

Brier delta v5 − v4 = −0.00026. **The refit is a confirmation, not an improvement:** the
coefficients were never the defect; the serving overrides were. Current season only
(2026-10-01 → 2026-11-11, 170 games): v4 Brier 0.2047 / 67.6% accuracy, v5 0.2045 / 68.8%.

## Registration

`config/production.yaml`: new entry `nba-elo-trend-lr-v5` (`serving_status: shadow`,
`evidence_status: historical_only`, rollback v4); `challengers.NBA.moneyline` now points at it
(the previous value `nba-structural-v5` had no registry entry). Champion stays
`nba-elo-trend-lr-v4`. Promotion, if wanted, is the explicit command:

```
python -m model_prediction.model_promotion promote --new nba-elo-trend-lr-v5 --sport NBA \
  --market moneyline --approved-by <operator> --evidence docs/NBA_LR_V5_REFIT_2026-11-12.md
```

What promotion would buy: an evidence window that includes 2026-27 games, and ~5% more calls
from the lower threshold. What it would not buy: better probabilities.

## Caveats

- Holdout metrics are −110 diagnostic units, not executable EV; `market_inputs_used: false`.
- `historical_only` is deliberate for both versions: a walk-forward backtest is not live
  evidence, so neither reaches Main as predictively qualified.
- Evidence files: `outputs/nba/nba_lr_v5_validation.json`, `config/models/nba-elo-trend-lr-v5.json`.
