# RETROSPECTIVE REPLAY: NCAAF totals on every settled 2026-11-14 game (quick-261115-183)

**RETROSPECTIVE REPLAY.** 2026-11-14 is inside the data the lopsided / FBS-vs-FCS tilt was found on, so none of this tests the tilt. The tilt's real test is the pre-registered forward evaluation. v6.1 here is the frozen spec (spec_sha256 `8a3e5c6612aa72d0d9fc40b94d1ade4e40acb2ea649201750cbd74747db4573c`; v6.1 = v6 + tracker (no source beyond the tracker is PIT_PASS); tracker window days 90). It is not routed and no decision follows from this table.

- rule_sha256 `1481273f1fbd860a5e35ecc25a864818e74818f564aa0f6d5710ea42f524145f`; script sha256 `84ba443f28c5333849e7e8bc9ff55712a434f9673987f2bc4b942c203789b629`; generated 2026-11-15T07:40:36Z
- window: event start in [2026-11-14T10:00Z, 2026-11-15T10:00Z), ESPN scoreboards groups 80 and 81, FBS and FCS

## What was settled at run time

- games listed in the window: 117; completed: 117; unfinished at run time: 0; completed forfeits left out: 0
- cohort A (every completed non-forfeit game): 117; with an ESPN closing total 116; with an ESPN closing spread 116; line fetches that failed 0
- v6 no-calls (unrated team, counted, not scored): 0
- cohort A rows that also have a v5 ledger projection: 55
- cohort B (settled flat-tier cfb-structural-v5 total rows, one per event): 52; priced 52; left out 0
- closing lines: ESPN core odds fetched into the new file `data/research/ncaaf_v6/espn_lines_20261114.jsonl` (sha256 `5447ef0ecf8348bac3e1fc685998085dde9928e0a8492f819269b0db20c9b31a`), espn-lines-rule-1, `espn_lines.jsonl` untouched
- history: 16403 games (frozen snapshot plus 25 topped up from the scoreboards), 5 h result buffer, decision time = kickoff
- fresh v6 projections vs the frozen research rows for the same games: 92 overlapping, max abs difference 0.0
- tracker offsets applied to cohort A: mean -0.84 points, range [-1.10, -0.77], games averaged 1270 to 1363

## Seam check

A fresh v6.1 instance with offset 0 and a fresh v6 instance give exactly equal over/under/push probabilities on every row in the same order: cohort B rows 52, cohort A rows 117, mismatches 0. Exact equal: True.

Conventions: bias = mean(model - actual) in points; over lean = share of games where the model total is above the closing total, ties excluded; MAE in points. Percentages and units only.

## Cohort A: every completed 2026-11-14 game

#### A / all games

rows 117, v6 projected 117, no-call 0, projected and lined 116, on the common rows 116

