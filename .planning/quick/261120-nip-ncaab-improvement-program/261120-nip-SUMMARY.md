---
phase: quick-261120-nip
plan: 01
status: complete
commits:
  - cb1b980f fix(ncaab): keep the mascot when a spread market lists first; UCA/UNCW/NAU aliases
  - 4596c606 feat(ncaab): v7 margin engine gets its own id, artifact hash, engine-pure calibration, pre-tip CLV
  - c3f97a4a promote ncaab-spread-v7 (moneyline + spread); retire models.NCAAB.margin_engine
  - c2465224 research(ncaab): protocol 261120, grader, CBBD/ESPN data, six candidate scorecards
  - 9e65ec24 docs: handoff NCAAB section
  - 2e8b2bca research(ncaab): A_ml_w0 confirmation read PASSES on the 2015-2022 backcast
---

# 261120-nip: NCAAB improvement program — summary

## Shipped (live)

- Team matching: mascot no longer dropped when a spread market lists first; UCA, UNCW, NAU aliases.
  Replay over 98 live events: no previously resolved event changed.
- `ncaab-spread-v7` registered and promoted (moneyline + spread) through `model_promotion`; rollback
  `ncaab-spread-v6`. Slate replay 46/46 identical served probabilities. The `margin_engine` yaml key is
  retired and fails closed.
- Calibration (operator-approved): fits on the raw probability, engine-pure pools, served only through
  `load_serving_calibrator` (sample floor + OOF Brier gain). 06:00 Daily 2026-11-21: all NCAAB
  calibrators resolved to identity.
- Ledger: `model_artifact_hash` on every NCAAB row (06:00 Daily: 69/69 v7 rows, 16/16 total rows).
  Best-pick audit reads the served probability.
- CLV: NCAAB closes must be observed after the decision; buyer worker captures pre-tip books
  (first captures expected from ~10:40 ET 2026-11-21).
- Paper trial trades v7 only; main buyer blacklists v7 and NCAAB stays disabled (state v937).

## Research (protocol 261120)

Development window (LOSO 2023-2026, 22,947 games): no candidate beats the closing line
(v7 margin RMSE 11.46 vs close 11.19; ML Brier 0.1822 vs 0.1769). Only A_ml_w0 (moneyline at
Elo weight 0) was DEV_ELIGIBLE; B1/B2/B3 scale/cap, C1/C2 per-possession and D1 preseason prior were not.

Confirmation read (one logged read, 2026-11-21T12:09:48Z), A_ml_w0 vs served v7 over
2015-2020 + 2022, 38,471 games: log-loss delta -0.004627, one-sided 95% upper -0.003872, every
season negative, holds ex-2015 and on lined games. **PASS.** Promotion is an operator decision
(new id, v7 margin unchanged); nothing registered.

## Open

- A_ml_w0 promotion decision (operator).
- Close bound not on `_settle_by_resolution` / `auto_buyer_ledger.py:1404` paths.
- model_ledger xlsx mirror map stale; `docs/ncaab_review_2026-11-20/forensics_scripts/` uncommitted
  (ruff); prediction files over 3 MB untracked.
