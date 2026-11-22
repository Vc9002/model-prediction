---
quick_id: 261120-nip
description: NCAAB improvement program from the 2026-11-20 review (serving fixes + research challengers graded on closing lines)
---
# NCAAB improvement program (operator request 2026-11-20: "do all of this")

Evidence: `docs/ncaab_review_2026-11-20/` (loss forensics, code review, external research).
Operator decisions 2026-11-20: Torvik data via the cbbdata API (operator supplies `CBBDATA_API_KEY`;
never scrape barttorvik.com, its robots.txt blocks AI crawlers); grade on ESPN closing lines only
(no CollegeBasketballData key).

## Lanes
- **A. Serving (orchestrator, serial, after the 00:30 Daily and 01:00 Calibrate runs):**
  1. Mascot-drop team matching in `college_basketball_forward.py` (spread-first market leaves mascot
     empty -> D1 teams declined NO_CALL_NON_D1). Regression test proven against the revert; slate replay:
     every previously resolved event resolves identically; each newly resolved school checked.
  2. Record v7: own identity for the v7 margin engine (id or artifact hash), promotion record through
     `model_promotion`, commit the `margin_engine: v7` yaml line; forward check split on
     `feature_basis.margin_engine`. Must not change served probabilities (slate replay). Old rows not relabelled.
  3. Ledger gaps: `model_artifact_hash` populated for NCAAB; served probability stored without breaking
     readers of `model_probability` (calibrator fit source checked first); pre-tip close for CLV.
- **B. Grader + protocol (agent, first):** shared closing-line grader over the existing ESPN line archive
  and `ncaab-residual-rule-1`; pre-registered windows and candidate grid committed before any outcome join.
- **C. Data (agent):** Kaggle Massey ordinals (historical, PIT by RankingDayNum, fail-closed crosswalk);
  cbbdata client (fail closed without key); box-score coverage 2021-22..2024-25 + backfill if missing.
- **D. Candidates (agents, after B):** moneyline elo_weight 0 vs 0.5 on the served blend; margin-dependent
  scale, blowout-capped target, early-season scale; opponent-adjusted per-possession spread ratings;
  preseason prior + external PIT ratings.
- Winners register as shadow challengers under a new pin. Frozen v7 constants and `config/models/*.json`
  are not edited. Promotion is a separate operator decision on the evidence table.

## Rules
Shared checkout, no worktrees, other sessions have uncommitted edits: agents do not commit; the
orchestrator stages explicit paths. No full pytest; targeted `-o addopts='' -q`; ruff on touched files.
Heavy multi-season fits paused 23:55-01:15 local (host memory pressure at the 00:30 run).
November 2026 games are a locked holdout. Never American odds.
