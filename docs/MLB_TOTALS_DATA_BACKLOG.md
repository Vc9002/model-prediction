# MLB totals data backlog

The v0.7 totals rebuild may proceed with the currently available official MLB Stats API snapshots, but the candidate remains research-only until the data layer is expanded.

## Later collection work

- Backfill 2024 and 2025 regular-season official MLB game feeds into `data/mlb_statsapi_games_2024_2026.jsonl` using `collect-mlb-context`.
- Add timestamped pregame odds history for totals lines, not just reconstructed market controls.
- Add archived probable starters, confirmed lineups, weather, roof status, and umpire context with observed-at timestamps.
- Add pitcher/batter quality upgrades such as Statcast expected metrics, pitch velocity/spin, platoon splits, and bullpen workload quality.
- Keep the locked July 1-12, 2026 control window untouched for candidate evaluation. Do not tune coefficients, dispersion, or side selection on that test window.

## Current limitation

Historical official feeds retrieved after games are reconstruction evidence. They are useful for a research candidate, but they do not prove point-in-time profitability without archived pregame observation timestamps.
