---
phase: quick-261115-0pq
plan: 01
subsystem: ncaaf/cfb-structural-v6
tags: [ncaaf, cfb-structural-v6, history-backfill, pinned-rule, known-team-gate, verify-frozen]
requires: [quick-261114-srb]
provides:
  - scripts/cfb_v5_history_impact.py with the acceptance rule pinned as a constant and committed before the first run
  - v5 history-impact verdict REJECT (outputs/ncaaf/cfb_v5_history_impact.{json,md})
  - v6 known-team gate (TeamResolver.is_known, checked in project())
  - checkout-proof verify-frozen (CRLF-normalised lib hash) and a parity newly_declined report
affects: [NCAAF research; docs/DEBUG.md; docs/ROADMAP.md]
tech-stack:
  added: []
  patterns: [rule pinned and committed before the measurement, two-arm point-in-time replay, mutation-proven tests]
key-files:
  created:
    - scripts/cfb_v5_history_impact.py
    - outputs/ncaaf/cfb_v5_history_impact.json
    - outputs/ncaaf/cfb_v5_history_impact.md
  modified:
    - scripts/cfb_v6_research.py
    - src/model_prediction/features/cfb_features_v6.py
    - tests/test_cfb_structural_v6.py
    - docs/DEBUG.md
    - docs/ROADMAP.md
    - .planning/STATE.md
decisions:
  - "D-D1: the frozen-fit file is not re-pinned (the lib pin is inside the params hash); verify-frozen hashes the lib after normalising to CRLF"
  - "D-A1, D-A2, D-B1, D-B2: conditional on ACCEPT; the verdict was REJECT so the ingest change and the live append were not made"
  - "D-C1: the known-team gate sits only in project(); the solve, RatingBook._key and scripts/cfb_v6_lib.py are untouched"
status: complete
plan_head_before: 47a787e12ca1af56c68b73bf3fc7dac5b6f8efa3
plan_head_after: 3b9728fc542e6d3c59a5e2aad2ca3cc35e8f72fc
commits: 3
actuals:
  tokens: 40000
  tasks: 3
  commits: 3
metrics:
  completed: 2026-11-15
---

# Phase quick-261115-0pq Plan 01: v6 open items Summary

The known-team gate and the checkout-proof lib pin shipped; the v5 live-history backfill and the NCAAF groups 80/81 ingest were
measured by a pre-pinned rule and REJECTED (holdout Brier delta +0.001065 against a +0.001 limit), so the live history files are untouched.

## Commits (own hashes; `commits:` above counts the three code/output commits made before this docs commit)

| Hash | Content |
|------|---------|
| 403df5f3 | scripts/cfb_v5_history_impact.py with RULE and RULE_SHA256, committed before its first run |
| fbe7cb6c | scripts/cfb_v6_research.py (lib_sha256_crlf, parity newly_declined), impact report json and md |
| 3b9728fc | cfb_features_v6.py known-team gate, three tests in tests/test_cfb_structural_v6.py |
| (docs commit) | DEBUG.md entry, ROADMAP.md open items, STATE.md row, this SUMMARY and the PLAN file |

`plan_head_before` is 47a787e1 (HEAD at plan start); no plan-head ledger file was written, and `rev-list --count` on this shared branch
can include peer commits, so the three hashes above are the authoritative list.

## Pinned impact check (B, measure)

Rule `cfb-v5-history-impact-rule-1`, sha256 `f780ad86a2b3ab378382d873cdbd02455b5b5900c4c46e721726d3c6c2f5a236`, blob `6010d8af8285`
identical at the pre-run commit, in the report and in the working file. Arm (i) reproduced the recorded v5 numbers (n 1,527,
Brier 0.18933, margin log score -4.21265, total log score -4.21155) and the 575/576 reference gate passed every row.

