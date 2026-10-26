# College Football (NCAAF) Prediction System

**Status (updated 2026-10-26)**: Wired to production. Champion is **`cfb-structural-v3`** for all three markets (moneyline, spread, total) — `serving_status: production` in `config/production.yaml`. `cfb-structural-v2` is the rollback.  
**Earlier documents named `cfb-total-v1` as totals champion.** That claim rests on the 2020–2024 backtest in §5, which was scored against constant lines, not market prices, and so is invalid (see §5 and §7).  
**Older models**: `college-football-v1` (moneyline) and `cfb-spread-v1` (spread) remain in the ledgers as history. `cfb-spread-v1` is losing on the Main ledger.  
**Historical Dataset**: 8,146 completed games spanning 2016-2024 seasons (Structural 2016+, Market-relative 2020+)  
**Primary Module Entrypoints**:
- Data & Venue Geography: [`src/model_prediction/data_sources/cfb_data.py`](src/model_prediction/data_sources/cfb_data.py)
- PIT Feature Extraction: [`src/model_prediction/features/cfb_features.py`](src/model_prediction/features/cfb_features.py)
- Joint Scoring Distribution: [`src/model_prediction/models/cfb_distribution.py`](src/model_prediction/models/cfb_distribution.py)
- Unified CFB Model: [`src/model_prediction/models/college_football.py`](src/model_prediction/models/college_football.py)
- Slate & Forecast Wiring: [`src/model_prediction/cli/forecast.py`](src/model_prediction/cli/forecast.py)
- Unit & Integration Tests: [`tests/test_college_football.py`](tests/test_college_football.py)
- Research & Validation Battery: [`scripts/cfb_research_pipeline.py`](scripts/cfb_research_pipeline.py)

---

## 1. System Architecture & Governing Invariants

The College Football prediction system implements a joint game-level scoring framework where Moneyline, Spread, and Total markets are derived coherently from expected possessions and scoring efficiency per possession:

- mu_away = Possessions * PPP_away
- mu_home = Possessions * PPP_home

### Permanent Sign Conventions
- **Margin**: Margin = HomePoints - AwayPoints.
- **Home Spread**: If home spread line is -7.5, MarketImpliedHomeMargin = +7.5.
- **Actual Total**: ActualTotal = HomePoints + AwayPoints.
- **Spread Residual**: R_spread = ActualMargin - MarketImpliedHomeMargin.
- **Total Residual**: R_total = ActualTotal - MarketTotal.

### Dual-Ledger Invariants
1. **Flat Ledger (`data/flat/ncaaf.xlsx` & SQLite mirror)**: Unconditionally receives **100% of all eligible CFB predictions** across Moneyline, Spread, and Total without applying pick-edge or unit gating.
2. **Main Ledger (`data/main/ncaaf.xlsx`)**: Gated strictly by market qualification:
   - Gating rule (applies to any champion): Main ledger writes need Edge >= 3.5%, Uncertainty <= 0.18, non-FCS, and sizing criteria passing. Which model is the champion per market is an open decision (see §7 and `docs/handoffs/HANDOFF.md`, item 3).

---

## 2. Feature Extraction Engine & Mechanistic Channels

### Point-in-Time Opponent Adjustments
- Computed strictly using historical games with kickoff timestamp t_game < t_decision.
- Exponentially weighted moving average (EWMA) and regularized Ridge regression over offensive and defensive efficiency.

### Granular Preseason Priors & Decay
- Conference tier baseline offsets: SEC (+8.5), Big Ten (+7.5), Big 12 (+3.5), ACC (+3.0), G6 (+0.0 to -7.0), FCS (-18.0).
- Exponential weekly decay schedule:
  TeamState_week = w_week * Prior + (1 - w_week) * CurrentSeason
  (w_0 = 1.00, w_1 = 0.90, w_2 = 0.78, ..., w_14 = 0.01).

### Quarterback Starter Model & Probabilistic Mixtures
- Models starting QB experience and starter availability probability P(QB = s):
  P(Y) = sum_s P(QB = s) * P(Y | QB = s)
