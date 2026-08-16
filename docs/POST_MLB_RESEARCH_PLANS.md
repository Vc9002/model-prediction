# Post-MLB Research Plans (per sport, expanded)

**Written:** 2026-08-17. Expands the one-line queue in
`docs/RESEARCH_BACKLOG.md` §"Post-MLB queue" into executable per-sport
plans. MLB v9 lives in `docs/V9_RESEARCH_PLAN.md`. Each plan below
follows the same shape: current state → hypothesis → experiment design →
promotion gate → risks. Nothing here starts before its turn in the queue
and nothing promotes without the shadow-first chain (walk-forward → OOF →
same-event → bootstrap → freeze → prospective shadow → settled comparison →
`PROMOTION_CANDIDATE` → operator).

---

## 1. WNBA — v5 challenger, then possession architecture

**Current state.** `wnba-moneyline-v4` (Elo + trend) is champion.
Evidence from prior ablation work says `defensive_trend_gap` was
*harmful* for WNBA — it enters the challenger's feature list only as a
documented removal. Spread baseline `wnba-spread-margin-v1` (margin_normal,
P(away_cover)=Φ(line; margin, 10.5)) replaced the broken
`wnba-spread-baseline-v1` that was predicting moneyline, not spread
(2026-08-13). The 2026-08-15 WNBA threading bug (0-row forecast day,
per-thread sqlite connections) is fixed; 08-14/15 rows are a documented
data gap, not backfilled.

**Plan.**

1. **v5 paired test** — challenger = v4 feature set minus
   `defensive_trend_gap` (and, per the retention rules, any other feature
   the evidence flags). Same frozen table, same folds, paired ΔBrier/ΔLogLoss
   vs v4, date-cluster bootstrap. KEEP only on pre-registered thresholds
   (formalized in `docs/V9_RESEARCH_PLAN.md` §0.5). Also re-audit spread
   sign, home/away orientation, push handling, and settlement grading on
   the spread baseline before ANY spread work — the moneyline-not-spread
   bug was live for months and nobody noticed.
2. **Possession architecture (the real fix)** — WNBA has no Elo-era
   shortage; the ceiling is structural. Move from team-level probability to
   possessions × PPP → score distribution → ML/spread/total from one
   joint draw, mirroring MLB Phase 6. Fit pace (possessions) and efficiency
   (PPP) separately; both are more stable and more interpretable than the
   current aggregate.
3. **Promotion gate.** v5 becomes `PROMOTION_CANDIDATE` only after the
   full shadow chain; v4 stays champion through the transition.

**Risks.** Small league (12 teams, ~44 games/season) — every ablation has
tiny N; bootstrap CIs will be wide. Pre-register a minimum shadow window
realistic for WNBA volume (a season is ~220 games total) or the gate will
never fire.

---

## 2. NFL — calibration first, features only when PIT-safe

**Current state.** NFL v4 incumbent. No QB/EPA/CPOE/OL features yet —
correctly: most public sources are not point-in-time safe. The rebuild
track already has an `nflverse` provider, which IS PIT-safe (snapshot per
week) — a bridge candidate for the incumbent side.

**Plan.**

1. **Calibration first (cheap, high value).** Run Identity / Platt /
   Temperature / Isotonic on the incumbent's OOF probabilities. Identity
   is a legitimate winner. This is the single fastest evidence-gathering
   experiment in the whole queue.
2. **Feature candidates, gated on PIT-safety proofs.** QB EPA/CPOE,
   OL quality, injuries (with report timestamps), weather (already
   partially captured) — each admitted only when its source's
   as-of semantics are verified, per the shadow-feature pattern
   (CLAUDE.md: capture → fail-closed feature → inert until an artifact
   lists it → walk-forward validation → explicit promotion).
3. **Promotion gate.** Same chain; note NFL's ~272-game season gives the
   largest N of any sport here, so the gates can be tighter.

**Risks.** NFL has few games/season relative to MLB — holdout cohorts
span multiple seasons; watch season-boundary leakage in any
train/val/holdout split.

---

## 3. Tennis — v2 surface-weighting challenger, then the ladder

**Current state.** Tennis v1 (fixed 60/40 surface weighting) is champion;
ATP wired 2026-08-03 (WTA + ATP); ITF remains unbuildable with current
sources. Data: `tennis_sackmann.py` (pandas, the one pandas consumer —
matching its upstream format).

**Plan.**

1. **v2 challenger** — surface weighting `w_max × n_surface/(n_surface + c)`
   with c learned, instead of fixed 60/40. One change at a time, paired vs
   v1, pre-registered thresholds.
2. **Then the ladder, each as its own paired experiment:** K factor,
   inactivity decay, surface-sample-size floor, tournament-level weight,
   retirement handling (retirement ≠ loss — currently a documented edge
   case; model the "did not finish" outcome explicitly rather than
   folding it into the binary).
3. **Promotion gate.** v1 stays champion until v2 wins the paired
   comparison AND the prospective shadow; tennis has the second-largest
   event volume after MLB, so a shadow window of ≥30 settled picks is
   reachable in weeks, not months.

**Risks.** Surface-specific sample sizes are small for clay/grass
subsets; challenger must report per-surface metrics, not pooled only.
ITF coverage gap stays documented until a source appears (no
Polymarket ATP market either — execution surface matters).

---

## 4. Soccer — league split (replaces the pooled research direction)

