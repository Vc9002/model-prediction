# WNBA Rebuild Data Foundation: Phase-1 Audit and Feature-Code Recovery

**Purpose:** groundwork only -- data audit and archived-feature-code
recovery for a future, independent `wnba-elo-trend-lr-rebuild-v1` sibling
model. Training that model is explicitly **not** in scope for this pass;
this document's job is to answer "is there enough real rebuild-owned data,
and what's actually recoverable and working" for whoever does that next.

**Date:** 2026-08-11. **Branch:** `rebuild/wnba-model-curation-v1` (based on
`origin/main`). **Builds on, does not re-derive:**
`docs/model_audit/models/WNBA_ELO_TREND_LR_V4.md`,
`docs/model_audit/features/WNBA.md`,
`docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md` -- all already-verified
findings about the incumbent model and the archived
`origin/rebuild/wnba-v1` branch's code. Machine-readable companion:
`outputs/rebuild/wnba/data_audit.json`.

---

## 1. What data exists (real numbers, real commands)

Before this session, `data/rebuild/` did not exist in this worktree at all
(gitignored, and never previously backfilled here). Real real backfill
commands were run against the real SportsDataverse provider via
`model_prediction.rebuild.data_cli backfill --sport wnba`, one table at a
time, for seasons 2022-2025:

| Table | 2022 | 2023 | 2024 | 2025 | Wall time (4 seasons) |
|---|---|---|---|---|---|
| `schedule` (-> `games`) | 241 games | 263 games | 264 games | 312 games | ~8s |
| `team_box` | 482 rows (241 games, both sides) | 524 rows (262 games) | 528 rows (264 games) | 624 rows (312 games) | ~8s |
| `rosters` | 0 -- **HTTP 404** | 0 -- **HTTP 404** | 166 rows | 181 rows | ~7s |
| `player_box` | 0 -- **normalization error** | 5,796 rows | 5,929 rows | 7,140 rows | ~12s |
| `pbp` | 0 -- **normalization error** | 0 -- **normalization error** | 0 -- **normalization error** | 0 -- **normalization error** | ~18s |

Total wall time for all 5 tables x 4 seasons: **under a minute** --
SportsDataverse serves one bulk file per season/table, not per-game calls,
so this was fast, not the "expect real time" caveat's worst case.

`rebuild-data audit --sport wnba --season {2022..2025}` (the real audit
tool, not a hand count) independently confirms, for every season: **zero
duplicate events, zero duplicate observations, zero timestamp violations,
zero missing canonical team IDs, zero missing canonical player IDs**, and
complete team_box coverage for every completed game. Full per-season
numbers are in `outputs/rebuild/wnba/data_audit.json`.

2025 shows 15 distinct teams instead of 14 -- real WNBA expansion (Golden
State Valkyries), not a data defect. 2023 shows 262 completed games against
263 scheduled -- one game never resolved to `completed=true` in the source
release; not investigated further here, flagged for whoever trains against
2023.

## 2. What's missing, and why (real gaps, not fabricated)

Three real, reproduced gaps, in order of relevance to this task's scope:

1. **Rosters unavailable for 2022-2023** (`HTTP 404` from the SportsDataverse
   asset endpoint) -- genuine provider unavailability, not a bug. 2024-2025
   are complete (347 rows combined).
2. **2022 `player_box` blocked by a real normalization bug**: one row
   (`Kianna Smith`, one 2022 game) has `plus_minus == "--"` -- ESPN's own
   not-applicable placeholder -- which `normalize.py`'s `_as_float()`
   rejects with `ValueError`, and because the season is normalized as one
   frame, this one value blocks all 5,307 rows of 2022 player_box. Traced
   directly to the exact row and column; reproduction command in
   `data_audit.json`. **Not fixed in this pass** -- `normalize.py` is
   shared, already-tested infrastructure outside this task's RECOVER scope
   (`features.py`/`horizon_builder.py` only); flagged for whoever owns
   `normalize.py`'s contract next.
3. **`pbp` is unusable for all 4 backfilled seasons**, every time failing
   with `unresolved WNBA player identity for ESPN ID <N>`. Traced directly:
   the raw PBP feed references athlete IDs with `athlete_name_1 == null`
   that never appear in any team_box/player_box/roster row, so they were
   never registered in the `IdentityRegistry`; `_pbp()`'s
   `_existing_identity()` call raises hard on the first unresolvable ID,
   blocking the entire season (93,166 rows for 2022 alone). **Not fixed in
   this pass**, same reasoning as above. This is a currently-total,
   0-for-4-seasons gap for anyone who wants WNBA PBP specifically.

**Neither gap blocks this task's deliverable.** The recovered
Four-Factors feature path (`features.py`/`horizon_builder.py`) consumes
only `games` and `team_box` -- both **complete across all 4 seasons**.

## 3. A real, structural data-vintage finding (not in the prior audit docs)

