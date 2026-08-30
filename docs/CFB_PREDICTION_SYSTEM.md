# College Football (NCAAF) Prediction System

**Status**: Production-Wired, Validated & Calibrated  
**Active Production Champion (Totals)**: `cfb-total-v1` (Qualified, Main + Flat Ledger)  
**Shadow Baseline Models**: `college-football-v1` (Moneyline, Flat Ledger Only), `cfb-spread-v1` (Spread, Flat Ledger Only)  
**Historical Dataset**: 8,146 completed games spanning 2016-2024 seasons (Structural 2016+, Market-relative 2020+)  
**Primary Module Entrypoints**:
- Data & Venue Geography: [`src/model_prediction/data_sources/cfb_data.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/data_sources/cfb_data.py)
- PIT Feature Extraction: [`src/model_prediction/features/cfb_features.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/features/cfb_features.py)
- Joint Scoring Distribution: [`src/model_prediction/models/cfb_distribution.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/models/cfb_distribution.py)
- Unified CFB Model: [`src/model_prediction/models/college_football.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/models/college_football.py)
- Slate & Forecast Wiring: [`src/model_prediction/cli/forecast.py`](file:///Users/vincentc9002/model-prediction/src/model_prediction/cli/forecast.py)
- Unit & Integration Tests: [`tests/test_college_football.py`](file:///Users/vincentc9002/model-prediction/tests/test_college_football.py)
- Research & Validation Battery: [`scripts/cfb_research_pipeline.py`](file:///Users/vincentc9002/model-prediction/scripts/cfb_research_pipeline.py)

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
   - **Total (`cfb-total-v1`)**: **`QUALIFIED`** - written to Main ledger when Edge >= 3.5%, Uncertainty <= 0.18, non-FCS, and sizing criteria pass.
   - **Moneyline (`college-football-v1`) & Spread (`cfb-spread-v1`)**: **`FLAT_LEDGER_ONLY`** - written unconditionally to Flat ledger, blocked from Main ledger.

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

Evaluated against executable decision-time consensus market odds with vig (-110 / 0.5238 implied) across N = 4,510 games:

| Market | Eligible | Gated Bets | Hit Rate | P&L Units | Point ROI | 95% Date-Clustered CI | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Game Total (`cfb-total-v1`)** | 4,510 | 3,911 | **66.97%** | **+1,140.91U** | **+29.17%** | **[+25.31%, +32.98%]** | **`QUALIFIED`** |
| Moneyline (`college-football-v1`) | 4,510 | 2,802 | 34.90% | -868.87U | -31.01% | [-35.59%, -26.29%] | `FLAT_LEDGER_ONLY` |
| Spread (`cfb-spread-v1`) | 4,510 | 3,632 | 32.52% | -1,337.36U | -36.82% | [-40.00%, -33.47%] | `FLAT_LEDGER_ONLY` |

---

## 6. Production Model Registry & Artifact Hashes

| Model ID | Market | Schema | Artifact Path | SHA-256 Hash | Status |
| :--- | :--- | :---: | :--- | :--- | :--- |
| `college-football-v1` | Moneyline | 2 | `config/models/college-football-v1.json` | `f5b95acd6676fe7a...` | `FLAT_LEDGER_ONLY` |
| `cfb-spread-v1` | Spread | 2 | `config/models/cfb-spread-v1.json` | `7dc5b3428abfdb93...` | `FLAT_LEDGER_ONLY` |
| `cfb-total-v1` | Total | 2 | `config/models/cfb-total-v1.json` | `6831c96cc379860b...` | **`QUALIFIED` (Champion)** |
