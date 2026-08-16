# Research Dive 2 — Baseball Microstructure, Uncertainty Economics, Cross-Platform Signal

**Written:** 2026-08-17 (agent-reach pass 2; same tiered source policy as
`docs/RESEARCH_LITERATURE_DIVE_2026-08-17.md` — peer-reviewed / textbook /
open-source only, social media excluded).
**Relation to pass 1:** pass 1 covered CLV, market efficiency, blending,
Bayesian Poisson priors, Kelly, Brier decomposition. This pass covers the
MLB-specific model blueprint, line-movement microstructure, the economics
of calibration, uncertainty quantification, and cross-platform
prediction-market ↔ sportsbook signal.

---

## 1. The MLB model blueprint is published — and matches the plan

*DataField sports-betting textbook, ch. 17 (Modeling MLB)* — the full
chapter maps to this system's v9 plan almost one-to-one, which is
independent validation of the plan's shape:

1. **Pitcher-centric models dominate** — a starter faces 55–78% of
   batters; ace-vs-replacement is ~2.5 runs/game; **a starter scratch
   moves the line 50–100 cents in minutes** (the single largest
   timing-advantage window in the sport).
2. **Sabermetric inputs**: wOBA, FIP, wRC+ translated to game-level
   probabilities (our starter FIP/K-BB% ablations are the v9 instance).
3. **Park factors with environmental adjustments**: temperature,
   altitude, humidity, wind — wind is "the most impactful weather
   variable for a single game" (15 mph out to CF adds 20–30 ft of fly
   distance). This upgrades the backlog's wind×park brainstorm item from
   "interesting" to "textbook-core," and the Coors/Oracle examples match
   the altitude item.
4. **Poisson + negative binomial run-scoring models** for ML/runline/
   totals — the Phase-6 plan's exact distribution set.
