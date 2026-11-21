---
phase: quick-261121-spt
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - scripts/cfb_stale_price_test.py
  - outputs/ncaaf/cfb_stale_price_test.json
  - outputs/ncaaf/cfb_stale_price_test.md
  - .planning/quick/261121-spt-ncaaf-stale-price-test/261121-spt-PLAN.md
  - .planning/quick/261121-spt-ncaaf-stale-price-test/261121-spt-SUMMARY.md
autonomous: true
requirements:
  - QUICK-261121-spt
---

# quick-261121-spt: NCAAF stale-price test (overhaul plan step 1)

Source: `docs/ROADMAP.md`, College Football item, "Overhaul plan (2026-11-20 review ...)", step 1.

## Question

Do 2026 Polymarket NCAAF decision-time asks lag the sportsbook line enough to profit, net of the ask?
Model-free: only the Polymarket rung at the anchor line is traded, so the answer does not depend on any
margin distribution.

## Pre-registration

The `RULE` dict in `scripts/cfb_stale_price_test.py` and its canonical-JSON sha256 (`rule-sha`
subcommand) are committed BEFORE the graded `run`. `run` refuses unless the script is committed and
unmodified at HEAD and the outputs do not exist yet. A `dry-run` on seeded fake outcomes (scores
removed before matching) exercised the selection, P&L, bootstrap and report path before the commit.

Pinned (see `RULE` for the exact text):

- Inputs: Polymarket NCAAF snapshots (runtime root, all dates, plus the repo's 2026-11-04 file), ESPN 2026
  closes (`data/research/ncaaf_v6/espn_lines.jsonl`), `data/processed/ncaaf/games.jsonl`, the DraftKings
  forward capture. Data cutoff 2026-11-21T04:00:00Z; input sha256 recorded at run time.
- Full-game rungs only, by slug regex (`asc-...-(pos|neg)-NptM`, `tsc-...-total-NptM`); half, quarter
  and team-total markets excluded.
- Side mapping: spread `long` = the title's first team at `line`; `team` is an assertion (contradiction
  excludes the row); total `long` = Over. Outcome-free proof: the latest pre-kickoff midpoint at the
  ESPN-close rung has quartiles 48.5% / 49.75% / 51.25% (spreads, 249 events) and 48.5% / 49.5% / 51.5%
  (totals, 289 events), none more than 15 points from 50%.
- Rows: OPEN, `timestamp_valid`, `usage` prospective_executable_bbo, not reconstructed, observed strictly
  before kickoff = min(Polymarket start, ESPN start).
- Anchors: ESPN consensus close (primary, UPPER BOUND, verdict arm); DraftKings as of the quote time
  (latest non-live snapshot <= quote, <= 6 h old; secondary, descriptive, no verdict).
- Selection: half-point anchors only (integer anchors are no pick: every Polymarket rung is x.5); the rung
  at the anchor line; buy a side with 0 < ask <= 48c and ask size >= 1 contract; earliest qualifying
  snapshot per (ESPN event, market); both sides qualifying -> lower ask.
- Grading: final scores in `games.jsonl`; P&L per 1U at the ask; fee 0.
- Statistics: date-clustered (ET kickoff date) bootstrap, 2000 resamples, seed 20261121, percentile CI for
  ROI, hit rate and hit minus break-even.
- Verdict (ESPN arm, spread and total pooled): ROI CI upper <= 0 -> PREMISE_DEAD; lower > 0 ->
  PREMISE_SUPPORTED_UPPER_BOUND; else INCONCLUSIVE; fewer than 30 picks or 5 clusters -> INCONCLUSIVE.

## Tasks

1. Write the script, `ruff check`, dry-run on fake outcomes, commit script + this plan (rule sha in the
   commit message).
2. `python scripts/cfb_stale_price_test.py run` once; commit `outputs/ncaaf/cfb_stale_price_test.{json,md}`.
3. Write `261121-spt-SUMMARY.md`; commit.

Out of scope: `src/`, `config/`, ledgers, `docs/ROADMAP.md`, `docs/DEBUG.md`, `.planning/STATE.md` (the
parent session records the result). No pytest.
