# Research Dive 3 — Deep Subagent Pass: Distributions, Microstructure, PM Capacity, Open-Source Audits

**Written:** 2026-08-17 (subagent deep-research pass; 7 areas, 40+ sources,
12 read in full text, citations crossref-verified; same tiered source
policy as dives 1–2 — load-bearing numbers from peer-reviewed work or
official docs, tier-3 labeled inline).
**Relation to dives 1–2:** this pass adds hard numbers and, in three
places, *reverses or tightens* earlier recommendations. Reversals are
called out explicitly.

---

## 1. MLB run distributions — NB with zero-inflation, starter-IP as the bullpen input

**Sources:** Lindsey 1963 (Operations Research — origin of the
run-expectancy matrix, 6,000+ half-innings); Albert 2015 (J. Sports
Analytics — "the Poisson is a poor fit to run scoring data"; tried NB
and zero-inflated, dropped them only for parameter count); Bukiet et al.
1997 (Operations Research — Markov-chain run distributions); Dolinar
2014 FanGraphs (tier-3, the only published parameter values, Retrosheet
2008–2013): **variance ≈ 2.2× mean, NB r ≈ 3.7 per team-game, and both
Poisson and NB underestimate the shutout rate** (managerial bullpen
effect — zero-inflation needed); Clemens 2024 FanGraphs (tier-3):
**starter IP ≈ 5.2 and falling** (5.97 in 2014; only 39.8% of starts
≥6 IP in 2023 vs 60.7% in 2013) — right-skewed, shrinking.

**Adoption delta (Phase 6 totals):** the run-distribution candidate set
in `docs/V9_RESEARCH_PLAN.md` §6 should be **NB (r ≈ 3.7 as the prior,
re-estimated from our own snapshots) + zero-inflation for the shutout
tail**, not plain Poisson. Totals are priced on exact run counts, so
the shutout tail and 2.2× overdispersion move over/under probabilities
meaningfully. Add a **starter-IP distribution feature** (PIT-safe,
mean ≈5.2, falling over time) to feed bullpen-exposure — the bullpen
decides the second half of totals variance.

## 2. Umpires — real, time-varying, and nobody publishes per-umpire pricing

**Sources:** Mills 2016 Economic Inquiry (peer-reviewed, crossref-
verified): called strike-zone growth accounts for **20–40% of the
2009–2014 run-scoring decline — 0.3–0.5 runs/game directly attributable
to umpire ball-strike calls**; Mills 2016 Labour Economics: low-strike
accuracy 78.7%→87.8% over the same window; SABR Umpire Analytics 2017
(full text): 89.9% accuracy, best umpires miss ~12–13 calls/game,
"considerable" per-umpire spread; **no peer-reviewed paper prices
individual umpires into over/under lines** — the gap is unfilled.

**Adoption delta:** the backlog's umpire item is now peer-reviewed-core.
Build a **rolling umpire called-strike-rate / zone-size z-score** from
Statcast (free, lag-safe). Honest expectation: ±0.1–0.3 runs/game for
extreme umpires — small, real, plausibly unpriced. Validate on CLV of
the umpire-affected segment before trusting it; the era effect is
time-varying, so the feature must be rolling, not static.

## 3. Microstructure — one concrete, sport-matched, peer-reviewed anomaly

**Sources:** **Simon 2024, Management Science 70(12) — the strongest
finding of the entire pass**: 3,681 MLB games, 4 sportsbooks, 10-minute
moneylines open→close. Lines are negatively autocorrelated (markets
overreact; weak-form efficiency rejected), and on **weekend day games,
final-90-minute line moves are fadeable: betting the team whose price
rose in the last 90 minutes returned 10–13% ROI** (β₂ = −4.496,
p = 0.008). Paul & Weinbach 2008 (J. Sports Economics): against first
moves to favorites, with last moves to underdogs (LV venue; offshore
book was efficient — venue-dependent). BetBetter 2026 (tier-3, 11.7M
snapshots): **MLB open→close de-vigged move averages 0.60 pp (totals
0.09 pp; 16% of totals move at all)** — MLB is the deepest, most stable
market; there is almost no open-vs-close CLV to harvest. Miller &
Rapach 2013 (J. Empirical Finance): lines slanted against prestige
teams, stale/sentiment prices persist within a week. Woodland &
Woodland 1994 (JF): MLB reverse favorite-longshot bias (favorites
underbet) — stable across decades but NOT exploitable net of
commission.

