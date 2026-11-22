---
quick_id: 261121-pbp
description: NCAAF overhaul step 4 start - ESPN play-by-play crawl and per-team-game PBP features (v7 rating-core data foundation)
---
# NCAAF v7 data foundation: ESPN play-by-play crawl (operator approved 2026-11-20)

Source: `https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary?event=<id>`
(no key). Event ids: `data/research/ncaaf_v6/history_snapshot.jsonl` (16,378 games, game_class) plus
completed games in `data/processed/ncaaf/games.jsonl` not yet in the snapshot (class from team ids).

## Tasks
1. `scripts/cfb_espn_pbp_crawl.py`: polite (~1 req/s, never above 2/s), retries with backoff and
   `Retry-After`, circuit breaker on repeated 403/429, single instance via an OS file lock held for the
   process lifetime, resumable via an append-only manifest (terminal: ok / no_plays / not_final / 404;
   errors retried next run). Raw payload cached gzipped at
   `data/research/ncaaf_v7/raw/<season>/<id>.json.gz` (season = CFB season, Jan-Jun games belong to the
   prior year) with `fetched_at_utc` and sha256 of the raw bytes; written temp + `os.replace`.
   Completed games only (scores present, kickoff in the past, `STATUS_FINAL` in the response).
   `sample` mode: seeded stratified sample, season x class x early/late.
2. Coverage report on the sample -> `outputs/ncaaf/cfb_espn_pbp_coverage.{json,md}`. "Usable" fixed
   before looking: drives present, >= 80 scrimmage plays, period/score/down/distance/yardsToEndzone/
   clock/start.team present on >= 95% of scrimmage plays, and last play's running score equal to the
   known final. Plus a play-type census.
3. If >= 90% of FBS-involved sampled games are usable: full crawl in the background, FBS-involved first,
   then FCS-FCS.
4. `scripts/cfb_pbp_features.py`: per (event, team) rows with offense and mirrored defense metrics,
   garbage-time filtered and unfiltered; kickoff_utc from the header for PIT use. Box-score cross-check.
   Unit tests: `tests/research/test_cfb_pbp_features.py`.

## Rules
Shared checkout: only new files; never edit `src/`, `config/`, `docs/`, `.planning/STATE.md`.
Crawl output under untracked `data/research/ncaaf_v7/`. Commit scripts, tests, small reports with an
explicit pathspec. No full pytest. Research only: nothing here touches serving.
