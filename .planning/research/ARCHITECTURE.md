# Architecture Patterns

**Domain:** Live multi-sport prediction-market trading system — closing gaps in settlement, market-quote archival linkage, and accuracy/profitability measurement without touching the dual-track promotion-gate safety architecture
**Researched:** 2026-09-22
**Confidence:** HIGH (first-party — grounded directly in the live codebase: `cli/settle.py`, `domain.py`, `data_sources/mlb_market_odds.py`, `data_sources/polymarket_us.py`, `international_baseball.py`, `rebuild/shadow_ledger.py`, `production_store.py`, `model_promotion.py`, `docs/ARCHITECTURE.md`, `docs/rebuild/ARCHITECTURE.md`, `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/CONCERNS.md`)

## Recommended Architecture

All three sub-problems share one governing constraint, verified in the actual code: this system already has the *shape* of the solution for all three gaps, built once for MLB or for soccer, and simply not generalized. The right architecture is **extend the existing pattern to every sport, in place** — not a new subsystem, not a parallel store, not a rewrite. Concretely:

```text
(a) KBO/NPB postponement          (b) archive-to-contract linkage        (c) accuracy/profitability
    settlement gap                     beyond MLB                            tracking layer
┌─────────────────────────┐   ┌──────────────────────────────────┐   ┌────────────────────────────┐
│ cli/settle.py            │   │ data_sources/mlb_market_odds.py   │   │ ledger.py (SQLite canonical)│
│ _settle_international_   │   │ MarketOddsSnapshotStore           │   │ already carries closing_*,  │
│ baseball_pick()          │   │ (hash-bound, MLB-only)            │   │ serving_probability,        │
│                           │   │            vs.                    │   │ market_probability_at_      │
│ MISSING the branch        │   │ data_sources/polymarket_us.py     │   │ decision per pick           │
│ soccer already has        │   │ PolymarketSnapshotStore           │   │                              │
│ (STATUS_POSTPONED →       │   │ (plain JSONL, no hash/record-id,  │   │ MISSING: persisted rollup   │
│ ledger.void())            │   │ used by all 13 other sports)      │   │ layer (CLV%, calibration-   │
│                           │   │                                    │   │ over-time) — currently only │
│ international_baseball.py │   │ Fix: lift MLB's canonical-hash +  │   │ computable ad hoc           │
│ already HAS the signal    │   │ load_verified_* contract into a   │   │                              │
│ (international_baseball_  │   │ sport-generic module; write it    │   │ Fix: additive columns on    │
│ unplayed_index(), never   │   │ into the SAME per-sport-date      │   │ ledger_records + DuckDB     │
│ called from settle.py)    │   │ JSONL files PolymarketSnapshot-   │   │ rolling-window views, NOT   │
│                           │   │ Store already writes               │   │ a new metrics DB            │
└─────────────────────────┘   └──────────────────────────────────┘   └────────────────────────────┘
```

None of the three touches `production_registry.py`, `model_promotion.py`, `champion_challenger.py`, or anything under `rebuild/` — the promotion gate's evidence inputs (walk-forward Brier/log-loss/locked-holdout accuracy, per `docs/ARCHITECTURE.md`) stay exactly as they are. All three fixes are **additive, sport-scoped, and downstream of settlement** — they cannot destabilize a live model.

### Component Boundaries (as they exist today, and where each fix lands)

