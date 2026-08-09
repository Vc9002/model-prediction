# Foundation Frozen

Branch: `rebuild/clean-slate-v1`
SHA at freeze: `7b3bfca08f05ed19c7b5cddd5570a987302f3c6`
Frozen: 2026-08-08
Python: 3.14.5 (matches `pyproject.toml`'s `requires-python = ">=3.14,<3.15"`)

This closes the foundation-completion pass that started from
`outputs/rebuild/foundation_status.md`'s 2026-08-07 snapshot (commit
`39f3f0c`). That snapshot's own "Known blockers" named two remaining
unmigrated canonical-identity types (event, venue), non-MLB player
identity, horizon architecture generalization, and a resume system that
tracked a run ID but not real per-stage completion. All five are closed
by commits `44c483b`..`7b3bfca` on this branch. What "frozen" means here,
precisely:

- The shared identity/horizon/resume **infrastructure** every sport
  plugs into is real, tested, and consistent across MLB and the other 5
  sports with a real collector (NBA/WNBA/NFL/Soccer/Tennis).
- MLB is the only sport with a real feature/predict/market/decide chain
  (per CLAUDE.md's own explicit sequencing: "MLB must fully satisfy this
  gate before expansion to other sports"). The other 5 sports correctly
  remain collection + identity only — building fake feature/predict
  stages for them would violate this codebase's own
  never-fabricate-missing-data principle, not complete the foundation.
- **This is not a claim of predictive or economic qualification.**
  Nothing here changes MLB's held-out evaluation status
  (`mlb_predictive_qualification: NOT_STARTED` as of the last generated
  `foundation_inventory.json`) or any sport's foundation-gate status.
  Those are Part 2/Part 3 questions this freeze does not touch.

## What closed this session (commits `44c483b`..`7b3bfca`)

| Commit | What |
|---|---|
| `44c483b` | Canonical event identity (`resolve_or_register_event`, `resolve_espn_scoreboard_event_id`, `resolve_event_by_team_pair`) — wired into all 5 real ESPN scoreboard collectors. Doubleheaders verified to resolve to two distinct canonical events, not one. |
| `cc2dde4` | Links Polymarket's own event_id into the same canonical event registry ESPN populates (`resolve_or_link_polymarket_event_id`), wired into MLB's `match_markets_stage`. |
| `ee13425` | Canonical venue identity (`resolve_or_register_venue`, `resolve_espn_scoreboard_venue_id`) — wired into all 5 collectors. Real ESPN venue fields only (id/name/city/state/indoor); lat/long/timezone/capacity/surface left honestly unset, not fabricated. |
| `9c37e4d` | Canonical player identity outside MLB via real ESPN roster collection (`resolve_or_register_player`, `resolve_espn_roster_player_id`, new `ROSTER_CONTRACT`) — wired into NBA/WNBA, NFL, Soccer. Deliberately *not* fuzzy-matched by name (unlike teams/venues) since real distinct players commonly share names — CLAUDE.md's own "duplicate player names" test case. Tennis excluded on purpose: ESPN tennis competitors are already canonical "team" entities (each match competitor *is* one player). |
| `ab5f724` | Generalized horizon architecture: `compute_decision_times()` extracted from MLB-only inlined logic into sport-agnostic shared infra; `horizon_specs_for_sport()` (previously dead code, zero real callers) wired into real MLB horizon output as `available_information`, closing CLAUDE.md Part 1 §9's explicit per-prediction storage requirement for the one sport with a real builder. |
| `7b3bfca` | True resume system: real `run_stages` ledger table + `record_stage_result()`/`get_completed_stages()`. `--resume-run-id` now genuinely skips already-`SUCCESS` `collect`/`build_features` (both disk-backed, idempotent). `predict`/`match_markets`/`decide` deliberately never skipped — they depend on in-process `MLBAdapter.state`, which a resumed process doesn't have; real cross-process resume for those needs fitted-model artifact reload, a separate, already-disclosed gap. |

## Pipeline confirmation: data → features → model → probability → market → decision → ledger → settlement

| Stage | MLB | NBA/WNBA | NFL | Soccer | Tennis |
|---|---|---|---|---|---|
| Collect (ESPN scoreboard) | real | real | real | real | real |
| Canonical team/event/venue identity | real | real | real | real | real |
| Canonical player identity | real (pybaseball) | real (ESPN roster) | real (ESPN roster) | real (ESPN roster) | real (via team-identity path) |
| Point-in-time features | real (`horizon_builder.py`) | not implemented | not implemented | not implemented | not implemented |
| Sports probability model | real (two-head) | not implemented | not implemented | not implemented | not implemented |
| Market matching (Polymarket) | real, incl. canonical-event linking | not implemented | not implemented | not implemented | not implemented |
| Winner-first decision | real | not implemented | not implemented | not implemented | not implemented |
| Shadow ledger persistence | real | not implemented | not implemented | not implemented | not implemented |
| Resume (stage-skip) | real for collect/build_features | n/a (no stages past collect yet) | n/a | n/a | n/a |

"Not implemented" here means the shared CLI (`sport_adapter.py`) honestly
reports `NOT_IMPLEMENTED` for that stage — never a fabricated result.
Esports remains registered wrapping an honest stub collector. KBO/NPB
remain an explicit `research_only` decision (no real schedule/result
source exists).

## Verification run at freeze

All commands run against `7b3bfca` on 2026-08-08.

```bash
git status --short          # clean at HEAD (see note below)
git branch --show-current   # rebuild/clean-slate-v1
git rev-parse HEAD          # 7b3bfca08f05ed19c7b5cddd5570a987302f3c6
python3 --version           # Python 3.14.5
```

**Tests** (existing dev venv):
```
pytest -q
1050 passed, 1 skipped, 181 warnings in ~52-85s
```

**Fresh-clone reproduction** (genuine, not reused from a prior session):
cloned `rebuild/clean-slate-v1` at `7b3bfca` from the local repo into an
isolated scratch directory, fresh `venv`, `pip install -e ".[dev]"` from
a cold pip cache-index state, then:
```
python -c "import model_prediction.rebuild"   # OK
pytest -q                                     # 1050 passed, 1 skipped — identical to the dev venv
ruff check src/model_prediction/rebuild tests/rebuild    # All checks passed
mypy src/model_prediction/rebuild                        # 33 pre-existing errors, 9 files (see below)
```

**Ruff**: `ruff check src/model_prediction/rebuild tests/rebuild` is
clean — matches CI's own blocking scope
(`.github/workflows/ci.yml`). Full-repo ruff has ~190 pre-existing,
non-blocking findings unrelated to `rebuild/` (documented in CI as
intentionally non-blocking).

**Mypy**: `mypy src/model_prediction/rebuild` surfaces 33 pre-existing
errors across 9 files (`economic.py`, `models/tennis.py`, `ensemble.py`,
`validation.py`, `models/__init__.py`, `ablation.py`, `mlb_features.py`,
`collectors.py` ×2) — a subset of the 112-error whole-package baseline
CI already tracks as non-blocking. None of the 9 files are ones this
session's five commits created (`identity.py`, `horizons.py`,
`shadow_ledger.py` all mypy-clean); the 2 pre-existing `collectors.py`
errors were individually confirmed via `git stash` at each of this
session's five commits to already exist on the prior baseline, not
introduced here.

