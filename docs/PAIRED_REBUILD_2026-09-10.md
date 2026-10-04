# Rebuild: prospective outcomes and exact paired capture

The 21 fitted research candidates now have a market-aware comparison path.
The first 41 pregame forecasts have completed outcomes. Neither development
establishes a reliably more accurate or profitable replacement. The rebuild
remains open; current models and prior artifacts remain available.

## First prospective batch

`outputs/research/forward_outcome_scoring_20260910_settled_v1/` scores all 41
original predictions from `forward_capture_20260909_v1`, rebuilding every
prediction from its archived pregame source bytes. These are 11 distinct
events across two UTC start dates, not 41 independent games. No refit occurred.

| Candidate | Forecasts | Correct top outcome | Vector Brier | Log loss |
|---|---:|---:|---:|---:|
| MLB moneyline | 10 | 5 | 0.486528 | 0.679604 |
| MLB spread | 10 | 8 | 0.439055 | 0.631820 |
| MLB total | 10 | 3 | 0.536278 | 0.780589 |
| MLB NRFI | 10 | 6 | 0.498084 | 0.691241 |
| NFL moneyline | 1 | 1 | 0.190839 | 0.369471 |

Vector Brier sums squared error over every outcome class; it is twice the
usual scalar Brier for binary predictions. The totals result is weak in this
small batch. The spread result does not establish a durable advantage. Exact
lines came from archived ESPN schedules, not verified exchange executions;
profitability remains unavailable.

MLB outcomes were independently fetched from ESPN's completed September 9
scoreboard. The raw response, source URL/time/hash, strict normalized results,
and reconciliation are retained in
`outputs/research/espn_outcomes_20260910_verified_v2/`. All 80 corresponding
canonical ledger records agree on event identity, start, participants, and
the correct score dimension. NFL used its completed local historical record.
The first empty ESPN output directory records the unsuccessful sandbox attempt.

The new `research_outcomes.py` distinguishes explicit zero runs from missing
scores, requires completed events and exact identities, and reads explicit
first-inning periods. Missing first-inning data can leave NRFI unavailable
while full-game outcomes remain usable. The scoring command accepts archived
outcome overrides without replacing any original prediction inputs.

## Exact comparisons and payout math

`scripts/evaluate_captured_pairs.py` covers all 21 model registrations. Each
pair must reproduce both models, match event/start/participants/line and model
observation time, and independently reproduce the canonical settlement.
Soccer/international draws and integer-line pushes remain separate outcomes.
International expected settlement values are not mistaken for win probabilities.
Materially incoherent MLB spread side/push probability mass is rejected;
only tiny recorded serving-rounding error can be normalized.

The actual first-batch evaluation has zero eligible comparisons:

| Family | Rows | Exclusion |
|---|---:|---|
| MLB moneyline | 10 | Different candidate/incumbent observation times |
| MLB spread | 10 | Missing older incumbent serving snapshots |
| MLB NRFI | 10 | Missing older incumbent serving snapshots |
| MLB structural v10 total | 10 | No matching current-incumbent decisions |
| NFL moneyline | 1 | No matching incumbent decisions |

Evidence: `outputs/research/captured_pair_evaluation_20260910_v1/`.
Zero pairs produce null comparative metrics, not a zero-loss or passing result.

`research_pairing.simulate_contracts` evaluates complete explicit payout tables,
including numeric half-value draws and returned entry principal. It retains
entry fees, observes depth and a common $5 all-in budget, and reproduces the
existing binary policy. This is policy math, not quote authentication. The
runner currently authenticates archived full-game binary moneyline quotes;
other contract families still require source-backed rule/quote adapters.

## Pregame paired capture

`scripts/capture_research_pairs.py` separates preparation from capture:

1. `prepare` freezes explicit future fixtures, fitted artifacts, recipes and
   source bytes in a new immutable directory.
2. After an incumbent forecast, `capture` reads canonical SQLite in read-only
   mode and selects the first incumbent decision after preparation. It does
   not skip a bad first decision to choose a convenient later probability.
3. The candidate uses the incumbent's information cutoff. Its actual later
   creation time is recorded separately, and must still precede event start.
   Source capture and preparation must precede the common cutoff.
