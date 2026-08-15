# NPB research contract

## Current model

- Model: `npb-tie-aware-elo-v1`
- State: research only; never qualified for betting
- Target: expected Polymarket US moneyline settlement value
- Tie semantics: `P(win) + 0.5 × P(tie)` because a tied game settles at $0.50
- Backfill: official NPB English regular-season calendar/results, 2022 onward
- Cost/access: free, no account, no API key
- Forecast requirement: exact team identity and a timestamp-valid executable ask on both sides
- Exposure: `units=0`

The NPB calendar exposes stable official game links, team codes, and final
scores. Canceled games are discarded. The v1 collector intentionally ends at
September 30 because the October calendar mixes late regular-season games with
Climax Series and Japan Series rows without a reliable machine-readable
competition label. Contamination is worse than omitting a small tail.

## Baseline verdict

The locked chronological result makes the limitation obvious: score-only Elo
is a weak control. NPB has meaningful starting-pitcher, bullpen, park, travel,
roster, and tie dynamics that must be modeled before any economic claim.

`_metrics()` reports a diagnostic `units_at_minus_110` alongside `calls`/`hits`
for the train/validation/locked-test cohorts, with ties correctly scored as a
push (0 P&L) rather than a loss. NPB's contract line is comparatively even
(unlike the skewed esports lines), so the flat `-110` diagnostic is a closer
proxy here, but it is still not an executable-price claim. Real per-side
moneyline BBO capture started 2026-07-20 (`data/odds/npb/<date>/`).

## Feature order

1. **Probable and confirmed starter.** Effective-dated pitcher identity,
   starter quality, handedness, days rest, recent pitch/inning workload,
   strikeout/walk/run-prevention components, and explicit uncertainty when the
   announced starter changes.
2. **Bullpen availability.** Reliever pitches/innings and leverage over 1/3/7
   days, consecutive appearances, closer/setup availability, extra innings,
   and travel. A season bullpen ERA is not an availability feature.
3. **Lineup and roster strength.** Confirmed order at the decision horizon,
   platoon matchup, injured/deactivated players, foreign-player slots, and
   effective-dated player/team identity. Organization Elo must regress after a
   major roster or manager regime change.
4. **Park and point-in-time weather.** Stadium run factors, roof state,
   temperature, humidity, wind, rain, and forecast issue time. NPB parks are
   heterogeneous; a generic home-field term is inadequate.
5. **Interleague and league context.** Central/Pacific league strength,
   designated-hitter context, interleague schedule, travel, series game number,
   rest, makeup games, and doubleheaders.
6. **Tie model.** Regulation/extra-inning context, starter/bullpen quality,
   expected run environment, and season rules. Estimate `P(tie)` separately,
   then preserve the 50¢ settlement conversion.
7. **Prospective market evidence.** First BBO, later BBO, spread, depth, fees,
   and closing pregame BBO. Model accuracy without executable prices does not
   establish profitability.

Keep day-ahead and confirmed-lineup/starter models separate. Features learned
after first pitch cannot be smuggled into the earlier decision horizon.

## Commands

```bash
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli international-baseball-backfill --league npb --from 2022-01-01 --to YYYY-MM-DD
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli validate-international-baseball --leagues npb
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli polymarket-slate --sport npb --date YYYY-MM-DD --timezone Asia/Tokyo
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli international-baseball-forecast --league npb --date YYYY-MM-DD
```

## Known gaps

- No point-in-time starters, lineups, bullpen workload, transactions, park, or
  weather features yet.
- October regular-season tail is omitted to prevent postseason contamination.
- The official calendar lacks first-pitch time, so all games on a local date
  are predicted before that date updates ratings.
- No historical executable-price archive exists before prospective capture.
