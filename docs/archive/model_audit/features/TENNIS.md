# Feature audit: TENNIS

Audited 2026-08-11 against branch `audit/model-feature-reconciliation-v1`
(based on `origin/main` @ `826c89342bd2f3f1ea44fc29eaf20fad520dc5d5`).
Read-only audit: `config/tested_features.json` and `docs/FEATURE_REGISTRY.md`
were read, not edited — corrections below are recommendations for whatever
process consolidates those files next.

## Headline finding: the `tennis_surface` registry entry is factually wrong, not just stale

**Registry claim** (`config/tested_features.json`, `features[]` entry for
`tennis_surface`):

```json
{
  "evidence_grade": "A",
  "file": "src/model_prediction/features/tennis_surface.py",
  "name": "tennis_surface",
  "notes": "No active tennis model exists. Tennis is deferred per docs/PROJECT_STATUS.md. Untestable, not rejected.",
  "retest_when": "If and only if a tennis model is revived.",
  "sports": ["TENNIS"],
  "status": "registered_orphan",
  "tested_on": "n/a",
  "verdict": "exclude"
}
```

Mirrored in `docs/FEATURE_REGISTRY.md:55`:
`| tennis_surface | TENNIS | **exclude** | No active tennis model consuming it. |`

**This is false on both of its load-bearing claims**, verified directly against
current source and config, not against any other doc:

1. **"No active tennis model exists" is wrong.** `config/model.yaml:318-345`
   (TENNIS block): `status: shadow_qualified`, `family: surface_blended_elo`,
   `active_research_version: tennis-surface-elo-v1`. The
   `qualification_override_reason` field records a real walk-forward result
   run 2026-08-03 (`validation.qualify_tennis_elo_model`): **65.5% hit rate on
   4,269 locked-holdout calls, +1,070.7 units at -110, every qualifying month
   positive** — "the strongest result of any sport checked this session"
   per that same config comment. This model is wired into the live daily
   forecast pipeline (`tennis_forward.py::build_tennis_slate`, called from
   `cli.py`) and produces real, sized `QUALIFIED_SHADOW_CALL` rows into both
   the Flat and (per operator override) Main ledgers whenever a contract
   clears `min_edge` (`config/model.yaml:346`, `min_edge: 0.05`,
   `research_confidence_gate: 0.037239`). "Deferred" and "no active model"
   describe a state that predates this promotion and is no longer true.

2. **"No active tennis model consuming it" (surface) is wrong about the
   underlying signal, and imprecise about the mechanism.** The active
   model, `TennisModel` (`src/model_prediction/models/tennis.py`), builds a
   **per-surface Elo book and blends it into every match probability at a
   fixed 60% weight**:

   - `build_elo` (`models/tennis.py:59-88`) constructs both an `overall`
     Elo book and a `by_surface: dict[(player, surface), float]` book from
     the same chronological match loop (lines 69, 79-85).
   - `match_probability` (`models/tennis.py:90-106`) computes each player's
     blended rating as
     `surface_weight * by_surface[(player, surface)] + (1 - surface_weight) * overall[player]`
     (lines 100-101, 104-105), with **`surface_weight: float = 0.6`** as the
     function's own default (line 97) — i.e. surface Elo carries *more*
     weight than overall Elo in every prediction.
   - `predict_games` (`models/tennis.py:112-161`) calls `match_probability`
     for every upcoming match using `match.surface` (populated from
     `UpcomingMatch.surface`, itself derived per-match by
     `data_sources/espn.py::_infer_tennis_surface`, `espn.py:291-297`), and
     echoes the per-surface Elo values into `feature_basis` (lines 148-149)
     for audit/provenance.
   - This is exercised in production every day: `tennis_forward.py:212-221`
     calls `tennis_model()` and `model.predict_games(tour_history,
     upcoming_by_tour[tour])` inside `build_tennis_slate`, which is the
     function `cli.py` calls to build the live tennis research/Main slate.

   The narrow technical nuance: the specific **registered feature function**
   named `tennis_surface` (`features/tennis_surface.py:59-72`,
   `@register_feature("tennis_surface")`) — a win-rate-by-surface snapshot,
   a *different* implementation from the Elo blend above — is genuinely not
   consumed by the generic ML feature framework (`learned_forward.py`'s
   `_compute_features`/`wanted = set(artifact.raw...feature_names...)`
   dispatch, grep-confirmed: `tennis_surface` appears nowhere in
   `learned_forward.py`). That framework is used by the *other* family of
   models (elo_probability/trend_gap logistic regressions for MLB/NBA/WNBA/
   NFL/soccer) — `TennisModel` never routes through it at all; it computes
   its own surface-Elo signal inline from raw match dicts. So on the single
   narrowest possible reading — "is this exact registered feature *function*
   wired into `_compute_features`'s `wanted` set" — the registry's "exclude"
   verdict for that literal artifact is defensible. But that is not what the
   registry's stated reason says, and it is not what a reader would
   conclude from "no active tennis model consuming it" — the active,
   shadow-qualified, real-money-adjacent tennis model's single most
   important structural signal after Elo itself *is* surface-specific,
   weighted at 60%.

