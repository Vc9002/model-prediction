# Domain Pitfalls

**Domain:** Quant sports-prediction / prediction-market trading system (model-prediction, live real-money Polymarket execution)
**Researched:** 2026-09-22
**Overall confidence:** MEDIUM (repo-grounded findings are HIGH; external ecosystem findings are cross-verified web search, MEDIUM per this project's confidence classifier — no primary academic papers or vendor docs were directly fetched)

## Grounding: what Phase 23 already mitigates

Before reading the pitfalls below, note what this repo has **already
implemented** (`docs/ROADMAP.md` "Phase 23 — Formal Promotion Gate",
rewritten 2026-08-27 per the operator's market-edge directive, and
"Phase F — Market-Relative Edge & Information Discovery Roadmap"):

- **Gate 1 (Predictive, market-relative):** paired ΔLogLoss < 0 and
  ΔBrier ≤ 0 **vs. no-vig market probability** (not vs. 0.5), date-cluster
  bootstrap P(better) ≥ 80%. Calibration reported **model × market_type ×
  league**, calibrator selected only on out-of-fold data, never on final
  holdout.
- **Gate 2 (Operational):** ≥95% serving coverage, zero PIT leakage,
  latency <500ms, graceful fallback tested.
- **Gate 3 (Prospective):** statistically meaningful sample of live,
  untouched games (explicitly "not 5 games, not 10 bets").
- **Gate 4 (Economic, primary gate):** full `market_eval.market_relative_report`
  battery on timestamped executable prices — ROI with date-clustered
  bootstrap CI excluding 0, profit factor ≥ 1, CLV rate ≥ 50% vs. closing
  no-vig, stability slices (month-to-month, favorite/underdog, home/away,
  high/low), hit-rate demoted to diagnostic-only.
- **Reproduction-gate discipline:** every "promising" ablation result is
  re-run against the incumbent's own frozen numbers before being trusted
  (e.g., MLB half-inning decomposition PASS-vs-incumbent 0.690434 vs.
  0.6910 before being called anything other than a null).
- **A prior, concrete finding this gate structure was built to answer:**
  the WNBA totals market-relative evaluation found the market line beats
  the model decisively (MAE 13.12 vs 20.92, Brier 0.252 vs 0.369, ROI
  −4.1%) on 89 real-market-line holdout games — i.e., a model can look
  "reasonable" in isolation and still be worthless once benchmarked
  against the market it would actually trade against. This is precisely
  the calibration-vs-edge distinction covered below, already internalized
  once by this project.

This means several classic pitfalls below are **already addressed
structurally**, and this file flags them as such rather than treating
them as open gaps. The pitfalls that remain genuinely open are called out
explicitly in each section and summarized in "Phase-Specific Warnings."

## Critical Pitfalls

### Pitfall 1: Look-ahead bias / point-in-time (PIT) leakage

**What goes wrong:** A feature, label, or evaluation step uses information
that would not actually have been available at decision time T. The model
looks highly accurate in backtest and decays or collapses live.

**Why it happens:** Unshifted rolling-window aggregates (a rolling average
computed over a window that includes the current/future observation),
retroactively-confirmed lineups/rosters used as if known pre-game, global
normalization/scaling fit over the whole historical dataset (including
future rows relative to a given decision point), non-chronological
train/test splitting, and — subtly — capturing `utc_now()` before a slow
data-building call finishes internally timestamping itself (this exact bug
class already zeroed every real WNBA/KBO/NPB pick in this repo silently
for months; see `CLAUDE.md`'s "point-in-time correctness" section).

**Consequences:** Backtest metrics are systematically inflated; the model
looks like it has edge purely because it "knows" the outcome or near-outcome
indirectly. Live performance regresses toward — or below — the market,
often invisibly (no error is raised; the pipeline just silently produces
worse picks).

**Prevention:** This is already this repo's own documented #1 invariant —
"by wide margin, single most common source of real bugs." The general
industry pattern matches: strict d-1 (or finer) temporal safety buffers on
rolling features, last-known-value heuristics instead of retroactive
ground truth for uncertain pre-event state (lineups, injury status),
walk-forward-only validation (`validation.py::build_walk_forward_rows`,
`chronological_split`), and — a subtlety worth re-emphasizing for new
sports/features — **checking granularity, not just chronology**: a
date-only field compared against a full timestamp is a leak (same-day ≠
before), and a live-only signal (current roster snapshot) must never be
used for a past decision time even if it "would have been true."

**Detection:** Train/serve parity tests (this repo already has
`test_train_serve_parity_for_v9_features` as a testable invariant catching
exactly this divergence class), and a backtest that goes suspiciously flat
or reverses sign the moment features are re-verified against a strict PIT
ledger.

**Status here:** Actively mitigated and monitored, but not "solved once" —
every new feature/sport is a fresh opportunity to reintroduce it (per this
repo's own history: weather timing, KBO/NPB home/away labels, MLB starter
selection, soccer team-name collisions, an MLB transaction-date ambiguity).
Any new sport onboarded in this milestone should get an explicit PIT audit
pass before its first promotion attempt, not just before launch.

### Pitfall 2: Confusing calibration with market-beating edge

**What goes wrong:** A model is well-calibrated (its stated 60% actually
wins ~60% of the time) and the team concludes it therefore has a tradeable
edge. It doesn't — calibration is a property of the model in isolation; it
says nothing about whether the model knows anything the market's price
doesn't already reflect.

**Why it happens:** Calibration is the easy, comfortable thing to measure
(reliability diagrams, Brier score against 0.5 or against outcomes) and
historically was over-weighted relative to the market-relative comparison
that actually matters for profitability. An uncalibrated model can also
produce dangerously misleading "value" — a raw overconfident score (e.g.
80% when the true rate is 52%) creates enormous fake edge that a proper
calibration pass would shrink or eliminate.

**Consequences:** Positive-looking backtests and reasonable Brier scores
that translate to a decisively money-losing live system when actually
priced against the market — this is exactly what happened with WNBA
totals in this repo (reasonable-looking model, market beat it decisively
once evaluated on real executable market lines).

**Prevention:** Already structurally fixed here via Phase 23 Gate 1
(ΔLogLoss/ΔBrier vs. no-vig market probability, not vs. 0.5) and Gate 4
(the full economic battery is the *primary* gate — "a model predicts
winners but loses money" is explicitly called out in `ROADMAP.md` as
non-promotable). The general industry framing: CLV is the standard bridge
metric between calibration and real profitability — being better
calibrated than the market is the *precondition* for generating CLV, but
CLV (beating the market's own closing price) is what confirms the edge
actually exists and is exploitable, not just theoretically well-scored.

**Detection:** Any promotion candidate whose ΔLogLoss/ΔBrier vs. market is
~0 or negative despite "good" standalone calibration should be treated as
a hard no, regardless of how clean its reliability diagram looks — this is
already the gate design, but worth stating explicitly as a review
heuristic for humans reading dashboards, since a standalone Brier/calibration
tile can still *look* reassuring in isolation.

### Pitfall 3: Overfitting on sparse, per-sport custom models

**What goes wrong:** Per-sport, per-market custom models (e.g. MLB NRFI,
WNBA totals, low-volume international leagues) are tuned against a
historical sample small enough that brute-force feature/parameter search
finds structure that is actually noise. The system looks excellent on the
exact historical window it was tuned on and fails to reproduce out of
sample.

**Why it happens:** Rare-event outcomes (upsets, NRFI/YRFI, specific prop
resolutions) combined with a limited historical window per sport mean
events-per-predictor ratios can fall well below safe thresholds (general
statistical guidance: ≥10 events per candidate predictor to limit
overfitting/optimism; below ~50 events, standard MLE-style estimation can
hit separation and needs penalized methods like Firth's correction).
Adding features opportunistically until backtest metrics improve, without
holding out a frozen final check, is the single most common mechanism.

**Consequences:** A candidate model reproduces its own claimed numbers
only under lucky conditions (this repo has hit this concretely: WNBA
total's own artifact failed its own locked holdout, and separately was
"not reproducible from current code" — 9 features present vs. 11 the
artifact was trained on — an overfitting/reproducibility failure caught
before it did damage, but exactly the failure mode to keep guarding
against for every new per-sport model).

**Prevention:** This repo already has the core structural defenses:
60/20/20 chronological split with thresholds selected on validation only
and a locked final holdout, the reproduction gate (re-run the incumbent's
exact configuration and demand it reproduces its own shipped numbers
before trusting any new comparison), and `production_feature_ablation.py`
/ `roadmap_challenger.py` as shared tooling rather than hand-rolled
backtests. What's worth reinforcing for this milestone: apply
penalized/regularized estimation (not raw MLE) for any genuinely rare-event
target with <50 positive events in the training window, and treat "how
many *independent contracts*, not rows" as the real sample-size unit (see
Pitfall 5 below — this repo already learned this lesson once).

**Detection:** A candidate whose backtest edge is concentrated in a small
number of dates/games/seasons rather than distributed broadly across the
sample; a feature set that changes between what an artifact claims and
what current code actually computes (train/serve or artifact/code drift);
CI bounds on the reported gain that straddle zero once bootstrapped by
date-cluster rather than by row.

### Pitfall 4: Vig/fee erosion consuming theoretical edge

**What goes wrong:** A model's edge is computed against a flat 50%/"fair"
baseline or against decimal mid-price without accounting for the real
transaction cost (vig on traditional books; spread/slippage on Polymarket),
so a "positive EV" signal is actually break-even or negative once real
execution costs are included.

**Why it happens:** Vig raises the win-rate bar needed to break even well
above 50% (52.38% at standard -110-equivalent pricing) — any edge
calculation not benchmarked against the no-vig/fair line materially
overstates real profitability. This compounds sharply in parlay-style or
multi-leg structures (not the typical shape here, but relevant if any
future correlated-market strategy is considered).

**Consequences:** A model that is "right" more often than the market's
naive 50/50 split can still lose money once the real cost of taking a
position is included.

**Prevention:** Already addressed structurally — Phase 23 Gate 1 compares
against the no-vig market probability specifically to avoid this trap, and
Gate 4's economic battery evaluates ROI/profit-factor against real
"timestamped executable prices," which is the correct treatment on a
CLOB venue like Polymarket where "vig" manifests as spread/depth rather
than a bookmaker-style hold. Polymarket itself charges no explicit trading
fee, so the dominant real-world cost here is not vig in the traditional
sportsbook sense but market impact/slippage (Pitfall 6) — worth being
explicit in any dashboard-facing profitability report that "edge vs. no-vig
price" and "edge after realistic fill price" are two different, both
necessary, numbers.

**Detection:** Any reported ROI or edge figure that doesn't specify
whether it was computed against mid-price, best-bid/ask, or an actually
achievable fill price should be treated as provisional, not gate-grade.

### Pitfall 5: Sample-size pitfalls on lower-volume sports/markets

**What goes wrong:** A promotion or evaluation batch that looks
statistically solid by row-count is actually built on far fewer
independent trials than it appears, because multiple ledger rows trace
back to the same underlying contract (re-forecasts, multiple books/prices
per event, re-settled rows), or because a low-volume sport/market simply
doesn't generate enough live events per unit time to reach a trustworthy
sample within a reasonable evaluation window.

**Why it happens:** This repo has already hit this concretely: the WNBA
spread ledger had 51 settled rows but only 14 distinct contracts —
"minimum-sample gates must count contracts, not rows" was the explicit
lesson recorded in `docs/PROJECT_STATUS.md` (2026-08-18). More generally,
CLV and ROI are only trustworthy metrics at fairly large samples (industry
convention cites 300+ bets for CLV, and notes that even a genuine ~3% edge
still carries a meaningful chance of a net-negative outcome after only
~100 bets from variance alone) — a threshold many of this project's
lower-volume sports (esports, KBO/NPB, international soccer, low-liquidity
Polymarket niches) will take a long time to clear organically.

**Consequences:** A promotion decision made on an apparently-adequate row
count that is really a thin, high-variance sample of true independent
trials; false confidence in either direction (premature promotion of
noise, or premature rejection of a real but not-yet-provable edge).

**Prevention:** Explicitly define and enforce the "unit of sample" as
independent settled contracts (or independent decision events), not rows,
for every gate — this repo already applies that lesson to WNBA spread;
worth confirming it's applied uniformly across every sport's Gate 3
prospective-sample check, especially for any newly onboarded low-volume
sport in this milestone. For genuinely thin sports, prefer wider
confidence intervals and a longer minimum observation window over lowering
the bet-count bar to reach a decision faster.

**Detection:** Any gate check that counts ledger rows without first
deduplicating to distinct contract/event identity; a reported N that looks
adequate for CLV/ROI significance but corresponds to far fewer actual
independent games once cross-referenced against the event calendar for
that sport.

### Pitfall 6: Market impact / liquidity limits specific to Polymarket

**What goes wrong:** A backtest or theoretical-edge calculation assumes
execution at the observed mid-price or best quote, but the real fill price
on a live order is materially worse because the position size consumes
multiple levels of a thin order book — especially likely on lower-tier
sports, international leagues, or niche prop-style contracts rather than
flagship markets (major MLB/NBA moneylines, elections).

**Why it happens:** Polymarket's CLOB shows visible depth per price level;
large/flagship markets stay tight, but niche markets can have very shallow
top-of-book depth, so even a modest order can move the observed price
several points before being fully filled. This project already captures
executable BBOs (Polymarket US snapshot JSONL) across mlb/wnba/esports/
kbo/npb/soccer/tennis, which is the right foundation, but the presence of
a captured quote does not guarantee it was actually fillable at size at
that instant.

**Consequences:** Reported ROI/CLV computed against a captured
quote/mid-price overstates real live profitability for any position sized
close to or beyond available depth, particularly on the lower-liquidity
sports/markets this system trades in research/shadow mode today and might
promote to real execution later.

**Prevention:** Size any real position against visible top-of-book depth
(a common practitioner heuristic caps position size around ~1/3 of
displayed depth at the target price), and separately track "theoretical
edge at quoted price" vs. "realized edge at actual fill price" in the
economic gate's reporting so slippage on thin markets doesn't get silently
absorbed into a single blended ROI number. Given real-money execution
exists behind a separately gated dashboard surface here, any newly
promoted market should have its typical book depth profiled before sizing
rules are set, not assumed from the flagship-market experience.

**Detection:** A gap between quoted-price ROI and realized-price ROI that
grows specifically on the system's lower-volume sports; live order fills
whose average price is consistently worse than the pre-order best quote.

### Pitfall 7: Model decay / regime change

**What goes wrong:** A model that was genuinely calibrated and edge-positive
at promotion time degrades in live performance months later because the
underlying data-generating process shifted (rule changes, roster
composition shifts, league-wide scoring-environment changes, market
efficiency itself improving as other participants adapt) — without any
code bug, just a stale model applied to a changed world.

**Why it happens:** Splits into data drift (input feature distributions
shift — e.g., seasonality, personnel changes) and concept drift (the
relationship between features and outcome itself shifts — e.g., league-wide
scoring-environment changes). Both are especially likely across a
multi-season, multi-sport horizon and are easy to miss because nothing
"breaks" in the software sense.

**Consequences:** A promoted model quietly stops earning its Gate 4
economic numbers without any alert, because promotion gates are typically
evaluated once at promotion time, not continuously against live serving
performance.

**Prevention:** Detection methods in general use include distributional
comparison tests (e.g. Kolmogorov-Smirnov, Population Stability Index)
between the training window and recent live inputs/outcomes. The
important design principle from the wider industry (and directly relevant
here) is to keep drift *detection* separate from automatic production
changes — a monitor should surface "the statistical environment may have
changed" without silently re-training or re-promoting anything, so
degradation is caught by monitoring rather than discovered only via
realized P&L months later. This repo's rolling walk-forward validation
tooling (`validation.rolling_walk_forward_splits`) and its existing
CLV/health API surface (`/api/clv`, `/api/capture_health`) are a starting
point, but there is no evidence in the reviewed docs of an explicit,
automated drift-detection layer comparing live serving performance against
the frozen promotion-time baseline on a rolling basis.

**Status here — open gap:** This appears to be a genuinely open item for
this milestone: a scheduled, automated comparison of any promoted model's
rolling live Gate 1/Gate 4 metrics against its promotion-time frozen
baseline, with an alert (not an auto-rollback) on material divergence,
would close this gap. Worth flagging explicitly for roadmap consideration
given the system now runs 4x/day and has multiple promoted (MLB
moneyline, WNBA moneyline) real-money paths.

## Moderate Pitfalls

### Pitfall 1: Fail-closed promotion gates creating false confidence via criteria gaps

**What goes wrong:** A gate designed to fail closed (block promotion on
any ambiguity) can still create false confidence if the specific criteria
it checks don't actually distinguish "calibrated" from "profitable," or if
a stability slice that matters for a given sport isn't one of the slices
being checked.

**Cross-reference to this repo's Phase 23 gate:** This project's gate
structure already directly addresses the calibration-vs-profitable
conflation at the architecture level — Gate 1 is explicitly market-relative
(not vs. 0.5) and Gate 4 (economic) is the *primary* gate, with hit-rate
explicitly demoted to diagnostic-only. That is the correct fix for the
most common version of this failure mode. Two narrower residual risks are
still worth naming:

1. **Slice coverage gaps.** Gate 4's stability slices are listed as
   month-to-month, favorite/underdog, home/away, high/low — a reasonable
   general-purpose set, but for a specific new sport (e.g. tennis surface
   type, soccer competition tier, esports patch version) the slice that
   would reveal instability might not be one of the generic four. A
   fail-closed gate is only as good as the slices it actually checks;
   passing all four generic slices is not proof of uniform stability
   across every dimension that matters for a given sport.
2. **CLV rate threshold vs. CLV magnitude.** The gate's stated CLV
   criterion is a *rate* (≥50% of bets beat the closing no-vig line).
   Industry practice more commonly emphasizes average *CLV magnitude*
   (e.g. 1-3% average edge is called "excellent") as the more predictive
   figure, since a bettor can beat the close 50%+ of the time by a hair
   while losing badly on the other side. Worth confirming the economic
   battery also reports magnitude-weighted CLV, not just a pass-rate,
   before treating "CLV rate ≥ 50%" alone as sufficient evidence of edge.

**Prevention:** Periodically red-team the gate's own slice list against
each newly onboarded sport's known volatility axes, and report CLV
magnitude alongside CLV rate.

### Pitfall 2: Small-sample statistical estimation without penalization

**What goes wrong:** Standard maximum-likelihood-style estimation (logistic
regression and similar) is known to produce biased or non-finite parameter
estimates under small samples or full/quasi-separation, which is exactly
the regime rare-event markets (NRFI, upsets) sit in.

**Prevention:** Use penalized-likelihood methods (e.g. Firth's correction)
below roughly 50 positive events, and treat any reported coefficient from
an unpenalized fit on a sparse target with elevated suspicion until
cross-validated.

### Pitfall 3: Validation-method mismatch for flexible/non-parametric models

**What goes wrong:** Bootstrap optimism-correction, valid for classic
parametric models in small samples, has been shown in the wider literature
to not reliably validate rare-event models built with flexible learners
(e.g. random forests/ensembles) — cross-validation on all available data
is the more defensible approach there.

**Prevention:** Match the validation method to the model class, not just
apply one house-standard validation recipe (e.g. date-clustered bootstrap)
uniformly regardless of whether the underlying model is a simple
calibrated linear/logistic form or a more flexible learner.

## Minor Pitfalls

### Pitfall 1: In-play/live decay treated identically to pre-game decay

**What goes wrong:** A live/in-play signal's importance should decay
relative to real-time market information as an event progresses (both a
smooth time-based decay and a sharp event-based decay on high-impact
moments); treating a pre-game model's output as equally trustworthy deep
into a live event is a subtler version of the concept-drift problem,
scoped to within a single event rather than across a season. Only relevant
if/when this system extends into in-play markets beyond what it captures
today.

### Pitfall 2: Reporting realized ROI without also reporting the bootstrap CI that was computed

**What goes wrong:** This repo's own history has already surfaced a
version of this: a totals-model verdict used the point estimate while
storing (but not surfacing) a bootstrap CI it effectively ignored, and a
downstream consumer (NFL reporting) read `improved_vs_baseline=True` off
the point estimate alone. Any dashboard or report layer built or extended
in this milestone should always render the CI alongside the point
estimate, not just the point estimate.

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Onboarding any new sport/market to research/shadow status | PIT leakage (Pitfall 1) | Explicit PIT audit before first promotion attempt; verify train/serve parity test exists for every new feature |
| Any per-sport rare-event model (NRFI-style, upset props) | Overfitting on sparse data (Pitfall 3); small-sample estimation bias (Moderate 2) | Penalized estimation below ~50 events; ≥10 events per predictor; reproduction gate before trusting any ablation |
| Promotion evaluation for any sport/market | Confusing calibration with edge (Pitfall 2) | Already gated (Phase 23 Gate 1 market-relative + Gate 4 economic-primary) — keep hit-rate diagnostic-only, don't let it creep back into decisioning |
| Low-volume sports (esports, KBO/NPB, int'l soccer, niche Polymarket markets) | Sample-size pitfalls (Pitfall 5) | Count distinct contracts not rows in every Gate 3 check; wider CIs, longer observation windows instead of lowering bet-count bar |
| Real-money order execution sizing, especially thin markets | Market impact/slippage (Pitfall 6) | Size against visible top-of-book depth; report quoted-price ROI vs. realized-fill-price ROI separately |
| Any promoted model, ongoing (post-promotion) | Model decay / regime change (Pitfall 7) — currently an open gap | Build a scheduled rolling comparison of live Gate 1/Gate 4 metrics vs. promotion-time frozen baseline, alert-only (not auto-rollback) |
| Any new stability-slice design for a new sport | Fail-closed gate with incomplete slice coverage (Moderate 1) | Explicitly enumerate the sport's own volatility axes (surface, tier, patch version, etc.), don't rely solely on the generic four-slice set |
| Any dashboard/report surfacing a model verdict | CI ignored in favor of point estimate (Minor 2) | Always render bootstrap CI alongside the point estimate in reports |

## Sources

- Repo-grounded (HIGH confidence): `docs/ROADMAP.md` (Phase 23 — Formal
  Promotion Gate; Phase F — Market-Relative Edge & Information Discovery
  Roadmap), `docs/PROJECT_STATUS.md` (2026-08-18 WNBA settlement/promotion
  entry), `CLAUDE.md` (point-in-time correctness invariant), `docs/AGENTS.md`.
- Web (MEDIUM confidence, cross-verified across multiple independent
  results per query, via this project's `classify-confidence --verified`
  seam):
  - [Overfitting Explained: Why Backtests Fail in Live Trading](https://medium.com/@SentientTradingSociety/this-is-why-your-backtested-strategies-dont-work-live-fa733edfefad)
  - [How to Backtest a Sports Betting Strategy Without Overfitting](https://www.greatbets.co.uk/how-to-backtest-a-sports-betting-strategy-without-overfitting/)
  - [The Overfitting Problem: Why Backtested Betting Systems Fail in Production](https://www.betting-forum.com/threads/the-overfitting-problem-why-backtested-betting-systems-fail-in-production.47444/)
  - [Data Leakage, Lookahead Bias, and Causality in Time Series Analytics](https://medium.com/@kyle-t-jones/data-leakage-lookahead-bias-and-causality-in-time-series-analytics-76e271ba2f6b)
  - [A leakage-aware workflow for pre-match forecasting of secondary football markets (LaLiga case study)](https://www.sciencedirect.com/science/article/pii/S2590005626003620)
  - [How Betting Models Are Validated — Holdout, Grade, EV, CLV | ModelPlay](https://modelplay.ai/learn)
  - [Closing Line Value (CLV) Guide: Validate AI Betting Models](https://www.sports-ai.dev/blog/closing-line-value-and-ai-model-performance)
  - [What is CLV Betting? How to Beat the Lines & Get More Value](https://www.sharpfootballanalysis.com/sportsbook/clv-betting/)
  - [Vigorish (Wikipedia)](https://en.wikipedia.org/wiki/Vigorish)
  - [What Is a Vig? A Must-Know Concept to Beat the Books! — Outlier](https://outlier.bet/sports-betting-strategy/positive-ev-betting/what-is-a-vig-a-must-know-concept-to-beat-the-books/)
  - [How Liquidity Affects Polymarket Prices (And Why It Matters)](https://medium.com/@predictionmarkets/how-liquidity-affects-polymarket-prices-and-why-it-matters-d3668d1d508f)
  - [The Anatomy of a Decentralized Prediction Market: Microstructure Evidence from the Polymarket Order Book](https://arxiv.org/html/2604.24366v1)
  - [Polymarket 2026: Institutional Liquidity & Execution Benchmarks](https://ats.io/prediction-markets/polymarket/liquidity/)
  - [On estimation for accelerated failure time models with small or rare event survival data](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9188212/)
  - [Empirical evaluation of internal validation methods for prediction in large-scale clinical data with rare-event outcomes](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9890785/)
  - [How to Build a Sports Betting Model (Beginner Guide)](https://www.underdogchance.com/how-to-build-a-sports-betting-model/)
  - [Machine Learning Monitoring, Part 5: Why You Should Care About Data and Concept Drift](https://www.evidentlyai.com/blog/machine-learning-monitoring-data-and-concept-drift)
  - [P1 — Football Model & Market Drift Monitoring (QuantBet issue tracker)](https://github.com/Filip1994/h2h/issues/87)
  - [Concept Drift and Model Decay in Machine Learning | Towards Data Science](https://towardsdatascience.com/concept-drift-and-model-decay-in-machine-learning-a98a809ea8d4/)
  - [Live Betting Strategy: Master In-Play Decay Models](https://thewagertheorem.com/live-betting-decay-models/)
