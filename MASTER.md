# MASTER.md — Unified Project Reference (In-Depth)

**Generated**: 2026-08-02 | **Last verified against live code**: 2026-08-02 (later) |
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

---

## Quick Status

| Metric | Value |
|---|---|
| Tests | **638 pass, 0 fail** (re-verified 2026-08-02 later; was 624 at this file's generation) |
| Ruff | 118 findings in `src/ tests/` (documented scope, unchanged); 165 including `dashboard_server.py` (its own separate, larger baseline) |
| Audit chain | 43,304 events in `data/events.jsonl`, zero chain breaks, zero hash mismatches (as of generation; not re-verified) |
| Git | Single `main` branch; **143 modified/new files uncommitted** since `d0568ac` (grew further after this file was generated) |
| Last pushed commit | `d0568ac` — synced with `origin/main` |
| CI | `.github/workflows/ci.yml` — ruff + pytest on every push/PR, Python 3.12, ubuntu-latest |
| Dashboard | Live at `127.0.0.1:8765`, launchd-managed, per-session token-based auth |
| Daily pipeline | Running through 2026-08-02; `run_daily.sh` does settle → ingest → daily forecast; locked via `daily_lock.py` |
| BBO capture | 8 sports active in `data/odds/` (MLB, WNBA, esports, KBO, NPB, soccer, tennis) |
| Sports modeled | 14 sports across 4 ledger tiers, **plus** 12 real per-model ledgers under `data/model_ledgers/` (new architecture, see Part 0) |
| Release status | **BLOCKED** — see P0 defects below |

### Active Model Versions

| Sport | Artifact | Status | Locked-holdout | Real units? |
|---|---|---|---|---|
| MLB moneyline | `mlb-elo-trend-lr-v7` | shadow_qualified (override) | 58.0%, +27.5u/-110 | Yes — Main ledger, 1.0-2.0U |
| MLB spread | `measured-edge-margin-v2` | active_research | — | No — Flat only, zero-unit |
| MLB totals | `measured-edge-totals-v2` | active_research | — | No — Flat only, zero-unit |
| NBA moneyline | `nba-elo-trend-lr-v4` | shadow_qualified | 73.66%, 88.2% called | No — Flat only |
| WNBA moneyline | `wnba-elo-trend-lr-v4` | shadow_qualified | 67.48%, 100% called | Yes — Main ledger |
| NFL moneyline | `nfl-elo-trend-lr-v4` | shadow_qualified (offseason) | 71.26%, 71.3% called | No — Flat only |
| Soccer | `soccer-poisson-dc-v1` | shadow_qualified (override) | 62.5%, +90.4u | Yes — Main+Flat override; execution blocked by missing walk-forward artifact |
| LOL | `lol-tiered-elo-v5` | shadow_qualified (override) | — | No — Gated Research |
| CS2 | `cs2-tiered-elo-v5` | shadow_qualified (override) | — | No — Gated Research |
| Dota 2 | `dota2-tiered-elo-v5` | shadow_qualified (override) | — | No — Gated Research |
| Valorant | `valorant-tiered-elo-v5` | shadow_qualified (override) | — | No — Gated Research |
| Rainbow Six | `rainbow_six-tiered-elo-v5` | research | — | No — Research only |
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
against numbers already confirmed earlier the same session):

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

---

# PART 1: EVERY KNOWN BUG — Open and Fixed

## 🔴 P0 — Capital Safety (release blockers)

