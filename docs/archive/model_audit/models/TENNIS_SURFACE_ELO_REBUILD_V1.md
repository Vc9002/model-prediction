# Model Card: `tennis-surface-elo-rebuild-v1`

**Model ID:** `tennis-surface-elo-rebuild-v1`
**Artifact:** `config/models/challengers/tennis-surface-elo-rebuild-v1.json`
**Calibrator:** `config/models/challengers/tennis-surface-elo-rebuild-v1-calibrator.json`
**Family:** `surface_elo` (same family as incumbent `tennis-surface-elo-v1`, independently trained)
**Status in config:** `qualified: false`, `production_allowed: false` — challenger artifact only
**Market:** moneyline only
**Audit date:** 2026-08-11
**Branch:** `rebuild/tennis-model-fit-v1`

---

## Why it exists

This is the first rebuild-native curated Tennis candidate: a Surface Elo model
trained entirely from TennisMyLife historical match data (ATP+WTA, 2021–2025,
27,949 matches), independently of the incumbent `tennis-surface-elo-v1`. Per
`docs/model_audit/ARCHITECTURE_CORRECTION.md`, a rebuild-native artifact
never loads, aliases, or shares state with its incumbent counterpart — this
model was trained from a fresh, separate code path (`scripts/train_tennis_rebuild_v1.py`)
against the rebuild-owned data foundation audited in
`docs/model_audit/models/TENNIS_REBUILD_DATA_FOUNDATION.md`.

Unlike the WNBA rebuild which fits a logistic regression on top of Elo features,
the Tennis model is **raw Surface Elo**: probability estimates come directly
from the Surface Elo formula (blended overall/surface rating with dynamic
surface weight), with no learned coefficients beyond Elo's K-factor and
surface-weight parameters. The calibrator is `identity` (pass-through) —
cross-fit calibration was skipped because the model produces single-class
labels (winner-side probability only).

## Market(s) predicted

Moneyline win probability only. The model predicts P(winner wins), where
"winner" is the match winner in the historical TennisMyLife data (the Elo
snapshot is taken before the outcome, and the probability is P(the actual
winner would win) using the Elo formula). Spread and total are not in scope
for this model.

## Feature set

Exactly one feature, per `market_models.moneyline.feature_names` in the artifact:

| Feature | Coefficient |
|---|---|
| `elo_probability_winner` | 1.0 |
| intercept | 0.0 |

Method: `surface_elo`. `positive_class: "winner"`. No market inputs used in
training (`training.market_inputs_used: false`).

The single feature is computed by `rebuild/tennis/elo.py::SurfaceEloBook`:

- **Per-surface Elo tracks**: Hard, Clay, Grass (missing surface → Hard fallback)
- **Overall Elo track** (cross-surface)
- **Dynamic surface weight**: `min(0.6, 0.1 + 0.025 × min(n_a, n_b))` — per incumbent lineage
- **K=32** default, **surface K boost=8.0** for surface-specific ratings
- Default Elo: 1500
- Walk-forward, chronologically ordered by `tourney_date`, day-bucketed (no same-day leakage)

No `trend_gap`, `defensive_trend_gap`, or auxiliary features — this is pure
Elo, not Elo+trend LR. The incumbent `tennis-surface-elo-v1`'s own internal
Elo formula is not documented in its artifact or an equivalent model card,
so direct coefficient comparison is not possible.

## Training method

- Data source: TennisMyLife historical matches (ATP+WTA, 2021–2025), loaded via
  `TennisNormalizedStore` → `load_matches()` → `build_walk_forward_rows()`.
- Day-bucketed walk-forward Elo construction: for each calendar day, take an Elo
  snapshot from all prior-day matches, predict today's matches, then update Elo
  with today's results. Same-day leakage is structurally prevented.
- Chronological 60/20/20 date-cluster split (`date_cluster_split`):
  train on earliest 60% of unique dates, calibration on middle 20%, locked
  holdout on most recent 20%.
- `walk_forward_features: true` — every row's `elo_probability_winner` is
  computed from Elo ratings built from `history` strictly before that row's date.
- `framework: "locked_complete_date_60_20_20"`, `locked_holdout: true`.
- Random seed: 42. Reproducible from `data/rebuild/normalized/tennis/` with
  `scripts/train_tennis_rebuild_v1.py`.
- Irregular results (retirement/walkover/default): **skipped for prediction**
  (not included in walk-forward rows), but the winner still receives a half-K
  Elo update afterward — the match result is still informative, just less so.
- Cold-start: minimum 3 prior matches per player before predicting; minimum
  100 matches in global history before any prediction.

### Walk-forward data summary

| Metric | Value |
|---|---|
| Total matches loaded | 27,949 |
| Walk-forward prediction rows | 23,559 |
| Skipped: bootstrap (< 100 history matches) | 121 |
| Skipped: cold-start (< 3 player matches) | 3,228 |
| Skipped: irregular (retirement/walkover/default) | 1,041 |
| Tours | ATP, WTA |
| Date range | 2021-01-04 to 2025-12-22 |

