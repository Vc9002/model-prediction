# MLB v9 Research Execution Plan

**Written:** 2026-08-17 (burn-in day 2, ~01:30 local)
**Scope:** burn-in prep → v8 reproduction gate → v9 ablation matrix → v9-LR/XGB → calibration → score-distribution/totals rebuild → post-MLB queue.
**Companion docs:** `docs/BURN_IN.md` (freeze window), `docs/RESEARCH_BACKLOG.md` (P0 ordering + brainstorm items), `docs/CHAMPION_CHALLENGER.md` (paired harness), `docs/V8_REPRODUCTION.md` (pin-and-replay history), `docs/MODEL_IMPROVEMENTS.md` §1/§12 (live status), `docs/PROJECT_STATUS.md` repair-order item 9 (reproduction history).

---

## 0. Verified state — what is actually true today

These facts were checked against the repo on 2026-08-17, not assumed:

1. **v8 reproduction is NOT currently failing.** The aggregate-level reproduction was
   CONFIRMED 2026-08-13 via `scripts/mlb_v8_reproduction.py` (pin-and-replay):
   `call_ratio = 1.0` (148 vs 148), `hit_rate 0.6149 vs 0.6081` (delta 0.0068),
   `Brier 0.2378 vs 0.2464` (replay slightly better), train/validation/holdout
   counts 3814/1082/1391 exactly matching the artifact's recorded `training`
   block — `reproduced_closely=True`, inside the existing 0.7–1.3 / ≤0.03 bands.
   The "failure" premise is stale; the diagnosis (split/threshold non-determinism)
   landed 08-13 and the harness was fixed with date-boundary + fixed-threshold
   parameters (`build_walk_forward_rows`/`chronological_split`/`evaluate_variant`,
   additive, default-`None`).
2. **What remains is the stricter standard**: the current gate compares AGGREGATE
   bands (call ratio, hit rate, Brier). This plan upgrades it to ROW-LEVEL parity
   (event_id-level probability + feature deltas) — the standard this document
   adopts from the operator's 2026-08-17 directive. Aggregate parity is necessary
   but not sufficient: the same hit-rate can hide offsetting row-level errors.
3. **The isolated research workspace already exists**:
   `/Users/vincentc9002/worktrees/mlb-research` (branch `research/mlb-v8-reproduction`,
   HEAD `7a5d181`, clean). It already carries evaluator work
   (`--bootstrap` flag, cohort coverage, 24-variant smoke) and an expected
   CI delta-gate note. Main is at `69bd131` — 10 commits past the
   `consolidation-2026-08-15` tag (`37be479`); see §10 for the reconciliation.
