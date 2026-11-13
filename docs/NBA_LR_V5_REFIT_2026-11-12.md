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

## Registration and promotion

2026-11-12 (quick-261112-kj3): registered as the NBA moneyline challenger (`serving_status:
shadow`, `evidence_status: historical_only`, rollback v4); the previous challenger value
`nba-structural-v5` had no registry entry.

2026-11-13 (quick-261113-j3m, operator directive "promote"): promoted through
`model_promotion.promote` — promotion id `promo-20261113T184744Z-6b0b74`, recorded in the
runtime `promotions` table with `evidence_id` = this document and an explicit
`market_evidence_unavailable_reason` (no market-relative replay exists for v5 or v4; the
promotion changes the evidence window and the threshold, not the served probabilities).
Record copy: `outputs/nba/nba_lr_v5_promotion.json`. Wiring completed by hand where the
command does not reach: `serving_status` v5 → production, v4 → rollback; `config/model.yaml`
`models.NBA.active_production_version` / `production_artifact` → v5 (the learned slate serves
from model.yaml and `_is_registered_serving_model` compares that artifact's version to the
champion, so both had to move together). `evidence_status` stays `historical_only` for v5
as it was for v4, so NBA moneyline still reaches Main only as a paper call
(`PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED`); changing that is a separate decision, as is the
auto-buyer whitelist (neither v4 nor v5 is listed).

## Caveats

- Holdout metrics are −110 diagnostic units, not executable EV; `market_inputs_used: false`.
- `historical_only` is deliberate for both versions: a walk-forward backtest is not live
  evidence, so neither reaches Main as predictively qualified.
- Evidence files: `outputs/nba/nba_lr_v5_validation.json`, `config/models/nba-elo-trend-lr-v5.json`.
