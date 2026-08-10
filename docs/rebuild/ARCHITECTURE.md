# Rebuild Architecture

## Boundary

```text
provider data
  -> data/rebuild/raw
  -> data/rebuild/normalized
  -> data/rebuild/features
  -> challenger artifact
  -> calibrated shadow forecast
  -> exact market match
  -> BET / NO_BET paper decision
  -> data/rebuild/shadow.db
  -> read-only rebuild dashboard APIs
```

The rebuild may not import incumbent order/execution adapters, load an active
incumbent artifact as its candidate, or write `data/main`, `data/flat`, any
production ledger, or any non-challenger model path. `config/rebuild.yaml` is
the only rebuild configuration root, and its execution hard-off gate
(`shadow_only`/`execution_enabled`/`production_promotion`/`allow_live_orders`/
`allow_production_ledger_write`) cannot be overridden by a CLI flag or
environment variable.

The *location* of mutable runtime state (`data_root`: raw cache, normalized
parquet, `shadow.db`) is a separate concern from that safety gate and does
follow `MODEL_PREDICTION_RUNTIME_ROOT`, via the same `RuntimePaths`
repo_root/runtime_root split the dashboard reader uses -- so a deployment
that points the dashboard at an external runtime root also gets `rebuild-*`
CLI writes routed there, rather than the two silently diverging.
`output_root` and `challenger_root` stay repo-relative always: they hold
versioned evidence meant to be committed, not disposable runtime state.

Runtime databases and per-run material are ignored. Canonical model cards,
benchmarks, source audits, and qualification evidence may be versioned only
when their provenance is explicit. Dashboard readers open runtime databases
read-only and return an unavailable/degraded envelope when an input is absent
or malformed; reading status must never create a database.

The rebuild and incumbent systems may share a repository and dashboard shell,
but not decision state, ledgers, artifacts, execution controls, or promotion
state.
