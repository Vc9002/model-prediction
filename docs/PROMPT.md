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

- MLB and NFL do not clear the current locked qualification contract.
- The active MLB artifact is internally inconsistent and fails the config test.
- Soccer report/config/registry status is inconsistent.
- The audit chain is broken, the installed CLI entry point is stale, and Ruff is not clean.
- Spread, total, F5, YRFI/NRFI, and research-league economics remain blocked without exact point-in-time inputs and executable prices.

## Required next evidence

Repair release alignment first. Then prospectively collect observed-at source
records, executable BBOs, confirmed starters/lineups, pitcher and bullpen state,
and closing snapshots. Promote only a new version reproduced from a stable,
green checkout.
