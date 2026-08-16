# NBA Rebuild Data Foundation — Groundwork Audit

Status: audited 2026-08-11, on `rebuild/nba-model-curation-v1`. This is a
groundwork/architecture-prep pass (per this branch's task scope), not a data
build. **No code was written in this pass.**

## Verdict

> **BLOCKED — `rebuild-data` has no real NBA backend. `--sport nba` is a
> stub that always returns `{"status": "NOT_IMPLEMENTED"}` for both
> `backfill` and `audit`, on both real invocation and by direct source
> read. There is no `src/model_prediction/rebuild/nba/` package at all —
> unlike `wnba/`, `nfl/`, `soccer/`, `tennis/`, and `mlb_v3/`, which all
> exist. NBA curated model work cannot start until a data foundation is
> built; that is a separate, larger, explicit decision, out of scope for
> this pass.**

This is a real, larger blocker than any other sport in this curation
series. WNBA/NFL/Soccer/Tennis each already had a real backend to verify or
lightly wire up. NBA has none.

## Evidence (real invocation, not inferred)

```
$ PYTHONPATH=src:. python -m model_prediction.rebuild.data_cli backfill --sport nba --table schedule
{
  "sport": "nba",
  "operation": "backfill",
  "status": "NOT_IMPLEMENTED",
  "reason": "no data foundation is registered for nba yet (config/rebuild.yaml sports.nba.status='data_foundation'); see data_foundation.py's module docstring"
}

$ PYTHONPATH=src:. python -m model_prediction.rebuild.data_cli audit --sport nba
{
  "sport": "nba",
  "operation": "audit",
  "status": "NOT_IMPLEMENTED",
  "reason": "no data foundation is registered for nba yet (config/rebuild.yaml sports.nba.status='data_foundation'); see data_foundation.py's module docstring"
}
```

Source confirms this is not a runtime accident:

- `src/model_prediction/rebuild/data_foundation.py::build_data_foundation`
  only special-cases `mlb`, `wnba`, `nfl`, `soccer`, `tennis` (each imports
  a real `<sport>/cli_adapter.py`). Every other sport, including `nba`,
  falls through to `_NotImplementedFoundation`, which always returns the
  `NOT_IMPLEMENTED` envelope shown above for both `backfill` and `audit`.
- The module's own docstring lists which sports have a real transplant:
  *"A real, working `backfill`/`audit` implementation existed per-sport
  (mlb_v3, wnba, nfl, soccer) on now-archived branches... `mlb`, `wnba`,
  `nfl`, `soccer`, and `tennis` are now registered for real... every other
  sport remains a `_NotImplementedFoundation`."* **`nba` is absent from
  that list of sports that ever had an archived source branch to
  transplant from** — this is not merely unscheduled work, there is no
  known prior implementation to port.
- `docs/rebuild/OPERATIONS.md`'s own safe-invocation examples list backfill
  commands for `mlb`, `wnba`, `nfl`, `soccer`, `tennis` only; there is no
  NBA example, and its prose confirms *"every other sport on `rebuild-data`
  ... still reports `NOT_IMPLEMENTED`."*
- `git branch -a` shows `origin/rebuild/wnba-v1`, `origin/rebuild/nfl-v1`,
  `origin/rebuild/soccer-v1`, `origin/rebuild/tennis-v1`,
  `origin/rebuild/mlb-v3-research` — **no `origin/rebuild/nba-v1` branch
  exists or ever existed** in this repository's history. NBA is the one
  sport in this curation series with no prior rebuild attempt of any kind
  to recover, unlike every other sport `ARCHIVE_RECOVERY_MAP.md` covers.
- `find src/model_prediction/rebuild -maxdepth 1 -type d` shows `wnba/`,
  `nfl/`, `soccer/`, `tennis/`, `mlb_v3/` — no `nba/` directory exists.

### A real, live-forward pipeline does exist for NBA — but it is not this

To avoid overclaiming the blocker: NBA is **not** completely absent from
the rebuild track. `sport_adapter.py`'s `_BasicEloAdapter` (the
deliberate, disclosed "basic prediction, working pipeline, not an advanced
model" foundation documented in
`docs/model_audit/ARCHITECTURE_CORRECTION.md`) already covers NBA today,
sharing `NBACollector` (`collectors.py`) with WNBA — real ESPN scoreboard
collection, a basic Elo pipeline (`basic_elo.py`/`basic_sport_pipeline.py`),
and a real shadow-ledger write, all exercised by real tests
(`tests/rebuild/test_sport_adapter.py`'s `test_nba_*` methods,
`test_basic_elo.py`, `test_basic_sport_pipeline.py`). This is
`rebuild-shadow`'s day-of collect -> predict -> decide path, moneyline-only,
no derived feature store — it is what every sport in this position runs on
until its curated model lands.

