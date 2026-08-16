# Distribution Migration Plan — From Outcome Models to Score-Distribution Models

**Written:** 2026-08-17. **Basis:** `docs/PREDICTION_PLAYBOOKS.md` (per-market
recipes with published numbers) vs. the live model inventory verified this
session. **Status:** planning document — nothing here promotes, ships, or
changes live behavior; execution slots into the existing phase ordering
(`docs/V9_RESEARCH_PLAN.md`, `docs/POST_MLB_RESEARCH_PLANS.md`).

---

## 1. The strategic shift

The research program converged on one architectural verdict, repeated
across every sport's literature:

> **Predict the score distribution per match. Every market — moneyline,
> spread, total — is a different functional of that one joint
> distribution. Never build them as separate models.**

Three independent reasons:

1. **Coherence.** Separate ML/spread/total models can contradict each
   other on the same game (different engines, different features,
   different calibration). One distribution makes contradiction
   structurally impossible and makes calibration errors shared — fix
   the distribution once, all three markets improve.
2. **Accuracy evidence.** The single largest documented modeling error
   in any of these markets is the likelihood choice: NB beats Poisson
   for MLB scores by LOO Δ = +4,116 (±121 dSE) — and NFL is even more
   overdispersed (σp = 4.56 vs MLB 2.27; PMC8282683). Outcome-level
   logistic models never see this error class because they skip the
   score space entirely; it shows up as mispriced totals tails and
   run-line mass.
3. **Where the published edges in accuracy live** — park/weather
   physics (closed-form air density, ±2.8 runs at altitude), TTO,
   starter-IP distributions, rest effects, draft interactions — are
   all *mean-model inputs to a score distribution*, not classification
   features. An outcome-level model cannot absorb a ±3-run mean shift
   as cleanly as the distribution can.

**Current-state summary (verified):** the system is mostly
outcome-level. The distribution machinery EXISTS in code but is not
promoted: `simulate_game(method=...)` (gamma_poisson default /
negative_binomial / independent_poisson) and
`compare_distribution_methods()` were built 2026-08-13, with NB noted
as "the first serious challenger to the incumbent gamma-Poisson;
runnable but not yet promoted." This plan is the promotion path for
that machinery, per sport.

## 2. Per-market migration

### 2.1 MLB — the flagship (Phase 6, expanded)

**From:** `mlb-elo-trend-lr-v8` (moneyline LR on 6 features),
`measured-edge-margin-v3` (spread), `measured-edge-totals-v3` (totals —
known weak spot), three separate engines.

**To:** one per-game joint NB matrix
`P_ij = NB_h(i; λ_h, r_h) × NB_a(j; λ_a, r_a)`, with:

```text
ML:      P(home) = Σ_{i>j} P_ij + P(tie)×~0.54
RL:      P(home −1.5) = Σ_{i−j≥2} P_ij
Totals:  P(over L) = 1 − CDF(convolution, L)
```

**Mean model (the input stack, in build order):**
1. League mean (~4.35) × team runs rating (projection-class blend,
   PA-weighted — Steamer/ZiPS-class RMSE ≈ 0.83–1.03 ERA is the
   published quality bar).
2. **Air-density layer** (closed-form Bahill equation: altitude 80% /
   temp 13% / pressure 4% / humidity 3% of density variance; +2.8
   runs/game at 5,000 ft; ~1.1 runs cold→warm swing). This replaces
   the single scalar `weather_factor` with the physics-correct
   decomposition and is pure feature engineering — no fitting.
3. Starter adjustment: FIP-class projection × expected-IP share, with
   the **starter-IP distribution** (mean ≈ 5.2, right-skewed, falling
   ~0.1 IP/year) feeding bullpen exposure.
4. Time-through-order: continuous +13 wOBA pts per TTO (≈ +0.3–0.4
   cumulative runs/9 over three TTOs).
5. Lineup/platoon: only for extremes (±25 wOBA pts same-hand split,
   1,000–2,200 PA to trust it — model rare, deliberate cases only).

**Calibration & targets:** Platt/temperature on the differential
logits, temporal splits only. Published reference points: ECE 0.0168
(open-source NB engine) vs MLB's own 0.0222; game accuracy ceiling
≈ 62%, Brier ≈ 0.23, AUC ≈ 0.67 (9,700-game test). Expect calibration
to improve before discrimination. Zero-inflated NB is a test candidate
(dive-3 vs dive-4 reconciliation) — run it against plain NB on our
snapshots; keep whichever wins proper scores.

