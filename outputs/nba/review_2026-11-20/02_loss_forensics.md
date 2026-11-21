# NBA loss forensics (paper ledger), 2026-11-20

Read-only. Sources:
- `E:\model-prediction-runtime\ledgers\ledgers.db`, tables `ledger_records` and `ledger_events` (sport='nba').
- Polymarket snapshot history: `E:\model-prediction-runtime\odds\nba\*\polymarket_snapshots.jsonl`.

Scripts in this folder: `load.py`, `validate.py`, `enrich.py`, `analyze.py`, `extra.py`, `checks.py`.
Raw output: `analysis_out.md`, `extra_out.md`, `checks_out.md`.
Prices are implied probability (ask or midpoint) or cents.

## 0. Population and validation gates

- **Row counts:** 3,442 NBA rows (settled 675, superseded 2,732, open 35).
  - No settled NBA pick was voided, archived or removed. `ledger_events` holds 8 `remove` events, all on unsettled 10-20 opening rows.
  - The runtime root has no NBA archive files.
- **Dedupe** on (event, selection, line, model), keeping Main over Flat: **512 settled picks on 205 games**.
- **Headline = verified Polymarket executable quote at decision: 480 picks.**
  - Every settled spread and total row is quoted. The quoted/unquoted split matters only for ML.
  - The 32 unquoted rows are ML: `elo-trend-lr-v4` at a default price, and `moneyline-v7` at DraftKings consensus. They went 22-10, +5.92u, and are excluded.
  - Regimes: QUAL (edge-qualified, 282), FLAT (flat every-game call, can have negative edge, 174), RESEARCH (24).
- **Gates (all passed):**
  - Re-grading from stored scores, selection and line gives 0 mismatches. `line` is the selected side's own line.
  - `model_probability` reproduces from `inference_inputs` to <0.0001 for every spread/total model. The exception is 14 v9-total rows that carried an extra calibration layer (§8).
  - The snapshot join (by market slug) reproduces the ledger decision ask on 597/597 joined rows (max diff 0.0008).
  - The 50c fair line, interpolated from the snapshot ladder, sits within a median 0.17 pts of the pick line when the ask is 47-53c (p95 0.63, max 1.50).
  - 0 timestamp parse failures: `Z`, `-0500` and short `T01:00Z` were all parsed to UTC.
- **The 11-18 figures reproduce exactly** (event before 11-18 12:00Z, edge>=2%):
  - Totals **56-61**, spreads **58-72**, ML edge>=10% **17-18**.
  - The ML 17-18 includes 8 unquoted rows. Quoted-only it is **9-13** (ROI -18.0%).
  - Through today: totals 61-68 (ROI -5.3%), spreads 67-73 (ROI -3.5%).
- **On n and correlation:**
  - Within one market each game appears once, because model versions ran in sequence, not in parallel.
  - Pooled cross-market figures share games, so their CIs use a game-clustered bootstrap.
  - n<15 is marked unproven. Unless stated otherwise, CIs are Wilson 95%.

## 1. Record, ROI, calibration by model (quoted)

ROI = sum(pnl) / sum(units staked).

| mkt | model | n | W-L | hit (95% CI) | mean model | mean ask | mean mid | pnl u | ROI |
|---|---|---|---|---|---|---|---|---|---|
| ML | elo-trend-lr-v4 | 78 | 52-26 | 66.7% (56-76) | 65.8% | 64.9% | 64.7% | -2.88 | -3.4% |
| ML | elo-trend-lr-v5 | 49 | 30-19 | 61.2% (47-74) | 70.2% | 69.2% | 68.7% | -3.85 | -7.9% |
| ML | moneyline-v9 (unproven, all FLAT) | 11 | 10-1 | 90.9% | 62.8% | 73.8% | 73.3% | +3.12 | +28.4% |
| spread | v2 | 21 | 8-13 | 38.1% (21-59) | 58.8% | 50.9% | 50.2% | -5.21 | -18.1% |
| spread | v4 (unproven) | 8 | 5-3 | 62.5% | 61.0% | 50.7% | 50.3% | +2.59 | +20.3% |
| spread | v5 | 31 | 10-21 | 32.3% (19-50) | 60.9% | 50.6% | 49.9% | -14.39 | -29.9% |
| spread | v7 | 25 | 15-10 | 60.0% (41-77) | 62.1% | 52.0% | 51.5% | +5.08 | +13.7% |
| spread | **v9** | 85 | 43-42 | 50.6% (40-61) | 59.1% | 50.6% | 50.1% | +0.42 | +0.3% |
| total | v2 | 22 | 10-12 | 45.5% (27-65) | 60.7% | 52.9% | 51.4% | -1.05 | -3.6% |
| total | v4 (unproven) | 8 | 3-5 | 37.5% | 58.2% | 50.5% | 49.9% | -3.04 | -26.8% |
| total | v5 | 33 | 16-17 | 48.5% (33-65) | 65.7% | 52.0% | 51.0% | -1.90 | -3.6% |
| total | v7 | 24 | 11-13 | 45.8% (28-65) | 56.5% | 50.5% | 50.0% | -3.66 | -11.2% |
| total | **v9** | 85 | 39-46 | 45.9% (36-56) | 54.4% | 50.7% | 50.1% | -9.10 | -8.3% |

