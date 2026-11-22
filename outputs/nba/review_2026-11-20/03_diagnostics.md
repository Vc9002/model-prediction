# NBA v9 structural engine: logic-problem diagnostics (EXPLORATORY)

Date 2026-11-20. Read-only replay: nothing was fit for promotion, and the locked holdout was not scored.

## Setup

- **Engine replayed:** the served champions `nba-spread-margin-v9` / `nba-total-margin-v9`
  (`models/nba_v9.py::NBAStructuralV9Engine`, built from the frozen artifact JSON). The moneyline
  champion `nba-elo-trend-lr-v5` was replayed through `validation.build_walk_forward_rows`, the
  construction it was fit on.
- **Point-in-time rule:** for each ET date D, history = `FeatureStore.games_before("nba", D)`
  (the production cutoff at ET midnight). Minimum history is 200 games.
  - Availability terms are 0, because historical injury snapshots don't exist. This is the one
    served input the replay does not cover.
  - The decomposition parity check passes (02_tables §8e). On non-b2b rows,
    exp_margin − [0.7·Elo + 0.3·(trend + 1.75) + 0.15·rest] clusters only at
    {−0.24, 0, +0.12, +0.36}, which are exactly the 3-in-4 terms.
- **Data:** `data/processed/nba/games.jsonl` has 3,915 rows and **starts 2024-01-01**, partway
  through 2023-24. That gives about 2.8 seasons, and Elo cold-starts at 1500 in January 2024.
  - v9 is points-only and uses no box-score data.
- **Market lines:** `data/odds/nba/historical_closing_lines/nba_closing_lines_2024_2026.jsonl`
  (Kaggle closing spread and total, 3,481 games, 2024-01-01..2026-06-14). It covers 100% of
  train and validation.
  - Sign convention checked: corr(model margin, line) = 0.86 and corr(actual, line) = 0.50.
- **Split:** 60/20/20 on complete ET dates (572 dates).
  - Train: 2024-01-29..2025-11-02 (n=2,049).
  - Validation: 2025-11-03..2026-02-22 (n=762). It is capped at the v5 registry's recorded
    holdout start, 2026-02-23.
  - Holdout: 692 rows from 2026-02-23, dropped and never scored.
- **Scripts (this folder):** `01_replay_v9.py`, `01b_replay_ml_v5.py`, `01c_live_served.py`,
  `02_diagnostics.py` → `02_tables.md` (every table, with n).

## Headline: the model's disagreement with the market carries no information

| market | split | n | corr(model−line, actual−line) | RMSE model | RMSE closing line |
|---|---|---|---|---|---|
| margin | train | 2049 | −0.02 | 14.49 | 13.79 |
| margin | val | 762 | −0.02 | 14.59 | 13.97 |
| total | train | 2049 | +0.01 | 18.87 | 17.62 |
| total | val | 762 | −0.02 | 19.42 | 18.18 |

Shrink-to-market test: pred = line + λ·(model − line).

| market | λ fit on train (OLS) | λ argmin on VAL | val RMSE line | val RMSE blend (train λ) | val RMSE model |
|---|---|---|---|---|---|
| margin | −0.05 | −0.03 | 13.97 | 13.97 | 14.59 |
| total | +0.03 | +0.01 | 18.18 | 18.18 | 19.42 |

- The optimal weight on the model is about 0.
- The model still departs from the line by an SD of **4.1–4.2 pts on margin** and **5.7–7.0 pts
  on total**. All of that departure is noise.
- Moneyline shows the same thing (line-implied probability = Φ(line/13.79), σ taken from train):

| split | n | Brier line-implied | Brier ML v5 (served) | Brier v9 coherent ML | Brier-optimal logit weight on v5 |
|---|---|---|---|---|---|
| train | 2049 | 0.2007 | 0.2168 | 0.2173 | 0.00 |
| val | 762 | 0.2122 | 0.2232 | 0.2242 | 0.00 |

## 1. Totals: the model lags scoring drift, by construction

| season (reg) | n | actual | model | act−model | line | act−line | line−model | 2×engine baseline |
|---|---|---|---|---|---|---|---|---|
| 2023-24 (Jan–Apr) | 546 | 224.96 | 226.96 | −2.00 | 225.46 | −0.49 | −1.51 | 229.19 |
| 2024-25 | 1231 | 227.61 | 224.94 | +2.67 | 226.87 | +0.74 | +1.93 | 226.74 |
| 2025-26 (to 02-22) | 856 | 230.97 | 227.14 | **+3.83** | 231.13 | −0.16 | **+3.99** | 228.18 |

