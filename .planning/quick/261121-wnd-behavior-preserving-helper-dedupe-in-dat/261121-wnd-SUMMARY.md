---
status: complete
plan: 261121-wnd
branch: simplify/helpers-2026-11-21
base: 8c0dea2f
commits: [10609304, 32fa2f9b, fbf5deb0, da3a4727, 2c805adc]
---

# 261121-wnd: behavior-preserving helper dedupe in data_sources. Summary

All work was done in `E:/MP-wt/simplify-2026-11-21`. Nothing was pushed or merged, and `E:/MP` was not touched.
`MODEL_PREDICTION_RUNTIME_ROOT` is set in this shell, so every pytest run and every commit was wrapped in
`env -u MODEL_PREDICTION_RUNTIME_ROOT`. A temporary probe confirmed that pytest imports the worktree's `src/`
(through `pythonpath = [".", "src"]`) and not the editable install at `E:\MP\src`. The probe was deleted afterwards.

## Task 1: dead function (commit 10609304)
- **What changed:** removed `ncaab_cbbd.game_date_utc`. `date` became unused, so it was dropped from the `datetime` import.
- **Evidence:** `grep -rn game_date_utc src tests scripts config ops docs` found only the definition, plus an
  unrelated local parameter name in `tests/rebuild/test_mlb_features.py`. No `.js`, `.html` or `.css` file mentions it.

## Task 2: identical pure helpers (commits 32fa2f9b, fbf5deb0, 2c805adc)

**Correction (commit 2c805adc):** `cfb_pbp_live.py` was restored to its 8c0dea2f blob.
`cfb_structural_v7_challenger._V7_SERVING_SOURCES` hashes that file's bytes into every v7 row's `code_revision`.
Moving `_iso` out of it changed `v7_serving_code_hash()` from `efb5bad53e8a727d` to `f9f3187ccb010881`. After the
restore the hash is `efb5bad53e8a727d` again, and `git hash-object` equals the base blob. In the table below,
cfb_pbp_live's `_iso` is therefore not consolidated; only ncaab_cbbd uses `iso_utc_z`.

Hasher check: I grepped src, config, tests and scripts for each touched data_sources file as a quoted
`"<name>.py"` or path, and ran a `read_bytes()`/`getsource` grep outside `rebuild/`. cfb_pbp_live was the only
touched file found in any hashed source list. The lists checked were v51, v62, v7_challenger,
college_football `_SERVING_SOURCE_FILES`, scalar_artifact_replay, coded_market_replay, mlb_v10_replay,
mlb_margin_replay and international_replay, plus config `source_sha256` maps.
Shared copies went into `provider_capture.py`, which imports only stdlib, so there is no circular import. Each module
imports them back under its old private name, so `module._record_hash`, `module._iso`, `module._default_http_get`
and `module._opt_float` still resolve, and tests can still monkeypatch them.

| Family | Consolidated | Skipped and why |
|---|---|---|
| `_record_hash` | espn_cfb_pregame_capture, espn_nfl_odds, nba_pretip_capture → `record_hash` | none |
| `_iso` (isoformat) | espn_cfb_pregame_capture, nba_injury_live, nba_pretip_capture → `iso_utc` | none |
| `_iso` (`%Y-%m-%dT%H:%M:%SZ`) | ncaab_cbbd → `iso_utc_z` | cfb_pbp_live: reverted because its bytes are hashed (see above). This is a separate function from `iso_utc` because the output formats differ; the two were never merged. |
| `_default_http_get` | espn_cfb_pregame_capture, nba_pretip_capture → `browser_http_get` | nba_injury_live: its timeout is 10 s (`REQUEST_TIMEOUT_SECONDS`), not 20 s |
| `_opt_float` | ncaab_cbbd, ncaab_cbbdata → `opt_float` | cfbd: it catches TypeError/ValueError and returns None, where the others raise |
| `_read_jsonl` / `_append_jsonl` | none | Not identical. cfb_pbp_live has no `_read_jsonl` (it has a generator, `_iter_jsonl`). Its `_append_jsonl` uses default JSON separators (different bytes on disk), fsyncs and returns an int. |
| MLB curl `_get_json` | Both bodies moved to `mlb_statsapi.curl_get_json(url, params, *, timeout)`. Both `_get_json` methods remain and delegate with `self.timeout`. | none |

