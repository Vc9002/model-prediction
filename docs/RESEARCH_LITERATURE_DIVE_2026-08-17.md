# Research-Backed Improvement Levers — Literature Dive Mapped to This System

**Written:** 2026-08-17 (agent-reach research pass; Exa web search).
**Purpose:** take published/practitioner findings on profitable sports
prediction modeling and map each to THIS system — what we already do,
what we're missing, and where the adoption slots into the existing plans
(`docs/V9_RESEARCH_PLAN.md`, `docs/POST_MLB_RESEARCH_PLANS.md`,
`docs/ROADMAP.md`). Findings are synthesized, not copied;
sources are cited for verification.

---

## 1. What the literature says (synthesized)

### 1.1 Closing Line Value is the skill metric — and it's measurable here

*DataField sports-betting textbook, ch. 11 (case study: are closing lines
efficient?)* — across 100 simulated bettors × 300 bets, CLV-to-ROI
correlation r = 0.87; top-CLV-quintile bettors profitable 85% of the
time, bottom quintile ~5%. Closing lines are well-calibrated overall but
carry small persistent biases: **favorite-longshot bias** (longshot
probabilities inflated — betting big underdogs bleeds ~3× the vig cost;
favorites bleed ~1×), primetime/public-attention effects.

**This system already does:** captures closing snapshots prospectively
(`market_odds_snapshots.jsonl`), computes `probability_clv` on every
settled pick payload, and reports it. **Missing:** CLV is reported but
not yet gated on — the literature says CLV (not hit rate, not P&L
noise) should be the primary ongoing skill diagnostic. Adoption slot:
continuous-prospective-evaluation report (backlog) should lead with
CLV-by-model, and the Phase-5 shadow gate should include a minimum-CLV
criterion alongside proper-score criteria.

### 1.2 Polymarket sports markets: efficient price, noisy outcome — magnitude alone isn't a thesis

*Wajimaa, "Polymarket Efficiency and Mispricing" (substack, 2026-02,
10k+ high-performing trades)* — sports prices correct fast (~20h);
outcomes remain high-variance; "obvious mispricing" doesn't reliably
point at edge; conviction on extreme prices looks like overconfidence.
Sustainable edges need **information advantage, timing advantage, or
structure advantage** — not mispricing magnitude.

**This system's implication (concrete):** our edge-gating is magnitude-
based (model prob − market ask ≥ threshold). That's necessary but the
literature says it's not sufficient. The two backlog brainstorm items
that supply the missing layers are exactly right and should be
prioritized: **sharp-book lead/lag** (timing) and **line-movement**
(shadow module built 2026-08-16; weak first backtest, revisit with a
larger settled sample). Also: the current 1-hour stale-quote block on
orders is likely conservative-but-fine given ~20h correction times —
but it also means we're not using the fast-correction window for
timing decisions; a "price last moved X ago" feature belongs next to
the stale-gate, not instead of it.

### 1.3 Ensembles + market blending beat single models — with a gate

*gmalbert/ligue-1 (open-source Ligue 1 system)* — soft-voting ensemble
(XGBoost 2.0 / RF 1.5 / GBM 1.0 / LR 0.5), and crucially: **blend with
market-implied probabilities only when validation log-loss improves** —
"prevents the model from inventing artificial edges when the market is
already stronger."

**This system's implication:** the v9 plan runs LR vs XGB head-to-head
with LR winning on Brier/LogLoss. Add the third arm from the start:
**model−market blending** as a calibration-layer variant, accepted only
if OOF proper score improves (this is the "market-as-prior shrinkage"
backlog item, now backed by both this and §1.4).

### 1.4 Bayesian hierarchical scoring with odds-derived priors — published, profitable

*Dixon-era lineage: "Combining historical data and bookmakers' odds in
modelling football scores" (Statistical Modelling, 9 European leagues)* —
hierarchical Bayesian Poisson where each team's scoring rate is a
**convex combination of historical-data estimates and odds-derived
estimates**; published profitable backtests (EPL 33.5%/22.1%/49.0%
across three seasons in the related Kelly strategy paper). *DataField
ch. 10* gives the mechanism: priors as effective sample size
(Beta(α,β) carries α+β games of conviction).

**This system's implication:** this is the blueprint for Phase 6
(score distribution rebuild). Fit (μ_home, μ_away, dispersion) with
market-derived shrinkage on scoring rates — not a free-form blend, a
learned convex-combination weight with the odds as a hierarchical
prior. This is the single most literature-backed change the system can
make for totals, the known weakest structural piece.

### 1.5 Staking: fractional Kelly + explicit risk of ruin

*DataField ch. 4* — Kelly maximizes log-growth but is exquisitely
sensitive to probability-estimation error; the standard practice is
**fractional Kelly** (½ or ¼) calibrated to estimation error, with
risk-of-ruin computed analytically AND by Monte Carlo.

