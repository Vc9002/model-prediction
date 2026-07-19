# model-prediction internal entry point

Read `docs/PROJECT_STATUS.md` first. It contains the verified current health,
model table, source-of-truth hierarchy, and repair order. Historical numbers in
this folder are retained as evidence, not current qualification claims.

## Verify the current checkout

```sh
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli validate-models \
  --output outputs/latest/learned-model-validation.json
```

Do not add `--write-artifacts` until the tests are green, the checkout is stable,
and the output is intended to become a new versioned release. Run `DEBUG.md` and
preserve every failure in the status documentation.

## Safe inspection loop

```sh
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli polymarket-slate --all --date YYYY-MM-DD
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli forecast --sport mlb --date YYYY-MM-DD
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary
```

`daily`, `--log`, settlement, bans, archive POSTs, and artifact-writing commands
mutate state. `execute`, `sell-position`, cancellation paths, and dashboard
order-submit routes are real-money surfaces and require a separate explicit
request and confirmation.

## Evidence locations

- Current status: `docs/PROJECT_STATUS.md`
- Active configuration: `config/model.yaml`
- Versioned artifacts: `config/models/`
- Current validation report: `outputs/latest/learned-model-validation.json`
- Point-in-time market snapshots: `data/odds/<sport>/<date>/`
- Ledger and audit state: `data/picks.xlsx` and `data/events.jsonl`
