# NCAAF and NCAAB: next research generation

## Decision

Further improvement is plausible, especially for NCAAB totals. There is **no
demonstrated executable edge** for either new model. Keep current champions,
eligibility, and sizing until timestamped common-contract evidence exists.

## What the current evidence actually says

1. NCAAF legacy v1 QUALIFIED lost 91.35 units while PAPER_CALL_LOW_EDGE gained
   20.50 units. QUALIFIED probability errors were large, especially on
   moneylines and spreads. Those cohorts have no executable quote lineage;
   reversing the gate based on this split would be post-outcome selection.
   Current v4 has no hash-backed settled quote sample, so it cannot yet be
   ranked against the market or older versions at a common timestamp.
2. NCAAB v3 uses score trends **and Elo**. Its v3 home margin includes a 7.2
   point home-court parameter inside the trend component; the Elo component
   also has its own home advantage. These are mixed by the model, so the
   effective home adjustment needs a direct ablation, with neutral-site
   identification, before any further hand correction.
3. The 2025–26 possession challenger fitted to early season games improves
   total MAE on February 2–March 4 (1,448 games, 13.94 to 12.94) and March 5
   onward (525 games, 14.04 to 12.88). Date-clustered bootstrap intervals for
   the paired MAE gains are 0.75–1.28 and 0.75–1.52 points respectively.
   Margin MAE worsens on both later periods. The gain is a single historical
   season's retrospective signal; it is not a qualified live model or price
   edge. April has only 13 games and its total gain is negative.
4. The previous report's probability scores were labeled too strongly. They
   compare **normal-CDF margin proxies**. Serving v3 blends Elo and margin
   probabilities, so those Brier and log-loss values are not serving v3
   moneyline scores. The prior script and report now label them correctly.
5. NCAAB forecast selection currently uses `price_probability` from the live
   market summary and explicitly labels it indicative. That price can enter
   Main eligibility. Pre-game BBO is now captured separately but is not joined
   to the decision by exact contract and timestamp. Until it is, Main P&L is
   a paper diagnostic, not evidence of an executable edge.
6. NCAAF v4 treated a valid `0.0` home spread as absent and substituted −3.5.
   A focused regression test and source fix now preserve pick'em. No v4
   spread ledger row had line zero at this audit, so observed historical P&L
   is unaffected. Missing-line defaults still need a separate fail-closed
   contract audit before any promotion claim.

## External methods worth adopting, with limits

| Source | Useful method | What it does not establish |
|---|---|---|
| [Ken Pomeroy ratings glossary](https://kenpom.com/blog/ratings-glossary/) and [team-page explanation](https://kenpom.com/blog/help-with-team-page/) | Estimate possessions from FGA, offensive rebounds, turnovers and free throws; estimate opponent-adjusted offense, defense and tempo. | Copying the components does not reproduce proprietary ratings or guarantee an edge over a priced market. |
| [Ken Pomeroy preseason discussion](https://kenpom.com/blog/a-quick-note-about-the-influence-of-preseason-ratings/) | Carry a decaying preseason prior through the small-sample opening weeks. | A prior derived from final-season ratings would leak future results; fit from previous seasons and timestamped roster inputs only. |
| [NCAA basketball home-court study](https://arxiv.org/abs/1909.04817) | Separate home, away and neutral-site effects; venue affects box-score performance. | The paper's historical home effect is not a plug-in value for the present market. |
| [sportsdataverse MBB team-box documentation](https://github.com/sportsdataverse/hoopR-mbb-data/blob/main/docs/datasets/team_box.md) | Obtain game-level box-score components with ESPN game IDs. | A season release lacks a provable first-seen time for each historical row. |
| [sportsdataverse CFB dataset documentation](https://github.com/sportsdataverse/cfbfastR-cfb-data/blob/main/DATASETS.md) and [CFBD availability](https://api.collegefootballdata.com/data-availability) | Research prior-game EPA, success rate, pass/rush splits, explosive plays and drives. | Season-end opponent-adjusted tables cannot be inserted into earlier-week forecasts; derive rolling ratings from prior games. |
| [scikit-learn scoring documentation](https://scikit-learn.org/stable/modules/model_evaluation.html) | Compare actual probability vectors with proper Brier and log loss, plus reliability and resolution. | A lower Brier score alone is not proof of calibration or profitability. |

## Prioritized experiments

### 1. Repair the evidence join (highest priority)

At every forecast timestamp, save one immutable record per listed exact
contract: event and team IDs, rules/horizon, side, exact line, both asks and
depth, observed/effective times, market status, feature packet hash, model
revision/artifact hash, and all candidate probabilities. Join the already
captured NCAAB BBO into decision research by snapshot record ID, not by a
later price lookup. Capture NCAAF v4 and frozen challenger outputs on the
same contracts. Keep shadow calls distinct from orders.

**Gate:** At least 95% of evaluated contracts must resolve to one unambiguous
pre-game, two-sided quote; report missingness by date and market. Treat this
95% as a proposed research coverage requirement, not a live betting rule.

### 2. NCAAB total-only challenger

Build a separate total head with previous-game adjusted offense, defense,
pace, and explicit home/neutral venue. Seed opening weeks from prior-season
ratings with uncertainty that shrinks as current-season possessions accrue.
Compare against v3 on multiple later-date blocks and, after the model is
frozen, fresh 2026–27 calls. Keep margin and moneyline v3 until they win their
own tests. Test total MAE and distribution calibration; compare over/under
probabilities at exact recorded lines and asks. The historical 1.0–1.16 point
MAE lead is the theory to replicate, not an approved stake increase.

### 3. NCAAF calibration before another feature overhaul

Reproduce probabilities for *every listed side* on a common quote set,
including no-call contracts. Check reliability by market, favorite/dog,
line band, season week, and model version. Fit any calibration map on past
dates only. Against the contemporaneous market, test incremental Brier/log
loss and whether model-minus-market residual predicts outcomes after
conditioning on price. Only then compare a frozen eligibility rule and fixed
stake to the current rule on later contracts. Do not infer a new rule from
the legacy QUALIFIED/LOW_EDGE sign reversal.

### 4. NCAAF structural inputs, only after the quote comparison works

Use rolling prior-game opponent-adjusted EPA/play, success rate, explosive
rate, drive efficiency and pass/rush splits as small ablations. Version every
source and release hash and exclude game-final or season-final aggregates
that were unavailable at kickoff. Check quarterback availability from
timestamped reports; otherwise retain the missingness indicator and do not
impute a retrospective starter. Test each addition against the same market
and previous model on the same dates and exact contracts.

## Promotion protocol

Freeze each candidate and its preprocessing before a new prospective block.
Report all scheduled games, all listed contracts, matched quote coverage,
unselected sides, and missingness. Compare accuracy, proper probability
scores, calibration, and fixed-stake quote-aware P&L separately. Use
date-clustered paired intervals and report every attempted candidate to
limit selection bias. Promote a head only if the later-date and fresh
prospective comparison beat v3/v4 and market baselines on its own contract
set. No historical re-splitting of the inspected March–April 2026 block can
restore its status as an untouched holdout.
