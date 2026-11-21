---
quick_id: 261121-cfbd
description: NCAAF v7 research data - CollegeFootballData (CFBD) source for preseason priors and betting lines
---
# NCAAF v7: CFBD data source (worktree `E:\MP-wt\ncaaf-cfbd`, branch `ncaaf-cfbd-261121`)

Source: `https://api.collegefootballdata.com`, `Authorization: Bearer $CFBD_API_KEY` (loaded from the
repo `.env` inside the script; never printed, logged, cached or committed). Academic tier, 3,000
calls/month shared with CBBD; this task budgets at most ~600 calls, enforced by a persisted
per-attempt call ledger in the cache root (every HTTP attempt, retries included), not by `/info`
deltas (other sessions spend the same key).

## Tasks
1. `src/model_prediction/data_sources/cfbd.py`: cached, quota-aware client (same shape as
   `ncaab_cbbd.py`): fail closed without a key, retries with backoff on transport / 5xx, stop on 429,
   call-budget cap, raw JSON cached with key-free params, `fetched_at_utc`, sha256. Normalizers for
   /teams, /player/returning, /talent, /recruiting/teams, /player/portal, /rankings, /lines.
   ESPN identity: verify CFBD team id == ESPN team id and CFBD game id == ESPN event id against
   `data/research/ncaaf_v6/history_snapshot.jsonl` (names + ids); decline disagreements and collisions
   with `NO_CALL_*` codes rather than guessing.
2. Probe one season (2023) first: does `/lines?year=` return every week (vs the snapshot's game
   count)? does postseason need its own `seasonType`? does `/info` cost a call? is `/rankings` week 1
   the preseason poll? does `/player/portal` carry a per-record date?
3. `scripts/cfbd_backfill.py`: fetch 2014-2026 (recruiting classes from 2011 for the 4-year window),
   write `data/research/ncaaf_v7/cfbd/team_season_priors.jsonl` and `lines.jsonl` (no American odds:
   moneylines become implied probabilities; unplayed games carry no closing line).
4. Point-in-time: every row carries `observed_at_utc` (season's first kickoff for preseason data,
   kickoff for lines) and `as_published_verified` (false unless shown otherwise). Investigate revision
   (portal dates vs week 1, destination fill-in, current-season values fetched mid-season).
5. Network-free tests with small recorded fixtures: `tests/test_cfbd.py`.
6. Coverage report `outputs/ncaaf/cfbd_coverage.{md,json}`: rows per dataset per season, ESPN match
   rates, calls used, as-published finding.

## Rules
Worktree only; data written to the shared untracked `E:\MP\data\research\ncaaf_v7\cfbd\`. Research
only: nothing touches serving, config or the registry. ruff clean; targeted pytest with
`MODEL_PREDICTION_*` unset. Commit explicit paths only.
