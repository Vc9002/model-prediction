# World Cup research contract

**Retired 2026-07-27**: `League.WORLD_CUP` was removed from the codebase
entirely (enum member, model registry entry, live-trading league list, and
settlement mapping) — the tournament is over and there are no games left to
forecast or settle. This document is kept as historical research context
only; nothing below describes a currently wired model.

## Model identity

World Cup coverage is `WORLD_CUP`, a research-only international soccer league. The first safe version should be an international goal-intensity model, not an odds-only hunch:

```text
team strength + squad availability + venue/rest/tournament context
        -> away/home goal intensities
        -> correlated low-scoring goal distribution
        -> result, handicap, and total probabilities
```

One simulated goal distribution must produce every market. Do not build separate winner, spread, and total classifiers that can contradict each other.

## Markets and grading

The generic ledger still names participants `away_team` and `home_team`; for neutral-site World Cup matches these mean listed team A and listed team B from the source market, not a home-field claim.

- **Moneyline:** the selected team wins under the contract's stated basis. For soccer, this must explicitly say whether the market is 90 minutes plus stoppage time, regulation including extra time, "to advance", or trophy/outright.
- **Spread:** selected team goals plus the selection-relative handicap under the same score basis as the contract.
- **Total:** combined goals versus the listed line under the same score basis as the contract.

Three-way 1X2 markets include draw as a third outcome and are not supported by the current binary `moneyline` ledger field. Do not squeeze draw into `away` or `home`. A World Cup moneyline row is valid only when the market is binary, such as "team to advance", "lift trophy", or an explicit two-way draw-no-bet style contract. If the market is 1X2, log it outside `picks.xlsx` until the ledger has a three-way market type.

Knockout matches require special care. A regulation spread/total, extra-time-inclusive result, penalty-shootout advance market, and outright/tournament market are different contracts. Record the score basis before logging. If the score basis is missing, stop with `NO_CALL_INVALID_MARKET`.

## Required pre-match inputs

Required point-in-time inputs:

- canonical national-team entities for both participants;
- tournament stage and match identifier;
- neutral-site venue, local kickoff time, altitude/heat/weather, and travel/rest days;
- team strength: FIFA/Elo-style rating, recent competitive results, xG and shot-quality trend where licensed;
- squad availability, suspensions, likely keeper, and tactical rotation risk;
- group-table incentive state or knockout advancement rules;
- source market rule text and decision price timestamp.

The market price is the baseline to beat. It may be used for no-vig comparison and residual research, but not as an input to the independent raw goal model.

## Data sources

Primary odds source: The Odds API sport key `soccer_fifa_world_cup`, requested with `h2h,spreads,totals` when available. This key is provider-specific and should be verified against the active sports endpoint before relying on it for a live slate.

Supplemental result and fixture sources must preserve observation time and licensing. Acceptable source categories are official FIFA match data, licensed football data APIs, and timestamped sportsbook/market snapshots. Scraped or crowd-edited lineups may inform risk notes but cannot silently become deterministic features.

## Logging gate

World Cup rows may enter `data/picks.xlsx` only when all of the following are true:

- both participants resolve to canonical `WORLD_CUP` entities;
- the exact market score basis is captured;
- the event has not started;
- the price is frozen from an approved provider snapshot;
- the output is zero-unit `RESEARCH_OBSERVATION` unless a future trained artifact clears the normal promotion gates.

Until national-team entities are populated, World Cup research can be summarized and short-listed, but ledger calls should fail closed with entity resolution rather than inventing teams.

## Validation gate

This contract is infrastructure, not proof of edge. Candidate status requires chronological evaluation across prior World Cup, continental tournament, and qualifier samples, reported separately by:

- group stage versus knockout stage;
- favorite, underdog, and near-pick'em buckets;
- 90-minute, to-advance, handicap, and total market families;
- regulation versus extra-time or penalty-inclusive rules;
- calibration against no-vig market probabilities and price-aware ROI.

Do not tune a World Cup model on the same live tournament observations used as its final test. Outrights and tournament futures need a bracket simulation layer and must not be mixed with single-match ROI.
