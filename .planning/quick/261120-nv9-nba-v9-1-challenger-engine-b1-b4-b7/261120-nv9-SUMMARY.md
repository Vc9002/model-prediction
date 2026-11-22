---
phase: quick-261120-nv9
plan: 01
subsystem: nba-research
tags: [nba, v9.1, challenger, ablation]
status: complete
requirements: [QUICK-261120-nv9]
---

# v9.1 challenger (Summary)

Details and tables: `outputs/nba/v10_harness/V91_ABLATION.md`.

- Parity: v9.1 with all switches off = golden v9 on 2,811 rows (0 changed).
- Kept: B5 (rolling 300-game current-season baseline, 30-game prior; W tuned on validation),
  B6 for totals only (fitted on train: b2b offense -2.22 / defense +0.54, 3-in-4 -0.59 / -0.12),
  B7 for totals (playoff offset -12.25, train-internal season-out evidence only; validation has
  no playoff games), B4 sign fix (inert at weight 0).
- Dropped: B1 (worsens validation total RMSE 19.068 -> 19.074 on top of B5); B6 for spreads
  (spread RMSE 14.590 -> 14.613).
- Validation: total RMSE 19.423 -> 19.025, bias +3.34 -> -0.34, Brier 0.2689 -> 0.2656;
  spread unchanged (14.591 -> 14.590). corr(model-line, actual-line) stays about 0.
- Artifacts: `config/models/research/nba-{spread,total}-margin-v9-1.json` (self-verifying
  artifact_hash; not in production.yaml; not whitelisted).