### P0-1: Execution tickets not bound to exact ledger rows
**File**: `dashboard_server.py`, `polymarket_execute.py`
**What's wrong**: When submitting a real-money order, the server does not recompute market, side, action, price, quantity, and cost server-side against the exact qualified ledger row. A mismatched ticket — whether from UI drift, a stale cached preview, or a malicious caller — could be executed against the wrong row with the wrong economics.
**Impact**: Real money at risk. This is the single most important unfixed bug in the project.
**Status (2026-08-02, later) — partially resolved, re-verified directly against live code**: `pick_id`, `market_slug` (from the row's own rationale), and `estimated_cost_usd` (recomputed server-side, never trusted from the ticket) were already bound before this session. This session added independent, live-fetched verification of **token_side** (must match the row's real recorded selection, checked against a fresh Polymarket quote — moneyline only, since it's the one market type with an unambiguous two-team side mapping), **pregame status** (event must not have started), and **quote freshness** (refuses a quote older than 5 minutes). `action` (buy/sell) still isn't independently bound to anything about the row. `size_shares`/price still aren't checked against a live reference directly — they're bounded *indirectly*: cost (`price × size_shares`) is recomputed and capped against the row's own real authorized `maximum_cost_usd`, so an inflated quantity or price can't exceed what that specific pick was actually sized for, even without a standalone quantity check. Decision (operator's call): leave as-is — the cost cap already provides the load-bearing protection for quantity/price; a separate quantity-matching check would be redundant given that. **Not yet done for spread/total/btts markets** — those still rely only on the market_slug-from-rationale binding, since a genuine line/selection-to-side resolver for them doesn't exist yet.
**Source**: PROJECT_STATUS.md, TODO.md P0, DEBUG.md §5

### P0-2: Ledger mutation and audit append not atomic
**File**: `ledger.py:500-507,635-648,743-770,795-796`
**What's wrong**: Ledger writes (create, settle, void, remove) commit BEFORE the corresponding audit event is appended. A crash or `AuditLockTimeout` between these two operations leaves a mutated row with no audit record. Some retry paths return early once the ledger already reflects the mutation, so retry does not necessarily repair the gap.
**Evidence**: No failure-injection tests exist. Verified by reading the actual code paths — ledger mutation occurs first, audit append second, with no rollback mechanism.
**Correction (2026-08-02, later) — this claim is backwards, re-verified directly against live code**: `ledger.py`'s own module docstring and `_append_record`'s actual code both show the *opposite* ordering — the audit event is appended **before** the ledger row is written, specifically so a crash between the two leaves a detectable orphaned audit event (an audit entry with no matching row) rather than a real mutation with zero audit trail. This was evidently already fixed before this file was generated; whatever produced this entry read stale code or a different commit. The underlying *concern* (no true cross-file atomicity, since ledger and audit are separate files with separate locks) is still real and still undocumented as fully solved — a genuine transaction log doesn't exist — but the specific failure mode described here (silent mutation with no audit record) is not how the current code behaves. No failure-injection tests still exist either way; that part of the entry stands.
**Source**: PROJECT_STATUS.md, TODO.md P0, DEBUG.md §5

### P0-3: Artifact qualification and quote timestamp not enforced
**File**: `learned_forward.py:304-330`, `cli.py:756-760`
**What's wrong**: `learned_forward.py` labels a confidence-threshold call `QUALIFIED_SHADOW_CALL` even when the artifact's `qualified` field is `false`. The CLI routes `calls` (not `qualified_calls`) before later config/state gating. Separately, quote snapshots with `timestamp_valid=false` are not rejected — stale/untrustworthy price data can feed into the pricing and classification pipeline.
**Impact**: The system tells you a pick is "qualified" when the underlying model artifact says it isn't. This label reaches the dashboard, ledgers, and (if execution gates are bypassed) real orders.
**RESOLVED as a deliberate operator decision, 2026-08-02 (later) — not a bug, upheld as intentional.** Operator directive, verbatim: *"remove all promotion qualification, its up to me"* / *"no restrictions... up to my discretion."* `lifecycle.py::can_create_qualified_call` no longer gates on model_state (RESEARCH/SHADOW_CANDIDATE/SHADOW_QUALIFIED/DEGRADED all now equally produce a real call — RETIRED/SUSPENDED remain hard stops as genuine "off" states, kept intentionally distinct). `PolymarketExecutor.execute()` no longer requires `QUALIFIED_SHADOW_CALL`/a manual override. This is the operator's explicit, informed choice to move authority from the classification system to themselves per-pick, using the evidence the dashboard/ledgers now surface (see Part 0) — not an oversight to fix. The `timestamp_valid` half of this entry (quote snapshots not rejected) was **not** addressed and remains a real, separate, still-open gap — kept on the list as such.
**Source**: PROJECT_STATUS.md, TODO.md P0, DEBUG.md §6

### P0-4: Missing artifact config reference
**File**: `config/model.yaml` → `models.market_residual.artifact: config/models/market-residual-v1.json`
**What's wrong**: The config references a file that does not exist on disk.
**Severity correction (2026-08-02, later)**: confirmed the file is genuinely missing. Also confirmed `config["models"]["market_residual"]["artifact"]` is **never read by any code** — grepped the whole `src/model_prediction` tree and `dashboard_server.py`; `market_residual` only appears as an import of `MarketResidualModel`/`ResidualTrainingRow` into `cli.py` (for the `train-residual` command) and as a config-loader passthrough key, never as a consumer of this specific path. This is real, but it is dead/orphaned config, not a live capital-safety path today. **Operator directive: fix, don't remove — create the real artifact and wire the config reference into an actual consumer, with tests.** Queued, not yet done (see Part 0's "what did NOT ship").
**Source**: PROJECT_STATUS.md, TODO.md P1, CHECKLIST.md

### P0-5: MLB spread artifact reused for totals
**File**: `config/model.yaml` → `models.MLB`
**What's wrong**: Both `spread_research_artifact` and `total_research_artifact` point to `config/models/mlb-spread-baseline-v1.json`. The same artifact file serves two different market types. MLB totals research is running on a spread artifact — the coefficients, calibration, and thresholds were fit for spread outcomes, not totals.
**Severity correction (2026-08-02, later)**: confirmed the duplicate reference is real. Also confirmed `spread_research_artifact`/`total_research_artifact` are **never read by any code** in `src/model_prediction` or `dashboard_server.py` — MLB's actual live spread/totals system is the separate, working Measured Edge margin/totals v1/v2 pipeline (`measured-edge-margin-v2.json`/`measured-edge-totals-v2.json`), which doesn't go through these config keys at all. So this is dead config left over from an earlier design, not something actively producing wrong totals predictions today. **Operator directive: fix, don't remove — make these real and wire them in.** Likely candidate: these may be meant as the "frozen baseline" model identities `model_ledger.py` already reserves (`nba-spread-baseline`, `nfl-spread-baseline`, and presumably an `mlb-spread-baseline` equivalent) per the Part 0 "keep every model, incumbents vs. baselines" framing — needs a real decision on what these baseline models are before building them, not just a mechanical hash/pointer fix. Queued, not yet done.
**Source**: PROJECT_STATUS.md, TODO.md P1, CHECKLIST.md

### P0-6: Mismatched artifact hashes
**Files**: `config/models/nba-spread-baseline-v1.json`, `config/models/nfl-spread-baseline-v1.json`
**What's wrong**: The canonical `artifact_hash` embedded in these files does not match the SHA-256 of the file's own content-minus-hash. The artifacts' contents have drifted from whatever produced the stored hash. This invalidates any validation evidence tied to the hash.
**Severity correction (2026-08-02, later)**: both mismatches confirmed real (recomputed both hashes directly). Also confirmed: the only reference to these filenames anywhere in `src/model_prediction` is inside `model_ledger.py`'s `MODEL_ID_BY_LEAGUE_AND_MARKET` mapping (a model-identity *string*, `"nba-spread-baseline"`/`"nfl-spread-baseline"` — not a file load). Neither JSON artifact is actually loaded/read by any code today, so nothing currently trusts the broken hash at runtime. Real data-hygiene issue, not a live capital-safety bug. **Operator directive: fix, don't remove — test and wire it in**, same as P0-4/P0-5 (likely the same underlying "baseline model" work). Queued, not yet done.
**Source**: PROJECT_STATUS.md, TODO.md P1, CHECKLIST.md

---

## 🟠 P1 — Data Correctness and Routing

### P1-1: Non-atomic exposure check
**File**: `ledger.py`, `cli.py`
**What's wrong**: Exposure is read from the ledger, checked against caps, and the new row is appended — all outside a single transaction. Two concurrent processes can both read the same stale exposure snapshot and both approve, exceeding caps. A single-process daily lock (`daily_lock.py`) mitigates this for the scheduled pipeline but does not fix the general case.
**Source**: PROJECT_STATUS.md, TODO.md P1

### P1-2: API key leaked in error URLs
**File**: `data_sources/the_odds_api.py`
**What's wrong**: The Odds API error path can return a URL containing the `THE_ODDS_API_KEY` query parameter. If that error is logged or surfaced in the dashboard, the API key is exposed.
**Source**: PROJECT_STATUS.md, TODO.md P2

### P1-3: Polymarket discovery silently truncates
**File**: `data_sources/polymarket_us.py`
**What's wrong**: The Polymarket API discovery endpoint is paginated but the client does not paginate — it fetches one page only (50 events). If more than 50 events exist, the extras are silently dropped. Additionally, the aggregate `timestamp_valid` is hardcoded to `true` — a provider failure (timeout, 500, empty response) is indistinguishable from a genuinely empty slate.
**Source**: PROJECT_STATUS.md, TODO.md P2

### P1-4: Future timestamps pass freshness checks
**File**: `lifecycle.py`, `eligibility.py`
**What's wrong**: The freshness check compares `current - observed_at_utc` against `maximum_age_hours`. If `observed_at_utc` is in the future, the subtraction produces a negative number which passes the "not too old" check. A clock-skewed or misconfigured data source could inject future-dated data and it would be accepted.
**Source**: PROJECT_STATUS.md, TODO.md P2

### P1-5: Unvalidated rows poison feature-ingest dedup
**File**: `ingest.py`
**What's wrong**: A row's event ID is added to the dedup set before the row passes validation. If the row is rejected later (bad score, malformed fields), its event ID stays in dedup — permanently blocking the real/good version of that game from ever being ingested.
**Source**: TODO.md P2

### P1-6: Silently swallowed exceptions
**File**: `esports.py`, `international_baseball.py`, various source-refresh paths
**What's wrong**: Several narrow exception catches silently discard failures without logging. Esports match-refresh errors, KBO/NPB source-read failures, and data-source refresh exceptions can all vanish with no trace. The daily pipeline completes "successfully" while silently missing data.
**Source**: TODO.md P2

### P1-7: Ban mechanism non-functional for registry-free sports
**Files**: `bans.py`, `entities.py`
**What's wrong**: `TeamBanList.check()` resolves through `EntityRegistry.resolve()`, which requires the team to be in the canonical entity registry. Esports, soccer, tennis, KBO, and NPB deliberately use name-based `PlaceholderTeam` objects (registry-free by design — `registry.resolve(League.LOL, "T1")` raises `EntityResolutionError`). If the operator ever bans a team in any of these sports, the ban silently has zero effect.
**Evidence**: Live-verified — `registry.resolve(League.LOL, "T1")` raises. Config stubs for these sports were removed 2026-08-02 to stop the false impression that banning works, but the real gap remains. A proper fix requires a genuinely separate, registry-free ban mechanism for these sports — a non-trivial cross-cutting change touching eligibility logic in 4+ sports.
**Source**: DEBUG.md §2026-08-02 ban-list

### P1-8: CLI reports wrong model status
**Files**: `models/registry.py:136-200`, `cli.py:1739-1740`
**What's wrong**: The `model-prediction models` command prints static registry specs ("research") for Soccer, esports, KBO, and NPB, even though Soccer is now `shadow_qualified` (operator override) and esports/KBO/NPB have `qualification_override: true` in the live config. The CLI does not read config-derived status — it reports a hardcoded registry table.
**Source**: DEBUG.md §2703-2707

### P1-9: `/api/scan` route broken
**File**: `dashboard_server.py`
**What's wrong**: The route calls `capture_snapshots(s, _today())` — a function that does not exist in `polymarket_us.py`. The real function is `capture_slate_snapshots(client, events_by_league, data_root, game_date)` — different name, different signature. Every call raises `ImportError` → 500. The route also hardcodes a sport fallback of `["mlb","nba","wnba"]` that omits NFL and all esports/KBO/NPB.
**Impact**: Currently dead (no frontend calls it), but would break immediately if wired up.
**Source**: ENGINEERING_ROADMAP.md §1

### P1-10: NBA/NFL spread/total zero snapshots
**What**: Offseason — no games, no Polymarket markets. Will resolve when seasons start.
**Source**: CHECKLIST.md

### P1-11: WNBA total baseline suspicious
**What**: 78.3% baseline accuracy for WNBA totals — suspiciously high. Needs formal investigation with more data.
**Source**: CHECKLIST.md

### P1-12: MLB ingest pipeline intermittently misses games
**What**: ESPN API returns completed game data, but the Ingestor sometimes doesn't process it. Intermittent, hard to reproduce.
**Source**: CHECKLIST.md

### P1-13: Validation report unreproducible
**File**: `outputs/latest/learned-model-validation.json`
**What's wrong**: The file names an old worktree path, points MLB at v5 (current is v7), and predates current KBO/NPB artifacts. It is not a reproducible release report from this checkout. Anyone running the validation command from a fresh clone would get different output.
**Source**: DEBUG.md §2701-2703

### P1-14: WNBA availability fails open
**File**: `features/player_availability.py`
**What's wrong**: When WNBA availability data is missing, conflicting, or malformed, the system fails open — model opinions are still shown and logged. Fail-closed is the correct behavior for a real-money-adjacent path: if you can't determine availability, you should refuse to generate a call, not guess.
**Source**: DEBUG.md repair order step 5

### P1-15: KBO/NPB half-settlement P&L incorrect
**File**: `international_baseball.py`, `cli.py`
**What's wrong**: KBO/NPB ties settle at `$0.50` on the dollar (half-payout). The settlement P&L computation didn't use the correct half-settlement economics — it used ordinary moneyline push math.
**Status**: Marked as fixed in TODO.md P1 checks but DEBUG.md repair order step 8 still lists "correct half-settlement P&L" as an open item.
**Source**: DEBUG.md repair order step 8

### P1-16: 18 stale `.bak` data files
**Files**: `data/gated_research/*.bak-v1`, `*.bak-v3`, `data/research/*.bak-v3`, `data/*.backup-before-*`
**What**: Backup files from ledger cleanup operations (July 24-26) still sitting in the data directories. Clutter, not a bug, but they're tracked by git and add noise to every `git status`.
**Source**: Git status 2026-08-02

---

## 🟡 P2 — Architecture and Maintainability

### P2-1: `cli.py` — 3,943 lines, 8.3% coverage, zero dedicated tests
**File**: `src/model_prediction/cli.py`
**What's wrong**: The largest file in the repo has near-zero behavioral test coverage. There is no `tests/test_cli.py` (DEBUG.md §2713 lists it but that reference is aspirational — the file doesn't exist). argparse wiring, default-date logic (`eastern_today()`), command dispatch, and all 25+ subcommands are only exercised indirectly by whatever other tests happen to call CLI functions.
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