PIT season-to-date points per team vs the baseline the engine anchors to:

| season | n | season-to-date | engine baseline | gap |
|---|---|---|---|---|
| 2024-25 | 1195 | 113.19 | 113.37 | −0.18 |
| 2025-26 | 823 | 116.56 | 114.11 | **+2.46** (×2 ≈ 4.9 pts of game total) |

**Monthly pattern:** the market tracks the scoring level and the model doesn't.

| month | split | n | act−model | act−line | line−model |
|---|---|---|---|---|---|
| 2025-01 | train | 227 | +2.25 | +0.21 | +2.04 |
| 2025-02 | train | 176 | +5.01 | +0.72 | +4.29 |
| 2025-10 | train | 80 | +7.83 | +3.26 | +4.57 |
| 2025-11 | train | 219 | +6.20 | +0.45 | +5.76 |
| 2025-12 | val | 198 | +5.87 | +0.38 | +5.49 |
| 2026-01 | val | 233 | −0.57 | −3.54 | +2.98 |

The bias does not fade within the season. Regular season 2024-25 onward, total act−model by
current-season games played:

| games played | 0–5 | 6–10 | 11–20 | 21–40 | 41+ |
|---|---|---|---|---|---|
| act−model | +4.8 | +3.4 | +3.6 | +2.1 | +3.4 |

(Full table in §3.)

**Live 2026-27 season.** This is served output from the runtime ledger (mode=ro): the latest v9
row per event, events 2026-11-08..2026-11-19, n=85. It is bias only, because these dates sit
inside the holdout window.

| metric | value |
|---|---|
| actual − served v9 total | +3.91 |
| actual − Polymarket line | +2.96 |
| line − model | +0.95 |

**The "+2.7" figure was not reproduced.** On this window the line sits +0.95 above the model,
and the model sits about 3.9 below actual results.

**Mechanism (code, `features/trends.py` + `nba_v9.py`):**
- `TrendEngine.league_baseline` is the **unweighted mean of every non-postseason game in
  history**, back to 2024-01. Every team's offense/defense is:
  - shrunk toward it, with a 12-game prior, and
  - opponent-adjusted by dividing by `opp_level / baseline`.
- Both steps renormalise every team to the multi-season mean. In 2025-26 the summed trend points
  were 228.6 against 2×baseline of 228.2, while actual scoring ran about 231.
- `league_pace` is a constant 99.2. "Possessions" are inferred from points-relative-to-baseline,
  not from box-score pace. So pace can't adapt either, and it is a second function of the same
  points signal.
- `offseason_retention=0.40` re-weights games, but nothing re-centres the baseline.

**Playoffs:** the engine prices playoff totals as if they were regular season.

| type | n | total act−model | total act−line |
|---|---|---|---|
| post-season | 166 | **−12.98** | +0.19 |
| play-in | 12 | −8.50 | +0.67 |

**Drift-fix check (02_tables §8c, regular season):** adding 2×(PIT season-to-date ppg − baseline)
moves validation bias from +3.34 to −1.25. But RMSE only goes from 19.42 to 19.10 (line: 18.18),
and corr(model−line, actual−line) stays at −0.02. The bias is real and worth fixing, but
**fixing it does not create edge**.

## 2. Home-court advantage

| season (reg) | n | actual home margin | model margin | act−model | closing line | act−line | home win% |
|---|---|---|---|---|---|---|---|
| 2023-24 | 546 | 1.63 | 1.46 | +0.17 | 1.80 | −0.17 | 51% |
| 2024-25 | 1231 | 1.62 | 1.56 | +0.06 | 1.89 | −0.27 | 54% |
| 2025-26 | 856 | 1.31 | 1.46 | −0.15 | 1.91 | −0.59 | 54% |

- **Served HCA:** 0.70 × (38 Elo / 28) + 0.30 × 1.75 = **1.48 pts**.
- **Regression:** OLS of actual margin on Elo diff (without HCA) gives an intercept of **1.73**
  (b = 1.02, n = 2,811).
- HCA is roughly right and is **not** a structural problem. The closing line prices home teams
  0.2–0.6 pts too high.