**This system's implication:** the `betting` skill (installed) covers
the math; the backlog's "formal bankroll re-scaling policy" should be
specified as: fractional-Kelly sizing from the *shrunk* probability
(§1.4's blend), fraction set by Monte Carlo risk-of-ruin at a
pre-registered drawdown tolerance, re-scaled only on a fixed cadence.
Combined with the correlation-aware exposure buckets (backlog
brainstorm #2), this closes the capital-management loop the literature
says separates profitable from unprofitable bettors independent of
handicapping skill.

### 1.6 Diagnostic decomposition: reliability / resolution / uncertainty

*DataField ch. 11* — Brier decomposes into reliability (calibration
error), resolution (separation of easy from hard games), uncertainty
(irreducible). The decomposition tells you *why* a score is what it is
— a model can improve resolution while degrading reliability, and
accuracy alone hides that.

**This system's implication:** add the three-way decomposition to the
standardized evaluator (`docs/V9_RESEARCH_PLAN.md` §0.4) — it's a few
lines on top of Brier and turns every ablation report from "Brier
moved" into "resolution up, reliability down" — which is exactly the
trade the XGB-vs-LR decision (§Phase 3) needs to be made on.

---

## 2. Adoption priority (what changes where)

| # | Adoption | Where it slots in | Effort |
|---|---|---|---|
| 1 | Brier decomposition (reliability/resolution/uncertainty) in the evaluator | V9 plan §0.4 (Phase 0, now) | small |
| 2 | Model−market blending as a named calibration-layer variant, accepted only on OOF proper-score improvement | V9 plan §4 (calibration) + backlog market-prior item | medium |
| 3 | Odds-derived hierarchical priors on scoring rates | V9 plan §6 (totals rebuild) — replaces free-form blending | large |
| 4 | Fractional-Kelly sizing + Monte Carlo risk-of-ruin + pre-registered re-scale cadence | Backlog bankroll policy item → spec now, applies before any real-money promotion | medium |
| 5 | CLV as the lead metric in continuous prospective evaluation + CLV criterion in the Phase-5 shadow gate | Backlog continuous-evaluation item + V9 plan §7 | small |
| 6 | Timing/structure advantage layer (sharp-book lead/lag; price-age feature) beside the stale-quote gate | Backlog brainstorm #2 + line-movement revisit | medium |
| 7 | Favorite-longshot exposure audit: are our qualified calls systematically fading favorites or chasing longshots? | New — one analysis pass over the ledger, pre-Phase-2 | small |

---

## 3. System capability inventory (verified baseline this session)

For completeness — the verified state these levers build on:

- **Data assets:** `game_snapshots.jsonl` (full box scores + officials +
  weather + starters), `market_odds_snapshots.jsonl` (repeated per-event
  price observations — line movement), `point_in_time/` archives
  (probable starters, PIT-eligible), ESPN disk caches, closing-price
  capture, sqlite canonical ledgers with per-pick decision payloads
  (full feature vectors at decision time).
- **Governance:** shadow-first promotion chain, pre-registration
  template, paired bootstrap comparisons, per-run incumbent-
  reproduction gate (plan §0.4), frozen artifact hashes, audit chain
  with chain-verify.
- **Tooling:** feature freezer + manifest, experiment registry,
  pin-and-replay reproduction, row-parity harness (baseline measured
  2026-08-17: probability parity max |Δ| 0.0006, call-set identity
  exact), hypothesis PIT property suite, standardized evaluator in
  progress (Brier/LogLoss/bootstrap present; decomposition pending).
- **Known structural gaps the literature targets directly:** totals
  model (→ §1.4), capital management formalization (→ §1.5), CLV
  usage (→ §1.1), timing advantage (→ §1.2).

## Sources (tiered; social media is deliberately not a tier)

Source policy for this document: adoptions are justified only by
peer-reviewed work, published textbooks, or open-source implementations
whose results can be reproduced locally. Practitioner analyses with
large disclosed datasets rank as supporting context, not evidence.
Social media (Twitter/X, Reddit, forums) is **not** a citable tier —
anecdotes and engagement-optimized claims don't survive contact with
this system's promotion gates, and no finding here rests on one.

- **Tier 1 — peer-reviewed:** "Combining historical data and bookmakers'
  odds in modelling football scores", Statistical Modelling (Sage),
  9-league hierarchical Bayesian Poisson. Related: "Profiting from the
  English Premier League" (predictive elicitation + Kelly + black-swan
  stopping rules).
- **Tier 1 — textbook:** DataField sports-betting textbook — ch. 11
  (closing-line efficiency, CLV, favorite-longshot bias, Brier
  decomposition), ch. 4 (bankroll, Kelly, risk of ruin), ch. 10
  (Bayesian priors as effective sample size).
  — datafield.dev/sports-betting-textbook
- **Tier 2 — open-source implementation:** gmalbert/ligue-1 — Ligue 1
  soft-voting ensemble with market-blending gated on validation
  log-loss; adoptions from it must reproduce locally before they count.
  — github.com/gmalbert/ligue-1
- **Tier 3 — practitioner analysis (context only):** Wajimaa,
  "Polymarket Efficiency and Mispricing" (2026-02-13) — 10k+ disclosed
  Polymarket trades; cited for the correction-speed/timing framing only,
  and any adoption derived from it must first pass this system's own
  backtests on its own data.
  — wajimaaa.substack.com
