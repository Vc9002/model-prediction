# Agent Execution Guidelines

## PRE-CHANGE CHECKLIST
1. State the theory. 2. State the evidence. 3. State the test.
4. State the failure mode. 5. State the rollback.

## CHANGE WORKFLOW
Diagnose → Test → Implement → Verify (pytest + ruff) → Document

## RULES
- Walk-forward only. Locked holdout. Never peek.
- Never hardcode weights or confidence thresholds. Load them from hash-verified artifacts.
- A monthly gate binds only for a complete calendar month with at least 10 called picks. Keep partial and insufficient months visible but non-binding.
- Protected: NBA model, WNBA model, `data/historical/*`, and every existing file in `config/models/*`.
- Improvements are new versions alongside old versions; never overwrite or delete rollback artifacts.
- Reject retrospective features that cannot prove they were observable before event start.
- Do not invent spread, total, F5, or YRFI/NRFI contracts when exact historical lines are absent.
- Shadow calls are not orders. Never log or execute during model validation.
