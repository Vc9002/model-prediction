# Feature Landscape

**Domain:** Professional multi-sport prediction / trading system (live production, Polymarket execution)
**Researched:** 2026-09-22
**Scope:** What mature, professional sports-prediction/betting systems have that this system may be missing, in service of "accurate and profitable" — cross-referenced against the already-built Phase 23 market-relative promotion gate and surrounding infrastructure.

## What's Already Built (do not re-recommend)

Direct codebase inspection (`docs/ROADMAP.md`, `docs/PROJECT_STATUS.md`, `src/model_prediction/*`) confirms this system already has, at HIGH confidence (read directly from source):

| Capability | Where | Notes |
|---|---|---|
| Market-relative accuracy gate | `docs/ROADMAP.md` Phase 23, `market_eval.py::market_relative_report` | Paired ΔLogLoss/ΔBrier vs no-vig market price (not vs 0.5), date-clustered bootstrap P(better) ≥ 80% |
| CLV tracking | `dashboard/status.py`, `/api/clv` | Rolling 30-day CLV and closing-beat rate; Phase 23 gate 4 requires CLV rate ≥ 50%, realized CLV ≥ 0 |
| Capture/data-freshness health | `/api/capture_health` | 7-day BBO (best bid/offer) snapshot freshness |
| Fractional Kelly staking | `portfolio/polymarket_kelly.py`, `units.py` | "Real-Edge Quarter-Kelly" (adjusted_prob − market_prob, not raw edge) — already the professionally-recommended fraction (see External Findings below) |
| Correlation-aware exposure caps | `rebuild/correlation.py`, `polymarket_kelly.py::apply_correlation_exposure_caps` | Same-event and same-team ML+spread groups de-duplicated before exposure is summed |
| Drawdown measurement | `economic_gate.py::max_drawdown` | Peak-to-trough on chronologically ordered P&L; feeds Phase 23 gate 4 |
| Basic drift check | `cli/commands.py::_drift_check` | Live settled hit-rate vs. holdout hit-rate, per sport, gated on n≥10 |
| Multiple-testing discipline | `features/hypothesis_ledger.py`, `PreRegisteredExperiment`, SPRT (`rebuild/sprt.py`) | Immutable pre-registration of hypothesis + metric + thresholds before evaluation; exactly the "haircut for number of trials" academic literature calls for |
| Walk-forward / no-peek validation | `docs/ARCHITECTURE.md`, `validation.py` | 60/20/20 chronological split, calibrator selected out-of-fold only, locked holdout never touched pre-decision |
| Profitability breakdown (at promotion time) | Phase 23 gate 4 / `validation.py` | Month-to-month, favorite/underdog, home/away, high/low-hit-rate segmentation — but see Gap 3 below on whether this is a standing view or a one-off check |
| Champion/challenger paired framework | `champion_challenger.py`, Phase 22 | Freeze-production + paired comparison harness, reused across sports |
| Atomic promotion/rollback | Phase 24, `model_promotion.py` | Explicit versioned promotion, rollback target preserved, never silent overwrite |
| Cross-market consistency | `cross_market_consistency.py` | Monotonicity (P(cover) ≤ P(ML win)) and complementarity (P(over)+P(under)=1) checks |
| Execution cost modeling | `market_eval.py::expected_roi_after_costs`, `execution_rehearsal.py` | ROI computed after realistic execution costs, not gross edge |

This is an unusually mature promotion/evaluation layer for a project at this stage — most of the "obvious" table-stakes items a generic research pass would recommend are already present. The gaps below are the genuinely missing or partially-built pieces.

## Table Stakes

Features professional systems are expected to have. Missing/partial here is a real operational risk, not a nice-to-have.

