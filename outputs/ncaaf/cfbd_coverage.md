# CFBD coverage (NCAAF v7 research priors and lines)

Generated 2026-11-21T20:04:07Z by `scripts/cfbd_backfill.py`. Normalized outputs:

* `E:\MP\data\research\ncaaf_v7\cfbd\team_season_priors.jsonl`
* `E:\MP\data\research\ncaaf_v7\cfbd\lines.jsonl`
* raw cache `E:\MP\data\research\ncaaf_v7\cfbd\raw`

## As-published finding

**Verdict: NOT verified as-published. Treat every CFBD prior as current-revised**
(`as_published_verified: false` on every row).

CFBD stores one value per team and season. It serves no publication timestamp, revision history or
as-of parameter for any of these tables, so the API cannot show that a historical value matches what
was public before that season's kickoff. Field by field:

* **Portal OUT** is point-in-time: `transferDate` is the date the player entered the portal, and the
  backfill counts only entries dated at or before `observed_at_utc`. A season's list runs from about
  the previous October to that season's kickoff. Entries dated after kickoff were excluded: 1 in
  2021-2025 (in 2022) and 18 in 2026.
* **Portal IN** is as of the fetch. `destination` has no date and is filled in (or overwritten)
  whenever the player commits, so a commitment made after the cutoff leaks into `portal_in_count`.
  The share with no destination falls from about 40% (2021-22) to about 16-22% (2024-26). That fits a
  field that keeps being updated, not one frozen at a past date.
* **Returning production** covers offense only and is derived from CFBD rosters. Roster snapshots are
  undated, so the value is current-revised by construction. `returning_def_pct` is always null
  because CFBD has no defensive figure.
* **Talent** (247 composite for year Y): the publication date is unknown and could fall after week 1.
  `talent_prev_season` (year Y-1) was public before season Y and is the safe field.
* **Recruiting classes**: class Y is final by February Y, so classes Y..Y-3 predate season Y. 247 can
  still re-rate players later, so small revisions are possible.
* **Prior-season SP+** is the final table for Y-1 (normally published in January). Restated versions
  are possible. The same-season SP+ is never used, because that would be hindsight.
* **Preseason AP / Coaches** (CFBD regular week 1) is a historical record, so revision risk is low.
  For 2023, week 1 was confirmed to be the preseason poll.
* **2026** was fetched on 2026-11-21, mid-season (`in_season_fetch: true`). Its priors are whatever
  CFBD holds now, not what was known at kickoff.
* **Lines**: CFBD's `spread` / `overUnder` is the provider's last stored number, with no timestamp.
  It is read as the close only for completed games (null otherwise). `spreadOpen` / `overUnderOpen`
  have no timestamp either. Each line row's `observed_at_utc` is the kickoff. Open lines and
  moneylines exist only from 2021.

## Calls

* Data-call attempts in the call ledger (all runs and probes, retries included, /info excluded): 79. By endpoint: {'teams': 1, 'lines': 13, 'recruiting/teams': 1, 'talent': 15, 'player/returning': 14, 'player/portal': 7, 'rankings': 13, 'ratings/sp': 15}.
* Account remaining: 2824 at the first call, 2749 after this run. The budget is shared with CBBD, so other sessions may account for part of the difference.
* /info does not consume quota (two back-to-back /info calls left remainingCalls unchanged).

## Rows per dataset per season (raw CFBD rows)

| season | first kickoff (observed_at) | returning | talent | recruiting classes | SP+ final (same yr) | portal entries | lines games | ranking weeks |
|---|---|---|---|---|---|---|---|---|
| 2014 | 2014-08-27T23:00:00Z | 125 | 0 | 231 | 129 |  | 868 | 17 |
| 2015 | 2015-09-03T22:00:00Z | 128 | 232 | 231 | 129 |  | 870 | 16 |
| 2016 | 2016-08-27T02:00:00Z | 128 | 237 | 238 | 129 |  | 873 | 16 |
| 2017 | 2017-08-26T18:30:00Z | 128 | 157 | 233 | 129 |  | 874 | 16 |
| 2018 | 2018-08-25T21:30:00Z | 130 | 236 | 229 | 131 |  | 884 | 16 |
| 2019 | 2019-08-24T23:00:00Z | 130 | 231 | 223 | 131 |  | 888 | 17 |
| 2020 | 2020-09-04T00:00:00Z | 130 | 219 | 206 | 131 | 0 | 570 | 16 |
| 2021 | 2021-08-28T17:20:00Z | 128 | 224 | 191 | 131 | 1770 | 887 | 16 |
| 2022 | 2022-08-27T16:00:00Z | 130 | 233 | 184 | 132 | 2273 | 1463 | 16 |
| 2023 | 2023-08-26T18:30:00Z | 131 | 238 | 177 | 134 | 2502 | 1416 | 16 |
| 2024 | 2024-08-24T16:00:00Z | 133 | 134 | 194 | 135 | 3378 | 1573 | 17 |
| 2025 | 2025-08-23T16:00:00Z | 134 | 134 | 232 | 137 | 4499 | 1597 | 17 |
| 2026 | 2026-08-27T22:00:00Z | 136 | 138 | 221 |  | 4484 | 1537 | 12 |

