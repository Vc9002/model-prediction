# Project Maintenance Checklist

**Last updated**: 2026-08-26

Current status and blockers: `docs/PROJECT_STATUS.md`.

Run these checks regularly. Pinned to the repo root for discovery.

## Daily

- [ ] **Burn-in checks** (through 08-18) — `docs/BURN_IN.md` table
- [ ] **Dashboard alive** — `http://127.0.0.1:8765` loads, matrix shows all sports
- [ ] **Daily pipeline ran** — check `data/logs/daily_$(date +%Y-%m-%d).log` exists
- [ ] **Polymarket snapshots captured** — `ls data/odds/{mlb,wnba,esports,kbo,npb,soccer,tennis}/$(date +%Y-%m-%d)/`
- [ ] **scheduled jobs verified** — inspect the actual loaded launchd labels; do not assume documented labels are current
- [ ] **Dashboard server token valid** — dashboard startup generates a fresh per-session token; if the browser shows auth errors on order submission, restart the dashboard
- [ ] **MLB ingest completeness** — `env PYTHONPATH=src:. .venv/bin/python scripts/check_mlb_ingest_completeness.py --days 7` (P1-12 detector)

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

- [ ] **Pre-flight** — `env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q` (expect 0 failures; current pass: 2,298, 3 skipped)
- [ ] **Stale-open rows** — `env PYTHONPATH=src:. .venv/bin/python -m model_prediction.system_health` reports `stale_open_rows` (open rows >24h/72h past start = postponed/rescheduled exposure); >72h degrades health
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
- [x] `archive/wnba-spread-baseline-v1.json` embedded artifact_hash mismatch — confirmed a deliberate documented non-issue: `_retired`/`_retired_date`/`_retirement_reason` were appended at archive time and archived rollback evidence is never re-signed (docs/FEATURE_MODEL_AUDIT.md "Archive integrity"). Hash re-verifies exactly under the canonical compact convention when the annotation fields are excluded.
- [x] `research/mlb-v9-candidate-1.json` embedded artifact_hash mismatch — confirmed a deliberate documented non-issue: quarantine fields (`status`/`invalidation_reason`/`replacement`) were appended by the 2026-08-23 quarantine commit to a VOID artifact "preserved for audit, never promoted" — same never-re-signed evidence rule. Re-verifies when the three quarantine fields are excluded.
- [x] `nfl-elo-trend-lr-v4-temperature.json` / `wnba-elo-trend-lr-v4-temperature.json` orphaned challenger configs — removed 2026-08-26 (zero references; inert per their own commit messages).
- [x] Ledger settlement backlog + 6 identity conflicts (cs2/tennis/esports) — resolved 2026-08-26; audit signature stake-normalized; see DEBUG.md 2026-08-26 (evening).
- [x] Installed launchd daily plist drifted from `ops/launchd/` — re-synced byte-identical and reloaded 2026-08-26.
- [x] `production` + `rebuild-shadow` launchd jobs disabled/unloaded (37h stall) — operator approved re-enable 2026-08-26; both loaded and completed exit 0.
- [ ] NBA/NFL spread/total: 0 snapshots and zero odds sources wired (offseason — will resolve when seasons start; TheRundown evaluated as the candidate second-book source)
- [ ] WNBA total baseline 78.3% suspiciously high — needs investigation with more data
- [ ] Soccer results capture: The Odds API 401 for ≥31 days; API-FOOTBALL client implemented + wired 2026-08-26 (`data_sources/api_football.py`, step1b, `cmd_collect_scores`, source policy) — awaiting operator API key (`API_FOOTBALL_KEY`) and a live verification pass (league IDs, AET/PEN goal shape, rate pacing)
- [x] v9 feature collinearity (platoon/projected r≈0.9997; bullpen freshness/hl r=1.0) — documented as construction-collinear in `audit_mlb_v9_feature_distribution.py` `KNOWN_COLLINEAR_PAIRS` (2026-08-26); revisit when a pitch-level or roster-role source lands
- [ ] Market-snapshot lineage absent for 7,263 esports/soccer/KBO/NPB ledger rows
- [x] NRFI model tracked market prices with no edge — root-caused (2x league run-rate constant + hand-set weights), new fitted first-inning model lands 2026-08-26; next step: capture real Polymarket NRFI quotes for true CLV measurement
- [x] Dashboard startup process used `pkill -f` — replaced with a PID-file approach 2026-08-14 (`./dash` records `.dashboard.pid`, `./stop` signals that exact PID). `.codewhale/instructions.md` records this pattern-matched kill has triggered a macOS security lock on this machine before — never reintroduce it.
- [x] `config/model.yaml` referenced `market-residual-v1.json` which doesn't exist — resolved 2026-08-03 (F-50), real artifact now trained and wired as diagnostic-only
- [x] `mlb-spread-baseline-v1.json` reused for both spread and total research — resolved 2026-08-03 (P0-5): both keys now point at MLB's real, separately-fitted spread/total pipeline artifacts instead of the unrelated baseline file (see `config/model.yaml` around the P0-5 comment).
- [x] `nba-spread-baseline-v1.json` and `nfl-spread-baseline-v1.json` "mismatched canonical hashes" — confirmed never a real bug (verification script's own JSON convention), per `docs/PROJECT_STATUS.md` release verdict item 6
- [x] `/api/scan` dashboard route called a nonexistent function — route no longer exists (removed; P1-9)
- [x] Orphaned source modules — corrected 2026-08-14 (operator GO): the earlier "7 orphans" claim was a scan artifact (the import-graph check missed `__init__` re-exports: `from . import (a, b)` for `@register_feature` side effects and same-package `from .x import` package exports). The precise verification: **4 true orphans deleted** (`data_sources/football_data.py`, `features/starting_pitcher.py`, `models/nfl.py`, `models/wnba.py` — zero imports anywhere, zero tests; the inert `soccer_enrichment` config key removed; the `starting_pitcher_fip` entry removed from `tested_features.json`, counts 25→24) and **3 false positives restored** (`sportsdataio.py` is the package's `SportsDataIOClient` export used by rebuild; `lineup_strength.py` + `tennis_surface.py` are load-bearing feature registrations).
- [x] `cli.py` (3,943 lines) has zero dedicated test file — `tests/test_cli.py` exists with substantial coverage (grading, ledger routing, WNBA spread promotion tests added 2026-08-14); still not comprehensive line-for-line, but no longer zero.
- [x] `dashboard_server.py` (previously 4,782 lines) split into `model_prediction.dashboard` package (thin 260-line re-export shim in place)
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
