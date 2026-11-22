# NCAAB loss forensics, 2026-11-20 (read-only)

Source: `E:/model-prediction-runtime/ledgers/ledgers.db` (opened `mode=ro`), `ledger_records`, `sport='ncaab'`, `status='settled'`. Nothing was edited or mutated.
Prices are implied probabilities. The 2026-11 games are a locked holdout, so everything below is diagnosis. No value in this report was fitted on these games.

## 0. Method and data-contract findings

**Regimes (on `created_at_utc`):**
- **A:** before 2026-11-12T07:00Z. Pre-fix v6. Rows carry `effective_hca` 7.2; the 11-12 05:xx run is the last pre-fix run.
- **B:** 2026-11-12T07:00Z up to the first v7 row. Fixed v6. From the 11-12 11:xx run onward rows carry `effective_hca` 5.0, `base_margin_scale` 10.5 and `elo_weight_effective` 0.5. `effective_total_sd` jumps from about 16.5-17 to about 20-21.
- **C:** spread and moneyline rows whose `feature_payload.features.margin_engine == 'v7'`.
  - The first v7 row is at 2026-11-16T05:53:18Z; the last v6-engine row is at 11-16T01:37:14Z. No v6-engine row appears after that.
  - `margin_engine: v7` is not committed. It is an edit in the working tree of `config/model.yaml` (comment: "Operator wired 2026-11-15").
  - The code that serves v7 landed in commit `2c93813d` at 2026-11-15 21:55 -0500 (11-16T02:55Z). No row ever records `margin_engine` = "v6". The config flip therefore falls between 11-16T02:55Z and 05:53Z.
  - Totals are not priced by v7. Their "C" label is time-based only; the total model is the same as in B.

**Integrity and data-contract checks:**
- **`model_artifact_hash` is NULL on every NCAAB row**, so it cannot be used to check the regimes. The feature payload and the engine field were used instead.
- **The calibrator changes almost daily.** `calibration_method` rotates through identity, beta, temperature and platt, with 20+ distinct calibration artifact hashes between 11-09 and 11-20. This is a second regime variable that the A/B/C split does not control for.
- **`model_probability` holds the raw value on calibrated rows.** On 222 of the 224 rows with a non-identity calibrator, the ledger column is the RAW probability, while `edge` = `serving_probability` − market. All tables here use the served probability (the one consistent with `edge`), and raw is shown where relevant.
- **Results check out.** All 2,776 NCAAB settled rows recompute exactly from `home_score`/`away_score` and `line` (0 mismatches, 0 pushes). `line` is the handicap on the selected side.
- **Dedupe:** key (event_id, market_type, selection, line, model_id), Main preferred over Flat. This takes the 1,079 v6 settled rows down to **963** (all 116 removed duplicates are regime A). 12 event-markets still carry 2 rows at different lines on the same side (correlated); no event-market has both sides.
- The 30 rows archived for team-identity collisions (UConn/CCSU, UMass/Rutgers) are `status='archived'` and are excluded.
- **No row ever has current-season sample ≥ 8.** The sample is the minimum of the two teams (`ncaab_spread_v6.py:494`, `ncaab_total_v6.py:340`), and the maximum seen is 7.
- **Placeholder prices:**
  - 76 rows are zero-unit placeholder rows, all priced at exactly 50%.
  - 196 rows in total are priced at exactly 50%.
  - Prices such as 50.98% and 51.92% are real 51¢ and 52¢ asks that were converted through integer odds; they are not placeholders.
  - The standard deviation of market prices is 1.4 pp on spreads and 1.1 pp on totals, against 18.9 pp on moneyline.
- **Quote provenance values:** `decision_time_executable_quote` never appears on any NCAAB row. The closest value is `archived_two_sided_bbo`, which is treated here as "verified".
- **CLV is degenerate.** 339 rows have a closing price recorded. On 304 of them the closing price equals the decision price to 1¢, and mean `probability_clv` is +0.0002. CLV cannot be used yet.
- **Flat logs the model's preferred side even when the served edge is ≤ 0** (`trade_candidate=False`, 1.0U). Regime A is mostly positive-edge Main calls, so the regimes are not like-for-like. The edge-sign split below handles this.

