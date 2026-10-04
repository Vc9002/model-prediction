# All-model incumbent comparison — 2026-09-09

Follow-up: [recording fixes and the second MLB candidate](MODEL_ITERATION_2026-09-09.md).
The results below remain the preserved first comparison.

The first generation does **not** establish an improved, profitable replacement.
MLB moneyline is the only market whose exact current artifact can be replayed
and compared end to end from the available decision records. Its new candidate
has worse point estimates for accuracy, Brier score, and log loss. WNBA replay
fails. The other 19 markets lack either settled current-model decisions or
reproducible stored inputs/model state. No model was promoted and no order was
placed by this evaluation.

The operator's research reset remains in force. These are evidence limitations,
not reinstated frozen-protocol restrictions.

## Verified output

`outputs/research/incumbent_comparison_20260909_verified/` contains the policy,
canonical ledger snapshot, code snapshot, full report, and one report per
market. Each market has per-context stored/reproduced probabilities and replay
status. MLB additionally has exact prediction pairs and the complete archived
quote records used for hypothetical economics.

Run `scripts/evaluate_research_incumbents.py` with the existing generation,
canonical runtime database, and a new output directory:

```bash
env OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. \
  .venv/bin/python scripts/evaluate_research_incumbents.py \
  --generation outputs/research/generation_20260909_v1_verified \
  --database /Users/vincentc9002/model-prediction-runtime/ledgers/ledgers.db \
  --output outputs/research/incumbent_comparison_NEW_RUN
```

The runner opens SQLite read-only and saves the selected records from one read
transaction. It checks the current champion registry and hashes production
artifacts before and after. It never imports an execution or promotion path.
Earlier failed attempts remain under `incumbent_comparison_20260909_v1`–`v3`;
`v4` is the first complete run. The verified run reproduces its results.

## MLB result

663 settled ledger rows become 445 unique decision contexts after removing
218 mirror duplicates. Four contexts differ only in tier-specific feature
availability annotations; their actual feature values, hashes, predictions,
outcomes, and quote references agree. All 378 contexts with the exact current
artifact hash reproduce within 0.000002 probability. Another 67 contexts carry
an earlier artifact hash and are outside the current-artifact comparison.

Choose the earliest decision per event before checking quote eligibility.
Five events are absent from the candidate's exact saved holdout and one has a
source start-time mismatch; one later decision is removed. This leaves 371
games across 30 UTC dates. Every pair checks participant orientation, start
time, pregame decision time, score, outcome, and candidate target. All 1,492
saved candidate holdout predictions also reproduce from the fitted artifact.

| Metric, same 371 games | Current MLB v8 | New research candidate |
|---|---:|---:|
| Accuracy | 56.33% | 52.56% |
| Brier score, lower is better | 0.243103 | 0.247241 |
| Log loss, lower is better | 0.679154 | 0.687610 |

Brier improvement is -0.004138; its date-cluster bootstrap 95% interval is
[-0.009810, +0.001758]. The candidate fails the required lower-bound improvement
of 0.002. This is no reliable gain, not proof of a statistically significant
degradation. The generic candidate also omits the starter, bullpen, park, and
weather inputs used by v8; the experiment does not isolate which inputs explain
the difference.

The historical source file changed after training, but rebuilding from the
current source exactly reproduces **all 6,855 saved feature-and-target rows**.
Both source hashes and this verification are recorded. The original fitted
candidate and saved evaluation predictions were not changed.

## Hypothetical economics, not realized fills

194 of the 371 games have a uniquely linked, pregame full-game binary contract
with a fresh, valid two-sided book. Exclusions: 169 missing archived references,
one contract start-time mismatch, and seven quotes older than 300 seconds.
All 194 links use the known legacy reduced-quote hash, uniquely reconciled to
an archived full record. This is explicitly distinguished from a full-record
hash created at decision time. The evaluation saves each recovered raw record
and its full hash; historical ledger records are not rewritten.

The common policy permits up to $5 per event including fees, buys whole
contracts within displayed ask depth, chooses the side with the largest
positive expected net return per dollar, and otherwise abstains. Prices are
asks, never midpoint or a -110 fallback. It assumes an immediate single taker
fill and holds to binary settlement. It does not reproduce the incumbent's
actual serving/trading policy or assert that a historical order would fill.

