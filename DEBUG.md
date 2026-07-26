# DEBUG.md — Current Project Audit and Reproduction Guide

**Last audited**: 2026-07-26

**Project**: `/Users/vincentc9002/model prediction`

**Checkout audited**: `deepseek-phase5` at `697d765`, with a heavily dirty
working tree

## Safety boundary

This file records diagnoses and read-only reproduction commands. It does not
authorize code fixes, artifact regeneration, ledger mutation, order placement,
or real-money execution. Do not use `--log`, `--write-artifacts`, `daily`,
settlement, dashboard POST routes, or execution commands during an audit unless
the operator separately authorizes that state change.

The source tree was changing during this audit. Re-run the checks before acting
on any line number or count.

## 2026-07-27 remediation note

The historical findings below remain useful audit context, but the approved
forecast scope is now repaired in this working tree:

- MLB historical validation uses only pregame-observed starter archive rows;
  the first real prospective rows were captured 2026-07-26. MLB v6 remains
  unqualified.
- Missing/invalid MLB or WNBA executable quotes retain a zero-unit Today model
  opinion with `NO_CALL_MARKET_UNAVAILABLE`; the 5% valid-quote edge gate is
  unchanged.
- WNBA availability conflicts/errors default affected inputs neutral and are
  surfaced in Today rather than suppressing the model opinion.
- Soccer runs a draw-aware Poisson/Dixon-Coles full-game 2.5-total research
  path. KBO/NPB preview, research/gated routing, daily coverage, settlement
  output, and `$0.50` tie-contract P&L are wired.
- Flat is isolated from soccer/esports/KBO/NPB. The unified runner no longer
  lets a flat phase clear or overwrite research/gated ledgers.
- Research and Gated Research are split into independent per-sport workbooks
  for Soccer, LoL, CS2, Dota 2, Valorant, KBO, and NPB. The dashboard
  aggregates those files without merging their storage.
- A centralized Gated Research eligibility wrapper now requires exact model
  inputs, a timestamp-valid executable quote, and the configured per-sport
  edge/confidence floors. Valid low-edge rows remain zero-unit Research
  `NO_CALL` observations; unresolved or untrained inputs enter neither ledger.
- The legacy mixed ledgers were archived intact under
  `data/archive/research-ledger-split-20260726T192729Z/`. The cleaned live
  ledgers contain 32 Research rows and 22 Gated rows with zero invariant
  violations.
- The full suite passes 436 tests.

## Verified audit result — 2026-07-26

The checkout is **not release-ready**.

| Check | Verified result | Interpretation |
|---|---|---|
| Tests | **410 passed, 4 failed** in 7.93s | Four dashboard order-preview tests are stale against the current `$5.00` unit value/cost cap. |
| Critical focused tests | **84 passed** | `audit`, `cli`, `domain`, `forward`, and `xlsx_ledger` now have focused tests; the old “zero tests” claim was false. |
| Critical imports | Pass | All requested core modules and all feature/data-source modules imported. |
| Python/package | Python 3.14.5; editable install points to this project | Environment and installed console entry point are healthy. |
| Console entry point | Pass | `.venv/bin/model-prediction --help` exits 0. |
| Audit chain | **16,387 events, 0 link breaks, 0 hash mismatches** | Cryptographic chain is intact. |
| Ledger/audit reconciliation | **Not reconciled** | No current ledger row lacks a creation event, but 1,150 historical creation events lack a matching audited removal event. |
| Artifact integrity | **31 of 33 valid; 2 mismatches** | `nba-spread-baseline-v1.json` and `nfl-spread-baseline-v1.json` fail canonical SHA-256 verification. |
| Config artifact references | One missing; one semantically wrong | `market_residual.artifact` is missing; MLB total research still points to its spread artifact. |
| Ruff | **117 findings** | 79 are `EXE002`; the remaining 38 include six broad exception catches and correctness/style findings. |
| CLI summary | Pass | 4 open picks, 7.75 open units, shadow-accounting note present. |
| Dashboard | Health/status/matrix pass | `/api/summary` no longer exists. `/api/status` reports `promotion_allowed=true` while also warning MLB is below its qualification gate. |
| MLB dry forecast | Pass, no logging | 15 games, 9 confidence calls, `logged=0`; active v6 artifact reports `qualified=false`. |
| Targeted line execution | Mixed | Stdlib tracing measured 65–100% on the core pipeline modules, but only **8.3%** on `cli.py`. |

