# quick-261122-dnm: one model display name and one edge-bucket set

Operator (2026-11-22): "clean up how the model names work and the edge bucket, it's so confusing".
Chosen: display names only (ledger ids, promotions, buyer whitelist unchanged); one bucket set
<0%, 0-2%, 2-5%, 5-8%, 8-12%, 12%+ (the widget/audit bands, so no logged pick changes bucket).

## Changes
- `src/model_prediction/display_names.py` (new, stdlib only): `edge_bucket`, `edge_bucket_pct`,
  `EDGE_BUCKETS`, `model_display_name(model_id, market_type, payload)` -> "NCAAB Spread v7".
  Market comes from the pick, not the id; NCAAB rows are named by `features.margin_engine` when its
  number differs from the id's (v6-id rows priced by the v7 engine 11-16..11-21 -> v7; v7m stays v7m).
- `scripts/audit_best_picks.py`: `get_edge_bucket` returns the shared labels (loads the module by path
  when the package is not importable, as under the widget's `python -I`). Test labels updated.
- `market_eval._edge_bucket` (was 0-2/2-5/5-10/10+) and `nba_replay_harness.EDGE_BUCKETS`
  (was 0-2.5/2.5-5/5-10/10-15/15+) now use the shared bands.
- `tests/test_display_names.py`: bands, audit/market_eval parity, naming cases.
- Outside the repo: Rainmeter `BestPickAudit/@Resources/BestPicks.py` `model_label` uses
  `model_display_name` (backup `BestPicks.py.pre-names.bak`); tracker rows with id `ncaab-spread-v6`
  logged from 2026-11-16 12:15 ET are named v7. Widget NCAAB block now "NCAAB Spread v7 18-9 +9.69U"
  (was "spread v7 12-3" + "spread v6 6-6").
- `outputs/audit/bucket_profitability.py` (research, untracked) uses the shared names and bands.

## Verify
72 passed (test_display_names, test_audit_best_picks, test_market_eval, test_nba_replay_harness);
ruff src/ + tests clean; mypy clean on changed files; widget refresh exit 0.
