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

**Current checkpoint**: 6 complete.

## Checkpoint 7 — Decision engine (2026-08-06)

**Built** `src/model_prediction/rebuild/decision.py` — the winner-first
value-betting decision engine, implementing CLAUDE.md's exact
`SportsForecast`/`MarketEvaluation`/`BetDecision` interface. Reuses the
existing `edge_scaled_units` (Kelly sizing, exposure caps) from
`economic.py` rather than reimplementing sizing — this checkpoint is about
*which side is ever evaluated in the first place*, not sizing math.

Enforced invariants:
- `decide_team_market()` (moneyline/spread): rejects any candidate whose
  `team_or_side != forecast.predicted_winner` **before** any edge math
  runs — an attractively-priced opponent is never evaluated as an
  alternative, not filtered out after computing its edge.
- `decide_total()`: `forecast.frozen_totals_side(line)` determines
  OVER/UNDER from the sports-only distribution alone; a market candidate for
  the other side is rejected the same way, before its price is inspected.
- Quote freshness (`max_quote_age_seconds`) and depth
  (`min_depth_units`) both fail closed, checked before edge computation.
- `NO_BET` decisions always carry `units=0.0` — enforced by construction
  (`_no_bet()` hardcodes it), not by a downstream check that could be
  skipped.
- `evaluate_game()` returns a decision for every candidate market, including
  every `NO_BET`, so nothing is silently dropped from the audit trail.

