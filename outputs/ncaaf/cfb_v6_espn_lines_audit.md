# NCAAF historical betting lines from ESPN (cfb-structural-v6, workstream E)

- AS_OF_UTC: `2026-11-15T03:00:00Z`; generated `2026-11-15T04:04:55Z`
- Script: `scripts/cfb_v6_espn_lines.py`. Read-only GET of ESPN's public odds endpoint, one request per event, single worker, generic User-Agent, no price or moneyline field stored.
- Lines file: data/research/ncaaf_v6/espn_lines.jsonl (untracked), unique events 9364 of 9364 in scope (not fetched: 0), sha256 `2b19a2545fb2e5f9df4c55a1b57ee9f88b11eab371828a4a7ed88e8bdfb7fa54`, 41496203 bytes; stored `selected` blocks recomputed from the provider items with 0 mismatches.

## What these lines are

Archived closing/final pregame lines from ESPN's odds feed. There is no per-quote `observed_at`, so they are a closing-line benchmark and NOT decision-time quotes. Live and in-play items (provider names containing 'Live', e.g. 'ESPN Bet - Live Odds') are excluded from every selected value. Only spread and total lines are kept.

## Pinned selection rule

Rule sha256 `ee23e34e91544b797a1feaa9c6ab604fa79597671f6bec110dd34df4b806ee97` is stamped on every stored record; first fetch started `2026-11-15T02:59:33Z`. Fixed before any outcome was joined; the script never joins outcomes.

- **version**: espn-lines-rule-1
- **population**: FBS-FBS and FBS-FCS completed games 2016-2026, forfeits excluded
- **line_semantics**: archived closing/final pregame lines; no per-quote observed_at; a closing-line benchmark, not a decision-time quote
- **excluded_items**: any provider item whose name contains 'live' (case-insensitive) or that is flagged live; these are in-play lines
- **home_spread_line_orientation**: negative = home favoured by that many points. Magnitude from the last number in the item's `details` text; side from homeTeamOdds.favorite / awayTeamOdds.favorite (exactly one true). 'EVEN'/'PK'/'PICK' details = 0. If details or flags are unusable, the home team's own point-spread block string is used (direct home-signed value). The raw `spread` field's sign is NEVER assumed and a provider with neither resolution is excluded from the median.
- **per_provider_value**: spread: homeTeamOdds.close point spread when the item carries a close block, else the details/flags-derived current line. total: close.total when present, else overUnder, else current.total.
- **plausibility_drops**: a spread-field value with |x| > 70 or a total-field value outside [15, 120] is treated as missing (a price or garbage leaking into a line field; ESPN BET 2022-2023 close blocks carry prices) and is dropped BEFORE storage, so no price enters the records, and counted; a close spread is rejected (the details/flags current line is used instead) when it differs from that line by more than 3.0 points; disagreement between the home point-spread block and the details/flags line is counted, never silently resolved
- **primary_line**: per event and market, spread and total separately: the median (statistics.median, unrounded) across eligible non-live providers of their per-provider value
- **also_emitted**: ESPN BET (provider name exactly 'ESPN BET') close value where present; eligible provider count; min/max/range across providers as a dispersion diagnostic
- **no_line_reasons**: http_404, empty_items, only_live_items, no_spread_resolved, fetch_failed
- **outcomes**: never joined in this script

## Coverage by season

