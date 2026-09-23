# Requirements: Model Prediction

**Defined:** 2026-09-22
**Core Value:** The system must be profitable and accurate in predicting events — forecasts must beat the market, and the trading layer must only fire on genuine edge.

## v1 Requirements

Requirements for this milestone. Each maps to roadmap phases. All three phases are additive and sport-scoped — none touches `production_registry.py`, `model_promotion.py`, or `champion_challenger.py`; promotion/demotion stays human-gated throughout.

### Safety

- [ ] **SAFETY-01**: System has a portfolio-level circuit breaker that halts new position entry when `max_drawdown` (already computed via `economic_gate.py`) crosses a configured threshold
- [ ] **SAFETY-02**: Circuit breaker trip state is visible and requires explicit human action to reset — no silent auto-resume

### Settlement

- [ ] **SETTLE-01**: KBO/NPB picks for postponed/unplayed games settle via void (not stuck open indefinitely), using the existing `international_baseball_unplayed_index()` detection wired into `_settle_international_baseball_pick`, mirroring soccer's existing `STATUS_POSTPONED` pattern
- [ ] **SETTLE-02**: The existing 44 stuck-open KBO/NPB rows are resolved (settled or explicitly voided) once the postponement branch ships

### Archive Linkage

- [ ] **ARCHIVE-01**: Every sport's market quotes are hash-verified and linked to contract records the same way MLB's are today, via a generalized `market_archive.py` applied in place to each sport's existing `PolymarketSnapshotStore` JSONL files
- [ ] **ARCHIVE-02**: `domain.py::PickRequest`'s `market_snapshot_*` audit fields are populated for every sport, not just MLB

### Measurement

- [ ] **MEASURE-01**: System computes and persists Closing Line Value (CLV) per settled pick, using power-method devig to convert market price to comparable implied probability
- [ ] **MEASURE-02**: System computes and persists Brier score and log loss per settled pick (via `sklearn.metrics`)
- [ ] **MEASURE-03**: System provides rolling 30/90/365-day CLV/Brier/calibration views per sport/model (additive `ledger_records` columns + DuckDB rolling-window views)
- [ ] **MEASURE-04**: A standing per-sport/per-market profitability dashboard panel is always-on, promoted from a promotion-time-only check to a continuously viewable panel
- [ ] **MEASURE-05**: A scheduled job compares each promoted model's live rolling Gate 1/Gate 4 metrics against its promotion-time frozen baseline and alerts on divergence — alert-only, never auto-rollback

### Alerting

- [ ] **ALERT-01**: CLV/drift/capture-health degradation (via existing `/api/clv`, `/api/capture_health`, `_drift_check`) triggers a push notification instead of requiring a human to pull/query the dashboard
- [ ] **ALERT-02**: A circuit breaker trip (SAFETY-01) triggers an immediate alert through the same notification channel

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Sizing & Parity

- **SIZING-01**: CLV-informed dynamic Kelly sizing — automatically shrink the Kelly fraction as trailing CLV degrades, before the monthly gate catches it
- **PARITY-01**: Shadow-to-live CLV parity check — verify shadow (paper) CLV matches live (real execution) CLV post-promotion, to catch slippage (currently too few promoted sports for a meaningful sample)

### Cross-Sport & Regime Analysis

- **CROSS-01**: Cross-sport drift correlation detection — simultaneous degradation across unrelated sports flags a systemic pipeline bug rather than N independent model failures
- **REGIME-01**: Regime-change stress segmentation (needs more historical line-movement depth per sport before this is actionable)

### Gate Refinement

- **GATE-01**: Revisit Gate 4's CLV criterion from pass-rate (≥50%) to magnitude-weighted, once enough tracked CLV history exists to validate the change

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| New backtesting framework (backtrader, zipline, sports-betting, Flumine) | Explicitly evaluated and rejected — this codebase's own `validation.py` walk-forward harness already correctly implements the domain-specific point-in-time-correctness contract; retrofitting an external framework risks fragmenting that contract |
| Full Kelly bankroll sizing | Drawdown profile (50% chance of a 50%+ drawdown) is unacceptable for live capital — fractional Kelly (already implemented) stays the standard |
| Auto-rollback on drift/decay signals | Project's explicit governance philosophy keeps promotion/demotion human-gated; MEASURE-05's decay monitor is alert-only by design |
| Expanding to new sports beyond current 14 | Per PROJECT.md's existing scope boundary — focus is accuracy/profitability of existing coverage, not breadth |
| Rebuilding the dashboard UI from scratch | Per PROJECT.md's existing scope boundary — existing dashboard is functional |
| Non-Polymarket execution venues | Per PROJECT.md's existing scope boundary — future milestone |
| Hardcoding external CLV/Kelly threshold rules of thumb as gate law | PITFALLS.md and STACK.md flag these as MEDIUM-confidence industry norms, not to be hardcoded without validating against this system's own historical data |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SAFETY-01 | Phase 1 | Pending |
| SAFETY-02 | Phase 1 | Pending |
| SETTLE-01 | Phase 1 | Pending |
| SETTLE-02 | Phase 1 | Pending |
| ARCHIVE-01 | Phase 2 | Pending |
| ARCHIVE-02 | Phase 2 | Pending |
| MEASURE-01 | Phase 3 | Pending |
| MEASURE-02 | Phase 3 | Pending |
| MEASURE-03 | Phase 3 | Pending |
| MEASURE-04 | Phase 3 | Pending |
| MEASURE-05 | Phase 3 | Pending |
| ALERT-01 | Phase 3 | Pending |
| ALERT-02 | Phase 3 | Pending |

**Coverage:**
- v1 requirements: 13 total
- Mapped to phases: 13
- Unmapped: 0 ✓

---
*Requirements defined: 2026-09-22*
*Last updated: 2026-09-22 after initial definition*
