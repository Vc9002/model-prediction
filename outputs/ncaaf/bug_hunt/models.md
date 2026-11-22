# NCAAF model code bug hunt (read-only), 2026-11-21

Scope: serving paths of the champion `cfb-structural-v5` (`models/college_football.py`, `models/cfb_structural_v5.py`
on the v4 base, `features/cfb_features.py`, `models/cfb_distribution.py`) and the challengers v6.2 and v7.
HEAD 292e87ac. Nothing in src/, config/ or tests/ was edited; nothing committed.

Detail for the challengers is in sibling files written by sub-reviews:
- `models_v62.md` (v6.2), `models_v7.md` (v7).
- `pricing.md` (another session) covers quote selection and ledger price fields. Its findings are not repeated here
  (stale rungs priced over fresh ones; integer-odds round trip of ledger prices; no executable-quote requirement for Main).

Ledger figures are from `E:/model-prediction-runtime/ledgers/ledgers.db` opened read-only.

**Live vs all versions.** The daily job re-prices every pick several times a day, and v5 has 2,062 superseded rows
against 469 live ones (open plus settled). Live rows are one per (event, market, tier), so a live row count equals the
number of distinct picks. Counts below give the live figure first and the all-version figure in parentheses.

**Prices** are stated as probabilities: the Main moneyline dog floor is an ask of at least 33.3%. Experiment scripts are in the
session scratchpad (`v5_otherside.py`, `v5_epa.py`) and reproduced inline below where short.

---

## Champion v5 findings, ranked

### 1. MEDIUM, confirmed interaction defect in a deliberate rule: the spread side rule's 0.08 thresholds equal Main's max_edge

The key-window rule was tuned on purpose (46f94dd0), and no test pins it (grep of `tests/test_college_football.py`
and `tests/test_cfb_*.py` for the rule or the 0.08 threshold finds nothing). The defect is the interaction with
`max_edge` added later, together with the stale conference flag of finding 3 that picks the branch.

**Where:** `models/college_football.py:1137-1156` (Polymarket branch) and `:1173-1194` (ESPN consensus branch). Main
gate: `cli/forecast.py` (`min_edge <= contract_edge <= max_edge`, max_edge 0.08 from the v5 artifact `gates`).

**Mechanism:** the side is not chosen by edge. The rule is:
- line magnitude in (3.5, 9.5]: take the underdog unless the favourite's edge is above 0.08 and above the dog's;
- outside that window and not a conference game: take the favourite unless the dog's edge is above 0.08;
- conference game outside the window: take the larger edge.

The thresholds are hard-coded, tuned on in-sample PnL (commit 46f94dd0, "+125.28U recovery"). Main later got
`max_edge = 0.08`. The two rules combine so that:
- a key-window favourite is selected only with edge > 0.08, which Main then rejects as `excessive_edge`. Key-window
  favourites can never reach Main;
- a non-conference underdog outside the window is likewise unreachable;
- the default side is logged even when its edge is negative and the other side's is positive.

**Evidence (ledger, v5 spread):**
- **Main, 31 live picks (155 rows across all versions):** 0 key-window favourites and 0 non-conference underdogs
  outside the window. Breakdown: key dog 7, other dog conf 11, other fav conf 10, other fav non-conf 3.
- **Flat:** 14 live key-window underdog picks with negative edge (82 rows across all versions).
- **Executable-quote Flat picks:** 98 live (286 rows across all versions). The other side's ask was recovered from the
  archive record by hash, with 0.6 pp tolerance for the integer-odds round trip.
  - 13 selected a negative-edge side (42 across all versions);
  - on 13 the unselected side had the higher edge (39 across all versions);
  - on 7 the unselected side's edge was inside the Main band [2%, 8%] (19 across all versions).
- **Examples (live):**
  - 401871101, 11-21 19:20Z: home +5.5 at -6.9%, while away -5.5 was +6.4%;
  - 401862835, 11-21 14:21Z: home +6.5 at -6.4%, while the other side was +6.0%.

**Fix:** select the side by maximum edge (as for totals and as the v4 docstring claims). If a key-number penalty is
wanted, express it as a validated probability adjustment, not a side override, and move any threshold into the artifact
or config.

### 2. MEDIUM, confirmed (deliberate choice with a measurable cost): moneyline side = higher model probability, not higher edge

