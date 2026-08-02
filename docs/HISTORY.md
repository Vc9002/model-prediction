# Project History — model-prediction

This document records the chronological arc of this project. It is compiled from
`DEBUG.md`'s dated audit entries, `CHANGELOG.md`, `docs/TODO.md`, git history,
and direct inspection of the working tree on **2026-08-02**. It exists so a new
reader (or a future you) can understand what happened, when, and why — without
re-reading 2,868 lines of debug audit.

## Phase 0: Foundations (pre-2026-07-17)

- Project scaffolded as a multi-sport prediction system with Elo-based models,
  Polymarket US integration, and an Excel-based ledger.
- Early models trained: MLB v1-v3, NBA/WNBA/NFL v1-v3, soccer Elo-trend v1-v2.
- Core infrastructure built: `ingest.py` (ESPN scoreboards), `learned_forward.py`
  (logistic regression on Elo+trend), `ledger.py` (Excel append/settle),
  `dashboard_server.py` + `dashboard.html`.
- KBO/NPB, esports, and tennis backends exist in research state.
- Console entry point broken; `PYTHONPATH=src:.` workaround used everywhere.

## Phase 1: Qualification Push (2026-07-17)

- V2 artifacts generated for MLB, NBA, WNBA, NFL with hash-verified
  chronological 60/20/20 splits.
- WNBA requalified at 65.98% (97 calls, +25.18U).
- NFL qualified at 60.55% (109 calls, +17.00U).
- Monthly gate rules formalized: ≥10 calls per complete month, partial/incomplete
  months non-binding.
- Learned trailing-30-day adaptive HFA tested on MLB, rejected.
- Confidence-gap gating proved redundant (exactly `2*max_prob-1`), rejected.
- MLB pitcher data audited: 4,325/4,785 events have both starters and ERA, but
  retrospective caches lack PIT validity — training blocked.
- 120 tests pass, ruff clean. Final evidence report written.

## Phase 2: Bug Sweep + Hardening (2026-07-17, later)

- Audit hash serialization fixed (compact JSON separators).
- Polymarket executor: `token_id` resolved from market slug.
- Empty `observed_at_utc` crash fixed with `.strip()` guard.
- Ban list `EntityResolutionError` catch added — stale config no longer blocks CLI.
- Platt calibrator boundary fix: `(0,1)` → `[0,1]` auto-clip.
- Config drift fixed: `maximum_data_age_hours` and `maximum_unreviewed_disagreement`
  now flow from `config/model.yaml` through the forecast path.
- Float comparison epsilon added to `evaluate_locked_holdout`.
- `normalize_no_vig` rejects probabilities `>= 1` (was only checking `≤ 0`).
- CLI dispatch fallthrough replaced with explicit elif + ValueError.
- LRU caps (256 entries) added to ESPN client caches.
- `MarketOddsSnapshotStore` flush-on-append for crash resilience.
- Ledger cleared and re-initialized; backup preserved.
- 120/120 tests pass, ruff clean.

## Phase 3: Esports, KBO/NPB, and Data Infrastructure (2026-07-20)

- Confidence-gate no-op fixed in esports: threshold selection was picking the
  loosest gate (most observations = threshold 0.0), never actually gated anything.
  Fixed to select by `units_at_minus_110` on validation.
- `units_at_minus_110` reporting added to esports + KBO/NPB validation.
- Real per-side moneyline BBO capture started for all 7 sports (MLB/NBA/WNBA/NFL/
  esports/KBO/NPB).
- Roadmap Challenger factorial experiment ran: 64 feature combinations across
  MLB/NBA/WNBA/NFL. **Zero cleared the full statistical screen.**
- `schedule_available` found structurally degenerate (near-constant).
- No production model or config changed by the experiment.
- Documentation truth reset: `docs/PROJECT_STATUS.md` created as source-of-truth
  entry point; stale production tables removed; packaging issues documented.

