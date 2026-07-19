# Project status and source of truth

Last verified: 2026-07-20 (Asia/Shanghai) against the current working tree.

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
| Test suite | 190 passed | Green is misleading: the MLB expectation changed during review while the inconsistent artifact did not. |
| Ruff | 3 errors | The tree is not lint-clean. |
| Artifact hashes | 28 JSON artifacts checked, 0 hash mismatches | Files are internally hash-stable, not necessarily current or valid. |
| Audit chain | 6,712 lines, 9 broken links; first failures at 5, 33, 922, 927, 928 | Do not describe the audit chain as intact. |
| Console entry point | `.venv/bin/model-prediction` raises `ModuleNotFoundError` | Use the module invocation below until packaging is repaired. |
| Working tree | Five commits ahead of `origin/main` plus broad tracked/untracked changes | Tie every conclusion to this checkout; do not regenerate artifacts during a concurrent write. |

During this review,
`tests/test_config.py::test_configured_production_artifact_state_matches_locked_audit`
initially expected MLB to be unqualified and failed. Another writer then changed
the test to expect `true`; the artifact itself remained unchanged. The active
MLB v3 artifact still contains `qualified: true` alongside sub-60% results and
explicit failure reasons. Making the test mirror the artifact removed the alarm;
it did not resolve the contract violation.

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
- `src/model_prediction/learned_forward.py`: active learned forecast path and quote matching.
- `src/model_prediction/eligibility.py`: record type, edge, provenance, disagreement, and exposure gates.
- `src/model_prediction/ledger.py`: append, settlement, closing-price, review, and reporting logic.
- `src/model_prediction/data_sources/`: provider adapters and the hard-gated Polymarket executor.
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

1. Stop qualification drift: regenerate or demote the MLB artifact so its
   qualification boolean, failures, metrics, config, and tests agree.
2. Restore a test that asserts the qualification contract rather than copying
   the artifact boolean; resolve NFL and Soccer status/registry inconsistencies.
3. Repair the audit chain without deleting historical evidence; document the
   exact corrupt ranges and recovery method.
4. Repair packaging so the installed console entry point imports the package.
5. Fix the three Ruff errors and return the full suite to green.
6. Make dashboard startup process-safe; do not use broad `pkill -f` or the
   system browser. Use Dia for UI verification.
7. Reproduce one versioned release from a stable checkout, then update model
   tables from that release only.
