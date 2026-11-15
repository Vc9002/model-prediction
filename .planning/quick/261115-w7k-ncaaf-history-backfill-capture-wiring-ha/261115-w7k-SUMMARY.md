---
status: complete
---
# 261115-w7k: NCAAF history backfill + groups ingest, capture wiring, hash-test documentation

Operator approved all three open items from 261115-183 ("go for it", 2026-11-15).

## 1. Backfill and groups ingest (waiver)
- The operator waived the pinned `cfb-v5-history-impact-rule-1` REJECT. Record: holdout Brier delta +0.001065 against the +0.001 limit, 95% CI [-0.000815, +0.002998] (spans zero); log scores passed. No v5 refit was done; no model was promoted or routed.
- `ESPNClient.scoreboard_ncaaf_divisions` (groups=80 and 81, limit 500, merged by id) and `completed_ncaaf_games` (FBS-involved, non-forfeit; adds neutral site, week, season type, feed groups) in espn.py; NCAAF-only branch in ingest.py with parser_version `espn-scoreboard-ncaaf-groups-1` and a past-date cache refetch keyed on `ncaaf_groups_80_81_v1`. `scoreboard()` is unchanged for every sport. 5 new tests in tests/test_ingest.py; mutation proof: disabling the NCAAF branch fails the row test.
- Live check on real data (2026-11-14, temp dir): 55 FBS-involved rows, all 20 keys, 55 of 55 already in the live history.
- `scripts/cfb_backfill_live_history.py` ran once: appended 1,478 events (sha256 `7d0f9caf...`, equal to the 0pq record) to data/processed/ncaaf/games.jsonl only. The processed file's prefix hash matched its backup; data/historical was byte-identical; every appended id is unique; loaded history is 9,498 rows. Backup: data/research/ncaaf_v6/live_backup_20261115T211835Z. The second run printed "already applied".
- Effect: the champion v5 now reads a longer history on its next daily run. Expect small projection changes; the measured holdout cost was +0.001065 Brier.

## 2. Forward capture wiring
Commit c7c28386: `_run_cfb_pregame_capture` in cli/daily.py, skipped when NCAAF daily is off, any error becomes `{"status": "error"}` and is not a material daily error. Result key `step1m_cfb_pregame_capture`. 3 wiring tests; mutation proof passed.

## 3. Four artifact-hash failures
Root cause: the three NBA v8 files embed a label string, not a hash (unchanged since a107a3e8, unrouted research evidence); tennis-games-markov-v3 has a stale hash unchanged since 9a568a99 (disabled rollback entry, out of scope). Not CRLF drift. Nothing was repinned; the four are listed in `KNOWN_NON_VERIFIERS` with reasons.

## Verification
`ruff check src/ tests/` clean; 110 tests across ingest, v5, v6, v6.1, serving parity, college football, capture wiring and hash verification passed. The whole suite was not run.
