---
name: develop-model-prediction
description: Design, implement, test, and document model or data-pipeline changes in the model-prediction repository. Use when Vincent asks to add or improve a model, feature, league, market, data source, calibration, validation rule, artifact, backtest, or point-in-time evidence pipeline, or asks whether a claimed improvement is real. Keep changes versioned, reproducible, shadow-first, and fail-closed.
---

# Develop Model Prediction

Work from the repo root (`e:\MP` on this Windows machine; `/Users/vincentc9002/model prediction` on the macOS deployment) and
preserve unrelated dirty-worktree changes.

## Define the change

State the theory, existing evidence, falsifiable test, likely failure mode, and
rollback artifact. Trace the live path through source, tests,
`docs/PROJECT_STATUS.md`, `config/model.yaml`, the named artifact, and the
relevant league contract. Treat prose claims as hypotheses when they conflict
with runnable code or generated evidence.

Check for concurrent writers before generating reports or artifacts. Do not
rebuild a release from a moving checkout.

## Protect evaluation integrity

- Split by complete dates: 60% train, 20% model/threshold selection, 20% untouched locked holdout.
- Fit weights on train and select thresholds on validation only.
- Require point-in-time provenance: observed time, effective time, source, parameters, and content hash.
- Keep the independent outcome model free of market price; label any price-aware residual or decision layer.
- Separate accuracy, calibration, and diagnostic `-110` units from executable EV, ROI, and CLV.
- Treat reconstructed/postgame prices and retrospective starters as diagnostic only.
- Preserve contract semantics: regulation versus advance, line orientation, horizon, and KBO/NPB tie value `P(win) + 0.5 * P(tie)`.
- Missing identity, inputs, exact lines, or two-sided timestamp-valid asks are blockers, not proxy permission.
- Before claiming qualified performance applies to serving, compare every live
  feature and transform with the exact hash-verified artifact formula. A
  point-in-time feature outside that formula is still an unqualified change.
  Check snapshot observation time and producer hash, not filename recency.
- Calibrate score-distribution variance against empirical score and margin
  variance before deriving win, spread, or total probabilities. Test variance
  matching and probability calibration on held-out dates; arbitrary dispersion
  can produce severe overconfidence.
- Exact training/inference reproduction is a consistency check. Add independent
  domain invariants, including that opponent-only inputs cannot alter an
  own-team feature and unplayed events cannot contribute outcomes. Keep the
  reproduced legacy control and corrected common cohort distinct.

## Implement safely

Add new model and artifact versions alongside rollback versions. Never hand-edit
an artifact hash or overwrite a protected artifact. Add tests for timing,
identity, horizon, missingness, and fail-closed behavior before production
wiring.

Trace each declared statistical configuration field to the serving arithmetic.
Verify that priors, strength denominators, and rate reconstruction use one
scoring scale. When a regression targets a prior defect, substitute the old
calculation to prove the test detects it. Trace migrated ledger destinations
from actual caller arguments before changing legacy gating predicates; preserve
current operator policy over obsolete helper names. Test that policy through
the real caller contract.

Use:

```sh
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli <command>
```

On Windows PowerShell use `$env:PYTHONPATH='src;.'; .venv\Scripts\python -m model_prediction.cli <command>` and `.venv\Scriptsuff`.

Do not pass `--write-artifacts`, mutate ledgers, or call execution surfaces
unless that mutation is explicitly part of the task and preceding checks pass.

## Verify and report

Per the project CLAUDE.md fast-iteration directive: never run the full `pytest tests/` suite (about 6 minutes) unless Vincent explicitly asks. Run `ruff check src/ tests/` for refactors, renames, docs, and formatting; for algorithm or pricing changes run only the directly affected test file.

```sh
.venv/bin/ruff check src/ tests/
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/<affected_file>.py -o addopts='' -q
```

Reproduce the relevant report without changing the declared split. Verify
artifact hashes and keep every failed gate explicit. Update docs only after
source, tests, config, artifact, and report agree. Never place an order.
For loss-driven research, first reconcile canonical pick rows, unique events,
and units at a pinned as-of time. Require pregame evidence and a walk-forward
ablation before promoting a causal explanation into a feature or threshold.

## Pre-flight check

Before claiming an improvement, confirm the serving formula matches the
qualified artifact, each configured statistical field has a numerical
consumer, distribution variance matches empirical evidence, semantic
invariants hold independently of training/inference parity, and the
evaluation cohort and point-in-time inputs are reproducible.
