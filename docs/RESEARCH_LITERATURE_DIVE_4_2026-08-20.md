# Research Literature & Architecture Dive #4: Plate-Appearance Monte Carlo, Catcher Framing, and Lineup Alpha Windows

**Date:** 2026-08-20  
**Sources Analyzed:**  
1. `r/algobetting` — *"Sharing my Monte Carlo MLB prop model architecture + 2024 backtest calibration results (12,847 predictions)"* by `gmoneycinco`  
2. `gmalbert/baseball-predictions` (*Betting Cleanup*) — Streamlit MLB analytics & multi-market model  
3. `laplaces42/mlb_game_predictor` — 2000–2024 historical boxscore EMA engine  

---

## 1. Executive Summary & Quantitative Insights

This review synthesizes the quantitative methodology, feature engineering, and market microstructure dynamics of plate-appearance (PA) level Monte Carlo baseball modeling and contrasts them with the existing `model-prediction` production architecture.

### Key Takeaways:
1. **Generative Consistency**: A PA-by-PA 8-class Monte Carlo simulation generates Moneyline, Runline (-1.5), Game Totals, Team Totals, First Inning (NRFI/YRFI), and Player Props (K, Hits, TB) from **one single unified game-state simulation**, eliminating line inversions by construction.
2. **Catcher Framing is a Massive Structural Alpha Lever**: A $+2$ framing runs catcher shifts called-strike probabilities in the Statcast Shadow Zone by $1\text{--}2$ percentage points per PA, compounding across count leverage (e.g. $0\text{-}1$ vs $1\text{-}0$ count shifts wOBA by $-.115$ and raises $K\%$ by $+14.2\%$) to materially swing game totals and pitcher strikeouts.
3. **Isotonic Calibration Outperforms Platt Scaling in Skewed Tails**: Logistic Platt scaling imposes artificial symmetry that distorts extreme-probability markets (<15%, >85%). Pool-Adjacent-Violators (PAV) Isotonic Regression preserves empirical calibration in the tails (yielding $3.1\%$ ECE across 12,847 bets).
4. **The 15–30 Minute "Lineup Confirmation Alpha Window"**: Edge degrades within 15–30 minutes of official lineup release as market makers adjust to sharp syndicate flow. Our automated 35-minute pre-game wake planner (`plan_lineup_wakes.py`) perfectly positions our pipeline in this golden execution window.

---

## 2. Mathematical Architecture: Discrete-Event PA Monte Carlo

### A. The 8-Class Multinomial Plate-Appearance Predictor
For pitcher $i$ facing batter $j$ under context vector $\mathbf{c}$, the probability distribution across all 8 terminal PA outcomes is:
$$\mathbf{P}(Y = k \mid \mathbf{x}_{ij}) = \frac{\exp\left(\mathbf{w}_k^T \mathbf{x}_{ij} + b_k\right)}{\sum_{m=1}^8 \exp\left(\mathbf{w}_m^T \mathbf{x}_{ij} + b_m\right)}$$
where $k \in \{\text{Single, Double, Triple, Home Run, Walk, HBP, Strikeout, In-Play Out}\}$.

### B. Dynamic Markov Base-Out Transitions (24 Discrete States)
The simulation tracks state $S = (\text{Inning } i, \text{Outs } o, \text{Bases } \mathbf{b}, \text{PitchCount } p_c, \text{ScoreDiff } \Delta s)$:
- **Outs**: $o \leftarrow o + 1$. If $o = 3$, inning concludes, bases clear $\mathbf{b} \leftarrow (0,0,0)$, and $o \leftarrow 0$.
- **Walk / HBP**: Force advances only on occupied consecutive bases.
- **Hits (1B, 2B, 3B)**: Baserunner advancement is conditioned on runner speed / out state (e.g., runner on 1st advancing to 3rd on single with $P \approx 0.28 + 0.05 \times z_{\text{speed}}$).
- **Pitcher Fatigue Hazard Function**: Pitcher removal transitions to the bullpen pool once pitch count crosses a survival hazard threshold:
  $$h(t) = \frac{\beta}{\alpha} \left(\frac{t}{\alpha}\right)^{\beta - 1}$$

