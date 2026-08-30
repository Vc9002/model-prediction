# Comprehensive System Defects, Gaps, and Risk Audit (2026-08-27)

This document provides an exhaustive, code-verified audit of all identified defects, architectural gaps, serving landmines, data pipeline vulnerabilities, and technical debt across the `model-prediction` codebase.

---

## 1. Executive Summary & Audit Verdict

| Category | Status | Summary Finding |
| :--- | :--- | :--- |
| **Test Suite Health** | **100% PASS** | 2,414 tests pass, 3 skipped, 0 failures across all unit, integration, and regression suites. |
| **Linting & Code Style** | **100% CLEAN** | 0 Ruff findings across `src/`, `tests/`, and `scripts/`. |
| **Static Type Checking** | **ACTION REQUIRED** | Mypy reports 220 type errors across 59 files (primarily unchecked annotations in dashboard routes, ledger casting, and optional openpyxl imports). |
| **Model Serving Safety** | **RESOLVED 2026-08-27** | `mlb-nrfi-v1` artifact (holdout `0.690572` logloss) promoted after the live path was rewired off the uncalibrated hand-set model: `mlb_first_inning_live.py` PIT accumulator + `MLBFirstInningModel.from_dict()` + champion `MLB.nrfi` (commit `693744b`). |
| **Upstream Data Feeds** | **DEGRADED (EXTERNAL)** | The Odds API soccer feed is down ($\ge 31$ days, 401 Unauthorized); API-Football adapter is written but awaits `API_FOOTBALL_KEY` provisioning. |
| **Point-in-Time (PIT)** | **TIGHTENED** | Historical static table leaks identified and isolated; rolling PIT builders (`park_factor_pit`, `mlb_game_context`) implemented with strict causal boundaries. |

---

## 2. Deep Subsystem Defect & Gap Catalog

### A. Model Serving & Gating Landmines