5. **MLB-specific market patterns named as exploitable**: reverse line
   movement, umpire effects, seasonal inefficiencies. The umpire item
   (§backlog brainstorm #2) is now textbook-cited, not just conventional
   wisdom.

**Adoption delta:** none structurally — the v9 plan already follows this
blueprint. One addition: the ch. 17 framing justifies adding
**handedness and workload** to the starter feature set (currently
ERA/FIP/K-BB% only), as a Phase-2-adjacent candidate after the matrix.

## 2. Line-movement microstructure — the profile-conditional finding

*Paul & Weinbach (2007), "Line Movements and Market Timing in the
Baseball Gambling Market", Journal of Sports Economics* — MLB-specific:
**betting against first movements to favorites and with last movements
to underdogs** generated statistically significant profits in Las Vegas
data (offshore book appeared efficient). I.e., early favorite-steam is
public money to fade; late underdog-steam is information to follow.

*Price movements and the prevalence of informed traders (college
basketball; ScienceDirect)* — closing lines beat opening lines; **line
movements in low-profile games are more likely information-realization;
movements in high-profile games are more likely noise.** Public
attention draws noise traders.

**Adoption delta (concrete, on existing tooling):**
- The line-movement shadow module (`features/line_movement.py`,
  built 2026-08-16) currently measures first→decision movement on the
  picked side. Upgrade to a **two-segment decomposition**: first-movement
  (open→early) vs last-movement (late→decision), with the
  **profile-conditioned interpretation**: a move against us in a
  low-profile game is the strongest fade signal; a move with us in a
  high-profile game is weak (noise). This is a small, testable extension
  — no new data needed; both timestamps exist in
  `market_odds_snapshots.jsonl`.
- "Game profile" needs a proxy: league-wide public attention is not in
  our data; usable proxies that ARE: market volume/liquidity if
  captured, market size tier, team market size, game slot
  (primetime/weekend flags — ch. 11 pass 1 found primetime effects).

## 3. Calibration is the economic lever — not accuracy

*Montrucchio, Barbierato, Gatti (2026), "Uncertainty-Aware Machine
Learning for NBA Forecasting in Digital Betting Markets", MDPI
Information (peer-reviewed)* — comprehensive NBA study with strict
chronological splits, ablations to remove odds circularity, and a
fractional-Kelly betting simulator. Findings that map directly:

- **Calibration-selected pipelines achieve higher ROI than
  accuracy-selected ones** (citing Walsh & Joshi) — probability
  reliability affects economic outcomes even when classification
  metrics are comparable. Our proper-score gating (Brier/LogLoss over
  accuracy for LR-vs-XGB) is the same decision, now externally
  corroborated.
- **Economic value concentrates in less-efficient segments: moneylines
  beat spreads/totals.** Matches our live experience (MLB moneyline
  edge found; totals the weakest piece).
- **Cross-season drift is real** (ensemble AUC 0.90 in-season → 0.78–0.80
  across seasons). Confirms the continuous-prospective-evaluation +
  drift-alert backlog item as load-bearing, not nice-to-have.
- Their decision layer = calibrated probs → fractional-Kelly + EV
  threshold + bootstrap uncertainty — identical to what pass 1 and the
  bankroll backlog item specify.

**Adoption delta:** none structurally; adds the evidence citation to the
calibration-first ordering and to the "accuracy never decides" rule in
`docs/V9_RESEARCH_PLAN.md` §3.

## 4. Uncertainty quantification — conformal prediction is the next layer

*Conformal Prediction for Time-series Forecasting with Change Points
(NeurIPS 2025)* and the Jumbo-Visma calorie-forecasting case
(MLR Press 2023) — conformal methods give valid, distribution-free
prediction intervals; for binary game outcomes the analogue is
conformalized probability sets: per-pick uncertainty bounds that are
valid regardless of model family.

**Adoption delta:** add a conformal-prediction layer to the evaluator
roadmap (after the §0.4 metric set): per-pick predictive sets with
coverage guarantees feed directly into sizing (wider uncertainty →
smaller Kelly fraction). It composes with the existing bootstrap —
bootstrap quantifies coefficient/sample uncertainty, conformal
quantifies per-pick validity. Cheap relative to payoff; schedule
post-Phase-4.

## 5. Cross-platform: prediction-market ↔ sportsbook signal and arb

*Market Math, "Sportsbook vs Prediction Market Arbitrage" (2026-03)* —
cross-platform arbs of 4–8% exist because the two platforms price
events with different mechanisms and **different information arrival
speeds: PMs reprice in minutes on breaking news; sportsbooks take
hours on non-core events.** Polymarket's 0% trading fee makes it the
preferred PM leg. The non-negotiable mechanics: de-vig the sportsbook
side before any comparison; account per-platform fees.

**Adoption delta — this re-prioritizes an existing housekeeping item:**
rotating The Odds API key (open item in PROJECT_STATUS repair order)
stops being mere hygiene and becomes a **research unlock**. With a live
multi-book feed we gain (a) cross-platform lead-lag: PM price vs
sharp-book price divergence as a timing signal — the concrete instance
of the sharp-book lead/lag backlog item; (b) arb screening between the
Polymarket market and sportsbook odds (execution-manual, so arbs are
opportunities, not automation); (c) the de-vig math already exists in
the installed `betting` skill. Priority bump: key rotation moves from
"whenever" to "before Phase-5 shadow," so the shadow window can capture
cross-platform snapshots alongside PM snapshots.

## 6. Consolidated adoption deltas (what changed vs the existing docs)

| Delta | Where it lands |
|---|---|
| Starter features: + handedness, + workload (post-matrix candidate) | `docs/V9_RESEARCH_PLAN.md` Phase 2 note |
| Line-movement module: first/last-movement decomposition + profile conditioning (low-profile moves = information) | line-movement backlog item upgrade; module extension testable now |
| Game-profile proxy: market-size/slot/primetime flags from existing capture | same item |
| Conformal prediction layer for per-pick uncertainty → sizing | evaluator roadmap, post-Phase-4 |
| The Odds API key rotation = research unlock (lead-lag + arb screening), priority bumped to before Phase-5 | `docs/PROJECT_STATUS.md` repair item 4 re-scope |
| Calibration-first + accuracy-never-decides: external corroboration cited | `docs/V9_RESEARCH_PLAN.md` §3 citation only |
| Cross-season drift: evidence cited for continuous-evaluation backlog item | backlog item citation only |

No direction changes: every delta extends or re-prioritizes an item
already in the plans.

## Sources

- **Tier 1 — peer-reviewed:** Paul & Weinbach (2007), J. Sports
  Economics (MLB line movements); "Price movements and the prevalence
  of informed traders" (college basketball, ScienceDirect);
  Montrucchio et al. (2026), MDPI Information 17(1):56 (NBA
  uncertainty-aware forecasting); NeurIPS 2025 conformal + change
  points; MLR Press v204 (conformal, Jumbo-Visma case).
- **Tier 1 — textbook:** DataField ch. 17 (Modeling MLB).
- **Tier 3 — practitioner (context only):** Market Math
  (cross-platform arb mechanics; any adoption passes our own
  backtests first).
