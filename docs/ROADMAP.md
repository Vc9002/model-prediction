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

1. **Market-blend serving layer.** [✅ DONE 2026-08-22] Implemented in
   `market_blend.py` and `market_blend_stage1.py` with exact-byte SHA-256
   experiment specs, chronological out-of-fold gate, and `ServingBlendPolicy`.
2. **MLB totals v2 structural rebuild.** [✅ DONE 2026-08-22] Innings-weighted
   pitching expected runs allowed (`mlb_pitching_runs_allowed`), short-rest
   fatigue penalty, stadium wind-orientation vector multiplier
   (`stadium_wind_orientation_multiplier`), and composite game projections
   (`mlb_totals_v2_projected_runs`) implemented in `total_score.py`.
3. **Statcast pitch-level data acquisition for MLB moneyline.** [✅ DONE 2026-08-22]
   `StatcastProvider.aggregate_pitcher_metrics` implemented in
   `rebuild/providers/statcast.py` computing pitch-level fastball velocity
   levels, CSW% (called strikes + whiffs), K-BB%, and xwOBA allowed.
4. **Validation discipline upgrades.** [✅ DONE 2026-08-22] Minimum Detectable Effect
   pre-check before every new feature test (`minimum_detectable_effect` in
   `rebuild/validation.py`) to prevent underpowered tests (<2% delta), and
   hierarchical season-block bootstrap (`season_block_bootstrap` in
   `rebuild/validation.py`) preserving within-season temporal autocorrelation
   and year-over-year structural shifts.
5. **Soccer draw calibration & Double Chance pricing.** [✅ DONE 2026-08-22]
   `prob_double_chance`, `soccer_double_chance_probabilities`, and
   `draw_calibrated_probabilities` implemented in `models/soccer_dixon_coles.py`.
6. **Reliever workload feature.** [✅ DONE 2026-08-23] Dynamic bullpen state,
   reliever availability decay functions ($P(\text{avail} \mid \text{pitches}_{1d, 2d, 3d}, \text{consec})$),
   leverage weighting, and Empirical Bayes talent shrinkage implemented in
   `features/bullpen_state.py`.

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

## Tier 4 — Dead code cleanup & feature registry hygiene

[✅ DONE 2026-08-22] Verified, wired, and unit-tested all feature modules
(`features/tennis_surface.py`, `features/head_to_head.py`,
`features/lineup_strength.py`, and `data_sources/mlb_statsapi.py`) with 100%
passing test coverage in `tests/test_features_tier4_modules.py`.

## Strategic Implementation Specification (Next Major Project Cycle)

The operating principle:
> **Freeze the experiment → add genuinely new information → test one logical family at a time → only then increase model complexity → only then promote.**

### 1. Rebuild the MLB research branch on current `main`
- The existing `research/mlb-v8-reproduction` branch is too divergent from `main`. Do **not** merge it wholesale.
- Create a new research branch from current production: `git checkout -b research/mlb-v9`.
- Preserve old tip: `git tag archive/mlb-v8-reproduction-20260823 origin/research/mlb-v8-reproduction`.
- The new branch inherits `main`'s: lineup collector, wake-planner fixes, CLI package split, settlement fixes, `provider_capture.py`, BALLDONTLIE client, `uv.lock`, current tests, and production behavior.
- Selectively bring over research assets: `scripts/mlb_evaluator.py`, `scripts/mlb_research_common.py`, `scripts/mlb_v9_feature_table.py`, `scripts/mlb_v9_ablation_matrix.py`, `scripts/mlb_v9_calibration_xgb.py`, relevant v8 parity scripts, `outputs/research/mlb_v8_parity/*`, `outputs/research/mlb_v9_feature_table/*`.
- Do **not** bring over: production config/serving changes, market-blend experiments, old `cli.py` modifications, uncommitted market blend work, or anything touching order execution.

### 2. Repair the MLB v9 experiment harness
- Evaluator must operate from an **immutable matrix**, not runtime reconstruction.
- Versioned dataset layout:
  ```text
  outputs/research/mlb_v9/
      tables/mlb_v9_feature_table_v1.parquet
      manifests/mlb_v9_feature_table_v1.json
      cohorts/train_event_ids_v1.json, validation_event_ids_v1.json, research_test_event_ids_v1.json
  ```