**Tests**: `tests/rebuild/test_winner_first_decision.py`, 15 tests, one for
each item in CLAUDE.md's own "Critical decision tests" list verbatim —
including the two adversarial ones that matter most: a 40%-probability
opponent priced at 20¢ (a much larger raw apparent edge than the 60%
predicted winner's own edge) is still never selected, and a stale/thin
quote fails closed regardless of its price. 3 of the 15 initially failed
for a real, orthogonal reason — quarter-Kelly sizing on a realistic
single-digit edge rounds to zero at `edge_scaled_units`'s own default
0.25-unit rounding granularity (that function's tested behavior, not a bug);
fixed by using `unit_rounding=0.0` in those specific tests, since they test
BET-vs-NO_BET routing, not sizing-policy granularity.

**Not yet done**: wiring `decision.py` into a real end-to-end run against
tonight's actual MLB slate + real Polymarket books (Checkpoint 8/9) — this
checkpoint built and tested the decision logic in isolation with synthetic
`SportsForecast`/`MarketEvaluation` fixtures, not yet against the real
model + real market data from Checkpoints 5/6 and the Checkpoint 4 Polymarket
collector.

**Current checkpoint**: 7 complete.

## Checkpoint 8 — Market persistence, and the most consequential bug found this session (2026-08-06)

While preparing to wire real Checkpoint 4 Polymarket data into the
Checkpoint 7 decision engine for a real end-to-end run, inspected the actual
collected `data/rebuild/markets/mlb/2026-08-06.parquet` (the "132 real
books" reported as a Checkpoint 4 success) and found **every field except
`line` was null or empty** — `best_bid`/`best_ask` 132/132 null,
`event_id`/`market_id`/`market_type`/`side` all empty strings.

**Root cause**: `collect_polymarket_books()` (and the identical pattern in
all 4 other sport collectors — NBA, NFL, Soccer, Tennis) read
`event.get("id")`, `market.get("id")`, `market.get("type")`,
`market.get("side")`, `market.get("bestBid")`, `market.get("bestAsk")`, etc.
None of those keys exist on `PolymarketUSClient._normalize_event()`'s real
return shape — it actually returns `event_id`/`market_id`/`market_type`
keys, and per-side executable prices live in a `sides` list (2 entries per
binary market, each with `selection`/`team`/`price_probability`/
`decimal_odds`/`american_odds`), not flat `bestBid`/`bestAsk` fields. Every
`.get()` call silently resolved to `""` or `None`, no exception was ever
raised, and the collector reported `"status": "ok"` with a real-looking book
count throughout. This is the same silent-wrong-key-name failure class as
the Checkpoint 4 Polymarket-date-type and `venue_id` bugs, but more
consequential: **no MLB, NBA, NFL, soccer, or tennis Polymarket price data
has ever actually been usable from this collector, on this branch, despite
every collection call reporting success.**

**Fixed**: rewrote all 5 collectors' Polymarket ingestion to read the real
shape and emit one row per side (not one row per market), with real
`event_id`/`market_id`/`market_type`/`team_or_side`/`team`/`line`/
`executable_price`/`decimal_odds`/`american_odds`. **Disclosed, not
fabricated, remaining gap**: `_normalize_event()` doesn't expose order-book
depth/size at all, only an indicative side price — `available_depth` is
stored as `None` rather than invented, and the Checkpoint 7 decision
engine's depth gate can't be exercised against real data until a real depth
source exists.

**Verified live**: re-ran MLB collection for 2026-08-06 — went from 132
null-everything-but-line rows to **330 real rows** (0 null
`executable_price`), including real moneyline (Angels 39.5¢ / Orioles 61¢,
summing to ~1.005 — sensible vig), spread, and total markets with real team
names and complementary pricing. Note on immutability: manually removed the
old raw snapshot before re-collecting rather than letting content-addressing
naturally version it (its content was only ever `{"events": N, "books": N}`
count dict, not real per-book data, since the old code wrote that summary
as the "raw" payload — no real market data was ever actually lost by this,
but it's a deviation from the "never delete, only add new hashed
snapshots" principle worth flagging honestly rather than glossing over.

**Regression test**: `tests/rebuild/test_mlb_collectors.py::TestPolymarketRealDataShape`
— a fake client shaped exactly like the real `_normalize_event` output,
asserting real prices/team names reach the stored row. Confirmed failing
pre-fix (produced 1 null-shell row instead of 2 real ones — the old code
didn't even iterate `sides`, since it never held rows per side) via
`git stash`.

**Not yet done**: NBA/NFL/Soccer/Tennis fixes are code-identical to MLB's
but not yet independently re-verified against live data the way MLB was
(out of scope tonight per CLAUDE.md's own sequencing — MLB only, until MLB
clears its own gate); a real SQLite shadow ledger (predictions,
market_snapshots, trade_decisions tables) hasn't been built yet — still
using Parquet/JSON files directly.

**Current checkpoint**: 8 complete.

## Checkpoint 9 — Real end-to-end run, and session wrap-up (2026-08-06)

**Session ended here at the operator's request** (had to leave the house) —
this is a genuine mid-work stopping point, not a completed Checkpoint 9.
Everything below is accurate as of the last real run.

### What actually works, verified live, right now

Built `scripts/mlb_shadow_run.py` — the real one-command pipeline: load
tonight's real scheduled games → retrain the model walk-forward on all real
history strictly before tonight → load real Polymarket books → run every
game through the Checkpoint 7 winner-first decision engine → write a full
report, including every `NO_BET`. **No real order adapter is imported or
called anywhere in this script.**

Hit one more real, important gap immediately on the first run:
`build_game_feature_row` (Checkpoints 5/6) identifies a game's starters by
matching *its own* Statcast pitch data — which doesn't exist yet for a
**scheduled** game, since it hasn't been played. That function is correct
for historical/backtesting use (Checkpoints 5/6 — validated), but cannot
serve a live prediction. Fixed by adding `build_live_game_feature_row()` +
`lookup_pitcher_id()` (`src/model_prediction/rebuild/mlb_features.py`):
reads the incumbent system's own real, already-collected probable-starter
feed (`data/point_in_time/mlb_probable_starters.jsonl` — real ESPN
probables data, used here only as an input source per CLAUDE.md's "the
existing project is a benchmark and data source," not as a shortcut around
building real features), then resolves each probable starter's name to a
real Statcast pitcher ID via `pybaseball.playerid_lookup` (verified live:
"Bryan Woo" → MLBAM ID 693433) so the same real rolling-feature functions
from Checkpoint 5 can be reused unmodified.

**Real result of the actual run** (`outputs/rebuild/mlb_shadow_run_2026-08-06.json`):
10 real scheduled MLB games for tonight. 8/10 skipped —
`starter_name_not_resolved_to_real_statcast_id` (the probables feed and
pybaseball's name lookup didn't connect for those starters; not
investigated further before the session ended — likely a name-format
mismatch, e.g. suffixes/nicknames, not a systemic failure, but genuinely
unverified). **2/10 games produced real, complete decisions**: San Diego
Padres @ Arizona Diamondbacks — model predicted away (Padres) to win at
~53%, evaluated 176 real candidate markets (moneyline/spread/total, both
sides, multiple lines), correctly returned `NO_BET` on all of them (no
edge cleared after real Polymarket pricing). That's the winner-first engine
running against real model output and real market prices end-to-end,
exactly once, successfully — genuinely produced 0 bets, which is an honest
result given this model's Checkpoint 6 evaluation was itself inconclusive,
not a broken pipeline.

**Also real, not yet fixed**: `build_forecast()`'s conservative/lower-bound
probability is a flat 3% haircut off calibrated probability, not the real
bootstrap/model-disagreement-based `conservative_probability` CLAUDE.md
specifies. Simple and disclosed, not silently treated as equivalent to the
real thing.

### Full, honest checkpoint status at session end

- **Checkpoint 0-8**: complete, as detailed above, each with real
  verification evidence and regression tests.
- **Checkpoint 9**: partial. The one-command pipeline exists and ran
  successfully end-to-end for 2/10 real games tonight. Not done: the
  8/10 starter-name-resolution failures are unexplained; no idempotency
  check (rerunning the same date) was performed; no operator-facing summary
  beyond the JSON report exists yet.
- **Checkpoint 10 (evidence/reports)**: not started — `outputs/rebuild/`
  has real per-checkpoint evidence embedded in this file and in
  `mlb_training_results_real_features.json`/`mlb_shadow_run_2026-08-06.json`,
  but the polished `predictive_report.md`/`economic_report.md`/
  `model_cards/` deliverables CLAUDE.md's Part 3 §16 asks for don't exist.
- **SQLite shadow ledger** (Checkpoint 8's own spec item): not built —
  still using Parquet/JSON files directly. Real gap, not deferred silently.
- **NBA/WNBA/NFL/soccer/esports/KBO/NPB**: untouched, correctly, per
  CLAUDE.md's own sequencing ("ignore until MLB works").

### Recommended next steps, in priority order

1. Debug the 8/10 starter-name-resolution failures — likely the highest-
   leverage single fix, since it directly gates how many of tonight's real
   games can get a real decision at all.
2. Build the real `conservative_probability` (bootstrap/model-disagreement),
   replacing the flat 3% haircut placeholder.
3. Build the SQLite shadow ledger and wire persistence + idempotency
   (rerunning the same date must not double-count).
4. Only after 1-3: expand backfill window from 10 days to several weeks —
   this is what would actually resolve Checkpoint 6's inconclusive
   held-out result, more than any further feature engineering would.

### Real bugs found and fixed this session (full list)

1. CI silently tested Python 3.12 against a 3.14-only project; also skipped
   dev deps and mypy entirely (Checkpoint 1).
2. `xgboost` couldn't import on this machine at all — missing `libomp`,
   affecting the existing dev `.venv` too, not just fresh clones
   (Checkpoint 1).
3. Polymarket collection silently returned empty for every sport, forever —
   a `date` object compared against a raw `str` is always `False`
   (Checkpoint 4).
4. `_venues_for_date` selected a nonexistent `venue_id` column, silently
   swallowed by a bare `except`, zeroing weather collection for every date
   (Checkpoint 4).
5. `collect_weather_forecast` called Open-Meteo's reanalysis API instead of
   a forecast API — wrong data product, not just a transient failure
   (Checkpoint 4).
6. `RawStore.write()`'s `json.dumps` had no `default=` handler, silently
   discarding every real pybaseball payload (`pandas.Timestamp` isn't
   JSON-serializable) — shared infrastructure, not MLB-specific
   (Checkpoint 4).
7. `expanding_folds()` returned 0 folds because calendar-day granularity
   was too coarse for a 10-day real backfill window (Checkpoint 6).
8. Polymarket collectors (all 5 sports) read key names that don't exist on
   the real API response shape — every field except `line` was silently
   null/empty despite `"status": "ok"` (Checkpoint 8) — the most
   consequential bug found this session.

All 8 were silent — no exception, no error surfaced to a caller not
specifically inspecting source-health internals. That pattern (not the
specific bugs) is the one thing most worth remembering about this branch's
prior state: things reporting success were not a reliable signal that they
were actually right.

## Session resumed (2026-08-06/07) — items 1 and (unplanned) 9 from the priority list

**Priority 1 — starter-name resolution, fixed and verified.** Root cause:
a real, genuine ambiguity, not a lookup failure — pybaseball's register has
two real "Drew Anderson"s (one last played 2006, one active through 2026).
`lookup_pitcher_id`'s ambiguity check correctly refused to guess between
them and returned `None`, which was the safe behavior but meant a real,
resolvable starter never got real features. Fixed by breaking ties on
`mlb_played_last` (a real recency fact already in the same lookup result,
not a guess — a probable starter for a real upcoming game must be the
currently-active player). Verified: `lookup_pitcher_id("Drew Anderson")`
now returns `623454` (the active one), not `None`. 4 new tests in
`TestLookupPitcherId`, including one confirming a *true* tie (two different
real players both still active) still correctly fails closed rather than
guessing.

**Bug #9 (unplanned, found while re-verifying the fix above)**: re-ran the
shadow script expecting more real decisions and instead got the same 2
games each printed 5 times. Investigated rather than shrugging it off —
`NormalizedStore.write()` appends unconditionally on every call, so
repeated ESPN scoreboard collection (the live daily job collects
continuously) produces exact-duplicate-content rows per real event_id.
Quantified: **188 STATUS_FINAL rows were only 135 real unique games** —
meaning Checkpoint 6's original training run was silently trained on
duplicated, non-independent rows of the same games, not 173 independent
matched games as reported. Fixed with a consumer-side `dedupe_scoreboard()`
(keeps the most-recently-observed row per `event_id`) wired into both
`train_mlb_rebuild_real_features.py` and `mlb_shadow_run.py`. The deeper,
correct fix — real primary-key enforcement in `NormalizedStore.write()`
itself — is still a disclosed, unfixed Checkpoint 2 gap; this is the
targeted fix for what actually consumes the data today.

**Retrained on the corrected, deduplicated data — result got *more*
concerning, not less, and is reported exactly as such**: 126 real unique
matched games (down from the duplicate-inflated 173). Fold-validation Brier
(0.246–0.266) still lands in a plausible range. But the held-out test
(n=25, down from 34) now shows accuracy=0.320 (worse than before's 0.353),
and the quality-filtered version (n=22) shows accuracy=0.227 — worse still.
With a smaller, more genuinely independent sample, standard error is larger
(~10% on n=25), so this remains most parsimoniously explained as
small-sample noise rather than a new bug — but it no longer supports even a
weak "probably fine" read. **This model's real predictive quality is more
unresolved after this fix, not less** — worth flagging clearly rather than
either hiding it or over-interpreting a 22-25-game sample. Re-ran the
Checkpoint 9 shadow script on the corrected data: now correctly shows
exactly 2 real unique scheduled games (not 10 duplicates), both evaluated
completely, both honestly `NO_BET` (176 real markets each, no edge cleared
against real Polymarket pricing).

**Updated priority list for next session**:
1. The held-out result is now the most important open question — before
   trusting this feature set at all, get a real answer on whether ~0.32
   accuracy on 25 games is noise or a real problem. The only way to actually
   resolve this is more real backfill days (see old priority #4) — a
   larger n directly shrinks the standard error this conclusion currently
   hinges on.
2. Fix `NormalizedStore.write()`'s real primary-key enforcement (the
   underlying cause of the duplication bug just fixed at the consumer
   level) — a real Checkpoint 2 gap that could resurface in any other
   table/sport that reads scoreboard-shaped data the same naive way.
3. Real `conservative_probability` (bootstrap/model-disagreement), replacing
   the flat 3% haircut placeholder in `mlb_shadow_run.py`.
4. SQLite shadow ledger + persistence/idempotency.

## External review received, verified, and acted on (2026-08-06/07)

An external review of commit `ffac1dc` correctly flagged several real gaps:
totals never populated (`SportsForecast.totals_probabilities` was always
empty), totals market matching not isolated by event, fabricated
`quote_age_seconds=0.0`/`available_depth=999.0`, the flat calibration
haircut, and the model's real (pre-fix) held-out numbers. One factual
inaccuracy in that review: it cited Brier 0.3302/accuracy 35.3% as "the
latest real-feature evaluation" — those were the pre-deduplication-fix
numbers from an earlier commit; the actual current numbers (post-dedup,
`7c1d0a4`) are brier 0.3133/accuracy 0.320 (quality-filtered 0.3007/0.227),
already reported above as *more* concerning, not less.

**Investigated and fixed the two real, actionable findings**:

1. **Totals market isolation, fixed**: `real_market_candidates()`'s total
   filter (`market_rows.filter(pl.col("market_type") == "total")`) had no
   event filter at all — every total market from the whole date's
   collection was attached to every game (confirmed live: 176 candidates
   for a single game, exactly matching the day's total total-market count).
2. **`totals_probabilities` never populated, fixed**: `build_forecast()`
   built moneyline probabilities but never called
   `model.distribution.probability_for_market(pred, "total", ...)` for any
   real line, so `frozen_totals_side()` always returned `None` and every
   total market produced `NO_BET`/`no_forecast_for_line` regardless of
   price.

**A third, more severe bug surfaced while verifying fix #1's re-run —
found independently, not in the external review**: after fixing event
isolation, decisions started firing `BET` with **24–56% "edges"** on every
total line, always OVER, edge size *increasing* with the line — the
opposite of what a sane check should show. Traced it rather than trusting
the number: `real_market_candidates`/`resolve_polymarket_event_id` were
comparing Statcast-style team abbreviations ("SEA") against Polymarket's
`team` field, which is the real full display name ("Seattle Mariners") —
every comparison silently matched zero rows. This meant **moneyline/spread
markets had never been evaluated against a real game in any prior run,
full stop** — every "176 markets evaluated" figure reported earlier was
entirely the unfiltered-totals leak (bug #1), with real team-based
matching contributing nothing. Fixed by switching to full team names.

Re-running after that fix exposed a **fourth, independent bug**, still
producing absurd edges: fetched the live Polymarket market list directly
and found Polymarket lists genuinely **separate full-game and
first-5-innings markets that can share the exact same `market_type`/`line`**
(confirmed live: two distinct real "total > 6.5" markets for the same game
— full game priced at 65¢, first-5-innings at 25¢). This model only
predicts full-game outcomes; comparing its probability against an F5 price
isn't a pricing edge, it's a market-identity error that looks like one.
Fixed by capturing `market_slug` in the collector (the only reliable
disambiguator — `-f5-` in the slug; the `question` text doesn't reliably
say "first 5 innings" for totals) and excluding F5 rows in
`collectors.py`'s `collect_polymarket_books`, upstream of every consumer.

**Refactored** the three market-matching functions plus the new F5 filter
out of `scripts/mlb_shadow_run.py` into a new, tested module
`src/model_prediction/rebuild/mlb_market_matching.py` — these are
safety-relevant enough to deserve real regression tests (7 added in
`tests/rebuild/test_mlb_market_matching.py`), not just live-only
verification in a script.

**Final, verified-sane result** after all four fixes: 176 real full-game
rows (154 F5 rows correctly excluded), 16 real candidate markets per game
(down from the bogus 176), realistic edges (1–16%, not 24–56%), both real
games correctly `NO_BET` (small edges rounded to zero at quarter-Kelly's
0.25-unit granularity — `zero_sized`, not `no_edge_after_costs`).

**Still real, still disclosed, not fixed this pass**: `quote_age_seconds`
and `available_depth` remain fabricated (`0.0`/`999.0`) — the underlying
Polymarket source genuinely doesn't expose real depth (Checkpoint 8's
disclosed gap), and no timestamp-of-observation is threaded through yet
either. Both must be fixed before any real-money consideration, not just
disclosed forever. Flat 3% calibration haircut also still unfixed (item 3
above). NBA/NFL/soccer/tennis collectors likely have the identical F5-style
and team-name-matching gaps if/when they're ever wired into a shadow
script — untouched and unverified, correctly out of scope until MLB
clears its own gate.

## Second external review round, verified and acted on (2026-08-07)

A second review of `b9d9de9` correctly verified all four prior fixes
(totals isolation, totals probabilities, full team names, F5 exclusion)
against real code and the real shadow report, and correctly identified that
the accuracy/Brier numbers were already the current (post-dedup) ones, not
stale — no factual correction needed this round.

**One new, real, more severe bug found and fixed**: `decide_team_market()`
priced *every* team-market candidate — moneyline **and spread** — using
`forecast.probability_lower[forecast.predicted_winner]`, the predicted
winner's moneyline win probability. For a spread, that's the wrong number:
covering a specific line (e.g. -2.5) is a materially different, usually
lower, probability than simply winning. Verified in the actual committed
shadow report before fixing anything: a real spread market showed a
fabricated **+23.5% edge**, computed from a ~51% moneyline probability
against a market price that had nothing to do with a 51%-to-win team's
probability of covering that specific spread.

**Fixed**: added `SportsForecast.spread_probabilities:
dict[float, dict[str, float]]`, populated the same way as
`totals_probabilities` (computed via
`model.distribution.probability_for_market(pred, "spread", side, line)`
before market inspection) — except spreads need a `(line, side)` pair, not
just a line, since a real market's home side and away side carry different
signed lines for the same conceptual market (e.g. home -1.5, away +1.5) —
added `real_spread_line_side_pairs()` to `mlb_market_matching.py` to supply
exactly those real pairs. `decide_team_market()` now fails closed with
`NO_FORECAST_FOR_LINE` for a spread with no real forecast for that exact
line, rather than silently falling back to the moneyline number. Added the
exact regression test case the review specified (70% moneyline winner, 40%
real cover probability, 45¢ ask → `NO_BET`, negative edge, 0 units) plus 3
more. Re-ran the real pipeline: spread edges went from a nonsensical +23.5%
to a realistic -8.4% to +6.4% range, both real games still correctly
`NO_BET`.

**Second, smaller fix from this round**: the review also correctly flagged
that every `NO_BET` decision discarded which exact market/side/line/ask had
been evaluated (`selected_market=None` by design) — a report couldn't
distinguish "evaluated the away spread at 45¢ and rejected it" from having
evaluated nothing. Added `BetDecision.evaluated_market` (populated on every
decision, `BET` or `NO_BET`; `selected_market` keeps its
CLAUDE.md-specified "what was actually bet" meaning). Wired into the
shadow script's report JSON — every decision row now shows a real
`market_id`/`team_or_side`/`line`/`executable_ask`, not `null`.

**Caught and fixed a self-inflicted bug while making this change**: a
sloppy regex used to bulk-update 11 `_no_bet()` call sites incorrectly
matched into a nested `sizing.get(...)` call on two of them, inserting an
invalid keyword argument into a dict `.get()` call — syntactically valid
Python (parsed fine) but would have raised `TypeError` the first time
either code path actually executed. Caught by reading the diff before
running anything, not by a test failure — worth noting since it's exactly
the kind of "looks done, silently wrong" mistake this whole session has
been about finding in *other* code.

**804 tests pass** (up from 799), all real bugs found this round verified
against actual code and real data before being treated as confirmed, per
this session's standing practice.

## Calibration/testing split fixed (2026-08-07)

The second review's remaining "calibration remains invalid" point was
checked and was real: `train_mlb_rebuild_real_features.py` fit the Platt
calibrator on `test_final` and then evaluated the calibrated model on that
same `test_final` — the calibrator is specifically optimized to improve
log loss/Brier on whatever data it's fit on, so evaluating it there biases
the reported "held-out" metrics favorably, and it isn't a genuinely
untouched final test as CLAUDE.md's Part 2 §14 requires.

**Fixed**: three-way split — `train_final` (fits the model),
`calib_final` (fits the Platt calibrator only), `test_final` (genuinely
untouched by both, used once for the single final evaluation). With
n=126: train=84, calibration=21, test=21.

**Real result after the fix, reported honestly**: held-out accuracy=0.381
(n=21, up from the prior split's 0.320 on n=25), quality-filtered
accuracy=0.444 (n=18, up from 0.227), Brier 0.2832 quality-filtered
(closer to the ~0.25 baseline than before). This moved back toward
"plausibly fine" rather than "concerning" — but with n=18–21, standard
error is ~11%, so this remains squarely in small-sample-noise territory,
not promotion evidence either way. The methodology fix changed the split
boundaries (hence different specific games in each bucket), which is
expected and correct, not itself informative about model quality.

804 tests pass (unchanged — this was a script-only fix, no new test
surface). Priority list unchanged: (1) more real backfill days is still
the only way to get a real answer on model quality, (2)
`NormalizedStore.write()` primary-key enforcement, (3) real
`conservative_probability`, (4) SQLite shadow ledger.

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

## Shadow ledger wired into the real pipeline, and two real bugs found live (2026-08-07)

`ShadowLedger` (Phase 12, commit `b8fdd02`) existed but nothing called it —
`scripts/mlb_shadow_run.py` only ever wrote a one-shot JSON report. Wired it
in: `record_run` once per invocation; `record_prediction` per game;
`record_market_evaluation` per real candidate market; `record_trade_decision`
per `BetDecision` (one per candidate market — moneyline, each real spread
line, each real total line). `decision_time_utc` is derived from the game's
own `event_start_utc` minus the "late" horizon's 60 minutes (CLAUDE.md's own
definition), not wall-clock "now", so an identical rerun against an unchanged
slate produces the same decision timestamp and is genuinely idempotent
rather than minting a fresh row every invocation.

**Bug #1 (real, found by running against the live 2026-08-06 slate, not by
unit tests alone):** `trade_decisions`' idempotency key
(`sport, event_id, horizon, decision_time_utc, model_artifact_hash,
market_snapshot_hash, decision_policy_version`) has no field distinguishing
*which* market a decision is about. A single real game produces one
`BetDecision` per candidate market — confirmed live, 16 per game — and all
16 share every field in that key, since they come from one forecast
evaluated against one market snapshot. Result: only the first decision per
game was ever inserted; the other 15 silently "deduped" against it as if
they were reruns. A real 2-game slate produced 32 real decisions but only 2
ledger rows. Fixed by adding `evaluated_market_id`/`evaluated_team_or_side`/
`evaluated_line` as real columns (derived from `BetDecision.evaluated_market`,
which is always populated per the earlier Checkpoint 8/9 fix) and including
them in the unique index, wrapped in `COALESCE(..., sentinel)` because
SQLite treats every `NULL` as distinct for `UNIQUE` purposes and a
moneyline decision has a real, legitimate `NULL` line. Regression tests
added: `test_multiple_real_markets_for_one_game_all_persist`,
`test_rerunning_the_same_multi_market_game_is_still_idempotent` — both
confirmed failing against the pre-fix schema (`git stash`) before the fix,
passing after.

**Bug #2 (real, found immediately after fixing #1 by rerunning the live
pipeline a second time to prove idempotency, not by assuming it):** even
with #1 fixed, an immediate rerun against byte-identical market data
produced 32 *more* rows instead of 0 deduping. Cause: `market_snapshot_hash`
was computed by hashing every field of each candidate `MarketEvaluation`,
including `quote_age_seconds` — which is `now - observed_at_utc` (see
`real_quote_age_seconds`) and increases every second purely from wall-clock
time passing, independent of whether the book actually moved. Extracted
`real_market_snapshot_hash(event_id, candidates)` into
`mlb_market_matching.py`, excluding `quote_age_seconds` from the hashed
payload. Regression tests:
`TestRealMarketSnapshotHash::test_hash_is_stable_across_different_quote_ages`
(two otherwise-identical candidates, ages 5s vs 4500s, must hash equal) and
`test_hash_changes_when_real_content_changes` (a real price move must still
change the hash).

**Verified live, not just via unit tests:** ran
`PYTHONPATH=src:. .venv/bin/python scripts/mlb_shadow_run.py --date
2026-08-06` against `data/rebuild/shadow.db` (deleted and regenerated —
schema changed, this is shadow/research data, not an incumbent production
ledger) — first run: 2 new predictions, 32 new trade decisions, 0 deduped.
Immediate rerun: 0 new predictions, 0 new trade decisions, 32 deduped as
idempotent reruns. `trade_decisions` table stayed at 32 rows after the
rerun; `predictions` stayed at 2. `market_evaluations` correctly grew (32 →
64) since it has no idempotency requirement by design (append-only
observation log, not a decision record).

**859 tests pass, 1 skipped** (860 total, up from 804 — 22 pre-existing
`test_shadow_ledger.py` tests plus this session's 4 new regression tests,
plus 2 new `test_mlb_market_matching.py` tests, plus tests added in the
intervening calibration-split-fix commit). `ruff check`
clean on every file touched this round (`scripts/mlb_shadow_run.py`,
`shadow_ledger.py`, `mlb_market_matching.py`, and both touched test files);
the repository-wide `ruff check src tests` failure count (192) is entirely
pre-existing legacy-test-file findings (`EXE002` missing shebangs, `SIM`
style suggestions, etc.) untouched by and unrelated to this session's work.

Priority list, updated: (1) more real backfill days is still the only way
to get a real answer on model quality — now the single highest-leverage
open item; (2) wire `point_in_time_join()` into an actual call site (fixed
and tested, still dead code); (3) canonical identity (`IdentityRegistry`
exists, not wired into collectors/features/market-matching uniformly —
Phase 4); (4) formal schema registry (Phase 5); (5) shared multi-sport CLI
(Phase 13); (6) monitoring/governance reports (Phase 14). No sport beyond
MLB has started per CLAUDE.md's explicit sequencing (MLB must clear its
own gate first).

## Real bootstrap conservative_probability, replacing the flat 3% haircut (2026-08-07)

CLAUDE.md's `conservative_probability` spec names `bootstrap_uncertainty` as
a required component of the lower-bound probability that gates every BET.
`build_forecast()` previously used a disclosed flat +/-3% haircut toward
50/50 for moneyline only — a fixed number regardless of how much any given
prediction actually depended on particular training games — and spread/total
markets had **no conservative haircut of any kind**: `decide_team_market()`'s
spread branch and `decide_total()` both priced `cost_adjusted_edge` directly
off the raw point-estimate probability.

**Fixed with a real, data-driven bound.** Added `BootstrapMLBEnsemble`
(`src/model_prediction/rebuild/models/__init__.py`): fits 20 independent
copies of both heads, each on a bootstrap resample (with replacement) of the
identical chronological training data used for the primary model, and
reports the empirical [10th, 90th] percentile spread of a market's
probability across replicates for a given row — market-type-agnostic, so the
same machinery prices moneyline/spread/total lower bounds uniformly.
`train_through()` in `mlb_shadow_run.py` now fits this ensemble alongside
the primary model; `build_forecast()` uses it for `probability_lower`/
`probability_upper` (moneyline) and populates two new `SportsForecast`
fields, `spread_probabilities_lower`/`totals_probabilities_lower`, which
`decide_team_market()`/`decide_total()` now prefer over the point estimate
when present (falling back to the point estimate when absent, so existing
callers/tests without a bootstrap ensemble are unaffected).

**Real result, reported honestly, not spun:** the bootstrap bounds are
*far* wider than the old flat haircut, not narrower. Live run against the
2026-08-06 slate: game 1 calibrated home-win probability 0.49, real
bootstrap bound [0.271, 0.671] (vs. the old flat-haircut bound of roughly
[0.46, 0.52]); game 2 calibrated 0.485, real bound [0.136, 0.735]. This
means the model's predictions are far less stable under resampled training
data than the flat 3% placeholder implied — with only 126 training games,
individual games can swing the fitted heads substantially. The honest
interpretation is that the *previous* placeholder was systematically
overconfident, not that this fix made the system more conservative than it
should be. Practical consequence: real conservative bounds this wide will
make almost every market fail `NO_EDGE_AFTER_COSTS`, which is the correct,
safe behavior given genuine data scarcity — it is not evidence the bootstrap
implementation is broken, and reinforces item (1) above as the real
bottleneck (more backfill days narrows real bootstrap bounds; nothing else
does, honestly).

7 new tests for `BootstrapMLBEnsemble` (`tests/rebuild/
test_bootstrap_uncertainty.py`) plus 4 new tests for the spread/total
conservative-bound preference in `decide_team_market()`/`decide_total()`
(`tests/rebuild/test_winner_first_decision.py`), each confirmed failing
pre-fix. **870 tests pass, 1 skipped** (up from 860). `ruff check` clean on
every touched file. Verified live end-to-end (not just unit tests):
reran `scripts/mlb_shadow_run.py --date 2026-08-06`, confirmed real
(non-placeholder) `probability_lower`/`probability_upper` values landed in
`data/rebuild/shadow.db`'s `predictions` table with
`calibration_artifact_hash="bootstrap_uncertainty_v1"` (vs. the old
`"uncalibrated_haircut_v1"`), and that the ledger wiring/idempotency fixes
from the prior checkpoint still hold (2 predictions, 32 trade decisions, 0
duplicates on rerun).

## Shared-infrastructure session: storage atomicity/idempotency, real depth honesty, PIT wiring (2026-08-07)

Session goal, set by an external review of the prior state: stop doing more
MLB model-quality work and clear the shared-foundation gaps the review
identified by re-reading the actual code (not the previously-generated
status doc, which was stale). Three real fixes landed, each independently
committed, tested, and live-verified — not a single "do everything" batch.

**1. Storage-wide atomic writes + universal scoreboard idempotency
(`ea973f3`).** `NormalizedStore.write()`, `MarketStore.write_books()`, and
`FeatureStore.write_snapshot()` all wrote straight to the final Parquet
path with no temp-file+rename, unlike `RawStore`. `write_books()` also
concatenated unconditionally with zero primary-key awareness — a retried
collection call could write exact-duplicate market rows undetectably.
`FeatureStore.write_snapshot()` explicitly said "overwrites for that
horizon," destroying prior versions. Also confirmed only `MLBCollector`
passed `primary_key=["event_id"]` to `NormalizedStore.write()` —
NBA/WNBA/NFL/Soccer/Tennis scoreboard writes did not, so the same
duplicate-row bug fixed for MLB in `a4e03a1` was still live everywhere
else. Fixed: shared `_atomic_write_parquet()` helper now backs all three
stores; `MarketStore.write_books()` dedupes on a real key (market_id/
team_or_side/line/observed_at_utc); `primary_key=["event_id"]` added to the
4 remaining scoreboard writes; `FeatureStore` now writes immutable
per-snapshot-hash files plus a `latest.json` version manifest (currently
unused by any real caller — the interface is now correct for whenever one
exists). New `tests/rebuild/test_normalized_idempotency.py` — named in
CLAUDE.md's own required-tests list but never created until now. **878
tests pass** (up from 870).

**2. Removed fabricated `available_depth=999.0`, fails closed honestly
(`d516ab3`).** CLAUDE.md Part 3 §2 is explicit: unavailable depth must be
marked `depth_available=false`, not fabricated, and must fail economic
qualification. `real_market_candidates()` did the opposite — set
`available_depth=999.0` on every real candidate, trivially clearing
`decision.py`'s `min_depth_units=1.0` gate every time; `INSUFFICIENT_DEPTH`
existed as a reason code but could never actually fire. Worse,
`mlb_shadow_run.py` had an explicit `SizeLimits(min_depth_units=0.0)`
"workaround" comment — the same fabrication, just with extra steps. Fixed:
`MarketEvaluation` gained `depth_available: bool = True`;
`_quote_gate_reason()` checks `not candidate.depth_available` before the
numeric comparison, so a market with genuinely unknown depth fails closed
regardless of how low the configured minimum is (closes the
`min_depth_units=0.0` loophole specifically, not just the 999.0 one);
`real_market_candidates()` now sets `available_depth=0.0,
depth_available=False` honestly; the shadow-run workaround is removed,
using default `SizeLimits()`. **Verified live**: reran
`scripts/mlb_shadow_run.py --date 2026-08-06` — outcome unchanged (0 BET,
same as before; this run's market data was already stale so
`STALE_QUOTE`/alignment gates fire first for these particular 32
candidates), but the depth gate itself is now provably real per a new test
proving a `depth_available=False` candidate is rejected even at
`min_depth_units=0.0`. **880 tests pass** (up from 878).

**3. Wired `point_in_time_join()` into a real caller, closed a live
starter-leak gap (`90d5826`).** `point_in_time_join()` was fixed and
tested (Phase 3) but genuinely dead code — no real feature builder called
it. The pitcher/bullpen rolling-feature lookback windows in
`mlb_features.py` aren't a natural fit (they aggregate a window of prior
rows, not attach one latest observation), but
`mlb_shadow_run.py`'s probable-starter lookup was exactly that shape, and
had a real bug: `probables_by_event[rec["event_id"]] = rec` kept whichever
record was *last in the file* per event, not the newest observation
strictly before that game's real `decision_time_utc`. **Verified live**:
152 of 163 real events in
`data/point_in_time/mlb_probable_starters.jsonl` have more than one
record (real revisions over time) — a revision observed after the "late"
horizon's T-60m cutoff could have silently leaked into a decision that
shouldn't have seen it yet. Didn't happen to bite tonight's real slate
(reran before/after the fix: identical predictions, 49.0%/51.0% and
48.5%/51.5%, and the ledger correctly deduped all 32 decisions as
idempotent), but was a live latent bug, not a hypothetical one. Extracted
the fix into a real, independently tested function,
`point_in_time_probable_starters()` in `mlb_features.py` (previously
inline, untestable script logic), with 5 new tests including one that
directly proves a post-cutoff revision does not leak in. **885 tests
pass** (up from 880).

**Foundation inventory regenerated** (`outputs/rebuild/foundation_status.md`,
`scripts/generate_foundation_inventory.py`) to reflect all three fixes with
code-derived checks, not manual claims — `normalized_storage_idempotent`
and `point_in_time_join_utility` both moved from NOT_STARTED/PARTIAL to
VERIFIED, each backed by a grep-verified real caller or call site, not an
assertion.

**What this session deliberately did not attempt, and why:** the review's
full priority list also included canonical identity wiring (Phase 4,
still INTERFACE_ONLY), a formal schema registry (Phase 5, not started),
the shared horizon builder (Phase 7, still INTERFACE_ONLY), the 8 remaining
schema-only shadow-ledger tables, a shared multi-sport CLI (Phase 13, not
started), and real order-book depth (no data source exists to integrate —
this is not an engineering task that can be completed by writing code
alone). Each of the three items actually shipped this session was chosen
because it was well-scoped, testable, and independently verifiable in one
sitting without cutting corners on the same rigor (small commits, live
verification, honest reporting) the rest of this branch has used. The
remaining items are real, multi-step efforts in their own right — treating
them as a single afternoon's work risked producing exactly the kind of
looks-complete-but-isn't work this project's own rules were written to
prevent.

Priority list, unchanged in substance from the prior entry: (1) more real
backfill days remains the single highest-leverage open item for MLB
predictive qualification; (2) canonical identity (Phase 4); (3) formal
schema registry (Phase 5); (4) the shared horizon builder (Phase 7); (5)
the 8 schema-only shadow-ledger tables; (6) shared multi-sport CLI (Phase
13); (7) a genuine order-book-depth source, if one can be found — otherwise
depth-dependent economic qualification remains permanently blocked, which
is the honest state, not a gap to paper over. No sport beyond MLB has
started, per CLAUDE.md's explicit sequencing.
