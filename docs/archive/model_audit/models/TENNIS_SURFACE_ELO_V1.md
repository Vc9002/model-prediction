# Model Card: `tennis-surface-elo-v1`

Audited 2026-08-11 against branch `audit/model-feature-reconciliation-v1`
(based on `origin/main` @ `826c89342bd2f3f1ea44fc29eaf20fad520dc5d5`).

See `docs/model_audit/features/TENNIS.md` for the full feature-level audit,
including the confirmed registry contradiction on `tennis_surface` (registry
says "no active tennis model consuming it"; the active model blends
per-surface Elo at 60% weight into every prediction).

## Why it exists

`TennisModel` (`src/model_prediction/models/tennis.py`) is the project's
only tennis model — a surface-aware Elo system for tennis singles
moneyline. Its module docstring calls it "research state until validated
through the backtester," but that has since happened:
`validation.qualify_tennis_elo_model` ran for real 2026-08-03 and the
model was promoted by explicit operator override
(`config/model.yaml:318-345`, `status: shadow_qualified`,
`qualification_override: true`). It is the incumbent and should be audited,
not replaced — a coherent Elo-plus-surface-blend architecture with a real,
strong walk-forward result (65.5% hit rate, 4,269 locked-holdout calls,
+1,070.7 units, every qualifying month positive) is not something to rebuild
from zero.

## Market(s) predicted

