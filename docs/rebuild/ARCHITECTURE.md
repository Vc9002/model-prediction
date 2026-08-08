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
the only rebuild configuration root, and its execution hard-off gate cannot be
overridden by a CLI flag or environment variable.

Runtime databases and per-run material are ignored. Canonical model cards,
benchmarks, source audits, and qualification evidence may be versioned only
when their provenance is explicit. Dashboard readers open runtime databases
read-only and return an unavailable/degraded envelope when an input is absent
or malformed; reading status must never create a database.

The rebuild and incumbent systems may share a repository and dashboard shell,
but not decision state, ledgers, artifacts, execution controls, or promotion
state.
