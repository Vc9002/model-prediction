# Daily pipeline write map

**Date**: 2026-08-11. Written while fixing the `com.modelprediction.daily` launchd job,
paused at `826c893` because a scheduled run would have silently recreated `data/main`
after its archival. See that commit's message for the original incident.

## What `scripts/run_daily.sh` actually does

1. **Settlement** (`model_prediction.cli settle --all-unsettled`) — grades every open pick
   that has started, across Main, Flat, Research, and Gated Research.
2. **Historical game ingestion** (`model_prediction.cli ingest`) — feeds completed games
   back into `data/historical/*_games_all.jsonl` for MLB/NBA/WNBA/NFL.
3. **Unified daily forecast** (`model_prediction.cli daily`) — MLB/WNBA qualified calls to
   Main+Flat, all learned US-sport candidates to Flat, soccer/esports/KBO/NPB to
   Research(+Gated).

## Every real disk write, and its retirement status

| Write target | Triggered by | Retirement-affected? |
|---|---|---|
| `data/main/{mlb,wnba,soccer,tennis}.xlsx` | `_forecast_learned_sport`/`_forecast_soccer_sport`/`_forecast_tennis_sport` calling `ledger.append_evaluated(...)` on the one Main `MultiSportPickLedger` constructed in `cli.py::main()` | **Yes — now silently no-ops.** `retired=not config["project"].get("main_ledger_enabled", True)`, and `main_ledger_enabled: false` is now set in `config/model.yaml`. |
| `data/main/model_ledgers/<model-id>.xlsx` | The same append/settle calls above, additively, via `model_ledger.py::record_from_pick_request`/`settle_from_pick_row` | **Yes — now silently no-ops.** This was a real gap the first version of this fix missed: `PickLedger._append_record`/`.settle` call these unconditionally at `self.path.parent / "model_ledgers"`, which for a retired Main ledger is `data/main/model_ledgers` — unguarded, this alone would have recreated `data/main/`. Caught by `test_a_retired_ledger_never_touches_disk`, fixed by gating both call sites on `self.retired`. |
| `data/flat/{mlb,wnba,soccer,tennis}.xlsx` + `data/flat/model_ledgers/*` | Every `flat_ledger = MultiSportPickLedger(data_root, flat=True)` construction (3 call sites in `cli.py`) | No — untouched, `flat=True` ledgers were never given `retired=True`. |
| `data/research/{sport}.xlsx`, `data/gated_research/{sport}.xlsx` | `research_ledger(...)` / esports, KBO, NPB, soccer/tennis-adjacent research paths | No — separate `research_ledgers.py` module, not `main_ledgers.py`. |
| `data/historical/*_games_all.jsonl` | Step 1b ingestion | No — separate from ledger writes entirely. |
| Availability/odds/probables snapshots (`data/availability/`, `data/odds/`, `data/point_in_time/`) | The `daily` command's capture helpers (`_capture_wnba`, `_build_priors`, `_capture_mlb_probables`, `_capture_mlb_availability`, `_capture_mlb_starter_snapshots`) | No — independent of the ledger layer entirely. |
| `data/events.jsonl` (shared audit log) | `AuditLog.append(...)`, called by every ledger (Main, Flat, Research, Gated, per-model) | No — this file lives at the data root, not under `data/main/`, and recording audit events is expected regardless of Main's retirement. |

## What was NOT changed

- Flat, Research, Gated Research, model-ledger writes for non-Main sports, settlement,
  ingestion, and every capture step continue exactly as before.
- `existing_main_ledgers()` and the singular `main_ledger()` factory in `main_ledgers.py`
  are unused by any current code path (verified via `rg`) — not part of this fix's scope,
  but worth knowing: if either is wired up later without passing `retired=`, it would
  bypass this guard. Flagged here rather than silently left as a trap.

## What was deliberately left for a separate, explicit step

Re-enabling the actual launchd job (`launchctl bootstrap`) is **not** done by this PR. Per
the master plan's own sequencing, that should happen only after at least one real,
attended invocation of `run_daily.sh` confirms `data/main` stays absent end-to-end — a
step that hits live external APIs (ESPN, Polymarket, etc.) and writes a real audit trail,
so it's left for an explicit follow-up rather than run unattended as part of merging this
code fix.
