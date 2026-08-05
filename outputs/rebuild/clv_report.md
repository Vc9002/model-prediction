# Closing Line Value Report

**Date:** 2026-08-05
**Status:** NOT YET AVAILABLE

## Prerequisites

CLV (Closing Line Value) measurement requires:

1. **Pre-game executable quotes**: Captured at decision time via Polymarket US order-book snapshots. The `polymarket-slate` command captures BBO data daily. ✓ Infrastructure exists.
2. **Closing prices**: Captured via `polymarket-clv` command from the final stored pregame snapshot. ✓ Infrastructure exists.
3. **Consistent contract matching**: Exact event ID, market ID, side, and line must match between the pre-game quote and the closing price. ✓ The `match_executable_quote()` function handles this.
4. **Sufficient trade sample**: At least 50 settled trades with both entry and closing prices. ✗ The rebuild pipeline has no paper-trading history yet.

## Current state

The infrastructure for CLV measurement exists in the production pipeline (`polymarket-clv` command, `match_executable_quote()` function, BBO capture). However, the rebuild models are in `RESEARCH_ONLY` status with no paper-trading ledger, so CLV cannot be computed for the rebuild models.

## What CLV would measure

For each bet:
```
CLV = closing_implied_probability - entry_implied_probability
```
- Positive CLV: The market moved toward the model's opinion after the bet
- Negative CLV: The market moved away from the model's opinion
- Mean CLV: Average across all bets (positive = model adds information)

## Target thresholds (from spec Part 3-E)

- `clv_mean > 0`: Positive or non-negative mean CLV
- `clv_positive_frac > 0.5`: More than half of bets have positive CLV
- Bootstrap CI for CLV must exclude zero

## Next steps

1. Complete rebuild model training for at least MLB (Part 2-D)
2. Run prospective paper-trading for ≥50 events (Part 3-L, Stage 2-3)
3. Capture closing prices for all paper-traded events
4. Compute CLV and bootstrap confidence intervals
5. Update this report with results

## Verdict

CLV cannot be reported until the rebuild models have a paper-trading history with executable quotes. The infrastructure exists but has not been exercised on rebuild models.
