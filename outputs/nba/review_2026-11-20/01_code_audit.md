# NBA model code audit (read-only), 2026-11-20

Scope: how NBA moneyline / spread / total picks are produced today, and the logic bugs and
design flaws that make them lose to the market. Repo `e:\MP` (branch refactor/cleanup-2026-11-11,
working tree as of today). Paths are relative to `e:\MP` unless absolute. No repo or runtime file
was modified. Evidence scripts are in this folder: `ledger_peek.py` (read-only SQLite),
`v9_retro.py`, `v9_retro_nofatigue.py`, `v9_retro_summary.py`, `v9_edge_buckets.py`, with
outputs `v9_retro_output.txt` and `v9_retro_nofatigue_output.txt`.

## 0. What serves right now

| Market | Champion (config/production.yaml:961-964) | Registry evidence | Engine |
|---|---|---|---|
| moneyline | nba-elo-trend-lr-v5 | historical_only (production.yaml:190-207) | LR on elo_probability, trend_gap, defensive_trend_gap |
| spread | nba-spread-margin-v9 | predictively_qualified | models/nba_v9.py NBAStructuralV9Engine |
| total | nba-total-margin-v9 | predictively_qualified | same engine, total branch |

- `challengers: NBA: {}`.
- Serving calibrators: data/calibration/models/nba-spread-margin-v9, nba-total-margin-v9 and
  nba-elo-trend-lr-v5. All are `champion_method: identity`, fit on settled live rows (n = 85 / 85 / 49).
  Their raw Brier is 0.2598 / 0.2485 / 0.2286; a coin flip scores 0.25. No calibration is applied anywhere.
- Main gate: config/model.yaml:261 `min_edge: 0.05` and :25 `maximum_unreviewed_market_disagreement: 0.1`.
- Paper auto-buyer (runtime auto_buyer_state.json): whitelists nba-spread-margin-v9 and nba-elo-trend-lr-v5.
  `disabled_sport_markets` contains `[nba, total]`. Edge band is 3.5%-12%.
  DEBUG.md (2026-11-13) records spread v9 being pulled from the whitelist; it is whitelisted again today.
- Provenance: DEBUG.md:1075-1093 says both v9 models became champions on 2026-11-08 (`84258909`)
  "with no promotions-table record and no evidence doc".
  - No code in `src/` or `scripts/` produces the v9 artifacts' `qualification` blocks: spread holdout
    Brier 0.1948, total 0.2235, "Spread win rate 60.0%".
  - No code fits the v9 weights or penalties. `scripts/calibrate_nba_v9.py` only fits post-hoc
    calibrators on settled picks.
- `features/nba_player_logs.py` (real per-player values) is not on the serving path. Serving calls
  `compute_nba_matchup_availability` with defaults, so the static name tiers apply.
- `economic_gate.py` is promotion and verification tooling only.

## 1. Pipeline

