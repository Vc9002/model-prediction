# NBA v10 forward research track: pre-registration

Written 2026-11-21, before any forward record of this track existed and before any of its
statistics was computed. This file is committed before the capture code that produces the forward
logs (`nba_pretip_capture_worker` extras) and before `scripts/nba_v10/forward_report.py` exists;
the report script refuses to score anything unless this file is in `git log`. After that commit
nothing below changes in response to forward data. Plan reference:
`docs/NBA_REVIEW_AND_V10_PLAN_2026-11-20.md` sections 6 (Phase 4), 9 (pinned T-C2), 10.

Research logs only: nothing here touches a ledger, a Main or Flat row, `config/production.yaml`,
a champion, the auto-buyer or any served price.

## 0. What already stands

- Gate G1 FAILED for `nba-player-v10` in all four cells (spread/total x exavail/avail, validation
  n = 762; `outputs/nba/v10_player/G1_PLAYER.md`). That result stands; nothing in this track
  re-opens or re-tunes it. The research artifact keeps lambda = 0.
- The artifact is used exactly as committed: `config/models/research/nba-player-v10.json`,
  `artifact_hash` 945d35a72f2f861bd720edc039df480c30ca0574a39120e46743aa1731a246d0. Its
  coefficients, half-lives (margin 730 d, total 365 d), ridge penalties (30) and minutes model are
  not refit on forward data. No threshold in this file is tuned on forward data.

## 1. Forward inputs and point-in-time rules

| input | written by | PIT rule |
|---|---|---|
| official NBA injury report PDFs, every 15-min slot | the pre-tip worker (this track) | a report is usable from its `fetched_at_utc` (our capture time) on; `published_at_utc` = max(filename slot, PDF header time) is logged and used only to date events |
| ESPN odds (`<runtime>/odds/nba/<ET date>/espn_odds.jsonl`) | the existing pre-tip capture | `observed_at_utc <= t` |
| Polymarket event-detail mids for every game in the ESPN 12 h window (`pm_mids.jsonl`, research dir) | the pre-tip worker (this track), after the existing capture | `observed_at_utc` stamped after the response |
| player box scores | research box file (< 2026-02-23) plus the ESPN summary captures in `data/availability/nba/espn_boxscores/`, joined onto the schedule `data/historical/nba_games_all.jsonl` | the player state for ET date D contains only games on ET dates before D; a schedule game without a box is `has_box = False` and trips the existing `NO_CALL_BOX_GAP` |

Amendment (2026-11-21, before any forward record existed): the schedule is the research
scoreboard index (`data/research/nba_v10/games/scoreboard_index.jsonl`, proper season types)
for the ET dates it covers, then `nba_games_all.jsonl` for later dates. Current-season player
boxes are not captured by any scheduled job today (only the manual
`scripts/backfill_nba_boxscores.py`), so the forward path adds one feed-level fail-closed rule
on top of the model's own `NO_CALL_BOX_GAP`: if either team's most recent completed game on the
schedule has no accepted box, that game's record is `NO_CALL_BOX_FEED_STALE` (no model mean,
never an observation for tests A or B). Each record also logs, per team, how many of its last 5
current-season games lack a box.

Games on or after 2026-02-23 (the locked holdout of the player-layer research) enter the forward
state as rating inputs only, the way a serving path would use them. They are never scored here,
and the research loaders' holdout guards are not loosened (the forward path advances the state
directly).

The model's decision time for availability is the record's own `observed_at_utc` (not tip - 60 min,
which `game_features` hard-codes for the historical walk): the latest stored report with
`fetched_at_utc <= observed_at_utc` that lists the game, at most 18 h old (the existing
`REPORT_MAX_AGE`), otherwise the existing `NO_CALL_INJURY_*` reasons. `observed_at_utc` is
stamped after every slow load of the run.

## 2. Test A: line movement (nba-player-v10)

Decision times: every run of the pre-tip worker writes one `player_log.jsonl` record per game in
its ESPN 12-hour window, with `observed_at_utc = t`.

- Model mean at t: variant `avail` (primary). `exavail` is reported alongside, not gated.
  Spread in the home-margin convention (home minus away), total in points.
- Consensus mean at t: the latest ESPN capture of the game with `observed_at_utc <= t` and at most
  15 minutes older than t, translated to line space with the `nba-anchor-v10` rule
  `mean = line - sigma * Phi^-1(p_low)` (`cli.forecast_nba_anchor_v10.anchor_mean`), sigma from
  `config/models/research/nba-anchor-v10.json` (spread 13.792, total 17.615). Spread line = minus
  ESPN's `home_line`, `p_low` = devigged P(away covers); total `p_low` = devigged P(under).
- x = model mean(t) - consensus mean(t).
- y = consensus mean(T10) - consensus mean(t), where T10 is chosen exactly as gate G2 chooses its
  benchmark: `nba_v10_g2.select_t10_benchmark` (the latest ESPN capture with
  `t < observed_at_utc <= tip - 10 min`, excluded as stale if older than tip - 40 min), scored only
  after tip.
- One observation per game per market: the EARLIEST decision time t for that game at which the
  model mean, the consensus at t and the T10 benchmark all exist.
- Evaluation set: the first 400 games per market in tip order that have an observation. Evaluated
  once, when the 400th game of that market has tipped; below that the verdict is PENDING and
  nothing is used to stop or extend collection. Games after the 400th are reported as descriptive
  only.