## 1. v6 settled rows, deduplicated: regime x market

| regime | market | n | W-L | hit% | units | PnL | ROI% | flat-1U ROI% | mean served p | mean mkt p |
|---|---|---|---|---|---|---|---|---|---|---|
| A | moneyline | 50 | 30-20 | 60.0 | 50.0 | +3.08 | +6.2 | +6.2 | 66.3% | 63.9% |
| A | spread | 90 | 49-41 | 54.4 | 164.4 | +12.80 | +7.8 | +7.0 | 64.0% | 50.7% |
| A | total | 120 | 52-68 | 43.3 | 193.1 | -32.18 | -16.7 | -14.2 | 57.9% | 50.5% |
| B | moneyline | 122 | 82-40 | 67.2 | 122.0 | -12.81 | -10.5 | -10.5 | 66.3% | 74.0% |
| B | spread | 151 | 74-77 | 49.0 | 210.4 | +2.48 | +1.2 | -3.6 | 58.2% | 50.8% |
| B | total | 145 | 75-70 | 51.7 | 171.7 | +15.51 | +9.0 | +1.9 | 53.7% | 50.9% |
| C | moneyline | 47 | 25-22 | 53.2 | 45.0 | -3.18 | -7.1 | -2.5 | 62.1% | 55.2% |
| C | spread | 153 | 74-79 | 48.4 | 157.6 | -4.46 | -2.8 | -5.1 | 53.7% | 50.8% |
| C | total (v7 n/a) | 85 | 49-36 | 57.6 | 87.1 | +15.86 | +18.2 | +14.1 | 55.6% | 50.6% |

The same rows split by served-edge sign (n<30 cells marked):

| regime | market | edge ≤ 0 | edge > 0 |
|---|---|---|---|
| B | moneyline | 91: 69-22, -6.36U (-7.0%) | 31: 13-18, -6.45U (-20.8%) |
| B | spread | 16: 10-6, +3.21U (n<30) | 135: 64-71, -0.73U (-0.4%; flat -6.4%) |
| B | total | 28: 12-16, -4.72U (n<30) | 117: 63-54, +20.23U (+14.1%) |
| C | spread | 67: 39-28, +11.02U (+18.7%) | 86: 35-51, -15.48U (-15.7%) |
| C | total | 4 (n<30) | 81: 47-34, +15.98U (+19.2%) |

In regime C spreads, the rows the model would not bet (edge ≤ 0) beat the rows it would bet by 17.5 pp of hit rate.

### Retired tempo v2/v3 moneyline (history)

Deduplicated across Flat and Main, tempo-v3 moneyline went **26-151, -61.69U**. Main alone went 20-144. Tempo-v2 went 14-55, -20.79U.
- **v3:** 159 of the 177 picks were away (157 of them **away underdogs**). 128 of them were priced under 20% (mean market 6.3%, mean model 33.3%) and went **5-123 (-71.5U)**. The few home picks and the 20-50% dogs were roughly flat.
- **v2:** the same shape. Away picks went 8-48 at a mean market price of 14.8% against a model 37.6%.
- **Diagnosis:** a strongly compressed margin distribution, which turned blowout mismatches into "value" long shots. All rows were priced on `model_opinion_indicative_market`, with no quote provenance.
- The same compression signature survives in v6 spreads (§4, §5), though in a much milder form.

## 2. The discriminating test: does model − market predict outcomes?

**Verified-quote rows (`market_quote_provenance = archived_two_sided_bbo`) are too few to decide anything.**

| set | moneyline | spread | total |
|---|---|---|---|
| verified (two-sided BBO) | 6 | 16 | 17 |
| research-join matched, but priced at the indicative ask | 2 | 7 | 9 |
| unverified (indicative or blank) | 211 | 371 | 324 |

