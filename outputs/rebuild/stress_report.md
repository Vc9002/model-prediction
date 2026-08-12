# Stress Test Report

**Date:** 2026-08-05
**Status:** NOT YET AVAILABLE — requires paper-trading history

## Required stress tests (from spec Part 3-K)

| Test | Status | Notes |
|------|--------|-------|
| One tick worse | ✗ | Requires executable order-book depth data |
| Two ticks worse | ✗ | Same |
| Delayed execution | ✗ | Requires timestamped order-book history |
| Reduced depth | ✗ | Requires depth data |
| Partial fills | ✗ | Requires fill simulation |
| Increased fees | ✗ | Fees currently 0.0 on Polymarket sports |
| Probability shrinkage | ✓ code exists | `ConservativeProbability` in `rebuild/conservative.py` |
| Best month removed | ✗ | Requires multi-month paper-trading history |
| Best team removed | ✗ | Same |
| Largest wins removed | ✗ | Same |
| Doubled correlation | ✗ | Requires multi-position paper history |
| Stale-data exclusions | ✓ code exists | `maximum_data_age_hours` in eligibility |
| Stricter market matching | ✓ code exists | `match_executable_quote()` exact matching |

## Current state

Two stress mechanisms are implemented in code:
1. **Probability shrinkage**: `rebuild/conservative.py`'s `ConservativeProbability` applies safety margins and uncertainty deductions
2. **Stale-data exclusions**: `eligibility.py` rejects candidates with `observed_at_utc` older than `maximum_data_age_hours`

The remaining stress tests require a paper-trading history with executable quotes, which the rebuild models do not yet have.

## Verdict

Stress testing cannot be completed until the rebuild models have a minimum paper-trading history of 50+ events across multiple sports, months, and teams. The probability-shrinkage and stale-data mechanisms are the only stress tests currently executable.
