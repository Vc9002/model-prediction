# Project Maintenance Checklist

Run these checks regularly. Pinned to the repo root for discovery.

## Daily

- [ ] **Dashboard alive** — `http://127.0.0.1:8765` loads, matrix shows all 4 sports
- [ ] **Daily pipeline ran** — check `data/logs/daily_$(date +%Y-%m-%d).log` exists
- [ ] **Polymarket snapshots captured** — `ls data/odds/{mlb,nba,wnba}/$(date +%Y-%m-%d)/`
- [ ] **launchd jobs running** — `launchctl list | grep modelprediction` shows both jobs

## Weekly

- [ ] **Data backfill** — `model-prediction bootstrap --all --from $(date -v-7d +%Y-%m-%d) --to $(date +%Y-%m-%d)` to catch missed days
- [ ] **Artifact hash integrity** — all 20 artifacts in `config/models/` pass SHA-256 verification (see DEBUG.md check 2)
- [ ] **Audit chain intact** — `data/events.jsonl` chain unbroken (see DEBUG.md check 1)
- [ ] **Game count growth** — MLB should gain ~90 games/week in season, NBA ~50, WNBA ~20
- [ ] **Polymarket snapshot count** — `data/odds/` directories growing daily (target: 60+ days for spread/total training)

## Monthly / Per-Season

- [ ] **Revalidate all models** — `model-prediction validate-models` and compare to baseline
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

- [ ] **Pre-flight** — `PYTHONPATH=src .venv/bin/python -m pytest tests/ -q` (expect 159 pass)
- [ ] **Ruff lint** — `.venv/bin/ruff check src/ tests/`
- [ ] **Full DEBUG.md 10-step audit** — see `DEBUG.md`
- [ ] **Module imports** — all 47 modules import cleanly (DEBUG.md check 3)
- [ ] **Data integrity** — 0 no-score games, 0 duplicates (DEBUG.md check 4)
- [ ] **Config consistency** — `config/model.yaml` maps to existing artifact files
- [ ] **Dashboard server logs** — `data/logs/dashboard.err` for runtime errors
- [ ] **Polymarket API health** — `curl -s https://gateway.polymarket.us/health`

## Current Known Issues

- [ ] MLB ingest pipeline sometimes misses completed games (ESPN API returns data but Ingestor doesn't process)
- [ ] NBA/NFL spread/total: 0 snapshots (offseason — will resolve when seasons start)
- [ ] WNBA total baseline 78.3% suspiciously high — needs investigation with more data
- [ ] Audit chain broken at event 4 — needs repair
- [ ] 6 pytest failures (dashboard server + validation — pre-existing from Codex refactor)