**Pooled by market:**
- ML: 92-46, ROI -2.5%. Model 67.1% vs ask 67.2%.
- Spread: 81-89, -11.50u, ROI -4.6%. Model 59.9% vs ask 50.8%.
- Total: 79-93, -18.75u, ROI -8.0%. Model 57.8% vs ask 51.2%.
- All: 252-228, -33.86u, ROI -5.4%.

**v9 only** (live since 11-08): 92-89, -5.55u, ROI -2.3%.
- Spread QUAL: 39-39, ROI -0.6%. Total QUAL: 30-37, ROI -10.5%.
- The 144-115 (-0.1%) for "all models since 11-08" is lifted by the flat ML favorites. It is not a v9 number.

**Spread loss attribution** (FLAT and v5 overlap on 14 rows, so these are union and complement):
- FLAT ∪ v5: 15-25, -15.01u, ROI -24.1% (n=40).
- Everything else: 66-64, +3.51u, ROI +1.9% (n=130).

**Model vs market calibration on the same picks** (Brier(model) − Brier(decision midpoint); + = market better):

| set | n (games) | Brier model | Brier mid | logloss model / mid | diff, 95% CI |
|---|---|---|---|---|---|
| ML, all versions | 133 (133) | 0.2126 | 0.1950 | 0.615 / 0.572 | +0.0176 [+0.0006, +0.0338] |
| spread, all versions | 163 (163) | 0.2630 | 0.2482 | 0.721 / 0.689 | +0.0148 [-0.0019, +0.0335] |
| total, all versions | 165 (165) | 0.2565 | 0.2499 | 0.707 / 0.693 | +0.0067 [-0.0068, +0.0214] |
| ALL, pooled | 461 (170) | 0.2461 | 0.2334 | 0.685 / 0.656 | **+0.0127 [+0.0026, +0.0221]** (game-clustered) |
| spread v9 | 85 | | | | +0.0099 [-0.0134, +0.0348] |
| total v9 | 85 | | | | -0.0023 [-0.0195, +0.0157] |

- Pooled across versions, the market is significantly better calibrated, driven by ML and the v2/v5 spreads.
- **v9 alone is statistically indistinguishable from the market** on Brier. It is level on totals and slightly worse on spreads.
- So v9 has no edge rather than demonstrably negative skill. On W/L alone, v9's CIs (40-61%, 36-56%) still contain its own claims (59.1%, 54.4%).
- The gap between claimed and realized is large in the pooled data (spread 59.9% vs 47.6%, total 57.8% vs 45.9%) but is not individually significant for v9.
- All of this is conditional on the picks the model selected.

## 2. Edge buckets (quoted; edge = model − ask)

| mkt | <0 | 0-2 | 2-5 | 5-8 | 8-12 | 12+ |
|---|---|---|---|---|---|---|
| spread W-L | 1-7 (unproven) | 13-9 | 13-18 | 13-12 | 11-15 | 30-28 |
| spread hit (CI) | 12.5% | 59.1% (39-77) | 41.9% (26-59) | 52.0% (33-70) | 42.3% (26-61) | 51.7% (39-64) |
| spread ROI | -86.7% | +13.7% | -13.7% | +1.6% | -17.7% | +3.9% |
| total W-L | 7-11 | 11-14 | 15-25 | 17-10 | 13-18 | 16-15 |
| total hit (CI) | 38.9% (20-61) | 44.0% (27-63) | 37.5% (24-53) | 63.0% (44-78) | 41.9% (26-59) | 51.6% (35-68) |
| total ROI | -29.0% | -13.1% | -23.4% | +25.0% | -17.6% | +3.1% |
| ML W-L | 56-11 | 9-7 | 7-8 | 8-1 (unproven) | 4-10 (unproven) | 8-9 |
| ML hit (CI) | 83.6% (73-91) | 56.2% (33-77) | 46.7% (25-70) | 88.9% | 28.6% | 47.1% (26-69) |

