# 261121-xbn — NBA v7/v9 market-blend sign and v9 artifact key drift guard

Worktree `E:/MP-wt/fix-nba-2026-11-21`, branch `fix/nba-v9-blend-keys-2026-11-21`, base `387c0489`.
Operator request. Do not push or merge. Never touch `E:/MP`. Do NOT run `git stash`,
`git checkout <sha> -- .`, `git reset --hard` or `git clean`.

NOTE: `docs/AGENTS.md` lists the NBA model and every file in `config/models/*` as protected, and
`models/nba_v91.py` says v9 is not edited. The operator explicitly asked for this fix, so edit
`nba_v7.py` and `nba_v9.py`, but ONLY as specified. Do NOT edit any `config/models/*.json`, any default
value, or any served number.

## Fix 1: B4 market-blend sign (nba_v7.py:243, nba_v9.py:268)

1. Theory: both set `market_home_margin = -game.spread_away_line`, but the cover formula
   (`_normal_cdf(game.spread_away_line, expected_margin, sd)`, basketball.py:457, v9:345) treats
   `spread_away_line` as the HOME-MARGIN line (away covers iff margin < line). So the blend pulls away from
   the market. `nba_v91.py:319-321` already carries the corrected `+line` (B4).
2. Evidence: docs/NBA_REVIEW_AND_V10_PLAN_2026-11-20.md row B4. Weight is 0.0 in every artifact under
   `config/` and `data/calibration` and in the runtime models root, and `nba_replay_harness.py:127` raises
   if a scored engine has a non-zero weight, so served numbers cannot change.
3. Test: add to a new `tests/test_nba_blend_sign.py` (or beside tests/test_nba_v91.py; read it first for
   fixtures): for BOTH `NBAStructuralV7Engine` and `NBAStructuralV9Engine`, with `market_blend_weight=1.0`
   the blended expected margin equals `spread_away_line` (home-margin convention), and with weight 0.2 it moves
   TOWARD the line (strictly between the unblended margin and the line when they differ); weight 0.0 output is
   bit-identical to the unblended output. Show the weight>0 tests FAIL with the fix reverted.
4. Failure mode: a research script that runs v7 with blend weight above 0 now reports different numbers if
   re-run (scripts/ablate_nba_v7.py, scripts/market_blend_kaggle_revalidation.py,
   scripts/test_combo_features_v7.py). List them in the summary; do NOT edit those scripts.
5. Rollback: `git revert`.
Also add a one-line `# B4` comment at each site (why: spread_away_line is the home-margin line).

## Fix 2: v9 artifacts silently drop unread keys and fall back to code defaults (containment only)

Theory: `NBASpreadModelV9.from_artifact` / `NBATotalModelV9.from_artifact` / `NBAMoneylineModelV9.from_artifact`
(nba_v9.py ~439-490) call `cls(**artifact)`; the engine constructor takes `**_extra_kwargs`, so a retuned or
misspelled artifact key does nothing, and an artifact that omits a key silently uses a default. Per review:
nba-total-margin-v9.json omits 15 constructor params and carries `trend_total_weight`, `team_total_weight`
that the engine never reads; nba-spread-margin-v9.json carries `possession_model`, `dynamic_matchup_pace`,
`coherent_moneyline`, `use_logistic_moneyline`, all ignored. VERIFY these facts by introspecting the real
artifacts before relying on them; correct the numbers if they differ.

Also read the wrapper `__init__` first: `cls(**artifact)` may take the CONSTRUCTOR defaults while
`NBAStructuralV9Engine.from_artifact` takes its own `.get(key, default)` defaults. If the two tables differ on
a key an artifact omits, REPORT it (key, both defaults, which artifacts are affected); do not unify them.

Design (containment only; served output must stay identical):
- Add a small helper in nba_v9.py that, given an artifact dict and the accepted constructor parameter names
  (use `inspect.signature`), returns `(unread_keys, defaulted_keys)`. Call it once per `from_artifact` and emit
  ONE `logging` warning (module logger) listing both sets and the artifact's model version when either is
  non-empty. Never raise. Never change what is passed to the constructor.
- Add `tests/test_nba_v9_artifact_keys.py` that loads the shipped v9 artifacts from `config/models/`
  (nba-spread-margin-v9.json, nba-total-margin-v9.json, the v9 moneyline artifact; find the real file names via
  config/production.yaml `champions:`/`allowed_models`), and pins BOTH sets (unread and defaulted) and the
  resolved default values for each, so any future key drift or default change fails loudly. Also test the helper
  on a synthetic artifact with a misspelled key.
- Do NOT: honor the unread keys, edit any JSON artifact, change any default, or make the loader raise. Each of
  those changes served numbers or the artifact hash and needs a new version via `model_promotion`
  (operator decision). Say so in the summary.

## Verification (project rules: never the full suite)
- Unset `MODEL_PREDICTION_RUNTIME_ROOT` and `MODEL_PREDICTION_LEDGER_AUTHORITY` for pytest; confirm
  `model_prediction.__file__` is under `E:/MP-wt/fix-nba-2026-11-21` (`PYTHONPATH=src`, print it).
- Baseline BEFORE editing: run the existing NBA test files that import nba_v7/nba_v9/nba_v91
  (grep tests/ for `nba_v7`, `nba_v9`, `NBAStructuralV`, `nba_replay_harness`, `nba_v10_engines`), each with
  `python -m pytest <file> -o addopts='' -q`. One pytest process at a time. Record results.
- AFTER: rerun the same files; `ruff check src/ tests/`; `ruff format --check` on touched files.
- Prove served output is unchanged: before editing, record the output of the three v9 wrappers
  (`predict_spreads`/`predict_totals`/moneyline) on a small deterministic fixture slate that exists in the
  tests (or build one from tests helpers) for weight 0, and assert byte-identical after.
- Commit each fix separately (code + its tests only), message `fix(quick-261121-xbn): ...`, end with
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. No .planning files in code commits.
