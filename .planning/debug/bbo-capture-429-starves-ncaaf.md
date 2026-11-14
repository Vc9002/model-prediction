---
status: awaiting_human_verify
trigger: "NCAAF Main rows are priced off ESPN consensus instead of Polymarket because the prospective BBO capture abandons the whole contract queue on the first HTTP 429, sorts NBA first, and the NCAAF binder has no indicative-slate fallback."
created: 2026-11-14T19:35:00Z
updated: 2026-11-14T20:25:00Z
slug: bbo-capture-429-starves-ncaaf
goal: find_and_fix
---

## Current Focus

hypothesis: Capture dies on the first post-retry 429 (abandon), spends its budget on NBA/early non-scope games first, inflates NCAAB/NCAAF ladders to 3 rungs, and competes with the NFL week-ahead capture; the NCAAF binder then has no Polymarket fallback and prices at ESPN consensus with blank provenance.
test: Implement fixes 1-5 with regression tests; revert each fix and watch its test fail.
expecting: New tests fail on old code, pass on new; live `polymarket-slate --league NCAAF` captures the in-scope queue without abandoning.
next_action: Fix committed as 79487b2c. Await operator confirmation after the 21:00Z daily: its prospective_bbo_capture should show source_rate_limited false with rate_limit_pauses/captured_by_league, and NCAAF ledger rows should carry sportsbook polymarket_us or polymarket_us_indicative with non-blank market_quote_provenance. Then archive to resolved/ and append the knowledge-base entry.
bug_class: Bohrbug (deterministic given gateway 429 behaviour)
daily_run_gate: daily-20261114T190001Z-5cab9c completed 19:37:31Z; next scheduled daily 21:00Z (16:00 ET). Edits must finish before then or pause.

reasoning_checkpoint:
  hypothesis: "NCAAF rows get espn_consensus because (a) capture_slate_snapshots sets source_rate_limited on the first post-retry 429 and skips the rest of the queue, (b) the queue is NBA-first then mixed-kickoff across 15 sports so NCAAF sits behind disabled-scope games, (c) ladder_keep=3 globally whenever NBA is on the slate, (d) the NFL week-ahead capture runs concurrently on the same IP limit, and (e) build_cfb_slate has no event-list fallback between BBO snapshots and ESPN consensus."
  confirming_evidence:
    - "daily_2026-11-14.log: four runs captured 17/2/22/13 with skipped_after_rate_limit 1754-2052 and source_rate_limited true"
    - "odds/ncaaf/2026-11-14 has 143 rows, all from standalone CLI runs, none from a daily run"
    - "code: Event set on first 429 (polymarket_us.py ~1125), sort key (league != NBA, start), ladder first-match in commands.py 259-266, f0 and f17 concurrent in daily.py 651/668, sportsbook espn_consensus fallback college_football.py 875-1015"
  falsification_test: "If after the fix a paced capture of the same NCAAF slate still abandons after a single 429 or still captures ~20 contracts, the pacing/resume hypothesis is wrong (the gateway would be refusing all traffic, not throttling bursts)."
  fix_rationale: "Pacing + pause/resume addresses the abandonment directly; tiered round-robin ordering guarantees in-scope sports get the budget first; per-league ladders shrink the queue; serializing removes the competing traffic; the indicative fallback ensures a Polymarket price is used even when a contract was not captured."
  blind_spots: "True sustainable gateway rate is unknown (0.5 s default interval is adaptive: doubles on each 429 episode). Daily wall-clock grows with paced capture; bounded by max_capture_seconds (900 s) and a 120 s budget for out-of-scope/started contracts."
  candidate_causes:
    - "code: abandon-on-first-429 + NBA-first ordering + global ladder (capture_slate_snapshots / _polymarket_slate)"
    - "environment: IP-wide gateway rate limit shared by two concurrent captures in daily.py"
    - "config: scope narrowed 2026-11-13 but BBO_CAPTURE_SPORTS still queues nine disabled sports ahead of later NCAAF kickoffs"
  and_gate: "yes - NCAAF starvation needs the abandon behaviour AND the ordering (either alone would leave NCAAF partially captured); the consensus pricing additionally needs the missing binder fallback. root_cause is a set."

## HARD CONSTRAINTS (operator rules, binding)

