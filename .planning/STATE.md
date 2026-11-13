---
gsd_state_version: "1.0"
current_phase: 1
current_phase_name: Portfolio Circuit Breaker
status: planning
stopped_at: ROADMAP.md and STATE.md written; REQUIREMENTS.md traceability updated to 4-phase structure
last_updated: "2026-11-13T19:18:00.237Z"
last_activity: 2026-11-13
last_activity_desc: ROADMAP.md and STATE.md created from REQUIREMENTS.md and research/SUMMARY.md
state_head: 31f092a95844c9d5178ebfec3f403890c99466cd
progress:
  total_phases: 4
  completed_phases: 0
  total_plans: 2
  completed_plans: 0
  percent: 0
---

<!-- STATE-MD-SCHEMA:START:frontmatter — generated scripts/gen-state-md-docs.cjs src/state-md-schema.cts; do not edit by hand -->
```markdown
---
gsd_state_version: '1.0'
status: planning
progress:[░░░░░░░░░░] 0%
  total_phases: 4
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---
```
<!-- STATE-MD-SCHEMA:END:frontmatter -->

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-09-20 after initialization)

**Core value:** The system must be profitable and accurate in predicting events — forecasts must beat the market, and the trading layer must only fire on genuine edge, not noise.
**Current focus:** Phase 1 (Portfolio Circuit Breaker)

## Current Position

Phase: 1 of 4 (Portfolio Circuit Breaker)
Plan: None yet in current phase
Status: Ready to plan
Last activity: 2026-11-13 - Completed quick task 261113-jrq: Fix NBA availability feature matching foreign teams injuries via abbreviation substrings

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: -
- Trend: -

## Accumulated Context

**Decisions:** (full log in PROJECT.md Key Decisions table)
- Treat existing production system as Validated, not re-scoped from zero.
- "Accurate" is defined relative to market efficiency, not just absolute calibration.
- Settlement/coverage gaps (KBO/NPB, non-MLB archive linkage) are in-scope blockers to the core value.
- Roadmap split research's proposed 3-phase structure into 4 phases: Circuit Breaker and KBO/NPB Settlement are independent capabilities (different requirement categories, no dependency between them) bundled by research only for effort/severity reasons — split for cleaner, single-capability delivery boundaries.

**Pending Todos:**
None yet.

