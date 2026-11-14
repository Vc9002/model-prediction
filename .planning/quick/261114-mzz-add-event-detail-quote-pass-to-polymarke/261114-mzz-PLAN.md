---
phase: quick-261114-mzz
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/model_prediction/data_sources/polymarket_us.py
  - tests/test_polymarket_us.py
  - docs/DEBUG.md
autonomous: true
requirements: ["quick-261114-mzz"]
user_setup: []

must_haves:
  truths:
    - "One event-detail GET per in-scope upcoming event writes a top-of-book snapshot for every slate market of that event whose detail quote is open, active, not closed and satisfies 0 < bid < ask < 1"
    - "Event-detail rows carry reconstruction_status_source live_gateway_event_detail_quote, usage prospective_executable_bbo, market_state MARKET_STATE_OPEN, bid_size/ask_size None, transact_time_utc None, observed_at_utc stamped after the response arrives, and quote_cache_age_seconds from the Age header"
    - "A market with a missing, crossed, closed or inactive detail quote gets no event-detail row and is fetched by the /book pass ahead of already-quoted markets in its tier"
    - "An event-detail HTTP error falls through to the /book pass; a 429 ends the event-detail pass and leaves the rest to the /book pass"
    - "Ladder pruning applies only to the /book pass; the event-detail pass quotes every rung"
    - "A later /book row wins over an earlier event-detail row in _latest_capture_per_market and _fresh_latest_per_slug"
    - "A client without event_detail (test stubs, legacy) captures exactly as before; result keys are only added, never removed or renamed"
  artifacts:
    - path: src/model_prediction/data_sources/polymarket_us.py
      provides: "EventDetail dataclass, PolymarketUSClient._get_response and event_detail, _event_detail_quote_book, _annotate_capture, pass 1 in capture_slate_snapshots, uncovered-first pass 2"
      contains: "live_gateway_event_detail_quote"
    - path: tests/test_polymarket_us.py
      provides: "eight test_event_detail_* tests"
      contains: "test_event_detail_"
    - path: docs/DEBUG.md
      provides: "dated 2026-11-14 entry above the BBO-capture entry with evidence and live-proof numbers"
      contains: "live_gateway_event_detail_quote"
  key_links:
    - from: capture_slate_snapshots
      to: PolymarketUSClient.event_detail
      via: "getattr(client, 'event_detail', None); absent means pass 1 is skipped"
      pattern: "event_detail"
    - from: _event_detail_quote_book
      to: PolymarketUSClient._build_snapshot
      via: "synthetic book with bestBid/bestAsk/state and _response_source 'event_detail'"
      pattern: "_response_source"
    - from: pass 1 covered slugs
      to: pass 2 tier order
      via: "stable partition: uncovered first, covered after (depth)"
      pattern: "covered"
---

<objective>
Add an event-detail quote pass (pass 1) to `capture_slate_snapshots` so every in-scope game gets an
executable top-of-book quote from one `GET /v1/events/slug/{event_slug}` per event, ahead of the
existing paced `/book` pass (pass 2), which keeps its behaviour and budget but now spends it on
markets pass 1 could not quote first, then on the ladder shortlist for depth.

Purpose: the 21:00Z daily (run `daily-20261114T210002Z-126544`, commit 87d41d45) captured 203
contracts and left 955 `skipped_after_time_budget` (NCAAF 322, NCAAB 56) at its 600 s cap: `/book`
sustains only about 20 contracts a minute. Event detail returned both quotes on 525 of 530 slate
markets, matched live `/bbo` exactly on 26 of 27 spot checks, and served 40 requests in 1.1 s with no
429.

Output: modified `src/model_prediction/data_sources/polymarket_us.py`, eight new tests in
`tests/test_polymarket_us.py`, a dated `docs/DEBUG.md` entry, live-proof numbers.
</objective>

<execution_context>
@~/.claude/gsd-core/workflows/execute-plan.md
@~/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@.claude/CLAUDE.md
@.planning/debug/bbo-capture-429-starves-ncaaf.md
@src/model_prediction/data_sources/polymarket_us.py
@tests/test_polymarket_us.py

