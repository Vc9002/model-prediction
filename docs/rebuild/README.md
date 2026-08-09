# Clean-Slate Rebuild

The rebuild is an isolated research and shadow system. It lives under
`src/model_prediction/rebuild`, writes runtime state only below `data/rebuild`,
stores challenger artifacts only below `config/models/challengers`, and exposes
the separate `rebuild-shadow` CLI.

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
- `DATA_SOURCES.md` defines the free/open provider, provenance, and licensing policy.
- `OPERATIONS.md` defines safe CLI and runtime operation.
- `VALIDATION.md` defines evidence, sealed-test, and acceptance rules.
- `MARKETS.md` defines exact contract and economic semantics.
