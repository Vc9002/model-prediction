# Economic Report — Rebuild Platform

**Generated**: 2026-08-05 | **Branch**: rebuild/clean-slate-v1

## Status

Economic evaluation architecture is complete. No paper trading has been run —
the collector backfill has not yet executed. This documents the architecture.

## Executable Edge Calculation

The system uses ONLY executable Polymarket US order-book BBO data:
- **best_ask**: real executable ask, not midpoint
- **conservative probability**: model probability minus 5-source uncertainty margin
- **cost_adjusted_edge**: conservative_prob - best_ask - spread/2 - fees
- **No synthetic -110 pricing**: all edges are cost-adjusted against real quotes

## Position Sizing

- **Default valid size**: 0 units (no forced minimum)
- **Kelly fraction**: quarter-Kelly on conservative probability
- **Caps**: event (2U), team daily (3U), sport daily (5U), total daily (10U)
- **Unit rounding**: nearest 0.25U
- **Quote age limit**: 300 seconds max

## Economic Qualification Gates

A model advances from RESEARCH_ONLY through these stages:

| Stage | Requirement |
|---|---|
| PREDICTIVELY_QUALIFIED | Log loss < baseline, ECE < 0.10, Brier < 0.25, ≥50 events, 0 PIT violations |
| ECONOMIC_SAMPLE_INSUFFICIENT | Predictive OK but <50 paper trades |
| ECONOMICALLY_QUALIFIED_FOR_SHADOW | Positive cost-adjusted return, positive CLV, ≥50 trades, depth sufficient |
| ELIGIBLE_FOR_SEPARATE_LIVE_REVIEW | Outside this task — requires separate authorization after prospective evidence |

## Portfolio Evaluation

- **Metrics**: Total PnL, mean/std, Sharpe, max drawdown, win rate, ROI (bps)
- **Bootstrap**: 1,000-sample bootstrap of mean trade PnL, 95% CI
- **Probability positive**: Bootstrap fraction where mean PnL > 0
- **Bucket analysis**: By month, team, price bucket, edge bucket, liquidity, missingness cohort, horizon

## Correlation Adjustment

7 correlation types tracked:
- same_event (corr=1.0), same_team_ML_spread (0.85), pitcher_derived (0.5)
- weather_driven (0.4), same_team_cross_event (0.3), model_family (0.2), same_league_day (0.1)
- Nominal and correlation-adjusted exposure reported separately

## Stress Tests (13 scenarios)

All must pass for shadow qualification:
- One tick worse entry, two ticks worse
- Best month removed, best team removed, largest 10% wins removed
- Probability shrinkage toward 0.5
- Delayed execution, reduced depth, partial fills
- Increased fees, doubled correlation, stale data exclusion, stricter market matching

## Conservative Probability

Lower-bound estimate from 5 uncertainty sources:
- Model bootstrap uncertainty (variance across folds)
- Calibration uncertainty (residual from Platt/isotonic fit)
- Player/lineup uncertainty (available from missingness module)
- Data quality uncertainty (observation age, conflicting sources)
- Model disagreement (std across ensemble members, scaled by 1/√n)

A trade proceeds in paper simulation only when conservative_prob clears:
executable ask + fees + slippage + 2% safety margin.
