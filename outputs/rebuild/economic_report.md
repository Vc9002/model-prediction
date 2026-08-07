# Economic Report — Rebuild Platform

**Generated**: 2026-08-07 | **Branch**: rebuild/clean-slate-v1 @ `e03688f`

## Status — MLB (real, live-verified)

The one-command MLB shadow pipeline (`scripts/mlb_shadow_run.py`) has run
live against real scheduled games and real Polymarket order data multiple
times, most recently 2026-08-06 (2 real games, 176 real full-game
Polymarket rows, 32 real candidate market evaluations, all persisted to
`data/rebuild/shadow.db`). **Result: 0 BET every time, for two independent
reasons, one structural and permanent:**

1. **Structural, not a sample-size problem**: `real_market_candidates()`
   sets `depth_available=False` on every real candidate (fixed 2026-08-07
   — previously fabricated `available_depth=999.0`), because the
   Polymarket source this system reads exposes no order-book depth.
   `decision.py`'s depth gate fails closed on that unconditionally. **No
   market can economically qualify — at any edge, any price — until a
   genuine depth-providing data source is integrated.** This is a missing
   capability, not a threshold to tune.
2. In the specific 2026-08-06 run, most real candidates additionally
   failed `STALE_QUOTE`/alignment gates before ever reaching the depth
   check, because that run's market data was from an earlier collection
   rather than a fresh live pull.

**Economic status: REJECTED** (structural depth gap), not
`ECONOMIC_SAMPLE_INSUFFICIENT` — a larger sample of paper trades cannot
fix a missing data source. The architecture below (sizing, correlation,
stress tests, qualification gates) is real, implemented, and tested, but
has never been exercised end-to-end against a real accepted trade, because
no real trade has ever cleared the depth gate to size and execute.

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

Design target: lower-bound estimate from 5 uncertainty sources (bootstrap,
calibration, lineup, data quality, model disagreement). **Real current
state (MLB)**: only bootstrap uncertainty is implemented —
`BootstrapMLBEnsemble`, 20 independent resample-fit replicates, applied
uniformly to moneyline/spread/total, replacing a flat 3% haircut
(2026-08-07). Real bounds are wide given only 126 training games (e.g. a
0.49 point estimate → real [0.27, 0.67] bound) — correctly conservative
given genuine data scarcity, and the honest reason almost every market
fails the edge gate even before the depth gate is reached. The other 4
sources remain unimplemented; `model_disagreement` specifically requires
multiple independently-trained model families, which don't exist yet
(see predictive report).

A trade proceeds in paper simulation only when conservative_prob clears:
executable ask + fees + slippage + 2% safety margin — and, as of
2026-08-07, only when `depth_available=True`, which is never true for a
real MLB candidate today (see Status above).