## team_season_priors.jsonl

| season | rows | ESPN id matched | current-FBS rows matched | returning | talent | talent prev | recruiting 4yr | SP+ prev | preseason AP | portal |
|---|---|---|---|---|---|---|---|---|---|---|
| 2014 | 231 | 231 (100.0%) | 138/138 | 125 | 0 | 0 | 171 | 125 | 25 | 0 |
| 2015 | 249 | 248 (99.6%) | 138/138 | 128 | 232 | 0 | 214 | 128 | 25 | 0 |
| 2016 | 251 | 250 (99.6%) | 138/138 | 128 | 237 | 229 | 228 | 128 | 25 | 0 |
| 2017 | 235 | 234 (99.6%) | 138/138 | 128 | 157 | 221 | 228 | 128 | 25 | 0 |
| 2018 | 251 | 250 (99.6%) | 138/138 | 130 | 236 | 156 | 228 | 128 | 25 | 0 |
| 2019 | 245 | 244 (99.6%) | 138/138 | 130 | 231 | 231 | 224 | 130 | 25 | 0 |
| 2020 | 239 | 239 (100.0%) | 138/138 | 130 | 219 | 222 | 215 | 130 | 25 | 0 |
| 2021 | 236 | 236 (100.0%) | 138/138 | 128 | 224 | 214 | 208 | 130 | 25 | 236 |
| 2022 | 239 | 238 (99.6%) | 138/138 | 130 | 233 | 220 | 187 | 130 | 25 | 239 |
| 2023 | 244 | 243 (99.6%) | 138/138 | 131 | 238 | 230 | 169 | 131 | 25 | 244 |
| 2024 | 194 | 193 (99.5%) | 137/137 | 133 | 134 | 187 | 171 | 133 | 25 | 194 |
| 2025 | 232 | 231 (99.6%) | 138/138 | 134 | 134 | 134 | 176 | 134 | 25 | 232 |
| 2026 | 221 | 220 (99.5%) | 138/138 | 136 | 138 | 134 | 183 | 136 | 25 | 221 |

Unmatched prior names (no ESPN id): [('Grand Valley State', 'NO_CALL_ESPN_ID_UNSEEN')]

Name cross-check of matched ids: 265 of 265 (school, ESPN id) pairs have an ESPN-history name containing the CFBD school or one of its alternate names. Disagreements: []

## lines.jsonl

| season | games | with any line | rows (game x provider) | completed rows | rows with open spread | rows with moneyline (ML/spread sign conflict) | ESPN event match (games) | ESPN history games |
|---|---|---|---|---|---|---|---|---|
| 2014 | 868 | 865 | 2303 | 2303 | 0 | 0 (0) | 0 (0.0%; swapped 0) | 0 |
| 2015 | 870 | 832 | 2320 | 2320 | 0 | 0 (0) | 0 (0.0%; swapped 0) | 0 |
| 2016 | 873 | 866 | 2347 | 2347 | 0 | 0 (0) | 873 (100.0%; swapped 2) | 1550 |
| 2017 | 874 | 874 | 2400 | 2400 | 0 | 0 (0) | 874 (100.0%; swapped 0) | 1552 |
| 2018 | 884 | 869 | 3034 | 3034 | 0 | 0 (0) | 884 (100.0%; swapped 0) | 1559 |
| 2019 | 888 | 881 | 3506 | 3506 | 0 | 0 (0) | 888 (100.0%; swapped 1) | 1622 |
| 2020 | 570 | 567 | 2752 | 2752 | 0 | 0 (0) | 568 (99.6%; swapped 4) | 592 |
| 2021 | 887 | 887 | 3541 | 3541 | 878 | 739 (23) | 887 (100.0%; swapped 1) | 1569 |
| 2022 | 1463 | 1459 | 4310 | 4310 | 852 | 728 (32) | 1463 (100.0%; swapped 4) | 1597 |
| 2023 | 1416 | 1413 | 3072 | 3072 | 1605 | 1505 (50) | 1416 (100.0%; swapped 7) | 1602 |
| 2024 | 1573 | 1557 | 3172 | 3172 | 2334 | 2339 (61) | 1572 (99.9%; swapped 6) | 1665 |
| 2025 | 1597 | 1597 | 3289 | 3289 | 2495 | 2375 (40) | 1597 (100.0%; swapped 4) | 1686 |
| 2026 | 1537 | 1491 | 2304 | 2110 | 2281 | 1886 (39) | 1315 (85.6%; swapped 2) | 1384 |

