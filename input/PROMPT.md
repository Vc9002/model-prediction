# Execution protocol

The 2026-07-17 request has been executed. Future agents should not repeat rejected experiments without new point-in-time data.

## Current production contract

- Use the v2 hash-verified moneyline artifact pinned in `config/model.yaml`.
- Walk forward by complete dates; learn weights on train, threshold on validation, and grade once on locked holdout.
- Qualification requires at least 50 calls, at least 60% accuracy, positive flat P&L at -110, and positive P&L in every complete month with at least 10 calls.
- Report incomplete terminal months as provisional and complete months below 10 calls as insufficient.
- Remain shadow-only unless the user separately requests and confirms an operational action.

## Do not implement from the old prompt

- Do not claim WNBA July was below 10 calls; it had 27 and was incomplete.
- Do not publish NFL 68%/166 calls; the current pipeline reproduces 60.55%/109 calls.
- Do not substitute confidence gap for max probability; the two gates are algebraically equivalent.
- Do not add adaptive HFA based on the existing audit; its holdout hit rate was worse.
- Do not train on retrospectively cached starter ERA.
- Do not validate spread/total/F5/YRFI contracts without exact timestamp-valid pregame lines and inputs.

## Required next evidence

Prospectively collect observed-at snapshots for executable market lines, confirmed starters, pitcher game logs, and bullpen state. After enough settlements, create a new artifact version and rerun the full DEBUG, test, lint, and locked-holdout process.
