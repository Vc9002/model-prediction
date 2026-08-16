# Tennis research contract

## Model identity

**v0.1 — Surface Point Engine** (`tennis-point-markov-trend-v0.1`) is a research-only singles model. It estimates each player’s probability of winning a point on serve, then simulates the actual tennis scoring hierarchy:

```text
serve/return point strength
        -> service games
        -> sets and tiebreaks
        -> best-of-three or best-of-five match
        -> match ML, game spread, total-games distributions
```

One simulated match distribution must produce every market. Separate ML, spread, and total classifiers are prohibited because they can contradict one another.

## Features and trend analysis

Required pre-match inputs:

- player identity and tour (`ATP` or `WTA`);
- surface (`hard`, `clay`, `grass`, or `carpet`);
- surface serve points won and return points won with sample counts;
- surface and overall Elo available strictly before decision time;
- best-of-three/five and final-set tiebreak rules;
- recent serve/return point performance, rest/workload, and known injury/retirement risk.

Long-term surface point rates are shrunk toward a versioned tour/surface prior. Recent form is then capped at 20% influence and shrunk by a 400-point prior. This prevents a few hot matches from dominating the forecast. Elo affects the point logits symmetrically and is capped. Head-to-head record is not a primary feature; it is sparse, opponent-confounded, and may only be introduced after chronological ablation.

Fatigue is a predeclared `[0,1]` input that modestly reduces both serve and return effectiveness. Never infer fatigue merely because a player lost recently. Injury status and retirement risk must come from timestamped evidence.

## Markets and grading

- **Match moneyline:** model probability that the selected player wins the completed match under the book’s retirement rules.
- **Game spread:** selected player’s total games won plus the selection-relative handicap.
- **Total games:** combined completed-match games versus the listed line.

Retirement, walkover, default, match-tiebreak, and shortened-format rules vary by venue. A price is invalid until the exact contract’s retirement and grading rules are captured. Historical retirements, walkovers, defaults, and abandoned matches are excluded from v0.1 point-rate training. A retired match must never be graded from a final score alone.

The generic ledger still calls participants `away_team` and `home_team`. Tennis logging is therefore disabled operationally until both players exist in the canonical entity registry and the exact market’s score basis and retirement rule are recorded. Model research can run outside `picks.xlsx`; an actual pre-event call must still use the normal immutable ledger workflow.

## Data sources

1. [Jeff Sackmann ATP data](https://github.com/JeffSackmann/tennis_atp) and [WTA data](https://github.com/JeffSackmann/tennis_wta) for historical results, player IDs, rankings, surfaces, and match-level serve statistics. The repositories are convenient CSV inputs but are CC BY-NC-SA; attribution and non-commercial restrictions must be respected.
2. [The Odds API tennis coverage](https://the-odds-api.com/sports/tennis-odds.html) for current tournament-specific moneyline, spread, and total prices. Tennis sport keys are tournament-specific, so active keys must be discovered rather than hardcoded as one permanent `TENNIS` key.
3. [Tennis Abstract Match Charting Project](https://github.com/JeffSackmann/tennis_MatchChartingProject) for optional point/shot-level research. Coverage is contributor-driven and non-random, so it cannot silently replace the broader match dataset.

## Validation gate

v0.1 is infrastructure, not a trained profitable artifact. Before candidate status it requires:

- chronological ATP and WTA evaluations reported separately;
- surface and tournament-level cohorts;
- match ML Brier/log loss against no-vig market probability;
- game-spread and total-games calibration and price-aware ROI;
- retirement-rule audits;
- at least 500 settled matches for candidate consideration;
- at least 30 newly logged forward observations before changing any fixed trend or shrinkage parameter.

Do not claim accuracy or profitability from synthetic simulator tests. Those tests verify scoring coherence and directional behavior only.
