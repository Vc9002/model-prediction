# NCAAF v6 input audit: weather, QB and pace data

AS_OF_UTC: 2026-11-15T02:10:00Z  (generated 2026-11-15T02:17:24Z)

Read-only. Ledger opened with a `mode=ro` URI; no network, no order endpoint, no write to history,
ledger or src. Quote format: percentages and points only. Every code line below was located by
searching the source at run time.

## Verdict

- **Weather adjustment: not fitted and almost never non-zero, so effectively dead in the numbers; live only in plumbing since the weather capture began (2026-10-23).** Across 6129 NCAAF ledger rows (607 events) the adjustment is non-zero on 25 rows (0.4%; 0.9% of the 2829 rows that carried a real forecast), on 6 events, with a largest size of 1.8 total points. The training history carries only constant placeholder weather, so the coefficients (wind, precipitation, cold) are hand-set literals that were never fitted or validated.
- **QB adjustment: dead input.** The -4.5 point backup discount fired on 0 of 2430 recorded side cells and 0 side cells carry a non-zero adjustment; the starter is the season passing leader, not a point-in-time starter, a starter missing from the report is scored as available (1.0), and the captured injury feed holds 387 entries over 129 snapshots with 0 QB entries.
- **Pace data: none.** No drive or play fields exist; possessions is the constant 12.5, success rate the constant 0.42, EPA has no provenance. v6 estimates pace from scoring via additive points ratings.

## (a) Weather

### Code path (forecast to extractor)

1. `build_cfb_slate` calls `resolve_cfb_game_weather` at `src/model_prediction/models/college_football.py:925` with the home team,
   date, event start and the decision time `obs`; the weather file is `data_root/weather/ncaaf_historical.jsonl` (`src/model_prediction/features/multi_sport_weather.py:766`).
2. `weather_inputs_verified = is_dome or None not in (temp, wind, precip)` at `src/model_prediction/models/college_football.py:936`.
3. The three values go into `UpcomingCFBGame` (`src/model_prediction/models/college_football.py:963`, `src/model_prediction/models/college_football.py:964`, `src/model_prediction/models/college_football.py:965`) and then `predict_matchup` (`src/model_prediction/models/college_football.py:973`), which hands them to `extract_features`.

`resolve_cfb_game_weather` returns `(None, None, None, False)` when:

- neutral site, no venue name: unknown weather (`src/model_prediction/features/multi_sport_weather.py:763`)
- weather file missing: unknown weather (`src/model_prediction/features/multi_sport_weather.py:807`)
- decision time at or after kickoff, or no event start: unknown weather (`src/model_prediction/features/multi_sport_weather.py:820`)
- record is not an Open-Meteo forecast: skipped (`src/model_prediction/features/multi_sport_weather.py:856`)
- capture hash mismatch: skipped (`src/model_prediction/features/multi_sport_weather.py:858`)
- forecast captured after decision time or at/after kickoff: skipped (`src/model_prediction/features/multi_sport_weather.py:864`)
- no hourly slot for the kickoff hour: skipped (`src/model_prediction/features/multi_sport_weather.py:869`)
- temp, wind and precip must all be non-null to count as a hit (`src/model_prediction/features/multi_sport_weather.py:890`)
- no surviving record: (None, None, None, False) (`src/model_prediction/features/multi_sport_weather.py:904`)

A dome home team returns `(70.0, 0.0, 0.0, True)` (`src/model_prediction/features/multi_sport_weather.py:760`).

In `extract_features` a `None` becomes the default:

- temperature 70 F (`src/model_prediction/features/cfb_features.py:619`), wind 5 mph (`src/model_prediction/features/cfb_features.py:620`), precipitation 0 in (`src/model_prediction/features/cfb_features.py:621`).
- The adjustment only runs `if not is_dome` (`src/model_prediction/features/cfb_features.py:624`) and only below/above the hand-set thresholds: wind over 14 mph (`src/model_prediction/features/cfb_features.py:625`), precipitation over 0.05 in (`src/model_prediction/features/cfb_features.py:627`), temperature under 32 F (`src/model_prediction/features/cfb_features.py:629`). 70 F / 5 mph / 0 in trips none of them, so the adjustment is exactly 0.0 outside domes, and it is 0.0 in domes by the gate.
- `is_dome` inside the extractor comes from the home team's catalog entry (`src/model_prediction/features/cfb_features.py:541`) and is forced False on neutral sites (`src/model_prediction/features/cfb_features.py:546`); an indoor neutral venue still resolves to 70 F / 0 mph / 0 in, so it also scores 0.0.

