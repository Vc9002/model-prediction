# Working-tree classification (consolidation K-prep)

Snapshot taken 2026-08-15 against `cleanup/final-debug-2026-08-14` @ `7258294`.
132 dirty entries (81 modified-tracked, 51 untracked). Classified per the
operator's A/B/C/D scheme. **No files were moved, deleted, or restored as
part of this pass — classification only.** Execution needs a scoped
follow-up plan per class, not a single `git restore`.

## Class A — unique operational evidence (must be preserved, never discarded)

None found dirty right now. The ledger XLSX files below are borderline —
they ARE the authoritative real-money/shadow record today (pre-cutover),
so they're evidence, not disposable cache. Filed under B because their
*tracking location* is what's wrong, not their content:

- `data/main/*.xlsx`, `data/flat/*.xlsx`, `data/research/*.xlsx`,
  `data/gated_research/*.xlsx`, `data/model_ledgers/*.xlsx` — the live
  ledger dual-write's XLSX side. Content is real evidence (also mirrored
  losslessly into `ledgers.db`, 22/22 parity clean per DEBUG.md); the
  *location* (tracked in the git checkout) is the item J/K target to fix.

## Class B — mutable runtime state (belongs under RuntimePaths, not git)

- `config/models/{cs2,dota2,lol,valorant,kbo,npb,rainbow_six}*.json` (14
  files, ~12.5k line-changes) — the live daily pipeline retrains these
  Elo artifacts every scheduled cycle. They are config-*shaped* but
  behave exactly like a rolling cache: full-file rewrite each run, no
  human review between commits, `.previous.json` siblings are the
  rollback copy the training code itself manages. **This is the
  strongest K candidate**: move artifact writes to
  `runtime_root/models/` (or keep the *promoted/frozen* artifact
  checked in and split "live-training scratch copy" into a separate,
  gitignored path) — do not keep re-committing full model weights on
  every 3h cycle.
- `data/availability/{mlb,wnba}/{raw,snapshots}/2026-08-13,08-14/*` (33
  files) — MLB/WNBA player-availability roster/transaction snapshots,
  timestamped filenames, obviously a raw-capture cache matching the
  project's own "raw → normalized → PIT" pattern (`CLAUDE.md`'s shadow-
  feature pattern doc). Belongs at `runtime_root/raw/availability/...`.
- `data/player_priors/wnba/*` (2 files) — derived priors, same class.
- `data/production/*` (2 files) — should already be superseded by
  `production/production.db` under the runtime root (Phase B); these
  are very likely stale leftovers of the pre-B write path. Verify empty/
  stale before deleting (see Class C note).
