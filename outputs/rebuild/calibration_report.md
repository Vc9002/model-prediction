# Calibration Report

**Date:** 2026-08-05
**Status:** PARTIAL — out-of-fold calibration not yet implemented for rebuild models

## Current calibration methods

| Model | Calibration | Status |
|-------|-------------|--------|
| MLB learned LR (production) | Flat probability shrinkage toward 0.5 | Active |
| NBA/WNBA learned LR (production) | Flat probability shrinkage toward 0.5 | Active |
| Esports tiered Elo | Platt scaling | Active |
| MLB two-head v1 (rebuild) | None | RESEARCH_ONLY |

## MLB two-head v1 calibration assessment

Without any calibration, the rebuild MLB model shows:
- **ECE: 0.2073** — substantial miscalibration (target: <0.10)
- **Brier: 0.2601** — worse than coin-flip baseline (0.250)

This confirms the spec's warning: "Adding XGBoost directly to the existing feature matrices would probably produce a more complicated version of the same incomplete models."

## Required calibration methods (from spec Part 2-N)

| Method | Status | Notes |
|--------|--------|-------|
| Identity | ✓ trivial | No calibration = identity |
| Sigmoid/Platt | ✓ code exists | `rebuild/calibration.py` has Platt implementation |
| Isotonic | ✗ | Requires larger sample size |
| Temperature scaling | ✗ | Not implemented |
| Beta calibration | ✗ | Not implemented |

## Critical requirement not yet met

The spec states: "Fit calibration on data disjoint from base-model fitting."

The current production calibration (flat shrinkage toward 0.5) is applied as a fixed transformation, not fitted on out-of-fold predictions. The rebuild pipeline has no out-of-fold calibration at all.

## Next steps

1. Implement chronological cross-validation with held-out calibration fold (Part 2-B)
2. Fit Platt/sigmoid calibrator on calibration fold only
3. Measure ECE, Brier, and reliability curves on untouched test set
4. Store calibrator as a separately hashed artifact bound to the base model
5. Repeat for every sport, market, and horizon

## Verdict

Calibration is the single largest predictive quality gap. The rebuild MLB model (ECE 0.21) and the production NBA model (calibration slope ~1.79) both have substantial probability-shape errors. Out-of-fold calibration on independent data is required before any model can be considered for promotion.
