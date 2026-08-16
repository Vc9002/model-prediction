# Prediction Playbooks — How to Predict Each Market (accuracy-first)

**Written:** 2026-08-17 (subagent accuracy-focused research pass; sources
peer-reviewed unless marked tier-3; numbers verified where cited).
**Goal:** predict outcomes accurately (proper scores: Brier, log-loss,
RPS, ECE). Economic value is out of scope by operator directive.
**Architecture (playbook 6 first, because it governs everything):**
each match gets ONE joint score distribution; moneyline, spread, and
total are different functionals of that same distribution — never
separate models. Calibration errors then become shared and fixable
once.

---

## 1. MLB run totals

**Distribution: Negative Binomial, not Poisson — confirmed, and the
largest single modeling error in the program.**
Bayesian league comparison (PMC8282683): MLB dispersion σp = 2.27;
LOO predictive fit NB beats Poisson by **4,116 points (dSE ±121)** —
decisive. Inning-level evidence ("Beyond runs expectancy", J. Sports
Analytics): actual scoreless-inning rate 0.738, NB fits 0.736, Poisson
0.630. Tier-3 confirmation (NATEBAGS repo, 8,100 games): NB
log-likelihood 1.89 vs Poisson 2.09.
*Note reconciling dive-3:* Dolinar observed game-level shutout
under-estimation in both NB and Poisson; the inning-level fit says NB
absorbs the zero mass. Resolution: NB is the default; zero-inflated NB
is a candidate to test empirically on our own snapshots (cheap).

**Expected runs per team — the multiplicative input stack:**
```text
E[R] = league_mean (≈4.35)
     × team_runs_rating        (Steamer/ZiPS-class projections, PA-weighted)
     × park_multiplier         (3–5yr park factor)
     × air_density_multiplier  (Bahill equation — closed form, no fitting)
     × starter_adjustment      (projected ERA vs league × expected IP share; FIP-based)
     × TTO_adjustment          (time-through-order: +13 wOBA pts per TTO, continuous)
     × lineup/platoon_adjustment (small; only for extremes)
```

