# Model card: `mlb-elo-trend-lr-v8`

Status recommendation: **WORKING / HISTORICAL_CONTROL / NEEDS_RECALIBRATION**
— incumbent moneyline model, retained as a control rather than treated as
"proven best." All numbers below were re-verified directly against
`config/models/mlb-elo-trend-lr-v8.json` in this worktree (self-hash
verified — see Artifact reproducibility) on 2026-08-11, not transcribed
from prior sessions' summaries without checking.

## Why it exists

Current active MLB moneyline production artifact
(`config/model.yaml: mlb.active_production_version: mlb-elo-trend-lr-v8`).
Direct successor to `mlb-elo-trend-lr-v7`, replacing v7's team-level
`pitcher_era_gap` with a real per-starter rolling-ERA feature
(`starter_era_gap`) built specifically to close the gap `mlb-elo-trend-lr-v4`
had shipped broken (see the feature doc's `starter_era_gap_legacy_event_map`
entry). Promoted 2026-08-04 by explicit operator directive, not because it
cleared the project's own automatic promotion bar — see Threshold selection
and Known defects below.

## Market(s) predicted

Moneyline only (`market_models.moneyline`, `positive_class: "home"`).

## Feature set

Exactly the six features named in this audit's task brief, confirmed by
reading `feature_names` directly from the current artifact:

| # | Feature | Coefficient |
|---|---|---|
| 1 | `elo_probability` | 3.0404972651 |
| 2 | `trend_gap` | -0.0248808023 |
| 3 | `park_factor` | -0.9016764146 |
| 4 | `weather_factor` | -0.2981045997 |
| 5 | `starter_era_gap` | -0.0190272663 |
| 6 | `bullpen_weakness_gap` | 0.1519919276 |

Intercept: `-0.2831037846`. Method: `logistic_regression`.

Full per-feature detail (formula, source, PIT-safety, ablation evidence) is
in `docs/model_audit/features/MLB.md` — not duplicated here.

## Training method

`training.walk_forward_features: true`, `market_inputs_used: false`.
Three chronological, non-overlapping windows recorded on the artifact
itself:

- **Coefficient fit**: 2024-04-06 to 2025-07-22 (3,814 observations)
- **Threshold selection**: 2025-07-23 to 2026-04-10 (1,082 observations) — `threshold_source: "validation cohort; never locked holdout"`
- **Locked holdout**: 2026-04-11 to 2026-07-29 (1,391 observations)

Per the artifact's own `training.promotion_rationale`: a real walk-forward
ablation (`build_walk_forward_rows` + `chronological_split`, matching v7's
own methodology) was **self-consistency-verified** by first reproducing
v7's exact stored holdout numbers before making the v7→v8 comparison — a
real methodological safeguard against a broken re-implementation silently
producing a false "improvement."

## Threshold selection

`confidence_threshold: 0.61966524`, `threshold_source: "validation cohort; never locked holdout"` — selected on the validation window, never the locked holdout, matching the project's stated promotion contract (`docs/AGENTS.md`: "Walk-forward only. Locked holdout. Never peek.").

**Current-source correction to prior documentation** (see Known defects,
item 1): `MASTER.md`'s F-57 entry and `docs/PROJECT_STATUS.md` both describe
a *later* re-learned threshold (0.587335, target_hit_rate lowered from 0.65
to 0.60, yielding 352 calls / 58.5% hit rate) as the current live state.
**This is not what the artifact actually checked into the branch this audit
worktree is based on contains.** Verified via `git log --oneline -- config/models/mlb-elo-trend-lr-v8.json`
against the current worktree's `HEAD` (826c893): only one commit
(`face73f`, 2026-08-04) touches this file on this branch's history, and it
introduced `confidence_threshold: 0.61966524` / 148 calls / 60.81% hit
rate — the number described in F-57 as the artifact's *prior, too-selective*
state, before the fix F-57 claims to have applied. A second commit
(`0144d7b`, "chore: daily pipeline outputs 2026-08-05") does exist in the
repository's full history and does contain the F-57-described values
(`confidence_threshold: 0.58733546`, 352 calls, 58.5% hit rate,
`meets_primary_holdout_metrics: false`) — but `git merge-base --is-ancestor 0144d7b HEAD`
confirms it is **not an ancestor of the current `main`/HEAD this worktree is
built from**. In plain terms: the F-57 threshold fix exists somewhere in
this repository's git history but has not (yet, on this line of history)
landed on the `main` branch this audit is scoped to. **Report this
divergence to an operator before trusting either `docs/PROJECT_STATUS.md`'s
narrative or this document's own numbers as "the" current live threshold —
they currently disagree, and only one of the two states is actually
serving.**

