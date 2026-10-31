---
name: forecast-model-picks
description: Forecast, log, settle, review, and diagnose sports picks in the model-prediction repository. Use when Vincent asks for current model calls, exact model inputs, a slate forecast, a shadow or flat ledger update, settlement, CLV, performance, team bans, or a loss diagnosis across MLB, NBA, WNBA, NFL, soccer, tennis, esports, KBO, or NPB. Never place, modify, or cancel a real-money order.
---

# Forecast Model Picks

Work from the repo root (`e:\MP` on this Windows machine; `/Users/vincentc9002/model prediction` on the macOS deployment). On Windows PowerShell replace `env PYTHONPATH=src:. .venv/bin/python` with `$env:PYTHONPATH='src;.'; .venv\Scripts\python`.

## Establish current truth

1. Check `git status --short --branch`; preserve unrelated changes.
2. Read `docs/PROJECT_STATUS.md`, `config/model.yaml`, the exact active artifact,
   the current validation report, and the relevant league contract.
3. Confirm syntax with:

   ```sh
   env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli <command> --help
   ```

4. Fail closed when config, artifact, report, tests, or docs disagree. A YAML
   status label never overrides an invalid or inconsistent artifact.

## Forecast before logging

Run a non-logging forecast first:

```sh
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli forecast --sport <sport> --date <YYYY-MM-DD>
```

For each event, report the exact ingested feature values and cache/source paths,
timestamps, artifact version/hash, probability, confidence threshold,
executable side/ask, raw edge, action, and reason. Separate probable starters
from confirmed lineups. Missing two-sided prices, identities, starters,
point-in-time inputs, or valid qualification produces a no-call or zero-unit
research result.

Before declaring a forecast missing, compare the scheduled worker's runtime
root with the interactive CLI's, then inspect canonical full-slate and qualified
ledgers, saved pregame decisions, and each configured market lane. A game that
has started may disappear from fresh inference while its original decision
remains valid evidence. Report unpriced, started, and source-failed events
separately; a partially populated ledger must not mask events in another.
When backing up ledgers from multiple locations, include the full source-path
identity in each backup name and verify distinct sources cannot collide on a
case-insensitive filesystem.
For shared quote captures, bound concurrency and retries after a confirmed
rate limit, record skipped contracts, and judge daily coverage by event date
in the configured timezone. Report lookahead-date failures separately.

Use the dedicated research commands for esports, KBO, and NPB. Their output
remains zero-unit even when model fair value exceeds the ask.

## Mutate only when requested

- Add `--log` or use `log` only when Vincent asks to record picks.
- Use `flat-forecast` only when he asks for the flat comparison ledger; it can replace same-day rows.
- Settle, void, update closing prices, score research, review a loss, or mutate bans only when requested.
- Inspect exposure before and after a qualified ledger addition.
- Before settlement, re-query an authoritative structured scoreboard and
  require its explicit terminal-state marker and final score. A late clock,
  search snippet, or box-score page title does not establish finality.
- Never invoke `execute`, `sell-position`, cancellation code, dashboard order-submit endpoints, or credentialed order APIs.

## Diagnose losses

1. Start from settled ledger rows, not exchange orders.
2. Pin the settlement timezone and as-of time. Reconcile pick-row count,
   unique-event count, and units against canonical storage before describing
   a loss cohort. Deduplicate versions by event, selection, and
   feature-snapshot hash.
3. Reconstruct the decision packet from the ledger, audit chain, artifact,
   feature cache, and exact price snapshot.
4. Classify only after evidence supports `bad_data`, `bad_luck`,
   `market_or_rule_error`, `missing_information`, `model_error`, or
   `process_error`.
5. Do not tune from one loss. Require a versioned cohort or ablation for a model change.
6. Treat postgame causal stories as hypotheses until pregame-observable inputs
   and a walk-forward ablation support them.

Use the Dia Bridge for page verification. Keep all work shadow or zero-unit.

## Pre-flight check

Before reporting a slate or settlement, verify the worker runtime root,
canonical ledger coverage for every configured lane, source observation times,
and saved decision evidence. Reconcile row, event, and unit counts for any
loss cohort. Settlement additionally requires an explicitly terminal
scoreboard state and final score immediately before writing.