**Recommended correction for the consolidated registry** (not applied here —
`config/tested_features.json`/`docs/FEATURE_REGISTRY.md` are out of scope
for this audit per instructions):

> `tennis_surface` (registered feature, `features/tennis_surface.py`) is
> correctly unconsumed by the generic `learned_forward.py` feature
> pipeline — no logistic-regression tennis model exists to consume it.
> However, surface-awareness itself is **not** absent from tennis: the
> active `tennis-surface-elo-v1` model (`shadow_qualified`,
> `models/tennis.py`) computes its own inline per-surface Elo book and
> blends it at 60% weight into every match probability. Either (a) retire
> the orphaned `features/tennis_surface.py` win-rate-snapshot function as
> dead/duplicate code (recommended — it duplicates a concept the model
> already gets a stronger, Elo-based version of, and its only two fields
> beyond win-rate — `serve_return_status` and `recent_win_rate` — are
> either hardcoded to `"unavailable_from_source"` or unused downstream), or
> (b) if it is kept as a documented research candidate, correct its verdict
> reason to describe what is actually true: surface IS consumed by an
> active model, just not through this specific feature function.

## Feature entries

### `tennis_surface_elo` (inline, not a registered feature — the real surface signal)

- **Name**: no formal feature-registry name; implemented directly inside
  `TennisModel.build_elo`/`match_probability`
  (`src/model_prediction/models/tennis.py:59-106`).
- **Model(s) using it**: `tennis-surface-elo-v1` (`TennisModel`, the only
  tennis model in the project) — every match probability it produces.
- **Source location**: `src/model_prediction/models/tennis.py:69` (`by_surface`
  book construction), `:97-106` (60/40 blend in `match_probability`).
- **Provider**: surface label comes from `data_sources/espn.py:291-297`
  (`_infer_tennis_surface`) — a tournament-name string-match heuristic
  against hardcoded clay/grass keyword lists (`espn.py:279-289`); anything
  unmatched defaults to `"Hard"`. ESPN's tennis scoreboard carries no
  first-party surface field at all (comment at `espn.py:277-278`).
- **Formula**: `blend(player) = 0.6 * EloSurface[player, surface] + 0.4 * EloOverall[player]`,
  Elo updated with standard logistic expected-score, `K_FACTOR = 32.0`
  (`models/tennis.py:18`), applied identically to both the surface book and
  the overall book from the same chronological match loop.
- **Expected sign**: positive — a player with a materially stronger
  surface-specific record than their overall record should be favored more
  on that surface than overall-Elo alone would suggest; this is the whole
  point of blending in a surface-specific rating.
- **PIT-safe?**: Yes. `build_elo` only ever processes `history`/`matches`
  built from `_tennis_history_before(data_root, game_date)`
  (`tennis_forward.py:41-73`), which reads `data/processed/tennis/games.jsonl`
  filtered to `event_start_utc < midnight-ET-at-start-of(as_of_date)`
  (`tennis_forward.py:61`, the same PIT cutoff convention as
  `FeatureStore.games_before` elsewhere in the project). Surface for a
  *historical* match is captured once, at ingest time, from the same
  tournament-name heuristic (`data_sources/espn.py:204-268`,
  `completed_tennis_singles_matches`) — it is not re-derived at prediction
  time from anything that could change.