| Season | In scope | Attempted | Non-live spread | % | With total | % | Median providers | 404 | No-line reasons | Spread disagree >2 pts (multi-provider) | Total disagree >2 pts | ESPN BET close |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2016 | 873 | 873 | 867 | 99.31 | 864 | 98.97 | 13.0 | 0 | {'empty_items': 6} | 51.74% of 864 | 53.0% of 851 | 0 |
| 2017 | 874 | 874 | 874 | 100.0 | 785 | 89.82 | 10.0 | 0 | - | 24.83% of 874 | 32.87% of 785 | 0 |
| 2018 | 884 | 884 | 870 | 98.42 | 852 | 96.38 | 10.0 | 0 | {'empty_items': 14} | 26.01% of 869 | 24.47% of 842 | 0 |
| 2019 | 888 | 888 | 884 | 99.55 | 884 | 99.55 | 11.0 | 0 | {'empty_items': 4} | 15.72% of 884 | 41.52% of 884 | 0 |
| 2020 | 568 | 568 | 567 | 99.82 | 567 | 99.82 | 11.0 | 0 | {'empty_items': 1} | 25.75% of 567 | 22.93% of 567 | 0 |
| 2021 | 887 | 887 | 882 | 99.44 | 882 | 99.44 | 13.0 | 0 | {'empty_items': 5} | 10.54% of 882 | 10.54% of 882 | 0 |
| 2022 | 896 | 896 | 894 | 99.78 | 894 | 99.78 | 13.0 | 0 | {'empty_items': 2} | 12.99% of 893 | 19.26% of 893 | 849 |
| 2023 | 910 | 910 | 910 | 100.0 | 910 | 100.0 | 11.0 | 0 | - | 14.0% of 907 | 14.66% of 907 | 895 |
| 2024 | 919 | 919 | 875 | 95.21 | 877 | 95.43 | 1.0 | 0 | {'empty_items': 7, 'no_spread_resolved': 2, 'only_live_items': 35} | 83.33% of 12 | 40.0% of 10 | 875 |
| 2025 | 934 | 934 | 924 | 98.93 | 931 | 99.68 | 1.0 | 0 | {'empty_items': 1, 'no_spread_resolved': 9} | 7.41% of 54 | 3.92% of 51 | 877 |
| 2026 | 731 | 731 | 731 | 100.0 | 731 | 100.0 | 1.0 | 0 | - | None% of 0 | None% of 0 | 0 |

Overall: 9278 of 9364 events have a non-live spread, 9177 have a total. Provider disagreement (max minus min across non-live providers) exceeds 2 points on 22.41% of multi-provider spreads (median range 1.0) and 27.22% of multi-provider totals (median range 1.5).

## Providers seen

Live items excluded: 2067. Non-live orientation sources: {'details_flags': 75884, 'unresolved': 960, 'details_pick': 263}. Close block present on 7824 non-live items; differs from current on 235.

| Provider (id|name) | Items | Seasons |
|---|---|---|
| 1001|accuscore | 5761 | 2017-2023 |
| 1002|teamrankings | 5346 | 2016-2023 |
| 1003|numberfire | 3728 | 2016-2021 |
| 1004|consensus | 5765 | 2016-2023 |
| 1005|FanDuel | 108 | 2022-2023 |
| 1007|SportsInsights | 56 | 2016-2019 |
| 100|DraftKings | 834 | 2025-2026 |
| 15|SportsBetting.ag | 853 | 2016-2016 |
| 18|5Dimes.eu | 849 | 2016-2017 |
| 19|BETONLINE.ag | 853 | 2016-2016 |
| 1|SportsInteraction.com | 847 | 2016-2017 |
| 200|DraftKings - Live Odds | 7 | 2025-2026 |
| 20|BOVADA.lv | 819 | 2016-2017 |
| 23|Opening | 868 | 2016-2017 |
| 25|Westgate | 4340 | 2016-2022 |
| 26|BETNOW.eu | 845 | 2016-2016 |
| 28|Caesar's | 1472 | 2017-2018 |
| 31|Caesars Sportsbook | 4416 | 2017-2023 |
| 32|Wynn | 1933 | 2017-2019 |
| 35|CG Technology | 1264 | 2017-2019 |
| 36|Unibet | 5605 | 2017-2024 |
| 37|Betradar | 553 | 2019-2019 |
| 38|Caesars | 1986 | 2018-2020 |
| 3|Sportsbook.com | 742 | 2016-2017 |
| 40|DraftKings | 3897 | 2019-2023 |
| 41|SugarHouse | 3581 | 2019-2024 |
| 43|Caesars Sportsbook (Pennsylvania) | 1329 | 2020-2022 |
| 45|Caesars Sportsbook (New Jersey) | 3092 | 2020-2024 |
| 47|MGM | 2616 | 2020-2023 |
| 48|PointsBet | 2461 | 2020-2023 |
| 4|BetUS.COM | 698 | 2016-2017 |
| 52|Caesars Sportsbook (Colorado) | 2549 | 2021-2024 |
| 53|Titanbets | 2628 | 2021-2023 |
| 57|Caesars Sportsbook (Tennessee) | 906 | 2023-2024 |
| 58|ESPN BET | 3507 | 2022-2025 |
| 59|ESPN Bet - Live Odds | 2060 | 2023-2025 |