The rest of this card reports the numbers **as actually committed in the
current artifact** (0.619665 / 148 calls / 60.81%), since that is what
self-hash-verifies against the file in this worktree today.

## Historical results

From the artifact's own `qualification` block, self-hash-verified:

- `hit_rate`: 0.608108 (60.81%)
- `hits` / `calls`: 90 / 148
- `called_rate`: 0.106398 (10.64% of locked-holdout games cleared the confidence gate)
- `units_at_minus_110`: 23.818182
- `sample_size`: 1,391 (full locked-holdout window)
- `brier_score`: 0.246354 (holdout)
- `validation_brier_score`: 0.24702
- `meets_primary_holdout_metrics`: **true**
- `qualified`: **false**

v7's own recorded numbers for comparison (same locked-holdout window,
same self-hash-verified artifact): `hit_rate` 0.584746 (58.47%), 118 calls,
69 hits, `brier_score` (holdout) 0.246456, `validation_brier_score` 0.24645617995721666,
`meets_primary_holdout_metrics`: **false** (missed the 60% bar and had a
non-positive qualifying month, 2026-04, at -110).

So v8's holdout performance is a real, positive improvement over v7's on
every headline metric (higher hit rate, more calls, more units) — it is
the *validation-set* comparison, not the holdout comparison, that
disqualifies it. See Known defects.

## Calibration diagnostics

v8's own qualification block does **not** carry a `calibration` sub-object
(no `calibration_intercept`/`calibration_slope`/`expected_calibration_error`/`reliability_buckets` keys present) — confirmed by direct read of the full
JSON. v7's artifact does carry one: `calibration_slope: 1.645`,
`calibration_intercept: -0.740`, `expected_calibration_error: 0.0741`,
`log_loss: 0.6865`, on a 118-sample reliability check with two buckets
(108 calls at mean-predicted 65.4% resolving to 57.4% actual — i.e.
overconfident by ~8pp, matching `tested_features.json`'s corrected-claims
entry about MLB overconfidence direction). **v8 has no equivalent recorded
calibration diagnostic in its own artifact** — a real gap relative to v7,
not something this audit can fill in without rerunning the calibration
script against v8's locked holdout.

## Known defects

1. **Threshold/state divergence between `main` and prior documentation** —
   see Threshold selection above. This is the most operationally important
   finding in this card: PROJECT_STATUS.md and MASTER.md's F-57 entry
   describe a state that isn't present in the current committed artifact.
2. **Disqualified by a validation Brier regression, not a holdout failure**
   — `qualification.failures`: *"validation Brier regressed vs incumbent v7
   feature set (0.24702 vs 0.24655) even though locked-holdout metrics
   clear the bar (0.6081 hit rate, 148 calls) — disqualified under
   docs/AGENTS.md's promotion rule (a validation regression is not
   overridable by a good holdout number, since doing so is exactly
   'peeking at holdout')."* Promoted anyway by explicit operator directive
   2026-08-04 — the artifact is honest about this in its own file, which
   this audit independently confirms rather than takes on faith.
3. **No calibration diagnostics recorded** — see above; v7 has them, v8
   does not.
4. **Ledger audit-trail gap (fixed, historical)** — `MASTER.md` F-55:
   `starter_era_gap` was silently missing from the audit ledger for a
   period immediately after v8 shipped (correct model scoring throughout;
   incomplete audit trail only). Fixed same day.
5. **Operational dependency risk on `starter_era_gap`** — the artifact's
   own `training_data_note`: *"starter_era_gap depends on
   data/mlb_statsapi/game_snapshots.jsonl, kept current as of this
   artifact's build by cli.py's daily _capture_mlb_starter_snapshots step
   (added 2026-08-04, same day as this artifact) — verify that capture is
   still running before trusting this feature live."* Not independently
   re-verified as still running in this audit (out of scope — read-only
   documentation task, no live pipeline execution performed).
6. **`park_factor`/`weather_factor` both carry blocked point-in-time
   provenance** (season-retroactive static table; untimestamped historical
   weather cache) — see the feature doc for full detail. Two of the six
   feature coefficients in this model are, by the project's own registry,
   not currently defensible as production-safe on PIT grounds, independent
   of their statistical contribution.

