# Model Card: `soccer-poisson-dc-rebuild-v1`

Created 2026-08-11 as part of the Soccer rebuild on branch
`rebuild/soccer-model-fit-v1`.

This is a **rebuild-native** Dixon-Coles Poisson model, independently fitted
from ESPN capture-time-only normalized data. It does **not** load, alias, or
share state with the incumbent `soccer-poisson-dc-v1`.

## Why it exists

The incumbent `soccer-poisson-dc-v1` is an architecturally sound model
(audited and retained per `SOCCER_POISSON_DC_V1.md`), but it has known gaps:
hardcoded `HOME_GOAL_BOOST` and `DC_RHO` with no fitting evidence,
competition-pooled parameters across 19 heterogeneous leagues, and no
versioned qualification artifact. This rebuild model establishes an
independent fitting pipeline — MLE-estimated Dixon-Coles parameters from
ESPN site v2 data — that will enable competition-specific ablation and
parameter fitting against the project's own data.

## Market(s) predicted

One market from the shared Dixon-Coles score matrix:
1. **Moneyline (1X2)** — home/draw/away, `market_type: "moneyline_3way"`.

Total goals (O/U 2.5) and BTTS can be derived from the same score matrix
but are not separately evaluated in this initial fit.

Coverage: 19 leagues (expanded from the initial 6 in the soccer data foundation):
- eng.1 (EPL), esp.1 (La Liga), ger.1 (Bundesliga), ita.1 (Serie A), fra.1 (Ligue 1)
- ned.1 (Eredivisie), por.1 (Primeira Liga)
- usa.1 (MLS)
- uefa.champions (UCL)
- bra.1 (Brasileirão), bra.2 (Brazil Série B)
- arg.1 (Argentina Primera), arg.2 (Argentina Primera Nacional)
- col.1 (Colombia), chi.1 (Chile), uru.1 (Uruguay), ecu.1 (Ecuador), per.1 (Peru)
- conmebol.sudamericana (Copa Sudamericana)
- fifa.friendly, club.friendly (friendlies)

## Feature set

Not a discrete feature vector — a learned goal-rate model with MLE-fitted parameters:

- **Per-team attack strength** (`team_attack[t]`): float, constrained sum-to-zero
- **Per-team defense strength** (`team_defense[t]`): float, constrained sum-to-zero
- **League baseline** (`league_baseline[league]`): float, per-league scoring rate
- **Home advantage** (`home_advantage`): single float, MLE-estimated
- **Dixon-Coles rho** (`rho`): single float in [-0.3, 0.3], low-score draw correlation

All parameters are estimated jointly via maximum likelihood — no hardcoded
constants from the incumbent or from textbook defaults. This is the first
rebuild-native MLE fit for a soccer Poisson-DC model.

## Training/fitting method

### Data
- Source: ESPN site v2 scoreboards, captured as "current" data (not live PIT).
- Seasons: 2022–2025 for major European leagues; 2024–2025 for South American.
- Normalized via `SoccerNormalizedStore` → `read_matches()`.

### Split
- Chronological 60/20/20 by `event_start_utc` (dates, not row positions).

### Model
- Dixon-Coles bivariate Poisson:
  - `lambda_home = exp(league_baseline + attack_home + defense_away + home_advantage)`
  - `lambda_away = exp(league_baseline + attack_away + defense_home)`
- tau(rho) correction for (0,0), (1,0), (0,1), (1,1) scorelines
- MLE via `scipy.optimize.minimize` (L-BFGS-B, numerical gradients)
- Attack/defense centered post-fit (sum-to-zero constraint)

### Calibration
- Identity only (no Platt/isotonic).
- 3-way probabilities need multivariate calibration; identity is a placeholder.
  Proper 3-way calibration (e.g., Dirichlet regression or temperature scaling on
  the full simplex) is deferred to a future iteration.

### Evaluation
- **LogLoss** (3-way): `-mean(log(p_correct))`
- **Brier** (per-outcome): mean squared error for home/draw/away
- **Accuracy**: highest-probability outcome matches actual result

## Independent fitting

This model is independently fitted. It does **not**:
- Load the incumbent model artifact or its state
- Copy incumbent constants (`HOME_GOAL_BOOST`, `DC_RHO`, EWMA half-lives)
- Share any in-memory or on-disk state with the incumbent

All parameters are estimated from ESPN data via MLE. League code knowledge
(ESPN code mappings) is configuration/data knowledge ported from the
incumbent's documented league list, not an incumbent artifact.

## PIT-safety

**Status: `historical_result_research` (capture-time-only).**

All training data is capture-time-only provenance from ESPN site v2. These
are historical results downloaded from current ESPN scoreboards — they were
not captured live at the time the games were played. No retrospective PIT
evidence exists.

`pit_eligible = True` on all normalized rows because the capture-time
metadata is correctly structured, but the "capture time" is the date the
data was backfilled (August 2026), not the game date.

**Implication**: This model cannot pass a real PIT walk-forward validation
until live capture infrastructure exists. The current fit is a research
prototype demonstrating that the Dixon-Coles architecture works with the
rebuild data foundation.

## production_allowed

**`false`**. Capture-time-only provenance and lack of PIT evidence mean
this model is not eligible for production use. It is a research/challenger
artifact only.

## Known limitations

1. **Capture-time-only provenance**: All data was backfilled in August 2026,
   not captured live. No prospective PIT evidence.
2. **Identity calibration**: 3-way probability calibration is placeholder-only.
   Real calibration (temperature scaling, Dirichlet regression) not yet implemented.
3. **Global attack/defense**: Per-team parameters pool across all leagues. No
   league-specific attack/defense separation yet — same limitation as the incumbent.
4. **No per-league home advantage**: Single global home_advantage scalar.
5. **Training data recency**: Fitted once on the full available history, not
   periodically refit or walk-forward validated.
6. **Numerical gradients**: L-BFGS-B uses finite-difference gradients rather than
   analytical derivatives. Convergence may be slow for large team sets.

## Artifact location

- Model artifact: `config/models/challengers/soccer-poisson-dc-rebuild-v1.json`
- Calibrator: `config/models/challengers/soccer-poisson-dc-rebuild-v1-calibrator.json`
- Training script: `scripts/train_soccer_rebuild_v1.py`
- Model implementation: `src/model_prediction/rebuild/soccer/elo.py`

## Next steps

1. Implement live daily scoreboard capture for prospective PIT data.
2. Add proper 3-way calibration (temperature scaling on simplex).
3. Ablation: competition-specific attack/defense vs. global pooling.
4. Per-league home advantage estimation.
5. Add analytical Jacobian for faster MLE convergence.
6. Walk-forward refitting to track parameter stability over time.
