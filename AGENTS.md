# Agent Execution Guidelines

## 2026-09-09 OPERATOR RESEARCH RESET

Vincent explicitly declared all previous frozen research protocols obsolete
while requesting improved prediction accuracy AND profitability for every
current model. Historical Phase F locks, the v11 prohibition, and old frozen
development-window restrictions no longer prevent new research generations.
Treat their records as historical evidence, not active research authorization
gates. Preserve prior artifacts for comparison and rollback; distinguish
retrospective development from fresh prospective evidence. This reset does not
itself request changing live champions or placing orders.
Current work/results: `docs/GENERATION_2026-09-09.md` and the latest section of
`docs/ROADMAP.md`.

## FAST ITERATION & WORKFLOW SPEED (OPERATOR DIRECTIVE 2026-10-18)
- **Zero Test Overhead for Minor Changes**: NEVER run pytest suites for refactors, dead code cleanup, variable renames, formatting, comments, or documentation. Rely strictly on `ruff check` (runs in < 0.2s).
- **No Monolithic Test Runs**: NEVER run `pytest tests/` (all 3,200 tests) unless explicitly commanded by Vincent. It takes ~6 minutes and grinds the iteration loop to a halt.
- **Micro-Targeted Testing Only**: When core mathematical or pricing algorithms change, run ONLY the single directly affected test file with `-o addopts='' -q` (e.g. `pytest tests/test_nba_nfl_v5.py -o addopts='' -q` in < 0.5s).
- **Batch Before Verifying**: Complete all planned code modifications across all files before running any verification.

## PRE-CHANGE CHECKLIST
1. State the theory. 2. State the evidence. 3. State the test.
4. State the failure mode. 5. State the rollback.

## CHANGE WORKFLOW
Diagnose → Implement → Fast Lint (`ruff check` <0.2s) → Targeted Test (ONLY if core math changed) → Commit

## RULES
- Walk-forward only. Locked holdout. Never peek.
- Never hardcode weights or confidence thresholds. Load them from hash-verified artifacts.
- A monthly gate binds only for a complete calendar month with at least 10 called picks. Keep partial and insufficient months visible but non-binding.
- Protected: NBA model, WNBA model, `data/historical/*`, and every existing file in `config/models/*`.
- Improvements are new versions alongside old versions; never overwrite or delete rollback artifacts.
- Reject retrospective features that cannot prove they were observable before event start.
- Do not invent spread, total, F5, or YRFI/NRFI contracts when exact historical lines are absent.
- Shadow calls are not orders. Never log or execute during model validation.
- Codebase-structure questions go to the `/graphify` skill first (graph at `graphify-out/graph.json` in the repo root) instead of re-reading files.

## SPORTS-MODEL RESEARCH & PHASE F EXECUTION
For sports-model research, read:

1. `docs/PHASE_F_EXECUTION_PROTOCOL.md`
2. `config/research/phase_f_state.yaml`
3. `docs/ROADMAP.md`
4. latest Phase F experiment manifest/report

before making research changes.

Execute the next eligible state-machine action without requesting confirmation for ordinary research work.

Frozen artifacts, preregistrations, production registrations, locked confirmation data, or promotion gates must not be modified merely to obtain a passing result.
