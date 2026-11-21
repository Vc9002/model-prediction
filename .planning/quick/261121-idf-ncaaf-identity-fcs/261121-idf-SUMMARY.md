---
phase: quick-261121-idf
plan: 01
status: complete
branch: ncaaf-261121-idf
commits: [c116bd75, a0e30316, b9c79c0c, 8f3be391]
---

# Summary: NCAAF v5 serving path, team identity and per-season FBS/FCS class

## Commits

- **c116bd75 (A, identity).** `features/cfb_team_identity.py`: `CFBTeamIdentity` wraps v6's `TeamResolver`
  (unedited) and adds a normalised-name join for id-less rows. `cfb_features.py` matches history, Elo,
  efficiency, rest and opponents on the key, which is built only from rows before kickoff.
  `_load_cfb_history` keeps team ids and feed groups. `UpcomingCFBGame` carries the slate team ids, and v4
  `predict_matchup` passes them through. `cli/forecast.py` is untouched. `cfb_serving_code_hash` now also
  covers the new module and `cfb_features_v6.py`.
- **a0e30316 (B, class).** `CFBTeamClasses` gives one label per (key, season), from rows before kickoff:
  - feed 81 only => FCS; feed 80 only => FBS;
  - feed 80+81 => the side opposite a labelled team;
  - then last season, then the team table, else UNKNOWN.
  The tier follows the label. UNKNOWN counts as not-FBS (`is_fbs_vs_fcs` is True), and a warning is logged.
- **b9c79c0c (C, one fallback).** The team's own conference, the Elo opponent and the efficiency opponent all
  use `_class_conference`: FCS or UNKNOWN => FCS; an FBS team missing from the table => Mid-American.
- **8f3be391 (D).** A slate ESPN id that the history knows wins over the display name. Example: the slate's
  "FIU Panthers" is a team-table name with no rows, while FIU's history sits under ID:2229. On this slate no
  team was affected, and the numbers match C exactly. D also fixes the typing of the rest-day `max()`.

No fitted constant, calibration table or artifact changed.

## Evidence

- Claims verified. The 2026 splits are 7/3, 7/4 and 5/5 (San José's é is valid UTF-8). 351 of 497 names do
  not resolve. In 2026, FIU, Missouri State and Sacramento State are FBS-feed teams missing from the table;
  NDSU is listed in the table as FCS.
- Sweep:
  - Split within a season: App State (2024-26), Hawaii (2016-19, 2021-26), San Jose State (2021-24, 2026),
    UConn (2016).
  - Renamed across seasons, now merged: Southern Miss, UL Monroe, UTSA, Sam Houston, FIU, plus 36 FCS/D2
    ids.
  - No key spans more than one ESPN id.
- As-of class check on 732 FBS-involved 2026 games:
  - The new rule is wrong on 2, both week 0 (NDSU and Sacramento State, which moved up). The error is in the
    fail-closed direction.
  - The old rule is wrong on 124.
  - Caveat: the reference is the v6 backfill game_class, which comes from the same ESPN feeds.
- Impact on the 2026-11-21 slate (58 games), arms A / B / C:

  | Measure | A | B | C |
  |---|---|---|---|
  | Moneyline moved at all | 3 | 7 | 47 |
  | Moved at least 1 point | 1 | 3 | 13 |
  | Moved at least 3 points | 1 | 1 | 1 |

  - Under C the mean moneyline move is 0.7 points. The largest is Hawai'i @ Nevada, 57.2% to 64.9%, because
    sample_games went from 5 to 10.
  - `is_fbs_vs_fcs` went from 2 to 4: three games added (Wofford @ Ole Miss, Tennessee Tech @ Mississippi
    State, Samford @ Auburn) and NIU @ NDSU removed.
- Settled games, 2026-10-01 to 11-19 (427, lines fixed to HEAD's projection):
  - Moneyline moved by at least 1 point on 146 games and by at least 3 points on 37.
  - Brier: 0.2174 at base, 0.2165 under A, 0.2148 under C.
  - FBS-vs-FBS only: 0.2191 at base, 0.2182 under B, 0.2168 under C.
  - Margin MAE: 13.53 to 13.26.
  - The sample is small. These are not promotion claims.
- Cost: feature extraction went from 0.11 to 0.30 s/game.

## Findings not fixed

- The efficiency opponent adjustment (`adj_scored = s_pts - opp_offset * 0.35`) has the opposite sign to its
  comment. Under C it inflates teams that played FCS opponents:
  - Chattanooga's offence PPP goes from 1.728 to 2.249 and its defence PPP from 1.157 to 0.887.
  - FBS favourites over FCS teams on the slate each lose about 0.7-0.8 points (Auburn 10.0 to 9.2, Alabama
    12.1 to 11.3).
- `test_cfb_structural_v2.py::test_cfb_structural_v2_calibrated_dispersion` already fails at base a3cbdac7.
- v5's FBS-vs-FCS margin SD slice was fit under the old fail-open labels.

## Arm E: sign flip (549b5ef2, operator-requested follow-up)

`adj_scored = s_pts + opp_offset*0.35`, `adj_allowed = a_pts - opp_offset*0.35`. The new direction test
fails at b148ae24.

- 2026-11-21 slate, moneyline moves (games moved at all / >=1pt / >=3pts, mean pp):
  - E vs HEAD: 54/27/7, mean 2.4pp.
  - E vs D: 53/28/8, mean 2.5pp.
- The large moves are the FBS-vs-FCS games, all in the sensible direction:
  - Tennessee Tech @ Mississippi State: home 36.0 -> 64.5 (HEAD projected the SEC home team losing by 5.1).
  - Samford @ Auburn: 75.3 -> 91.2.
  - Chattanooga @ Alabama: 78.7 -> 94.5.
  - Wofford @ Ole Miss: 74.1 -> 89.4.
- 427 settled games:
  - Brier: 0.2174 at HEAD, 0.2148 under D, 0.2149 under E.
  - Brier on the same 419 FBS-vs-FBS games: 0.2191 at HEAD, 0.2168 under D, 0.2170 under E.
  - Calibrated margin MAE: 13.39 at HEAD, 13.06 under D, 13.06 under E. E is neutral on FBS-vs-FBS games.
- v5 margin calibration on the same games (artifact slope 1.3189, intercept -0.41):
  - Regressing the actual margin on the raw margin gives slope 1.53 under both D and E.
  - Favourites by calibrated size, projected vs actual margin under E: 0-3 pts 1.5 vs 0.2; 3-7 pts 4.8 vs 5.8;
    7-14 pts 9.8 vs 10.8; 14-21 pts 16.6 vs 17.0; 21+ pts 28.3 vs 32.8.
  - Projections remain mildly compressed for favourites of 3 pts or more. This is n=427 in a single season,
    which is not enough to refit. No constant was changed.
