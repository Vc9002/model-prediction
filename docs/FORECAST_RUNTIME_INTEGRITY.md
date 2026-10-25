# Forecast runtime and existing picks

Interactive forecasts and scheduled workers must read the same market archives.
Set `MODEL_PREDICTION_RUNTIME_ROOT` in the process environment or the checkout's
private `.env`. The process environment takes precedence. The resolver reads
only that key from `.env`; it does not import credentials or change the process
environment. This local setting is machine configuration, not a model artifact.

Quote discovery redirects the checkout's legacy `data/` market path to that
runtime root. Historical feature inputs retain their existing location.
Explicit isolated data roots remain isolated, and stale checkout quotes are
not merged into the canonical archive. Real quote paths remain in provenance.

NBA v5 enables dynamic margin volatility. Scalar replay reconstructs that
transform from the artifact and exact away/home trend consistency inputs.
Missing inputs, tampered volatility, invalid artifacts, and stale engine hashes
remain failures. This repair does not alter the protected basketball engine,
model artifacts, thresholds, or forecast probabilities.

Forecast responses include `existing_started_picks`, preserving original
pregame selections, lines, probabilities, and timestamps. Started games do not
get new forecasts. When saved started picks exist, `--force` is disabled for
that invocation so it cannot regenerate them. The existing refresh guard also
prevents superseding started picks. Canonical SQLite reads use read-only mode;
an explicitly supplied isolated ledger is respected.

`stored_slate_coverage` distinguishes the complete Flat slate from the qualified
Main subset. For October 25, 2026, NFL Flat has 36 picks over 12 games: 12
moneylines, 12 spreads, 12 totals. Main has 13 qualifying picks. These counts
must not be conflated. Duplicate event/market slots are reported explicitly.

The same reporting covers every configured sport. Ordinary learned-sport
logging writes a complete Flat companion independently of Main qualification.
A duplicate Flat row does not suppress attempts to append an eligible missing
Main counterpart. Daily coverage now fails explicitly if priced predictions
are missing from Flat or if a Main row is not a CALL. Missing/invalid event
times preserve existing picks rather than allowing them to be superseded.

The all-model audit also repaired hockey history timestamps and international
baseball v3 replay identities. Repaired forecasting does not authorize inventing
a pregame prediction after an event starts. Such historical capture gaps,
source timeouts, unavailable exact markets, and no-schedule days remain visible
with distinct reasons. They are not filled with reconstructed pretend picks.

Esports forecasts reuse the normalized slate's exact market identity and fetch
the live book once per contract. Their market client makes at most two attempts
per request, honoring provider `Retry-After`; the broader capture client keeps
its existing six-attempt policy. After a 429, the forecast stops asking for
additional books in that run and marks every unpriced contract as source rate
limited. The forecast status is `error`, including when some earlier contracts
were priced, so daily integrity and lane coverage cannot present partial source
coverage as complete. A later run can retry; no missing pick is fabricated.

Official KBO/NPB schedule requests now use a five-second connection timeout
and a 12-second read timeout. A source outage remains a reported refresh
warning and does not block forecasts from using the last verified artifact.

Verification: `pytest tests/test_forecast_runtime_regressions.py -o addopts='' -q`
plus non-logging NBA/NFL CLI forecasts and a before/after canonical ledger
comparison. Do not run `--log`, `flat-forecast`, or `--replace-today` to validate
the repair against live state.

Rollback: revert the repair commit and restore the previous local runtime-root
setting from its private backup. No model or ledger rollback is necessary.
