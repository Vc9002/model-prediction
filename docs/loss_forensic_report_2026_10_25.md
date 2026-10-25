# Main ledger loss audit — 2026-10-25 UTC settlement cohort

This report uses the canonical SQLite ledger at `E:/model-prediction-runtime/ledgers/ledgers.db`, queried at approximately 22:34 UTC on October 25. Its cohort is **Main rows with `status=settled`, `result=loss`, and `settled_at_utc` in `[2026-10-25T00:00:00, 2026-10-26T00:00:00)`**. It is neither an Eastern-calendar-day cohort nor a slate of events that all started on October 25. Rows remain distinct by pick ID; multiple markets on one game count as multiple losses. The live ledger can grow as later games settle.

## Reconciliation

| Sport | Losses | Realized units |
| --- | ---: | ---: |
| Soccer | 24 | -29.98 |
| Hockey | 10 | -18.40 |
| NCAAF | 16 | -27.30 |
| LoL | 3 | -4.25 |
| NBA | 4 | -6.75 |
| NFL | 5 | -7.64 |
| CS2 | 8 | -8.52 |
| Dota 2 | 3 | -3.00 |
| Tennis | 4 | -5.18 |
| Valorant | 2 | -2.00 |
| **All sports** | **79** | **-113.02** |

The prior `-108.01u` total is arithmetically wrong for these 79 rows. These are **losses only**, so the table is not the day's net P/L. A claim about all profitable or unprofitable picks must include wins, pushes, and the correct time cohort.

## Evidence versus hypotheses

- **Soccer:** There were 24 losing total-market rows. Five rows came from **three** 0–0 games: Lorient–Angers, Betis–Celta Vigo, and Minnesota–Houston. Seven losing rows had a *negative recorded model edge*; these merit a decision-packet audit. Seven losses were Over 1.5; their posted odds ranged from -285 to -525, with six at -300 or shorter. The score and line data do not establish that either team used a 5-4-1 low block or that such a formation was knowable before the pick.
- **Hockey:** Seven of ten losses were AHL games ending 1–0 or 0–1. Six losses, totaling -12u, had recorded edges over 8%. The hash-verified `hockey-poisson-dc-v2` artifact already declares `max_edge=0.08`; the serving path previously capped *sizing* at that bound but still admitted larger edges to Main. The serving fix reads that existing artifact bound and keeps excess-edge observations in Flat as zero-unit NO_CALLs. The ledger does not establish that the goalies were backups or that goalie identity was available at the decision timestamp.
- **NCAAF:** Of 16 losses, seven were totals, six spreads, and three moneylines. The USC–Wisconsin and Oregon–Illinois losing Over calls are real. Postgame rush counts and final scores cannot establish a pregame weather or play-script feature. Any challenger needs timestamped forecast weather and pregame play-rate inputs, then a walk-forward comparison against the incumbent.
- **LoL:** Three losses were moneylines. Two had model edges over 8%; one recorded a 38.7-point edge. Domestic-only Elo being inadequate for cross-region play is plausible, but the cited three outcomes alone cannot identify a regional adjustment. Build an interregional challenger only from matches and rosters observable before each event, and compare calibration and market-relative performance out of sample.
- **NBA:** Three losses were Unders and one was a spread. All four recorded edges exceeded 8%. The NBA engine is protected; a pace or transition adjustment requires a separate versioned challenger and pregame pace observations. Final game possession counts cannot be fed back as if known before tipoff.

## Integrity state and change policy

The timestamp parsing and replay identity failures found earlier were fixed. A controlled daily run on October 25 completed in 250 seconds with exit code 0, `material_errors=[]`, and ledger coverage `ok`: zero missing Flat lanes and zero Main non-call rows. Some next-day esports quote requests still encountered Polymarket US HTTP 429 limits; the date-scoped integrity check reports those as tomorrow's source exclusions rather than a failure of today's coverage. Bounded retries prevent a long stall. An actual current-date quote outage remains a material error and cannot be silently treated as a complete slate. The verification run set `MODEL_PREDICTION_SKIP_AUTO_BUYER=1` and `--skip-settlement`; it tested forecasts and coverage without placing orders or repeating settlement.

No result in this report warrants retroactively changing a settled pick. Started-game picks retain their original decision and price. Model changes beyond the artifact-backed hockey admission guard remain research candidates until point-in-time provenance and out-of-sample evidence support them.
