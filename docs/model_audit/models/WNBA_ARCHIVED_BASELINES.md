# Archived Research Material: `origin/rebuild/wnba-v1`

**Purpose:** research-material evaluation, not a merge decision. This
document inventories what actually exists in the historical
`origin/rebuild/wnba-v1` branch (pinned by tag
`archive/model-source-rebuild-wnba-v1-95c7dcc2`, commit `95c7dcc2`) for the
three files explicitly excluded when the WNBA data foundation was merged
into `main`: `src/model_prediction/rebuild/wnba/features.py`,
`horizon_builder.py`, and `baselines.py`. All content below was read via
`git show origin/rebuild/wnba-v1:<path>` — the branch was not checked out
or merged, per this audit's constraints.

**Audit date:** 2026-08-11. **Repo state:** `audit/model-feature-
reconciliation-v1` @ `826c893` (origin/main).

## Why these three files, specifically, were excluded — found directly, not inferred

The merge commit that ported the WNBA data-foundation layer into `main`
(`5aea31e`, "rebuild: wire WNBA data foundation into rebuild-data (#10)")
states the reason explicitly in its own commit message:

> Scoped to data ingestion only (foundation/normalize/store/audit/pit/
> contracts/time) -- deliberately excludes the source branch's
> features.py/horizon_builder.py/baselines.py (feature-engineering and
> model-baseline code, a different concern from backfill/audit, left for a
> separate rebuild-model decision rather than folded in silently here).

This is a load-bearing fact for the recommendations below: **the exclusion
was a scope decision at merge time, not a rejection of the code's
quality**, and the same commit message documents that "All 13 of the
branch's own data-foundation tests passed unmodified against current
main's provider/identity layer — no rights-vocabulary or provider-signature
drift here."

## What's already in `main` vs. what's still archive-only

Diffed directly (`diff <(git show origin/rebuild/wnba-v1:path) path`) for
every file in `src/model_prediction/rebuild/wnba/` on both sides:

| File | In `main`? | Identical to branch? |
|---|---|---|
| `__init__.py` | Yes | Docstring extended in `main`; otherwise same |
| `audit.py` | Yes | Byte-identical |
| `contracts.py` | Yes | Byte-identical |
| `foundation.py` | Yes | Byte-identical |
| `normalize.py` | Yes | Byte-identical |
| `pit.py` | Yes | Byte-identical |
| `store.py` | Yes | Byte-identical |
| `time.py` | Yes | Byte-identical |
| `cli_adapter.py` | Yes | **main-only** — built independently after the port, does not exist on the branch |
| `features.py` | **No** | Archive-only (this document) |
| `horizon_builder.py` | **No** | Archive-only (this document) |
| `baselines.py` | **No** | Archive-only (this document) |

This matters directly for the RECOVER/DO_NOT_RECOVER calls below: every
non-sklearn/non-polars dependency the three excluded files import from the
rest of `rebuild/wnba/` and `rebuild/` (`WNBANormalizedStore`,
`eligible_prior_team_games`, `sports_event_date`, `HORIZONS`/
`HORIZON_HOURS_BEFORE`, `canonical_json`/`dataframe_schema_hash`,
`FeatureStore`, `brier_score`/`log_loss`, `EloModel`) is **already present
in `main`, verified by direct grep of each name in the current tree**, and
`pit.py`/`store.py`/`contracts.py` etc. are byte-identical to what these
files were originally written against. This is a materially lower
adaptation cost than a typical "reattach an old branch" situation.

`polars`, `numpy`, and `scikit-learn` are declared in `pyproject.toml`
(`polars>=1.40,<2`, `numpy>=2,<3`, `scikit-learn>=1.5,<2`) but are not
installed in this docs-only audit worktree/venv — consistent with the
pre-existing `outputs/rebuild/audit/elo_leakage_trace.py` script's own
comment about the same limitation. This card could not execute or
import-check any of the three files; the RECOVER/AUDIT_ONLY judgments below
are based on static reading only.

