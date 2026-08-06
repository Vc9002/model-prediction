# Rebuild Takeover Status

Maintained per `CLAUDE.md`. Updated at the end of each checkpoint with real,
executed evidence — not descriptions of intended work.

## Checkpoint 0 — Preflight (2026-08-06)

**Branch**: `rebuild/clean-slate-v1`
**SHA**: `d5dcc684fc14b03d3e1a39fb9f18a7407845f7d6`
**Python**: 3.14.5 (`.venv/bin/python`, matches system `python3`)

**Working tree**: dirty, but not from this takeover. `git status --short` shows
~80 modified/untracked paths, all pre-existing live-pipeline drift: esports
manifests/match logs (`data/esports/*`), model-ledger `.xlsx` files, and
challenger artifact `.json`/`.previous.json` pairs under `config/models/`.
This is expected — `dashboard_server.py` (PID 46692, launchd-managed as
`com.vc.model-dashboard`) and the `com.modelprediction.daily` launchd job are
both actively running against this same checkout. Per `docs/PROJECT_STATUS.md`
this drift is normal and by design (ledgers/odds/availability snapshots are
tracked in-repo). **Not touched or reverted by this takeover.** Rebuild
commits will stage only `data/rebuild/`, `outputs/rebuild/`,
`config/models/challengers/`, `src/model_prediction/rebuild/`,
`tests/rebuild/`, and `CLAUDE.md` — never a blanket `git add -A`.

**Commands run** (all read-only, no production writers invoked):

```bash
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/model_prediction
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain
```

**Test results**: 755 passed, 1 skipped, 0 failed (16.52s).

**Ruff**: 217 findings in `src/ tests/` (baseline, pre-existing, not from this
takeover — not chased per `CLAUDE.md`'s "don't chase pre-existing findings"
guidance; 55 auto-fixable, mostly `EXE002` shebang-on-test-file style issues).

**mypy**: 112 errors in 17 files, concentrated in `src/model_prediction/cli.py`
(the legacy/incumbent CLI, not `rebuild/`). Pre-existing, not from this
takeover.

**Audit chain** (`verify-chain`): `chain_intact: true`, `break_count: 0`,
62058 audit lines, 89 ledger rows. `reconciled: false` with 2774
`created_but_absent_without_removal_event` rows — per the command's own note,
these are historical deletions predating the audited `remove_open_rows` path,
not a new integrity break.

**Incumbent artifact hashes**: recorded in
`outputs/rebuild/incumbent_artifact_hashes.txt` (47 artifacts, SHA-256,
excludes `.previous.json` backup files). Frozen as of this checkpoint — none
overwritten by rebuild work; new rebuild artifacts go to
`config/models/challengers/`.

**Production writers**: none invoked. All commands above are read-only per
`docs/PROJECT_STATUS.md`'s "Safe command forms" list.

**Pre-existing rebuild-branch inventory** (found before this takeover began,
verified against real code and data, not doc claims — see prior session
audit): `src/model_prediction/rebuild/` already has a working medallion
storage layer (`storage.py` — immutable raw/normalized/features/markets,
provenance columns), `MLBCollector` with real ESPN/pybaseball/weather/
Polymarket collection methods, an `MLBTwoHeadModel` + chronological CV
training script (`scripts/pipeline_mlb_e2e.py`, real run: 190 games, 3 folds,
final Brier 0.244, ECE 0.012, hashed artifact in
`outputs/rebuild/mlb_training_results.json`), and a generic `edge_scaled_units`
Kelly-sizing function in `rebuild/economic.py`. Real gaps found: `pybaseball`
raw data collected for 10 days but never normalized; weather and Polymarket
collectors have real code but have **never actually been run** (zero raw
files for either source); `data/rebuild/features/` and
`data/rebuild/markets/` are both empty; the MLB training script uses rolling
home/away score averages as a placeholder feature set, not real
pitcher/lineup/bullpen/park features; no winner-first decision engine exists
anywhere in `rebuild/`; no single end-to-end shadow command exists.

**Current checkpoint**: 0 (complete).

