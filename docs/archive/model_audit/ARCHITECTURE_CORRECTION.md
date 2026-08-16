# Architecture correction to the model/feature reconciliation audit

**Date**: 2026-08-11
**Supersedes**: framing in `docs/model_audit/FEATURE_RETENTION_MATRIX.md` (soccer row) and
the informal "Model Retention Bridge" concept discussed alongside the audit. Nothing in
`docs/model_audit/MODEL_INVENTORY.md` or `docs/model_audit/ARCHIVE_RECOVERY_MAP.md` needed
correcting — both already described the soccer situation accurately (see below); only the
top-level summary language did not.

## What was wrong

The audit's initial follow-on plan proposed a "Model Retention Bridge": wrapping the six
active incumbent models (`mlb-elo-trend-lr-v8`, `nba-elo-trend-lr-v4`,
`wnba-elo-trend-lr-v4`, `nfl-elo-trend-lr-v4`, `soccer-poisson-dc-v1`,
`tennis-surface-elo-v1`) as `rebuild` `ModelLifecycle` implementations, on the premise that
the rebuild architecture was the system now in need of a model layer.

That premise is false, and the literal instruction would have violated an explicit,
pre-existing architectural rule.

## The correction

**Incumbent models were never removed.** Per `docs/PROJECT_STATUS.md`, all six are already
live, producing real predictions today via the existing `daily` pipeline. What changed at
this audit's base commit (`826c893`, "archive `data/main`, shadow ledger now primary") was
the dashboard's *primary display* switching from the Main ledger to the rebuild's shadow
ledger — a display change, not a model-layer removal.

**The rebuild track has a hard isolation boundary**, stated in `docs/rebuild/ARCHITECTURE.md`:
*"The rebuild may not import incumbent order/execution adapters, load an active incumbent
artifact as its candidate... The rebuild and incumbent systems may share a repository and
dashboard shell, but not decision state, ledgers, artifacts, execution controls, or
promotion state."* A "bridge" that loads an active incumbent artifact as a rebuild candidate
would violate this directly.

**"Restore the models" means restore the model *lineage*, never the incumbent *artifact*:**

- Model family, feature definitions, training methodology, calibration methodology, and
  validated hyperparameter ideas may be studied and reimplemented inside the rebuild.
- The incumbent artifact itself, its decision state, its ledger rows, and its promotion
  status may never be loaded into or shared with the rebuild.
- A rebuild candidate for a sport already covered by an incumbent model is an
  **independently trained sibling**, not a copy — its own rebuild-normalized training data,
  its own fit, its own artifact (under `config/models/challengers/`), its own calibrator.
  Its name must make provenance unambiguous and must never reuse the incumbent's version
  number (e.g. `wnba-elo-trend-lr-rebuild-v1`, never `wnba-elo-trend-lr-v5`, even if the
  feature set starts out identical to v4's).
- Incumbent models continue operating and additionally serve as an external control/
  benchmark for rebuild research. The two prediction streams are compared offline only —
  never merged, ensembled, or averaged.

## `_BasicEloAdapter` is a deliberate foundation stage, not a bug

`src/model_prediction/rebuild/sport_adapter.py:271-280` documents `_BasicEloAdapter` as the
disclosed, working "basic prediction, working pipeline, not an advanced model" foundation
for NBA/WNBA/NFL/Soccer/Tennis inside the rebuild, explicitly scoped as moneyline-only with
no derived feature store, pending each sport's curated, individually-reviewed model
transplant (`docs/rebuild/OPERATIONS.md`). It is not an accidental regression.

`docs/model_audit/MODEL_INVENTORY.md` and `docs/model_audit/ARCHIVE_RECOVERY_MAP.md` already
described this precisely and did not need correction: both already characterize the
unmerged `origin/rebuild/soccer-v1` fix as `_SoccerCollectionOnlyAdapter`, which makes
soccer's `build_features`/`predict`/`match_markets`/`decide` stages explicitly return
`STAGE_NOT_IMPLEMENTED` with the reason *"a draw-aware three-way model is required; binary
Elo is unsafe"* — i.e. **fail closed instead of silently producing a wrong-shaped
prediction**, not a shortcut to wire in the incumbent `soccer-poisson-dc-v1`. Only
`FEATURE_RETENTION_MATRIX.md`'s top-level summary used looser language ("instead of the real
model") that could be misread as recommending exactly the forbidden bridge; that row has
been reworded to match the precise framing above.

`_BasicEloAdapter` must not be deleted. It remains the correct, documented rebuild fallback
for each of these five sports until that sport's curated candidate is ready to become
primary — at which point it becomes the fallback/control, not primary.

## Binding going forward

- Never load an active incumbent artifact (`config/models/*.json` outside
  `config/models/challengers/`) from any code under `src/model_prediction/rebuild/`.
- Every rebuild-owned serving/candidate artifact lives under `config/models/challengers/`
  (or another explicitly rebuild-owned challenger root) — never overwriting or aliasing an
  incumbent artifact path.
- When encountering a `NOT_IMPLEMENTED` status, a collection-only stage, or a generic
  fallback adapter inside `rebuild/`, check `docs/rebuild/ARCHITECTURE.md` and
  `docs/rebuild/OPERATIONS.md` first and classify it as DELIBERATE FOUNDATION, REAL BUG, or
  PLANNED MISSING CAPABILITY before editing — most of these are intentional, disclosed
  staging, not defects.