#### 1. MLB NRFI / First-Inning Live Serving Mismatch — ✅ RESOLVED (2026-08-27, commit `693744b`)
- **Location**: [`src/model_prediction/cli/forecast.py:616`](file:///Users/vincentc9002/model-prediction/src/model_prediction/cli/forecast.py#L616), [`src/model_prediction/models/mlb_first_inning.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/models/mlb_first_inning.py), [`src/model_prediction/models/mlb_first_inning_live.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/models/mlb_first_inning_live.py), [`config/models/mlb-nrfi-v1.json`](file:///Users/vincentc9002/model-prediction/config/models/mlb-nrfi-v1.json).
- **Finding (original)**: A new first-inning model was fitted and frozen into `config/models/mlb-nrfi-v1.json` with holdout logloss of **0.690572** (reproducing the 0.6910 baseline). However, the live forecast path `_forecast_mlb_nrfi_flat` in `forecast.py` instantiates `MLBNRFIModel()` from `mlb_nrfi.py`, which is an old, unfitted model with hand-set weights (`0.0424`, `-0.045`).
- **Landmine**: If `mlb-nrfi-v1` were registered in `config/production.yaml` without updating `_forecast_mlb_nrfi_flat`, the registry would report `mlb-nrfi-v1` as active, while the live pipeline would execute the uncalibrated hand-set model and stamp picks with the validated model's version string.
- **Resolution**: The pre-game point-in-time feature accumulator `mlb_first_inning_live.py` was built (same 19-feature formulas as the batch ledger, keyed by starter name — the accepted risk pattern `features/starter_history.py` already uses; exact-match verified vs the batch ledger on 7 real games across 3 seasons, parity test 1e-4 tolerance). `_forecast_mlb_nrfi_flat` now fails closed on a missing/hash-invalid artifact and predicts via `MLBFirstInningModel.from_dict()`. `mlb-nrfi-v1` registered and promoted as champion `MLB.nrfi`; `blocked_workflows` now empty.
- **Remaining caveat**: Polymarket has no NRFI/YRFI market — shadow rows have no market-side CLV/ROI grading. The legacy `MLBNRFIModel` class still exists (legacy tests) with `model_version="mlb-nrfi-v1"` as its default string — anyone re-wiring it would reopen this exact trap.

#### 2. WNBA Totals Serving Path Absence
- **Location**: [`src/model_prediction/cli/forecast.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/cli/forecast.py), [`src/model_prediction/total_score.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/total_score.py).
- **Finding**: While `wnba-total-score-ridge-v1` exists as an artifact, there is no active live serving pathway wired in `cli/forecast.py`. `total_research_artifact` is only referenced for dashboard status strings.
- **Root Cause**: WNBA totals modeling is data-bound; boxscore history starting only in ~2026-07 yields insufficient sample depth for structural models to beat the market closing line (market MAE 13.12 vs model MAE 20.92).

#### 3. Code-Backed Model Contracts (Soccer & Tennis)
- **Location**: [`src/model_prediction/champion_challenger.py:28-30`](file:///Users/vincentc9002/model-prediction/src/model_prediction/champion_challenger.py#L28-L30), [`src/model_prediction/models/soccer_dixon_coles.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/models/soccer_dixon_coles.py).
- **Finding**: `soccer-poisson-dc-v1` and `tennis-surface-elo-v1` are code-backed without JSON artifact files, utilizing the `CODE_BACKED` sentinel hash in `ProductionRegistry`.
- **Risk**: Code refactors in model classes can alter prediction values without triggering artifact hash mismatch detection.
- **Hardening Recommendation**: Pin explicit code-backed parameter bundles or freeze snapshot JSONs for code-backed models.

#### 4. MLB v8 Champion Qualification Gap
- **Location**: [`config/models/mlb-elo-trend-lr-v8.json`](file:///Users/vincentc9002/model-prediction/config/models/mlb-elo-trend-lr-v8.json).
- **Finding**: The active MLB champion carries `qualified: false` and was promoted via operator override. Full qualification requires prospective Level 3 validation against untouched future games.

---

### B. Upstream Data Feeds & External Dependencies

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        DATA PIPELINE STATUS                             │
├────────────────────────┬───────────────────┬────────────────────────────┤
│ Data Feed              │ Status            │ Operational Detail         │
├────────────────────────┼───────────────────┼────────────────────────────┤
│ The Odds API           │ ❌ 401 Outage     │ Outage ≥31 days.           │
│ API-Football v3        │ ⚠️ Pending Secret │ Implemented; needs API key │
│ Polymarket US Quotes   │ ⚠️ Partial        │ Missing NRFI/YRFI/F5 lines │
│ Open-Meteo Weather     │ ⚙️ Research-Only  │ Verified honest null       │
│ Statcast Daily Ingest  │ ✅ Automated      │ Wired into step5e daily    │
│ WNBA Injury Snapshots  │ ✅ Resolved       │ 81/81 morning PDFs parse   │
│ Bet Better Model Feed  │ ✅ Automated      │ Wired into step1e daily    │
└────────────────────────┴───────────────────┴────────────────────────────┘
```

#### 1. Soccer Upstream Outage & API-Football Key Dependency
- **Finding**: The Odds API credential has failed with `401 Unauthorized` for over 31 days.
- **Remedy**: [`src/model_prediction/data_sources/api_football.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/data_sources/api_football.py) is implemented to query API-Football v3 endpoints. It remains dormant until the operator sets `API_FOOTBALL_KEY`.

#### 2. Polymarket Market Coverage Gaps
- **Finding**: Polymarket US captures executable BBO quotes for major markets (Moneyline, Runline/Spread, Game Totals). However:
  - First-Inning (NRFI/YRFI) quote slugs are absent from the odds tree.
  - First 5 Innings (F5) runs markets are not captured.

#### 3. Offseason Feed Dormancy (NBA / NFL)
- **Finding**: NBA is in deep offseason and NFL in preseason; zero live odds sources or active ledger rows are currently generated.

---

### C. Point-in-Time (PIT) Correctness & Information Boundaries

#### 1. Static Table Lookahead Risk
- **Finding**: Static lookup tables (such as historical park factors) computed across entire seasons leak future scoring environment data into earlier games.
- **Mitigation**: Causal rolling accumulators ([`src/model_prediction/features/`](file:///Users/vincentc9002/model-prediction/src/model_prediction/features)) must strictly enforce `observed_at_utc <= decision_time`.

#### 2. macOS Sleep Interval Coalescing & Lineup Capture
- **Finding**: `launchd` coalesces missed timer firings when a Mac is asleep. For late-afternoon and west-coast MLB games, lineup postings (posted ~2–3 hours before first pitch) can be missed if the host is sleeping.
- **Status**: The wake planner script [`scripts/plan_lineup_wakes.py`](file:///Users/vincentc9002/model-prediction/scripts/plan_lineup_wakes.py) and daemon plist [`ops/launchd/com.vc.mlb-lineup-wake-planner.plist`](file:///Users/vincentc9002/model-prediction/ops/launchd/com.vc.mlb-lineup-wake-planner.plist) schedule system wakeups via `pmset`, requiring root privileges.

---

### D. Ledger Architecture, Settlement & Gating

#### 1. Stale Open Rows & Postponed Game Handling
- **Location**: [`src/model_prediction/system_health.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/system_health.py), [`src/model_prediction/cli/settle.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/cli/settle.py).
- **Finding**: 22 open rows in the ledger are $> 72\text{ hours}$ past their scheduled start time.
- **Cause**: Polymarket rules maintain open markets for up to two weeks to allow for makeup games, whereas ESPN scoreboards immediately report games as postponed.
- **Protocol**: Avoid bulk un-audited sweeps; clear stale rows via identity-scoped, operator-approved manual settlement scripts.

#### 2. Retired vs Canonical Ledger Separation
- **Finding**: Canonical ledger storage is strictly managed via SQLite (`RuntimePaths.ledger_db`). Legacy directories (`data/model_ledgers/`) froze on 2026-08-03 during the ledger migration.
- **Rule**: All reporting, backtesting, and dashboard queries must query SQLite authority (`MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite`) rather than static disk workbooks.

---

### E. Static Typing & Code Quality Gaps

#### 1. Mypy Type Checking Errors
- **Finding**: Running `mypy src/model_prediction` surfaces 220 errors across 59 files.
- **Key Clusters**:
  - `src/model_prediction/dashboard/routes.py` & `orders.py`: Missing annotations on request parameters and dictionary unpacking.
  - `src/model_prediction/portfolio/polymarket_ledger.py`: Type mismatches in string/int/float conversions.
  - `src/model_prediction/cli/daily.py`: Future return type mismatches in ThreadPoolExecutor fan-out.

---

## 3. Experimental Ablation & Research Results

### MLB Real Weather & Travel Context Ablation (2026-08-27)
- **Harness**: [`scripts/mlb_weather_travel_ablation.py`](file:///Users/vincentc9002/model-prediction/scripts/mlb_weather_travel_ablation.py)
- **Methodology**: Ingested Open-Meteo historical archive weather and away-team travel distance via [`features/mlb_game_context.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/features/mlb_game_context.py) and [`features/mlb_venue_geocoding.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/features/mlb_venue_geocoding.py). Tested against control baseline (weather=1.0, travel=0.0).
- **Results**:
  - Control MAE: `3.584296`, RMSE: `4.487904`
  - Candidate MAE: `3.578813`, RMSE: `4.484318`
  - Point Estimate Gain: `+0.005484` MAE
  - 95% Bootstrap CI: `[-0.000215, +0.011088]`
- **Verdict**: `NO_PROMOTION` (Honest null: 95% confidence interval straddles zero).

---

## 4. Prioritized Remediation Roadmap

1. **Phase 1: Serving Safety (Immediate)**
   - ✅ DONE 2026-08-27 (commit `693744b`): live pre-game feature builder for `MLBFirstInningModel` built (`models/mlb_first_inning_live.py`) and `mlb-nrfi-v1` promoted; see Section A.1 for the resolution record.
2. **Phase 2: Data Ingestion Resilience**
   - Provide `API_FOOTBALL_KEY` to restore soccer live data feeds.
   - Install lineup wake LaunchDaemon (`com.vc.mlb-lineup-wake-planner.plist`) with root privileges.
3. **Phase 3: Codebase Type Hardening**
   - ✅ DONE 2026-08-28: Resolved all 220 Mypy typing errors across `src/model_prediction/dashboard/`, `cli/`, `ledger.py`, and models. Repository achieves 0 Mypy errors across all 319 source files.
4. **Phase 4: Data Warehouse Architecture**
   - ✅ DONE 2026-08-28: Implemented canonical `MarketQuote` schema and `MarketQuoteWarehouse` with strict Point-In-Time (`observed_at_utc <= as_of_utc`) filtering (`data_sources/market_warehouse.py`, `domain.py`, `tests/test_market_warehouse.py`).

---

## Addendum — 2026-08-30 NCAAF serving and governance audit

Four defects, all found in the CFB system built and production-wired on 2026-08-29. Each is now closed with a regression test unless noted.

| # | Defect | Evidence | Status |
| :--- | :--- | :--- | :--- |
| A-1 | **Qualification measured on a simulated cohort.** `scripts/build_cfb_historical_dataset.py` was named like an ingest script but its body was `generate_historical_dataset()` — a seeded RNG simulator. Every headline metric (`N=8,146` walk-forward, `+29.17%` ROI, `66.97%` hit rate) came from it. | Builder had no HTTP/import call anywhere; `data_sources/cfb_data.py` was a static team registry. Cohort has since been rebuilt from the real ESPN API (3,930 games), so the original numbers are unreproducible. | **CLOSED** — cohort replaced; all three artifacts self-report `qualification.qualified: false`. |
| A-2 | **Backtest edge was circular by construction.** The simulator drew scores from a latent `mu`, then set the market total line to `mu + gauss(0, 3.0)` — the model's own estimation target plus noise. The model beat that by arithmetic, not skill. | A 66.97% hit rate against a −110 line is outside any plausible market range and should have halted acceptance before any code was read. | **CLOSED** — generator removed with the cohort. |
| A-3 | **Served ask was the no-vig `0.50`, not the real price.** ESPN publishes the spread/total line but no price, so the ask is synthesized. `0.50` understates the true −110 ask by ~2.4pp, and that gap became fictitious edge on every call. Three market-probability variables were also declared and never assigned, so *all* asks fell through to the constant. | 32/32 NCAAF ledger rows had `market_probability` exactly `0.5` (1 distinct value); every other sport showed 5–53 distinct values. Result: 8/8 NCAAF picks `QUALIFIED` at up to 2.0U while peers were mostly `PAPER_CALL`. | **CLOSED** — `STANDARD_LAY_ASK` (0.52381) matches the research pipeline; contracts are skipped when no ask is available. Pinned by `test_cfb_standard_lay_ask_is_vig_inclusive_not_no_vig`. |
| A-4 | **Config contradicted the artifacts, and the docs contradicted the config.** All three artifacts read `qualified: false`, yet `production.yaml` had them `enabled: true` and listed as `champions`, and `model.yaml` had NCAAF `shadow_qualified`. `PROJECT_STATUS.md` had already *recorded* the demotion as done. | Verified directly in the config files after the doc claimed otherwise. | **CLOSED** — demotion genuinely applied; pinned by `test_cfb_models_are_demoted_and_not_champions`. |

**Resolved 2026-08-30 (operator authorised):** the 9 open Main-ledger NCAAF rows — plus the 19 open Flat rows carrying the same fabricated price — were **voided**, not deleted, via `PickLedger.void` (`scripts/void_ncaaf_fabricated_ask.py`). Each is now `settled/push` at 0.0000 units with a `void_reason` naming the fabricated ask and the `CFB_BASELINE_TOTAL` 54.0 line, and a `pick_voided` entry in `data/events.jsonl`; nothing was removed, so the record that these picks were made survives in full. `CLAUDE.md` reserves `allow_staked_removal=True` for ledgers that can never hold real money — explicitly not Main — which is exactly why voiding, not removal, was the right instrument here. The settled rows were deliberately left untouched: Main **1 win / 6 losses, −7.5U** (Flat 11–10) is the live evidence that disproved the backtest, and rewriting it would erase the finding.

Two things surfaced while doing it, both worth their own follow-up: NCAAF is absent from `MAIN_LEDGER_SPORTS`, so `main_ledger()`/`flat_ledger()` — and therefore the operator `void` CLI, which routes through `MultiSportPickLedger` — cannot reach these rows at all, even though the daily job wrote them. A tier that can be written but not administered through the sanctioned CLI is a governance gap, not a convenience one.

### The generalisable lesson

The full verification stack was green throughout: 2,462 tests passing, 0 ruff findings, 0 mypy errors. None of it could see any of this, because no test asserts anything about the *distribution* of served values. A-3 was found in a single `GROUP BY` against the ledger, comparing distinct-value counts per sport — a constant where a distribution belongs is invisible to every static check and obvious in one query against real output. Suite and linter cleanliness is not evidence about served values.
