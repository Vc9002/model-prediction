# docs/archive — one-shot and historical documents

Moved here 2026-08-16 during the documentation restructure. These are
investigation records and dated artifacts whose conclusions have been
folded into the live docs (`PROJECT_STATUS.md`, `DEBUG.md`, `CLAUDE.md`)
or superseded by shipped work:

- `SETTLEMENT_GAP.md` — 08-13 investigation; routing fix shipped the
  same day, and the J sqlite cutover (08-14) made the SQLite ledger the
  canonical evidence store. The 08-03→08-13 stale window is documented
  as deliberately not backfilled.
- `WORKING_TREE_CLASSIFICATION.md` — K-prep classification; execution
  status appended 08-15 (all classes handled; tree clean).
- `PRODUCTION_FEATURE_ABLATION_2026-07-22.md` — dated ablation evidence
  for the production-feature harness.
- `MLB_TOTALS_DATA_BACKLOG.md`, `MLB_TOTALS_CONTINUOUS_RESEARCH.md`,
  `EVAL_METHODOLOGY_BRIEF.md`, `INPUT_README.md` — dated research
  notes; the live research contract is `RESEARCH_BACKLOG.md` +
  `MODEL_IMPROVEMENTS.md`.
- `model_audit/`, `mlb_trend_score_v2/`, `leagues/` — historical audit
  and experiment trees (model_audit is still referenced by
  `FEATURE_REGISTRY.md` for per-feature evidence).