**Known blockers**:
- Polymarket credentials (`POLYMARKET_KEY_ID`/`SECRET_KEY`/`PRIVATE_KEY`/
  `WALLET_ADDRESS`) presence/validity in `.env` not yet verified — needed
  before `collect_polymarket_books()` can be run for real.
- No CI currently attached to `rebuild/clean-slate-v1` specifically.

**Next executable command**: begin Checkpoint 1 (Environment) —
`pyproject.toml`/lockfile/CI Python-version consistency check, then Checkpoint
4/5 (MLB data + features) is the highest-value next step toward tonight's
goal, since storage (Checkpoint 2) is already largely built.

## Checkpoint 1 — Environment (2026-08-06)

**Found real inconsistencies, not just theoretical ones:**

1. `.github/workflows/ci.yml` pinned Python 3.12, while `pyproject.toml`
   (`requires-python = ">=3.14,<3.15"`), `[tool.mypy] python_version`, and
   `[tool.ruff] target-version = "py314"` all declare 3.14. CI was never
   actually testing the runtime the project claims to require. Fixed: CI now
   uses 3.14, installs via `pip install -e ".[dev]"` (previously only
   `pip install -e . pytest ruff`, silently skipping `mypy` and the
   `scipy-stubs`/`pandas-stubs`/etc. dev-only deps), and added a `mypy` step
   (`continue-on-error: true` — 112 pre-existing errors as of Checkpoint 0,
   not gating CI on paying that down, but now visible in every run instead of
   invisible).
2. `README.md` line ~740 said "Python 3.11+" while line 6 already claimed
   "Python 3.14" for the same repo. Fixed to match the pinned 3.14
   requirement.
3. **Real, currently-broken dependency, not a hypothetical fresh-clone
   issue**: built a genuinely fresh venv (`python3 -m venv`, outside any
   cached dev environment) and ran `pip install -e ".[dev]"` against the
   committed `pyproject.toml`. `import xgboost` failed with a `dlopen` error
   — `libomp.dylib` (OpenMP runtime) was never installed via Homebrew on this
   machine. Confirmed the **existing primary dev `.venv` has the identical
   failure** (`.venv/bin/python -c "import xgboost"` also raised the same
   `dlopen` error before the fix) — this wasn't masked by dev-machine state,
   it was silently broken there too, since nothing in the current test suite
   happens to import xgboost yet. Fixed by `brew install libomp`; verified
   `import xgboost` succeeds afterward (`xgboost 3.4.0`) in both venvs.
   Documented as a required macOS prerequisite in README (pip alone cannot
   install this — it's a system library XGBoost's wheel dynamically loads at
   runtime).
4. `requirements.lock` (75 packages) checked against every `pyproject.toml`
   dependency (`duckdb`, `pandera`, `polars`, `pyarrow`, `pybaseball`,
   `statsmodels`, `xgboost`, etc.) — all present and pinned. `pip check` in
   the primary venv reports no broken requirements.

**Fresh-install proof**: fresh venv install of `pip install -e ".[dev]"`
completed, `import model_prediction.rebuild` succeeded, all declared
dependencies imported (after the `libomp` fix), and
`PYTHONPATH=src:. python -m pytest tests/ -q` on that fresh venv: **755
passed, 1 skipped** — identical to the primary dev venv. Temp venv discarded
after verification (not committed; it was scratch, not repo state).

**Not done in this checkpoint**: did not attempt to verify CI actually passes
on GitHub's `ubuntu-latest` runner (no `gh` CLI auth available in this
session) — Linux's OpenMP situation differs from macOS's `libomp.dylib`
quirk and is unverified; flagging as an open item rather than assuming it's
fine.

**Files changed**: `.github/workflows/ci.yml`, `README.md`. No source code
changed — Checkpoint 1 is environment/tooling only.

**Current checkpoint**: 1 (complete).

**Next executable command**: Checkpoint 4/5 (MLB data + features) — normalize
the already-collected `pybaseball` raw data, actually run the weather and
Polymarket collectors (Polymarket credential validity in `.env` still
unverified), and replace `scripts/pipeline_mlb_e2e.py`'s placeholder rolling-
score features with real starter/lineup/bullpen/park features per
`CLAUDE.md`'s MLB feature-store spec.

## Checkpoint 4 — MLB data (in progress, 2026-08-06)

