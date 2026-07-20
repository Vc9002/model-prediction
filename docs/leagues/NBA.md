# NBA research contract

## Current implementation

The newest report's Elo, trend, and defensive-trend candidate clears the locked
model-accuracy gate. That is not an executable-profitability result, and the
current report still needs to be aligned with the named artifact in a clean
release.

## Target model and kill gates

Predict possessions and lineup-adjusted points per possession using effective-
dated on/off impact, minutes projections, injuries, altitude, shot
profile, and opponent adjustments. Confirm active lineups and minutes
restrictions. Do not transfer WNBA parameters. Injury and lineup freshness are
primary kill gates in an efficient market.

Simple rest, back-to-back, schedule-density, consistency, hot/cold, and
schedule-availability additions failed the 2026-07-20 isolated audit. Keep them
out of the predictive roadmap; schedule context is operational only.

Spread and total claims remain research-only until exact timestamp-valid lines
and the matching decision-horizon inputs exist.