**Current state.** Pooled soccer model; The Odds API credential is a
known-DEGRADED external dependency (does not block infra burn-in but does
limit odds data). Main ledger carries soccer with a real edge+confidence
gate.

**Plan.**

1. **League split, ordered:** EPL, La Liga, Bundesliga, Serie A, MLS,
   UCL first (largest data), then the long tail (Brasileirao, Argentina,
   etc.). Each league gets independent fitted state: Elo, trend, home
   advantage, rest/travel — the 2026-08-17 brainstorm's shared
   cross-sport rest/travel module is a natural fit here.
2. **Draws are draws.** The 2026-08-04 fix already treats draws as draws
   in head-to-head features; the long-term target is a real three-outcome
   model P(home)/P(draw)/P(away) instead of the forced binary structure
   (backlog: "a score model producing three outcomes"). The league split
   is the right moment to build it, league by league.
3. **Promotion gate.** Per-league, not per-pool: a league model promotes
   only within its league. The pooled model stays as fallback until a
   league model has shadow evidence.

**Risks.** The degraded Odds credential may cap closing-price capture;
the split plan should not depend on odds data it can't reliably get.

---

## 5. Esports — per-title independence, no generic v7

**Current state.** Five titles at v6 (CS2, Valorant, LoL, Dota2, R6) with
the F-63 inactivity-decay + thin-data shrink shipped (real ~30-35% edge
reduction on thin matchups, verified held-out). Gated Research curation is
deliberately tightened vs MLB's no-gates philosophy — sport-specific and
intentional.

**Plan.**

1. **Title split** — per title: independent players, teams, features,
   hyperparameters, calibration, artifacts, and promotion. No shared
   "esports v7" — the titles differ more than soccer leagues do.
2. **Per-title next experiment** (the one with the largest current
   known weakness first): map/hero/meta effects (LoL/Dota2 drafts,
   CS2 map pool, Valorant agent comps) are the obvious untapped signal
   class; each starts as a shadow feature per the standard pattern.
3. **Promotion gate.** Per title; the existing Gated Research
   curation contract applies unchanged.

**Risks.** Roster volatility — a "team" changes players between events;
name-level identity (already fixed for accents, 2026-08-16) and
player-level tracking must be solid before any player feature ships.
The `_identity_key` settlement path is defensive-fixed; don't rely on
it for anything heavier without a real player crosswalk.

---

## 6. KBO / NPB — starter models and PIT discipline

**Current state.** KBO/NPB v2 as controls; tied to MLB-style Elo with
team-level features. The 2026-08-13 timestamp-ordering bug (silently
zeroed every real pick for months) and the home/away label bug were both
KBO/NPB-class PIT bugs — this sport family is where PIT discipline has
failed hardest historically.

**Plan.**

1. **Starter models** — port the MLB starter feature pattern (ERA/FIP/
   K-BB% rolling, PIT-safe) to KBO/NPB. Their own feeds' probable-starter
   data must pass the same PIT eligibility checks MLB's does.
2. **Every new feature ships with a timestamp-ordering property test**
   (the hypothesis suite from 2026-08-16 generalizes directly) — this is
   the one family where a per-feature PIT test is non-negotiable, given
   its history.
3. **Promotion gate.** v2 stays control; anything new runs the full
   shadow chain.

**Risks.** Small leagues, fewer sources; the 14-day-stale artifact
problem (fixed in cli.py refresh cadence) must stay fixed — staleness
alerts for these two leagues specifically.

---

## 7. Data acquisition matrix (what exists → what's next)

| Domain | Have today | Candidate additions |
|---|---|---|
| MLB | ESPN scoreboard/probables, MLB Stats API box + game snapshots, Statcast (rebuild only), park/weather, Polymarket, The Odds API | Statcast pitch-level on incumbent side (velo/spin trend), umpire factors (already in snapshots — build the table), altitude/elevation table |
| WNBA | ESPN | Lineups/availability PIT feeds; pace/PPP is derivable from existing box data |
| NFL | ESPN; nflverse (rebuild only) | nflverse on incumbent (PIT-safe by construction); OL/injury reports with timestamps |
| Tennis | Sackmann + ESPN + MyLife providers | Serve/return stats (Sackmann already has them — unused); retirement-cause data |
| Soccer | ESPN; The Odds API (degraded credential) | Per-league roster/lineup feeds; venue distance tables for rest/travel |
| Esports | Polymarket only (for odds); title APIs via existing collectors | Draft/map data per title; player-level history |
| KBO/NPB | ESPN international | Probable-starter feeds with PIT eligibility |

Every addition must answer the PIT question first — a source that can't
prove its as-of semantics is a research liability, not an asset.

---

## 8. Cross-cutting systems (already logged; binding here for scheduling)

From `docs/RESEARCH_BACKLOG.md` brainstorm sections — these schedule
against the phases above, not independently:

- **Economic evaluator** (secondary always, never retention-determining)
  and **continuous prospective evaluation** — land BEFORE Phase 5 shadow
  windows start, so every shadow has standardized economic reporting.
- **Correlation-aware exposure sizing** — before ANY real-money
  promotion (main-ledger MLB/WNBA already live; sizing applies now).
- **Runtime-root offsite backup** — as soon as possible; it protects the
  audit chain every other experiment depends on.
- **Per-pick feature-contribution panel** — after the v9 feature set
  stabilizes (Phase 3); cheap once features are frozen.
