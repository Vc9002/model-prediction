# College Overhaul 2026-11-04 (NCAAF/NCAAB)

> Consolidated from: NCAAF_NCAAB_RESEARCH_2026-11-04.md, NCAAF_NCAAB_DEEP_RESEARCH_2026-11-04.md, COLLEGE_MODEL_RESEARCH_2026-11-04.md, COLLEGE_CONTRACT_QUOTE_JOIN_2026-11-04.md, COLLEGE_OVERHAUL_NEXT_STEPS_2026-11-04.md. Archival merge — all content preserved, section order follows original filename/topic order.

---

## Part 1 — NCAAF/NCAAB Research (source: NCAAF_NCAAB_RESEARCH_2026-11-04.md)

# NCAAF and NCAAB research, 2026-11-04

## NCAAF: market evidence and legacy gate

The settled Main ledger has 1,014 NCAAF calls for −58.41 units. The quote audit
verifies content hashes, contract identity, open status, pregame timing, two
asks, and recorded selection price for all 51 calls with a snapshot hash. None
of the 830 legacy v1 calls has executable quote lineage. The current v4 has 19
settled Main calls and none has a snapshot hash. A simultaneous, timestamped
v1–v4 comparison on common contracts is therefore **not identifiable** from
the archive. The 51 verified calls span v2 (5) and v3 (46), on different
dates and selections. Do not rank versions using their pooled P&L.

Within v1, 486 QUALIFIED calls lost 91.35 units, while 344 PAPER_CALL_LOW_EDGE
calls gained 20.50 units. QUALIFIED moneyline: 83 calls, 32.5% wins against
66.5% mean model probability, −48.34 units. QUALIFIED spread: 242 calls (239
non-push), 48.1% wins against 76.5% mean model probability, −36.65 units.
QUALIFIED total: 161 calls, 50.3% wins against 60.8% mean model probability,
−6.36 units. The indicative recorded prices predicted outcomes better by
Brier score in all three markets. This is strong evidence of legacy
miscalibration or selection defects, not evidence that reversing the gate
would work. The winning low-edge moneylines were largely favorites with a
mean indicative ask of 83.9%, so their win rate alone does not prove edge.

Keep sizing unchanged. Collect *same-contract, same-time* pregame BBO and
shadow probabilities from each candidate version, with no orders. Require
quote validity, calibration, Brier/log loss, and later-date net results before
revising eligibility or stake rules.

Reproduction: `python scripts/audit_ncaaf_market_calibration.py`; detailed
output: `outputs/research/ncaaf_market_calibration_2026-11-04.json`.

## NCAAB: data and possession challenger

First-seen hashed box-score capture covers all 210 completed November 2–3
games (182 + 28). The live forecast now stores a hashed feature packet and
serves v3 as recorded in `config/model.yaml` (prior commit `fd474e04`). The
November 4 BBO capture adds 45 timestamp-valid pregame, two-sided main-line
contracts across 26 events: 25 moneylines, 10 spreads, and 10 totals. Daily
slate capture now includes a bounded NCAAB main-line selection. Existing
November 2–3 quote coverage is sparse, and the live summary price remains
indicative; those settled calls cannot support an executable-edge claim.

Research challenger v4 uses estimated possessions (FGA − ORB + turnovers +
0.475 FTA), points per possession, 20-iteration opponent-adjusted offense and
defense, 12-game shrinkage, and pace. Features use only **previous dates**.
The source is the [sportsdataverse 2025–26 team box-score release](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_mens_college_basketball_team_boxscores),
asset `team_box_2026.parquet`, SHA-256
`db58650c1a9b2a1b6c8735507b8e922580ea514bb9f2ccbc1d91ae6588a27f42`.
It matches 6,280 of 6,281 v3 evaluation games. Individual box-score
first-seen times for that historical season are unknown, so this is
retrospective development evidence.

| Period | Games | Margin MAE v3 → v4 | Total MAE v3 → v4 | Margin-CDF proxy Brier v3 → v4 | Margin-CDF proxy log loss v3 → v4 |
|---|---:|---:|---:|---:|---:|
| Train, before Feb 2 | 4,307 | 13.84 → 11.86 | 14.61 → 13.70 | .193 → .158 | .574 → .481 |
| Validation, Feb 2–Mar 4 | 1,448 | 8.86 → 10.94 | 13.94 → 12.94 | .204 → .221 | .597 → .629 |
| Later, Mar 5 onward | 525 | 9.03 → 12.04 | 14.04 → 12.88 | .208 → .245 | .605 → .685 |