Probe of the real extractor (`history=[]`, synthetic matchup, offline):

| Case | weather_total_adjustment | extractor is_dome | wind mph | temp F | precip in |
|---|---|---|---|---|---|
| weather None/None/None, outdoor home | 0.0 | False | 5.0 | 70.0 | 0.0 |
| weather 70F / 5 mph / 0 in, outdoor home | 0.0 | False | 5.0 | 70.0 | 0.0 |
| weather None/None/None, dome home | 0.0 | True | 5.0 | 70.0 | 0.0 |
| wind 20 mph / 20 F / 0.2 in, outdoor home | -4.08 | False | 20.0 | 20.0 | 0.2 |
| wind 20 mph / 20 F / 0.2 in, dome home | 0.0 | True | 20.0 | 20.0 | 0.2 |

Probe of the real resolver against the repo weather file (decision 5 h before kickoff):

| Case | Result (temp, wind, precip, is_dome) | Provenance |
|---|---|---|
| outdoor home, game date before file coverage (2026-09-05) | (None, None, None, False) | {} |
| neutral site with no venue name | (None, None, None, False) | {"source": "neutral_site_venue_unknown"} |
| dome home team (catalog) | (70.0, 0.0, 0.0, True) | {"home_team": "Syracuse Orange", "source": "cfb_team_catalog:indoor"} |

### Weather files on disk

| File | Rows | Game dates | Forecast rows hash-verified | Sources |
|---|---|---|---|---|
| repo: `E:\MP\data\weather\ncaaf_historical.jsonl` | 767 | 2026-10-23 to 2026-11-14 (18 dates) | 740 | {'open_meteo:archive': 18, 'open_meteo:forecast': 749} |
| runtime: `E:\model-prediction-runtime\weather\ncaaf_historical.jsonl` | 128 | 2026-10-31 to 2026-10-31 (1 dates) | 128 | {'open_meteo:forecast': 128} |

The forecast reads `data_root/weather/...`; `data_root` is the parent of `project.ledger_path` (`config/model.yaml`: `data/picks.xlsx`), i.e. the repo `data/` folder unless `MODEL_PREDICTION_LEDGER` is set (value at audit time: None). The ledger's recorded capture hashes (matched below) identify which file actually served the forecasts.

### Ledger counts (sport = ncaaf, every tier and status)

Rows 6129, events 607; tier/status {'flat/open': 72, 'flat/settled': 670, 'flat/superseded': 3624, 'main/open': 15, 'main/settled': 1073, 'main/superseded': 675}.
Event start range 2026-08-29 to 2026-11-15; models {'cfb-structural-v5': 1119, 'cfb-structural-v4': 1614, 'cfb-structural-v3': 544, 'cfb-structural-v2': 1145, 'cfb-total-v1': 602, 'cfb-spread-v1': 604, 'college-football-v1': 501}.

| Measure | Row level (all rows) | Event level (any row) | Latest row per event |
|---|---|---|---|
| Units | 6129 | 607 | 607 |
| With a feature `features` block | 6129 | 607 | 607 |
| `weather_total_adjustment` non-zero | 25 | 6 | 1 |
| Evidence source is an Open-Meteo forecast | 2829 | 173 | 173 |
| Non-null raw wind/temp/precip value under any matching key | 0 | 0 | 0 |

Row-level `weather_inputs_verified`: {'True': 2925, 'False': 60, '<absent>': 3144}.
Row-level `weather_evidence.source`: {'open_meteo:forecast': 2829, 'cfb_team_catalog:indoor': 96, '<empty evidence {}>': 60, '<key absent>': 3144}.
Row-level extractor-side `is_dome`: {'False': 5933, 'True': 166, '<absent>': 30}.
Latest-row-per-event `weather_inputs_verified`: {'True': 179, 'False': 5, '<absent>': 423}; `weather_evidence.source`: {'open_meteo:forecast': 173, 'cfb_team_catalog:indoor': 6, '<empty evidence {}>': 5, '<key absent>': 423}.
Rows whose event starts before the repo weather file's first game date: 2765.
Non-zero adjustment values seen: [-1.8, -0.36, -0.17, -0.11, -0.03]; events: ['401856733', '401858520', '401858526', '401866456', '401869927', '401871060'].
Raw weather key paths with a non-null value: none.

