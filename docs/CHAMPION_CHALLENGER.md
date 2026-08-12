# Champion/Challenger System — Production Model Gating

## What this is

Production incumbents stay live and immutable throughout research. Every
improvement becomes a CHALLENGER tested against the frozen PRODUCTION CHAMPION.
No production pointer changes during experimentation — candidate code,
features, and artifacts stay isolated until the candidate proves better.

## CLI

```bash
# Snapshot current production champions (writes data/production/frozen_champions.json)
.venv/bin/python -m model_prediction.cli freeze-production

# Paired comparison: challenger predictions against champion settled picks
.venv/bin/python -m model_prediction.cli compare-champion \
    --champion-predictions <file> --challenger-predictions <file> \
    --sport mlb --market moneyline
```

## Module: `src/model_prediction/champion_challenger.py`

- `ProductionRegistry` — immutable snapshot of production pointers; `freeze()`
  captures + hashes every artifact; `validate_no_tampering()` detects drift.
- `FrozenProductionStore` — persists/loads `data/production/frozen_champions.json`.
- `PairedComparison` — paired deltas (ΔLogLoss, ΔBrier, ΔECE, Δaccuracy) with
  date-cluster bootstrap CIs.
- `PromotionVerdict` — `promote` / `reject` / `needs_more_data`.
- `load_settled_predictions(sport, market)` — reads a model's real settled
  win/loss rows from `data/model_ledgers/<id>.xlsx`.
- `settled_champion_calibration(sport, market)` — Brier/log-loss/ECE/accuracy
  from real settled outcomes (the "settled calibration" the roadmap references).

## Promotion rule

Candidate must: beat or tie incumbent LogLoss AND Brier, not materially worsen
ECE, not reduce coverage excessively, show improvement across multiple date
blocks, and not rely on PIT-invalid data.

## Known limitation

The canonical `data/model_ledgers/` directory (what the dashboard and the
settled-picks loader read) is written by the **retired main ledger's** mirror
hook. Since the main ledger was retired (`main_ledger_enabled: false`), that
mirror no longer fires. The live per-tier mirrors at
`data/{flat,research,gated_research}/model_ledgers/` are current, but the
canonical directory froze on 2026-08-03. See `docs/SETTLEMENT_GAP.md`.