---

## 3. High-Alpha Feature Engineering Deep Dive

### A. Catcher Pitch Framing & Shadow Zone Leverage
- **Statcast Shadow Zone (Zones 11–19)**: Encompasses the 2-inch border along the strike zone perimeter (~26% of all pitches).
- **Framing Impact on Count Transitions**:
  - Count transition from $0\text{-}0$ to $0\text{-}1$: Strikeout probability jumps from $14.8\%$ to $29.0\%$.
  - Count transition from $0\text{-}0$ to $1\text{-}0$: Batter wOBA jumps from $.315$ to $.380$.
- **Automated Ball-Strike (ABS) Review Dynamics**:
  - The ABS challenge system does not replace umpires on every pitch; teams hold 2–3 challenges.
  - Framing signal remains active and unreviewed on $\sim 95\%$ of takes.

### B. Pitch-Type Arsenal vs. Lineup Vulnerability Tensor
Rather than scalar ERA/FIP metrics, compute the dot product between starting pitcher pitch frequencies and lineup pitch-type run values ($w\text{Pitch}$):
$$\text{Arsenal\_Mismatch}_{ij} = \sum_{p \in \{\text{4S, SI, FC, SL, CH, CU, ST, FS}\}} f_{i, p} \cdot \text{RV}_{j, p}$$
- High-spin sweepers facing a top-order with negative run values against breaking balls creates a large mispricing vs consensus sportsbook odds.

### C. Environmental Aerodynamics (Air Density $\rho \times$ Magnus Spin Decay)
$$\rho = \frac{P_d}{R_d T} + \frac{P_v}{R_v T}$$
- Every 10°F increase in temperature adds $\sim 3.5$ feet of carry to fly balls.
- At altitude ($ho \approx 82\%$ at Coors Field), Magnus force $F_M = S(\mathbf{\omega} \times \mathbf{v})$ drops by $\sim 18\%$, flattening pitch movement and suppressing strikeout props.

---

## 4. Forensic Code Review of Open Source Projects

### 1. `gmalbert/baseball-predictions` (*Betting Cleanup*)
- **Strengths**: Rich feature inventory (Statcast barrel%, exit velo, OAA defense, bullpen fatigue index, umpire run environments).
- **Critical Failure**: Admitted full-season **lookahead bias** in `src/models/features.py:L19-23`:
  `"We join same-season team and pitcher stats (full-season aggregates). This is intentional for a backtesting/analysis tool."`
- **Verdict**: Backtest metrics are invalid for prospective deployment. All extracted features must be strictly re-implemented inside our expanding-window Point-in-Time (PIT) pipeline.

### 2. `laplaces42/mlb_game_predictor`
- **Strengths**: Clean 2000–2024 historical dataset with exponential moving averages (EMA).
- **Weaknesses**: Misses starting pitcher dominance vs bullpen fatigue separation and lacks simulation granularity.

---

## 5. Implementation Roadmap for `model-prediction`

- [ ] **Step 1: Catcher Framing Ingestion** (`src/model_prediction/features/catcher_framing.py`)
  - Ingest rolling 30-day Shadow Zone called-strike rates per catcher.
  - Expose `catcher_framing_gap = home_catcher_rate - away_catcher_rate`.
- [ ] **Step 2: Pitch Arsenal Matchup Tensor** (`src/model_prediction/features/pitch_arsenal.py`)
  - Compute pitcher pitch distribution $\times$ lineup Top-5 $w\text{Pitch}$ run values.
- [ ] **Step 3: Discrete PA-by-PA Monte Carlo Engine** (`src/model_prediction/models/mlb_monte_carlo.py`)
  - 5,000 simulations per game tracking 24 base-out states and pitcher fatigue curves.
- [ ] **Step 4: Isotonic Tail Calibrator** (`src/model_prediction/meta_calibrator.py`)
  - Route NRFI, Runline -1.5, and K-props through Isotonic regression.
- [ ] **Step 5: Lineup Alpha Window Trigger** (`scripts/plan_lineup_wakes.py`)
  - Couple 35-minute pre-game wake trigger to automated forecast and execution ticket generation.
