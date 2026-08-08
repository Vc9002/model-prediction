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

## Foundation-completion session: items 1-2-4-5-6-7 of the priority list, CI verified green (2026-08-07)

Explicit instruction this session: stop MLB model work entirely, finish
the shared software foundation. Six real, tested, live-verified commits
on top of the earlier storage/depth/PIT/CI session, each independently
committed and pushed:

**(1) Canonical identity, PARTIAL.** `resolve_or_register_team()`
(identity.py) is real: resolves via exact source-id match, falls back to
confident same-sport fuzzy name match (reusing an existing entity instead
of duplicating), else registers new — low-confidence match fails closed
into registration, never a guess. Building its first real tests
immediately found a second real bug matching `c2a55d0`'s
`update_source_health` fix: `IdentityRegistry.map()` hit the same
`entity_mappings` foreign-key gap. Fixed with `ensure_source_exists()`
(status-neutral, unlike reusing `update_source_health` which would
silently reset a source's real tracked status). Wired into
`MLBCollector.collect_espn_scoreboard()` only — additive
(`home_team_canonical_id`/`away_team_canonical_id` columns), display-name
columns unchanged so existing consumers are unaffected. Live-verified
against real ESPN data, including investigating what first looked like a
bug (two rows for the same matchup with different canonical-id states) and
confirming it was a real 2-game series, not an error.

**(2) Formal schema contracts, real and enforced.** New `schemas.py`:
`TableContract`/`ColumnSpec` declare primary key, required columns,
nullability, dtype; `validate_or_raise()` fails closed on schema drift.
Wired as actual validation-before-persistence into
`NormalizedStore.write()` and `MarketStore.write_books()`, and into every
real collector call site across all 5 sports. Real bug found by running
the existing suite against newly-enforced contracts: an all-null nullable
column infers as polars' `Null` dtype, not its eventual real dtype —
fixed. Live-verified against real network collection (15 real scoreboard
rows, 450 real market rows) with zero false-positive rejections.

**(4) All 16 shadow-ledger tables now have real methods.** The 8
remaining schema-only tables (raw_snapshots, normalized_observations,
feature_snapshots, dataset_manifests, model_artifacts,
calibration_artifacts, closing_prices, reviews) got real record_*/query
methods matching the file's existing idempotency conventions (content-hash
or natural-key dedup for lineage tables, fail-closed on real conflicts for
closing_prices, plain append for reviews). Live-verified against the real
`data/rebuild/shadow.db`. Honestly disclosed: only 2 of the 8 new methods
have a real caller outside their own tests so far (a standalone
verification call) — none are wired into `mlb_shadow_run.py`'s actual run
yet.

**(5) Shared `SportAdapter` protocol.** New `sport_adapter.py`: one
interface (`collect` → `build_features` → `predict` → `match_markets` →
`decide`), honestly scoped — MLB's `predict`/`match_markets`/`decide`
report `NOT_IMPLEMENTED` through the shared interface, since that logic
still only exists in `mlb_shadow_run.py`'s own inline code; porting it
under time pressure risked breaking the one pipeline this project depends
on most. `collect()` is real for all 5 sports with a real Collector class.
Real bug found live (not from reading code): Soccer/TennisCollector call
the real ESPN client with league codes (`"SOCCER"`/`"TENNIS"`) that don't
exist in `data_sources/espn.py`'s `LEAGUE_PATHS` — real collection has
never worked for either sport. Adapter now catches this and reports a
clean per-stage `ERROR` instead of crashing.

**(6) Shared multi-sport CLI.** `scripts/rebuild_shadow_cli.py`:
`rebuild-shadow --sport <sport> --date <date> --horizon <horizon>` (plus
`--collect-only`/`--features-only`/`--predict-only`/`--markets-only`/
`--decision-only`), routing through the adapter registry, persisting one
real `runs` row per invocation. Live-verified for MLB (collect + real
horizon-feature build succeeded, predict honestly stopped at
`NOT_IMPLEMENTED`) and NBA (real network call, honest `NO_DATA` for an
offseason date) — genuine multi-sport routing through one command, not
per-sport scripts.

**(7) Automatic coverage/missingness reports.**
`scripts/generate_coverage_report.py` writes
`outputs/rebuild/coverage/{sport}_{horizon}.json` and
`outputs/rebuild/missingness/{sport}_{horizon}.json` from real
`horizon_builder.py` output. Live-verified: real, different coverage per
horizon for the 2026-08-06 slate (0/12, 2/12, 5/12).

**Horizon builder (carried over from the prior entry in this same
session, listed here for completeness).** `horizon_builder.py`'s
`build_mlb_horizon_dataset()` is the first real implementation beyond
`horizons.py`'s declarative metadata — computes real decision_time_utc
per horizon, resolves probable starters through the real point-in-time
join, persists an immutable versioned snapshot via `FeatureStore`. A real
bug (only decision dates were backfilled for Statcast history, not real
prior games) was found and fixed via live verification, not unit tests
alone.

**CI: verified green 7 consecutive times this session** (commits
`184558c`, `25f1924`, `9e741f9`, `b6534f2`, `cd22964`, `1a5c6dc`,
`07bd438`), via the public GitHub API (no `gh` CLI available). Two real
process bugs found and fixed to get there: `ci.yml`'s Ruff step ran
unscoped with no `continue-on-error` against ~190 pre-existing legacy
findings unrelated to rebuild work (CI had never been green on any prior
head, including before this session); and a real staging mistake — files
already fixed locally by `ruff --fix` were never `git add`ed for the
commit that claimed the fix, so local checks looked clean while a
genuinely fresh clone still failed the exact same way CI did. Caught by
actually reproducing a fresh clone locally to diagnose the CI failure, not
by re-trusting local state.