The verified rows went 3-3 on moneyline, 9-7 on spread and 11-6 on total. All analysis below is on all rows, with that caveat.

**Brier score, served vs raw vs market price (selected side; lower is better):**

| market | set | n | hit% | served p | mkt p | Brier served | Brier raw | Brier mkt | LL served | LL mkt |
|---|---|---|---|---|---|---|---|---|---|---|
| moneyline | A | 50 | 60.0 | 66.3 | 63.9 | 0.2357 | 0.2360 | 0.2492 | 0.665 | 0.702 |
| moneyline | B | 122 | 67.2 | 66.3 | 74.0 | 0.1889 | 0.1890 | **0.1786** | 0.553 | **0.513** |
| moneyline | C | 47 | 53.2 | 62.1 | 55.2 | 0.2600 | 0.2600 | **0.2485** | 0.721 | **0.695** |
| spread | A | 90 | 54.4 | 64.0 | 50.7 | 0.2734 | 0.2691 | **0.2470** | 0.802 | **0.687** |
| spread | B | 151 | 49.0 | 58.2 | 50.8 | 0.2564 | 0.2707 | **0.2501** | 0.709 | **0.693** |
| spread | C | 153 | 48.4 | 53.7 | 50.8 | 0.2657 | 0.2700 | **0.2487** | 0.726 | **0.691** |
| total | A | 120 | 43.3 | 57.9 | 50.5 | 0.2734 | 0.2734 | **0.2505** | 0.742 | **0.694** |
| total | B | 145 | 51.7 | 53.7 | 50.9 | **0.2474** | 0.2474 | 0.2505 | **0.688** | 0.694 |
| total | C | 85 | 57.6 | 55.6 | 50.6 | 0.2489 | 0.2489 | 0.2493 | 0.691 | 0.692 |
| spread | verified BBO | 16 | 56.2 | 57.7 | 51.1 | 0.2544 | 0.2469 | 0.2479 | 0.704 | 0.689 |
| total | verified BBO | 17 | 64.7 | 56.3 | 50.5 | 0.2466 | 0.2466 | 0.2495 | 0.687 | 0.692 |

On spreads and totals the market price is a near-constant coin flip, so "Brier mkt" is only the 50% baseline. The model fails to beat that baseline everywhere except totals in B (and ties it in C).

**Moneyline: does the model carry information beyond the price?** Logistic regression `y ~ logit(mkt) + [logit(served) − logit(mkt)]`:

| set | n | b on logit(mkt) | b on (model − mkt) | se | z |
|---|---|---|---|---|---|
| all | 219 | 0.85 | +0.12 | 0.35 | 0.33 |
| B | 122 | 1.11 | -0.00 | 0.50 | -0.01 |
| C | 47 | 0.23 | -1.26 | 1.69 | -0.75 |
| B+C | 169 | 0.95 | **-0.03** | 0.42 | -0.07 |

**Verdict: the model adds no measurable information beyond the price.** The raw correlation of edge with outcome is -0.22. That number comes from the model being compressed relative to the price; it is not anti-skill.

**Spreads and totals, tested in points** (does model-minus-line predict actual-minus-line?):

| market | regime | n | corr | slope | sign agree% |
|---|---|---|---|---|---|
| spread (home margin vs market-implied margin) | A | 90 | +0.07 | +0.10 | 54.4 |
| spread | B | 151 | -0.05 | -0.10 | 49.0 |
| spread | C | 153 | -0.07 | -0.28 | 48.4 |
| total | A | 120 | -0.19 | -0.80 | 43.3 |
| total | B | 145 | +0.20 | +1.49 | 51.7 |
| total | C | 85 | +0.05 | +0.24 | 57.6 |

Spread disagreement with the line carries zero or negative signal in every regime. Totals are mixed: positive in B, roughly zero in C, negative in A.

**Calibration by served-probability bucket, post-fix (B+C):**

