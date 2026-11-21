# Quick 261120-nbf summary: NBA v10 research backfills (Phase 0.3-0.5)

All data is under `E:/MP/data/research/nba_v10/` and nothing else was written. The live
`data/historical/nba_games_all.jsonl` still starts at 2024-01-01; its 9 new rows on 2026-11-21 came from
the scheduled daily ingest. Each dataset folder has a PROVENANCE.json written by
`scripts/nba_v10_backfill/provenance.py`.

## Coverage (as of 2026-11-21)

| Dataset | Span | Rows | Gaps / caveats |
|---|---|---|---|
| games/scoreboard_index.jsonl | 2007-10-01 .. 2026-11-19 | 26,461 completed events | July-Sept skipped except 2020 and July 2021; 0 failed dates |
| games box scores | 2016-17 .. 2023-24, regular + play-in + post | 10,212 games, 262,939 player rows, 20,424 team rows, 10,212 plays files | none: target equals stored for every season |
| closing_lines (Kaggle) | 2007-10-30 .. 2026-06-14 | 24,440 rows, all with an ESPN event id | 2024-26 check against existing file: 3,467 of 3,481 identical, 14 differ (source revisions); moneylines missing from 2022-23 (half) onward |
| closing_lines 2025-26 open/close | 2025-26 | 1,315 rows | 16 without an event id |
| espn_odds | 2016-17 .. 2026-27 to 2026-11-19 | 13,081 events | opening proxy: Opening provider (2016-17), teamrankings timestamped movement (2016-17 .. 2022-23), per-book open blocks (2023-24 on). 2025-26 and later have DraftKings only |
| injury_reports | 2021-10-01 .. 2026-11-19 | 45,706 PDFs (3.0 GB), 3,210,231 rows | hourly names until 2025-12-21, 15-minute names from 2025-12-22; 597 days without reports |

## Known issues

- Injury parse: 0 PDF errors. 288 rows (row failure rate 0.009%) are All-Star games printed as
  UNK@UNK / "Non-NBA Team", flagged `consistent=false`. In the 2023-24+ layout, 4,771 wrapped reason
  lines were not placed: the player and status rows are complete, but those reasons are truncated
  (no more than 0.15% of rows). July files are Summer League.
- The teamrankings movement `line` is away-signed: negate it to get the home spread (93-99% agreement
  with the close).
- Hourly PDFs are headed 30 minutes after their filename slot, so `observed_at_utc` is the later of
  the two times.

## Resume

Every script resumes from its checkpoint. Run with `PYTHONPATH=E:/MP-wt/backfill/src E:/MP/.venv/Scripts/python.exe`:
`espn_scoreboards.py --end <date>`, then `espn_boxscores.py fetch` and `build`, `espn_odds.py`,
`injury_report_pdfs.py --start <next day> --end <date>`, `injury_report_parse.py`
(`--months YYYY-MM` for the current month), `kaggle_closing_lines.py normalize`, and finally
`provenance.py`.