### Four failing tests

All failures are in `tests/test_dashboard_server.py`:

- `test_resting_order_preview_and_submit_persist_exchange_id`
- `test_submit_parses_success_after_interactive_prompt`
- `test_buy_at_current_ask_submits_marketable_ioc_limit`
- `test_manual_control_can_buy_at_ask_when_positive_edge_gate_is_disabled`

The current config sets `unit_value_usd: 5.00`. The test orders cost `$5.50`,
`$5.50`, `$6.00`, and `$6.40`, while their authorized caps are `$5.00`,
`$5.00`, `$5.00`, and `$6.25`. `preview_order()` therefore refuses them before
creating a nonce. This is test/config drift, not evidence that the cost cap
itself is broken.

## P0 findings

### 1. Real-money execution ticket is not bound to the ledger pick

`PolymarketExecutor.execute()` checks the row's record type and status but does
not prove that the ticket's pick ID, market slug, token side, action, price, or
quantity belongs to that row
(`src/model_prediction/data_sources/polymarket_execute.py:91-148`).
The dollar cap trusts caller-supplied `estimated_cost_usd` instead of
recomputing `price * size_shares`. Submission happens before the audit append
(`polymarket_execute.py:162-187`), so an audit failure can leave a submitted
order unrecorded.

**Operational rule:** do not use the real-money execution surface until the
ticket is cryptographically/structurally bound to the exact ledger row and all
economic fields are recomputed server-side.

### 2. MLB probable-starter validation is not point-in-time

On a historical cache miss, `espn_probables.py` fetches the current ESPN
scoreboard for the historical date and then caches the response indefinitely
(`src/model_prediction/data_sources/espn_probables.py:57-123`).
`validation.py:197-210` consumes this as a historical feature without a
historical `observed_at_utc`.

The active MLB v6 artifact is therefore a live research experiment, not a
promotable historical validation result. Its own artifact correctly says
`qualified=false`; the config override does not cure the provenance problem.

### 3. WNBA availability does not fail closed on source conflicts

`player_availability.py` documents fail-closed behavior but defaults to the
research-only `most_conservative` conflict policy
(`src/model_prediction/features/player_availability.py:151-164`).
The production path does not request `fail_closed` and suppresses common
parsing/conflict exceptions (`player_availability.py:275-301`).
`learned_forward.py:291-301` also logs a skipped availability adjustment at
DEBUG and continues.

### 4. Current eligibility policy bypasses declared risk gates

As of the 2026-07-26 operator directive,
`src/model_prediction/eligibility.py:28-91` accepts but does not use exposure or
market-disagreement inputs. After model-state, staleness, provenance, and ban
checks, `_call_result()` always returns a `QUALIFIED_SHADOW_CALL` and sizes
with `edge_scaled_units`; the exposure-aware `recommend_units()` decision is
not consulted.

The CLI still applies a pre-log executable-ask edge floor for some forecast
paths, but config exposure caps and the maximum disagreement value no longer
gate eligibility. Documentation and dashboard language must not claim that
those gates are enforced.

### 5. Ledger mutation and audit append are not one transaction

Ledger writes commit before the corresponding audit event is appended
(`ledger.py:500-507,635-648,743-770,795-796`). A crash or
`AuditLockTimeout` can therefore leave a created, settled, voided, or removed
row without its audit event. Some retry paths return early once the ledger
already reflects the mutation, so retry does not necessarily repair the audit
gap. Existing tests do not inject an audit failure between these two commits.

### 6. Artifact qualification and quote timestamp validity are informational

`learned_forward.py:304-330` labels a confidence-threshold call
`QUALIFIED_SHADOW_CALL` even when `artifact.qualified` is false. The CLI routes
`calls`, not `qualified_calls` (`cli.py:756-760`), before later config/state
gating. Separately, quote matching returns `timestamp_valid`, but the caller
does not enforce it (`learned_forward.py:431-439`, `cli.py:791-815`).
An invalid pregame snapshot can therefore price a call.

## P1 correctness and integrity findings