Notes:
- `USER_AGENT = "Mozilla/5.0"` was dropped from espn_cfb_pregame_capture and nba_pretip_capture. Each was used only
  inside the removed `_default_http_get`. Grepping src, tests and scripts found no other reference and no
  monkeypatch of it. The value now lives in `provider_capture.BROWSER_USER_AGENT`.
- The curl helper went into `mlb_statsapi.py` rather than `provider_capture.py`. It is MLB-specific (its error text
  is "MLB Stats API request failed"), `mlb_lineups` already named mlb_statsapi as the reference shape, and the
  import introduces no cycle. This departs from the plan's wording on where shared code goes.
- The `provider_capture.py` module docstring still describes only the snapshot writer. A short comment above the
  new helpers explains why they are there.
- espn_nfl_odds no longer uses `hashlib`, so that import was dropped. No other module was affected.

Equivalence checks (scratchpad scripts, not committed):
- `equiv_helpers.py` compares each removed function at 8c0dea2f with its shared replacement as an AST. Parameter
  names are normalized and module constants are inlined. All 12 pairs printed `SAME`, then `ALL IDENTICAL`.
  Negative control: `cfbd._opt_float`, `nba_injury_live._default_http_get` and a mismatched pair each printed `DIFF`.
- `equiv_mlb_get_json.py` runs the base-commit clients (loaded from `git show`) against the new ones, with
  `subprocess.run` and `time.sleep` replaced by recorders. It covers 3 timeouts × 5 params shapes × 6 outcome
  scripts × 2 clients, and compares argv, kwargs, sleep durations, return value, and error type and text.
  Result: `180 scenarios, 0 differences`. Negative control (new timeout +1): `180 scenarios, 180 differences`.
- No test in the repo exercises `mlb_statsapi`. The only mention is a docstring in `tests/rebuild/`. The harness
  above is the evidence for that module.

## Task 3: stale docstrings (commit da3a4727)
- **execution_ticket.py:** no longer says the dashboard issues tickets. Nothing in src, scripts or ops (dashboard
  included) calls `create_ticket` or `verify_ticket`. The docstring also cited `tests/test_execution_boundary.py`,
  which does not exist. It now points at
  `tests/test_consolidation_c.py::test_research_and_shadow_have_no_import_path_to_execution`, which scans only the
  rebuild package, and the docstring now says so.
- **models/meta_calibrator.py:** removed the "Beta Calibration (Kull et al., 2017)" claim. `TailCalibrator` is
  isotonic only. The top-level `meta_calibrator.py` makes no Beta claim and was not touched.
- **models/mlb_runline_v4.py:** the module and class docstrings now say the run matrix is an outer product of
  independent Poisson marginals, truncated and renormalized. The `compute_bivariate_run_matrix` and
  `bivariate_run_matrix` names were left unchanged, because they are API.
- **Skipped: models/mlb_first_inning.py.** `scalar_artifact_replay.engine_hash` hashes its bytes for
  `first_inning_logistic` and `half_inning_logistic`, and `replay_snapshot` rejects a mismatch unless the hash is in
  `HISTORICAL_ENGINE_HASHES`. A docstring edit would therefore change replay behavior. Fixing that docstring needs
  an explicit decision to add the current hash to `HISTORICAL_ENGINE_HASHES`.
- **Proof that only docstrings changed:** `ast_no_docstrings.py` printed `CODE-IDENTICAL` for all three files
  against 8c0dea2f.
- **Commit prefix:** this commit uses `docs(...)` rather than `refactor(...)`, because it changes no code.

## Tests (each file alone, `-o addopts='' -q`, without MODEL_PREDICTION_* env vars, one process at a time)