- Missing starter applies a -4.5 point efficiency discount and expands epistemic uncertainty.

### Multi-Channel Home Advantage & Travel Geography
- Replaces flat constants with decomposed offensive boost (+1.8 pts) and defensive suppression (-1.0 pts).
- Neutral site games set HFA strictly to 0.0.
- Great-circle Haversine travel distance and timezone disparity.
- Stadium elevation fatigue penalty for visiting teams at venues > 4,000 ft (e.g. Wyoming War Memorial 7,220 ft, Air Force 6,621 ft, Colorado 5,360 ft, Utah 4,657 ft).
- Rest disparity and +1.5 point bye-week advantage.

### Conditional Weather Physics
- **Wind Speed**: Suppresses explosive pass plays and field goal accuracy (Delta Total = -0.28 * (Wind - 14.0) for Wind > 14 mph).
- **Precipitation**: -1.8 point suppression for rain/snow > 0.05 in.
- **Indoor / Dome Overrides**: Domed stadiums (Syracuse, Allegiant, Alamodome, Mercedes-Benz, Caesars Superdome, etc.) strictly zero out all outdoor weather adjustments.

---

## 3. Joint Scoring Distribution Benchmark

Evaluated on the locked 2023-2024 out-of-sample holdout (N = 1,796 games):

| Distribution Engine | ML Brier | ML LogLoss | Spread Brier | Total Brier | Margin MAE | Total MAE |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Negative Binomial (Overdispersed)** | **0.11581** | **0.37128** | 0.37858 | 0.21836 | **18.39** | **11.45** |
| Bivariate Normal Residuals | 0.12679 | 0.40746 | 0.33560 | 0.20443 | 18.39 | 11.45 |
| Empirical Residual Simulation | 0.13649 | 0.43808 | 0.31673 | 0.20388 | 18.39 | 11.45 |
| Possession-Level Monte Carlo | 0.13476 | 0.43212 | 0.31688 | **0.18095** | 18.39 | 11.45 |

Negative Binomial discrete scoring achieved the highest discrimination and lowest Brier score / LogLoss on collegiate football margin and moneyline distributions.

---

## 4. Structural Feature Ablation Battery

Evaluated out-of-sample on locked holdout seasons:

| Step / Feature Family | ML Brier | ML LogLoss | Margin MAE | Total MAE |
| :--- | :---: | :---: | :---: | :---: |
| BASE (Raw Points/Game) | 0.17820 | 0.52450 | 13.85 | 12.90 |
| + Opponent Adjustment (Ridge/Iterative) | 0.16120 | 0.48120 | 12.40 | 12.10 |
| + Preseason Priors & Dynamic Decay | 0.15480 | 0.46350 | 11.85 | 11.75 |
| + Returning Production & Transfers | 0.15110 | 0.45280 | 11.50 | 11.55 |
| + QB Model & Starter Mixture | 0.14780 | 0.44310 | 11.20 | 11.40 |
| + Pace & Possession Engine | 0.14590 | 0.43850 | 11.05 | 11.10 |
| + Multi-Channel HFA, Travel & Altitude | 0.14320 | 0.43120 | 10.82 | 10.95 |
| **+ Conditional Weather Mechanisms (Final)** | **0.14180** | **0.42780** | **10.74** | **10.82** |

---

## 5. Market-Relative Economic Evaluation (2020-2024 Market Data)

**Correction (2026-10-22): the table below is not a market evaluation. Do not cite it.** An earlier version of this section said it used "executable decision-time consensus market odds". That was wrong. `data/historical/ncaaf_games_all.jsonl` has no market data: none of its 4,781 games from 2020 on carries `total_line`, `spread_home_line` or `market_ml_home_prob`. `scripts/cfb_research_pipeline.py` fell back to constants for every game: totals scored against a 54.0 line, spreads against a pick'em line (0.0), and moneyline against a 50% probability, all at a flat -110 price (0.5238 implied).

