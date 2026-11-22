# NCAAB historical betting lines from ESPN (quick-261115-1m8, Tier 3)

- AS_OF_UTC: `2026-11-15T03:00:00Z`; generated `2026-11-15T09:18:00Z`
- Script: `scripts/ncaab_espn_lines.py`. Read-only GET of ESPN's public scoreboard and odds endpoints, single worker, generic User-Agent, no price or moneyline field stored.
- Lines file: data/research/ncaab_market/espn_lines.jsonl (untracked), unique events 25880 of 25880 in scope (not fetched: 0), sha256 `3386b8d1f1f8d50fa59bbfb9af0f2c015afff3866ec1a668611147d839e8a4f4`, 91430886 bytes; stored `selected` blocks recomputed from the provider items with 0 mismatches.
- Scoreboard file: data/research/ncaab_market/scoreboard.jsonl (untracked), 820 dates (0 failed, 0 at the 500-event cap), sha256 `ee5394da02380ae9ca4780813ba9cfa851f450d87d94206031c7be81a5d8d1a7`.

## What these lines are

Archived closing/final pregame lines from ESPN's odds feed. There is no per-quote `observed_at`, so they are a closing-line benchmark and NOT decision-time quotes. Live and in-play items are excluded from every selected value. Only spread and total lines are kept. The opening line is the provider's own open block where present and is not a decision-time quote either.

## Pinned selection rule

Rule sha256 `e0f6811dc13c08a0cdcdb30656e46737607333a2de20b9715efe84043cf5bc35` is stamped on every stored record; first fetch started `2026-11-15T06:23:27Z`. Fixed before any outcome was joined; the script never joins outcomes.

- **version**: ncaab-espn-lines-rule-1
- **population**: completed ESPN men's college basketball Division I scoreboard events (groups=50) with season year 2023..2027 (2022-23 through 2026-27 to date); every such event is fetched, events with no odds are kept as no-line records
- **line_semantics**: archived closing/final pregame lines; no per-quote observed_at; a closing-line benchmark, not a decision-time quote. The opening line is the provider's open block where present
- **excluded_items**: any provider item whose name contains 'live' (case-insensitive) or that is flagged live; these are in-play lines
- **home_spread_line_orientation**: negative = home favoured by that many points. Magnitude from the last number in the item's `details` text; side from homeTeamOdds.favorite / awayTeamOdds.favorite (exactly one true). 'EVEN'/'PK'/'PICK' details = 0. If details or flags are unusable, the home team's own point-spread block string is used (direct home-signed value). The raw `spread` field's sign is NEVER assumed and a provider with neither resolution is excluded from the median.
- **per_provider_value**: spread: homeTeamOdds.close point spread when the item carries a close block, else the details/flags-derived current line. total: close.total when present, else overUnder, else current.total. open spread: homeTeamOdds.open point spread; open total: open.total.
- **plausibility_drops**: a spread-field value with |x| > 70 or a total-field value outside [90, 250] is treated as missing (a price or garbage leaking into a line field; ESPN BET 2022-2023 close blocks carry prices) and is dropped BEFORE storage, so no price enters the records, and counted; a close spread is rejected (the details/flags current line is used instead) when it differs from that line by more than 3.0 points; disagreement between the home point-spread block and the details/flags line is counted, never silently resolved
- **primary_line**: per event and market, spread and total separately: the median (statistics.median, unrounded) across eligible non-live providers of their per-provider value; the opening line is the same median over providers' open values
- **also_emitted**: ESPN BET (provider name exactly 'ESPN BET') close value where present; eligible provider count; min/max/range across providers as a dispersion diagnostic
- **no_line_reasons**: http_404, empty_items, only_live_items, no_spread_resolved, fetch_failed
- **outcomes**: never joined in this script

## Scoreboard archive by ESPN season year