- **Not monotonic in any market.** Every cell with n>=15 has a CI that includes the 50-51% break-even.
- 12%+ claims 68.0% on spreads and hits 51.7%. It claims 71.1% on totals and hits 51.6%.
- v9-only buckets are equally flat. Spreads 2-5 / 5-8 / 8-12 / 12+ go 9-8 / 8-3 / 6-9 / 12-12; totals go 7-9 / 11-5 / 7-9 / 3-4.
- Correlation of edge with (win − ask), edge>0 picks only: spread **+0.008** (n=162), total +0.094 (n=154), ML +0.164 (n=71).
- The totals 5-8% bucket (17-10) sits between losing neighbours. It is noise, not a band.
- ML "<0" is FLAT calls on favorites the market priced above the model: 56-11 against a 77% ask, ROI +9.1%.

## 3. Sides and line sizes (quoted)

| cut | all versions W-L, ROI | v9 only W-L, ROI |
|---|---|---|
| spread home | 39-49, -12.0% | 23-22, -1.4% |
| spread away | 42-40, +3.2% | 20-20, +2.1% |
| spread favorite (line<0) | 29-36, -10.6% | 17-21, -13.5% |
| spread dog (line>0) | 52-53, -0.8% | 26-21, +11.7% |
| spread \|L\| <=3.5 | 29-24, +13.9% | 14-9, +30.6% |
| spread \|L\| 4-9.5 | 34-47, -16.8% | 20-23, -11.6% |
| spread \|L\| 10+ | 18-18, -4.9% | 9-10, -7.6% |
| total over | 18-28, -17.8% | 14-13, +6.7% |
| total under | 61-65, -4.7% | **25-33, -15.8%** |
| total line 235+ | 18-24, -14.1% | 5-8, -25.0% (unproven) |
| ML favorite / dog | 86-35, +0.7% / 6-11, -20.8% | |

- Every n>=15 CI includes break-even.
- Two directions repeat across versions:
  - **Spread favorites on mid-size lines lose.** All versions, \|L\| 4-9.5: 34-47. v9 favorites: 17-21.
  - **v9 totals unders lose.** v9 picks are 68% unders (58 of 85). §7 explains why.

## 4. Closing line value

**There is no true closing line.**
- Quotes exist only at forecast-run captures, in both the snapshot jsonl and `market_quotes.db`. `market_quotes.db` was sparse in the window checked (11-12/13: 7-17 rows per game, against about 2,500 on 11-15..11-17).
- The last pre-tip capture is a median **65 min** before tip (IQR 24-135).
- On **246 of 461** joined picks, the decision capture is also the last capture, so CLV is 0 by construction.
- The ledger `closing_*` / `probability_clv` fields are filled on 191/480 rows (mean -0.15c). They come from the same captures, mostly the decision quote itself, so they are not usable.

Recomputed from snapshots (same market slug, pick side, midpoint), on the 215 picks with a strictly later capture:

| mkt | n | mean CLV mid | mean CLV ask | toward / away | corr(CLV, win) | hit toward / away |
|---|---|---|---|---|---|---|
| ML | 43 | +0.21c | +0.21c | 9 / 9 | -0.08 | 56% / 56% (unproven) |
| spread | 85 | +0.24c | +0.19c | 30 / 27 | -0.04 | 43% / 41% |
| total | 87 | +1.10c | +0.48c | 42 / 28 | +0.15 | 55% / 36% |

- 50c main-line movement in pick terms (+ = toward the pick):
  - Spreads: n=83, mean +0.12 pts, median 0.00.
  - Totals: n=83, mean +0.29 pts, median +0.04. Hit rate is 54% when the line moved toward the pick and 31% when it moved away (n=35/26).
- In the last 1-2 hours the market does not move against the picks, and on totals it drifts slightly with them.
- CLV does not explain the losses. This is "pre-close value", not CLV.

## 5. Injury / availability adjustment