Before 2016 the ESPN history is empty, so 2014-2015 games are unmatched by design: CFBD game ids are ESPN event ids, but the backfill only passes one on after the history has confirmed it. The unmatched 2026 games are later than the history snapshot or not yet played.

### Sign checks (home-side spread)

| season | close spread vs away margin (corr) | close total vs points (corr) | formattedSpread favourite vs spread sign: mismatches / checked | open and close >= 3 on opposite sides (rows) |
|---|---|---|---|---|
| 2014 | 0.67 | 0.45 | 0 / 2285 | 0 |
| 2015 | 0.689 | 0.481 | 0 / 2286 | 0 |
| 2016 | 0.664 | 0.436 | 0 / 2333 | 0 |
| 2017 | 0.696 | 0.416 | 0 / 2377 | 0 |
| 2018 | 0.673 | 0.481 | 0 / 3003 | 0 |
| 2019 | 0.719 | 0.405 | 0 / 3478 | 0 |
| 2020 | 0.646 | 0.384 | 0 / 2726 | 0 |
| 2021 | 0.708 | 0.359 | 0 / 3510 | 5 |
| 2022 | 0.701 | 0.446 | 0 / 4270 | 2 |
| 2023 | 0.689 | 0.382 | 0 / 3049 | 11 |
| 2024 | 0.671 | 0.296 | 0 / 3150 | 13 |
| 2025 | 0.723 | 0.342 | 0 / 3262 | 11 |
| 2026 | 0.735 | 0.399 | 0 / 2296 | 4 |

Rows with conflicting signs are flagged per row (`ml_spread_sign_conflict`, `open_close_sign_conflict`), never dropped.

## Portal (per season, cutoff = first kickoff)

| season | entries | dated after cutoff (excluded) | counted with no destination | transferDate range | current-FBS rows with zero portal activity |
|---|---|---|---|---|---|
| 2021 | 1770 | 0 | 717 (40.5%) | 2020-08-01 .. 2021-07-31 | Florida International |
| 2022 | 2273 | 1 | 906 (39.9%) | 2021-08-01 .. 2022-09-07 | none |
| 2023 | 2502 | 0 | 895 (35.8%) | 2020-11-29 .. 2023-07-06 | none |
| 2024 | 3378 | 0 | 724 (21.4%) | 2022-12-18 .. 2024-05-16 | none |
| 2025 | 4499 | 0 | 729 (16.2%) | 2024-10-01 .. 2025-08-23 | none |
| 2026 | 4484 | 18 | 975 (21.8%) | 2025-09-25 .. 2026-10-14 | none |

Joins between tables (and to the portal) use the resolved CFBD team id, not the school name: CFBD spells three FCS schools differently across years and sources (Albany / UAlbany, Southeastern Louisiana / SE Louisiana, Saint Francis (PA) / Saint Francis). FIU's zero in 2021 is in the data: CFBD's 2021 portal list has no FIU entry under any name.

## Semantics

* Team keys: `cfbd_team` (the CFBD school name used by the season tables), `cfbd_team_id` and `espn_team_id`. CFBD team ids are ESPN team ids. An ESPN id is set only when the ESPN history (`data/research/ncaaf_v6/history_snapshot.jsonl`) has seen that id; otherwise the row has `NO_CALL_*` in `espn_match_status`.
* `spread_home_*` is from the CFBD home side (negative = home favoured). `spread_espn_home_*` is from the ESPN home side, with the sign flipped on neutral-site games where the two disagree (`MATCHED_HOME_AWAY_SWAPPED`).
* Moneylines are written only as implied probabilities (`*_ml_implied_prob_close`, vig included) plus a two-way no-vig home probability. No American odds appear in any normalized file.
* The recruiting weights are fixed in advance, not fitted: class Y 0.15, Y-1 0.25, Y-2 0.30, Y-3 0.30. With one class missing the weights are renormalised; with two or more missing the value is null.