**Sequencing with v9:** the A–N ablation matrix (v9 features) and this
distribution engine are complementary, not competing: the matrix
improves the ML *features*; the engine replaces the *structure*.
Per the existing plan, the engine is Phase 6 (parallel to Phase 5
shadow) — do not reorder the v8 gate or the matrix.

### 2.2 WNBA — biggest relative gap

**From:** Elo+trend v4 ML; `wnba-spread-margin-v1` (Φ(line; margin,
10.5)); **no totals model**.

**To:** `E[PTS] = pace × ORtg / 100` per team (Dean Oliver
decomposition; WNBA pace ≈ 76–78, league ≈ 80 ppg), then NB team
distributions (σp ≈ 1.5 — modest but real gain over Poisson), totals
and spread from the joint:

- `pace_est`: rolling team pace, recency-decayed, opponent-adjusted.
- `ORtg_est`: rolling efficiency, home/away split × opponent defense.
- **Rest features** (the published, transferable magnitudes): +2.08
  points per extra recovery day vs opponent; >1-day rest +1.1 home /
  +1.6 away; **back-to-back cancels home advantage entirely**.
- Home court +2–4 points otherwise.

**Build order:** (1) pace×ORtg decomposition (data derivable from box
scores we already capture — no new source needed); (2) rest features
(schedule data in hand); (3) NB totals + spread from the joint; (4)
re-estimate the NBA-transferred magnitudes on WNBA data before trusting
them. Parsimony warning from the literature: more features HURT small
leagues (MLP degraded at top-17 features) — keep it a linear/GLM model.

### 2.3 Soccer — the most mature literature, gated on one data source

**From:** pooled league Elo, binary structure with patched draw
handling.

**To (per league, after the league split):** Dixon-Coles engine
`λ_home = base × att_h × def_a × γ; λ_away = base × att_a × def_h;
P(i,j) = Pois(λ_h,i)·Pois(λ_a,j)·τ(i,j)` with τ on the four low-score
cells (0-0: 8.4% empirical vs 7.1% naive; ρ ≈ −0.13), γ ≈ 1.25–1.35
re-estimated per league, and **xG-based strength updates** (xG-Elo:
update on expected-goals margin, not goals; xG predicts future goals
r = 0.574 vs 0.456 for goals themselves).

**Blocker, stated honestly:** the xG input layer needs an xG data
source the incumbent side doesn't have. Until one lands, the engine
ships with goals-only strength updates (still beats the current model
on structure alone) and xG slots in as a measured upgrade. Benchmark:
bookmaker RPS 0.188 (Groll et al.) — the number every league engine
must beat to justify itself.

### 2.4 Esports — ratings swap + the strongest untapped signal

**From:** NeutralElo v6 per title (inactivity decay + thin-data
shrink — good), no draft/map features.

**To (per title):** `P(win) = logit(β1·ΔGlicko + β2·Δdraft + β3·side +
β4·roster_recency)`.

- **Glicko-2 replaces Elo** (uncertainty-aware updating; published
  out-of-sample log-loss −811.8 vs Elo −1,841.8; roster changes reset
  rating uncertainty). Infrastructure swap, reuses the existing
  per-title pipeline.
- **Pairwise draft features** — the strongest published esports signal
  (draft-only 69.8–72.9% accuracy in Dota 2; pairwise interactions
  ≈ +10 accuracy points; interaction-blind features HURT). Rolling
  pairwise hero-winrate matrix, re-estimated per meta window (patches
  drift hero values).
- Streak/form features: empirically near-zero — don't spend effort.

**Blocker:** draft/map data source needed per title (not currently
ingested). The Glicko swap has no blocker; draft features queue behind
data acquisition.

### 2.5 Tennis — point-level chain as the long-term destination

**From:** Elo-family with fixed 60/40 surface weighting.

**To:** O'Malley-style chain from two serve-point probabilities
(serve ≈ 0.65 men / 0.62 women; first-serve point-win 69% clay → 75%
grass/hard): point → game → tiebreak → set → match, closed form. The
v2 surface-weighting challenger remains the next step (it's the
incremental improvement the data supports today); the chain is the
architecture after v2, using Sackmann's serve stats (already in our
ingest path, unused).

