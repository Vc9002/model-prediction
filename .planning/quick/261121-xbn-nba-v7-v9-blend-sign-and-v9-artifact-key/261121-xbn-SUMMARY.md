---
quick_id: 261121-xbn
status: complete
branch: fix/nba-v9-blend-keys-2026-11-21
base: 387c0489
commits: [7427ba98, f7df1fac]
---

# 261121-xbn: NBA v7/v9 market-blend sign and v9 artifact key drift guard

Served output is byte-identical before and after both fixes (see Served-output proof).
No `config/models/*.json`, default value, or research script was edited.

## Fix 1: B4 blend sign (commit 7427ba98)

- Files changed: `src/model_prediction/models/nba_v7.py`, `src/model_prediction/models/nba_v9.py`
  (`market_home_margin = -game.spread_away_line` became `game.spread_away_line`, with a `# B4` comment
  at each site; the stale "home margin = -away_line" comment in v7 was reworded),
  `tests/test_nba_v91.py` (one assertion), `tests/test_nba_blend_sign.py` (new).
- Tests added (8, parametrized over `NBAStructuralV7Engine` and `NBAStructuralV9Engine`): weight 1.0 lands on
  the line; weight 0.2 equals `0.8*base + 0.2*line` and lies strictly between base and line; weight 0.0 is
  bit-identical to the default engine (with a line and with `spread_away_line=None`).
- Existing test changed (disclosed): `tests/test_nba_v91.py::test_b4_blend_uses_spread_away_line_as_home_margin_line`
  asserted that v9 at weight 1.0 yields `-6.5` (it pinned the bug). Flipped to `+6.5`, same strength, comment
  updated. Without this the suite would fail on the intended behaviour change.
- BEFORE (fix applied to nothing yet, new tests): `4 failed, 4 passed`; e.g.
  `E assert -25.0 == 25.0 +- 2.5e-05` (full weight) and `E assert 2.6655764951690712 < -2.867538803864743` (partial).
- REVERTED by editing both sites back to `-game.spread_away_line`, run of `test_nba_blend_sign.py` + `test_nba_v91.py`:
  `FAILED ...test_full_weight_blend_lands_on_the_home_margin_line[NBAStructuralV7Engine]`, `[...V9Engine]`,
  `FAILED ...test_partial_blend_moves_toward_the_line[NBAStructuralV7Engine]`, `[...V9Engine]`,
  `FAILED tests/test_nba_v91.py::test_b4_blend_uses_spread_away_line_as_home_margin_line` -> `5 failed, 11 passed`.
- RESTORED: `65 passed in 3.45s` across the 7 NBA test files below.
- Research scripts whose results change if re-run with blend weight above 0 (not edited):
  `scripts/ablate_nba_v7.py` (market_blend_20/35/50, v8_combo),
  `scripts/market_blend_kaggle_revalidation.py`, `scripts/test_combo_features_v7.py`.
  No other script under `scripts/` sets a blend weight on NBA v7/v9 (`ablate_hockey_v2_market_blend.py` only
  mentions the v7 pattern in a docstring). `nba_replay_harness.py` raises on non-zero weight, so replay is unaffected.

## Fix 2: v9 artifact key drift guard (commit f7df1fac)

- Files changed: `src/model_prediction/models/nba_v9.py` (module logger, `artifact_key_drift(artifact, accepted)`
  using `inspect.signature`, `_warn_on_artifact_key_drift` called once in each of the three wrapper
  `from_artifact`; one `logging` warning when either set is non-empty; wrapped so it never raises; what
  reaches `cls(**artifact)` is unchanged), `tests/test_nba_v9_artifact_keys.py` (new, 14 tests).
- Tests: pins unread set, defaulted set, and resolved default values (value and type) for the three shipped
  artifacts; wrapper-vs-engine attribute equality on each artifact; misspelled key (`margn_sd`) flagged and still
  ignored; complete artifact is silent; one warning logged naming both sets; helper never raises.
  Mutation check: changing the `league_pace` constructor default to 99.3 failed 6 of these tests; restored.
