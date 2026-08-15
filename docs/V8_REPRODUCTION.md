# MLB v8 reproduction — contract, findings, status

Research-only document. Burn-in (O) runs through 2026-08-18; no promotion
or serving decisions are made from anything in this file.

## Frozen incumbent definition (mlb-elo-trend-lr-v8)

| Field | Value |
|---|---|
| artifact | `config/models/mlb-elo-trend-lr-v8.json` |
| artifact_hash | from artifact (self-verifying) |
| feature order | `elo_probability`, `trend_gap`, `park_factor`, `weather_factor`, `starter_era_gap`, `bullpen_weakness_gap` |
| coefficients | `[3.0404972651, -0.0248808023, -0.9016764146, -0.2981045997, -0.0190272663, 0.1519919276]` |
| intercept | `-0.2831037846` |
| threshold | `0.61966524` |
| positive_class | from artifact (orientation check, layer L) |
| train window | `2024-04-06 .. 2025-07-22` — recorded 3,814 rows |
| validation window | `2025-07-23 .. 2026-04-10` — recorded 1,082 rows |
| holdout window | `2026-04-11 .. 2026-07-29` — recorded 1,391 rows |
| missingness policy | artifact carries NONE; harness drops rows with unavailable features |
| decision horizon | game-day walk-forward (ET game dates) |

**Known PIT defect, preserved deliberately:** v8's `park_factor` comes
from a static table with a 2026-table leak into history. Do NOT refit v8
to fix it — that would destroy the incumbent control. v9 replaces it with
`park_factor_pit`. Reproducing v8 means reproducing the defect.

## How the replay works