The raw wind, temperature and precipitation readings are not persisted in the ledger payloads at all (the feature dict stores only `weather_total_adjustment`, `is_dome`, `weather_inputs_verified` and the evidence block), so the raw-reading count above is the count of payloads that carry one under any key matching wind/temp/temperature/precip. The effect is visible only through the adjustment itself.

Ledger read is pinned: created_at_utc <= 2026-11-15T02:10:00Z.

#### Field presence by model (absent is not zero)

| model_id | rows | features block | has weather_total_adjustment | non-zero | has weather_evidence | has qb_availability | has away/home qb_value_adjustment | qb adj non-zero |
|---|---|---|---|---|---|---|---|---|
| cfb-spread-v1 | 604 | 604 | 594 | 0 | 0 | 0 | 0/0 | 0 |
| cfb-structural-v2 | 1145 | 1145 | 1145 | 0 | 0 | 0 | 0/0 | 0 |
| cfb-structural-v3 | 544 | 544 | 544 | 3 | 252 | 0 | 0/0 | 0 |
| cfb-structural-v4 | 1614 | 1614 | 1614 | 15 | 1614 | 96 | 96/96 | 0 |
| cfb-structural-v5 | 1119 | 1119 | 1119 | 7 | 1119 | 1119 | 1119/1119 | 0 |
| cfb-total-v1 | 602 | 602 | 592 | 0 | 0 | 0 | 0/0 | 0 |
| college-football-v1 | 501 | 501 | 491 | 0 | 0 | 0 | 0/0 | 0 |

Payload strings that looked like containers but failed to decode: 0 rows (none). A non-zero value here would mean an absent key was a decode failure.

#### Positive control: every payload key containing weather, wind, temp or precip

| Key path | Rows with key | Occurrences | Non-empty |
|---|---|---|---|
| `decision.model_input_snapshot_json.features.weather_evidence` | 2985 | 2985 | 2925 |
| `decision.model_input_snapshot_json.features.weather_inputs_verified` | 2985 | 2985 | 2985 |
| `decision.model_input_snapshot_json.features.weather_total_adjustment` | 6099 | 6099 | 6099 |
| `decision.weather_factor` | 6129 | 6129 | 0 |
| `feature.features.weather_evidence` | 2985 | 2985 | 2985 |
| `feature.features.weather_inputs_verified` | 2985 | 2985 | 2985 |
| `feature.features.weather_total_adjustment` | 6099 | 6099 | 6099 |
| `feature.model_input_snapshot.features.weather_evidence` | 2985 | 2985 | 2925 |
| `feature.model_input_snapshot.features.weather_inputs_verified` | 2985 | 2985 | 2985 |
| `feature.model_input_snapshot.features.weather_total_adjustment` | 6099 | 6099 | 6099 |

A key here that holds a raw reading (not a flag, evidence or adjustment) with a non-empty count above 0 would have been counted in the raw-reading row above.

#### The non-zero weather adjustments, cross-tabbed

| model_id | evidence source | created date | rows |
|---|---|---|---|
| cfb-structural-v3 | open_meteo:forecast | 2026-10-31 | 3 |
| cfb-structural-v4 | open_meteo:forecast | 2026-11-04 | 8 |
| cfb-structural-v4 | open_meteo:forecast | 2026-11-07 | 3 |
| cfb-structural-v4 | open_meteo:forecast | 2026-11-13 | 4 |
| cfb-structural-v5 | open_meteo:forecast | 2026-11-14 | 7 |