### 2.6 NFL — calibration now, NB later, features gated on PIT

**From:** Elo-based v4, no calibration challenger promoted.

**To:** (1) calibration challengers (Identity/Platt/Temperature/
Isotonic) — queued, cheapest accuracy gain available; (2) NB score
distributions for totals/spread — **NFL is the most overdispersed of
the studied leagues (σp = 4.56)**, so the NB upgrade matters even more
here than MLB; (3) EPA/CPOE/OL/injury features only when PIT-safe
(nflverse as the candidate source, rebuild-side already has it).

## 3. Cross-cutting engineering

1. **Joint-distribution interface, one per sport.** A shared shape:
   per-team score distribution → `P_ij` matrix → market functionals
   (ML / spread / totals / alt lines). MLB NB matrix, WNBA NB pair,
   soccer bivariate-Poisson-with-τ, tennis chain, esports logit
   (esports has no natural score space — the logit IS the joint for
   its single market; that's a recognized exception, not a violation).
2. **Blending distributions: log-score stacking** (Yao, Vehtari,
   Simpson, Gelman) over BMA — BMA asymptotically picks one model,
   stacking optimizes the proper score directly. Weights re-fit on
   rolling windows; Pseudo-BMA+ as the cheap fallback. This replaces
   winner-take-all champion selection *at the research level*;
   promotion governance (shadow chain, operator decision) is
   unchanged.
3. **Evaluation backbone** (Gneiting & Raftery): log score as the
   primary proper score, Brier for binary markets, RPS for three-outcome
   soccer, ECE + reliability diagrams on temporal splits only. Already
   partially built (evaluator §0.4); add RPS and ECE-bucketed output.
4. **Calibration layer reuse:** the existing calibrator challenger
   portfolio (Identity/Platt/Temperature/Isotonic) applies unchanged
   to distribution outputs — calibrate the market functionals, not the
   distribution parameters.

## 4. Sequencing, dependencies, and blockers

```text
Phase 0–1 (now → 08-18):  v8 row-level gate (unchanged)
Phase 2 (08-18+):         v9 ablation matrix A–N (features for the ML market)
Phase 3–4:                v9-LR/XGB + calibration (outcome level, as planned)
Phase 5:                  prospective shadow (unchanged)
Phase 6 (parallel):       MLB distribution engine → NB means → air-density
                          layer → starter-IP → TTO → ZINB test → joint
                          ML/RL/totals → stacking blend
Post-MLB queue:           WNBA pace×ORtg+rest → soccer DC engine (xG gated)
                          → esports Glicko (draft gated) → NFL calibration
                          → tennis v2 → chain
```

**Data blockers (each queues its dependent work, not the whole plan):**
soccer xG source; esports draft/map feeds per title; NFL PIT-safe
feature source verification. **No blocker for:** MLB engine + physics
layer, WNBA decomposition + rest, Glicko swap, NFL calibration.

## 5. Risks and honest expectations

- **Published ceilings are modest** (MLB ~62% accuracy / 0.23 Brier /
  0.67 AUC). The plan's value is proper-score gains from structure and
  calibration, not a step-change in discrimination. Expect ECE to move
  first.
- **"Simple beats complex" is the recurring result** across every
  sport studied (LR ≈ stacking ≈ MLP within noise; features beat
  algorithms). Resist architecture sprawl — the migration is a
  structure change plus targeted feature layers, not a deep-learning
  program.
- **Nulls are expected outcomes.** The open-source honesty audit
  (dive-3) showed even the best-aligned systems report CIs spanning
  zero. Every step above ships through the existing shadow chain with
  pre-registered proper-score thresholds.
- **The v8 gate and matrix ordering are untouched by this plan.**
  Nothing here starts before its phase; the distribution engine does
  not bypass the A–N matrix, the shadow window, or promotion
  governance.

## 6. Acceptance criteria per migration step

Each step (e.g., "air-density layer", "Glicko swap", "τ correction")
enters the registry with: temporal-split proper-score delta vs the
current model, fold agreement, bootstrap CI, coverage, and a
pre-registered KEEP threshold (`docs/V9_RESEARCH_PLAN.md` §0.5
rules apply verbatim). Economic metrics reported, never gating.
