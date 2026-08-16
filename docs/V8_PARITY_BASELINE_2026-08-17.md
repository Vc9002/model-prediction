# v8 Row-Parity Baseline (2026-08-17) — measured pre-gate state

Run on the synced research branch (`b7b59ca`, merged from main `9d99221`)
with main's venv, read-only against live data. Tooling:
`scripts/mlb_v8_row_parity.py` + `scripts/mlb_v8_feature_parity_sample.py`
(existing on the branch; both ran clean post-sync). Outputs land in
`outputs/research/mlb_v8_parity/` (gitignored).

## What passes today

| Check | Result |
|---|---|
| B train boundary | 3814 / 3814 exact |
| B validation boundary | 1082 / 1082 exact |
| C feature ordering | matches artifact |
| J missing-feature behavior | 0 rows with NaN feature |
| **Row probability parity** | **1389 rows, max |Δ| = 0.0006148** (shipped coefs applied verbatim) |
| Call-set identity | 148 calls at pinned threshold vs 148 recorded — exact |
| Aggregate replay (pin-and-replay) | call_ratio 1.0135, hit_delta 0.0052, `reproduced_closely=True` (150 vs 148 calls — dataset growth, within bands) |

Row probability parity sits far inside the plan's proposed bar
(|Δ| < 0.005 on ≥98% rows).

## What does NOT reproduce exactly — classified causes

| Class | Measurement | Cause (classified) |
|---|---|---|
| A cohort | 31 backfill rows correctly excluded; **2 freeze-time rows missing** from today's games file; unidentifiable | v8's build never snapshotted the holdout event-id list — a v8 packaging gap, not a harness defect. Fixing retroactively is impossible without guessing; policy decision required (see below). |
| F starter ERA | 2/36 exact in the 40-game feature sample (4 games no-call) | Two compounding causes: (1) the 2026-08-16 accent-folding fix now matches starter names that failed at v8's decision time (José Soriano/Jesús Luzardo class), changing rolling windows; (2) `game_snapshots.jsonl` lineage — historical reconstructions updated after freeze. Both are the §0.6 F-bucket question made concrete. |
| I weather | max delta 0.0269, 9/40 exact | Post-freeze weather re-fetches changed the frozen input; v8 stored no weather snapshots. Bounded, downstream impact absorbed by the LR (probability parity holds). |
| coefficients (refit) | max |Δ| 0.0107 vs shipped | Diagnostic only: the harness REFITS on today's reconstructed train rows; train-window Elo/trend/park/weather values shifted with backfills. The probability replay uses SHIPPED coefficients, so this does not affect the gate. Exact coefficient reproduction requires the freeze-time dataset, which v8 never snapshotted. |
| L orientation | `positive_class='home'` recorded; `learned_forward` never reads it | Convention (home-win probability) currently consistent across every shipped artifact, but the field is inert at serving time — a contract gap, not a current mis-orientation. |

## Operator decisions required BEFORE the Phase-1 gate run (2026-08-18)

These must be recorded in the experiment registry before the gate result
can be interpreted — per `docs/V9_RESEARCH_PLAN.md` §0.6:

1. **F-bucket policy** — historical (buggy) matching as the control, or
   fixed matching? Default recommendation: historical is the control;
   fixed matching is a classified, excluded deviation.
2. **A-bucket 2-row policy** — the two unidentifiable freeze-time rows:
   exclude with a documented note (default recommendation), or treat the
   gate as FAIL? Exclusion keeps the probability-parity bar meaningful;
   the rows are unidentifiable, not contradictory.
3. **Acceptance-criteria revision** — the plan's criterion "zero feature
   deltas" is already measured-unmet (F, I classes) while probability
   parity and call-set identity hold. Proposed revision: feature deltas
   are acceptable when (a) every row carries a taxonomy class + cause,
   (b) no `unclassified` rows, (c) row-probability |Δ| < 0.005 on ≥98%
   of rows, (d) call-set identity exact.

No candidate selection, promotion, or live-system change was made by
this work; it is research-prep per the burn-in boundary.

## Known branch issue (2026-08-17, post-sync)

`tests/rebuild/test_rebuild_shadow_cli.py::test_direct_run_rejects_repo_production_root_before_opening_ledger`
FAILS in this worktree (passes on main: 1879 green there). The test
expects `RebuildSafetyError` before a rebuild run opens a ledger at
`data/main/rebuild_must_not_create`; in the worktree checkout it does
not raise. Not caused by the A1/A3 work (calibration decomposition +
air-density module — untouched paths); appears to be a checkout-location
interaction in the rebuild safety path resolution. Does not affect the
v8 row-parity gate (no rebuild paths involved), but MUST be fixed
before any rebuild-shadow work runs from this branch. Status: open,
unowned.
