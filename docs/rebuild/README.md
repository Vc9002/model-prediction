# Clean-Slate Rebuild

The rebuild is an isolated research and shadow system. It lives under
`src/model_prediction/rebuild`, writes mutable runtime state below
`RuntimePaths.rebuild_root` (repo-local `data/rebuild` unless
`MODEL_PREDICTION_RUNTIME_ROOT` is set), stores challenger artifacts only
below `config/models/challengers`, and exposes three separate CLIs:
`rebuild-shadow` (the decision pipeline: collect through decide),
`rebuild-data` (per-sport backfill/audit), and `rebuild-model` (per-sport
train/compare). `rebuild-data --sport mlb --version v3` is real (MLB v3, a
separate research lane from the frozen `mlb_moneyline_v2` candidate -- see
`MLB_V3_DATA.md`); `rebuild-data --sport wnba` and `rebuild-data --sport
nfl` are also real (data ingestion only -- feature-engineering/model-
baseline work is a separate, not-yet-made decision for both). Every other
sport on `rebuild-data`, and every sport on `rebuild-model`, currently
reports `NOT_IMPLEMENTED` -- see `OPERATIONS.md`.

Its permanent safety state is:

```text
SHADOW_ONLY = true
EXECUTION_ENABLED = false
PRODUCTION_PROMOTION = false
```

It is not a replacement for the incumbent production system. Rebuild forecasts
are evidence, not orders or promotions. The incumbent CLI, model artifacts,
ledgers, dashboard order controls, and execution path remain separate.

Read the remaining documents in this directory before changing rebuild code:

- `ARCHITECTURE.md` defines isolation and data flow.
- `OPERATIONS.md` defines safe CLI and runtime operation.
- `VALIDATION.md` defines evidence, sealed-test, and acceptance rules.
- `MARKETS.md` defines exact contract and economic semantics.
- `MLB_V3_DATA.md` defines the MLB v3 data foundation's source, licensing,
  and point-in-time boundary.
