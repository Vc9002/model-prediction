# Architecture

The authoritative current-state overview is `docs/PROJECT_STATUS.md`. This file
defines the durable architecture contract.

## Data and decision flow

```text
provider response
  -> immutable/raw cache with observed and effective time
  -> canonical entity resolution
  -> point-in-time feature snapshot and hash
  -> versioned independent model artifact
  -> validation-learned confidence gate
  -> exact contract and executable-quote match
  -> CLI executable-ask edge gate where configured
  -> trust-boundary eligibility and record classification
  -> ledger, settlement, CLV, calibration, and review
```

Dashboard view state and exchange order state are separate from model evidence.
The audit chain is intact, but ledger mutation and audit append are separate
commits; chain integrity alone does not prove ledger/audit reconciliation.

As of the 2026-07-26 operator directive, eligibility accepts but does not use
exposure or market disagreement when deciding `CALL` versus `NO_CALL`.
Post-uncertainty edge is also not a decision gate there. Older diagrams that
show exposure/disagreement as enforced eligibility gates are stale.

## Validation contract

1. Split by complete dates: 60% train, 20% model/threshold selection, 20% untouched locked holdout.
2. Fit coefficients on train only and select thresholds on validation only.
3. Require at least 50 locked calls and at least 60% called-pick accuracy.
4. Require positive diagnostic `-110` units in every complete calendar month with at least 10 calls when that monthly rule is part of the active contract.
5. Keep partial and insufficient months visible but non-binding.
6. Version every promoted change and preserve rollback artifacts.
7. Treat point-in-time provenance and exact market semantics as prerequisites.

## Invariants

- **100% Paper ("Fugazi") Invariant**: The entire platform operates in paper simulation mode. Under no circumstances can real capital be traded, authorized, or lost.
- **Realistic Execution Mechanics**: Sizing (1.0U–2.0U Kelly/edge-scaled), live Polymarket CLOB executable quote evaluation, bankroll tracking, drawdown circuit breakers, and mark-to-market PnL behave with institutional realism *as if* real money were at stake.
- **Direct Model Wiring**: When an updated model exists for any sport, league, or market, it is directly wired up to the production serving pipeline without bureaucratic "shadow-qualified" holdout quotas or quarantine gates.
- Market price never enters the independent outcome model.
- A market residual/decision layer is separate and labeled.
- No retrospective pick logging or postgame feature leakage.
- No hardcoded fallback threshold when the named artifact is missing or invalid.
- No candidate may be priced from a snapshot with `timestamp_valid=false`.
- No fabricated spread, total, F5, YRFI/NRFI, or three-way contract semantics.
- Real-money live order execution is hard-disabled (`automated_orders: false`, `mode: "paper"`).

## Current model status

Do not duplicate volatile metrics here. Use the table in
`docs/PROJECT_STATUS.md` and `docs/ROADMAP.md`. Model promotion follows the direct wiring policy in `docs/PROMOTION_POLICY.md`.

