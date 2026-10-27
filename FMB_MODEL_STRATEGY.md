# FadeMeBets + MP Model Combination Strategy
**Quick Reference Guide (Updated: October 25, 2026)**
*Full Quantitative Backtest & Implementation Details: [`docs/FMB_MODEL_COMBINATION_STRATEGY.md`](file:///E:/MP/docs/FMB_MODEL_COMBINATION_STRATEGY.md)*
*Live Provider Ledger: [`Books_Tracker.md`](file:///E:/MP/Books_Tracker.md)*

---

### The Winning Strategy: Strategy 6 (High-Conviction Fade, WNBA Pass)
> **Rule**:
> 1. **PASS entirely on WNBA** (sample size $N=2$ is statistically insufficient to claim durable edge; eliminates unverified variance).
> 2. Follow FadeMeBets picks by default across MLB, NBA, NFL, and NCAAF.
> 3. **Actively FADE FMB when an MP Model has an opposing CALL with an Edge ≥ 10.0%**.

> **Evidence boundary:** The results below are retrospective discovery evidence from October 17–25, not a prospective guarantee. Track all later slates separately and do not retune the rule from a single day.

### Incremental Pick Decisions

Picks may be evaluated as they arrive; a complete three-provider slate is not always required.

- **BUY NOW** — exact line and price verified; the applicable strategy qualifies; no unresolved same-event/provider conflict exists; and all already-known correlated and daily exposure stays within cap.
- **BUY — REDUCED** — qualified, but existing correlated exposure requires a smaller maximum stake, which must be stated.
- **HOLD FOR FULL SLATE** — another provider's pending slate, an unresolved same-game relationship, or missing exact-line evidence could change the decision.
- **PASS** — unsupported prop, invalid or stale price, missing exact-line validation, provider conflict without a justified survivor, or excessive exposure.
- **FADE** — an exact opposing model call clears the strategy threshold; state the replacement side, exact line, price assumption, and maximum stake.

Every recommendation must separate the provider-posted stake from the proposed personal action. `BUY NOW` is a recommendation, never confirmation that an order was placed.

### Empirical Backtest Results (Oct 17–25):
- **Strategy 6 (High-Conviction Fade + WNBA Pass)**: **22W–11L (66.7%)**, **+16.45u net**, **+34.8% ROI** (+6.76u over raw baseline).
- **Strategy 7 (High-Conviction Veto + WNBA Pass)**: **18W–7L (72.0%)**, **+14.65u net**, **+37.1% ROI** (saves 10.25u in risk, +4.96u over raw baseline).
- **FMB Baseline (WNBA Pass)**: 22W–11L (66.7%), +12.19u net, +25.8% ROI.
- **Raw FMB Baseline (as posted)**: 22W–13L (62.9%), +9.69u net, +19.5% ROI.
- **Pure Counter-Betting (Fade All Disagreements)**: 12W–19L (38.7%), -16.57u net, -36.6% ROI.

---

### Sport-Specific Rules:
1. **WNBA**: **AUTO-PASS / DO NOT BET**. Insufficient sample size ($N=2$). Passing immediately saves -2.50u of drawdown.
2. **MLB**: **100% Auto-Play FMB** (FMB is 4–0, +6.11u). Never fade.
3. **NBA**: **Auto-Play FMB** (FMB is 6–2, +4.08u). Heavy respect; low-edge disagreements are within noise.
4. **NFL & NCAAF**: Follow FMB by default; **FADE** only when MP Model has an opposing call with **Edge ≥ 10.0%**.
5. **Props / Parlays**: Play FMB props (2–0, +1.46u); Pass Scout/Kytra props and parlays.

*For full 36-pick breakdown, complete 9-strategy comparison matrix, and mathematical derivations, see [docs/FMB_MODEL_COMBINATION_STRATEGY.md](file:///E:/MP/docs/FMB_MODEL_COMBINATION_STRATEGY.md).*