**What is missing is specifically the `rebuild-data` layer**: bulk historical
season backfill into `data/rebuild/normalized`, immutable per-season
manifests, and a real `audit` command reporting coverage/missingness/PIT
status (the `outputs/rebuild/<sport>/data_audit.json` artifact shape this
task was asked to produce for other sports). That layer is what a curated
NBA model needs to train against — `_BasicEloAdapter`'s live day-of
scoreboard alone is not a training dataset. This distinction is why the
verdict above is BLOCKED for curated-model groundwork specifically, not a
claim that "NBA has nothing in rebuild."

Per this task's own instruction and the project's standing policy
(`docs/model_audit/ARCHITECTURE_CORRECTION.md`'s classification rubric —
DELIBERATE FOUNDATION / REAL BUG / PLANNED MISSING CAPABILITY), NBA's
`rebuild-data` gap is **PLANNED MISSING CAPABILITY**, not a bug: it is the
next disclosed, unstarted seam `data_foundation.py`'s docstring names,
exactly analogous to what WNBA/NFL/Soccer/Tennis looked like before their
own transplants landed. Building it is out of scope for this groundwork
pass (per this task's brief: "If NBA data ingestion is genuinely missing,
do NOT build it yourself either — that's a bigger, separate decision").

### Config naming note (minor, worth flagging)

`config/rebuild.yaml` labels `sports.nba.status: data_foundation` —
**identical** to `wnba`/`nfl`/`soccer`/`tennis`, all of which have a real
backend. Taken alone, this label misleadingly implies NBA already has one.
The `_NotImplementedFoundation._report()` error message actually echoes
this same status string back (`"...status='data_foundation')"`), which
reads as self-contradictory in the raw JSON output above (`status:
data_foundation` in the reason string, `status: NOT_IMPLEMENTED` at the top
level — two different meanings of "status" collapsed into one field name).
Not a functional bug, but worth a follow-up config fix (e.g. a distinct
`data_foundation_pending` value) before it misleads whoever picks up the
NBA transplant next.

## Reusable infrastructure for a future NBA data-foundation build

None of this was built or modified in this pass — documented as a map for
whoever picks up the actual transplant, per the master plan's explicit
instruction that NBA curation should reuse WNBA's *basketball
utilities/architecture*, not its *trained parameters*.

### 1. `src/model_prediction/rebuild/wnba/` — the direct architectural template

This package (1,153 lines across 8 files) is the closest real analog to
what an `nba/` package would need, since NBA and WNBA share the same
ESPN/basketball schema shape (games, team box, player box, rosters,
play-by-play). Every module is basketball-generic in structure, hardcoded
to the string `"wnba"` in specifics:

- `contracts.py` — `TableContract`/`ColumnSpec` definitions for
  `games`/`team_box`/`player_box`/`rosters`/`pbp`. Column names
  (`event_id`, `home_team_canonical_id`, `event_start_utc`, etc.) are
  basketball-generic, not WNBA-specific.
- `normalize.py` — pure source-to-canonical transforms from SportsDataverse
  raw tables into the contract shape above, including team/player identity
  resolution via `IdentityRegistry`.
- `store.py` — `WNBANormalizedStore`: immutable, season-partitioned Parquet
  storage with content-hash part files. `BUSINESS_KEYS` per table
  (`games`: `event_id`; `team_box`: `event_id`+`team_id`, etc.) — identical
  shape needed for NBA.
- `pit.py` — `eligible_prior_team_games`: the point-in-time eligibility
  gate (`observed_at_utc <= decision_time`, `event_start_utc < cutoff`,
  latest-observation-wins via `group_by(...).last()` after sorting by
  `_observed`). This is a clean, generic PIT chokepoint pattern independent
  of WNBA specifically — directly reusable logic, not just inspiration.
- `audit.py` — structural/provenance audit (duplicate detection, latest-row
  selection, coverage reporting) — the actual `data_audit.json`-shape
  producer for WNBA; same shape this task was asked to produce for NBA.
- `foundation.py` — `WNBAFoundation`: orchestrates `backfill(seasons,
  tables)`/`audit(season)`, writes immutable per-season manifests with
  source/schema content hashes. This is the class an `NBAFoundation` would
  mirror.
- `cli_adapter.py` — thin `DataFoundation` protocol wrapper wiring
  `WNBAFoundation` into `rebuild-data`'s registry; this is the ~76-line
  file `data_foundation.py::build_data_foundation` would need an NBA
  equivalent of.
- `time.py` — `sports_event_date` helper (ET-anchored calendar date from a
  UTC timestamp), generic.

### 2. `providers/sportsdataverse.py` — the real data source, extensible by design

`SportsDataverseProvider` fetches versioned release-asset Parquet files
(schedule/team_box/player_box/rosters/pbp/standings) from
`github.com/sportsdataverse/sportsdataverse-data` releases, plus a live
ESPN scoreboard endpoint. **It is WNBA-only today by explicit, deliberate
code gating** (`if sport != "wnba": return
ProviderResult.unavailable(...)` on every method), not because NBA data
doesn't exist there — SportsDataverse's upstream catalog (the `hoopR`
NBA/`wehoop` WNBA data-collection projects) publishes the same asset shape
for NBA under an analogous `espn_nba_*` release-tag naming convention. The
module's own docstring explicitly invites this extension: *"Other sports
wanting SportsDataverse-catalogued data should add their own
`<SPORT>_RELEASE_ASSETS`-shaped table and dispatch branch here rather than
building a second copy of this file."* This was **not verified against a
live network call in this pass** (out of scope — no ingestion code was
written or run) — it is a documented, plausible extension seam, not a
confirmed-working path. Whoever builds the NBA transplant should verify the
actual `espn_nba_*` release asset names/schema against the live GitHub
release catalog before assuming parity with WNBA's `WNBA_RELEASE_ASSETS`
table.

### 3. `rebuild/models/basketball.py` — NBA/WNBA joint model definition (unwired, informational only)

A third, pre-existing NBA/WNBA model file already lives on `main`:
`PossessionsModel` (Ridge, pace) + an efficiency model + `JointScoreDistribution`,
explicitly designed for both sports ("NBA and WNBA trained independently
with WNBA using stronger shrinkage"). Per `docs/model_audit/MODEL_INVENTORY.md`
(`nba-wnba-possessions-efficiency-v1`), this is **dead code / unwired**,
recommendation `RETIRE` — it was never connected to any data path and
predates the rebuild's data-foundation seam entirely. Noted here only
because it is literally the one piece of NBA/WNBA-joint *model* code in the
tree; it is not a data-foundation shortcut and this pass does not change
its RETIRE recommendation. Model-fitting is explicitly out of scope for
this pass regardless.

### 4. WNBA's excluded feature/baseline modules — a later, separate step

`docs/rebuild/OPERATIONS.md` and `data_foundation.py`'s docstring both note
that WNBA's own transplant deliberately excluded `features.py`/
`horizon_builder.py`/`baselines.py` (feature-engineering and model-baseline
code) from the data-foundation-only transplant, as a scope decision, not a
rejection. `docs/model_audit/FEATURE_RETENTION_MATRIX.md`'s WNBA row
records the archived `rebuild/wnba-v1` efficiency features (ORtg/DRtg/
NetRtg/pace/eFG/TOV%/ORB%/FT-rate — PIT-safe, multi-window) as `RECOVER`
candidates for a future WNBA v5. These are basketball-generic formulas
(box-score-derived efficiency stats), not WNBA-specific math, so once an
NBA data foundation exists, this same feature set is a plausible template
for NBA's own curated feature work — but that is a second, later decision
gated on the data foundation existing first, and on WNBA's own
`baselines.py` rights question (`SourceRightsProfile.upstream_rights_status:
"unresolved"`) being resolved or independently re-cleared for NBA's data
source.

### 5. Existing NBA-side incumbent features remain the reference, not a source of code to reuse for rebuild

Per this task's background, five real, wired-but-inert PIT-safe candidate
features already exist on the **incumbent** side —
`consistency_gap`/`hot_cold_gap`/`rest_disparity`/`games_last_7_gap`/
`schedule_available` (tracked under the mismatched name
`schedule_missingness`) in `src/model_prediction/features/trends.py` and
`src/model_prediction/features/schedule_load.py`. **No rebuild-native
equivalents of these exist anywhere in `src/model_prediction/rebuild/`** —
confirmed by `git grep -l "consistency_gap\|hot_cold_gap\|rest_disparity\|games_last_7_gap\|schedule_available" src/model_prediction/rebuild/`
returning no matches. This is expected and consistent with the rest of
this finding: there is no NBA feature-computation layer in the rebuild
track yet because there is no NBA data-foundation layer for it to read
from. When the data foundation exists, these five incumbent formulas (not
the incumbent code paths themselves, per the architecture-correction rule
against loading incumbent artifacts) are candidate reference formulas for
a rebuild-native reimplementation — same reuse posture as `elo_ratings.py`/
`trends.py`'s formulas already are for the model *lineage* generally.

## Go/no-go recommendation

**No-go on curated NBA model work in this pass or the near term, distinct
from WNBA.** WNBA already has a real `rebuild-data` backend
(`wnba/cli_adapter.py`, exercised by `test_wnba_data_foundation.py`) — its
curation path is "verify coverage, then build/retest features against real
backfilled data." NBA has neither. The correct next step for NBA is a
separate, explicitly-scoped data-foundation build (mirroring the WNBA
transplant shape above, verified against the live SportsDataverse NBA
release catalog), not a model-curation pass — and that build is out of
scope for this branch per its own task brief.

**What this pass did establish**, so the next NBA session does not have to
re-derive it:

1. The blocker is real, verified by direct source read and a live CLI
   invocation, not assumed from documentation alone.
2. NBA has zero prior rebuild-branch history to recover from (unlike every
   other sport in this series) — a from-scratch build, not a transplant.
3. `wnba/`'s 8-module package is the correct architectural template; its
   PIT gate (`pit.py`) and audit shape (`audit.py`) are close to directly
   portable given NBA's schema-compatible ESPN/SportsDataverse data shape.
4. `providers/sportsdataverse.py` most likely already carries the raw NBA
   data one hop away (same catalog family as WNBA's), gated by a
   deliberate `if sport != "wnba"` software restriction rather than a real
   data-availability gap — but this was not verified live in this pass and
   must be confirmed against the real release catalog before being relied
   upon.
5. The incumbent NBA Elo/trend model (`nba-elo-trend-lr-v4`) stays exactly
   as-is per this task's scope; nothing here touches it or its leakage
   trace (`docs/model_audit/models/NBA_ELO_TREND_LR_V4.md`), which remains
   the closed, resolved reference for the model *lineage* once a rebuild
   candidate is eventually trainable.

No `outputs/rebuild/nba/data_audit.json` was produced — there is no real
backend to audit. Producing one would require either fabricating data
(forbidden by this task's own instruction not to fabricate capability) or
building the data-ingestion layer (explicitly out of scope for this pass).