| File | Before | After |
|---|---|---|
| tests/test_ncaab_cbbd.py | 9 passed in 0.36s | 9 passed in 0.27s |
| tests/test_espn_cfb_pregame_capture.py | 15 passed in 0.38s | 15 passed in 0.29s |
| tests/test_daily_cfb_pregame_capture_wiring.py | 3 passed in 3.56s | 3 passed in 1.92s |
| tests/test_espn_nfl_odds.py | 11 passed in 0.40s | 11 passed in 0.35s |
| tests/test_nba_pretip_capture.py | 15 passed in 0.44s | 15 passed in 0.30s |
| tests/test_nba_anchor_v10_wiring.py | 14 passed in 2.37s | 14 passed in 2.03s |
| tests/test_cfb_pbp_live.py | 18 passed in 3.20s | 18 passed in 2.97s |
| tests/test_nba_v10_forward.py | 17 passed in 2.58s | 17 passed in 2.53s |
| tests/test_ncaab_cbbdata.py | 21 passed in 0.66s | 21 passed in 0.54s |
| tests/test_mlb_lineups.py | 14 passed in 0.33s | 14 passed in 0.29s |
| tests/test_provider_capture.py | 3 passed in 0.26s | 3 passed in 0.26s |
| tests/test_mlb_runline_v4.py | 1 passed in 0.26s | 1 passed in 0.29s |
| tests/test_meta_calibrator.py | 4 passed in 1.16s | 4 passed in 1.17s |
| tests/test_consolidation_c.py | 24 passed in 1.59s | 24 passed in 1.45s |
| tests/test_optimizations.py | 6 passed in 1.35s | 6 passed in 1.35s |
| tests/test_cfb_structural_v71.py (before = run detached at 8c0dea2f) | 10 passed in 4.21s | 10 passed in 3.55s |
| tests/test_nfl_settlement_consensus.py (before = run detached at 8c0dea2f) | 1 passed in 2.15s | 1 passed in 2.06s |

The "after" column for test_cfb_pbp_live (18 passed in 2.97s) and test_ncaab_cbbd (9 passed in 0.30s) was rerun
at HEAD 2c805adc. There were no pre-existing failures. `ruff check src/ tests/` printed `All checks passed!`, and
`ruff format --check` passed on every touched file. The pre-commit hooks (ruff, ruff-format) passed on all four
commits.

## Process notes
- **Stray command, recovered.** While verifying the cfb_pbp_live restore, a stray chained command ran
  `git stash` followed by `git checkout 8c0dea2f -- .`, which reset the worktree's tracked files to base. No commit
  was lost. It was recovered with `git checkout HEAD -- src` and `git stash pop --index`, after which
  `git diff HEAD` was empty except for the intended cfb_pbp_live restore. `E:/MP` was never touched.
- **Co-author line.** Commits carry `Co-Authored-By: Claude Sonnet 5.5`, as the brief asked. The session's
  attribution reminder named Opus 5.5. History was not rewritten.
- **execution_ticket claim checked repo-wide.** `git grep` across the whole repo, excluding tests, found no caller
  of `create_ticket`, `verify_ticket`, `is_ticket_valid` or `extract_order`, root `dashboard_server.py` included.
  The only hits are in docs.

## Observed, not changed
- `execution_ticket._secret_path` says the secret is "NOT in the git checkout". It calls `RuntimePaths.resolve()`
  without `require_external_runtime=True`, so with no env var set it may fall back to the repo's `data/`. This
  was not verified.

## git diff --stat 8c0dea2f..HEAD
```
 .../data_sources/espn_cfb_pregame_capture.py       | 20 +------
 src/model_prediction/data_sources/espn_nfl_odds.py |  7 +--
 src/model_prediction/data_sources/mlb_lineups.py   | 35 +-----------
 src/model_prediction/data_sources/mlb_statsapi.py  | 65 ++++++++++++----------
 .../data_sources/nba_injury_live.py                |  5 +-
 .../data_sources/nba_pretip_capture.py             | 20 +------
 src/model_prediction/data_sources/ncaab_cbbd.py    | 16 +-----
 src/model_prediction/data_sources/ncaab_cbbdata.py |  5 +-
 .../data_sources/provider_capture.py               | 37 +++++++++++-
 src/model_prediction/execution_ticket.py           | 14 +++--
 src/model_prediction/models/meta_calibrator.py     |  4 +-
 src/model_prediction/models/mlb_runline_v4.py      |  6 +-
 12 files changed, 101 insertions(+), 133 deletions(-)
```