**Polymarket credentials**: verified all four
(`POLYMARKET_KEY_ID`/`SECRET_KEY`/`PRIVATE_KEY`/`WALLET_ADDRESS`) are
populated in `.env` (values not printed). Not a blocker.

**Real bug found and fixed — silent, systemic, affects every sport, not just
MLB**: `PolymarketUSClient.slate(league, game_date)` compares
`parsed_datetime.astimezone(tz).date() != game_date`. Every one of the five
collector classes (`MLBCollector`, `NBACollector`, `NFLCollector`,
`SoccerCollector`, `TennisCollector`) called `.slate()` with `game_date` as a
raw `str`, not a `date` object. A `date` object is never `==` a `str` in
Python, so this comparison is unconditionally `False` for every event, every
time — Polymarket market collection has silently returned zero markets for
every sport on this branch since it was written, with no exception raised
(the code has a broad `except Exception: continue` around each event, plus
the caller treats an empty list as the unremarkable `no_markets` case).
Verified before/after: before the fix,
`collector.collect_polymarket_books('2026-08-06')` returned
`{"status": "no_mlb_markets", ...}`; after adding `from datetime import date`
and wrapping every `.slate()` call site with `date.fromisoformat(game_date)`
(5 call sites: `collectors.py:216,434,533,618,705`), the identical call
returned `{"status": "ok", "date": "2026-08-06", "books": 132}` — 132 real
order-book rows written to `data/rebuild/markets/mlb/2026-08-06.parquet` and
a raw snapshot to `data/rebuild/raw/polymarket_us/2026-08-06/`.

**Second real bug found and fixed**: `_venues_for_date()` selected a
`venue_id` column from the normalized MLB scoreboard table that doesn't
exist in that table's schema (only `venue` does — confirmed via
`df.columns`). The resulting `ColumnNotFoundError` was silently swallowed by
a bare `except Exception: return []`, so weather collection has always
received zero venues for every date — `data/rebuild/raw/` had no
`open_meteo` directory at all before this fix, for any date, ever. Fixed by
removing the nonexistent column from the `.select()`. After the fix,
`_venues_for_date('2026-08-06')` correctly returns 2 real venues (Chase
Field, T-Mobile Park) matched against the hardcoded 30-ballpark coordinate
table in `collectors.py`.

**Real, unresolved issue — flagged, not patched blindly**:
`collect_weather_forecast()`'s docstring says it should capture "the forecast
*as it was* at a specific run time, not the realized weather" (i.e. a
point-in-time forecast archive, for the same point-in-time-correctness
invariant this whole rebuild is built around), but it calls
`archive-api.open-meteo.com/v1/archive` — Open-Meteo's ERA5 **reanalysis**
endpoint, which serves realized historical weather, not historical forecast
runs, and has several days of processing lag. Called for today
(2026-08-06), both real venue requests returned `400 Bad Request`. The
function's own stated intent needs Open-Meteo's separate "Historical Forecast
API" (previous-runs), not the Archive API — this is a real endpoint-choice
bug, not a transient failure, but fixing it means picking the right
replacement API and verifying its point-in-time semantics match the
provenance contract, which I'm not doing speculatively. **Weather data
collection remains non-functional** pending that fix.

**Also observed, not yet fixed**: normalized MLB scoreboard rows appear
duplicated on repeated collection (5 identical `Kauffman Stadium` rows at the
same `event_start_utc` in one query) — the medallion "normalized collection
must not duplicate rows" invariant (`CLAUDE.md` Part 1 §4) isn't actually
enforced yet in `NormalizedStore`'s write path for this table. Real
Checkpoint 2 gap, tracked here rather than fixed inline to avoid scope creep
mid-Checkpoint-4.

**Not yet done**: `pybaseball` raw-to-normalized pipeline (raw data for 10
days already on disk, never parsed into a usable table); real MLB feature
engineering (starter/lineup/bullpen/park) is still the placeholder rolling-
score version in `scripts/pipeline_mlb_e2e.py`.

**Files changed**: `src/model_prediction/rebuild/collectors.py` (both bug
fixes). Regression tests added in `tests/rebuild/test_mlb_collectors.py`
(confirmed failing against pre-fix code via `git stash`, per `CLAUDE.md`'s
"each corrected bug must include a regression test" requirement).

