---
quick_id: 261121-cfbd
status: complete
---
# NCAAF v7: CFBD data source (summary)

Commits b11312dd, c40dd8dc on ncaaf-cfbd-261121.

- src/model_prediction/data_sources/cfbd.py: client (key from env, fail closed, per-attempt call ledger with a 600 ceiling, quota floor 200, retry 5xx, stop on 429) and normalizers for teams, returning production, talent, recruiting, SP+ (prior season only), preseason polls, portal and lines.
- scripts/cfbd_backfill.py writes data/research/ncaaf_v7/cfbd/{team_season_priors.jsonl (3,067 rows), lines.jsonl (38,350 rows)} and outputs/ncaaf/cfbd_coverage.{md,json}.
- tests/test_cfbd.py: 17 network-free tests on recorded fixtures.

Findings: CFBD team and game ids are ESPN ids (ESPN event match 99.6-100% for 2016-25; FBS prior rows 100% matched). Year-level /lines needs no week paging. Open lines and moneylines exist only from 2021. Spread signs verified (0 of 38,202 favourite/sign mismatches). Tables are joined on the resolved team id (three FCS schools are renamed across sources).

As-published: NOT verified; every row has as_published_verified=false. Portal out is point-in-time on transferDate; portal in is as of the fetch; 2026 was fetched in season; talent_prev_season is the safe talent field; returning production is offense-only.

Calls: 79 data-call attempts, 75 counted by the server (2,824 -> 2,749 remaining).

SUMMARY written by the orchestrator: the harness refuses Write calls from subagents.
