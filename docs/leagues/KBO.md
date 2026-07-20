# KBO research contract

## Current model

- Model: `kbo-tie-aware-elo-v1`
- State: research only; never qualified for betting
- Target: expected Polymarket US moneyline settlement value
- Tie semantics: `P(win) + 0.5 × P(tie)` because a tied game settles at $0.50
- Backfill: official KBO regular-season schedule/results, 2022 onward
- Cost/access: free, no account, no API key
- Forecast requirement: exact team identity and a timestamp-valid executable ask on both sides
- Exposure: `units=0`

The official regular-season selector is `0,9,6`. Exhibition and postseason
games are excluded. Results are cached as normalized JSONL with a manifest,
source URLs, extraction time, and SHA-256 hashes under
`data/international_baseball/kbo/`.

## Baseline verdict

The locked chronological test is a control, not a strategy. Team Elo alone is
too weak for KBO betting because the starting pitcher, bullpen state, and
lineup can move fair value far more than small Elo changes.

`_metrics()` reports a diagnostic `units_at_minus_110` alongside `calls`/`hits`
for the train/validation/locked-test cohorts, with ties correctly scored as a
push (0 P&L) rather than a loss. KBO's contract line is comparatively even
(unlike the skewed esports lines), so the flat `-110` diagnostic is a closer
proxy here, but it is still not an executable-price claim. Real per-side
moneyline BBO capture started 2026-07-20 (`data/odds/kbo/<date>/`).

## Feature order

1. **Probable and confirmed starter identity.** Build pitcher ratings from
   prior starts only; add handedness, recent workload, days rest, pitch count,
   innings, strikeout/walk/run-prevention components, and a missing/late-change
   flag. Do not backfill a starter from a postgame box score into a decision
   timestamp when the pregame starter was not yet known.
2. **Bullpen availability.** Rolling relief innings/pitches over 1/3/7 days,
   leverage-weighted closer/setup usage, consecutive-day appearances, extra
   innings, and travel-day compression. This needs pitcher-level game logs,
   not merely team ERA.
3. **Effective-dated lineup strength.** Confirmed batting order, absences,
   handedness matchup, foreign-player roster status, and player talent with
   shrinkage. Maintain separate day-ahead and confirmed-lineup horizons.
4. **Park and weather.** Venue run factor, roof status, temperature, humidity,
   wind direction/speed, precipitation risk, and forecast age. Use the forecast
   available at the declared decision time, never realized weather.
5. **Travel, rest, and schedule context.** Home/away sequence, distance,
   doubleheaders, rainout makeup games, days off, and series game number.
6. **Run-environment and rules regimes.** Season/month scoring level, baseball
   and strike-zone/rules changes, extra-inning/tie rules, and park renovations.
7. **Market evaluation.** Preserve first observable and later pregame BBOs,
   price age, spread, depth, and fees. Profit is untestable on the historical
   score-only backfill.

Add one group at a time, compare it with the frozen Elo control on expanding
walk-forward folds, and reserve a new prospective cohort after selection.

## Commands

```bash
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli international-baseball-backfill --league kbo --from 2022-01-01 --to YYYY-MM-DD
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli validate-international-baseball --leagues kbo
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli polymarket-slate --sport kbo --date YYYY-MM-DD --timezone Asia/Seoul
env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli international-baseball-forecast --league kbo --date YYYY-MM-DD
```

## Known gaps

- No point-in-time starters, lineups, reliever workload, roster transactions,
  park, or weather features yet.
- Tie probability is league-wide rather than game-specific.
- No historical executable-price archive exists before prospective capture.
- Official web endpoints are public but not a promised stable bulk API; cached
  raw provenance and failure-closed behavior are mandatory.
