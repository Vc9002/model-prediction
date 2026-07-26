# Execution protocol

Read `docs/PROJECT_STATUS.md` before acting. Do not trust this file, a prior
prompt, or a README table over current source, tests, config, artifact, and
generated evidence.

## Durable contract

- Walk forward by complete dates; fit on train, select on validation, and grade a declared candidate once on locked holdout.
- Require point-in-time inputs and exact market semantics.
- Keep outcome modeling independent from market price.
- Keep model accuracy separate from executable EV and profitability.
- Preserve failed gates, rejected experiments, and rollback artifacts.
- Remain shadow or zero-unit unless Vincent separately requests a real-money action and confirms the exact order.

## Current blockers

- Real-money execution tickets are not bound to the exact qualified ledger row,
  and ledger mutation is not atomic with audit append.
- Active MLB v6 is unqualified and uses probable-starter history without
  historical pregame provenance.
- Four dashboard order-preview tests fail; Ruff reports 117 findings.
- Two spread artifacts fail canonical hash verification, config has a missing
  residual artifact and a wrong MLB total reference, and the latest learned
  report is stale for the active checkout.
- WNBA availability can fail open; learned artifact qualification and quote
  `timestamp_valid` are not enforced at the first classification/pricing step.
- KBO/NPB preview, research routing, and tie-settlement economics are wrong.
- Spread, total, F5, YRFI/NRFI, and research-league economics remain blocked
  without exact point-in-time inputs and executable prices.

## Required next evidence

Repair capital/evidence integrity first: execution binding, ledger/audit
recoverability, point-in-time starter provenance, artifact/timestamp
enforcement, and fail-closed availability. Then restore a green checkout,
reproduce the release, and continue prospective observed-at source records,
executable BBOs, confirmed starters/lineups, pitcher and bullpen state, and
closing snapshots. Promote only a new version reproduced from a stable, green
checkout.
