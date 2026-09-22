# Project Research Summary

**Project:** Model Prediction — accuracy/profitability measurement & tracking layer
**Domain:** Live, multi-sport prediction-market trading system (quant sports-betting / Polymarket execution)
**Researched:** 2026-09-22
**Confidence:** HIGH

## Executive Summary

This is not a greenfield build — it's a live, real-money production system (MLB and WNBA moneyline promoted, 12 other sports in research/shadow) with an unusually mature promotion-gate architecture already in place (Phase 23's market-relative, four-gate evidence battery; walk-forward-only validation; fail-closed human-gated promotion). The research question this milestone actually needs to answer is narrower than "how do professional systems measure accuracy and profitability" in the abstract: it's "what is this specific, already-sophisticated system still missing to close its own documented gaps." All four research passes converge on the same finding — the missing pieces are consistently **extensions of patterns already proven once in this codebase (usually for MLB), not new subsystems, new dependencies, or new architecture.** No new runtime dependency is required anywhere in this research.

The recommended approach is threefold and maps directly onto the three gaps ARCHITECTURE.md identifies as sharing one root cause (built-once-not-generalized): (1) close the KBO/NPB postponement-settlement gap by wiring an already-built, already-tested detection function (`international_baseball_unplayed_index`) into `settle.py`, mirroring the soccer postponement branch that already exists; (2) generalize MLB's hash-verified market-quote archive (`MarketOddsSnapshotStore`) into a sport-parameterized module and add the same hash/record-id fields to the thin `PolymarketSnapshotStore` all 13 other sports currently use unverified; (3) add a persisted CLV/Brier/log-loss/calibration rollup layer as additive columns on the existing SQLite ledger plus DuckDB rolling-window views — computing metrics scikit-learn already ships (Brier, log loss, calibration curves) against data the ledger already captures (decision-time and closing-time market probabilities), with Closing Line Value (CLV) as the standard quant-literature bridge metric this system captures the raw ingredients for but doesn't yet report as a first-class tracked series.

The key risk, surfaced consistently across FEATURES.md and PITFALLS.md, is **not** model accuracy in isolation — this system already learned that lesson concretely (WNBA totals: reasonable-looking Brier score, decisively beaten by the market once evaluated on real executable lines) and built Gate 1/Gate 4 specifically to prevent recurrence. The live open risks are operational: no portfolio-level circuit breaker/kill switch exists anywhere in `src/` (the single largest safety gap for a live-money system), no push-based alerting exists on CLV/drift degradation (everything is pull/dashboard-query today), and there is no scheduled comparison of a promoted model's live rolling metrics against its promotion-time frozen baseline (model decay/regime change is a genuinely open gap, not yet mitigated). Mitigation for all three is additive and low-risk: wire a hard-stop into `auto_executor.py` off the already-computed `max_drawdown`, add a notification layer on top of already-built `/api/clv`/`/api/capture_health`, and schedule a rolling comparison job that alerts but never auto-rolls-back — consistent with this project's explicit governance philosophy that promotion/demotion stays human-gated.

## Key Findings

### Recommended Stack