- Required table identity columns: `event_id`, `game_start_utc`, `decision_time_utc`, `date_et`, `home_team_id`, `away_team_id`, `home_score`, `away_score`, `home_win`, `split`, followed by feature values.
- Availability must be explicit (`starter_available`, `bullpen_available`, `weather_available`, `park_available`), never relying on `feature == 0`.
- Hard-pin manifest hashes: `dataset_sha256`, `schema_sha256`, `train_event_ids_sha256`, `validation_event_ids_sha256`, `research_test_event_ids_sha256`. Evaluator aborts with `ABORT_DATASET_CONTRACT_MISMATCH` if hashes do not match.

### 3. Separate v8 reproduction from v9 research
- **v8 reproduction**: historical unscaled features, historical sklearn fitting behavior, historical static park factor.
- **v9 research**: clean pipeline with `StandardScaler` (fit **only on training**) $\to$ `LogisticRegression(max_iter=5000, solver="lbfgs")`.
- Distinct entry points: `v8_reproduction_fit()` vs `v9_research_fit()`.

### 4. Correct the K-BB experiment
- Rename old concept explicitly: `starter_kbb_per_ip_legacy` and mark verdict as `starter_kbb_legacy_per_ip = VOID_INVALID_FEATURE_DEFINITION`.
- New starter representation dataclass: `StarterGameLine(game_start_utc, innings, batters_faced, earned_runs, strikeouts, walks, home_runs, hit_by_pitch)`. Source `batters_faced` ($BF$) directly when available, never approximating from IP.
- Rate definitions: $\text{k\_pct} = K / BF$, $\text{bb\_pct} = BB / BF$, $\text{k\_minus\_bb\_pct} = (K - BB) / BF$ (HBP is not BB%).
- Test three variants: A (incumbent starter ERA), B (true starter K-BB%), C (starter K% + starter BB%).

### 5. Make missingness a first-class feature
- Every feature family tracks value, availability, and sample strength (e.g. `starter_k_pct_gap`, `starter_k_pct_available`, `starter_k_pct_home_bf`, `starter_k_pct_away_bf`).
- Imputation fit only from training: training median / neutral prior + missing indicator.

### 6. Replace fake coverage metrics with distinct dimensions
- Report: event coverage, feature coverage, fully observed coverage, imputation rate, production-equivalent coverage.
- Report per family (starter, batter, bullpen, lineup, weather) and score proper scores separately for all rows vs fully observed rows vs fallback/imputed rows.

### 7. Redefine the holdout policy & prospective evidence
- Treat old v8 holdout as v9 research/model-selection cohort.
- Final v9 evidence must be prospective: freeze model, features, hyperparameters, and calibration, then accumulate future games writing predictions before first pitch with hashes and availability flags.

### 8. Build `projected_offense_pit` (Empirical Bayes Batter Talent)
- Module: `src/model_prediction/research/mlb_v9/batter_priors.py` and `projected_offense.py`.
- Batter state: PIT estimates for K%, BB%, HR/PA, ISO, wOBA, xwOBA, barrel%, hard-hit%, exit velocity.
- Shrinkage: Binomial rates use $\hat{p} = \frac{\tau p_{\text{prior}} + \text{successes}}{\tau + \text{opportunities}}$, continuous metrics use $\hat{x} = \frac{\tau x_{\text{prior}} + PA \cdot x_{\text{observed}}}{\tau + PA}$. Tune $\tau$ via chronological training/validation only.

### 9. Historical projected lineup without target-game leakage
- Do not use target-game boxscore batting order.
- Estimate $P_i(\text{start at }T)$ from pregame signals (started previous game, starts last 7/14 team games, PA last 7/14, active roster/injury status) and compute expected batter weight $P(\text{start}_i) \times \text{expected PA share}$.
- Output compact family: `projected_offense_quality_gap`, `projected_offense_kbb_gap`, `projected_offense_power_gap`, `projected_offense_sample_strength`.

### 10. Pre-registered batter ablation ladder
- Run: v8 control $\to$ +projected offense quality $\to$ +projected K/BB $\to$ +projected power $\to$ +all three.
- Report $\Delta\text{LogLoss}$, $\Delta\text{Brier}$, ECE, calibration slope, AUC, bootstrap CI, $P(\text{challenger better})$, coverage, monthly performance. Retention criterion is proper score, not ROI.