## 3. Early season vs later (regular season 2024-25+, rows with lines)

| current-season games (min of the two teams) | n | RMSE margin model | RMSE margin line | mean abs(model−line) | corr(m−l, a−l) | RMSE total model | RMSE total line | total act−model |
|---|---|---|---|---|---|---|---|---|
| 0–5 | 162 | 12.98 | 12.95 | 3.29 | +0.15 | 19.87 | 19.08 | +4.81 |
| 6–10 | 151 | 13.13 | 12.14 | 2.95 | −0.13 | 18.58 | 17.10 | +3.38 |
| 11–20 | 306 | 13.14 | 12.91 | 2.91 | +0.07 | 19.99 | 18.85 | +3.59 |
| 21–40 | 606 | 14.63 | 14.05 | 3.37 | +0.01 | 18.74 | 17.51 | +2.08 |
| 41+ | 862 | 14.94 | 14.20 | 3.43 | −0.03 | 18.89 | 17.76 | +3.38 |

- On margins, early season is not worse relative to the line. The +0.15 at 0–5 games has n = 162
  and SE about 0.08, so it is not significant.
- On totals, the drift bias is present all season and is largest in games 0–5.
- **Bug:** the engine's `current_season_games_min` uses **lifetime** `games_played`, not
  `current_season_games_played`. The trends.py docstring warns about exactly this. On 2,163 of
  2,811 rows it overstates maturity by more than 5 games, so the "sample-scaled" overtime boost
  (+1.2 / +0.6 pts on total) runs at full strength from opening night. A live ledger row on
  2026-11-08 shows `current_season_games_min=220`.

## 4. Rest and back-to-backs (regular season, rows with lines; SE ≈ 0.8 margin, 1.0 total at n≈300)

| b2b | n | margin act−line | margin model−line | margin act−model | total act−line | total model−line |
|---|---|---|---|---|---|---|
| neither | 1846 | −0.19 | −0.28 | +0.08 | +0.23 | −1.53 |
| home b2b | 307 | −1.29 | **+1.06** | −2.35 | +1.21 | −2.93 |
| away b2b | 330 | −1.07 | **−2.06** | +0.99 | +0.20 | −1.66 |
| both b2b | 150 | +1.11 | −0.69 | +1.80 | −2.34 | −4.65 |

Shift in mean home margin relative to "neither" (decomposition in 02_tables §8b):

| situation | model | closing line | actual |
|---|---|---|---|
| home b2b | −0.8 | −2.1 | −3.2 |
| away b2b | +0.2 | +1.9 | +1.1 |

**Margins: the b2b effect is diluted.** The penalties (−2.0 home, −1.5 away) are subtracted from
home_points/away_points. Those only reach the margin through `trend_weight = 0.30`, plus
`rest_weight = 0.15 × rest_diff`. So the model moves about a third as much as the market.

This is measured precisely: model−line has SD of about 4, so SE ≈ 0.23. Relative to "neither",
model−line moves +1.3 for home b2b and −1.8 for away b2b. Those divergences go against the
direction of the outcomes (actual moves −3.2 / +1.1, SE ≈ 0.8).

**Totals: the evidence is mixed.** Relative to "neither", the model takes 2.0 (home b2b),
1.6 (away b2b) and 2.8 (both) pts off the total. The comparison:

| situation | model | closing line | actual (SE 1.0–1.5) |
|---|---|---|---|
| home b2b | −2.0 | −0.6 | +0.4 |
| away b2b | −1.6 | −1.5 | −1.5 |
| both b2b | −2.8 | +0.3 | −2.2 |

Only the home-b2b total penalty looks over-applied, and that rests on n=307.

## 5. Sigma and calibration

| split | SD margin resid (model) | SD margin resid (line) | SD total resid (model) | SD total resid (line) |
|---|---|---|---|---|
| train | 14.48 | 13.79 | 18.87 | 17.61 |
| val | 14.59 | 13.95 | 19.13 | 18.17 |

Served σ = 14.0 / 18.0, which is slightly low but secondary. The real problem: the engine prices
Φ((line − model)/σ), and model − line is noise with SD about 4. So noise becomes 60–70%
probabilities.

Spread cover calibration (picked-side probability):