- **Train/serve parity?**: Yes. `validation.qualify_tennis_elo_model`
  (`validation.py:1510-1660`) instantiates the same `TennisModel` class and
  calls the same `build_elo`/`predict_games` methods used by
  `tennis_forward.py::build_tennis_slate` in live serving — no
  reimplementation. One real, documented asymmetry: the qualification
  walk-forward pools ATP+WTA history for the confidence-threshold learning
  step in a way that mirrors production's per-tour split (`tennis_forward.py:213-221`
  explicitly builds Elo "per tour, on that tour's own history only ...
  blending them would corrupt ratings"), and `qualify_tennis_elo_model`'s
  per-row `UpcomingMatch(..., str(row.get("league", "ATP")))` construction
  keeps that same tour separation implicitly via the `league`/tour field on
  each row — confirmed consistent, not a parity gap.
- **Coverage**: every match where `_infer_tennis_surface` returns a value —
  effectively 100% (defaults to `"Hard"` when the tournament name doesn't
  match a known clay/grass keyword, so there is no missing/null surface
  state, only a *possibly-wrong* one for an unrecognized tournament name).
- **Missingness behavior**: never missing by construction (default-to-Hard
  fail-open, not fail-closed) — this is a real, unquantified source of
  surface mislabeling for any clay/grass tournament whose name isn't in the
  hardcoded hint lists (`espn.py:279-289`), rather than a hard failure that
  would surface the gap. No accuracy impact has been measured for this
  specific failure mode in this audit.
- **Correlation notes**: by construction correlated with overall Elo (same
  underlying match results, just filtered to one surface) — the 60/40 blend
  is a deliberate partial-pooling choice, not evidence the two signals are
  independent.
- **Coefficient/importance**: not a fitted coefficient — `surface_weight =
  0.6` is a hardcoded constant (`models/tennis.py:97`), not derived from a
  regression or grid search anywhere in this codebase (no other reference
  to `surface_weight` exists in `src/` outside this one default-parameter
  declaration).
- **Ablation deltas**: none exist. `qualify_tennis_elo_model` evaluates the
  model as shipped (60/40 blend baked in); there is no walk-forward run in
  this repo comparing 60/40 against overall-Elo-only or against other blend
  ratios. The 65.5%/+1,070.7u result is real and locked-holdout, but it is a
  single-configuration result, not evidence the 60/40 split is optimal.
- **Calibration impact**: not isolated. `qualify_tennis_elo_model` grades
  hit-rate/units on the model's final blended probability only; no
  calibration diagnostic (Brier, calibration slope/intercept, reliability
  buckets) is computed for tennis anywhere in `validation.py` or
  `rebuild/calibration.py` — the generic `calibration_intercept_slope`/
  `cross_fit_calibration_eval` functions in `rebuild/calibration.py:220-320`
  are defined but have zero call sites in the repo (`grep -rln
  calibration_intercept_slope src/` returns only the defining file).
- **Known bugs**: none found in the Elo/blend math itself — the 2026-07-31
  full-project logic review (`DEBUG.md:1888-1889`) explicitly traced
  "tennis's surface-blend weighting" and found it correct. The real issue is
  the registry misdescription documented above, not the model code.
- **Verdict: `KEEP_CORE`** — this is a structurally load-bearing part of the
  active, walk-forward-qualified tennis model (60% of the blended rating),
  not an optional add-on; retain as part of the incumbent architecture per
  the standing instruction not to rebuild `tennis-surface-elo-v1` from
  zero. Recommend future work: (a) a real ablation (surface-weight-only vs.
  full 60/40) to confirm the blend ratio earns its keep rather than being an
  untested magic number, (b) a calibration diagnostic (currently nonexistent
  for tennis), (c) tightening `_infer_tennis_surface`'s keyword-list
  fail-open default.

### `tennis_surface` (registered feature function, `features/tennis_surface.py`)

- **Name**: `tennis_surface` (`@register_feature("tennis_surface")`,
  `features/tennis_surface.py:59`).
- **Model(s) using it**: none. Not `TennisModel` (which never imports or
  calls anything from `features/tennis_surface.py` — confirmed by grep: the
  only reference to the string `"tennis_surface"` in `src/` is the
  registration decorator itself and the module's own import in
  `features/__init__.py:7`). Not any generic ML model either — no
  `feature_names` config anywhere in `config/models/*.json` lists
  `tennis_surface`, and `learned_forward.py::_compute_features`'s dispatch
  has no branch for it.
- **Source location**: `src/model_prediction/features/tennis_surface.py:1-72`.
- **Provider**: reads `data/historical/tennis_matches_all.jsonl` directly
  (`tennis_surface.py:19-32`) — a **different file** from the one
  `TennisModel`'s live path reads (`data/processed/tennis/games.jsonl`, via
  `tennis_forward.py::_tennis_history_before`). This module has its own
  separate, PIT-filtered load function (`load_matches`, filters
  `match_date < as_of_date` as a bare date-string comparison, not the
  midnight-ET timestamp cutoff used elsewhere) — a second, parallel, unused
  implementation of "give me tennis history before a date."
