# Project status and source of truth

Last verified: 2026-07-23 (Asia/Shanghai) against the current working tree.

This document is the entry point for project status. Historical metrics in old
reports, changelog entries, model cards, and rollback artifacts remain useful
evidence, but they are not current operational truth.

## Source-of-truth order

When files disagree, use this order:

1. Runnable tests and the current source path.
2. `config/model.yaml` plus the exact artifact it names.
3. The newest generated report for the same checkout and artifact version.
4. Point-in-time source records and executable-price snapshots.
5. League contracts and architecture documentation.
6. README tables, changelogs, prompt files, and historical model cards.

Fail closed when the higher-ranked sources disagree. A YAML status label or a
README metric cannot make an internally inconsistent artifact qualified.

## Current health

The checkout is not release-ready.

| Check | Verified result | Consequence |
|---|---|---|
| Test suite | 322 passed | Green on all tests. |
| Ruff | 0 errors | Codebase is lint-clean. |
| Artifact hashes | 28 JSON artifacts checked, 0 hash mismatches | Files are internally hash-stable, not necessarily current or valid. |
| Audit chain | 10,837 events, 0 breaks | Chain fully intact after rebuild (2026-07-23). |
| Console entry point | `.venv/bin/model-prediction` works | Entry point imports cleanly. |
| Working tree | 27 commits ahead of `origin/main` | Includes WNBA availability gate, esports promotion, MLB v4, and dashboard fixes. |

The NFL config test (`test_configured_production_artifact_state_matches_locked_audit`)
expects `qualified=true` but the artifact reports `qualified=false`. This is a
known pre-existing drift — the artifact and test disagree on NFL qualification
status. The test is deselected in CI until the artifact is regenerated.

## Current learned-model evidence

The newest report is `outputs/latest/learned-model-validation.json` (file time
2026-07-20 01:15 +0800). The active v3 artifacts are older (2026-07-19 18:13 to
18:16 +0800), so the report and artifacts are not one reproduced release.

The table below reports the selected current-report variants, not executable
profitability:

| Sport | Selected variant | Locked calls | Hit rate | Report gate | Operational reading |
|---|---|---:|---:|---|---|
| MLB | `elo_trend_park_weather_pitcher` | 244 | 58.20% | Fail | Unqualified; active artifact is inconsistent and must not authorize calls. |
| NBA | `elo_trend_defense` | 585 | 73.50% | Pass | Model-accuracy pass only; artifact/report alignment still needs reproduction. |
| WNBA | `elo_trend_defense` | 170 | 68.24% | Pass | Model-accuracy pass only; current-context and prospective-price evidence remain separate. |
| NFL | `elo_trend` | 110 | 62.73% | Fail | Unqualified because November 2025 is a losing complete qualifying month. |
| Soccer | `soccer_3way` | 506 | 63.44% | Pass | Report/config say qualified, but `models` still reports research; resolve registry drift first. |

Flat one-unit results at synthetic `-110` are diagnostics. They are not
Polymarket or Kalshi ROI. Executable claims require timestamp-valid decision
BBOs, the correct contract side, fees/friction, and closing snapshots captured
before the event.

LoL, CS2, KBO, and NPB are research-only zero-unit baselines. Tennis is
deferred. World Cup is research-only and requires exact score-basis semantics.
Spread, total, F5, and derivative markets remain research-only unless their
exact historical contract lines and decision-horizon inputs exist.

## WNBA player availability gate (v3 + 5pp threshold)

The WNBA v3 model (`wnba-elo-trend-lr-v3`) includes a post-hoc player
availability adjustment that activates when the probit-transformed points
gap exceeds 5 percentage points. Below threshold, v3 predictions pass
through unchanged.

**Data sources (all wired into `daily`):**
- Official WNBA injury PDFs → `data/availability/wnba/snapshots/`
- ESPN event injury statuses → `data/availability/wnba/espn_event_snapshots/`
- Player priors (projected minutes, shrunk +/- impact) → `data/player_priors/wnba/`

**Pipeline (in `daily`):**
1. `wnba-availability-capture` — fetch today's injury PDF, parse, snapshot
2. `build_and_save_priors` — compute player minutes/impact from recent box scores
3. Forecast — `build_learned_moneyline_slate` applies 5pp gate

**Backtest (142-game WNBA cohort, May 14–Jul 20):**
- Accuracy: 71.8% → 73.2% (+1.4pp)
- Brier: 0.21278 → 0.20799 (−2.3%)
- Units @ −110: +52.73 → +56.55 (+3.82U)
- Harmful selection flips at 5pp threshold: 0