**Where:** `models/college_football.py:1125-1129` ("Pure Probability Winner Selection", restored in 5e134c0b on
2026-11-01). It contradicts `cfb_structural_v4.py:12-13` ("select sides based on maximum predictive edge ...
eliminating negative-edge chalk selection").

**Evidence (v5 Flat moneyline):**
- 96 live picks on executable quotes (421 rows across all versions):
  - 48 selected a negative-edge favourite (221);
  - on 47 the unselected side had the better edge (216);
  - on 23 that edge was in the [2%, 8%] band (119);
  - 2 of those also cleared the 33.3% ask floor (8), so they would have been Main-eligible.
- All live Flat moneyline picks: 64 of 127 have negative edge.
- Example: 401856866, 11-21 14:21Z, away 59.9% at ask 66.5% (-6.6%) logged, while home 40.1% at ask 34.0% was +6.1%.
- Main is unaffected because it filters edge ≥ 2%.

**Impact:** the Flat ledger, which is meant to log every candidate, records the wrong side on about half the moneyline
picks. Main never sees a moneyline underdog with an ask between 33.3% and 50%.

**Fix:** choose by edge, or log both sides to Flat. This is an operator decision because the 11-01 revert was
deliberate, so it is not filed as a code bug.

### 3. MEDIUM, confirmed: the static conference map is stale for 2026 realignment, and `is_conference_matchup` drives selection and SDs

**Where:**
- `features/cfb_features.py:557-564`: `is_conf_matchup` from `resolve_team(...).conference`.
- Static table in `data_sources/cfb_data.py`: Boise State, Fresno State, San Diego State, Utah State, Colorado State are
  "Mountain West"; Texas State is "Sun Belt"; Northern Illinois "Mid-American"; Louisiana Tech "Conference USA"; UTEP
  "Conference USA". All changed for 2026 (Pac-12 rebuild; NIU and UTEP to the MWC; La Tech to the Sun Belt).

Static values verified by `resolve_team`: Northern Illinois "Mid-American", UTEP "Conference USA", Louisiana Tech
"Conference USA", Texas State "Sun Belt". Florida International, Sacramento State and Missouri State do not resolve at
all, so their games are always labelled non-conference.

**Evidence:**
- ESPN's own `competitions[0].conferenceCompetition` on cached 2026 scoreboards (`data/raw/ncaaf/2026-10-16..11-21`,
  FBS games including unresolved FBS teams): 39 of 340 ESPN conference games are labelled non-conference.
  - 13 of the 39 involve the three unresolved teams.
  - No game is labelled conference when ESPN says it is not.
  - Examples: Boise State @ Washington State 10-24, Texas State @ Boise State 11-01, Oregon State @ Fresno State 11-01,
    Northern Illinois @ UNLV 11-01, Louisiana Tech @ South Alabama 10-31.
- Live v5 picks on those events: 14 Flat per market (42 rows) and 5 Main (1 moneyline, 1 spread, 3 total).

**Effect on v5:**
- **Spread side rule:** finding 1 takes the non-conference branch. This is the largest effect: it flips the default
  side.
- **Margin SD:** the conference scale is 0.9641, so mislabelled games get about 3.6% more SD on established games.
- **Total baseline:** the total macro baseline is 49.5 vs 54.0 points (small in November, since the macro weight is
  0.05).

**Training side (known):** training uses the same time-invariant table, so Texas carries its SEC tier offset in
2016-2023 rows. This is hindsight in the training features (ROADMAP step 4, "season-aware conference map"); I am
noting it, not filing it.

**Fix:** use ESPN's `conferenceCompetition` (and the competitor `conferenceId`) at serving time, or a season-keyed
conference map. The same map should serve training.

### 4. LOW-MEDIUM, confirmed: the "EPA" channel stopped being populated mid-October 2026 (train/serve drift)

**Where:**
- `features/cfb_features.py:482-501` reads `home_epa_per_play` / `away_epa_per_play` from history.
- These are not EPA. They are a lagged scoring proxy, `(last-5 points scored - 28) / 70`, written only by the one-off
  backfill `scripts/build_cfb_historical_dataset.py:189-190`. The daily ingest (`ingest.py`, parser
  `espn-scoreboard-1`) writes none.

**Coverage (non-zero EPA):**

| Period | Games with EPA |
|---|---|
| 2016-2025 | about 50% of games every season (the FBS games) |
| Aug 2026 | 5 |
| Sep 2026 | 198 |
| Oct 2026 | 57 |
| Nov 2026 | 0 |

So a November team's EPA term is the mean of a few September proxies, or the PPP fallback. Training saw full-season
coverage.

**Quantified (`v5_epa.py`):** filling the proxy for 2026 games that lack it, then re-pricing the 11-21 slate at fixed
lines, moves the 54 FBS-vs-FBS games by:
- projected margin: mean 0.50 pts, max 1.85;
- total: mean 0.39, max 1.6;
- home win probability: mean 0.9 pp, max 5.0 pp.