## Phase 4: Esports + Soccer Wiring, Data Quality Fixes (2026-07-27 → 07-28)

Two serious, live-verified data bugs found and fixed:

1. **Dota2 and Valorant swapped discipline IDs** — each model trained on the other
   game's history. Fixed by correcting the API → model mapping.
2. **Tennis silently used zero match history** — a `FeatureStore`/`GameRecord` shape
   incompatibility meant every tennis pick showed exactly 50%. Fixed by matching
   the data schema to what the feature pipeline expected.

Other wiring completed:
- MLB Measured Edge totals/spread rebuilt with real fitted elasticities (was
  hardcoded 1.0 for both).
- Soccer moneyline now prices against Polymarket's real 3-way `team_win` market
  shape (was dropping these markets silently).
- Esports ratings auto-refresh before every forecast (was manual backfill only).
- KBO/NPB silent-gap bug fixed: markets with wrong number of sides now record
  `NO_CALL_MARKET_SIDES_INVALID` reason.
- `League.WORLD_CUP` fully retired.
- Rainbow Six Siege (5th esports title) added.
- NBA/NFL spread/total: 0 snapshots (offseason — will resolve).
- WNBA total baseline 78.3% flagged as suspicious.
- Soccer BTTS: model works but no BTTS market exists on Polymarket US.
- Audit chain repaired: 10,837 events, 0 breaks.

## Phase 5: Operator Overhaul — Gates, Sizing, and Model Review (2026-07-30 → 07-31)

Major operator-directed changes:

1. **MLB confidence gate removed** — every forecasted game becomes a real, sized
   Main-ledger call. Both confidence and edge numbers still recorded in reason
   for human review.
2. **MLB min-edge-vs-market gate removed** — same philosophy: show everything.
3. **Unit sizing formula fixed** — `model_uncertainty` was a dead parameter in
   `edge_scaled_units` (accepted at 6 call sites but never read). Two picks with
   identical `model_probability` always got same size regardless of uncertainty.
   Fixed: uncertainty now haircuts the raw edge before scaling.
4. **Unit range widened** from 0.5U-2.0U to 1.0U-2.0U (1U floor, 2U ceiling).
5. **MLB v7 rebuilt** via standard walk-forward pipeline — replaced v6's ad-hoc
   experiment with honest, systematic rebuild. Hits 58.0% on locked holdout
   (+27.5U/-110). Does not clear the 60% bar, but is real, not resting on a
   broken coefficient (`starter_era_gap` retired).
6. **Esports v4→v5** — K/threshold selection overhauled: K now chosen by min Brier
   (pure calibration), confidence_threshold by `units_at_minus_110` (genuine
   volume-vs-quality tradeoff). v4's K=96 was at the exact top of its search grid
   for 4/5 titles — overfitting.
7. **Gated Research tightened** for esports — `research_confidence_gate` raised
   from 0.0 to artifact-validated thresholds (0.03-0.05). Real settled Gated
   picks were performing *worse* than unfiltered Research.
8. **Soccer Poisson-DC model validated** — 62.5% locked-holdout hit rate, +90.4u,
   every month positive. No code changes needed; it was already good.
9. **Ledger routing documented** in `docs/LEDGER_ROUTING.md` — definitive per-sport/
   per-market routing conventions.
10. **Soccer promoted to Main+Flat** by operator override (not genuine promotion —
    no walk-forward artifact exists; `_row_artifact_qualified` fails closed so
    real execution still requires `--manual-research-order`).
11. **Weather park-factor key mismatch fixed** — A's/River Cats key collision
    silently zeroed weather for one team.
12. **NPB destructive-overwrite bug** — `international_baseball.py` was overwriting
    all historical NPB data on each forecast run instead of appending.
13. **CLV wiring gap fixed** — Flat/Research/Gated ledgers were never scanned for
    closing prices; only Main was.