- Statistics: Pearson corr(x, y); OLS slope of y on x (with intercept); 95% percentile bootstrap CI
  resampling games (one observation per game, so the game is the cluster), 10,000 draws, seed
  20261121.
- PASS for a market: corr > 0 AND slope > 0 AND the slope CI lower bound > 0. Otherwise FAIL.
  Spearman correlation and the y-on-x scatter deciles are diagnostics only.
- Comparator (not a gate): v9's Flat rows (`nba-spread-margin-v9`, `nba-total-margin-v9`) in
  `ledgers.db`, model mean from the row's stored `model_input_snapshot_json.inference_inputs.mean`
  (home-margin convention for spreads, as `nba-anchor-v10` reads it), t = the snapshot's
  `observed_at_utc`. Same consensus-at-t rule, same y, same earliest-observation rule, same first
  400 games, same statistics. The report also prints corr(v9 mean, consensus mean) as a sign check
  (it must be clearly positive).

## 3. Test B: T-C2 injury latency

Pinned in plan section 9: decisions within 30 minutes after an official injury report changes the
status of a player averaging at least 20 minutes, measured against the Polymarket mid before and
after.

Event definition.

- Report sequence: the stored official reports, ordered by `published_at_utc`. A report is
  compared with the previous stored report only when that one was published at most 20 minutes
  earlier (consecutive slots). After a gap, no event is dated from the first report.
- Statuses: Available, Probable, Questionable, Doubtful, Out, and "unlisted" (the player's team
  submitted for the game and the player is not on it). A team marked NOT YET SUBMITTED in either
  report, or a game missing from either report, yields no event.
- A change into or out of "unlisted" counts only when both reports parsed with zero unplaced
  fragments and every row of the game is consistent (`row_is_consistent`). A change between two
  listed statuses needs the player's row parsed in both reports.
- Qualifying player: the mean of raw box minutes over the player's last 10 played games
  (minutes > 0) on ET dates before the report's game date is >= 20.0, with at least 3 played games,
  and the report name maps to one rotation candidate of the team (`match_report`).
- Event unit: one (game, report) pair with at least one qualifying status change. Several
  qualifying players changing in the same report are one event.

Model delta. With the day's player state, the `avail` variant is computed with the previous report
and with the new report (all status changes of both teams included); d_margin and d_total are
after minus before. A market with |d| < 0.05 points has no direction and is excluded from that
market's statistics (a numerical floor, not a tuned threshold).

Timing. Event time tau0 = `published_at_utc` of the new report. Detection time = its
`fetched_at_utc` (logged; latency = fetched - published is reported). Actionable quotes are polls
with `fetched_at_utc <= observed_at_utc <= tau0 + 30 min`.

Quotes. Polymarket long-side mid from the research event-detail polls, a two-sided quote no wider
than 10c (`nba-anchor-v10`'s `MID_MAX_WIDTH`).

- Rung fixed at the event: per market, the spread and the total rung whose BEFORE mid is closest to
  50c (ties: the smaller absolute line), plus the moneyline. That slug is followed afterwards; the
  balanced rung is never re-picked.
- Before: the latest poll of that slug with `observed_at_utc < tau0`, at most 20 minutes older than
  tau0. Without it the event is logged and excluded (`no_before_quote`).
- After at +10, +20, +30: the latest poll of the slug with `tau0 < observed_at_utc <= tau0 + k`.
- T-10: the latest poll of the slug with `observed_at_utc <= tip - 10 min`, no older than
  tip - 40 min.
- A mid converts to line space with `anchor_mean` at the fixed rung's contract line (home-margin
  convention) and the anchor sigma. p_high is the mid of the home-covers / over side.

Statistics, per market (spread, total), evaluated once at the first 100 events in `published_at`
order that have a direction, a before quote and a +30 quote. Below that: PENDING. Cluster: game.
95% percentile bootstrap resampling games, 10,000 draws, seed 20261121.

- B1, direction (primary): s30 = sign(d) * (mean(+30) - mean(before)), in points. PASS: mean s30 > 0
  with the CI lower bound > 0. The share of events with sign(move) = sign(d) among nonzero moves is
  reported alongside.
- B2, gap within 30 minutes: dp_model = Phi((m_b + d - C) / sigma) - Phi((m_b - C) / sigma) is the
  model's implied change of p_high at the fixed rung (m_b = before mean, C = contract line). At each
  actionable quote q, gap(q) = sign(d) * (dp_model - (p_high(q) - p_high(before))) in percentage
  points. An event has a gap if max_q gap(q) >= 3.0 percentage points (the T-B threshold unit,
  plan section 9). Reported: the share of events with a gap, with its CI.
- B3, does the gap pay (gated on B1 PASS): for gap events, sign(d) * (p_high(T-10) - p_high(q*)) in
  cents, where q* is the first actionable quote with gap >= 3.0. PASS: mean > 0 with the CI lower
  bound > 0, on at least 30 gap events (else INSUFFICIENT). Reported gross; the Polymarket taker
  fee (0.06 p (1 - p), about 1.5c at 50c) is printed next to it.

The moneyline mid moves are logged and reported descriptively, not gated.

## 4. What a result means

- A PASS on test A says the player model anticipates where the sportsbook line goes before tip. It
  is a reason to refit a serving lambda on decision-time lines under a new pre-registration, not a
  promotion.
- A PASS on B1 and B3 says Polymarket lags the official report enough to trade. It is a reason to
  pre-register a T-C2 paper track, not to trade.
- FAIL is final for that statistic on this data; collection is not extended to fish for
  significance.
