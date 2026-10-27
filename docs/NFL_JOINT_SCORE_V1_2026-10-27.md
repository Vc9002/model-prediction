# NFL joint score v1 research challenger (2026-10-27)

## Problem and change

The incumbent learned NFL moneyline and structural spread/total heads were
priced independently. On the saved Philadelphia–Dallas packet, Philadelphia
moneyline was 52.4444% while Dallas −4.5 was 50.7757%. Those outcomes cannot
both occur, so the sum above 100% was an internal pricing contradiction.

`nfl-joint-score-v1` is a new research version in
`src/model_prediction/models/nfl_joint_score_v1.py`. It derives all three
market probabilities from a single distribution over feasible integer final
scores. The incumbent learned moneyline probability fixes the win marginal;
the existing structural margin and total distributions supply conditional
score shapes. It adds no trained weights and does not change incumbent artifacts,
production registration, or Flat call status.

For the saved pregame Eagles–Cowboys inputs, the joint version gives:

| Market | Incumbent | Joint v1 |
| --- | ---: | ---: |
| Philadelphia moneyline | 52.4444% | 52.4444% |
| Dallas −4.5 | 50.7757% | 37.0566% |
| Over 43.5 | 70.8592% | 71.3730% |

The Dallas spread change is a model probability change, not merely a routing
or display override. The actual final was Dallas 38, Philadelphia 21; this
one game cannot establish which spread probability was better calibrated.

## Retrospective replay

Command:

```powershell
$env:PYTHONPATH='src;.'
.venv\Scripts\python scripts/evaluate_nfl_joint_score_v1.py E:\model-prediction-runtime\ledgers\ledgers.db
```

The evaluator reads only settled Flat NFL records, takes the latest saved call
per event and market, requires all three markets, checks that input snapshots
predate kickoff, and verifies replay hashes and incumbent probabilities. On
16 complete verifiable game packets:

| Market | Incumbent Brier | Joint v1 Brier | Mean absolute probability change |
| --- | ---: | ---: | ---: |
| Moneyline | 0.244405 | 0.244405 | 0.000000 |
| Spread | 0.289437 | 0.278788 | 0.076329 |
| Total | 0.228664 | 0.227859 | 0.011051 |

Twenty-two events lacked a complete settled three-market packet. Eighteen
complete packets failed the saved scalar engine source hash check and were
excluded rather than silently replayed with today's source. The cohort is
small and selected from called picks; the score difference is development
evidence, not proof of generalization or profitability. Saved snapshots also
mark historical source observation times as unverified; the replay checks
snapshot timing, not every upstream feature's point-in-time provenance.

## Decision and next gate

Keep v1 as a research challenger. It fixes the logical inconsistency by
construction and improves spread Brier on the verifiable retrospective subset.
It does **not** improve moneyline Brier, and the total improvement is tiny.
Do not promote it as a three-head accuracy upgrade or change live serving on
this evidence. Collect fresh prospective packets with verified source timing,
run the same per-market calibration and profitability comparison, and require
the production promotion gate before replacing the incumbent. Existing Flat
CALL records remain CALL records, with any conflict explicitly annotated.