```
ESPN scoreboard (events, home/away)            Polymarket US snapshots (odds/nba/<date>/polymarket_snapshots.jsonl)
data/processed/nba/games.jsonl (final scores)  ESPN injury snapshots (data/availability/nba/espn_injury_snapshots)
        |                                                   |
FeatureStore.games_before(date)  features/base.py:270-290   _quotes_known_at + _fresh_latest_per_slug + "ask closest to 50%"
  ET-midnight cutoff (PIT ok); preseason/All-Star dropped     cli/forecast_market_select.py:129-213
  base.py:151-157                                            spread rows anchored to AWAY team, line = away handicap
        |                                                    total rows: long=Over, short=Under
        v
SPREAD / TOTAL  (cli/forecast.py:1650-1909 spread, 2084-2363 total)
  build_elo(HCA 38 Elo, K 20, ln(MOV+1) mult, regress 0.35 after 60d gap)  features/elo_ratings.py:35,121-201
  TrendEngine(hl3/10/25 opponent-adjusted, shrink to lifetime league mean)  features/trends.py:117-351
  compute_nba_matchup_availability (tier points x status)                features/nba_player_availability.py:263-358
  NBAStructuralV9Engine.predict_games                                    models/nba_v9.py:140-426
    pts_side = (off_hl10 + opp_def_hl10)/2 - b2b/3in4 penalties          :169-226
    elo_margin = (Elo_h + 38 - Elo_a)/28                                 :178-180
    margin = 0.70*elo_margin + 0.30*(trend diff + 1.75) + 0.15*rest_diff + availability_gap   :258-264
    total  = pts_a + pts_h + 0.55*(pace_proxy - 99.2) - fatigue - 0.15*avail_loss + OT bump  :229-283
    P(away cover) = Phi((away_line - margin)/14.0); P(over) = 1 - Phi((line - total)/18.0)  :344-345, :386-388
  side = whichever side the model puts >= 50%  (forecast.py:1845-1853, 2289-2297)
  total only: load_serving_calibrator -> identity (forecast.py:2279-2304)
  edge = model_prob - executable ask (no fee; raw ask, not vig-free)
  Flat: every contract.  Main: CALL and 5% <= edge <= 10% (spread forecast.py:2052-2063;
  total forecast.py:2504-2517 also needs qualified; intends a 9% floor for thin-sample overs)

MONEYLINE (cli/forecast.py:3765-4300 -> learned_forward.build_learned_moneyline_slate :604-943)
  build_elo(defaults: HCA 70, gap 90), TrendEngine(defaults)  :680-693  (parity with validation.py:393-394 OK)
  gate: min(current_season_games_played) >= 8 (model.yaml:262)  :783-792
  p_home = logistic(-1.94 + 3.60*elo_prob - 0.0018*trend_gap - 0.0115*def_trend_gap)  (nba-elo-trend-lr-v5.json)
  side = model favourite (>= 50%)  :885-886;  no injury input for NBA (WNBA only, :821)
  edge = model_prob - Polymarket ask, or ESPN consensus implied prob if no quote  forecast.py:4012-4021
  Main band 5%..8% (max_edge defaults 0.08)  :4201-4211;  then downgraded because evidence is
  historical_only :3845-3858, :4218-4227 (stays CALL, qualified=False)
        |
eligibility._call_result -> edge_scaled_units (spreads/totals 1.0-2.0U, ML fixed 1.0U)  eligibility.py:288-327, units.py:50-78
model_uncertainty is a constant 0.03 for NBA spread/total (nba_v9.py:304; sample is capped at 200)
        |
Main / Flat ledgers (SQLite ledger_records) -> paper auto-buyer (whitelist + 3.5%-12% band)
```

## 2. Replay of the served v9 engine against closing lines

`v9_retro.py` replays the exact v9 artifact per ET date. It uses `games_before`, the same
point-in-time chokepoint as serving, and sets availability to 0 because no historical injury
snapshots exist. It joins `data/odds/nba/historical_closing_lines/nba_closing_lines_2024_2026.jsonl`
(Kaggle closing lines, 2,572 games, 2024-11 to 2026-06). That file has no closing moneyline prices,
so the moneyline was not replayed.

| Metric (all 2,572 games) | Margin (spread) | Total |
|---|---|---|
| RMSE: model vs actual | 14.59 | 19.21 |
| RMSE: closing line vs actual | 13.90 | 17.91 |
| corr(model - close, actual - close) | **-0.017** | **-0.005** |
| SD of (model - close) | 4.21 pts | 6.39 pts |
| mean of (model - close) | -0.45 | **-2.03** (2025-26: -2.90) |
| SD of model projections vs SD of close | 5.78 vs 8.04 | 5.04 vs 9.11 |
| slope of actual on model | 1.28 (compressed) | 1.29 (compressed) |
| hit rate of model side vs close | 49.3% ATS | 48.4% O/U; 66% of picks are unders (72% in 2025-26) |

Hit rate vs close by model-implied edge over a 50% fair price (`v9_edge_buckets.py`):

| Model edge | Spread: share of games, hit rate | Total: share of games, hit rate |
|---|---|---|
| under 3.5% | 22%, 49.6% | 18%, 49.2% |
| 3.5-5% | 9%, 46.0% | 8%, 45.5% |
| 5-10% (Main band) | 27%, 49.4% | 24%, 47.0% |
| over 10% | 41%, 49.8% | 50%, 49.1% |

Reading: the engine has no information the closing line lacks. Hit rate is about 49-50% in every
edge bucket, so this is no skill rather than anti-skill. The engine still reports at least 5pp of
edge on 68% of spreads and 74% of totals. Buying those at an ask of 51-52c loses the spread, about
2% to 4% per bet.