### International baseball

- `forecast --sport kbo|npb` without logging passes no research ledger, but
  `_forecast_international_sport()` dereferences it
  (`cli.py:1190-1208,1852-1860`). Read-only preview can crash.
- An early edge check skips KBO/NPB rows before the function can fulfill its
  contract of logging all research rows and only eligible rows to the gated
  ledger (`cli.py:1111-1148`).
- Ties are graded as ordinary moneyline pushes (`cli.py:1466-1493`,
  `pricing.py:33-56`). A contract settling at `$0.50` is not a refund unless
  its entry price was `$0.50`; tie P&L and calibration are economically wrong.
- Secondary-ledger settlement details are computed but hidden from `settle`
  and `daily` output (`cli.py:1882-1900,2116-2139`).

### Market and source semantics

- The Odds API key is placed in a query URL and exception text is returned to
  the caller, which can expose the key
  (`data_sources/the_odds_api.py:85-90`,
  `data_sources/odds_soccer_scores.py:64-69`).
- Polymarket snapshot aggregation hardcodes `timestamp_valid=true` even when
  an individual snapshot is invalid, and can report `status=ok` with missing
  executable asks (`data_sources/polymarket_us.py:404-448`).
- Event discovery requests at most 50 events with no pagination and turns
  per-league HTTP failures into empty slates
  (`data_sources/polymarket_us.py:112-153`).
- `guaranteed_signal.py:40-55` treats future timestamps as fresh because it
  checks only that age is below six hours, not that age is non-negative.
- Soccer head-to-head treats draws as away wins
  (`features/head_to_head.py:20-35`).
- MLB weather extraction passes the wrong payload shape, wind is not applied
  to the run factor, and live weather selects the first forecast hour instead
  of the event hour (`data_sources/espn.py:224-250`,
  `features/weather.py:40-75,115-160`).
- Feature ingestion marks an event ID as seen before validating the row, so a
  malformed first copy can suppress a later valid copy
  (`features/base.py:101-150`).
- The economic bootstrap gate fails only when the ROI confidence interval's
  upper bound is below zero; an interval spanning zero passes even though it
  does not exclude loss (`economic_gate.py:165-168`). The module also states
  that these gates are not wired into live eligibility.

### Concurrency and auditability

Ledger and audit file locks now use non-blocking `flock` with a 30-second
timeout; the old “locks hang forever” finding is fixed. The remaining problem
is transaction scope: exposure is calculated before the CLI's append lock, and
paired research/gated writes are separate transactions
(`cli.py:895-929,1085-1092,1204-1208`). Concurrent writers can approve from the
same stale exposure snapshot or leave paired ledgers inconsistent.

`verify-chain` reports the current chain intact but reconciliation false because
1,150 old creation events do not have audited removal events. Preserve that
historical gap; do not fabricate removal events.

### Artifact and release alignment

- All 33 JSON artifacts carry a hash field, but the NBA and NFL spread
  baselines mismatch their canonical content.
- `config/model.yaml` still points MLB total research to
  `mlb-spread-baseline-v1.json`, not `mlb-total-score-ridge-v1.json`.
- `models.market_residual.artifact` points to missing
  `config/models/market-residual-v1.json`.
- `outputs/latest/learned-model-validation.json` still names an old worktree,
  points MLB at v5, and predates active MLB v6 plus current KBO/NPB artifacts.
  It is not a reproduced release report for this checkout.
- `model-prediction models` still reports Soccer, esports, KBO, and NPB as
  research because it prints static registry specs instead of config-derived
  status (`models/registry.py:136-200`, `cli.py:1739-1740`).

## Current test map

The old zero-test inventory is obsolete. Focused files now exist for:

- `audit.py`: `tests/test_audit.py`
- `cli.py`: `tests/test_cli.py`
- `domain.py`: `tests/test_domain.py`
- `forward.py`: `tests/test_forward.py`
- `xlsx_ledger.py`: `tests/test_xlsx_ledger.py`
- core sport models: `tests/test_sport_models.py`
- execution gate: `tests/test_execution_gate.py`

The five critical focused files (`audit`, `cli`, `domain`, `forward`,
`xlsx_ledger`) pass 84 tests. Remaining high-risk gaps include the exact
execution-ticket binding invariant, KBO/NPB half-settlement economics,
transactional exposure-plus-append behavior, fail-closed WNBA conflict
handling, and secret redaction from provider errors.

