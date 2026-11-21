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

## Operational note

The pre-tip worker captures consensus only within 25 min of tip (every 10 min, 11:00-02:00 ET). The
2026-11-21 file came from a wider manual capture. At ordinary daily-run times most games will have no
consensus capture inside 90 min, so v10 will mostly anchor on the Polymarket mid (net edge always
negative, T-B not evaluated) until consensus is captured at forecast time. No capture call was added to
the forecast (out of scope).

## Tests

`tests/test_nba_anchor_v10_wiring.py` (11), `tests/test_nba_settlement_clv_alias.py` (2),
`tests/test_system_health_start_offsets.py` (1); each was shown failing with its source change
reverted. `tests/test_nba_serving_plumbing.py` unchanged and passing. Targeted runs only
(`-o addopts='' -q`); ruff check and format clean on touched files.