| Component | Responsibility today | What changes | Communicates With |
|-----------|----------------------|---------------|--------------------|
| `cli/settle.py::_settle_international_baseball_pick` | Grades KBO/NPB picks; returns `None` (= still pending) for both "not played yet" and "will never be played" | Add a postponement branch mirroring the existing soccer branch (lines ~165–181 of the same file) | `international_baseball.py`, `ledger.void()` |
| `international_baseball.py::international_baseball_unplayed_index` | Already returns `(game_date, away_team_id, home_team_id)` for every scheduled-but-scoreless official-source row | Called from settlement, not just from forecast-slate building (its current only caller) | Official KBO/NPB source client |
| `data_sources/mlb_market_odds.py::MarketOddsSnapshotStore` | MLB-only append-only JSONL archive with SHA256 canonical hash, `snapshot_record_id`, and `load_verified_mlb_market_snapshot()` authentication | Generalize into a sport-parameterized module (new `market_archive.py` or extend in place) | `data_sources/polymarket_us.py::PolymarketSnapshotStore` |
| `data_sources/polymarket_us.py::PolymarketSnapshotStore` | Generic per-sport-date JSONL append/scan (`data/odds/{sport}/{date}/polymarket_snapshots.jsonl`), used by all 13 non-MLB sports, **no hash binding, no record-id, no authentication** | Add the same `snapshot_hash`/`snapshot_record_id` fields at write time + a `load_verified_snapshot()` reader; storage location unchanged | `cli/settle.py` closing-quote lookups |
| `domain.py::PickRequest` | Already has `market_snapshot_hash`, `market_snapshot_archive_path`, `market_snapshot_record_id`, `market_quote_provenance` fields — populated only for MLB today | No schema change; just populate these fields for the other 13 sports once (b) lands | `ledger.py` |
| `ledger.py` (canonical SQLite `ledger_records`) | Already stores `closing_line`, `closing_american_odds`/`closing_raw_probability`, `serving_probability`, `market_probability_at_decision` per settled pick | Add a small number of additive columns (`market_prob_at_close` if not already covered by `closing_raw_probability`, `clv`, `brier_contribution`) — no new table | Dashboard, DuckDB rollups |
| New: `measurement.py` (or similar) | Does not exist yet | Pure-Python CLV/Brier/calibration computation over ledger rows, following the existing shadow-feature-module pattern (explicit provenance, fail-closed on missing closing quote) | Reads `ledger.py`, writes rollups; never writes promotion-relevant state |
| `production_registry.py`, `model_promotion.py`, `champion_challenger.py` | Promotion gate, fail-closed, explicit `--approved-by` | **Untouched.** New tracking data is read-only input for a human decision, never an automatic trigger | Not touched by any of (a)/(b)/(c) |

## (a) KBO/NPB postponement-handling gap — close it with the pattern soccer already uses

**Root cause, verified in code:** `_settle_international_baseball_pick()` (`cli/settle.py`) calls `find_international_baseball_result()`, which returns `None` whenever no score is found — for a genuinely-future game AND for a postponed-forever game, indistinguishably. The function has no branch analogous to the soccer settlement path's `status in {"STATUS_POSTPONED", "STATUS_CANCELED"}` check that calls `ledger.void()`. This is a pure gap, not a missing capability: `international_baseball.py::international_baseball_unplayed_index(league, year)` already exists, is already tested/verified live (2026-08-31 per its docstring — postponed KBO rows keep their calendar row instead of disappearing), and is already parsed by `parse_kbo_unplayed_rows` / `parse_npb_unplayed_calendar`. It is simply never called from `settle.py` — its only current caller is the forecast-slate builder.

**Recommended fix (in order of what already exists to reuse):**
1. In `_settle_international_baseball_pick`, when `find_international_baseball_result` returns `None`, call `international_baseball_unplayed_index(league, year)` for the row's `game_date`/team pair. If the pair is present in the unplayed index **and** `event_start_utc` is more than a threshold (recommend 48–72h, generous enough to cover same-day doubleheader makeups, which KBO/NPB do use) in the past, treat it as postponed.
2. Thread the existing `args.void_postponed` flag (already defined in `parser.py`, already plumbed through `_settle_all_unsettled`, already defaulted `True` in the unattended daily run per `cli/daily.py`'s explicit comment about ESPN reschedules) into this branch, calling `ledger.void(pick_id, "kbo_npb_postponed_unplayed")` exactly like the soccer branch calls `ledger.void(row["pick_id"], f"event {status.lower()}")`.
3. Do **not** try to auto-resolve via a makeup-date result. A postponed KBO/NPB game is typically replayed on a different `game_date` as effectively a new scheduled contract; the existing team+date matching in `find_international_baseball_result` will simply never find the original date's game. Voiding the original pick (rather than silently re-pointing it at a different day's outcome) matches this codebase's stated philosophy of failing closed on ambiguity rather than guessing — and matches how the soccer path already treats postponement.
4. This directly resolves the 44 stuck-open KBO/NPB rows (`.planning/codebase/CONCERNS.md`) going forward; the existing backlog can be cleared in one pass with the same logic run once manually (`settle --void-postponed --all-unsettled`), since `void_postponed` is already a real CLI flag.