`scripts/mlb_v8_reproduction.py` (aggregate) and
`scripts/mlb_v8_row_parity.py` (row-level, this directory's tooling) pin:
- date boundaries read verbatim from the artifact's `training` block
  (no fractional re-split on a growing dataset — the 08-13 diagnosis),
- `confidence_threshold` verbatim (no relearning),
- feature order verbatim,
- `end_date` capped one day past the holdout end (no future leakage).

## Findings (2026-08-15, research workspace @ consolidation-2026-08-15)

### 1. Cohort drift: +31 net-new rows, -2 lost rows

The games file (`data/historical/mlb_games_all.jsonl`) is ingest-ordered;
its tail carries a late-ingest block of games dated 07-16..07-25 (the
08-14 reconciliation, appended after the 08-13 batch). 31 of those are
net-new event ids inside v8's holdout window.

- After excluding the 31: **1,389 rows vs 1,391 recorded** — two
  freeze-time rows are missing from today's file.
- **v8's build never snapshotted the holdout event-id list**, so the two
  lost rows cannot be identified. The frozen v9 feature table (below)
  exists to prevent this class of loss for future models.
- Aggregate consequence is small: calls reproduce **148/148** at the
  pinned threshold; hit-rate delta 0.0052; Brier 0.2379 vs 0.2464
  (replay slightly better).

### 2. Date convention: harness dates are ET, raw file is UTC

Four games on UTC 2026-04-11 (ET 04-10, the validation cohort's last
day) and three on UTC 07-30 (ET 07-29, the holdout's last day) straddle
the window boundaries. This explains the raw-count (1,421) vs
harness-count (1,420) delta of one and is a contract note, not a defect:
walk-forward dates are ET game dates.

### 3. Coefficient parity FAILS — root cause: history-dependent features

Refitting on today's pinned train rows yields coefficients that diverge
from v8's shipped ones by up to **0.0107** (not within 1e-6). Elo,
trend, park, and weather features for train-window rows are computed
from the FULL prior history; post-freeze backfills changed that history,
so the feature values v8 was fit on no longer exist anywhere.

Row-level probability consequences are bounded: max per-row |p_shipped −
p_refit| = **0.0006**, mean ~1e-4. Aggregate reproduction at the pinned
threshold is therefore tight (148/148) even though the exact
probabilities are unrecoverable.

### 4. Provenance gap in the games file — FIXED 2026-08-15

Only 16 of 8,147 rows carried `raw_source` (the rows ingested since
08-14); the 08-14 session record's claimed backfill never landed in this
file. Fixed: `scripts/mlb_provenance_backfill.py` matched every
unprovenanced row to the EARLIEST surviving ESPN snapshot containing its
event_id (8,131 filled, 0 unmatched, 658 distinct snapshots,
2024-02-22..2026-08-14), stamped the ingest convention verbatim
(raw_source/raw_hash/parser_version), and verified 0 non-provenance
field mismatches vs the pre-backfill backup. Walk-forward rows and v8
parity numbers are unchanged; only the games-file source hash moved.

### 5. Missingness policy is undocumented

The artifact stores no missingness policy; `build_walk_forward_rows`
silently drops rows with unavailable features. For the frozen table,
availability flags per feature per row make this explicit (layer J).

## Parity status

| Layer | Status |
|---|---|
| B split boundaries | PASS — 3,814 / 1,082 exact; holdout +29 explained |
| C feature order | PASS — harness order equals artifact order |
| K threshold | PASS — pinned verbatim |
| L orientation | CHECK — artifacts record positive_class='home'; learned_forward never reads the field (always returns home-win p). Consistent with every shipped artifact, but the field is inert at serving — hardening note |
| A cohort identity | PARTIAL — 31 net-new identified and excluded; 2 freeze-time rows unidentifiable (no snapshot) |
| Coefficients | FAIL (drift ≤ 0.0107) — freeze-time features unrecoverable |
| Row probability | BOUNDED — max Δp 0.0006 vs refit; calls 148/148 |
| D–I per-feature train/serve parity | PASS (40-game sample, 2026-08-15): elo/trend/park 40/40 exact; weather ≤0.029 source-drift (archive recompute vs build-time value, no definition skew); starter ≤5e-4 (map stores rounded gaps, live computes raw) — see outputs/research/mlb_v8_parity/feature_parity_sample.json |
| J missing-feature | NOTED — no NaN features in holdout; policy undocumented |

## What this means for the v8 reproduction decision

The formal decision (`mlb-v8-reproduction-final`, recorded in the
experiment registry only after burn-in) will be one of:

- **PASS with drift quantified** — cohort + threshold + orientation
  reproduce; probabilities reproduce to Δp ≤ 0.0006 under coefficient
  drift ≤ 0.0107 caused by post-freeze backfills. Acceptable for
  ablations IF the harness always compares challengers against the same
  refit baseline on the same rows (paired comparisons).
- **FAIL → fix lineage first** — if exact reproduction is required, the
  harness needs a frozen feature snapshot; the v9 frozen table is that
  mechanism going forward, but v8's freeze-time features cannot be
  reconstructed retroactively.

Either way, **v8 itself is never modified** (park defect included).

## Frozen v9 feature table (prep)

`mlb_v9_feature_table.parquet` + manifest (dataset_hash,
feature_schema_hash, source hashes, git SHA, created_at, decision
horizon) is built and committed on this branch
(`scripts/mlb_v9_feature_table.py`, full walk-forward history, no date
cap). Not-yet-built features (lineup strength, bullpen talent, PIT
forecast temperature/humidity/wind/roof) are listed in the manifest;
any addition changes the schema hash. It must NOT be used to select a
model until burn-in passes.

## Standardized evaluator (prep)

`scripts/mlb_evaluator.py` — per-candidate: N, coverage, LogLoss, Brier,
ECE, calibration slope/intercept, accuracy, AUC, per-split metrics,
paired ΔLogLoss/ΔBrier vs the same-refit v8 baseline, and date-cluster
bootstrap CI with P(challenger better) (2,000 resamples, seeded).
Smoke-verified: `elo_only` vs baseline — dLL +0.0033, dBr +0.0016,
P(better|LL)=0.002 (baseline wins, as expected). Economics stay
secondary by design.