No point-in-time leak: the proxy uses only prior games.

**Fix:** compute the proxy at feature time from history (it is a pure function of prior scores) instead of storing it
per row, or drop the channel and refit. Either way, training and serving must read the same thing.

### 5. LOW-MEDIUM, confirmed: the "key-number engine" has no key numbers; push and cover mass are wrong on whole lines

**Where:** `models/cfb_distribution.py:119-144, 207-224`. Independent negative binomials with the common noise
cancelling in the margin give a smooth margin distribution.

**Engine vs history:**
- At line -7 with mu 31/24 and SD 17.5, the engine's push is 2.43%. At -3 with mu 29/26 it is 2.47%.
- Empirical P(|margin| = 3) is 9.98% and P(|margin| = 7) is 8.63% (2016-2025 history, both signs). That is about
  4-5% at the one-signed number, which is where the push sits.
- So on a whole line each cover probability is overstated by about 1-1.5 pp.
- On half-point lines next to 3 and 7, the value of crossing the key number is underpriced by about the same amount.

**Ledger:**
- 11 live v5 spread picks are on whole lines (10 Flat, 1 Main); 70 rows across all versions.
- Every one is `sportsbook = espn_consensus`. The ledger's provenance field is blank on all of them: they predate the
  11-14 lineage fix.
- All were created on 2026-11-14; none since. Polymarket lines in the current window are all half-point
  (`models_v7.md`).

So current exposure is only the half-point key-crossing error. The whole-line push error returns whenever a market
falls back to consensus on a whole number.

**Fix:** an empirical margin pmf with key-number multipliers, as v6.2 already uses (`models_v62.md` confirms its pmf
matches its fit script).

### 6. LOW, suspected: v5 under-predicts the ESPN "home" team in the recent era, not specifically at neutral sites

**Where:** `models/cfb_structural_v5.py:168-173` applies `m' = -0.4143 + 1.3189 m` to every game. At neutral sites the
v4 projection has zero home advantage (`cfb_features.py:658-661, 715`), so the intercept and slope act on
neutral-site games too.

**Evidence:** the v5 training cache (`outputs/ncaaf/cfb_v5_base_rows.jsonl`) gives actual minus calibrated margin of:

| Subset | Games | Residual (pts) | SE |
|---|---|---|---|
| Neutral | 580 | +1.41 | 0.71 |
| Home | 7,380 | +0.04 | |
| Neutral postseason | 373 | +1.23 | 0.92 |
| Neutral, holdout | 109 | +1.24 | 1.43 |

| Home, holdout | 1,418 | +1.25 | 0.43 |

**Reading:** on the holdout, home and neutral games carry the same +1.25 residual. So for v5 there is no
neutral-specific gap; there is a general recent-era under-prediction of the ESPN home team, about 3 SE on home games.
That is consistent with the already-known under-stretched calibration (see "Known" below). The cache predates the 11-21
feature fixes, so this needs re-measuring on current features. The v7 neutral finding (`models_v7.md` #1, intercept
-1.62) is a different and clearer case.

**Fix:** re-measure at the next refit, with a neutral-site term as one candidate.

### 7. LOW, confirmed: postseason week parity

- **Serving** maps ESPN season type 3 to week 15, so the prior weight is 0.01 (`college_football.py:49-52, 200-207`).
- **Training** took `week` from the history rows. 31 postseason-dated rows carry week 1 with season_type
  "post-season", so they were trained with a 0.90 preseason prior weight (`scripts/cfb_v5_research.py:97-99`).

Small (31 of 7,960 rows). Fix at refit: apply the same mapping.

### 8. LOW, confirmed: labels say v4 on v5 output

- The candidate-evidence packet stamps every candidate probability `model_version: "cfb-structural-v4"`
  (`college_football.py:1374`, `MODEL_VERSION`), although it is priced with the v5 model's inputs and SDs.
- Every v5 ledger rationale begins "CFB Structural v4" (`cfb_structural_v4.py:452, 480, 508`).
- Audit and attribution only; no pricing effect.
- Fix: use `model.version`.

### 9. LOW, replay-only: the history cutoff ignores `observed_at`

**Where:** `college_football.py:765-766` loads history up to 09:00Z on the game date regardless of the decision time.

**Live:** the history files hold only completed games (`ESPNClient.completed_games`), so nothing after the decision can
be in them.

**Replay:** a replay or backfill at a past `observed_at` (or a day-ahead run against a rebuilt history) would see games
completed between the decision and 09:00Z. The features themselves cut at kickoff (`cfb_features.py:156-167`, strict
`<` on full timestamps), which bounds the leak to other teams' games. For v5 those affect only the class and identity
builders, not team states.