The probability scores in this table use a normal CDF of each projected
margin. Serving v3 blends Elo and margin probabilities, so these are **proxy
scores, not serving v3 moneyline Brier or log loss**. The challenger fails
later-date margin and proxy probability gates. The total head improves MAE
by 1.00 points on validation (date-clustered bootstrap 95% interval
0.75–1.28) and 1.16 on later dates (0.75–1.52). That is a retrospective,
single-season total-head lead, not an executable edge or sufficient reason
to promote the full model. V3 stays in service. No v4 bet selection or P&L
is reported as actionable because archival executable quote coverage is
insufficient.

Reproduction: download the pinned asset to `scratch/team_box_2026.parquet`,
verify its SHA-256, then run `python scripts/test_ncaab_possession_v4.py`.
Detailed output: `outputs/research/ncaab_possession_v4_2026-11-04.json`.

---

## Part 2 — NCAAF/NCAAB Deep Research (source: NCAAF_NCAAB_DEEP_RESEARCH_2026-11-04.md)

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

---

## Part 3 — College Model Research (source: COLLEGE_MODEL_RESEARCH_2026-11-04.md)

# College model audit and settled-slate replay — 2026-11-04

## Decision

Do not promote either probability challenger. The NCAAB margin calibration fails its
chronological validation and holdout checks. The NCAAF normal-variance replacement
worsens total Brier score on replayed settled contracts. The live models remain the
same versions; football price integrity and artifact lineage were corrected.

## External research considered