| Feature | Why Expected | Complexity | Status Here |
|---|---|---|---|
| CLV alerting (not just reporting) | CLV is the leading indicator (external research, MEDIUM confidence — see below); a degrading trend should page someone before a full monthly gate cycle confirms it in P&L | Low | **Gap.** `/api/clv` and `_drift_check` are pull-based (dashboard/CLI query). No push notification (Slack/webhook/email) found anywhere in `src/` on CLV degradation, capture-health failure, or drift-check breach. `run_supervisor.py`/`polymarket_ws.py` reference "notify" only for internal state, not external alerting. |
| Portfolio-level circuit breaker / kill switch | Protects against catastrophic loss during an undetected regime shift or a bug that survives review — the single highest-severity gap for a live-money system | Low–Medium | **Gap.** No `circuit_breaker`/`kill_switch`/`halt_trading` found anywhere in `src/`. Per-bet Kelly caps and correlation-aware exposure caps exist, but nothing stops new orders when realized portfolio drawdown crosses a hard threshold (e.g., "halt all execution if trailing-30-day drawdown > X units until human review"). |
| Continuous drift monitoring beyond hit-rate | External research (MEDIUM confidence): performance-metric drift (not just win-rate) — calibration drift, CLV trend, log-loss trend — catches degradation win-rate alone hides for months due to variance | Medium | **Partial.** `_drift_check` compares live hit-rate to holdout hit-rate per sport (n≥10 gate) — good first pass, but doesn't track calibration drift or CLV trend over time, and it's a CLI/dashboard query, not a scheduled monitor with alerting. The esports inactivity-decay fix (F-63) shows the team *can* diagnose decay well when it looks — this should be systematized as a recurring check across all promoted models, not a one-off per-sport fix. |
| Standing per-sport/per-market profitability dashboard | Aggregate P&L hides which sport/market is actually carrying (or dragging) results — needed to decide where to add research effort or pull a model | Low (mostly wiring existing pieces) | **Partial.** The segmentation logic (month-to-month, favorite/underdog, home/away, hit-rate buckets) exists in `validation.py` and is used inside the Phase 23 gate-4 economic battery, but that's evaluated at promotion-decision time, not exposed as an always-on dashboard panel a human can check daily. Worth promoting from "gate input" to "operating view." |
| Bankroll-relative unit re-basing | Kelly sizing should scale with the *current* bankroll, not a static base, or units silently over/under-risk as bankroll compounds | Low–Medium | **Needs verification, not confirmed built.** `units.py` computes `edge_scaled_units`/`recommend_units` but no `bankroll` or dynamic re-basing keyword was found in that file — worth confirming units are re-derived from live bankroll rather than a fixed constant. Flag for phase-specific research, not a hard gap (may already be handled via a config value not caught by this grep pass). |

## Differentiators

Not universally expected, but genuinely valuable given this system's specific shape (multi-sport, dual-track challenger/incumbent, shadow-first).

| Feature | Value Proposition | Complexity | Notes |
|---|---|---|---|
| CLV-informed dynamic sizing | Beyond binary CLV reporting: use *realized* CLV trend per model/sport as an input to the sizing fraction itself (shrink Kelly fraction automatically when trailing CLV degrades, before the monthly gate would catch it) | Medium | Natural extension of already-built CLV tracking + Kelly engine; ties two existing systems together instead of adding new infrastructure |
| Shadow-to-live CLV parity check | Verify that a model's *shadow* CLV (paper) matches its *live* CLV (real execution) post-promotion — catches execution slippage or a promotion that looked good only because shadow fills used stale/ideal prices | Medium | This project's shadow-first architecture (most sports shadow, few promoted) makes this cheap to add and directly answers "does the backtest edge survive contact with real fills" |
| Regime-change stress segmentation | Beyond favorite/underdog/home/away buckets: explicitly test performance across market-volatility or line-movement regimes (e.g., high closing-line movement vs. stable lines) — catches models that only work when markets are calm | Medium–High | Differentiator because most retail/prosumer systems stop at the demographic-style buckets already built here; this goes one level deeper |
| Cross-sport drift correlation | If drift/CLV degradation appears simultaneously across multiple unrelated sports, that's a strong signal of a systemic pipeline or data-source bug rather than N independent model failures — worth a specific check given this system already tracks per-sport drift | Low–Medium | Builds directly on the existing `_drift_check` infra; mostly an aggregation/alerting layer, not new modeling |