## Calibration

Cross-fit was **skipped** because the model produces single-class labels
(winner-side probability only — every row has `winner_win=1`). The
calibration evaluation framework (`cross_fit_calibration_eval`) requires
both positive and negative class labels for Platt/isotonic/temperature
fitting, but the walk-forward rows are all winner-side predictions.

**Winning method:** `identity` (pass-through). The identity calibrator was
selected as the only viable option — it literally returns `p → p` with no
transformation. The locked-holdout raw and calibrated metrics are therefore
identical.

## Historical results (locked holdout, 60/20/20 split)

| Metric | Train (60%) | Validation (20%) | Locked Holdout (20%) |
|---|---|---|---|
| N | 13,456 | 5,149 | 4,954 |
| Accuracy | 63.80% | 64.42% | 63.91% |
| LogLoss | 0.630 | 0.622 | 0.628 |
| Brier | 0.220 | 0.217 | 0.219 |
| ECE | 0.441 | 0.426 | 0.425 |

## PIT safety

**Capture-time-only provenance.** TennisMyLife's `tourney_date` is date-only
(one value per tournament, not per match), so match rows carry
`availability_basis: "capture_time_only"` — they prove what TennisMyLife
published and when this repo captured it, never a fine-grained historical
match-start vintage.

The day-bucketed walk-forward construction is structurally PIT-safe: a
match's Elo snapshot never includes its own result or same-day results.
The chronological split further ensures train/calibration/holdout sets are
real, non-fabricated date boundaries.

**The locked-holdout result is descriptive backtesting, not prospective
point-in-time evidence.** The chronological ordering and Elo walk-forward
discipline are real, but the underlying observation timestamps are backfill
capture times, not historical match-instant observations. This model cannot
be directly compared to the incumbent's live shadow ledger results, which
are genuine prospective PIT calls.

## Serving readiness

**Not ready for live serving.** Two blocking gaps:

1. **Cross-provider player identity resolution (TennisMyLife ↔ ESPN) is not
   yet built** — the Elo book is keyed by TennisMyLife's own provider-scoped
   player IDs, which cannot be matched to ESPN's live scoreboard identity
   space without a real identity resolver (the same kind of gap WNBA's
   `IdentityRegistry` closes for that sport, per `docs/rebuild/OPERATIONS.md`).
2. **Production not allowed** per artifact provenance (`production_allowed: false`).

The model is a credible research candidate and the data foundation is real
and audited (`GO` per `TENNIS_REBUILD_DATA_FOUNDATION.md`), but it should
not be wired into `rebuild-shadow` or any live pipeline until identity
resolution exists and a genuine prospective PIT qualification has been run.

## Comparison to incumbent (`tennis-surface-elo-v1`)

| Dimension | Incumbent (`tennis-surface-elo-v1`) | Rebuild v1 |
|---|---|---|
| Data source | ESPN-only (live API) | TennisMyLife (historical CSV) |
| Tours | ATP+WTA (ESPN) | ATP+WTA (TennisMyLife) |
| Date range | Unknown (artifact not inspected) | 2021–2025 |
| Match count | Unknown | 27,949 loaded, 23,559 rows |
| Surface classification | Tournament-name keyword heuristic | TennisMyLife native `surface` column |
| Hit rate (locked holdout) | 65.5% (4,269 calls, prospective PIT) | 63.9% (4,954 rows, descriptive backtest) |
| Calibration | Unknown | Identity (no calibration needed for raw Elo) |
| Production status | `shadow_qualified` | `qualified: false`, `production_allowed: false` |
| PIT provenance | Prospective (live ESPN captures) | Capture-time-only (TennisMyLife backfill) |

**Direct comparison is misleading** — the incumbent's 65.5% is genuine
prospective PIT evidence over 4,269 locked-holdout calls, while the rebuild's
63.9% is descriptive backtesting over 4,954 rows with different player
populations, different surface classification, and no cross-provider
identity resolution. The rebuild's result is directionally consistent
(surface-aware Elo produces ~64% accuracy on tennis moneyline) but should
not be interpreted as "the rebuild is worse" — the evaluation frameworks
are not comparable.

## Retention verdict

**KEEP_CHALLENGER.** The data foundation is real (`GO` per audit), the Elo
mechanics are faithful to the incumbent lineage, and the walk-forward
construction is structurally PIT-safe. The model is not ready for promotion
but is a credible starting point for the rebuild-native Tennis track. The
blocking gaps (cross-provider identity resolution, prospective PIT
qualification) are sequenced follow-ups, not defects in this artifact.

Do not archive or delete this challenger. It replaces `_BasicEloAdapter` as
the rebuild fallback for Tennis once identity resolution is built and the
model can be wired into `rebuild-shadow` for a prospective PIT evaluation.