14. **Soccer flat/Main-ledger pairing** — soccer writes real rows to Flat,
    correctly paired.

## Phase 6: Player Availability Features (2026-08-01 → 08-02)

1. **MLB pitching-staff availability** — new shadow feature
   (`features/mlb_player_availability.py`). Cross-references ESPN probable starters
   against MLB Stats API IL transaction history. Two real gaps found and fixed:
   - Missing "rehab assignment" marker (291 real transactions silently skipped)
   - Roster snapshots captured but never read (dead-weight capture)
   - Same-day transaction ambiguity resolved (strict `<` not `<=`)
2. **MLB position-player (lineup) availability** — extends the above to all players,
   not just probable starters. Wired as `step5c_mlb_availability` in CLI daily.
3. **`starter_era_gap` feature formally ablated** — genuine negative result
   (removing it *improves* every metric). Not promoted; documented honestly.
4. **Model ledger architecture designed and built** — `model_ledger.py` with
   `ModelLedger` class: one `.xlsx` per model identity (not per sport), common
   schema, no classification/record_type field, operator-decision block separate
   from model output. Additive; existing `PickLedger` untouched.

## Phase 7: Dashboard Hardening (2026-08-02)

1. **Dashboard token-based auth** — real order-execution endpoint (`POST
   /api/order/submit`) now requires a per-session token generated server-side,
   injected into the served page. Previously had only Origin/Host CSRF check
   + client-supplied `confirm:true` flag (not a credential).
2. **SELL-path P&L formula safeguard** — BUY and SELL settlement both now use
   a single canonical `_settle_pnl` function with algebraically verified
   formulas.
3. **Scan Open Ledger Prices** fix — was only scanning Main ledger, never Flat/
   Research/Gated.
4. **Archive settled rows** — new `archive_settled_rows` function for audited
   removal of settled rows; never raw deletion.

---

## Current State (2026-08-02)

- **Tests**: 624 passed, 0 failed
- **Ruff**: 118 findings (117 baseline + 1 new EXE002 on test_validation.py)
- **Audit chain**: 43,304 events, 0 breaks
- **Git**: Single `main` branch, 82 dirty files (9,929 insertions, 4,023 deletions
  uncommitted), latest commit `d0568ac`
- **CI**: `.github/workflows/ci.yml` exists (ruff + pytest on push/PR)
- **Dashboard**: Live at `127.0.0.1:8765`, managed via launchd
- **Daily pipeline**: Running through 2026-08-02, logs in `data/logs/daily_*.log`
- **Odds capture**: Active across 8 sports (MLB, NBA, WNBA, NFL, esports, soccer,
  tennis, KBO, NPB)
- **Sports modeled**: 13 sports across 3 tiers:
  - **Production (Main)**: MLB, WNBA, Soccer (override) — real-sized calls
  - **Flat (diagnostic)**: NBA, NFL — zero-unit, all candidates logged
  - **Research**: Esports (5 titles), Tennis, KBO, NPB — zero-unit, with Gated
    subset

### Open P0 Blockers (from `docs/PROJECT_STATUS.md` and `DEBUG.md` section 5)

1. Execution tickets not bound to exact qualified ledger rows
2. Ledger mutation and audit append not atomic (ledger writes before audit)
3. Artifact qualification and quote timestamp validity not enforced at
   classification/pricing step
4. `config/model.yaml` references `market-residual-v1.json` which doesn't exist
5. `mlb-spread-baseline-v1.json` used for both spread AND total research
6. Dashboard startup uses `pkill -f` (should use PID-file)
7. Two artifact hashes mismatched (`nba-spread-baseline-v1.json`,
   `nfl-spread-baseline-v1.json`)
8. Exposure checked outside append transaction — concurrent writers can approve
   from same stale snapshot
9. The Odds API error path can leak the API key in URLs
10. Polymarket discovery silently truncates at 50 events with false
    `timestamp_valid=true`
