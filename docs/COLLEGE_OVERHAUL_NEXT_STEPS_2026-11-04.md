# College model overhaul: corrected baseline and capture plan

## NCAAF: the Main/Flat gap was a cohort error

The read-only audit at `outputs/research/ncaaf_main_flat_reconciliation_2026-11-04.json`
reconciles the canonical SQLite records. A `status='settled'` query returns
395 Flat calls at +8.03u and 1,014 Main calls at -58.41u. The Flat archive
also has **975 superseded rows with final results**, totaling -63.91u.
All 975 exactly match Main settled contracts on event, market, selection,
line, units, result, and P&L. Counting all graded Flat records gives 1,370
calls at -55.88u. These are overlapping shadow observations, not independent
wagers, and multiple historical versions may exist per event.

Of the 1,014 Main settled calls, 1,006 have an exact graded Flat contract
(-55.95u); eight match the event, market and side but have a changed line.
There is no evidence that Main and Flat sized identical calls differently.
The loss is concentrated in legacy v1 versions: 830 calls, -70.85u. Later
v2-v4 versions together are 184 calls, +12.43u. Within v1, the 486 calls
tagged `QUALIFIED` lost -91.35u, while 344 `PAPER_CALL_LOW_EDGE` gained
+20.50u. This is a historical eligibility failure signal, **not** a reason
to reverse the gate based on already observed outcomes. Current serving code
admits only v2-v4 to Main.

The immediate reporting fix is to show a graded historical view separately
from an active `status='settled'` view, deduplicated by decision identity
when evaluating a strategy. Never present the active Flat view as the full
historical baseline. Future NCAAF model decisions need an exact point-in-time
price, model input snapshot, and calibration test against market probability.

## NCAAB: collect the inputs before fitting another model

The serving adapter now stores a hashed pregame feature-capture packet on
each new NCAAB call: model feature values, history-game count, score-history
digest, decision time, event identity, and served probability. It explicitly
marks `replay_verified=false`; this records inputs but does not prove exact
model replay. The model version in `config/model.yaml` now agrees with the
v3 production registry and executable code. The legacy indicative price
label is preserved because no executable quote lineage is attached.

ESPN completed-game summaries contain field-goal attempts, free-throw
attempts, offensive rebounds, and turnovers. A terminal-only, first-seen
capture now stores those raw fields and a source hash, never a fabricated
possession estimate. November 2 and 3 captured **182/182** and **28/28**
completed games, respectively, in the runtime feature archive. The daily
pipeline will capture the previous day's completed games and report gaps.
Those box scores were first observed now and cannot be claimed as pregame
evidence for calls already settled on November 2–3.

Current NCAAB market archives are still thin: November 2 has 487 captured
contracts across 11 market events, and November 3 has 28 across two; only
one November 3 contract was timestamp-valid before event start. The serving
adapter uses live indicative summary prices rather than an archived,
two-sided decision quote. The next data task is to capture and join an exact
pregame BBO for each candidate with market slug, line, both asks, observation
time, and event identity. Missing or late quotes must produce no executable
edge claim while remaining visible in research coverage.

Only after enough first-seen box scores and quotes accumulate should the next
version fit opponent-adjusted points per possession and tempo, with offseason
roster uncertainty. Fit parameters on earlier dates, choose decisions on later
dates, and compare calibration, Brier/log loss, margin/total error, and
recorded-price P&L on a common contract cohort. The November settled results
remain retrospective diagnostics and cannot themselves qualify the version.
