# Architecture

Shadow-first sports prediction and market-research system. No task in the 2026-07-17 run placed an order or wrote a pick.

## Active moneyline models

| Sport | Artifact | Locked-holdout result | Status |
|---|---|---|---|
| MLB | `mlb-elo-trend-lr-v2.json` | 60.87%, 92 calls, +14.91U | shadow qualified |
| NBA | `nba-elo-trend-lr-v2.json` | 67.35%, 294 calls, +84.00U | shadow qualified; protected |
| WNBA | `wnba-elo-trend-lr-v2.json` | 65.98%, 97 calls, +25.18U | shadow qualified; protected |
| NFL | `nfl-elo-trend-lr-v2.json` | 60.55%, 109 calls, +17.00U | shadow qualified |

Each artifact is hash verified. Version 2 changes only the qualification metadata and monthly grading policy; version 1 artifacts remain intact for rollback.

## Validation contract

1. Build point-in-time features with a complete-date walk-forward process.
2. Split chronologically into 60% train, 20% threshold-validation, and 20% locked holdout cohorts.
3. Learn logistic-regression coefficients on train only.
4. Learn the confidence threshold on validation only.
5. Require at least 50 locked-holdout calls, at least 60% called-pick accuracy, positive flat one-unit P&L at -110, and positive P&L in every complete calendar month containing at least 10 calls.
6. Grade the terminal incomplete month as provisional and a complete month with fewer than 10 calls as insufficient. Both remain visible but do not decide qualification.

## Data flow

ESPN/StatsAPI -> immutable raw cache -> point-in-time feature store -> independent Elo+Trend model -> hash-verified confidence gate -> shadow call/no-call result

Polymarket US BBO data remains separate. Executable ask prices are required for trade-profitability claims; model qualification alone does not establish an edge after price and fees.

## Non-moneyline readiness

- NBA/WNBA spread and total validation is blocked: the historical cache contains no exact pregame contract lines.
- MLB full-game spread and total reconstruction is diagnostic only because the recovered sportsbook lines have invalid pregame timestamps.
- MLB F5 spread/total is blocked by missing exact historical lines and point-in-time inputs.
- MLB YRFI/NRFI outcomes exist, but historical starter inputs are retrospective and leakage-prone.

## Invariants

- Market prices never enter the independent game-outcome model.
- No retroactive pick logging.
- No hardcoded fallback threshold; active thresholds come from verified artifacts.
- Proposed features must improve the locked holdout before promotion.
- Research outputs remain zero-unit unless separately promoted and explicitly executed.
