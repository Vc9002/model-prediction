# Rebuild Operations

## Safe invocation

Install the repository and inspect the dedicated interface:

```bash
pip install -e ".[dev]"
rebuild-shadow --help
rebuild-shadow --sport mlb --date YYYY-MM-DD --horizon late
```

There is no `--execute`, `--live`, or `--real-order` mode. Supplying one must
fail explicitly. Never route rebuild work through `model-prediction`, and never
point its data, output, or challenger roots at incumbent paths.

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
