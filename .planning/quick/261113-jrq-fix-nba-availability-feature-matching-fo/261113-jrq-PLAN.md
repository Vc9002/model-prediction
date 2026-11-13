---
quick_id: 261113-jrq
slug: fix-nba-availability-feature-matching-fo
date: 2026-11-13
status: in-progress
---

# Fix NBA availability feature matching foreign teams' injuries via abbreviation substrings

## Evidence

`compute_nba_matchup_availability` (features/nba_player_availability.py, lines 287-289) assigns an
injury entry to a side when the entry's team name is a substring of the matchup team name (or
vice versa) OR when the entry's abbreviation is a substring of the matchup team name. Abbreviations
are two or three letters, so "orl" matches "new orleans pelicans", "phi" matches "memphis grizzlies",
"gs" matches "sacramento kings", "sa" matches "sacramento kings", "den" matches "golden state warriors".

Tonight (2026-11-13) three of nine NBA games are polluted: Grizzlies at Pelicans counts seven Orlando
Magic and four Philadelphia 76ers injuries (home_lost 12.0, away_lost 10.1 instead of roughly 0.3 and
0.5); Blazers at Kings counts six Warriors and four Spurs; Warriors at Spurs counts one Nugget. The
feature is wired into nba-spread-margin-v9 and nba-total-margin-v9 (expected_margin +=
availability_points_gap; total -= 0.15 * availability_total_loss), so the Main under 233.5 and the
Flat Pelicans/Kings/Spurs spreads were priced on other teams' injuries. v7 used the same code, so
settled v7/v9 history carries the same contamination for every matchup whose name contains another
team's abbreviation.

## Tasks

1. `src/model_prediction/features/nba_player_availability.py`: replace the substring tests with a
   `_team_matches()` helper: normalized full-name equality, else nickname (last token) equality
   (NBA nicknames are unique, covers "LA Clippers" vs "Los Angeles Clippers"), else abbreviation
   equality only when the matchup string itself is the abbreviation. No substring matching.
2. `tests/test_nba_nhl_cfb_injuries.py`: regression test with Orlando, Philadelphia, Golden State
   and Denver decoys for a Pelicans/Grizzlies matchup (fails on the old matcher), plus nickname and
   abbreviation-equality cases; keep the existing Celtics/Heat test green.
3. `docs/DEBUG.md`: entry. Then rerun the daily cycle through the supervisor so tonight's NBA rows
   are re-priced.

Verification: `pytest tests/test_nba_nhl_cfb_injuries.py -o addopts='' -q`, `ruff check`.