4. **Existing tooling maps onto this plan** — nothing below rebuilds what exists:
   - `scripts/mlb_v8_reproduction.py` — pin-and-replay control (extend, don't replace).
   - `python -m model_prediction.feature_freezer freeze` — frozen walk-forward rows + manifest.
   - `python -m model_prediction.experiment_registry record --model-id ...` — experiment log.
   - `validation.py::evaluate_variant` (+ `_3way`) and `roadmap_challenger.py`
     (Brier/LogLoss per fold, date-cluster bootstrap ΔBrier) — evaluator base.
   - `production_feature_ablation.py`, `champion_challenger.py` (`freeze-production`,
     `compare-champion`) — paired comparison + promotion governance.
5. **The sqlite ledger stores per-pick decision payloads** — every historical v8
   row carries the full feature vector AND `model_probability` as computed at
   decision time. That is the ground truth for row-level parity; no re-derivation
   from scratch is needed to know what v8 "actually did."

---

## 1. Constraints and boundaries (bind until burn-in passes)

- **Live system frozen.** Until 2026-08-18 05:25 UTC acceptance: no promotion,
  no champion changes, no scheduler/runtime-schema/ledger-semantics changes.
  Bugfixes already committed this session (see §10) stand; no new live-behavior
  changes except genuine defect fixes.
- **All research prep happens in the worktree**, never in the merged checkout's
  runtime state. The worktree shares no writes with the runtime root; running
  read-only analyses there cannot disturb the burn-in.
- **No candidate selection, no promotion, no "promising results" acting**
  during the window. Preparing machinery is allowed; making model decisions is not.
- The Soccer Odds API DEGRADED state remains an accepted external dependency and
  does not block any phase below.

---

## 2. Phase 0 — research prep (now → 2026-08-18 05:25 UTC)

### 0.1 Sync the worktree from main

- Pull `main` (`69bd131`) into the `research/mlb-v8-reproduction` worktree.
  The 08-13 research commits (`7a5d181` lineage) are evaluator/freeze work that
  this plan consumes; sync forward so the research branch sees the session's
  fixes (notably `_normalize_name` accent-folding — §10.1) and the ruff
  target-version correction.
- Re-run the worktree's existing 24-variant evaluator smoke post-sync to confirm
  the branch still works against the merged tree.

### 0.2 Upgrade v8 reproduction to row-level parity

Extend `scripts/mlb_v8_reproduction.py` (or add
`scripts/mlb_v8_row_parity.py` beside it) to emit, for every v8 decision row:

```text
event_id, date, selection, market_type,
stored_v8_probability   (from ledger decision_payload_json.model_probability)
reproduced_probability  (pin-and-replay with v8's frozen boundaries/threshold)
absolute_delta,
stored_features         (from decision_payload_json, verbatim)
reproduced_features     (recomputed under the frozen contract)
feature_deltas          (per-feature, named)
mismatch_class          (from the taxonomy below, or "exact")
```

Ground-truth source: `ledger_records` where `model_id='mlb-elo-trend-lr-v8'`
(flat + main tiers — the 133+ settled rows plus open rows, NOT just main).
Reproduction source: v8's own recorded date boundaries + `confidence_threshold:
0.61966524` verbatim (the 08-13 method).

**Mismatch taxonomy (classify every row, output counts):**

```text
A. cohort/event identity        B. train/validation/holdout boundaries
C. feature ordering             D. Elo state
E. trend calculation            F. starter ERA
G. bullpen weakness             H. park factor
I. weather factor               J. missing-feature behavior
K. threshold                    L. probability orientation
```

Example output shape (illustrative, not a prediction):

```text
133 rows compared
  110 exact
   14 F starter-history mismatch
    5 H park mismatch
    3 D Elo-state mismatch
    1 A missing-event mismatch
```

**Acceptance criteria for Phase 1 (below) live in this table**: ≥98% of rows
`exact` (|Δprob| < 0.005 and zero feature deltas), and every non-exact row
classified with a cause. Any `unclassified` bucket at nonzero count = gate
failure. Note: the 2026-08-16 accent-folding fix (commit `287d979`) is expected
to produce a small, fully-classified `F` bucket — see §10.1; that is the one
bucket whose rows get a documented policy decision, not a silent pass.

### 0.3 Prepare the frozen v9 feature table

`python -m model_prediction.feature_freezer freeze --sport mlb` with the full
candidate column set (columns that exist as of today; new ones get added as
their providers land, each re-freeze bumps the manifest):

```text
event identity, decision timestamp, outcome
Elo; raw trend; residualized trend
starter ERA, FIP, K%, BB%, K-BB%        (starter_history.py — rolling, PIT-safe)
bullpen talent / availability / fatigue
PIT park (park_factor_pit)              (v8's static table is NOT reused for v9)
PIT weather: temperature, humidity, pressure, wind, roof
projected lineup strength; confirmed lineup strength   (P0 steps K/L — when built)
feature availability flags per row
```

Manifest must record: `dataset_hash`, `feature_schema_hash`, source hashes,
git SHA, `created_at`, decision horizon. **The frozen table is input-only until
08-18** — no model may be selected from it during the window.

### 0.4 Complete the standardized evaluator

Extend the existing evaluator so every binary MLB candidate automatically
reports (per fold + pooled):

```text
N, coverage
LogLoss, Brier
ECE, calibration slope, calibration intercept
accuracy, AUC
fold-by-fold metrics
paired ΔLogLoss vs v8, paired ΔBrier vs v8
date-cluster bootstrap CI + P(challenger better)
```

Economic output is computed but always reported SECONDARY (timestamp-valid N,
net EV, ROI, CLV where available) and never gates feature retention. Existing
coverage: Brier/LogLoss per fold + date-cluster bootstrap ΔBrier
(`roadmap_challenger.py`). To add: ECE + slope/intercept + AUC + paired deltas
+ P(better) formatting. Do this in the worktree; it is evaluator-only and
cannot touch the live system.

### 0.5 Pre-register the experiment template and retention rules

Extend the experiment template (already in `docs/RESEARCH_BACKLOG.md`) with a
`registered_threshold` field: hypothesis + success criteria recorded BEFORE the
run, so post-hoc threshold-fishing is structurally impossible. Retention rules:

```text
KEEP         consistent OOF improvement + stability + no PIT issue + coverage
REJECT       no OOF improvement, or CI crossing zero widely, or PIT issue
INCONCLUSIVE directionally good but unstable / low coverage — retest later
RETEST       same as INCONCLUSIVE but with a scheduled re-run condition
```

Worked example (from the operator's directive):

```text
FIP:        ΔBrier -0.0031, 4/5 folds better, bootstrap P(better)=0.91, coverage 96%  → KEEP
K-BB%:      ΔBrier -0.0003, 2/5 folds better, CI crosses zero widely                 → INCONCLUSIVE
```

---

## 3. Phase 1 (2026-08-18, ≥05:25 UTC) — burn-in acceptance, then the v8 gate

### 3.1 Burn-in final check (day 3)

Run `scripts/burn_in_checks.sh` + manual checks 1–7 + SQLite integrity check
(`PRAGMA integrity_check` on the runtime-root ledgers/runs DBs). Requirements:
no unexplained DOWN/DEGRADED, no repo DBs, one runtime, one scheduler, one
dashboard, no duplicate runs, no corrupted jobs, clean git tree. Soccer Odds
API DEGRADED remains accepted. Record day 3 in `docs/BURN_IN.md`.

On pass: `INFRASTRUCTURE_CONSOLIDATION = ACCEPTED`. **Infrastructure work stops**
unless a real defect appears. No new cleanup cycles.

### 3.2 v8 reproduction — the hard gate

Register in the experiment registry: `mlb-v8-reproduction-final`, incumbent
`mlb-elo-trend-lr-v8`. Run the row-parity report from §0.2. Two outcomes only:

- **PASS** → begin v9 ablations (Phase 2).
- **FAIL** → do NOT run v9 ablations. Fix the harness/data lineage until it
  passes. (Aggregate-level PASS already exists from 08-13, so a FAIL here means
  the row-level upgrade exposed a real lineage defect — that is exactly what
  this gate is for.)

**Do not "fix" v8's park leak.** The v8 static-park-table PIT defect
(`docs/V8_REPRODUCTION.md`) is part of v8's historical contract. Reproducing v8
means reproducing the leak; v9's `park_factor_pit` is the corrected successor.
Never refit v8 — that destroys the incumbent control. (This is also standing
repo policy: v8 is frozen and never modified.)

---

## 4. Phase 2 — v9 ablation matrix (one change at a time, in this order)

Each row = one experiment in the registry with pre-registered thresholds,
frozen feature table + v8 reproduced control as the paired incumbent:

```text
A   v8 reproduced control          (the reproduction from Phase 1)
B   FIP instead of ERA
C   K-BB%
D   FIP + K-BB%
E   remove trend
F   raw trend
G   Elo-residualized trend
H   bullpen talent
I   bullpen availability
J   bullpen talent + availability
K   PIT park (park_factor_pit)
L   PIT weather
M   projected lineup
N   confirmed lineup
```

Hard rules: one change at a time; never a kitchen-sink variant
("FIP + xFIP + K-BB + bullpen + weather + lineup + XGBoost" is forbidden —
it cannot attribute improvement); every variant evaluated against the SAME
frozen events/folds/timestamps as A; retention uses the KEEP/REJECT/
INCONCLUSIVE/RETEST rules from §0.5; economic metrics reported but never
retention-determining. Note the prior v9 numbers are void per CLAUDE.md
(2026-08-13 train/serve parity audit) — nothing pre-08-13 carries forward.

---

## 5. Phase 3 — build v9-LR, then compare XGBoost on the same table

- Retained features → regularized logistic regression → `mlb-v9-lr`
  (the simple challenger).
- Then `mlb-v9-xgb` on exactly the same events/features, compared on OOF
  proper scores. Decision rule: if XGBoost only improves accuracy while
  degrading Brier/LogLoss, LR wins. Do not assume XGBoost is better.

## 6. Phase 4 — calibration (OOF predictions only)

Identity / Platt / Temperature / Isotonic on the surviving model. Identity is
a legitimate winner; do not force calibration. Reuse the existing calibrator
infrastructure (challenger portfolio from prior sessions).

## 7. Phase 5 — MLB score distribution, then totals rebuild

- Fit (μ_home, μ_away, dispersion) and test Poisson / Negative Binomial /
  Poisson-lognormal (existing `simulate_game(method=...)` work is the
  starting point; the stable-seed pin must survive).
- Derive ML / spread / total from ONE coherent joint distribution.
- **Totals rebuild** (the known structural weak spot): build the
  absolute-run-environment model — offense, starting pitcher, expected starter
  innings, bullpen, lineup, park, weather. Fold in the 2026-08-17 brainstorm
  items whose raw data is already captured: **umpire over/under factors**
  (`officials` per game in `game_snapshots.jsonl`), **park altitude/elevation**
  (`venue_name` captured). Evaluate with total MAE, exact-line Brier,
  exact-line LogLoss, variance calibration. Current totals model = baseline,
  not something to incrementally tune forever.

---

## 8. Post-MLB queue (in order)

```text
1. WNBA v5 paired test (evidence says defensive_trend_gap was harmful)
2. WNBA possession/PPP architecture → score distribution
3. NFL calibration (Identity/Platt/Temperature/Isotonic on incumbent OOF)
4. Tennis v2 (surface-weighting challenger)
5. Soccer league split (EPL / La Liga / Bundesliga / Serie A / MLS / UCL / …)
   — independent fitted league state
6. Esports title split (CS2 / Valorant / LoL / Dota 2 / R6) — independent
   players/teams/features/hyperparameters/calibration/artifacts/promotion;
   no generic "esports v7"
7. KBO/NPB starter models
```

## 9. Promotion state (unchanged, and frozen through burn-in)

| Model | Decision |
|---|---|
| MLB v8 | KEEP incumbent/control |
| MLB v9 | Not yet eligible |
| MLB spread v3 | Keep baseline |
| MLB totals v3 | Rebuild (§7) |
| WNBA v4 | Keep |
| WNBA v5 | Queued challenger |
| NBA v4 / NFL v4 | Keep (NFL calibrator challenger next) |
| Tennis v1 | Keep |
| Soccer pooled | Keep temporarily; league-split replaces the research direction |
| CS2/Valorant/LoL/Dota2/R6 v6 | Keep |
| KBO/NPB v2 | Keep as controls |

No promotion during burn-in, full stop.

## 10. Risks, open questions, and reconciliation items

**10.1 Frozen-contract vs. the 2026-08-16 accent fix.** Commit `287d979`
changed `_normalize_name` so accented starter names now match across sources
where they previously failed. For live v8 serving this is a behavior change;
for the reproduction gate it means "what v8 actually did" (historical rows
computed with the buggy matching) vs. "what the code computes now" can
legitimately differ on exactly the affected rows (José Soriano/Jesús Luzardo
class). Policy: the row-parity report classifies these as `F` (starter-history)
mismatches with a documented cause; they neither fail the gate nor get silently
folded in. The operator decides whether v8's frozen control should replay the
historical buggy matching or the fixed one — a decision to record in the
experiment registry when the first `F` rows appear, not to pre-judge here.

**10.2 Main is 10 commits past the tag.** `37be479` → `69bd131` includes this
session's bugfixes (accent normalization ×2, 3.11-syntax repair, ESPN ID
capture), inert additions (line-movement shadow module, property tests), and
docs. None changed champions, scheduler behavior, runtime schema, or ledger
semantics; the launchd jobs execute from the working tree, so the fixes are
live. If strict tag-freeze is preferred, the research worktree can pin
`37be479` as the reproduction SHA and compare against it explicitly — the
pin-and-replay harness makes either choice explicit. Flag, don't bury.

**10.3 Sample sizes.** Row-parity ground truth is the full v8 ledger footprint
(flat 133 settled + main 5 settled + open rows); the 1391-row holdout is the
ablation evaluation set. Pre-registered bootstrap P(better) thresholds must be
set with these N in mind — a ΔBrier of ±0.003 at N≈1400 is near the noise
floor, which is exactly why the KEEP bar demands multiple folds AND bootstrap
agreement, not a single delta.

**10.4 Do-not-touch list for the worktree.** The research worktree must never
write to the runtime root, never call promotion/ledger-mutation commands, and
never load its own launchd jobs. Read-only against live data; writes confined
to its own checkout and scratch paths.