## Anti-Features

Practices to explicitly avoid — several are things this project already guards against; listed here so the roadmap doesn't accidentally regress them.

| Anti-Feature | Why Avoid | What to Do Instead |
|---|---|---|
| Retuning promotion thresholds after seeing results | Classic look-ahead/data-snooping — external research (MEDIUM confidence) flags this as the single most common source of inflated backtest confidence in quant betting | Keep thresholds in `PreRegisteredExperiment`/hypothesis ledger locked before evaluation, as already done — don't loosen a gate because a promising candidate narrowly misses it |
| Reporting win rate as the headline success metric | Win rate is dominated by variance over the sample sizes this system sees (single-sport, single-month); professional consensus (MEDIUM confidence) is CLV/market-relative edge is the real signal | Already the direction of Phase 23 — keep CLV and ΔLogLoss/ΔBrier vs. market as the primary reported numbers; per user's own standing preference, discuss wiring/features not hit rates in review conversations |
| Auto-retraining/auto-promotion on any performance dip or improvement | External research (MEDIUM confidence): treat drift alerts as investigation triggers, not auto-retrain commands; auto-deploying on an improved test score without checking data/cohort/robustness evidence causes silent regressions | Keep the existing human-gated, atomic promotion flow (Phase 24) — do not wire drift alerts (once built) directly to automatic model swaps |
| Testing a strategy across many leagues/markets and reporting only the best one | External research (MEDIUM confidence): classic selection/look-forward/survivorship bias specific to multi-sport systems — directly relevant here given this is a multi-sport project | Report per-sport/per-market results for everything tested, including the sports/models that didn't qualify (the existing Main-ledger "no gates, show everything" philosophy already does this — keep it) |
| Full Kelly (or unadjusted edge-proportional) sizing | External research (MEDIUM confidence): full Kelly assumes the probability estimate is exact; real edge estimates have error, and full Kelly amplifies that error into steep drawdowns | Already avoided — Real-Edge Quarter-Kelly is in the professionally-recommended 25–50%-of-full range |
| Treating a single monthly gate as the only drift signal | A 10-pick, single-month gate is a reasonable *minimum* bar but is a lagging indicator; waiting for it to fail before reacting means real money keeps flowing on a degraded model for up to a month | Add a continuous/leading monitor (CLV trend, calibration trend) that can flag concern before the monthly gate formally fails — see Table Stakes gap above |

## Feature Dependencies

```
CLV alerting → requires existing /api/clv + capture_health (built) → add threshold+notification layer
Portfolio circuit breaker → requires existing drawdown computation (built, economic_gate.py) → add a hard-stop check in auto_executor.py before order placement
Continuous drift monitoring → requires existing _drift_check + market_eval CLV (built) → schedule it, track trend not just point-in-time, wire to alerting
Standing profitability dashboard → requires existing validation.py segmentation logic (built) → expose as dashboard panel independent of promotion-gate runs
CLV-informed dynamic sizing → requires CLV alerting (leading indicator) + Kelly engine (both exist/planned) → feed CLV trend into recommend_units
```

## MVP Recommendation

Prioritize (highest safety/value per unit effort, since this is a live-money system):

1. **Portfolio-level circuit breaker** — the single largest safety gap found; low complexity given `max_drawdown` and the auto-executor already exist, just need a hard-stop wired in before order placement.
2. **CLV/drift alerting (push, not pull)** — turns already-built `/api/clv`, `/api/capture_health`, and `_drift_check` from dashboards-you-have-to-check into monitors that reach the operator.
3. **Promote profitability segmentation from gate-input to standing dashboard panel** — cheapest item on this list; the logic already exists in `validation.py`, this is exposure/wiring only.

