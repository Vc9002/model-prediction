# NCAAB point-in-time player features (quick 261121-hmc, lane P)

Data and features only. Nothing here is joined to lines, margins or results. Spread candidates built on
these features must be pinned in a later protocol addendum (v4) before any outcome join.

## Data

- `scripts/backfill_ncaab_player_boxes.py` re-fetches the ESPN game summary for every completed game on
  the archived scoreboard (`data/research/ncaab_market/scoreboard.jsonl`) in season years 2022-2026
  (2021-22..2025-26). Two passes: D1-vs-D1 first, then D1-vs-non-D1, so a D1 team's game log is complete.
  2026-27 (season 2027) is refused.
- One file per event: `data/research/ncaab_external/espn_player_boxes/<date>/<event_id>.json`, schema
  `ncaab-player-boxes-v1`, with `observed_at_utc`, `source`, `source_url` and `source_sha256` (sha256 of
  the canonical summary JSON). Per team: score, period count, the totals row, and per athlete the id, name,
  jersey, position, starter, did-not-play, reason, ejected, active, minutes, points, FGM/FGA, 3PM/3PA,
  FTM/FTA, OREB, DREB, REB, AST, TOV, STL, BLK and PF. Stats are mapped through each block's own `keys`
  array, never by position. The raw summary is not kept (about 350 KB per game).
- `<date>` is the scoreboard's Eastern calendar day, kept as `game_date`. It is the PIT key: a game
  tipping at 02:00Z belongs to the previous Eastern day.

## Box quality gate (fail closed)

ESPN serves some boxes with real players listed as did-not-play, without stats and usually without an
athlete id. In one example, four starters were missing and the team logged 57 minutes. Such a box would
mark those players absent, so a team-game counts as having a box only if all four checks pass:

1. points summed over players equal the team score;
2. team minutes are within 5 of 200 + 25 per overtime period (the period count comes from the linescores);
3. no athlete who logged minutes is missing an id;
4. at least one player is listed.

A team-game that fails the gate is treated like a missing box: it is excluded from history and from the
prior-season index.

## Point-in-time rule

A feature row for game G of team T, season s, Eastern date d uses only:

- T's same-season games with `game_date < d` (strict, on the Eastern date; the game itself, any other
  game on day d and everything later are excluded), and
- the complete season s-1. Seasons do not overlap; the builder raises if the s-1 max date is not before d.

"Played" means minutes > 0 in that box. The `didNotPlay` flag is not used, because an absent athlete may
simply be unlisted. Same-day injury reports are never used. A missed earlier game is known before tip,
so absence features are PIT-valid. Athletes are joined across games and seasons on the ESPN athlete id
only, with no name fallback.

## Features (row key: `event_id`, `team_id`)

Context: `n_prior_games_box` (prior same-season games with a usable box), `n_prior_games_scheduled`
(prior completed games on the scoreboard), `early_season` (fewer than 5 prior boxes; informational).

### Availability

These need at least 3 prior boxes; otherwise every field is null with reason `insufficient_history`.

- The rotation is the top 7 athletes by season-to-date minutes over the prior games (ties broken by
  athlete id). `m_a` is that season-to-date minute total.
- `prev1` is the team's last completed game before day d on the scoreboard schedule. If that game has no
  usable box, every field is null with reason `prev_game_box_missing`; a gap is never silently skipped.
  `prev2` is the game before it. If `prev2` has no box, only `avail_top7_prev2_any` is null, with reason
  `prev2_game_box_missing`.

| field | definition |
|---|---|
| `avail_top7_prev1` | sum of m_a over rotation athletes who played in prev1, divided by the sum of m_a over the rotation |
| `avail_top7_prev2_any` | the same, counting an athlete who played in prev1 or prev2. One minus this is the minutes-weighted share out for 2 or more straight games |
| `top7_n_out_prev1` | number of rotation athletes who did not play in prev1 |
| `top7_max_absence_streak` | longest run of consecutive missed games, counted back from the last prior box, across the rotation |
| `top7_minutes_share_out_3plus` | m_a-weighted share of the rotation with an absence streak of 3 or more games (long-term out or gone) |
| `top1_out_prev1` | the season-to-date minutes leader did not play in prev1 |
| `roster_minutes_share_out_prev1` | over every athlete with season minutes: the minutes share of those who did not play in prev1 |

Streaks are counted over usable boxes, so a game with a broken box is skipped. When the team's previous
game itself is broken, the row is null instead.

### Roster continuity and returning production

These fields are null when:

- there is no season s-1 in the data (all of 2022, reason `no_prior_season_data`);
- T has fewer than 10 usable s-1 boxes (`team_absent_prior_season`, which covers new D1 members);
- G is T's first game of the season (`no_current_season_history`).

Only athletes who have already logged minutes for T this season (before day d) are counted. A returner
or transfer therefore enters on the day after his first appearance, never earlier.

| field | definition |
|---|---|
| `continuity_same_team` | share of T's current-season minutes-to-date played by athletes who logged minutes for T in s-1 |
| `continuity_any_team` | the same, for athletes who logged minutes for any team in the data in s-1. Transfers count; freshmen and transfers from outside the data do not |
| `ret_min_share_same` | sum over appeared same-team returners of their s-1 minutes for T, divided by T's s-1 total minutes (classic returning minutes; low early in the season while returners are still unseen) |
| `ret_gmsc_share_same` | the same with Hollinger game score in place of minutes (null when T's s-1 game-score total is not positive) |
| `ret_min_share_transfer_in` | sum over appeared athletes of their s-1 minutes for each other team, divided by that team's s-1 total minutes ("lineup-share" units: a full-time starter is about 0.16-0.2) |
| `ret_gmsc_share_transfer_in` | the same in game-score shares |
| `prior_gmsc40_cur_weighted` | over appeared athletes with at least 100 s-1 minutes (any team): the s-1 game score per 40 minutes, weighted by current-season minutes-to-date. Measures the quality of who is actually playing, by last season |
| `prior_gmsc40_coverage` | share of current-season minutes-to-date covered by athletes with such a prior rate |

Hollinger game score: PTS + 0.4·FGM - 0.7·FGA - 0.4·(FTA - FTM) + 0.7·OREB + 0.3·DREB + STL + 0.7·AST
+ 0.7·BLK - 0.4·PF - TOV. It is not opponent- or pace-adjusted. A transfer from a weak conference
carries an inflated rate. Adjusting by the old team's PIT rating is a candidate refinement for v4.

## Known limits

- **2021-22 has no prior season,** because the scoreboard archive starts there. Every prior-season field
  is null for season 2022.
- **Only identified athletes are covered.** Transfers from non-D1 programs, or from D1 teams whose games
  are missing, are not covered (`continuity_any_team` and the transfer fields undercount them).
- **The top-7 rotation is a choice.** It is fixed here, before any outcome join, and is not tuned.
- **Mid-season departures** (dismissal, transfer) look like long absences. That is the intended signal:
  the player is not available.

## Files

- `data/research/ncaab_external/player_features/pit_player_features.jsonl`: feature rows (untracked).
- `.../player_features/feature_summary_261121.json`: per-season null rates and quantiles (D1 teams).
- `.../player_features/audit_261121.json`: backfill coverage and box-quality audit.
- Tests: `tests/research/test_ncaab_player_features_261121.py`.
