# NCAAF settlement, grading and closing-line audit (2026 season)

Run 2026-11-22 01:00-01:55 UTC, read-only. Ledger `E:/model-prediction-runtime/ledgers/ledgers.db`
(`mode=ro`), every NCAAF row not superseded/removed. The live ledger moved during the audit (daily runs
settled ~160 rows at 23:3xZ and 01:3xZ); counts below are from the final pass at ~01:37Z: 2,287 rows
(2,094 settled, 34 archived, 159 open; Main 1,142, Flat 1,145); CLV counts from the last replay at ~01:50Z
(573 settled Polymarket rows). Truth: the ESPN college-football `summary?event=<event_id>` of all 672 events
(cached in `espn_summary_cache/`, refreshed for games that were not final at first fetch). Prices are
shown as probability (cents); no American odds.

Scripts (all in this folder, read-only):
- `fetch_espn.py`, `fetch_scoreboard.py`: ESPN summary per event; the default CFB scoreboard per ET day
  exactly as settlement requests it (`dates`, `limit=100`, no `groups`).
- `analyze_settlement.py` -> `settlement_rows.csv` (one line per row, every check, flags) and
  `settlement_findings.json`.
- `replay_close.py` -> `close_replay.csv`: the CURRENT `cli.settle._closing_probability_for_moneyline_pick`
  (with `ncaaf_settlement_slug` and the after-decision bound) replayed on every settled Polymarket row,
  compared with an independent close (latest OPEN quote of the row's slug after the decision quote and
  before min(ledger kickoff, contract kickoff, ESPN kickoff), side picked by the line sign / Over-Under
  word / nickname).