**Why this is architecturally safe:** it only touches the settlement path for two leagues that are Research/Gated-Research tier (zero real units), reuses an already-tested data-source function, and reuses the already-tested `ledger.void()` contract (append-only mutation, audit-logged, never a raw delete) — it introduces no new invariant and no new failure mode beyond what soccer's settlement already carries in production today.

## (b) Extending archive-to-contract linkage beyond MLB

**Root cause, verified in code:** MLB moneyline's "194 verified events with exact quotes/fees/provider-IDs" linkage is not a generic capability of the system — it is a bespoke module, `data_sources/mlb_market_odds.py`, built only for MLB:

- `MarketOddsSnapshotStore.append()` writes a canonical SHA256 digest (`_snapshot()` → `canonical_mlb_market_snapshot_hash()`) over the full quote payload (event id, provider, observed time, every market/side/line/odds, and the raw provider response) and binds it via `snapshot_record_id == snapshot_hash`.
- `load_verified_mlb_market_snapshot()` is the read-side authentication: given an archive path + record id + expected hash, it re-locates the exact archived line, **recomputes** the hash, and refuses to return anything if the recomputed hash, the stored hash, the archive path, or the quote's own `selection`/`line`/`american_odds` don't all agree. This is what makes MLB's linkage "exact" — a decision-time record can be cryptographically proven to correspond to one specific archived quote, not just approximately matched.

The 13 other sports (WNBA, NFL, NCAAF, CFB, tennis, soccer, LOL/CS2/DOTA2/VALORANT/RAINBOW_SIX, KBO, NPB) settle their closing quotes through a **different, much thinner** store: `PolymarketSnapshotStore` (`data_sources/polymarket_us.py`). It appends plain JSONL to `data/odds/{sport}/{date}/polymarket_snapshots.jsonl` with no canonical hash, no `snapshot_record_id`, and no authentication function — `closing_snapshot()` just scans and returns the latest matching object with no proof the record hasn't been altered or misidentified. `domain.py::PickRequest` already *has* the audit-trail fields for this (`market_snapshot_hash`, `market_snapshot_archive_path`, `market_snapshot_record_id`, `market_quote_provenance`, `market_quote_reconstructed`) — they are simply left `None` for every non-MLB sport today, because nothing populates them.

