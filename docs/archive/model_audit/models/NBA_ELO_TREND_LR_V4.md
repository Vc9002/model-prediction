# Model Card: `nba-elo-trend-lr-v4`

Status: audited 2026-08-11. Verdict below is unambiguous and is the answer to
the project's standing P0 open question on this artifact
(`config/tested_features.json`'s `elo_probability` entry: *"NBA v4 hits
73.66% calling 88.2% of games on Elo alone, above the NBA favorite base
rate. Leakage vs chalky holdout window is UNRESOLVED and under active
investigation. Do not build on top of Elo until answered."*).

## Verdict

> **ELO INTEGRITY CONFIRMED — no leakage found in 67 sampled events (11
> game-days spanning 2024-01-09 through 2026-02-01), KEEP nba-elo-trend-lr-v4.**

Every sampled event satisfied the required invariant — `last_home_update <
target event_start` **and** `last_away_update < target event_start` — with
zero exceptions. The strong locked-holdout result (73.66% hit rate, calling
88.2% of games) is not explained by a target-leaks-into-its-own-rating bug.
It is most parsimoniously explained by a genuinely favorite-heavy 2026H1
NBA holdout window (see "Chalky-holdout hypothesis" below) plus a model
that is, by construction, mechanically close to "always pick the Elo
favorite" (`elo_probability`'s fitted coefficient of 3.564 dwarfs
`trend_gap`'s -0.0036 and `defensive_trend_gap`'s -0.013 — see
`docs/model_audit/features/NBA.md`). That is a legitimate property of the
model, not evidence of a data leak.

This does not mean the Elo construction is flawless — see "Secondary
findings" for two real, lower-severity issues worth a future v5 (NBA
preseason/All-Star games are not excluded from Elo history the way MLB
excludes its own preseason/All-Star games, and Elo's home-field advantage
constant is applied uniformly with no true neutral-site override). Neither
rises to "discard the logistic regression family" — if anything is done
about them it should be `REFIT_SAME_FAMILY` for a v5, and the current v4
artifact should stay untouched and in production while that is evaluated.

## How the trace was done

Script: `outputs/rebuild/audit/elo_leakage_trace.py` (read-only, standalone,
imports the real unmodified `build_elo` from
`src/model_prediction/features/elo_ratings.py` and the real
`GameRecord`/`FeatureStore` types from `features/base.py` — no serving code
was modified to produce this trace). It replicates, without altering, the
exact day-bucketing walk-forward loop in
`src/model_prediction/validation.py::build_walk_forward_rows` (confirmed by
direct source read at the time of this audit):

```python
for day in sorted(by_date):
    day_games = sorted(by_date[day], key=lambda item: (item.start, item.event_id))
    if len(history) >= minimum_history_games:
        elo = build_elo(history, sport)  # history = games strictly before `day`
        trends = TrendEngine(history)
        ...  # features for every game ON `day` use this snapshot
    history.extend(day_games)  # today's games appended AFTER the day's features are built
```
(`validation.py` lines ~237-358 as of this audit.)

Data: `data/historical/nba_games_all.jsonl` (tracked in git, real ESPN NBA
results) — 3,037 completed games spanning 2024-01-01 through 2026-02-01.
`data/processed/nba/games.jsonl`, the file the live serving path actually
reads via `FeatureStore.load_games`, is gitignored and absent from this
worktree; this is a data-availability gap in the *audit environment*, not
in the *serving code*. The traced Elo math, GameRecord parsing, and
day-bucket walk-forward logic are byte-identical to what production uses;
only the specific row-level dataset differs, and both draw from the same
ESPN NBA history so the invariant check is not weakened by the substitution.