## HARD CONSTRAINTS (operator rules, verbatim; executor preconditions for every task)

- Do not edit src/ or tests/ while a daily run is in progress. Check: `python -c "import sqlite3;db=sqlite3.connect(r'E:\model-prediction-runtime\runs.db');print(db.execute(\"select run_id,status from runs where worker='daily' order by rowid desc limit 1\").fetchone())"`. Next scheduled daily 23:30Z today; finish and commit before then or pause until it completes.
- Never run the full pytest suite. Only affected files with `-o addopts='' -q`, and without MODEL_PREDICTION_* env vars. `ruff check src/ tests/` must be clean.
- Shared working tree with other sessions: stage only your own files by name, never `git add -A`/`.`. Sequential execution on the main tree, branch refactor/cleanup-2026-11-11.
- Commit trailer: `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
- Add a dated docs/DEBUG.md entry (above today's BBO-capture entry) with the evidence numbers.
- Live proof with read-only GETs only: `python -m model_prediction.cli polymarket-slate --league NCAAB --date 2026-11-14` and `--league NCAAF`; report `event_detail_captured`, `captured`, `elapsed_seconds`. Never call order/execute endpoints. Percentages only, never American odds.
- One known unrelated failure exists at HEAD: `test_open_pick_slugs_reads_main_and_flat_and_survives_a_ledger_error`. Leave it.

Shell notes: `MODEL_PREDICTION_RUNTIME_ROOT=E:\model-prediction-runtime` is exported in this shell, so
every pytest command below is prefixed with `env -u MODEL_PREDICTION_RUNTIME_ROOT -u
MODEL_PREDICTION_LEDGER_AUTHORITY`. The live CLI proof in Task 3 keeps the variable (snapshots must
land where the daily and the forecast readers look). At planning time (21:37Z) the latest daily was
`daily-20261114T210002Z-126544` with status `completed`; `polymarket_us.py`, `test_polymarket_us.py`
and `docs/DEBUG.md` had no uncommitted hunks, while other sessions hold uncommitted edits in
`dashboard/common.py`, `polymarket_execute.py`, `portfolio/auto_executor.py`, `runtime_paths.py`,
`tests/conftest.py` and two other test files: do not stage or touch those.

## Facts observed live at planning time (read-only GETs, 2026-11-14 21:36Z)

- `GET /v1/events/slug/cbb-njit-fair-2026-11-14`: headers `age: 27`, `cache-control: public,
  max-age=30`, `cf-cache-status: HIT`, `last-modified` 27 s before `date`. All 63 detail markets had
  `status "MARKET_STATUS_OPEN"`, `active true`, `closed false`, `ep3Status "OPEN"`. Each market has
  `slug`, `line`, `bestBidQuote`/`bestAskQuote` as `{"value": "0.8200", "currency": "USD"}`,
  `marketSides` (with `long`), `gameStartTime`, `updatedAt`. `updatedAt` was 07:25:35Z on a 21:36Z
  read: it is market-metadata time, not a quote time, so it must not be used as one.
- The same market's `GET /v1/markets/{slug}/bbo` returned `bestBid 0.82 / bestAsk 0.99` (equal to
  the detail quotes), `state "MARKET_STATE_OPEN"`, and no `transactTime`; it is CDN-cached too
  (`age: 8`, `max-age=30`). So existing `/bbo`-fallback rows already have `transact_time_utc None`,
  `bid_size/ask_size None`, `market_state "MARKET_STATE_OPEN"`. Event-detail rows must match that
  shape exactly; that is what "accepted wherever a /bbo row is" means here.

## Reader audit (done at planning time; Task 2 re-confirms)

- The only code that names a `reconstruction_status_source` value outside `polymarket_us.py` is
  `research_quote_contracts.py:68`, which requires `live_gateway_market_and_book_response` and so
  rejects `/bbo` rows too. There is no allow-list that accepts `/bbo` rows, so nothing is extended
  and that file is not edited (do not loosen it).
- Executable-quote gates read `market_state == "MARKET_STATE_OPEN"` (`college_contract_evidence.py:56`,
  `research_incumbent_evaluation.py:347`, `portfolio/auto_buyer_ledger.py:743`,
  `portfolio/auto_executor.py:2018`, `dashboard/orders.py:320`), `provider`, `reconstructed is
  False`, `timestamp_valid`, `usage` (`cli/forecast.py:231` and `models/college_football.py:292`
  lineage). `college_contract_evidence.py:60` also needs a parseable `transact_time_utc`, so it
  rejects `/bbo` and event-detail rows alike; `/book` rows from pass 2 still serve it for the ladder
  shortlist.
- Latest-per-slug readers: `models/college_football.py::_latest_capture_per_market` (`>=`, later
  file row wins ties), `cli/forecast_market_select.py::_fresh_latest_per_slug` (strict `>`),
  `PolymarketSnapshotStore.latest` (`>=`). Pass 1 runs before pass 2 and stamps earlier
  microsecond-precision times, so the later `/book` row wins in all three.
- `captured` consumers: `cli/commands.py:148` (NFL week-ahead, informational),
  `portfolio/auto_executor.py:542` (informational), `portfolio/ncaab_paper_trial.py:147` (sums
  rows written), `cli/daily.py:1691` (logs the whole dict). No health monitor gates on `failures`.
</context>

<tasks>

<task type="tracer" tdd="true">
  <name>Task 1: Tracer: event_detail client helper through pass 1 to stored snapshot rows, with every new test written red first</name>
  <files>src/model_prediction/data_sources/polymarket_us.py, tests/test_polymarket_us.py</files>
  <precondition>Daily-run check prints a row whose status is not `started` (no daily in progress), `git branch --show-current` prints `refactor/cleanup-2026-11-11`, and `git status --porcelain -- src/model_prediction/data_sources/polymarket_us.py tests/test_polymarket_us.py` prints nothing.</precondition>
  <behavior>
    All eight tests are added BEFORE any src edit, named with the token `event_detail`, and run once against HEAD's src; each must fail (record the failure lines for the SUMMARY). Reference new symbols as `polymarket_us.EventDetail` inside test bodies, never in the module-level import list, so HEAD fails per test rather than at collection. Add `monkeypatch.setattr(polymarket_us, "EVENT_DETAIL_MIN_REQUEST_INTERVAL_SECONDS", 0.0, raising=False)` to the existing autouse `_unpaced_capture` fixture. Stub style follows `_SlateClient` / `_slate_market`: an `_EventDetailClient(_SlateClient)` stub records `event_detail(slug)` calls and returns `polymarket_us.EventDetail(event={"slug": ..., "markets": [...]}, cache_age_seconds=12.0)`; a `_detail_market(slug, bid, ask, **overrides)` helper builds a detail market with status `MARKET_STATUS_OPEN`, active True, closed False, ep3Status `OPEN`, line -3.5, and `{"value": "0.5900", "currency": "USD"}`-shaped quotes (None when bid/ask is None). Slate events carry `event_id`, `event_slug`, `event_start_utc` two hours after the `now=` passed to capture.
    - T1 test_event_detail_returns_event_and_cdn_cache_age: real `PolymarketUSClient` over `httpx.MockTransport`; `Age: 27` gives `cache_age_seconds == 27.0` and the parsed event; no Age header gives None; `Age: abc` gives None.
    - T2 test_event_detail_pass_writes_one_quote_per_quoted_slate_market_from_one_request (the tracer's end-to-end check): one NBA event, three quoted slate markets; `event_detail` called exactly once; the stored JSONL (read back from `PolymarketSnapshotStore.for_sport_date(tmp_path, "nba", day).path`) has three rows with `reconstruction_status_source == "live_gateway_event_detail_quote"`, `usage == "prospective_executable_bbo"`, `market_state == "MARKET_STATE_OPEN"`, `long.ask == ask`, `long.bid == bid`, `short.ask == round(1 - bid, 6)`, `long.bid_size is None`, `transact_time_utc is None`, `quote_cache_age_seconds == 12.0`, `timestamp_valid is True`, `league/event_id/event_slug/market_type/line` from the slate; result `event_detail_events == 1`, `event_detail_captured == 3`, `event_detail_failures == 0`, `captured_by_source["live_gateway_event_detail_quote"] == 3`. Uses an advancing clock (monkeypatch `polymarket_us.utc_now` so each call returns one second later; the stub reads the same clock inside `event_detail`) and asserts every row's `observed_at_utc` is strictly later than the stub's fetch stamp (per CLAUDE.md: never pin utc_now to one value when capture order matters).
    - T3 test_event_detail_pass_skipped_for_clients_without_event_detail: the T2 slate through plain `_SlateClient` (no `event`, no `event_detail`); `slate_calls` equals the slate order, `captured == 3`, `event_detail_events == event_detail_captured == event_detail_failures == 0`, `captured_by_source` has no `live_gateway_event_detail_quote` key.
    - T4 test_event_detail_unquoted_crossed_or_closed_markets_fall_through_to_book_pass_first: markets m1 quoted, m2 bestAskQuote None, m3 quoted, m4 crossed (bid 0.60, ask 0.55), m5 `closed True`; `max_workers=1`; no event-detail row for m2/m4/m5; pass-2 `slate_calls == ["m2", "m4", "m5", "m1", "m3"]`.
    - T5 test_event_detail_http_error_falls_through_and_429_stops_the_pass: two events; (a) first `event_detail` raises `httpx.HTTPStatusError` 500: `event_detail_failures == 1`, second event's quoted markets are captured by pass 1, every first-event market is fetched in pass 2; (b) first raises a 429 (reuse `_rate_limit_error`): the stub's call list holds only the first event slug, `event_detail_captured == 0`, all markets of both events fetched in pass 2.
    - T6 test_event_detail_quotes_every_ladder_rung_while_book_pass_keeps_the_shortlist: NCAAF event via `_ladder_event({"far": 0.95, "near": 0.52, "mid": 0.70, "close": 0.49})` plus `event_slug`, all four quoted in detail, `ladder_keep=1`: `event_detail_captured == 4`, pass-2 `slate_calls == ["close"]`, `skipped_ladder_alternates == 3`.
    - T7 test_event_detail_covered_market_book_failure_is_not_a_capture_failure: m1 quoted and its pass-2 `snapshot_from_slate` raises `ValueError`; m2 unquoted and its pass-2 fetch raises: `failures == 1` (m2 only), `failure_details` names only m2, `covered_book_failures == 1`, `captured == 1` (m1's event-detail row), `failures_by_league` counts 1.
    - T8 test_event_detail_row_loses_to_a_later_book_row_in_latest_capture_readers: NBA spread market quoted in detail (clock T0) and pass-2 stub row observed T0+5 s; the store holds two rows for the slug (event-detail first, then the pass-2 row); `models.college_football._latest_capture_per_market(rows)` and `cli.forecast_market_select._fresh_latest_per_slug(rows, None)` each return the pass-2 row.
  </behavior>
  <action>
    Implement in `src/model_prediction/data_sources/polymarket_us.py` only. Do not touch slate hydration, `refresh_contract_snapshots`, `_book_with_bbo_fallback`, `_CapturePacer`, `_capture_tiers` or the ladder helpers.

    Client: move the body of `PolymarketUSClient._get` (retry loop and all its comments) into a new `_get_response(self, path, params=None) -> httpx.Response` that returns the response after `raise_for_status()`; `_get` becomes a one-line `.json()` wrapper, so its behaviour and `test_get_retries_connect_errors_then_succeeds_and_raises_when_exhausted` are unchanged. Add a frozen dataclass `EventDetail` (fields `event: dict[str, Any]`, `cache_age_seconds: float | None`) beside `SportSlateResult`. Add `event_detail(self, slug) -> EventDetail` that GETs `/v1/events/slug/{slug}` through `_get_response`, raises `TypeError` when the `event` payload is not a dict, and parses the `Age` header with a new module helper `_cache_age_seconds(response) -> float | None` (non-negative integer digits only, else None). Keep `event()` as is: slate hydration uses it. Gate pass 1 on `event_detail`, which wraps `event()`'s endpoint: a stub or legacy client without `event` also lacks `event_detail`, so it skips pass 1 silently; a stub that only has `event()` skips too, which keeps every existing stub's behaviour.

    Quote validation: add `_event_detail_quote_book(detail_market, slate_line) -> dict | None`. Return None (fail closed: no snapshot, leave the market to pass 2) unless `status == "MARKET_STATUS_OPEN"`, `active is True`, `closed is False`, `ep3Status` is absent or `"OPEN"`, both quotes parse via `_amount` to finite floats with 0 < bid < ask < 1, and, when both the detail `line` and the slate line are present, they are equal as floats. Otherwise return a book dict with `bestBid`/`bestAsk` (the raw quote dicts), `state: "MARKET_STATE_OPEN"` and `_response_source: "event_detail"`. Comment why the state is mapped: the detail endpoint reports the open state as `MARKET_STATUS_OPEN` and every executable-quote reader gates on `MARKET_STATE_OPEN`; only the verified open state is mapped, so an event-detail row is exactly as executable as a `/bbo` row, never looser. Leave `transactTime` out: neither event detail nor `/bbo` carries one (observed 2026-11-14), and `updatedAt` is metadata time.

    Snapshot shape: in `_build_snapshot`, replace the two-way `reconstruction_status_source` conditional with a lookup on `book.get("_response_source")` (`"bbo"` gives `live_gateway_market_and_bbo_response`, `"event_detail"` gives `live_gateway_event_detail_quote`, anything else gives `live_gateway_market_and_book_response`). Extract the `market_like` construction in `snapshot_from_slate` into a static `_slate_market_like(event, market)` used by both passes. Extract the `snapshot.update({...})` block of `_fetch_once` (with its comments) into a module function `_annotate_capture(snapshot, league, event, market) -> dict` used by both passes, so every pass-1 row has exactly the pass-2 field set.

    Pass 1 in `capture_slate_snapshots`: add `EVENT_DETAIL_MIN_REQUEST_INTERVAL_SECONDS = 0.25` beside the capture constants, with a comment citing the evidence (40 event-detail requests in 1.1 s with no 429; responses are CDN-cached, max-age 30). In the per-league loop, collect each league's entries after the summer-league, preseason and horizon filters and BEFORE `_prune_line_ladders`, because ladder pruning must not apply to pass 1. Move the `overall_deadline` computation up so it is set before pass 1. Group those entries by (league upper, `event_slug`), dropping events with no `event_slug` and events that `_has_started` at `capture_now`. Order the events as `_round_robin_by_kickoff` over one representative entry per event: first events of priority leagues (every league when `priority_leagues` is None), then events of other leagues holding at least one `ladder_keep_slugs` slug. If `getattr(client, "event_detail", None)` is not callable, skip pass 1. Otherwise loop sequentially: stop when `overall_deadline` has passed; space requests at least `EVENT_DETAIL_MIN_REQUEST_INTERVAL_SECONDS` apart with `time.monotonic`/`time.sleep` (so `_FakeClock` works); count each request in `event_detail_events`; call `event_detail(event_slug)` and capture `observed_at = utc_now()` only AFTER it returns (a stamp taken before the call would predate the data). On `httpx.HTTPStatusError` with status 429, count a failure and end pass 1: the gateway limit is IP-wide and pass 2 needs the budget. On any other `httpx.HTTPError`, `KeyError`, `TypeError` or `ValueError`, count a failure and continue: fail soft per event, and pass 2 still covers its markets. Index the detail markets by `slug`. For each slate market of the event that has `market_slug`, `sides` and `market_id` and passes `_event_detail_quote_book`, build `_build_snapshot(_slate_market_like(event, market), book, slug, observed_at)`, run `_annotate_capture`, set `quote_cache_age_seconds`, append `(league, sport, slug, snapshot, None)` to `results` ahead of the pass-2 results, and add the slug to `covered_slugs`. Catch `KeyError`, `StopIteration`, `TypeError` and `ValueError` per market and skip that market (fail closed). Pass-1 rows then flow through the existing result loop.

    Result dict: add `event_detail_events`, `event_detail_captured`, `event_detail_failures` and `captured_by_source` (a count per `reconstruction_status_source`, `"unknown"` when absent, sorted keys). Decision, to be recorded in the docstring and in DEBUG.md: `captured`, `captured_by_league`, `missing_executable_ask`, `timestamp_invalid_count` and `nba_missing_quote_event_ids` count rows written by BOTH passes, because consumers read `captured` as snapshots persisted (`ncaab_paper_trial` sums it); `captured_by_source` gives the split. `event_detail_failures` is never added to `failures`: those markets fall through to pass 2, which records the real outcome. Extend the docstring with a pass 1 / pass 2 paragraph. Do not change gates, models, the auto-buyer or any order endpoint.
  </action>
  <verify>
    <automated>env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY python -m pytest tests/test_polymarket_us.py -o addopts='' -q -k "event_detail_returns or event_detail_pass or event_detail_http_error or event_detail_quotes_every or event_detail_row_loses"</automated>
  </verify>
  <done>All eight new tests were observed failing against HEAD's src (output recorded). After the src change, T1, T2, T3, T5, T6 and T8 pass. `grep -v '^\s*#' src/model_prediction/data_sources/polymarket_us.py | grep -c live_gateway_event_detail_quote` is at least 1 (baseline at HEAD: 0). No commit yet; T4 and T7 may still fail until Task 2.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Pass 2 serves unquoted markets first, covered /book failures are not capture failures, reader audit, file green</name>
  <files>src/model_prediction/data_sources/polymarket_us.py, tests/test_polymarket_us.py</files>
  <precondition>Daily-run check shows no daily in progress, and Task 1's tests T1, T2, T3, T5, T6 and T8 pass.</precondition>
  <behavior>
    - T4: within each tier the pass-2 queue is a stable partition, unquoted markets first in their existing order, then quoted markets in their existing order.
    - T7: a pass-2 fetch that fails or is skipped for a slug in `covered_slugs` increments `covered_book_failures` and is NOT added to `failures`, `failures_by_league` or `failure_details` (so not to `skipped_after_time_budget` or `skipped_after_rate_limit` either).
  </behavior>
  <action>
    In `capture_slate_snapshots`, after `_capture_tiers` returns `(primary_contracts, secondary_contracts)`, reorder each tier with a new helper `_uncovered_first(entries, covered_slugs)`: a stable partition, entries whose `market_slug` is not in `covered_slugs` first, then the rest, each part in its existing round-robin order. The pass-2 set is unchanged: the same ladder-pruned contracts, the same pacer, the same `max_capture_seconds` and `secondary_max_seconds`. Only the order changes, so the paced budget reaches markets with no quote first and then refreshes the ladder shortlist with depth. `primary_contracts` and `secondary_contracts` in the result keep counting the same lists.

    In the result loop, when `snapshot is None` and the slug is in `covered_slugs`, increment a new `covered_book_failures` counter and skip the failure bookkeeping. The market already has this run's executable quote, so `failures`, `status`, `failures_by_league`, `skipped_after_time_budget` and `skipped_after_rate_limit` keep meaning "contracts left with no snapshot from this run". Add `covered_book_failures` to the result dict and state this meaning in the docstring.

    Re-run the reader audit from the context section against the current tree and record the outcome in the SUMMARY. The audit grep `grep -rn "live_gateway_market_and_bbo_response\|live_gateway_market_and_book_response" src/model_prediction --include=*.py | grep -v data_sources/polymarket_us.py` must list only `research_quote_contracts.py` (same as HEAD), confirming there is no `/bbo` allow-list to extend. `git diff --quiet -- src/model_prediction/research_quote_contracts.py src/model_prediction/college_contract_evidence.py src/model_prediction/cli/forecast_market_select.py src/model_prediction/models/college_football.py` must exit 0: readers are confirmed, not edited, and validation is not loosened.

    Then run ruff and the whole of `tests/test_polymarket_us.py`. The only allowed failure is the known `test_open_pick_slugs_reads_main_and_flat_and_survives_a_ledger_error`. Re-check the daily-run gate, review `git diff -- src/model_prediction/data_sources/polymarket_us.py tests/test_polymarket_us.py` to make sure it holds only this task's hunks, then commit those two files by name: `git add src/model_prediction/data_sources/polymarket_us.py tests/test_polymarket_us.py`, message `feat(quick-261114-mzz): event-detail quote pass ahead of the paced /book capture`, body summarising pass 1 / pass 2 and the `captured` decision, ending with the trailer `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`. Confirm `git show --name-only --format= HEAD` lists exactly those two files.
  </action>
  <verify>
    <automated>ruff check src/ tests/ && env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY python -m pytest tests/test_polymarket_us.py -o addopts='' -q</automated>
  </verify>
  <done>ruff is clean. `tests/test_polymarket_us.py` shows exactly 1 failure, the known `test_open_pick_slugs_reads_main_and_flat_and_survives_a_ledger_error`, and all eight `event_detail` tests pass. The audit grep matches HEAD, and the reader files have no diff. One commit holds exactly the two files with the required trailer.</done>
</task>

<task type="auto">
  <name>Task 3: Live read-only proof on the NCAAB and NCAAF slates, DEBUG.md entry, docs commit</name>
  <files>docs/DEBUG.md</files>
  <precondition>Task 2's commit exists, the daily-run check shows no daily in progress, and the current time is before 23:25Z (otherwise wait for the 23:30Z daily to complete first: the live capture must not share the IP-wide gateway limit with a daily run).</precondition>
  <action>
    Live proof uses read-only GETs only. Keep `MODEL_PREDICTION_RUNTIME_ROOT` exported so snapshots land in the runtime root where the daily and the forecast readers look. Run `python -m model_prediction.cli polymarket-slate --league NCAAB --date 2026-11-14 > "${TEMP:-/tmp}/mzz-ncaab.json"`, then the same with `--league NCAAF` into `mzz-ncaaf.json`. From `prospective_bbo_capture` in each file, extract and report `event_detail_events`, `event_detail_captured`, `event_detail_failures`, `captured`, `captured_by_source`, `covered_book_failures`, `skipped_after_time_budget`, `rate_limit_pauses` and `elapsed_seconds`. Use a short `python -c` with `json.load` and keep the JSON files out of the repo.

    Spot-check up to three stored `live_gateway_event_detail_quote` rows: read them from `$MODEL_PREDICTION_RUNTIME_ROOT/odds/ncaab/2026-11-14/polymarket_snapshots.jsonl` and `.../odds/ncaaf/2026-11-14/...`, then compare each with a read-only `GET https://gateway.polymarket.us/v1/markets/{slug}/bbo` (bestBid/bestAsk). Report prices as percentages (for example 82.0% / 99.0%), never American odds. Never call any order, execute or portfolio endpoint. If a slate has no upcoming games left at run time, report that and the counters as they are; do not change the date or widen scope.

    Then add a dated entry to `docs/DEBUG.md` directly above the `## 2026-11-14 — Prospective BBO capture abandoned on the first 429 and starved NCAAF; ...` heading, titled `## 2026-11-14 — Event-detail quote pass ahead of the paced /book capture (quick-261114-mzz)`. Include:
    - the evidence: run `daily-20261114T210002Z-126544` captured 203 and left 955 `skipped_after_time_budget` (NCAAF 322, NCAAB 56) at the 600 s cap; `/book` sustains about 20 contracts a minute; event detail had both quotes on 525 of 530 slate markets, 26 of 27 spot checks matched live `/bbo`, and 40 requests took 1.1 s with no 429;
    - the planning-time observations: detail status values; `/bbo` has no `transactTime` and is CDN-cached like event detail; `updatedAt` is metadata time;
    - the design: pass 1 per event, the fail-closed per-market gate, fail-soft per event, 429 ends pass 1, the ladder exempt in pass 1, pass 2 uncovered-first with an unchanged set and budget;
    - the decisions: `captured` counts both passes with the split in `captured_by_source`; `covered_book_failures` is excluded from `failures`; `market_state` is mapped only from the verified open state;
    - the reader audit result, including that `college_contract_evidence` rejects `/bbo` and event-detail rows alike for want of a transact time, so pass 2 keeps serving it;
    - the live-proof numbers, the spot-check percentages, and the Task 2 commit hash.

    Commit `docs/DEBUG.md` by name only, message `docs(quick-261114-mzz): record the event-detail quote pass and its live proof`, with the trailer `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
  </action>
  <verify>
    <automated>grep -n "quick-261114-mzz" docs/DEBUG.md && test "$(git show --name-only --format= HEAD)" = "docs/DEBUG.md"</automated>
  </verify>
  <done>Both live runs completed with read-only GETs. The SUMMARY reports `event_detail_captured`, `captured` and `elapsed_seconds` for NCAAB and NCAAF, plus spot-check percentages. The DEBUG.md entry sits directly above the BBO-capture entry with the evidence and live numbers. The docs commit holds only `docs/DEBUG.md` with the required trailer.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Polymarket public gateway / CDN to snapshot archive | Untrusted JSON (quotes, states, headers) becomes executable-quote rows that forecasts and the paper auto-buyer price against |
| Snapshot archive to forecast/auto-buyer readers | Rows must keep point-in-time and executability semantics identical to existing `/bbo` rows |

## STRIDE Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation Plan |
|-----------|----------|-----------|----------|-------------|-----------------|
| T-mzz-01 | Tampering | `_event_detail_quote_book` | high | mitigate | Fail closed per market: requires MARKET_STATUS_OPEN + active + not closed + ep3Status OPEN when present, finite 0 < bid < ask < 1, and a matching line; anything else writes no row (T4) |
| T-mzz-02 | Tampering | pass-1 `observed_at_utc` (PIT) | high | mitigate | `utc_now()` is captured only after `event_detail` returns, and an advancing-clock test (T2) pins the order; `timestamp_valid` is computed by the shared `_annotate_capture` |
| T-mzz-03 | Information integrity | CDN-cached quote staleness | medium | mitigate | `quote_cache_age_seconds` is stored from the Age header (max-age 30, the same cache class as `/bbo`); a later `/book` row still wins in the latest-per-slug readers (T8) |
| T-mzz-04 | Denial of service | event-detail 429s eating the capture budget | medium | mitigate | The first 429 ends pass 1, pass 1 honours `max_capture_seconds`, and requests are spaced 0.25 s apart (T5) |
| T-mzz-05 | Elevation of privilege | real-money order path | high | mitigate | No order, execute or portfolio endpoint is called or edited; the live proof uses read-only GETs; commits are checked to contain only the named files |
| T-mzz-SC | Tampering | npm/pip/cargo installs | high | accept | No package installs in this plan |
</threat_model>

<verification>
- Every new test was observed failing against HEAD's src before the change (Task 1 RED output in the SUMMARY).
- `ruff check src/ tests/` is clean. `tests/test_polymarket_us.py` has 1 known unrelated failure and nothing else.
- Reader files are unchanged, and the audit grep matches HEAD.
- Live NCAAB and NCAAF runs report `event_detail_captured`, `captured` and `elapsed_seconds`; spot checks match `/bbo` in percentages.
- Two commits on `refactor/cleanup-2026-11-11`, each holding only its named files, both with the Fable 5.2 trailer, both finished before the 23:30Z daily or after it completed.
</verification>

<success_criteria>
- One event-detail request per in-scope upcoming event yields an executable top-of-book row for every validly quoted slate market, in the existing row shape with the new source tag.
- The /book pass keeps its budget and set, spends it on unquoted markets first, then on ladder depth.
- Result keys `event_detail_events`, `event_detail_captured`, `event_detail_failures`, `captured_by_source` and `covered_book_failures` are added; no key is removed or renamed.
- No change to gates, models, the auto-buyer or order endpoints; no reader validation is loosened.
</success_criteria>

<output>
Create `.planning/quick/261114-mzz-add-event-detail-quote-pass-to-polymarke/261114-mzz-SUMMARY.md` when done
</output>
