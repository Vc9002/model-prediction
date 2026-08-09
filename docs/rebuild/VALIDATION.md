# Rebuild Validation

Model selection uses chronological, complete-date splits. Fit on training data,
select model/calibration/thresholds on validation data, and leave the final test
untouched until its predeclared consumption rule is satisfied. Point-in-time
provenance and exact market semantics are prerequisites, not metrics to trade
off against accuracy.

For an unconsumed test, APIs, reports, and UI may expose its existence, start
date, model/calibrator hashes, prediction count, and coverage. They must not
expose aggregate accuracy, log loss, Brier, ROI, or CLV. This applies to
`mlb_moneyline_v2` while `consumed=false`.

Predictive qualification and economic qualification are separate. Winning a
challenger comparison does not establish either. The current MLB benchmark
barely differs from constant 0.5 and remains unqualified.

Integration requires two complete passes: once on the curated integration PR
head and again from a clean worktree at the merged `origin/main`. Each pass
requires a fresh Python 3.14 environment, dependency check, compilation,
imports/CLI smoke, rebuild tests, full repository tests, Ruff, no-new mypy
findings, dashboard/API smoke without runtime data, sealed-test regression,
market/fail-closed tests, and an unchanged incumbent checksum. CI additionally
tests the package minimum on Python 3.11 and compatibility on 3.12 and 3.13.

Machine verification evidence must be generated from JUnit/static-analysis
outputs. Counts must never be typed into a report by hand.
