# NFL rebuild data foundation — audit and go/no-go

**Scope:** data-foundation groundwork only for a curated, rebuild-native `elo-trend-lr`
sibling to the incumbent `nfl-elo-trend-lr-v4`. No model fitting was performed in this
pass. This document is a companion to `docs/model_audit/models/NFL_ELO_TREND_LR_V4.md`
and `docs/model_audit/features/NFL.md` (both already on `main`) — it does not re-derive
their findings, it builds on them with real backfilled data and one real code fix.

**Date:** 2026-08-11
**Branch:** `rebuild/nfl-model-curation-v1`
**Machine-readable companion:** `outputs/rebuild/nfl/data_audit.json`

---

## 1. What already existed (before this pass)

`src/model_prediction/rebuild/nfl/*` and `src/model_prediction/rebuild/providers/nflverse.py`
are **substantial, real, working infrastructure**, not a stub — confirmed by full read of
all 8 files (964 lines) before any other work started, per this task's instructions:

- **`providers/nflverse.py`** (`NFLVerseProvider`) — real HTTP client for official
  `nflverse-data` GitHub release Parquet assets (`schedule`, `pbp`, `weekly_rosters`),
  with content-hash-verified raw capture, schema-drift detection between successive
  fetches, and a raw cache (`ProviderRawCache`) that makes repeat runs free.
- **`nfl/normalize.py`** — real transforms from nflverse's raw schema to this project's
  canonical contracts (`nfl/contracts.py`), including a real Eastern-timezone-aware
  kickoff-time UTC conversion (`_kickoff_utc`, DST-correct) and already selecting
  `epa`/`success` per play (not just game-level scores).
- **`nfl/pit.py`** — real point-in-time filtering (`eligible_prior_team_plays`,
  `eligible_weekly_roster`): strictly-prior, completed-only, `pit_eligible`-gated joins.
  Independently tested and confirmed correct in this pass (existing test suite, unchanged).
- **`nfl/store.py`** — content-addressed, season-partitioned Parquet storage with
  idempotent writes and "keep last observed vintage" dedup semantics.
- **`nfl/foundation.py`** / **`nfl/cli_adapter.py`** — orchestration wired into
  `rebuild-data backfill --sport nfl` / `rebuild-data audit --sport nfl`, already
  producing atomic, hash-addressed manifests that **unconditionally** self-flag
  `retrospective_pit_qualified: false` and `production_allowed: false`.
- **`nfl/audit.py`** — structural audit (duplicate/timestamp-violation detection,
  HEALTHY/DEGRADED/ERROR status) — this is the same tool this pass ran per season.
- **`rebuild/models/nfl.py::NFLModel`** — a dormant, unwired, never-fitted drive-based
  Monte Carlo score simulator (Ridge EPA scalers + `HistGradientBoostingRegressor` for
  expected drives). Confirmed still dormant (zero callers, no test file) — not touched,
  not evidence of progress, exactly as `docs/model_audit/features/NFL.md` already found.

**Conclusion: almost nothing needed to be built from scratch for basic schedule-based
data.** The gap this pass closed was exercising this code against real data end-to-end,
which surfaced one real bug (below) and one real, unpatched data-policy blocker.

## 2. Real bug found and fixed

`src/model_prediction/rebuild/nfl/normalize.py`'s three row-builders (`_schedule`,
`_pbp`, `_weekly_rosters`) all ended with bare `pl.DataFrame(rows)`. Polars defaults
`infer_schema_length=100` — it only samples the first 100 rows to lock a column's dtype.
Several NFL columns are legitimately `None` for many consecutive real rows (e.g.
`temperature_f`/`wind_mph` are `None` for every dome game). When a season's schedule
happens to list more than ~100 dome games before the first outdoor game with a real
temperature reading, polars has already committed that column to a `Null` dtype and
crashes the instant a real `float` value appears:

```
polars.exceptions.ComputeError: could not append value: 66.0 of type: f64 to the builder
```

