# Model Prediction

## What This Is

A multi-sport event-prediction and trading system: point-in-time feature computation and per-sport models (MLB, WNBA, NFL, soccer, tennis, esports, CFB, international baseball) generate forecasts, which are compared against prediction-market prices (Polymarket) and, where edge exists, traded with real money. Runs on a daily scheduler with a dashboard for monitoring. Built and operated by a single developer (Vincent) as a live production trading system.

## Core Value

The system must be profitable and accurate in predicting events — forecasts must beat the market (or at minimum identify real edge), and the trading layer must only fire on genuine edge, not noise. If accuracy or profitability erodes, everything else (dashboard polish, coverage breadth) is secondary.

## Requirements

### Validated

- ✓ Point-in-time feature computation architecture (`learned_forward.py`) with logistic regression serving (v7/v4 artifacts) — existing, and explicitly the most fragile/highest-risk area per the codebase's own documentation
- ✓ Per-sport generic models (Elo + trends) for baseball, basketball/WNBA, football, soccer moneyline, tennis baseline, esports — existing
- ✓ Per-sport custom domain-specific models (MLB totals/spreads, soccer Dixon-Coles, tennis Elo+surface, esports series Elo, CFB structural) — existing
- ✓ Daily scheduler, settlement/ingestion pipeline, dashboard web UI, real-money Polymarket execution — existing
- ✓ Dual-track rebuild/incumbent model isolation (challengers evaluated in shadow before promotion) — existing
- ✓ Promotion gate for challenger models — existing, fail-closed by design

### Active

- [ ] Accuracy and profitability are the explicit, tracked success criteria for every model change (not just shipped/not-shipped)
- [ ] Point-in-time (PIT) correctness is protected as the highest-priority invariant across all sports, not just MLB
- [ ] Known settlement/coverage gaps (KBO/NPB stale rows, missing archive linkage outside MLB) are resolved or explicitly triaged, since unsettled/unlinked data undermines both accuracy measurement and profitability tracking

### Out of Scope

- Rebuilding the dashboard UI from scratch — existing dashboard is functional, cosmetic changes not prioritized
- Expanding to new sports beyond the current 8+ covered — focus is accuracy/profitability of existing coverage, not breadth, until existing models prove out
- Non-Polymarket execution venues — Polymarket is the current and only trading venue; adding others is a future milestone

## Context

- This is a live production system already trading real money — changes must be evaluated against the existing dual-track (challenger/incumbent) safety architecture, not shipped directly to production models.
- Per the codebase's own CLAUDE.md: point-in-time correctness bugs are "by a wide margin, the single most common source of real bugs" — any change touching `learned_forward.py`, `features/`, or `data_sources/` carries elevated risk and should be treated accordingly.
- Prior project history (per memory) shows market-relative evaluation already matters here: e.g., WNBA totals analysis found the market beats the model decisively in that market — "accurate" must be measured relative to market efficiency, not just raw calibration/Brier score in isolation.
- Known data-quality gaps directly threaten the "accurate and profitable" goal: 44 KBO/NPB rows are stuck unsettled (up to 29 days stale), and only MLB moneyline has full archive-to-contract linkage (194 events) — the other 13 sports lack this, which limits how well profitability can even be measured for them.

## Constraints

- **Safety architecture**: Model changes must go through the existing challenger/shadow → promotion-gate pipeline, not bypass it, since this is live-money trading
- **Point-in-time correctness**: Any feature/data-source change must preserve PIT correctness — this is the system's most fragile area and its most common bug source
- **Tech stack**: Python 3.11+, existing per-sport model architecture (Elo/trends generic + domain-specific custom models) — new sport coverage should follow established patterns unless there's a clear reason not to
- **External data dependencies**: Soccer forecasting depends on `api_football` (single point of failure); MLB feature extraction depends on `pybaseball` scraping Baseball-Reference (fragile upstream)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Treat existing production system as Validated, not re-scoped from zero | System is live and trading real money today; re-litigating shipped architecture wastes effort and risks destabilizing production | — Pending |
| "Accurate" is defined relative to market efficiency, not just absolute calibration | Prior WNBA totals finding showed market beats model despite reasonable calibration — profitability requires beating the market, not just being "correct" in isolation | — Pending |
| Settlement/coverage gaps (KBO/NPB, non-MLB archive linkage) treated as in-scope blockers to the accuracy/profitability goal | Unmeasured or unsettled markets can't be scored for accuracy or profitability, so they undermine the core value even if not directly asked for | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-09-20 after initialization*