**Blockers/Concerns:**
- Phase 4 (Measurement/Alerting) research flag: no notification channel (Slack/webhook/email) has been selected yet — needs a short scoping decision during Phase 4 planning, not deep research.
- Phase 3 (Archive Linkage) research flag: per-sport team-alias/market-slug matching risk is real (soccer's ~45-competition collision history) — each sport's rollout may need a short targeted verification pass on its own identifier quirks.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 261111-o7p | Fix auto-buyer rejection telemetry miscategorization and add state-file conflict detection | 2026-11-11 | d349715b | [261111-o7p-fix-auto-buyer-rejection-telemetry-misca](./quick/261111-o7p-fix-auto-buyer-rejection-telemetry-misca/) |
| 261111-tx4 | Fix NCAAB paper-trial duplicate-buy bug: dedup keyed only on pick_id misses re-forecast churn of the same game/selection/line | 2026-11-11 | 69856d6b | [261111-tx4-fix-ncaab-paper-trial-duplicate-buy-bug-](./quick/261111-tx4-fix-ncaab-paper-trial-duplicate-buy-bug-/) |
| 261111-w1m | Fix scripts/audit_best_picks.py hardcoded CONFIRMED_BEST overrides; recompute verdicts live from ledgers.db with verified-quote gate | 2026-11-11 | 3ca0a4c5 | [261111-w1m-fix-scripts-audit-best-picks-py-audit-ca](./quick/261111-w1m-fix-scripts-audit-best-picks-py-audit-ca/) |
| 261111-wv9 | Exclude MANUAL-* buyer-ledger rows from auto-buyer performance summary, model quarantine ROI, and dashboard KPIs | 2026-11-12 | b2ad1781 | [261111-wv9-audit-every-script-that-computes-model-e](./quick/261111-wv9-audit-every-script-that-computes-model-e/) |
| 261112-01r | Audit legacy research/gated_research tier readers; fix calibration pipeline and edge scanner treating frozen rows as live evidence | 2026-11-12 | 8a544cbb | [261112-01r-audit-every-remaining-reader-of-the-lega](./quick/261112-01r-audit-every-remaining-reader-of-the-lega/) |
| 261112-0li | Generalize NCAAB dedup fix audit: confirmed no other sport has the bug; added mutation-proven regression tests pinning main-buyer identity dedup | 2026-11-12 | b2ff76a1 | [261112-0li-commits-b7a25ddf-69856d6b-5e2212ef-fixed](./quick/261112-0li-commits-b7a25ddf-69856d6b-5e2212ef-fixed/) |
| 261112-118 | Add process-local threading.Lock to ledger.py for intra-process mutation contention; document deferred XLSX-rebuild-split decision | 2026-11-12 | 222584ed | [261112-118-in-ledger-py-1-every-ledger-mutation-tak](./quick/261112-118-in-ledger-py-1-every-ledger-mutation-tak/) |
| 261112-112 | Add AuditLog.reversed_events() bounded backward reader; fix dashboard audit-tail and polymarket_execute single_order gate to stop fully materializing events.jsonl (428MB) | 2026-11-12 | d06d8db5 | [261112-112-the-events-jsonl-bug-audit-py-fixed-2026](./quick/261112-112-the-events-jsonl-bug-audit-py-fixed-2026/) |
| 261112-111 | Make audit_ledger_pnl.py fail its exit code on unresolved model-ledger drift it already computed; remove corrupt stray xlsx; document generated-on-demand rejection rationale | 2026-11-12 | cafbe94c | [261112-111-evaluate-whether-the-per-model-xlsx-mirr](./quick/261112-111-evaluate-whether-the-per-model-xlsx-mirr/) |
| 261112-0xd | NCAAF: close every open CFB finding of the 2026-11-11 model review (serve v4 from artifact, ask integrity, artifact Main gates, venue weather, postseason week, QB channel, 200k sims, source code_revision) | 2026-11-12 | f6fa91d1 | [261112-0xd-ncaaf-fix-all-open-cfb-findings-from-the](./quick/261112-0xd-ncaaf-fix-all-open-cfb-findings-from-the/) |
| 261112-nc6 | Close the NCAAB findings of the 2026-11-11 model review: total baseline mismatch, artifact drift, coherent moneyline, early-season compression, push mass; validation-selected parameters | 2026-11-12 | e32335c7 | [261112-nc6-ncaab-model-review-fixes](./quick/261112-nc6-ncaab-model-review-fixes/) |
| 261112-11j | Fix OPEN NBA findings from MODEL_REVIEW_2026-11-11 (#3 moneyline path, #6, #8d, #14) plus three new: totals serving-calibrator provenance guard, nba-moneyline-v9 placeholder artifact + whitelist, Main no longer relabels downgraded paper calls | 2026-11-12 | 83b76fc7 | [261112-11j-fix-open-nba-findings-from-model-review-](./quick/261112-11j-fix-open-nba-findings-from-model-review-/) |
| 261112-2s7 | Widen xlsx_ledger.py except clause to BaseException; add crash-safe orphan temp-file sweep to both xlsx_ledger.py and model_ledger.py; document real leak root cause (hard worker-timeout kill, not just the except asymmetry) | 2026-11-12 | 0a934d6e | [261112-2s7-an-except-exception-src-model-prediction](./quick/261112-2s7-an-except-exception-src-model-prediction/) |
| 261112-2s5 | Make auto-buyer bought-index fail closed on any unreadable dedup source, aggregating all failures, instead of silently treating a read error as an empty index | 2026-11-12 | 5298dd33 | [261112-2s5-build-bought-index-in-src-model-predicti](./quick/261112-2s5-build-bought-index-in-src-model-predicti/) |
| 261112-2s6 | Require exact same-UTC-date match in edge scanner's _lookup_model_prob instead of an unbounded cross-date fallback; live data showed 1,070/6,037 matches (17.7%) were genuinely different events | 2026-11-12 | 9c022311 | [261112-2s6-src-model-prediction-portfolio-polymarke](./quick/261112-2s6-src-model-prediction-portfolio-polymarke/) |
| 261112-kj3 | Refit NBA moneyline as nba-elo-trend-lr-v5 (walk-forward, restored fit construction); registered as challenger, not promoted; same-holdout Brier 0.1889 vs v4 0.1892 | 2026-11-12 | 9bf925e4 | [261112-kj3-refit-nba-moneyline-nba-elo-trend-lr-v5-](./quick/261112-kj3-refit-nba-moneyline-nba-elo-trend-lr-v5-/) |
| 261112-m31 | NCAAF cfb-structural-v5 challenger: fitted margin/total mean calibration, slice SD table, residual total SD on walk-forward splits; artifact + class + harness, research status | 2026-11-12 | d86876e7 | [261112-m31-ncaaf-v5-challenger-fit-margin-calibrati](./quick/261112-m31-ncaaf-v5-challenger-fit-margin-calibrati/) |
| 261112-mpz | Full pipeline verification: reroute health/daily/dashboard odds readers to the runtime root, stop the schedule rewriting frozen config/models artifacts, single-thread BLAS in ingestion | 2026-11-12 | c96f28cf | [261112-mpz-fix-odds-root-split-brain-and-frozen-art](./quick/261112-mpz-fix-odds-root-split-brain-and-frozen-art/) |
| 261113-r2d | Disable NBA totals in the auto-buyer: nba:total categorical gate + nba-total-margin-v9 off the whitelist; live state updated (v173->v174); 11 settled NBA total paper rows left in place | 2026-11-13 | c3522f2a | [261113-r2d-disable-nba-totals-in-the-paper-auto-buyer](./quick/261113-r2d-disable-nba-totals-in-the-paper-auto-buyer/) |
| 261113-j3m | Promote nba-elo-trend-lr-v5 to NBA moneyline champion (promo-20261113T184744Z-6b0b74); v4 rollback; model.yaml serving pointer moved with it | 2026-11-13 | ec3210a2 | [261113-j3m-promote-nba-elo-trend-lr-v5-to-nba-money](./quick/261113-j3m-promote-nba-elo-trend-lr-v5-to-nba-money/) |
| 261113-jb1 | Harden RuntimeLedgerStore against transient sqlite disk I/O error under memory pressure | 2026-11-13 | de9c71c3 | [261113-jb1-harden-runtimeledgerstore-against-transi](./quick/261113-jb1-harden-runtimeledgerstore-against-transi/) |
| 261113-jf1 | Pull nba-spread-margin-v9 (and total v9) from the paper auto-buyer whitelist after the 2026-11-12 loss review; persisted state updated via versioned save | 2026-11-13 | c648e048 | [261113-jf1-pull-nba-v9-spread-and-total-from-the-paper-wh](./quick/261113-jf1-pull-nba-v9-spread-and-total-from-the-paper-wh/) |
| 261113-j4w | Promote cfb-structural-v5 to NCAAF champion (Main + Flat) via model_promotion; cfb_model() follows the champion map; replay/forecast/naming/whitelist wiring | 2026-11-13 | 02f45736 | [261113-j4w-promote-cfb-structural-v5-to-ncaaf-champ](./quick/261113-j4w-promote-cfb-structural-v5-to-ncaaf-champ/) |
| 261113-jpw | System-usage pass: bound the dashboard parsed-snapshot cache (LRU by on-disk bytes); measured memory/disk drivers and recorded recommendations | 2026-11-13 | 4fb785cd | [261113-jpw-bound-the-dashboard-parsed-snapshot-cache](./quick/261113-jpw-bound-the-dashboard-parsed-snapshot-cache/) |
| 261113-jqw | Whitelist nba-elo-trend-lr-v5 in the auto-buyer (code constant via 4fb785cd + live auto_buyer_state.json v179->180); inert until evidence_status leaves historical_only | 2026-11-13 | df4b3d68 | [261113-jqw-whitelist-the-serving-nba-moneyline-cham](./quick/261113-jqw-whitelist-the-serving-nba-moneyline-cham/) |
| 261113-jrq | Fix NBA availability feature matching foreign teams injuries via abbreviation substrings | 2026-11-13 | 31f092a9 | [261113-jrq-fix-nba-availability-feature-matching-fo](./quick/261113-jrq-fix-nba-availability-feature-matching-fo/) |

## Milestone History

*(none yet)*

## Deferred Items

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| *(none)* | | | | |

## Session Continuity

Last session: 2026-09-22 — Roadmap creation
Stopped at: ROADMAP.md and STATE.md written; REQUIREMENTS.md traceability updated to 4-phase structure
Resume file: None
