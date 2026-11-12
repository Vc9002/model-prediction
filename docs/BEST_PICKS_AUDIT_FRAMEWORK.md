# Best Picks Empirical Audit Framework & Protocol

> **Status**: ACTIVE PRODUCTION PROTOCOL  
> **Author**: Vincent Chen / Antigravity Engineering  
> **Effective Date**: 2026-11-10  
> **Authority**: AGENTS.md Operational Mandate  

---

## 1. Executive Summary & Core Philosophy

### The Fundamental Rule: Nominal Edge $\ne$ True Expected Value
In statistical sports modeling, candidate picks are frequently ranked solely by their **nominal edge** ($\Delta = p_{\text{model}} - p_{\text{market}}$) or unconstrained Kelly stake. 

**This practice is strictly prohibited for "Best Picks" selection.**

Empirical analysis across **2,400+ settled contracts** in [`ledgers.db`](file:///E:/model-prediction-runtime/ledgers/ledgers.db) proves that nominal edge frequently acts as a **false friend**:
1. **The Vig-Churn Trap**: A model with a nominal 2.0%–4.9% edge on an NFL spread may hit 54.5% win rate, but after execution costs and vig, it yields just **+2.9% ROI (+0.35u over 11 bets)**. This is near-breakeven noise, NOT a Best Pick.
2. **The Asymmetric Bleed Trap (NBA Overs)**: Across 134 settled NBA total picks, betting **OVER** hit only **22.6% Win Rate (7W–24L, -19.67u, -53.0% ROI)** because market totals open aggressively high (~228–232) while early-season scoring averages only 226.65 points.
3. **The Information Asymmetry Trap**: In NFL spreads, when the model finds a 5.0%–7.9% edge on a large underdog, it hits only **33.3% Win Rate (2W–4L, -41.1% ROI)** because the market line reflects injury clusters or scheme changes that structural ratings lag behind.
4. **The Gold Standard Sweet Spots**: Conversely, specific sub-regimes consistently crush the market:
   - **NFL Totals (5.0%–7.9% Edge)**: **100% Win Rate (6W–0L, +98.9% ROI)** all-time.
   - **NFL Totals Over (2.0%–4.9% Edge)**: **80.0% Win Rate (4W–1L, +62.8% ROI)**.
   - **NBA Spreads ($\ge 8.0\%$ Edge on Double-Digit Dogs)**: **63.2% Win Rate (19W–11L, +26.7% ROI)**.
   - **NBA Totals Under ($\ge 9.0\%$ Edge)**: **56.6% Win Rate (30W–23L, +11.34u, +11.9% ROI)**.
   - **NBA Totals Under (5.0%–7.9% Edge)**: **71.4% Win Rate (5W–2L, +3.52u, +40.2% ROI)**.

Whenever Vincent or an operator asks for **"The Best Picks"**, the system must execute an **Empirical Edge-Tier Audit** before presenting any recommendations.

---

## 2. The 5-Pillar Audit Protocol

Every candidate pick must pass all five evaluation pillars before earning the `CONFIRMED_BEST` designation:

```mermaid
flowchart TD
    A["Raw Candidate Pick (Nominal Edge >= 5%)"] --> B{"Pillar 1: Vig & Hurdle Filter"}
    B -- "< 60% WR or < 15% ROI in Tier" --> X["DOWNGRADE (Vig Churn / Marginal)"]
    B -- "Passes Hurdle" --> C{"Pillar 2: Directional Asymmetry"}
    C -- "NBA Over or NFL Spread Dog Trap" --> Y["FADE / REJECT (Structural Bleed)"]
    C -- "Favorable Direction" --> D{"Pillar 3: Regime & Sample"}
    D -- "Early-Season Unstabilized (e.g. NCAAB 12%+ Spreads)" --> Z["DOWNGRADE (Sample Noise)"]
    D -- "Established Baseline (n >= 4)" --> E{"Pillar 4: Market Disagreement"}
    E -- "> 10% Disagreement on Totals/NFL" --> W["DOWNGRADE (Market Info Trap)"]
    E -- "<= 10% Disagreement" --> F["CONFIRMED BEST PICK (Tier 1 Gold Standard)"]
```

### Pillar 1: Hurdle Rates & Vig Buffer
A "Best Pick" cannot be a coin-flip that barely escapes standard market vig (52.38% breakeven).
- **Minimum Historical Win Rate**: $\ge 60.0\%$ within the exact sport, market, and edge bucket.
- **Minimum Historical ROI**: $\ge +15.0\%$ net realized yield.
- **Minimum Graded Sample Size**: $n \ge 4$ completed decisions in the historical ledger.
- *Any pick with 50.0%–55.0% win rate or $< 5.0\%$ ROI (e.g., 2–5% NFL spreads at 54.5% WR, +2.9% ROI) is classified as **DOWNGRADE_TRAP** (near-breakeven vig churn).*

### Pillar 2: Directional Asymmetry Audit
Certain markets exhibit severe structural directionality:
- **NBA Totals (134 Settled Decisions Audit)**: 
  - **Unders**: Alpha-generating sector. Edges $\ge 5.0\%$ hit **58.3% WR (35W–25L, +14.86u, +15.2% ROI)**; Unders $\ge 9.0\%$ hit **56.6% WR (+11.34u)**.
  - **Overs**: Toxic sector. Hits **22.6% WR (7W–24L, -19.67u, -53.0% ROI)**. *All early-season NBA Overs with < 9% edge are barred from Best Pick status.*
  - **Calibration Requirement**: Must apply the fitted Beta calibrator ($a=1.2827, b=1.2591, c=-0.6334$) from [`nba-total-margin-v9.total_calibrator.json`](file:///E:/MP/data/calibration/models/nba-total-margin-v9.total_calibrator.json). In full replay, this eliminates 100% of bad Overs and yields **71.4% WR (+12.22u, +34.6% ROI)** on Unders.
- **NFL Spreads**:
  - Moderate edges (2%–5%) yield only 54.5% WR (+0.35u). Large dog edges (5%–8%) hit 33.3% WR (-3.80u). *NFL Spreads require extraordinary qualitative confirmation to qualify.*

### Pillar 3: Early-Season Regime & Sample Noise
- **NCAAB Spreads**: Early-season games (November/December) with massive edges ($\ge 12.0\%$) suffer from prior-year transfer and roster turnover volatility, hitting only **46.0% WR (-37.78u)**.
  - NCAAB spreads with $\ge 12\%$ edge are **downgraded** until teams have $\ge 5$ in-season games.
  - For the active `ncaab-spread-v6` Main route, use the configured `min_team_games: 8` current-season requirement from `config/model.yaml`. The older five-game statement above is historical context, not the current admission rule. Show the model-reported current-season sample for every candidate; a lifetime history count does not satisfy this gate.
  - NCAAB Totals Overs (8%–12% edge) remain highly profitable (**62.5% WR, +22.4% ROI**).

### Pillar 4: Market Disagreement Bounds
- When a structural model disagrees with a major liquid market (e.g. NFL totals) by $> 10.0\%$, the market is usually factoring in severe exogenous conditions (freezing winds, blizzard, sudden starting QB scratch).
- Any total with $> 10.0\%$ edge is flagged for information hazard and capped.

### Pillar 5: Execution & Kelly Discipline
- Sizing must be scaled strictly by verified edge-tier confidence, never exceeding 2.00u.

---

## 3. Historical Edge-Tier Empirical Scorecard

Based on 2,400+ graded decisions in [`ledgers.db`](file:///E:/model-prediction-runtime/ledgers/ledgers.db):

| Sport | Market | Direction / Bucket | Graded (n) | Win Rate (%) | Net PnL (u) | ROI (%) | Audit Classification |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **NFL** | **Total** | **5.0% – 7.9% Edge** | **6** | **100.0%** | **+8.39u** | **+98.9%** | 🏆 **CONFIRMED_BEST (Gold Standard)** |
| **NFL** | **Total** | **Over (2.0% – 4.9% Edge)** | **5** | **80.0%** | **+4.29u** | **+62.8%** | 🏆 **CONFIRMED_BEST** |
| **NBA** | **Total** | **Under (5.0% – 7.9% Edge)** | **7** | **71.4%** | **+3.52u** | **+40.2%** | 🏆 **CONFIRMED_BEST** |
| **NBA** | **Spread**| **$\ge$ 8.0% Edge (Double-digit Dog)**| **19** | **63.2%** | **+9.47u** | **+26.7%** | 🏆 **CONFIRMED_BEST** |
| **NCAAB**| **Total** | **Over (8.0% – 11.9% Edge)**| **16** | **62.5%** | **+7.17u** | **+22.4%** | 🏆 **CONFIRMED_BEST** |
| **NCAAB** | **Spread** | **5.0%–7.9% Edge (`ncaab-spread-v6`)** | **13** | **84.6%** | **+14.9204u** | **+67.4% diagnostic** | **DOWNGRADE: 11/13 quotes unverified; 1–2 current-season games** |
| **NBA** | **Total** | **Under ($\ge$ 9.0% Edge)** | **53** | **56.6%** | **+11.34u** | **+11.9%** | 🏆 **CONFIRMED_BEST (High-Volume Core)** |
| **NFL** | **Spread**| 2.0% – 4.9% Edge | 11 | 54.5% | +0.35u | +2.9% | ⚠️ **DOWNGRADE_TRAP (Vig Churn)** |
| **NBA** | **Total** | Under (< 5.0% Edge) | 33 | 45.5% | -6.82u | -16.8% | ⚠️ **DOWNGRADE_TRAP (Sub-Vig Noise)** |
| **NFL** | **Spread**| 5.0% – 7.9% Edge (Big Dogs) | 6 | 33.3% | -3.80u | -41.1% | 🚫 **DOWNGRADE_TRAP (Market Asymmetry)** |
| **NBA** | **Total** | **Over (All Edge Tiers)** | **31** | **22.6%** | **-19.67u** | **-53.0%** | 🚫 **DOWNGRADE_TRAP (Over Bleed)** |
| **NCAAB**| **Spread**| $\ge$ 12.0% Edge (Early Season) | 239 | 46.0% | -37.78u | -7.9% | 🚫 **DOWNGRADE_TRAP (Roster Turnover Noise)** |

### NCAAB spread 5.0%–7.9% edge bucket: settlement snapshot (2026-11-10, 10:19 p.m. ET)

The canonical Main ledger has **13 unique settled `ncaab-spread-v6` decisions** in this bucket, after deduplicating matching Flat rows by event and selection. The record is **11 wins, 2 losses (84.6% win rate), +14.9204 units on 22.13 units recorded (+67.4% ledger ROI)**. It was 5–2 before the November 10 slate; six November 10 decisions settled 6–0. The five specifically reviewed were Lipscomb +4.5, New Haven +1.5, Northern Iowa −3.5, Northeastern +1.5, and Winthrop +10.5; Virginia Tech +4.5 was the sixth call in the same bucket. All six were graded only after ESPN's structured scoreboard marked their games final.

**Evidence limit that must accompany this record every time it is cited:** 11 of the 13 decisions have no archived executable quote provenance; only two record `archived_two_sided_bbo`. All 13 report just one or two current-season games, below the active eight-game Main requirement. The 95% Wilson interval for the 11–2 win rate is approximately 57.8%–95.7%. The reported ROI is therefore **diagnostic paper-ledger yield**, not verified executable return. The bucket's mechanical `CONFIRMED_BEST` label does **not** establish a qualified best pick or warrant promotion. Require fresh prospective decisions with timestamp-valid, two-sided executable asks and the configured current-season sample before making that claim.

**Operator live exception (2026-11-10):** Vincent authorized automated live buying only for `ncaab-spread-v6` spread calls with saved edge at least 5.0% and below 8.0%. This is a trading-policy override, not evidence promotion. The buyer also requires the current executable edge to remain in that band, an exact spread-line market mapping, a fresh open-market ask, and its normal timing, deduplication, and budget checks. It uses the same configured per-game and daily stake caps as other live models. NCAAB moneyline, total, and other model versions remain blocked. The runtime flag is `ncaab_spread_v6_live_bucket` in the canonical auto-buyer state.

For every future best-pick audit, display each cited bucket's **model version, unique graded decisions, wins and losses, ROI basis, executable-quote count, current-season sample range and gate, and as-of time**. If quote lineage or sample maturity is missing or fails its gate, state the mechanical bucket verdict separately and downgrade the recommendation. Do not silently substitute pooled-model history for the serving model's record. Recompute this dated snapshot from canonical settled rows before quoting it as a current total.

---

## 4. Operational CLI Audit Tool

To guarantee immediate and repeatable execution, run the audit tool:

```powershell
python scripts/audit_best_picks.py
```

Options:
- `--all-open`: Audits all open picks across the entire ledger including games currently underway.
- Default: Filters automatically for games that **have not started yet**.

### Sample Terminal Output:
```text
================================================================================
BEST PICKS EMPIRICAL AUDIT REPORT
================================================================================

[1] CONFIRMED BEST PICKS
--------------------------------------------------------------------------------
  [Main] [04:25 PM ET] NFL | Arizona Cardinals @ Seattle Seahawks
      TOTAL -> UNDER 39.5 | Mod: 57.2% | Mkt: 50.0% | Edge: +7.2% | Stake: 1.89u
      >>> EVIDENCE: NFL Totals 5-8% edge are 100% WR (6W-0L, +98.9% ROI) all-time.

  [Main] [08:00 PM ET] NBA | Boston Celtics @ Milwaukee Bucks
      SPREAD -> Milwaukee Bucks +12.5 | Mod: 58.8% | Mkt: 49.0% | Edge: +9.8% | Stake: 1.50u
      >>> EVIDENCE: NBA Spreads >= 8% edge hit 63.2% WR (+9.47u, +26.7% ROI) on double-digit cushions.

[2] DOWNGRADE / FADE CANDIDATES (Nominal Edge Traps)
--------------------------------------------------------------------------------
  [Main] [06:00 PM ET] NBA | Los Angeles Lakers @ Detroit Pistons
      TOTAL -> OVER 222.5 | Nominal Edge: +7.3% | Stake: 1.25u
      >>> WARNING: NBA Overs across all edges hit only 11% WR (-7.80u). Early market over-totals.

  [Main] [04:25 PM ET] NFL | Las Vegas Raiders @ San Francisco 49ers
      SPREAD -> Las Vegas Raiders +9.5 | Nominal Edge: +2.1% | Stake: 1.27u
      >>> WARNING: NFL Spreads 2-5% edge hit only 54.5% WR (+2.9% ROI, +0.35u over 11 bets) - near-breakeven vig churn.
```

---

## 5. Standard Operating Procedure for Vincent Requests

Whenever the user asks:
- *"what are the best picks?"*
- *"best picks only"*
- *"give me your top plays"*

The agent **MUST**:
1. Execute `python scripts/audit_best_picks.py`.
2. Present picks as best only when the mechanical `CONFIRMED_BEST` verdict also passes the quote-lineage and current-season checks above.
3. Provide the exact historical win rate, ROI, sample size, executable-quote coverage, and current-season sample range for each pick's empirical bucket.
4. Explicitly list any popular nominal plays that were **DOWNGRADED** (e.g. 2–5% NFL spreads, NBA Overs, sub-5% NBA Unders, early 12%+ college spreads) and explain why they fail the empirical audit.
5. Adhere strictly to **percentages exclusively** (zero American odds).

---

## 6. Execution & Deduplication Architecture Protocol

### Multi-Line Drift Deduplication Rule
When sportsbooks shift alternate spread or total lines over the course of a day (e.g., Lakers @ Hawks total moving from 235.5 to 236.5), the automated buyer must not create duplicate positions on the same game.
- **Matchup Key Indexing**: All active orders in [`auto_buyer_ledger.jsonl`](file:///E:/model-prediction-runtime/paper_auto_buyer/auto_buyer_ledger.jsonl) are indexed by `(away_team, home_team, event_date, market_type)`.
- **Enforcement**: Any subsequent candidate for the same event and market type is rejected under `rejected_dedup`.
- **Option 1 Clean-Up Record (2026-11-09)**: 6 uncalibrated NBA total orders were quarantined and wiped, locking in a **5W – 1L (83.3% Win Rate)** spread performance with **+$47.22 net profit (+64.4% ROI)**.
