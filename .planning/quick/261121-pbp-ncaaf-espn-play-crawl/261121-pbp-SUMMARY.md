---
quick_id: 261121-pbp
status: crawl running (resumable); scripts, tests and coverage report committed
---
# Summary: ESPN CFB play-by-play crawl + per-team-game features

## Coverage (seeded stratified sample, 394 games, 6 per season x class x early/late, 2016-2026)
- FBS-involved: 262 games, **93.1% usable**, 96.9% with drives+plays (gate >= 90%: passed).
- FCS-FCS: 132 games, 81.1% usable, 87.1% with drives+plays.
- On scrimmage plays, period/score/down/distance/yardsToEndzone/clock/start.team/statYardage are present 100%.
- Failures are (a) no drives at all (25 games, mostly FCS) and (b) last-play running score != final (19 games:
  truncated overtime, a trailing 0-0 "End of Game" row, 1-3 point PAT glitches). No season is missing.
- With 12 games per season x class cell, one or two failures trip 90%, so the cell gap list is noisy. Real
  gaps: 2022 FBS-involved (18/24 usable, 4 with no drives) and FCS-FCS no-drive games in 2017, 2018, 2022.
- Report: `outputs/ncaaf/cfb_espn_pbp_coverage.{json,md}` (includes the play-type census).

## Crawl
- `scripts/cfb_espn_pbp_crawl.py crawl` running in the background since 2026-11-20 23:36 ET, 1 req/s,
  16,399 completed games (9,391 FBS-involved first, then 7,008 FCS-FCS), ETA about 4.5-5 h.
- Raw: `data/research/ncaaf_v7/raw/<season>/<id>.json.gz` (raw body + fetched_at_utc + sha256);
  manifest `crawl_manifest.jsonl`; log `crawl.log`. Resume: rerun the same command (terminal outcomes skipped);
  a second copy refuses to start (OS lock on `crawl.lock`). Progress: `... status`.
- The first attempt used a UA containing "crawler" and got 403; switched to the repo's existing ESPN client UA
  (`Mozilla/5.0`, as in `cfb_v6_espn_lines.py`), which works. Breaker: 15 min pause after 5 straight 403/429,
  exit after 3 pauses.

## Features (`scripts/cfb_pbp_features.py` -> `data/research/ncaaf_v7/team_game_pbp.jsonl`)
- Per (event, team): offense and mirrored defense plays, drives, success rate, yards/play, explosive rate
  (>= 20 yd), turnovers, points/drive, seconds/play, each all-plays and garbage-filtered (`_ng`); kickoff_utc.
- Data quirks handled: post-play scores (pre-play margin from the previous row), goal-to-go distance 0
  (falls back to yardsToEndzone; without it the 2016 1st-and-goal snaps and short TDs vanished),
  back-to-back duplicate rows, non-monotone score glitches.
- Sanity on the first 792 team-games: success rate 0.420, yards/play 5.79, explosive 6.7%, 2.09 pts/drive,
  27 s/play. Box cross-check: yards diff median 0 (|diff| median 7), turnovers match 76% (box counts
  special-teams fumbles; ours are ESPN-flagged offensive scrimmage turnovers and undercount slightly, e.g.
  a lost fumble typed as a plain Rush without the flag), points_for - drive points = 0 on 73%
  of team-games, the rest mainly defensive/return TDs.
- Each row carries `pbp_final_score_match` (feed's last running score == final); 96.7% True on the first
  608 games. Filter on it for rating fits.
- Tests: `tests/research/test_cfb_pbp_features.py` (29 pass).
- Rerun `python scripts/cfb_pbp_features.py` after the crawl finishes to cover all games.

## Crawl complete (2026-11-21 04:04 ET)
- 16,404 games: 15,535 ok, 869 no plays (exited cleanly, no 403/429 after the UA fix).
- Full features: 31,066 team-game rows; final-score match 95.6%. Success rate 0.414 (FBS-FBS 0.420,
  FCS-FCS 0.406), yards/play 5.74, explosive 6.8%, 2.05 pts/drive; box turnover match 76%, |yards diff| median 8.
- Unknown types excluded: "fumble" (344 rows) and empty type (323 rows).
