# Production Canary

**Note (verified 2026-11-08 against `config/production.yaml` and `docs/PROMOTION_POLICY.md`):**
the "single canary, everything else research/shadow" framing below describes the
2026-08-13 era of this system. Per the 2026-10-16 direct-wiring policy
(`docs/PROMOTION_POLICY.md`), the "shadow-qualified" quarantine this doc describes
has been eliminated — `config/production.yaml` is now schema v3 with roughly 80+
registered models across all sports (`allowed_models` is a long list, not one
entry), and `prediction_service.primary` (still `wnba-elo-trend-lr-v4` / WNBA
moneyline) selects which model the canary *predict cycle* runs, not the only model
serving production. See `docs/CHAMPION_CHALLENGER.md` and `docs/PROMOTION_POLICY.md`
for the current champion/challenger and direct-wiring model. The mechanics below
(health check states, module reference, rollback procedure shape) still match the
code; the schema version, scheduler, and "only model" framing do not — corrected
inline below.

The production canary is the first real model whose predictions may eventually
drive live decisions. Every other model in the system remains research or shadow
only. The canary runs alongside the main rebuild shadow pipeline, exposing its
predictions through a separate, independently health-checked path.

## Model

- **Model ID:** `wnba-elo-trend-lr-v4`
- **Sport:** WNBA
- **Market:** moneyline
- **Artifact:** `config/models/wnba-elo-trend-lr-v4.json`

## Configuration

- **Config file:** `config/production.yaml`
- **Schema version:** `3` (was `1` when this doc was written; see note above — v3 adds
  `models:`/`champions:` and multiple `implementation` types, not just the single
  canary artifact)
- **Mode:** canary (manual-only for this one model; other models serve directly per
  `docs/PROMOTION_POLICY.md`)

## Runtime

The runtime root is resolved via the `MODEL_PREDICTION_RUNTIME_ROOT` environment
variable:

```
export MODEL_PREDICTION_RUNTIME_ROOT=/Users/vincentc9002/model-prediction-runtime
```

When this variable is not set, the system falls back to `<repo_root>/data`.

## Scheduler

Stale: this section describes the single-daily-run, launchd-only, macOS era.
The daily worker now runs 4x/day (00:30, 06:00, 12:00, 18:00) via the Windows
Daily scheduled task that invokes `run_supervisor run daily` (see
`docs/LEDGER_ROUTING.md`'s "NBA operational wiring" note and `docs/PROJECT_STATUS.md`).
It settles open picks first and then produces the day's unified forecasts.

Whether the separate `production` and `rebuild-shadow` jobs are currently enabled
is a volatile operational fact — check `docs/PROJECT_STATUS.md`, not this doc, for
current state.

## Execution Policy

- **automated_orders:** `false`
- **manual_orders_only:** `true`

No position is ever opened automatically. Every production pick requires an
explicit human decision. This is a hard constraint — the canary produces
predictions, but only a human can act on them.

## Health Check

The health check (`health_check` in `src/model_prediction/production_canary.py`)
runs every production prediction cycle. It returns one of three statuses:

| Status     | Meaning                                              |
| ---------- | ---------------------------------------------------- |
| `HEALTHY`  | All checks pass; predictions are safe to consume.    |
| `DEGRADED` | Data is stale but the model itself is intact.        |
| `DOWN`     | A critical failure — the canary must not be used.    |

### Checks performed

1. **Config validity** — `config/production.yaml` loads and passes validation
   (exactly one allowed model, artifact exists, artifact hash matches, model_id
   matches).
2. **Artifact integrity** — the artifact parses as valid JSON and its embedded
   `artifact_hash` matches a re-computed SHA-256 of the canonical form.
3. **Finite probabilities** — every probability field (hit rates, confidence
   thresholds, reliability bucket means) is a finite float (no NaN or ±Inf).
4. **Data freshness** — the youngest data file under the runtime root is no
   older than `max_data_age_minutes` (default: 120).

### Failure behavior

The canary is **fail-closed**. Any validation or health failure returns `DOWN`;
there is no silent fallback. When the status is not `HEALTHY`, the prediction
service returns `NO_PREDICTION` rather than a stale or invalid forecast.

## Rollback Procedure

The `wnba-elo-trend-lr-v4` artifact is immutable — never edit it in place. To
roll back or switch models:

1. Create a new artifact file under `config/models/` (e.g., `wnba-elo-trend-lr-v5.json`).
2. Update `config/production.yaml`:
   ```yaml
   prediction_service:
     primary:
       model_id: wnba-elo-trend-lr-v5
       artifact: config/models/wnba-elo-trend-lr-v5.json
     allowed_models:
       - wnba-elo-trend-lr-v5
   ```
3. Run the health check to confirm the new artifact passes validation.
4. Commit both files.

To roll back to v4, reverse the config changes — v4 itself never changes.

## Module Reference

- **`load_production_config()`** — loads `config/production.yaml`, returns dict.
- **`validate_production_config(config)`** — fail-closed validation.
- **`get_production_model(config)`** — returns the single allowed model dict.
- **`health_check(config, runtime_root)`** — returns `{status, model_id, artifact_hash, checked_at_utc}`.
