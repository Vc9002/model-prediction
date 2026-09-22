# Technology Stack

**Project:** model-prediction — accuracy/profitability measurement & tracking layer
**Researched:** 2026-09-20
**Scope:** What professional quant sports-betting / prediction-market systems use to *measure and track* accuracy and profitability (not generic MLOps). Evaluated for direct fit against the existing Python 3.11 stack (scikit-learn logistic regression + Elo models, SQLite ledger, DuckDB feature store, Polymarket execution).

## Recommended Stack

### Core Metrics (accuracy/calibration)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `sklearn.metrics.brier_score_loss` | scikit-learn 1.5+ (already a dependency) | Brier score per prediction/window | Already in the stack; strictly proper scoring rule; standard baseline metric across the sports-forecasting literature |
| `sklearn.metrics.log_loss` | scikit-learn 1.5+ | Log loss per prediction/window | Preferred over Brier in academic competitive-outcome-prediction work (e.g. Bradley-Terry-Élo papers) because it doesn't suffer Brier's re-labelling variance; run both, not one |
| `sklearn.calibration.calibration_curve` | scikit-learn 1.5+ | Reliability diagrams (predicted-probability bins vs. observed frequency) | Brier/log-loss alone can hide miscalibration — a model can have a good Brier score while being systematically over/under-confident in a probability band. Calibration curves are the standard diagnostic paired with Brier/log-loss in the literature |
| `sklearn.calibration.CalibratedClassifierCV` | scikit-learn 1.5+ | Platt scaling / isotonic regression to fix a calibration drift once detected | Standard fix once calibration_curve flags a problem; isotonic can overfit on small per-sport samples (esports/KBO/NPB), Platt (sigmoid) is safer at low N |

**Verdict:** no new dependency needed here — scikit-learn already ships everything required. The gap in this codebase is not *tooling* but *systematic time-series tracking* of these three numbers per model/sport/window (rolling 30/90/365-day Brier, log loss, and a calibration-curve snapshot), stored alongside existing ledger rows rather than computed ad hoc.

### Closing Line Value (CLV) — the standard quant benchmark this codebase is missing

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| No-vig / power-method devig on Polymarket BBO snapshot at decision time and at market close | Custom (pure Python, no new dependency) | Convert the model's edge claim into implied-probability terms | The professional consensus (sharp bettor / quant literature) treats **CLV, not win rate or raw P&L, as the primary short-run skill signal** — it stabilizes with far fewer samples than ROI (CLV: high confidence at ~100-200 bets; ROI: needs thousands). This directly matches the "MEASURE TRACK accuracy profitability rigorously" ask |
| `data/odds/` BBO snapshots (already captured) | existing | Source of both entry price and closing price | Already logging Polymarket BBO snapshots per `docs/PROJECT_STATUS.md`/`STACK.md` — the raw data for CLV already exists; it is not being turned into a CLV% metric today |