This reproduced **deterministically on real 2022 schedule data** (backfilling
2021/2023/2024/2025 all happened to succeed — pure row-order luck, not evidence the bug
wasn't there). **Fix:** pass `infer_schema_length=None` (scan every row) in all three
normalizers. Added a regression test
(`tests/rebuild/test_nfl_data_foundation.py::test_schedule_normalizer_survives_all_null_prefix_longer_than_inference_window`)
that synthesizes 120 all-null-weather rows followed by one real value — confirmed to
fail with the identical `ComputeError` against the pre-fix code (reverted, re-ran,
restored) and pass after. `pytest tests/rebuild/ -q` → 743 passed. `ruff check` on both
touched files → clean.

This is exactly the kind of "genuine bug, light fix" the task scoped in — it blocked
**real backfill from completing at all** for a randomly-ordered real season, independent
of any feature-engineering decision.

## 3. Real bug found, deliberately NOT fixed

`rebuild-data backfill --sport nfl --season {2021..2025} --table weekly_rosters` fails
for **every one of the 5 tested seasons**:

```
ValueError: missing NFL gsis_id
  raised from normalize.py::_weekly_rosters -> _required_id
```

Root condition: a small, real, permanent fraction of weekly roster rows are `DEV`
(practice/development squad) players who have not yet been issued a permanent GSIS ID
by the league. `_required_id` is fail-closed by design — it raises rather than
fabricate a player identity, and there is no natural fallback key available in the
released schema.

**Why this was not patched:** this convention is not an NFL-specific oversight — the
WNBA rebuild normalizer's `_player_canonical_id` (`src/model_prediction/rebuild/wnba/normalize.py`)
raises `ValueError` on the identical condition, with no per-row skip/continue anywhere
upstream either. It is a consistent, deliberate, project-wide "never guess an identity"
policy. Whether unidentified practice-squad rows should be skipped-and-flagged (losing
those specific rows but preserving the rest of the season) versus hard-failing the whole
season the way it does today is a real design decision — not a mechanical defect — and
changing validation semantics for player identity is explicitly out of scope for a
data-foundation-only pass per this task's own instructions ("light, well-tested repairs
... if you find a genuine bug (not a feature addition)"). Loosening it silently would
also cut against this project's own "don't fabricate/guess" convention.

**Measured severity** (diagnostic only, computed directly from the already-fetched raw
provider response, bypassing the normalizer, never written to `data/rebuild/normalized`):

| Season | Total roster rows | Missing `gsis_id` | % |
|---|---:|---:|---:|
| 2021 | 46,696 | 26 | 0.056% |
| 2022 | 46,163 | 27 | 0.059% |
| 2023 | 45,655 | 5 | 0.011% |
| 2024 | 46,579 | 7 | 0.015% |
| 2025 | 46,849 | 29 | 0.062% |

Consistently under 0.1% every season — a real, narrow, addressable-later edge case, not
a broad data-quality problem. **Recommended shape of a future fix** (not implemented
here): an explicit, provenance-preserving fallback identity for unidentified rows (e.g.
`season:week:team:full_name` with an `identity_source: synthetic_fallback` provenance
flag distinct from `gsis_id`-backed rows), decided and reviewed on its own, not smuggled
in as a side effect of a data-audit pass.

**Impact on this pass's actual goal: none.** `elo_probability`/`trend_gap` are computed
purely from schedule/game results — rosters are never an input. Roster data only matters
for QB-identity/injury-state features, which are separately blocked (see §5).

## 4. Real coverage backfilled

| Table | Seasons | Result |
|---|---|---|
| `schedule` | 2021, 2022, 2023, 2024, 2025 | **All 5 succeeded** after the fix. 1,424 games total, all `completed=true`, 0 duplicates, 0 PIT-timestamp-ordering violations, 32 teams/season, full REG+WC+DIV+CON+SB coverage each season. |
| `pbp` | 2024 (spot check) | Succeeded: 49,492 real plays, 0 duplicate plays, 0 games missing a complete-pbp marker, `epa`/`success` populated on 98.85% of rows (nulls are non-play rows: timeouts, two-minute warnings — expected, not a defect). |
| `weekly_rosters` | 2021–2025 (all attempted) | **All 5 failed** via the wired CLI path — see §3. Not committed to `data/rebuild/normalized`. |

Full per-season detail is in `outputs/rebuild/nfl/data_audit.json`.

`data/rebuild/normalized/` is git-ignored runtime state (per `docs/rebuild/ARCHITECTURE.md`
and `.gitignore`), consistent with this pass not committing raw/normalized data itself —
only the JSON audit summary and this doc are meant to be versioned evidence.

## 5. Basic Elo/trend feature servability — explicit answer

**Yes, servable from what is backfilled today.** `elo_probability` and `trend_gap`
(`features/elo_ratings.py::build_elo`, `features/trends.py::TrendEngine`) require only
`event_id`, `event_start_utc`, `home_team_id`, `away_team_id`, `home_score`,
`away_score`, `completed` — every one of those fields is present, 100% populated, and
internally consistent (no duplicates, no timestamp-ordering violations) across all 5
backfilled seasons. That feature code is already sport-generic and already exercised
live by the incumbent `nfl-elo-trend-lr-v4` — it would not need to be rewritten, only
pointed at rebuild-normalized `nflverse` schedule data instead of the incumbent's
ESPN-sourced `data/processed/nfl/games.jsonl`, which is itself the correct posture for
an "independently trained sibling" per `docs/model_audit/ARCHITECTURE_CORRECTION.md` —
not a copy of the incumbent's training data.

**One caveat to carry forward, not silently drop:** the foundation's manifest marks
`retrospective_pit_qualified: false` **unconditionally**, including for `schedule` —
the stated reason (mutable releases, no proof a row was observable at an earlier
decision time) is written at the source-provider level, not conditioned per-table.
Completed-game final scores are, in practice, essentially immutable once a game ends
(score corrections are vanishingly rare), which is a materially different risk profile
than EPA/CPOE data (§6). But this pass did not independently re-derive or verify that
practical-immutability claim against nflverse's actual revision history — any future
walk-forward validation built on this schedule data should explicitly document this
caveat rather than treat schedule as pre-cleared just because it's simpler than pbp.

## 6. EPA-based features remain blocked — do not work around

Unchanged from `docs/model_audit/features/NFL.md`'s prior finding, and **not to be
routed around**: `epa`/`success` are already captured in the pbp normalizer (confirmed
at 98.85% real coverage in this pass's 2024 spot check), but `NFLFoundation.backfill`'s
manifest hard-codes `retrospective_pit_qualified: False` for every table, every season,
unconditionally (`foundation.py:97-98`). A single bulk download of historical pbp today
— no matter how complete — cannot serve as honest walk-forward backtest evidence for
`epa_per_dropback`/`success_rate`/any EPA-derived feature, because nflverse pbp releases
are mutable and there is no proof of what was knowable at any specific historical
decision time. The documented fix is calendar time: start daily `observed_at_utc`-stamped
capture now (the same shadow-feature pattern this project already uses for MLB's
`starter_era_gap`) and only validate against the portion of history captured that way
going forward. **This pass did not attempt, and explicitly recommends against, any
workaround** (e.g. treating today's bulk pbp download as if it were a real historical
observation log) — that would silently reintroduce exactly the leakage risk the existing
self-flag exists to prevent.

## 7. Safe season range for eventual training

For a future rebuild-native `elo-trend-lr` candidate (the audited incumbent family —
Elo + EWMA trend, logistic regression, chronological 60/20/20 split, walk-forward
discipline — per `docs/model_audit/models/NFL_ELO_TREND_LR_V4.md` §8's "retain" list),
the schedule data backfilled in this pass (2021–2025, 1,424 completed games) is a
reasonable starting range: comparable in size to the incumbent's own training window
(634 walk-forward rows across ~1.5 seasons) with room for a proper 60/20/20 chronological
split plus the incumbent's 50-game minimum-history floor. `nflverse` schedule data goes
back to 1999 per `NFLVerseAsset.minimum_season`, so extending the backfill further back
is mechanically trivial (just more `--season` values) whenever a future modeling pass
wants a larger or more chronologically-distant training set — nothing in this pass
found a reason that couldn't be done; it just wasn't necessary for a data-foundation
audit. Any such extension should keep the same PIT caveat in mind (§5) and should
independently confirm walk-forward feature construction (Elo/trend rebuilt strictly
before each decision day) rather than assume it carries over from the incumbent's proof.

**Calibration-first reminder, carried forward, not re-derived:** per
`docs/model_audit/models/NFL_ELO_TREND_LR_V4.md` §5/§8, the incumbent's confirmed
~0.10 ECE means that once a rebuild-native candidate exists and is fitted, real
cross-fit calibration evaluation (`cross_fit_calibration_eval` in
`src/model_prediction/rebuild/calibration.py`, already built, unused) should be the
first priority — before any feature expansion — exactly as the incumbent's own audit
already established. This pass did not fit anything, so this is forward guidance only.

## 8. Go/no-go recommendation for the next phase (model fitting)

**GO**, scoped narrowly to `elo_probability` + `trend_gap` on the schedule data now
backfilled (2021–2025, extendable further back with the same command). Rationale:

- The exact feature code path (`build_elo`, `TrendEngine`) already exists,
  is sport-generic, and is already proven correct in production for the incumbent.
- The data those two features need is 100% populated, real, backfilled, and passed
  structural audit (no duplicates, no timestamp violations) across all 5 seasons.
- The one real defect blocking a full real backfill (the schema-inference crash) is
  fixed and regression-tested.
- Nothing about this candidate depends on the still-blocked EPA/roster/QB-state work.

**NO-GO, unchanged, for anything EPA/CPOE/pressure/QB-state-based** — per §6, this
remains blocked on calendar-time daily capture accumulation, not on anything this pass
could fix, and should not be worked around when the next phase starts.

**One open item to carry into the next phase, not resolved here:** the `weekly_rosters`
fail-closed gap (§3) should be deliberately decided (skip-and-flag vs. an explicit
fallback identity) before any QB-identity/injury-state feature work begins, since that
work will need roster data to actually load. It does not block the `elo-trend-lr`
candidate itself.