Defer:
- **Regime-change stress segmentation**: valuable but needs enough historical line-movement data per sport to be meaningful; revisit once more sports have multi-season market snapshot history (the ROADMAP already flags 6-week snapshot depth as a binding constraint for MLB v9 economic evidence).
- **Shadow-to-live CLV parity check**: valuable differentiator but only actionable once more models are past the shadow→promoted transition; currently only MLB moneyline and WNBA moneyline are promoted, so the sample would be thin.

## Confidence Notes

- Everything under "What's Already Built" is HIGH confidence — read directly from this repo's own source and docs (`docs/ROADMAP.md`, `docs/PROJECT_STATUS.md`, `src/model_prediction/*`), not inferred.
- Gap claims (e.g., "no circuit breaker found") are grounded in targeted `grep`/search passes across `src/`, `scripts/`, `ops/` for the relevant keywords (circuit_breaker, kill_switch, halt_trading, slack/webhook/notify, bankroll rebasing) — absence-of-evidence, not a guarantee nothing exists under an unanticipated name. Flagged as "Gap" (high confidence in the search) vs. "Needs verification" (lower confidence, worth a quick manual check) accordingly.
- External web-research findings (CLV as leading indicator, fractional Kelly norms, drift-alerting workflow, multiple-testing pitfalls) are MEDIUM confidence — practitioner/community consensus across multiple independent search results and one academic-adjacent source (Harvey & Liu multiple-hypothesis-testing framework, `davidhbailey.com` overfitting paper), not a single authoritative primary source. Treat quantitative benchmarks (e.g., "+5% CLV is excellent," "200-300 bets for significance") as industry rules of thumb, not proven thresholds — do not hardcode them into gate logic without independent validation against this system's own data.

## Sources

- Direct codebase inspection: `/Users/vincentc9002/model-prediction/docs/ROADMAP.md`, `docs/PROJECT_STATUS.md`, `docs/AGENTS.md`, `CLAUDE.md`, `src/model_prediction/market_eval.py`, `economic_gate.py`, `units.py`, `portfolio/polymarket_kelly.py`, `rebuild/correlation.py`, `cli/commands.py`, `features/hypothesis_ledger.py` (HIGH confidence, primary source)
- [What is CLV Betting? — Sharp Football Analysis](https://www.sharpfootballanalysis.com/sportsbook/clv-betting/) (MEDIUM)
- [Closing Line Value — VSiN](https://vsin.com/how-to-bet/the-importance-of-closing-line-value/) (MEDIUM)
- [How to Track CLV — Pikkit](https://pikkit.com/blog/how-to-track-closing-line-value-clv-in-sports-betting) (MEDIUM)
- [Model drift detection — Statsig](https://www.statsig.com/perspectives/model-drift-detection) (MEDIUM)
- [What is data drift in ML — Evidently AI](https://www.evidentlyai.com/ml-in-production/data-drift) (MEDIUM)
- [Model Drift in Machine Learning — Aerospike](https://aerospike.com/blog/model-drift-machine-learning/) (MEDIUM)
- [Optimal sports betting strategies in practice: an experimental review — arXiv 2107.08827](https://arxiv.org/pdf/2107.08827) (MEDIUM, academic-adjacent)
- [The Fractional Kelly Bankroll Management System — SportsTrade](https://www.sportstrade.io/blog-detail/141/the-fractional-kelly-bankroll-management-system.html) (MEDIUM)
- [Backtest overfitting in financial markets — David H. Bailey](https://www.davidhbailey.com/dhbpapers/overfit-tools-at.pdf) (MEDIUM, academic-adjacent)
- [8.3 The Dangers of Backtesting — Portfolio Optimization Book](https://portfoliooptimizationbook.com/book/8.3-dangers-backtesting.html) (MEDIUM)
- [A Systematic Review of Machine Learning in Sports Betting — arXiv 2410.21484](https://arxiv.org/pdf/2410.21484) (MEDIUM, academic-adjacent)
