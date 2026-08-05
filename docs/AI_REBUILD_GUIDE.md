# AI Rebuild Guide — model-prediction

**For AI agents (ChatGPT, Claude, etc.)** being instructed to rebuild or improve
this project. Read the [master README](../README.md) first, then use this guide
for step-by-step execution.

---

## Quick-Start: Get Running in 5 Minutes

```bash
cd "/Users/vincentc9002/model prediction"
source .venv/bin/activate
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
# Should show: 699 passed
```

If the venv is missing or broken:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## The Three Most Important Commands

```bash
# 1. Forecast today's games (shadow/paper only)
cd "model prediction"
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli daily \
  --date $(TZ=America/New_York date +%Y-%m-%d) --skip-settlement

# 2. Settle all open picks (grade against real results)
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli settle --all-unsettled

# 3. Verify data integrity
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain
```

---

## File-by-File: What to Change for Each Task

### Task: Fix a Model's Features

1. **Feature computation**: `src/model_prediction/features/<feature>.py`
2. **Feature registry**: `src/model_prediction/learned_forward.py` →
   `_compute_features()` and `_FEATURE_PROVIDERS`
3. **Training**: the artifact JSON in `config/models/<name>.json`
   contains fitted coefficients. Retrain via backtester.
4. **Validation**: `python -m model_prediction.cli backtest --sport <sport>`
   then `python -m model_prediction.cli validate --sport <sport>`
5. **Promotion**: update `config/model.yaml` → `models.<SPORT>.active_production_version`
6. **Tests**: `tests/test_learned_forward.py`, `tests/test_feature_regressions.py`

### Task: Add a New Sport

Files to create/modify:

1. `src/model_prediction/domain.py` — add to `League` enum, update sport tuples
2. `config/model.yaml` — add `models.<SPORT>` section
3. New `src/model_prediction/models/<sport>.py` — model implementation
4. `src/model_prediction/models/registry.py` — add `ModelSpec`
5. New forward module (e.g. `src/model_prediction/<sport>_forward.py`)
6. `src/model_prediction/cli.py` — add forecast/settle subcommands
7. `src/model_prediction/data_sources/polymarket_us.py` — add league slugs
8. `src/model_prediction/ingest.py` — add data source integration
9. `docs/LEDGER_ROUTING.md` — document routing
10. Tests: `tests/test_<sport>_forward.py`, `tests/test_sport_models.py`

### Task: Fix a Dashboard Bug

1. `dashboard_server.py` (~4,800 lines) — find the route/function
2. `dashboard.html` — if it's a UI issue
3. Test: `tests/test_dashboard_server.py`, `tests/test_dashboard_html.py`

### Task: Fix a Ledger Bug

1. `src/model_prediction/ledger.py` (1,485 lines) — `PickLedger` class
2. `src/model_prediction/model_ledger.py` (586 lines) — new `ModelLedger` (parallel, not primary)
3. `src/model_prediction/xlsx_ledger.py` — low-level read/write
4. `src/model_prediction/audit.py` (98 lines) — `AuditLog`
5. Tests: `tests/test_ledger.py`, `tests/test_ledger_hardening.py`,
   `tests/test_xlsx_ledger.py`, `tests/test_model_ledger.py`,
   `tests/test_audit.py`

### Task: Fix a CLI Bug

1. `src/model_prediction/cli.py` (4,411 lines) — monolithic, search for subcommand name
2. The subcommand function is typically named `_<subcommand_name>` or lives in
   the `build_parser()` → `parser.set_defaults(func=...)` chain
3. Test: `tests/test_cli.py`

### Task: Add a Test

1. Find the existing test file (or create `tests/test_<module>.py`)
2. Conftest: `tests/conftest.py` for shared fixtures
3. Run: `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_<module>.py -q`

---

## Common Pitfalls

### PYTHONPATH
Almost every command requires `PYTHONPATH=src:.` because the source lives in
`src/model_prediction/` rather than directly in a package directory. The venv
has an editable install (`pip install -e .`) but many commands still need
the explicit path.

### Timezone
- "Today" means US Eastern calendar day, not UTC or local time
- Defined in `domain.py`: `EASTERN = ZoneInfo("America/New_York")`
- Always use `eastern_today()` or pass dates explicitly

### Walk-Forward Only
- Never let features from after game time leak into a prediction
- `FeatureStore.games_before(sport, event_start)` enforces this
- Don't bypass it — use it as intended

### Locked Holdout
- Each model artifact has a locked holdout date range
- The holdout is 20% of data, chronologically last
- Never peek at holdout during feature development or threshold tuning
- Qualification is measured ONLY on holdout performance

### Protected Files
These files must never be overwritten:
- `config/models/*` — all existing artifact JSON files
- `data/historical/*_games_all.jsonl` — all historical data
- NBA and WNBA model code (modify via new versions alongside old)