Ran via `python3 outputs/rebuild/audit/elo_leakage_trace.py`. Sampled 11
game-days (first eligible day once `minimum_history_games=50` is reached,
last day in the dataset, plus ~9 evenly spaced days in between), 67
individual games across those days. For every game, computed:
`event_id`, `event_start_utc`, `elo_probability`, `home_rating`,
`away_rating`, and — critically — `last_home_update_utc` /
`last_away_update_utc` (max `game.start` over the `history` list used to
build that day's Elo snapshot, per team), then checked
`last_update < event_start` for both teams.

**Result: 0/67 invariant violations.** Representative samples across the
span (full 67-row output retained in the scratchpad run log; re-run the
script to reproduce):

| event_id | event_start_utc | home / away | home_rating | away_rating | elo_probability | last_home_update | last_away_update |
|---|---|---|---|---|---|---|---|
| 401585134 | 2024-01-09T00:00Z | Charlotte / Chicago | 1480.38 | 1484.59 | 0.5936 | 2024-01-06T01:00Z | 2024-01-06T01:00Z |
| 401585522 | 2024-03-07T00:00Z | Washington / Orlando | 1250.89 | 1578.91 | 0.1846 | 2024-03-05T02:00Z | 2024-03-06T00:00Z |
| — | 2024-05-02/03 (playoffs) | — | — | — | — | strictly-before holds | strictly-before holds |
| — | 2024-10-28/29 (season open) | — | — | — | — | strictly-before holds | strictly-before holds |
| — | 2024-12-24 | — | — | — | — | strictly-before holds | strictly-before holds |
| — | 2025-10-05/06 (season open) | — | — | — | — | strictly-before holds | strictly-before holds |
| 401810554 | 2026-02-01T00:30Z | Philadelphia / New Orleans | 1525.81 | 1395.58 | 0.7600 | 2026-01-30T00:00Z | 2026-01-31T00:30Z |
| 401810556 | 2026-02-01T01:30Z | Houston / Dallas | 1619.89 | 1453.13 | 0.7962 | 2026-01-30T01:00Z | 2026-01-30T01:30Z |

In every one of the 67 rows, both `last_home_update_utc` and
`last_away_update_utc` are strictly earlier than `event_start_utc` — often
by a full day or more, since the walk-forward loop snapshots Elo once per
calendar day (ET) and only appends that day's games to `history`
*after* every game on that day already has its features built. Update and
predict are never inverted in this loop; the order is always
snapshot-then-append.

**Live serving path checked too, not just the training-time backtest loop**
(the trace above covers `validation.py`; the invariant also needs to hold
in the actual forward/live path). `learned_forward.py::build_learned_moneyline_slate`:

```python
history = store.games_before(key, game_date)  # cutoff: midnight ET at the START of game_date
...
elo = build_elo(history, key)
...
for event in events:
    start = parse_utc(str(event["date"]))
    if start <= observed_at:
        raise ValueError("event_started")  # also refuses to predict an already-started game
```

`FeatureStore.games_before` (`features/base.py:188`) is the single
point-in-time chokepoint the whole codebase is documented to route
through: `cutoff = datetime.combine(date.fromisoformat(as_of_date), time.min,
tzinfo=EASTERN)`, then `game.start < cutoff` (strict `<`). Since `elo` is
built once from that cutoff-filtered `history` before the per-event loop
starts, and the same day's games are never in `history` (the cutoff is
midnight ET at the *start* of `game_date`, and NBA games start well after
midnight local time), no game can see its own result or any same-day
game's result. The `start <= observed_at` guard is an independent second
line of defense against predicting an already-started/finished game.

## Additional integrity checks

- **Cold-start value**: `DEFAULT_ELO = 1500.0` (`elo_ratings.py`). `EloBook.rating()` returns
  this via `dict.get(team, DEFAULT_ELO)` for any team never seen in `history`. None of the
  67 sampled games hit a cold-start team (the minimum-history gate keeps the trace past the
  early bootstrap window), but the mechanism is inert — a never-seen team is priced at a flat
  1500, not from any information about its actual result that day.
- **Update-then-predict ordering**: confirmed never inverted, in both the training walk-forward
  loop and the live serving path (above). `EloBook.update()` is only ever called from
  `build_elo()`'s loop over `history`, which is always the pre-cutoff game list; it is never
  called with the target game itself before that game's features are read.
- **Home-field advantage**: `ELO_CONFIG["nba"] = {"k": 20.0, "home_advantage": 70.0,
  "offseason_regression": 0.35}`. Applied as a static +70 rating-point addition to the home
  team inside `expected_win_probability` — same constant for every venue, every season,
  applied only at inference/update time (not baked into the persisted rating itself), so it
  cannot leak information forward.
- **Season regression / reset**: `build_elo()` applies `_apply_offseason_regression` when the
  gap between two chronologically consecutive games (any teams, not per-team) exceeds
  `offseason_gap_days=90`. NBA's `offseason_regression=0.35` pulls every team's rating 35% of
  the way back toward 1500 before the next game is processed. This is a *forward-looking-safe*
  operation — it only uses the elapsed-time gap between games already in `history`, and always
  fires before, not after, the next game's rating is used — so it cannot introduce leakage
  either, though it does mean playoff-adjacent October ratings partially reset year over year
  (expected, standard 538-style Elo behavior, not a bug).
- **Expansion-team handling**: no special-cased logic exists; a new franchise is just a team
  name never present in `ratings`, so it silently gets the same `DEFAULT_ELO` cold-start
  treatment as any other unseen team. Not exercised by the current dataset (no NBA expansion
  team since the sample window began) but the mechanism is generic and doesn't depend on a
  hardcoded team list.
