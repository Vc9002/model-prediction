# Recency experiment and prospective quote evaluation

The rebuild now has a completed 17-market recency experiment and an executable
quote evaluator for captured full-game binary moneylines and MLB half-point
spreads/totals. Neither establishes improved production performance or profit.
The existing 21-model research generation remains available; this experiment
is preserved alongside it, without champion changes or orders.

## Recency experiment

Theory: season and roster changes can make older training games less useful.
Evidence: the original training partitions end substantially before their later
evaluation dates. Test: keep the exact features, date partitions, estimator
architecture and hyperparameters; compare no weighting against training weights
with 180-day and 730-day half-lives. Normalize weights to mean one and apply them
to both the scaler and estimator. Choose on the original selection partition;
fit temperature or residual calibration only on its separate original partition.

All 17 v1 research references reproduce every saved evaluation prediction with
maximum absolute error zero before candidate fitting. The control is explicitly
the prior research candidate, not the production incumbent. CFB retains its
separate three-market joint-score experiment; NRFI is excluded because its
archived generic feature files do not contain the specialized first-inning
cohort. Its original source/lineage limitations remain unresolved.

Ten markets selected recency weighting. Seven selected the unchanged reference:
WNBA total, NBA, NFL, CS2, Valorant, KBO and NPB moneylines. All 17 fitted bundles
are versioned and reloadable, but those seven are equivalent reference fits,
not seven predictive improvements. Two complete runs produced identical fitted
artifact hashes for every market.

None clears the predeclared improvement threshold: the lower endpoint of the
pointwise date-cluster 95% interval must exceed 0.004 vector Brier or 1% of
reference score MAE. The intervals are exploratory and not adjusted for the
17 comparisons. Reused evaluation periods are development evidence.

| Market | Reference | Recency candidate | Interpretation |
|---|---:|---:|---|
| MLB moneyline accuracy | 54.22% | 54.62% | Brier gain too small and uncertain |
| LoL moneyline accuracy | 67.40% | 68.03% | Vector Brier gain 0.001172, interval [0.000367, 0.001984]; below 0.004 |
| WNBA moneyline accuracy | 63.64% | 62.91% | Worse point estimate |
| WNBA spread score MAE | 10.5135 | 10.9667 | Worse; reference-minus-candidate interval [-0.7460, -0.1592] points |
| Soccer vector Brier | 0.608251 | 0.608439 | No improvement |

Failure mode: emphasizing recent samples can discard useful information and
amplify a transient selection-period pattern. The WNBA spread result illustrates
that failure. Preserving the existing generation is the rollback; no production
configuration has been replaced. Further generic weighting is not supported by
this experiment. Sport-specific information and fresh outcome/price evidence
remain the substantive work.

Artifacts:

- `outputs/research/recency_iteration_20260911_v2_verified/`
- `outputs/research/recency_iteration_20260911_v2_reproduced/`
- Trainer: `scripts/train_recency_research.py`.
- 39 actual pregame forecasts from the three MLB recency models, covering 15
  games: `outputs/research/recency_forward_20260911_v2/`. They are a separate
  cohort, not additional independent games or replacements for the prepared
  incumbent-paired forecasts. Their archived sources and predictions rebuild.

## Captured quote and prediction audit

The corrected observer has captured 49 paired forecasts across 18 games. All 49
reproduce both the candidate and its bound production decision. At this
checkpoint, five contracts have insufficient participant history and fourteen
are still pending. The live observer report remains authoritative:
`outputs/research/paired_observer_20260911_v2/report.json`.

The final audit is
`outputs/research/paired_quote_audit_20260911_v3_verified/`.
There are **24 verified event-market quote opportunities across 14 MLB games**:
14 moneylines and 10 spreads. The remaining 25 reproduced forecasts have no
verified economics: 14 NRFI, eight CFB, one moneyline start mismatch, one spread
book timestamp mismatch, and one spread price/depth failure. Missing references
and unsupported quote adapters are distinct from invalid archived evidence.

The new MLB adapter uses the existing canonical envelope hash loader. It checks
archive root and record identity, event/start/team matching, full-game contract
slug, signed selection line, long/short mapping, market state, raw book timestamp,
ask/bid/depth and agreement between the projected price and raw executable ask.
It rejects first-five-inning contracts and integer lines whose push terms are
not established by this archive. For half-point lines, an impossible push can
be removed only when both models assign exactly zero probability to it.

The same $5 all-in, whole-contract, depth-limited, entry-fee policy selects 18
candidate trades and 14 incumbent trades across those 24 opportunities. These
are independently simulated per-market choices at archived decision prices;
they are not current quotes, fills, a combined portfolio, or independent events.
All realized P&L, ROI and accuracy remain null before settlement. Exact quote
records, payout tables, choices, source hashes and a read-only ledger snapshot
are retained. Settled evaluation uses the same adapter and policy math.

A discovered ledger-mirroring race is also fixed: when an equivalent tier copy
arrives after capture with a smaller pick ID, it must not replace the original
bound decision. The first deduplicated decision context must still match, and
the original bound ID must belong to that verified mirror group. A genuinely
earlier or conflicting decision remains disqualifying.

## Verification and remaining work

118 focused tests pass, including exact incumbent/reference reproduction,
archive tampering, participant/line/time mismatches, wrong contract horizons,
missing depth, invalid numeric inputs, impossible pushes, pregame null P&L,
settlement parity, mirror races, training-only weighting and serialization.
Scoped Ruff and mypy pass. Exact artifacts, code and verification hashes are in
`outputs/research/recency_quote_verification_20260911/validation.json`.

The rebuild is not complete in the sense requested: no replacement has yet
proved both improved production prediction accuracy and executable profitability.
Next evidence comes from these already-captured games settling, verified NRFI
and CFB price capture, and sport-specific feature improvements. Existing frozen
protocols remain obsolete; missing statistical or price evidence is a factual
limitation, not a frozen-protocol restriction.