**Recommended fix — generalize, don't duplicate:**
1. Extract the three MLB-specific pieces that are actually sport-agnostic logic into a shared module (e.g. `data_sources/market_archive.py`): the canonical-hash function (parameterize the fields it hashes — it's already just "the whole snapshot dict, sorted keys"), the write-time `snapshot_record_id = snapshot_hash` binding, and `load_verified_snapshot()` (rename from `load_verified_mlb_market_snapshot`, generalize `approved_roots`/`market_type` handling — the logic itself has zero MLB-specific assumptions once you look at it; MLB is only baked into the name and the surrounding `MLBGameOdds`/`MarketSideQuote` dataclasses).
2. Add the same hash + record-id fields to `PolymarketSnapshotStore.append()`'s payload at write time (it already receives a full snapshot dict before writing — this is a few added keys, not a new store or new file location). **Keep the existing storage location** (`data/odds/{sport}/{date}/polymarket_snapshots.jsonl`) — there is no reason to migrate 13 sports' existing snapshot history to a new path; the fix is making the *existing* files self-authenticating going forward.
3. Wire `load_verified_snapshot()` into `cli/settle.py`'s non-MLB closing-quote path (currently `_closing_probability_for_moneyline_pick`, which already reads from `PolymarketSnapshotStore.for_sport_date` but does no hash verification) and into forecast-time decision-quote capture, so `PickRequest.market_snapshot_hash` etc. get populated for every sport the same way MLB's already are.
4. Fees and provider IDs: MLB's "exact quotes/fees" linkage computes fees at *evaluation* time from the archived ask price using the published Polymarket US fee schedule (`docs/INCUMBENT_COMPARISON_2026-09-09.md` cites the taker coefficient formula) — fees are not stored per-quote, they're derived from the verified price. The same derivation applies unchanged to any sport once its quotes are hash-verified; no new fee-tracking machinery is needed per sport.
5. Do this sport-by-sport, starting with whichever markets are closest to a promotion decision (per `docs/ROADMAP.md`'s stated order: extend the 194-event MLB linkage "to other market families" before attempting new paired-economics comparisons) rather than attempting all 13 at once — the risk of a subtle team-alias or market-slug mismatch (the same class of bug this codebase has hit repeatedly, e.g. soccer's ~45-competition team-name collision history noted in `settle.py`'s own comments) is per-sport, not systemic.

**Why this is architecturally safe:** it is a pure extension of an existing, already-proven-correct pattern into existing storage; it changes no schema on the ledger side (the fields already exist, unused) and does not touch settlement's terminal-state logic, only the evidentiary metadata attached to a quote.

**Note on `rebuild/shadow_ledger.py`'s `closing_prices` table:** this table is schema-defined but has zero insert/query methods (`.planning/codebase/CONCERNS.md`), and its shape (`market_id`, `side_id`, `line`, `closing_price`, `observed_at_utc`, `quote_type`, `seconds_to_start`) is a close cousin of what's needed here. **Do not use it for this fix** — it lives in the isolated `rebuild/` shadow tree (`docs/rebuild/ARCHITECTURE.md`'s no-production-write boundary), which cannot be the system of record for a linkage that live settlement and live promotion evidence depend on. It is, however, useful *evidence* that this schema shape has already been designed once independently and converged on nearly the same fields — a second independent confirmation that (b)'s recommended fields (hash, record id, market/side/line identity, observed time) are the right ones.

## (c) Structuring ongoing accuracy/profitability measurement

**What already exists, verified in code:** the canonical SQLite ledger (`ledger.py`, `ledger_records` table) already carries, per settled pick, `closing_line`, `closing_american_odds` → `closing_raw_probability`, `serving_probability`, `market_probability_at_decision`, and `model_probability_raw` (`domain.py::PickRequest`, `ledger.py::settle()`). The raw ingredients for CLV (entry probability at decision time vs. probability at close) and for Brier/log-loss-over-time already land in the ledger for every pick that successfully resolves a closing quote. What's missing is not the data — it's (1) reliable closing-quote coverage outside MLB, which is exactly gap (b), and (2) a **persisted, queryable rollup layer**, which today doesn't exist at all; any CLV/calibration number currently has to be computed ad hoc.

**Recommended structure — three additive pieces, all consistent with `.planning/research/STACK.md`'s already-completed stack research for this same question:**

1. **A small number of additive columns on `ledger_records`** (or, if the team prefers not to widen the hot-path ledger table further, a companion `ledger_metrics` table keyed 1:1 on `pick_id`, joined at read time) — `clv`, `brier_contribution`, `log_loss_contribution`, `claimed_edge_bucket`. This follows the same "additive-columns problem, not new system" pattern the codebase already used for the `closing_*`/`serving_*`/`blend_*` fields added incrementally to `PickRequest` over time (see the `as_dict()` comments in `domain.py` documenting each field's addition date and reason) — this is a well-worn, low-risk migration shape in this codebase specifically.
2. **A new pure-Python computation module** (e.g. `measurement.py`), following the codebase's own documented shadow-feature-module pattern from `CLAUDE.md` ("The shadow-feature pattern"): a data-capture step with explicit `observed_at_utc` provenance (already satisfied by existing `closing_*`/`decision_*` fields), fail-closed behavior (no closing quote → leave CLV `null`, never guess — exactly the existing comment in `_closing_probability_for_moneyline_pick`: *"blank CLV is missing measurement; a guessed one reads as real"*), and a devig step (power method, per STACK.md) before comparing model probability to market-implied probability.
3. **DuckDB rolling-window views** (30/90/365-day Brier, log-loss, CLV%, calibration-curve buckets, segmented by sport/model/claimed-edge-bucket) over the SQLite ledger — DuckDB is already the feature-store engine in this codebase, already proven for point-in-time aggregation, and can query SQLite directly (via its `sqlite_scanner` or a periodic materialized export) without adding a second database technology.

**Isolation from the promotion gate — the hard requirement:** `model_promotion.py::promote()` is a separate, explicit, human-invoked function (`--approved-by` required) that mutates `config/production.yaml`; it does not read any metrics table today, and nothing in this recommendation wires the new CLV/calibration rollups into it. The new tracking layer is designed as **read-only, downstream-of-settlement evidence for a human to consult**, in the same relationship the existing walk-forward evidence (`validation.py`, `production_feature_ablation.py`, `roadmap_challenger.py`) already has to promotion — evidence and gate are already architecturally separate in this codebase (`docs/ARCHITECTURE.md`'s validation contract: fit on train, select on validation, report on locked holdout, promote only via the explicit CLI). This proposal extends that same separation to live/ongoing measurement rather than introducing a new relationship between "a metric changed" and "a model got promoted."

## Anti-Patterns to Avoid

### Anti-Pattern 1: Building a second closing-quote store for non-MLB sports
**What:** Standing up a brand-new archive format/location for the 13 non-MLB sports' hash-verified quotes, separate from `data/odds/{sport}/{date}/polymarket_snapshots.jsonl`.
**Why bad:** Fragments the storage layer, duplicates the JSONL-append-plus-hash pattern that already exists once (MLB), and orphans years of already-captured non-MLB snapshot history that would need migrating for no benefit — the existing files already have everything except the hash/record-id fields.
**Instead:** Add the hash/record-id fields to the existing `PolymarketSnapshotStore.append()` write path in place.

### Anti-Pattern 2: Using `rebuild/shadow_ledger.py`'s stub tables as the production linkage/metrics store
**What:** Implementing the missing `closing_prices`/`model_artifacts` insert methods in `shadow_ledger.py` and treating that as the new system of record for (b) or (c).
**Why bad:** `rebuild/` is architecturally isolated from production by design (`docs/rebuild/ARCHITECTURE.md`'s no-production-write boundary) — settlement, live promotion evidence, and the dashboard all need this data, and none of them are allowed to read from the shadow tree.
**Instead:** Treat the shadow-ledger schema as useful prior art (it independently converged on nearly the same field shape) but implement the live-path version in `ledger.py`/`data_sources/`, where production already reads.

### Anti-Pattern 3: Making CLV/calibration rollups an automatic promotion input
**What:** Wiring the new rolling-window metrics directly into `model_promotion.py` or `production_registry.py` so a model auto-promotes/demotes on a metric threshold.
**Why bad:** The existing promotion gate is deliberately fail-closed and human-gated (`--approved-by` required); an automatic trigger would be a real governance change to a live-money system, explicitly out of scope per the research question and per this codebase's own stated philosophy ("Config/model promotion... is a real decision with real governance requirements... Don't do it as a side effect of something else").
**Instead:** Surface the rollups on the dashboard / in reports for a human to read alongside the existing walk-forward evidence; leave `promote()`'s explicit-approval contract untouched.

### Anti-Pattern 4: Guessing a postponed KBO/NPB game's result from a makeup date
**What:** When a game is detected as postponed, searching for a same-team-pair game on a nearby date and settling the original pick against that result.
**Why bad:** Violates this codebase's strongest documented convention (fail-closed on ambiguity, never guess) and risks silently mis-grading a real pick against a different (makeup) game's actual market context, line, and pregame conditions — exactly the class of bug `CLAUDE.md` and `DEBUG.md` repeatedly flag as this project's highest-risk failure mode.
**Instead:** Void the original pick with an explicit reason code; let the makeup game (if any) be forecast and settled as its own independent event, which the existing daily pipeline already does naturally since it operates per-date.

## Scalability Considerations

| Concern | At current scale (14 sports, single operator) | If sport/market count doubles | If real-money tiers expand beyond MLB/WNBA |
|---------|--------------------------------------------------|-------------------------------|----------------------------------------------|
| Archive-to-contract linkage (b) | Roll out sport-by-sport, MLB pattern proven | Generalized module already sport-parameterized — no redesign needed, just per-sport team-alias/market-slug config | Real-money tiers should be the **first** priority for linkage extension, since profitability measurement there has real financial stakes, not just research-tier bookkeeping |
| Postponement handling (a) | KBO/NPB-specific fix; soccer already solved | Same `args.void_postponed` + per-source unplayed-index pattern generalizes to any league with a similar "keeps a placeholder row" schedule shape | No change — postponement handling is orthogonal to real-money promotion |
| Metrics/tracking layer (c) | Additive columns + DuckDB views, cheap at current row counts (thousands of picks) | DuckDB rolling-window queries scale comfortably into the millions-of-rows range without redesign; revisit only if per-pick feature-vector storage (not just scalar metrics) is added | Real-money tiers need the tightest closing-quote coverage (from b) before their CLV numbers can be trusted — sequence b before relying on c's output for MLB/WNBA governance decisions |

## Sources

- `src/model_prediction/cli/settle.py` (read in full) — settlement dispatch, soccer postponement handling (existing pattern), KBO/NPB settlement gap (confirmed: no postponement branch), `_closing_probability_for_moneyline_pick`, `_extract_market_slug` — HIGH (first-party, current code)
- `src/model_prediction/domain.py` (read in full) — `MarketQuote`, `PickRequest` field-by-field audit trail, including already-present-but-MLB-only-populated `market_snapshot_*` fields — HIGH
- `src/model_prediction/data_sources/mlb_market_odds.py` (read in full) — `MarketOddsSnapshotStore`, canonical hash, `load_verified_mlb_market_snapshot` — HIGH
- `src/model_prediction/data_sources/polymarket_us.py` (`PolymarketSnapshotStore`, lines 571–624) — generic non-MLB snapshot store, confirmed no hash/record-id binding — HIGH
- `src/model_prediction/international_baseball.py` (`find_international_baseball_result`, `international_baseball_unplayed_index`, lines 587–686) — confirmed the postponement-detection signal already exists and is unused by settlement — HIGH
- `src/model_prediction/rebuild/shadow_ledger.py` (schema section, lines 1–537) — confirmed `closing_prices`/`model_artifacts` are schema-only stubs, isolated shadow tree — HIGH
- `src/model_prediction/ledger.py` (`settle()`, lines 1088–1160) — confirmed closing/serving probability fields already flow into settled rows — HIGH
- `src/model_prediction/production_store.py`, `src/model_prediction/model_promotion.py` — confirmed promotion gate is explicit/human-gated, not metrics-driven — HIGH
- `docs/ARCHITECTURE.md`, `docs/rebuild/ARCHITECTURE.md`, `CLAUDE.md` — durable architecture contracts, PIT-correctness invariant, rebuild isolation boundary — HIGH (first-party, current)
- `docs/ROADMAP.md`, `docs/INCUMBENT_COMPARISON_2026-09-09.md` — MLB's 194-event linkage methodology, fee schedule, stated next step ("extend the exact archive linkage... to other market families") — HIGH (first-party, current)
- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/CONCERNS.md` — prior codebase-mapping pass (2026-09-20), cross-checked against direct source reads above — HIGH (first-party, same repo, same-week)
- `.planning/research/STACK.md` — parallel research artifact for the same milestone's metrics/tracking-stack question; this document's (c) section is consistent with and builds on it rather than duplicating it — HIGH (first-party, same-week)
