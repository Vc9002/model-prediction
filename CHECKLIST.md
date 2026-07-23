# Project Maintenance Checklist

Current status and blockers: `docs/PROJECT_STATUS.md`.

Run these checks regularly. Pinned to the repo root for discovery.

## Daily

- [ ] **Dashboard alive** — `http://127.0.0.1:8765` loads, matrix shows all 4 sports
- [ ] **Daily pipeline ran** — check `data/logs/daily_$(date +%Y-%m-%d).log` exists
- [ ] **Polymarket snapshots captured** — `ls data/odds/{mlb,nba,wnba}/$(date +%Y-%m-%d)/`
- [ ] **scheduled jobs verified** — inspect the actual loaded launchd labels; do not assume documented labels are current

## Weekly

- [ ] **Data backfill** — use `env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli bootstrap ...` after inspecting the requested date range
- [ ] **Artifact hash integrity** — every JSON artifact in `config/models/` passes SHA-256 verification (see DEBUG.md check 2)
- [ ] **Audit chain intact** — `data/events.jsonl` chain unbroken (see DEBUG.md check 1)
- [ ] **Game count growth** — MLB should gain ~90 games/week in season, NBA ~50, WNBA ~20
- [ ] **Polymarket snapshot count** — `data/odds/` directories growing daily (target: 60+ days for spread/total training)

## Monthly / Per-Season

- [ ] **Revalidate all models** — run the module CLI without `--write-artifacts`, then compare to the active release
- [ ] **Regenerate dashboard matrix** — `PYTHONPATH=src .venv/bin/python -c "..."` (then restore calibration gates)
- [ ] **Check spread/total snapshot accumulation** — when 60+ days available, train real spread/total models
- [ ] **Elo regression rates** — verify sport-specific: MLB 0%, NBA 35%, WNBA 40%, NFL 50% still optimal
- [ ] **Confidence gate sweep** — run threshold sweep to check if gates need tuning
- [ ] **Rest flip filter** — verify still profitable for WNBA/NFL, test NBA if enough data
- [ ] **New season prep** — when a league's new season starts, verify:
    - [ ] ESPN scoreboard data flowing
    - [ ] Polymarket markets active
    - [ ] Elo ratings carry correctly across offseason
    - [ ] Artifact qualified status still valid

## Debug (when things look wrong)

- [ ] **Pre-flight** — `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` (expect zero failures; do not hardcode a test count)
- [ ] **Ruff lint** — `.venv/bin/ruff check src/ tests/`
- [ ] **Full DEBUG.md 10-step audit** — see `DEBUG.md`
- [ ] **Module imports** — every current package module imports cleanly (DEBUG.md check 3)
- [ ] **Data integrity** — 0 no-score games, 0 duplicates (DEBUG.md check 4)
- [ ] **Config consistency** — `config/model.yaml` maps to existing artifact files
- [ ] **Dashboard server logs** — `data/logs/dashboard.err` for runtime errors
- [ ] **Polymarket API health** — `curl -s https://gateway.polymarket.us/health`

## Current Known Issues

- [ ] MLB ingest pipeline sometimes misses completed games (ESPN API returns data but Ingestor doesn't process)
- [ ] NBA/NFL spread/total: 0 snapshots (offseason — will resolve when seasons start)
- [ ] WNBA total baseline 78.3% suspiciously high — needs investigation with more data
- [x] ~~Audit chain has 9 verified breaks~~ — repaired 2026-07-23 (10,837 events, 0 breaks)
- [x] ~~MLB artifact qualification is inconsistent~~ — resolved: operator override documented, test pins current state (2026-07-23)
- [x] ~~NFL config test drift~~ — resolved: artifact qualified=true at 71.3%, test passes (2026-07-23)
- [x] Ruff clean (0 errors) — fixed 2026-07-23
- [x] ~~Installed `.venv/bin/model-prediction` entry point cannot import the package~~ — fixed 2026-07-23
- [x] ~~30-pick freeze gate active~~ — removed 2026-07-23, `parameter_freezes_allowed: true`
- [ ] Dashboard startup process uses `pkill -f` — replace with PID-file approach
