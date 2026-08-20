# MASTER.md — Unified Project Reference (In-Depth)

**Generated**: 2026-08-02 | **Last verified**: 2026-08-19 |
**Session 2026-08-18/19 — v9 ladder measured null; lineup capture live; wake planner built**: Operator directed: fix the sleep gap rather than accept it, add capture-quality metrics, and make batter PIT priors the next experiment. **v9 isolating ladder (7 variants, each changing exactly one term vs the v8 control, 2,000-bootstrap date-cluster paired, 1,389-row holdout): a clean null.** Best change residual_trend ΔLL −0.00017 (P=0.658); PIT park fix does NOT help (+0.00023); starter_fip significantly worse (P=0.025). Scale: constant home base rate 0.6912 vs v8 0.6866 — all six v8 features worth 0.0046 nats at AUC 0.567. NB challenger REJECTED on operator review: its 203-game OOF win never included gamma_poisson (only independent_poisson + skellam); the 3,513-game head-to-head that does shows NB losing 0.68844 vs 0.68813 (P=0.175). Closed branches on measured evidence: rest_disparity, probable_starter_era_gap, starter_fip, starter_kbb. **Lineup capture shipped and live**: verified battingOrder is fully posted at Pre-Game/Warmup hours before first pitch; capture module with PIT contract (timestamps stamped after response; only `pregame` rows decision-grade; unknown status → unknown, never pregame). Live data caught the fixture-missed dedupe bug: identity including observed_at_utc is unique every run → 45 rows for an unchanged 15-game slate (~360 duplicates/day hourly). Fixed via content_hash + first/last_observed_at_utc + capture_count (confirmation evidence survives dedupe; last_observed never moves backwards; merge under exclusive flock). Archive migrated 16/16, 0 collisions, 5 decision-grade rows. **Hourly collector installed and verified** (com.vc.mlb-lineup-capture: exit 0, clean stderr, real JSON — verified by log, not status query); schedule-aware (filters by status BEFORE boxscore fetch; idle hour = one request); carries no runtime/ledger env vars — their absence is the enforcement. **Sleep gap**: launchd coalesces missed StartInterval firings into one run at wake — for lineups that is permanent, west-coast-concentrated loss. `scripts/plan_lineup_wakes.py` follows the slate with one-time wakeorpoweron events (~35 min pre-pitch, coalesced within 20 min) — `pmset` needs root, so it is plan-only until the operator installs a root LaunchDaemon (PENDING). 8 planner tests; the 2 initial failures were both test bugs, proven by computing the tz arithmetic directly. **Quality metrics**: collector records scheduled/eligible/posted/unavailable/capture-rate denominators at run time (schedule is mutable — cannot be reconstructed); `scripts/lineup_capture_quality.py` stratifies by local start bucket — first report already shows 5/5 games in 7-9pm, ZERO 9pm+ coverage, median lead 24.4 min. Note: game_start_utc is scheduled start, so −0.6 min leads are legit Warmup captures. **Corrected plan**: batter PIT priors first (`projected_offense_pit` — participation weights from preceding games ONLY, no target-game order in history; `confirmed_lineup_offense_pit` separate, prospective cohort only), then reliever workload × quality; predeclared 3-component batter family, Beta-Binomial shrinkage from existing machinery; `mlb_v9_feature_table_v2.parquet` versioned with its own hashes, v1 immutable; market benchmark only on the identical 293-row timestamp-valid intersection, labelled "market benchmark cohort — not model-selection cohort". All commits on `research/mlb-v9-lineup-and-bullpen` (a7c9669 settlement fix, ce89eca capture, 70db329 hourly+schema, c9d5800 planner+metrics). 1903 passed, 3 skipped; ruff clean.
**Session 2026-08-18 — model-ledger settlement bug (63% of evidence stranded), WNBA totals retrain rejected**: Operator asked to promote the WNBA total model and to fix/diagnose the WNBA spread model. **Neither promotion happened; champions unchanged.** **Settlement bug (real, live, silent)**: `model_ledger.settle_from_pick_row` graded using the APPEND-side `_prediction_dedupe_key`, which carries `observed_at_utc`, via a single `next(...)` — so of the several rows one event accumulates as the line is re-forecast, only the one whose timestamp matched the settled pick ever graded, and the rest stayed `open` forever behind a documented never-raises no-op. Same silent-evidence-starvation class as the 2026-08-02 fix, one layer down. WNBA spread ledger: 67 rows / 9 settled / 58 open, **42 of them for games already finished** (oldest 08-13), every graded event showing 1 settled + N-1 orphaned; `compute_model_evidence` ran on 9 rows instead of 51. Fixed with `_event_settlement_key` (event/market/line/model/**selection**, no timestamp) + `ModelLedger.settle_event` (all matching rows in one lock/read/write). `selection` is in the key because away +6.5 and home -3.5 resolve oppositely — event 401857151 has exactly that shape (`away → loss`, `home → win`), and keying without it would have written one side backwards as real evidence. 4 regression tests, each verified to fail against the pre-fix key and pass after. Per operator decision, `pnl_units` now lands on every graded row, not only the staked one — `compute_model_evidence` sums it, so reported model P&L scales with re-forecast count (per-prediction record, not a bankroll total). **Backfill**: new `scripts/backfill_model_ledger_settlement.py` grades from the game's FINAL SCORE, not by copying another row's result — the only way to reach rows at a never-staked line and the three events (401857143/144/151) whose picks were all `removed` rather than settled. Refuses to write unless it first reproduces every already-settled row from the score: **9/9, 0 mismatched**; 42 rows settled after a timestamped backup; ledger now 51 settled / 16 open. **Honest reading of the recovered evidence**: those 51 rows are only 14 distinct contracts across 12 events — re-forecast rows are correlated repeats, not independent samples. Per-row 52.9%; **per-contract 7W-7L = 50.0%**, Brier 0.2856 vs 0.2500 for always-0.5. The spread model's normal-CDF pricing is correct (sign convention verified independently); it has no demonstrated edge and is far short of the 50-call minimum. **WNBA totals**: retrained per operator choice — came back **worse** (MAE 24.02 vs rolling-league-mean baseline 16.42, mean error -19.8, gain CI [-9.94, -6.32], `no_improvement`). Two real defects found: (1) `total_score.py` built `last_10_total_avg` as `last_10_avg = baseline`, an exact duplicate of `league_total_mean`, so ridge split one weight across two identical columns (both -1.825776) and the level term pulled twice as hard the wrong way — **fixed** with the real PIT last-10 signal, which improves MLB (3.5503→3.5497), NBA (15.4187→15.3341) and NFL (11.2265→10.9961); (2) `park_factor`/`weather_factor`/`bullpen_rest_days`/`travel_distance` are hardcoded constants for every sport (0.0 coefficients) — baseball features in a shared builder, left in place. Residual-target refit (fit `actual - baseline`) tested under an honest 60/20/20 with alpha chosen on validation only: locked holdout **16.63 vs 17.27 baseline, gain +0.64, CI [-0.48, +1.76]** — better than shipped v1 (which was *provably worse*, CI [-3.39, -0.39] entirely negative) but straddling zero, so still not promotable. Also noted: the shipped `wnba-total-score-ridge-v1` artifact has 9 features while current code produces 11 — it is not reproducible from the tree, and there is **no WNBA totals serving path at all** (`total_research_artifact` is read by nothing but a dashboard display string), so promoting it would have produced zero picks. **Unrequested finding, left as a policy decision**: `total_score.py:238` sets its verdict from a bare point estimate while storing a bootstrap CI it ignores — NFL is currently flagged `improved_vs_baseline=True` on +0.6327 with CI [-0.0427, +1.5336]. 1883 tests pass, 3 skipped; ruff clean.
**Session 2026-08-16 — open-items closure sweep + docs restructure**: Executed the full remaining open-items list from the documentation read-through. Code debt: `SportModel`/`ScoreModel` dead protocols removed; ruff baseline cleared 117 → **0 findings** (exec bits, safe auto-fixes, noqa-with-justification) with `.pre-commit-config.yaml` installed (hook live, ran green on commit); in-code debt markers added (DD-7); DD-14 investigated and closed — the 105 `model_id`-less ledger rows are all from the 08-14 cutover day (0 since; payloads carry no lineage; not backfillable). Detectors/reports: `scripts/check_mlb_ingest_completeness.py` built and run (7-day scan, 0 missing — the P1-12 intermittent miss has not recurred); `outputs/latest/learned-model-validation.json` regenerated via `validate-models` (the `validate-learned` doc name was stale); the 5 remaining ruff sites fixed (including a units.py None-guard ordering bug my own first pass introduced — caught by the suite, fixed before commit). Research layers: D–I feature parity closed on a 40-game sample (elo/trend/park 40/40 exact through serving definitions; weather ≤0.029 archive-drift; starter ≤5e-4 map rounding) plus L orientation note (positive_class inert at serving — consistent, hardening note); park leak verified with numbers (static table = 7,926 games 2024-02-22→2026-08-12 applied retroactively). Infrastructure: launchd dashboard pointed at a deleted `.venvs/model-prediction` interpreter (next restart would have crash-looped) — plist fixed to the repo `.venv` and re-verified (lsof=pidfile=launchctl, evidence API valid, job history intact). Burn-in day 1 PASS (no repo DBs, all supervisor runs completed, ownership agrees, tree clean, main CI green). Git hygiene: 7 more stale remote branches deleted (all verified 0-unique-vs-main); remotes now `origin/main` + `origin/research/mlb-v8-reproduction`. Docs restructured: one-shot records moved to `docs/archive/` with all references updated; PROJECT_STATUS/CONSOLIDATION/CHECKLIST/MASTER refreshed. 1,875 tests collected, 1,872+ pass.
**Session 2026-08-15 — consolidation P0/P1/K/N executed, burn-in (O) started, MLB research prep begun**: Executed the combined two-review plan end to end. **P0-1** `RuntimePaths.resolve(require_external_runtime=True)` fails closed at every operational entry point (supervisor, canary, promotion, system_health, dashboard + data_service, cli_production); env-less invocations raise instead of silently creating a repo-local second runtime (the 2026-08-13 split-brain class). Stray empty repo `data/ledgers/ledgers.db` and stale production/prediction DBs (646 rows, verified strict subset of the runtime copy's 660) quarantined to `backups/split-brain-quarantine-20260815/` (gitignored, local-only). **P0-2** exactly one daily scheduler: dashboard "daily" routes through `run_supervisor` (never `scripts/run_daily.sh` directly); supervisor lease exit 75 maps to job status `skipped` ("another run already active"), never `failed`. **P0-3** one launchd-owned dashboard (`com.vc.model-dashboard`): single :8765 listener, `dashboard/server.pid`/launchctl/lsof agree across three restarts; plist gained `MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite` (the default was xlsx — a third authority path). **P1** `_hydrate_jobs()` at dashboard start preserves job history across restarts (persisted running→interrupted, `started_monotonic` never restored). **K** runtime singularity: rolling/frozen artifact split (`RuntimePaths.models_root` for esports/KBO/NPB retrains; `config/models/*.json` frozen — scheduled retraining never rewrites checked-in champions), ~2,300 churn files untracked (files stay on disk; `data/archive/` and `snapshots/` remain tracked evidence), frozen-champion snapshots + dashboard cache DB + daily worker lock relocated to the runtime root, all repo-local DB relics quarantined. 10/10 K criteria pass — a real supervisor production+daily cycle leaves `git status --porcelain` empty; ledger events 4,144→4,258 with exit 0. **N** exact-head CI green (all 4 jobs incl. ruff/mypy delta gates; three pre-existing CI blockers fixed en route: freezer/calibrate tests reading untracked data, rebuild ruff gate, line-shift fingerprint), PR #30 merged → main `37be479`, tag `consolidation-2026-08-15`. **O** burn-in clock started 2026-08-15 05:25 UTC (≥3 days, through 08-18; checklist + results in `docs/BURN_IN.md`). **Git hygiene** (operator directive): 19 stale local branches + 2 stale worktrees deleted after verification (18 branches had 0 unique commits vs origin/main; the six-sport contract branch's 3 commits were superseded parallel implementations of registry/supervisor/CI; its one dirty doc diff preserved as a patch under `backups/removed-branch-artifacts-20260815/`); merged remote `cleanup/final-debug-2026-08-14` deleted; `main` now tracks `origin/main`. **MLB research prep (isolated worktree at the frozen tag)**: aggregate pin-and-replay re-confirmed (calls 150 vs 148, hit delta 0.0052 — `reproduced_closely=True`) but row-level parity tooling (`scripts/mlb_v8_row_parity.py` on branch `research/mlb-v8-reproduction`, doc `docs/V8_REPRODUCTION.md`) found: (1) **cohort drift** — 31 net-new post-freeze reconciliation rows in the holdout window, and 2 freeze-time rows missing from today's file; v8's build never snapshotted the holdout event-id list (itself a finding); after excluding the 31, calls reproduce 148/148; (2) **coefficient parity FAILS** (refit vs shipped max delta 0.0107) — Elo/trend/park/weather features are computed from full prior history, and post-freeze backfills changed that history, so v8's exact freeze-time probabilities are unrecoverable; row-level p drift bounded at 0.0006; (3) date convention note: harness dates are ET, raw file UTC (4/3 boundary straddles); (4) provenance gap: only 16 of 8,147 games rows carry `raw_source` despite the 08-14 backfill claim. The frozen v9 feature table + standardized evaluator are the next prep items. **No promotion decisions anywhere in this session; champions unchanged.** 1871 tests pass, 3 skipped. See `docs/PROJECT_STATUS.md`, `docs/BURN_IN.md`, `docs/V8_REPRODUCTION.md` (research branch), `docs/PURGE_REQUEST_TEMPLATE.md`.
**Session 2026-08-12 — production canary deployed, all rebuild models converged onto main**: 
WNBA, Tennis, NFL, and Soccer rebuild challengers independently trained, tested, and merged 
into  (). Production canary () configured 
with health checks, CLI, dashboard card, and launchd scheduler. All rebuild challengers 
in  — , research/shadow only. 
Automated order execution remains disabled ( in ). 
Daily pipeline verified 2026-08-11 and 2026-08-12 — Main absent, exit 0, no phantom events.
Worktree cleanup: 4 removed, 18 retained as historical references. 
See  for canary details,  for current state.
**Session 2026-08-05 — worked the full remaining open-item backlog (operator directive: "tackle all of them and reconcile the inconsistency as well")**: Reconciled the P1-17 doc inconsistency (Part 1's "needs follow-up" table still listed it open after Part 2's checklist had already marked it done). Fixed DD-1 (narrowed `international_baseball.py`'s `suppress(Exception)` to real expected failures — two existing tests were accidentally relying on the old broad catch as a mocking shortcut, fixed to use a realistic client double). Fixed DD-2 (F-66) — all 14 silent `suppress(DuplicatePickError)` sites across every per-sport forecast function in `cli.py`, plus 4 structurally identical bare `except DuplicatePickError: continue` sites; a shared helper now makes every secondary-ledger duplicate traceable to the exact existing pick that blocked it, with 6 new regression tests (one per function), each verified to fail against the pre-fix code. Remaining items from this backlog (P1-11/12/13, DD-3/4/8/9/10, the `cli.py`/`dashboard_server.py` package split) continue in later entries below as they're worked.
**Session 2026-08-04 (P1 cleanup)**: All 15 open P1 items worked — 10 code fixes applied (P1-1 through P1-9, P1-14, P1-16), 1 confirmed already-resolved (P1-9), 4 investigated and documented with findings (P1-11, P1-12, P1-13, P1-17). 675 tests pass, 0 fail. See Part 1 for per-item detail.
**Session 2026-08-04 (deep debug)**: Comprehensive codebase audit — found 1 critical `suppress(Exception)`, 14 silent `DuplicatePickError` drops, 8 orphaned feature modules (469 lines), 2 files above 4k lines and growing, zero TODO markers, multiple hardcoded thresholds, no pre-commit lint hook. Full findings in "Deep Debug Audit" section below.
**Session 2026-08-03**: Ledger routing restructured (Main=gated, Flat=everything, Research/Gated=esports/KBO/NPB). Soccer league expansion (+11 leagues). ATP tennis fully wired (WTA+ATP dual-tour). Picks migrated to correct ledgers. Esports/KBO/NPB now also write to Flat. Dashboard 500 fixed (corrupted backup). `research_ledger` default=None bug fixed. P1-17 MLB totals: elasticities bumped, selection-order fix applied (minimal effect — calibration is monotonic; documented for proper refit in roadmap item 5). 30 stale .bak files cleaned. 654 tests pass.
**Session 2026-08-04**: Deep debugging pass across workflow coverage, ledger gating, and MLB probability correctness (operator request: "MLB model is very critical rn" + "some picks have enormous edges"). Main/Flat split into per-sport files (`01634e2`, prior session) further audited: found and fixed a duplicate-row gap the 2026-08-03 soccer-only special case never extended to tennis, plus the symmetric non-flat-run-never-clears-Flat direction neither sport had (F-47). Found and fixed a real MLB-critical audit-trail bug: `bullpen_weakness_gap` (active MLB v7 coefficient) and `defensive_trend_gap` (active NBA/WNBA v4 coefficient) were computed correctly and used correctly in live probability scoring, but `PickRequest.as_dict()` never serialized either into the ledger — permanently blank on every real pick since each was added, the same bug class as the pre-2026-07-25 `pitcher_era_gap` incident, recurring (F-48). Verified this did NOT affect real predictions: hand-recomputed MLB v7 probabilities from raw artifact coefficients against real logged picks and they reconciled. Confirmed MLB itself has no implausible edges (max ~14% in real ledger data); the real "enormous edges" (28-38%) are in esports (LOL, CS2), traced to `NeutralElo` having no confidence discount or inactivity decay for teams that haven't played in 2+ months — flagged as an open model-quality gap, not fixed (see Esports roadmap). Ruff unchanged at 122 baseline findings. **Side effect**: adding the new ledger column triggered the project's own auto-migration path on every Main/Flat xlsx file the moment it was read — verified rigorously (identical row counts and pick_ids, zero field-value drift across all rows, `verify-chain` 0 breaks) before treating it as safe; `.bak-v3`/`.bak-v1` backups and audited `ledger_migrated` events were created automatically. Nothing committed — all changes sitting in the working tree pending review.
**Session 2026-08-04 (later) — coverage/gating deep-verify + MASTER.md cleanup**: Directly re-verified, against live data rather than code-reading alone, that Flat/Research call every real game for MLB/WNBA (1:1 match against ESPN's scoreboard, both dates checked) and NBA/NFL correctly show zero (offseason, matches P1-10's now-removed claim). Tennis's much lower Flat coverage (16-34 matchups/day vs. hundreds of ESPN-listed matches) is confirmed **not a bug** — live dry-run diagnostic shows the gap is `_latest_moneyline_snapshots` correctly declining matches with no unique timestamp-valid Polymarket quote ("no unique timestamp-valid moneyline matched by player name"), not a silent drop; tennis is deliberately "only games with real matched market data," per design. Empirically verified the edge AND confidence gates directly against real data: zero violations across all 60 real CALL rows spanning Main(soccer/tennis)+Gated Research(cs2/dota2/lol/valorant) — every single CALL genuinely clears its sport's configured `min_edge`/`research_confidence_gate` from `config/model.yaml`, no exceptions found. Research ledger row counts confirmed ≥ Gated Research row counts per sport (subset relationship holds) and Gated Research rows are 100% CALL (no downgraded rows persisted there), matching the documented design. **MASTER.md cleanup**: removed all 6 P0 items from the open list (all resolved or confirmed never-live; condensed record + real fixes moved to Fixed Bugs as F-49–F-51), removed P1-10 (not a bug, offseason) and P1-15 (confirmed already-correct half-settlement P&L via direct code read — `ledger.py`'s `settle()` already uses the right formula with guard rails; logged as F-52) from the open P1 list. Numbering intentionally left non-contiguous rather than renumbered.
**Session 2026-08-04 (P0 execution + MLB pitcher work, concurrent with the P1 cleanup session above)**: A real dashboard screenshot (operator-provided) showed every MLB spread/total row blocked with "no exact executable Polymarket US market mapping." Traced to `dashboard_server.py::_pick_quote` being moneyline-only since it was written, unconditionally returning `None` for spread/total even when the exact live Polymarket market genuinely existed — verified live before touching anything. This made P0-1's spread/total/btts execution-time re-verification unreachable from the real order flow despite being tested at the function level (F-53). Caught and fixed a second, more subtle bug while building the first fix (never shipped): the initial spread/total matching had no game-identity check and would have matched across unrelated games sharing the same line — added an event-title check to close it, with a dedicated regression test proving the collision is prevented. Separately, ran the "different functional form" pitcher-feature experiment `docs/MODEL_IMPROVEMENTS.md` flagged as worth trying (real walk-forward test, `starter_era_gap` replacing `pitcher_era_gap` instead of the already-tested additive form) — found the same validation/holdout disagreement as before, reported honestly, not promoted on its own. Operator then explicitly directed promotion anyway; built the missing live infrastructure `starter_era_gap` never had (new `features/starter_history.py`, new daily capture step keeping `data/mlb_statsapi/game_snapshots.jsonl` current — it was a one-time static dump with zero live-update path, caught before it could silently go stale in production), added an MLB moneyline starter-confirmation gate (operator-directed caution measure, not a model requirement), and promoted `mlb-elo-trend-lr-v8.json` with honest `qualified: false` documentation of the validation regression. Verified live end-to-end before considering it done: 13/15 real MLB games priced with real non-zero `starter_era_gap` values. See F-53/F-54. 683 tests pass. Working alongside a concurrent P1-focused session (deepseek) in the same working tree — deliberately scoped this session's own work to P0/execution-safety and explicitly-requested model work, left every P1 item untouched.
**Session 2026-08-04 (later still) — ran the real daily pipeline with --log, found 2 more real bugs the dry-run testing above didn't catch**: Operator instruction: "run daily forecast and make sure the model is working." Ran `scripts/run_daily.sh` for real (not a dry run) and checked actual logged ledger rows rather than trusting forecast-output JSON alone. Found `starter_era_gap` (F-54's own new feature, built hours earlier) had recurred the exact F-48/F-53 audit-trail bug class — never added to `PickRequest`/`as_dict()`/`LEDGER_SCHEMA` when built, so the first real v8-logged pick showed `starter_era_gap: None` despite scoring correctly (F-55). Separately found a real crash in the concurrent P1-2 exception-redaction fix (`the_odds_api.py`): reconstructing `httpx.HTTPStatusError` via `type(exc)(msg)` fails because that class requires `request`/`response` keyword-only args beyond the message, breaking soccer score collection for all 12 Odds-API leagues with a `TypeError` instead of a clean redacted error (F-56) — fixed given explicit operator authorization to touch the concurrent session's files ("touch deepseeks stuff... fix anything left broken"). Also confirmed, by actually running the pipeline, a real and separate (not fixed) issue: The Odds API key itself appears genuinely invalid — all 12 configured leagues return `401 Unauthorized` once the crash is fixed, a credential problem outside what a code fix can address. Also surfaced and corrected an earlier gap in this session's own gating verification: `cli.py::_forecast_learned_sport` has its own separate, real confidence-threshold gate (independent of `evaluate_eligibility`'s edge/exposure/disagreement removal already verified) that skipped every one of today's 13 real MLB games from Main (still correctly logged to Flat) because none cleared v8's newly-learned, more selective threshold — confirmed this is legitimate existing behavior ("operator directive, reversing F-34/F-35"), not a new bug, and not touched. 686 tests pass. Committed and pushed.
**Session 2026-08-04 (final) — full audit of every remaining deepseek-touched P1 file, MLB threshold lowered for real coverage, docs rewritten**: Operator directed lowering v8's confidence threshold for real coverage (F-57) and then a systematic file-by-file audit of every file touched by the concurrent P1 session, following the same diff-then-verify pattern that had already found F-56. Checked all 7 remaining files (`bans.py`, `eligibility.py`, `esports.py`, `ingest.py`, `international_baseball.py`, `features/player_availability.py`, `data_sources/polymarket_us.py`) against their pre-P1 versions. Found and fixed two more real, complete bugs: `bans.py` referenced a nonexistent `entities.PlaceholderTeam` class in its registry-free-league fallback (would `ImportError` on first real use) and separately never applied that same fallback inside `add()`/`remove()` at all — three unguarded `registry.resolve()` calls (F-58). `features/player_availability.py`'s conflict-policy fix (default `most_conservative`→`fail_closed`, intended to force a real source disagreement to fail the whole forecast closed) was silently defeated by a pre-existing `suppress(ValueError, ...)` wrapper at the one real call site, written for a different, legitimate purpose (treating "ESPN just hasn't posted a snapshot yet" as a silent fall-back) — the two exception types collided, so a genuine conflict (e.g. ESPN reporting a star player Out while the official report still says Available) was silently discarded with zero record, `availability_source_conflict_count` reporting 0, and the model scoring the player as 100% available. Reproduced live before fixing (`conflict count: 0`, `home_available_minutes_share: 1.0` for a fabricated real conflict) — this is the same "old code's exception handling wasn't updated for a new exception's meaning" bug class as F-56, just one level higher (F-59). Also hardened `data_sources/polymarket_us.py`'s new event-pagination loop (a real, correct fix for a previously-undiscovered single-page 200-event cap) with a hard page cap, since it had no upper bound and this project's own convention (`audit.py`'s lock-wait timeout) is to never leave a `while True` network loop unbounded — an API that ignored `offset` would otherwise hang the daily pipeline forever with unbounded memory growth (F-60, defensive hardening rather than an observed live failure). `eligibility.py`'s future-timestamp fix (P1-4) was verified correct as shipped but had zero test coverage for either `evaluate_eligibility` or `evaluate_esports_eligibility`; added regression tests for both, confirmed both fail against the pre-fix code. `esports.py`/`international_baseball.py`'s logging-instead-of-silent-except changes were correct but introduced a new (non-baseline) `ruff` import-sort finding in each; fixed. `ingest.py`'s reorder (mark-as-existing after write instead of before) is a real, minor crash-safety improvement, verified correct, no test needed. Every fix verified via the established revert/confirm-fails/restore cycle. 692 tests pass, ruff clean (baseline findings only). Committed and pushed.
**Session 2026-08-04 (final, later) — P1-17 MLB totals refit, promoted (operator directive: "Refit and promote, like starter_era_gap")**: Found `mlb-analyst-poisson-trend-v0.3.yaml` — a real Poisson-GLM elasticity refit, already built 2026-08-02, deliberately never promoted (a dedicated test locked in its rejection) — sitting unused. Promoted it (ENGINE_VERSION + both Measured Edge model-version constants bumped, `cli.py`/`mlb_baseline_refresh.py`'s hardcoded formula paths repointed so the daily baseline refresh doesn't silently keep patching the now-retired file forever), then rebuilt fresh margin/totals calibration artifacts against it. Caught and fixed a real bug in `mlb_measured_edge_calibrate.py` itself while doing this: `main()` hardcoded stale `"...v2"` model-version literals regardless of the actual output filename, which would have made this exact promotion (and any future one) silently unloadable once the version constants moved. Honest result: margin/spread genuinely improved (correlation 0.2057→0.208, hit rate 59.5%→60.0%); totals specifically got marginally worse (correlation 0.0585→0.0414, hit rate 55.3%→52.9%), and the previously-reported 71% over-pick figure couldn't be reproduced against the full diagnostic dataset in either formula version — confirms rather than resolves the project's own standing diagnosis that totals needs an absolute-run-environment signal, not better relative elasticities. Promoted anyway (both markets share one simulation; margin's real gain can't ship without moving totals too), matching the starter_era_gap precedent of promoting on operator directive with full honest documentation rather than a clean win. Verified live end-to-end via a real dry-run forecast. See F-62. 694 tests pass. Committed and pushed.
**Session 2026-08-04 (final, even later) — esports NeutralElo inactivity decay + thin-data confidence discount, promoted as v6 for all 5 titles (operator directive: "Yes, add decay + discount and promote")**: Addressed the documented "largest, least-trustworthy edges in the system" gap. Added two prediction-time-only adjustments to `NeutralElo.probability()` (never `raw_probability()`, which stays pure for rating updates): inactivity decay (rating pulled toward neutral the longer since a team's last match) and thin-data shrink (probability shrunk toward 0.5 when either side has few recorded games). Ran the real training/validation pipeline for all 5 titles to build real v6 artifacts. Verified the fix does what it claims: mean predicted edge for thin-data matchups dropped 30-35% across every title on real, held-out locked-test data. Honest, per-title trade-off reported rather than smoothed over: aggregate locked-test accuracy moved slightly worse in 4/5 titles, slightly better in 1 — an expected, disclosed cost of deliberately reducing confidence on genuinely uncertain matchups. Verified live end-to-end. See F-63. 699 tests pass. Committed and pushed.
**Session 2026-08-04 (final, last) — failure-injection test audit found the work already done, closed one real coverage gap**: Task: add failure-injection tests for ledger/audit atomicity (a standing open item since P0-2). Found real crash-injection tests already existed in `tests/test_ledger_hardening.py` (since commit `222b6a6`, predating this session) — both docs' "no failure-injection tests" claim was itself stale, never updated after those tests were written. Closed the one genuine gap: extended the existing crash test to confirm the operator-facing `_verify_chain` tool itself detects the orphaned-audit-event state, not just that raw audit/ledger data disagrees when inspected by hand. Corrected both docs. See F-64. 699 tests pass (test count unchanged — an existing test extended, not a new one added).
**Session 2026-08-05 — Git LFS for the two growing repo-hygiene files, forward-only (operator directive: "Track going forward only (safe)")**: Installed `git-lfs`, added `.gitattributes` tracking exactly `data/events.jsonl` and `data/mlb_statsapi/game_snapshots.jsonl` (85MB/61MB, both flagged in the repair order as heading toward GitHub's 100MB hard cap). Merged the LFS pre-push hook into the existing, load-bearing pytest/mypy pre-push gate rather than letting `git lfs install` overwrite it. Renormalized both files into LFS starting with this commit — existing history untouched, matching the requested forward-only semantics exactly (not a history rewrite). Verified the push actually uploaded both LFS objects (`git lfs push --dry-run` empty afterward) and the full suite (699 tests) stays green with LFS active. See F-65.
**Session 2026-08-13 — deep audit + full fix pass (F-72 through F-84), plus Main-ledger un-retire (operator directive)**: Ran the complete 10-step audit against the 2026-08-13 tree (audit snapshot evidence in this session's DEBUG.md sections and docs/PROJECT_STATUS.md's "2026-08-13 deep-audit fix pass"). Found and fixed, all verified with regression tests: **F-72** `compare-champion` CLI always crashed — `bootstrap_ci` dict values indexed with `[0]`/`[1]`, KeyError swallowed by the NO_CALL handler, exit 0 with a bogus `NO_CALL_INVALID_MARKET` (cli.py); **F-73** `freeze()` silently treated missing artifacts as CODE_BACKED and kbo/npb artifacts were written as `-v1.json` while claiming `model_version v2` — files renamed v1→v2, config refs fixed, freeze now fails loudly for artifact-backed holes (champion_challenger.py, international_baseball.py); **F-74** `load_settled_predictions` mixed artifact versions (MLB "champion" metrics were 244 v7 rows vs 14 v8) — now filtered by `model_version`; **F-75** champion/challenger CI escape hatch was dead code — now implemented as documented (`ci_low > 0` required to reject); **F-76** `dashboard/rebuild_status.py` assigned to `locals()` (a CPython no-op) so three new probability fields were permanently None; **F-77** market-evaluation join used the Polymarket slug against the autoincrement rowid — market metrics always null live, latent `int()` crash on non-numeric slugs; fixed to the real FK `evaluated_market_evaluation_id`; **F-78** probability-precedence regression surfaced raw over the conservative lower bound — conservative order restored (calibrated → conservative → lower → upper → raw); **F-79** three MLB v9 train/serve feature-definition skews (`residual_trend_gap` league-wide vs team-specific, `park_factor` PIT-empirical vs static table (which also carries a 2026-season leak), `bullpen_fatigue_gap` 3-calendar-days vs last-10-games) — training now matches serving literally; v9 variants moved to the new `park_factor_pit` feature while v8 keeps its trained static contract; **prior v9 ablation numbers are void and must be re-run**; **F-80** `stable_seed` gained a `method` component that silently shifted every incumbent Measured Edge simulated price bit-for-bit — default path restored to the pre-refactor stream and pinned by test; **F-81** ProductionLedger was 341 unwired lines with no lifecycle — now written fail-soft by every predict cycle with guarded settle/void/supersede/error transitions and CLI commands; **F-82** canary `_check_data_freshness` was a stub (HEALTHY while predictions frozen since 08-11) — now real vs `max_data_age_minutes`; **F-83** `latest_health` same-microsecond ties produced duplicate rows — ROW_NUMBER tiebreak; **F-84** hygiene/stale-test batch: dashboard price-scan test, canary allowlist tests (13-model shipped behavior), production evidence tests, cs2 drift test, kbo/npb config + fixture renames, NBA/NFL dangling spread/total config refs removed, `dashboard/server.log` untracked, DEBUG.md hash snippet corrected to the loader's `ensure_ascii=True` convention. **Main ledger un-retired** 2026-08-13 by explicit operator directive: `main_ledger_enabled: true`, archived per-sport workbooks restored to `data/main/` (Aug 11–13 gap documented as historical), Phase B config-pinning tests updated. **Both launchd agents loaded and verified 2026-08-13** (post-audit follow-up): `com.modelprediction.production` and `com.modelprediction.rebuild-shadow` bootstrapped and each confirmed with a manual `launchctl kickstart` — production canary wrote a fresh prediction batch (predictions.db 623→626 rows), rebuild-shadow wrote fresh trade_decisions (shadow.db 349→365 rows, unfrozen from its 08-10 stall). Verifying rebuild-shadow surfaced a real bug in the just-rewritten `run_rebuild.sh`: its sports-enablement filter read `cfg["rebuild"]["sports"]` but `config/rebuild.yaml` has `sports:` at the top level, so the filter always resolved to `{}` — fixed to `cfg.get("sports", {})`, re-verified it now runs exactly the 6 enabled sports (mlb/wnba/nba/nfl/soccer/tennis) with zero failures and correctly skips the 3 disabled ones (esports/kbo/npb). **Still open, awaiting explicit operator action**: `outputs/rebuild/verification.json` regeneration; v8 park-factor 2026-table leak (needs a refit under v8's contract).

**Session 2026-08-13 (later) — park_factor_at regen bug, v8 reproduction confirmed, SOP merge**: Found and fixed a live regression (~05:16–06:15 local): `park_factor_at()`/`compute_park_factors_from_games()`/`_game_date_str()` had been hand-added directly into `park_factors.py`, an AUTO-GENERATED file (`mlb_baseline_refresh.refresh_park_factors`, a daily-job step) — the next scheduled regen silently wiped them, breaking `validation.py`/`learned_forward.py`/`cli_production.py` imports and taking down both the production canary and rebuild-shadow for about an hour. Moved the three functions to a new hand-written module, `features/park_factors_pit.py`, that the generator can never touch; both agents re-verified healthy afterward. Separately, resolved the open "MLB v9 ablation's v8 reproduction gate fails" item: added optional date-boundary/fixed-threshold parameters to `build_walk_forward_rows`/`chronological_split`/`evaluate_variant` (additive, default-`None`) and a new `scripts/mlb_v8_reproduction.py` that pins a replay to v8's own recorded training-block date boundaries and `confidence_threshold` instead of letting them drift with the growing dataset — reproduction is now near-exact (148 calls vs. 148, hit-rate delta 0.0068, Brier slightly better), confirming the earlier "gate failure" was a harness artifact (fractional split + threshold relearning on a growing dataset), not a real data or pipeline problem. The ablation gate can now be trusted for future v9 promotion evaluation. Also merged a user-supplied governance SOP into `docs/MODEL_IMPROVEMENTS.md` (+255 lines, purely additive): a 5-state reporting-verdict taxonomy (REJECT/INCONCLUSIVE/CONTINUE_RESEARCH/CONTINUE_SHADOW/PROMOTION_CANDIDATE), a consolidated Empirical Bayes/shrinkage reference, a Python/ML stack-conventions section (noting the two parallel MLB pipelines — legacy StatsAPI-driven vs. newer pybaseball/Statcast `rebuild/`), and the previously-missing Tennis and Soccer roadmap sections.
**Session 2026-08-14 — KBO settlement bug, retired-model-version archival, orphaned-branch ledger repair, ops verification**: Investigated an operator report of "picks with no units and broken pnl" in the esports ledgers. Found two distinct real issues, neither a math bug: (1) 365 rows across Research/Gated/Flat carried `reason_code NO_CALL_WINNER_OVERVALUED`, a value-gate check that exists in zero files anywhere in the current codebase — traced via `git log -S` to commit `ed580af` on the never-merged `archive/rebuild-clean-slate-v1-...` tag, whose lifetime (Aug 6–9) matches every affected row's timestamp window exactly; the working tree was evidently checked out onto that branch for ~4 days while the daily job ran against it, writing real rows into the canonical ledgers with logic later abandoned. Repaired by backfilling `units`/`pnl_units` via the live `edge_scaled_units()` formula (the same one every peer NO_CALL row already uses, satisfying this codebase's own "every logged pick carries a real paper size" invariant) without retroactively promoting any row to CALL. (2) Separately found and fixed a genuine settlement bug: every one of KBO's 16 settled research-ledger rows showed `away_score=0, home_score=0` (statistically impossible — 16 different games can't all be scoreless ties); NPB, same code path, had zero such rows. Root cause: `parse_kbo_rows` (`international_baseball.py`) fabricated a `game_id` from date+teams whenever the official schedule page's relay cell was empty (an unplayed game, rendered "0 vs 0"), and that phantom row cached as a genuine scoreless tie — NPB's parser already skipped unplayed rows, KBO's didn't. Fixed to skip instead of fabricate, plus a guard against already-cached phantom rows from before the fix; 2 regression tests added. All 16 affected rows self-corrected via the ledger's audited `pick_resettled_corrected` path on the next scheduled settlement run once the fix was on disk — no manual ledger surgery needed. Also archived 406 settled picks under retired model versions (MLB moneyline v7→v8, spread/total v1/v2→v3; LOL/CS2/Dota2/Valorant v5→v6) across Main/Flat/Research/Gated Research via the sanctioned `archive_settled_rows` path, following the `2026-07-31-retired-mlb-model-picks` precedent (manifest + per-tier archive files, row-count-reconciled exact, `verify-chain` 0 breaks). Removed a `CLAUDE.md` section with unrecoverable data loss (empty template placeholders baked in at commit time — confirmed via `git log -p`, no original content ever existed to restore) rather than fabricate replacement content. Verified two claims from an external review against `origin/main` (both confirmed real, both already fixed in local unpushed commits, neither present on remote): `production.yaml` allowlisting 13 models while `production_canary.py` on `origin/main` requires exactly 1 (mechanically incompatible there), and `_check_data_freshness` on `origin/main` being a literal `return None` stub. Pushed local `main` (8 commits ahead of `origin/main`) to `origin/cleanup/final-debug-2026-08-14` for exact-head CI rather than merging blind. Verified both launchd agents are not just loaded but genuinely advancing (`state=active`, `last exit code=0`, `predictions.db`/`shadow.db` both written within the current 3-hour scheduling interval). Corrected stale claims in `README.md`/`CHECKLIST.md`/`docs/PROJECT_STATUS.md` (test-pass counts, branch count, three esports model-version references still at v5 after the v6 promotion, three "known issues" already resolved in later docs). 1759 tests pass, 3 skipped (up from 1753). Ruff unchanged at baseline (~120 findings, all pre-existing).

**Depth**: Exhaustive — every bug, gap, and TODO from all 2,868 lines of `DEBUG.md`
plus `TODO.md`, `CHECKLIST.md`, `PROJECT_STATUS.md`, `ENGINEERING_ROADMAP.md`,
`HISTORY.md`, `FEATURE_REGISTRY.md`, `MODEL_IMPROVEMENTS.md`, `AGENTS.md`,
`ARCHITECTURE.md`, `LEDGER_ROUTING.md`, `CLAUDE.md`

**Verification note (2026-08-02, later)**: this file was generated from a
snapshot that predates a large, separate architecture change made later the
same day (the "every model runs in production, no classification, operator
decides" directive — see Part 0 below, added after direct verification against
live code). Several P0 items below were re-checked directly against the
current codebase at that time; corrections are inline where a claim no longer
matched reality, not silently edited away.

**Verification note (2026-08-03)**: every remaining open P0 item was worked
this session, directly against live code, with real tests and (where a real
artifact was needed) real training runs against real settled data — see each
P0 entry below and Part 1's "Fixed 2026-08-03" additions. Three of the five
(P0-3b, P0-2 already noted above, P0-6) turned out to be **stale claims**,
not live bugs — re-verification found the described failure mode does not
reproduce against current code. This is the same pattern as P0-2's original
correction: this file mixes claims carried over from `DEBUG.md`'s history
with claims re-checked live, and accuracy varies by which. Two (P0-4, P0-5)
were real, confirmed dead/wrong config, now fixed with real artifacts/tests.
One (P0-1) had a real, confirmed gap (spread/total/btts had no live side
resolver at all), now closed with a real resolver, live-data-verified. Also
found: `tests/test_cli.py` **already exists** (1,358 lines) — P2-1's "no
tests/test_cli.py" claim below is itself stale; left uncorrected in place
since it wasn't in this session's scope, flagged here so it isn't relied on.

---

## Quick Status

| Metric | Value |
|---|---|
| Tests | **1,875 collected / 1,872+ pass, 3 skipped** (2026-08-15; the +3 are the 2026-08-15 regression tests: supervisor fail-closed, runtime-paths fail-closed, store thread-affinity) |
| Ruff | **0 findings** (2026-08-15 — baseline cleared: exec bits removed, safe auto-fixes, noqa-with-justification for deliberate catches; `.pre-commit-config.yaml` added) |
| Audit chain | **Re-verified 2026-08-14**: `verify-chain` reports `chain_intact: true`, `break_count: 0`, after today's 406-row archival + KBO settlement corrections (73,333 audit lines). |
| Git | **Single branch: `main`** (2026-08-15 hygiene pass: 19 stale branches + 2 worktrees removed after verification). `main` tracks `origin/main`; consolidation merged via PR #30 (`37be479`), tag `consolidation-2026-08-15`. Research workspace: worktree at the frozen tag, branch `research/mlb-v8-reproduction`. |
| Last pushed commit | `main` (2026-08-15); CI green on the exact merged head; subsequent pushes are docs/fix commits. |
| CI | `.github/workflows/ci.yml` — ruff + pytest on every push/PR, Python 3.12, ubuntu-latest. Pre-push hook also runs pytest (blocking) + mypy (advisory) locally. |
| Dashboard | Live at `127.0.0.1:8765`, launchd-managed, per-session token-based auth. Order-readiness (`_pick_quote`) now correctly resolves spread/total, not just moneyline (F-53). |
| Daily pipeline | **Running through 2026-08-04**, two real `--log` runs verified today; `run_daily.sh` does settle → ingest → daily forecast; locked via `daily_lock.py`. New capture step keeps `data/mlb_statsapi/game_snapshots.jsonl` current (F-54). |
| BBO capture | 8 sports active in `data/odds/` (MLB, WNBA, esports, KBO, NPB, soccer, tennis) |
| Sports modeled | 14 sports across 4 ledger tiers, **plus** 12 real per-model ledgers under `data/model_ledgers/` (new architecture, see Part 0) |
| Release status | **P0 fully resolved, P1 fully resolved (both re-verified 2026-08-04).** Real-money execution readiness is a separate, still-open question — see `docs/PROJECT_STATUS.md`'s Release verdict section for the current honest answer (short version: the pipeline works correctly; MLB v8 is honestly `qualified: false`; that's not the same as "ready to risk capital"). |

### Active Model Versions

| Sport | Artifact | Status | Locked-holdout | Real units? |
|---|---|---|---|---|
| MLB moneyline | `mlb-elo-trend-lr-v8` | shadow_qualified (override) | 58.5%, +41.3u/-110 (352 calls, threshold lowered 2026-08-04 to target_hit_rate=0.60 for real Main coverage — see F-57) — `qualified: false` for two honest reasons: validation Brier regressed vs. v7's retired feature set, AND holdout no longer clears the 60% bar at this looser threshold either. | Yes — Main ledger, 1.0-2.0U |
| MLB spread | `measured-edge-margin-v3` | active_research | — | **Yes — Main ledger** (corrected 2026-08-04; the "Flat only, zero-unit" claim below is stale — spread/total are real, sized Main-ledger rows, gated on both confirmed starters, same as moneyline). **v2→v3, 2026-08-04 (F-62/P1-17)**: real, marginal improvement from the promoted `mlb-analyst-poisson-trend-v0.3` Poisson-GLM elasticity refit — diagnostic correlation 0.2057→0.208, hit rate 59.5%→60.0%, +39.36u→+41.45u/285 picks |
| MLB totals | `measured-edge-totals-v3` | active_research | — | **Yes — Main ledger** (same correction). **v2→v3, 2026-08-04 (F-62/P1-17)**: promoted the same real elasticity refit (shared Trend Engine simulation with spread — can't move one without the other), but totals specifically got marginally *worse* on the diagnostic window (correlation 0.0585→0.0414, hit rate 55.3%→52.9%, +6.82u→+0.73u/68 picks). The previously-reported "71% over-picked" figure could not be reproduced against the full 284-game diagnostic set in either the old or new formula (both show a near-balanced ~52-53% over/under raw selection split) — see the honest writeup in `config/model.yaml`'s `problem_cohorts.totals` note and F-62 below. Still not fixed; still needs the absolute-run-environment-specific work already on the roadmap (`totals_specific_market_residual`/`branched_absolute_run_intensity_head`), not another elasticity refit. |
| NBA moneyline | `nba-elo-trend-lr-v4` | shadow_qualified | 73.66%, 88.2% called | No — Flat only |
| WNBA moneyline | `wnba-elo-trend-lr-v4` | shadow_qualified | 67.48%, 100% called | Yes — Main ledger |
| NFL moneyline | `nfl-elo-trend-lr-v4` | shadow_qualified (offseason) | 71.26%, 71.3% called | No — Flat only |
| Soccer | `soccer-poisson-dc-v1` | shadow_qualified (override) | 62.5%, +90.4u | Yes — Main+Flat override; execution blocked by missing walk-forward artifact |
| LOL | `lol-tiered-elo-v6` | shadow_qualified (override) | — | No — Gated Research. **v5→v6, 2026-08-04 (F-63)**: added inactivity decay + thin-data confidence discount, real ~33% reduction in mean edge for thin-data matchups; locked-test accuracy 70.6%→69.2% (disclosed trade-off) |
| CS2 | `cs2-tiered-elo-v6` | shadow_qualified (override) | — | No — Gated Research. Same v6 promotion (F-63); this title's locked-test accuracy improved slightly (65.8%→66.0%) |
| Dota 2 | `dota2-tiered-elo-v6` | shadow_qualified (override) | — | No — Gated Research. Same v6 promotion (F-63); largest accuracy trade-off of the 5 titles (68.1%→64.8%) |
| Valorant | `valorant-tiered-elo-v6` | shadow_qualified (override) | — | No — Gated Research. Same v6 promotion (F-63) |
| Rainbow Six | `rainbow_six-tiered-elo-v6` | research | — | No — Research only. Same v6 promotion (F-63) |
| KBO | `kbo-tie-aware-elo-v2` | shadow_qualified (override) | — | No — Research only (no Polymarket markets) |
| NPB | `npb-tie-aware-elo-v2` | shadow_qualified (override) | — | No — Research only (no Polymarket markets) |
| Tennis | `tennis-surface-elo-v1` | research | — | No — Research only (WTA only) |

**Note (2026-08-02, later)**: the "shadow_qualified"/"research" status column above
still reflects the classification system this file's rest of the content critiques
throughout (P0-3, NS-6). That system was operator-directed out of the routing/
execution path later the same day — see Part 0 immediately below. The column
values themselves are still accurate as config labels; they no longer gate
whether a model produces a real logged prediction or whether an order can be
submitted.

---

# PART 0: Architecture change made after this file was generated (2026-08-02, later)

Operator directive, verbatim: *"recompile all models will be production in its
own ledger, the classification of benchmarks or shadow should not exist, there
should be no classification, all models are the same. i decide to promote it or
not."* Followed by explicit confirmation to build the real thing ("all of it in
order"), not just discuss it. This is a real, substantial, already-shipped
change this file does not know about. Everything below was directly verified
against live code and real data before being recorded here.

### What shipped

- **`src/model_prediction/model_ledger.py`** (new) — one `.xlsx` ledger per
  *model identity* (not per sport/routing-destination), schema per the
  operator's own spec: `model_id`, `model_version`, `artifact_hash`,
  `code_revision`, `feature_schema_version`, `model_probability`,
  `model_projection`, `model_uncertainty`, `decision_price`,
  `market_no_vig_probability`, `model_market_difference`, `observed_at_utc`,
  `event_start_utc`, `input_availability`, `missing_inputs`, `source_lineage`,
  `status`, `result`, `closing_price`, `probability_clv`, `pnl_units`,
  `settled_at_utc`, plus a separate operator-decision block
  (`operator_decision`, `operator_selected_model`, `operator_selected_market`,
  `operator_units`, `operator_timestamp`, `operator_note`) that never mutates
  the model's own fields. `append_failure()` only accepts a fixed
  `INTEGRITY_FAILURE_REASONS` set (event started, identity unresolved, bad
  artifact hash, stale feature timestamp, wrong market, unmapped side,
  calculation failure, undefined missing-input behavior) — per the operator's
  own list of the *only* things allowed to block a numeric prediction now.
- **`scripts/migrate_to_model_ledgers.py`** (new) — read-only against every old
  ledger (`picks.xlsx`, `flat_picks.xlsx`, `research/*.xlsx`,
  `gated_research/*.xlsx`); real run: 688 source rows scanned → **483
  genuinely unique decisions** written across 12 real models (deduped by
  market identity, same key as `ledger.py`'s own `_market_duplicate_key`).
  Idempotent — verified re-run writes 0 new rows.
- **Live pipeline wired in** — `ledger.py::_append_record` (the one chokepoint
  every sport's `append_evaluated`/`append_call` already shares) now also
  writes to the matching `ModelLedger`, fail-soft (a write failure there
  cannot break the real, working primary ledger write — verified with a
  simulated-failure test).
- **Classification removed from routing and execution**:
  `lifecycle.py::can_create_qualified_call` no longer requires
  `SHADOW_QUALIFIED` — RESEARCH/SHADOW_CANDIDATE/SHADOW_QUALIFIED/DEGRADED all
  equally produce a real call now. `RETIRED`/`SUSPENDED` remain hard stops
  (explicit "off" states, not promotion tiers — kept intentionally distinct
  from the gate that was removed). `PolymarketExecutor.execute()` no longer
  requires `QUALIFIED_SHADOW_CALL`/a manual override to submit an order —
  **this directly supersedes P0-3 below**, which is now a resolved, deliberate
  design decision, not an open bug. Every *other* execution gate (credentials,
  ticket-to-row binding, cost recompute, single-order dedup, live
  side/pregame/quote-freshness verification, interactive confirmation, audit
  chain) is unchanged.
- **Dashboard**: new "Models" tab — an evidence table (sample size, Brier, log
  loss, CLV coverage/mean, missing-input rate, PnL — no qualified/research
  badges) plus a live one-event/every-applicable-model comparison view, backed
  by a new `/api/model-ledgers` endpoint. Operator-decision recording wired via
  `/api/model-ledgers/decision`, reusing `ModelLedger.record_operator_decision`
  through the same local-import "heavy import" pattern `dedupe_ledger` already
  used (dashboard_server.py keeps its zero-module-level-import-from-
  model_prediction property).
- **Also shipped this session, unrelated to the classification change**:
  per-session dashboard bearer-token auth (closes the "no auth on order
  execution" gap — see F-2 below, already recorded); a real `orders.json`
  read-modify-write race fixed (`_reconcile_orders` now holds `_ORDER_LOCK`);
  `config/model.yaml` schema validation added at `load_config()` (catches a
  typo'd `status` at startup instead of a cryptic failure deep in a forecast
  call); rollback-backup safety net for esports/KBO/NPB's intentionally
  continuously-refreshed ratings artifacts (a `.previous.json` copy before
  each overwrite); a real crash-on-slate-capture-failure bug fixed in `daily`
  (a transient Polymarket network error used to take down the *entire* day's
  forecasting for every sport, even though each sport fetches its own market
  data independently); `rationale`/`risks` were never exposed anywhere in the
  dashboard, for any ledger, ever — fixed (backend field list + frontend
  pick-detail drawer).

### What did NOT ship (explicitly, so it's not mistaken for done)

- **Live pipeline cutover is additive, not a replacement.** `cli.py`'s ~15
  forecast functions and `daily` still write through the old `PickLedger`
  exactly as before. `data/model_ledgers/*.xlsx` receives every new
  prediction going forward (verified live), but nothing has been switched
  *off* the old system.
- **No new statistical models exist.** Total-score Ridge, tennis point-Markov,
  roster-aware esports Elo variants, joint Negative Binomial totals (NS-1
  through NS-4 below) — zero code. Explicitly declined to fake these as
  placeholders; this is real data-science research, not wiring.
- **Dashboard redesign (NS-6) is partial.** The new Models tab covers the
  "one event, every model, evidence not badges" spec. The *old* dashboard
  views (picks with QUALIFIED_SHADOW_CALL/RESEARCH_OBSERVATION badges) are
  untouched and still the primary UI.

### Real numbers, verified against live data (2026-08-02, later)

Per-model settled record from the new ledgers (independently cross-checked
against numbers already confirmed earlier the same session). **Superseded
by the corrected table directly below** — kept as-is (not edited) per this
file's own "correct in place, don't silently edit away" convention.

| Model | Settled | Record (W-L-P) | P&L (U) |
|---|---|---|---|
| `mlb-moneyline-elo-trend-lr` | 40 | 25-15-0 | +4.66 |
| `mlb-spread-measured-edge` | 39 | 25-14-0 | +1.07 |
| `mlb-total-measured-edge` | 38 | 14-24-0 | -9.39 |
| `soccer-poisson-dc` | 62 | 28-19-15 | +6.84 |
| `tennis-surface-elo` | 97 | 58-39-0 | -6.17 |
| `dota2-tiered-elo` | 13 | 8-5-0 | +3.33 |
| `wnba-moneyline-elo-trend-lr` | 15 | 11-4-0 | -0.18 |
| `valorant-tiered-elo` | 8 | 4-4-0 | -0.39 |
| `cs2-tiered-elo` | 26 | 13-13-0 | -3.18 |
| `lol-tiered-elo` | 15 | 7-8-0 | -4.06 |
| `kbo-tie-aware-elo` | 0 | 0-0-3 (pushes) | -0.17 |
| `npb-tie-aware-elo` | 0 | — | 0.00 |

### Live-run verification (2026-08-02, latest) — 3 real bugs found and fixed; `soccer-poisson-dc` numbers corrected

Ran a real `daily` end-to-end and cross-checked results against the model
ledgers, per operator instruction to actually verify rather than assume.
Found and fixed 3 real bugs (full detail in DEBUG.md's matching dated
section — this is the summary):

1. **Soccer moneyline draws were graded PUSH, should be LOSS.**
   Polymarket's soccer win market is 3 independent Yes/No contracts (home
   wins / draw / away wins), not one 2-outcome market with a tie —
   confirmed correct by contrast with KBO/NPB, which really are 2-outcome
   markets and really do settle a tie at $0.50 (verified, unchanged,
   nothing wrong there). `pricing.py::grade_pick` now takes a `league`
   parameter; soccer ties grade LOSS. 15 already-settled historical rows
   (mirrored in `flat_picks.xlsx`, `research/soccer.xlsx`, and
   `model_ledgers/soccer-poisson-dc.xlsx`) corrected via the sanctioned
   archive/re-import/re-settle path, original content preserved at
   `data/archive/2026-08-02-soccer-draw-push-bug/`.
2. **Model ledger dedupe key silently dropped genuine re-forecasts** of an
   event whose still-open pick got replaced (missing `observed_at_utc` in
   the key). Fixed, backfilled 109 real rows via re-running the (idempotent)
   migration script.
3. **`ModelLedger.settle()` existed but was never called from anywhere** —
   model ledger rows stayed `open` forever, so the per-model hit-rate/
   Brier/calibration evidence this whole architecture exists to produce
   never actually populated. Wired into `PickLedger.settle()`, fail-soft,
   same pattern as the append-side hook.

**Corrected `soccer-poisson-dc` record** (the only model whose numbers
changed — every other row in the table above is still accurate):
**62 settled, 28-34-0, PnL -10.41** (was 28-19-15, +6.84). This is a real,
material correction to the one model with a genuine settlement-logic bug,
not a rounding update — soccer's real record is meaningfully worse than
what the migration originally captured.

Full suite: 645 passed. Ruff: 118 findings, matching the existing baseline
exactly, 0 new.

---

# PART 1: EVERY KNOWN BUG — Open and Fixed

## 🔴 P0 — Capital Safety (release blockers)

**None open as of 2026-08-04.** All 6 items originally logged here were either resolved with real fixes and tests (now in the Fixed Bugs section as F-49–F-51) or, on direct re-verification against live code, turned out to have never been live bugs — the claim was stale (read wrong/old code) or an artifact of the verification script itself, not the code:

- **Ledger/audit atomicity**: the real code appends the audit event *before* the ledger write, the opposite of what was originally claimed. True cross-file atomicity across separate ledger/audit files still doesn't exist (a lower-severity, still-real architectural gap), but the specific "silent mutation with no audit record" failure mode never happened. **Correction, 2026-08-04**: the "no failure-injection tests" half of this claim was itself stale — `tests/test_ledger_hardening.py` already had real crash-injection coverage (`test_ledger_write_crash_leaves_a_recoverable_audit_event_not_a_silent_gap`, a genuine simulated mid-write crash via `write_xlsx_rows_atomic` monkeypatched to raise, plus a lock-ordering test) since commit `222b6a6`, well before this doc's claim was written; extended it to also confirm `_verify_chain` itself detects the orphan, not just raw data inspection (F-64).
- **Artifact qualification / quote-timestamp enforcement**: resolved as a deliberate operator decision, not a bug — "remove all promotion qualification, it's up to me" (verbatim). Every sport's `timestamp_valid=false` handling was separately re-verified live and was already correct everywhere it applies.
- **Two "mismatched" artifact hashes** (`nba-spread-baseline-v1.json`, `nfl-spread-baseline-v1.json`): both hashes are actually correct against the codebase's real loader convention (`ensure_ascii=True`); the mismatch only existed against this file's own verification snippet, which used the wrong convention (`ensure_ascii=False`) — never fixed since the artifacts were never wrong.

---

## 🟠 P1 — Data Correctness and Routing

**Resolved 2026-08-04 (P1 cleanup session)** — all 15 open P1 items worked. 10 code fixes applied across 10 files, 1 confirmed already-resolved, 4 investigated with documented findings. 673 tests pass.

### ✅ Code fixes applied

| # | Item | Fix | File |
|---|---|---|---|
| P1-1 | Non-atomic exposure check | Added `lock_exclusive()` context manager so callers can hold the file lock across exposure read + append | `ledger.py` |
| P1-2 | API key leaked in error URLs | Moved `raise_for_status()` inside `_safe_get` so HTTP error strings also go through key redaction | `the_odds_api.py` |
| P1-3 | Polymarket discovery truncates | Added offset-based pagination loop (`limit=500`, paginate until exhausted); `timestamp_valid` was already computed dynamically (not hardcoded) | `polymarket_us.py` |
| P1-4 | Future timestamps pass freshness checks | Added explicit `parse_utc(observed) > current` rejection before age check in both `evaluate_eligibility` and `_light` | `eligibility.py` |
| P1-5 | Unvalidated rows poison dedup | Moved `existing.add(key)` from before writes to after successful writes | `ingest.py` |
| P1-6 | Silently swallowed exceptions | Added `logging.getLogger(__name__)` + `logger.debug`/`warning` calls to 5 silent except blocks | `esports.py`, `international_baseball.py` |
| P1-7 | Ban mechanism broken for registry-free sports | Added `_registry_free_check()` name-based fallback in `check()`; `_entries()` now loads registry-free entries using input string as ID | `bans.py` |
| P1-8 | CLI reports wrong model status | `models` command now calls `model_spec(league)` to pull config-derived status instead of iterating static `MODEL_SPECS` | `cli.py` |
| P1-14 | WNBA availability fails open | Changed `merge_availability_sources` default `conflict_policy` from `"most_conservative"` to `"fail_closed"` | `player_availability.py` |
| P1-16 | Stale `.bak` data files | Deleted all 22 `.bak-v*` and `.backup-before-*` files from `data/` | — |

### ✅ Already resolved (confirmed)

| # | Item | Verdict |
|---|---|---|
| P1-9 | `/api/scan` route broken | Route no longer exists in `dashboard_server.py` — already removed |

### 🔍 Investigated — needs follow-up

| # | Item | Finding |
|---|---|---|
| P1-11 | WNBA 78.3% total baseline | `wnba-backtest.json` has 0 dates examined (`insufficient_history`). `total_research_artifact` in `config/model.yaml` points to `wnba-spread-baseline-v1.json` — a **spread baseline misused as a totals model** |
| P1-12 | MLB ingest intermittently misses games | Intermittent ESPN API issue — hard to reproduce. Consider adding a missed-game detector comparing ESPN scoreboard IDs against ingested IDs |
| P1-13 | Validation report unreproducible | `learned-model-validation.json` from 2026-07-27 references stale worktree paths + old artifacts. → DONE 2026-08-15 via `model-prediction validate-models` (the `validate-learned` name was stale) |

**P1-17 (MLB totals over-picks "over") moved out of this table 2026-08-05**: refit attempted and promoted (F-62) per operator directive — real Poisson-GLM elasticity refit (`mlb-analyst-poisson-trend-v0.3`) shipped, margin/spread genuinely improved, but totals specifically did not improve (see F-62's full numbers). Closed as "worked, honestly did not fix it" rather than left as an open follow-up item — the next real step is a structurally different feature (`totals_specific_market_residual`/`branched_absolute_run_intensity_head`, already in `config/model.yaml`'s `problem_cohorts.totals`), not another elasticity pass. This correction itself fixes a real inconsistency: this table still listed P1-17 as open after Part 2's checklist had already marked it `[x]` done.

---

## 🔵 Deep Debug Audit (2026-08-04)

Comprehensive codebase audit covering error handling, dead code, file growth, config integrity, and security surface. Run against `HEAD 01634e2` + uncommitted P1 fixes.

### 🔴 Critical — fixed 2026-08-05

| # | Issue | Location | Impact | Resolution |
|---|---|---|---|---|
| DD-1 | **`suppress(Exception)` swallows everything** | `international_baseball.py:533` | Silently discards ALL exceptions including `KeyboardInterrupt` and `SystemExit` — if the data source fails, the daily pipeline reports success with zero games | **Fixed.** Narrowed to `(httpx.HTTPError, KeyError, TypeError, ValueError, OSError)`. New regression test confirms a real bug (e.g. an `AttributeError` from a broken client) now propagates instead of being silently swallowed. |
| DD-2 | **14 `suppress(DuplicatePickError)` silently drop real duplicates** | `cli.py` (14 occurrences across `_forecast_mlb_totals_flat`, `_forecast_learned_sport`, `_log_esports_forecast`, `_forecast_international_sport`, `_forecast_soccer_sport`, `_forecast_tennis_sport`) | A genuine duplicate pick (same event, market, line, model_version — different selection or same-day re-forecast with different probability) is silently discarded with no audit trail. Operator cannot distinguish "model chose not to pick" from "model picked but ledger already had this market" | **Fixed (F-66).** Shared `_append_secondary_ledger()` helper wired into all 14 sites (plus 4 structurally identical bare `except DuplicatePickError: continue` sites); every function's return dict now reports which secondary-ledger writes were duplicates, and of which existing pick. 6 new regression tests, one per function. |

### 🟠 Important — DD-3, DD-4, DD-6 fixed, DD-5 and DD-7 still open

| # | Issue | Location | Impact | Resolution |
|---|---|---|---|---|
| DD-3 | **`validation.py` bare `except: pass` blocks silently discard errors** | `validation.py:253` (`except ValueError: pass` on pitcher ERA gap), `validation.py:763,781` (`except OSError: pass` on odds file reads) | Validation report can silently report zero spread/total counts when files are corrupted — no distinction from genuinely empty data | **Fixed (F-67).** Added a module logger; all 5 sites now log the specific failing path/exception before degrading gracefully (behavior otherwise unchanged — the graceful degradation itself was already correct). 2 new regression tests inject a real `OSError` mid-read and confirm it's logged. |
| DD-4 | **9 orphaned modules (658 lines of dead code) — 5 deleted, 4 kept** | `confidence_gate.py`, `guaranteed_signal.py`, `market_signals.py`, `openligadb.py`, `football_data.py` **deleted** — zero production imports. `starting_pitcher.py`, `head_to_head.py`, `lineup_strength.py`, `tennis_surface.py` **kept** — real feature code worth wiring in later. `mlb_statsapi.py` (listed in P2-3) verified actually in use by `cli.py`, kept. Related dead tests (`test_confidence_gate.py`, `test_guaranteed_signal.py`, `test_feature_regressions.py`) also removed. Feature registry updated (27→24 entries). | **Fixed (F-68).** 5 dead modules + 3 dead test files removed; 4 useful-but-unwired modules preserved for future integration. Also: FIP pipeline built as a replacement for ERA in the MLB model — locked-holdout comparison shows FIP improves hit rate (+1pp), calibration ECE (-39%), and units (+11 per season). FIP's learned coefficient is 2x ERA's (-0.031 vs -0.017); when both are present, ERA shrinks to near-zero (-0.007). `_load_starter_fip_map()` and `_starter_fip_gap()` added to `validation.py`. |
| DD-5 | **`dashboard_server.py` at 5,121 lines (+339 since last audit)** | `dashboard_server.py` | Single monolithic file with ~15 POST routes + ~20 GET routes dispatched via manual `if/elif parsed.path` chain. Every new feature lands here |
| DD-6 | **`cli.py` at 4,264 lines (+321 since last audit), 48 subcommands** | `cli.py` | Largest module in the codebase, near-zero test coverage, all commands in one file | **Fixed 2026-08-19.** `cli.py` split into a `cli/` package (`parser.py`, `main.py`, `commands.py`, `forecast.py`, `daily.py`, `settle.py`, `state.py`, `__init__.py`, `__main__.py`) by domain responsibility — dispatch/parsing separated from command bodies, forecast/daily/settle pipelines each own module. `forecast.py` (2,081 lines) is still the largest piece and a candidate for further splitting later, but the monolith itself is gone. Full suite green (1910 passed, 3 skipped), ruff clean. |
| DD-7 | **Zero TODO/FIXME/HACK markers in entire codebase** | All source files | No visible technical debt tracking — problems found in code reviews or audits are only tracked externally (in MASTER.md, DEBUG.md, CHECKLIST.md), not in the code where they'd be visible to anyone reading it |

### 🟡 Lower — fix when convenient

| # | Issue | Location | Impact |
|---|---|---|---|
| DD-8 | **Multiple hardcoded thresholds scattered across code** | `units.py` (min_edge=0.02, unit_increment=0.25), `tennis.py` (uncertainty=0.05), `esports.py` (min_observations=50, min_accuracy=0.60), `roadmap_challenger.py` (p≤0.05) — `confidence_gate.py`/`guaranteed_signal.py` thresholds removed with DD-4 | Thresholds duplicated across modules with no single source of truth — changing a policy requires finding every copy | **Fixed (F-69).** Created `src/model_prediction/config.py` constants (`UNIT_MIN_EDGE`, `UNIT_INCREMENT`, `TENNIS_MODEL_UNCERTAINTY`, `ESPORTS_MIN_OBSERVATIONS`, `ESPORTS_MIN_ACCURACY`, `SIGNIFICANCE_THRESHOLD`) with identical numeric values. Every hardcoded threshold now annotated with a `# source of truth: config.X` comment pointing to the canonical location. Values unchanged — only the location added. 703 tests pass. |
| DD-9 | **No pre-commit ruff hook** | Project config | Lint violations accumulate between CI runs. 126 ruff findings exist (mostly EXE002 shebangs — cosmetic) |
| DD-10 | **126 ruff findings (baseline) — 79 are EXE002 shebang** | `tests/` directory | 79 test files have executable bits set without shebangs — harmless but noisy. Remaining 47 are style/formatting |

### 🔴 New — found 2026-08-05 deep debug

| # | Issue | Location | Impact |
|---|---|---|---|
| DD-11 | **FIP pipeline built and validated but not wired into live serving** | `learned_forward.py:98-117` | Live MLB forecasts still use `starter_era_gap` (ERA) despite locked-holdout proof that `starter_fip_gap` beats it: +1pp hit rate, -39% calibration ECE, +11 units/season. | **Fixed (F-70).** Imported `starter_fip_gap_live` into `learned_forward.py`; added `starter_fip_gap` feature computation alongside `starter_era_gap`. Both features now available — v8 artifacts use ERA, v9+ artifacts use FIP. |
| DD-12 | **starter_era_gap NO_CALL silently defaults to 0.0 without operator-visible warning** | `learned_forward.py:105-107` | When either starter lacks sufficient history, the gap silently becomes 0.0 — the model still produces a pick with a fabricated feature value. | **Fixed (F-71).** Added `logger.warning(...)` with the event_id and exception when `starter_era_gap` falls back to 0.0, so the daily pipeline log now records every occurrence. The 0.0 fallback behavior itself is unchanged (matches training contract). |
| DD-13 | **Git push failing — LFS upload timeout** | Background push task | Both LFS-tracked files (`data/events.jsonl` 85MB, `data/mlb_statsapi/game_snapshots.jsonl` 61MB) fail to upload: `read tcp ... i/o timeout` from GitHub's S3. The LFS objects push but the connection drops during upload. All 7 local commits are ahead of origin |
| DD-14 | **Flat Ledger picks sometimes missing model_version** | Flat ledger rows, dashboard display | Some flat ledger picks are created without `model_version` populated (e.g. manually-created picks, legacy rows). Fixed the dashboard display to show "unknown" as fallback (commit `453a01d`), but the root cause — some pick creation paths not setting model_version — remains uninvestigated |

### ✅ Verified good

- **675 tests pass, 0 fail** — all P1 fixes + deep debug changes verified
- **Audit chain intact** — `verify-chain` reports 0 breaks, `chain_intact: true`
- **All 46 config/model artifact hashes valid** — no mismatches
- **All config artifacts resolve** — no missing file references
- **Model ledger dual-write has fail-soft pattern** — primary write never blocked by model ledger failure
- **No secrets exposed** — `THE_ODDS_API_KEY` and Polymarket credentials properly handled
- **All XLSX ledgers load cleanly** — no corruption detected
- **All critical imports pass** — 0 import errors

---

## 🟡 P2 — Architecture and Maintainability


### P2-1: `cli.py` — 3,943 lines, 8.3% coverage, zero dedicated tests — **stale, see DD-6**
**File**: `src/model_prediction/cli.py`
**What's wrong**: The largest file in the repo has near-zero behavioral test coverage. There is no `tests/test_cli.py` (DEBUG.md §2713 lists it but that reference is aspirational — the file doesn't exist). argparse wiring, default-date logic (`eastern_today()`), command dispatch, and all 25+ subcommands are only exercised indirectly by whatever other tests happen to call CLI functions.
**Superseded 2026-08-19**: `cli.py` no longer exists as a monolith — split into the `cli/` package per DD-6's resolution above. `tests/test_cli.py` also already existed by 2026-08-03 (noted in the Verification note above), so this entry's "zero dedicated tests" claim was stale even before the split. Left in place rather than deleted, per this file's own convention of flagging stale claims inline instead of silently removing them.
**Source**: ENGINEERING_ROADMAP.md §3, DEBUG.md §2728-2744

### P2-2: `dashboard_server.py` — 4,782 lines monolithic
**File**: `dashboard_server.py`
**What's wrong**: Grew from 2,978 to 4,782 lines (+60%) since the July review. Every new feature — token auth, SELL P&L fix, portfolio history, multi-ledger scan, order readiness, market question caching — landed in this same file. Manual if/elif routing for ~20 GET + 8 POST routes. The recommended split (`dashboard/routes.py`, `views.py`, `orders.py`) is more urgent than ever.
**Source**: ENGINEERING_ROADMAP.md §3

### P2-3: 12 orphaned modules (~1,800 lines of dead code)
Never imported, never tested, dead code creating false signal:

| Module | Location |
|---|---|
| `soccer_form.py` | `features/` — docstring described by `models/soccer.py` but never imported |
| `lineup_strength.py` | `features/` — rejected feature, code left behind |
| `starting_pitcher.py` | `features/` — MLB rank-1 feature stub, never wired |
| `tennis_surface.py` | `features/` — excluded feature, code left behind |
| `guaranteed_signal.py` | `features/` — excluded (post-hoc tag, not input) |
| `rest_travel.py` | `features/` — dead code |
| `head_to_head.py` | `features/` — rejected feature, code left behind |
| `market_signals.py` | `features/` — excluded (violates market isolation) |
| `pitchers.py` | `features/` — dead code, carries ruff E741 error |
| `openligadb.py` | `data_sources/` — dead data source |
| `mlb_statsapi.py` | `data_sources/` — not imported by any src module |
| `football_data.py` | `data_sources/` — not imported by any src module |

**Source**: ENGINEERING_ROADMAP.md §2

### P2-4: Dashboard uses `pkill -f`
**What**: Both the manual startup path and CHECKLIST.md reference `pkill -f` for process management. `.codewhale/instructions.md` explicitly forbids this.
**Source**: CHECKLIST.md, ENGINEERING_ROADMAP.md

### P2-5: Dead `SportModel` protocol + unwired model registry
**File**: `models/registry.py`
**What's wrong**: An abstraction layer that nothing uses. The protocol is non-conformant with the actual model implementations, and the registry reports static hardcoded statuses rather than config-derived state.
**Source**: TODO.md P2

### P2-6: 4 dashboard tests need pinning
**File**: `tests/test_dashboard_server.py`
**What's wrong**: Order-preview tests use unit values that don't match the current `$5.00`-per-unit cap. Tests need to either pin the intended unit value or use sizes within the current cap.
**Source**: TODO.md P1

### P2-7: Additional low-coverage modules
**Files**: `mlb_statsapi.py`, `odds_soccer_scores.py`, `openligadb.py`, `wnba_availability_evaluation.py`
**What**: These modules have near-zero line execution coverage and no dedicated behavioral tests. Their correctness is untested.
**Source**: DEBUG.md §2740-2744

### P2-8: Coverage ≠ correctness
**What**: Line execution coverage is not proof of behavioral correctness. Transaction failure, timestamp validity, conflict handling, and secret-redaction invariants still lack direct behavioral tests even in higher-coverage modules (e.g., `ledger.py` at 89.2%, `audit.py` at 93.5%).
**Source**: DEBUG.md §2742-2744

### P2-9: dirty working tree (resolved 2026-08-04, later; ongoing daily-pipeline data drift is normal, not this item)
**What was true**: everything accumulated across the day's sessions (F-47 through F-56 fixes, P1 cleanup, daily pipeline data) sat uncommitted through most of 2026-08-04.
**Resolved**: committed and pushed in two commits (`face73f`, `31d3b7c`), HEAD is now current on `origin/main`. Remaining working-tree changes at any given moment are normal daily-pipeline data drift (ledgers, odds snapshots, availability captures), tracked in this repo by design — not the same issue this item originally described.
**Source**: Git status 2026-08-04

---

## ✅ All Fixed Bugs (historical reference — 26 entries)

### Real-money path (critical)
| # | Bug | Fixed | Real impact |
|---|---|---|---|
| F-1 | **SELL orders skipped quote freshness and game-start checks** — BUY went through `_order_readiness` (5-min quote freshness, market open, game not started); SELL checked only `bid is not None`. An intentional design choice ("you can always try to close") turned out to have an unconsidered consequence: `_pick_quote` permanently excludes snapshots at/after `event_start_utc`, so post-game-start SELL uses a frozen pregame quote forever, regardless of how stale | 2026-08-02 | Every SELL order |
| F-2 | **Dashboard no auth on order execution** — `POST /api/order/submit` had Origin/Host CSRF check only + client-supplied `confirm:true` flag (not a credential). Any local process could curl the API directly. Fixed with per-session server-generated token, injected into served page | 2026-08-02 | Real-money safety |
| F-3 | **SELL-path P&L formula** — BUY and SELL used different settlement logic. Fixed with single canonical `_settle_pnl` function, algebraically verified | 2026-08-02 | SELL settlement P&L |

### Sizing and units
| # | Bug | Fixed | Real impact |
|---|---|---|---|
| F-4 | **Unit sizing dead parameter** — `model_uncertainty` accepted at 6 call sites in `edge_scaled_units` but never read. Two picks with identical `model_probability` always got identically-sized stakes (1.5U-2.0U) regardless of whether uncertainty was 0.01 or 0.49. Every existing test checked "does this produce a plausible number" never "does changing uncertainty change output" | 2026-07-31 | Every real pick for unknown months |
| F-5 | **Unit range widened** from 0.5U-2.0U to 1.0U-2.0U per operator directive | 2026-07-31 | All sizing |
| F-6 | **30-pick freeze gate active** — `parameter_freezes_allowed: false` was silently capping iteration | 2026-07-23 | All model iteration |

### Data integrity (silent corruption)
| # | Bug | Fixed | Real impact |
|---|---|---|---|
| F-7 | **NPB destructive overwrite** — `international_baseball.py` overwrote all historical NPB data on each forecast run instead of appending. Every past game was lost every day | 2026-08-01 | All NPB history |
| F-8 | **Dota2 and Valorant swapped discipline IDs** — each model trained on the other game's match history. The bo3.gg API discipline ID mapping was wrong | 2026-07-27 | Both titles, every forecast |
| F-9 | **Tennis zero match history** — `FeatureStore`/`GameRecord` shape incompatibility meant `games_before()` silently returned zero rows for every query. Every tennis pick showed exactly 50%. All 1,878 real cached files were valid — the parser was the bug | 2026-07-27 | Every tennis pick ever |
| F-10 | **KBO/NPB timestamp-ordering bug** — `utc_now()` captured before a slow live-data-building call, then a second `utc_now()` captured for `validate(now=)`. The first timestamp was earlier than the second, so every pick's `observed_at_utc` was before the `now` cutoff — silently zeroing every real pick with no error surfaced anywhere | 2026-07-28 | Every KBO/NPB pick, for months |
| F-11 | **KBO/NPB home/away labels guessed from raw array position** — `international_baseball.py` resolved `home_id`/`away_id` correctly via Polymarket side tags for the probability math, then DISCARDED that and guessed `home_team = teams[1]`, `away_team = teams[0]` from raw array order (which has no ordering guarantee). If the gateway ever lists home-first, ledger labels silently swap — settlement matches on labels, so it would settle the wrong side | 2026-07-28 | KBO/NPB ledger rows |
| F-12 | **KBO/NPB silent market skip** — a market with the wrong number of sides was silently `continue`d past with no recorded reason. Fixed by appending `NO_CALL_MARKET_SIDES_INVALID` before skipping | 2026-07-27 | KBO/NPB logging |
| F-13 | **Soccer team-name collision** — `_GENERIC_TEAM_WORDS` filter stripped "City" and "United" from team names, so "Manchester United" and "Manchester City" both resolved to "Manchester" and could match the wrong team's Polymarket contract. Fixed by removing non-corporate words from the filter AND adding an opponent cross-check: refuse rather than guess when ambiguous | 2026-07-28 | Soccer pricing |
| F-14 | **Weather park-factor key collision** — the A's temporarily sharing a park with the River Cats created identical `(league, team_input)` keys. `"Athletics_home_park"` resolved to the River Cats' indoor stadium → `weather_run_factor=1.0`, losing real weather signal for the team now playing outdoors in Sacramento | 2026-07-31 | One MLB team's weather |
| F-15 | **Soccer draws treated as away wins** in head-to-head features — `head_to_head.py` coded draw as away_win | 2026-07-28 | Soccer H2H |
| F-16 | **MLB weather payload shape/wind contribution/event-hour selection** all wrong in `features/weather.py` | 2026-07-28 | MLB weather feature |
| F-17 | **Tennis stale cache false positive** — `ingest.py` used the wrong parser for tennis cache staleness checks, causing unnecessary refetches of all 1,878 files. Fixed with sport-aware parser | 2026-07-28 | Tennis ingest |

### Feature and model correctness
| # | Bug | Fixed | Real impact |
|---|---|---|---|
| F-18 | **Esports confidence gate no-op** — threshold selection picked whichever gate had the most observations, which always resolved to the loosest threshold (0.0). Never actually gated anything. Fixed to select by `units_at_minus_110` on validation | 2026-07-20 | All esports gating |
| F-19 | **Esports v4 K overfitting** — K=96 sat at the exact top of its search grid for 4 of 5 titles (a truncated-search/overfitting signal). v5 rebuild: K chosen by min Brier (pure calibration), threshold by `units_at_minus_110` (genuine volume-vs-quality interior optimum) | 2026-07-31 | Esports model quality |
| F-20 | **Gated Research performing worse than unfiltered Research** — `research_confidence_gate` was 0.0 for every esports title, barely filtering anything. Real settled Gated picks were below unfiltered in every title (e.g., LOL 46.4% gated vs 54.2% research). Fixed: raised gates to artifact-validated thresholds (0.03-0.05) | 2026-07-31 | Esports gating |
| F-21 | **MLB rehab-assignment marker missing** — 291 real "rehab assignment" transactions silently skipped in availability feature. Player still recovering, not activated — but marked neither available nor unavailable. Fixed by adding to `UNAVAILABLE_TRANSACTION_MARKERS` | 2026-08-02 | MLB availability |
| F-22 | **MLB same-day transaction ambiguity** — Stats API `date` field has no time-of-day. A transaction on the same calendar day as the decision could be before or after — ambiguous. Fixed with strict `<` (exclude same-day) rather than `<=` (assume safe) | 2026-08-02 | MLB availability PIT |
| F-23 | **Roster snapshots captured but never read** — `cli.py` called `capture_roster_snapshot` daily, but `features/mlb_player_availability.py` only consulted transaction history. The roster snapshot's direct, current-status read was dead weight | 2026-08-02 | MLB availability |
| F-24 | **MLB Measured Edge frozen config missing keys** — `factor_bounds`, `uncertainty`, and `simulation` blocks absent from `mlb-analyst-poisson-trend-v0.2.yaml`; file couldn't load at all | 2026-07-27 | MLB totals/spread |
| F-25 | **Soccer moneyline silently dropped** — `MARKET_TYPES` in `polymarket_us.py` didn't recognize `SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME`. Soccer's three-way `team_win` markets were invisible to the system | 2026-07-27 | Soccer moneyline |
| F-26 | **Esports no auto-refresh** — ratings only updated via full-file-overwrite manual backfill, not auto-refreshed before each forecast. Fixed: `esports.py::refresh_recent_matches` called inside `daily` | 2026-07-27 | Esports daily |

### Infrastructure
| # | Bug | Fixed | Real impact |
|---|---|---|---|
| F-27 | **CLV scanning only Main** — `settle()` technically accepted closing-price args for all ledgers, but only Main was ever scanned. Flat/Research/Gated never got closing prices → no CLV for non-Main rows | 2026-07-31 | All non-Main CLV |
| F-28 | **Audit hash serialization** — event hashes written with non-compact JSON, couldn't verify chain from JSONL alone | 2026-07-17 | Audit chain |
| F-29 | **Empty `observed_at_utc=""` crashed `parse_utc()`** — `.strip()` guard added | 2026-07-17 | Eligibility |
| F-30 | **Config drift** — `maximum_data_age_hours` and `maximum_unreviewed_disagreement` never flowed from config into the actual forecast path | 2026-07-17 | Data freshness |
| F-31 | **Console entry point broken** — `.venv/bin/model-prediction` raised `ModuleNotFoundError` | 2026-07-23 | CLI usability |
| F-32 | **Legacy mixed Research/Gated workbooks** — one monolithic file per category, no per-sport isolation | 2026-07-28 | Research integrity |
| F-33 | **Economic bootstrap-CI gate** — passed intervals spanning zero as positive-ROI evidence | 2026-07-31 | Validation gating |

### Settlement and ledger
| # | Bug | Fixed |
|---|---|---|
| F-34 | MLB confidence gate removed per operator directive — every forecasted game now a real, sized Main-ledger call | 2026-07-31 |
| F-35 | MLB min-edge-vs-market gate removed per operator directive | 2026-07-31 |
| F-36 | Soccer promoted to Main+Flat by operator override | 2026-08-02 |
| F-37 | Soccer flat/Main-ledger pairing fixed — soccer writes real rows to Flat, correctly paired | 2026-08-01 |
| F-38 | Archive settled rows — new `archive_settled_rows` function, audited removal; never raw deletion | 2026-07-31 |

### Added 2026-08-02 (later) — not in this file's original 38-entry list
| # | Bug/change | Fixed | Real impact |
|---|---|---|---|
| F-39 | **`_reconcile_orders` read-modify-write race** — ran without holding `_ORDER_LOCK`, unlike every other orders.json mutation; called on essentially every `/api/picks` request, so it could interleave with a real order submission and silently erase the just-submitted order record | 2026-08-02 | Order record integrity |
| F-40 | **Dashboard had no authentication on order execution** — Origin/Host CSRF check + client-supplied `confirm:true` only; any local process could curl the API directly and place a real order. Fixed with a per-process bearer token, auto-injected into the served page | 2026-08-02 | Real-money safety (also listed as F-2 above; consolidated) |
| F-41 | **NPB destructive-overwrite fallback** — `find_international_baseball_result`'s cache-miss fallback called the full-overwrite `backfill_international_baseball` instead of merging; had already collapsed real NPB history from 3,936 games to 566 before being caught. Restored with zero data loss; fixed to merge by `game_id`, matching the safe path already used elsewhere | 2026-08-02 | All NPB history (also listed as F-7 above with an earlier date; this is the same bug's actual fix date) |
| F-42 | **`daily` crashed entirely on a transient Polymarket slate-capture failure** — `f0.result()` was unhandled; a network blip during the BBO/event snapshot capture (which nothing downstream actually depends on — every sport fetches its own market data independently) took down forecasting for every sport that day, not just the capture step | 2026-08-02 | Daily pipeline resilience |
| F-43 | **`validation.py`/`market_residual.py` artifact writers had no overwrite guard** — a stale/unbumped version constant could silently overwrite a kept rollback artifact or the live production artifact in place. Added a hard `FileExistsError` guard to both | 2026-08-02 | Artifact/rollback integrity |
| F-44 | **`rationale`/`risks` never exposed anywhere in the dashboard** — not in `_parse_picks`'s field list, not in the pick-detail drawer, for any ledger view, ever. Fixed (backend + frontend) | 2026-08-02 | Dashboard usability |
| F-45 | **Soccer's flat/gated/main ledgers weren't cleared symmetrically on `flat-forecast`** — only `flat_ledger` got cleared before re-forecasting; research/gated/main didn't, so a second same-day `flat-forecast` run duplicated every soccer row in those three | 2026-08-02 | Soccer ledger row counts |
| F-46 | **`ModelLedger` dedupe key was silently broken** — compared a raw pre-write value (`line=None`) against a value already read back from the file (`line=""`), and separately compared a `sportsbook` field the new schema doesn't even have against the `.get()` default for a missing key. Both permanently mismatched, so the same real decision logged to more than one old destination created a duplicate row instead of being deduped. Found via direct reproduction before it reached real data; fixed | 2026-08-02 | New model-ledger data integrity |

### Added 2026-08-04 (not yet committed — sitting in working tree)
| # | Bug | Fixed | Real impact |
|---|---|---|---|
| F-47 | **Dual-ledger (soccer/tennis) duplicate-row gap incomplete** — the 2026-08-03 fix (F-45's successor) added a special case in `cli.py`'s `forecast`/`log`/`flat-forecast` dispatch to clear soccer's main ledger before a second same-day `flat-forecast` run, since soccer writes both `main_ledger` and `flat_ledger` unconditionally regardless of which command ran. Two gaps survived that fix: (1) tennis has the identical unconditional-both-ledgers behavior (`_forecast_tennis_sport`'s docstring confirms it) but was never added to the special case, so a second same-day `flat-forecast --sport tennis --log` would duplicate tennis's Main rows the same way soccer's did before F-45/this session's fix. (2) The reverse direction was never covered for *either* sport — a non-flat `forecast --sport soccer --log` (or tennis) run writes `flat_ledger` unconditionally too, but nothing ever cleared Flat in the non-`is_flat` branch, so a second same-day non-flat run would duplicate Flat rows. Fixed by replacing the soccer-only special case with a `DUAL_LEDGER_SPORTS = frozenset({"soccer", "tennis"})` constant applied symmetrically in both directions. New parametrized regression test (`test_replace_today_clears_the_ledger_the_other_command_variant_writes`, `tests/test_cli.py`) covers both sports × both directions; verified it fails without the fix (reverted fix, confirmed 2/2 failures, restored). The `daily` command (the actual production cron path) was never affected — it always clears unscoped since it regenerates every sport every run; this only mattered for manual single-sport re-runs. | 2026-08-04 | Manual re-runs of soccer/tennis forecast commands, same-day |
| F-48 | **`bullpen_weakness_gap`/`defensive_trend_gap` silently missing from the audit ledger** — same bug class as `domain.py`'s own documented pitcher_era_gap incident (silently blank on every real pick until 2026-07-25), recurring for two newer features that never got the same fix. `bullpen_weakness_gap` is an active MLB v7 moneyline coefficient (`features/bullpen.py`, added when v7 shipped 2026-07-30); `defensive_trend_gap` is an active NBA/WNBA v4 moneyline coefficient. Both were correctly computed in `learned_forward.py::_compute_features`, correctly included in `candidate.feature_basis`, and correctly consumed by `artifact.probability(...)` for real scoring — model predictions were never affected. But `PickRequest.as_dict()` (`domain.py`) never listed either field, so neither one was ever written to its already-reserved ledger column (`defensive_trend_gap` had a `ledger.py` `LEDGER_SCHEMA` column since 2026-07-22 that just never got populated; `bullpen_weakness_gap` had no column at all until this fix). Verified against real production data: 63/63 real MLB v7 moneyline flat-ledger rows had `bullpen_weakness_gap` and `defensive_trend_gap` blank pre-fix. Cross-checked correctness by hand-recomputing MLB v7's logistic-regression probability from raw artifact coefficients against real logged picks (approximating the then-unlogged `bullpen_weakness_gap` as 0 for rows predating the fix) — residual discrepancies (≤2.1%) were fully explained by that approximation, confirming the real scoring path used the correct (just unlogged) value all along. Fixed: added `bullpen_weakness_gap` to `PickRequest` (`domain.py`) and `LEDGER_SCHEMA` (`ledger.py`), added both fields to `PickRequest.as_dict()`, wired `bullpen_weakness_gap=candidate.feature_basis.get("bullpen_weakness_gap")` into `cli.py`'s `PickRequest(...)` construction (the `defensive_trend_gap` wiring already existed there — only its `as_dict()` serialization was missing). New parametrized test (`test_as_dict_serializes_every_diagnostic_feature_field`, `tests/test_domain.py`) covers all 9 diagnostic feature fields so a future addition that misses `as_dict()` fails loudly; verified it fails without the fix. **Side effect**: adding the new `bullpen_weakness_gap` ledger column triggered the project's own `_migrate_if_needed` auto-migration on every Main/Flat xlsx file the moment it was read (new blank column, `.bak-v3`/`.bak-v1` backup, audited `ledger_migrated` event) — verified safe (identical row counts/pick_ids, zero field-value drift, `verify-chain` 0 breaks) before relying on it. | 2026-08-04 | MLB v7 and NBA/WNBA v4 audit-trail completeness (not prediction correctness) |

### Moved out of the open P0/P1 lists, 2026-08-04 cleanup — real fixes or resolved ambiguity
| # | Bug | Fixed | Real impact |
|---|---|---|---|
| F-49 | **Real-money execution tickets not bound to exact ledger row for spread/total/btts** (moneyline was already bound) — added live spread/total/btts side/line resolvers to `_verify_live_side_and_timing`, refuse-on-mismatch, fail-closed on unrecognized `market_type` instead of silently skipping the check | 2026-08-03 | Real money at risk on non-moneyline order execution |
| F-50 | `config/model.yaml` referenced a nonexistent `market-residual-v1.json` — trained the real artifact (honest `identity_fallback: true`, 51 real samples below the 100-sample minimum) and wired a real, diagnostic-only consumer (`PickRequest.market_residual_probability`) that never feeds sizing | 2026-08-03 | Dead config cleaned; new diagnostic evidence surfaced, sizing unchanged |
| F-51 | MLB `spread_research_artifact`/`total_research_artifact` pointed at the wrong (unrelated, generic cross-sport) baseline file instead of MLB's own real, live Measured Edge artifacts (`measured-edge-margin-v2.json`/`measured-edge-totals-v2.json`, already referenced correctly elsewhere) | 2026-08-03 | Dead config cleaned |
| F-52 | **KBO/NPB half-settlement P&L "incorrect" claim (former P1-15) — confirmed already correct, not a bug.** Direct code read of `ledger.py`'s `settle()`: `pnl = units * (binary_contract_settlement_value / entry_probability - 1)` is the real, correct economics for a tie settling at $0.50 instead of $0/$1, with guard rails (`0 <= value <= 1`, moneyline-only, only valid for a `PUSH` result) — not the naive push-to-zero math the original claim described. The doc's own prior note flagged this as an unresolved contradiction between TODO.md (fixed) and DEBUG.md (open); this resolves it in TODO.md's favor | 2026-08-04 | None — confirms existing KBO/NPB tie settlement was already correct |

### Added 2026-08-04 (later) — dashboard order-readiness + MLB starter identity
| # | Bug | Fixed | Real impact |
|---|---|---|---|
| F-53 | **Dashboard `_pick_quote` (`dashboard_server.py`) was moneyline-only** — returned `None` unconditionally for any spread/total row, so `_order_readiness` always reported "no exact executable Polymarket US market mapping" for every real MLB spread/total pick, even when the exact live Polymarket market genuinely existed (verified live: a real -1.5 spread and a real 8.5 total both existed for a real logged Main pick showing this exact error). This made P0-1's spread/total/btts execution-time re-verification (`_verify_live_side_and_timing`, `polymarket_execute.py`) unreachable from the real dashboard order flow — the earlier, ticket-building stage always refused first, so that P0-1 work never actually protected a real spread/total order despite being tested at the function level. Fixed: extended `_pick_quote` to resolve spread (team+signed-line, duplicated from `polymarket_execute.py::_resolve_spread_side`'s exact convention) and total (over/under description+line) sides, generalized across every sport using the same snapshot shape (soccer's real total snapshots confirmed identical shape live). Caught and fixed a second bug while building the first: the initial spread/total matching had no game-identity check, so it matched across *unrelated* games sharing the same line (e.g. every day's "-1.5" spread market satisfied a completely different game's line-negation check) — added `_row_matches_snapshot_event` (both team names must appear in the snapshot's own `event_title`) to close it. 3 new regression tests in `tests/test_dashboard_server.py`, including one that specifically proves the cross-game collision is prevented; verified all three fail without their respective fixes. | 2026-08-04 | Every real MLB spread/total order attempt in the dashboard, previously always refused |
| F-54 | **MLB moneyline gated on confirmed starters + `starter_era_gap` promoted to v8** (operator-directed, not a bug fix). Two related changes: (1) `build_learned_moneyline_slate` (`learned_forward.py`) now skips an MLB event with `"unresolved probable starter"` if either team's ESPN probable pitcher isn't announced yet — consistency/caution measure matching what Measured Edge's `reconstructed_features` already enforces for spread/total; v7's own coefficients never needed starter identity, so this is purely a caution gate, not a correctness fix. (2) A real walk-forward test (`build_walk_forward_rows` + `chronological_split`, self-consistency-verified by reproducing v7's exact stored holdout numbers first) found that *replacing* `pitcher_era_gap` (team-level rolling runs-allowed) with `starter_era_gap` (real per-starter rolling ERA) shows the same validation/holdout disagreement the additive form showed on 2026-08-02 (validation Brier worse: 0.24655→0.24702; locked-holdout better: 0.24763→0.24635, 58.5%→60.8% hit rate). Built the missing live infrastructure `starter_era_gap` never had (`features/starter_history.py`: per-starter rolling ERA from `mlb_statsapi` boxscore history, mirroring `features/bullpen.py`'s design exactly; a new daily capture step, `cli.py::_capture_mlb_starter_snapshots`, keeps the underlying snapshot file current — it was a one-time static dump last refreshed 2026-07-20 with zero live-update path, the same silent-staleness bug class as the NPB destructive-overwrite incident, caught before it ever served live). Live wiring defaults to a neutral 0.0 + unavailable-features note when a starter's history is too thin, exactly matching `validation.py`'s own training-time fallback (never fails the whole game closed, which would have silently diverged live behavior from what was actually validated). Promoted `mlb-elo-trend-lr-v8.json` by explicit operator directive despite the validation regression — `qualified: false` in the artifact itself, honestly documented (locked-holdout alone clears the 60%/50-call bar; validation Brier does not), matching this project's existing pattern for v7 and soccer's own override history. Verified live end-to-end (dry run, no `--log`): 13/15 MLB games priced with real non-zero `starter_era_gap` values (e.g. -1.79, -2.20) computed from real `mlb_statsapi` history, correct artifact hash, 2 games correctly skipped for unresolved starters. 10 new tests (`tests/test_starter_history.py` + `tests/test_learned_forward.py` additions); full suite green. | 2026-08-04 | MLB moneyline coverage (fewer early-day games priced until starters announce) and real position sizing (new feature set, new threshold) |

### Added 2026-08-04 (even later) — found by actually running the real daily pipeline with --log
| # | Bug | Fixed | Real impact |
|---|---|---|---|
| F-55 | **`starter_era_gap` (v8's own new feature, F-54 above) recurred the exact F-48/F-53-class audit-trail bug within hours of shipping.** Caught by running the real `daily` pipeline live (operator instruction: "run daily forecast and make sure the model is working") and checking the actual logged ledger row, not just a dry run — the first real v8-logged MLB pick showed `starter_era_gap: None` despite the model scoring correctly with a real value. Root cause identical to F-48: `PickRequest`/`as_dict()`/`LEDGER_SCHEMA`/the `cli.py` construction were never updated when the feature was built, only `bullpen_weakness_gap`/`defensive_trend_gap` had been (the fix I'd made hours earlier). Fixed the same way: added the field to `domain.py`'s `PickRequest` + `as_dict()`, `ledger.py`'s `LEDGER_SCHEMA`, and `cli.py`'s `PickRequest(...)` construction. Extended the existing parametrized regression test (`test_as_dict_serializes_every_diagnostic_feature_field`) to cover it, closing the pattern for real this time — that test now fails loudly for any future diagnostic field that repeats this exact mistake. Model probability was never affected (same reason as F-48/F-53: `artifact.probability()` scores off the full features dict directly). | 2026-08-04 | MLB v8 audit-trail completeness (not prediction correctness) |
| F-56 | **`the_odds_api.py::_safe_get`'s API-key redaction (a concurrent P1-2 fix) crashed on every real HTTP error instead of redacting it** — `raise type(exc)(msg) from None` tries to reconstruct the *exact* original exception class with only the redacted message as a positional arg; works for a plain transport error, but `httpx.HTTPStatusError.__init__` requires `request`/`response` as additional keyword-only args, so reconstructing it this way raised its own `TypeError` ("missing 2 required keyword-only arguments") instead of the intended redacted error. Found in the first real live `daily` run after the redaction fix shipped — every one of soccer's 12 Odds-API-sourced leagues failed with this crash instead of a clean, redacted error message. Every real caller (`mlb_market_odds.py`, `cli.py`) catches the `httpx.HTTPError` base class, not a specific subtype, and `httpx.HTTPError` itself only requires a message — fixed by raising that directly instead of trying to preserve the exact original exception type. 2 new tests in `tests/test_the_odds_api.py` (HTTP status error + transport error, both confirming redaction *and* no crash); verified the HTTP-status-error test fails with the exact production `TypeError` when reverted. **Separate, real, NOT fixed**: even with the crash gone, all 12 configured Odds-API soccer leagues now cleanly report `401 Unauthorized` — the API key itself appears genuinely invalid/expired (external credential issue, verified live against the real API; not something a code fix can address). | 2026-08-04 | Soccer score collection for 12 leagues was crashing outright, not just returning stale/missing data; the crash is fixed, the 401s are a separate open issue |
| F-57 | **MLB v8's `confidence_threshold` (target_hit_rate=0.65) was too selective in practice — zero real MLB moneyline picks reached Main on its first live day** (operator-directed threshold fix, not a bug). Confirmed via a real live daily run: 0/13 games cleared 0.619665; all 13 correctly still reached Flat (no gate there), but Main — which requires clearing this separate confidence gate on top of `evaluate_eligibility`'s trust checks — got nothing. Re-learned the threshold at `target_hit_rate=0.60` (`validation.py`'s own existing `DIAGNOSTIC_THRESHOLD_TARGET_HIT_RATE` constant, not an arbitrary number) via the same `learn_confidence_threshold` methodology already used to build the artifact: new threshold 0.587335, roughly doubles validation selectivity (12.9%→25.2%) and holdout volume (148→352 calls), holdout hit rate stays real and positive (58.5%, well above the 50% coin-flip line) with more total units (+41.3u vs +23.8u) from the added volume. Does **not** additionally clear the 60% qualification bar at this looser threshold — `qualified` stays `false` for two honestly-listed reasons now (the pre-existing validation Brier regression, plus this new holdout-hit-rate shortfall at the lower bar) rather than one. Updated `tests/test_config.py`'s pinning test to assert the new, real double-shortfall state. Verified live: re-ran the real forecast against the new artifact and confirmed 5/13 candidates now clear the lowered threshold (up from 0/13), then confirmed via a real `daily --log` run that these reach Main. `docs/PROJECT_STATUS.md` rewritten from a stale 2026-08-02 snapshot (still referenced v7, single-file ledgers) to current, accurate state. | 2026-08-04 | MLB Main-ledger real position coverage (was 0 real calls/day at the old threshold, now real coverage restored) |

### Added 2026-08-04 (final) — systematic audit of every remaining deepseek-touched P1 file
| # | Bug | Fixed | Real impact |
|---|---|---|---|
| F-58 | **`bans.py`'s registry-free-league fallback was completely broken, in two separate ways.** `_registry_free_check()` (the P1-7 fix making team bans work for esports/soccer/tennis/KBO/NPB, which have no `EntityRegistry` entry) referenced `entities.PlaceholderTeam` — a class that has never existed in this codebase (`entities.py` only defines `CanonicalTeam`/`SourceTeamAlias`/`EntityRegistry`); the moment any registry-free league's team was ever actually checked against the ban list, this would raise `ImportError` immediately, before the P1-7 fix it belongs to could do anything. Fixed by using `CanonicalTeam` directly, matching the exact placeholder pattern `eligibility.py::evaluate_esports_eligibility` already established. Separately, and never touched by that same fix: `_mutate()` (backing `add()`/`remove()`) had three of its own unguarded `self.registry.resolve()` calls (main resolve, existing-entry comparison loop, removal filter) with no registry-free fallback at all — banning or unbanning a registry-free team through the real API would have raised `EntityResolutionError` directly, a second, independent way the same feature never worked end-to-end. Fixed with a shared `_resolve_or_placeholder()` helper applied at all three sites. New test (`test_add_check_remove_work_for_a_registry_free_league`, `tests/test_bans.py`) exercises the full real add→check→list→remove→check cycle for a registry-free league end to end; verified it fails with the exact real `ImportError`/`EntityResolutionError` traceback when reverted. | 2026-08-04 | Team-ban feature for esports/soccer/tennis/KBO/NPB was completely non-functional (both directions) since the P1-7 fix shipped |
| F-59 | **`features/player_availability.py`'s conflict-fail-closed fix (P1: default `conflict_policy` changed `most_conservative`→`fail_closed`) was silently defeated by a pre-existing exception-handling wrapper at its one real call site.** `matchup_player_availability` wraps both the ESPN-snapshot lookup and the merge call in a single `with suppress(ValueError, KeyError, TypeError, OSError):` — written, correctly, to make "ESPN hasn't posted an event snapshot yet" (`_latest_espn_snapshot` raising `NO_CALL_AVAILABILITY_ESPN_UNAVAILABLE`) a silent, expected fall-back to official-only data. The P1 fix gave `merge_availability_sources` a new reason to raise `ValueError` too — `NO_CALL_AVAILABILITY_SOURCE_CONFLICT`, when official and ESPN explicitly disagree — but that exception landed inside the exact same `suppress()` block, so it was caught by the same handler meant for a completely different, benign case. Net effect: a real conflict (e.g. ESPN reports a star player Out due to injury while the official report still says Available) was silently discarded with `availability_source_conflict_count: 0` and the player scored as 100% available — the opposite of "fail closed," and worse than the pre-fix `most_conservative` behavior it replaced (which at least recorded the conflict and picked the lower probability). Reproduced live before fixing: a fabricated real conflict produced `conflict count: 0`, `home_available_minutes_share: 1.0`. Fixed by separating the two calls — only `_latest_espn_snapshot` stays inside `suppress()`; `merge_availability_sources` is called outside it, so its conflict `ValueError` now genuinely propagates up to `learned_forward.py`'s existing `except (KeyError, TypeError, ValueError)` handler, which is the real fail-closed/neutral-feature path this was always supposed to reach. New end-to-end regression test (`test_matchup_level_espn_conflict_propagates_as_no_call`, `tests/test_wnba_availability.py`) exercises the full real conflict path through `matchup_player_availability` itself, not just the `merge_availability_sources` unit tests that already existed; verified it fails (silently returns instead of raising) against the pre-fix code. | 2026-08-04 | Real WNBA player-availability source conflicts (official vs. ESPN disagreeing on an injury status) were silently discarded rather than triggering the intended fail-closed no-call, for every pick using this feature |
| F-60 | **`data_sources/polymarket_us.py`'s new `events()` pagination loop had no upper bound** (defensive hardening, not an observed live failure). The pagination itself is a real, correct fix — `events()` was previously hard-capped at a single 200-event page, silently losing any league with more events than that; the P1 fix adds real `offset`-based pagination, looping until a short page signals the last one. But the loop was unbounded (`while True`), unlike every other `while True` network/lock loop in this codebase (see `audit.py`'s `_acquire_exclusive_lock`, which has an explicit comment about exactly this failure mode: "would otherwise wedge... forever with no diagnostic"). An API that ignored the `offset` parameter and kept returning a full page would hang the daily pipeline in an unbounded network loop with ever-growing memory, with no error and no diagnostic. Added a 50-page hard cap (25,000 events at the default limit — far beyond any real sport-league slate) that raises a clear `RuntimeError` naming the league instead of hanging. Two new tests (`tests/test_polymarket_us.py`): one confirms real multi-page pagination works (previously untested — no existing test exercised more than a single page), one confirms a runaway always-full-page API raises instead of looping forever; verified the pagination test fails against the pre-fix single-page code. | 2026-08-04 | Daily pipeline resilience against a hypothetical misbehaving/malicious upstream API (not a bug that had occurred in practice) |
| F-61 | **`eligibility.py`'s future-timestamp fix (P1-4, "future timestamps pass freshness checks") was verified correct as shipped but had zero test coverage.** Read the diff against both `evaluate_eligibility` and `evaluate_esports_eligibility`: `NoCallReason.STALE_DATA` is a real, correctly-scoped enum member, `current`/`parse_utc` are correctly in scope, and the new future-timestamp check is mutually exclusive with the adjacent pre-existing too-old check (both terminate with the same reason code, so no double-counting or conflict) — a genuinely complete, correct fix. But neither `tests/test_eligibility.py` nor `tests/test_fix_regressions.py` (the two files covering these two functions) had a test for the future-timestamp direction at all — only the pre-existing "too old" direction was covered. Added one regression test per function (`test_future_observed_at_becomes_research`, `test_esports_eligibility_fails_closed_on_future_data`); verified both fail against the pre-fix code with the exact real `QUALIFIED` (should be `NO_CALL_STALE_DATA`) mismatch. | 2026-08-04 | No live impact (the fix itself was already correct) — closes a real gap in regression coverage for a real-money-adjacent freshness gate |
| F-62 | **P1-17: promoted a real MLB Trend Engine elasticity refit already sitting unused in the repo, found and fixed a real bug in the calibration script that would have broken the promotion, and got an honest (partially negative) result for totals specifically.** `config/models/mlb-analyst-poisson-trend-v0.3.yaml` — a genuine Poisson-GLM refit (`scripts/mlb_elasticity_refit.py`, held-out correlation 0.10-0.16 across 4 chronological folds against 1136 real completed games) replacing v0.2's hand-bumped round-number elasticities (offense/starter_weakness/park=0.5, weather=0.3, bullpen=0.0 — none ever fit against real outcomes) — had been built 2026-08-02 but never promoted; a dedicated 2026-08-02 test (`test_engine_version_rejects_the_un_promoted_v03_elasticity_refit`) even locked in that it stayed rejected. Promoted it: bumped `ENGINE_VERSION`/`MARGIN_MODEL_VERSION`/`TOTALS_MODEL_VERSION` in `models/mlb.py`, repointed `cli.py`'s two hardcoded formula-spec load sites and `mlb_baseline_refresh.py`'s hardcoded daily-refresh target path (would otherwise have kept silently patching the now-retired v0.2.yaml forever — the same silent-staleness bug class as the pre-fix `starter_era_gap` snapshot and the NPB destructive-overwrite incident), then re-ran `scripts/mlb_measured_edge_calibrate.py` to rebuild fresh margin/totals calibration artifacts against the new formula. Caught a real bug in that calibration script itself while doing this: `main()` hardcoded the literal strings `"measured-edge-margin-v2"`/`"measured-edge-totals-v2"` as the `model_version` argument to `write_artifact` regardless of the actual `--output-margin`/`--output-totals` filename — every future promotion would have silently written an artifact whose own `model_version` field still said v2 even though the file was named v3.json, which `_load_artifact`'s strict version-match gate would have then rejected the instant `MARGIN_MODEL_VERSION`/`TOTALS_MODEL_VERSION` were bumped to match. Fixed by deriving `model_version` from `Path(args.output_margin).stem` instead; new regression test drives the real `main()` entry point end to end and confirms it fails against the pre-fix code with the exact real version mismatch. Honest result once promoted: margin/spread genuinely improved (diagnostic correlation 0.2057→0.208, hit rate 59.5%→60.0%, +39.36u→+41.45u/285 picks) but totals specifically got marginally worse (correlation 0.0585→0.0414, hit rate 55.3%→52.9%, +6.82u→+0.73u/68 picks) — since both markets share one Trend Engine simulation, there is no way to ship margin's improvement without moving totals onto the same formula. Directly checked the raw model's own selected-side split across the full 284-game diagnostic set (not just real, edge-gated logged picks) in both formula versions and found it near-balanced (~52-53% over either way) — the previously-reported "71% over-picked" figure could not be reproduced at this level, suggesting it reflects a smaller, higher-variance sample of real picks rather than a structural property fixable by elasticity refitting; documented honestly in `config/model.yaml`'s `problem_cohorts.totals` note rather than claimed as fixed. Verified live end-to-end (`forecast --sport mlb --model legacy-measured-edge`, dry run): 39 real market calls created, correct `measured-edge-margin-v3`/`measured-edge-totals-v3` model versions and artifact hashes, calibration math independently reconciled by hand (e.g. totals raw=0.5268 → calibrated 0.2484×0.5268+0.3816=0.5124, matches). Also updated `scripts/mlb_elasticity_refit.py`'s own base-spec loader (used for a *future* re-refit) to use the same unchecked loader `mlb_measured_edge_calibrate.py` already relies on, since its old strict loader would otherwise hard-fail on every future refit the instant `ENGINE_VERSION` moves past whatever `BASE_SPEC_PATH` currently points at — caught via a genuine, unrelated test failure while re-running the full suite, not anticipated in advance. 6 files' test fixtures updated (v0.2→v0.3/v2→v3 literals), 6 new tests added. 694 tests pass. | 2026-08-04 | MLB spread real position sizing (genuine improvement); MLB totals bias confirmed still open, sharpened diagnosis (needs an absolute-run-environment feature, not another elasticity refit) |
| F-63 | **Added inactivity decay + thin-data confidence discount to the esports `NeutralElo` model (operator directive: "Yes, add decay + discount and promote"), promoted as `-tiered-elo-v6` for all 5 titles.** Addressed the documented gap (`docs/PROJECT_STATUS.md`): a stale or thin-history team's point-estimate rating was used with full confidence at prediction time, producing the largest, least-trustworthy edges in the system (25-38%). Distinct from the pre-existing `RECENCY_HALF_LIFE_DAYS`/`RECENCY_MAX_BOOST` (a training-time K-factor boost controlling how fast a rating *updates*, not how confidently a stale rating is *used*). Added two new, independent adjustments applied only in `NeutralElo.probability()` (never `raw_probability()`, which stays the pure basis for `update()`'s rating dynamics — deliberately kept separate so decay/shrink can never feed back into how ratings actually move): (1) inactivity decay — a team's rating is pulled toward the neutral 1500 prior the longer since their last recorded match relative to the prediction's own reference date (half-life 45 days, max 60% pull toward neutral — hand-set constants matching this module's existing precedent for `RECENCY_*`, not grid-searched); (2) thin-data shrink — the predicted probability is shrunk toward 0.5 proportional to how far the *less*-established side of the matchup is below 10 recorded games (full shrink at 0 games, matching the existing 1500-default treatment of a genuinely unseen team). `NeutralElo` gained `last_match_utc`/`games_played` per-team dicts, populated by `update()`; `_predict()` (used identically by training, validation, and the ablation script) now passes each row's own `start_utc` as the reference date, so walk-forward metrics exercise the exact same logic live serving uses (`forecast_esports_slate` passes `observed_at_utc`) — not a version of `probability()` that silently skips the new behavior. Persisted the new per-team metadata into the artifact JSON (`last_match_utc`/`games_played` keys) so it survives being loaded fresh for live serving. Verified the fix does what it claims: for thin-data matchups (either side under 10 games) in each title's own real, held-out locked-test set, mean predicted edge dropped 30-35% across all 5 titles (e.g. LOL 0.095→0.064, CS2 0.112→0.073) — a direct, real reduction in exactly the overconfident-edge problem this was meant to fix. Honest trade-off, reported per-title rather than smoothed over: aggregate locked-test accuracy/Brier moved slightly worse in 4 of 5 titles (dota2 the largest: 68.1%→64.8% accuracy; lol 70.6%→69.2%; valorant 63.2%→61.3%; rainbow_six 66.9%→65.7%) and *improved* slightly in 1 (cs2: 65.8%→66.0%) — expected, since deliberately shrinking confidence on genuinely uncertain matchups costs some accuracy on the ones that turn out correct anyway, not just the overconfident misses; this is the real, disclosed cost of fixing the stated problem, not evidence the fix doesn't work. Ran the real training/validation pipeline for all 5 titles (`validate-esports --write-artifacts`) to build real `-v6.json` artifacts (K/confidence_threshold re-selected under the new regime via the same grid-search methodology as v5); every prior lineage (v3/v4/v5) kept on disk for rollback, matching project convention. Updated `config/model.yaml`'s 5 esports entries to point at v6 and each title's own re-validated `research_confidence_gate`. 6 new regression tests (`tests/test_esports.py`) covering: `raw_probability()` staying pure regardless of decay/shrink inputs, thin-data shrink actually shrinking, established teams NOT being shrunk, inactive-team decay reducing (never increasing) an edge, and omitting `reference_date` preserving old no-decay behavior for any caller not yet updated. Verified all new/changed tests fail against the pre-fix code (3 new tests fail outright missing the new methods/fields; 2 pre-existing tests fail on the v5→v6 artifact filename, confirming the lineage bump is load-bearing too). Verified live end-to-end: `forecast --sport lol` against the real Polymarket slate, 35 real matches priced with `model_version: lol-tiered-elo-v6` and a correct real artifact hash. 699 tests pass. | 2026-08-04 | Esports (5 titles) edge sizing — the largest, least-trustworthy edges in the system are now genuinely smaller for the specific stale/thin-data cases that produced them, at a modest, disclosed accuracy cost on the aggregate locked-test metric |
| F-64 | **Not a bug — corrected a stale doc claim.** Task: "add failure-injection tests for ledger/audit atomicity" (open item since P0-2, both this file and `docs/PROJECT_STATUS.md`'s repair order claimed none existed). They already did: `tests/test_ledger_hardening.py::test_ledger_write_crash_leaves_a_recoverable_audit_event_not_a_silent_gap` (a real simulated crash — `write_xlsx_rows_atomic` monkeypatched to raise `OSError` mid-write, confirming the orphaned audit event survives, the ledger row genuinely doesn't land, and a retry gets a fresh, distinct `pick_id`) and `test_audit_append_happens_while_the_ledger_lock_is_still_held` (confirms the audit-before-ledger-write ordering invariant by tracking real `fcntl.flock` depth around every `AuditLog.append` call) both already existed, since commit `222b6a6` — well before either doc's "no failure-injection tests" claim. Found and closed the one real, genuine gap instead: neither test confirmed the operator-facing `_verify_chain` tool (not just raw audit/ledger data inspected by hand in the test) actually detects the orphaned-event state the crash produces. Extended the crash test to call the real `_verify_chain` and assert `reconciled: False`, `created_but_absent_without_removal_event: 1` — closing the loop between "this failure mode is theoretically detectable" and "the tool that's supposed to detect it, does." Corrected both docs' stale claims. 699 tests pass (no new test functions — one existing test extended). | 2026-08-04 | Documentation accuracy only — the real atomicity behavior and its test coverage were already correct; only the record of what was tested was wrong |
| F-65 | **Git LFS tracking for the two growing repo-hygiene-flagged files (`data/events.jsonl`, `data/mlb_statsapi/game_snapshots.jsonl`) — forward-only, per explicit operator choice ("Track going forward only (safe)").** Both files (85MB/61MB) exceeded GitHub's 50MB recommendation and grow daily (audit log appends, starter-history capture); flagged in `docs/PROJECT_STATUS.md`'s repair order as heading toward the 100MB hard cap. Installed `git-lfs` (not previously on the machine), added `.gitattributes` tracking exactly these two paths (not a broader sweep — matching the already-documented, already-scoped flag rather than expanding it unprompted). Deliberately did NOT let `git lfs install` overwrite the existing, load-bearing `.git/hooks/pre-push` (runs the full pytest suite plus advisory mypy before every push) — merged the LFS pre-push call into it manually per `git lfs update --manual`'s exact instructions instead, and added the missing post-checkout/post-commit/post-merge hooks fresh (none existed before). Ran `git add --renormalize` on both files to convert them from full git blobs to LFS pointers starting with this commit — existing history is untouched (every prior commit keeps its original full-size blob; only commits from this point forward store these two paths via LFS), exactly the "forward-only" semantics requested, not a history rewrite. Verified: `git lfs status` showed the conversion staged correctly before commit and confirmed zero objects left to push after (`git lfs push origin main --dry-run` empty output) following the real push; both files remain full-size, readable, non-corrupted on disk post-conversion (LFS transparently smudges them back in the working tree); full test suite (699) still green with LFS active. | 2026-08-05 | Repo hygiene / GitHub push reliability — these two files can now keep growing indefinitely without risking the 100MB hard push-block cap |
| F-66 | **DD-2: 14 `with suppress(DuplicatePickError):` sites (plus 4 structurally identical bare `except DuplicatePickError: continue` sites, same problem) across all 6 per-sport forecast functions in `cli.py` silently dropped a genuine secondary-ledger duplicate with zero trace — indistinguishable from "the model produced nothing here."** Every one of `_forecast_mlb_totals_flat`, `_forecast_learned_sport`, `_log_esports_forecast`, `_forecast_international_sport`, `_forecast_soccer_sport`, `_forecast_tennis_sport` writes the same candidate to more than one ledger per loop iteration (Flat, Gated Research, Main mirroring a primary write already logged elsewhere); the secondary writes were each individually wrapped in a bare `suppress`, so "this exact market was already logged to `<ledger>`" vanished completely, while the *primary* ledger's own duplicates were already correctly tracked via `duplicates.append(error.pick_id)` in the surrounding `except DuplicatePickError` — an inconsistency across the primary/secondary boundary, not a uniformly-missing feature. Added a shared `_append_secondary_ledger()` helper (returns `None` on a genuinely new row, or the *already-logged pick's own pick_id* on a duplicate — matching the existing `duplicates.append(error.pick_id)` convention exactly, so a secondary duplicate is traceable back to the specific row that blocked it, not just a count) and wired it into all 14 sites; each function's return dict now carries a `duplicates`/`*_duplicate_pick_ids`/`*_duplicate_event_ids` field (field name matches each function's own existing return-dict idiom rather than forcing one shape). Also fixed a real, unrelated stale doc string caught in the same diff: `_forecast_mlb_totals_flat`'s returned `"note"` field still said "Flat only, no main-ledger promotion" despite the function genuinely writing to `main_ledger` when eligible (true before the 2026-08-03 Main+Flat directive, never updated after). Added one real regression test per function (6 total, `tests/test_cli.py`), each forcing a genuine duplicate via a new `_DuplicateLedger` test double and asserting the duplicate surfaces in the returned dict with the correct existing-pick-id; traced `_forecast_learned_sport`'s `gated_ledger` branch to its one real call site (`cli.py`'s `daily` dispatch) and confirmed it's currently dead in production (always passes `research_ledger=None`) but is still a real, tested part of the function's API contract. Verified all 6 new tests fail with `KeyError` against the pre-fix code (the new return-dict fields didn't exist at all), confirming they exercise the real fix rather than passing vacuously. Removed the now-fully-unused `from contextlib import suppress` import as a result (incidentally also fixed a pre-existing baseline `I001` import-sort finding when re-running ruff --fix). 706 tests pass. | 2026-08-05 | Every one of this project's per-sport forecast functions — a genuine secondary-ledger duplicate (e.g. a second same-day forecast run) is now visible in the returned diagnostics instead of silently invisible |
| F-67 | **DD-3: 5 bare `except: pass`/`except: continue` blocks in `validation.py` silently discarded real errors with zero observability, most seriously two `except OSError: pass` blocks mid-way through a file read (not on the file's existence check, which was already handled separately).** `_add_legacy_backfill`'s two legacy-file scans and `multi_market_readiness`'s two snapshot-glob scans could silently truncate `spread_count`/`total_count` partway through if the underlying read failed mid-stream (permission error, disk error, file deleted/corrupted concurrently) — the resulting undercount was indistinguishable from a legacy file that legitimately had nothing further to contribute, exactly DD-3's stated concern ("no distinction from genuinely empty data"). Added a module-level `logger` (this file had none at all) and a `logger.warning(...)` naming the specific file and the real exception at all four `OSError` sites — degrading behavior is unchanged and correct (a corrupted legacy auxiliary file shouldn't crash validation reporting), only the observability gap closes. The fifth site (`except ValueError: pass` around `point_in_time_pitcher_era_gap`) is different in kind: traced the function and confirmed its *only* failure mode is a real, well-scoped `NO_CALL_STARTERS_NO_PIT_ARCHIVE` signal, not a masked bug — genuinely correct as a narrow catch, just previously silent. Added `logger.debug` (not `warning` — this is an expected, common per-game outcome across a large historical backtest, not a real problem) rather than restructuring the correct fail-closed logic. 2 new regression tests (`tests/test_validation.py`) inject a real `OSError` mid-read via a monkeypatched `Path.open` and confirm via `caplog` that the specific failing path is named in a warning log, not silently swallowed; both verified to fail against the pre-fix code (no log emitted at all). 708 tests pass. | 2026-08-05 | Validation report accuracy — a corrupted or partially-unreadable legacy odds file is now visible in logs instead of silently producing an undercounted, indistinguishable-from-empty readiness report |
| F-69 | **DD-8: Multiple hardcoded thresholds scattered across code with no single source of truth.** Created shared constants in `src/model_prediction/config.py` (`UNIT_MIN_EDGE`=0.02, `UNIT_INCREMENT`=0.25, `TENNIS_MODEL_UNCERTAINTY`=0.05, `ESPORTS_MIN_OBSERVATIONS`=50, `ESPORTS_MIN_ACCURACY`=0.60, `SIGNIFICANCE_THRESHOLD`=0.05) with identical numeric values to the original hardcoded values. Every hardcoded threshold site in `units.py`, `tennis.py`, `esports.py`, and `roadmap_challenger.py` now annotated with a `# source of truth: config.X` comment. Values unchanged — only a canonical location added. Two original DD-8 thresholds (`confidence_gate.py:0.60`, `guaranteed_signal.py:0.08/0.05`) were in modules already deleted by DD-4. | 2026-08-05 | Config hygiene — every threshold now traceable to a single source |
| F-68 | **DD-4: 9 orphaned feature/data-source modules (658 lines of dead code) creating false signal about what's active.** Audited all 12 candidates from DD-4 and P2-3 against real imports. **5 modules deleted** (zero production imports, never wired): `features/market_signals.py` (architecturally excluded — market prices in outcome model), `features/confidence_gate.py` (thin wrapper duplicating logic in `validation.py`/`roadmap_challenger.py`), `features/guaranteed_signal.py` (post-hoc label, not a model input), `data_sources/openligadb.py` (German/Swiss/Austrian leagues not on Polymarket US), `data_sources/football_data.py` (never imported). **3 dead test files deleted**: `test_confidence_gate.py`, `test_guaranteed_signal.py`, `test_feature_regressions.py`. **4 modules kept** (real code worth wiring later): `features/starting_pitcher.py` (FIP/K%/BB%/WHIP — richer than ERA-only `starter_history.py`), `features/head_to_head.py`, `features/lineup_strength.py`, `features/tennis_surface.py`. **1 false positive corrected**: `data_sources/mlb_statsapi.py` listed in P2-3 but verified actually imported by `cli.py`. Feature registry (`config/tested_features.json`) updated: 27→24 entries. **Also: FIP pipeline built** — `_load_starter_fip_map()` and `_starter_fip_gap()` added to `validation.py`; `starter_rolling_fip()`/`starter_fip_gap_live()` added to `features/starter_history.py`. Locked-holdout comparison (1396 games, 60/20/20 split): FIP beats ERA on hit rate (+1pp, 59.2% vs 58.2%), calibration ECE (0.0228 vs 0.0372, -39%), and units at -110 (+52.3 vs +41.3, +11u/season). FIP's learned coefficient is 2x ERA's (-0.031 vs -0.017); when both present, ERA shrinks to near-zero (-0.007). Recommendation: replace `starter_era_gap` with `starter_fip_gap` in next MLB model version. | 2026-08-05 | Codebase hygiene + MLB model quality — dead code removed, FIP pipeline validated as superior replacement for ERA |

---

# PART 2: COMPLETE TODO — Everything That Must Be Done

## 🔴 Priority 0 — Capital Safety

- [x] **P0-1** (2026-08-03, fully resolved): token_side/pregame/quote-freshness independently verified for every market type now — moneyline (2026-08-02), spread/total/btts (2026-08-03, real resolvers built on live-verified Polymarket contract semantics). Unrecognized/missing market_type now refuses rather than silently skipping. Still open by explicit operator decision: no standalone action(buy/sell)/quantity binding beyond the existing cost cap.
- [x] **P0-2** (2026-08-02 later, corrected 2026-08-04): re-verified — was already backwards in this file; real code is audit-before-ledger-write. Failure-injection tests already existed (`test_ledger_hardening.py`, since `222b6a6`), extended to also cover `_verify_chain` detection (F-64). Still open: no true cross-file transaction log (a real, distinct, lower-severity gap from test coverage)
- [x] **P0-3** (RESOLVED as deliberate operator decision, 2026-08-02 later): classification no longer gates routing or execution — see Part 0. Not a bug.
- [x] **P0-3b** (2026-08-03, confirmed already resolved — stale claim, no code change needed): re-verified directly against live code; `_forecast_learned_sport`, `soccer_forward.py`, and `tennis_forward.py` all already reject/degrade `timestamp_valid=false` snapshots, tested by `tests/test_cli.py`. Esports/KBO/NPB price off live fetches with no stale-timestamp risk; MLB spread/total uses The Odds API, not Polymarket BBO.
- [x] **P0-4** (2026-08-03, resolved): real artifact generated via `train-residual` against real settled data (51 samples, honest identity-fallback since below the 100 minimum); wired into `_forecast_learned_sport` as a new diagnostic-only `market_residual_probability` field (never feeds sizing); verified end-to-end against 6 real settled picks; 2 new tests.
- [x] **P0-5** (2026-08-03, resolved): `spread_research_artifact`/`total_research_artifact` under `models.MLB` now point at MLB's real, already-live Measured Edge artifacts (`measured-edge-margin-v2.json`/`measured-edge-totals-v2.json`) instead of an unrelated generic baseline file. Re-ran the config-artifact-resolution check: zero missing references.
- [x] **P0-6** (2026-08-03, confirmed not a bug — stale claim, no code change needed): both files' `artifact_hash` match under the exact convention this codebase's real loaders use (default `ensure_ascii=True`); the "mismatch" was this doc's own verification script using a non-representative `ensure_ascii=False`, now fixed in the Quick Reference section below.
- [x] **P1-16** (2026-08-03): 30 stale `.bak` files deleted from `data/` directories.

## 🟠 Priority 1 — Data Integrity

**Corrected 2026-08-04, later: this checklist was never synced when the P1 cleanup session (line 254 above) resolved these items with real code changes. Trust the P1 section above, not the checkboxes below, which are now mostly stale.**

- [x] Atomic exposure-check-plus-append across processes — **fixed via `lock_exclusive()` in `ledger.py`, see P1-1**
- [ ] Preserve paired-ledger consistency (research ↔ gated) — genuinely still open, not part of the P1 cleanup session's scope
- [x] Redact The Odds API key from all logged/returned errors — **fixed in `the_odds_api.py`, see P1-2 (and F-56 for a real crash this introduced, since fixed)**
- [x] Reject future `observed_at_utc` values — already enforced at `domain.py:244`; stale claim
- [x] Paginate Polymarket discovery; distinguish provider failure from empty slate — **fixed via offset-based pagination in `polymarket_us.py`, see P1-3**
- [x] Validate rows before adding event ID to feature-ingest dedup state — **fixed in `ingest.py`, see P1-5**
- [x] Surface narrow exception catches: esports, KBO/NPB — **fixed (logging added), see P1-6**
- [x] Build registry-free ban mechanism for esports/soccer/tennis/KBO/NPB — **fixed via `_registry_free_check()` in `bans.py`, see P1-7**
- [x] Fix `model-prediction models` CLI — **fixed via `model_spec(league)` in `cli.py`, see P1-8**
- [x] Fix or delete `/api/scan` route — route does not exist in current `dashboard_server.py`; stale claim
- [ ] Fix 4 dashboard order-preview tests — pin unit value or use sizes within current `$5.00` cap (genuinely still open)
- [ ] Reproduce `outputs/latest/learned-model-validation.json` from one stable green checkout — investigated, not fixed, see P1-13
- [x] Make WNBA availability fail closed — **fixed via `conflict_policy="fail_closed"` default in `player_availability.py`, see P1-14**
- [x] Verify KBO/NPB half-settlement P&L correctness — **confirmed already correct via direct code read, see F-52**
- [x] Clean up stale `.bak` data files in `data/` directories — **fixed twice: 30 files 2026-08-03, 22 more files in the P1 cleanup session, see P1-16**
- [x] **P1-17**: Fix MLB totals over-selection bias — **refit attempted and promoted 2026-08-04 (F-62), honestly did not fix it.** Promoted a real Poisson-GLM elasticity refit (`mlb-analyst-poisson-trend-v0.3`, already built 2026-08-02 but never promoted — found sitting unused) replacing v0.2's hand-bumped round-number elasticities (offense/starter_weakness/park=0.5, weather=0.3, none ever fit against real data). This is a genuine improvement for margin/spread (diagnostic correlation 0.2057→0.208, hit rate 59.5%→60.0%) but totals specifically got marginally *worse* (correlation 0.0585→0.0414, hit rate 55.3%→52.9%) — confirms the standing diagnosis (`config/model.yaml`'s `problem_cohorts.totals.diagnosis: absolute_run_environment_miss`) rather than resolving it: elasticities govern each team's *relative* run differentiation (helps margin), not the *absolute* run-environment signal totals needs. The previously-reported 71% over-pick figure could not be reproduced against the full 284-game diagnostic set in either formula version (both ~52-53% over/under raw split) — likely an artifact of a smaller, higher-variance sample of real gated picks. Promoted anyway per explicit operator directive ("Refit and promote, like starter_era_gap") since both markets share one Trend Engine simulation — margin's real improvement can't ship without moving totals onto the same formula. Real fix still needs the roadmap's own next step (`totals_specific_market_residual`/`branched_absolute_run_intensity_head`), not another elasticity refit.

### DEBUG.md repair order — remaining items (2026-08-03)

Done (6/12): #1 ticket binding, #2 audit atomicity, #4 artifact qualification, #6 green tests, #7 artifact hashes, #10 API key + timestamp + soccer draw.

| # | Item | Directive |
|---|---|---|
| **3** | Probable-starter provenance | Keep the feature — fix it so each record has genuine pregame `observed_at_utc` instead of removing starter data from validation |
| **5** | WNBA availability fail closed | Fix the feature — reject/flag malformed or conflicting source combinations instead of falling through to a call |
| **8** | KBO/NPB half-settlement P&L | Fix — correct the P&L formula for tie/push edge cases |
| **9** | Transactional exposure-check-plus-append | Fix — make exposure check + ledger append atomic across processes, preserve paired-ledger consistency |
| **11** | Economic CI gate | Fix — a zero-crossing confidence interval must not pass as evidence of positive ROI |
| **12** | Reproduce versioned report | Fix — generate `outputs/latest/learned-model-validation.json` from one stable green checkout |

## 🟡 Priority 2 — Architecture and Maintainability

- [ ] **Create `tests/test_cli.py`** — 3,943 lines, 8.3% coverage, zero dedicated tests
- [ ] **Split `cli.py` → `cli/` package** — one module per command family, thin `__main__.py`
- [ ] **Split `dashboard_server.py` → `dashboard/` package** — `routes.py`, `views.py`, `orders.py`, thin entrypoint
- [ ] **Delete or wire-in 12 orphaned modules** — dead code creating false signal
- [ ] Replace `pkill -f` with PID-file dashboard management
- [ ] Resolve or remove unused `SportModel` protocol + unwired model registry
- [ ] Add execution-ticket binding tests (inject mismatched ticket, confirm rejection)
- [ ] Add audit-failure recovery tests (inject failure between ledger-write and audit-append)
- [ ] Add provider secret-redaction tests
- [ ] Add future-timestamp rejection tests
- [ ] Add multiprocess ledger serialization tests
- [ ] Add tests for low-coverage modules: `mlb_statsapi.py`, `odds_soccer_scores.py`, `openligadb.py`, `wnba_availability_evaluation.py`
- [ ] Add direct behavioral tests for transaction failure / timestamp validity / conflict handling
- [ ] Clear 118 Ruff findings: prioritize blind-except catches (5), unused timezone replacements (12), naive datetime (3); 79 EXE002 shebangs on test files are low-risk
- [x] **Commit and push working tree** — **done 2026-08-04**, `face73f`/`31d3b7c` pushed to `origin/main`. This line's original claim is stale.

## 🟢 Priority 3 — Evidence Quality, Dashboard, and Meta-Model

### Storage and Infrastructure
- [ ] Migrate ledger to SQLite (`data/ledger.db`): ACID transactions, real schema, `.xlsx` export for human review
- [ ] Continue prospective BBO + closing-snapshot capture (ongoing)
- [ ] Build NFL injury/lineup snapshot infrastructure (not started)
- [ ] Build NBA/WNBA possession-level snapshot infrastructure (play-by-play/lineup archival for RAPM)

### Dashboard Features
- [ ] Push notifications: macOS `osascript` or Slack webhook on new qualified pick / settlement / stale-order
- [ ] CLV/edge-decay chart (data already exists in `cli.py clv`)
- [ ] Drawdown/exposure chart (`economic_gate.py` already computes max_drawdown + bootstrap CIs)
- [ ] BBO-capture health view: captured-vs-discovered per sport/day
- [ ] CSV/weekly-summary export for offline review

### Meta-Model Layer
- [ ] Cross-market consistency check: detect mismatches between moneyline/spread/total implied probabilities for same game (buildable from existing BBO data)
- [ ] CLV-triggered health monitoring: auto-flag when realized CLV trends negative over last N graded picks
- [ ] Simple ensembling: shared isotonic/Platt meta-calibrator across MLB/NBA/WNBA/NFL out-of-fold predictions

### Reporting
- [ ] Reproduce `learned-model-validation.json` from stable green checkout, current artifacts
- [ ] Report model quality, calibration, CLV, and executable profitability as separate claims — never conflate
- [ ] Keep spread/total/F5/YRFI/NRFI non-promotable until exact historical contract lines + timestamp-valid inputs exist

---

## 📋 Per-Sport Feature Roadmap

### NBA (best target for new features — 73.66% hit rate, models Elo-dominated)
1. Create `nba-elo-trend-lr-v5` with consistency_gap + hot_cold_gap + rest_disparity + games_last_7_gap + schedule_missingness
2. Run full 60/20/20 split; ablate each feature individually; promote if holdout improves + validation doesn't regress
3. Build opponent-adjusted Four Factors + pace (eFG%, TOV%, OREB%, FTA rate on 5/10/season horizons)
4. Build projected-minutes × player-impact model (NBA RAPM with lineup priors, partial pooling by position)
5. Build possession-level snapshot infrastructure for RAPM (play-by-play/lineup archival)
6. Build separate market-residual layer using timestamp-valid executable prices (don't put market price into the independent model)
7. **Unresolved**: NBA 73.66% above favorite base rate — Elo leakage, chalky holdout window, or real? Do not build on Elo until answered

### MLB (active in-season, 14 games/day, most complex model)
1. **Rank 2 (in progress)**: Lineup-regular position-player availability — extend `features/mlb_player_availability.py` from probable-starters-only to all position players
2. **Rank 3**: Bullpen role availability — closer/setup/long relief status from Stats API boxscores (not just aggregate pitching-staff health; Stats API identifies position type, not bullpen role)
3. Park-factor point-in-time fix — season-correct factors with timestamped provenance (currently static 2025 three-year table)
4. Weather point-in-time fix — forecast issue time and lead time needed for production (currently has no timestamps)
5. Build coherent score-distribution model: derive margin, total, spread, and moneyline from ONE distribution (not disconnected binary classifiers that can imply contradictory forecasts). **See P1-17**: current totals elasticities are too weak to differentiate real per-game run environment from the league-average baseline, causing a systematic over-selection bias (71% over vs. 29% under in real logged picks, with overs losing far more often) — needs a real elasticity refit against totals outcomes specifically, likely alongside this item rather than as a standalone patch. **Higher priority now**: MLB spread/total are real, sized Main-ledger rows as of 2026-08-04, not zero-unit Flat-only — this bias now affects real position sizing.
6. **DONE, 2026-08-04**: `starter_era_gap` (real per-starter rolling ERA, `features/starter_history.py`) now replaces `pitcher_era_gap` in the live MLB moneyline artifact (v8) — the exact "revisit with a different functional form" this item used to recommend. Real walk-forward result: validation Brier regressed slightly vs. the incumbent, locked-holdout improved; promoted by explicit operator directive with both honestly disclosed in the artifact's own `qualification.failures`, not a clean pass. See F-54/F-57. Line 8 below ("formally rejected... removal improves every metric") describes an *earlier*, different test (production_feature_ablation.py's additive leave-one-out check, 2026-08-02) and is now superseded, not contradicted — that test found mixed additive results too (validation worse, holdout better), it just wasn't promoted at the time.
7. **Already done**: Starter ERA zero-shrinkage for small-innings samples, bullpen hardcoded-neutral fixed, park factors recomputed empirically, weather feature now wired (was completely dead), rehab-assignment marker, same-day transaction PIT safety
8. **Formally rejected** (2026-08-02, additive-only test — see item 6's correction above for the 2026-08-04 update): `starter_era_gap` added *alongside* the incumbent feature set showed mixed validation/holdout results, not a clean win, so it wasn't promoted at the time. `starting_pitcher_fip` (84% coverage, zero effect, collinear) and `trailing_home_win_rate_30d` (fell from 60.87% to 60.42%) remain genuinely rejected, unrelated to this correction.

### WNBA (short rotation = availability matters most, 12-team league)
1. **Rank 1**: Official availability + projected minutes — prospectively archive WNBA injury report PDFs; build projected-minutes × player-impact with restriction/role/replacement tracking
2. **Rank 2**: Hierarchical player/lineup impact — WNBA-only RAPM with partial pooling by role/position; stronger shrinkage than NBA (fewer games, more roster churn)
3. **Rank 3**: Pace and Four Factors — opponent-adjusted with reliability shrinkage on 5/10/season horizons
4. Build possession-level snapshot infrastructure for RAPM
5. **Already done**: WNBA availability infrastructure (official injury PDFs captured, `features/player_availability.py` built)
6. **Rule**: Do not copy NBA coefficients. WNBA game is different (shorter games, different structure, historically thinner data)

### NFL (small samples ~110 games, QB-driven, offseason until ~September)
1. **Rank 1**: Quarterback identity and uncertainty — expected starter, backup probability, opponent-adjusted early-down EPA/dropback, CPOE, sack/pressure response, scramble value, designed-run share; injury/practice status
2. **Rank 2**: Stable unit efficiency — offense/defense early-down pass EPA, rush EPA, success rate, explosive-play rate, sack rate, neutral-situation pace; opponent + game-state adjusted
3. **Rank 3**: Injury and lineup value — snap-weighted availability by QB, OL, WR, pass rusher, coverage, interior defense; unit continuity; replacement quality
4. Build NFL injury/lineup snapshot infrastructure (not started — highest priority when season approaches)
5. Build when season starts: verify ESPN data flowing, Polymarket markets active, artifact carry-over from offseason, Elo regression rate (50%) still optimal
6. **Rule**: NFL numbers look best in raw delta (-0.0025 val, -0.0038 hold) but sample is tiny (110 games). Do not promote without more data.

### Soccer (17 leagues, Poisson-Dixon-Coles, 62.5% locked-holdout, +90.4u)
1. Multi-league Poisson-DC extensions beyond current 17 leagues
2. BTTS market detection — model works but no Polymarket US BTTS market exists; monitor for platform addition
3. **Already done**: Soccer moneyline now prices against Polymarket's real 3-way `team_win` shape (not silently dropped); Poisson-DC model qualifies on project's own bar; operator override to Main+Flat
4. **Gap**: No walk-forward artifact exists for soccer — `_row_artifact_qualified` fails closed, so real execution requires `--manual-research-order`
5. **Gap**: Gated Research often empty on a given day — `min_edge` 0.05 is a genuinely hard bar against an efficiently-priced full-game 2.5 total market; this is real, not a wiring bug

### Tennis (WTA + ATP, surface-blended Elo)
1. **DONE, 2026-08-03**: ATP wired in alongside WTA (`tennis_forward.py`, dual-tour loop) — Polymarket US's "no ATP market" was true as of 2026-07-16 but went stale; a real, operational ATP league now exists on the gateway. Line 2 below ("Constraints: no ATP market") is superseded, kept for the ITF explanation which still holds.
2. **Remaining constraint**: ITF still unbuildable — ESPN has no ITF scoreboard at all, so even though Polymarket lists ITF markets, there's no data source to build a real prediction against them. Sackmann CSV historical data covers WTA/ATP.
3. **Already done**: Tennis zero-match-history bug fixed (schema incompatibility); stale cache false-positive fixed (wrong parser); 1,878 cached files verified

### Esports (5 titles, all v5 Platt-scaled Elo)
1. Run formal omission study on `neutral_elo_rating_difference` for all 5 titles
2. Monitor Gated Research performance under tightened confidence gates (0.03-0.05 per title)
3. **Already done**: v4→v5 rebuild (K by min Brier, threshold by `units_at_minus_110`); Gated Research gates tightened; Dota2/Valorant swap fixed; auto-refresh wired; Rainbow Six added
4. **Gap**: K/threshold optimized but formal omission study never run
5. **Gap**: COD, Rocket League, Overwatch confirmed to exist in Polymarket taxonomy but have no bo3.gg data source — not buildable
6. **Gap, found 2026-08-04**: `NeutralElo.raw_probability` (`esports.py`) has no confidence discount or inactivity decay — a team's rating carries full weight in scoring regardless of how long ago it last played or how few matches its rating rests on, and `confidence_score` is a flat 100 for every esports row (no `model_uncertainty` output at all, unlike MLB/NBA/WNBA). Found while investigating "some picks have enormous edges": a real open LOL Gated Research pick (2026-08-04, `Barcząca Esports` vs `Lodis`) showed a 38.4% edge — both teams *are* genuinely trained (not the `unknown:`-synthetic-ID new-team case, which already correctly gates out of Gated Research via `source_teams_trained`), but neither has played since May 2026, a 2+ month gap with no rating regression toward 1500 in between. CS2/tennis/Dota2/Valorant showed the same pattern at smaller magnitude (23-28% edges). Not fixed this session — a real modeling change (recency-weighted confidence discount or explicit inactivity decay pulling stale ratings back toward the mean), not a quick patch. Worth prioritizing given it directly produces the largest, least-trustworthy edges anywhere in the system.

### KBO/NPB
1. Run formal omission study on `tie_aware_elo_rating_difference`
2. **Gap**: No Polymarket markets exist at all (platform coverage gap, not a bug — confirmed across many real days)
3. **Already done**: Tie-aware Elo v2 (margin-weighted K, recency decay, game-specific tie probability); silent market-skip bug fixed; timestamp-ordering bug fixed; half-settlement P&L issue documented

---

# PART 3: ALL PROBLEMS AND GAPS

## Data Gaps
- NBA/NFL offseason → zero spread/total snapshots accumulating (expected; resolves when seasons start)
- KBO/NPB: Polymarket does not list markets at all (platform gap, not bug)
- Soccer BTTS: model works but no BTTS market exists on Polymarket US
- Tennis: WTA only — no ATP market, no ITF scoreboard
- MLB park factors: static cross-season provenance blocked; not production-safe
- MLB weather: forecast timestamps missing; not production-safe
- NFL injury/lineup snapshots: infrastructure not started
- NBA/WNBA possession-level snapshots: infrastructure not started
- WNBA total baseline 78.3% suspicious — formal investigation needed
- MLB ingest intermittently misses games — hard to reproduce

## Test Coverage Gaps
- `cli.py`: 8.3% line coverage, zero dedicated test file (no `tests/test_cli.py`)
- `dashboard_server.py`: thin coverage (65 tests for 4,782 lines)
- `mlb_statsapi.py`, `odds_soccer_scores.py`, `openligadb.py`, `wnba_availability_evaluation.py`: near-zero
- Execution-ticket binding: zero tests
- Audit-failure recovery: zero tests
- Secret redaction: zero tests
- Future-timestamp rejection: zero tests
- Multiprocess ledger serialization: zero tests
- Transaction failure / timestamp validity / conflict handling: no direct behavioral tests even in higher-coverage modules

## Architecture Gaps
- No ACID transactions — Excel-based storage with `fcntl.flock`, not database guarantees
- Monolithic files: `cli.py` 3,943 (+115% since July), `dashboard_server.py` 4,782 (+60%)
- 12 orphaned modules: dead code creating false signal about what's active
- Spreadsheet-as-database: no schema enforcement, no type checking, full-file rewrite on every append
- Two concurrent writers on same `.xlsx` → corruption risk (mitigated but not eliminated by `.lock` files)
- `SportModel` protocol + model registry: dead abstractions, unused/non-conformant
- `model-prediction models` CLI: reports stale registry status, not live config-derived state
- 18 stale `.bak` data files cluttering working tree

## Process Gaps
- No automated regression detection (CLV monitoring, calibration drift alerts)
- No push notifications (pull-only dashboard)
- No offline export/report (must view dashboard live)
- Dashboard uses `pkill -f` (explicitly forbidden in `.codewhale/instructions.md`)
- Working tree perpetually dirty (82 files uncommitted) → sub-agents/CI see stale code
- No pre-commit hook for ruff (pre-push hook exists but doesn't catch before commit)
- Documentation was stale across 5 files until 2026-08-02 update

## Promotion Governance
- Locked-holdout gate: ≥50 calls, ≥60% hit rate, every complete month ≥10 calls positive
- Operator override: `qualification_override: true` + documented reason (used for MLB v7, Soccer, esports)
- Override ≠ genuine qualification: `_row_artifact_qualified` fails closed for override rows
- Never promote an artifact, change a threshold, or enable a filter without explicit operator approval
- Validation contract: 60/20/20 chronological split, fit on train, thresholds on validation, locked holdout exactly once
- Point-in-time correctness is the single most important invariant — see `CLAUDE.md` for the full rule

## NOT STARTED: Genuine Statistical Models & Architecture Changes

These are real research/engineering projects — data prep, fitting, walk-forward
validation, and pipeline cutover — not stubs to mark complete just to close a
checkbox. None of the modeling code exists yet. Listed here explicitly so
nobody mistakes them for "already done" or "just needs wiring."

### Statistical Models (zero code exists)

| # | Model | Sport | What it is | Source |
|---|---|---|---|---|
| NS-1 | **Total-score Ridge regression** | MLB/NBA/WNBA/NFL | Ridge-regularized linear model for game totals (predicts combined score, not binary over/under). Artifact file `mlb-total-score-ridge-v1.json` exists in `config/models/` but `config/model.yaml` points MLB total research at the spread baseline instead. Whether this artifact represents a real fitted model or a placeholder is unverified. | DEBUG.md §57-58, §2697-2698 |
| NS-2 | **Tennis point-Markov model** | Tennis | Point-level Markov chain model for tennis match prediction (serve/return point probabilities → set → match). Replaces the current surface-blended Elo which only produces win probabilities. Requires point-level data (Sackmann CSV has it; needs fitting pipeline). | DEBUG.md §57-58 |
| NS-3 | **Roster-aware esports Elo variants** | LOL/CS2/Dota2/Valorant/RainbowSix | Esports Elo that adjusts for roster changes (player transfers, substitutions). The current v5 Platt-scaled Elo treats teams as atomic — a roster change resets nothing. A roster-aware variant would decay or split Elo when players move. Requires player-level match data (bo3.gg API may have it; unverified). | DEBUG.md §57-58 |
| NS-4 | **Joint Negative Binomial totals** | MLB | Hierarchical Poisson/Negative-Binomial model for correlated run scoring, as described in MODEL_IMPROVEMENTS.md §404-414 ("Correct MLB model form"). Produces moneyline, run line, and total from one coherent run distribution instead of disconnected binary classifiers. | MODEL_IMPROVEMENTS §404-414, DEBUG.md §57-58 |

### Architecture Changes (code partially exists, not cut over)

| # | Change | Status | Source |
|---|---|---|---|
| NS-5 | **Live pipeline cutover to ModelLedger** | **Updated 2026-08-02 (later), re-verified live**: `model_ledger.py` built, historical data migrated (483 unique decisions across 12 models), AND the live pipeline is now actively wired in — `ledger.py::_append_record` writes every new prediction to both the old `PickLedger` and the matching `ModelLedger`, fail-soft, going forward (verified: a simulated write failure there doesn't touch the primary write). What's still NOT done: `cli.py`'s ~15 forecast functions still write through `PickLedger` as their primary/only intentional target — nothing has been switched *off* the old system, and the old system remains authoritative. See Part 0. | DEBUG.md §51-63, Part 0 |
| NS-6 | **Dashboard redesign** | **Updated 2026-08-02 (later), re-verified live**: no longer zero code. A new "Models" tab now exists — evidence table (sample size, Brier, log loss, CLV, PnL, no qualified/research badges) plus a live one-event/every-applicable-model comparison view, backed by `/api/model-ledgers`, plus operator-decision recording (`/api/model-ledgers/decision`). This covers the spec for the *new* view. The *old* dashboard views (picks tables with QUALIFIED_SHADOW_CALL/RESEARCH_OBSERVATION badges) are untouched and remain the primary UI — this is additive, not a replacement. See Part 0. | DEBUG.md §55-56, Part 0 |

## Documentation State (2026-08-02)
- ✅ `PROJECT_STATUS.md` updated
- ✅ `TODO.md` updated
- ✅ `README.md` updated
- ✅ `FEATURE_REGISTRY.md` updated
- ✅ `CHECKLIST.md` updated
- ✅ `ENGINEERING_ROADMAP.md` updated
- ✅ `HISTORY.md` created
- ✅ `MASTER.md` created (this file)
- ✅ `MASTER.md` re-verified against live code and corrected (2026-08-02, later) — see the note at the top of this file and Part 0. `TODO.md`/`CHECKLIST.md`/`PROJECT_STATUS.md`/`ENGINEERING_ROADMAP.md`/`HISTORY.md`/`FEATURE_REGISTRY.md` were not re-verified in this pass; only this file was updated.
- ✅ `MASTER.md` all five remaining open P0 items worked and re-verified directly against live code (2026-08-03) — see the verification note at the top, and each P0 entry in Part 1/Part 2. `TODO.md`/`CHECKLIST.md`/`PROJECT_STATUS.md`/`ENGINEERING_ROADMAP.md`/`HISTORY.md`/`FEATURE_REGISTRY.md` were again not re-verified in this pass; only this file was updated.
- ⚠️ `DEBUG.md` already has its own later entries (2026-08-02, later, "Per-model ledger architecture") this file's original generation predates — that's the primary source for Part 0's claims. Not re-checked for a 2026-08-03 entry covering this session's P0 work.

---

# Quick Reference: All Verification Commands

```bash
# ── Health ──
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
.venv/bin/python --version

# ── Critical imports (all 9 modules) ──
env PYTHONPATH=src:. .venv/bin/python -c "
import model_prediction.cli, model_prediction.validation
import model_prediction.learned_forward, model_prediction.eligibility
import model_prediction.ledger, model_prediction.forward
import model_prediction.audit, model_prediction.xlsx_ledger
import model_prediction.model_ledger
print('All critical imports OK')
"

# ── Entry point ──
.venv/bin/model-prediction --help

# ── Audit chain ──
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain

# ── Artifact hash verification (run from project root) ──
# ensure_ascii left at its default (True) deliberately -- matches the
# convention every real loader in this repo uses (models/mlb.py::_load_artifact,
# MarketResidualModel). An earlier version of this script passed
# ensure_ascii=False, which produced a false MISMATCH for any artifact
# containing a non-ASCII character (see P0-6's 2026-08-03 correction).
.venv/bin/python - <<'PY'
import hashlib, json
from pathlib import Path
for path in sorted(Path("config/models").glob("*.json")):
    raw = json.loads(path.read_text())
    key = "artifact_hash" if "artifact_hash" in raw else "model_hash"
    canonical = {n: v for n, v in raw.items() if n != key}
    computed = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",",":")).encode()).hexdigest()
    print(path.name, "OK" if computed == raw.get(key) else "MISMATCH")
PY

# ── Config artifact resolution (find missing references) ──
.venv/bin/python - <<'PY'
from pathlib import Path
import yaml
config = yaml.safe_load(Path("config/model.yaml").read_text())
for model, item in config.get("models", {}).items():
    if not isinstance(item, dict): continue
    for key in ("production_artifact","research_artifact","spread_research_artifact","total_research_artifact","artifact"):
        v = item.get(key)
        if v and not Path(v).exists(): print(f"MISSING: {model}.{key} -> {v}")
PY

# ── Runtime (all read-only, no side effects) ──
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary
curl -s http://127.0.0.1:8765/api/health
curl -s http://127.0.0.1:8765/api/status | python3 -m json.tool
curl -s http://127.0.0.1:8765/api/matrix | python3 -m json.tool

# ── Dry forecast (--model learned, no --log, no --execute) ──
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli forecast \
  --sport mlb --date $(TZ=America/New_York date +%Y-%m-%d) --model learned

# ── Dashboard ──
python3 dashboard_server.py  # then http://127.0.0.1:8765/
```