- Only v2-v5 rows store `availability_points_gap` / `availability_total_loss` (123 quoted rows).
- **v7 and v9 store neither the availability component nor a pre-adjustment margin**, so v9 cannot be split by injury effect.
- In v2-v5 the adjustment almost never fired:
  - Spreads: \|gap\| <=0.5 on **55 of 60** picks (median 0, range -7.6..+7.3).
  - Totals: total loss is 0 at the median.

| cut (v2-v5) | W-L | ROI |
|---|---|---|
| spread, \|gap\| <=0.5 (no adjustment) | 20-35 | -23.6% |
| spread, gap toward pick / against pick (unproven) | 2-2 / 1-0 | -2.9% / +100% |
| spread, +edge only because of gap (unproven) | 0-1 | -100% |
| total, \|loss\| <=1 | 25-31 | -9.6% |
| total, \|loss\| 6+ (unproven) | 4-2 | +30.2% |

The v2-v5 spread losses came from picks with no injury input. There is no evidence that the adjustment drove losses.

## 6. Timing and flips

| lead time of settled pick | spread W-L (ROI) | total W-L (ROI) | ML W-L (ROI) |
|---|---|---|---|
| <1h | 33-33 (-3.6%) | 27-38 (-16.5%) | 35-14 (+3.7%) |
| 1-3h | 39-40 (+1.4%) | 40-42 (+0.1%) | 42-22 (-2.7%) |
| 3-8h | 9-16 (-27.0%) | 11-13 (-16.0%) | 14-8 (-9.7%) |

- There is no clean timing effect; every n>=15 CI includes 50%.
- Runs per (event, market, model): 4.2 on average.
- The first-run side differs from the settled side in **37 of 516 groups (7%)**.

| market | flipped | groups | first-run pick (hypothetical) | settled |
|---|---|---|---|---|
| spread | no / yes | 153 / 19 | 45.8% / 52.6% | 47.1% / 52.6% |
| total | no / yes | 156 / 17 | 46.2% / 58.8% | 45.5% / 47.1% |

- Hypothetical means the first-run pick graded at its own line using final scores, ignoring price drift.
- These counts cover all regimes.
- Flips are rare and too small to call.

## 7. Residuals: projection vs market line vs result (one row per event x model)

The market line is the interpolated 50c line from the decision-time snapshot.

| model | n | model bias (act−proj) | model MAE (same games) | market MAE | market bias | market closer |
|---|---|---|---|---|---|---|
| spread v2 | 21 | -3.18 | 13.53 | 11.64 | -3.97 | 76% |
| spread v5 | 31 / 24 | -1.76 | 11.94 | 10.51 | -0.02 | 79% |
| spread v7 | 25 / 24 | -1.05 | 11.58 | 10.43 | -0.27 | 54% |
| spread v9 | 85 / 83 | +0.01 | 12.75 | 12.03 | +0.18 | 57% |
| total v2 | 22 | -2.07 | 13.90 | 14.26 | -5.77 | 64% |
| total v5 | 33 / 26 | +2.47 | 16.63 | 16.09 | -4.02 | 58% |
| total v7 | 24 | -0.20 | 13.87 | 12.59 | -1.68 | 63% |
| total v9 | 85 / 82 | **+3.91** (SE 1.85) | 13.80 | 13.13 | **+3.67** | 59% |

Where two n values are shown, the second is the games with a market line.

- **On MAE, the market line beats the projection for every version except v2 totals.** v9: spread 12.75 vs 12.03, total 13.80 vs 13.13.
- **No detectable market-relative skill.** corr(model − market, actual − market):
  - Spreads: **-0.075 [-0.23, +0.08]**, slope -0.27 (SE 0.29), n=160. v9 only: r = +0.08 (n=83).
  - Totals: **-0.061 [-0.21, +0.09]**, slope -0.27 (SE 0.35), n=162. v9 only: r = -0.03 (n=82).
- **v9 totals bias** (each about 2 SE, so suggestive but not proven):
  - Games finished **+3.9 above the model** (the 11-18 note said +2.7), **+3.7 above the market 50c line**, and +3.0 above the pick line.
  - The model sits only 0.76 pts below the market on average. The bias is mostly early-season scoring above every number, not a model-specific miss.
  - It costs money through selection: the model's small under-lean becomes an under bet.
    - Model below market: 23-32, all unders.
    - Model above market: 14-13, almost all overs.

## 8. Biggest losses and patterns (quoted)

The twelve biggest losses are all 2.00U max-stake picks claiming 64-79% against a 43-52% ask.