**Published magnitudes:**
- Altitude: **+2.8 runs/game at 5,000 ft** (SABR "High Altitude
  Offense"); pre-humidor Coors 3.20 HR/game vs 1.93 elsewhere.
- Temperature: 8.95 → 10.08 runs/game cold→warm (AMS Weather, Climate
  & Society 2013, 22,215 games); air density: altitude 80% / temperature
  13% / pressure 4% / humidity 3% of variance; 10% density drop ≈ +4%
  fly-ball distance (Bahill 2009).
- Starter projections: out-of-sample ERA RMSE Steamer 0.832, xFIP-proj
  0.901, Marcel 0.907, PECOTA 1.024, ZiPS 1.030; last-year ERA alone
  1.282 (Swartz/FanGraphs).
- Time-through-order: +13.4 wOBA pts TTO1→2, +12.5 TTO2→3, continuous
  (Brill et al., JQAS 2023).
- Platoon: ~±25 wOBA pts average same-hand split, but true skill needs
  1,000–2,200 PA of regression — only model extremes (The Book).
- Home field: ~53–54% home win (modern era).

**Totals math:** fit NB per team-game (mean λ; dispersion from
variance ≈ 2.2×λ); `P(over L) = 1 − CDF(convolution, L)`.

## 2. MLB moneyline + run line

From the same two NB distributions, build the joint matrix
`P_ij = NB_h(i)·NB_a(j)`. Then:
```text
ML:  P(home) = Σ_{i>j} P_ij + P(tie) × ~0.54     (extra-inning home edge)
RL:  P(home −1.5) = Σ_{i−j≥2} P_ij ; away +1.5 = 1 − Σ_{i−j≤−2} P_ij
```
The run line needs the full differential distribution — especially the
P(diff=1) mass that NB overdispersion concentrates (relative to
Poisson); the ML only needs the sign. Never fit them separately; one
engine, then calibrate both (Platt on differential logits; reference
implementation achieves ECE 0.0168 vs MLB's own 0.0222).
**Ceiling honesty:** published state of the art ≈ 62% accuracy, Brier
≈ 0.23, AUC ≈ 0.67 (Cui, Wharton thesis, 9,700-game test); expect
calibration to improve faster than discrimination.

## 3. WNBA totals

**Decomposition:** `E[PTS] = pace × ORtg / 100` (Dean Oliver,
Basketball on Paper). WNBA pace ≈ 76–78 possessions, league scoring
≈ 80 ppg (2024).
**Inputs:**
- `pace_est`: rolling team pace, recency-decayed, opponent-adjusted.
- `ORtg_est`: rolling offensive efficiency (home/away split) ×
  opponent defensive adjustment.
- **Rest: +2.08 points per extra recovery day vs opponent** (NBA
  11,598 games; travel effect fully mediated by recovery); >1 day rest
  +1.1 home / +1.6 away; **back-to-backs cancel home advantage
  entirely** (Frontiers 2021). Transfer NBA magnitudes with a WNBA
  re-estimation step.
- Distribution: NB (σp ≈ 1.5 — NB's edge over Poisson is real but
  modest for basketball).
**Ceiling:** published WNBA game accuracy 67.5% (MLP, Computation
2025); stacking ≈ logistic regression; more features HURT MLP on small
leagues (62.6% at top-17) — keep WNBA models parsimonious.

## 4. Soccer totals (over/under)

**The engine is Dixon-Coles (1997) — the baseline every modern paper
must beat:**
```text
λ_home = base × att_home × def_away × γ      (γ ≈ 1.25–1.35 home multiplier)
λ_away = base × att_away × def_home
P(i,j) = Pois(λ_h,i)·Pois(λ_a,j)·τ(i,j)
```
- **τ correction** for the four low-score cells (0-0, 1-0, 0-1, 1-1):
  empirical 0-0 rate 8.4% vs 7.1% pure-Poisson; 1-1 11.2% vs 9.8%.
  ρ ≈ −0.13 (Lindstrom 2014; Sheehan 2017).
- **xG is the input layer:** xG predicts future goal-ratio better than
  goals (r = 0.574 vs 0.456); winner had higher xG in 73.3% of matches
  (vs 56.2% for shots). Update team strength on **xG margins**
  (xG-Elo beats goals-Elo). Best published xG model: gradient boosting,
  RPS 0.197 (Frontiers 2021, 105k Bundesliga shots).
- Poisson is adequate for goals (low-scoring sport — NB's gains are an
  MLB/NFL-scale phenomenon).
- `P(over 2.5) = 1 − F(0) − F(1) − F(2)` from the same grid as 1X2 —
  one distribution, all markets.
- **Benchmark:** bookmaker RPS 0.188 (World Cup 2002–14, Groll et al.)
  — the number to beat, and team-ability covariates improved EVERY
  criterion (features beat algorithms).

## 5. Esports winner (moneyline)

**Per-title models — feature spaces are incomparable across titles.**
```text
P(win) = logit( β1·Δrating + β2·Δdraft_score + β3·side_adv + β4·roster_recency )
```
- **Ratings: Glicko/Glicko-2, not Elo.** Out-of-sample NBA log-loss
  (2018-19, 1,230 games): Glicko −811.8 vs Elo −1,841.8 — uncertainty-
  aware updating is the difference. Roster changes reset a team's
  rating uncertainty. Explicit streak features ≈ zero value (LoL study).
- **Draft features are the strongest esports evidence in the
  literature:** Dota 2 draft-only logistic regression 69.8–72.9%
  accuracy; **pairwise hero-winrate interactions are worth ~10 accuracy
  points** over single-hero baselines; interaction-blind PCA features
  HURT. Use a rolling pairwise winrate matrix (patches change hero
  values — the meta drifts, re-estimate on a window).
- LoL: pre-game + in-game combined 76.8% accuracy, AUC 0.851; pre-game
  alone ~20 points worse.
- Calibrate (Platt, temporal splits); report ECE per confidence bucket
  — draft-only models earn their calibration at high-confidence buckets
  (67% accuracy where predicted >60%).

## 6. Cross-market principle + evaluation backbone

- **One joint distribution per match** (tennis: the O'Malley
  point→game→set→match chain from two serve-point probabilities —
  serve ≈ 0.65 men / 0.62 women — is the same principle in closed
  form; surfaces move first-serve point-win 69% clay → 75% grass).
- **Blending distributions: log-score stacking** (Yao, Vehtari,
  Simpson, Gelman) dominates BMA in M-open settings — BMA asymptotically
  selects one model, stacking optimizes the proper score directly.
  Re-fit weights on rolling windows; Pseudo-BMA+ as cheap fallback.
- **Evaluation:** Gneiting & Raftery (JASA 2007) — log score is the
  only proper local score; Brier for binary; reliability diagrams for
  calibration. **Linear models + good features equal or beat complex
  architectures everywhere this was tested** (iWinRNFL: LR Brier 0.158
  vs NN 0.156; WNBA stacking ≈ LR; Groll: covariates > algorithm) —
  spend effort on features and calibration, not architectures.

## Top-5 accuracy levers (proper-score gain per effort)

1. **NB instead of Poisson for MLB/NFL scores** — LOO +4,116 points;
   a one-line change once the mean model exists.
2. **Park/weather air-density layer on MLB run means** — ±2.8
   runs/game at altitude, ~1.1 cold-warm swing, closed-form published
   coefficients. Pure feature engineering, no fitting.
3. **One joint distribution per match + Platt/temperature + temporal
   splits** — coherence compounds every other lever; reference ECE
   0.0168.
4. **Esports: pairwise draft features + Glicko ratings** — ~10
   accuracy points from pairwise interactions; Glicko ≫ Elo.
5. **Soccer: xG-based strength updates + Dixon-Coles τ/γ** — the most
   mature literature in sports forecasting; benchmark RPS 0.188.

## Sources

Peer-reviewed: PMC8282683 (Bayesian home advantage, dispersion table);
"Beyond runs expectancy" (J. Sports Analytics); Brill et al. JQAS 2023
(TTO); Bahill 2009 (air density); SABR High Altitude Offense; AMS
Weather, Climate & Society 2013; Cui (Wharton, 9,700-game MLB test);
Dean Oliver, Basketball on Paper; Computation 2025 (MLB→WNBA ML study);
NBA rest/travel (PMC10109310; Steenland & Deddens 1997; Frontiers
2021); Dixon & Coles 1997 (JRSS-C); Koopman & Lit 2015 (JRSS-A);
Frontiers 2021 xG; PLOS One 2023 (xG predictive value); Groll et al.
(World Cup); CEUR 2017 + NTU 2020 (Dota 2 drafts); MDPI Applied
Sciences 2025 (LoL); Glickman & Jones, Annual Review of Statistics
2025; O'Malley JQAS 2008 + Klaassen & Magnus (tennis); Yao et al.
(stacking); Gneiting & Raftery JASA 2007; iWinRNFL. Tier-3 (labeled):
NATEBAGS repo, FanGraphs (Dolinar/Swartz), predictionengine.app.