- `slug_attribution.py` -> `slug_attribution.csv`: does each priced row's contract belong to its game
  (archived contract `event_title` and `event_start_utc` vs the row's teams and kickoff).
- `debug_close.py <pick_id>...`, `repro_stuck.py <pick_id>...`: per-row evidence and a dry run of
  settlement's ESPN match + grade + PnL (no writes).

## Findings, ranked

### F1 (HIGH, 12 settled rows: 10 Flat + 2 Main, all wins, +13.94U recorded, Main +2.40U): rows priced on another game's Polymarket contract are still settled

Same class as the 11-21 integrity item `collided_quote` (archived 23 rows in phases 2-3; see
`data/archive/2026-11-21-ncaaf-integrity-rows/MANIFEST_phase*.json`), but these 12 were not on the list.
Grading itself is right (ESPN by event id, the row's own line), but the line, the price, the edge, the
PnL and any close all belong to a different game, so the "bet" never existed at that price.

| pick_id | tier | row game (ESPN final) | bet | contract actually used (kickoff) | price | result / PnL |
|---|---|---|---|---|---|---|
| 07fa23ae52954ada | flat | Tennessee @ South Carolina | Tennessee +5.5 | asc-cfb-chsou-tenst-2026-10-24-neg-5pt5 (Charleston Southern vs Tennessee State, same kickoff) | 68.1c | win +0.59 |
| e5a035615a50414e | flat | Iowa State @ Arizona | Arizona +7.5 | asc-cfb-iowa-minnst-2026-10-24-neg-7pt5 (Iowa vs Minnesota, 19:30Z vs 23:30Z) | 51.0c | win +1.92 |
| 8321c28ba0ca456e | flat | Purdue 17 @ Penn State 31 | Penn State +1.5 | asc-cfb-penn-brwn-2026-10-31-pos-1pt5 (Penn vs Brown, 16:00Z vs 20:15Z) | 50.0c | win +2.00 |
| 463e7d62b1c64de5 | flat | Boise State @ Colorado State | Boise +2.5 | asc-cfb-more-mrst-2026-11-07-neg-2pt5 (Morehead State vs Marist) | 94.5c | win +0.06 |
| f8d737b1b8644e61 | flat | Boise State @ Colorado State | Over 55.5 | tsc-cfb-cencon-stnh-2026-11-07-total-55pt5 (Central Connecticut vs Stonehill) | 49.0c | win +1.04 |
| 103bc4f68005402b | flat | Sam Houston @ Jacksonville State | JSU -2.5 | asc-cfb-samf-etnst-2026-11-07-neg-2pt5 (Samford vs East Tennessee State) | 53.5c | win +1.52 |
| 1e8c93a5f4ce4a5a | flat | Fresno State @ Utah State | Fresno +7.5 | asc-cfb-librty-nmxst-2026-11-07-neg-7pt5 (Liberty vs NM State) | 50.0c | win +2.00 |
| aad4db471cab4f71 | flat | Fresno State @ Utah State | Under 60.5 | tsc-cfb-sill-indst-2026-11-07-total-60pt5 (Southern Illinois vs Indiana State) | 99.0c | win +0.01 |
| 84a7435ae73447f8 | flat | Texas State @ Oregon State | Texas State +7.5 | asc-cfb-librty-nmxst-2026-11-07-neg-7pt5 | 50.0c | win +1.40 |
| cd201aa13391404d | MAIN | Texas State @ Oregon State | Texas State +7.5 | asc-cfb-librty-nmxst-2026-11-07-neg-7pt5 | 50.0c | win +1.40 |
| 861618c1943a403a | flat | Texas State @ Oregon State | Over 60.5 | tsc-cfb-sill-indst-2026-11-07-total-60pt5 | 50.0c | win +1.00 |
| 766fafab4f2946dc | MAIN | Texas State @ Oregon State | Over 60.5 | tsc-cfb-sill-indst-2026-11-07-total-60pt5 | 50.0c | win +1.00 |

Evidence: `slug_attribution.csv` (contract_title / contract_start vs row teams / start), e.g.
`debug_close.py 8321c28ba0ca456e` shows every archived record of the Penn State row's contract is titled
Penn vs Brown, anchor team "Penn", kickoff 16:00Z, while the row's game kicked at 20:15Z.
Root-cause hypothesis: the v3/v4-era (2026-10-24 .. 11-07) contract-to-event join attached one
snapshot record to several events (the integrity manifest's own description). The current matchers in
`models/college_football.py:354-553` (`_match_cfb_moneyline/_spread/_total`) require both teams to
match by canonical id or `_team_matches`, and no OPEN row and no row after 2026-11-08 shows the pattern
(all 25 open-row "mismatches" in the CSV are spelling variants of the right game: NM State, LA Tech,
UMass, Miami (FL), San Jose State), so this looks like historical residue, not a live bug.
Action for the caller: archive under the same governed path as the 11-21 phases (operator approval).

### F2 (MEDIUM, 21 settled Flat rows, net -2.97U): opening-weekend (2026-08-29) rows carry the fabricated 50c decision price but settled PnL from a different price

All 21 settled NCAAF rows with kickoff before 2026-08-31 (Flat, `espn_consensus`, imported
`legacy-ledger-import-2026-10-25`) have a decision price of exactly 50c
(`decision_raw_implied_probability = 0.5`, `decision_decimal_odds = 2.0`) and
`edge = model_probability - 0.5`
(e.g. ea381bef94d645b0: model 98.0%, consensus 90.0%, edge recorded 48.0%), while `market_probability`,
`american_odds`, `decimal_odds` hold the real consensus price (90.0c) and `pnl_units` was computed from
that (2.0U win -> +0.2222). This is the same fabricated-ask class the 2026-08-30 void note describes
(`61e2a1cdad814465`, `837f9f1ac2f944e9`, `b55eb736b8524001`, archived 11-21), but these 21 were never
voided. The 11 wins have a `pnl_units` that differs from what `ledger.settle` would compute from the
row's own decision fields (`ledger.py:1328` uses `decision_decimal_odds` first -> 2.0):
19ce0365f44d45b0, 5d9019677d97400f, 168367adc7004e74, 3d64dbbd304248d1, 3d8a984b6f2b4b1b,
44bd72e2b35a4d2e, 471546048c1c4f95, 6baec084dd2446df, 7d336336dc224676, a52d27898b5847fd,
ea381bef94d645b0. The 10 losses (-units, price-independent) are c6960e4228f24bb9, 069a8c294c5f4338,
1e9902aa1d474b76, 2c9c560e577c47d7, 35f75567bf10472e, 4ba103d5eb794e1e, 777d4520a2934789,
a3fd0d6da83c4eee, aea038cefe1140a8, b3ad75beb0d94f4a. The edges that qualified them are fabricated;
the PnL is internally inconsistent with the decision record. One of them also has an inverted-looking
line: aea038cefe1140a8 (Jacksonville State +6.5 at North Dakota State) while the sibling consensus
moneyline row 1e9902aa1d474b76 prices Jacksonville State as a 71.4% favourite (the only sign conflict
among 525 ESPN-consensus spreads; graded a loss either way). Decide: void/archive as the 08-30 note
intended.

### F3 (LOW-MEDIUM, CLV coverage): stored closes on 94 rows predate the after-decision fix; 1,518 ESPN-consensus rows can never get a close

Population: the 573 settled, non-void Polymarket rows in the final `close_replay.csv`.
- Stored close vs the current code: 70 same, 2 different, 80 stored but the current code finds no
  qualifying quote, 36 blank but the current code would find one, 385 blank both ways. 115 of the 152
  stored closes are within 0.11c of the row's own entry price, i.e. the decision quote recorded as its
  own close. Every stale one was settled before b7110b65 (2026-11-21 09:37Z) / 9adde04e; newest examples
  f289177cd5c4434a, e5380f09cbfc462b (Main), 4b44651160704f56, settled 2026-11-21 05:30Z. Nothing
  re-derived the history after the fix, so season CLV stats are still mostly zero-CLV noise (the fix is
  known; the un-backfilled residue of 82 wrong + 36 missing closes is the finding).
- No disagreement between the current code and the independent close where both produce one (113
  agree on the replay population incl. archived rows, 0 differ): side, line and timing are right now; no
  close came from the wrong side, another line, a closed book, or after ESPN's kickoff (6 rows my first
  pass tagged "wrong side" are stale decision-quote closes, checked with `debug_close.py`).
- Current-code blanks where a valid quote exists (fail-closed; 18 rows of class `code_none_indep_has`,
  8 not archived, one of which, 8321c28ba0ca456e, is an F1 wrong-contract row):
  - Spread anchor not resolved: `_closing_probability_for_moneyline_pick` (`cli/settle.py:714-728`)
    matches the contract's anchor team with the strict `_team_matches` on raw names, not the NCAAF
    identity (`cfb_team_identity`): "NM State" vs New Mexico State Aggies (881b2c77229146ee), "UMass" vs
    Massachusetts Minutemen (e7572dd7ee2f4850), "San Jose State" vs "San José State Spartans"
    (46e5e2d2a4604e8a), "Appalachian State" vs "App State Mountaineers" (de52a88894054878,
    17da4f63161841fb). The row's line sign already identifies the side (long and short carry opposite
    signs).
  - Moneyline nickname not matched (`cli/settle.py:757-769`):
    Georgia Southern @ Troy, 11d9a44b00e64ee1 (settled 2026-11-22 01:32Z by the current code): long
    "Eagles" ask 74.5c at 20:55Z, but `_team_matches` rejects it because "southern" (a disqualifying
    prefix, `learned_forward.py:1088-1120`) precedes "eagles" in "Georgia Southern Eagles". Same for any
    "<directional> <nickname>" team. Missouri State @ Delaware, 18c67c5ffe454a55: Polymarket's
    "Fightin' Blue Hens" vs ledger "Delaware Blue Hens" (close 57.5c available).
- Why the 385 are blank: none of them kicked off at or after 21:00Z on 2026-11-21. Checked against the
  pre-kickoff capture (55207786): its first run was 20:45:05Z (log
  `ncaaf-prekickoff-capture-20261121T204505Z-a53b8d.log`: 35 captured, 0 failures, 12/12 held in-window
  slugs discovered); the 58 settled rows of 20:30Z games and 7 of 20:45Z games kicked before it could
  quote them (60 s minimum lead). Every Polymarket row kicking off 21:00Z-01:00Z (134 rows, settled or
  open) has at least one quote after its decision quote and before kickoff, and 22 of the 23 settled
  21:00Z rows have a close (the exception is the Georgia Southern row above). So the capture is working;
  the blanks are history.
- 1,518 settled `espn_consensus` rows (1,016 Main) have no close at all: the consensus close at
  `cli/settle.py:376-394` is NFL-only, although `ncaaf_pregame_capture/espn_pregame_snapshots.jsonl`
  (3,824 records since 2026-11-15, DraftKings current spread/total probabilities per event) exists.
  Coverage gap, not a wrong number.

### F4 (LOW, latent, 0 rows affected): settlement's name-match fallback would cross FCS/FBS namesakes and shared mascots

`_find_espn_result` (`cli/settle.py:516-604`) uses the event id first; every one of the 2,287 rows was
on its ET-day default scoreboard (max 80 events/day vs `limit=100`), so the fallback never ran. But the
fallback's matcher (`_team_matches_espn_competitor` -> `polymarket_execute._team_name_matches`,
mascot-token rule at `polymarket_execute.py:142-150`) returns True for: Kansas State Wildcats vs
Arizona Wildcats, Georgia Bulldogs vs Mississippi State Bulldogs, Auburn Tigers vs LSU Tigers, Penn State
Nittany Lions vs Penn Quakers, Iowa State Cyclones vs Iowa Hawkeyes (tested directly). It would fire if a
Saturday feed passes 100 events (truncation; `espn.py:258-266` adds `groups`/`limit=500` only for NCAAB)
or a game moves to another ET date. Both teams must match, so e.g. "Iowa State @ Kansas State" vs "Iowa @
Kansas" on the same day would be graded on the wrong game. Separately, the event-id branch
(`settle.py:551-552`) returns ESPN's own home/away without re-orienting; correct today because all 2,287
rows carry ESPN's orientation (0 swapped, 18 neutral-site rows included).

