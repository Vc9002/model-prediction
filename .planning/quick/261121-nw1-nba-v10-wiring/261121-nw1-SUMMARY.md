---
phase: quick-261121-nw1
plan: 01
status: complete
branch: nba-v10/wiring (from refactor/cleanup-2026-11-11 @ dd8716d2)
---

# quick-261121-nw1 summary: nba-anchor-v10 wired as a Flat-only challenger

## Commits

- `c0222198` feat: wire nba-anchor-v10 into the NBA slates as a Flat-only challenger
- `2d473ef3` fix: NBA-scoped LA alias in settlement closing-CLV lookup
- `3321c7c9` fix: stale-open watcher compares event starts as instants
- `a7a19779` fix: v10 mid anchor rules, settlement slug, T-B side count (+ rationale wording commit)

## Deviations from the plan / spec

- Polymarket mid has NO 90-min age limit (it is the quote v9 priced, chosen by the slate's own
  freshness selector; its age is stored). The 90-min limit applies to the consensus capture only.
  Without this, a run more than 90 min after the last quote capture would skip games v9 calls.
- A mid anchors only on a two-sided quote at most 10 points wide; otherwise the game is skipped
  (`NO_CALL_ANCHOR_MISSING`). Replay: 1 of 12 (NO @ SA total, over bid 3c / ask 50c).
- ANCHOR_WIRING item 6 overridden: no-anchor games are skipped and counted, not logged as
  `NO_ANCHOR_MODEL_ONLY` (Flat forces every row to CALL; no reason-coded no-call rows).
- Moneyline skipped (anchor module prices spread and total only).
- T-B may fire on the side opposite the row's selection (anchored favourite). The payload carries
  `tb.row_side_is_tb_side` and the summary counts `tb_flags_off_row_side`; grade T-B from the payload.
- Rationale carries `market_slug=` so settlement's closing-CLV fallback finds the contract.

## What changed

- `cli/forecast_nba_anchor_v10.py` (new): input collection (v9 spread mean; v9.1 totals engine with
  scoreboard season types), anchor loading (ESPN devigged consensus, else Polymarket mid, each turned
  into a line-space mean with the artifact sigma, 90-min freshness, decision time read after loading),
  `price_game`, T-B block, one Flat row per game and market via `flat_ledger.append_evaluated`.
- `cli/forecast.py`: the NBA spread/total slates collect `anchor_v10_inputs` after v9's contracts are
  final (fail-soft); `_forecast_nba_{spread,total}_sport` call `_with_nba_anchor_v10` after v9 logging,
  inside a broad `except`, and drop the inputs from the result.
- `models/nba_anchor_v10.py`: `CapturedAnchorSource.max_age` (optional; default unchanged).
- `nba_anchor_v10_replay.py` (new) + `learned_replay.py` dispatch: the ledger replays every stored
  snapshot on append and rejected the first v10 rows (`invalid_learned_replay_snapshot`, found on the
  real-ledger replay). Schema `nba-anchor-v10-replay-v1` recomputes the probability from the stored
  anchor mean, model mean, lambda, sigma and contract line.
- Follow-ups: `cli/settle.py` uses `_nba_team_matches` for NBA spread closes only;
  `system_health._stale_open_rows` parses starts in Python (stored values untouched).

Not changed: Main routing, `config/production.yaml`, auto-buyer whitelists, `auto_buyer_state.json`.
Moneyline skipped (anchor module prices spread and total only).

## v9-unchanged proof (real 2026-11-21 slate, read-only replay)

Scratch data root (copies of `processed/nba`, injury snapshots, calibration, `odds/nba/2026-11-21`),
scratch SQLite ledgers, `MODEL_PREDICTION_RUNTIME_ROOT` pointed at scratch, frozen clock 05:00Z and
saved scoreboard. Baseline at dd8716d2 vs final HEAD, two passes each: Flat 12 v9 rows (6 spread,
6 total) and Main 2 v9 rows, all 119 stored fields identical (pick_id / created_at excluded, scratch
paths normalised), same order. Pass 2: v9 duplicates as before, v10 `duplicate` 12, nothing raised.

Replayed v10: 12 Flat rows, 0 Main; all 12 anchored on the DraftKings consensus captured 04:38Z
(22 min before the replay decision time); T-B flags 0 (largest gross gap +1.43 pts, LAC @ DAL over).

Extra replays (v9 diff 0 in each): no ESPN file at 05:00Z gives 11 `polymarket_mid` rows
(probability = mid, net edge -2.0% to -4.0%, T-B not evaluated) plus 1 skipped one-sided book;
clock 06:30Z (consensus 112 min old) gives the same 11 mid rows, against a baseline run of the
dd8716d2 code at 06:30Z. Real runtime `ledgers.db` (mode=ro): 0 `nba-anchor-v10` rows.

## Operational note

The pre-tip worker captures consensus only within 25 min of tip (every 10 min, 11:00-02:00 ET). The
2026-11-21 file came from a wider manual capture. At ordinary daily-run times most games will have no
consensus capture inside 90 min, so v10 will mostly anchor on the Polymarket mid (net edge always
negative, T-B not evaluated) until consensus is captured at forecast time. No capture call was added to
the forecast (out of scope).

## Tests

`tests/test_nba_anchor_v10_wiring.py` (14), `tests/test_nba_settlement_clv_alias.py` (2),
`tests/test_system_health_start_offsets.py` (1); each was shown failing with its source change
reverted. `tests/test_nba_serving_plumbing.py` unchanged and passing. Targeted runs only
(`-o addopts='' -q`); ruff check and format clean on touched files (`data_sources/espn.py` and
`tests/test_ingest.py` were already unformatted at dd8716d2 and are untouched).