## Orientation check (a): against the v5 reference lines

Reference rows 577; matched 577 (exact start 565, same teams within 36 h of the reference start 12; home/away swapped relative to the reference 0, excluded); unmatched 0; comparable 577. 2026 events carry a single provider (DraftKings), so the primary line is that provider's own line.

| Comparison | Pairs | Exact (<=0.01) % | Within 0.5 % | Within 1.0 % | Gap > 3 pts | Mean abs diff | Median abs diff | Sign flips |
|---|---|---|---|---|---|---|---|---|
| spread_vs_reference | 576 | 56.94 | 73.44 | 92.01 | 14 | 0.691 | 0.0 | 13 |
| spread_vs_reference_ref_pair_unique | 344 | 57.27 | 73.84 | 93.6 | 1 | 0.4535 | 0.0 | 2 |
| spread_vs_reference_ref_pair_repeated | 232 | 56.47 | 72.84 | 89.66 | 13 | 1.0431 | 0.0 | 11 |
| total_vs_reference | 575 | 59.13 | 59.13 | 93.74 | 11 | 0.6104 | 0.0 | - |
| total_vs_reference_ref_pair_unique | 343 | 60.64 | 60.64 | 95.34 | 3 | 0.5102 | 0.0 | - |
| total_vs_reference_ref_pair_repeated | 232 | 56.9 | 56.9 | 91.38 | 8 | 0.7586 | 0.0 | - |

The reference is a decision-time capture and these are closing lines, so small differences (and a line crossing zero) are expected; sign flips are listed in full, not explained away. Every flip below has derived orientation supported three ways inside the ESPN item itself (details text, favourite flags, home point-spread block all agree on all 2026 items: see the data-quality section), so the disagreement is in the reference side or in line movement, not in this script's orientation.

| Event | Reference home line | Derived home line | Abs gap | Reference (hl, tl) pair repeats on other rows |
|---|---|---|---|---|
| 401871070 | -3.5 | 3.0 | 6.5 | True |
| 401862808 | -1.5 | 1.5 | 3.0 | False |
| 401856834 | 1.5 | -1.5 | 3.0 | True |
| 401866447 | 2.5 | -14.5 | 17.0 | True |
| 401856838 | 7.5 | -11.5 | 19.0 | True |
| 401860906 | 2.5 | -14.5 | 17.0 | True |
| 401871071 | 1.5 | -2.5 | 4.0 | True |
| 401860910 | 8.5 | -9.5 | 18.0 | True |
| 401858506 | 1.5 | -10.0 | 11.5 | True |
| 401860909 | 8.5 | -2.5 | 11.0 | True |
| 401860908 | 8.5 | -10.0 | 18.5 | True |
| 401860911 | -2.5 | 15.5 | 18.0 | True |
| 401869937 | -1.5 | 1.5 | 3.0 | False |

## Orientation check (b): favourite by line wins straight up

Computed outside this script (the script never joins outcomes) after the rule was pinned: favourite by pinned median line won straight up 7050 of 9260 decided games (76.13%), 0 ties, 18 pick'em lines excluded

| Season | Games with non-zero line | Favourite won | Underdog won | Tie | Favourite win % |
|---|---|---|---|---|---|
| 2016 | 864 | 652 | 212 | 0 | 75.46 |
| 2017 | 872 | 672 | 200 | 0 | 77.06 |
| 2018 | 868 | 663 | 205 | 0 | 76.38 |
| 2019 | 883 | 685 | 198 | 0 | 77.58 |
| 2020 | 567 | 428 | 139 | 0 | 75.49 |
| 2021 | 878 | 659 | 219 | 0 | 75.06 |
| 2022 | 891 | 670 | 221 | 0 | 75.2 |
| 2023 | 907 | 695 | 212 | 0 | 76.63 |
| 2024 | 875 | 643 | 232 | 0 | 73.49 |
| 2025 | 924 | 707 | 217 | 0 | 76.52 |
| 2026 | 731 | 576 | 155 | 0 | 78.8 |