| holdout (1,527) | arm (i) live | arm (ii) live + append | delta | 95% CI |
|---|---|---|---|---|
| Brier | 0.18933 | 0.19040 | +0.001065 | [-0.000815, +0.002998] |
| margin log score | -4.21265 | -4.20970 | +0.002951 | [-0.004547, +0.010515] |
| total log score | -4.21155 | -4.20731 | +0.004240 | [-0.001014, +0.009189] |

Verdict REJECT: the Brier delta exceeds the pinned +0.001 limit by 0.000065 (the two log-score checks passed). Append set 1,478 events
(FBS-FCS 1,101, FBS-FBS 376, FCS-FCS 1), sha256 `7d0f9caf...`. The plan expected about 1,513 because the live files grew from 7,965 to
8,014 rows before the run; 35 of those 49 new rows were FBS-FBS backfill ids.

## Live history

Both live files stayed at 8,014 rows, 5,510,866 bytes, sha256 `a934dd88dc035d800eb2d4fba8211e470d1fb92fa24310bf2b9fceb1f3cc230f` at the
measurement and at the final check. No backup was needed; `scripts/cfb_backfill_live_history.py` and the espn.py, ingest.py and
tests/test_ingest.py edits were not made (A depends on B per D-B1).

## C and D

- C: `TeamResolver.is_known` plus a check in `project()` before `book.identity`. Parity over 14,822 cached rows: 14,639 projections, max abs difference 0.0, 183 of 183 cached no-calls matching, 0 newly declined; frozen params sha256 `6e38b14c...` unchanged. Against the real resolver an invented name is not known.
- D: verify-frozen passes on the CRLF working file and on the LF blob (`git show HEAD:scripts/cfb_v6_lib.py`); a one-byte edit fails; the frozen-fit file is byte-identical to HEAD; market_check `--check-only`, residual and pool_audit return the frozen sha256.

## Verification

- Prove-it-fails: three mutations of cfb_features_v6.py (gate deleted; gate also declining pooled identities; `resolve_team` fallback dropped), each failed exactly one test and the file sha256 matched after restore.
- `ruff check src/ tests/` clean. `tests/test_ingest.py` plus `tests/test_cfb_structural_v6.py`: 32 passed, also in a detached worktree at `C:/wt0pq` on commit 3b9728fc (`model_prediction.__file__` inside the worktree), where verify-frozen also passed on the CRLF checkout. The worktree was removed.
- runs.db gate: the 05:30Z daily finished at 05:59:08Z; the first src edit was after 06:02Z with zero `started` daily rows. The gate was read before the src edit batch and again before the mutation runs, not before every individual edit.

## Deviations from Plan

1. **[Rule 4 outcome, by the plan's own rule] A and B not shipped.** The pinned rule returned REJECT, so the plan's conditional steps (apply script, ingest change, A tests) were skipped by design.
2. **[D-D1] Frozen-fit file not re-pinned**, as planned: the lib pin is inside the params hash.
3. **Disclosures.** `append-set` was run once (count and sha256 only, no metrics) before commit 403df5f3. The C tests were written after the gate, not RED-first, and each is proven by mutation. The LF proof is the `git show` blob test; the detached worktree checkout is CRLF. The first heredoc attempt to write the docs failed on a shell quoting error before any change; the docs were then edited with the Edit tool.

## Known Stubs

None.

## Open items

- Re-opening groups 80/81 and the backfill needs a v5 refit on the complete history or an operator waiver of the rule.
- FCS-vs-FCS live-history append (about 6,900 events) is an operator decision, outside the measured scope.
- The four pre-existing non-verifying artifacts (`nba-moneyline-v8`, `nba-spread-margin-v8`, `nba-total-margin-v8`, `tennis-games-markov-v3`) are untouched.

No promotion. v6 is not routed; v5 parameters and gates unchanged.

## Self-Check: PASSED

Hashes 403df5f3, fbe7cb6c and 3b9728fc are ancestors of HEAD; the impact script, report, gate and tests exist at HEAD.
