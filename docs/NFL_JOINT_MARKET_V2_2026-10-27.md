# NFL joint market v2 research result — 2026-10-27

## What changed

`nfl-joint-market-v2` is a **price-aware research challenger**, not a new
independent source of betting edge. It starts with the existing learned
moneyline probability, blends toward the exact saved two-sided no-vig
moneyline quote, and derives moneyline, spread, and total from one feasible
integer score distribution. The blend weight is the Brier-optimal value fitted
on earlier complete game dates whose outcomes were settled before the call.
No weight or confidence threshold was chosen from the current game.

The versioned hash-verified research artifact is
`config/models/challengers/nfl-joint-market-v2.json`; incumbent production
artifacts and Flat calls are unchanged. The previous `nfl-joint-score-v1`
challenger anchored on the incumbent moneyline, so it repaired coherence but
did not improve moneyline accuracy. This v2 tests an actual change to that
probability.

## Reproducible comparison

```powershell
$env:PYTHONPATH='src;.'
.venv\Scripts\python scripts/evaluate_nfl_joint_market_v2.py E:\model-prediction-runtime\ledgers\ledgers.db
```

The evaluator reads canonical settled NFL Flat records without mutating them.
It requires all three same-event market packets, verifies parent artifact and
replay hashes, checks each input and quote was available by the decision time,
and excludes unavailable or later-settled training outcomes. It compares the
original selected side on each exact historical line; it does not invent a
new contract or retrospective bet.

| Market | Calls | Incumbent Brier | V2 Brier | Incumbent log loss | V2 log loss |
| --- | ---: | ---: | ---: | ---: | ---: |
| Moneyline | 15 | 0.24104 | 0.22354 | 0.67555 | 0.64451 |
| Spread | 15 | 0.29610 | 0.25351 | 0.78717 | 0.69996 |
| Total | 15 | 0.23698 | 0.23641 | 0.66633 | 0.66520 |

For Philadelphia–Dallas, the as-of weight was 1.0 and the saved no-vig market
probability was 32.9177% Philadelphia. V2 returned **32.9177% Philadelphia
moneyline, 52.3135% Dallas −4.5, and 71.5271% over 43.5**. Dallas won 38–21.
These probabilities are coherent. They do not imply that buying Dallas at its
available ask would have had positive expected value.

The single-head market-anchor ablation is in
`scripts/evaluate_nfl_market_anchor_v1.py`. On its own it improved moneyline
and spread but worsened total Brier from 0.24759 to 0.26876 on 16 later
quote-backed total calls. V2 therefore uses the moneyline quote as a score
distribution anchor, not the one-sided total ask as a direct target.

## Limitations and decision

The comparison has only 15 games, all selected from existing called picks. The
total gain is 0.00057 Brier, too small to establish a real improvement.
Twenty-two events lacked a complete triad; 12 complete events had parent
artifact hashes that did not match this challenger; five lacked previously
settled training history; two lacked a valid moneyline quote. The saved
structural feature snapshots explicitly do not verify every upstream
historical source observation time. This is date-walk-forward retrospective
development, not a fresh prospective holdout or production qualification.

**Decision:** keep V2 as a challenger. It delivers an observable probability
change and improves all three metrics on this verifiable subset, but do not
promote it, rewrite settled calls, or claim improved profitability. The next
evidence gate is prospective capture of exact decision-time quotes and source
features across a larger unselected slate, followed by per-market calibration,
market-relative accuracy, and executable EV review under the existing
production promotion rules.
