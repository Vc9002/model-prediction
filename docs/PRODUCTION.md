# Production Canary

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
- **Schema version:** `1`
- **Mode:** canary (single-model, manual-only)

## Runtime

The runtime root is resolved via the `MODEL_PREDICTION_RUNTIME_ROOT` environment
variable:

```
export MODEL_PREDICTION_RUNTIME_ROOT=/Users/vincentc9002/model-prediction-runtime
```

When this variable is not set, the system falls back to `<repo_root>/data`.

## Scheduler

The combined `com.modelprediction.daily` worker runs at 08:30 and 12:00 local time.
It settles open picks first and then produces the day's unified forecasts.

The separate `com.modelprediction.production` and
`com.modelprediction.rebuild-shadow` launchd jobs are disabled. Their commands
remain available for explicit manual use, but they do not run automatically.
This avoids duplicate forecast work and continuous research rebuilds on a
battery-powered workstation.

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
