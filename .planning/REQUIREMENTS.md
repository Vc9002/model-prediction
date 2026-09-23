# Requirements: Model Prediction

**Defined:** 2026-09-22
**Core Value:** system must profitable accurate in predicting events — forecasts must beat market, trading layer only fire on genuine edge.

## v1 Requirements

Requirements milestone. Each maps roadmap phases. All four phases additive sport-scoped — none touches `production_registry.py`, `model_promotion.py`, or `champion_challenger.py`; promotion/demotion stays human-gated throughout.

### Safety

- [ ] **SAFETY-01**: System portfolio-level circuit breaker halts new position entry when `max_drawdown` (already computed via `economic_gate.py`) crosses configured threshold
- [ ] **SAFETY-02**: Circuit breaker trip state visible requires explicit human action reset — no silent auto-resume

Settlement

- [ ] **SETTLE-01**: KBO/NPB picks postponed/unplayed games settle via void (not stuck open indefinitely), using existing `international_baseball_unplayed_index()` detection wired into `_settle_international_baseball_pick`, mirroring soccer's existing `STATUS_POSTPONED` pattern
- [ ] **SETTLE-02**: existing 44 stuck-open KBO/NPB rows are resolved (settled explicitly voided) once postponement branch ships

### Archive Linkage

- [ ] **ARCHIVE-01**: Every sport's market quotes hash-verified linked contract records same way MLB's today, via generalized `market_archive.py` applied in place each sport's existing `PolymarketSnapshotStore` JSONL files
- [ ] **ARCHIVE-02**: `domain.py::PickRequest`'s `market_snapshot_*` audit fields populated every sport, not just MLB

### Measurement

- [ ] **MEASURE-01**: System computes persists Closing Line Value (CLV) power-method
- [ ] **MEASURE-02**: Brier score/log-loss/calibration metrics (via `sklearn.metrics`)
- [ ] **MEASURE-03**: 30/90/365-day CLV/Brier/calibration per sport/model, DuckDB rolling-window
- [ ] **MEASURE-04**: per-sport/per-market always-on, not promotion-time-only
- [ ] **MEASURE-05**: Gate 1/Gate 4 metrics against promotion-time frozen baseline alerts on divergence — alert-only, never auto-rollback

### Alerting

- [ ] **ALERT-01**: CLV/drift/capture-health degradation (via existing `/api/clv`, `/api/capture_health`, `_drift_check`) triggers push notification instead requiring human pull/query dashboard
- [ ] **ALERT-02**: circuit breaker trip (SAFETY-01) triggers immediate alert through same notification channel

## v2 Requirements

Deferred future release. Tracked but not in current roadmap.

### Sizing & Parity

- **SIZING-01**: CLV-informed dynamic Kelly sizing — automatically shrink Kelly fraction trailing CLV degrades, before monthly gate catches it
- **PARITY-01**: Shadow-to-live CLV parity check — verify shadow (paper) CLV matches live (real execution) CLV post-promotion, catch slippage (currently too few promoted sports meaningful sample)

### Cross-Sport & Regime Analysis

- **CROSS-01**: Cross-sport drift correlation detection simultaneous degradation across unrelated sports flags systemic pipeline bug rather N independent model failures
- **REGIME-01**: Regime-change stress segmentation (needs more historical line-movement depth per sport before actionable)

### Gate Refinement

- **GATE-01**: Revisit Gate 4's CLV criterion pass-rate (≥50%) magnitude-weighted, once enough tracked CLV history exists validate change

## Out Scope

Explicitly excluded. Documented prevent scope creep.

| Feature | Reason |
|---------|--------|
| New backtesting framework (backtrader, zipline, sports-betting, Flumine) | Explicitly evaluated rejected — codebase's own `validation.py` walk-forward harness already correctly implements domain-specific point-in-time-correctness contract; retrofitting a general-purpose framework would not preserve it |
| Auto-rollback on drift/decay | Promotion/demotion stays human-gated; MEASURE-05's alerting is alert-only |
| Expanding to new sports beyond current 8+ | Per PROJECT.md's existing scope boundary — focus is accuracy/profitability of existing coverage, not breadth |
| Rebuilding dashboard UI scratch | Per PROJECT.md's existing scope boundary — existing dashboard is functional |
| Non-Polymarket execution venues | Per PROJECT.md's existing scope boundary — future milestone |
| Hardcoding external CLV/Kelly threshold rules of thumb as gate law | PITFALLS.md/STACK.md flag these as MEDIUM-confidence industry norms, not to be hardcoded without validating against system's own historical data |

## Traceability

Which phases cover requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SAFETY-01 | Phase 1 | Pending |
| SAFETY-02 | Phase 1 | Pending |
| SETTLE-01 | Phase 2 | Pending |
| SETTLE-02 | Phase 2 | Pending |
| ARCHIVE-01 | Phase 3 | Pending |
| ARCHIVE-02 | Phase 3 | Pending |
| MEASURE-01 | Phase 4 | Pending |
| MEASURE-02 | Phase 4 | Pending |
| MEASURE-03 | Phase 4 | Pending |
| MEASURE-04 | Phase 4 | Pending |
| MEASURE-05 | Phase 4 | Pending |
| ALERT-01 | Phase 4 | Pending |
| ALERT-02 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 13 total
- Mapped to phases: 13
- Unmapped: 0 ✓

---
*Requirements defined: 2026-09-22*
*Last updated: 2026-09-22 after roadmap creation (research's 3-phase grouping split into 4 phases: Circuit Breaker and KBO/NPB Settlement separated as independent, single-capability phases)*