- Verified key sets (introspected from the real artifacts, "unread" includes metadata keys):
  - `nba-spread-margin-v9` (41 keys): unread 18 = artifact_hash, cdf_family, coherent_moneyline,
    dynamic_matchup_pace, expected_margin_detail, expected_margin_formula, expected_margin_source, league, method,
    model_version, note, positive_class, possession_model, prediction_formula, qualification, schema_version,
    sport, use_logistic_moneyline. Defaulted 4 = league_pace (99.2), moneyline_version, spread_version, total_version.
    The four behavioural keys named in the plan (possession_model, dynamic_matchup_pace, coherent_moneyline,
    use_logistic_moneyline) are confirmed unread.
  - `nba-total-margin-v9` (30 keys): unread 18 (same metadata set with expected_total_*, plus possession_model,
    dynamic_matchup_pace, team_total_weight 0.15, trend_total_weight 0.85). Defaulted 15 (plan number confirmed) =
    b2b_penalty_away 1.5, b2b_penalty_home 2.0, elo_home_advantage 38.0, elo_weight 0.7, home_court_points 1.75,
    league_pace 99.2, logistic_scale 11.8, margin_sd 14.0, market_blend_weight 0.0,
    moneyline_margin_weight 0.3 (derived 1-elo_weight), rest_weight 0.15, trend_weight 0.3, and the 3 versions.
    These margin-side defaults matter to totals because |expected_margin| drives the close-game OT adjustment.
  - `nba-moneyline-v9` (7 keys): unread 7 = artifact_hash, feature_schema_version, market_models, method,
    model_version, qualification, sport (it is a logistic-regression artifact; the engine reads none of it).
    Defaulted 27 (every engine constructor parameter that has a code default, i.e. the pure code-default engine). Serving status: rolled-back placeholder per its
    own qualification note; only spread and total wrappers are called from `cli/forecast.py`.
- Wrapper-vs-engine default tables: they do NOT differ. `NBAStructuralV9Engine()` and
  `NBAStructuralV9Engine.from_artifact({})` produce identical attributes, and on all three shipped artifacts
  `wrapper.from_artifact(a).engine` equals `NBAStructuralV9Engine.from_artifact(a)` attribute-for-attribute
  (now pinned by a test). Only difference is that the engine classmethod coerces with `float()`/`bool()`.
- Deliberately NOT done (each changes served numbers or the artifact hash and needs a new version via
  `model_promotion`, an operator decision): honouring the unread keys, editing any artifact JSON, changing any
  default, making the loader raise.

## Verification

- `model_prediction.__file__` = `E:\MP-wt\fix-nba-2026-11-21\src\model_prediction\__init__.py`; pytest run with
  `PYTHONPATH=src`, `env -u MODEL_PREDICTION_RUNTIME_ROOT -u MODEL_PREDICTION_LEDGER_AUTHORITY`, one process per file.
- Baseline before editing: test_nba_v7 4 passed; test_nba_v9 7 passed; test_nba_v91 8 passed;
  test_nba_replay_harness 5 passed; test_nba_serving_plumbing 28 passed; test_nba_nfl_v5 5 passed.
- After both commits: test_nba_v9_artifact_keys 14 passed; test_nba_blend_sign 8 passed; test_nba_v7 4 passed;
  test_nba_v9 7 passed; test_nba_v91 8 passed; test_nba_replay_harness 5 passed; test_nba_serving_plumbing 28 passed;
  test_nba_nfl_v5 5 passed. `ruff check src/ tests/`: All checks passed. `ruff format --check` on touched files: clean.
- Served-output proof: a deterministic fixture slate (history and upcoming built from `tests/test_nba_v91.py`
  helpers; spread lines 3.5, -6.0, None) run through `NBASpread/Total/MoneylineModelV9` and `...V7` built with
  `from_artifact` on the real shipped artifacts (48 predictions: line, probabilities, inference_inputs,
  rationale). SHA-256 `34c4519fe604799d277e04aa003e38526f44b55d44b0561571fd309667df7761` before editing, after
  fix 1, and after fix 2; `cmp` reports identical both times.

## git diff --stat 387c0489..HEAD

```
 src/model_prediction/models/nba_v7.py |   9 +-
 src/model_prediction/models/nba_v9.py |  46 ++++++-
 tests/test_nba_blend_sign.py          | 106 ++++++++++++++++
 tests/test_nba_v91.py                 |   4 +-
 tests/test_nba_v9_artifact_keys.py    | 221 ++++++++++++++++++++++++++++++++++
 5 files changed, 379 insertions(+), 7 deletions(-)
```