Fees use the [official Polymarket US fee schedule](https://docs.polymarket.us/fees):
the taker coefficient is 0.06 in `coefficient × contracts × price × (1-price)`,
rounded to cents with half-even rounding. The published effective time is July
1, 2026 at midnight ET; earlier quotes are excluded. Rebates are not assumed.

| Same 194 opportunities | Current v8 probabilities | Candidate probabilities |
|---|---:|---:|
| Hypothetical trades | 134 | 152 |
| Cost including entry fees | $640.395 | $727.570 |
| Net P&L | -$91.395 | -$69.570 |
| ROI on deployed cost | -14.27% | -9.56% |
| Entry fees | $20.25 | $24.49 |
| Maximum drawdown of settled P&L | $103.445 | $90.785 |

Candidate-minus-incumbent P&L is +$0.1125 per available event, with a date-cluster
95% interval of [-$0.4030, +$0.6451]. Both policies lose money and the incremental
advantage is uncertain. ROI denominators differ because the models abstain on
different events under the same per-event budget. CLV and actual fills remain
null. The sample is selected by incumbent ledger coverage, and historical
feature observation times remain unverified. The policy was written before
this evaluator consumed outcomes, but it is retrospective development, not
prospective preregistration or fresh confirmation.

## Other markets

- **WNBA moneyline:** 169 rows reduce to 94 contexts. 70 replay, 22 lack required
  valid inputs, and two mismatch. Event `401857168`, August 23: stored 0.547062
  vs reproduced 0.634997 for away. Event `401857171`, August 24: stored 0.552593
  vs reproduced 0.669877 for home. These are unexplained differences, not
  calibration evidence. Comparison and economics are blocked for this market.
- **No settled current-model decisions:** WNBA spread v2, WNBA total v2, MLB
  structural total v10, NBA moneyline v4, NFL moneyline v4. Older/rollback
  decisions are not relabeled as evidence for these incumbents.
- **Fourteen other markets:** MLB spread and NRFI; all three NCAAF markets;
  soccer; tennis; five esports; KBO; NPB. The canonical records contain no stored
  model feature inputs for these cohorts. This evaluator has no replay adapter
  for these families. Recover versioned decision state and inputs and implement
  their exact inference paths before interpreting a candidate delta. This is
  not a claim that replay is impossible using other archives.

## Fix and verification

`match_executable_quote` previously returned a reduced object. The moneyline
logger hashed that object while pointing to an archive containing the full
market record. The matcher now preserves the exact raw record privately, and
the lineage function hashes that record. The regression test checks the hash
against the actual archived JSON, rather than merely checking a non-null
lineage object. No retrospective ledger mutation is performed.

126 focused tests passed across research generation, incumbent evaluation,
quote regressions, CLI, and learned-forward behavior. Scoped Ruff and mypy
passed. This is not a new full-suite result. Existing production model/config
hashes are unchanged. Code backups are under
`outputs/research/incumbent_20260909_code_backup/`; the logging fix is reversible
independently of the research evaluator.

The old `evaluate_ready_challengers.py` is not used: it expects `result` and
`model_probability` from a loader returning `outcome` and `probability`, joins
only event IDs, and defaults missing probabilities to 0.5. Those issues remain
in that legacy path; this new runner is the supported path for this generation.

## Next iteration

1. Reconcile the two WNBA mismatches and capture complete, hash-bound model
   inputs/state for all families. The five empty current-model cohorts need
   decision collection or an independently reproducible historical replay.
2. For MLB, preserve useful incumbent information and test incremental
   sport-specific features and quote-aware selection in a new development
   generation. Do not select new thresholds on this now-examined sample.
3. Continue sport-specific improvements, prioritizing degraded NCAAF and the
   weak KBO/NPB and WNBA-total candidates already identified by generation one.
4. Collect a fresh forward cohort with exact contracts, actual observation
   times, full raw quote hashes, depth, and independently matched closing data.
   Evaluate predictive gains and absolute net returns before proposing any
   replacement. More model versions by themselves are not progress.