---

## `features.py` (158 lines) — RECOVER

**What it computes:** `build_team_form_snapshot()` — PIT-safe rolling team
form for one team as of a decision timestamp, over `WINDOWS = (5, 10, 20)`
games plus a `season` window, across `METRICS = ("ortg", "drtg", "netrtg",
"pace", "efg", "tov_pct", "orb_pct", "ft_rate")`. Possession counts follow
the standard basketball-analytics formula (`FGA + 0.44*FTA - OREB + TOV`,
symmetric for the opponent side), `ortg`/`drtg` are points per 100
estimated possessions, `pace` is the average of both teams' estimated
possessions, `efg` is `(FGM + 0.5*3PM) / FGA`, `tov_pct` and `orb_pct` and
`ft_rate` are the standard Four-Factors ratios. This exactly matches the
task brief's description and `docs/MODEL_IMPROVEMENTS.md` §7's rank-3
feature-group proposal ("Opponent-adjusted pace, eFG%, TOV%, OREB%, FTA
rate on 5/10/season horizons with reliability shrinkage") — **with one
honest gap**: this code computes plain rolling means, not
opponent-adjusted or shrinkage-adjusted values. The roadmap's
"opponent-adjusted" and "reliability shrinkage" qualifiers are not yet
implemented here; that's real remaining work, not a discrepancy in this
audit.

**PIT-safety:** Strong. `build_team_form_snapshot` requires a
timezone-aware `decision_time_utc`, converts to UTC, and calls
`eligible_prior_team_games` (from the already-in-`main` `pit.py`) which
itself filters both the games table and the team-box table to
`observed_at <= decision` before taking the latest-known state per
`(event_id, team_id)` and additionally requiring `pit_eligible` and
`event_start < decision`. `pit.py`'s own comment flags a real, specific
correctness concern it defends against: "Latest-as-of state must be
selected before checking completed or PIT eligibility. Otherwise an older
FINAL survives a later correction to postponed/not-completed" — i.e. it
explicitly protects against exactly the kind of same-day/late-correction
leak class `CLAUDE.md`'s "one invariant that matters most" section warns
about project-wide. `features.py` itself does not re-implement any of this
filtering; it consumes `pit.py`'s already-filtered frame, which is the
correct separation of concerns.

**Code quality:** High. Every returned snapshot carries
`source_raw_snapshot_hashes` and a `source_manifest_hash` (content-addressed
provenance of exactly which raw captures produced this number), a
`status: "AVAILABLE" | "UNAVAILABLE"` flag rather than silently returning
partial/zero data, and `sample_size`. Uses `polars` idiomatically
(`group_by(...).last()` for latest-as-of joins). No obvious bugs found on
read.

**Adaptation cost to current `main`'s data contracts:** Low. Its only
non-stdlib imports are `polars`, `model_prediction.rebuild.providers.base.
canonical_json` (present, byte-verified signature match), and
`.pit.eligible_prior_team_games` (present, byte-identical to the branch
version this file was written against). No adaptation to team/game schema
should be required beyond whatever normal drift has occurred in
`normalize.py`'s output schema since `95c7dcc2` — and `normalize.py` is
itself byte-identical between branch and `main`, so there has been none.

**Verdict: RECOVER.** This is the actual Four-Factors feature engine
`docs/MODEL_IMPROVEMENTS.md` §7 calls for as WNBA roadmap rank #3, already
built to the project's PIT and provenance standards, with its dependencies
already merged and unchanged. The missing opponent-adjustment/shrinkage
layer is real future work, not a reason to discard the rolling-window
plumbing underneath it.

---

## `horizon_builder.py` (395 lines) — RECOVER

**What it computes:** `build_wnba_replay_features()` /
`build_wnba_live_features()` — the single shared feature-build path for
both historical replay and live serving (mirroring this project's existing
"one code path for both" philosophy, e.g.
`matchup_player_availability_from_payloads` in the live availability
feature). For a `(game_date, horizon)` pair, it resolves each target
game's actual decision cutoff (`event_start_utc - HORIZON_HOURS_BEFORE
[horizon]`, horizons `"early"/"mid"/"late"` = 36h/6h/1h before tipoff, from
the already-in-`main` `rebuild/horizons.py`), builds both teams' `features.
py` snapshots as of that cutoff, and assembles one feature row per game.

**PIT-safety — this is the most rigorous part of the whole archived
module.** `_target_as_of_cutoff` handles the specific, real problem that
"a postponed game's start can itself change": it iterates (capped at 5
attempts) re-resolving the target row from schedule state known as of the
*previous* cutoff estimate, until the resolved start time stops moving,
and fails closed (`raise ValueError(... "did not stabilize at a PIT
cutoff" ...)`) if it never converges rather than silently using a stale or
wrong cutoff. This is precisely the class of bug `CLAUDE.md` calls out
project-wide ("Check the *order* two timestamp captures happen in... A
live-only signal can never be used for a decision time in the past") —
here proactively engineered against for a schedule-change scenario that
has bitten this project before in other sports (KBO/NPB timestamp-ordering,
per `CLAUDE.md`'s own incident list). For live mode specifically, it also
enforces `live_knowledge_time < decision_time` → skip
(`decision_cutoff_not_reached`), so a live call can't be answered before
its own horizon cutoff has actually arrived.

**Rights/provenance gating — a real, hard-fail safety property.**
`_assert_research_source_provenance()` runs before any PIT filtering and
requires every source row to carry `availability_basis ==
"capture_time_only"`, `commercial_use_status == "unresolved"`, and
**`production_allowed == False`** — i.e. the code will raise rather than
silently proceed if a row claims to be production-cleared. Combined with
`baselines.py`'s identical gate (below), this means the entire archived
pipeline is structurally incapable of producing a result it would consider
production-safe, by design, until the upstream SportsDataverse/ESPN
commercial-use question referenced in `docs/MODEL_IMPROVEMENTS.md` §7
("Public WNBA advanced data exists; verify use and archival terms") is
actually resolved. That's a feature of this code, not a defect — but it
does mean recovering these files does not, by itself, unblock anything for
production; it only unblocks *research*.

**Reproducibility engineering:** Every feature snapshot is content-hashed
(`hashlib.sha256(canonical_json(...))`), persisted via the already-in-`main`
`FeatureStore.write_snapshot`, then immediately re-read back and compared
(`if not frame.equals(persisted): raise ValueError(...)`) — a genuine
write-then-verify round trip, not an assumed-correct write. Schema
manifests are written with an atomic tmp-file + `os.link` pattern that
raises on any conflicting concurrent write of different content
(`_write_schema_manifest`). This matches the "immutable hashes" and
"content-addressed storage" properties `current_system_audit.md` credits
the rebuild branch's infrastructure with generally.

**Code quality:** High, with one piece of genuine complexity worth
flagging for whoever eventually re-integrates this: the 5-attempt
cutoff-stabilization loop in `_target_as_of_cutoff` is subtle enough that
it deserves its own targeted unit tests for the postponement-drift case
specifically (a game whose start moves more than once) — the branch's own
`tests/rebuild/test_wnba_features.py` (148 lines) exists but this card did
not confirm from the diff alone whether that specific multi-move scenario
is covered; that's a concrete follow-up for whoever recovers this file,
not a defect finding.

**Adaptation cost:** Low, same reasoning as `features.py` — its imports
(`rebuild.horizons.{HORIZON_HOURS_BEFORE,HORIZONS}`,
`rebuild.providers.base.{canonical_json,dataframe_schema_hash}`,
`rebuild.storage.FeatureStore`, local `.features`, `.store.
WNBANormalizedStore`, `.time.sports_event_date`) are all present in `main`
and, for the local-package ones, byte-identical to what this file expects.

**Verdict: RECOVER.** This is the file that turns `features.py`'s
single-team snapshots into an actual walk-forward-safe, replay/live-shared
dataset — exactly the "horizon builder" role its name implies, built with
unusually careful postponement handling and real write-verify
reproducibility discipline. Recommend recovering it together with
`features.py` (it has a hard dependency on it) rather than separately.

---

## `baselines.py` (605 lines) — AUDIT_ONLY

**What it computes — confirmed against the task brief's list, all eight
present:**

1. `constant_probability` — fixed 0.5.
2. `expanding_home_base_probability` — the training fold's realized
   home-win rate.
3. `elo` — via `rebuild.basic_elo.EloModel` (already in `main`,
   `k_factor=20.0, home_advantage=65.0, initial_rating=1500.0` per the
   `model_parameters` block — note this is a **different** home-advantage
   constant than the live serving path's `ELO_CONFIG["wnba"]["home_
   advantage"] = 60.0` in `features/elo_ratings.py`; a real, if minor,
   discrepancy between this research module and the production Elo config
   that a future integrator should reconcile, not silently inherit).
4. `regularized_logistic` — `LogisticRegression(C=1.0, solver="lbfgs",
   max_iter=2000)` in a `StandardScaler` pipeline, trained on the 16
   season-window Four-Factors features from `features.py` (home+away ×
   8 metrics).
5. `linear_margin` — `Ridge(alpha=10.0)` predicting home-minus-away score
   margin, converted to a win probability via a Gaussian CDF on the margin
   residual distribution.
6. `linear_total` (total/Ridge) — `Ridge(alpha=10.0)` predicting the game
   total.
7. **margin-residual-variance**: `residual_std` from the margin Ridge's
   own training residuals (floored at 1.0 to avoid degenerate variance).
8. **margin-total-covariance**: the fold's residual covariance matrix
   between margin and total residuals, eigenvalue-floored for
   positive-semi-definiteness (`np.linalg.eigh` + `np.maximum(eigenvalues,
   1e-6)` reconstruction) — then used to derive an implied home/away score
   joint distribution (`joint_residual_family:
   "bivariate_gaussian_margin_total"`) with its own home/away covariance
   and correlation, which is genuinely more sophisticated than a naive
   independent-margin/total assumption.
9. **Chronological date-fold research machinery**:
   `chronological_date_folds()` — expanding-window folds over *whole
   calendar dates* (never split within a date), `n_splits=4` default,
   `min_train_dates=3` — and `evaluate_research_baselines()`, which
   fits/evaluates per fold and asserts every model reports over "one
   common OOF sample" (`if any(model["n"] != same_sample_n for model in
   model_reports.values()): raise RuntimeError(...)`) — a real guard
   against silently comparing models on different subsets.

**Rights-gating, again enforced as a hard fail, not a label:**
`_validate_frame()` re-checks (independently of `horizon_builder.py`'s
gate) that every row has `production_allowed == False`,
`commercial_use_status == "unresolved"`, `availability_basis ==
"capture_time_only"`, and non-empty provenance hash columns — raising
`ValueError` otherwise. `load_research_baseline_dataset()` additionally
requires final-score labels to be "observed after event start" and to
match the feature snapshot's team/date identity exactly before joining,
and independently re-derives `computed_hash` for each snapshot and
compares it to the claimed `snapshot_hash` before trusting it — i.e. it
does not trust its own filename/pointer, it re-verifies content. The module
docstring is explicit about what this buys: "This module evaluates
controls; it does not create a deployable challenger... every result is
qualification-blocked." The written report always carries `status:
"RESEARCH_ONLY"`, `qualification_status: "BLOCKED"`,
`production_allowed: False`, and an explicit `qualification_blockers` list
naming the two real reasons (capture-time-only historical data is not
retrospective PIT evidence; SportsDataverse/ESPN commercial-use rights are
unresolved).

**PIT-safety of the research design itself:** The chronological-date-fold
splitter never splits within a date and enforces `fold.train_end <
fold.validation_start` before fitting (`if fold.train_end >=
fold.validation_start: raise ValueError("... leaks validation dates into
training")`) — a real leakage guard, not just a naming convention.
Combined with `features.py`'s per-decision-time PIT filtering, the
resulting OOF comparison is genuinely walk-forward in spirit. The one
caveat, which the module's own docstring names honestly, is that the
*underlying historical captures* are "capture-time-only" — meaning the
raw SportsDataverse/ESPN data this trains against was captured once
(presumably after the fact, for backfill purposes) rather than genuinely
observed prospectively game-by-game, so even a leakage-guarded fold split
over that data is not the same evidentiary strength as a truly prospective
walk-forward run. This module is honest about that distinction
(`qualification_blockers[0]`); it is not claiming more than it has.

**Code quality:** High, same standard as the other two files — atomic
write-once artifact persistence (`write_research_baseline_artifacts`, same
tmp+`os.link` pattern, with `os.fsync` before linking), explicit dataclass
result types, no bare excepts. The heaviest dependency surface of the
three files: `scikit-learn` (`LogisticRegression`, `Ridge`, `Pipeline`,
`StandardScaler`) plus `numpy` in addition to `polars` — none installed in
this docs-only worktree, so none of this was actually executed or
import-checked here.

**Adaptation cost:** Low for the same structural reason as the other two
files (`rebuild.basic_elo.EloModel`, `rebuild.storage.FeatureStore`,
`rebuild.validation.{brier_score,log_loss}`, `rebuild.providers.base.
{canonical_json,dataframe_schema_hash}`, local `.store.
WNBANormalizedStore` all verified present in `main` with matching
signatures) — **except** the Elo home-advantage constant mismatch noted
above (item 3), which is a small but real thing to reconcile before
treating this module's Elo baseline numbers as comparable to the live
`elo_probability` feature's numbers.

**Verdict: AUDIT_ONLY**, not RECOVER-as-is, for a reason distinct from
`features.py`/`horizon_builder.py`: this module is explicitly a
**comparison/evaluation harness that produces an evidence report, not a
deployable artifact** ("does not create a deployable challenger" is in its
own docstring), and it is hard-gated to research-only data whose
commercial-use status is unresolved. Recovering it verbatim would be safe
in the sense that its own guards would prevent it from producing anything
that could be mistaken for production-ready — but it should not be
recovered and then *left dormant* the way several other features in this
project are ("wired but never consumed") without an explicit owner,
because unlike a dormant feature-computation function, a baseline-
comparison harness has no value sitting unexecuted. Recommend: read it
fully (done, this document), keep it as reference for how a future
WNBA-Four-Factors challenger's OOF evaluation *should* be structured
(the date-fold splitter and common-OOF-sample guard in particular are
worth reusing patterns, not just this file's own output), but do not
silently re-merge it into `main` without (a) resolving or explicitly
re-scoping the commercial-use-rights blocker it itself refuses to bypass,
and (b) fixing the Elo home-advantage constant mismatch against the live
`ELO_CONFIG`.

---

## Summary table

| File | Verdict | Primary reason |
|---|---|---|
| `features.py` | **RECOVER** | PIT-safe, well-provenanced, all dependencies already merged into `main` unchanged; implements roadmap item §7 rank #3's core plumbing |
| `horizon_builder.py` | **RECOVER** | Same dependency story; adds the hardest-to-get-right part (postponement-safe cutoff resolution, write-verify persistence) correctly and defensively |
| `baselines.py` | **AUDIT_ONLY** | Real, well-built comparison harness, but self-scoped to research-only/qualification-blocked by design; recover only alongside an explicit rights resolution and the Elo-constant reconciliation, not silently |

No file in this set warrants **DO_NOT_RECOVER**. All three are real,
carefully written, PIT-conscious code with verified-unchanged dependencies
already living in `main` — the exclusion at merge time was correctly
scoped as "different concern, separate decision," not a quality rejection,
and nothing found in this reading contradicts that framing.
