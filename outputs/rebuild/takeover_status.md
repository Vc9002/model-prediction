# Rebuild Takeover Status

Maintained per `CLAUDE.md`. Updated at the end of each checkpoint with real,
executed evidence — not descriptions of intended work.

## Checkpoint 0 — Preflight (2026-08-06)

**Branch**: `rebuild/clean-slate-v1`
**SHA**: `d5dcc684fc14b03d3e1a39fb9f18a7407845f7d6`
**Python**: 3.14.5 (`.venv/bin/python`, matches system `python3`)

**Working tree**: dirty, but not from this takeover. `git status --short` shows
~80 modified/untracked paths, all pre-existing live-pipeline drift: esports
manifests/match logs (`data/esports/*`), model-ledger `.xlsx` files, and
challenger artifact `.json`/`.previous.json` pairs under `config/models/`.
This is expected — `dashboard_server.py` (PID 46692, launchd-managed as
`com.vc.model-dashboard`) and the `com.modelprediction.daily` launchd job are
both actively running against this same checkout. Per `docs/PROJECT_STATUS.md`
this drift is normal and by design (ledgers/odds/availability snapshots are
tracked in-repo). **Not touched or reverted by this takeover.** Rebuild
commits will stage only `data/rebuild/`, `outputs/rebuild/`,
`config/models/challengers/`, `src/model_prediction/rebuild/`,
`tests/rebuild/`, and `CLAUDE.md` — never a blanket `git add -A`.

**Commands run** (all read-only, no production writers invoked):

```bash
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/model_prediction
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli verify-chain
```

**Test results**: 755 passed, 1 skipped, 0 failed (16.52s).

**Ruff**: 217 findings in `src/ tests/` (baseline, pre-existing, not from this
takeover — not chased per `CLAUDE.md`'s "don't chase pre-existing findings"
guidance; 55 auto-fixable, mostly `EXE002` shebang-on-test-file style issues).

**mypy**: 112 errors in 17 files, concentrated in `src/model_prediction/cli.py`
(the legacy/incumbent CLI, not `rebuild/`). Pre-existing, not from this
takeover.

**Audit chain** (`verify-chain`): `chain_intact: true`, `break_count: 0`,
62058 audit lines, 89 ledger rows. `reconciled: false` with 2774
`created_but_absent_without_removal_event` rows — per the command's own note,
these are historical deletions predating the audited `remove_open_rows` path,
not a new integrity break.

**Incumbent artifact hashes**: recorded in
`outputs/rebuild/incumbent_artifact_hashes.txt` (47 artifacts, SHA-256,
excludes `.previous.json` backup files). Frozen as of this checkpoint — none
overwritten by rebuild work; new rebuild artifacts go to
`config/models/challengers/`.

**Production writers**: none invoked. All commands above are read-only per
`docs/PROJECT_STATUS.md`'s "Safe command forms" list.

**Pre-existing rebuild-branch inventory** (found before this takeover began,
verified against real code and data, not doc claims — see prior session
audit): `src/model_prediction/rebuild/` already has a working medallion
storage layer (`storage.py` — immutable raw/normalized/features/markets,
provenance columns), `MLBCollector` with real ESPN/pybaseball/weather/
Polymarket collection methods, an `MLBTwoHeadModel` + chronological CV
training script (`scripts/pipeline_mlb_e2e.py`, real run: 190 games, 3 folds,
final Brier 0.244, ECE 0.012, hashed artifact in
`outputs/rebuild/mlb_training_results.json`), and a generic `edge_scaled_units`
Kelly-sizing function in `rebuild/economic.py`. Real gaps found: `pybaseball`
raw data collected for 10 days but never normalized; weather and Polymarket
collectors have real code but have **never actually been run** (zero raw
files for either source); `data/rebuild/features/` and
`data/rebuild/markets/` are both empty; the MLB training script uses rolling
home/away score averages as a placeholder feature set, not real
pitcher/lineup/bullpen/park features; no winner-first decision engine exists
anywhere in `rebuild/`; no single end-to-end shadow command exists.

**Current checkpoint**: 0 (complete).

