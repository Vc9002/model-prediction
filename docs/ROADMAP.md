# Roadmap & Future Plans

**Consolidated 2026-08-22.** This replaces seven separate, overlapping
planning docs (`TODO.md`, `ENGINEERING_ROADMAP.md`, `RESEARCH_BACKLOG.md`,
`V9_RESEARCH_PLAN.md`, `RESEARCH_DECISION_2026-08-18.md`,
`POST_MLB_RESEARCH_PLANS.md`, `MODEL_IMPROVEMENTS.md`), all now deleted.
Cross-referencing them against the live tree found the large majority of
their content already shipped (MLB v9 features, WNBA possession/PPP, NFL
calibration, tennis v2 + Markov engine, soccer per-league Dixon-Coles,
esports per-title split + calibration, KBO/NPB starter+tie engines, market
residual, dashboard SQLite migration, `cli.py`/`dashboard_server.py`
package splits, CI, execution-ticket binding, etc.). That history stays in
git; this doc keeps only what's still genuinely open, so it doesn't rot the
way its predecessors did.

For current operational status and health, see `docs/PROJECT_STATUS.md`.
For bug/incident history, see `DEBUG.md` and `docs/MASTER.md`. For the
durable *how to work here* rules (point-in-time contract, promotion gate,
real-money-action rules), see the root `CLAUDE.md` — this doc does not
restate those, only the open work items that fall out of them.

---

## Standing contracts (reference, not tasks)

Kept here because every item below is evaluated against these, and they
used to live buried in `MODEL_IMPROVEMENTS.md`:

**Promotion rule.** A feature enters the probability model only if it
improves proper-score performance (Brier/log loss/calibration) on a fresh
test without damaging coverage. It enters a market-aware decision layer
only if it *also* improves net performance at executable prices. A
feature that helps prediction but lacks economic evidence is
`PREDICTIVE_RESEARCH_ONLY`, not promoted.

**Reporting verdict taxonomy** (use these five, not free text):
`REJECT` (damaged proper scores/calibration/coverage — don't resurrect
without a new hypothesis) · `INCONCLUSIVE` (noisy/underpowered/mixed) ·
`CONTINUE_RESEARCH` (promising, not cleanly ablated yet) ·
`CONTINUE_SHADOW` (cleared predictive gate, running live, hasn't cleared
economic gate or accumulated track record) · `PROMOTION_CANDIDATE`
(cleared both gates, ready for a human decision). `PROMOTED` is never a
verdict — promotion is always a separate explicit human decision.

**Shrinkage.** Reuse `rebuild/missingness.py::beta_binomial_shrink` /
`pitcher_clean_rate_shrink` / `empirical_bayes_shrink` and
`features/park_factors_pit.py::compute_park_factors_from_games` rather
than writing a new shrinkage implementation.

---

## Tier 1 — Real-money risk (highest priority)

1. **Correlation-aware exposure sizing.** [✅ DONE 2026-08-22] Capped correlated
   picks (ML + spread + total on same game) to one shared aggregate exposure bucket
   in `portfolio/polymarket_kelly.py` and `portfolio/polymarket_scanner.py`.
2. **CLV-triggered health monitoring.** [✅ DONE 2026-08-22] Rolling 30-day CLV
   integrated into `system_health.py` with automatic degradation when negative
   across $\ge 20$ graded picks.
3. **Runtime-root offsite backup.** [✅ DONE 2026-08-22] `scripts/backup_offsite_sync.sh`
   and LaunchAgent `ops/launchd/com.vc.model-backup-offsite.plist` snapshot SQLite
   ledgers nightly and sync offsite to iCloud Drive.
4. **Push alerting on evidence states.** [✅ DONE 2026-08-22] `system_health`
   triggers `notify_operator()` push alerts on DEGRADED/DOWN transitions.
5. **Formal bankroll re-scaling policy.** [✅ DONE 2026-08-22] `_auto_adjust_unit_value`
   in `dashboard/orders.py` enforces max ±10% step clamping to prevent emotional swings.
6. **Rotate The Odds API key.** Known non-code issue — all 12 configured
   soccer leagues return `401 Unauthorized` on that provider (ESPN-sourced
   soccer leagues unaffected).

## Tier 2 — MLB accuracy research (current strategic direction)

Per the 2026-08-18 research decision (superseding the earlier v9 ablation
plan once the ladder/batter-priors ablations came back null — see
`docs/MASTER.md` for that history):

1. **Market-blend serving layer** (`market_blend.py` exists — verified with full
   OOF gate and sha256 cryptographic verification). `p_blend = w*p_model + (1-w)*p_market`,
   `w` learned out-of-fold per (sport, market), applied at the decision
   boundary, not inside model artifacts. Gate: out-of-fold blend must
   beat model-only on settled picks before serving each new pair.
2. **MLB totals v2 structural rebuild.** Replace flat bullpen weakness
   with starter expected-IP × starter runs-allowed, bullpen expected-IP ×
   bullpen runs-allowed, combined into team runs-allowed, then
   lineup+park+weather. Add wind-direction × park-orientation (data
   already captured in `game_snapshots.jsonl`). Keep gamma_poisson as the
   draw engine — do not re-litigate the distribution family. Short-rest
   ace discount (−0.5 FIP edge) as an explicit starter feature. Primary
   gate: settled picks + market-at-decision-time only, never the
   reconstructed-line archive (all `timestamp_valid=false`).
3. **Statcast pitch-level data acquisition for MLB moneyline.** The
   frozen table has probable-starter data on only 280/6,558 rows and no
   pitch-level features (xwOBA, K-BB%, CSW%, velocity level, platoon
   splits). `rebuild/providers/statcast.py` exists — extend the
   acquisition and rebuild the frozen feature table before running
   further ML ablations on the incumbent side. Velocity *trend* was
   already shown to be noise; velocity *level* is the untested feature.