| date | game (away @ home) | model | pick | model / ask | final |
|---|---|---|---|---|---|
| 11-19 | UTA @ LAL | spread v9 | LAL -11.5 | 69.5% / 51.0% | 109-97 |
| 11-09 | MIA @ SAC | spread v9 | MIA -8.5 | 67.2% / 50.0% | 114-108 |
| 11-07 | TOR @ MIL | spread v7 | MIL +6.5 | 79.0% / 50.0% | 121-107 |
| 11-14 | NYK @ IND | spread v9 | NYK -8.5 | 68.3% / 50.0% | 104-106 |
| 11-17 | PHI @ BKN | spread v9 | BKN +4.5 | 73.5% / 48.1% | 135-123 |
| 10-25 | IND @ BKN | spread v5 | BKN +7.5 | 74.2% / 51.9% | 136-109 |
| 10-22 | IND @ NOP | ML elo-v4 | NOP | 64.1% / 42.9% | 123-120 |
| 10-27 | PHX @ OKC | total v5 | U 219.5 | 70.2% / 51.0% | 114-125 |
| 10-29 | NYK @ CHI | total v5 | U 234.5 | 74.6% / 51.0% | 133-127 |
| 11-17 | ORL @ BOS | spread v9 | BOS -8.5 | 68.1% / 51.9% | 115-105 |
| 10-28 | POR @ LAL | total v5 | U 236.5 | 74.2% / 51.9% | 136-129 |
| 11-16 | OKC @ SAC | spread v9 | SAC +12.5 | 74.0% / 50.0% | 125-103 |

- **The pattern:** the largest edges are the model's largest disagreements with a liquid market, and they hit at coin-flip rates (§2).
  Edge-scaled sizing puts the most units on exactly those picks.
- **Teams** (all small or unproven):
  - Backed: SAC 1-7 (-9.3u), WAS 1-6 (-6.0u), NYK 7-10 (-8.7u).
  - Faded, and they won or covered: MEM 10 of 12 (-9.0u), BOS 5/5 (-7.5u), HOU 7/9 (-6.4u).
  - Totals: games involving BKN went 2-8, MIL 3-9, MIA 3-10.
  - These are consistent with the model leaning on its Elo/trend prior over current-season form. That is a hypothesis; these rows cannot test it.
- **Back-to-backs and star absences cannot be tested reliably.**
  - v9 rows store neither.
  - A B2B flag derived from ledger game dates is unreliable: the ledger lists only 2-15 games per date and none on 10-30 or 11-03, so B2Bs are undercounted.
  - With that caveat, the derived cut shows overs 8-23 when neither team was flagged B2B. That is one cut of about 30 and is not a finding.
- **Rest (v2-v5 `rest_diff`):** spreads with equal rest went 6-16 (ROI -39.1%, n=22), against 17-21 with a rest edge.
- **A side-selection bug candidate in v9 totals, 11-11..11-13:**
  - 61 rows (14 settled and quoted) carry a "Calibrated X% -> Y%" layer.
  - In 11 of the 14, the stored post-calibration probability for the chosen side is **below 50%** (for example under: raw 53.9%, calibrated 38.9%).
  - The side was chosen on the raw probability, while the stored probability and edge are post-calibration.
  - Those 14 went 4-10 (-6.49u). The layer is gone after 11-13; it is worth a look in git history.

## Bottom line

1. **No version shows market-relative skill.** The correlation between model-vs-market disagreement and outcome is near zero (CIs about ±0.15), and edge size does not rank picks.
   - Pooled across versions, the market is significantly better calibrated: Brier +0.013 [+0.003, +0.022].
   - v9 alone is roughly market-equivalent on Brier and loses about the vig: 92-89, ROI -2.3%.
2. **Spreads:** the damage is v5 and the FLAT calls, 15-25 (-15.0u). Everything else is 66-64 (+1.9%).
   **Totals** are the persistent loser: 79-93 (-8.0%), negative in every week. v9 unders went 25-33 in a season scoring about 3-4 pts above market totals (about 2 SE).
3. **Sizing:** edge-scaled sizing (1.0-2.0U) concentrates stake on the biggest model-market disagreements, which do not win more. All top-12 losses were 2U picks.
4. **What cannot be tested:** there is no true closing data (the last capture is about 65 min pre-tip, and half the picks have no later capture). The injury effect cannot be attributed for v7/v9. In v2-v5 the adjustment was about 0 on 92% of spread picks.
