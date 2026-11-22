# 261121-wnd — Behavior-preserving helper dedupe in data_sources

Origin: whole-project review 2026-11-21 (seven read-only reviewers). The simplifier is limited to
the low-risk candidates below. Everything else the review found is reported to the operator, not
changed here.

Worktree: `E:/MP-wt/simplify-2026-11-21`, branch `simplify/helpers-2026-11-21`, base `8c0dea2f`.
Do not push or merge. Scheduled tasks run the `E:\MP` tree, which this task never touches.

## Tasks

1. **Dead function.** Remove `ncaab_cbbd.game_date_utc` (`data_sources/ncaab_cbbd.py:393`) if a repo-wide
   grep (src, tests, scripts, config, ops, docs, dashboard assets) finds no caller and no string reference.
2. **Identical pure helpers.** For each helper family below, diff the copies. Consolidate only copies whose
   bodies are behaviorally identical, into the existing `provider_capture.py` (or a new private
   `_capture_common.py` if that avoids a circular import). Keep the old module-level name in every
   importing module (import it under the same name) so `monkeypatch.setattr(module, "_default_http_get", ...)`
   in tests keeps working. A copy that differs in any branch, default, or clamp stays as it is.
   - `_record_hash`: `espn_cfb_pregame_capture.py:232`, `espn_nfl_odds.py:169`, `nba_pretip_capture.py:357`
   - `_iso`: `cfb_pbp_live.py:149`, `espn_cfb_pregame_capture.py:237`, `nba_injury_live.py:74`, `nba_pretip_capture.py:367`, `ncaab_cbbd.py:218`
   - `_default_http_get`: `espn_cfb_pregame_capture.py:242`, `nba_injury_live.py:78`, `nba_pretip_capture.py:371`
   - `_opt_float`: `cfbd.py:326`, `ncaab_cbbd.py:246`, `ncaab_cbbdata.py:266`
   - `_read_jsonl` / `_append_jsonl` in `nba_injury_live.py:87,100` vs `cfb_pbp_live.py:178` (only if identical)
   - the curl-subprocess `_get_json` in `mlb_statsapi.py` and `mlb_lineups.py`
3. **Stale docstrings only** (no code): `mlb_first_inning.py` header ("research-only", "serving path untouched"),
   `models/mlb_runline_v4.py` "bivariate" wording, `meta_calibrator.py` "Beta calibration" claim,
   `execution_ticket.py` claim that the dashboard issues tickets. Do NOT touch any file whose bytes are hashed:
   `models/mlb.py`, `models/mlb_structural_v10.py`, `features/mlb_v10_features.py`, `mlb_v10_replay.py`,
   `mlb_margin_replay.py`.

## Hard exclusions
`rebuild/`, v8, `simulate_game` RNG stream, `production_canary` messages, `config/`, any disabled-sport
module, `validation.py`, `learned_forward.py`, ledger/audit/settlement/pricing/devig paths, `portfolio/`
order and sizing paths, `polymarket_execute.py`, and the `_normal_cdf` / `_effective_current_season_games`
copies in `models/`.

## Verification (per project rules: no full suite, no MODEL_PREDICTION_* env vars)
- `ruff check src/ tests/` and `ruff format --check` on touched files.
- For each touched module, run its single test file with `-o addopts='' -q` BEFORE and AFTER the edit and
  compare. Pre-existing failures are not regressions; report them with their output.
- Commit each task atomically (code only). No SUMMARY, STATE or PLAN files in code commits.
