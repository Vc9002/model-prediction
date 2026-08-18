# Research decision — how to build a more accurate MLB model (2026-08-18)

Online research via agent-reach (Exa search + full-text reads), cross-referenced
against this system's own settled-picks evidence. The decision at the end is
binding for the next build phase unless the operator overrides it.

## What the research says

### 1. The market is the benchmark, and it's efficient (closing lines especially)
- DataField sports-betting textbook (ch. 11): Pinnacle closing odds are
  extremely well-calibrated; opening lines are measurably less accurate;
  CLV is the gold-standard skill measure; "you cannot consistently beat the
  closing line of a sharp book without genuine skill."
- Volodymyr4K/market-efficiency-lab (the closest prior art to this project —
  walk-forward-only, season-block bootstrap, sacred holdout year, honest
  negative results): across three studies, the ONLY durable edge found is
  **MLB totals, thinly** (+9.3% ROI, 95% CI [+4.0, +20.9], n=1,215 bets,
  2023–2026). MLB moneyline/runline: honest nulls, explicitly reported as
  "the sample cannot even detect an effect of plausible size" for 3 of 4
  markets. Tennis: their Elo beats all published models but remains +0.0083
  Brier behind Pinnacle closing, and no public-data feature closes the gap.
- Our own settled-picks evidence (2026-08-18, `ebed9d4`) says the same thing
  about THIS system: on the 143 decisions the promoted totals model actually
  made, the market-at-decision-time scored 56.6% direction-correct vs the
  model's 46.9%, and the model's picks are −12.13 units. The market is
  informationally stronger than us on MLB right now.

### 2. Where MLB totals edge actually lives: pitcher structure + Statcast-grade features
- sharksnip.com MLB starter writeup: the cleanest totals workflow is
  **project each starter's expected innings AND runs allowed, project the
  bullpen's expected innings AND runs allowed, combine into team
  runs-allowed, then layer lineup and park.** Known mispricings: two-ace
  games total too low (bullpens still pitch ~3 innings each); ace-vs-back-end
  moneylines overprice the ace; short-rest aces lose ~0.5 FIP edge.
- market-efficiency-lab's 63-feature MLB pipeline is dominated by Statcast
  features: SP xwOBA allowed 21d, K%/BB%/first-pitch-strike 21d, **fastball
  velocity LEVEL (trend was noise — 9-day window, 48% SHAP direction)**,
  SP depth (avg IP/start), platoon advantage vs LHB/RHB, last-3-starts form,
  lineup xwOBA, bullpen workload, park, weather, umpire, travel.
- ATSwins: NB regression for totals + LR/GBDT for wins; rolling 7/14/30d
  windows, platoon splits, features cut at first pitch, Platt/isotonic
  calibration, SHAP/permutation monitoring.

### 3. Weather: air density and wind DIRECTION × park orientation
- Sage 2014 study (n=2012 season): air density inversely related to total
  runs; simple strategies from it returned positive — on the totals market.
