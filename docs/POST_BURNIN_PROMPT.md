# Post-Burn-In Execution Prompt

Run this prompt (paste to Claude Code) once burn-in passes on
2026-08-18 05:25 UTC. It is a self-contained execution brief; every
step points at the doc that carries the depth.

---

You are executing the post-burn-in research sequence for the
model-prediction system. Verified state as of 2026-08-17: main at
`34add4e`, research worktree `/Users/vincentc9002/worktrees/mlb-research`
(branch `research/mlb-v8-reproduction`) synced from main; burn-in days
0–2 recorded PASS in `docs/BURN_IN.md`; v8 row-parity baseline measured
(`docs/V8_PARITY_BASELINE_2026-08-17.md` in the worktree — probability
parity max |Δ| 0.0006, call-set identity exact 148/148); 1879 tests
green; ruff clean on src/+tests/.

**Standing rules (bind every step below):**
- Accuracy-first: proper scores (Brier/log-loss/RPS/ECE) decide every
  model decision. Economic metrics are reported but never gating
  (operator directive 2026-08-17).
- No promotion without the shadow-first chain: walk-forward → OOF →
  same-event → bootstrap → freeze → prospective shadow → settled
  comparison → `PROMOTION_CANDIDATE` → operator decides. No shortcuts.
- One change at a time in ablations. Never kitchen-sink variants.
- Every harness run includes the v8 control-reproduction gate first;
  if the control can't reproduce its own shipped numbers, the run is
  void.
- Pre-register hypothesis + thresholds in the experiment registry
  BEFORE each run. Adjusting thresholds after results exist is
  forbidden.
- Honest nulls are expected outcomes — record them as successful
  negative results, never paper over them.
- Sources policy: adoptions rest on peer-reviewed/textbook/open-source
  evidence only (see `docs/RESEARCH_LITERATURE_DIVE_*.md` tiering).
- Research writes stay in the worktree; never write to the runtime
  root or mutate live ledgers from research tooling.

## Step 0 — Burn-in day 3 (final acceptance)

1. Run `scripts/burn_in_checks.sh` plus manual checks 1–7 and
   `PRAGMA integrity_check` on the runtime-root SQLite DBs (see
   `docs/BURN_IN.md` for the contract). Requirements: no unexplained
   DOWN/DEGRADED, no repo DBs, one runtime, one scheduler, one
   dashboard, no duplicate runs, no corrupted jobs, clean git tree.
2. Record day 3 in `docs/BURN_IN.md`. On pass:
   `INFRASTRUCTURE_CONSOLIDATION = ACCEPTED`. Infrastructure work stops
   unless a real defect appears.
3. If any check fails: hold, diagnose, re-check. Do not proceed.

## Step 1 — Record the three gate decisions (registry, BEFORE Step 2)

From `docs/V8_PARITY_BASELINE_2026-08-17.md`, record in the experiment
registry (with operator confirmation where a default is not chosen):
1. F-bucket policy (accent-fix rows) — default: historical behavior is
   the control; fixed matching is a classified, excluded deviation.
2. A-bucket 2-row policy — default: exclude with documented note
   (rows are unidentifiable, not contradictory).
3. Acceptance-criteria revision — proposed: feature deltas acceptable
   when (a) every row carries a taxonomy class + cause, (b) zero
   unclassified, (c) row-probability |Δ| < 0.005 on ≥98% of rows,
   (d) call-set identity exact.

## Step 2 — v8 reproduction hard gate