Targeted stdlib line tracing during the full run measured:

| Module | Lines executed |
|---|---:|
| `domain.py` | 100% |
| `xlsx_ledger.py` | 96.2% |
| `audit.py` | 93.5% |
| `economic_gate.py` | 90.8% |
| `ledger.py` | 89.2% |
| `learned_forward.py` | 81.2% |
| `eligibility.py` | 77.9% |
| `forward.py` | 65.0% |
| `cli.py` | **8.3%** |

The highest-risk remaining low-execution modules are `cli.py`,
`mlb_statsapi.py`, `odds_soccer_scores.py`, `openligadb.py`, and
`wnba_availability_evaluation.py`. Line execution is not proof of behavioral
coverage; transaction failure, timestamp validity, conflict handling, and
secret-redaction invariants still lack direct tests.

## Reproduction commands

Run from the project root.

### Health

```bash
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q

env PYTHONPATH=src:. .venv/bin/python -c "
import model_prediction.cli, model_prediction.validation
import model_prediction.learned_forward, model_prediction.eligibility
import model_prediction.ledger, model_prediction.forward
import model_prediction.audit, model_prediction.xlsx_ledger
print('All critical imports OK')
"

.venv/bin/python --version
.venv/bin/model-prediction --help >/dev/null
```

### Audit and ledger reconciliation

```bash
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain
```

### Canonical artifact hashes

```bash
.venv/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path

for path in sorted(Path("config/models").glob("*.json")):
    raw = json.loads(path.read_text())
    key = "artifact_hash" if "artifact_hash" in raw else "model_hash"
    canonical = {name: value for name, value in raw.items() if name != key}
    computed = hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    print(path.name, "OK" if computed == raw.get(key) else "MISMATCH")
PY
```

### Config artifact resolution

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import yaml

config = yaml.safe_load(Path("config/model.yaml").read_text())
keys = (
    "production_artifact",
    "research_artifact",
    "spread_research_artifact",
    "total_research_artifact",
    "artifact",
)
for model, item in config.get("models", {}).items():
    if not isinstance(item, dict):
        continue
    for key in keys:
        value = item.get(key)
        if value and not Path(value).exists():
            print(f"MISSING: {model}.{key} -> {value}")
PY
```

### Runtime, without writes

```bash
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary

curl -s http://127.0.0.1:8765/api/health
curl -s http://127.0.0.1:8765/api/status | python3 -m json.tool
curl -s http://127.0.0.1:8765/api/matrix | python3 -m json.tool

env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli forecast \
  --sport mlb --date YYYY-MM-DD --model learned
```

Do not add `--log` to the forecast audit.

### Ruff

```bash
.venv/bin/ruff check src/ tests/
```

## Repair order

1. Bind and recompute every execution ticket field against the exact qualified
   ledger row before any real-money order can be submitted.
2. Make ledger mutation plus audit append recoverable as one transaction, and
   add failure-injection tests before relying on reconciliation.
3. Remove probable-starter data from historical validation unless each record
   has genuine pregame `observed_at_utc` provenance; keep MLB v6 unqualified.
4. Enforce artifact qualification and `timestamp_valid` before a candidate can
   be classified, priced, or logged.
5. Make WNBA availability conflicts fail closed and test malformed/conflicting
   source combinations.
6. Restore green tests by making dashboard tests explicit about unit value and
   intended order cost.
7. Repair the two mismatched spread artifacts, the missing residual reference,
   and the MLB total artifact reference without overwriting rollback artifacts.
8. Make KBO/NPB preview read-only, correct half-settlement P&L, and expose all
   secondary-ledger settlement results.
9. Make exposure-check-plus-append transactional across processes and preserve
   consistency between paired ledgers.
10. Redact provider secrets, enforce non-negative timestamp age, fix soccer draw
   and weather semantics, and make slate truncation/errors explicit.
11. Correct the economic confidence-interval gate so a zero-crossing interval
   does not pass as evidence of positive ROI.
12. Reproduce a new versioned report from one stable green checkout. Keep model
   quality separate from executable-price profitability.