**Adoption delta:** (1) add a **90-minute-move fade signal, conditioned
on weekend day games** — the single most concrete peer-reviewed
sport-matched anomaly found anywhere in this research program, and it
lives in the movement data our CLV pipeline already captures; (2) set
expectations: in MLB, betting early is nearly free (open ≈ close), so
"CLV" in this sport mostly means *not getting worse than open*, not
beating the close; (3) the line-movement module upgrade from dive 2
(first/last decomposition) gets the Simon condition layered on top.

## 4. Recency weighting — exponential in event-time, grid-searched; Elo loses to market baselines

**Sources:** Hvattum & Arntzen 2010 (Int. J. Forecasting, full text):
**Elo-based football models were significantly worse than market-odds
benchmarks but better than all non-market methods; statistical loss
functions beat ROI measures for model comparison.** Pelánek 2014 (full
text): exponential and hyperbolic decay both strong; sliding-window and
linear decay significantly worse; decay rate grid-searched in
validation. Aldous 2017 (Statistical Science): simulation doubts the
"30 matches suffice" claim; Glickman 1999 (JRSS-C) as the Bayesian
alternative.

**Adoption delta:** exponential decay in **games-played** (event-time,
not calendar — MLB plays daily) on team-strength inputs, rate chosen
by walk-forward grid search, never assumed. The Hvattum/Arntzen result
re-inforces the dive-1 architecture decision: **de-vigged market price
is the base rate; the model earns its keep on the residual.**

## 5. Uncertainty & calibration — conformal is REVERSED; blend weight leans market

**Sources:** Egidi, Pauli, Torelli 2018 (Statistical Modelling, read in
full with numbers): hierarchical Bayesian Poisson, scoring rates as
convex combinations of history + odds. **The blend lands ~1–2 Brier
points of the raw market (slightly worse), yet betting at model
probabilities yields positive expected profit where bookmaker
probabilities are always negative** — the edge lives in selected spots,
not average calibration; market weight dominates the blend. Koning 2023
(Annals of OR): Shin de-vigging is the calibration-safe choice (league
bias under basic normalization). Wunderlich & Memmert 2020 (IJF):
**betting returns are a high-variance, unreliable measure of forecast
accuracy — proper scoring rules for comparison.** Conformal prediction:
an arXiv sweep found **no published sports-betting application
demonstrating value** — the field is empty.

**Adoption delta:** (1) **REVERSAL of dive-2 §4:** deprioritize the
conformal-prediction layer — it is an unproven novelty for binary
betting decisions; isotonic/Platt recalibration + proper scores is the
state of the art that's actually supported. (2) **TIGHTENING of the
blend design (dive-1 §1.4 / dive-2):** the market weight in the blend
should be *expected to dominate*; the model's value is tail
opportunities. Gate on Brier-vs-de-vigged-market (Shin), never ROI
(Wunderlich/Memmert). This is consistent with our "no gates lied
about" ethos — a blend that's within noise of the market is the
expected honest outcome, not a failure.

## 6. Open-source systems worth copying patterns from (audited for honesty)

**Sources (GitHub, README-audited):** the field is thin on honest
reporting — only two repos self-grade with null-inclusive results:

- **crollila/polymarket-vegas-edge** — closest to our thesis
  (de-vigged FanDuel vs live PM, fractional Kelly, CLV tracking,
  bootstrap CI). Post-mortem: +6.9% ROI with **95% CI [−3.5%, +17.8%]
  spanning zero; 69% of profit from two positions; two ledger-convention
  bugs flipped a +$643 record to −$1,331.** The honest-null structure
  is the model to copy.
- **outpostmyles/overlay** — de-vigged PM as sharp anchor, frozen
  pre-game predictions auto-graded: MLB anchor run **null result
  reported as such** (market favorites 57.9% vs 57.5% expected);
  World Cup model won picks but lost Brier (0.446 vs 0.418).
- **kofuj/game163** — closest MLB feature match (Elo diff, L10/L30
  rate differentials, run diff, rest, starter ERA/WHIP; gradient
  boosting + isotonic; walk-forward; PIT discipline documented).
- **braedonsaunders/homerun** — full PM platform: L2 book replay,
  **Cox-PH fill-probability model, microstructure-aware shadow fill
  simulator** — directly reusable for our shadow execution.
- **philippdubach/polymarket-microstructure** — replication package
  for the Anatomy paper (Area 7), usable as-is for PM backtests.
- flumine/betfairlightweight (Betfair frameworks); dsw225/
  TennisPredictionModel (CLV-graded tennis); ianalloway/
  awesome-sports-betting (curated list).

**Adoption delta:** copy three patterns — (1) vegas-edge's post-mortem
structure: **plan for the null**; a live 200-bet sample with a CI
spanning zero is the *expected* early outcome, and their ledger-bug
episode is a direct warning to audit our sqlite ledger conventions;
(2) overlay's frozen-line auto-grading (maps 1:1 to our PIT rule);
(3) homerun's fill-simulator for shadow execution, game163's game-level
splits.