Run in the worktree (main's venv):
`scripts/mlb_v8_row_parity.py` and
`scripts/mlb_v8_feature_parity_sample.py`. Registry entry:
`mlb-v8-reproduction-final`. Two outcomes only:
- PASS → proceed to Step 3.
- FAIL → stop; fix harness/data lineage; do NOT run v9 ablations.
Do not refit v8 or "fix" its park-factor leak — v8 is the frozen
control (see `docs/V9_RESEARCH_PLAN.md` §3.2).

## Step 3 — v9 ablation matrix (A–N, one change at a time)

Order: A v8 control; B FIP; C K-BB%; D FIP+K-BB%; E remove trend;
F raw trend; G Elo-residualized trend; H bullpen talent; I bullpen
availability; J both; K PIT park (`park_factor_pit`); L PIT weather;
M projected lineup; N confirmed lineup.
Rules, thresholds, and the per-run control gate:
`docs/V9_RESEARCH_PLAN.md` §4 + §0.5 (KEEP requires ΔBrier < −0.002,
≥4/5 folds agreeing, bootstrap P ≥ 0.90, coverage ≥ 90% — pre-
registered; adjust only before the run, in the registry). Report one
paragraph per ablation: verdict letter + numbers + next experiment.
Abort criterion: if no feature reaches KEEP after the full matrix, v8
stays champion — record as a successful negative result and move to
the distribution migration (Step 7).

## Step 4 — v9-LR, then same-table XGBoost

Retained features → regularized LR (`mlb-v9-lr`). Then XGBoost on the
same events/features, compared on OOF proper scores. LR wins if XGB
only improves accuracy while degrading Brier/LogLoss
(`docs/V9_RESEARCH_PLAN.md` §5).

## Step 5 — Calibration

Identity / Platt / Temperature / Isotonic on OOF predictions only;
Identity is a legitimate winner (`docs/V9_RESEARCH_PLAN.md` §6).

## Step 6 — Freeze, prospective shadow, promotion governance

Freeze the surviving v9 configuration; run prospective shadow picks on
the flat ledger vs live quotes; settled comparison at a pre-registered
minimum (≥30 settled picks, N recorded BEFORE the shadow starts);
`compare-champion` vs v8; status → `PROMOTION_CANDIDATE` only on the
pre-registered criteria; operator decides (`docs/V9_RESEARCH_PLAN.md`
§7).

## Step 7 — Distribution migration (parallel with Step 6)

Execute `docs/DISTRIBUTION_MIGRATION_PLAN.md` for MLB in its build
order: NB joint engine promotion (existing `simulate_game(method=...)`
machinery) → air-density physics layer (shadow module built
2026-08-17 in the worktree: `features/air_density.py` — needs an
elevation table + walk-forward validation before wiring) → starter-IP
distribution → time-through-order → ZINB test → joint ML/RL/totals →
log-score stacking blend. Each step pre-registered in the registry
with proper-score deltas vs the current model.

## Step 8 — Post-MLB queue (in order)

WNBA v5 paired test → WNBA pace×ORtg + rest (from
`docs/DISTRIBUTION_MIGRATION_PLAN.md` §2.2) → NFL calibration
challengers → tennis v2 → soccer league split + Dixon-Coles engine
(xG source pending) → esports Glicko swap (draft features pending
data) → KBO/NPB starters. Details: `docs/POST_MLB_RESEARCH_PLANS.md`.

## Doc map (depth lives here)

- Burn-in contract: `docs/BURN_IN.md`
- v9 gate + matrix + thresholds + shadow chain: `docs/V9_RESEARCH_PLAN.md`
- Per-market recipes + published numbers: `docs/PREDICTION_PLAYBOOKS.md`
- Gap analysis + build orders + blockers: `docs/DISTRIBUTION_MIGRATION_PLAN.md`
- Per-sport plans: `docs/POST_MLB_RESEARCH_PLANS.md`
- Research evidence + source tiers: `docs/RESEARCH_LITERATURE_DIVE_{1,2,3}_2026-08-17.md`
- Row-parity baseline + three decisions: worktree `docs/V8_PARITY_BASELINE_2026-08-17.md`
- Experiment template + retention rules: `docs/ROADMAP.md` (backlog merged 2026-08-22; open items live in ROADMAP.md)