| model, every projected game (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 117 | 13.36 | 5.79 | n/a |
| v6.1 | 117 | 13.04 | 4.95 | n/a |

| model, common lined rows (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 116 | 13.32 | 5.69 | 69.0% |
| v6.1 | 116 | 13.02 | 4.85 | 60.3% |
| closing line | 116 | 12.53 | 3.97 | n/a |

side vs the closing total, unpriced W-L-P: v6 51-65-0; v6.1 55-61-0

paired MAE difference, game-level bootstrap (one date, no clustering; negative = first is better): v6.1 - v6 -0.31 [-0.46, -0.16]; v6.1 - line +0.49 [-0.19, +1.18]; v6 - line +0.80 [+0.07, +1.52]

#### A / lopsided (closing home spread 28 points or more) or FBS-vs-FCS

rows 1, v6 projected 1, no-call 0, projected and lined 1, on the common rows 1

| model, every projected game (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 1 | 5.01 | 5.01 | n/a |
| v6.1 | 1 | 4.24 | 4.24 | n/a |

| model, common lined rows (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 1 | 5.01 | 5.01 | 100.0% |
| v6.1 | 1 | 4.24 | 4.24 | 100.0% |
| closing line | 1 | 3.50 | 3.50 | n/a |

side vs the closing total, unpriced W-L-P: v6 0-1-0; v6.1 0-1-0

#### A / all other games

rows 116, v6 projected 116, no-call 0, projected and lined 115, on the common rows 115

| model, every projected game (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 116 | 13.43 | 5.80 | n/a |
| v6.1 | 116 | 13.12 | 4.96 | n/a |

| model, common lined rows (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 115 | 13.40 | 5.70 | 68.7% |
| v6.1 | 115 | 13.09 | 4.86 | 60.0% |
| closing line | 115 | 12.60 | 3.97 | n/a |

side vs the closing total, unpriced W-L-P: v6 51-64-0; v6.1 55-60-0

paired MAE difference, game-level bootstrap (one date, no clustering; negative = first is better): v6.1 - v6 -0.30 [-0.45, -0.16]; v6.1 - line +0.49 [-0.24, +1.19]; v6 - line +0.79 [+0.06, +1.52]

#### A / FBS-involved games

rows 55, v6 projected 55, no-call 0, projected and lined 55, on the common rows 55

| model, every projected game (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 55 | 13.10 | 6.39 | n/a |
| v6.1 | 55 | 12.65 | 5.51 | n/a |

| model, common lined rows (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 55 | 13.10 | 6.39 | 76.4% |
| v6.1 | 55 | 12.65 | 5.51 | 63.6% |
| closing line | 55 | 11.39 | 4.45 | n/a |

side vs the closing total, unpriced W-L-P: v6 19-36-0; v6.1 22-33-0

paired MAE difference, game-level bootstrap (one date, no clustering; negative = first is better): v6.1 - v6 -0.45 [-0.64, -0.24]; v6.1 - line +1.26 [+0.14, +2.33]; v6 - line +1.71 [+0.58, +2.75]

### Cohort A, only games that also carry a v5 ledger projection (v5 on the same rows)

#### A with v5 / all

rows 55, v6 projected 55, no-call 0, projected and lined 55, on the common rows 55

| model, every projected game (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 55 | 13.10 | 6.39 | n/a |
| v6.1 | 55 | 12.65 | 5.51 | n/a |

| model, common lined rows (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v5 | 55 | 12.62 | 5.13 | 56.4% |
| v6 | 55 | 13.10 | 6.39 | 76.4% |
| v6.1 | 55 | 12.65 | 5.51 | 63.6% |
| closing line | 55 | 11.39 | 4.45 | n/a |

side vs the closing total, unpriced W-L-P: v6 19-36-0; v6.1 22-33-0; v5 20-35-0

paired MAE difference, game-level bootstrap (one date, no clustering; negative = first is better): v6.1 - v6 -0.45 [-0.64, -0.24]; v6.1 - line +1.26 [+0.14, +2.33]; v6 - line +1.71 [+0.58, +2.75]

#### A with v5 / lopsided or FBS-vs-FCS

rows 1, v6 projected 1, no-call 0, projected and lined 1, on the common rows 1

| model, every projected game (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 1 | 5.01 | 5.01 | n/a |
| v6.1 | 1 | 4.24 | 4.24 | n/a |

| model, common lined rows (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v5 | 1 | 5.86 | 5.86 | 100.0% |
| v6 | 1 | 5.01 | 5.01 | 100.0% |
| v6.1 | 1 | 4.24 | 4.24 | 100.0% |
| closing line | 1 | 3.50 | 3.50 | n/a |

side vs the closing total, unpriced W-L-P: v6 0-1-0; v6.1 0-1-0; v5 0-1-0

## Cohort B: settled flat-tier v5 total rows (priced)

#### B / all

rows 52, v6 projected 52, no-call 0, projected and lined 52, on the common rows 52

| model, every projected game (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 52 | 12.82 | 7.35 | n/a |
| v6.1 | 52 | 12.32 | 6.48 | n/a |

| model, common lined rows (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v5 | 52 | 12.29 | 5.95 | 57.7% |
| v6 | 52 | 12.82 | 7.35 | 78.8% |
| v6.1 | 52 | 12.32 | 6.48 | 65.4% |
| closing line | 52 | 11.21 | 5.33 | n/a |

side vs the closing total, unpriced W-L-P: v6 19-33-0; v6.1 22-30-0; v5 20-32-0

paired MAE difference, game-level bootstrap (one date, no clustering; negative = first is better): v6.1 - v6 -0.50 [-0.68, -0.28]; v6.1 - line +1.11 [+0.10, +2.21]; v6 - line +1.61 [+0.59, +2.71]

##### B / all: flat 1U PnL on the total market (ledger market_probability on the ledger side, 1.0476 minus it on the other side (4.76% vig))

| model | picks | W-L-P | PnL | ROI |
|---|---|---|---|---|
| v5 (ledger row's own result), all rows | 52 | 18-34-0 | -16.77 U | -32.2% |
| v6, all rows | 52 | 19-33-0 | -15.27 U | -29.4% |
| v6.1, all rows | 52 | 20-32-0 | -13.43 U | -25.8% |
| v5, 2-8% edge view | 20 | 5-15-0 | -10.11 U | -50.5% |
| v6, 2-8% edge view | 20 | 8-12-0 | -4.66 U | -23.3% |
| v6.1, 2-8% edge view | 19 | 7-12-0 | -5.44 U | -28.7% |

#### B / lopsided or FBS-vs-FCS

rows 1, v6 projected 1, no-call 0, projected and lined 1, on the common rows 1

| model, every projected game (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 1 | 5.01 | 5.01 | n/a |
| v6.1 | 1 | 4.24 | 4.24 | n/a |

| model, common lined rows (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v5 | 1 | 5.86 | 5.86 | 100.0% |
| v6 | 1 | 5.01 | 5.01 | 100.0% |
| v6.1 | 1 | 4.24 | 4.24 | 100.0% |
| closing line | 1 | 3.50 | 3.50 | n/a |

side vs the closing total, unpriced W-L-P: v6 0-1-0; v6.1 0-1-0; v5 0-1-0

##### B / lopsided or FBS-vs-FCS: flat 1U PnL on the total market (ledger market_probability on the ledger side, 1.0476 minus it on the other side (4.76% vig))

| model | picks | W-L-P | PnL | ROI |
|---|---|---|---|---|
| v5 (ledger row's own result), all rows | 1 | 0-1-0 | -1.00 U | -100.0% |
| v6, all rows | 1 | 0-1-0 | -1.00 U | -100.0% |
| v6.1, all rows | 1 | 0-1-0 | -1.00 U | -100.0% |
| v5, 2-8% edge view | 0 | n/a | n/a | n/a |
| v6, 2-8% edge view | 0 | n/a | n/a | n/a |
| v6.1, 2-8% edge view | 0 | n/a | n/a | n/a |

#### B / all other

rows 51, v6 projected 51, no-call 0, projected and lined 51, on the common rows 51

| model, every projected game (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v6 | 51 | 12.97 | 7.40 | n/a |
| v6.1 | 51 | 12.48 | 6.53 | n/a |

| model, common lined rows (points) | n | MAE | bias | over lean |
|---|---|---|---|---|
| v5 | 51 | 12.42 | 5.95 | 56.9% |
| v6 | 51 | 12.97 | 7.40 | 78.4% |
| v6.1 | 51 | 12.48 | 6.53 | 64.7% |
| closing line | 51 | 11.36 | 5.36 | n/a |

side vs the closing total, unpriced W-L-P: v6 19-32-0; v6.1 22-29-0; v5 20-31-0

paired MAE difference, game-level bootstrap (one date, no clustering; negative = first is better): v6.1 - v6 -0.49 [-0.68, -0.29]; v6.1 - line +1.12 [+0.07, +2.21]; v6 - line +1.61 [+0.57, +2.71]

##### B / all other: flat 1U PnL on the total market (ledger market_probability on the ledger side, 1.0476 minus it on the other side (4.76% vig))

| model | picks | W-L-P | PnL | ROI |
|---|---|---|---|---|
| v5 (ledger row's own result), all rows | 51 | 18-33-0 | -15.77 U | -30.9% |
| v6, all rows | 51 | 19-32-0 | -14.27 U | -28.0% |
| v6.1, all rows | 51 | 20-31-0 | -12.43 U | -24.4% |
| v5, 2-8% edge view | 20 | 5-15-0 | -10.11 U | -50.5% |
| v6, 2-8% edge view | 20 | 8-12-0 | -4.66 U | -23.3% |
| v6.1, 2-8% edge view | 19 | 7-12-0 | -5.44 U | -28.7% |

v6 and v6.1 chose different sides on 1 of 52 cohort-B rows.

Price rule: ledger market_probability on the ledger side, 1.0476 minus it on the other side (4.76% vig); pick = larger model probability minus ask; flat 1U; v5 = the ledger row's own result at 1U. Edge view: model probability minus ask in [2%, 8%] (v5 Main gate analogue).

The closing line makes no pick, so it has no PnL; it is the benchmark in the MAE tables.

## RETROSPECTIVE REPLAY: reading

One Saturday is a small sample drawn from the data the tilt was found on: the tables describe what happened, they do not validate v6, v6.1 or the tilt.