### F5 (LOW, latent, code-read only, not reproduced): the contract-resolution fallback for NCAAF does not apply the NCAAF slug mapping

`_settle_by_resolution` (`cli/settle.py:973`) calls `_extract_market_slug` without
`ncaaf_settlement_slug`, so an NCAAF moneyline row's slug keeps its `-home/-away` suffix and a v6.2 row's
slug is "anchor-blended": `client.market(slug)` 404s, the row stays pending and is voided after 7 days
(`settle.py:293-297`) even if the contract resolved. Also a whole-number spread loser would be graded a
push by the synthetic scores of `_scores_for_outcome` (`settle.py:915`: margin floor(-line) lands
exactly on the line). Not exercised: no NCAAF row has ever settled through resolution (ledger scores equal
ESPN finals on every row).

### F6 (LOW): entry price is the ask round-tripped through integer American odds

On 466 of 573 settled Polymarket rows `market_probability_at_decision` differs from the rationale's
"Executable ask" (e.g. ask 51.5c stored as 51.4563c; max 0.107c, unbiased: 204 up / 262 down). PnL and
CLV use the rounded value. Net PnL effect +0.11U all tiers (+0.005U Main); CLV error up to +-0.1c, which
matters only because so many closes equal the decision ask (F3).

## Clean checks (with counts)