**Recommended CLV pipeline (build, don't buy):**
1. At decision time, snapshot the market-implied probability (devigged) alongside the model probability — this is the "your line" side.
2. At market close/game-start, snapshot the same market's final devigged probability — this is the "closing line" side.
3. `CLV = model_side_implied_prob_at_close_vs_entry` — report **CLV% (fraction of picks with positive CLV)** and **average CLV magnitude**, per sport/model, rolling windows, exactly like Brier/log loss.
4. Track this as a first-class ledger-adjacent metric, not a one-off notebook computation — it is cheap to compute from data already captured and is the single most quant-standard signal this system currently does not report.

**Devigging method choice:** use the **power method** as default (closed-form, corrects for favorite-longshot bias, no new dependency — a ~10-line implementation), not simple proportional/multiplicative devigging, when comparing model probability to market-implied probability. Reserve the more complex Shin method (iterative, no closed form) for 3-way soccer/politics markets where the model already special-cases devigging; for the two-way markets this system mostly trades (MLB/WNBA moneyline), power and multiplicative converge closely, so this is a small, well-bounded addition — not a new dependency.

### Market-Relative Edge Measurement

| Approach | Purpose | Why |
|----------|---------|-----|
| `edge = model_prob - market_implied_prob(devigged)` logged at decision time, joined against actual outcome | Direct model-vs-market comparison, the second pillar (with CLV) of quant self-evaluation | This is the metric the `betting` skill already computes ad hoc for edge detection; the gap is *persisting it to the ledger as a tracked time series* rather than a point-in-time calculation, so edge quality (does a claimed 5% edge actually convert to hit rate ≈ 55% over time) can be audited historically |
| Segment edge tracking by claimed-edge bucket (e.g. 0-2%, 2-5%, 5%+) | Detect whether the model's edge estimates are themselves calibrated | Standard practice in quant sports betting: a model that is well-calibrated in aggregate (good Brier) can still have systematically wrong edge sizing in specific buckets — this is the market-relative analogue of a calibration curve and is what separates "accurate outcome model" from "profitable edge model" |

### Backtesting / Validation

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Existing in-repo `validation.py::build_walk_forward_rows` / `chronological_split` | already built | Chronological 60/20/20 walk-forward split | Already matches the professional standard (walk-forward, never k-fold, never shuffle) documented in this project's own `docs/ARCHITECTURE.md` — **do not introduce an external backtesting framework** (e.g. `sports-betting` (georgedouzas/py) or `Flumine`); they duplicate what's already correctly built and would fragment the point-in-time-correctness contract that is this codebase's most hard-won invariant |
| `production_feature_ablation.py`, `roadmap_challenger.py` (existing) | already built | Feature/model ablation against locked holdout | Already the right tool; the improvement opportunity is reporting Brier/log-loss/CLV deltas (not just accuracy/ROI) from these harnesses as standard output columns |

**Explicitly rejected:** general-purpose backtesting libraries (`backtrader`, `zipline`, `bt`) — built for continuous-price asset trading (bar-by-bar OHLCV, position rebalancing), not discrete binary-outcome event markets. They add dependency weight without solving anything `validation.py` doesn't already solve for this domain. The `sports-betting` (georgedouzas) package and `Flumine` (Betfair/Kalshi/Polymarket-aware simulator) are the closest domain-matched open-source options if this were greenfield, but retrofitting either onto an already-built production ledger/registry (`production_registry.py`, `champion_challenger.py`) would be pure churn for equivalent functionality.

### Bankroll / Kelly Sizing

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Fractional Kelly, hand-implemented (`f* = (b*p - (1-p)) / b`, then scale by 1/4–1/2) | n/a — ~15 lines, no dependency | Position sizing from model edge | Already partially present via the `bankroll-tail` / `betting` skills in this environment; standard quant practice is **never full Kelly** — quarter/half-Kelly cuts variance 75%/50% for a small growth-rate cost, and full Kelly's drawdown profile (50% chance of a 50%+ drawdown at full Kelly) is unacceptable for a system that stakes real money (MLB/WNBA moneyline) |
| `keeks` (wdm0006/keeks) — evaluated, not recommended for adoption | n/a | Off-the-shelf multi-strategy Kelly library (full/fractional/drawdown-scaled) | Small, unmaintained-risk dependency for something already correctly hand-rollable in this codebase; only worth pulling in if the system wants more exotic sizing strategies (drawdown-scaled Kelly) than fractional Kelly — flag as a **future consideration**, not now |

### Infrastructure (tracking storage — extends what's already built)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| SQLite (existing canonical ledger, `data/ledgers/ledgers.db` / `data/production/production.db`) | already built | Store per-pick Brier/log-loss contribution, CLV, and market-relative edge alongside existing decision rows | This is an additive-columns problem, not a new system: add `market_prob_at_decision`, `market_prob_at_close`, `clv`, `brier_contribution` fields to the existing decision schema rather than standing up a parallel metrics store |
| DuckDB (existing feature store) | already built | Rolling-window aggregation (30/90/365-day Brier, log loss, CLV%) over the SQLite ledger for dashboard reporting | Already used for point-in-time feature computation; the same engine is a natural fit for windowed metric aggregation without adding pandas-heavy custom code |

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Backtesting framework | Extend existing `validation.py` walk-forward harness | `sports-betting` (georgedouzas), `Flumine` | Both are legitimate domain-matched open-source projects, but adopting either now means re-deriving the point-in-time-correctness guarantees this codebase already has hand-built and tested (`test_train_serve_parity_for_v9_features`) — high migration risk for no new capability |
| Backtesting framework | (same) | `backtrader` / `zipline` / `bt` | Built for continuous-price bar-based asset trading, not discrete event-outcome markets; wrong abstraction entirely |
| Calibration/scoring | scikit-learn (already a dependency) | Custom Brier/log-loss implementation | No reason to hand-roll strictly-proper scoring rules scikit-learn already ships and this repo already depends on |
| Devig method | Power method (custom, ~10 lines) | Shin method | Shin requires iterative numeric solving for a benefit that mostly shows up in multi-way/soccer markets and heavy-favorite props; not worth the complexity for MLB/WNBA moneyline, which is two-way and close to -110-style |
| Devig method | Power method | Multiplicative/proportional | Multiplicative is simpler but systematically mis-estimates true probability on lopsided (favorite/longshot) lines — power method is the field's accepted middle ground |
| Kelly sizing | Hand-rolled fractional Kelly | `keeks` library | Marginal-value dependency for logic that's ~15 lines and already partially present via installed skills; revisit only if drawdown-scaled/more exotic sizing strategies are wanted later |
| Bet tracking dashboard | Extend existing SQLite ledger + dashboard | Commercial trackers (Pikkit, Betfolyo, BettorEdge) or spreadsheet templates | Those are consumer-facing manual-entry tools for individual bettors; this system already has an automated production ledger and dashboard — building CLV/Brier reporting into it is strictly better than exporting to an external tracker |

## Installation

No new runtime dependencies are required. Everything above is either already in `pyproject.toml` (scikit-learn, DuckDB, SQLite via stdlib) or a small (~10-30 line) addition of pure-Python logic (devig, CLV computation, fractional Kelly) that belongs in a new module, e.g. `src/model_prediction/measurement.py` or similar, following this repo's existing shadow-feature-module pattern (build data-capture step with explicit `observed_at_utc` provenance, fail-closed on missing data, wire into reporting).

```bash
# No new installs required — scikit-learn, DuckDB, SQLite already present.
# If drawdown-scaled Kelly sizing becomes a real requirement later:
# pip install keeks
```

## Sources

- [scikit-learn: brier_score_loss](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html) — HIGH confidence (official library docs)
- [scikit-learn calibration_curve / CalibratedClassifierCV via train in data blog and MachineLearningMastery overview](https://machinelearningmastery.com/how-to-score-probability-predictions-in-python/) — HIGH confidence (documents official scikit-learn API; cross-checked against scikit-learn docs)
- [Modelling Competitive Sports: Bradley-Terry-Élo Models (arXiv)](https://arxiv.org/pdf/1701.08055) — HIGH confidence (peer-reviewed-adjacent academic source), used for Brier-vs-log-loss guidance in competitive-outcome prediction
- [Pikkit: How to Track CLV](https://pikkit.com/blog/how-to-track-closing-line-value-clv-in-sports-betting) — MEDIUM confidence (practitioner/vendor content, consistent with broader sharp-betting consensus), used for CLV methodology and sample-size benchmarks
- [AgentBets.ai: Tracking CLV with an API](https://agentbets.ai/sharp-betting/closing-line-value-api/) — MEDIUM confidence (practitioner blog), used for automated CLV pipeline shape
- [Bet Hero: Devigging Methods Explained (Power, Shin, Additive, Multiplicative)](https://betherosports.com/blog/devigging-methods-explained) — MEDIUM confidence (practitioner blog, cross-checked against multiple independent devig-method explainers converging on the same tradeoffs), used for devig method selection
- [Outlier: How to Devig Odds - Comparing the Methods](https://help.outlier.bet/en/articles/8208129-how-to-devig-odds-comparing-the-methods) — MEDIUM confidence, cross-check source for devig method comparison
- [GitHub: wdm0006/keeks](https://github.com/wdm0006/keeks) — MEDIUM confidence (inspected repo description directly), evaluated Kelly-sizing library
- [GitHub: georgedouzas/sports-betting](https://georgedouzas.github.io/sports-betting/) — MEDIUM confidence, evaluated backtesting framework
- [Flumine (via SportsBetEdge open-source roundup)](https://sportsbetedge.com/automation/open-source-repos/) — LOW-MEDIUM confidence (single secondary source describing Flumine's Kalshi/Polymarket support), noted as domain-relevant but not independently verified against Flumine's own docs
- [Matthew Downey: Why Fractional Kelly? Simulations of bet size with uncertainty](https://matthewdowney.github.io/uncertainty-kelly-criterion-optimal-bet-size.html) — MEDIUM confidence (independent technical blog with simulation methodology shown), used for fractional-Kelly drawdown rationale
- Existing project docs (`.planning/codebase/STACK.md`, `docs/ARCHITECTURE.md`, `CLAUDE.md`) — HIGH confidence (first-party, current repo state as of 2026-09-20), used to determine what's already built vs. genuinely missing
