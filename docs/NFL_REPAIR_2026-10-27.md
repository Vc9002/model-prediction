# NFL input repair and v6 research routing — 2026-10-27

This change repairs the current NFL input path and starts a new spread/total
research generation. It does **not** establish improved prediction accuracy or
profitability. The incumbent v4 moneyline and v5 spread/total artifacts remain
available for rollback; the new v6 artifacts explicitly say `qualified: false`.

## Theory, evidence, test, failure, rollback

- **Theory:** NFL structural calls were exposed to old or missing EPA/QB rows,
  an unverified injury file, UTC-based rest dates, and a last-game QB heuristic.
  Captured release vintages and a depth-chart QB1 projection make these inputs
  traceable and prevent qualification when they are absent.
- **Evidence:** legacy efficiency Parquets stopped at Oct 12 while completed
  NFL games reached Oct 26. The Oct 27 nflverse capture has
  214 team-game rows and 242 QB-game rows through Oct 26 plus 32 projected
  QB1 entries. The depth chart is a **projection**, not an official game-day
  starter confirmation.
- **Test:** `tests/test_nfl_efficiency_snapshot.py` checks exclusion of future
  results and future depth-chart rows, source hash tampering, last-game
  coverage, and ET date conversion. The seven focused `test_cli.py -k nfl`
  caller tests pass after the ledger mock was brought up to its current API.
- **Failure mode:** nflverse or ESPN can fail or lag, and the projected QB1 can
  differ from the actual starter. The capture leaves an explicit error status;
  no v6 contract is qualified for Main. Flat remains a research diagnostic.
- **Rollback:** v5 artifacts are untouched. Revert the v6 forecast routing and
  daily capture changes; immutable source captures remain available for audit.

## Implementation

`capture_nfl_efficiency` downloads current-season nflverse weekly player
stats, schedules, and dated depth charts before each daily forecast. It keeps
only score-final games before the capture date, derives the exact team/QB
columns consumed by the structural engine, archives raw releases and derived
Parquets, and hashes every file and its manifest. The reader chooses only a
hash-valid snapshot observed by the decision time; legacy Parquets are
diagnostic fallback only. This timing rule follows the source's update and
revision schedule: [nflverse data availability](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).
The live downloader rejects caller-supplied past observation timestamps so a
modern release cannot be mislabelled as a historical vintage.

Spread/total v6 uses the latest captured QB1 projection, rather than the
last historical starter, and computes rest from Eastern game dates. It embeds
source metadata in the model input snapshot. Per-contract input qualification
requires verified efficiency coverage of both teams' latest games, projected
  QB names from same-day depth-chart updates, and a same-day hash-valid injury snapshot with neither projected
starter listed Out/IR. The v6 artifact itself is research-only, so these
checks cannot be mistaken for a predictive-performance promotion.

## Remaining research gates

1. **Moneyline and joint scores:** `nfl-joint-market-v2` remains a separate
   challenger. Its 15 selected quote-backed games are insufficient to promote
   moneyline, spread, or total. Capture full unselected prospective slates and
   compare all three heads with contemporaneous no-vig prices and incumbent
   calls on identical event-line-time pairs.
2. **QB availability:** depth-chart QB1 is provisional. Add timed official
   game-status and inactive feeds with player IDs, then fit a replacement
   effect with an explicit team-EPA overlap ablation.
3. **Structural coefficients:** v6 still uses the v5 engine's code-owned home
   advantage, rest, EPA-to-points, key-number, and weather values. Train a
   versioned joint distribution from a date-forward training split and have
   each artifact field drive serving arithmetic. Neither v6 artifact claims
   that this has been done.
4. **Totals and weather:** capture decision-time forecast runs and fit
   pace/possession and weather responses before changing live total pricing.
5. **Promotion:** keep the new versions in Flat research until a prospective
   unselected cohort establishes paired per-market calibration and
   market-relative improvement, followed by executable EV/P&L review.
