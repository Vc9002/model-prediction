# 261121-xaw — Settlement non-binary void and supervisor exit-75 fixes

Worktree `E:/MP-wt/fix-2026-11-21`, branch `fix/settle-supervisor-2026-11-21`, base `387c0489`.
Operator request: fix both items from the 2026-11-21 project review. Do not push. Do not merge
(the orchestrator lands it after review). Never touch `E:/MP`. Do NOT run `git stash`,
`git checkout <sha> -- .`, `git reset --hard` or `git clean`.

## Fix 1: `_settle_by_resolution` must not void on a non-binary terminal price

File: `src/model_prediction/cli/settle.py` (`_settle_by_resolution`, ~line 1123-1170).

AGENTS.md checklist
1. Theory: Polymarket US returns pre-close trading prices on decided markets, so non-binary prices are not
   a forfeit/postponement signal. The esports path learned this (about 260 picks mis-voided; see the comment at
   ~line 1346 and `tests/test_fix_regressions.py:270`). The generic fallback still voids on that signal and
   records a PUSH with 0 pnl, silently.
2. Evidence: read-only query of `E:/model-prediction-runtime/ledgers/ledgers.db` found 207 settled-push rows
   whose payload says "non-binary": cs2 ~198, dota2 7, lol 1, darts 1. None are in-scope sports. The
   generic fallback is reached from settle.py lines ~142, 171, 205, 230, 285.
3. Test: new regression tests in `tests/test_fix_regressions.py` (or the file that already tests
   `_settle_by_resolution`; grep first). Cases: (a) terminal book state HALTED or CLOSED with prices 0.41/0.59 ->
   pick stays open (ledger row unchanged, status not settled/voided) and the return value carries a reason;
   (b) clean 1.0/0.0 still settles win/loss exactly as before; (c) not-terminal -> None as before.
   Show (a) FAILS with the fix reverted, then restore the fix.
4. Failure mode: a market that is genuinely void stays pending instead of closing as a push. That is visible
   and reversible, and it is the safe direction; a wrong void silently books 0 pnl. Do NOT add any age-based
   auto-void, and do not re-grade past voids (operator decision; report only).
5. Rollback: `git revert` the commit.

Design (follow unless the code shows it is wrong; if so, stop and report):
- In `_settle_by_resolution`, when terminal and prices are not exactly `[0.0, 1.0]`, do NOT call `ledger.void`.
  Log at warning with the slug and prices and return `{"pick_id": row["pick_id"], "reason": "..."}` — the same
  shape the function already returns on errors. First read how EVERY caller (lines ~142-290) treats a returned
  dict with `reason` and no `settled`/`voided` (it must count as unsettled and show in the settle report, not
  as success). If a caller mishandles it, fix that caller minimally and test it.
- Do NOT edit the shared `_TERMINAL_MARKET_STATES` or `_market_is_terminal` (other users).
- The esports path (`_settle_esports_pick`) and `portfolio/auto_buyer_ledger.py:~2173` (which also records a
  push on non-binary outcomes) are OUT OF SCOPE: do not change them; mention the auto-buyer site in the
  summary as an open item.
- If an existing test pins void-on-non-binary for the generic path, STOP editing that test: report the
  conflict and the test name in the summary and in your final message, and change it only to assert the new
  behavior with a comment saying why. Never skip or delete a test.

## Fix 2: `run_supervisor` must not record child exit 75 as `failed`

Files: `src/model_prediction/run_supervisor.py` (~line 375-392, where status is set from `exit_code`) and
`src/model_prediction/system_health.py` (~line 515-535).

AGENTS.md checklist
1. Theory: CLAUDE.md says exit 75 is a coalesced lock skip, never a failure. `daily_lock.py` returns 75 after
   its wait; the supervisor records any non-zero exit as `failed`, and `system_health` then reports the worker DOWN.
2. Evidence: `run_supervisor.py` `status="completed" if exit_code == 0 else "failed"`; `dashboard/jobs.py:51`
   already treats 75 as a skip; `_RUN_STATUSES` already includes `skipped`.
3. Test: add to `tests/test_run_supervisor.py` (read its fixtures first): a worker command that exits 75 is
   recorded `skipped` with `exit_code == 75` and a note, `run()` still RETURNS 75; exit 1 is still `failed`;
   exit 0 still `completed`; a timeout-killed run is still `failed` (the 75 mapping applies only when no
   timeout kill happened, i.e. `note is None`). Show the 75 case fails with the fix reverted.
   Add a `system_health` test: a latest row skipped with exit_code 75 is NOT reported down.
4. Failure mode: a persistently held daily lock now shows as degraded (skipped rows already degrade) instead of
   DOWN. Keep degrading on skipped rows. In `system_health`, the "(lease held)" text is hard-coded for every
   skipped row: use the row's note instead so a 75 skip is not mislabeled.
5. Rollback: `git revert`.
- Grep every other reader of `status == "failed"` / `"failed"` in `src/` and `scripts/` that reads the `runs`
  table, and list them in the summary (change one only if it would now misreport a 75 skip).

## Verification (project rules: never the full suite)
- Unset `MODEL_PREDICTION_RUNTIME_ROOT` and `MODEL_PREDICTION_LEDGER_AUTHORITY` for every pytest run, and
  confirm `model_prediction.__file__` is under `E:/MP-wt/fix-2026-11-21` (use `PYTHONPATH=src` and print it).
- Baseline BEFORE editing: `python -m pytest tests/test_run_supervisor.py tests/test_fix_regressions.py -o addopts='' -q`
  plus every other test file that imports `settle` or `run_supervisor` and `system_health`
  (grep tests/ for `_settle_by_resolution`, `run_supervisor`, `system_health`). Record results; failures that
  exist before are pre-existing, report them, never weaken tests. One pytest process at a time.
- After: rerun the same files; `ruff check src/ tests/`; `ruff format --check` on touched files.
- Commit each fix separately (code + its tests only), message `fix(quick-261121-xaw): ...`, end with
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. No .planning files in code commits.