- `dashboard/jobs.json`, `dashboard/server.log`,
  `dashboard/portfolio_history.json`\*, `dashboard/orders.json`\* —
  dashboard runtime scratch. `.gitignore` already lists
  `dashboard/orders.json` / `dashboard/portfolio_history.json`
  (confirm — they show as modified-tracked here, meaning they were
  committed BEFORE the gitignore rule existed and now need `git rm
  --cached`, not just an ignore rule).
  \* Portfolio investigation is a separate concurrent workstream
  (task #22) — do not touch these two files until that lands.

## Class C — generated cache/log (safe to regenerate, delete once verified stale)

- `outputs/latest/esports-baseline-validation.json`,
  `outputs/latest/international-baseball-baseline-validation.json` —
  named "latest", explicitly a generated snapshot the validation CLI
  overwrites every run. Confirm no `docs/` or CI step reads these as
  frozen evidence (grep clean at classification time) before excluding
  from git.
- `data/esports/{cs2,valorant,rainbow_six,dota2,lol}/*` (match history
  appends) and `data/international_baseball/{kbo,npb}/*` — append-only
  ingest output, same "raw feeds the model, not a document a human
  edits" shape as availability snapshots above.
- `data/historical/*_games_all.jsonl` (4 files) — the MLB ingest-
  provenance backfill from the 2026-08-14 session (raw_source/raw_hash/
  parser_version added to every row) plus the routine daily ingest
  appends. Legitimate content, wrong tracking location long-term.

## Class D — source/config change (should be reviewed and committed normally)

None found in this dirty set beyond what's already staged in prior
consolidation commits. Everything remaining after A/B/C classification
above is runtime output, not a deliberate code/config edit.

## Recommended execution order (NOT executed in this pass)

1. Fix `.gitignore` cache/log entries that were committed before the
   ignore rule existed (`git rm --cached` — keeps the file on disk,
   only untracks it) for the Class C paths and the two dashboard
   portfolio files (after task #22 lands).
2. Design the model-artifact write path split (Class B, config/models/*)
   as its own small task — this is the biggest single class by diff
   size and needs a real decision: promoted artifacts stay in git,
   live-training scratch copies move to `runtime_root/models/`.
3. Move the remaining Class B raw/derived caches
   (`data/availability/`, `data/player_priors/`, `data/production/`)
   under `RuntimePaths` the same way B (production.db) and the ledger
   mirror already did — same `migrate_legacy_state`-style one-time
   carry-over pattern, not a silent rewrite.
4. Class A (ledger XLSX) waits on the J canonical-cutover decision —
   once SQLite is canonical and XLSX becomes export-only, its location
   question resolves itself (exports live under the runtime root too).
5. Only after 1-4: `git status --porcelain` empty after one full
   scheduled cycle becomes a testable acceptance gate (burn-in item 13
   from the operator's plan).

This document intentionally stops at classification — no `git
restore`/`rm`/`checkout` was run against any of the files above.

---

# Execution status — 2026-08-15 (consolidation K)

Executed per the recommended order above:

1. **gitignore/untrack pass**: all Class B and C paths are now untracked
   (`git rm --cached`, files remain on disk): `data/availability/`,
   `data/player_priors/`, `data/production/`, `data/esports/`,
   `data/international_baseball/`, `data/historical/`, `data/odds/`,
   `data/point_in_time/`, `data/main/`, `data/flat/`, `data/research/`,
   `data/gated_research/`, `data/model_ledgers/`, `outputs/latest/`,
   `data/logs/`, `data/features/`, `data/dashboard_cache.db`,
   `data/market_odds_snapshots.jsonl`, `data/audit_log.jsonl`,
   `data/espn_probables_cache.jsonl`, and dashboard scratch
   (`jobs.json`, `archive.json`, `server.log`, `server.pid`,
   `launchd.*.log`). Still tracked: `data/archive/` (audit archives),
   `snapshots/` (migration evidence), `data/entities/teams.json`,
   `data/experiments/`.
2. **Model-artifact write-path split**: `RuntimePaths.models_root`
   (runtime root `models/`) now holds the rolling esports/KBO/NPB
   ratings artifacts; the daily refresh (`_refresh_esports_ratings`,
   `_refresh_international_baseball_ratings`) retrains into the rolling
   dir, and research forecasts read rolling-first with a frozen
   `config/models/` fallback (`cli._research_models_dir()`). The 14
   checked-in `config/models/*.json` copies are now frozen promoted
   artifacts and never rewritten by the scheduled cycle. Rolling state
   seeded into `/Users/vincentc9002/model-prediction-runtime/models/`
   at cutover.
3. **Class A (ledger XLSX)**: untracked per the J canonical-cutover
   decision — SQLite (`ledgers.db`, runtime root) is the authority; XLSX
   is export-only and lives outside git now.
4. **Daily worker lock relocated**: `scripts/run_daily.sh` now takes
   its lock at `${MODEL_PREDICTION_RUNTIME_ROOT:-data}/locks/daily.lock`
   instead of repo `data/locks/`.
5. Clean-tree acceptance: a full supervisor production + daily cycle
   leaving `git status --porcelain` empty is the K gate (burn-in item).