- **Do not edit any file under `src/` or `tests/` while a daily run is in progress.** Check with:
  `python -c "import sqlite3;db=sqlite3.connect(r'E:\model-prediction-runtime\runs.db');print(db.execute(\"select run_id,status,heartbeat_at_utc from runs where worker='daily' order by rowid desc limit 1\").fetchone())"`
  Run `daily-20261114T190001Z-5cab9c` was still `started` at 19:30Z (heartbeat 19:30:15Z). Poll until its status is `completed`/`failed` before the first edit. If a new daily run starts mid-edit, pause edits until it finishes. Scheduled daily runs today fired at 05:30Z, 11:00Z, 14:00Z, 17:00Z, 19:00Z.
- **Never run the full pytest suite.** Run only the affected files with `-o addopts='' -q`: `tests/test_polymarket_us.py`, `tests/test_college_football.py` (or whichever file covers `build_cfb_slate` / `_forecast_cfb_sport`), plus any new test file. `ruff check src/ tests/` must stay clean.
- `pytest` runs WITHOUT `MODEL_PREDICTION_*` env vars.
- Regression-test discipline: for each fix, revert it, watch the new test fail, restore it.
- Never place, modify, or cancel any Polymarket order; never call execute/sell endpoints. Capture is read-only GET traffic.
- Percentages only in any user-facing text, never American odds.
- Shared working tree with other sessions: stage only the files you changed (`git add <path>` by name, never `git add -A`). Commit message trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Branch: `refactor/cleanup-2026-11-11`.
- Add a dated entry to `docs/DEBUG.md` describing the root cause, the fix, and the evidence numbers below.
- Comments explain *why* (the specific real bug), not *what*.

## Symptoms

expected: Every scheduled daily run captures an executable Polymarket BBO for every in-scope (NBA, NCAAF, NFL, MLB, WNBA, NCAAB) main-line contract, and every NCAAF ledger row is priced against a Polymarket quote (executable if captured, indicative event-list price otherwise).
actual: Every daily capture on 2026-11-14 died within ~1 minute after NBA. NCAAF received zero snapshots from any daily run today; every NCAAF Main row written today carries `sportsbook=espn_consensus` with blank `market_quote_provenance`.
errors: `capture_slate_snapshots` result in `data/logs/daily_2026-11-14.log` for the four runs: captured 17 / 2 / 22 / 13, failures 1755 / 1925 / 1865 / 2054, `skipped_after_rate_limit` 1754 / 1921 / 1863 / 2052, `source_rate_limited: true`, `ladder_keep: 3`, `skipped_ladder_alternates` ~8,000. Skipped contracts by league in the last run: NCAAF 798, NCAAB 403, WNCAAB 218, NHL 210, ITF_MEN 85, ITF_WOMEN 63, ATP 28, CS2 26, NBA 22, WTA 21, plus ~20 soccer/esports/darts leagues.
timeline: Chronic. `docs/DEBUG.md` records 1,700 (2026-08-24) and 1,686 (2026-10-04) throttled book fetches per run; the "abandon queue on first 429" behaviour was added as mitigation and is pinned by `tests/test_polymarket_us.py::test_slate_capture_stops_queued_book_requests_after_rate_limit` (line ~378). Became visible now because the NCAAF/NCAAB audit on 2026-11-14 bucketed edges by quote source.
reproduction: `python -m model_prediction.cli polymarket-slate --league NCAAF --date 2026-11-14` (standalone, no competing traffic) captured 23 and skipped 551 at 17:34Z. An earlier standalone run at 16:10Z-16:13Z captured 120 NCAAF rows in 4 minutes (~0.5 req/s sustained) before stopping, which shows the gateway tolerates roughly one book request per 2 seconds, not a 4-worker burst.

## Evidence