- Stanford MLB ParkCast (2026, Giants analytics): air density is the most
  significant park-factor predictor; **ambient wind direction matters more
  than wind speed**; the marine layer suppresses fly balls at West Coast
  night games. → Confirms the brainstorm backlog's wind-direction ×
  park-orientation item, and our air_density shadow module's premise
  (its totals delta −0.0007 was INCONCLUSIVE — this research says the
  missing half is wind direction, which we capture but don't model).

### 4. Run distribution families are a second-order problem
- ZINB for runs is the literature standard (Patriot's series; bravesjournal
  2023 team-by-team ZINB), and JSA "Beyond runs expectancy" shows multilevel
  inning-level models beat flat Poisson/NB fits.
- But our own Step-7 comparison already closed this on ~3k games:
  gamma_poisson beats NB, independent Poisson, AND ZINB (P(better) 0.175 /
  0.049 / 0.0). The accuracy-first queue's ZINB item is superseded by that
  test — do not re-litigate the family; the loss to the market is in the
  MEANS (pitcher/bullpen structure), not the tail.

## The decision

**Build in this order. Each stage has a gate; nothing proceeds without the
previous gate clearing. The market becomes a first-class input, not a
reference.**

### Stage 1 — Market-blend serving layer (highest EV per unit effort)
Every serving probability becomes `p_blend = w*p_model + (1-w)*p_market`,
`w` learned out-of-fold per (sport, market). Evidence already in-hand:
tennis λ=0.1 (holdout Δ−0.0186, P=0.9985), soccer λ=0.45 (Δ−0.0244,
P=0.9955), and the MLB totals settled record demands heavy market weight.
Shipping model: blend is applied at the decision boundary (edge/threshold
computation), NOT inside the model artifacts — the model keeps producing its
own probability (auditable), the blend is a separate calibrated policy layer
with its own experiment-registry entries. Gate: out-of-fold blend must beat
the model-only path on settled picks before serving.

### Stage 2 — MLB totals v2 (the accuracy-first rebuild, now evidence-shaped)
- Replace flat bullpen weakness with the sharksnip structure: **starter
  expected-IP distribution × starter runs-allowed, bullpen expected-IP
  distribution × bullpen runs-allowed**, combined into team runs-allowed,
  THEN lineup + park + weather. Starter-IP distribution feature exists in
  the accuracy-first queue; the earlier null was an *additive* test, this is
  a structural rebuild of the means.
- Add wind-direction × park-orientation (ParkCast + Sage support; raw data
  already captured per game in `game_snapshots.jsonl` weather block).
- Keep gamma_poisson as the draw engine. Do NOT touch the distribution
  family (Step 7 closed it).
- **Primary gate changes: settled picks and market-at-decision-time are the
  evaluation surface. The reconstructed-line archive stays a development
  convenience, never a gate** (all 340 rows are `timestamp_valid=false`).
- Short-rest ace discount (−0.5 FIP edge) as an explicit starter feature.

### Stage 3 — MLB moneyline: data acquisition before features
The v9+v10 result (23 variants, zero KEEP) is a coverage statement as much
as a feature statement: the frozen table has probable-starter data on only
280/6,558 rows and NO pitch-level features. Every credible ML improvement in
the literature is Statcast-grade (xwOBA, K-BB%, CSW%, velo LEVEL, platoon
splits). Decision: **acquire pitch-level ingestion (Statcast via the
existing pybaseball path or Statcast search CSV) and rebuild the frozen
feature table** before running any further ML ablations. Velocity trend was
shown to be noise in prior art; velocity level is the feature. Until that
data exists, further ML variant work is explicitly deprioritized — do not
burn the frozen table again.

### Stage 4 — Validation discipline upgrades (adopt from prior art, cheap)
- Minimum Detectable Effect (MDE) pre-check before every new feature test:
  if the sample can't detect a plausible effect, report an honest null
  instead of running it (3 of 4 MLB markets fail this in prior art).
- Season-block bootstrap for MLB (our date-cluster is close; block by
  season for MLB where season effects dominate).
- PROVISIONAL label on every retrospective acceptance; only the shadow
  ledger's forward record (we already have it) converts to confirmed.

### Not to do (negative decisions, with evidence)
- Re-litigate the run-distribution family (Step 7 closed it).
- Build more ML features on the current frozen table (coverage-bound).
- Chase line-movement/RLM signals (weak-form efficiency; datafield ch. 11).
- Treat reconstructed opening lines as decision-grade evidence.
- Keep the MLB totals promotion un-revisited: the settled record is
  negative; Stage 1's blend weight for MLB totals will effectively decide
  the serving question — operator review of the promotion is still the
  immediate call (see `ebed9d4`).

## Sources
- market-efficiency-lab: https://github.com/Volodymyr4K/market-efficiency-lab
  (README + sports/mlb/docs/FEATURES.md)
- DataField sports-betting textbook ch. 11 (market efficiency, CLV):
  https://datafield.dev/sports-betting-textbook/part-03/chapter-11/
- DataField ch. 17 (modeling MLB): https://datafield.dev/sports-betting-textbook/part-04/chapter-17/
- sharksnip MLB starter impact: https://sharksnip.com/blog/mlb-starting-pitcher-betting
- ATSwins advanced-stats MLB modeling:
  https://atswins.ai/articles/recent-articles/using-an-mlb-advanced-stats-prediction-model-to-predict-game-outcomes-and-player-props/
- Sage 2014, atmospheric conditions and the baseball totals market:
  https://sage.cnpereading.com/doi/10.1177/155862351400900305
- Stanford MLB ParkCast (2026): https://doi.org/10.25740/yf914wg4561
- JSA "Beyond runs expectancy": https://content.iospress.com/articles/journal-of-sports-analytics/jsa0001
- bravesjournal ZINB run distributions: https://bravesjournal.com/2023/08/03/predicting-runs-scored-and-allowed/
