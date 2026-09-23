# Roadmap: Model Prediction

## Overview

This milestone closes three operational gaps in a live, real-money multi-sport
prediction/trading system, in order of severity and dependency: (1) no
portfolio-level hard-stop exists if losses cascade, (2) 44 KBO/NPB picks are
stuck open indefinitely because postponed games never settle, (3) only MLB's
market quotes are hash-verified back to their original snapshot, and (4)
accuracy/profitability (CLV, Brier, calibration) are measured only at
promotion time and require a human to pull the dashboard to notice
degradation. All four phases are additive and sport-scoped — none touches
`production_registry.py`, `model_promotion.py`, or `champion_challenger.py`;
promotion/demotion stays human-gated throughout, and no phase introduces a
new runtime dependency.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked INSERTED)

- [ ] **Phase 1: Portfolio Circuit Breaker** - Trading halts automatically when portfolio drawdown crosses threshold, and only a human can resume it
- [ ] **Phase 2: KBO/NPB Postponement Settlement** - Postponed/unplayed KBO/NPB picks settle to void instead of staying stuck open, and the existing backlog is cleared
- [ ] **Phase 3: Archive-to-Contract Linkage Generalization** - Every sport's market quotes are hash-verified and linked to their contract records, the same way MLB's already are
- [ ] **Phase 4: Accuracy/Profitability Tracking Layer + Alerting** - CLV/Brier/calibration are computed and tracked continuously across rolling windows for every sport, and degradation reaches a human as a push alert instead of requiring a dashboard pull

## Phase Details

### Phase 1: Portfolio Circuit Breaker
**Goal**: The system automatically halts new position entry before losses cascade past an acceptable threshold, and only a human can resume trading afterward.
**Depends on**: Nothing (first phase)
**Requirements**: SAFETY-01, SAFETY-02
**Success Criteria** (what must be TRUE):
1. When `max_drawdown` (already computed via `economic_gate.py`) crosses the configured threshold, the system stops entering new positions across the entire portfolio — not just one sport or market.
2. The circuit breaker's tripped state is visible to an operator (dashboard, log, or API) without needing to infer it from an absence of trades.
3. Once tripped, the system stays halted until a human explicitly performs a reset action — no code path silently auto-resumes trading.
**Plans**: TBD

Plans:
- [ ] 01-01: TBD

### Phase 2: KBO/NPB Postponement Settlement
**Goal**: Postponed or unplayed KBO/NPB games no longer leave picks stuck open — they settle to void automatically, and the existing backlog is cleared.
**Depends on**: Nothing (independent of Phase 1; sequenced second on severity/effort, not coupling)
**Requirements**: SETTLE-01, SETTLE-02
**Success Criteria** (what must be TRUE):
1. A KBO/NPB pick tied to a postponed/unplayed game settles automatically as void the next time settlement runs — it never remains open indefinitely.
2. The settlement behavior mirrors soccer's existing `STATUS_POSTPONED`/void pattern, driven by the existing (already-built, previously-unused) `international_baseball_unplayed_index()` detector wired into `_settle_international_baseball_pick`.
3. All 44 currently-stuck-open KBO/NPB rows are settled (voided) once the fix ships — zero stale KBO/NPB open rows remain past a normal settlement cycle.
**Plans**: TBD

Plans:
- [ ] 02-01: TBD

### Phase 3: Archive-to-Contract Linkage Generalization
**Goal**: Every sport's market quotes are hash-verified and linked to their contract records, the same way MLB's are today — not just MLB.
**Depends on**: Nothing structurally (sequenced after Phases 1–2 on effort/risk ordering); Phase 4 depends on this phase's output
**Requirements**: ARCHIVE-01, ARCHIVE-02
**Success Criteria** (what must be TRUE):
1. For any of the 13 non-MLB sports, an operator can hash-verify a pick's market snapshot against its original `PolymarketSnapshotStore` JSONL record via the generalized `market_archive.py`, the same way MLB picks already can.
2. `domain.py::PickRequest`'s `market_snapshot_*` audit fields are populated (not null/empty) for picks across all sports, not only MLB.
3. No second/parallel closing-quote store is introduced — the existing per-sport `PolymarketSnapshotStore` JSONL files remain the single source, verified in place.
**Plans**: TBD

Plans:
- [ ] 03-01: TBD

### Phase 4: Accuracy/Profitability Tracking Layer + Alerting
**Goal**: Model accuracy and profitability are measured continuously (CLV, Brier, calibration) across rolling windows per sport/model, and degradation reaches a human as a push alert instead of requiring a dashboard pull — without the system ever auto-rolling back a model.
**Depends on**: Phase 2 (settlement correctness) and Phase 3 (verified closing-quote linkage) — metrics computed against unsettled or unverified data would be provisional, not gate-grade
**Requirements**: MEASURE-01, MEASURE-02, MEASURE-03, MEASURE-04, MEASURE-05, ALERT-01, ALERT-02
**Success Criteria** (what must be TRUE):
1. For every settled pick, the system computes and persists Closing Line Value (CLV) using the power-method devig, alongside Brier score/log-loss/calibration metrics (via `sklearn.metrics`).
2. An operator can query rolling 30/90/365-day CLV/Brier/calibration windows per sport/model (DuckDB rolling-window view), not just a promotion-time snapshot.
3. These metrics are always-on for every sport/market already in production, not computed only at promotion time.
4. When a promoted model's live rolling Gate 1/Gate 4 metrics diverge meaningfully from its promotion-time frozen baseline, the system raises an alert — and takes no automatic promotion/demotion action.
5. CLV/drift/capture-health degradation (via existing `/api/clv`, `/api/capture_health`, `_drift_check`) and a circuit-breaker trip (Phase 1) both trigger a push notification through the same channel, without requiring the operator to open the dashboard.
**Plans**: TBD

Plans:
- [ ] 04-01: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Portfolio Circuit Breaker | 0/TBD | Not started | - |
| 2. KBO/NPB Postponement Settlement | 0/TBD | Not started | - |
| 3. Archive-to-Contract Linkage Generalization | 0/TBD | Not started | - |
| 4. Accuracy/Profitability Tracking Layer + Alerting | 0/TBD | Not started | - |