Sizing the totals bias (`v9_retro_nofatigue.py`): with b2b/3-in-4 penalties, pace drag and the OT
bump switched off, model - close moves from -2.03 to -0.48 overall (2025-26: -2.90 to -1.38), and
the under share drops from 66% to 56%. Skill is unchanged (corr -0.001). The one-sided fatigue
penalties are therefore the main measured cause of the low bias. Live serving also subtracts the
injury shave (#6), which the replay leaves out, so the live bias should be larger.

Live ledger (settled rows, one per event, since 2026-10-20):

| Model | Market | Ledger | Record | Units |
|---|---|---|---|---|
| nba-spread-margin-v9 | spread | Main | 14-9 | +5.2U |
| nba-spread-margin-v9 | spread | Flat | 43-42 | +0.4U |
| nba-total-margin-v9 | total, over | Main | 3-4 | -1.2U |
| nba-total-margin-v9 | total, under | Main | 11-7 | +3.9U |
| nba-total-margin-v9 | total, over | Flat | 14-13 | +2.4U |
| nba-total-margin-v9 | total, under | Flat | 25-33 | -11.5U |
| nba-elo-trend-lr-v5 | moneyline | Flat | 30-19 | -3.9U |

These live samples are too small to separate from noise. The 2,572-game replay is the evidence.

## 3. Findings

Severity: BUG = code does something other than intended; FLAW = design choice that loses money; MINOR.
"Not measured" marks mechanisms read from code whose PnL size was not quantified.

| # | Sev | Finding | Evidence | Expected PnL effect | How to confirm |
|---|---|---|---|---|---|
| 1 | FLAW | No market information in NBA spread/total pricing: `market_blend_weight 0.0`, identity calibrators, edge = raw model vs ask. The replay shows zero market-relative skill (section 2). The artifact qualification (holdout Brier 0.1948 spread / 0.2235 total, better than a coin flip) contradicts this and has no producing code. Yet that block plus `predictively_qualified` sets `model_qualified` (forecast.py:1682-1686), which opens Main. | nba-spread-margin-v9.json `market_blend_weight: 0.0` and `qualification`; nba_v9.py:266-271; identity calibrators; DEBUG.md:1091-1093 | Main and auto-buyer picks are coin flips bought at a 1-2c premium: about -2% to -4% ROI per bet before fees | `v9_retro_summary.py v9_retro_rows.jsonl`; `v9_edge_buckets.py v9_retro_rows.jsonl` |
| 2 | FLAW | The edge band is filled by noise. With sigma 14 and a 4.2-pt model-close SD, 27% of spreads and 24% of totals land in the 5-10% Main band and 41-50% land above 10%. In-band hit rate vs close is 49.4% (spread) and 47.0% (total). Sizing is edge-scaled, so bigger fake edges get 1.25-1.5U. | forecast.py:2056, 2516; units.py:70-77; section 2 bucket table | No skill, and pays the spread on every pick; edge-scaling adds stake to the same noise | Bucket table above |
| 3 | BUG | `current_season_games_min` in v9 is built from lifetime `games_played`, not `current_season_games_played`. Live rows read 220-229. The OT "sample maturity" is therefore always 1.0, and the thin-sample over floor (#4) could never trigger even with the right key. TeamTrend's docstring forbids this use. | nba_v9.py:275 vs trends.py:42-53, 332-343; ledger Main v9 over rows: min_games 222-229, edges 5.1%-9.8% | Early-season overs enter Main at the 5% floor instead of 9%. v9 Main overs are 3-4 (-1.2U); too few to size | `ledger_peek.py`; print both counts for a November slate |
| 4 | BUG | Even with #3 fixed, the totals thin-sample floor reads `model_input_snapshot["feature_basis"]`, but `build_snapshot` stores the dict under `"features"`. `fb` is always `{}`, `min_games` defaults to 15, and the 9% floor never applies. | forecast.py:2508-2512; scalar_artifact_replay.py:281-287 | Same as #3: a dead risk control | No Main over row has a `feature_basis` key in its snapshot |
| 5 | FLAW | Totals are biased low and compressed. Measured: switching off the fatigue terms and the OT bump together moves the bias by +1.55 pts (section 2). The OT bump adds about +0.7, so the one-sided fatigue terms alone are worth about 2 pts (inferred): b2b -2.0/-1.5, 3-in-4 -0.8/-1.2, both-b2b -1.0, density -0.6, pace drag; the baseline already averages tired and rested games. Candidate causes, not decomposed: (b) the shrinkage target `league_baseline` is the mean of all games since 2024-01 (about 227.7 per game vs 230.2 in 2025-26); (d) side points use `(off+def)/2`, which halves matchup deviations, plus a points-based "pace" proxy that re-adds part of them (projection SD 5.0 vs close 9.1). Live only, on top of this: (c) `-0.15 * availability_total_loss`, about 1.5 pts on a typical game. | nba_v9.py:174-175, 218-256; trends.py:186-190 | Replay: 66-72% unders, O/U 48.4%; matches the reported "+2.7 pts live vs model" | `v9_retro_nofatigue.py`; next variants: trailing current-season baseline and additive `off+def-baseline` |
| 6 | FLAW | Injury adjustment is an unfitted heuristic. Every listed player defaults to 1.1 pts and is charged in full, including season-long absences already in Elo and the hl10 trends (double count). `return_date` is ignored. Tiers are hand-made and exact-match (`Jimmy Butler` vs feed `Jimmy Butler III`). The gap is added to the margin at full weight while the trend half is weighted 0.30. v9 weights were never fit with it. Today's snapshot: 108 entries, 87 at the 1.1 default, team loss median 4.95 pts, max 11.7. | nba_player_availability.py:38-129, 321-340; nba_v9.py:255-256, 264; forecast.py:1780-1806 | Not measured here; the known MAE rise from 11.78 to 12.51 fits. A systematic -1.5 on live totals | Replay November games with live snapshots, with and without `availability_points_gap`: MAE and corr to the close residual |
| 7 | FLAW | Elo looks too reactive for NBA: K=20 with the NFL-style `ln(MOV+1)` multiplier gives an effective K of about 50 at a 12-pt margin, vs about 23 for 538's NBA formula. The `/28` points-per-Elo conversion assumes 538-scaled ratings. | elo_ratings.py:35, 126-133; nba_v9.py:180 | Not measured | Per season, regress actual margin on (Elo diff + HCA) and compare the slope with 1/28; grid K on replay RMSE |
| 8 | FLAW | Moneyline LR v5 qualification is not a market test: "73% hit rate, +236 units at the 52.4% break-even price" scores picking favourites at a fixed coin-flip price. Holdout calibration slope 1.83 says the model understates favourites (70-80% bucket hit 84% vs 75% predicted). Only the model favourite is selected (learned_forward.py:885-886), so "edge" should arise mainly where the market disagrees on who is favoured. | nba-elo-trend-lr-v5.json qualification; forecast.py:4018 | Not measured. Flat v5 is 30-19, -3.9U (n=49), consistent with paying favourite prices without an edge | Join Flat ML rows to the decision ask; check hit rate vs mean ask by edge bucket; refit on logit(elo_prob) |
| 9 | BUG | Clippers road spreads are not priced. ESPN `displayName` is "LA Clippers", Polymarket rows say "Los Angeles Clippers", `_team_matches` returns False (verified), and spread candidates filter on the away team only. Totals match on title words, so they still work. | forecast.py:1769-1774; learned_forward.py:1096-1121. Ledger: Clippers road spread rows exist only for 10-24 and 10-25 (v2/v5); 11-10 and 11-19 have ML and total rows but no spread row | Coverage loss, not a loss driver | `_team_matches('LA Clippers','Los Angeles Clippers') -> False` |
| 10 | BUG (latent) | The market-blend hook has the wrong sign: `market_home_margin = -spread_away_line`, but the expected home margin equals `+away_line` (away +6.5 means home is favoured by 6.5). Inactive while the weight is 0. The natural fix for #1 (weight > 0) would push away from the market. | nba_v9.py:268; nba_v7.py:243 | None today; large if switched on | Unit test: away line +6.5 with weight 1.0 should give margin +6.5 |
| 11 | FLAW | Cover sigma is a constant 14.0 (total 18.0). Replay residual SD is 14.6 (margin) and 19.0 (total), so slightly overconfident, with no early-season or injury widening. `model_uncertainty` is a constant 0.03, so sizing ignores it. | nba_v9.py:286-304 | Overstates edges by about 4-5% of their size; secondary to #1 | Residual SD by month and by line-size bucket from the replay rows |
| 12 | FLAW | The OT bump (+1.2 when |margin| <= 3.5, +0.6 up to 6.5) double counts: trend levels come from final scores that already include OT. | nba_v9.py:273-283 | Small (about +0.7 a game); masks part of #5 | Replay with only the OT bump off |
| 13 | MINOR | B2B penalties reach the margin only through the 0.30 trend weight (a -2.0 home b2b moves the margin 0.6), plus part of rest_diff, though the artifact describes them as full-point effects. | nba_v9.py:219-226, 258-262 | Margin misses most of the rest signal | Replay split on b2b games |
| 14 | MINOR | The injury snapshot has no freshness bound: the latest file at or before the cutoff is used however old (fails open, not closed). Snapshots are current today. | nba_player_availability.py:179-230 | Risk only if capture stops | Pause capture and check rows still carry injuries |
| 15 | MINOR | The ML gate `minimum_team_history_games: 8` is inert for LR v5. TrendEngine is built without `offseason_gap_days`, so `current_season_games_played` counts lifetime rows. Train/serve parity holds. | learned_forward.py:680-693, 783-792; trends.py:332-339 | Opening-week ML calls use prior-season form | Print the gate values on an October date |
| 16 | MINOR | ML rows store `event_start_utc` in ET with an offset (`-0500`/`-0400`, forecast.py:4040-4047); spread and total rows use `...Z`. | ledger_records.event_start_utc | Bookkeeping and CLV-join risk only | Join ML to spread rows by start string vs by event_id |
| 17 | MINOR | `engine_hash` for `margin_normal`/`total_normal` hashes basketball.py, but v9 runs nba_v9.py, so replay provenance does not cover the serving engine. | scalar_artifact_replay.py:21-27 | None (audit integrity) | Edit nba_v9.py; `engine_source_sha256` does not change |
| 18 | MINOR | Edge ignores exchange fees and uses the raw ask (correct as a cost, but not vig-free). The ask goes through a probability-to-integer-price round trip in eligibility (<= 0.1pp). No double-vig error was found. | forecast.py:2056, 2504; eligibility.py:302; units.py:70 | A few tenths of a pp per bet if a taker fee applies | Compare auto-buyer fee-inclusive fills with the ask |

Checked and found OK:
- Spread sign: rows anchor to the away team, `line` is the away handicap, and `P(away cover) = P(home_margin < away_line)` is correct. Home side uses `-line` and the short ask.
- Over/under mapping: long = Over, short = Under.
- Point-in-time: history is cut at ET midnight; quotes and injury snapshots are filtered to `observed_at`; no closing data or box scores reach serving. LR v5 Elo/Trend train/serve parity holds (validation.py:393-394 vs learned_forward.py:680-693).
- Exhibitions excluded (base.py:151-157).
- Home-court edge is about 1.5 pts in effect: replay mean margin is +1.58 model vs +1.86 actual.
- Polymarket NBA spread and total lines are half-points, so push mass is moot.
- Settled ledger rows are one per event, so the W-L figures above are not inflated by repeat runs.

## 4. What to keep for a rebuild

- Keep the plumbing:
  - `FeatureStore.games_before` (ET-midnight point-in-time chokepoint, exhibition filter).
  - Quote freshness and balanced-line selection (`_quotes_known_at`, `_fresh_latest_per_slug`).
  - Polymarket row conventions: away-anchored handicap, long = Over.
  - Edge measured against the executable ask, and the ledger/eligibility plumbing.
  - Scalar snapshot replay (fix the engine-hash map first).
  - The `load_serving_calibrator` guard, which correctly refused the foreign Beta calibrator on 11-11.
- Keep the injury data layer (ESPN snapshots, hash-verified, point-in-time filtered) and replace the valuation:
  - per-player minutes- or box-score-weighted value (`features/nba_player_logs.py` already exists);
  - charge only changes relative to the team's recent sample, and use `return_date`;
  - fail closed past a maximum snapshot age;
  - fit one coefficient walk-forward.
- Discard or redo:
  - the hand-set v9 weights and penalties, the `(off+def)/2` point construction, the points-based pace proxy and the OT bump;
  - the lifetime league baseline and the NFL-style Elo MOV multiplier for NBA;
  - the v9 and LR v5 qualification blocks (no producing code; hit rate scored at a fixed 52.4% price).
- Rebuild principle: model the residual to the market line. Start from the Polymarket mid or the
  consensus close and add only features that move it. Fit walk-forward on closing-line residuals.
  Require corr(model - close, actual - close) > 0 with a confidence interval before any Main band.
  Until then the honest NBA edge is about zero.