### P2-9: 82 dirty files uncommitted
**What**: All session 2026-08-02 work — model ledger, player availability, dashboard auth, ban-list cleanup, documentation — plus daily pipeline data (ledgers, snapshots, esports data) is uncommitted. GitHub shows July 31 state. Sub-agents in worktrees see stale committed code. CI runs on stale code.
**Source**: Git status 2026-08-02

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

---

# PART 2: COMPLETE TODO — Everything That Must Be Done

## 🔴 Priority 0 — Capital Safety

- [x] **P0-1** (partial, 2026-08-02 later): token_side/pregame/quote-freshness now independently verified for moneyline. Still open: same for spread/total/btts; no standalone quantity check (mitigated by the existing cost cap, operator call to leave as-is)
- [x] **P0-2** (2026-08-02 later): re-verified — was already backwards in this file; real code is audit-before-ledger-write. Still open: no failure-injection tests; no true cross-file transaction log
- [x] **P0-3** (RESOLVED as deliberate operator decision, 2026-08-02 later): classification no longer gates routing or execution — see Part 0. Not a bug.
- [ ] **P0-3b**: Enforce `timestamp_valid` — reject candidates from snapshots with `timestamp_valid=false` (still open, unrelated to P0-3's resolution)
- [ ] **P0-4**: Fix `market-residual-v1.json` config reference — confirmed dead/unread by any code (2026-08-02 later). Operator directive: fix, don't remove — create the real artifact and wire it into an actual consumer, with tests. Queued.
- [ ] **P0-5**: Point `total_research_artifact` at a real totals artifact, not `mlb-spread-baseline-v1.json` — confirmed dead/unread by any code (2026-08-02 later); live MLB totals actually runs through the separate Measured Edge pipeline. Operator directive: fix, don't remove — likely the `*-spread-baseline` model identities `model_ledger.py` already reserves. Queued.
- [ ] **P0-6**: Repair canonical hashes for `nba-spread-baseline-v1.json` and `nfl-spread-baseline-v1.json` — confirmed real, confirmed neither file is loaded by any code today (2026-08-02 later). Operator directive: fix, don't remove — test and wire in, same underlying work as P0-4/P0-5. Queued.

## 🟠 Priority 1 — Data Integrity

- [ ] Atomic exposure-check-plus-append across processes
- [ ] Preserve paired-ledger consistency (research ↔ gated)
- [ ] Redact The Odds API key from all logged/returned errors
- [ ] Reject future `observed_at_utc` values (negative age → fail freshness)
- [ ] Paginate Polymarket discovery; distinguish provider failure from empty slate; never hardcode `timestamp_valid=true`
- [ ] Validate rows before adding event ID to feature-ingest dedup state
- [ ] Surface narrow exception catches: esports, KBO/NPB, source-refresh failures must log, not silently discard
- [ ] Build registry-free ban mechanism for esports/soccer/tennis/KBO/NPB (not coupled to `EntityRegistry.resolve`)
- [ ] Fix `model-prediction models` CLI — report config-derived status, not static registry specs
- [ ] Fix or delete `/api/scan` route — either call `capture_slate_snapshots` with real client+events or delete
- [ ] Fix 4 dashboard order-preview tests — pin unit value or use sizes within current `$5.00` cap
- [ ] Reproduce `outputs/latest/learned-model-validation.json` from one stable green checkout
- [ ] Make WNBA availability fail closed — test malformed/conflicting source combinations
- [ ] Verify KBO/NPB half-settlement P&L correctness (DEBUG.md repair order item 8 not clearly resolved)
- [ ] Clean up 18 stale `.bak` data files in `data/` directories

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
- [ ] **Commit and push working tree** — 82 modified + ~40 new files uncommitted since July 31

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
5. Build coherent score-distribution model: derive margin, total, spread, and moneyline from ONE distribution (not disconnected binary classifiers that can imply contradictory forecasts)
6. Revisit `pitcher_era_gap` replacement — try interaction term instead of additive alongside Elo (the standalone correlation was promising; the ablation was negative)
7. **Already done**: Starter ERA zero-shrinkage for small-innings samples, bullpen hardcoded-neutral fixed, park factors recomputed empirically, weather feature now wired (was completely dead), rehab-assignment marker, same-day transaction PIT safety
8. **Formally rejected**: `starter_era_gap` (removal improves every metric), `starting_pitcher_fip` (84% coverage, zero effect, collinear), `trailing_home_win_rate_30d` (fell from 60.87% to 60.42%)

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

### Tennis (WTA-only, surface-blended Elo)
1. Extend beyond WTA — ATP market detection if/when Polymarket lists it
2. **Constraints**: Polymarket US has no ATP market; ESPN has no ITF scoreboard; Sackmann CSV historical data covers WTA/ATP but only WTA has current Polymarket contracts
3. **Already done**: Tennis zero-match-history bug fixed (schema incompatibility); stale cache false-positive fixed (wrong parser); 1,878 cached files verified

### Esports (5 titles, all v5 Platt-scaled Elo)
1. Run formal omission study on `neutral_elo_rating_difference` for all 5 titles
2. Monitor Gated Research performance under tightened confidence gates (0.03-0.05 per title)
3. **Already done**: v4→v5 rebuild (K by min Brier, threshold by `units_at_minus_110`); Gated Research gates tightened; Dota2/Valorant swap fixed; auto-refresh wired; Rainbow Six added
4. **Gap**: K/threshold optimized but formal omission study never run
5. **Gap**: COD, Rocket League, Overwatch confirmed to exist in Polymarket taxonomy but have no bo3.gg data source — not buildable

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
- ⚠️ `DEBUG.md` already has its own later entries (2026-08-02, later, "Per-model ledger architecture") this file's original generation predates — that's the primary source for Part 0's claims

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
.venv/bin/python - <<'PY'
import hashlib, json
from pathlib import Path
for path in sorted(Path("config/models").glob("*.json")):
    raw = json.loads(path.read_text())
    key = "artifact_hash" if "artifact_hash" in raw else "model_hash"
    canonical = {n: v for n, v in raw.items() if n != key}
    computed = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode()).hexdigest()
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