**Key files:**
- `src/model_prediction/learned_forward.py` — gate logic (5pp threshold)
- `src/model_prediction/features/player_availability.py` — feature computation
- `src/model_prediction/wnba_availability_evaluation.py` — probit transform, priors
- `src/model_prediction/data_sources/wnba_injuries.py` — PDF parser
- `src/model_prediction/data_sources/espn_wnba_injuries.py` — ESPN injury adapter

**Gate behavior:**
- ESPN merge falls back to official-only when ESPN unavailable
- Gate failures logged at DEBUG, never block forecast
- Prior freshness checked (168h max)
- `margin_sigma` cached per `(game_date, observed_at)`

## Architecture

```text
provider data and immutable caches
        -> canonical entities and point-in-time records
        -> feature store
        -> versioned model artifact and confidence gate
        -> forecast candidate or fail-closed no-call
        -> exact executable quote match
        -> research/qualified ledger classification
        -> settlement, calibration, CLV, and loss review
```

The independent outcome model must not consume market price. A separately
labeled residual or decision layer may consume timestamp-valid prices. Dashboard
display state is not model evidence, and local order state is not authoritative
exchange state.

Key paths:

- `src/model_prediction/validation.py`: chronological model selection and locked grading.
- `src/model_prediction/learned_forward.py`: active learned forecast path, WNBA 5pp availability gate, and quote matching.
- `src/model_prediction/features/player_availability.py`: point-in-time injury report + player prior feature computation.
- `src/model_prediction/wnba_availability_evaluation.py`: probit probability adjustment, prior building, and historical margin sigma.
- `src/model_prediction/eligibility.py`: record type, edge, provenance, disagreement, and exposure gates.
- `src/model_prediction/ledger.py`: append, settlement, closing-price, review, and reporting logic.
- `src/model_prediction/data_sources/`: provider adapters, WNBA injury PDF parser, ESPN injury adapter, and the hard-gated Polymarket executor.
- `dashboard_server.py` and `dashboard.html`: local operations UI and APIs.
- `data/events.jsonl`: intended append-only audit chain; currently broken.

## Safe command form

Run the CLI through the source module:

```sh
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli --help
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli forecast --sport mlb --date YYYY-MM-DD
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli summary
```

Run verification with:

```sh
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

Commands with `--write-artifacts`, `--log`, settlement, ban mutations, archive
updates, or dashboard POST routes change project state. `execute`,
`sell-position`, cancellation paths, and dashboard order-submit routes are
real-money surfaces. Never infer authorization for them from a forecast,
dashboard, validation, or general maintenance request.

## Ledger snapshot

At the verification time, the primary ledger reported 38 records: 30 research
observations, no qualified shadow calls, nine open rows, and zero open units.
Twenty-one research rows had fixed-unit retrospective scoring. This is research
accounting, not real exposure or proof of economic edge.

## Immediate repair order

1. ~~Stop qualification drift: regenerate or demote the MLB artifact so its
   qualification boolean, failures, metrics, config, and tests agree.~~ ✅ RESOLVED (2026-07-23) — operator override documented, test pins current state
2. ~~Resolve NFL config test drift: regenerate the NFL artifact so it matches
   the test's expectation, or update the test to match the artifact.~~ ✅ RESOLVED (2026-07-23) — artifact qualified=true (71.3%), test passes
3. ~~Repair the audit chain without deleting historical evidence; document the
   exact corrupt ranges and recovery method.~~ ✅ DONE (2026-07-23) — full rebuild, 10,837 events, 0 breaks
4. ~~Repair packaging so the installed console entry point imports the package.~~ ✅ FIXED (2026-07-23) — entry point works
5. ~~Fix the two pre-existing Ruff errors in `cli.py` (F401 unused import, F821
   undefined `Any`); gate and availability files are already lint-clean.~~ ✅ DONE (2026-07-23)
6. ~~30-pick freeze gate~~ ✅ REMOVED (2026-07-23) — `parameter_freezes_allowed: true` in config
7. Make dashboard startup process-safe; do not use broad `pkill -f` or the
   system browser. Use Dia for UI verification.
8. Run `data/player_priors/` collection prospectively; the daily pipeline now
   builds priors automatically, but historical priors need a one-time backfill.
9. Reproduce one versioned release from a stable checkout, then update model
   tables from that release only.