- timestamp: 2026-11-14T19:20Z
  checked: `src/model_prediction/data_sources/polymarket_us.py::capture_slate_snapshots` (lines ~984-1200)
  found: (a) line ~1025 `source_rate_limited = Event()`; `_fetch` returns `source_rate_limited_after_http_429` for every remaining contract once set (line ~1092), and sets it on any `HTTPStatusError` 429 (line ~1125). No pause-and-resume, no budget, no per-sport fairness. (b) Sort key at line ~1080: `(league != "NBA", event_start_utc, event_id)` — NBA first, then by kickoff across ALL leagues mixed, so NCAAF 19:30Z+ games sit behind every soccer/tennis/esports game that starts earlier. (c) `ThreadPoolExecutor(max_workers=min(max_workers, len(all_contracts)))`, default 4, no inter-request pacing (the 1 req/s pacer in `_book_with_bbo_fallback` only applies when `prefer_bbo=True`, which the capture never sets). (d) `ladder_keep` is one integer for every ladder league; `ladder_leagues` default `{"NCAAF"}` but the daily passes `{"NBA","NCAAF","NCAAB","NFL"}`.
- timestamp: 2026-11-14T19:22Z
  checked: `src/model_prediction/cli/commands.py::_polymarket_slate` (lines ~230-300)
  found: Capture client is `PolymarketUSClient(..., max_attempts=2)`: one 429 + one ~0.5s retry, then `_book_with_bbo_fallback` sleeps >=1s and tries `/bbo` once; a 429 there raises and poisons the queue. `ladder_keep` resolution is first-match: `if ladder_keep is None and "NBA" in events: ladder_keep = 3` wins whenever NBA has games, so NCAAB (intended 1) and NCAAF (intended 2) get 3 rungs each, inflating the queue. Env override `POLYMARKET_CAPTURE_LADDER_KEEP` is global too. `_capture_horizon_hours` default 24h (fine).
- timestamp: 2026-11-14T19:23Z
  checked: `src/model_prediction/cli/daily.py` lines ~485-506 and ~650-670
  found: `_capture_nfl_week_ahead` -> `commands.capture_nfl_polymarket_week_ahead` runs `capture_slate_snapshots` for 7 future NFL dates INSIDE the same 16-worker io_pool, concurrently with the main `_polymarket_slate` capture (f0 and f17). Today's task timings: polymarket_slate 53.8 / 34.3 / 72.6 / 63.2 s; nfl_week_ahead_quotes 74.4 / 56.9 / 73.5 / 63.4 s — fully overlapping. Both hit the same IP-wide gateway limit, so each one's 429 poisons the other.
- timestamp: 2026-11-14T19:24Z
  checked: `BBO_CAPTURE_SPORTS` (polymarket_us.py line 267)
  found: 15 sports including soccer, esports, kbo, npb, tennis, hockey, darts, basketball (Euroleague etc.), wncaab — all `daily_forecast_enabled: false` since 2026-11-13. They account for ~800 of ~1,900 queued contracts per run and are fetched before later-kickoff NCAAF games. Only their OPEN picks' slugs are needed (for CLV), and those are already in `ladder_keep_slugs`.
- timestamp: 2026-11-14T19:25Z
  checked: snapshot archives `E:\model-prediction-runtime\odds\<sport>\2026-11-14\polymarket_snapshots.jsonl`
  found: nba 197 rows / 4 events (captured 14:22Z, 17:01Z, 17:20Z, 19:00Z — i.e. every daily run); ncaab 87 rows / 29 events (6 at 14:23Z and 8 at 17:22Z from daily, 68 at 17:59Z from a standalone CLI run); ncaaf 143 rows / 24 events, ALL from standalone CLI runs at 16:10Z and 17:34Z — none from a daily run; nfl none (Saturday). This proves daily budget is consumed by NBA then dies before NCAAB/NCAAF.
- timestamp: 2026-11-14T19:26Z
  checked: `src/model_prediction/models/college_football.py::build_cfb_slate` (lines 568-720, 860-1030)
  found: Only quote source is `evidence_root(root)/odds/ncaaf/<date>/polymarket_snapshots.jsonl` (line 613), matched via `_match_cfb_moneyline/_spread/_total`. If no match: `sportsbook = "espn_consensus"` (lines 875, 892, 976, 1015) and provenance `espn_consensus_indicative` (line 1029). No branch reads the Polymarket event-list (`PolymarketUSClient().slate("NCAAF", date)`) `price_probability`. `build_cfb_slate` has a `client` param that is the ESPN client, and `_forecast_cfb_sport` (forecast.py 5343) does not receive the daily's slate events.