No new dependencies. scikit-learn (already installed) supplies everything needed for Brier score, log loss, and calibration curves/fixing — the gap is systematic time-series tracking of these numbers per model/sport/window, not tooling. The single most quant-standard signal this system captures the raw data for but doesn't compute is **Closing Line Value (CLV)**: professional consensus treats CLV, not win rate or raw ROI, as the primary short-run skill signal because it stabilizes at far smaller sample sizes (~100-200 bets vs. thousands for ROI). Devigging should use the power method (closed-form, ~10 lines, no dependency) rather than simple multiplicative devigging or the more complex Shin method, which is unnecessary for this system's mostly-two-way MLB/WNBA moneyline markets. Bankroll sizing should stay hand-rolled fractional Kelly (already the case via `polymarket_kelly.py`'s "Real-Edge Quarter-Kelly") — never full Kelly, whose drawdown profile (50% chance of a 50%+ drawdown) is unacceptable for live capital. General-purpose backtesting frameworks (`backtrader`, `zipline`, `sports-betting`, `Flumine`) were explicitly evaluated and rejected: this codebase's own `validation.py` walk-forward harness already correctly implements the domain-specific point-in-time-correctness contract that retrofitting an external framework would risk fragmenting.

**Core technologies:**
- `sklearn.metrics.brier_score_loss` / `log_loss` / `calibration_curve` (already a dependency) — strictly proper scoring + reliability diagnostics; run both Brier and log loss, not either alone
- Power-method devig (custom, ~10 lines, no dependency) — converts market price to comparable implied probability for CLV/edge computation
- SQLite (existing canonical ledger) + DuckDB (existing feature store) — additive columns and rolling-window views, not a new metrics store
- Hand-rolled fractional Kelly (existing) — never full Kelly; quarter/half-Kelly is the professional norm

### Expected Features

This system already has an unusually mature evaluation/promotion layer for its stage — most "obvious" table-stakes items a generic research pass would suggest (market-relative accuracy gate, CLV tracking, fractional Kelly, correlation-aware exposure caps, drawdown measurement, walk-forward validation, multiple-testing discipline via pre-registered hypotheses, champion/challenger framework, atomic promotion/rollback, cross-market consistency checks, execution-cost-adjusted ROI) are already built. The genuine gaps cluster around turning already-computed evidence into *continuous, alerted, operating* surfaces rather than *point-in-time, pull-based* checks.

**Must have (table stakes, currently gaps):**
- Portfolio-level circuit breaker / kill switch — no `circuit_breaker`/`kill_switch`/`halt_trading` exists anywhere in `src/`; highest-severity gap for a live-money system
- CLV/drift alerting (push, not pull) — `/api/clv`, `/api/capture_health`, `_drift_check` all exist but require a human to query them; no Slack/webhook/email notification on degradation
- Continuous drift monitoring beyond hit-rate — `_drift_check` compares live vs. holdout hit-rate but doesn't track calibration or CLV trend over time, and isn't a scheduled monitor
- Standing per-sport/per-market profitability dashboard — segmentation logic exists in `validation.py`/Gate 4 but is evaluated only at promotion time, not exposed as an always-on panel

**Should have (differentiators, given this system's specific shape):**
- CLV-informed dynamic sizing — shrink Kelly fraction automatically as trailing CLV degrades, before the monthly gate catches it
- Shadow-to-live CLV parity check — verify shadow (paper) CLV matches live (real execution) CLV post-promotion, catching slippage
- Cross-sport drift correlation — simultaneous degradation across unrelated sports signals a systemic pipeline bug, not N independent model failures

**Defer (v2+):**
- Regime-change stress segmentation (needs more historical line-movement depth per sport first)
- Shadow-to-live CLV parity check at scale (only MLB/WNBA moneyline are promoted today — sample too thin to be actionable yet)
- Bankroll-relative unit re-basing — flagged as "needs verification, not confirmed built," not a hard gap; worth a quick manual check before treating as a real item

### Architecture Approach

All three concrete gaps (KBO/NPB postponement settlement, archive-to-contract linkage beyond MLB, and the accuracy/profitability tracking layer) share one governing constraint verified directly in code: this system already has the *shape* of the solution for each, built once (usually for MLB or soccer) and never generalized. The correct architecture is to extend the existing pattern to every sport in place — never a new subsystem, parallel store, or rewrite — and critically, **none of the three fixes touch `production_registry.py`, `model_promotion.py`, `champion_challenger.py`, or `rebuild/`**; all are additive, sport-scoped, and strictly downstream of settlement, so none can destabilize a live model or its promotion evidence.

**Major components:**
1. `cli/settle.py::_settle_international_baseball_pick` — add a postponement branch mirroring soccer's existing `STATUS_POSTPONED`/`ledger.void()` pattern, calling the already-built-but-unused `international_baseball_unplayed_index()`
2. `data_sources/market_archive.py` (new, generalized from `mlb_market_odds.py`) — sport-parameterized canonical-hash + `load_verified_snapshot()`, applied to the existing `PolymarketSnapshotStore` JSONL files all 13 non-MLB sports already write, in place (no migration)
3. `measurement.py` (new) — pure-Python CLV/Brier/log-loss/calibration computation module following the codebase's documented shadow-feature-module pattern (explicit `observed_at_utc` provenance, fail-closed on missing closing quote — never guess a CLV value)
4. Additive columns on `ledger_records` (`clv`, `brier_contribution`, `log_loss_contribution`, `claimed_edge_bucket`) + DuckDB rolling-window views (30/90/365-day) — read-only, downstream-of-settlement evidence for humans, never wired into `model_promotion.py`'s auto-decision path

### Critical Pitfalls

1. **Look-ahead / point-in-time (PIT) leakage** — this project's own documented #1 invariant and single most common source of real bugs (already silently zeroed real picks for months once via a timestamp-ordering bug). Every new sport/feature onboarded this milestone needs its own explicit PIT audit pass before first promotion attempt — it is never "solved once."
2. **Confusing calibration with market-beating edge** — a well-calibrated model can still have zero tradeable edge if the market already prices it in; this repo already learned this concretely with WNBA totals (good-looking Brier, market beat it decisively on real executable lines) and structurally fixed it via Gate 1 (market-relative, not vs. 0.5) and Gate 4 (economic battery as the primary gate). Keep reinforcing this framing as new sports/models are evaluated — a clean standalone calibration tile can still mislead a human reviewer.
3. **Sample-size pitfalls on lower-volume sports** — gates must count distinct contracts, not ledger rows (already learned once concretely: WNBA spread had 51 rows but only 14 distinct contracts). CLV/ROI need ~100-300 bets to be trustworthy; many of this system's sports (esports, KBO/NPB, international soccer) will take a long time to clear that organically — prefer wider CIs and longer windows over lowering the bar.
4. **Model decay / regime change — a genuinely open gap** — no automated, scheduled comparison of a promoted model's live rolling Gate 1/Gate 4 metrics against its promotion-time frozen baseline currently exists. This is the one pitfall PITFALLS.md flags as unmitigated rather than already-addressed, and it directly motivates the CLV/drift-alerting item in FEATURES.md's MVP recommendation.
5. **Market impact / liquidity limits specific to Polymarket** — a captured BBO quote doesn't guarantee it was fillable at size; niche/low-liquidity sports can have fill prices materially worse than the quoted mid. Size real positions against visible top-of-book depth and report quoted-price ROI separately from realized-fill-price ROI, especially before promoting any new market to real execution.

## Implications for Roadmap

Based on combined research, the four research passes independently converge on the same three-gap structure, which suggests a natural phase grouping ordered by risk-reduction-per-effort and dependency (settlement correctness before archive linkage before metrics, since metrics quality depends on settlement/linkage being correct first) with the highest-severity safety item pulled forward.

### Phase 1: Portfolio Circuit Breaker + KBO/NPB Postponement Settlement
**Rationale:** These are the two lowest-complexity, highest-value-per-effort items — the circuit breaker is the single largest live-money safety gap found across all research, and the KBO/NPB fix clears an existing 44-row stuck-open backlog using code that already exists and is already tested. Neither depends on anything else in this list.
**Delivers:** A hard-stop in `auto_executor.py` gated on existing `max_drawdown`; a postponement branch in `_settle_international_baseball_pick` mirroring soccer's existing pattern, wired to `international_baseball_unplayed_index()` and `ledger.void()`.
**Addresses:** FEATURES.md's #1 MVP priority (circuit breaker); clears the settlement gap ARCHITECTURE.md section (a) documents in full.
**Avoids:** No specific PITFALLS.md item directly, but structurally prevents the class of "undetected regime shift + no hard stop" catastrophic-loss scenario Pitfall 7 (model decay) warns compounds silently.

### Phase 2: Archive-to-Contract Linkage Generalization (MLB pattern → all 13 sports)
**Rationale:** CLV/profitability metrics in Phase 3 are only as trustworthy as closing-quote coverage; ARCHITECTURE.md explicitly sequences "extend verified linkage" before "rely on tracking-layer output for governance decisions" for exactly this reason. Rolling out sport-by-sport (starting with whichever markets are closest to a promotion decision) bounds the per-sport team-alias/market-slug mismatch risk this codebase has hit before (soccer's ~45-competition collision history).
**Delivers:** A generalized `market_archive.py` (canonical hash, `snapshot_record_id`, `load_verified_snapshot()`) applied in place to the existing `PolymarketSnapshotStore` JSONL files; `domain.py::PickRequest`'s already-present-but-unpopulated `market_snapshot_*` fields get populated for every sport the same way MLB's already are.
**Uses:** STACK.md's power-method devig (needed once quotes are hash-verified, to convert to comparable implied probability); no new stack element beyond what's already recommended.
**Implements:** ARCHITECTURE.md section (b) in full — explicitly avoids Anti-Pattern 1 (a second closing-quote store) and Anti-Pattern 2 (using `rebuild/shadow_ledger.py`'s isolated stub tables as production linkage).

### Phase 3: Accuracy/Profitability Tracking Layer (CLV, Brier, calibration rollups + alerting)
**Rationale:** Comes last because it depends on Phase 2's closing-quote coverage to be trustworthy outside MLB, and because it's explicitly the read-only, evidence-for-humans layer — it has no dependency the other two phases don't already resolve, and building it before linkage coverage is fixed would produce misleading numbers for 13 of 14 sports.
**Delivers:** `measurement.py` (CLV/Brier/log-loss/calibration computation, fail-closed on missing closing quote); additive `ledger_records` columns; DuckDB rolling-window views (30/90/365-day); a standing profitability dashboard panel (promoted from gate-input to always-on view); push-based alerting on CLV/drift/capture-health degradation layered on top of the already-built `/api/clv`, `/api/capture_health`, `_drift_check`.
**Delivers (secondary):** A scheduled rolling comparison of any promoted model's live Gate 1/Gate 4 metrics against its promotion-time frozen baseline — alert-only, never auto-rollback — directly closing PITFALLS.md's one flagged-open gap (Pitfall 7, model decay).
**Addresses:** FEATURES.md's #2 and #3 MVP priorities (alerting, standing dashboard); STACK.md's CLV pipeline recommendation in full; ARCHITECTURE.md section (c).

### Phase Ordering Rationale

- Dependency-driven: metrics (Phase 3) need trustworthy closing-quote coverage (Phase 2) to be meaningful outside MLB; the circuit breaker and KBO/NPB fix (Phase 1) have no dependency on either and are pulled forward purely on severity/effort ratio.
- Architecture-driven grouping: all three phases are explicitly additive and sport-scoped per ARCHITECTURE.md's core finding — none touches `production_registry.py`/`model_promotion.py`/`champion_challenger.py`, so they can be sequenced by value/risk without any cross-phase coupling to the promotion gate.
- Pitfall avoidance: sequencing linkage before metrics directly avoids reporting CLV/ROI numbers computed against unverified quotes (a version of PITFALLS.md's Pitfall 4 — cost/edge figures that don't specify what price they were computed against should be "provisional, not gate-grade"); keeping all three phases read-only/human-gated avoids PITFALLS.md's and ARCHITECTURE.md's shared warning against wiring any new metric into automatic promotion/demotion (Anti-Pattern 3).

### Research Flags

Needs research during phase planning:
- **Phase 2 (archive-to-contract linkage):** the per-sport team-alias/market-slug matching risk is explicitly called out as non-systemic-but-per-sport (soccer's team-name collision history); each sport's rollout may need a short targeted research/verification pass on its own identifier quirks before wiring `load_verified_snapshot()` in.
- **Phase 3 (tracking layer), alerting sub-item:** no specific notification channel (Slack/webhook/email) is currently wired anywhere in `src/`; needs a brief scoping pass on which channel fits this project's existing operational surface before implementation.

Phases with standard, well-documented patterns (skip research-phase):
- **Phase 1:** both the circuit-breaker logic (standard drawdown-threshold hard-stop, already computed via `economic_gate.py::max_drawdown`) and the postponement-settlement fix (directly copies soccer's existing, live, proven pattern) are well-documented, low-novelty implementations.
- **Phase 3's core metrics (CLV/Brier/log-loss/calibration):** the computation itself is standard scikit-learn + a ~10-line devig function per STACK.md; no new pattern to discover, only wiring.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | scikit-learn recommendations are official-docs-grounded; CLV/devig/Kelly guidance is MEDIUM-confidence practitioner consensus cross-checked across multiple independent sources, but the overall "no new dependency needed" verdict is grounded directly in this repo's own `pyproject.toml`/existing modules (HIGH) |
| Features | HIGH | "What's already built" section is HIGH confidence, read directly from source (`market_eval.py`, `economic_gate.py`, `units.py`, `polymarket_kelly.py`, etc.); gap claims (circuit breaker, alerting) are grounded in targeted repo-wide keyword search — high confidence in the search itself, though absence-of-evidence for an unanticipated naming convention is a small residual risk |
| Architecture | HIGH | Every recommendation is grounded in direct, full-file reads of the actual implicated modules (`settle.py`, `domain.py`, `mlb_market_odds.py`, `polymarket_us.py`, `international_baseball.py`, `ledger.py`, `production_store.py`) — this is the strongest-sourced of the four documents |
| Pitfalls | MEDIUM | Repo-grounded findings (what Phase 23 already mitigates, the WNBA totals/spread lessons) are HIGH; external pitfall framing (CLV-as-leading-indicator thresholds, drift-detection methodology, Kelly drawdown norms) is MEDIUM — practitioner/community consensus across multiple sources, not primary academic/vendor documentation directly fetched |

**Overall confidence:** HIGH — three of four research documents are grounded primarily in direct first-party code reads of this specific repository, not general-domain inference; the MEDIUM-confidence external findings are consistently used only for framing/prioritization (e.g., CLV sample-size rules of thumb), never for a concrete implementation decision that isn't otherwise repo-grounded.

### Gaps to Address

- **Bankroll-relative unit re-basing** (FEATURES.md): flagged as "needs verification, not confirmed built" — `units.py` computes `recommend_units` but no `bankroll` keyword was found in a targeted grep. Resolve with a quick manual read of `units.py` during Phase 3 planning, not a full research pass — likely already handled via a config value the grep pass didn't catch.
- **Quantitative thresholds cited from external sources** (e.g., "+5% CLV is excellent," "300+ bets for CLV significance," "~1/3 of displayed depth" for position sizing): these are industry rules of thumb, MEDIUM confidence, explicitly flagged by PITFALLS.md and STACK.md as not to be hardcoded into gate logic without independent validation against this system's own historical data. Any Phase 3 implementation should treat these as starting defaults, not fixed law, and re-derive/validate them against this system's own ledger once enough tracked history accumulates.
- **CLV rate vs. CLV magnitude** (PITFALLS.md Moderate Pitfall 1): the existing Phase 23 Gate 4 criterion is a pass-*rate* (≥50% of bets beat the close); industry practice more often emphasizes average CLV *magnitude* as more predictive. Worth confirming during Phase 3 whether the economic battery already reports magnitude alongside rate, or whether that's a small additional field to add.
- **Notification channel selection** (FEATURES.md/Phase 3 research flag above): no existing Slack/webhook/email integration was found in `src/`; needs a short scoping decision, not deep research, before the alerting sub-item can be implemented.

## Sources

### Primary (HIGH confidence)
- Direct repo reads: `docs/ROADMAP.md`, `docs/PROJECT_STATUS.md`, `docs/ARCHITECTURE.md`, `docs/AGENTS.md`, `CLAUDE.md`, `docs/INCUMBENT_COMPARISON_2026-09-09.md`, `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/CONCERNS.md`
- Direct source reads: `src/model_prediction/cli/settle.py`, `domain.py`, `ledger.py`, `data_sources/mlb_market_odds.py`, `data_sources/polymarket_us.py`, `international_baseball.py`, `market_eval.py`, `economic_gate.py`, `units.py`, `portfolio/polymarket_kelly.py`, `rebuild/correlation.py`, `rebuild/shadow_ledger.py`, `cli/commands.py`, `features/hypothesis_ledger.py`, `production_registry.py`, `model_promotion.py`, `production_store.py`
- [scikit-learn: brier_score_loss / calibration_curve / CalibratedClassifierCV official docs](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html)

### Secondary (MEDIUM confidence)
- CLV methodology and sample-size benchmarks: Pikkit, AgentBets.ai, Sharp Football Analysis, VSiN
- Devig method comparison: Bet Hero, Outlier
- Fractional Kelly rationale: Matthew Downey (technical blog with simulation methodology), SportsTrade
- Backtesting/overfitting: David H. Bailey (overfit-tools paper), Portfolio Optimization Book, arXiv 2107.08827, arXiv 2410.21484
- Drift detection: Statsig, Evidently AI, Aerospike, Towards Data Science
- Polymarket liquidity/microstructure: arXiv 2604.24366, ats.io institutional liquidity benchmarks
- Small-sample estimation (Firth's correction, rare-event validation): NCBI/PMC (PMC9188212, PMC9890785)

### Tertiary (LOW-MEDIUM confidence)
- Flumine Kalshi/Polymarket support: single secondary source (SportsBetEdge open-source roundup), not independently verified against Flumine's own docs — noted as domain-relevant but not load-bearing for any recommendation

---
*Research completed: 2026-09-22*
*Ready for roadmap: yes*