- **Formula**: per player, per surface (`Hard`/`Clay`/`Grass`/`Carpet`):
  win/loss counts and `win_rate = wins / (wins + losses)`, plus a
  last-10-matches `recent_win_rate` across all surfaces combined
  (`surface_profile`, `tennis_surface.py:35-56`). `serve_return_status` is
  hardcoded to the literal string `"unavailable_from_source"` for every
  player (line 55) — a stub field, not real data.
- **Expected sign**: would be positive if consumed (higher surface win rate
  → higher win probability on that surface) — moot since nothing reads it.
- **PIT-safe?**: the loader itself is PIT-respecting (date-string filter),
  but this is unverifiable end-to-end since no live prediction path
  actually calls it.
- **Train/serve parity?**: N/A — never served, never trained against.
- **Coverage**: N/A — never invoked outside its own module.
- **Missingness behavior**: `win_rate`/`recent_win_rate` are `None` when a
  player has zero matches on a surface — a real fail-safe pattern, but
  again unexercised by any consumer.
- **Correlation notes**: would be highly correlated with `tennis_surface_elo`
  above (same underlying match outcomes, coarser aggregation — raw win rate
  vs. Elo) if both were ever used together.
- **Coefficient/importance**: none — never fit into any model.
- **Ablation deltas**: none — never tested.
- **Calibration impact**: none — never in a served prediction.
- **Known bugs**: genuinely orphaned/dead code — reads a different data
  file than the live model, uses a different PIT-cutoff mechanism than the
  rest of the project's convention, and has a stub field
  (`serve_return_status`) that was seemingly never finished. Confirmed
  `registered_orphan` status in the registry is accurate for *this specific
  function* (unlike the registry's stated *reason*, which is not — see
  headline finding above).
- **Verdict: `REMOVE`** — genuinely dead code, duplicates a concept the
  active model already implements better (Elo vs. raw win rate), reads a
  stale data path, and its one distinguishing field is an unfilled stub.
  Recommend the consolidated registry either delete this module and its
  registration or, if retained deliberately as a documented research
  candidate for a *future* generic-feature-framework tennis model, correct
  its `notes`/reason field per the headline finding above — do not leave
  "no active tennis model exists" standing as the justification for either
  choice.

## Other tennis parameters worth registry visibility (not currently tracked as "features" at all)

These are structural constants inside `TennisModel`, not registered
features, so they don't have `tested_features.json` entries — flagged here
since the task asked for K-factor/surface-weight/cold-start/minimum-matches
verification specifically.

| constant | value | location | fitted or hardcoded? |
|---|---|---|---|
| `K_FACTOR` | 32.0 | `models/tennis.py:18` | Hardcoded. No grid search, no other reference to `K_FACTOR` anywhere in `src/` besides this declaration and its two use sites inside `build_elo` (lines 84-85). Standard textbook default for Elo, not a project-specific fit. |
| `surface_weight` | 0.6 (60/40 surface/overall blend) | `models/tennis.py:97` | Hardcoded default parameter. No other reference in `src/`. |
| `DEFAULT_ELO` (cold-start) | 1500.0 | `models/tennis.py:17` | Hardcoded, standard Elo convention. Used both for genuinely-unseen players (fail-open cold start) and — per the module's own comment (`models/tennis.py:118-124`) — deliberately made *inert* for prediction purposes: `predict_games` **hard-skips** any match where either player has zero real history (`if not (known_one and known_two): continue`), so a player never actually gets a live prediction while resting on the bare 1500 default. |
| Minimum player match history | Two-tier, not a single constant | `models/tennis.py:118-124` (hard skip at 0 matches) + `cli.py:2055` (`MINIMUM_PLAYER_MATCHES = 10`, gates `model_inputs_valid` for logging at `cli.py:2075`) | Tier 1 (skip entirely below 1 match) is hardcoded in the model. Tier 2 (`10` matches minimum before a contract is treated as having a trustworthy rating) is a separate hardcoded downstream gate, justified in-comment by analogy to `MINIMUM_MONTHLY_CALLS = 10` and soccer's `MINIMUM_TEAM_GAMES = 10` — an internally-consistent convention across sports, not empirically derived for tennis specifically. |

