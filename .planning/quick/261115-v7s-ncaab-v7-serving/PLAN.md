# 261115-v7s: make the NCAAB v7 multi-season margin model servable (off by default)

Scope: NCAAB only. No change to config/production.yaml, champions, auto_buyer_state.json, config/model.yaml,
the live ledger, or any other sport. No settle/forecast run against the live ledger.

## Problem
v7's gain (RMSE 11.50 vs 11.81 on 2024-25 D1-vs-D1; mismatch-cohort bias +4.97 -> +0.68) needs three seasons of
history and per-season ESPN conference ids. The live path (`FeatureStore.games_before("ncaab")`) only holds
2025-26 onward, and the research scoreboard is untracked.

## Plan
1. Frozen archive `data/archive/ncaab_v7_prior_seasons.jsonl.gz` (data/historical is gitignored, data/archive is
   tracked) built deterministically by `scripts/build_ncaab_v7_serving_archive.py` from the research scoreboard:
   season_year <= 2025 games with scores, neutral flag, ranks and box aggregates (what `NCAABScoreboardStats`
   reads), plus every season's team -> ESPN conference id map. sha256-verified on load.
2. `models/ncaab_v7_serving.py`: merge archive + live history, cut at midnight ET of the target day, run the
   research `NCAABV7MultiSeasonModel.components` (imported, not forked), apply the frozen F1 combiner and bucket
   scales. D1-vs-D1 only; everything else is left to v6.
3. Hook in `NCAABSpreadModelV6` behind `margin_engine` (default "v6"); wired from `models.NCAAB.margin_engine` in
   `college_basketball_forward.py`. v7 replaces expected margin and scale; rest/density and availability terms
   are OFF on that path because v7 was fitted without them. Failure -> v6 + warning.
4. Tests: combiner and frozen-JSON parity, serving == research output (tolerance 1e-6), PIT, fallback,
   default-off identical, existing NCAAB suites.
