# Esports Overhaul Execution Plan

Spec: docs/esports-overhaul-proposal-2026-10-22.md. Approved by operator: execute, 2026-10-23.

Goal: execute the evidence-first overhaul, produce corrected serving/replay and series pricing foundations, evaluate learned challengers where evidence allows, and expose unavailable evidence without inventing features or profitability.

Architecture: preserve v6 identity and all protected artifacts. New research engines and artifacts remain distinct from active champions. Existing editable work is copied into isolated branch codex/esports-overhaul-20261023 before execution; publication back to E:/MP uses exact file ownership and concurrent-edit hash checks.

## Global constraints
- Paper only; no orders, ledger mutations, protected artifact rewrites or champion activation.
- Chronological evaluation and result-availability bounds; reused history is development evidence.
- Exact incumbent reproduction before comparative verdicts; no synthetic prices.
- Batch edits before Ruff and micro-targeted test files; never full pytest suite.
- No fresh contextual information without observation timestamps. Unsupported contracts decline explicitly.

## Tasks and ownership
1. Serving (agent): esports.py, esports_replay.py, optional esports_probability.py; focused esports tests. Remove unlearned format scaling, make replay consistent, fail closed on derivative ambiguity, expose availability-aware research replay while preserving exact legacy control.
2. Research (agent): esports_overhaul_research.py, evaluate_esports_overhaul.py, own test file and outputs/esports_overhaul. Read-only unique SQLite cohort; exact incumbent parity; result-available learned rating/logistic candidates across titles; hash-verified research artifacts; no fabricated market profitability.
3. Series (agent): new models/esports_series.py, its test file and API doc. Exact terminal distribution, scenario mixtures, moneyline/spread/total with push, explicit known format, probability invariants.
4. Calibration (root): calibration_pipeline.py and new esports cohort test. Verify within-tier duplicates/order and artifact precedence before changing; retain other-sport behavior. No active calibrator regeneration during research.
5. Integration (root): review all owned files, focused checks, run actual research report, copy exact changes to main workspace after byte-hash conflict checks, record remaining evidence constraints and rollback.

## Interface review
| Tasks | Shared boundary | Ruling |
|---|---|---|
| Serving / Research | _predict legacy vs result_availability=True | Legacy control retained; corrected research explicitly opts in. |
| Serving / Series | No shared files; new series pure API | Existing v6 derivatives preserved pending separate validation. |
| Research / Calibration | Unique event/market/model keys and explicit tier | Flat is canonical where present; no pooled tiers. |
| All / Integration | Shared isolated checkout and source imports | Tests set PYTHONPATH to isolated src; root verifies module origin. |

## Acceptance and progress
- [x] Corrected serving replay preserves incumbent math and rejects unsupported derivative semantics.
- [x] Calibration dedup/order and canonical-tier precedence have direct regression coverage.
- [x] Series distribution passes independent exhaustive/closed-form invariants.
- [x] Actual research execution emits provenance, reproduction, per-row parity, candidate evidence/blocked status and new versioned artifacts where eligible.
- [x] Independent review is resolved and exact owned changes are published without overwriting concurrent edits.

Ruling: user requested execution of staged proposal. Complete all feasible code/evaluation phases; when observational data or reproduction fails, keep the corresponding statistical/promotion gate unsatisfied and publish explicit blocked evidence instead of asserting model improvement. Promotion remains a separate operator decision under approved spec.

Outcome: implementation/evidence deliverables complete; comparative model activation remains BLOCKED_REPRODUCTION (380 exact, 71 source mismatches). See docs/ESPORTS_OVERHAUL_2026-10-23.md.
