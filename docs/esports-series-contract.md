# Research series distribution contract

`model_prediction.models.esports_series` is an isolated mathematical API, with
no serving registration, trained artifact, champion activation or market lookup.

```python
from model_prediction.models.esports_series import (
    MapScenario, mixture_series_distribution, series_distribution,
)

post_veto = series_distribution(3, [0.8, 0.3, 0.6])
pre_veto = mixture_series_distribution(3, [
    MapScenario(weight=0.25, map_win_probabilities=[0.8, 0.3, 0.6]),
    MapScenario(weight=0.75, map_win_probabilities=[0.2, 0.7, 0.4]),
])
assert abs(post_veto.moneyline_a - 0.612) < 1e-12
handicap = post_veto.map_handicap(-1, team="a")
total = post_veto.total_maps(2, side="over")
```

The example's probabilities and weights are illustrative inputs, not learned
estimates or evidence of observed vetoes. Callers establish decision-time
availability separately. `series_distribution` requires explicit integer
`best_of` equal to 1, 3 or 5 and exactly that many ordered probabilities of an A
map win, each finite and in [0, 1]. Both boundary probabilities are valid. Unknown
formats reject. Every potentially played map needs an input; play stops at the
first team to reach `best_of // 2 + 1` wins. No default map, veto, format, or
inverse-series estimate is supplied.

Conditional on a scenario, map results are independent, but probabilities may
differ by map position. Mixtures integrate shared scenario uncertainty rather
than first averaging map probabilities. Each scenario needs an explicit finite,
nonnegative weight; weights must sum to one within absolute tolerance 1e-12.
Empty mixtures and unnormalized weights reject. Zero-weight scenarios still
require valid inputs. Normalization only removes floating-point summation error.

`terminal_scores` is an immutable mapping from `(A maps won, B maps won)` to
unconditional probability, including zero-probability legal terminal scores.
`moneyline_a` and `moneyline_b` sum their respective winning terminal scores.
All derivative outputs are `OutcomeProbabilities(win, loss, push)`, each
unconditional; push mass is never redistributed into win/loss probabilities.

- `map_handicap(line, team="a" | "b")`: add the signed line to the selected
  team's map margin. Positive is a win, zero a push, negative a loss. A -1.5
  requires an A victory by at least two maps. B +1.5 is its complementary side.
- `total_maps(line, side="over" | "under")`: compare total maps played to the
  finite threshold. Equality is a push for either side. Over/under swap win and
  loss mass and retain identical push mass.

Finite numerical thresholds are analytical queries, not claims that such
contracts exist. These are map counts, never round counts. A real market still
needs verified participant identity, selection anchor, signed line, archived
quote, and provider settlement rules. The API assumes ordinary completed series;
draws, map advantages, forfeits, cancellations, voids, and title-specific variants
are unsupported. Mathematical push equality does not establish a provider's
refund policy. Quarter-line split stakes are not implemented.

The DP is exact for its stated probability model up to floating-point arithmetic.
It does not establish calibration, historical derivative coverage, predictive
lift, profitability, or superiority of independent maps over richer dependence
models. Those require separately audited data and chronological evaluation.

Verification: `tests/test_esports_series.py` checks Bo1/3/5 closed forms,
independent exhaustive heterogeneous-map paths, normalization, team swaps,
selection complements, integer pushes, scenario integration, immutability, and
invalid-input rejection.