### 11. Real starter-state representation vector
- Module: `src/model_prediction/research/mlb_v9/starter_state.py`.
- Features: K%, BB%, K-BB%, xwOBA allowed, FIP, velocity, CSW%, first-pitch-strike%, expected innings/start, recent form, handedness.
- Derive talent vs recent residual: $\text{RecentResidual} = \text{RecentK\%} - \text{ShrunkLongTermK\%}$.

### 12. Expected starter depth
- Model expected starter innings ($\text{IP}/\text{start}$, recent pitch count, season workload, rest days, recent starts) outputting `home_expected_starter_ip`, `away_expected_starter_ip`, `starter_depth_gap`.

### 13. Platoon matchup layer
- Match starter handedness $\times$ opposing projected hitters: `projected_lineup_woba_vs_L/R`, `projected_lineup_k_pct_vs_L/R`.

### 14. Bullpen as Quality $\times$ Availability
- Modules: `reliever_state.py` and `bullpen_state.py`.
- Workload tracking: pitches $1d, 2d, 3d$, appearances $2d/3d$, consecutive days, rest days.
- Availability probability: $P(\text{reliever appears today} \mid \text{workload}, \text{rest}, \text{role})$.

### 15. Reliever role weighting & aggregate bullpen feature
- Estimate role from PIT usage (recent leverage, innings entered, save/hold usage).
- Aggregate quality: $\text{ExpectedBullpenQuality} = \frac{\sum_r A_r R_r Q_r}{\sum_r A_r R_r}$.
- Output: `effective_bullpen_quality`, `available_high_leverage_count`, `bullpen_workload_pressure`.

### 16. BALLDONTLIE acquisition pipeline
- Maintain raw-only boundary: `capture` $\to$ `normalize` $\to$ `entity reconcile` $\to$ `feature build` $\to$ `forecast`.
- Access/coverage audit: mark `BLOCKED_PROVIDER_PLAN` if endpoints are inaccessible rather than silently substituting stats.

### 17. Canonical MLB player identity registry
- `data/entities/mlb_players.json` mapping `canonical_player_id` $\leftrightarrow$ `mlb_statsapi_id`, `balldontlie_id`, `espn_athlete_id`, name, birthdate, teams. Hierarchy: explicit IDs $\to$ stable metadata $\to$ name+team+birthdate $\to$ manual review. Fail closed on ambiguity.

### 18. Plate appearances backfill
- Completed games $\to$ crosswalk $\to$ PA $\to$ immutable provider snapshots with payload hash and PIT provenance.

### 19. Pitcher arsenal $\times$ hitter profile
- Matchup quality: $\text{Matchup} = \sum_{\text{pitch}} \text{Usage}_{\text{pitcher},\text{pitch}} \times \text{LineupAbility}_{\text{pitch}}$. Shrink small samples.

### 20. Confirmed-lineup model & observation selection
- Filter strictly: $\text{lineup\_state} == \text{pregame}$ and $\text{observed\_at\_utc} \le T$. Never select latest record without timestamp filter.

### 21. Confirmed-lineup features & prospective evaluation
- Batting-order-weighted xwOBA/wOBA, K%, BB%, ISO, barrel%, hard-hit%, platoon-adjusted offense. Compare `projected_offense_pit` vs `confirmed_lineup_offense_pit` on prospective cohort.

### 22. Continuous lineup quality tracking
- Track capture rate, median first-observation lead, median confirmation lead, and lineup-change rate by hour, team, and venue.

### 23. XGBoost & Monotonic XGBoost evaluation
- Evaluate only after richer feature signal exists in LR.
- Chronological date-blocked expanding folds (never random CV). Tune hyperparameters on train/val only.

### 24. Defensible monotonic constraints
- Enforce monotonicity only where domain theory is unambiguous (e.g. better projected offense cannot lower win probability, worse starter quality cannot raise win probability).

### 25. Calibration discipline
- Out-of-fold or held-out validation fits only. Compare raw, Platt, beta calibration, reporting LogLoss, Brier, ECE, slope, and intercept.

### 26. Promotion criteria & evidence standards
- Promotion requires prospective $\Delta\text{LogLoss} < 0$, non-worse Brier, acceptable calibration, stable monthly cohort performance, and no coverage regression.

