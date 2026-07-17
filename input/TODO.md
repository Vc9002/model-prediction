# Project status — 2026-07-17 (updated)

## Qualified models — preserve every version

| Sport | Market | Hit | Calls | Units at -110 | Active artifact |
|---|---|---:|---:|---:|---|
| MLB | moneyline | 60.87% | 92 | +14.91U | `mlb-elo-trend-lr-v2.json` |
| NBA | moneyline | 67.35% | 294 | +84.00U | `nba-elo-trend-lr-v2.json` |
| WNBA | moneyline | 65.98% | 97 | +25.18U | `wnba-elo-trend-lr-v2.json` |
| NFL | moneyline | 60.55% | 109 | +17.00U | `nfl-elo-trend-lr-v2.json` |

## Completed

- [x] Fix monthly grading with an explicit 10-call complete-month minimum.
- [x] Treat the incomplete terminal month as provisional while retaining it in aggregate reporting.
- [x] Requalify WNBA without altering or deleting the protected v1 artifact.
- [x] Qualify NFL with the reproducible locked-holdout result.
- [x] Test learned trailing-30-day MLB HFA; reject because hit rate declined.
- [x] Test confidence-gap gating; reject because it is an exact monotonic reparameterization of max probability.
- [x] Audit pitcher inputs; block retrospective ERA because it is not point-in-time valid.
- [x] Audit every requested non-moneyline market and record the missing-data blockers.
- [x] Correct and run the ten-check DEBUG protocol.
- [x] Preserve v1 artifacts and activate hash-verified v2 artifacts.
- [x] Fix 4 stale "research state" docstrings → accurate shadow-qualified labels.
- [x] Fix audit hash serialization mismatch (compact separators now used in write too).
- [x] Fix Polymarket executor token_id resolution (slug → token lookup).
- [x] Fix empty observed_at_utc guard in eligibility staleness check.
- [x] Fix bans.py EntityResolutionError crash on stale config entries.
- [x] Fix FixedPlattCalibrator boundary handling (accept [0,1] inclusive).
- [x] Fix config drift: maximum_data_age_hours now flows through forecast path.
- [x] Fix float comparison epsilon in qualification gate.
- [x] Fix normalize_no_vig missing upper-bound guard.
- [x] Fix CLI fallthrough trap (else → explicit elif + raise).
- [x] Add LRU cap to ESPNClient and ESPNMLBClient response caches.
- [x] Add handle.flush() to mlb_market_odds snapshot append.
- [x] Daily forecast now includes Polymarket US odds in output.
- [x] Ledger cleared and re-initialized (backup at data/.backup_2026-07-17/).
- [x] Removed redundant input/INPUT.md (content covered by TODO.md + PROMPT.md).

## Next work, in order

1. Prospectively snapshot exact pregame NBA/WNBA spread and total contracts with observed timestamps.
2. Prospectively snapshot MLB F5/full-game lines, confirmed starters, starter game logs, and bullpen usage before first pitch.
3. Accumulate enough settled, timestamp-valid samples for a new train/validation/locked-holdout cycle.
4. Re-evaluate WNBA July only after the calendar month closes. Its current 12/27 result is a real warning, not evidence to hide.
5. Compare model probabilities with executable Polymarket asks before making any trade-profitability claim.

## Scan record

Full audit completed on 2026-07-17 at 18:13 CST. All ten checks passed: 865-event chain intact, 0 broken hashes, all imports healthy, historical data clean, no stale references, 11 JSON + 1 YAML artifacts present, feature pipeline healthy, 4 active learned artifacts loaded and qualified, config consistent, 0 off-season MLB games.

Ledger was reset after audit (backup preserved). Bug sweep across all 59 source files found and fixed 14 issues. 120/120 tests pass, ruff clean.
