---
name: develop-model-prediction
description: Design, implement, test, and document model or data-pipeline changes in the model-prediction repository. Use when Vincent asks to add or improve a model, feature, league, market, data source, calibration, validation rule, artifact, backtest, or point-in-time evidence pipeline, or asks whether a claimed improvement is real. Keep changes versioned, reproducible, shadow-first, and fail-closed.
---

# Develop Model Prediction

Work from `/Users/vincentc9002/model prediction` and
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

## Implement safely

Add new model and artifact versions alongside rollback versions. Never hand-edit
an artifact hash or overwrite a protected artifact. Add tests for timing,
identity, horizon, missingness, and fail-closed behavior before production
wiring.

Use:

```sh
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli <command>
```

Do not pass `--write-artifacts`, mutate ledgers, or call execution surfaces
unless that mutation is explicitly part of the task and preceding checks pass.

## Verify and report

```sh
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

Reproduce the relevant report without changing the declared split. Verify
artifact hashes and keep every failed gate explicit. Update docs only after
source, tests, config, artifact, and report agree. Never place an order.
