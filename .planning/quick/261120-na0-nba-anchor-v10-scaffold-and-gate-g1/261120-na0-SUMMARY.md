---
phase: quick-261120-na0
plan: 01
subsystem: nba-research
tags: [nba, v10, anchor, gate-g1]
status: complete
requirements: [QUICK-261120-na0]
---

# nba-anchor-v10 scaffold and G1 (Summary)

- `models/nba_anchor_v10.py`: `price_game` / `price_market` (always a side; coin-flip side from
  the raw model direction; model-only fallback without an anchor), anchor sources
  (`ClosingLineAnchorSource` benchmark-only, `CapturedAnchorSource` PIT-filtered,
  `FallbackAnchorSource`), `AnchorParams.from_artifact`, taker fee net edge.
- `nba_replay_harness.gate_g1` + `replay_harness.py g1` -> `G1.md` and
  `config/models/research/nba-anchor-v10.json` (self-verifying hash).
- G1 (validation n = 762, exploratory): FAIL for v9 and v9.1 in both markets. Train lambda
  v9 spread -0.048 [-0.180, +0.092], total +0.029 [-0.079, +0.136]; v9.1 spread -0.048
  [-0.180, +0.092], total +0.063 [-0.089, +0.205]. Validation corr all within +-0.03 with CIs
  spanning 0. Artifact ships lambda = 0, main_min_edge null.
- Wiring spec: `outputs/nba/v10_harness/ANCHOR_WIRING.md` (not wired).

## Follow-up (review fixes)

- Main eligibility no longer requires lambda > 0: it depends only on net edge vs the artifact's
  `main_min_edge` (still null) with a servable anchor, so a consensus-vs-Polymarket gap (T-B)
  can surface at lambda 0. New test pins it.
- v9.1 research artifacts are now written by `replay_harness.py v91-artifacts` with
  market-correct metadata (total: `total_normal`, `over`, total formula) and an engine-parity
  check against the replayed config; hashes re-signed.
- Checked: the served v9 wrappers (`NBATotalModelV9` from `nba-total-margin-v9.json`,
  `NBASpreadModelV9` from the spread artifact) reproduce the golden rows (64 predictions over 7
  dates, 0 mismatches), so the harness "v9" totals are the served total model.
- Wiring spec now covers v9.1 (two engines, `season_types` for B7) and the no-contract case.
