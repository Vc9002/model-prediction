# Project status — 2026-07-17 (FINAL)

## Qualified models — preserve every version

| Sport | Calls | Hit Rate | P&L | Artifact |
|---|---:|---:|---:|---|
| MLB | 92 | 60.87% | +14.91U | mlb-elo-trend-lr-v3 |
| NBA | 294 | 67.35% | +84.00U | nba-elo-trend-lr-v3 |
| WNBA | 97 | 65.98% | +25.18U | wnba-elo-trend-lr-v3 |
| NFL | 109 | 60.55% | +17.00U | nfl-elo-trend-lr-v3 |
| SOCCER | 470 | 68.09% | +140.91U | soccer-elo-trend-lr-v1 |

All 5 use the same learned LR + confidence-gate pipeline.
Soccer covers 7 Polymarket leagues across 3,525 games (Aug 2025 - May 2026).

## Research

| Sport | Status |
|---|---|
| Tennis | Baseline artifact exists — needs data bootstrap |

## Completed

- [x] All 14 architecture bugs fixed (audit hash, executor token, eligibility guard, bans crash, calibration boundary, config drift, float epsilon, pricing validation, CLI fallthrough, ESPN cache LRU, odds flush, docstrings, event count, input file dedup)
- [x] Soccer model: promoted from research to shadow_qualified (verified: 66.5% accuracy on 496-game locked holdout)
- [x] Dead code removed (nfl.py BasketballModel, redundant INPUT.md)
- [x] Dashboard: Portfolio tab, odds endpoint, edge filter, dash launcher
- [x] Polymarket edge filter (2% minimum over executable ask)
- [x] v3 learned artifacts for all 4 original sports
- [x] Architecture: nba/wnba/nfl clarified as learned LR production, not BasketballModel

## Scan record

Full audit completed 2026-07-17. All 10 DEBUG checks pass: 739-event chain intact, 21 JSON artifacts with 0 broken hashes, 128 tests, ruff clean. Data: MLB 4,703g · NBA 1,996g · WNBA 630g · NFL 670g · Soccer 3,525g — all 0 dupes, 0 no-score.

## Revert

```
git checkout v1.0.0
```