Raw `spread` field vs the derived home line (non-live, details+flags oriented, non-zero lines):

| Season | n | raw == derived | raw == -derived | other |
|---|---|---|---|---|
| 2016 | 10501 | 10501 | 0 | 0 |
| 2017 | 8028 | 8028 | 0 | 0 |
| 2018 | 7710 | 7710 | 0 | 0 |
| 2019 | 9223 | 9223 | 0 | 0 |
| 2020 | 5412 | 5412 | 0 | 0 |
| 2021 | 11202 | 11202 | 0 | 0 |
| 2022 | 11239 | 11239 | 0 | 0 |
| 2023 | 9953 | 9953 | 0 | 0 |
| 2024 | 907 | 907 | 0 | 0 |
| 2025 | 978 | 978 | 0 | 0 |
| 2026 | 731 | 731 | 0 | 0 |

## Data-quality flags and provider deviation

Flag counts per season over non-live items (see `plausibility_drops`):

- 2016: {'nonlive_items': 10595, 'ps_current_dropped': 148}
- 2017: {'nonlive_items': 8055, 'ps_current_dropped': 22, 'raw_over_under_dropped': 6, 'total_current_dropped': 6}
- 2018: {'nonlive_items': 7763, 'raw_over_under_dropped': 36, 'total_current_dropped': 36, 'ps_current_dropped': 76}
- 2019: {'nonlive_items': 9429, 'raw_over_under_dropped': 26, 'total_current_dropped': 26, 'ps_current_dropped': 38}
- 2020: {'nonlive_items': 5918, 'ps_current_dropped': 36}
- 2021: {'nonlive_items': 11273, 'ps_current_dropped': 60, 'raw_over_under_dropped': 2, 'total_current_dropped': 2}
- 2022: {'nonlive_items': 11273, 'ps_close_dropped': 812, 'total_close_dropped': 812, 'ps_current_dropped': 64, 'block_vs_details_conflict': 1}
- 2023: {'nonlive_items': 10172, 'ps_close_dropped': 311, 'total_close_dropped': 313, 'ps_current_dropped': 82, 'close_rejected_vs_current': 22, 'total_open_dropped': 1}
- 2024: {'nonlive_items': 909}
- 2025: {'nonlive_items': 989, 'close_rejected_vs_current': 1}
- 2026: {'nonlive_items': 731}

Per-provider spread deviation from the event median (events with 3+ non-live providers, providers with 30+ items; diagnostic only, the rule is unchanged):

