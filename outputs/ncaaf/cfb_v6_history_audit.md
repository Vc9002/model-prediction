# NCAAF history gap audit and ESPN groups=80/81 backfill (cfb-structural-v6, item 6)

- AS_OF_UTC: `2026-11-15T02:10:00Z`; fetch started `2026-11-15T02:21:59Z`, finished `2026-11-15T02:26:23Z`
- Script: `scripts/cfb_v6_history_backfill.py` (parser `cfb-v6-backfill-1`); read-only GET of the public ESPN scoreboard, groups=80 and groups=81, limit=500, 440 requests, 0 retries, 0 failed after retries
- Division reading: an event only in groups=80 is FBS-FBS, only in groups=81 is FCS-FCS (includes lower-division opponents), in both is FBS-FCS.

## Live history inputs (opened read-only)

- `data/processed/ncaaf/games.jsonl`: rows 7965, new events after dedupe 7965, sha256 `659e33b21de95e02633251c5614df3b63f9fea62898a140bd7efaa83e525ecb7`
- `data/historical/ncaaf_games_all.jsonl`: rows 7965, new events after dedupe 0, sha256 `659e33b21de95e02633251c5614df3b63f9fea62898a140bd7efaa83e525ecb7`
- Loaded events (loader precedence, deduped by event_id): 7965; rejected rows: {}

## Backfill file

- `data/research/ncaaf_v6/ncaaf_games_backfill.jsonl` (gitignored): rows **16378**, sha256 `cc704059e4684edb9e580dff5033433424ee34e6a5fe96995c7c272ae82d8bf4`, 11785384 bytes, first start `2016-08-27T02:00Z`, last start `2026-11-14T23:00Z`

## Seasons covered