| model side prob | train n | train model % | train hit % | val n | val model % | val hit % |
|---|---|---|---|---|---|---|
| 50–52.5 | 310 | 51.2 | 54.2 | 122 | 51.2 | 50.0 |
| 52.5–55 | 315 | 53.8 | 50.8 | 116 | 53.8 | 46.6 |
| 55–60 | 552 | 57.5 | 52.5 | 213 | 57.4 | 46.5 |
| 60–65 | 477 | 62.4 | 50.9 | 181 | 62.6 | 50.8 |
| 65+ | 394 | 69.9 | **44.9** | 123 | 70.0 | 49.6 |

Totals have the same flat shape. For example, the val 65%+ bucket is 71.0% model vs 51.9% hit.

| split | spread cover Brier | total over Brier | hit% at model side (spread / total) |
|---|---|---|---|
| train | 0.2656 | 0.2687 | 50.7 / 50.5 |
| val | 0.2638 | 0.2689 | 48.6 / 47.8 |

- All four Brier scores are worse than a coin flip (0.2500).
- Widening σ only approaches 0.25 (val: σ=30 gives 0.2535; σ=60 gives 0.2510). That is λ≈0
  expressed another way.

## 6. Market information

- See the headline tables. Margin, total and moneyline add no information beyond the closing
  line in train or validation: corr ≈ 0, λ ≈ 0, logit weight on v5 = 0.00.
- Caveat: the replay zeroes the availability terms. So "no information" is shown for the
  Elo + trend + rest/fatigue core, not for the served availability adjustment.
- Closing lines are a stricter benchmark than Polymarket decision-time quotes.

## 7. PIT-leak and integrity signals

- **Engine data path: no leak found in code.**
  - History is cut at ET midnight (`games_before`).
  - Rest uses `p.start < game_start`, and the trend and Elo objects see only that history.
  - The engine has no fitted coefficients, so train≈val is not itself evidence either way.
  - The best evidence against a leak is that there is no skill versus the line at all.
  - The availability path (`compute_nba_matchup_availability`) was not inspected.
- **Artifact qualification metrics can't be reached against real lines.**
  - The v9 spread `holdout_brier_score` is 0.1948 ("60% spread win rate", "+6.34u"). The
    closing-line cover Brier measured here is 0.263–0.266, and no σ gets below about 0.251.
  - 0.1948 is on the scale of a straight-up win Brier (v9 coherent ML: 0.217–0.224).
  - **Provenance was not located.** No script in `scripts/` emits these numbers. The v7 research
    harness (`scripts/test_combo_features_v7.py::evaluate_engine`) scores "spread" as
    `home covers zero` (line 0), which fits the pattern.
- **Latent sign bug in the market-blend hook.**
  - The hook sets `market_home_margin = -spread_away_line`. But the cover formula, the
    production quote mapping and the Kaggle data (checked above) all treat `spread_away_line`
    as the home-margin line: away covers iff margin < line.
  - It is inert while `market_blend_weight = 0.0`. If the weight is ever raised, the blend
    pulls toward the *negated* market line.
- **Cold start:** history begins 2024-01-01. Elo starts at 1500 in the middle of a season, and
  the first weeks of train carry that.

## Structural reading (exploratory, train/val only)

1. **The core problem is no signal.** The Elo + points-EWM + rest core reproduces what the
   closing line already contains, and adds noise of about 4 pts margin and 6–7 pts total. With
   σ=14/18 the engine prices that noise as 60–70% probabilities. Spread and total Brier end up
   worse than a coin flip, and the shrink-to-market λ ≈ 0.
2. **Totals are anchored to a stale multi-season baseline.** Shrinkage and opponent adjustment
   both normalise to `league_baseline`, the mean of all history. Pace is a constant 99.2,
   derived from points. Result: +2.7 to +3.8 per season, +6 to +8 in early months, and about
   +3.9 live. Playoff totals are about 13 pts too high.
   - A PIT re-centring fixes the bias but not the edge.
3. **Back-to-backs move the margin about a third as much as the market**, because the penalty
   passes through the 0.30 trend weight. Total penalties are full-weight, and only home b2b looks
   over-applied.
4. **HCA (1.48 vs 1.73 empirical) and σ (14 vs 14.5) are second-order.**
5. **Bugs and hygiene:**
   - the overtime maturity scaler uses lifetime games;
   - the market-blend hook sign is flipped (inert today);
   - the artifact qualification numbers can't be reproduced against market lines.