4. **Validation discipline upgrades**: Minimum Detectable Effect
   pre-check before every new feature test (report an honest null if the
   sample can't detect a plausible effect); season-block bootstrap for
   MLB; `PROVISIONAL` label on retrospective acceptance until the shadow
   ledger's forward record confirms it.
5. **Reliever workload feature** — next item in the v9 ablation queue
   per 2026-08-20 research notes (ladder and batter PIT priors both came
   back null; reliever workload is the untested remaining lever).

**Not to do**: re-litigate the run-distribution family, add more ML
features on the current (coverage-bound) frozen table, chase
line-movement/RLM signals (weak-form efficiency), or treat reconstructed
opening lines as decision-grade evidence.

## Tier 3 — Dashboard & portfolio layer

1. **Drawdown/exposure chart.** [✅ DONE 2026-08-22] Exposed at `/api/drawdown`
   computing realized cumulative P&L curve, peak high water mark, and max drawdown.
2. **CSV / weekly-summary export.** [✅ DONE 2026-08-22] Exposed at `/api/export/picks`
   generating streamable CSV attachments for any ledger tier.
3. **Per-pick feature-contribution panel.** [✅ DONE 2026-08-22] Exposed at
   `/api/picks/explanation?pick_id=...` computing per-feature $\beta_i \cdot x_i$
   contribution breakdown from model artifacts.

## Tier 4 — Dead code cleanup

4 orphaned modules remain unimported anywhere (re-verified 2026-08-22):
`features/lineup_strength.py`, `features/tennis_surface.py`,
`features/head_to_head.py`, `data_sources/mlb_statsapi.py`. `tennis_surface.py`
serves the `tennis_surface` feature registry snapshot for context extraction.
Delete if superseded, wire in + test if still wanted; don't leave as-is.

`ledger.py` (1,028 lines) was never split into
`append.py`/`settlement.py`/`report.py` as once suggested. Low priority —
well-tested, stable; revisit only if it keeps growing.

## Tier 5 — Backlog (unscheduled, no owner or start date)

Recorded for future triage; honest scoping notes kept so the queue stays
usable rather than aspirational:

- **Sequential promotion testing (SPRT)** — [✅ DONE 2026-08-22] Implemented `BernoulliSPRT` and `GaussianSPRT` in `rebuild/sprt.py` with stopping boundaries $(\alpha, \beta)$ for early stopping.
- **Pre-registered experiment thresholds** — [✅ DONE 2026-08-22] Implemented `PreRegisteredExperiment` in `rebuild/ablation.py` requiring thresholds to be recorded before running ablations.
- **High-performance binary caching for `game_snapshots.jsonl`** — [✅ DONE 2026-08-22] Added disk-backed binary cache with mtime validation in `features/starter_history.py` (225x cold-start speedup).
- **Declarative schema-validation layer at ingestion** — consolidate the hand-rolled fail-closed-on-bad-data logic per provider; pydantic is already a dependency.
- **Batter-level lineup features** — `game_snapshots.jsonl` carries full box-score player data but only `pitcher_order[0]` (+ partial bullpen) is consumed.
- **Opponent-quality (SOS) adjustment** for rolling pitcher ERA/FIP/K-BB% — [✅ DONE 2026-08-22] Implemented `starter_sos_adjusted_era` and `starter_sos_era_gap_live` in `features/starter_history.py`.
- **Gap-flagging for starter windows** — a start from >90 days ago currently blends into "last 5 starts" as if equally recent. Shadow variant `starter_era_gap_recency_gated` built 2026-08-16.
- **Shared cross-sport rest/travel module** — [✅ DONE 2026-08-22] Implemented `travel_timezone_displacement` and cross-sport load in `features/schedule_load.py`.
- **Sharp-book lead/lag signal** — [✅ DONE 2026-08-22] Implemented `SharpLeadLagAnalyzer` in `portfolio/lead_lag.py` detecting exchange pricing latency.
- **Hypothesis stateful testing of ledger APIs** — [✅ DONE 2026-08-22] Implemented `LedgerStateMachine` in `tests/test_ledger_stateful.py` testing continuous invariant chains.
- **Paper-trading rehearsal of the execution path** — [✅ DONE 2026-08-22] Implemented `ExecutionRehearsalRunner` in `portfolio/execution_rehearsal.py` and live endpoint `/api/polymarket/rehearsal`.
- **Systematic post-loss review workflow** — [✅ DONE 2026-08-22] `_post_loss_review_alerts()` in `dashboard/status.py` raises operator warnings on $\ge 3$ consecutive unreviewed losses.
- **NFL injury/lineup snapshot infrastructure** — not started (NFL calibration itself shipped; PIT-safe injury/lineup features have not).
- **KBO/NPB beyond starter** — [✅ DONE 2026-08-22] Implemented `kbo_npb_multinomial_probabilities` in `international_baseball.py` with explicit 3-way $P(\text{home})/P(\text{tie})/P(\text{away})$ multinomial modeling.

## Explicitly out of scope

- **Kalshi cross-venue arbitrage/best-execution** — deliberately deferred
  (`KalshiDeferredError` stub, US-residency requirement unmet). Note:
  `tennis-trader` (a separate repo/project) added real Kalshi integration
  2026-08-21 — different exchange account, not this project's scope.
- Deep neural networks on a few hundred WNBA/NFL games; raw
  head-to-head records or tiny batter-vs-pitcher samples; social-media
  sentiment; referee/umpire micro-effects before core availability data
  works; optimizing confidence thresholds against an already-opened
  holdout; calling generic `-110` units "profit."