| market | served p bucket | n | mean served p | mean mkt p | hit% |
|---|---|---|---|---|---|
| spread | 50-55% | 145 | 52.1 | 50.8 | 49.7 |
| spread | 55-60% | 46 | 57.2 | 50.8 | 41.3 |
| spread | 60-65% | 31 | 61.9 | 50.5 | **35.5** |
| spread | 65-70% | 16 | 67.2 | 51.0 | 56.2 (n<30) |
| spread | 70-80% | 11 | 77.4 | 51.3 | 72.7 (n<30) |
| spread | 80-90% | 9 | 83.6 | 51.3 | 44.4 (n<30) |
| total | 50-55% | 144 | 52.2 | 50.8 | 52.8 |
| total | 55-60% | 67 | 57.0 | 50.7 | 55.2 |
| total | 60-65% | 16 | 61.1 | 50.6 | 62.5 (n<30) |
| moneyline | 50-55% | 33 | 52.6 | 53.6 | 45.5 |
| moneyline | 55-60% | 31 | 57.7 | 56.9 | 61.3 |
| moneyline | 70-80% | 33 | 74.7 | 81.9 | 81.8 |
| moneyline | 80-90% | 17 | 85.2 | 95.3 | 88.2 (n<30) |

On spreads, hit rate falls as model confidence rises between 50% and 65%. On moneyline favourites, the market's price sits closer to the hit rate than the model's does.

## 3. Cuts (all deduplicated v6; post-fix = B+C; n<30 marked)

| cut | post-fix n | W-L | PnL | ROI% | flat-1U ROI% |
|---|---|---|---|---|---|
| spread away | 194 | 101-93 | +18.30 | +7.7 | +1.8 |
| spread home | 110 | 47-63 | -20.28 | -15.4 | -15.2 |
| spread away dog | 181 | 94-87 | +16.19 | +7.2 | +1.5 |
| spread home dog | 47 | 21-26 | -8.64 | -14.5 | -11.4 |
| spread home fav | 63 | 26-37 | -11.64 | -16.2 | -18.0 |
| spread away fav | 13 (n<30) | 7-6 | +2.12 | +17.3 | +5.7 |
| spread \|line\| <5 | 103 | 49-54 | -3.30 | -2.8 | -6.6 |
| spread \|line\| 5-10 | 62 | 27-35 | -6.09 | -8.8 | -13.5 |
| spread \|line\| 10-16 | 50 | 25-25 | -3.62 | -5.6 | -1.7 |
| spread \|line\| 16+ | 89 | 47-42 | +11.03 | +9.5 | +3.2 |
| total over | 154 | 76-78 | +5.65 | +3.1 | -2.8 |
| total under | 76 | 48-28 | +25.73 | +32.4 | +25.1 |
| ML home fav | 108 | 75-33 | -13.22 | -12.2 | -12.2 |
| ML away fav | 30 | 19-11 | +0.26 | +0.9 | +0.9 |
| ML away dog | 18 (n<30) | 9-9 | +0.75 | +4.7 | +15.3 |
| ML home dog | 13 (n<30) | 4-9 | -3.78 | -29.1 | -29.1 |
| ML price 35-50% | 26 (n<30) | 11-15 | -0.03 | -0.1 | -0.1 |
| ML price 50-65% | 59 | 28-31 | -11.73 | -20.6 | -16.5 |
| ML price 65-80% | 26 (n<30) | 17-9 | -2.04 | -7.8 | -7.8 |
| ML price 80%+ | 58 | 51-7 | -2.20 | -3.8 | -3.8 |

Pre-fix (A), for reference:
- Spread: away 25-24, home 24-17 (+12.3U). Home dogs were 18-8 (n<30).
- Total: overs 23-30, unders 29-38, both about -17% ROI.
- Moneyline: home favourites 18-13 (-7.2U).

**Edge buckets (served edge), post-fix B+C:**