| Provider | Items | Mean abs dev | Mean signed dev | Share > 2 pts % |
|---|---|---|---|---|
| 5Dimes.eu | 849 | 0.159 | -0.007 | 0.24 |
| BETNOW.eu | 845 | 0.156 | -0.009 | 0.24 |
| BETONLINE.ag | 853 | 0.17 | 0.04 | 0.12 |
| BOVADA.lv | 819 | 0.342 | -0.013 | 0.49 |
| BetUS.COM | 698 | 0.155 | -0.016 | 0.14 |
| Betradar | 553 | 0.405 | -0.014 | 0.9 |
| CG Technology | 1264 | 0.281 | -0.033 | 0.55 |
| Caesar's | 1472 | 0.208 | -0.011 | 0.48 |
| Caesars | 1986 | 0.191 | -0.003 | 0.3 |
| Caesars Sportsbook | 4320 | 1.31 | -0.108 | 10.46 |
| Caesars Sportsbook (Colorado) | 2548 | 0.12 | -0.002 | 0.27 |
| Caesars Sportsbook (New Jersey) | 3050 | 0.103 | -0.001 | 0.2 |
| Caesars Sportsbook (Pennsylvania) | 885 | 0.158 | 0.001 | 0.23 |
| Caesars Sportsbook (Tennessee) | 906 | 0.099 | 0.004 | 0.11 |
| DraftKings | 3896 | 0.195 | 0.008 | 0.41 |
| ESPN BET | 1750 | 0.464 | 0.003 | 1.6 |
| FanDuel | 108 | 2.308 | 0.067 | 30.56 |
| MGM | 2579 | 0.2 | -0.027 | 0.12 |
| Opening | 866 | 2.216 | 0.119 | 37.88 |
| PointsBet | 2461 | 0.142 | 0.016 | 0.98 |
| SportsBetting.ag | 853 | 0.17 | 0.04 | 0.12 |
| SportsInteraction.com | 847 | 0.308 | -0.043 | 0.83 |
| Sportsbook.com | 742 | 0.277 | 0.004 | 0.27 |
| SugarHouse | 3496 | 0.325 | 0.003 | 0.83 |
| Titanbets | 2628 | 0.076 | 0.01 | 0.0 |
| Unibet | 5604 | 0.423 | 0.05 | 2.39 |
| Westgate | 4336 | 0.563 | 0.024 | 2.81 |
| Wynn | 1932 | 0.273 | -0.003 | 0.57 |
| accuscore | 5747 | 0.153 | -0.005 | 0.17 |
| consensus | 5758 | 0.478 | -0.046 | 6.2 |
| numberfire | 3655 | 0.325 | 0.028 | 1.42 |
| teamrankings | 5103 | 0.275 | 0.012 | 1.29 |

Provider-seasons deviating more than 2 points from the event median on 5%+ of their items (candidates for unflagged in-play or stale lines; the rule is unchanged, the median is robust to a minority of them):

- Caesars Sportsbook 2017: 15.78% of 862 items
- Caesars Sportsbook 2018: 15.99% of 794 items
- Caesars Sportsbook 2019: 5.99% of 584 items
- Caesars Sportsbook 2020: 9.13% of 504 items
- Caesars Sportsbook 2021: 5.12% of 800 items
- Caesars Sportsbook 2022: 6.94% of 692 items
- Caesars Sportsbook 2023: 22.62% of 84 items
- FanDuel 2022: 25.0% of 32 items
- FanDuel 2023: 32.89% of 76 items
- Opening 2016: 37.82% of 862 items
- Westgate 2020: 15.27% of 393 items
- consensus 2016: 38.11% of 858 items
- teamrankings 2023: 19.23% of 52 items

Non-sportsbook entries (Opening, accuscore, consensus, numberfire, teamrankings) are part of the pinned median. Diagnostic only: leaving them out changes the per-event median on 1136 of 9191 events. All provider items are in the lines file, so a downstream consumer can recompute a book-only median.

| Season | Events | Median differs % | Gap > 0.5 % | Gap > 1.0 % | Mean abs gap |
|---|---|---|---|---|---|
| 2016 | 862 | 12.99 | 1.39 | 0.7 | 0.081 |
| 2017 | 863 | 33.26 | 1.85 | 0.58 | 0.18 |
| 2018 | 804 | 25.62 | 3.11 | 1.0 | 0.114 |
| 2019 | 884 | 20.48 | 1.7 | 0.45 | 0.091 |
| 2020 | 562 | 19.93 | 3.74 | 1.6 | 0.109 |
| 2021 | 882 | 7.48 | 0.11 | 0.0 | 0.025 |
| 2022 | 894 | 9.62 | 0.22 | 0.0 | 0.035 |
| 2023 | 910 | 9.45 | 0.0 | 0.0 | 0.024 |
| 2024 | 875 | 0.0 | 0.0 | 0.0 | 0.0 |
| 2025 | 924 | 0.0 | 0.0 | 0.0 | 0.0 |
| 2026 | 731 | 0.0 | 0.0 | 0.0 | 0.0 |

## Re-fetch verification (25 random events)

Seed 20261115, sampled 25, re-fetched at `2026-11-15T04:03:05Z`: **25 of 25 identical** (provider items, derived values, selected lines); 24 of the sampled events carried a line, 24 of those identical.