- Seasons with every request answered: [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
- Seasons with at least one failed request: []
- Seasons with zero ESPN events: []

## Per season: existing history vs ESPN (FBS-FBS / FBS-FCS / FCS-FCS)

History column shows FBS-FBS / FBS-FCS / FCS-FCS / unclassified. The ESPN-only column is ESPN events whose event_id is not in the history, split by class.

| Season | History classes | History total | ESPN classes | ESPN total | ESPN not in history (by class) | History not in ESPN | Failed/total requests |
|---|---|---|---|---|---|---|---|
| 2016 | 709 / 10 / 15 / 0 | 734 | 760 / 113 / 677 | 1550 | 816 (51 / 103 / 662) | 0 | 0/40 |
| 2017 | 728 / 9 / 14 / 0 | 751 | 776 / 98 / 678 | 1552 | 801 (48 / 89 / 664) | 0 | 0/40 |
| 2018 | 743 / 7 / 14 / 0 | 764 | 772 / 112 / 675 | 1559 | 795 (29 / 105 / 661) | 0 | 0/40 |
| 2019 | 731 / 11 / 11 / 0 | 753 | 774 / 114 / 734 | 1622 | 869 (43 / 103 / 723) | 0 | 0/40 |
| 2020 | 516 / 2 / 0 / 0 | 518 | 534 / 34 / 24 | 592 | 74 (18 / 32 / 24) | 0 | 0/40 |
| 2021 | 748 / 12 / 16 / 0 | 776 | 770 / 117 / 682 | 1569 | 793 (22 / 105 / 666) | 0 | 0/40 |
| 2022 | 756 / 12 / 10 / 0 | 778 | 776 / 120 / 701 | 1597 | 819 (20 / 108 / 691) | 0 | 0/40 |
| 2023 | 769 / 7 / 16 / 0 | 792 | 792 / 118 / 692 | 1602 | 810 (23 / 111 / 676) | 0 | 0/40 |
| 2024 | 782 / 9 / 9 / 0 | 800 | 798 / 121 / 746 | 1665 | 865 (16 / 112 / 737) | 0 | 0/40 |
| 2025 | 780 / 6 / 7 / 0 | 793 | 808 / 126 / 752 | 1686 | 893 (28 / 120 / 745) | 0 | 0/40 |
| 2026 | 495 / 10 / 1 / 0 | 506 | 608 / 123 / 653 | 1384 | 878 (113 / 113 / 652) | 0 | 0/40 |
| all | | 7965 | | 16378 | 8413 | 0 | |

History class source: {'espn_feed': 7965}

## 2026 missing games

- ESPN completed 2026 games (start <= AS_OF): 1384; in history by event_id: 506; **missing: 878** (history holds 506 2026 events in total)
- By class (espn / missing): {'FBS-FBS': {'espn': 608, 'missing': 113}, 'FBS-FCS': {'espn': 123, 'missing': 113}, 'FCS-FCS': {'espn': 653, 'missing': 652}}
- FBS-involved games: 731 on ESPN, 226 missing
- FBS teams: 138; lower-division teams: 182

Per-team missing counts (a missing game counts once for each of its two teams):

| Group | Teams | Mean missing | Median | Max | Zero-missing teams | Mean ESPN games | Mean history games |
|---|---|---|---|---|---|---|---|
| FBS teams | 138 | 2.457 | 2.0 | 7 | 0 | 9.703 | 7.246 |
| Lower-division teams | 182 | 7.786 | 10.0 | 11 | 0 | 7.852 | 0.066 |

FBS team missing-count histogram (missing games -> teams): {1: 19, 2: 61, 3: 44, 4: 10, 6: 2, 7: 2}

FBS teams with the most missing games: Florida International Panthers 7/10; Missouri State Bears 7/10; Sacramento State Hornets 6/11; Sam Houston Bearkats 6/10; Buffalo Bulls 4/10; East Carolina Pirates 4/10; Georgia Southern Eagles 4/10; Jacksonville State Gamecocks 4/10; Kennesaw State Owls 4/10; Marshall Thundering Herd 4/10; Massachusetts Minutemen 4/10; Southern Miss Golden Eagles 4/10; UAB Blazers 4/10; Western Kentucky Hilltoppers 4/10; App State Mountaineers 3/10

Day check for 2026-09-26 (the '16 of 65 games' claim):

- By UTC start date: {'espn_games': 103, 'missing': 59, 'fbs_involved': 58, 'fbs_involved_missing': 14}
- By US-Eastern date: {'espn_games': 116, 'missing': 67, 'fbs_involved': 65, 'fbs_involved_missing': 16}
- Week 4 completed games per feed (80 / 81): {'80': 71, '81': 65}

By ESPN week (st2 = regular season, st3 = postseason):

| Week | ESPN games | Missing | FBS-involved | FBS-involved missing |
|---|---|---|---|---|
| st2-wk1 | 208 | 158 | 99 | 49 |
| st2-wk2 | 131 | 82 | 86 | 37 |
| st2-wk3 | 128 | 73 | 75 | 20 |
| st2-wk4 | 123 | 69 | 71 | 17 |
| st2-wk5 | 114 | 57 | 59 | 3 |
| st2-wk6 | 108 | 108 | 58 | 58 |
| st2-wk7 | 118 | 63 | 62 | 7 |
| st2-wk8 | 111 | 55 | 56 | 0 |
| st2-wk9 | 115 | 59 | 56 | 0 |
| st2-wk10 | 124 | 61 | 63 | 0 |
| st2-wk11 | 104 | 93 | 46 | 35 |

## Overlapping events (same event_id in history and ESPN)

- Events in both: 7965; score disagreements: **0**; home/away swapped: 0; other team-name mismatches: 106; start timestamp disagreements: 0
- Neutral flag: compared 7724, disagreements 0, history rows with no neutral flag 241

## Cross-checks

- ESPN events absent by event_id: 8413; of those present in history under a different id (same start and teams): 0
- Feed label vs team-division label mismatches: 1 [{'event_id': '401551470', 'start': '2023-12-16T17:00Z', 'teams': ['Howard Bison', 'Florida A&M Rattlers'], 'feeds': [80, 81], 'feed_class': 'FBS-FCS', 'division_class': 'FCS-FCS'}]; repo team table lists FCS but ESPN feeds put the team in FBS: 3 team-seasons [[2016, 'Idaho Vandals'], [2017, 'Idaho Vandals'], [2026, 'North Dakota State Bison']]
- Lower-division teams seen in exactly one groups=81 event per season (proxy for non-FCS opponents): {'2016': 46, '2017': 41, '2018': 48, '2019': 47, '2020': 11, '2021': 35, '2022': 41, '2023': 34, '2024': 42, '2025': 43, '2026': 43}

## Fetch integrity

- Rejected events by reason: {'not_completed': 691}
- Completed-event status names: {'STATUS_FINAL': 16372, 'STATUS_FORFEIT': 6}
- Events started after AS_OF dropped: 0; duplicate (same id, same feed) across weeks: 0; feed responses at the 500-event limit: 0; largest response: 159 events
- Failed requests after retries: none

## Regime note

All seasons 2016-2026 were fetched without a failed request. FBS-FBS: share of ESPN games absent from the history is 2.0% to 6.7% over 2016-2025 and 18.6% in 2026. FBS-FCS: share of ESPN games absent from the history is 89.7% to 95.2% over 2016-2025 and 91.9% in 2026. FCS-FCS: share of ESPN games absent from the history is 97.7% to 100.0% over 2016-2025 and 99.8% in 2026. The history is missing FCS-involved games in every fit season as well as 2026, so the backfill changes the rating inputs in the fit years and in the market-check season alike; the regime is consistent across them. Seasons with under 100 ESPN FCS-FCS games in the requested season types: {2020: 24}. The 2020 FCS season was played in spring 2021 and ESPN files it under season type 5, which this script does not request (probed once by hand: 238 completed games, 236 groups=81-only and 2 in both feeds, none of them in the backfill file), so 2020 FCS coverage is understated, not thin on ESPN.

