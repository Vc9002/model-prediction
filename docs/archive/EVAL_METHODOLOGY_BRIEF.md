# Shared evaluation brief (2026-07-22)

Every session working on model/feature evaluation MUST follow this. Read it before
writing code. It exists so three parallel tracks produce comparable, trustworthy numbers.

## Verified ground truth on `deepseek-phase5`

Production artifacts and their fitted moneyline coefficients:

```
NBA    nba-elo-trend-lr-v4     elo 3.564  trend_gap -0.004  defensive_trend_gap -0.013
WNBA   wnba-elo-trend-lr-v4    elo 3.134  trend_gap -0.007  defensive_trend_gap -0.003
NFL    nfl-elo-trend-lr-v4     elo 2.615  trend_gap  0.050
SOCCER soccer-elo-trend-lr-v2  elo 5.562  trend_gap -0.151
MLB    mlb-elo-trend-lr-v5     elo 3.319  trend_gap -0.030  park_factor -1.050
                               weather_factor -0.318  pitcher_era_gap 0.022
```

Locked-holdout results:

| Sport | Holdout window | Obs | Called | Hit | Brier | Qualified |
|---|---|---:|---:|---:|---:|---|
| NBA | 2026-01-24 → 06-13 | 654 | 88.2% (577) | 73.66% | 0.18541 | true |
| WNBA | 2026-05-19 → 07-19 | 163 | 100% (163) | 67.48% | 0.21414 | true |
| NFL | 2025-11-20 → 2026-02-08 | 122 | 71.3% (87) | 71.26% | 0.20474 | true |
| SOCCER | 2026-01-30 → 07-17 | 1601 | 86.3% (1381) | 64.88% | 0.22015 | true |
| MLB | 2026-04-04 → 07-18 | 1366 | 18.6% (254) | 56.30% | 0.25087 | false |

**Headline conclusion to test, not assume:** every non-Elo coefficient except MLB's
`park_factor` is approximately zero. The production models are functionally Elo plus a
logistic rescaling. Do not repeat the claim that `defensive_trend_gap` explains NBA/WNBA
performance — its fitted coefficients are -0.013 and -0.003.

## Objective hierarchy

1. **Primary objective: sustainable net profitability.** Evaluate against timestamp-valid
   executable prices after spread, fees, and other modeled execution costs. Report ROI,
   net units, CLV when available, drawdown, and call volume. Flat `-110` scoring is only a
   diagnostic and cannot establish Polymarket profitability.
2. **Predictor-quality constraints:** Brier score, log-loss, calibration, and leakage-free
   point-in-time coverage. A profitable-looking result from a miscalibrated or leaky model
   is rejected.
3. **Win-rate constraint:** report hit rate with uncertainty and called rate. Never optimize
   hit rate alone; buying overpriced favorites can raise win rate while destroying profit.
4. **Retention:** retain every feature that shows any positive out-of-sample contribution
   under the zero-threshold directional policy. Track the stricter uncertainty-adjusted
   decision separately; directional retention does not authorize production promotion.

## Feature keep/remove decision matrix

Evaluate every feature separately by sport. Do not carry an NBA verdict into WNBA, or an MLB
verdict into another baseball league.

- **RETAIN FOR RESEARCH:** omission worsens validation Brier, or worsens both locked-holdout
  Brier and log loss, by any positive amount. This is the zero-threshold retention rule and
  must be labeled separately from statistical significance and production safety.
- **KEEP AS PRODUCTION PREDICTOR CANDIDATE:** point-in-time provenance passes, real coverage is at least
  50%, at least one primary predictor metric improves by more than 1 paired date-cluster SE,
  and no primary metric regresses by more than 1 SE.
- **KEEP AS ECONOMIC CANDIDATE:** the predictor gate passes and matched prospective evidence
  shows incremental net ROI or CLV at timestamp-valid executable prices after costs. This is
  still shadow evidence until promotion requirements are met.
- **REMOVE CANDIDATE:** omission improves validation and both locked-holdout proper scores.
  Retain the result in the registry even when the feature is removed from a model.
- **UNTESTABLE / RESEARCH ONLY:** provenance is missing, coverage is below 50%, the feature has
  no historical variance, the model version is mismatched, or the sample cannot support the
  comparison. Do not translate missing evidence into a zero effect.

For each feature, present four columns: chronological backfill/locked-holdout evidence,
settled `data/picks.xlsx` evidence, settled `data/flat_picks.xlsx` evidence, and the overall
decision. Deduplicate repeated rows by event, market, selection, and feature-snapshot lineage;
stratify by sport and exact model version. Ledger results may validate the deployed decision
process, but they establish feature causality only when the row preserves the tested feature
value and matching model lineage.

## Non-negotiable methodology

1. **Chronological three-way split.** Train → threshold/hyperparameter selection →
   locked holdout. Never reorder or shuffle by time. Split boundaries must match the
   `training.coefficient_fit` / `training.threshold_selection` / `training.locked_holdout`
   windows recorded in the corresponding artifact.
2. **The locked holdout is touched exactly once**, to report. Nothing is fit, selected,
   tuned, or thresholded on it. Calibrators fit on the validation cohort only.
3. **Market isolation.** The independent outcome model must never consume market price,
   implied probability, vig-free probability, or any derivative. Only a separately
   labeled residual/decision layer may. This is why `market_signals` is excluded from
   ablation rather than tested.
4. **Point-in-time features only.** A feature computed for game G may use only records
   observable strictly before G's scheduled start, in Eastern-time game-date basis.
   Any feature that cannot prove this fails and is reported as untestable, not as zero.
5. **Report uncertainty.** Every accuracy/Brier delta must carry a standard error. For a
   hit-rate delta on n calls, SE ≈ sqrt(p(1-p)/n); a delta inside ±1 SE is noise, state
   it as noise. Never present a +0.11pp change as an improvement.
6. **Report data coverage** for every feature: fraction of holdout rows where the feature
   is non-null and non-default. A feature with <50% real coverage is untestable on this
   data; say so rather than reporting its diluted delta.
7. **Separate retention from promotion.** Retain any directionally positive feature in the
   registry/research layer. Ship or promote it only when it beats baseline by more than
   1 SE on the locked holdout and passes point-in-time and economic gates. Record both
   decisions so a tiny positive result is preserved without being misrepresented as proven.

## Commands

```sh
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli --help
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

The installed console entry point `.venv/bin/model-prediction` is broken
(`ModuleNotFoundError`). Use the module form above.

## Real-money surfaces — never touch without explicit per-action authorization

`execute`, `sell-position`, cancellation paths, dashboard order-submit routes, ban
mutations, settlement writes, and any command with `--write-artifacts` or `--log`.
A request to evaluate, validate, or fix models is NOT authorization for any of these.

## Reporting contract

End your work with a section titled `## Findings` containing, per item tested:
feature/change name, sport, coverage %, baseline metric, new metric, delta, SE,
verdict (`keep` / `remove` / `untestable`), and one sentence of reasoning.
State plainly what you did not test and why.
