# Quick 261120-nbf: NBA v10 research backfills (Phase 0.3-0.5)

Source plan: `docs/NBA_REVIEW_AND_V10_PLAN_2026-11-20.md` Phase 0.3-0.5. Operator approved the
backfills (free sources only). Data goes ONLY under `E:/MP/data/research/nba_v10/`; never into
`data/historical/`, `data/processed/nba/` or any live history file (serving v9 builds its baseline
and Elo from all loaded history).

## Tasks

1. Shared plumbing `data_sources/nba_backfill_common.py`: research-root guard, cache-free paced
   fetcher with backoff, 00:20-01:20 ET pause, American price -> implied probability.
2. Game index: `scripts/nba_v10_backfill/espn_scoreboards.py`, ESPN scoreboards every date
   2007-10 .. 2026-11-19 -> `games/scoreboard_index.jsonl` (nba_games_all schema).
3. Closing lines 2007-2026: `kaggle_closing_lines.py` (Kaggle HTTP API, CC0 / CC BY 4.0),
   normalised to the `nba_closing_lines_2024_2026.jsonl` schema, ESPN event ids by date+score,
   moneylines as implied probabilities. Acceptance: regenerated 2024-2026 rows agree with the
   existing file.
4. ESPN lines 2016-17 .. 2026-27: `espn_odds.py` (core odds; open blocks, Opening provider,
   teamrankings timestamped movement), parsing in `data_sources/nba_backfill_odds.py`.
5. Box scores 2016-17 .. 2023-24: `espn_boxscores.py fetch|build`; per-game files in the
   `load_nba_player_boxscores` shape, plays gzipped, flat player/team JSONL; superset parser
   `data_sources/nba_backfill_box.py`.
6. Injury report PDFs 2021-22 onward: `injury_report_pdfs.py` (per-day scheme detection, CDN
   check on miss runs) and `injury_report_parse.py` + `data_sources/nba_injury_report.py`
   (pypdf positioned fragments, both layouts, PIT observed time = max(filename, header)).
7. Tests on recorded real payloads `tests/test_nba_v10_backfill.py`; ruff clean.
8. PROVENANCE.json per dataset generated from disk; SUMMARY.md with coverage and resume steps.

## Guards

- No pytest beyond the new file; no edits to `.planning/STATE.md`; commits by explicit path.
- Long jobs run in the background, pause in the quiet window, resume from checkpoints.
