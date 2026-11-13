---
quick_id: 261113-jrq
slug: fix-nba-availability-feature-matching-fo
date: 2026-11-13
status: complete
commit: 31f092a9
---

# Summary: NBA availability feature matched other teams' injuries by abbreviation substring

## What was wrong

`compute_nba_matchup_availability` assigned an injury row to a side when the row's team name was a
substring of the matchup name (or vice versa) or when its two/three-letter abbreviation appeared
anywhere in the matchup name. "orl" is inside "new orleans pelicans", "phi" inside "memphis
grizzlies", "gs" and "sa" inside "sacramento kings", "den" inside "golden state warriors". On
2026-11-13 three of nine NBA games were priced on other teams' absences (Grizzlies at Pelicans:
22 phantom points), and "LA Clippers" rows were missed for "Los Angeles Clippers". The feature feeds
`nba-spread-margin-v9` and `nba-total-margin-v9` directly.

## Changes (commit 31f092a9)

- `src/model_prediction/features/nba_player_availability.py`: `_team_matches()` with exact
  normalized-name equality, unique-nickname equality, or exact abbreviation equality; no substrings.
- `tests/test_nba_nhl_cfb_injuries.py`: two regression tests (decoy teams; nickname and
  abbreviation cases), both failing on the old matcher.
- `docs/DEBUG.md`: entry with blast radius, scope of historical contamination, and the triage of
  the two unrelated guard-test failures.

## Verification

- `pytest tests/test_nba_nhl_cfb_injuries.py -o addopts='' -q`: all pass.
- Live recompute for tonight's nine games: zero foreign-team injuries; Clippers now 5.8 points lost
  (was 0.0); Pelicans/Grizzlies total loss 9.9 (was 22.1).
- `ruff check` and `ruff format --check`: clean.
- `tests/test_five_blind_spot_guards.py`: 2 failures predate this change (NCAAF v5 serving-class
  map; `unit_policy` mock vs changed `forecast.py`), not related to availability.

## Follow-up

Settled v7/v9 NBA spread and total history includes contaminated rows for matchups whose names
contain another team's abbreviation; the replay snapshot does not persist availability inputs, so
they cannot be re-priced from the ledger alone.