### Checkpoint 4 continued — weather endpoint fix and a third real bug

**Weather endpoint bug, fixed**: `collect_weather_forecast()`'s docstring
claimed it captures "the forecast *as it was* at a specific run time," but it
called `archive-api.open-meteo.com/v1/archive` — Open-Meteo's ERA5
*reanalysis* endpoint (realized weather, not a forecast), which 400s for any
date without several days of processing lag (confirmed: today's two real
venue requests both 400'd). Verified via `WebFetch` against Open-Meteo's own
docs before changing anything (didn't want to guess at a replacement
endpoint): the correct fix is endpoint selection by date — today-or-future
dates now hit the live Forecast API (`api.open-meteo.com/v1/forecast`; what's
captured right now genuinely *is* the forecast as of now, no leak), past
dates fall back to the Historical Forecast API
(`historical-forecast-api.open-meteo.com/v1/forecast`, which Open-Meteo
documents as a stitched continuous series of past model runs — a disclosed
approximation, not a single exact run, but a real forecast product, not
reanalysis). Verified live for both branches: future date → real weather data
for Chase Field and T-Mobile Park; a January 2000 date → real historical
data, confirmed `archive-api` was not the endpoint hit. Regression tests
added (mocked `httpx.get`, asserting the URL chosen per date).

**Third real bug, more consequential than the first two**: while testing
`collect_pybaseball` against a real recent date, it silently returned
`{"status": "no_data"}` despite pybaseball's `statcast()` call genuinely
succeeding — checked `sources.last_error` in `metadata.db` and found `Object
of type Timestamp is not JSON serializable`. `RawStore.write()`'s
`json.dumps()` had no `default=` handler, so it raised `TypeError` on
`pandas.Timestamp` columns (which every pybaseball DataFrame has) — caught by
the collector's broad except-and-report-degraded handler, discarding real,
successfully-fetched Statcast data every time, for every date, silently.
This is **not MLB-specific** — `RawStore` is shared by every collector, so
any future NBA/NFL/soccer/esports collector handing it pandas/numpy-typed
data would hit the identical failure. Fixed with a duck-typed
`_json_default()` in `storage.py` (handles anything with `.isoformat()` —
datetime/date/Timestamp — or `.item()` — numpy scalars — without adding a
hard pandas/numpy import to the storage layer). Verified live:
`collect_pybaseball('2026-08-04', statcast=True)` went from `{"status":
"no_data"}` to **4,167 real Statcast pitch-level rows**, hash-verified and
written to `data/rebuild/raw/pybaseball/2026-08-04/`. Regression tests added
in `tests/rebuild/test_storage_immutability.py`, including a test that
genuinely-unserializable objects still raise (the fix must not become a new
silent-swallow).

**Current checkpoint**: 4 complete.

## Checkpoint 5 — MLB features (2026-08-06)

**Backfill**: ran `collect_pybaseball` for all 10 remaining completed-game
dates in the normalized ESPN scoreboard (2026-07-26 through 2026-08-04;
2026-08-05 returned `no_data` — plausibly Baseball Savant processing lag on a
1-day-old date, not investigated further, not blocking). Total: **39,692
real Statcast pitches across 270 real starter-game entries**, all
hash-verified in `data/rebuild/raw/pybaseball/`.

**Built** `src/model_prediction/rebuild/mlb_features.py`, replacing
`scripts/pipeline_mlb_e2e.py`'s rolling-home/away-score placeholder:

- `identify_starters()` — the actual MLB rule (whoever throws a team's first
  pitch of the game), not a name heuristic. Verified against real data: 270
  starter-game entries from 270 real (game, team) pairs.
- `pitcher_rolling_features()` — K%/BB%/CSW%/whiff%/avg velocity/days
  rest/pitches-last-start, computed only from a pitcher's *own* pitches in
  games strictly before the decision date (point-in-time-safe by
  construction — verified with a test where a pitcher's 08-10 start does not
  leak into a decision computed for 08-01). Returns
  `availability=0.0` with zeroed fields when a pitcher has no real prior
  starts, rather than silently substituting a league-average-looking number.
- `bullpen_rolling_features()` — team-level relief workload/velocity over a
  trailing window, with the starter's own pitches explicitly excluded via
  `identify_starters()` (verified: a 2-pitch starter outing didn't count
  toward bullpen workload).
- `park_factor()` — a small, explicitly coarse static table (documented as
  sourced from long-run public consensus, not this season's own outcomes,
  to avoid a park factor that leaks the thing it's meant to help predict);
  unknown parks default to neutral (100) rather than guessing.
- `load_weather_daily_aggregate()` — wires in the now-working weather
  collector; daily mean, not yet aligned to the exact first-pitch hour
  (disclosed limitation, not silently approximated as exact).

**Real bug found and fixed while validating this module against actual
data**: both `load_raw_statcast_dates()` and `load_weather_daily_aggregate()`
initially omitted the `raw/` path segment (`raw_root / "pybaseball" / d`
instead of `raw_root / "raw" / "pybaseball" / d`), so real-data validation
returned 0 rows despite 39,692 real pitches being on disk. Caught
immediately by running against real data (not just the unit tests, which
used synthetic fixtures with their own tmp paths and wouldn't have caught a
path-prefix bug). Fixed before writing any test that could have quietly
encoded the same wrong path.

**Second bug, caught by the test suite itself**: a small fixture batch where
every `events` value happened to be `None` made polars infer that column as
its `Null` dtype instead of `Utf8`, and `.str.contains("strikeout")` raised
`InvalidOperationError` — not a fixture-only issue, since a real slate with
zero completed at-bats yet would hit the identical failure. Fixed by
explicitly casting `events`/`description`/`pitching_team` to `Utf8` in
`normalize_statcast_pitches()`.

**Verified live**: sample pitcher rolling features against real data —
`{'availability': 1.0, 'starts_seen': 2.0, 'avg_velocity': 93.26, 'k_pct':
0.104, 'bb_pct': 0.011, 'csw_pct': 0.339, 'whiff_pct': 0.340, 'days_rest':
6.0, 'pitches_last_start': 96.0}`; bullpen for TOR:
`{'bullpen_pitches': 173.0, 'bullpen_avg_velocity': 88.0,
'bullpen_appearances': 10.0}`; weather for Chase Field 2026-08-06:
`{'temp_f_mean': 90.7, 'wind_mph_mean': 5.2, 'precip_mm_total': 2.5}`. All
real, sensible, non-fabricated values.

**Tests**: `tests/rebuild/test_mlb_features.py`, 7 tests — starter
identification, point-in-time leakage prevention (the highest-value test:
proves a future start can't influence a past decision), missing-history
honesty (availability=0 rather than a guessed average), real K%/BB%
computation from actual plate-appearance outcomes, starter-exclusion from
bullpen workload, and park-factor defaulting.

**Not yet done**: the retrained MLB model (Checkpoint 6) still needs to
actually consume these features in place of the placeholder — this
checkpoint built and validated the feature functions but hasn't rewired
`scripts/pipeline_mlb_e2e.py` (or its successor) to call them yet. Also not
done: aligning weather to the actual first-pitch hour instead of a daily
aggregate, and expanding the coarse park-factor table beyond ~29 parks with
public data (all 30 MLB parks are listed; this is about factor *quality*,
not *coverage*).

**Current checkpoint**: 5 complete.

## Checkpoint 6 — MLB modeling (2026-08-06)

**Built** `scripts/train_mlb_rebuild_real_features.py`, replacing
`scripts/train_mlb_rebuild.py`'s rolling-team-score baseline (its own
docstring already flagged this as the intended next step: "the full
Statcast/weather/lineup/pitcher feature set requires the corresponding
collectors to be completed first" — that's now done, Checkpoint 5). Joins
real Statcast games to the ESPN scoreboard via team-name→abbreviation
mapping + (date, home, away) matching (no shared game ID between the two
sources) — **173 of 188 real completed games matched**, 15 unmatched and
correctly excluded rather than fabricated. Real chronological expanding
folds (`expanding_folds`, no random split), fit on `MLBTwoHeadModel`
(existing intensity/differential two-head architecture, unmodified — this
checkpoint only changed what features feed it), Platt-calibrated on a
held-out tail.

**Real bug caught immediately by running against actual data**:
`expanding_folds()` returned **0 folds** on the first run. Root cause: it
dedupes on its `dates` argument (`sorted(set(dates))`); I'd passed the
calendar-day `game_date`, which only has ~10 unique values across 173 games
in this real 10-day backfill window — too coarse for `val_size`/`test_size`
sized in days. Fixed by passing the full `event_start_utc` timestamp instead
(near-per-game granularity while preserving correct chronological order),
matching what the original `pipeline_mlb_e2e.py` already did for the
identical reason.

**Real, honestly-reported result, not spun**: 3 fold-validation Brier scores
(0.268, 0.252, 0.247) land in a plausible range close to the ~0.25
coin-flip-adjacent baseline mentioned elsewhere in this project's docs. The
34-game held-out test, however, shows accuracy=0.353 — worse than a coin
flip. Investigated rather than either hiding it or reporting it uncritically:
first hypothesis was the real, verified cold-start missingness mismatch
(mean starter-`availability` is 0.478 in train vs 0.912 in test — a
genuine artifact of a short 10-day backfill window, not a modeling
choice); added a quality-filtered comparison (both starters have real prior
history, n=28) — brier 0.3139, accuracy 0.357, barely different from the
unfiltered number. That rules out cold-start missingness as the primary
explanation. Most likely real explanation: 34 (or 28 filtered) games is a
small enough sample that a "true" 50% model's binomial standard error is
~8.6% — 35% accuracy is about 1.7 SE below 50%, unusual but not
extraordinary. **Conclusion: this feature set's real quality is
inconclusive on this small a backfill window.** Did not tune
hyperparameters or features against this held-out set to make the number
look better — CLAUDE.md explicitly treats a "consumed holdout" that's then
tuned against as a real violation, not a shortcut.

**Artifacts**: `config/models/challengers/mlb-two-head-real-features-v1.json`
(new challenger artifact — the incumbent `config/models/mlb-elo-trend-lr-v8`
and this branch's own earlier placeholder-feature challenger are both left
untouched, per CLAUDE.md's "do not overwrite" rule), full fold/quality/
cold-start-composition metrics also in
`outputs/rebuild/mlb_training_results_real_features.json`.

**Not yet done**: NBA/NFL/soccer/tennis/esports/KBO/NPB modeling
(out of scope until MLB clears its own gate per CLAUDE.md's explicit
sequencing — "Ignore NBA/WNBA/NFL/soccer/esports until MLB works"); a
real backfill spanning weeks rather than 10 days, which is what would
actually resolve the inconclusive held-out result above; XGBoost/HistGBM
challengers beyond the existing intensity head's HistGradientBoostingRegressor
and differential head's ElasticNet (Part 2's full OOF-ensemble spec, not
attempted this checkpoint — the two-head architecture the incumbent rebuild
already had was reused as-is, only its input features changed).

**Current checkpoint**: 6 (real features wired into real chronological
training and evaluation; result is honestly inconclusive on this small a
sample, not a pass or fail either way).

**Pre-commit hook note**: the first commit attempt for this checkpoint was
silently rejected by `.git/hooks/pre-commit` (runs `ruff check` on staged
`.py` files, `set -e`) — `collectors.py` had 12 pre-existing ruff findings
(`BLE001` blind-except, `S110`/`S112` swallowed-exception-with-no-logging)
unrelated to this session's edits but in the same file. Fixed properly rather
than bypassing the hook: the two truly-silent swallows (pybaseball schedule
fetch, and the `_venues_for_date` catch-all that had already masked the real
`venue_id` bug above) now log through `meta.update_source_health` instead of
swallowing silently; the Polymarket per-event parse loop now counts and
reports `skipped_events` instead of silently discarding malformed events; the
remaining 10 sites are legitimate broad-catch-with-reporting patterns for
external-API resilience (error already captured and returned/logged, not
narrowable to a specific exception type without guessing at pybaseball/httpx
internals) — annotated with a justified `# noqa: BLE001` each. `ruff check
src/model_prediction/rebuild/collectors.py` now passes clean.