## PIT-safety

Mixed, feature-by-feature (see `docs/model_audit/features/MLB.md` for the
full per-feature breakdown):

- `elo_probability`, `trend_gap`: PIT-safe.
- `park_factor`, `weather_factor`: **blocked** — static/untimestamped
  provenance, per the project's own registry.
- `starter_era_gap` (v8's live implementation): PIT-safe, verified in code
  (strictly-before-decision filtering, fail-closed on insufficient
  history) — **not** the older, structurally broken
  `starter_era_gap_legacy_event_map` implementation that shipped in v4.
- `bullpen_weakness_gap`: PIT-safe, verified in code, same design pattern
  as `starter_era_gap`'s live provider.

## Train/serve parity

- `elo_probability`, `trend_gap`, `bullpen_weakness_gap`, `starter_era_gap`:
  confirmed parity — same function/module used in both `validation.py`
  (training replay) and `learned_forward.py` (live serving), or an
  independently-reimplemented-but-methodologically-identical pair for the
  two starter/bullpen features (training uses an event-id-keyed historical
  replay for backtesting; serving uses a name-keyed live lookup — by
  design, not an accidental mismatch, per direct code comparison).
- `park_factor`: parity at the mechanical level (same static table, same
  function, both paths) but the table's own PIT provenance is invalid
  across the full historical training window regardless of serving parity.
- `weather_factor`: **weak parity** — training reads a day-level historical
  cache (`data/features/historical_weather.json`, closer to actual
  observed conditions), while live serving fetches a real-time Open-Meteo
  *forecast*. This is a real, previously undocumented definitional
  mismatch beyond the registry's existing PIT-timestamp complaint — flagged
  in the feature doc.

## Artifact reproducibility

Self-hash-verified in this audit: recomputed
`sha256(json.dumps({k:v for k,v in raw.items() if k != "artifact_hash"}, sort_keys=True, separators=(",", ":")))`
against the file's own `artifact_hash` field — **match confirmed**
(`3b345499bd6bad9bcf65367a07fe0c668fb5817d4f016a0a865c01d9212fdeb8`).

`docs/model_audit/prior_evidence/incumbent_artifact_hashes.txt` (an earlier
session's snapshot, dated on or before 2026-08-05) records a **different**
hash for this same filename
(`2abc3bd9c18d694357bdf83cfb6b24ca9fd106eb02673ce9a72be5889141907c`) — this
confirms the artifact has been regenerated/retrained at least once since
that snapshot was taken, consistent with the divergent-threshold finding
above. Prior evidence in this repo should not be assumed to describe the
artifact currently on `main`; always re-verify against the live file.

## What to retain / change

- **Retain** as the active moneyline control. Its holdout performance is
  real and positive; the disqualification is a project-governance call
  (validation-regression tripwire), not evidence the model is bad.
- **Change**: resolve the threshold/state divergence documented above
  before trusting any downstream summary of "current" MLB v8 performance —
  determine whether the `0144d7b` re-learned-threshold state should be
  cherry-picked/reapplied to `main`, or whether `main`'s current
  0.619665-threshold state is the intended one and MASTER.md/PROJECT_STATUS.md
  need correcting instead.
- **Change**: generate and record calibration diagnostics for v8 (currently
  absent from the artifact, present for v7) before any further promotion
  decision.
- **Change**: re-run `starter_era_gap` vs. `starter_fip_gap` head-to-head
  as a real, committed ablation artifact (see feature doc) — the FIP
  feature's performance claim (+1pp hit rate, -39% ECE, +11 units) is
  currently only a code comment, unverified, and would be a natural v9
  candidate if confirmed.

## What would justify replacing this family

A challenger that (a) beats v8's real locked-holdout hit rate (60.81%,
148 calls) **and** does not regress validation Brier vs. v8's own feature
set (closing the exact gap that disqualified v8 itself), **and** carries
full calibration diagnostics, **and** resolves the `park_factor`/
`weather_factor` PIT-provenance gaps rather than inheriting them. The
clean-slate XGBoost two-head/negative-binomial challenger evaluated in
`MLB_CLEAN_SLATE_TWO_HEAD.md` does not currently meet this bar — its own
corrected benchmark log loss (0.6927) barely beats a naive-constant
baseline (0.6931) and does not clearly beat v8's own log loss (0.6839) on
a different sample.