**Fix:** use `min(start_of_day, obs)` as the cutoff.

### Known and not re-filed

The v5 artifact (generated 2026-11-12) was fitted on features from before the 11-21 identity, class and sign fixes, and
on the history before the 1,478 + 6,894-row appends. DEBUG.md records "No calibration constant changed ... 427 games is
not enough to refit". The margin slope looks under-stretched (1.53 vs 1.3189). Serving therefore applies calibration
fitted on a different feature distribution. This is already tracked; it should be the first item of any v5 refit.

### v5 team identity in serving (task item 4)

These checks were run; `cfb_team_identity.py` was not re-audited line by line because the 11-21 identity fix
(`tests/test_cfb_team_identity.py`, 11 mutation-proven tests) was declared out of scope.

- **TBD placeholders.** No NCAAF ledger row of any model has a TBD team. For a TBD game, v5 would compute features with
  a no-history team (classified not-FBS, FCS prior). But there is no Polymarket quote and no ESPN odds for such a game,
  so every market hits `continue` (`college_football.py:1122, 1206, 1245`). v6.2 declines explicitly. So v5's
  protection here is incidental (no market), not a gate.
- **Teams with no history.** 0 live v5 rows have `sample_games` ≤ 1, so no live pick was priced on pure conference
  defaults this season.
  - The path still exists: an unknown team is classified not-FBS and gets the FCS tier offset of -18 points, logged
    only as a warning (`cfb_features.py:572-583`). It sets `is_fbs_vs_fcs`, which keeps it out of Main
    (`block_fcs`) but not out of Flat.
  - The three FBS teams the static table lacks (FIU, Sacramento State, Missouri State) are classified FBS from feed
    groups and fall back to the Mid-American tier offset (`_class_conference`).
  - Their games are labelled non-conference (finding 3).
- **Quote matching** on canonical names with a label cross-check is covered by `pricing.md` and the 11-21 quote-side
  fix.

### v5 checks that came back clean

- **Point in time:** team states use rows with start strictly before kickoff, on full timestamps
  (`cfb_features.py:156-167`). The current season is resolved per row (`row_season`). History holds only completed
  games. No history row lacks a score (checked all 16,405), so the `score 0` defaults in `_load_cfb_history:234-243` are
  never hit. QB availability is read at `obs`.
- **Orientation:**
  - Margin is home minus away throughout.
  - The Polymarket spread line conversion is correct (`college_football.py:862-868`): the quote's line belongs to the
    title's first team.
  - `implied_margin = -spread_home_line` in the engine.
  - Spread rows write the selected side's own line.
  - Neutral sites zero out home advantage, travel and elevation in both the Elo and efficiency channels.
  - The bye advantage and QB adjustments have the right signs, and the QB adjustment's total weight is 1
    (`eff_weight + (1 - eff_weight)`).
- **Probability math:**
  - The negative binomial variance hits target_var exactly (r = mu² / (var - mu)).
  - Totals use a normal CDF with ±0.5 continuity on whole lines; pushes are excluded from both sides.
  - Moneyline splits ties 52/48 home (about 0.05 pp; at neutral sites the designated home team still gets 52%,
    negligible).
  - 200k draws keep simulation noise at ±0.22 pp.
- **Calibrators:** `data/calibration/models/cfb-structural-v5.{moneyline,spread,total}_calibrator.json` are all
  `identity`, so no transform is applied by the buyer (`calibrate_for_serving`). There is no market or side mix-up to
  find.
- **Challenger isolation:** each challenger runs in its own try/except, after the champion's rows and candidate
  evidence (`college_football.py:1450-1487`). A failure writes no rows for that challenger. Started games are dropped
  before any write (`cli/forecast.py`, `event_started`).

---

## Challenger claims spot-checked against the code

- **v6.2 zero edge:** `cfb_structural_v62.py:463` reads `if edge <= 0 or ...` on the raw float. Confirmed.
- **v7 intercept:** `cfb_structural_v7.py:140-142` returns `intercept + slope * raw_m` for every game, with no neutral
  term. Confirmed.

## v6.2 challenger (from `models_v62.md`)

1. **MEDIUM, confirmed:** zero-edge contracts are emitted. `cfb_structural_v62.py:463` compares a raw float, so a 0.5
   pmf ± 1e-15 against a 0.50 ask "wins". 5 live rows have edge exactly 0 and 12 have edge below 1e-4. Fix: compare
   the rounded edge.