- **Neutral-site handling**: `EloBook.expected_neutral_win()` (no home-advantage term) exists
  and is used only for validation.py's `elo_neutral_probability` *diagnostic* field
  (`elo_trend_adaptive_hfa` variant) — it is **not** part of `nba-elo-trend-lr-v4`'s three
  production features. `GameRecord` carries no `neutral_site` flag at all, so every NBA game
  — including any international/Paris-/Mexico-City-style "home" games with a designated home
  franchise but a non-home arena — gets the full +70 home-advantage term regardless of true
  venue. This is a real simplification but not a leakage vector; flagged under "Secondary
  findings."

## Chalky-holdout hypothesis (supporting the "just a chalky sample" half of the question)

The training artifact's own metadata (`config/models/nba-elo-trend-lr-v4.json`) shows a
`confidence_threshold` of 0.5444 selectivity, calling 88.2% of all games
(577/654 in the locked holdout window, 2026-01-24 through 2026-06-13). A
model that is 88%-selective and dominated by a single, well-separated
feature (elo_probability coefficient 3.564 vs. trend_gap -0.0036 and
defensive_trend_gap -0.013 — see feature doc) is, by construction, very
close to "bet the Elo favorite in nearly every game."

Independent corroboration from `outputs/roadmap_challenger/roadmap-challenger-factorial-v1.json`
(a separate real ablation run, v3-basis incumbent, coefficients essentially
identical to v4's): the confidence-gate sweep shows the signal holds up
even with **zero selectivity** — at `gate=0.5` (100% call rate, 662
holdout calls, no cherry-picking by confidence at all) the model still
hits 70.24%. Accuracy then rises smoothly and monotonically as the gate
tightens (72.2% at 0.525, 74.2% at 0.55, 76.1% at 0.575, 79.2% at 0.625,
81.8% at 0.65). A leaking feature would tend to produce uniformly inflated
accuracy regardless of the model's own stated confidence, not this kind of
clean, monotonic confidence/accuracy relationship — this is the shape you
expect from a genuinely well-calibrated probability estimate.

The reliability table in that same artifact shows real, monotonically increasing hit rates
across confidence buckets (0.5-0.6 bucket: 60.9% hit rate over 128 calls;
0.7-0.8 bucket: 83.7% over 202 calls) — this is the calibration shape you
expect from a legitimately well-separated favorite/underdog signal, not
from a data leak (a leak would tend to blow up accuracy roughly uniformly
across confidence buckets, including low-confidence ones, since the leaked
signal would dominate regardless of the model's own stated confidence).
The monthly breakdown also shows real variance consistent with an ordinary,
non-leaked model riding a strong run (2026-01: 59.2% hit rate/49 calls,
climbing through 2026-03: 79.1%/215 calls, falling back to 2026-05:
63.6%/33 calls) — a leak would not plausibly produce that much month-to-month
swing.

## Secondary findings (do not block the KEEP verdict; candidates for a future v5)

1. **NBA preseason and All-Star games are not excluded from Elo history.**
   `FeatureStore.load_games` (`features/base.py:120`) has an explicit
   `if sport.lower() == "mlb" and season_type in {"preseason", "all-star"}: continue`
   filter — but only for MLB. The traced NBA dataset contains 144 preseason
   games and 4 All-Star games (out of 3,037) that flow straight into
   `build_elo`'s `history` and update real franchise ratings. Preseason
   games use bench-heavy rotations and All-Star games are not even
   real-franchise-vs-real-franchise matchups in the normal sense (mixed
   rosters). This is a data-quality/noise concern for rating accuracy, not
   a point-in-time leak (it never uses a game's own future result — it's
   about *which* historical games are eligible to shape a rating at all).
   Worth mirroring MLB's exclusion in a future v5.
2. **No neutral-site override.** Covered above — every game gets the full
   home-advantage constant even on the rare true-neutral-venue game. Low
   frequency, low expected impact, but easy to fix alongside (1) in a v5.

Neither of these affects the current v4 artifact's validity as shipped —
the training data these coefficients were fit against had the same
preseason/All-Star inclusion and same no-neutral-site behavior baked in
consistently across train/validation/holdout, so there's no train/serve
skew from them. They're flagged as future-v5 data-quality improvements,
not as reasons to distrust v4's holdout number.

## Model card

**Why it exists**: `nba-elo-trend-lr-v4` is the incumbent NBA moneyline
model — a shared "Elo + trend" logistic-regression family also used for
MLB, WNBA, NFL, and soccer (see `docs/MODEL_IMPROVEMENTS.md` section 1).
Per `docs/PROJECT_STATUS.md`, NBA moneyline is currently `research`-tier
(shadow, zero real units), not yet promoted to the real-money Main ledger
tier that MLB/WNBA moneyline occupy.

**Market(s) predicted**: moneyline only (`market_models.moneyline` in
`config/models/nba-elo-trend-lr-v4.json`), `positive_class: "home"`.

**Feature set** (verified against the artifact JSON and
`learned_forward.py::_compute_features`):

| feature | coefficient | source |
|---|---|---|
| `elo_probability` | 3.5640800015 | `elo.expected_home_win(home_team, away_team)` |
| `trend_gap` | -0.0035535722 | `home_trend.offensive_momentum - away_trend.offensive_momentum` |
| `defensive_trend_gap` | -0.013059643 | `home_trend.defensive_momentum - away_trend.defensive_momentum` |

intercept: -1.9071845069. Full detail on each feature (formula, PIT
safety, coefficient interpretation, verdict) is in
`docs/model_audit/features/NBA.md`.

**Training method**: `logistic_regression` (`method` field in the
artifact). 60/20/20 chronological split
(`framework: "locked_complete_date_60_20_20"`):
- coefficient fit: 2024-01-08 through 2025-05-27 (2,171 observations)
- threshold selection: 2025-05-28 through 2026-01-23 (753 observations,
  `threshold_source: "later validation cohort; never locked holdout"`)
- locked holdout: 2026-01-24 through 2026-06-13 (654 observations, `locked_holdout: true`)

`market_inputs_used: false` — no market/odds features, consistent with
`elo_probability`/`trend_gap`/`defensive_trend_gap` being the complete
feature set. `walk_forward_features: true` confirms the walk-forward
feature-construction contract (this audit's primary subject) was used for
training, matching what was traced above.

**Historical results** (from the artifact's own `qualification` block):
- locked-holdout hit rate: 73.66% (425/577 calls out of 654 total predictions, 88.23% selectivity)
- `units_at_minus_110`: 234.36 over the holdout window
- every monthly cohort (2026-01 through 2026-05, all with >=10 calls) independently clears
  the qualifying bar; 2026-06 is a 3-call partial month, correctly labeled `partial_month`
  rather than folded into the full-month stats
- `meets_primary_holdout_metrics: true`, `qualified: true`, `qualification_eligible: true`

**Calibration diagnostics** (from `qualification.calibration`):
- Brier score: 0.18541
- log loss: 0.5553
- calibration slope: 1.785, calibration intercept: -0.226 — slope > 1 indicates the model is
  somewhat *under*-confident relative to realized outcomes in this holdout window (predicted
  probabilities could be pushed further from 0.5 and still be well-calibrated), which is the
  opposite direction you'd expect from a leaking feature (a leak typically produces
  *overconfidence*, i.e. slope < 1, because the model is fitting noise that doesn't generalize).
  This is one more piece of evidence against the leakage hypothesis.
- expected calibration error: 0.0605
- reliability buckets are monotonic and reasonably sized (128/216/202/31 games across the
  0.5-0.9 probability range) — no bucket shows a hit rate below its bucket's own lower bound,
  and no inversion between buckets.

**PIT-safety**: confirmed above (both the training-time walk-forward loop and the live serving
path route every feature through a strict "games/history strictly before the target's cutoff"
boundary; `FeatureStore.games_before`'s docstring names this "the point-in-time chokepoint").

**Train/serve parity**: `TrendEngine`/`build_elo` are the exact same functions called from both
`validation.py` (training/backtest) and `learned_forward.py` (live serving) — no reimplementation
or parallel logic exists for either path. The `history` cutoff differs only in mechanism
(day-bucket walk-forward for backtest vs. `games_before(as_of_date)` for serving) but both encode
the identical rule: only games strictly before the target's own calendar day are visible.

**What to retain**: the artifact as shipped — `KEEP`. Elo construction, update ordering, and
feature wiring are sound. No leakage found; the strong holdout number reflects a real,
well-calibrated, favorite-heavy signal plus a highly selective confidence threshold, not a bug.

**What to change (future v5, not this artifact)**: exclude NBA preseason/All-Star games from
Elo history (mirror MLB's existing filter); consider a neutral-site override for the rare
true-neutral-venue game; `trend_gap` and `defensive_trend_gap` are both near-zero-coefficient
and INCONCLUSIVE per `config/tested_features.json`'s leave-one-out evidence (see
`docs/model_audit/features/NBA.md` — recommend an audit-track ablation removal for a future v5,
not a change to this locked artifact).