### Market Isolation
Market prices NEVER enter independent outcome models:
- `models/soccer.py` — no odds inputs
- `models/mlb.py` Trend Engine — no odds inputs
- `models/tennis.py` — no odds inputs
- `learned_forward.py` — no odds inputs
- `models/market_residual.py` — THE ONLY exception, explicitly labeled

### Key-Value Config
`config/model.yaml` is 522 lines. Key things that trip people up:
- `models.MLB.status: shadow_qualified` — but the artifact itself says
  `qualified: false`. This is an intentional operator override, documented
  in the `qualification_override_reason` field.
- `bankroll.unit_value_usd: 5.00` — shadow/paper only, no real money
- `execution.status: live` — the execution MODULE is wired, but the release
  is blocked (see PROJECT_STATUS.md)

---

## Debugging a Failed Pipeline Run

```bash
# Check if lock is held
ls -la data/locks/

# Check today's log
cat data/logs/daily_$(TZ=America/New_York date +%Y-%m-%d).log

# Check if ESPN is responding
env PYTHONPATH=src:. .venv/bin/python -c "
from model_prediction.data_sources.espn import ESPNClient
c = ESPNClient()
print(c.scoreboard('mlb', '$(TZ=America/New_York date +%Y-%m-%d)').get('events', [])[:1])
"

# Check Polymarket gateway
curl -s https://gateway.polymarket.us/health

# Run one step at a time
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli settle --all-unsettled
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli ingest --sport mlb --date $(TZ=America/New_York date +%Y-%m-%d)
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli daily --date $(TZ=America/New_York date +%Y-%m-%d) --skip-settlement
```

---

## The Audit Chain

The audit chain (`data/events.jsonl`) is a SHA-256 linked list of JSON records.
Every mutation appends a new event with `previous_hash` pointing to the prior
event's hash.

```python
# Verify integrity
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain

# Read recent events
tail -20 data/events.jsonl | python3 -m json.tool
```

If `verify-chain` reports breaks:
1. Check `data/events.jsonl` isn't corrupted (valid JSON per line)
2. Check for manual edits (every hash must chain correctly)
3. Repair by replaying from the last known-good hash

---

## Model Qualification Process

To qualify a new model version:

1. **Train on training split** (oldest 60% of data)
2. **Select thresholds on validation split** (middle 20%)
3. **Lock holdout** (newest 20%) — never touch until final evaluation
4. **Run walk-forward backtest** on holdout only
5. **Check gates**:
   - At least 50 called picks on holdout
   - At least 60% called-pick hit rate
   - Brier score and calibration as secondary reports
   - No monthly degradation (complete calendar months with ≥10 calls)
6. **Save artifact** to `config/models/<new-name>.json` with SHA-256 hash
7. **Update config** `config/model.yaml` → `models.<SPORT>.active_production_version`
8. **Keep old artifact** — never delete rollback versions

---

## Dependency Map

```
cli.py
├── domain.py (types, enums)
├── config.py (paths, validation)
├── ledger.py → xlsx_ledger.py, audit.py, pricing.py, units.py, eligibility.py, model_ledger.py
├── learned_forward.py → features/*.py, models/learned_market.py, models/registry.py
├── forward.py → models/mlb.py, data_sources/espn.py, data_sources/mlb_market_odds.py
├── soccer_forward.py → models/soccer.py, features/base.py
├── tennis_forward.py → models/tennis.py
├── esports.py → features/elo_ratings.py, research_io.py
├── international_baseball.py → features/elo_ratings.py, research_io.py, validation.py
├── ingest.py → data_sources/espn.py, audit.py
├── backtester.py → features/base.py, features/elo_ratings.py, features/trends.py, calibration.py, lifecycle.py
├── main_ledgers.py → ledger.py
├── research_ledgers.py → ledger.py
├── entities.py, bans.py, calibration.py, validation.py, lifecycle.py, pricing.py, units.py
├── data_sources/polymarket_us.py, data_sources/polymarket_execute.py
├── data_sources/espn.py, data_sources/mlb_market_odds.py, data_sources/the_odds_api.py
└── ...

dashboard_server.py
├── cli.py (shells out to CLI commands)
├── dashboard.html (inline HTML template)
└── data_sources/polymarket_us.py (for scan route — broken)
```

---

## When Tests Fail

```bash
# Run all tests, verbose
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -v

# Run failing tests only
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ --lf -v

# Run with stdout
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -s

# Run a specific test function
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_cli.py::test_specific_func -v

# Check for import errors
env PYTHONPATH=src:. .venv/bin/python -c "from model_prediction.cli import main"
```

---

## Making a Safe Change

1. Read the relevant source file(s)
2. Read the corresponding test file(s)
3. Read the relevant docs section
4. Check `MASTER.md` for any prior bugs in this area
5. Make the change
6. Run the specific test file: `pytest tests/test_<module>.py -q`
7. Run the full suite: `pytest tests/ -q`
8. Run ruff: `.venv/bin/ruff check src/ tests/`
9. If changing model behavior: run a backtest to confirm no regression
10. Document in `MASTER.md` with date, evidence, and test results