The table's figures are older still: `docs/SYSTEM_DEFECTS_AND_GAPS_AUDIT.md` (A-1, A-2) records that they came from a seeded random-number simulator, not real games, whose market line was drawn from the model's own target, so the edge was circular. That cohort was later replaced with real ESPN games, but this table was never regenerated. The original numbers are unreproducible, and rerunning the pipeline on the current file would score against the constants above (not re-run here).

A model with any game-level signal beats a constant line (a naive over/under bet against 54.0 wins about 50%), because a real market line already contains that signal. So these hit rates and ROIs measure the model against a constant, not an edge over the market. The N = 4,510 below also does not match the 4,781 games in the 2020+ cohort of the file, and the difference is unexplained. A valid evaluation needs historical NCAAF closing or decision-time lines, which are not in the repository. Until then, only the live results in §7 count.

Figures as originally produced (constant-line scoring, not market-relative):

| Market | Eligible | Gated Bets | Hit Rate | P&L Units | Point ROI | 95% Date-Clustered CI | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Game Total (`cfb-total-v1`) | 4,510 | 3,911 | 66.97% | +1,140.91U | +29.17% | [+25.31%, +32.98%] | Invalid: constant 54.0 line |
| Moneyline (`college-football-v1`) | 4,510 | 2,802 | 34.90% | -868.87U | -31.01% | [-35.59%, -26.29%] | Invalid: constant 50% price |
| Spread (`cfb-spread-v1`) | 4,510 | 3,632 | 32.52% | -1,337.36U | -36.82% | [-40.00%, -33.47%] | Invalid: constant 0.0 line |

These are not live results and not market results. See §7.

---

## 7. Live Ledger Results (checked 2026-10-21)

Source: `E:\model-prediction-runtime\ledgers\ledgers.db`, settled NCAAF rows, Main ledger. Rows are counted once per (event, market, selection, line) because re-forecasts wrote duplicate settled rows before commit `b77a563d` (2026-10-21). Units use -110 pricing, so breakeven is 52.4%.

| Model | Market | Distinct picks | Hit rate | Units | ROI |
| :--- | :--- | ---: | ---: | ---: | ---: |
| `cfb-structural-v2` | Moneyline | 19 | 63% | +1.7 | +9% |
| `cfb-structural-v2` | Spread | 46 | 63% | +12.8 | +28% |
| `cfb-structural-v2` | Total | 51 | 49% | -5.0 | -10% |
| `cfb-structural-v2` | All | 116 | 57% | +9.5 | +8% |
| `cfb-spread-v1` | Spread | 290 | 50% | -32.1 | -11% |
| `cfb-total-v1` | Total | 293 | 52% | -3.3 | -1% |

What this means:

- **`cfb-total-v1` has no valid backtest.** The §5 figure of +29.17% was scored against a constant 54.0 total line, not market prices (see the correction in §5). Live ROI is about -1% over 293 picks and is the only valid evidence.
- **`cfb-structural-v2` is positive but too small to conclude anything.** At 116 picks, one standard error on the hit rate is about 4.6 points. Its total market is negative.
- **Duplicates.** 18 keys in `cfb-structural-v2` Main carry duplicate settled rows: 148 rows for 116 distinct picks. No duplicates were written after `b77a563d`. The old rows remain in the store, so deduplicate them before reporting.

---

## 8. Production Model Registry & Artifact Hashes

| Model ID | Market | Schema | Artifact Path | SHA-256 Hash | Status |
| :--- | :--- | :---: | :--- | :--- | :--- |
| `cfb-structural-v2` | Moneyline, Spread, Total | 2 | `config/models/cfb-structural-v2.json` | `c70c3abe9e1d...` | Serving per `config/production.yaml` |
| `college-football-v1` | Moneyline | 2 | `config/models/college-football-v1.json` | `f5b95acd6676fe7a...` | `FLAT_LEDGER_ONLY` |
| `cfb-spread-v1` | Spread | 2 | `config/models/cfb-spread-v1.json` | `7dc5b3428abfdb93...` | `FLAT_LEDGER_ONLY` |
| `cfb-total-v1` | Total | 2 | `config/models/cfb-total-v1.json` | `6831c96cc379860b...` | Champion status contested (see §7) |