### 27. Separation of independent probability from markets
- Probability models consume sports information only. Market prices belong strictly in the downstream EV/edge decision layer.

### 28–40. Soccer v2: Dynamic Universe, Hierarchical Dixon-Coles & BTTS
- **Dynamic universe**: `discover_soccer_leagues()` from live Polymarket metadata, replacing static configs.
- **100% accounting invariant**: $\text{discovered} = \text{predicted} + \text{no\_call}$.
- **Identity & Coverage**: Structured IDs over fuzzy matching; fail closed on ambiguous clubs.
- **Dixon-Coles v2 (`soccer-dc-v2`)**: Competition baseline $\mu_c$, $HFA_c$, team attack/defense, $\rho$, exponential time decay ($w = e^{-\Delta\text{days}/\tau}$).
- **Hierarchical strengths**: Partial pooling shrinking promoted/small-league teams toward league/global priors.
- **Unified score matrix**: Single joint distribution $P(H=i, A=j)$ generates ML, Draw, Totals (1.5, 2.5, 3.5), Asian handicap, and BTTS ($1 - P(H=0) - P(A=0) + P(0,0)$).
- **BTTS calibration**: Store in `config/models/soccer-btts-dc-v2.json`.
- **Fail-closed contract parsing**: Verify exact Polymarket contract semantics before pricing.

### 41–44. Data Providers & Secondary Sports
- Order: Football-Data, NWS, Open-Meteo PIT archives, TheSportsDB, OSM, NBA Stats, news/injuries.
- NBA: Player minutes $\to$ offensive/defensive impact $\to$ lineup strength $\to$ pace $\times$ Four Factors.
- WNBA: Conservative sample sizes, hierarchical shrinkage.
- NFL: QB state, injuries, offensive-line availability, EPA success/explosive rates.

### 45–47. Governance, Commit & Documentation Discipline
- Branch protection on `main`, PRs for research changes, CI enforcing pytest, ruff, artifact contracts, and PIT properties.
- Granular, atomic research commits containing code, tests, experiment registry entries, and artifacts.
- Experiment registry verdicts strictly in `{"KEEP", "REJECT", "INCONCLUSIVE", "VOID"}`.

---

## Final 26-Step Execution Order

1. **Create clean `research/mlb-v9` from current `main`.**
2. **Transplant the frozen-research tooling, not the entire old branch.**
3. **Make the frozen Parquet matrix the evaluator's only authoritative input.**
4. **Freeze explicit event IDs and hashes.**
5. **Implement standardized v9 LR with explicit missingness.**
6. **Mark old KBB result VOID and implement true K%, BB%, K-BB%.**
7. **Rerun only the corrected starter-rate experiment.**
8. **Build empirical-Bayes PIT batter priors.**
9. **Build historical projected-offense aggregates without target-game batting orders.**
10. **Ablate projected offense.**
11. **Build richer starter state and expected starter depth.**
12. **Build reliever talent × availability.**
13. **Audit and backfill BALLDONTLIE PA/pitch-type coverage.**
14. **Build canonical player-ID crosswalk.**
15. **Add pitcher-arsenal × hitter-profile matchup signal.**
16. **Keep accumulating prospective confirmed lineups throughout all of this.**
17. **Build the separate confirmed-lineup model once sample size is meaningful.**
18. **Compare standardized LR vs XGB vs monotonic XGB on identical frozen data.**
19. **Lock calibration/model specification.**
20. **Begin genuinely untouched prospective MLB v9 evaluation.**
21. **In parallel, build dynamic Polymarket soccer universe.**
22. **Build hierarchical Soccer DC-v2.**
23. **Derive and separately calibrate BTTS.**
24. **Observe and verify real Polymarket BTTS market semantics before pricing it.**
25. **Wire qualified soccer markets through existing Main/Flat production plumbing.**
26. **Then move into player-level NBA/WNBA/NFL upgrades.**

---

## Explicitly out of scope

- **Kalshi cross-venue arbitrage/best-execution** — deliberately deferred (`KalshiDeferredError` stub, US-residency requirement unmet).
- Deep neural networks on small WNBA/NFL datasets; raw head-to-head records or tiny BvP samples; social-media sentiment; referee/umpire micro-effects before core availability data works; optimizing confidence thresholds against an already-opened holdout; calling generic `-110` units "profit."


