# Corrected NRFI model and first archived paired returns

The current 21-model research generation is
`outputs/research/generation_20260912_clean_update/`. It adds the corrected
NRFI candidate to the three joint CFB candidates and 17 generic candidates.
The recency experiments remain separate. This is a usable research generation,
not a set of qualified production replacements.

## Prospective results from September 11

The bounded observer exited with 49 captured forecasts, 19 unavailable contracts
and no pending work. Its process exited with code 2, matching a partial capture;
it is no longer running. Independent ESPN scoreboards contain completed results
for all 15 MLB and five CFB games on the prepared slate. All 49 captured forecasts
score, across 18 distinct games. Nineteen preparation failures remain failures.

All 49 bound canonical production rows are now marked `removed`, with no
canonical settlement. The pregame audit preserved exact decision records,
model inputs, source files, quotes and policy choices before the games. The new
archive evaluator independently reproduces both models, revalidates the embedded
quote hashes and contract terms, verifies the pregame choices, and joins the
completed scoreboard. It does not relabel or restore any removed ledger row.
These results measure archived forecasts and simulated trades, not actual fills.

| Market | Games | Incumbent accuracy | Candidate accuracy | Incumbent vector Brier | Candidate vector Brier |
|---|---:|---:|---:|---:|---:|
| MLB moneyline | 15 | 80.00% | 60.00% | 0.436588 | 0.481547 |
| MLB spread | 12 | 50.00% | 50.00% | 0.541600 | 0.468008 |
| MLB NRFI, original research v1 | 14 | 35.71% | 35.71% | 0.498529 | 0.503380 |
| CFB moneyline | 2 | 100.00% | 100.00% | 0.341037 | 0.466436 |
| CFB spread | 3 | 33.33% | 0.00% | 0.891484 | 0.926901 |
| CFB total | 3 | 66.67% | 0.00% | 0.522804 | 0.623864 |

The same games can appear in multiple markets. These are not 49 independent
outcomes. Brier is the sum across all outcome classes, including pushes where
applicable. The NRFI results above belong to the earlier research candidate,
not the corrected version fitted on September 12.

Twenty-four event-market opportunities across 14 MLB games retain eligible
quotes. The entry-fee, whole-contract, $5 all-in policy is unchanged from the
pregame audit. Its already-recorded choices produce:

| Quoted market | Opportunities | Incumbent simulated P&L | Candidate simulated P&L |
|---|---:|---:|---:|
| MLB moneyline | 14 | -$17.785 | -$28.235 |
| MLB spread | 10 | -$8.345 | +$14.310 |
| Combined event-market simulations | 24 | -$26.130 | -$13.925 |

The incumbent made 14 hypothetical trades costing $67.130, for -38.92% ROI.
The candidate made 18 costing $86.925, for -16.02% ROI. This is a single US game
date, not a reliable profitability estimate or an independently sized combined
portfolio. Both policies lost overall. The spread subset does not justify a
profitable-model claim. Confidence intervals, actual fills and CLV remain null.
NRFI/CFB forecasts without verified quotes remain unpriced.

Separate recency forecasts also scored all 39 contracts. They reuse many of
these games and are not additional independent evidence. Their output is
`outputs/research/recency_outcomes_20260912_v1/`.

Evidence:

- Raw and normalized independent results:
  `outputs/research/espn_outcomes_20260912_for_20260911/`.
- Original paired candidate outcome scores:
  `outputs/research/paired_outcomes_20260912_v1/`.
- Both-model comparison and immutable pregame choices:
  `outputs/research/archived_pair_evaluation_20260912_v2/`.
- Runner: `scripts/evaluate_archived_research_pairs.py`.

## NRFI input audit and corrected model

The old builder included a canceled game (`746577`, training) and a scheduled
game (`823042`, evaluation), both labeled NRFI. It also placed both teams'
batters into each team's recent player pool, reversed the home/away defensive
history buckets, and used different history/rest timing in training and live
research inference. A separate game (`746942`) has a player identity on both
teams; the corrected cohort excludes that ambiguous case rather than inventing
an assignment.

The new `research_nrfi.py` builder requires completed status and explicit,
consistent first-inning scores. It rejects missing counts, contradictory labels,
invalid IDs and ambiguous cross-team/player assignments. Both training and live
research inference share the same team-specific accumulators, opponent-role
priors, two-calendar-day outcome embargo and starter rest measured to scheduled
start. Normalized starter names may resolve to only one historical player ID.

The recent-batter features are named honestly: they are PA-weighted recent
team pools, not confirmed top-three batters or confirmed starting lineups.
Historical starter identities still come from completed box scores. The source
is a retrospective reconstruction, and historical pregame availability remains
uncertified. The repairs do not retroactively certify point-in-time provenance.
The production legacy builder and incumbent artifact remain unchanged.

All 1,375 saved v1 evaluation predictions reproduce with zero error before the
comparison. The cleaned cohort uses the original partition memberships, after
removing invalid/ambiguous rows: 4,105 train, 697 selection, 686 calibration,
1,374 evaluation. No boundary moves to compensate for removed rows. League
priors use only cleaned training dates; model/temperature selection uses the
same separate date partitions and existing five classifier variants.

The fitted candidate is `mlb-nrfi-clean-research-20260911-v2` (the version tag
was established when implementation began; the verified fit ran September 12).
It selects logistic regression C=0.1. On the same 1,374 valid evaluation games:

| Metric | Original research reference | Corrected candidate |
|---|---:|---:|
| Accuracy | 51.3828% | 51.6739% |
| Vector Brier | 0.499205 | 0.499835 |
| Log loss | 0.692351 | 0.692983 |

The reference-minus-candidate Brier interval is [-0.001315, -0.000012]. Cleaning
fixes input defects, but this fitted model does not improve probability quality.
It is not promoted. The old candidate remains available for exact comparison.

Two complete fits produce identical fitted artifact hashes:
`outputs/research/clean_nrfi_20260912_v2_verified/` and
`outputs/research/clean_nrfi_20260912_v2_reproduced/`.
Trainer: `scripts/train_clean_nrfi_research.py`.

Fourteen actual pregame forecasts for September 12 independently rebuild from
archived sources in `outputs/research/clean_nrfi_forward_20260912_v2/`.
One additional scheduled game lacked probable starters and was excluded.
Those outcomes and executable returns are not yet known.

## Validation and remaining scope

The complete suite passes: **2,897 passed, 3 skipped**. All 21 saved candidates
pass artifact-identity and probability-inference checks, including explicit
integer and half-point derivative lines. Fourteen new NRFI forecasts rebuild
from source, and the two corrected fits are byte-identical. Scoped mypy passes.
Exact code, artifact checks and lint results are retained in
`outputs/research/nrfi_and_settlement_verification_20260912/`.

Broader economic support still needs exact captured contract evidence for NRFI,
CFB and other unsupported settlement/archive forms. A missing quote reference is
not proof that a market does not exist: the current Polymarket archive does
contain NRFI books, but those are not automatically the price at a candidate's
recorded decision. New predictions need timely bound prices and explicit rules.
Inactive or uncaptured sports retain historical research metrics and null
prospective/economic evidence. No new champion registration, production ledger
write or order submission is part of this rebuild.