- timestamp: 2026-11-14T19:27Z
  checked: `src/model_prediction/college_basketball_forward.py` lines 231, 500-660
  found: Reference pattern. NCAAB fetches `PolymarketUSClient().slate("NCAAB", date)` itself (line 231), uses `side["price_probability"]` as the ask when no archived BBO matches, and stamps `sportsbook="polymarket_us_indicative"`, `market_quote_provenance="indicative_slate"`, `market_quote_timestamp_valid=False`, `market_quote_source="polymarket_us"`. The audit on 2026-11-14 verified these indicative asks match the live slate to the cent on 153/157 rows.
- timestamp: 2026-11-14T19:28Z
  checked: `tests/test_polymarket_us.py`
  found: 38 tests. `test_slate_capture_stops_queued_book_requests_after_rate_limit` (~line 378) pins the abandon behaviour: `calls == 1`, `skipped_after_rate_limit == 2`. Ladder tests at ~960-1075 (`ladder_keep=2`, `ladder_keep=1`, keep_slugs). Fixture style: a stub client class with `snapshot(slug)`; `capture_slate_snapshots(client, events, tmp_path, date, max_workers=1)`.

- timestamp: 2026-11-14T19:40Z
  checked: data/logs/daily_2026-11-14.log, fifth run (daily-20261114T190001Z-5cab9c, completed 19:37:31Z)
  found: captured 7, failures 2018, skipped_after_rate_limit 2017, skipped_ladder_alternates 8523. odds/ncaaf/2026-11-14 still 143 rows (none from any daily run).
  implication: Same starvation on every run of the day; the 19:00Z run is the pre-fix baseline for the next daily.
- timestamp: 2026-11-14T19:42Z
  checked: config.is_sport_daily_enabled over every Polymarket sport key
  found: group keys without a models entry return True (esports, basketball, wncaab, EPL...), so is_sport_daily_enabled alone cannot define capture priority.
  implication: priority leagues = leagues with their own config models entry AND daily-enabled (today NBA, NCAAB, NCAAF, NFL; MLB/WNBA rejoin when season_active flips).
- timestamp: 2026-11-14T19:50Z
  checked: fail-then-pass for every new test (file reverted to HEAD, or a targeted mutation, then restored from backup)
  found: polymarket_us.py at HEAD -> 9 new capture tests fail (resume: `assert 0 == 3`; spacing: `[0.0,0.0,0.0] == [0.5,0.5,0.5]`; cross-worker spacing `1.4e-05 >= 0.08`; the rest TypeError on the new kwargs). Mutations: no round-robin -> ordering + time-budget tests fail; abandon-on-first-429 -> resume test fails; started games kept in the primary tier -> ordering test fails. commands.py at HEAD -> CLI ladder/priority test fails (`'int' object is not subscriptable`). daily.py at HEAD -> serialization test fails (ImportError). college_football.py at HEAD -> 5/5 binder tests fail (consensus-provenance test: market_lineage None). forecast.py at HEAD -> ledger-row rationale assertion fails. PIT lag guard removed -> past-decision test fails. All pass with the fix restored.
  implication: Each regression test catches its defect.
- timestamp: 2026-11-14T19:53Z
  checked: tests/test_polymarket_us.py::test_open_pick_slugs_reads_main_and_flat_and_survives_a_ledger_error
  found: Fails at HEAD too (HEAD source + HEAD test file): `_open_pick_slugs(root, None)` reads existing_main_ledgers(), the test monkeypatches main_ledger(). Pre-existing since 32528f57 (2026-11-08); unrelated to this fix.
  implication: Left as is; reported.

## Eliminated

- hypothesis: Polymarket does not list NCAAF games, so consensus is the only option.
  evidence: Live slate 2026-11-14 lists 117 NCAAF events with moneyline/spread/total ladders (`polymarket-slate --league NCAAF`); the standalone capture priced 24 events before being throttled.
- hypothesis: The horizon filter (24h) excludes evening games.
  evidence: `skipped_beyond_horizon: 0` in all four runs.