2. **MEDIUM, confirmed:** v6.2 rows persist no inputs. `inference_inputs` is None, so no `model_input_snapshot_json` is
   written: 0 of 416 rows, against 1051 of 1051 for v5. Anchor value, time, hash and status live only in the rationale.
   Fix: persist `feature_basis`.
3. **LOW-MEDIUM:** no Polymarket quote-age limit in `emit` (lines 467-472). 22 of 416 rows were priced on quotes
   132-517 min old against a DraftKings anchor about 20 min old.
4. **LOW, latent:**
   - DraftKings is matched by name, not `provider_id "100"` (line 51), so 66 "Draft Kings" items are dropped.
   - v6.2 ignores the routed markets and expected hash (`college_football.py:1465`), unlike v7.
   - The static FBS/FCS table is used at serving (`cfb_features_v6.py:154-163`).
   - `v62_serving_code_hash` omits `espn_cfb_pregame_capture.py` and `cfb_data.py`.
5. **Design note:** if the anchor is missing or stale, v6.2 falls back to pricing pure v6 against Polymarket. v6 is up to
   9 points off DraftKings, so this would produce double-digit edges. It has never fired.

## v7 challenger (from `models_v7.md`)

1. **MEDIUM, confirmed:** neutral-site bias. The intercept -1.624 (`cfb_structural_v7.py:140-142`) is applied at
   neutral sites. Actual minus v7 margin is +2.62 ± 0.77 on 477 neutral games, against -0.13 on home games. About 45
   neutral FBS games from 12-05, at 4-6 pp on moneyline and spread.
2. **MEDIUM, confirmed:** the talent prior is standardised against a different population. CFBD talent dropped FCS
   teams from 2025, while `cfb_features_v7.py:161-184` standardises within season. Week 1 shifts by 0.69 pts on average
   (max 1.77); about 0.10 now.
3. **LOW:** the serving fit time differs from the validated rule (`cfb_structural_v7.py:459-470`): the run clock vs
   min(kickoff, 08:00 ET). Points-only rows are added for any game lacking play-by-play (`:369-375`). Not a leak; 0.16
   pts mean on margin.
4. **LOW, suspected:** a single HTTP 404 permanently marks a game `terminal_without_pbp` (`data_sources/cfb_pbp_live.py`).
5. **Store freshness:** there is no clock-based check. A frozen store makes slate teams decline with
   `NO_CALL_V7_PBP_MISSING`, while other teams' missing games drop out silently. v7 rows also persist no feature data.

---

## Overall ranking (all three models)

| # | Sev | Model | Finding | Status |
|---|---|---|---|---|
| 1 | MED | v5 | Spread rule's 0.08 thresholds = max_edge: key-window favourites unreachable for Main; 7 live picks with the better side in band | confirmed interaction (rule deliberate, 46f94dd0) |
| 2 | MED | v7 | Calibration intercept applied at neutral sites (+2.6 pt bias) | confirmed |
| 3 | MED | v5 | Static conference map stale for 2026 (39/340 ESPN conference games mislabelled) feeds the spread rule, SD and total | confirmed |
| 4 | MED | v5 | Moneyline picks probability favourite, not edge (47 of 96 live executable picks) | confirmed, deliberate (5e134c0b) |
| 5 | MED | v6.2 | Zero-edge contracts emitted (float compare) | confirmed |
| 6 | MED | v6.2/v7 | No model inputs persisted on challenger rows | confirmed |
| 7 | MED | v7 | Talent standardisation population shift (FCS dropped by CFBD) | confirmed |
| 8 | LOW-MED | v5 | EPA proxy channel unpopulated since mid-Oct (train/serve drift, 0.5 pt) | confirmed |
| 9 | LOW-MED | v5 | Engine lacks key-number mass (push 2.4% vs about 4-5%); no whole-line picks since 11-14 | confirmed |
| 10 | LOW-MED | v6.2 | No Polymarket quote-age bound | confirmed |
| 11 | LOW | v5 | Recent-era home-side under-prediction (+1.25 ± 0.43 holdout, neutral and home alike) | suspected, pre-fix cache |
| 12 | LOW | v7 | Fit-time and points-only rule differ from the validated rule | confirmed |
| 13 | LOW | v7 | One 404 marks a game play-by-play-terminal forever | suspected |
| 14 | LOW | v5 | Postseason week parity (31 training rows) | confirmed |
| 15 | LOW | v5 | v4 labels on v5 candidate evidence and rationale | confirmed |
| 16 | LOW | v6.2 | DraftKings matched by name; routes and hash ignored; static class; partial code hash | confirmed, latent |
| 17 | LOW | v5 | History cutoff ignores `obs` (replay-only) | confirmed, replay-only |
