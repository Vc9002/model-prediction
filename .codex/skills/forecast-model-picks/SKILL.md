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
5. Before repeating a handoff's or memory note's "still broken" or "not done"
   claim, run `git log -3 --format='%ci %h %s' -- <path>` on the named file and
   compare the dates with the note's date. The fix may already be committed.
6. When asked to review picks shown in a screenshot, widget, or tracker,
   re-run the audit against the live ledger first, diff line, price, edge, and
   bucket against the shown rows, and lead the answer with the picks that
   shifted. A displayed snapshot is only as fresh as its scheduler.

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

Resolve feature-history and market-archive roots separately. A valid forecast and
a valid quote archive do not prove a valid join: confirm the selected quote's
archive path, content hash, side, exact line, and ask match the decision payload,
and report quote coverage for all listed contracts with explicit missing reasons.

Check the model's current-season sample at the final Main admission boundary. A
lifetime-history gate does not establish it. Fail closed when the sample is missing.

Use the dedicated research commands for esports, KBO, and NPB. Their output
remains zero-unit even when model fair value exceeds the ask.

## Mutate only when requested

- Add `--log` or use `log` only when Vincent asks to record picks.
- Use `flat-forecast` only when he asks for the flat comparison ledger; it can replace same-day rows.
- Settle, void, update closing prices, score research, review a loss, or mutate bans only when requested.
- Inspect exposure before and after a qualified ledger addition.
- During a refresh, retire a call only against a committed same-event, same-market replacement. Capture open slots first, reconcile after the writer, and restore unreplaced slots with an audited event.
- When repairing refresh orphans, join slot identity with the audit-chain retirement reason, select the latest pregame row per slot, back up the database and exports, and verify unrelated rows did not change. Do not reopen intentional retirements.
- When reviewing an auto-buyer cohort, keep trade scope separate from stake policy, and keep configured caps unless Vincent names a different one. List the exact current pick IDs with tier, admission reason, model version, saved edge, quote provenance, projected side, and contract-side text. State any difference from the historical cohort, and block or separately authorize Flat-only execution.
- Before settlement, re-query an authoritative structured scoreboard and
  require its explicit terminal-state marker and final score. A late clock,
  search snippet, or box-score page title does not establish finality.
- Never invoke `execute`, `sell-position`, cancellation code, dashboard order-submit endpoints, or credentialed order APIs.

## Verify best-pick claims

An audit tool's label (for example CONFIRMED_BEST) is a claim to check, not a
finding. Hardcoded constants in a verdict tool are cached claims, and a "fixed"
tool can still rest on tiny buckets.

Before naming any pick best:

1. Recompute the cited bucket from each decision's first-sighting row (the
   earliest ledger row, graded at its own line and price) with the tool's own
   dedup. Check the sample size and whether neighbouring buckets agree. A label
   that contradicts the tool's own model-specific bucket line is unverified.
2. Split rows by price provenance first. Rows whose price equals a known
   default (such as 52.4% or 50.0%) with no quote provenance are placeholders:
   report them separately and exclude them from ROI. Keep only
   decision-time executable quotes in the evidence.
3. Report realized edge (hit rate minus break-even) next to nominal edge for
   each bucket, plus the slope across buckets. A green cell inside a negative
   slope is noise. Report the serving model separately from retired versions.

When asked for deeper verification, run each of these as a new query against
source data, never by re-reading the summary:

- Re-grade settled rows from stored scores; check that a win pays
  `units × (1/price − 1)` and a loss costs `units`.
- Binomial p-value of each cited cell against its price-implied break-even.
- Shift each bucket boundary by ±1 point and widen it; count how many cells
  were searched, since one significant cell among hundreds is expected.
- Calibration by model-probability band.
- Compare the artifact's described inputs with the feature keys actually in
  the serving snapshot.
- Re-read the live slate, price, and injury news for every named pick.

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
7. Score the probability actually served. Label any reconstructed substitute a
   proxy.
8. Break bucket results out by quote provenance and current-season maturity before
   applying a best-pick label. Indicative ROI is diagnostic only.
9. Report coherence, predictive accuracy, and executable profit as separate claims.
10. Before narrating any game, build a three-way table per settled row: model
    projection, market line, actual result. Report each one's MAE and the record
    bucketed by |model − market|. If the market beats the model and the record
    falls as disagreement grows, classify `model_error`, however clean the
    packet looks. Check whether a calibrator's minimum-outcome guard is why raw,
    over-confident probabilities are being served.

Use the Dia Bridge for page verification. Keep all work shadow or zero-unit.

## Pre-flight check

Before reporting a slate or settlement, verify the worker runtime root,
canonical ledger coverage for every configured lane, source observation times,
and saved decision evidence. Reconcile row, event, and unit counts for any
loss cohort. Settlement additionally requires an explicitly terminal
scoreboard state and final score immediately before writing.
Any refresh must leave each prior open slot replaced or restored, and any cohort
review must compare exact current picks with the historical tier.
Any best-pick claim must come from recomputed settled rows split by price
provenance, never from a tool label or a displayed snapshot. Any loss diagnosis
must include the model-versus-market-versus-actual table.