| market | ≤0 | 0-3.5 | 3.5-5 | 5-8 | 8-12 | 12+ |
|---|---|---|---|---|---|---|
| spread | 83: 49-34 +14.24U | 87: 37-50 -10.55U | 30: 15-15 -2.07U | 29: 13-16 +0.53U (n<30) | 30: 9-21 -14.59U | 45: 25-20 +10.47U |
| total | 32: 14-18 -4.83U | 100: 53-47 +11.20U | 25: 17-8 +6.79U (n<30) | 39: 21-18 +4.56U | 29: 17-12 +15.73U (n<30) | 5 (n<30) |
| moneyline | 91: 69-22 -6.36U | 24: 11-13 -7.44U (n<30) | 8 (n<30) | 17 (n<30) | 16 (n<30) | 13 (n<30) |

Spread results do not rise with edge. Bucketing spreads on raw (pre-calibration) edge shows the same pattern: 5-8% 20-26, 8-12% 22-31, 12%+ 48-41.

**Current-season games (minimum of the two teams), post-fix:**

| market | 0-3 | 4-7 | 8+ |
|---|---|---|---|
| spread | 173: 89-84, +15.63U | 131: 59-72, -17.61U | 0 |
| total | 151: 79-72, +15.41U | 79: 45-34, +15.97U | 0 |
| moneyline | 125: 82-43, -15.75U | 44: 25-19, -0.24U | 0 |

The rows by regime: A 260/0, B 383/35, C 66/219 (sample 0-3 / 4-7). Spreads did not improve as the sample grew.

## 4. Bias in points

**Spreads (home perspective, non-neutral games): compression test.** The slope of the actual home margin on the projected margin should be 1.0 for a well-scaled forecast; a slope above 1 means the forecast is compressed.

| regime | n | mean \|model\| | mean \|market-implied\| | slope actual~model | slope actual~market | corr model | corr market |
|---|---|---|---|---|---|---|---|
| A | 81 | 6.4 | 10.0 | **2.40** | 0.93 | 0.62 | 0.68 |
| B | 140 | 8.2 | 12.0 | **1.51** | 1.01 | 0.66 | 0.73 |
| C (v7) | 131 | 12.9 | 14.0 | **1.13** | 1.05 | 0.68 | 0.71 |

- **The model's margins are compressed; the market's are well scaled** (slope about 1.0).
- v7 largely fixed the scaling (slope 2.40 → 1.51 → 1.13), but the market still correlates better with outcomes in every regime.
- Because of the compression, **303 of 394 spread picks are underdogs** (230 of them away dogs).
- Mean home margin, model / market / actual: A 6.3 / 7.9 / 9.1; B 7.4 / 10.4 / 8.7; C 11.6 / 12.6 / 11.4. The model does not over-project home teams.
- **Selected-side cover margin, mean model vs actual (points):**
  - A: away +6.9 vs +0.5, home +4.5 vs +3.0.
  - B: away +5.4 vs +0.8, home +2.7 vs **-3.7**.
  - C: away +2.8 vs +0.4, home +1.9 vs **-1.0**.
- The model's projected cover edge does not materialise on either side. Its home-side picks (games where the model sits above the market on the home team) finish below the line on average after the fix. That is the no-signal result seen from the home side, not a home-court bias.
- **Neutral sites (n=42):** model-minus-line -1.3, actual-minus-line +1.2, correlation +0.03. There is no evidence of a neutral-site mislabel effect.

**Totals:**

| regime | side | n | mean model − line | mean actual − line |
|---|---|---|---|---|
| A | over | 53 | +3.65 | -1.39 |
| A | under | 67 | -3.25 | +4.53 |
| B | over | 104 | +2.00 | -1.99 |
| B | under | 41 | -1.57 | -5.38 |
| C | over | 50 | +3.53 | +3.96 |
| C | under | 35 | -1.83 | -0.70 |

- **Level (mean model / line / actual):** A 151.1 / 151.3 / 153.2; B 150.7 / 149.7 / 146.8; C 151.6 / 150.3 / 152.3.
- **There is no stable directional bias.** The model leans over roughly 2:1 (154 overs vs 76 unders post-fix). Results follow scoring swings between weeks (B ran 3 points under the line, C 2 points over), not the model.
- **Accuracy:** MAE against the actual total is 13.4 for the model vs 13.6 for the line (B), and 11.4 vs 11.4 (C).