**CI-attached-to-current-head**: UNVERIFIED — these 6 commits have not
been pushed to `origin` (no push was requested or performed this
session), so there is no remote CI run to check. Local verification
(fresh clone + install + test + lint + type-check) stands in for it
here; per `foundation_status.md`'s own convention, this line should be
manually reconfirmed once the branch is actually pushed.

**Working tree**: at the moment of running the above, `git status
--short` was not byte-for-byte empty — a background daily-collection job
independent of this session continues writing to `data/` (odds
snapshots, availability rosters, ESPN raw pulls) between commands. None
of that churn is part of this freeze; the five commits above touch only
`src/model_prediction/rebuild/`, `scripts/rebuild_shadow_cli.py`, and
their tests.

## Known blockers carried forward (unchanged by this session)

Everything below was already true in `foundation_status.md` and is
unaffected by this pass — listed here so this document doesn't imply
they were silently resolved:

- Real order-book depth still doesn't exist as a data source; every real
  market correctly fails `INSUFFICIENT_DEPTH`.
- `mlb_predictive_qualification`: `NOT_STARTED`. Held-out evaluation
  remains genuinely inconclusive on ~20-25 games — more real backfill is
  the only fix, not further feature engineering.
- No real artifact-reload mechanism for MLB's fitted sklearn models
  (`MLBTwoHeadModel.to_artifact()` persists metadata/hashes only) — this
  is exactly why `predict`/`match_markets`/`decide` can't be genuinely
  resumed across a process boundary yet (see the `7b3bfca` row above).
- 8 sports (NBA/WNBA/NFL/soccer/tennis/esports/KBO/NPB) still have zero
  foundation-gate items complete — correct and unchanged; this was never
  in scope for this pass per CLAUDE.md's sequencing.
- `conservative_probability` still implements `bootstrap_uncertainty`
  only, not the full spec (`calibration_uncertainty`,
  `lineup_uncertainty`, `missingness_penalty`, `model_disagreement`).
  Deliberately deferred to model-development phase.

## Next executable command

Model-development phase (Part 2 / Part 3 of `CLAUDE.md`) starts from
here — MLB probability engine rebuild (statistical + ML ensemble),
market-residual model, backtest. See `CLAUDE.md`'s own "PART 2" and
"PART 3" sections for the full required scope; nothing in this document
authorizes skipping predictive qualification before economic claims.

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest -q
PYTHONPATH=src:. .venv/bin/python scripts/rebuild_shadow_cli.py --sport mlb --date <YYYY-MM-DD> --horizon late
```