Every normalized row's `observed_at_utc` is this backfill's actual capture
timestamp (2026-08-11), because SportsDataverse serves one current release
per season, not per-date historical snapshots. Concretely verified via a
real test against the real backfilled 2024 data
(`tests/rebuild/test_wnba_features.py::test_real_backfilled_2024_data_produces_a_real_available_snapshot`):
asking `build_team_form_snapshot` for team form "as of 2024-12-31" (a date
inside the season) returns **zero eligible prior games**, every time --
because the PIT gate correctly requires `observed_at_utc <= decision_time`,
and `observed_at_utc` (2026-08-11) is always after any date inside the
season.

This is the concrete mechanism behind `WNBA_ARCHIVED_BASELINES.md`'s
already-documented, higher-level caveat ("capture-time-only historical data
is not retrospective PIT evidence") -- this audit reproduced it directly
rather than just citing it. Practical consequence: this data supports
**"team form as of today, ordered by real historical `event_start_utc`"**
feature construction (a legitimate research/backtest input, since game
order and box scores are real historical fact), but does **not**, by
itself, support a genuine prospective walk-forward replay where
`decision_time_utc` is set to a date inside the season. That distinction
matters for whoever designs the next phase's training methodology --
building the model on today's single-vintage snapshot is fine for a first
research pass, but should not be described as "the model would have seen
exactly this at the time," because it would not have.

## 4. Archived feature-code recovery: verdicts re-verified, code ported

`docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md` already read
`origin/rebuild/wnba-v1`'s three excluded files via `git show` and reached
RECOVER / RECOVER / AUDIT_ONLY verdicts for
`features.py` / `horizon_builder.py` / `baselines.py`. This pass
re-verified those verdicts against current `main`'s actual code (not just
re-read the archived files) and found them still correct:

- Re-diffed every file in `src/model_prediction/rebuild/wnba/` between
  `origin/rebuild/wnba-v1` and current `main`: `normalize.py`, `pit.py`,
  `store.py`, `contracts.py` are still **byte-identical** -- confirmed
  directly with `diff`, not assumed from the prior audit doc.
  `foundation.py`, `audit.py`, `time.py`, `__init__.py` (docstring aside)
  unchanged too.
- Traced every non-stdlib import the two RECOVER files use
  (`.pit.eligible_prior_team_games`, `providers.base.{canonical_json,
  dataframe_schema_hash}`, `rebuild.horizons.{HORIZONS,
  HORIZON_HOURS_BEFORE}`, `rebuild.storage.FeatureStore`,
  `.store.WNBANormalizedStore`, `.time.sports_event_date`) against current
  `main` directly -- all present, all matching signatures.
- Cross-checked the archived code's column-name assumptions
  (`field_goals_attempted`, `three_points_made`, `offensive_rebounds`,
  `turnovers`, `points`, etc.) against current `main`'s real
  `WNBA_CONTRACTS["team_box"]` and the real backfilled data's schema --
  exact match, no adaptation needed.

**Ported, real, working, tested:**

- `src/model_prediction/rebuild/wnba/features.py` -- `build_team_form_snapshot()`,
  rolling Four-Factors (`ortg`/`drtg`/`netrtg`/`pace`/`efg`/`tov_pct`/
  `orb_pct`/`ft_rate`) over 5/10/20-game and season windows, PIT-safe via
  `pit.py`'s already-tested `eligible_prior_team_games`.
- `src/model_prediction/rebuild/wnba/horizon_builder.py` --
  `build_wnba_replay_features()` / `build_wnba_live_features()`, the shared
  replay/live feature-build path with postponement-safe cutoff
  stabilization (`_target_as_of_cutoff`'s 5-attempt convergence loop) and
  write-then-verify snapshot persistence via `FeatureStore`.

**One real bug found and fixed during this port** (not present in the
original archived branch's own test coverage, since it was never exercised
against a genuinely empty, never-backfilled table): `build_team_form_snapshot`
crashed with an unhandled `ValueError` when `games` or `team_box` was a
fully empty (zero-row, zero-column) frame -- the exact shape
`WNBANormalizedStore.read_observations()` returns for a season that was
never backfilled at all. Fixed with an explicit empty-frame guard that
returns the same `UNAVAILABLE`/zero-sample result the function already
returns for zero eligible rows, rather than crashing the whole build.
Covered by
`tests/rebuild/test_wnba_horizon_builder.py::TestMissingnessIsHonest::test_target_with_no_prior_team_history_is_recorded_not_dropped_silently`.

**Also closed a real test-coverage gap the archive review itself flagged**:
"the 5-attempt cutoff-stabilization loop in `_target_as_of_cutoff` ...
deserves its own targeted unit tests for the postponement-drift case
specifically (a game whose start moves more than once) ... this card did
not confirm from the diff alone whether that specific multi-move scenario
is covered." It was not covered on the archived branch. This pass added
`TestPostponementCutoffStabilization` (two-revision, three-revision, and
non-converging-fails-closed cases) in
`tests/rebuild/test_wnba_horizon_builder.py`.

**`baselines.py` was not recovered**, per its confirmed-still-correct
`AUDIT_ONLY` verdict: it is a comparison/evaluation harness (its own
docstring: "does not create a deployable challenger"), still hard-gated
`production_allowed: False` / `commercial_use_status: "unresolved"`. No
code from it was copied into anything that runs in this pass; it remains
cited research material only, per the operator instruction.

## 5. Tests added

- `tests/rebuild/test_wnba_features.py` (10 tests) -- ported the archived
  branch's fixture-based tests unmodified (they still pass against current
  `main`'s schema, confirming the "no adaptation needed" finding directly
  rather than by inspection alone), plus one new test exercising the real
  backfilled 2024 data end-to-end.