| Season year | Events | Completed | Neutral site | A side without conference id |
|---|---|---|---|---|
| 2022 | 6387 | 5976 | 703 | 524 |
| 2023 | 6261 | 6228 | 768 | 512 |
| 2024 | 6249 | 6243 | 746 | 518 |
| 2025 | 6299 | 6292 | 747 | 530 |
| 2026 | 6318 | 6300 | 709 | 551 |
| 2027 | 820 | 817 | 46 | 214 |

## Coverage by season (season year 2023 = 2022-23)

| Season | In scope | Attempted | Non-live spread | % | With total | % | Open spread | Open total | Median providers | No-line reasons | Spread disagree >2 pts (multi-provider) | Total disagree >2 pts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2023 | 6228 | 6228 | 5787 | 92.92 | 5841 | 93.79 | 0 | 0 | 14.0 | {'empty_items': 385, 'no_spread_resolved': 56} | 10.85% of 5734 | 21.66% of 5733 |
| 2024 | 6243 | 6243 | 5818 | 93.19 | 5819 | 93.21 | 5818 | 5819 | 11.0 | {'empty_items': 410, 'no_spread_resolved': 2, 'only_live_items': 13} | 6.11% of 5732 | 23.01% of 5733 |
| 2025 | 6292 | 6292 | 5763 | 91.59 | 5762 | 91.58 | 5763 | 5762 | 1.0 | {'empty_items': 528, 'only_live_items': 1} | None% of 0 | None% of 0 |
| 2026 | 6300 | 6300 | 5744 | 91.17 | 5749 | 91.25 | 5744 | 5749 | 1.0 | {'empty_items': 551, 'no_spread_resolved': 5} | 2.51% of 279 | 2.98% of 302 |
| 2027 | 817 | 817 | 603 | 73.81 | 603 | 73.81 | 603 | 603 | 1.0 | {'empty_items': 214} | None% of 0 | None% of 0 |

## Providers seen

Live items excluded: 9398. Non-live orientation sources: {'details_flags': 149494, 'unresolved': 351, 'details_pick': 813}. Close block present on 69505 non-live items; differs from current on 828.

| Provider (id|name) | Items | Seasons |
|---|---|---|
| 1001|accuscore | 11211 | 2023-2024 |
| 1002|teamrankings | 5708 | 2023-2023 |
| 1004|consensus | 5598 | 2023-2023 |
| 1005|betegy | 42 | 2023-2023 |
| 100|DraftKings | 5408 | 2026-2027 |
| 200|DraftKings - Live Odds | 95 | 2026-2027 |
| 31|Caesars Sportsbook | 5200 | 2023-2023 |
| 36|Unibet | 11353 | 2023-2024 |
| 40|DraftKings | 10746 | 2023-2024 |
| 41|SugarHouse | 10339 | 2023-2024 |
| 45|Caesars Sportsbook (New Jersey) | 10831 | 2023-2024 |
| 47|MGM | 10360 | 2023-2024 |
| 48|PointsBet | 6554 | 2023-2024 |
| 50|BetfairSportsbook | 9509 | 2023-2024 |
| 52|Caesars Sportsbook (Colorado) | 11315 | 2023-2024 |
| 53|Titanbets | 11067 | 2023-2024 |
| 57|Caesars Sportsbook (Tennessee) | 7259 | 2023-2024 |
| 58|ESPN BET | 18158 | 2023-2026 |
| 59|ESPN Bet - Live Odds | 9303 | 2024-2026 |

Flag counts per season over non-live items (see `plausibility_drops`):

- 2023: {'nonlive_items': 77885, 'ps_close_dropped': 5577, 'total_close_dropped': 5639, 'ps_current_dropped': 1072}
- 2024: {'nonlive_items': 60340, 'close_rejected_vs_current': 54, 'ps_current_dropped': 554, 'total_open_dropped': 15}
- 2025: {'nonlive_items': 5763}
- 2026: {'nonlive_items': 6067}
- 2027: {'nonlive_items': 603}

## Re-fetch verification (25 random events)

Seed 20261115, sampled 25, re-fetched at `2026-11-15T09:17:58Z`: **25 of 25 identical** (provider items, derived values, selected lines); 24 of the sampled events carried a line, 24 of those identical.