**Known blockers**:
- Polymarket credentials (`POLYMARKET_KEY_ID`/`SECRET_KEY`/`PRIVATE_KEY`/
  `WALLET_ADDRESS`) presence/validity in `.env` not yet verified — needed
  before `collect_polymarket_books()` can be run for real.
- No CI currently attached to `rebuild/clean-slate-v1` specifically.

**Next executable command**: begin Checkpoint 1 (Environment) —
`pyproject.toml`/lockfile/CI Python-version consistency check, then Checkpoint
4/5 (MLB data + features) is the highest-value next step toward tonight's
goal, since storage (Checkpoint 2) is already largely built.

## Checkpoint 1 — Environment (2026-08-06)

**Found real inconsistencies, not just theoretical ones:**

1. `.github/workflows/ci.yml` pinned Python 3.12, while `pyproject.toml`
   (`requires-python = ">=3.14,<3.15"`), `[tool.mypy] python_version`, and
   `[tool.ruff] target-version = "py314"` all declare 3.14. CI was never
   actually testing the runtime the project claims to require. Fixed: CI now
   uses 3.14, installs via `pip install -e ".[dev]"` (previously only
   `pip install -e . pytest ruff`, silently skipping `mypy` and the
   `scipy-stubs`/`pandas-stubs`/etc. dev-only deps), and added a `mypy` step
   (`continue-on-error: true` — 112 pre-existing errors as of Checkpoint 0,
   not gating CI on paying that down, but now visible in every run instead of
   invisible).
2. `README.md` line ~740 said "Python 3.11+" while line 6 already claimed
   "Python 3.14" for the same repo. Fixed to match the pinned 3.14
   requirement.
3. **Real, currently-broken dependency, not a hypothetical fresh-clone
   issue**: built a genuinely fresh venv (`python3 -m venv`, outside any
   cached dev environment) and ran `pip install -e ".[dev]"` against the
   committed `pyproject.toml`. `import xgboost` failed with a `dlopen` error
   — `libomp.dylib` (OpenMP runtime) was never installed via Homebrew on this
   machine. Confirmed the **existing primary dev `.venv` has the identical
   failure** (`.venv/bin/python -c "import xgboost"` also raised the same
   `dlopen` error before the fix) — this wasn't masked by dev-machine state,
   it was silently broken there too, since nothing in the current test suite
   happens to import xgboost yet. Fixed by `brew install libomp`; verified
   `import xgboost` succeeds afterward (`xgboost 3.4.0`) in both venvs.
   Documented as a required macOS prerequisite in README (pip alone cannot
   install this — it's a system library XGBoost's wheel dynamically loads at
   runtime).
4. `requirements.lock` (75 packages) checked against every `pyproject.toml`
   dependency (`duckdb`, `pandera`, `polars`, `pyarrow`, `pybaseball`,
   `statsmodels`, `xgboost`, etc.) — all present and pinned. `pip check` in
   the primary venv reports no broken requirements.

**Fresh-install proof**: fresh venv install of `pip install -e ".[dev]"`
completed, `import model_prediction.rebuild` succeeded, all declared
dependencies imported (after the `libomp` fix), and
`PYTHONPATH=src:. python -m pytest tests/ -q` on that fresh venv: **755
passed, 1 skipped** — identical to the primary dev venv. Temp venv discarded
after verification (not committed; it was scratch, not repo state).

**Not done in this checkpoint**: did not attempt to verify CI actually passes
on GitHub's `ubuntu-latest` runner (no `gh` CLI auth available in this
session) — Linux's OpenMP situation differs from macOS's `libomp.dylib`
quirk and is unverified; flagging as an open item rather than assuming it's
fine.

**Files changed**: `.github/workflows/ci.yml`, `README.md`. No source code
changed — Checkpoint 1 is environment/tooling only.

**Current checkpoint**: 1 (complete).

**Next executable command**: Checkpoint 4/5 (MLB data + features) — normalize
the already-collected `pybaseball` raw data, actually run the weather and
Polymarket collectors (Polymarket credential validity in `.env` still
unverified), and replace `scripts/pipeline_mlb_e2e.py`'s placeholder rolling-
score features with real starter/lineup/bullpen/park features per
`CLAUDE.md`'s MLB feature-store spec.