## 5. The 15 largest-edge settled losses (hand-read)

| created | reg | pick | served p / mkt | projected home margin | final (home margin) | slug abbreviations |
|---|---|---|---|---|---|---|
| 11-12 01:38 | A | Ark-Pine Bluff +47.5 at UCLA | 99.6 / 52 | +17.4 | 106-37 (69) | arpb-ucla |
| 11-12 01:38 | A | Miss Valley St +33.5 at Oregon | 95.4 / 50 | +16.4 | 100-66 (34) | msvlst-ore |
| 11-11 23:37 | A | Jacksonville St +34.5 at Houston | 92.4 / 51 | +11.7 | 92-46 (46) | jaxst-hou |
| 11-11 23:37 | A | NJIT +33.5 at VCU | 89.7 / 49 | +13.7 | 87-49 (38) | njit-vcu |
| 11-13 23:34 | B | SIU Edwardsville +42.5 at Duke | 88.3 / 49 | +24.8 | 90-47 (43) | siue-duke |
| 11-11 23:37 | A | Colgate +28.5 at St. John's | 82.4 / 50 | +14.2 | 90-59 (31) | colg-stjohn |
| 11-12 01:38 | A | Cal Poly +18.5 at Grand Canyon | 81.4 / 50 | +9.1 | 99-71 (28) | calpol-gcan |
| 11-12 23:37 | B | Southern Utah +22.5 at High Point | 83.3 / 52 | +8.3 | 88-59 (29) | sutah-hpnt |
| 11-14 01:40 | B | UL Monroe +29.5 at Arizona St | 81.0 / 50 | +16.7 | 92-62 (30) | lamon-arzst |
| 11-13 19:21 | B | North Alabama +32.5 at Miami | 80.8 / 50 | +19.8 | 94-52 (42) | nal-mia |
| 11-13 23:34 | B | Northern Iowa +27.5 at Michigan St | 80.6 / 52 | +14.9 | 106-72 (34) | niowa-mst |
| 11-11 23:37 | A | Saint Peter's +21.5 at South Carolina | 77.7 / 52 | +9.9 | 77-41 (36) | stpete-sc |
| 11-16 21:12 | C | Alcorn St +35.5 at Maryland | 76.2 / 51 | +27.5 | 117-57 (60) | alcst-mary |
| 11-11 23:37 | A | Drexel ML vs American (home) | 75.6 / 51 | +6.2 | 64-66 (-2) | amercn-drexel |
| 11-17 23:43 | C | Central Michigan +13.5 at Dayton | 76.6 / 53 | +5.4 | 88-63 (25) | cmich-day |

**Checks:**
- **Team identity:** canonical name = original name = Polymarket slug abbreviation on all 15. No collision.
- **Neutral site:** `neutral_site=False` on all 15. By team name, all look like on-campus home games.
- **Division:** by team name, all 15 opponents are D1 programmes. This was not checked against the repo's D1 conference map.
- **Quotes:** all 15 were priced on `indicative_slate` with no quote timestamp, so their staleness cannot be checked. The prices were 49-53%.

**Pattern:** 14 of 15 are large away-underdog spreads in "buy games" (a much stronger host against a low-major visitor). The model projected the host to win by 5-28 against market lines of 13.5-47.5; actual margins were 25-69. The market was right every time. 8 of the 15 are regime A, 5 are B and 2 are C.

The one moneyline loss is the A-era 7.2 home-court advantage on a toss-up (Drexel lost by 2).

## 6. Pinned forward check

`ncaab-spread-v6`, spread, served edge in [5%, 8%), created on or after 2026-11-12, deduplicated:

| n | W-L | hit% | units | PnL | ROI% | flat-1U ROI% | mean served p | mean mkt p |
|---|---|---|---|---|---|---|---|---|
| 29 (n<30) | 13-16 | 44.8 | 42.5 | +0.53 | +1.2 | -10.9 | 56.8% | 50.4% |

- **Quote provenance:** 0 verified-quote rows, 0 research-join matched, 3 zero-unit placeholder rows. All 29 are Flat; none are pre-fix.
- **By regime:** B 21 rows, 9-12; C (v7) 8 rows, 4-4.
- **Sensitivity on raw edge in [5%, 8%):** 48 rows, 22-26, -1.78U.
- **The post-hoc 11-3 baseline has not held:** mean model probability 56.8% against a 44.8% hit rate, still well short of the 50-pick mark.

## 7. What this says

1. **There is no evidence of skill relative to the market in any NCAAB market.**
   - Moneyline: the model-minus-market coefficient is -0.03 ± 0.42 (B+C).
   - Spreads: the correlation of model-minus-line with actual-minus-line is between -0.07 and +0.07.
   - Totals: the same correlation runs -0.19 / +0.20 / +0.05 across A/B/C.
   - The post-fix totals profit (+31U, unders 48-28) does not line up with edge size or the model's direction, so treat it as variance.
2. **The dominant structural error was margin compression, worst in mismatch games.**
   - The slope of actual margin on model margin was 2.40 (A) and 1.51 (B), against about 1.0 for the market. So the Flat ledger kept choosing underdogs, and 14 of the 15 worst losses are buy-game away dogs.
   - This is the same failure as tempo-v3 (away long shots 5-123), in milder form.
   - v7 brought the slope to 1.13, but its disagreements with the line still carry no signal (correlation -0.07).
3. **Post-fix home-side spread picks lost** (47-63; home favourites 26-37). This is the no-signal result seen from the home side. Mean model home margin is at or below actual in every regime, so there is no home-court over-projection. It does not contradict the handoff's finding that Elo underrates home teams. Do not tune on it.
4. **The calibration layer is an uncontrolled moving part.**
   - The method and artifact change almost daily.
   - The C-era beta calibrator flipped 61 spread rows to edge ≤ 0, and those went 35-26 while the bettable beta rows went 9-16 (n<30).
   - Calibration lowers Brier only because it shrinks a no-skill raw probability toward 50%.
5. **Market-relative evaluation is not possible yet:**
   - 39 verified-quote rows in total.
   - 76 zero-unit rows at a 50% placeholder price, and 196 rows at exactly 50%.
   - CLV equals 0 on 304 of 339 rows.
   - `model_artifact_hash` is NULL on every row.

### Improvement and ablation candidates (to test on prior seasons only, never on these games)

- **Margin dispersion in mismatches.** Run an ablation on 2022-24 November slates of the early-season margin scale and prior-regression weight, and of a talent-gap or conference-tier feature for power-vs-low-major games. Score it as `(model − closing line)` vs `(actual − closing line)` using the NCAAB closing-line archive (`quick-261115-1m8`), not on outcomes alone.
- **Make market-relative scoring the acceptance metric** for any v8 or v7 follow-up: residual vs the close, with slope and correlation as in §2. Outcome MAE gains such as v7's RMSE 11.50 vs 11.81 have not turned into line-relative signal.
- **Calibrator governance:**
  - Pin one calibrator artifact per champion.
  - Fit it on prior-season data only.
  - Record its hash on the row.
  - Evaluate shrink-to-market as a calibrator family in the same prior-season harness.
- **Instrumentation (no model change):**
  - Write `model_artifact_hash`.
  - Make the `model_probability` column equal the served probability, or add a raw column.
  - Capture a true pre-tip BBO as the close so `probability_clv` stops copying the decision price.
  - Prefer executable two-sided quotes over placeholder slates for spreads and totals, so that the next audit has a real market comparison.

Scripts (read-only) are in the scratchpad: `extract.py`, `prep.py`, `q5.py` (dedupe), `t2.py`-`t8.py`, `t5b.py`, `lib.py`.