4. The scoring/comparison pipeline retains the preparation hash and checks
   the exact bound pick, incumbent snapshot, source and artifact identities.

No production forecast or order is launched by this command. Missing, changed,
conflicting or late inputs remain explicit failures. This proves use of the
frozen available sources, not upstream historical point-in-time certification.

Today’s public schedules were archived in
`outputs/research/paired_fixtures_20260910_v1/`: three future MLB games across
four markets and one NFL moneyline, 13 contracts total. Two already-started
MLB games were excluded; WNBA had no scheduled games. Inputs were prepared at
18:01 UTC in `paired_preparation_20260910_v1/`. The subsequent capture was run
after all 13 starts and correctly refused every prediction as
`fixture_not_future`; see `paired_capture_20260910_v1/`. This batch cannot be
recovered prospectively. Preparation alone does not collect paired evidence:
capture must actually run between an incumbent forecast and event start.

This timing gap is now addressed by the bounded `observe` command. It checks
the canonical ledger every 30 seconds, runs inference when pending fixtures
acquire new incumbent contexts, and retains the first successful pregame
prediction. Every intermediate batch is immutable. Later decisions or
postgame failures cannot replace a previously captured prediction. At event
start or the time limit, missing evidence becomes an explicit refusal. It
does not trigger the incumbent forecaster or alter its schedule.

The observer is running for the September 11 slate at
`outputs/research/paired_observer_20260911_v1/`, with inputs in
`paired_preparation_20260911_v1/` and public schedule evidence in
`paired_fixtures_20260911_v1/`. The cohort contains 68 explicit contracts over
15 MLB and five NCAAF games. One MLB NRFI lacks a named starter/venue and
three MLB games lack derivative lines; those contracts were not fabricated.
WNBA and NFL schedules were empty. The observer has a 24-hour maximum lifetime;
its live `report.json` states pending, captured and refused counts. Running
the observer is not evidence that any pair has already been captured.

Later update: the first observer was stopped before capturing any predictions
to correct NCAAF's historical participant namespace and incorporate three
new fitted joint CFB models. The active replacement is
`paired_observer_20260911_v2/`; see
[the fitted CFB iteration](CFB_JOINT_ITERATION_2026-09-11.md).

## Settlement repair and verification

NRFI settlement previously could substitute full-game scores when first-inning
scores were missing. It now leaves that pick pending before quote lookup.
The ESPN adapter no longer fabricates a zero from missing first-inning values.
No existing settled ledger row was rewritten; the 80 audited MLB rows were
already correct. Rollback is the previous code; new research artifacts remain
alongside old versions.

The complete suite collected 2,845 tests: 2,832 passed, three skipped, nine
localhost binding failures and one rounding-assertion failure. The latter
test now checks the documented per-team rounding order exactly. The full
affected 88-test group passes. The complete HTTP file passes all 23 tests
with localhost access after its GET authorization test was isolated from live
dashboard inventory I/O. These are a broad run plus focused resolutions, not
a claim that a second monolithic full-suite run occurred. Scoped Ruff/mypy and
file hashes are retained in `outputs/research/paired_verification_20260910/`.
After adding the observer, its complete affected inference/scoring/pairing
group passes 54 tests, including two new observer tests. All 70 changed Python
files pass Ruff. Protected JSON artifacts and champion configuration exactly
match the prior verified checkpoint.

## Remaining rebuild work

- Verify the active bounded observer captures real incumbent contexts, then
  extend ongoing schedule coverage and collect fresh paired outcomes across
  the 21 market families. V10's separate shadow path still needs its own
  canonical evidence connection when no canonical incumbent row exists.
- Bind nonbinary execution policies to actual archived exchange rules, exact
  quotes, fees and contract settlement semantics.
- Continue fitted sport-specific iterations, especially score distributions;
  the generic first generation is a development baseline, not an upgrade claim.
- Resolve historical source-availability limitations and the older lifecycle
  evidence-loader debt. Missing history cannot be repaired by relabeling it.

No reliable accuracy/profit superiority, promotion, or real-money result is
claimed by this checkpoint.