## 7. Polymarket capacity & fees — the cost model that gates everything

**Sources (arXiv 2026, official docs):** Anatomy of a Decentralized
Prediction Market (30B order-book events; pre-registered 600-market
panel): **half-spread ≈ 200 bps of mid at central prices; full spread
≈ 400 bps in [0.4, 0.6]; 1,300–1,800 bps below 0.10; top-of-book holds
only 13.6% of top-10 depth (geometric grid) → marketable limits beat
market orders; feed-inferred trade direction agrees with on-chain truth
only ~59%** → any direction-dependent backtest must use on-chain
OrderFilled events. NBA arbitrage paper: capacity-bound — 76.9% of
anomalies executable at a median 14.8 shares. Official fee docs:
Sports taker fee 0.05 rate → **2.5% of notional at 50¢ (5% of an
even-money stake)**; maker rebates exist.

**Adoption delta:** an explicit cost model in the bet-gating pipeline:
at 50¢, round-trip ≈ 2.5% notional taker fee + ~4% spread ⇒ **every
edge threshold must clear ~3–4% before it means anything**; longshot
PM legs (<10¢) are structurally unplayable for size; in-game windows
are capacity-bound (~15 shares); shadow-execution simulations need a
fill model, not assumption of top-of-book execution. This also
sharpens the fractional-Kelly sizing spec (dive-1 §1.5) — Kelly on
unadjusted model edge overstates stakes by the whole cost layer.

---

## Top-5 highest-leverage findings (ranked EV per unit effort)

1. **Anchor to de-vigged market; model adds only the residual** — three
   independent results agree (Hvattum/Arntzen; Egidi; overlay's live
   ledger). Effort: low. Determines architecture.
2. **Simon 2024: fade final-90-minute MLB weekend-day moves (10–13% ROI,
   3,681 games, 4 books).** Effort: low — snapshots exist. Most
   concrete peer-reviewed sport-matched anomaly in the whole program.
3. **Polymarket cost model: ~3–4% round-trip at 50¢, unplayable below
   10¢, ~15-share live-window capacity.** Effort: minimal. Gates
   edge thresholds and Kelly sizing.
4. **NB r≈3.7 + zero-inflation for totals; starter-IP ≈5.2 falling as
   the bullpen input.** Effort: low-moderate. Tail accuracy = totals
   money.
5. **Umpire strike-zone feature: 0.3–0.5 runs/game class effect,
   peer-reviewed, no published per-umpire pricing.** Effort: low.
   Possibly partially unpriced.

## Where these land (doc deltas)

- `docs/V9_RESEARCH_PLAN.md` §6: NB+zero-inflation replaces plain
  Poisson; starter-IP feature added.
- `docs/V9_RESEARCH_PLAN.md` §3: gate metric explicitly
  Brier-vs-de-vigged-market (Shin), never ROI.
- Line-movement backlog item: + Simon 90-min weekend-day condition;
  + "MLB open≈close: bet early" expectation.
- Dive-2 conformal item: **reversed** — deprioritized.
- Backlog sizing item: + PM cost model as a hard gate (3–4% round-trip).
- Backlog umpire item: upgraded to peer-reviewed-core with the rolling
  z-score design and CLV validation plan.
- New backlog item: on-chain OrderFilled-based microstructure backtests
  (feed-inferred direction is only 59% reliable); homerun-style fill
  simulator for shadow execution.

## Sources

Peer-reviewed: Lindsey 1963 (OR); Bukiet 1997 (OR); Albert 2015 (JSA);
Mills 2016 ×2 (Economic Inquiry; Labour Economics); Simon 2024 (Mgmt
Sci); Paul & Weinbach 2007/2008 (JPM; JSE); Woodland & Woodland 1994
(JF); Miller & Rapach 2013 (JEF); Hvattum & Arntzen 2010 (IJF); Egidi
et al. 2018 (Stat Modelling); Koning 2023 (Annals OR); Wunderlich &
Memmert 2020 (IJF); Aldous 2017 (Stat Sci); Glickman 1999 (JRSS-C);
Pelánek 2014; arXiv 2026 Polymarket papers (Anatomy; NBA arb;
Executable Arbitrage; Polymarket-v1). Official: Polymarket fee docs.
Tier-3 (labeled): FanGraphs (Dolinar; Clemens), ESPN, BetBetter, GitHub
repos. The reversal of dive-2's conformal item and the blend-weight
tightening are this document's explicit corrections.
