# Project Maintenance Checklist

**Last updated**: 2026-08-02

Current status and blockers: `docs/PROJECT_STATUS.md`.

Run these checks regularly. Pinned to the repo root for discovery.

## Daily

- [ ] **Dashboard alive** — `http://127.0.0.1:8765` loads, matrix shows all sports
- [ ] **Daily pipeline ran** — check `data/logs/daily_$(date +%Y-%m-%d).log` exists
- [ ] **Polymarket snapshots captured** — `ls data/odds/{mlb,wnba,esports,kbo,npb,soccer,tennis}/$(date +%Y-%m-%d)/`
- [ ] **scheduled jobs verified** — inspect the actual loaded launchd labels; do not assume documented labels are current
- [ ] **Dashboard server token valid** — dashboard startup generates a fresh per-session token; if the browser shows auth errors on order submission, restart the dashboard

## Weekly

- [ ] **Data backfill** — use `env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli bootstrap ...` after inspecting the requested date range
- [ ] **Artifact hash integrity** — every JSON artifact in `config/models/` passes SHA-256 verification (see DEBUG.md check 2)
- [ ] **Audit chain intact** — `data/events.jsonl` chain unbroken (see DEBUG.md check 1)
- [ ] **Game count growth** — MLB should gain ~90 games/week in season, NBA ~50, WNBA ~20
- [ ] **Polymarket snapshot count** — `data/odds/` directories growing daily across all 8 sports (target: 60+ days for spread/total training)
- [ ] **Esports match history advancing** — check `data/esports/*/manifest.json` for recent match additions

## Monthly / Per-Season

- [ ] **Revalidate all models** — run the module CLI without `--write-artifacts`, then compare to the active release
- [ ] **Regenerate dashboard matrix** — then restore calibration gates
- [ ] **Check spread/total snapshot accumulation** — when 60+ days available, train real spread/total models
- [ ] **Elo regression rates** — verify sport-specific: MLB 0%, NBA 35%, WNBA 40%, NFL 50% still optimal
- [ ] **Confidence gate sweep** — run threshold sweep to check if gates need tuning (esports: K by min Brier, threshold by units_at_minus_110)
- [ ] **Rest flip filter** — verify still profitable for WNBA/NFL, test NBA if enough data
- [ ] **New season prep** — when a league's new season starts, verify:
    - [ ] ESPN scoreboard data flowing
    - [ ] Polymarket markets active
    - [ ] Elo ratings carry correctly across offseason
    - [ ] Artifact qualified status still valid
- [ ] **Model ledger migration progress** — check whether `ModelLedger` is ready to cut over from `PickLedger`

## Debug (when things look wrong)

- [ ] **Pre-flight** — `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` (expect 0 failures; current pass: 624)
- [ ] **Ruff lint** — `.venv/bin/ruff check src/ tests/` (expect ~118 findings; 79 are EXE002 shebang on test files)
- [ ] **Full DEBUG.md audit** — see `DEBUG.md` for the full 12-step audit and reproduction commands
- [ ] **Module imports** — every current package module imports cleanly (DEBUG.md check 3)
- [ ] **Data integrity** — 0 no-score games, 0 duplicates (DEBUG.md check 4)
- [ ] **Config consistency** — `config/model.yaml` maps to existing artifact files (known gap: `market-residual-v1.json` missing)
- [ ] **Dashboard server logs** — `data/logs/dashboard.err` and `dashboard/server.log` for runtime errors
- [ ] **Polymarket API health** — `curl -s https://gateway.polymarket.us/health`
- [ ] **CLI imports** — `PYTHONPATH=src:. .venv/bin/python -c "from model_prediction.cli import main"` should exit 0
- [ ] **ModelLedger imports** — `PYTHONPATH=src:. .venv/bin/python -c "from model_prediction.model_ledger import ModelLedger"` should exit 0

## Current Known Issues

- [ ] MLB ingest pipeline sometimes misses completed games (ESPN API returns data but Ingestor doesn't process)
- [ ] NBA/NFL spread/total: 0 snapshots (offseason — will resolve when seasons start)
- [ ] WNBA total baseline 78.3% suspiciously high — needs investigation with more data
- [ ] Dashboard startup process uses `pkill -f` — replace with PID-file approach (`.codewhale/instructions.md` explicitly forbids `pkill`)
- [ ] `config/model.yaml` references `market-residual-v1.json` which doesn't exist
- [ ] `mlb-spread-baseline-v1.json` is reused for both spread and total research (should be separate artifacts)
- [ ] `nba-spread-baseline-v1.json` and `nfl-spread-baseline-v1.json` have mismatched canonical hashes
- [ ] `/api/scan` dashboard route calls a nonexistent function — returns 500 on every request
- [ ] 12 orphaned source modules (~1,800 lines of dead code, never imported or tested)
- [ ] `cli.py` (3,943 lines) has zero dedicated test file
- [ ] `dashboard_server.py` (4,782 lines) is monolithic; recommended split into `dashboard/` package
- [x] ~~Audit chain has 9 verified breaks~~ — repaired (43,304 events, 0 breaks)
- [x] ~~MLB artifact qualification is inconsistent~~ — operator override documented
- [x] ~~NFL config test drift~~ — resolved
- [x] ~~Ruff clean (0 errors)~~ — baseline reset to 117; currently 118 (1 new EXE002)
- [x] ~~Installed `.venv/bin/model-prediction` entry point broken~~ — fixed
- [x] ~~30-pick freeze gate active~~ — removed
- [x] ~~Unit sizing dead parameter~~ — `model_uncertainty` now read by `edge_scaled_units` (2026-07-31)
- [x] ~~Dashboard no auth on order execution~~ — per-session token added (2026-08-02)
- [x] ~~NPB destructive overwrite~~ — fixed (2026-08-01)
- [x] ~~CLV scanning only Main~~ — now scans Flat/Research/Gated too