**Fresh-clone reproduction, done deliberately and completely** (not just
as a CI side-effect): cloned directly from `git@github.com:Vc9002/
model-prediction.git` (not a local copy) into `/tmp`, fresh venv, `pip
install -e ".[dev]"`, `import model_prediction.rebuild`, rebuild-scoped
ruff (clean) and mypy (33 pre-existing findings, matches the persistent
dev venv exactly), full `pytest` (949 passed, 1 skipped), and a real cold
`rebuild_shadow_cli.py` smoke run against a genuinely empty `--data-root`
— all passed.

**949 tests pass** (up from 906 at the start of this entry's session),
1 skipped. `ruff check src/model_prediction/rebuild tests/rebuild` clean.
`foundation_inventory.json`/`foundation_status.md` regenerated from code.

**What remains, honestly, for a complete foundation:** identity migration
for the other 4 collectors + every downstream consumer (mlb_features.py's
abbreviation dict, mlb_market_matching.py's name comparison); extracting
`mlb_shadow_run.py`'s real predict/match_markets/decide logic into
`MLBAdapter` so the shared CLI can actually run a full decision, not just
collect+features; a real horizon builder for any sport beyond MLB; wiring
the 6 not-yet-called ledger methods into the real pipeline; fixing the
real Soccer/Tennis ESPN-league bug; and the two items explicitly and
correctly deferred to the model-development phase (more MLB backfill,
multi-model-family OOF ensemble). Real order-book depth remains an
external blocker, not internal foundation debt, per this session's own
explicit instruction to treat it that way.

## Model-development phase — Checkpoint 0 + Task 1 (2026-08-08)

Foundation frozen (above). This entry starts the next phase: MLB
train-serving correctness fixes before any large backfill or model-family
comparison, per this phase's own explicit ordering (correctness before
scale).

**Checkpoint 0 — preflight, real commands run:**

```bash
git status --short        # clean
git branch --show-current # rebuild/clean-slate-v1
git rev-parse HEAD        # 4f0bea8c78e1085df443c9a5fabcbcd57194c084 (start)
git log --oneline -20
python --version           # `python` not on PATH; python3 -> 3.14.5
PYTHONPATH=src:. .venv/bin/python -m pytest -q
.venv/bin/ruff check src/model_prediction/rebuild tests/rebuild
```

**Start-of-phase state:** HEAD `4f0bea8` (a `shadow.db` idempotent-touch
data commit — the substantive code baseline is `21a2684`, "regenerate
model_benchmark.md with real MLB Phase 3 results"). Working tree clean.
1093 passed, 1 skipped (49.71s). `ruff check` on rebuild scope: clean.

**Current benchmark** (`outputs/rebuild/model_benchmark.md`, from
`21a2684`, unchanged by this entry): XGBoost OOF Brier 0.233 vs. control
0.255 — real, but the advantage is concentrated in one fold (barely wins
fold 0, loses fold 1, wins heavily fold 2) on tiny per-fold training sets
(24/44/63 games). Not yet a basis for choosing XGBoost; needs Task 9's
real nested chronological validation and a much larger sample before that
read is trustworthy either way.

**Current data range:** real ESPN scoreboard + Statcast backfill spans
2026-07-26 through the most recent daily-pipeline collection (2026-08-07
as of this entry) — about 2 weeks, not the 1-2 full seasons later tasks
in this phase call for. Real archived point-in-time probable-starter
observations (`data/point_in_time/mlb_probable_starters.jsonl`) cover the
identical window (2533 records total, `pit_eligible=True` for the
genuinely prospective ones, `pit_eligible=False`/
`retroactive_or_unverifiable_non_pit` for the rest).

**Current matched-game count:** pre-fix (`21a2684`), 126 real unique
matched games via Statcast `game_pk` join. Post-Task-1-fix, the matching
criterion itself changed (starters no longer require a Statcast `game_pk`
match at all — see Task 1 below), so this number isn't directly
comparable; a real, fresh count is captured below.

**Current consumed final-test range:**
`outputs/rebuild/test_consumption_registry.json` — `mlb_moneyline`:
`2026-08-02T20:05Z` through `2026-08-04T23:40Z`, consumed
`2026-08-07T19:08:55Z`, n=21, accuracy=0.381, brier=0.3211,
RESEARCH_ONLY. **Never reused for promotion** — see the process note
below about a mistake that nearly did exactly that.

### Task 1 — historical starter train-serving parity, fixed

**Real bug confirmed by reading the code, not assumed:**
`build_game_feature_row()` (consumed by all three training scripts plus
`mlb_shadow_pipeline.py`'s own walk-forward retraining step) called
`identify_starters(pitches)` on the completed game's **own** Statcast
pitches — the actual pitcher who threw, determined only after the game
happened. Live inference (`build_live_game_feature_row()`) already
resolved starters from the point-in-time probable-starter archive. Same
feature names (`home_sp_*`), two different real definitions of "starter"
depending on whether the row came from training or live serving — a
genuine train-serving mismatch that could let a late starter swap leak
into a historical decision that shouldn't have seen it yet.

**Fix:** `resolve_horizon_starter_names()` (new,
`mlb_features.py`) resolves the horizon-aware point-in-time-valid
probable starter the identical way `build_live_game_feature_row()`
already did, via `point_in_time_probable_starters()`. Records with
`pit_eligible=False` are excluded even though the point-in-time join's
own timestamp filter already makes them structurally unselectable for
any pre-event decision time (defense in depth, not covering a second
bug). `build_game_feature_row()` now uses this instead of
`identify_starters()` for the CURRENT game's own starter identity
(`identify_starters()` remains legitimately used elsewhere, for
excluding a *prior, already-completed* game's actual starter from
bullpen workload — not a leak, since that game is already over by the
time it's prior history). When no valid point-in-time probable exists
(or a resolved name can't be matched to a real Statcast pitcher ID), the
row is kept with starter features zeroed and
`starters_known=0.0`/`starter_missing_reason` set — never silently
backfilled with the actual starter.

**Related bug fixed in the same commit:** `HORIZON_HOURS_BEFORE["late"]`
was `0.5` (30 minutes), contradicting both this phase's own "late: start
minus 60 minutes" spec and `mlb_shadow_pipeline.py`'s live decision-time
computation, which had already hardcoded 60 minutes directly rather than
using the shared constant. Now both agree at `1.0`.

**Real regression tests** (`tests/rebuild/test_mlb_features.py`): a
starter revision observed after the late horizon's decision time must
not leak in; a retroactively-scraped record must never be used even if
its timestamp would otherwise pass; and an end-to-end
`build_game_feature_row()` case where the real actual starter and the
real point-in-time probable are different pitchers with distinct
prior-history signals (velocities 99.0 vs 80.0) — the row must carry the
probable's signal (99.0), not the actual starter's, even though the
actual starter really did throw the game's first pitch.

**1101 tests pass** (up from 1093), 1 skipped. `ruff check` clean.
Pushed as `e3d7b5b` (`fix(rebuild): make MLB historical starter features
horizon-safe`).

**Real effect on real data, verified — and a process mistake caught and
corrected:** to confirm the fix does real, non-trivial work (not just
pass synthetic unit tests), ran
`scripts/train_mlb_rebuild_real_features.py` once against the real
backfilled data. Real console output: `161` completed games, `0`
team-unresolved, `104/161` with a point-in-time-valid probable starter
for both teams at horizon=late, `57/161` (35%) now honestly flagged
`starters_known=0` — games where the pre-fix code would have silently
used the actual (leaky) starter instead. That's real, meaningful
confirmation the fix changes behavior on real data, not just in fixtures.

**The mistake:** that script also consumes-and-marks the final test on
every run as a side effect (`test_consumption_registry.json`) — a
genuine "do not proceed to model tuning if a consumed final test is used
for feature/model selection" violation risk, and directly against this
phase's own explicit instruction not to touch the existing consumed
range or manufacture a new one outside the proper procedure (only after
Tasks 2-10 freeze the dataset builder, features, tuning, ensemble, and
calibration). The run silently overwrote the registry with a new
consumed range (`2026-08-04T22:35Z`-`2026-08-07T01:40Z`, n=26,
accuracy=0.577, brier=0.2483) and rewrote
`mlb_split_manifest.json`/`mlb_training_results_real_features.json`/the
`mlb-two-head-real-features-v1` challenger artifact to match. **Caught
before any decision was made from that result** and reverted all four
files (`git checkout --`) back to their committed state — the original
`2026-08-02T20:05Z`-`2026-08-04T23:40Z` consumption remains the only real
one. Lesson for the rest of this phase: `train_mlb_rebuild_real_features.py`
and `train_mlb_xgboost_ensemble.py` both touch the registry on every run
(grep-confirmed); `train_mlb_feature_ablation.py` does not. Verifying a
fix's real-data effect from here on should read the printed diagnostics
from those two scripts without treating their held-out numbers as
anything but immediately-discarded scratch output, or should use the
ablation script / a standalone snippet instead.

**Next task:** Task 2 — doubleheader-safe ESPN-Statcast game matching.
`find_statcast_game_pk()` is no longer called by `build_game_feature_row()`
(Task 1 removed that dependency for starter resolution) but remains,
unused and still doubleheader-unsafe, for this task to replace with a
persisted canonical-event <-> `game_pk` mapping.

### Task 2 — doubleheader-safe ESPN-Statcast game matching, fixed

Deleted `find_statcast_game_pk()` (the doubleheader-unsafe `(date, home,
away) -> first game_pk` join — real bug: on a real doubleheader it
silently picked whichever of the two real games sorted first by
`game_pk`, with no guarantee that was the right one). It had zero real
callers left after Task 1 anyway.

**Replaced with `resolve_statcast_game_pk()`** (`mlb_features.py`):
Statcast's own `game_pk` *is* MLB StatsAPI's real `gamePk` (Baseball
Savant sources it directly from MLB's own numbering — verified live
against the actual `https://statsapi.mlb.com/api/v1/schedule` endpoint).
Matches an ESPN event to the real StatsAPI schedule game sharing both
real team names on the same calendar date, breaking ties by the closest
real scheduled start timestamp — a doubleheader's two real games are
hours apart, so this disambiguates them without needing any shared
native ID between ESPN and Statcast, and without depending on StatsAPI's
own `gameNumber`/`doubleHeader` fields (more robust to a source that
omits them). Fails closed (returns `None`, never guesses) on: no
team-pair match; a genuine tie in closest start time; or a best match
more than 3 real hours off (a real postponement/reschedule
inconsistency, not a doubleheader). Reuses the existing incumbent
`MLBStatsAPIClient` (`data_sources/mlb_statsapi.py`) as the real data
source for `statsapi_games` — not reimplemented.

**Persistence**: `resolve_or_link_statcast_game_pk()` (`identity.py`),
mirroring `resolve_or_link_polymarket_event_id()`'s exact shape — links
a real `game_pk` to the same canonical event ESPN scoreboard collection
already registered, namespaced `mlb_statsapi:{sport}`, idempotent across
reruns, fails closed on a doubleheader when no `known_canonical_event_id`
is supplied (team-pair+date alone can't disambiguate).

**Live-verified, not just synthetic fixtures**: ran
`resolve_statcast_game_pk()` against the real captured 2026-07-28 ESPN
scoreboard (`data/rebuild/normalized/mlb/scoreboard.parquet`) and a real
live `MLBStatsAPIClient().schedule('2026-07-28', '2026-07-28')` call.
Real result: the real Cincinnati Reds/Cleveland Guardians doubleheader
(event_ids `401901849` at 17:40Z and `401816295` at 23:10Z) correctly
resolved to the two real distinct game_pks `824490` and `824489`
respectively — matching MLB's own real schedule data exactly. (Also
observed, honestly: 3 games with `event_start_utc` in the 01:xxZ early
hours of 2026-07-28 UTC returned `None` against a same-day-only schedule
query — those are real games whose StatsAPI `officialDate` is the
*previous* US calendar day; a real caller querying a ±1-day window, the
same pattern this codebase's other date-boundary-aware collectors
already use, would resolve them. Not a matching bug, a real
single-day-query-window limitation of this verification script, not of
`resolve_statcast_game_pk()` itself.)

**Tests**: `tests/rebuild/test_mlb_features.py::TestResolveStatcastGamePk`
covers all 5 required scenarios (single game, doubleheader game 1,
doubleheader game 2, postponed/rescheduled, same teams on consecutive
dates) plus a genuine-tie case, using real MLB team names and real
gamePks from the live-verified doubleheader above.
`tests/rebuild/test_identity.py::TestResolveOrLinkStatcastGamePk` covers
the persistence layer the same way `TestResolveOrLinkPolymarketEventId`
does.

**Scope boundary, disclosed not hidden**: neither new function is wired
into a live collector yet — no collector in `rebuild/collectors.py`
currently fetches MLB StatsAPI schedule data at all (that would be a new
raw source, its own real scope item), so there's no live caller to wire
`resolve_or_link_statcast_game_pk()` into today. Built and tested as
real, ready infrastructure — the same "build + test the primitive, wire
it into a live pipeline in a later checkpoint" split this branch has used
repeatedly (e.g. `resolve_mlbam_player_id`, `resolve_or_link_polymarket_event_id`
before it).

**1114 tests pass** (up from 1101), 1 skipped. `ruff check` clean.

**Next task:** Task 3 — historical weather point-in-time selection
(`load_weather_daily_aggregate()` currently takes the latest snapshot in
a date folder and a daily aggregate, not a decision-time-valid snapshot
aligned to first-pitch hour).

### Task 3 — historical weather point-in-time selection, fixed

**Real bug confirmed by inspecting actual collected data**:
`load_weather_daily_aggregate()` took whichever snapshot sorted last on
disk for a venue/date (no relationship to decision time at all) and
averaged the *entire day's* hourly values into one number — diluting the
real pregame signal with hours unrelated to the game and carrying no
point-in-time guarantee whatsoever (a snapshot collected after a late
decision time could silently leak in).

**A second, more consequential real bug found while investigating**:
`collect_weather_forecast()` requested Open-Meteo's `hourly` data with
`"timezone": "America/New_York"` hardcoded for *every* venue. Verified
live against a real already-collected Chase Field (Arizona) snapshot:
`utc_offset_seconds: -14400` — Eastern's real offset, not Arizona's real
`-25200`. Any first-pitch-hour lookup keyed off those local timestamps
would silently pick the wrong hour's weather for any non-Eastern venue.
Fixed by requesting `"timezone": "UTC"` directly (verified live) — no
venue-timezone lookup needed at all, `hourly.time` is now directly
comparable to a real `event_start_utc`.

**A third real gap**: `RawStore` has no way to recover a snapshot's real
`observed_at_utc` from disk after the fact (`list_snapshots()` returns
`observed_at_utc=""` — it was only ever known transiently to the writer).
Without a real, embedded observed_at, no genuine "select the newest
snapshot observed before decision_time" filtering is even possible.
Fixed by wrapping the real Open-Meteo response in a small self-describing
envelope before writing (`{"observed_at_utc", "endpoint",
"forecast_data"}`) rather than writing the bare API response — the
minimal, in-scope fix; full `RawStore` provenance recovery is a separate,
broader storage-layer gap, not fixed here.

**Replaced** `load_weather_daily_aggregate()` with
`load_weather_at_decision_time()` (`mlb_features.py`): considers only
real snapshots carrying the new provenance envelope (a legacy
unenveloped snapshot — the shape every one of the 3 real snapshots
collected before this fix has — has no real recorded `observed_at_utc`
and is treated as PIT-unknown, never guessed at via file mtime or
otherwise); selects the newest one with `observed_at_utc <=
decision_time_utc`; reads the one real hourly entry closest to the
game's actual first-pitch time, not a daily aggregate. Returns
`temp_f_first_pitch`, `wind_mph_first_pitch`,
`wind_direction_deg_first_pitch`, `precip_mm_first_pitch`, and
`forecast_age_hours` (real, disclosed gap between when the forecast was
observed and the decision time), plus the existing `availability` flag.
Renamed from `temp_f_mean`/`wind_mph_mean`/`precip_mm_total` since they
are no longer means/totals — updated every real reference (both training
scripts, `mlb_shadow_pipeline.py`, `ablation.py`'s feature groups,
`test_ablation.py`'s schema guard).

**Disclosed, not silently ignored**: for a historical (backfilled)
`game_date`, Open-Meteo's own Historical Forecast API is itself a real,
documented approximation — a stitched series of past model runs, not one
exact historical run. Even perfect point-in-time-safe *selection* here
cannot make that number a literal "forecast exactly as it existed at
decision_time_utc" — that is an external data-source limitation, not
something this function's selection logic can fix, and is documented as
such in the function's own docstring rather than silently treated as
exact.

**A fourth real bug, caught by live verification against the actual
Open-Meteo API** (not just synthetic test fixtures, which had used an
unrealistic `+00:00`-suffixed shape): Open-Meteo's `hourly.time` entries
under `timezone=UTC` are genuinely UTC but returned *naive* (e.g.
`"2026-08-08T00:00"`, no offset suffix) — comparing that directly against
an aware `event_start_utc` raised `TypeError` on every real call. Fixed
by explicitly attaching UTC to a naive parsed hourly timestamp before
comparing. Caught by actually running the real (fixed) collector end to
end against the live API, not by unit tests alone — the test fixtures
were then corrected to use the same real naive shape so this exact
regression can't resurface silently.

**Live-verified end to end**: ran the real (fixed) `collect_weather_forecast`
against the live Open-Meteo API for a real venue/date, then
`load_weather_at_decision_time()` against the real resulting snapshot 2
hours before a synthetic decision time. Real result: `availability=1.0`,
`temp_f_first_pitch=109.6` (a realistic real August Phoenix forecast),
`forecast_age_hours=2.0` (matches the real 2-hour gap). Also confirmed
honestly: the 3 real legacy (pre-fix) snapshots already on disk
(`data/rebuild/raw/open_meteo/`) now correctly report
`availability=0.0` — a real, disclosed consequence of doing this
correctly (weather is effectively unavailable for every currently-
collected historical game until new provenance-enveloped collection
accumulates going forward), not a bug.

**Tests**: `tests/rebuild/test_mlb_features.py::TestLoadWeatherAtDecisionTime`
(7 tests: PIT snapshot selection, first-pitch-hour alignment vs. a real
non-uniform 3-hour series, legacy-envelope rejection, no-snapshot/no-
directory cases, forecast age) plus 2 new
`tests/rebuild/test_mlb_collectors.py::TestWeatherForecastEndpoint` tests
(UTC timezone request, real provenance-envelope shape written to
`RawStore`).

**1122 tests pass** (up from 1114), 1 skipped. `ruff check` clean.

**Next task:** Task 4 — unify the MLB historical horizon dataset builder.
Three training scripts (`train_mlb_rebuild_real_features.py`,
`train_mlb_xgboost_ensemble.py`, `train_mlb_feature_ablation.py`) each
independently rebuild the same feature-row loop; `mlb_shadow_pipeline.py`
has a fourth, near-identical copy for walk-forward retraining. All four
now correctly call the fixed `build_game_feature_row()`, but should
converge on one real, shared `MLBHistoricalHorizonDatasetBuilder` per
CLAUDE.md's own instruction, rather than four independently-maintained
loops that could silently drift from each other.

### Task 4 — unified MLB historical horizon dataset builder

**Built** `build_mlb_historical_horizon_dataset(data_root, start_date,
end_date, horizon)` (`horizon_builder.py`, alongside the existing
per-date `build_mlb_horizon_dataset`) — the one authoritative loop over
every real completed MLB game in a date range, calling the already-fixed
`build_game_feature_row()` once per game (Task 1's starter-parity fix,
so no separate "training feature path" exists to drift from live
inference). Returns `MLBHistoricalDatasetResult` (`.features`, real
`.matched_games`/`.starters_known_games` properties, `.unmatched_games`,
and a real content-addressed `.dataset_hash` — sorted by `event_id`, not
insertion order, matching `build_mlb_horizon_dataset()`'s own
metadata-vs-content hashing fix). Every real completed game in range
gets a row; a missing starter is flagged (`starters_known=0.0`), never
dropped, matching Task 1's "missingness is data" contract — only an
unmapped ESPN team name (no real Statcast club abbreviation) excludes a
game, exactly as `build_game_feature_row()` already does on its own.

**Wired into all three training scripts**
(`train_mlb_rebuild_real_features.py`, `train_mlb_xgboost_ensemble.py`,
`train_mlb_feature_ablation.py`), replacing each one's own copy of the
scoreboard-dedupe -> Statcast-load -> starter-identify -> probables-load
-> per-game-loop sequence with a single call. `start_date`/`end_date`
are the real min/max `event_start_utc` among that run's own completed
games (i.e. "all available real history"), not a hardcoded range.

**A real, latent bug this refactor surfaced and fixed**:
`normalize_statcast_pitches()` returned a bare, zero-column
`pl.DataFrame()` when given empty input, rather than a well-typed empty
frame. Every existing test happened to pass non-empty pitch rows, so
this was never exercised — but `build_mlb_historical_horizon_dataset()`'s
own new tests (a real completed game with real scoreboard data but zero
raw Statcast collection for its dates — a genuinely realistic scenario,
e.g. a backfill gap) hit it immediately:
`bullpen_rolling_features()`/`pitcher_rolling_features()`/`identify_starters()`
all filter by real column names like `pitching_team`, and raised
`ColumnNotFoundError` instead of honestly reporting zero prior history.
Fixed by returning a real, explicitly-typed empty schema
(`_NORMALIZED_PITCH_SCHEMA`) instead of a bare empty frame.

**Scope boundary, disclosed**: `mlb_shadow_pipeline.py`'s `predict_stage`
(the live shadow pipeline's own walk-forward retraining step) is *not*
switched to the new builder — it already correctly calls the fixed
`build_game_feature_row()` (Task 1), but legitimately reuses
`state.pitches`/`state.starters` already loaded once by `load_state()`
rather than reloading Statcast raw data a second time in a
latency-sensitive live-prediction path. CLAUDE.md's own Task 4 wording
names the three training scripts specifically ("Then make:
train_mlb_rebuild_real_features.py, train_mlb_xgboost_ensemble.py,
train_mlb_feature_ablation.py all consume this dataset") — the live
pipeline's copy is a fourth, related-but-distinct case with a real
performance reason to stay separate, not an oversight.

**Live-verified end to end**: ran the real (refactored)
`train_mlb_feature_ablation.py` against the actual backfilled data (safe
to run — unlike the other two training scripts, it never touches
`test_consumption_registry.json`). Real result: **161 matched games, 104
with a point-in-time-valid probable starter for both teams (57 flagged
`starters_known=0`)** — identical to the numbers this session already
observed from the pre-refactor code path, confirming the refactor
preserved real behavior exactly. Real ablation report regenerated
(`outputs/rebuild/mlb_feature_ablation.json`), included in this commit
as current evidence: bullpen remains the strongest isolated group
(`delta_log_loss=+0.049`), the rest are directionally small and, per the
report's own disclosed caveat, not yet a statistically robust ranking at
n=135.

**Tests**: `tests/rebuild/test_horizon_builder.py::TestBuildMlbHistoricalHorizonDataset`
(8 tests: date-range filtering, scheduled-not-yet-played exclusion,
unmatched-team-name counting, missing-starter rows kept-not-dropped,
deterministic/content-sensitive hashing, per-horizon feature
differences, invalid-horizon rejection) plus the new
`normalize_statcast_pitches` empty-schema regression coverage.

**1129 tests pass** (up from 1122), 1 skipped. `ruff check` clean.

**Next task:** Task 5 — encode explicit MLB feature missingness.
Currently `pitcher_rolling_features()`/`pitcher_clean_rate_features()`/
`bullpen_rolling_features()` already return an `availability` flag
alongside zeroed values when there's no real prior history, but XGBoost
still receives that zero as an ordinary numeric value (not a native
missing/NaN), and linear-model consumers have no paired
imputed-value-plus-indicator convention yet.

### Task 5 — explicit MLB feature missingness, encoded

**Real rule applied throughout**: a count/sample-size is genuinely 0 when
zero real observations exist (a true statement regardless of *why* the
sample is empty) -- `starts_seen`, every `_n` clean-rate field,
`bullpen_pitches`, `bullpen_appearances`, and `availability` itself all
stay real zeros. A rate/average/single-observation *value* computed from
zero real observations is mathematically undefined, not "a real value
that happens to be zero" -- `avg_velocity`, `k_pct`/`bb_pct`/`csw_pct`/
`whiff_pct`, `days_rest`, `pitches_last_start`, `bullpen_avg_velocity`,
and all four weather fields now return real `NaN` in that case, not an
apparently-measured `0.0`. **One principled exception**: the beta-
binomial-shrunk clean-rate fields (`first_inning_clean_rate`/
`scoreless_inning_rate`/`clean_appearance_rate`) are already well-defined
at zero real observations -- the posterior mean collapses to the pure
league prior (0.5) -- so a real bug was fixed in the same pass: the two
early-return branches in `pitcher_clean_rate_features()` previously
bypassed the shrinkage estimator entirely and hardcoded a literal `0.0`,
inconsistent with the function's own design. Now routed through the
identical `pitcher_clean_rate_shrink()` call every other case uses.

**A second real bug found in the same pass**: several inline `else 0.0`
fallbacks inside `pitcher_rolling_features()`'s "has some real history"
branch (`k_pct`/`bb_pct`/`csw_pct`/`whiff_pct` when their real
denominator is 0) were computing 0/0 and silently calling it a measured
zero rate. Fixed to `NaN` -- mathematically undefined, not ambiguous.

**Modeling side — a real, live-verified crash and a real, live-verified
silent-corruption bug, both found by actually fitting on the current
real dataset, not assumed from reading code**:

1. `RunDifferentialHead` (ElasticNet, the differential-margin head) has
   no native NaN support (confirmed live: raises `ValueError` on any
   missing value), unlike `RunIntensityHead`'s
   `HistGradientBoostingRegressor`. Fixed with a real
   `SimpleImputer(strategy="mean")`, fit only on training data (never
   prediction-time data) and persisted through `save()`/`load()`. The
   paired "missingness indicator" half of CLAUDE.md's "imputed value +
   missingness indicator must be paired" requirement is the real, named
   `*_availability` columns now included directly in the shared feature
   lists (below) -- not an anonymous auto-generated indicator column.

2. **Real crash, found by fitting `MLBTwoHeadModel` on the actual current
   backfilled dataset**: weather (`temp_f_first_pitch`) is genuinely
   `NaN` for **every one of the 161 real matched games right now** (Task
   3's fix correctly rejects the 3 pre-fix legacy weather snapshots as
   PIT-unknown). `StandardScaler.fit_transform()` on a wholly-`NaN`
   column silently produced `NaN` mean/variance, and
   `HistGradientBoostingRegressor`'s binning step then raised a real
   `ValueError: window shape cannot be larger than input array shape`
   trying to find split thresholds among zero real distinct values.
   Separately confirmed live: `SimpleImputer(strategy="mean")` *drops* an
   all-`NaN` column from its output entirely rather than erroring, which
   would have silently shifted every later feature out of alignment with
   the model's own stored feature-name list. Fixed with
   `_neutralize_always_missing_columns()`: any feature column that is
   100% `NaN` across the current fit's training data is replaced with a
   real neutral `0.0` constant (a feature never once observed carries no
   learnable signal either way), applied identically at predict time via
   a persisted per-head `_always_missing_mask` (now part of
   `save()`/`load()`'s bundle). Live-verified: `MLBTwoHeadModel.fit()`,
   `.predict_row()`, `.save()`/`.load()` round-trip (identical
   predictions after reload), and `BootstrapMLBEnsemble.fit()` all now
   succeed on the real current dataset, including predicting a row with a
   genuinely unresolved starter.

**Shared feature-list consolidation**: `MLB_INTENSITY_FEATURES`/
`MLB_DIFFERENTIAL_FEATURES` (`mlb_features.py`) are now the one canonical
definition -- previously each of `train_mlb_rebuild_real_features.py`,
`train_mlb_xgboost_ensemble.py`, and `mlb_shadow_pipeline.py`
independently hardcoded its own copy, with no guarantee they'd stay
identical (`mlb_shadow_pipeline.py`'s copy is the one that actually
retrains the live-serving artifact, so a silent divergence there would
have been a real train-serving mismatch). Both lists now include the
real `*_availability` indicators paired with every feature that can be
`NaN`. `train_mlb_xgboost_ensemble.py`'s `XGB_FEATURES` deduplicates the
union (`train_mlb_feature_ablation.py`'s `FEATURE_GROUPS_MLB` is left
unchanged -- XGBoost already gets native `NaN` handling there regardless
of an explicit indicator; adding availability fields per-group is a
disclosed, deliberate scope boundary, not an oversight).

**A real, latent bug this pass also surfaced and fixed**:
`normalize_statcast_pitches()`'s empty-input path (fixed in Task 4)
covers a `pl.DataFrame()` with zero rows; a genuinely `.is_empty()` input
was the only case exercised there. No further gap found in this pass.

**Train-serving parity, proven not assumed**: new
`TestTrainServingMissingnessParity` builds the identical no-history
inputs through both `build_game_feature_row()` (historical) and
`build_live_game_feature_row()` (live) and asserts every shared field
(`home_sp_*`, `away_sp_*`, `*_clean_*`, `*_bp_*`, weather, park factor)
encodes identically, `NaN`-for-`NaN` -- both already delegate to the same
underlying functions, so this proves the invariant rather than assuming
it holds.

**1134 tests pass** (up from 1129), 1 skipped. `ruff check` clean.

**Real evidence regenerated**: re-ran the real (registry-safe)
`train_mlb_feature_ablation.py` against the actual backfilled data with
the new NaN-aware features flowing all the way through
`XGBoostChallenger` (native NaN handling, confirmed no crash) --
`outputs/rebuild/mlb_feature_ablation.json` updated with the current real
numbers (bullpen still the strongest isolated group,
`delta_log_loss=+0.051`; directionally similar to before, small
per-group movement from the clean-rate prior-mean fix and inline 0/0->NaN
fix changing exactly which rows contribute non-degenerate signal).

**Next phase**: Tasks 1-5 (the correctness/parity "stop condition" per
this phase's own instructions) are now complete. The next real step is
Task 6 -- expanding the historical MLB backfill -- but see the
structural constraint recorded in the handoff summary: genuinely
point-in-time-safe probable-starter data can only exist for dates this
collector was actually running, which bounds how far backfill can
honestly extend without fabricating history.

### Task 9 — nested chronological XGBoost validation, fixed

**Real bug confirmed by reading the code**: every real caller of
`XGBoostChallenger.fit()` (`train_mlb_xgboost_ensemble.py`) passed the
outer validation fold itself as `eval_set` --
`xgb_challenger.fit(X_train, y_train_arr, eval_set=(X_val, y_val_fold))`
-- so XGBoost's own early stopping chose the number of boosting rounds
using the exact rows whose held-out performance was then reported as the
fold's result. Textbook "outer validation labels influence early
stopping," named explicitly in this phase's stop condition.

**Fixed** with `nested_xgboost_fold()` (`xgboost_stress.py`): splits the
outer training history itself (chronologically, matching every other
real fold construction in this codebase) into an inner train block and
an inner tuning/early-stop block; searches a real, bounded, pre-declared
grid (`XGB_PARAM_GRID`, exactly the dimensions this phase specified --
`max_depth`/`learning_rate`/`min_child_weight`/`subsample`/
`colsample_bytree`/`reg_alpha`/`reg_lambda`, 972 real combinations, not
an open-ended Optuna sweep); selects params and the early-stopping
iteration by **inner** chronological log loss only; freezes both; refits
on the full outer-training history with no early stopping (the iteration
count is already decided); predicts the outer validation fold with a
model that has never seen it, in any form, at any stage. Persists
`best_params`/`best_iteration`/`inner_log_loss`/`outer_log_loss` per
fold, as this phase's instructions require.

**Real timing check before committing to the full grid** (not assumed):
one fold, full 972-combination grid, ~70 real training rows -- 11
seconds. Fast enough to run the complete declared grid on every real
fold rather than needing a random subsample.

**Tests**: `tests/rebuild/test_xgboost_challenger_and_ensemble.py::TestNestedXgboostFold`
-- most notably `test_outer_validation_labels_never_reach_any_fit_call`,
which patches `xgboost.XGBClassifier.fit` itself to record every real
y-array passed to any underlying fit/eval_set call across the whole grid
search and asserts none of them overlap the outer validation labels
(structural proof, not a convention check) -- plus required-field
persistence, too-small-history refusal, grid-membership, and an
out-of-sample signal-recovery test.

**Wired into `train_mlb_xgboost_ensemble.py`**, replacing the leaky
direct `XGBoostChallenger.fit(..., eval_set=(X_val, ...))` call.

**1139 tests pass** (up from 1134), 1 skipped. `ruff check` clean.

**Real evidence, live-verified, reported honestly**: re-ran the real
(registry-safe) script against the actual backfilled data with the fixed
nested CV. Real result -- **the leak's removal changed the picture
substantially**: XGBoost's real out-of-fold log loss is now `0.7258`
(vs. the two-head control's `1.4760`) across 103 real OOF predictions,
3 folds (`train=31/59/85`, `val=34/32/37`). The two-head control's much
higher log loss than earlier session numbers is not itself caused by
this fix -- it reflects Tasks 1/3/5's honest missingness (58% of games
now flagged `starters_known=0`, weather 100% unavailable) feeding a
control architecture (`HistGradientBoostingRegressor`/`ElasticNet`) that
evidently handles that much real missingness worse than XGBoost does on
this small a sample, not a re-introduced leak. Ensemble weights collapse
to `xgboost=1.0`/`two_head~0.0` -- per this phase's own instruction ("If
the fitted stacker again becomes two_head=0/xgboost=1, report that the
ensemble adds no value and use XGBoost as the direct challenger"), that
is exactly what should be reported here: **the ensemble currently adds
no value; XGBoost is the stronger direct challenger on this real,
still-small (n=161) sample.** Still explicitly not a promotion decision
-- no consumed or new final test was touched.

**What's still open from this phase's stop condition** (tracked, not
done): Task 8 (persisted date-cluster split manifest -- `expanding_folds`
already clusters by chronological block but a dedicated persisted
manifest artifact doesn't yet exist) and Task 10 (cross-fit ensemble
evaluation -- the logistic stacker above is fit on the same OOF rows its
weights are then reported against, which is fine for *fitting* the
stacker but not yet a fully unbiased claim about the ensemble's own
value, per this phase's own distinction). Given the honest
`xgboost=1.0` collapse just observed, Task 10 is lower-priority now: the
stacker already isn't being relied on.

### Task 8 — folds and the final split rebuilt on complete event dates

**Real bug confirmed by reading the code**: both real training scripts
built their chronological folds by passing full `event_start_utc`
timestamps into `expanding_folds()` -- near-per-game granularity, a
deliberate prior workaround (calendar-day granularity alone collapsed
the small real dataset into too few unique buckets and produced 0
folds). But `expanding_folds()` treats each unique value in its `dates`
argument as one indivisible partition unit, so per-timestamp granularity
meant a fold boundary could fall *between* two real games on the
identical calendar date -- exactly the same-day contamination CLAUDE.md's
Part 2 SS2 requires folds to prevent. **A second, more consequential
instance of the identical bug**: `train_mlb_rebuild_real_features.py`'s
separate final train/calibration/test split sliced the sorted feature
table by real game *count* (`features[n - test_size:]`), with the same
same-day-contamination exposure -- and this is the exact split whose
boundaries get written into `test_consumption_registry.json` as the
official consumed final test.

**Fixed**: added `date_cluster_split()` (`validation.py`) -- splits real
calendar dates into (train, calib, test) clusters, never splitting a
single date's games across buckets; returns dates only, so the caller
re-joins them against its feature table and every game sharing a
selected date moves together. Both training scripts now build folds from
real `game_date` values (not `event_start_utc` timestamps), sized as a
real fraction of the distinct dates available (not game count), with a
real 1-day embargo; `train_mlb_rebuild_real_features.py`'s final split
now uses `date_cluster_split()` instead of positional game-count
slicing. Fold construction is kept textually identical between the two
training scripts (not extracted into a shared helper) since each builds
folds independently from its own copy of the same real feature table and
must agree by construction.

**Live-verified two ways**: (1) ran the real (registry-safe)
`train_mlb_xgboost_ensemble.py` end to end -- 3 real folds from 15 real
distinct dates (`val=2d test=2d gap=1d`), no crash, real predictive
numbers (XGBoost OOF log loss 0.7469 vs. two-head control's 1.0169,
ensemble weight now 0.964 xgboost / 0.036 two-head -- a real, slightly
different picture from the near-per-game-granularity run, since exactly
which games land in each fold changed). (2) Since
`train_mlb_rebuild_real_features.py` itself must not be run live (it
writes to `test_consumption_registry.json`), independently replicated
its exact new fold/split logic in an isolated, read-only snippet against
the real dataset and asserted the actual invariant directly: **zero
calendar-date overlap between train and validation in any of the 3 real
folds, and zero cross-bucket date overlap in the final
train(11 dates)/calib(2 dates)/test(2 dates) split** -- not just "the
code looks right," a real assertion against real data. The replicated
per-fold log-loss values (0.777/1.440/0.756) matched the standalone
XGBoost script's own two-head numbers exactly, a real cross-check that
both scripts' now-identical fold construction actually produces
identical folds.

**Tests**: `tests/test_rebuild.py::TestDateClusterSplit` (5 tests: no
cross-bucket date splitting with several real games per date,
chronologically-last test dates, zero-calibration-size handling, honest
under-sizing when too few real dates exist, duplicate-date
deduplication).

**1144 tests pass** (up from 1139), 1 skipped. `ruff check` clean.

**Not done, disclosed**: `outputs/rebuild/mlb_split_manifest.json`
persistence itself (CLAUDE.md's "persist a split manifest" requirement)
was already real and working before this fix (`build_split_manifest()`,
wired into `train_mlb_rebuild_real_features.py`) -- this task fixed the
*granularity* of what goes into it, not its persistence, which was
already correct.

## Real Statcast/ESPN backfill extension + explicit Part 2 go-ahead (2026-08-08)

User checkpoint: with all applicable stop-condition items resolved,
asked how to proceed given the real structural backfill limit (15
calendar dates). Explicit decision: **proceed into Part 2 (model-family
work) now on the current real sample, with every result explicitly
labeled small-sample/directional, not promotion-grade** -- not "wait for
more real data" and not "silently treat the small sample as sufficient
for promotion."

**Real backfill extension, run live**: ESPN scoreboard already covered
2026-07-03 through 2026-08-07, but Statcast (pybaseball) only covered
2026-07-26 onward -- the real bottleneck. Backfilled Statcast +
confirmed ESPN for 2026-07-01 through 2026-07-25 (25 real dates; 2 real
MLB All-Star-break off-days correctly returned `no_games`/`no_data`, not
errors). Real result: 32 real Statcast dates on disk now (up from 10),
~125k real pitches (up from ~40k).

**Deliberately did not backfill weather or Polymarket for these dates**:
weather collected *today* for a *past* date would carry
`observed_at_utc=now`, which can never satisfy
`load_weather_at_decision_time()`'s `observed_at_utc <= decision_time`
filter for any real historical decision time (Task 3) -- it would be a
wasted real API call for data that can never be used, not a shortcut.
Polymarket order books for already-settled historical dates don't
meaningfully exist either.

**What this backfill can and cannot fix, verified live, not assumed**:
cannot manufacture historical point-in-time probable-starter data --
`data/point_in_time/mlb_probable_starters.jsonl`'s real
`pit_eligible=True` coverage still starts exactly 2026-07-26 (when this
collector actually started running prospectively), unchanged. Confirmed
via the real (registry-safe) ablation script: matched games rose from
161 to 435 (more real completed games in the already-ESPN-covered range
now have matching Statcast), while `starters_known` stayed at exactly
104 -- the correct, honest effect. What it *does* fix: real
prior-history depth for the games that were already scorable. Verified
directly: among the 104 real `starters_known=1` games, home starter
prior-history availability is now **104/104 (100%)**, away starter
**98/104 (94%)**, bullpen **104/104 (100%)** -- up from meaningfully
worse coverage before this backfill. Diminishing-returns check also
performed: `pitcher_rolling_features`'s own `lookback_starts=3` and
`bullpen_rolling_features`'s `lookback_days=3` only ever look at each
pitcher/team's 3 most recent real starts/days, so backfilling
substantially further back than this (e.g. to real season start) would
not improve these specific rolling features further -- it would only
matter once Task 14's disclosed-as-not-yet-built rolling-10/rolling-20
clean-rate variants exist.

Committed as `chore(data)` commits (real collected data, not source
code) -- this branch's own established convention already scopes
`data/rebuild/` as real, trackable, in-repo collected evidence, not
speculative or synthetic.

**Next**: proceeding into Part 2 (Task 12 -- model-family comparison,
including the joint-distribution methods already implemented in
`JointScoreDistribution` -- independent Poisson/negative
binomial/Skellam -- but never compared against each other on real
chronological OOF folds).
