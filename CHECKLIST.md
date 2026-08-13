# Project Maintenance Checklist

**Last updated**: 2026-08-13

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

- [ ] **Pre-flight** — `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` (expect 0 failures; current pass: 1759, 3 skipped)
- [ ] **Ruff lint** — `.venv/bin/ruff check src/ tests/` (expect ~118 findings; 79 are EXE002 shebang on test files)
- [ ] **Full DEBUG.md audit** — see `DEBUG.md` for the full 12-step audit and reproduction commands
- [ ] **Module imports** — every current package module imports cleanly (DEBUG.md check 3)
- [ ] **Data integrity** — 0 no-score games, 0 duplicates (DEBUG.md check 4)
- [ ] **Config consistency** — `config/model.yaml` maps to existing artifact files (the old `market-residual-v1.json` gap was resolved 2026-08-03, F-50: real artifact trained, wired as diagnostic-only)
- [ ] **Dashboard server logs** — `data/logs/dashboard.err` and `dashboard/server.log` for runtime errors
- [ ] **Polymarket API health** — `curl -s https://gateway.polymarket.us/health`
- [ ] **CLI imports** — `PYTHONPATH=src:. .venv/bin/python -c "from model_prediction.cli import main"` should exit 0
- [ ] **ModelLedger imports** — `PYTHONPATH=src:. .venv/bin/python -c "from model_prediction.model_ledger import ModelLedger"` should exit 0

## Current Known Issues

- [x] MLB ingest pipeline sometimes misses completed games — root-caused and fixed 2026-08-14: `cache_is_stale` only caught zero-completed caches, so mid-slate partial snapshots (some final, some `STATUS_IN_PROGRESS`) were trusted forever. Now any unfinished event (`state in/pre`) on a past date triggers re-fetch; 7 affected dates backfilled from live ESPN (incl. 5 games from 2026-08-07), processed/historical parity restored. See DEBUG.md 2026-08-14 (later).
- [ ] NBA/NFL spread/total: 0 snapshots (offseason — will resolve when seasons start)
- [ ] WNBA total baseline 78.3% suspiciously high — needs investigation with more data
- [x] Dashboard startup process used `pkill -f` — replaced with a PID-file approach 2026-08-14 (`./dash` records `.dashboard.pid`, `./stop` signals that exact PID). `.codewhale/instructions.md` records this pattern-matched kill has triggered a macOS security lock on this machine before — never reintroduce it.
- [x] `config/model.yaml` referenced `market-residual-v1.json` which doesn't exist — resolved 2026-08-03 (F-50), real artifact now trained and wired as diagnostic-only
- [x] `mlb-spread-baseline-v1.json` reused for both spread and total research — resolved 2026-08-03 (P0-5): both keys now point at MLB's real, separately-fitted spread/total pipeline artifacts instead of the unrelated baseline file (see `config/model.yaml` around the P0-5 comment).
- [x] `nba-spread-baseline-v1.json` and `nfl-spread-baseline-v1.json` "mismatched canonical hashes" — confirmed never a real bug (verification script's own JSON convention), per `docs/PROJECT_STATUS.md` release verdict item 6
- [x] `/api/scan` dashboard route called a nonexistent function — route no longer exists (removed; P1-9)
- [ ] 7 orphaned source modules (301 lines, never imported, zero tests) — re-verified 2026-08-14 by import-graph scan: `data_sources/football_data.py` (79, config-referenced as `soccer_enrichment` — key has no consumers), `data_sources/sportsdataio.py` (24), `features/lineup_strength.py` (40), `features/starting_pitcher.py` (58), `features/tennis_surface.py` (72 — `config/tested_features.json` already documents this feature function as dead; the live surface signal is `models/tennis.py`), `models/nfl.py` (8), `models/wnba.py` (20). Deletion needs operator confirmation (blocks on the same decision as the orphaned-worktree cleanup).
- [x] `cli.py` (3,943 lines) has zero dedicated test file — `tests/test_cli.py` exists with substantial coverage (grading, ledger routing, WNBA spread promotion tests added 2026-08-14); still not comprehensive line-for-line, but no longer zero.
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