- hypothesis: Snapshots are written to the wrong root (the 2026-10 write/read split-brain).
  evidence: Standalone captures land in `E:\model-prediction-runtime\odds\ncaaf\2026-11-14\` and the 12:35Z/16:10Z rows were visible to the binder; the problem is that the daily writes almost none.

## Fix plan (implement all five; each gets a regression test)

1. **Pace and resume instead of abandon** (`capture_slate_snapshots`). Add a shared pacer (token bucket or min-interval lock, default ~0.5 s between book requests across all workers, i.e. ~2 req/s; keep `max_workers` but they share the pacer). On a 429 that survives the client's retries, pause ALL workers for the Retry-After/backoff interval and resume; only set `source_rate_limited` (abandon) after a bounded budget (e.g. 5 consecutive post-retry 429s or a wall-clock cap passed in by the caller, default ~15 min). Keep result keys (`source_rate_limited`, `skipped_after_rate_limit`) so health monitors still parse; add `rate_limit_pauses` and `elapsed_seconds`. Update the pinned abandon test to the new contract (consecutive-429 budget) and add a test that a single 429 followed by a 200 does NOT skip the remaining queue.
2. **Scope-aware, fair ordering.** Order the queue: (a) contracts whose slug is in `ladder_keep_slugs` (open picks' CLV) first; (b) then daily-enabled sports (`config.is_sport_daily_enabled`, or an explicit `priority_sports` argument supplied by `_polymarket_slate` from config so `polymarket_us.py` does not import config) round-robin across sports by kickoff, so no single sport's early games starve another; (c) disabled-scope sports last. Test: with a budget that allows N fetches, every priority sport gets its earliest games before any disabled-scope sport.
3. **Per-league ladder_keep.** Replace the first-match integer with a mapping `{"NBA": 3, "NCAAF": 2, "NCAAB": 1, "NFL": 2}` (env override may stay global for operators). Test: NBA present must not change NCAAB's keep from 1.
4. **Serialize the two captures in `daily.py`.** Run `_capture_nfl_week_ahead`'s Polymarket part after `_polymarket_slate` completes (chain it to f0, or move it out of the pool), since both share one IP-wide limit. Keep the ESPN consensus part in the pool. Test at the daily level is optional; a unit test on the ordering helper is enough if the restructure is small — at minimum run the existing daily tests that touch the pool.
5. **NCAAF indicative-slate fallback** (`build_cfb_slate`). When no BBO snapshot matches a market, fetch the NCAAF event list once per slate build (`PolymarketUSClient().slate("NCAAF", game_date)`, fail-soft to consensus on error) and use the matched side's `price_probability` as the ask, stamping `sportsbook="polymarket_us_indicative"`, `market_quote_provenance="indicative_slate"`, `market_quote_timestamp_valid=False`, `market_quote_source="polymarket_us"`, lineage None. ESPN consensus remains the final fallback and must stamp a non-blank provenance (`espn_consensus_indicative` already exists at line 1029 — make sure it reaches the ledger row's `market_quote_provenance`, which is blank today). Reuse `_match_cfb_*` team matching on the slate's normalized market dicts (they are the same shape `capture_slate_snapshots` consumes: `event["markets"][i]["sides"][j]["price_probability"]`, `team`, `selection`, `line`). Test: slate fixture with a game and no snapshot file → row priced at the slate ask with `polymarket_us_indicative`; snapshot present → executable wins; both absent → consensus with non-blank provenance.

Do NOT change artifact gates (`max_edge`), model code, or the auto-buyer. Do NOT delete disabled-sport capture support; only reorder/budget it.

## Verification after fix

- `ruff check src/ tests/`
- `pytest tests/test_polymarket_us.py -o addopts='' -q` and the CFB slate test file.
- Live proof (read-only GET traffic, allowed): `python -m model_prediction.cli polymarket-slate --league NCAAF --date 2026-11-14` → `prospective_bbo_capture.source_rate_limited` false (or pauses > 0 with captured ≈ queued). Report captured / skipped numbers. Then `--league NCAAB` the same.
- Do not run `cli forecast` or the daily yourself; the next scheduled daily will exercise the binder. State that plainly in the summary.

## Resolution

root_cause: "(1) capture_slate_snapshots abandoned the whole queue on the first post-retry 429 with four unpaced workers; (2) the queue was NBA-first then kickoff-ordered across all 15 capture sports, so out-of-scope early games consumed the budget ahead of NCAAF; (3) _polymarket_slate picked ladder_keep first-match (NBA => 3 for every ladder league); (4) daily.py ran the NFL week-ahead book capture concurrently with the slate capture on the same IP-wide limit; (5) build_cfb_slate had no event-list fallback and consensus rows carried no quote lineage (blank market_quote_provenance)."
fix: "Shared pacer (0.5 s spacing, pause-and-resume on 429 with doubling pause/spacing, abandon only after 5 consecutive episodes, 3 attempts per contract); tiered round-robin queue (open-pick contracts, then daily-enabled leagues' upcoming games, then out-of-scope/started on a 120 s budget; 600 s overall cap in the CLI); per-league ladder_keep mapping {NBA 3, NCAAF 2, NCAAB 1, NFL 2}; NFL week-ahead books serialized after the slate capture; NCAAF indicative event-list fallback (polymarket_us_indicative / indicative_slate) with a 10-minute point-in-time guard, and espn_consensus_indicative provenance on consensus rows."
verification: |
  guardrail_verdict: accepted
  signal_1_regression_tests: 13 new/updated tests; each failed on the old code (file reverted to HEAD or targeted mutation) and passes with the fix.
  signal_2_suite: ruff check src/ tests/ clean; mypy clean on the 4 changed modules; pytest (no MODEL_PREDICTION_* env, -o addopts='') on test_polymarket_us, test_nfl_week_ahead_capture, test_health_monitor_repair, test_cfb_indicative_quote, test_cfb_quote_integrity, test_cfb_serving_parity, test_cfb_structural_v4, test_college_football, test_five_blind_spot_guards, test_daily_integrity, test_daily_export_timing, test_forecast_runtime_regressions: 143 passed, 1 failed (test_open_pick_slugs_reads_main_and_flat_and_survives_a_ledger_error, pre-existing at HEAD).
  signal_3_live_ncaaf: polymarket-slate --league NCAAF 19:55-20:10Z: captured 311 (before 23), skipped_after_rate_limit 0 (before 551), source_rate_limited false, 6 pauses; all 298 upcoming contracts captured, 59/60 upcoming games ML+spread+total (60th lists no spread); 225 time-budget skips all started games.
  signal_4_live_ncaab: polymarket-slate --league NCAAB 20:13-20:21Z: captured 161 of 162, skipped_after_rate_limit 0, 2 pauses, 432 s; 46/46 upcoming games fully quoted.
  signal_5_binder: not run live (instruction: no cli forecast / daily). The next scheduled daily (21:00Z) exercises build_cfb_slate; expect NCAAF rows with sportsbook polymarket_us or polymarket_us_indicative and non-blank market_quote_provenance.
oracle_type: specified (exact call order, counts and provenance values asserted against the fix contract)
commit: 79487b2cbbd519eec645bb0af7077703403acc1d
files_changed:
  - src/model_prediction/data_sources/polymarket_us.py
  - src/model_prediction/cli/commands.py
  - src/model_prediction/cli/daily.py
  - src/model_prediction/cli/forecast.py
  - src/model_prediction/models/college_football.py
  - tests/test_polymarket_us.py
  - tests/test_nfl_week_ahead_capture.py
  - tests/test_cfb_indicative_quote.py
  - docs/FORECAST_RUNTIME_INTEGRITY.md
  - docs/DEBUG.md
judgment_calls:
  - "Priority leagues = league has its own config models entry AND is_sport_daily_enabled; is_sport_daily_enabled alone returns True for group keys (esports, basketball, EPL)."
  - "Started games' contracts moved to the secondary tier: their quotes are timestamp_valid=False and can serve neither a pregame decision nor a closing line. Not dropped (the horizon docstring keeps them)."
  - "Out-of-scope capture time-boxed to 120 s per daily run (CLAUDE.md: research must never block or slow the daily run). BBO_CAPTURE_SPORTS unchanged."
  - "Overall daily capture cap 600 s (not the 900 s floated in the plan) because the daily worker timeout is 60 min and the 19:00Z run already took 37.5 min."
  - "Indicative rows carry a market_lineage dict without snapshot hash/path (that is how market_quote_provenance reaches PickRequest); 'lineage None' in the plan read as 'no snapshot lineage'."
  - "Live event list used for pricing only when fetched within 10 minutes of the decision time (point-in-time guard for replays with a past observed_at)."
