# Rebuild Operations

## Safe invocation

Install the repository and inspect the dedicated interface:

```bash
pip install -e ".[dev]"
rebuild-shadow --help
rebuild-shadow --sport mlb --date YYYY-MM-DD --horizon late

rebuild-data --help
rebuild-data backfill --sport mlb
rebuild-data audit --sport mlb

rebuild-model --help
rebuild-model train --sport mlb
rebuild-model compare --sport mlb
```

There is no `--execute`, `--live`, or `--real-order` mode on any of the three
CLIs. Supplying one must fail explicitly. Never route rebuild work through
`model-prediction`, and never point its data, output, or challenger roots at
incumbent paths.

`rebuild-data` (backfill/audit, per sport) and `rebuild-model` (train/compare,
per sport) are the data-ingestion and model-lifecycle CLIs; `rebuild-shadow`
remains the decision-pipeline CLI (collect through decide). As of
`rebuild/research-cli-v1`, every sport on `rebuild-data`/`rebuild-model`
reports `{"status": "NOT_IMPLEMENTED", ...}` rather than doing real work --
this landed the shared harness (argument parsing, the forbidden-live-flags
guard, `RuntimePaths`-aware `data_root` resolution, the safety wiring) ahead
of any per-sport backend, matching how `sport_adapter.py` already carries
honest `NOT_IMPLEMENTED` stubs for sports without a real adapter. A real
`backfill`/`audit` implementation previously existed per sport on now-archived
branches (`origin/rebuild/<sport>-v1` / `-research`) and is slated for a
curated, individually-reviewed transplant on its own `rebuild/<sport>-v1-next`
branch -- see `data_foundation.py`'s and `model_lifecycle.py`'s module
docstrings for the registration seam each of those branches fills in.

## Runtime behavior

- Raw, normalized, feature, market, resume, log, and database state belongs
  below `data/rebuild` and is not committed.
- Temporary reports belong below `outputs/rebuild/runtime` and are not
  committed.
- A failed, stale, ambiguous, post-start, or incomplete input produces an
  error/`NO_BET`; it never falls back to an incumbent artifact or live order.
- Run stages are `collect`, `build_features`, `predict`, `match_markets`,
  `decide`, and `settle`. Resumed and partial runs must be labeled honestly.

Before and after a shadow smoke, checksum incumbent ledgers and non-challenger
model artifacts. Any difference is a release blocker. After a run, tracked
source must remain clean; ignored runtime files are expected.

The dashboard is monitoring-only. All rebuild endpoints are GET routes and no
rebuild order, execution, or promotion control may be added.