Non-zero rows without Open-Meteo forecast evidence: 0 of 25; non-zero rows created at or after the event start: 0. A non-zero row with no forecast evidence would be a point-in-time finding (the resolver's no-decision-time branch returns the first matching record with no source, hash or capture-time check).

Which weather file the live process reads: 722 distinct capture hashes are recorded in ledger evidence; found in the repo file 722 of 740 hashes, in the runtime file 0 of 128.

### Training-history weather is placeholder only

- `data/historical/ncaaf_games_all.jsonl` (7965 rows, sha256 `659e33b21de95e02...`): temperature_f distinct values 2 [[65.0, 7542], [72.0, 182]], wind_mph 2 [[5.0, 7542], [0.0, 182]], precipitation_in present on 0 rows, is_dome [[False, 7542], [True, 182]]. Legacy dome rows carry {'72.0F/0.0mph': 182}; legacy outdoor rows carry {'65.0F/5.0mph': 7542}. The newest 241 rows have no weather fields.
- `data/processed/ncaaf/games.jsonl` (7965 rows, sha256 `659e33b21de95e02...`): temperature_f distinct values 2 [[65.0, 7542], [72.0, 182]], wind_mph 2 [[5.0, 7542], [0.0, 182]], precipitation_in present on 0 rows, is_dome [[False, 7542], [True, 182]]. Legacy dome rows carry {'72.0F/0.0mph': 182}; legacy outdoor rows carry {'65.0F/5.0mph': 7542}. The newest 241 rows have no weather fields.

The extractor never reads weather off the history records (0 reads found), and the coefficients are literals (`src/model_prediction/features/cfb_features.py:626`, `src/model_prediction/features/cfb_features.py:628`, `src/model_prediction/features/cfb_features.py:630`); `scripts/cfb_v5_research.py` has 1 weather reference(s) (['"- Weather and QB channels inert in this data (placeholder weather fields; no PIT injury s']); the v5 artifact lists `weather_total_adjustment` only as a feature name ([41]). No fit consumes weather, so the weather term was never fitted.

## (b) QB availability

### Code

- Status to starter probability map: `src/model_prediction/features/cfb_qb_availability.py:29` (Out/IR 0.0, Doubtful, Questionable, Probable 1.0).
- A starter **not on the injury report** gets `starter_prob=1.0`, `status="not_listed"`, `reason="starter_absent_from_report"` (`src/model_prediction/features/cfb_qb_availability.py:174`, `src/model_prediction/features/cfb_qb_availability.py:173`, `src/model_prediction/features/cfb_qb_availability.py:175`). No snapshot, a stale one, or an unresolved starter returns `no_report` with 1.0 (`src/model_prediction/features/cfb_qb_availability.py:57`); an unmapped status such as Active also scores 1.0 (`src/model_prediction/features/cfb_qb_availability.py:162`).
- The discount `(1 - prob) * -4.5` only applies when `prob < 0.99` (`src/model_prediction/features/cfb_features.py:659`).

Probe of the real resolver on a synthetic snapshot:

| Case | starter_prob | status | reason |
|---|---|---|---|
| starter absent from report | 1.0 | not_listed | starter_absent_from_report |
| starter listed Out | 0.0 | Out | starter_listed_on_report |
| no snapshot | 1.0 | no_report | no_injury_snapshot_before_decision |
| team with no entries at all | 1.0 | not_listed | starter_absent_from_report |

### Ledger counts

| Measure | Row level (2 sides per row) | Latest row per event |
|---|---|---|
| Side cells | 12258 | 1214 |
| With a `qb_availability` cell | 2430 | 122 |
| Without one (older rows) | 9828 | 1092 |
| Starter prob below 0.99 (discount fires) | 0 | 0 |
| Non-zero `*_qb_value_adjustment` | 0 | 0 |
| Starter unresolved (no name, no id) | 0 | 0 |

Row-level status: {'not_listed': 2430}; reason: {'starter_absent_from_report': 2430}; starter_prob: {'1.0': 2430}.
Latest-row-per-event status: {'not_listed': 122}; reason: {'starter_absent_from_report': 122}; starter_prob: {'1.0': 122}.
Events with any row carrying a starter probability below 0.99: 0 of 61 events with a QB cell.

### Injury snapshots on disk

`data/availability/ncaaf/espn_injury_snapshots`: 129 snapshots, observed 2026-10-25T02:11:41.583294+00:00 to 2026-11-15T01:30:52.490178+00:00. QB entries across all snapshots: 0 covering 0 distinct teams, statuses {}; 129 snapshots carry no QB entry. The whole feed holds 387 entries over 129 snapshots (3 to 3 per snapshot, 3 distinct team/player/position/status entries, 3 teams), so it cannot describe QB availability for the slate. Distinct (team, player, status) QB rows with a penalising status: 0: []. All-position entry statuses: {'Out': 129, 'Active': 258}.

### What `passing_leader_from_competitor` returns pregame

- It scans the scoreboard competitor's `leaders` for the `passingLeader` or `passingYards` group and returns the first QB entry (`src/model_prediction/features/cfb_qb_availability.py:97`); its own docstring says "The season passing leader is the de facto starter; ESPN offers no college depth chart" (`src/model_prediction/features/cfb_qb_availability.py:93`).
- That is a cumulative-statistics leader, not a point-in-time starter: it does not know who starts Saturday and moves only when a backup out-throws the incumbent. Whether ESPN's pregame scoreboard `leaders` are exactly season-to-date totals was **not verified** (no network call; no stored raw scoreboard payload was inspected).
- Observed in the ledger: 122 teams have a named starter in the latest row per event; 0 of them show two or more distinct starter names (examples: {}). Games per team with a QB cell: {'1': 122}. A team needs at least two such games to show a change, so starter stability over a season is **not testable** from the ledger (QB cells exist only on rows created 2026-11-12T11:06:33.235182Z to 2026-11-15T01:39:57.810652Z).
- The history files carry `home_qb_experience_starts` / `away_qb_experience_starts` (7724 rows) but the extractor reads them 0 time(s) outside the record definition, so no historical QB signal feeds any fit.

## Item 3 data finding: possessions, success rate, EPA, drive and play data

| Field | Present rows | Distinct values | Top values | Absent rows |
|---|---|---|---|---|
| `possessions` | 7724 | 1 | [[12.5, 7724]] | 241 |
| `home_success_rate` | 7724 | 1 | [[0.42, 7724]] | 241 |
| `away_success_rate` | 7724 | 1 | [[0.42, 7724]] | 241 |
| `home_epa_per_play` | 7724 | 339 | [[0.0, 137], [0.029, 91], [-0.026, 86], [0.006, 84]] | 241 |
| `away_epa_per_play` | 7724 | 331 | [[0.0, 152], [-0.011, 87], [-0.006, 83], [-0.009, 81]] | 241 |

- `ncaaf_games_all.jsonl`: 7965 rows; 7724 legacy rows and 241 newest rows (parser {'espn-scoreboard-1': 241}). `data/processed/ncaaf/games.jsonl` has 7965 rows; the two files are identical row for row.
- `possessions` is a single constant on every row that has it; `home_success_rate` and `away_success_rate` are a single constant on every row that has them. They carry no information.
- EPA varies (339 / 331 distinct home/away values, stored to 3 decimals: True), but neither file records where it came from (no source or parser field on the 7724 legacy rows). Both sides are exactly 0.0 on 50 rows (home 0.0 on 137, away on 152). Diagnostic only: correlation of EPA difference with final margin 0.401, of home EPA with the game total 0.137. Provenance is **unverified**; v6 does not use it.
- The newest 241 rows lack `possessions`, EPA, success rate, weather, `is_neutral_site` and `week` (is_neutral_site absent on 241, week absent on 241; absent rows are exactly the parser-version rows: True).
- Keys whose name suggests drive, play, yardage, possession, EPA, success, snap or down data: {'possessions': 7724, 'home_epa_per_play': 7724, 'away_epa_per_play': 7724, 'home_success_rate': 7724, 'away_success_rate': 7724}. Only possessions, the two success rates and the two EPA fields qualify; there is no drive, play-count, yardage, or turnover field.

**Conclusion:** No drive or play data exists in the history, possessions is a single constant, and EPA has no recorded provenance, so v6 pace cannot be measured and is estimated from scoring through the additive points ratings.

## Reproducibility

- Script: `scripts/cfb_v6_input_audit.py`; AS_OF_UTC 2026-11-15T02:10:00Z.
- Ledger DB: `E:\model-prediction-runtime\ledgers\ledgers.db` (sqlite uri mode=ro).
- `data/historical/ncaaf_games_all.jsonl`: 7965 rows, sha256 `659e33b21de95e02633251c5614df3b63f9fea62898a140bd7efaa83e525ecb7`.
- `data/processed/ncaaf/games.jsonl`: 7965 rows, sha256 `659e33b21de95e02633251c5614df3b63f9fea62898a140bd7efaa83e525ecb7`.
- weather (repo) `E:\MP\data\weather\ncaaf_historical.jsonl`: 767 rows, sha256 `1c628ef981a57cba3db2c5cb24077b967e4a483d6b6d8077182b271416210714`.
- weather (runtime) `E:\model-prediction-runtime\weather\ncaaf_historical.jsonl`: 128 rows, sha256 `b10844220c018243c8b2061002b62711254f1c0f244798f4ae888c5ae654a0a7`.

