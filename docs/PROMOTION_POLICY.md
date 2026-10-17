# Model Promotion Policy: Direct Model Wiring & Realistic Paper Simulation

**Status**: Active System Policy  
**Effective Date**: 2026-10-16  
**Authority**: Lead Quantitative Systems Directive  

---

## 1. Executive Summary & Core Invariant

The `model-prediction` platform is a **100% paper trading ("fugazi")** multi-sport quantitative forecasting and portfolio simulation system.

> [!IMPORTANT]
> ### The 100% Paper ("Fugazi") Invariant
> - **Zero Real Money**: Under NO circumstances is real money used, risked, or authorized for execution. Automated live order submission is strictly barred (`automated_orders: false`, `mode: "paper"`).
> - **Full Institutional Realism**: Although simulated ("fugazi"), every pick, stake, quote, and PnL calculation operates with rigorous realism *as if* real capital were at stake:
>   - **Realistic Unit Sizing**: Kelly-criterion and edge-scaled sizing (1.0U–2.0U per pick).
>   - **Live CLOB Orderbook Pricing**: Orders price against real Polymarket US executable asks and market spreads.
>   - **Daily Risk Controls**: Daily spend caps, league concentration limits, and drawdown circuit breakers.
>   - **Mark-to-Market Accounting**: PnL tracking, loss cause review, and post-match CLV (Closing Line Value) benchmarking.

---

## 2. Elimination of "Shadow-Qualified" Bureaucracy

Prior iterations of the system imposed complex bureaucratic gating:
- Requiring models to sit in `status: research` or `status: shadow_candidate`.
- Imposing artificial 50-call locked holdout requirements or 60% hit rate thresholds before a model could output sized units.
- Requiring special "qualification overrides" just to log real paper picks in Main or Flat ledgers.

**This bureaucracy is completely eliminated.** Because this entire project is 100% paper trading, quarantining models behind bureaucratic holdout gates only degraded coverage and delayed model iteration without providing any risk reduction.

### The Direct Wiring Principle
1. **Immediate Production Deployment**: Whenever an updated or specialized model is built for any sport, league, or market (e.g. dedicated Moneyline, Spread, or Total models, expanded soccer leagues, darts Markov models, hockey bivariate Poisson models), it is **directly wired** as the active serving champion.
2. **Active Paper Sizing**: All wired models immediately output full-fidelity, sized paper calls (1.0U–2.0U) in the operational ledgers.
3. **No Quarantine Holdouts**: Models do not require a 50-call "shadow-qualified" probation period to serve paper predictions.

---

## 3. Simplified Model Lifecycle

The operational model lifecycle is streamlined into three clean states:

```
┌─────────────────────────────────────────────────────────────┐
│                       MODEL LIFECYCLE                       │
├─────────────────────────────────────────────────────────────┤
│  PRODUCTION (Active Champion)                               │
│  - Most updated model for the sport/league/market.          │
│  - Directly wired in config/production.yaml and registry.   │
│  - Generates active daily forecasts and sized paper calls.  │
│                                                             │
│  ROLLBACK (Atomic Fallback)                                 │
│  - Previous stable champion retained for immediate fallback │
│    in the event of runtime data feed errors.                │
│                                                             │
│  RETIRED (Decommissioned)                                   │
│  - Obsolete models preserved in archive for audit trail.   │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Execution & Risk Invariants (Hard Edge Gate)

All models serving paper production pass through our execution safety gates:

1. **Hard Edge Gate ($0.02 \le \text{Edge} \le 0.08$)**:
   - Minimum edge of $+2.0\%$ ensures edge clears trading friction and execution vig.
   - Maximum edge ceiling of $+8.0\%$ guards against the "extreme edge anomaly trap" (unmodeled breaking injury scratches, lineup shifts, or data staleness).
2. **Unit Sizing Policy**:
   - Baseline unit stakes range from 1.0U (minimum edge / lower certainty) to 2.0U (maximum edge / high confidence).
   - `model_uncertainty` explicitly haircuts edge sizing.
3. **Drawdown Protection**:
   - Realized daily loss limit halts additional paper buys for the calendar day if breached.

---

## 5. Active Champion Matrix

All sports and markets are directly hooked to their most updated models:

| Sport / Domain | Markets Supported | Active Production Champion | Engine Type |
|---|---|---|---|
| **MLB** | Moneyline, Spread, Total, NRFI | `mlb-elo-trend-lr-v8`, `measured-edge-margin-v3`, `mlb-structural-v10-frozen`, `mlb-nrfi-v4` | Logistic Regression + Monte Carlo |
| **NBA** | Moneyline, Spread, Total | `nba-elo-trend-lr-v4`, normal margin/total | Normal Approximation Engine |
| **WNBA** | Moneyline, Spread, Total | `wnba-elo-trend-lr-v4`, `wnba-spread-margin-v2`, `wnba-total-margin-v2` | Normal Margin + Possessions Model |
| **NCAAB / WNCAAB** | Moneyline, Spread, Total | `ncaab-tempo-v1`, `wncaab-tempo-v1` | Tempo-Adjusted Normal Model |
| **EuroLeague** | Moneyline, Spread, Total | `euroleague-tempo-v1` | FIBA 40-Min Normal Model |
| **NFL** | Moneyline, Spread, Total | `nfl-elo-trend-lr-v4`, `nfl-spread-structural-v5`, `nfl-total-structural-v5` | Joint Score Normal Convolution |
| **NCAAF (CFB)** | Moneyline, Spread, Total | `cfb-structural-v2` | Bivariate Joint Score Engine |
| **Soccer (32 Leagues)** | 1X2 (ML), Spread, Total, BTTS | `soccer-poisson-dc-v2` + dedicated league parameters | Dixon-Coles Bivariate Poisson |
| **Hockey (NHL, AHL, etc.)** | Moneyline, Spread, Total | `hockey-poisson-dc-v1` | Bivariate Poisson with OT Resolution |
| **Darts (PDC, MODUS)** | Match Winner, Spread, Total | `darts-markov-v1` | Exact Markov Leg-Progression DP |
| **Tennis (WTA, ATP)** | Moneyline, Spread, Total | `tennis-surface-elo-v4` | Surface-Blended Elo + Markov Chain |
| **Esports (CS2, LoL, Dota, Val, R6)** | Match Winner, Spread, Total | `*-tiered-elo-v6` | Tier-Weighted Neutral Elo |
| **KBO & NPB** | Moneyline, Spread, Total | `*-tie-aware-elo-v2` | Tie-Aware Multinomial Elo |

---

## 6. Promotion & Wiring Procedure

To promote or wire a new model:
1. **Implement Model**: Build the model or dedicated market wrapper (in `src/model_prediction/models/market_models.py` or domain module).
2. **Register Entry**: Add entry to `config/production.yaml` with `serving_status: production`.
3. **Set Active Champion**: Point `champions[SPORT][MARKET]` to the new model ID.
4. **Verify Verification Tests**: Run unit tests (`pytest tests/`) to ensure clean load and execution.
5. **Begin Daily Forecasting**: The model immediately begins generating paper forecasts and sized paper calls on the next daily cycle.