Moneyline only (`market_type: "moneyline"`, `models/tennis.py:139`) —
binary win probability, no draw (tennis has none). Singles only: ESPN's
doubles draws are excluded at ingestion (`data_sources/espn.py`, filtered on
`"singles" not in slug`). Both WTA and ATP price live (`TENNIS_TOURS =
("WTA", "ATP")`, `tennis_forward.py:38`; ATP added 2026-08-03 per
`tennis_forward.py`'s module docstring). ITF cannot be priced at all — ESPN
has no ITF scoreboard endpoint, so those matches are permanently
BBO-capture-only (unpriceable), consistent with the project's known
tennis coverage gap (Polymarket US tennis is WTA/ITF only, ESPN is ATP/WTA
only, so the tradeable overlap is WTA-only; ATP trades only because
Polymarket added an ATP league 2026-08-03, per `tennis_forward.py`'s own
docstring).

## Feature set

Two signals, both derived from the same match history, no market/odds
inputs:

1. **Overall Elo** — standard Elo, `K_FACTOR = 32.0`, cold start `DEFAULT_ELO
   = 1500.0` (`models/tennis.py:17-18`).
2. **Per-surface Elo** — the same Elo mechanics run on a `(player, surface)`
   keyed book, built inside the same `build_elo` loop
   (`models/tennis.py:59-88`).

Blended per player as `0.6 * surface_elo + 0.4 * overall_elo`
(`match_probability`, `models/tennis.py:90-106`), then passed through the
standard logistic expected-score function
(`features/elo_ratings.py::expected_win_probability`) to get a match win
probability. Full detail (formula, PIT-safety, coverage, verdict) is in
`docs/model_audit/features/TENNIS.md`.

No feature-registry entries are wired to this model at all — it does not
route through `learned_forward.py`'s generic `_compute_features` pipeline
the way MLB/NBA/WNBA/NFL/soccer's logistic-regression models do; all
computation is inline inside `TennisModel`.

## Training / fitting method

Not a fit in the ML sense — Elo is an online update rule, not a regression.
"Training" here means the chronological Elo walk (`build_elo`,
`models/tennis.py:59-88`): matches are sorted by `match_date` and both Elo
books are updated match-by-match in that order. There is no separate
train/validation split for the Elo parameters themselves (`K_FACTOR=32`,
`surface_weight=0.6`) — both are hardcoded constants with no grid search or
sensitivity analysis anywhere in this repo (verified by grep: neither name
appears in `validation.py`, `roadmap_challenger.py`, or
`production_feature_ablation.py`).

What *is* real and walk-forward-validated is the **downstream qualification**
(`validation.qualify_tennis_elo_model`, `validation.py:1510-1660`):

- Self-contained rather than reusing `build_walk_forward_rows`/
  `chronological_split` — tennis rows (`winner`/`loser`/`surface`, no
  scores) are structurally incompatible with `GameRecord`'s
  `away_team`/`home_team`/`away_score`/`home_score` shape
  (`validation.py:1517-1524`'s docstring documents this explicitly, citing
  a real historical bug where every tennis row silently failed to load via
  the generic path and every match probability defaulted to a coin flip).
- Locked 60/20/20 chronological split by **distinct match date**
  (`train_count = floor(len(dates) * 0.60)`, etc.,
  `validation.py:1554-1558`), same convention as every other sport in the
  project.
- `minimum_history_matches: int = 200` gate before any prediction counts
  toward validation/holdout (`validation.py:1511,1576`) — cold-start Elo
  noise is excluded from grading, not from training (the model still sees
  and updates on those early matches, they're just not scored).
- Confidence = `abs(p_one - 0.5)` (binary market, no 3-way argmax needed).
- Threshold learned on the validation slice only
  (`_learn_threshold_from_confidence_hit`, target hit rate 65%,
  `PRIMARY_THRESHOLD_TARGET_HIT_RATE = 0.65`, `validation.py:40`), then
  graded once on the locked holdout — same shared methodology and shared
  constants (`MINIMUM_CALLS = 50`, `QUALIFICATION_MINIMUM_HIT_RATE = 0.60`)
  as every other sport's qualification path in this file.

## Threshold selection

`research_confidence_gate: 0.037239` in `config/model.yaml:347` — per the
adjacent comment, this is the *actual learned threshold* from the
2026-08-03 `qualify_tennis_elo_model` run, wired in by explicit operator
approval, not an arbitrary number. `min_edge: 0.05` (separate gate, edge
vs. executable market ask) is a project-wide convention value, not
tennis-specific.

Two additional serving-time gates outside the qualification path itself
(both confirmed in `cli.py`):
- `MINIMUM_PLAYER_MATCHES = 10` (`cli.py:2055`) — a contract's
  `feature_basis.min_player_matches` (the lesser of the two players' real
  match counts, `models/tennis.py:157`) must be >= 10 for
  `model_inputs_valid` to be true (`cli.py:2075`).
- A harder, unconditional skip inside the model itself: any match where
  either player has **zero** real match history is dropped before a
  prediction is even generated (`models/tennis.py:118-124`) — the model
  never emits a bare-cold-start (1500 vs 1500) prediction at all.

## Historical results

From `config/model.yaml`'s `qualification_override_reason` (TENNIS block,
recording the real 2026-08-03 `qualify_tennis_elo_model` run):

- **65.5% hit rate** on **4,269 locked-holdout calls**
- **+1,070.7 units at -110**
- Every qualifying month independently positive
- Explicitly called out as the strongest result of any sport checked in
  that audit session

No separately-tracked settled-picks-vs-backtest reconciliation for tennis
was found in `DEBUG.md` comparable to soccer's (`61.5% win rate, 8-5, n=13,
closely matching the backtest` — `DEBUG.md:1934-1936`); worth doing as a
follow-up once enough real settled tennis picks accumulate.

## Calibration diagnostics

**None exist.** `qualify_tennis_elo_model` reports hit rate, units, and
monthly consistency only — no Brier score, no calibration slope/intercept,
no reliability buckets. The project's generic calibration-diagnostic
tooling (`rebuild/calibration.py::calibration_intercept_slope`,
`cross_fit_calibration_eval`) is fully implemented but has zero call sites
anywhere in the repo (confirmed by grep) — it has never been run against
any model, tennis included. This is a real gap: a 65.5% hit rate with a
learned confidence threshold says the model separates winners from losers
well at the calling margin, but says nothing about whether its raw
probabilities (used for `edge_vs_executable_ask` sizing,
`tennis_forward.py:291-294`) are well-calibrated across the full confidence
range.

## Known defects

- **Registry contradiction** — see headline finding in
  `docs/model_audit/features/TENNIS.md`. Not a code defect, a documentation
  defect with real potential to mislead a future contributor into thinking
  tennis has no surface signal.
- **No calibration diagnostics** (above).
- **Untested constants** — `K_FACTOR=32`, `surface_weight=0.6`,
  `DEFAULT_ELO=1500`, `MINIMUM_PLAYER_MATCHES=10` are all hardcoded with no
  in-repo sensitivity/ablation evidence (detailed in the feature doc).
- **Surface-inference fail-open default** — `_infer_tennis_surface`
  (`espn.py:291-297`) defaults unrecognized tournament names to `"Hard"`
  rather than flagging them as unknown; a real, unquantified source of
  surface mislabeling.
- **Team-ban enforcement gap** (ops-layer, not model math) —
  `DEBUG.md:306-330` documents that `_forecast_soccer_sport`-style team-ban
  checking never got built for tennis (or esports/soccer/KBO/NPB); noted
  here for completeness since it affects what actually reaches the ledger,
  not because it's a defect in `TennisModel` itself.
- **Sackmann docstring reference is stale but inert** — see feature doc;
  confirmed not to affect runtime behavior, since `TennisPlayerForm`/the
  Sackmann loader are dead code on the live path.
- **Data-foundation framing gap** — the live model is fed by ESPN alone,
  not "TennisMyLife + ESPN" as commonly assumed; TennisMyLife exists but
  lives only in the unpromoted `rebuild/tennis/` track (see feature doc).

## PIT-safety

Confirmed safe. `_tennis_history_before` (`tennis_forward.py:41-73`) filters
strictly to `event_start_utc < midnight-ET-at-start-of(as_of_date)`, same
convention as `FeatureStore.games_before` elsewhere in the project.
`build_tennis_slate` additionally guards `start <= observed_at: raise
ValueError("event_started")` (`tennis_forward.py:196-197`) as a second,
independent line of defense against predicting an already-started match.
Historical surface labels are captured once at ingest time from the same
heuristic used live — not re-derived at prediction time from anything that
could drift.

## Train/serve parity

Confirmed. `validation.qualify_tennis_elo_model` and
`tennis_forward.py::build_tennis_slate` both instantiate the real
`TennisModel` class and call its real `build_elo`/`predict_games` methods —
no parallel or reimplemented Elo logic exists for either path. The one
structural difference (qualification pools history day-by-day inside its
own walk-forward loop; serving calls `_tennis_history_before` once and
splits into per-tour history before calling `predict_games` per tour) is a
harness difference, not a logic difference — both ultimately hand the same
`build_elo`/`match_probability` code the same shape of match-history input.

## Artifact reproducibility

**Weak.** Unlike MLB/NBA/WNBA/NFL, there is **no versioned JSON artifact**
for this model under `config/models/` (confirmed: no
`tennis-surface-elo-v1.json` exists; only the old, no-longer-active
`soccer-elo-trend-lr-v1/v2.json`-style legacy artifacts exist for other
sports). `config/model.yaml`'s own `qualification_override_reason` states
this explicitly: *"no artifact file with a qualified/qualified_for_betting
field exists for it, unlike every other shadow_qualified league."* The
entire "artifact" is the source code itself
(`models/tennis.py`, `K_FACTOR`/`surface_weight`/`DEFAULT_ELO` baked in as
literals) plus a `model_code_hash` computed at request time
(`tennis_forward.py:298-299`, `hashlib.sha256` of the model source file)
for provenance. This means: (a) there is no single frozen, re-loadable
qualification result to point to — the 65.5%/+1,070.7u number lives only in
a config-file prose comment, not a structured, machine-checkable artifact;
(b) any future change to `models/tennis.py` changes the code hash and
therefore the provenance trail, but nothing currently re-runs qualification
automatically when that happens, so a silent constant change (e.g. someone
tweaking `surface_weight`) would ship without a fresh qualification check
unless a human remembers to run one.

## What to retain

- The overall architecture: Elo + surface-Elo blend, walk-forward
  qualified with a real, strong locked-holdout result. Do not rebuild from
  zero.
- The two-tier minimum-history gating (hard skip at 0 matches inside the
  model, `MINIMUM_PLAYER_MATCHES=10` downstream).
- The per-tour history separation in serving (ATP/WTA never blended).

## What to change

- Fix the registry contradiction (recommendation detailed in the feature
  doc — out of scope to edit here).
- Add a real calibration diagnostic (Brier, calibration slope/intercept,
  reliability buckets) — the tooling already exists
  (`rebuild/calibration.py`) and just needs a call site for tennis.
- Run a real sensitivity check on `K_FACTOR`/`surface_weight` — even a
  simple grid (e.g. surface_weight in {0.4, 0.5, 0.6, 0.7}) against the
  same locked holdout would turn "60% is what the code happens to say"
  into an actual empirical claim.
- Tighten `_infer_tennis_surface`'s fail-open default, or at minimum log
  when it fires so the mislabeling rate becomes measurable.
- Produce a real, versioned qualification artifact (mirroring MLB/NBA's
  `config/models/*.json` shape) instead of leaving the only record of the
  65.5%/+1,070.7u result as a YAML prose comment.
- Retire or explicitly archive `data_sources/tennis_sackmann.py` +
  `TennisPlayerForm` + `tests/test_tennis_sackmann.py` (confirmed dead on
  the live path) and update `models/tennis.py`'s docstring accordingly.
- Build the TennisMyLife normalized-data adapter into `TennisModel`'s
  existing `dict`-row input contract as a real, explicit promotion step
  from `rebuild/tennis/` — after resolving `TENNIS_MYLIFE_RIGHTS`'s
  `production_allowed=False` gate, not around it.

## What would justify replacing the family

Nothing found in this audit does. The architecture is sound (Elo math
independently traced and confirmed correct in `DEBUG.md:1888-1889`'s
project-wide logic review), PIT-safe, train/serve-parity-clean, and has a
strong real qualification result. Replacement would only be justified by:
a genuine surface-weight/K-factor sensitivity study showing the current
constants are meaningfully suboptimal against the locked holdout, or a
calibration diagnostic (once built) revealing the raw probabilities are
unusable for sizing despite the good hit rate. Neither exists yet — this
audit surfaces the *absence* of that evidence, not a finding that the model
is broken.