None of these four have ever been subject to a real sensitivity/ablation
check in this repo (confirmed by grep — none appear as a swept parameter in
`validation.py`, `roadmap_challenger.py`, or `production_feature_ablation.py`).
The 65.5%/+1,070.7u qualification result is real for *this exact
configuration*; it is not evidence any individual constant is optimal.

## Data-foundation docstring check (Sackmann vs. TennisMyLife/ESPN)

Verified: this is a real, but **documentation-only**, inconsistency — it does
**not** affect runtime behavior of the active model. Two independent lines of
evidence:

1. **`models/tennis.py`'s docstring is stale.** Line 3: `"Also home of
   TennisPlayerForm, the record the Sackmann CSV loader builds."` — true as
   a description of what `TennisPlayerForm` *was built for*, but that class
   (`models/tennis.py:22-40`) and its loader
   (`data_sources/tennis_sackmann.py::player_form_from_matches`) are **dead
   code in the live path**: `TennisModel.build_elo`/`match_probability`/
   `predict_games` all operate on plain `dict[str, Any]` match rows
   (`winner`/`loser`/`surface`/`match_date` keys), never on a
   `TennisPlayerForm` instance. Grep-confirmed: `TennisPlayerForm` is
   referenced nowhere in `tennis_forward.py`, `validation.py`, or `cli.py` —
   only in its own definition and in `data_sources/tennis_sackmann.py`'s
   import, which itself has no other caller in `src/` (only exercised by
   `tests/test_tennis_sackmann.py`).

2. **The live data foundation is neither "Sackmann" nor "TennisMyLife +
   ESPN" as originally assumed — it is ESPN alone.** Traced the actual
   write path for `data/processed/tennis/games.jsonl` (the file
   `TennisModel`'s live serving path reads via
   `tennis_forward.py::_tennis_history_before`):
   `Ingestor.ingest_scores` (`ingest.py:57-137`) is the sole writer
   (`processed_path`, `ingest.py:49-50`), and for `sport_key == "tennis"` it
   calls **`ESPNClient.completed_tennis_singles_matches`**
   (`ingest.py:93,132`, defined in `data_sources/espn.py:204-268`) —
   exclusively. `rebuild/providers/tennis_mylife.py` (the real,
   independently-verified TennisMyLife provider, explicitly built to
   "replace the dead Sackmann source") and `rebuild/providers/tennis_espn.py`
   both live under `src/model_prediction/rebuild/`, the separate
   clean-slate rebuild track that `CLAUDE.md` documents as
   "shadow-only and no-production-write" — i.e. **not yet wired into the
   promoted serving pipeline** that feeds `TennisModel`. So: the task
   background's framing ("current data foundation uses TennisMyLife + ESPN
   Tennis providers") is **not accurate for the currently-served model** —
   TennisMyLife exists, is real, and is a plausible future replacement, but
   today's live `tennis-surface-elo-v1` predictions are built entirely from
   `data_sources/espn.py`'s historical scoreboard parser, same as they
   always have been. Also worth noting: `TENNIS_MYLIFE_RIGHTS`
   (`rebuild/providers/tennis_mylife.py:62-74`) sets
   `production_allowed=False` (licensing terms unverifiable) — so even if
   wired in, TennisMyLife could not directly replace ESPN as a
   production-serving source without a rights resolution first.

**Conclusion**: the `TennisPlayerForm`/Sackmann docstring reference is a
real but harmless documentation artifact — it describes an abandoned data
representation that the current model never touches. The bigger, previously
un-flagged finding is that the project's own mental model of "what feeds
tennis today" (TennisMyLife + ESPN) does not match reality (ESPN only); the
`rebuild/tennis/` track is where TennisMyLife actually lives, unpromoted.
Recommend: (a) update `models/tennis.py`'s docstring to stop citing the
Sackmann loader as current, (b) either delete `data_sources/tennis_sackmann.py`
+ `TennisPlayerForm` + `tests/test_tennis_sackmann.py` as confirmed-dead
code, or explicitly mark them archived/legacy, (c) build the normalized-data
adapter recommended by the task background as a real, explicit promotion
step for `rebuild/providers/tennis_mylife.py` into `TennisModel`'s existing
`dict`-row input contract — not a replacement of `build_elo`/surface-Elo
logic, just a new upstream feed, and only after the rights question is
resolved.