- `tests/rebuild/test_wnba_horizon_builder.py` (22 tests) -- ported the
  archived branch's rights-gate/provenance tests
  (`test_wnba_research_guards.py`), plus new coverage this repo's
  convention calls for and the archive review explicitly flagged as
  missing: postponement cutoff stabilization (2/3-revision convergence,
  non-convergence fail-closed), full replay-build row production, snapshot
  persistence/read-back, hash determinism and content-sensitivity, honest
  missingness reasons (cold-start team, no scheduled games), live-mode
  cutoff enforcement, and the rights gate against a real
  `production_allowed=True` row.

Result: **32/32 new tests pass**
(`env PYTHONPATH=src:. .venv/bin/python -m pytest tests/rebuild/test_wnba_features.py tests/rebuild/test_wnba_horizon_builder.py -q`).
Full `tests/rebuild/` suite (774 tests, everything in the directory, not
just the new WNBA files): **774 passed, 0 failed** -- nothing this pass
touched broke any other rebuild test.
`ruff check` on every file touched in this pass (`features.py`,
`horizon_builder.py`, `__init__.py`, both new test files): **all checks
passed**, no new findings.

## 6. Calendar/season range safe to build on

**2022-2025 (four completed WNBA seasons, 1,080 games)** for the recovered
Four-Factors feature path specifically -- `games` and `team_box` are
complete, PIT-clean (zero violations), duplicate-free, and
identity-resolution-clean for all four. 2023's one non-completed scheduled
game and 2022/2023's missing rosters/player_box do not affect this feature
path (it doesn't consume those tables).

**Not yet safe to build on**: any feature that would need `pbp` (currently
0% functional) or 2022 `player_box` (blocked by the `--` normalization
bug), and -- more fundamentally -- any claim that a future model trained
on this data was validated via genuine prospective walk-forward replay
inside the historical seasons, per section 3's finding. A future training
phase can legitimately use this data for a first research pass (event
order is real), but should describe its validation honestly as
capture-time-derived research evidence, not prospective PIT evidence,
matching `baselines.py`'s own self-imposed qualification language.

## 7. Go/no-go recommendation for the next phase

**GO, with two explicit caveats carried forward.**

There is enough real, rebuild-owned, PIT-clean `games`/`team_box` data
(2022-2025, 1,080 games) to start building and researching a
rebuild-native WNBA feature set on top of the now-recovered
`features.py`/`horizon_builder.py`. The archived RECOVER verdicts held up
under direct re-verification against current `main`'s real schema and real
backfilled data, not just static reading. No schema mismatch was found
between what the archived code assumed and what `main`'s data foundation
actually produces -- the one real bug found (empty-frame crash) was a
robustness gap, not a schema mismatch, and it's already fixed and tested.

The two caveats a training-phase owner must carry forward, not silently
drop:

1. **This is single-vintage, capture-time-only data.** Any training/
   evaluation methodology must be described as such -- do not present a
   walk-forward-shaped backtest over this exact data as genuine
   point-in-time evidence the way `docs/ARCHITECTURE.md`'s validation
   contract expects for production qualification. That qualification
   remains blocked (matches `WNBA_ARCHIVED_BASELINES.md`'s pre-existing
   finding) until a replay-safe, dated-snapshot data source is either
   sourced from SportsDataverse/ESPN with resolved commercial-use rights,
   or substituted.
2. **The commercial-use-rights blocker is unresolved and this pass did not
   change that.** `horizon_builder.py`'s own `_assert_research_source_
   provenance` will continue to hard-fail-closed on any row that isn't
   `capture_time_only`/`unresolved`/`production_allowed=False` -- so this
   recovered path is structurally incapable of producing anything that
   would pass as production-cleared regardless of how well a future model
   performs statistically. A future promotion decision needs that rights
   question resolved first, independent of any modeling result.

Neither caveat blocks *starting* research/training work in the next phase;
both must be stated plainly in whatever that phase reports, rather than
discovered later.
