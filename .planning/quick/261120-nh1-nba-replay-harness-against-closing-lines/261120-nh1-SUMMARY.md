---
phase: quick-261120-nh1
plan: 01
subsystem: nba-research
tags: [nba, replay, harness, closing-lines, B8]
status: complete
requirements: [QUICK-261120-nh1]
plan_head_before: a3cbdac7cc19ac94de4f186bcd1aade458ddc1df
---

# NBA replay harness (Summary)

- `src/model_prediction/nba_replay_harness.py`: day-by-day PIT replay (`replay`), scoring
  (`score_engine`, `gap_statistics`, `score_market`, `score_moneyline`, `bias_table`), golden
  helpers. `scripts/nba_v10/replay_harness.py golden|replay|diff|score`. Engines are named in
  `src/model_prediction/nba_v10_engines.py`.
- Split pinned: train < 2025-11-03 <= validation < 2026-02-23 (60/20/20 complete dates over all
  replayable dates, holdout capped at the registry holdout; recomputation drift is reported,
  not applied).
- Acceptance: v9 reproduces the review exactly (train 2049 / val 762; val RMSE 14.59 vs 13.97
  margin, 19.42 vs 18.18 total; val total bias +3.34; ML Brier line 0.2122 vs model 0.2242).
- Golden v9: 2,811 rows, `outputs/nba/v10_harness/golden_v9_rows.jsonl`, inputs sha256 in
  `golden_v9_meta.json`. Replay of one engine takes about 15 s.
- v9 validation (exploratory): corr(model-line, actual-line) spread -0.015 [-0.085, +0.053],
  total -0.018 [-0.089, +0.050]; lambda spread -0.029 [-0.273, +0.214], total +0.009
  [-0.198, +0.201]. No information beyond the close.