- Re-grade from ESPN final scores: 2,122 graded rows (657 moneyline, 729 spread, 736 total; 1,167 W /
  945 L / 10 P), 2,122 agree with the ledger result. Includes 93 overtime games, 18 neutral sites, 123
  whole-number spread lines; all 10 pushes are exact landings on whole lines (e.g. 9592028148784917
  home -7, final 14-21).
  The re-grade reuses each row's own line and side, so it proves the score lookup and the formula;
  line orientation was checked separately (next item).
- Line / side orientation at decision time (`line_orientation.py` -> `line_orientation.csv`):
  - Polymarket rows: for 729 of 758 rows the contract record observed at the row's
    `market_quote_observed_at_utc` was found, the row's side resolved independently (spread: the side
    whose description equals the row's line, cross-checked with the anchor team), and the entry equals
    that side's ask (within the 0.11c rounding of F6). 0 rows priced at the other side's ask, 0 matching
    neither. 26 rows have no archived record at their decision time: 10 are F1 rows, 10 are already
    archived collided rows, 6 are 2026-10-31 Notre Dame @ Navy and Bowling Green @ Western Michigan rows
    (d3bda226f5674c40, 6af5e571e4354c0f, 399cecbac0914a12, 90ad1825056a49cb, 9761ceedca744896,
    f5a3ce91c3ea4e97; their slugs and games match, only the decision-time record is not in the archive,
    so orientation unverified). 3 open Arizona @ Kansas State moneyline rows are undecidable this way
    (both sides "Wildcats", anchor "University of Arizona"; the case 2d4596bc addressed).
  - ESPN-consensus spreads: 525 checked; 470 have a sibling consensus moneyline row on the same event, and
    the home-perspective line sign agrees with the moneyline favourite on all but 1 (aea038cefe1140a8,
    F2). The NCAAF pregame capture covered only 1 of these events, no conflict.
- Ledger `away_score`/`home_score` equal ESPN's final on every graded row; ledger home/away equals ESPN's
  on all 2,287 rows (0 swapped).
- Same-name collisions in result lookup: 0 (every row matched by event id).
- PnL formula: 2,128 settled/archived rows checked; `pnl = units*(1/entry-1)` on Polymarket wins,
  `units*(decimal-1)` on ESPN-consensus wins, `-units` on losses, 0 on push/void; the only mismatches are
  the 11 opening-weekend wins (F2) and 1 archived legacy row (b55eb736b8524001).
- Voids: the only settled rows with a void reason are the 3 operator-approved Arizona @ Kansas State rows
  (a73eac39a721493a, fb2d86e1e11f4877, ac24d71045194a76; game in progress at the audit); no game was voided
  as postponed/canceled; no settled row's game is non-final on ESPN. The 3 archived 08-30 voids still
  carry `result` loss/win and non-zero `pnl_units` (archived, out of stats; cosmetic).
- Open rows: 159, none past kickoff + 36 h. The 6 USF @ FAU rows (52bcd5a1bd3742b9 etc.) open 6 h after
  a 19:30Z kickoff are not a bug: the last play was at 2026-11-22 01:30:18Z (long delay), after the
  01:30Z run's settlement pass; `repro_stuck.py` grades them correctly (FAU 35-14). Confirm they settle
  on the next daily run.
- CLV arithmetic: `probability_clv = close - entry` on all 152 rows with a close; the close is the
  selected side's own ask on every row the current code fills (113 agree with the independent close,
  0 differ).