- [hoopR's college basketball ratings](https://github.com/sportsdataverse/hoopR-mbb-data/blob/main/docs/models/ratings.md)
  use opponent-adjusted offense, defense, and tempo. This supports investigating
  possession and schedule adjustment, but no unverified external rating was joined
  retrospectively into settled calls.
- [Walsh and Joshi's calibration study](https://arxiv.org/abs/2303.06021)
  argues that calibration matters more than classification accuracy for wagering.
  Accordingly this audit evaluates Brier and log loss alongside units.
- A [CFB practitioner thread](https://www.reddit.com/r/algobetting/comments/1ncrr6n/building_a_college_football_betting_model/)
  describes PPA, success rate, explosiveness, tempo, and market differences. The
  author later acknowledged a wrong target variable; the discussion is a warning
  to verify outcomes and prices, not evidence of a profitable transferable model.
- [CBB-predictor](https://github.com/coreyw7/CBB-predictor) combines Elo with
  adjusted efficiency and tempo and compares projections to closing lines. It is
  an implementation idea, not a source of point-in-time features for our ledger.

## Reproducible evidence

`scripts/replay_college_challengers.py` and
`scripts/compare_college_settled_full_slate.py` produced the paired replay and
full-slate report. The canonical SQLite ledger was queried read-only. The
full-slate policy considers every settled row and retains a recorded contract
and its original stake only if the research probability exceeds its recorded
ask. Rows without a reproducible probability retain the incumbent probability
and are counted as fallback. No opposite side or missing historical price is
invented.

| Sport / tier | Settled | Replayed | Fallback | Incumbent P&L | Research policy calls | Research policy P&L |
|---|---:|---:|---:|---:|---:|---:|
| NCAAB Flat | 418 | 418 | 0 | -29.5550u | 281 | -18.6772u |
| NCAAB Main | 367 | 367 | 0 | -26.0668u | 264 | -19.2154u |
| NCAAF Flat | 395 | 371 | 24 | +8.0296u | 233 | +15.4269u |
| NCAAF Main | 1,014 | 1,011 | 3 | -58.4141u | 800 | -66.2204u |

This is a **retrospective decision-layer diagnostic**, not a newly deployed
model's realized P&L. The policy abstains on some incumbent calls; the columns
are therefore not the same set of wagers. Flat and Main overlap. NCAAF Flat's
24 earliest rows have no pregame inference snapshot; the Main ledger contains
snapshots for all but three of its rows. Counts and market-level results are in
`outputs/research/college_full_slate_2026-11-04.json`.

The NCAAB research calibration improves Brier on the 418 settled Flat calls,
but margin MAE worsens on chronological validation (8.86 to 10.69) and holdout
(9.03 to 13.52). This is a failed generalization check. The NCAAF candidate
total Brier worsens from 0.26429 to 0.27258 across 117 replayed Flat totals.
Its apparent Flat units gain therefore does not justify serving it. All NCAAF
snapshot records mark historical source observation times unverified.

A second ridge ablation using Elo margin, trend margin, and their combination
also failed to beat the incumbent's later-date margin MAE (9.03). The best
trend-only candidate tested here reached 10.94 on those dates. Early-season
fit does not transfer cleanly into later conference/tournament play; a larger
coefficient or an Elo-only substitution is not a defensible repair.

## Serving repairs and next research priority

Football ESPN fallback markets now require an actual quote on both sides. A
missing spread, total, or moneyline side produces no priced contract. The
football logger records the exact serving artifact hash instead of a digest
of the model version string. The prior version remains in Git for rollback.

The next research priority is NCAAB moneyline and spread projection with
opponent-adjusted efficiency and a verified pregame feature store. NCAAB v3
already uses Elo, blending Elo win and margin signals with recent trends; Elo
alone is not the missing fix. NCAAF needs complete quote and feature snapshots
before an all-row challenger can be claimed. Any new fit must beat market
baseline calibration and loss on chronological data before promotion.

## Follow-up test: score-based schedule rating

`scripts/test_ncaab_schedule_rating.py` constructs an opponent-adjusted score
margin rating from prior complete UTC dates. The available processed games do
not contain possessions, so this is not adjusted offensive/defensive
efficiency. A fixed-point opponent adjustment is fit on 4,308 pre-February
games and combined with the incumbent margin using ridge regression. The
entire 418-call settled Flat slate is replayed at recorded lines and prices.

| NCAAB cohort | Incumbent margin MAE | Challenger margin MAE |
|---|---:|---:|
| Fit games, 4,308 | 13.85 | 12.50 |
| Chronological validation, 1,448 | **8.86** | 10.60 |
| Later games, 525 | **9.03** | 13.47 |

The settled-slate decision diagnostic rises from -29.56u across all 418 Flat
calls to +26.54u across 310 retained calls. Its moneyline Brier improves
0.1757 to 0.1023 and spread Brier 0.4345 to 0.2708. That result is tempting
but the fitted schedule-rating coefficient is **negative** (-0.158), and the
challenger fails both later chronological splits by large margins. This is an
unstable retrospective result, not a promotable improvement. Full numbers are
in `outputs/research/ncaab_schedule_rating_2026-11-04.json`.

The NCAAF variance challenger was also rescored on the Main ledger's 1,011
reconstructable settled calls. Spread Brier worsens 0.3247 to 0.3257 (361
nonpush calls) and total Brier worsens 0.2594 to 0.2668 (365 calls). Its
same-contract Main decision P&L is -66.22u versus the incumbent -58.41u over
all 1,014 settled rows. Three rows retain the incumbent probability because
their pregame inference snapshot is missing. This independently rejects the
football variance change.

---

## Part 4 — College Contract/Quote Join (source: COLLEGE_CONTRACT_QUOTE_JOIN_2026-11-04.md)

# College contract evidence and NCAAF calibration, 2026-11-04

## Theory, evidence, test, failure, rollback

The previous NCAAB decision path used indicative summary prices, and its
archived executable BBO was separate. NCAAF preserved quote hashes for some
selected calls but did not retain probabilities for rejected sides and all
alternate lines. A path split also made the football slate look for BBO under
the checkout while the actual archive was under the runtime root. The new
research packet records every normalized listed contract, both sides, exact
line probabilities, quote join or explicit missingness, model input hash and
content hash. It does not change NCAAB's indicative-price gate or stake rule.

The falsifiable tests are exact event/market ID, slug, line, predecision and
pregame times, open state, two-sided BBO, and content-addressed packet
integrity. A mismatch fails closed in the research join. The rollback is to
remove the `college_contract_evidence` capture and restore the prior slate
builder; existing ledgers and model artifacts are untouched.

## First prospective capture

| Sport and slate | Listed contracts | Model scored | Valid exact BBO joined | Both model and BBO | Primary missingness |
|---|---:|---:|---:|---:|---|
| NCAAB, Nov 4 | 543 | 543 | 44 (8.1%) | 44 | 499 not archived |
| NCAAF, Nov 5 | 135 | 134 | 7 (5.2%) | 6 | 128 alternate lines not archived; one FCS moneyline has no matched ESPN model game |

The daily BBO capturer retains a bounded main-line shortlist, so low
all-listed-contract quote coverage is expected and is not a source failure.
It must remain visible as a denominator. For NCAAF's two matched ESPN games,
all six selected main-line candidate asks and content hashes equal the
packet's joined quote. The seventh quoted contract is an unmatched FCS game.
NCAAB research packets keep the indicative ask alongside the executable ask;
the ledger still labels its price indicative. No orders were placed.

Packet location: runtime `research/college_contract_candidates/{sport}/{date}/{hash}.json`.
The filename hashes the summary and every contract record hash. Quote hashes
are computed from the exact archived JSON row, and each row retains the
archive path and observed/transact times. The packet includes each serving
candidate's event, market slug, side, indicative or selected ask and quote
hash for reconciliation.

## NCAAF calibration audit

The legacy settled Main ledger still contains 1,014 calls, of which 51 have
verified executable-quote lineage. Those 51 are v2/v3 selected calls (5/46),
almost all in season week 8 (50) and one in week 9. By market, model versus
recorded-ask Brier: moneyline 0.1988 vs 0.1948 (9 calls); spread 0.2427 vs
0.2517 (25); total 0.2677 vs 0.2512 (17). The moneyline and total model
scores are worse than the ask baseline on this *selected, tiny* sample; the
spread score is better. Favorite status and line-size breakdowns are in the
machine-readable audit. Many bins have single-digit counts and cannot define
a calibration map or eligibility change.

The new prospective common-contract packet has **zero settled contracts** as
of this report. No new calibration was fitted, no alternative gate was
tested, and sizing remains unchanged. When later packets settle, evaluate
both sides on the same contract and timestamp, with chronological date
splits and proper probability scores against the market before testing a
frozen gate. An outcome-selected QUALIFIED versus LOW_EDGE reversal is not
an admissible gate experiment.

Reproduce the audit with `python scripts/audit_ncaaf_market_calibration.py`.
The JSON result is `outputs/research/ncaaf_market_calibration_2026-11-04.json`.

---

## Part 5 — College Overhaul Next Steps (source: COLLEGE_OVERHAUL_NEXT_STEPS_2026-11-04.md)

# College model overhaul: corrected baseline and capture plan

## NCAAF: the Main/Flat gap was a cohort error

The read-only audit at `outputs/research/ncaaf_main_flat_reconciliation_2026-11-04.json`
reconciles the canonical SQLite records. A `status='settled'` query returns
395 Flat calls at +8.03u and 1,014 Main calls at -58.41u. The Flat archive
also has **975 superseded rows with final results**, totaling -63.91u.
All 975 exactly match Main settled contracts on event, market, selection,
line, units, result, and P&L. Counting all graded Flat records gives 1,370
calls at -55.88u. These are overlapping shadow observations, not independent
wagers, and multiple historical versions may exist per event.

Of the 1,014 Main settled calls, 1,006 have an exact graded Flat contract
(-55.95u); eight match the event, market and side but have a changed line.
There is no evidence that Main and Flat sized identical calls differently.
The loss is concentrated in legacy v1 versions: 830 calls, -70.85u. Later
v2-v4 versions together are 184 calls, +12.43u. Within v1, the 486 calls
tagged `QUALIFIED` lost -91.35u, while 344 `PAPER_CALL_LOW_EDGE` gained
+20.50u. This is a historical eligibility failure signal, **not** a reason
to reverse the gate based on already observed outcomes. Current serving code
admits only v2-v4 to Main.

The immediate reporting fix is to show a graded historical view separately
from an active `status='settled'` view, deduplicated by decision identity
when evaluating a strategy. Never present the active Flat view as the full
historical baseline. Future NCAAF model decisions need an exact point-in-time
price, model input snapshot, and calibration test against market probability.

## NCAAB: collect the inputs before fitting another model

The serving adapter now stores a hashed pregame feature-capture packet on
each new NCAAB call: model feature values, history-game count, score-history
digest, decision time, event identity, and served probability. It explicitly
marks `replay_verified=false`; this records inputs but does not prove exact
model replay. The model version in `config/model.yaml` now agrees with the
v3 production registry and executable code. The legacy indicative price
label is preserved because no executable quote lineage is attached.

ESPN completed-game summaries contain field-goal attempts, free-throw
attempts, offensive rebounds, and turnovers. A terminal-only, first-seen
capture now stores those raw fields and a source hash, never a fabricated
possession estimate. November 2 and 3 captured **182/182** and **28/28**
completed games, respectively, in the runtime feature archive. The daily
pipeline will capture the previous day's completed games and report gaps.
Those box scores were first observed now and cannot be claimed as pregame
evidence for calls already settled on November 2–3.

Current NCAAB market archives are still thin: November 2 has 487 captured
contracts across 11 market events, and November 3 has 28 across two; only
one November 3 contract was timestamp-valid before event start. The serving
adapter uses live indicative summary prices rather than an archived,
two-sided decision quote. The next data task is to capture and join an exact
pregame BBO for each candidate with market slug, line, both asks, observation
time, and event identity. Missing or late quotes must produce no executable
edge claim while remaining visible in research coverage.

Only after enough first-seen box scores and quotes accumulate should the next
version fit opponent-adjusted points per possession and tempo, with offseason
roster uncertainty. Fit parameters on earlier dates, choose decisions on later
dates, and compare calibration, Brier/log loss, margin/total error, and
recorded-price P&L on a common contract cohort. The November settled results
remain retrospective diagnostics and cannot themselves qualify the version.
