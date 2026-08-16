# MLB v9 Research Execution Plan

**Written:** 2026-08-17 (burn-in day 2); **rev 2:** 2026-08-17 (promotion path, per-run control gate, decision timing, thresholds, timeline).
**Scope:** burn-in prep → v8 reproduction gate → v9 ablation matrix → v9-LR/XGB → calibration → prospective shadow → promotion governance → score-distribution/totals rebuild → post-MLB queue.
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
   (event_id-level probability + feature deltas) — the standard adopted from the
   operator's 2026-08-17 directive. Aggregate parity is necessary but not
   sufficient: the same hit-rate can hide offsetting row-level errors.
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
6. **The shadow-first promotion path is the real governance chain** and this plan
   does not shortcut it: research → walk-forward → OOF → same-event → bootstrap →
   freeze → prospective shadow (flat ledger, real quotes) → settled comparison →
   `PROMOTION_CANDIDATE` → operator decides. Phases 6–7 below are that chain.

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

- Merge `main` (`69bd131`) into the `research/mlb-v8-reproduction` worktree
  (merge, not rebase — the branch's evaluator commits stay attributable).
  Expect possible conflicts in files both sides touched (`roadmap_challenger.py`,
  `validation.py`); resolve with the branch's evaluator intent preserved and
  the main-side bugfixes kept (notably `_normalize_name` accent-folding and the
  ruff py311 target).
- Post-sync: re-run the worktree's existing 24-variant evaluator smoke AND the
  pin-and-replay script from the synced tree. Both must pass before anything
  downstream proceeds — the sync itself is an experiment-registry `void`-able
  event if it breaks lineage.

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

### 0.3 Prepare the frozen v9 feature table

`python -m model_prediction.feature_freezer freeze --sport mlb` with the full
candidate column set. **Existence status matters for scheduling** — freeze
what exists today; add the rest when their providers land; every re-freeze
bumps the manifest:

```text
EXISTS TODAY (freeze now):
  event identity, decision timestamp, outcome
  Elo; raw trend; residualized trend
  starter ERA, FIP, K%, BB%, K-BB%        (starter_history.py — rolling, PIT-safe)
  bullpen talent / availability / fatigue
  PIT park (park_factor_pit)              (v8's static table is NOT reused for v9)
  feature availability flags per row

REQUIRES PROVIDER WORK (P0 steps K/L/J — freeze on completion):
  PIT weather: temperature, humidity, pressure, wind, roof
  projected lineup strength; confirmed lineup strength
```

Manifest must record: `dataset_hash`, `feature_schema_hash`, source hashes,
git SHA, `created_at`, decision horizon. **Data-quality check before freezing**:
row counts must reconcile with v8's recorded training block (3814/1082/1391);
any drift is investigated BEFORE the freeze, never after an ablation result
depends on it. **The frozen table is input-only until 08-18** — no model may
be selected from it during the window.

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

**Per-run control gate (non-negotiable, every harness run):** each ablation
harness invocation runs the v8 control variant FIRST with v8's exact
configuration and compares it against v8's own shipped/documented numbers
(call ratio 1.0, hit 0.6081, Brier 0.2464). If the control cannot reproduce
itself on that run, the run is void — results from that run are discarded
regardless of how good a challenger looks. This prevents the whole matrix
from silently drifting on a broken harness.

### 0.5 Pre-register the experiment template and retention rules

Extend the experiment template (already in `docs/RESEARCH_BACKLOG.md`) with a
`registered_threshold` field: hypothesis + success criteria recorded BEFORE the
run, so post-hoc threshold-fishing is structurally impossible. Experiment IDs
follow the existing convention (`mlb-elo-trend-lr-v9-fip`, etc.). Retention rules:

```text
KEEP         consistent OOF improvement + stability + no PIT issue + coverage
REJECT       no OOF improvement, or CI crossing zero widely, or PIT issue
INCONCLUSIVE directionally good but unstable / low coverage — retest later
RETEST       same as INCONCLUSIVE but with a scheduled re-run condition
```

Formalized thresholds (set for the N≈1400 holdout; operator may adjust before
Phase 2 starts — adjusting AFTER results exist is forbidden):

```text
KEEP         ΔBrier < -0.002 AND ≥4/5 folds agree in sign AND
             bootstrap P(better) ≥ 0.90 AND coverage ≥ 90%
REJECT       ΔBrier ≥ 0 (worse) AND ≥3/5 folds agree in sign
INCONCLUSIVE anything in between — nothing is promoted from this state
```

Worked example (from the operator's directive):

```text
FIP:        ΔBrier -0.0031, 4/5 folds better, bootstrap P(better)=0.91, coverage 96%  → KEEP
K-BB%:      ΔBrier -0.0003, 2/5 folds better, CI crosses zero widely                 → INCONCLUSIVE
```

### 0.6 Decide the F-bucket policy (BEFORE Phase 1 runs)

The 2026-08-16 accent fix (§10.1) means "reproduce what v8 actually was" and
"reproduce what the code computes now" can legitimately differ on the
accent-affected rows. This is a policy decision, not a discovery task:
either the frozen control replays v8's historical (buggy) matching, or it
replays fixed matching. **Record the decision in the experiment registry
BEFORE the first Phase-1 gate run** — the gate result is ambiguous until it
exists. Operator decision; this plan defaults to: *historical behavior is the
control* (reproduce v8 as it was; fixed matching is a documented deviation
classified `F` and excluded from the gate's pass/fail numerator), because a
control you silently redefined is not a control.

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

**Acceptance criteria** (proposed for operator sign-off at the gate; derived
from the existing aggregate bands — ≤0.03 hit-delta ≈ per-row |Δprob| ≤ 0.005
at this N, and call-set identity must be exact):

```text
1. call-set identity: reproduced call set == stored call set (call_ratio 1.0)
2. row-level: ≥98% of rows |Δprob| < 0.005 with zero feature deltas
3. every non-exact row carries a taxonomy class + cause; "unclassified" count == 0
4. any class-F rows are governed by the §0.6 policy decision, recorded
```

**Do not "fix" v8's park leak.** The v8 static-park-table PIT defect
(`docs/V8_REPRODUCTION.md`) is part of v8's historical contract. Reproducing v8
means reproducing the leak; v9's `park_factor_pit` is the corrected successor.
Never refit v8 — that destroys the incumbent control. (Standing repo policy:
v8 is frozen and never modified.)

---

## 4. Phase 2 — v9 ablation matrix (one change at a time, in this order)

Each row = one experiment in the registry with pre-registered thresholds,
frozen feature table + v8 reproduced control as the paired incumbent. The
per-run control gate (§0.4) runs inside every harness invocation.

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
frozen events/folds/timestamps as A; retention uses the formalized KEEP/
REJECT/INCONCLUSIVE/RETEST rules from §0.5; economic metrics reported but
never retention-determining. Note the prior v9 numbers are void per CLAUDE.md
(2026-08-13 train/serve parity audit) — nothing pre-08-13 carries forward.

**Abort criteria for the whole v9 track:** if no feature reaches KEEP after
the full matrix, v8 remains champion and the outcome is recorded as a
successful negative research result — do not iterate "one more variant" ad
infinitum; the next step would be the score-distribution work (§8), not
threshold-shopping.

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

## 7. Phase 5 — prospective shadow and promotion governance (the real gate)

The shadow-first chain, no shortcuts:

1. Freeze the surviving v9 configuration (`freeze-production`).
2. Run prospective shadow picks on the flat ledger against live quotes
   (decision payloads carry the same audit fields as v8's).
3. Settled comparison at a pre-registered minimum (≥30 settled v9 picks, or
   the operator's chosen N — record the N BEFORE the shadow starts, so it
   can't be moved after results arrive).
4. Paired `compare-champion` vs v8 on the shadow window: proper-score delta
   + date-cluster bootstrap P(better), economic metrics secondary.
5. On the pre-registered criteria: status becomes `PROMOTION_CANDIDATE` and
   the operator decides — promotion is never automatic and never happens
   without this chain. A v9 that wins the ablation matrix but loses the
   prospective shadow is NOT promoted; that result gets written up, not
   papered over.

## 8. Phase 6 — MLB score distribution, then totals rebuild (parallel track)

Runs in parallel with Phase 5's shadow window; does not block the ML
promotion path and is not blocked by it.

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

## 9. Post-MLB queue (in order)

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

## 10. Promotion state (unchanged, and frozen through burn-in)

| Model | Decision |
|---|---|
| MLB v8 | KEEP incumbent/control |
| MLB v9 | Not yet eligible |
| MLB spread v3 | Keep baseline |
| MLB totals v3 | Rebuild (§8) |
| WNBA v4 | Keep |
| WNBA v5 | Queued challenger |
| NBA v4 / NFL v4 | Keep (NFL calibrator challenger next) |
| Tennis v1 | Keep |
| Soccer pooled | Keep temporarily; league-split replaces the research direction |
| CS2/Valorant/LoL/Dota2/R6 v6 | Keep |
| KBO/NPB v2 | Keep as controls |

No promotion during burn-in, full stop.

---

## 11. Timeline (estimates; recomputed as phases land)

```text
08-17 → 08-18   Phase 0 prep (row-parity report, evaluator, freeze, policies)     ~1-2 days
08-18            Phase 1 burn-in acceptance + v8 row-level gate                    ~1 day
08-19 → 08-31    Phase 2 ablation matrix A–N (14 variants × walk-forward+bootstrap)  ~2 weeks
09-01 → 09-06    Phase 3 v9-LR / XGB + Phase 4 calibration                         ~1 week
09-07 → 09-08    Phase 5.1 freeze + shadow start                                  —
09-07 → 09-21    Phase 6 score distribution + totals rebuild (parallel)           ~2 weeks
09-08 → 10-xx    Phase 5.2-5.5 prospective shadow accumulates ≥30 settled picks   calendar-bound
on criteria      Phase 5.5 paired comparison → PROMOTION_CANDIDATE → operator     —
```

The shadow phase is calendar-bound, not effort-bound: ≥30 settled picks at
~1-2 qualifying picks/day means promotion consideration lands weeks after
shadow start. That is the point — the ablation matrix is necessary but not
sufficient evidence for promotion.

## 12. Reporting cadence

One short paragraph per ablation: verdict letter (KEEP/REJECT/INCONCLUSIVE),
the retention-table numbers, and the next experiment. No long reports until
the Phase-2 matrix completes, at which point one consolidated write-up goes
into `docs/RESEARCH_BACKLOG.md`'s P0 section with every registry ID.

## 13. Risks, open questions, and reconciliation items

**13.1 Frozen-contract vs. the 2026-08-16 accent fix.** Commit `287d979`
changed `_normalize_name` so accented starter names now match across sources
where they previously failed. For live v8 serving this is a behavior change;
for the reproduction gate it means "what v8 actually was" (historical rows
computed with the buggy matching) vs. "what the code computes now" can
legitimately differ on exactly the affected rows (José Soriano/Jesús Luzardo
class). Governed by the §0.6 policy decision, made BEFORE the Phase-1 gate
run. Default recommendation: historical behavior is the control; fixed
matching is a classified, excluded deviation. Not a silent fold-in either way.

**13.2 Main is 10 commits past the tag.** `37be479` → `69bd131` includes this
session's bugfixes (accent normalization ×2, 3.11-syntax repair, ESPN ID
capture), inert additions (line-movement shadow module, property tests), and
docs. None changed champions, scheduler behavior, runtime schema, or ledger
semantics; the launchd jobs execute from the working tree, so the fixes are
live. If strict tag-freeze is preferred, the research worktree can pin
`37be479` as the reproduction SHA and compare against it explicitly — the
pin-and-replay harness makes either choice explicit. Flag, don't bury.

**13.3 Sample sizes.** Row-parity ground truth is the full v8 ledger footprint
(flat 133 settled + main 5 settled + open rows); the 1391-row holdout is the
ablation evaluation set. The §0.5 thresholds were set with N≈1400 in mind —
a ΔBrier of ±0.003 at this N is near the noise floor, which is why KEEP
demands multiple folds AND bootstrap agreement AND coverage, not a single
delta. If the operator changes N or the thresholds, that happens before
Phase 2, in the registry, never after.

**13.4 Do-not-touch list for the worktree.** The research worktree must never
write to the runtime root, never call promotion/ledger-mutation commands, and
never load its own launchd jobs. Read-only against live data; writes confined
to its own checkout and scratch paths.

**13.5 Worktree sync is itself a lineage event.** The §0.1 merge changes the
research branch's code lineage; record the before/after SHAs in the registry
so any post-sync result can be attributed or voided precisely.
