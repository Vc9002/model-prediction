# Model Improvement and Feature Research Roadmap

Current checkout status and blockers are maintained in
`docs/PROJECT_STATUS.md`. This roadmap describes candidate research; it does not
grant production or execution status. Re-verify external API access, schemas,
terms, and licensing at implementation time.

This document is the research and promotion contract for the NBA, WNBA, MLB,
NFL, League of Legends, and Counter-Strike 2 models. Its purpose is not to maximize the number of features. Its
purpose is to add information that remains available point-in-time, survives a
fresh out-of-sample test, improves probability quality, and can create positive
expected value at an executable price after costs.

No feature is production-worthy because it sounds predictive. Every candidate
must have an identical historical and forward definition, explicit observation
timestamps, a versioned source, a missingness policy, and an ablation result.

---

## 1. Current-state audit

**Reaudited 2026-08-02.** The original version of this section (below,
items 1-4 and 6) described a real state as of its own writing, but every
one of those specific problems has since been fixed by later sessions'
work — kept here as a resolved log, not an active blocker list, so a
reader doesn't waste time re-litigating settled questions. Item 5 remains
the one genuinely permanent, still-true standing principle; it is not a
bug to close out, it's this project's operating stance.

Production moneyline artifacts as of 2026-08-02 (all real versions moved
well past the "v3" this section originally audited):
`mlb-elo-trend-lr-v7`, `wnba-elo-trend-lr-v4`, `nba-elo-trend-lr-v4`,
`nfl-elo-trend-lr-v4`. MLB's live feature set is now Elo probability, trend
gap, park factor, weather factor, `pitcher_era_gap`, plus
`bullpen_weakness_gap`; NBA/WNBA remain Elo + offensive/defensive trend
gap; NFL remains Elo + trend gap. None of the four leagues' input lists
have grown much beyond what this section originally described — the
sections below (6-9) are the real to-do list for widening them.

### P0 blockers — resolution status

1. ~~**MLB training-serving skew.**~~ **Resolved.** `learned_forward.py`'s
   `pitcher_era_gap` now explicitly calls `pitcher_era_gap_from_history`,
   the same rolling-runs-allowed definition training uses — never an ESPN
   starter-ERA proxy, which was the original mismatch. See that function's
   own comment for the specific prior bug this replaced.
2. ~~**MLB weather mismatch.**~~ **Resolved** (fixed during this session's
   review passes; see DEBUG.md for the specific weather-timing bug and fix).
3. **Static park leakage/regime drift.** Partially addressed, not fully
   closed. `mlb_baseline_refresh.py` now self-throttles a weekly park-
   factor/league-rate refresh so the table doesn't silently drift stale for
   months, and a live ablation this session (2026-08-02) confirmed park
   factor's real, positive contribution with the current table. Not yet
   verified: whether historical training rows use the park-factor version
   that was actually knowable for that specific season/date, vs. one
   current table applied retroactively. Worth a direct check before fully
   closing this item.
4. ~~**The locked holdout is no longer untouched.**~~ **Superseded.** The
   artifacts this specifically warned about are gone — MLB (v7), all four
   live esports titles (v5), and KBO/NPB were all rebuilt this session with
   fresh chronological train/validation/locked-test splits. Still a real
   principle to re-apply the next time any of these artifacts are revised:
   don't re-open a cohort that already influenced a promotion decision.
5. **Profitability is not established.** Still the accurate, permanent
   standing principle — most models remain research/shadow/zero-unit by
   design; only MLB moneyline and WNBA moneyline currently produce real,
   sized Main-ledger calls. Flat `-110` diagnostics remain diagnostics, not
   proof of executable-price profitability. Keep this as the standard every
   new candidate is held to, not a one-time fix.
6. ~~**Documentation and artifacts disagree.**~~ **Substantially resolved.**
   DEBUG.md now carries an extensive, continuously reconciled record of
   real verified numbers (test counts, ruff findings, settled records,
   backtest results) for every change made this session. Not a guarantee
   against future drift — re-check if a future session's numbers stop
   matching what's actually in the ledgers.

---

## 2. What “better” means

Accuracy and profitability are different objectives. A feature can improve one
and damage the other.

### Predictive gate

A candidate should be compared with the declared incumbent and with a simple
baseline using:

- Brier score and log loss on all forecasts;
- calibration intercept, calibration slope, ECE, and reliability plots;
- discrimination and selectivity only as secondary diagnostics;
- score-distribution accuracy for spreads/totals, not only binary hit rate;
- uncertainty intervals around every delta, not only point estimates;
- coverage and results by missing-feature cohort.

Brier and log loss are proper scoring rules: unlike raw hit rate, they reward
honest probability estimates. Calibration is especially important when a model
is used to estimate expected value and size exposure.

### Economic gate

Economic activation is a separate test. It requires:

- a timestamp-valid executable ask for the chosen side;
- the opposing side's executable quote when calculating a no-vig market
  baseline;
- exact venue fees, spread/slippage assumptions, and order-size constraints;
- expected value at the decision timestamp;
- realized ROI, CLV, maximum drawdown, and bootstrap confidence intervals;
- comparison with the no-vig market forecast on the same events;
- enough independent dates and teams to avoid one streak or one franchise
  driving the result.

No reconstructed postgame price, midpoint-only quote, generic `-110` payout, or
indicative event-list price can pass the economic gate.

### Promotion rule

A feature may enter the independent probability model only if it improves
proper-score performance on a fresh test without damaging calibration or
causing unacceptable coverage loss. A feature may enter a market-aware residual
or decision layer only if it also improves net performance at executable prices.

If a feature improves prediction but lacks economic evidence, its correct status
is `PREDICTIVE_RESEARCH_ONLY`. If it needs data available only shortly before
the event, it should power a late-horizon model or a no-call gate, not be filled
with a neutral value and described as observed.

---

## 3. Point-in-time feature contract

Every source observation should carry at least:

| Field | Meaning |
|---|---|
| `event_id` | Canonical event identifier |
| `entity_id` | Canonical team, player, venue, official, or market identifier |
| `feature_name` | Stable semantic name; never reuse for a different variable |
| `value` | Raw value before model transformation |
| `effective_at_utc` | When the fact became applicable |
| `observed_at_utc` | When this system actually obtained it |
| `source` | Provider and endpoint/report type |
| `source_version` | Dataset, formula, or model version |
| `available` | Whether the value was genuinely observed |
| `missing_reason` | Unknown, not published, stale, source failure, or not applicable |
| `snapshot_hash` | Immutable content hash |

The model input for a decision at time `T` may use only observations with
`observed_at_utc <= T`. Corrections published after `T` remain excluded even if
their `effective_at_utc` is earlier.

### Separate decision horizons

Do not force one model to behave as if all information exists all day. Maintain
distinct, calibrated horizons such as:

- **early:** 24-48 hours before start;
- **mid:** 4-8 hours before start;
- **late:** 15-90 minutes before start, after lineups/inactives when available.

Each horizon needs its own coverage report, calibration, and economic result.
The early model should widen uncertainty or no-call when late information is a
primary driver.

---

## 4. Free, no-signup data-source policy

The default feature stack must not require a new account, API key, paid
subscription, or browser login. Paid or authenticated feeds may be evaluated
later as optional replacements, but no NBA, WNBA, MLB, or NFL model should fail
because a paid credential is absent.

### Source preference order

1. Existing cached repository data and immutable snapshots.
2. Official public league reports or public JSON/CSV endpoints requiring no
   authentication.
3. Public, versioned GitHub release files with clear provenance and licensing.
4. Keyless open-data APIs with documented rate limits.
5. Unofficial public endpoints or scrapers, only with caching, throttling,
   schema tests, and a fail-closed fallback.
6. Paid, keyed, or login-only sources as optional upgrades only.

### Default source stack

| Source | Account/key | Free coverage to use | Main limitation |
|---|---|---|---|
| Existing ESPN public client | None | Schedules, final scores, event IDs, box scores, rosters, and some probable-player/game context | Undocumented and unsupported; schemas can change |
| Polymarket US public gateway | None | Market discovery, order books, BBO, and settlement prices | Public read data only; trading and private portfolio endpoints require authentication |
| Open-Meteo free API | None for non-commercial use | Current forecasts, historical forecasts, and previous model runs for venue weather | Free endpoint is non-commercial, rate-limited, and has no uptime guarantee |
| SportsDataverse GitHub releases | None | NBA/WNBA play-by-play, box scores, shots, rosters, officials, and WNBA Stats-derived tables | Derived pipeline; release lag and upstream schema changes must be monitored |
| Official NBA/WNBA injury-report pages and PDFs | None | Timestamped player participation status and report updates | Prospective archive must be built locally; PDFs and layouts can change |
| Baseball Savant through `pybaseball` or direct CSV | None | MLB pitch-level Statcast, batted-ball quality, pitcher/batter, fielding, and catcher inputs | Scraping endpoint is not a contractual API; cache and throttle it |
| nflverse GitHub releases | None | NFL play-by-play from 1999, EPA/success fields, rosters, depth charts, injuries, officials, and aggregated NGS tables where available | Dataset-specific licences/attribution and in-season release lag |
| BO3 public website data endpoint | None | Series-level LoL and CS2 match IDs, timestamps, source team IDs, scores, best-of format, tier, and tournament ID | No published stable API contract; cache, hash, attribute, and keep replaceable |
| Oracle's Elixir public downloads | None | LoL game, player, draft, champion, patch, and team performance detail | Game-level rows require leak-free series grouping; use as enrichment after the series baseline |

### League assignment

| League | Default no-signup sources |
|---|---|
| NBA | Existing ESPN client + SportsDataverse `hoopR` releases + official NBA injury reports + Polymarket US gateway |
| WNBA | Existing ESPN client + SportsDataverse `wehoop` releases + official WNBA injury reports + Polymarket US gateway |
| MLB | Existing ESPN/MLB StatsAPI path + Baseball Savant via cached `pybaseball` pulls + Open-Meteo + local park/venue table + Polymarket US gateway |
| NFL | Existing ESPN client + nflverse release files + official public injury status where available + Open-Meteo + local venue table + Polymarket US gateway |
| LoL | BO3 series results baseline + Oracle's Elixir enrichment + Polymarket US gateway |
| CS2 | BO3 series/map history baseline + public Valve ranking snapshots when reproducibly archived + Polymarket US gateway |

### Deliberately excluded from the default build

- SportsDataIO, Sportradar, Stats Perform, PFF, Second Spectrum, and other paid or
  login-only feeds;
- The Odds API for these four leagues; it requires a key and is not needed when
  prospective Polymarket US BBO is the economic evidence source;
- full NBA/WNBA/NFL optical tracking unless a reproducible free export becomes
  available;
- social-media, beat-reporter, or account-gated injury data as deterministic
  features.
- Riot's developer API as a default dependency because it requires an account
  and key;
- Liquipedia APIs for this project: its published free-use policy excludes
  betting-related projects, even though its MediaWiki API can be queried
  without an authenticated session.

If a high-priority feature cannot be built from this stack, mark it
`OPTIONAL_PAID_SOURCE_BLOCKED` or use it only as a qualitative risk flag. Do not
silently replace it with a differently defined free proxy.

### Reliability rules for free sources

- Cache every raw response before transformation and retain its retrieval time.
- Use conditional requests or scheduled bulk downloads instead of repeatedly
  scraping the same endpoint.
- Pin release URLs and file hashes for GitHub-derived historical data.
- Record source schema/version and fail closed when required fields disappear.
- Keep the last valid local snapshot only when policy permits; never label stale
  data as current.
- Respect attribution, rate limits, robots/terms, and non-commercial restrictions.
- Maintain a provider-independent normalized schema so a source can be replaced
  without changing feature semantics.

---

## 5. Shared feature groups across all four leagues

These groups should be built once as reusable infrastructure, while coefficients
and transformations remain league-specific.

| Feature group | Candidate variables | Primary use | Priority |
|---|---|---|---|
| Availability state | projected active probability, confirmed/inactive flag, minutes/snaps/innings at risk, time since last update | Probability and uncertainty | P0 |
| Venue/environment | home/neutral, altitude, surface, roof state, game-time weather forecast | Margin and total | P1 |
| Regime and continuity | season phase, roster continuity, coach/manager change, expansion/relocation, rule era | Priors and uncertainty | P1 |
| Missingness and freshness | feature age, confirmed vs projected, number of key inputs missing, source disagreement | Uncertainty/no-call gate | P0 |
| Market residual | executable no-vig probability, spread/total context, bid-ask width, liquidity, price movement since first snapshot | Separate market-aware layer | P0 for profitability |

### Rejected score-history and schedule additions

The 2026-07-20 isolated-feature audit rejected `consistency_gap`,
`hot_cold_gap`, `rest_disparity`, `back_to_back_gap`, `games_last_7_gap`, and
`schedule_available` as predictive additions. None produced a reliable positive
effect after validation-direction checks, date-cluster randomization, and Holm
correction across 24 sport-feature tests. `schedule_available` was also constant
or nearly constant and behaved like a cohort marker.

Do not put these variables back into a promotion candidate without a new,
prospectively timestamped hypothesis. Schedule information may remain in
operational diagnostics and no-call context, but it is not a model-improvement
priority.

The market residual layer must remain separate from the independent sports
model. That preserves an honest answer to two different questions:

1. How likely is the outcome based on sports information?
2. Is the currently executable market price wrong enough to trade after costs?

---

## 6. NBA feature roadmap

The NBA market is fast and efficient. The largest plausible edge is not another
team-level rolling average; it is faster and more accurate player-availability
and minutes information.

| Rank | Feature group | Concrete construction | Why it can help | Timing/risk |
|---|---|---|---|---|
| 1 | Player availability and projected minutes | For every rotation player: probability active, projected minutes, restriction flag, starter/bench role, and replacement minutes; aggregate with a versioned player-impact prior | Converts injuries and rest into expected lineup strength instead of a binary team flag | Official reports update repeatedly; high stale-data risk |
| 2 | Shrunk player/lineup impact | Offensive and defensive RAPM-style player effects with multiseason priors; projected five-man lineup strength and uncertainty | Raw on/off and tiny lineup samples are noisy; regularization is essential | Requires possession/stint reconstruction and entity stability |
| 3 | Possession and efficiency decomposition | Projected pace plus opponent-adjusted eFG%, turnover rate, offensive-rebound rate, and free-throw rate; separate offense and defense | Maps directly to expected possessions and points per possession | Available from box/play-by-play history; must be walk-forward |
| 4 | Shot-profile matchup | Rim, short-mid, long-mid, corner-three and above-break-three frequency/efficiency; opponent allowed profile; transition and half-court rates | Captures stylistic interactions hidden by one net-rating number | Tracking data may be licensed; use public zone data only if stable |
| 5 | Lineup continuity and role change | Returning-minute share, games with current starting five, new-starter indicator, usage redistribution after a key absence | Helps early-season and post-trade adaptation | Requires transaction-effective timestamps |
| 6 | Referee crew | Crew foul rate, home/away differential, free-throw effect, interaction with drive rate | May affect totals and foul-dependent matchups | Assignment is late and effects are noisy; P3 only |

### NBA model form

Build a joint pregame score model:

`expected possessions x lineup-adjusted home/away points per possession`

Derive margin, total, spread cover probability, and moneyline probability from
one coherent score distribution. Do not train disconnected binary classifiers
that can imply contradictory moneyline, spread, and total forecasts.

### NBA first ablations

1. Elo/trend control.
2. `+ opponent-adjusted Four Factors + pace`.
3. `+ projected minutes x player impact`.
4. Combined basketball model.
5. Separate market-residual layer using timestamp-valid executable prices.

---

## 7. WNBA feature roadmap

Do not copy NBA coefficients. The WNBA has fewer games, more roster churn,
different game length and schedule structure, and historically thinner public
data. The solution is stronger shrinkage and better availability capture, not
pretending the sample is NBA-sized.

| Rank | Feature group | Concrete construction | Why it can help | Timing/risk |
|---|---|---|---|---|
| 1 | Official availability and minutes | Snapshot each WNBA injury report; projected minutes, restriction, starter/bench role, replacement player, and report freshness | Player availability has outsized impact in a short rotation | The official report is now available, but history must be prospectively archived |
| 2 | Hierarchical player/lineup impact | WNBA-only RAPM with player and lineup priors; partial pooling by role/position; uncertainty grows for low-minute players | Handles sparse lineups without using raw plus-minus | Must not transfer NBA coefficients; priors may share structure only |
| 3 | Pace and Four Factors | Opponent-adjusted pace, eFG%, TOV%, OREB%, FTA rate on 5/10/season horizons with reliability shrinkage | Directly models possessions and efficiency | Public WNBA advanced data exists; verify use and archival terms |
| 4 | Roster and role discontinuity | Returning-minute share, transactions, hardship contracts, expansion/new-franchise flag, coach change | Stabilizes early-season and expansion-era priors | Entity mapping is a kill gate |
| 5 | Overseas/offseason workload | Days since overseas season, games in prior 30/60 days, late arrival to camp | Could explain early-season fatigue and role uncertainty | Data collection is difficult and licensing-sensitive; P2 research |
| 6 | Shot-profile and matchup | Rim/three frequency, assisted-shot rate, transition share, paint touches, opponent allowed profile | Adds matchup context to team efficiency | Public tracking coverage may be incomplete; report coverage explicitly |

### WNBA first ablations

1. Elo/trend control.
2. `+ pace + Four Factors` with heavy shrinkage.
3. `+ projected minutes x WNBA player impact`.
4. `+ roster continuity`.
5. Combined model and a separately calibrated market-residual layer.

### Player-availability implementation status — 2026-07-20

Implemented, but not promoted:

- official timestamped WNBA injury-report PDF capture and normalized snapshots;
- timestamped ESPN event-injury capture to fill explicit statuses omitted from
  the official PDF, with fail-closed handling for explicit source conflicts;
- report/PDF timestamp reconciliation, SHA-256 provenance, submission-status
  tracking, and multi-page table parsing;
- projected minutes × player impact above replacement with explicit status
  probabilities, uncertainty, report age, and home-oriented point gap;
- fail-closed handling for stale/missing reports, unsubmitted teams, unmapped
  players, incomplete 200-minute rotations, and post-decision observations;
- forward-model support only when a future artifact explicitly requests the
  availability feature names; the active `wnba-elo-trend-lr-v3` remains
  unchanged;
- expanded May 14–July 20 reconstruction: 208 official reports covered 180
  scheduled matchups; V3 produced 169 candidates, 164 were settled, and 142
  had conflict-free fully mapped availability inputs. On that paired subset,
  winner accuracy moved from 71.83% to 71.13% while Brier improved from
  0.21278 to 0.20680 (delta -0.00599; paired bootstrap 95% interval -0.01076
  to -0.00119). Seven selections flipped: three corrections and four new
  errors. The 132 games before the original July 17 audit independently showed
  the same pattern: one fewer correct winner but Brier improved by 0.00590.

Keep the feature as a shadow challenger, but do not promote a coefficient from
this reconstruction. The larger sample supports probability-quality signal,
especially at confidence gates, but does not improve unconditional winner
accuracy. The official reports were downloaded retrospectively and the
research impact prior uses heavily shrunk 10-game box plus/minus, not
hierarchical WNBA lineup impact. Keep the collector and feature contract;
replace the impact proxy and score a fresh prospective cohort before
reconsidering activation.

Dallas/Paige Bueckers correction: the official PDF omitted Bueckers, but ESPN's
timestamped event status marked her Out with an undisclosed issue. The merged
feature now captures that. Her isolated absence moves the frozen July 20 Dallas
probability from 67.878% to 59.313%; after all listed New York and Dallas
absences are combined, Dallas remains favored at 66.100%. The status bug is
fixed, but the Dallas edge is not. An Alanna Smith disagreement (official
Doubtful, ESPN Out) makes the production disposition a no-call.

Because the WNBA sample is small, report season-by-season results and use a
fresh prospective cohort. A model chosen after repeatedly viewing a 100-game
holdout is not validated merely because the file calls that cohort “locked.”

---

## 8. MLB feature roadmap

MLB is the clearest case where the current team-score proxy is too lossy.
Starting pitcher, lineup, bullpen, park, roof, and weather are not optional
details; they are major components of the run-generating process.

| Rank | Feature group | Concrete construction | Why it can help | Timing/risk |
|---|---|---|---|---|
| 1 | True starting-pitcher quality | Confirmed starter ID; handedness; opponent-adjusted K-BB%, xwOBA allowed, ground-ball rate, pitch-count/expected innings, days rest; velocity/spin/movement change; uncertainty | Separates the starter from generic team runs allowed and models how long the bullpen is exposed | Starter must be confirmed and every rolling stat cut off before game time |
| 2 | Projected/confirmed lineup quality | Batting-order-weighted xwOBA, K%, BB%, barrel/hard-hit rate and baserunning; platoon splits versus starter hand; difference between projected and confirmed lineup | Captures who will actually take the plate and in what order | Official lineup is late; use separate early and late horizons |
| 3 | Bullpen availability and quality | Reliever-level pitches in prior 1/2/3 days, consecutive-use flags, leverage role, handedness mix, recent velocity, expected available innings, closer/setup absence | Full-game outcomes often depend on which relievers are available, not season bullpen ERA | Must use pregame workload only and handle opener/bulk roles |
| 4 | Park, roof, and game-time forecast | Season-versioned handedness-specific park factors; roof/open state; temperature, dew point, pressure, precipitation, and wind vector projected onto home-plate-to-center-field orientation | Models the physical run environment instead of a crude magnitude-only factor | Train on archived forecasts issued at the same lead time, not realized weather |
| 5 | Pitch-mix/platoon matchup | Expected lineup performance against starter pitch families, velocity bands and movement; team weakness/strength by pitch type with shrinkage | Captures nonlinear batter-pitcher style matchups without relying on tiny head-to-head samples | Avoid batter-vs-pitcher history; it is usually too sparse |
| 6 | Defense and catching | Projected-fielders OAA/fielding run value, catcher framing/blocking/throwing, lineup-position interaction | Converts balls in play and borderline pitches into expected runs | Lower priority than starter/lineup/bullpen; keep season-versioned |
| 7 | Umpire assignment | Called-strike tendency, zone width, walk/strikeout effect, interaction with catcher framing and pitcher command | May affect totals and strikeout/run environment | Late assignment and substantial noise; P3 only |

### Correct MLB model form

Use two reconciled components:

1. **Relative-strength head:** which team has the stronger run differential.
2. **Absolute-intensity head:** the total run environment.

Reconcile both into one away/home run distribution, such as a hierarchical
Poisson/negative-binomial or simulation model with correlated scoring where
supported. Moneyline, run line, and total probabilities must be derived from
that same distribution.

### MLB first ablations

1. Repair and rename current inputs so training and serving match exactly.
2. True starter quality only.
3. Confirmed/projected lineup quality only.
4. Bullpen availability only.
5. Starter + lineup + bullpen.
6. Add season-versioned park and archived game-time forecast.
7. Add pitch-mix/platoon interaction.
8. Evaluate defense/catcher as a later challenger.

The first three groups should be collected prospectively even before the model
is ready. Coefficients cannot recover information that was never timestamped.

### MLB player-availability implementation status — 2026-08-01

Implemented, shadow only, probable-starter unavailability only (rank-1
group above, narrowed to a single binary check per side):

- `data_sources/mlb_injuries.py` captures MLB Stats API's dated IL-transaction
  history (`/v1/transactions`) and a same-day 40-man roster status snapshot
  (`/v1/teams/{id}/roster?rosterType=40Man`), both free and keyless;
- unlike the WNBA PDF report, MLB Stats API transactions are retroactively
  queryable: a single capture fetched at any time, covering a date range that
  includes the target game date, can reconstruct availability as of that date.
  The point-in-time discipline lives entirely in each transaction's own
  `date` field (stored as `reported_date`) — `effectiveDate` (stored as
  `effective_date`, sometimes retroactively backdated, e.g. "placed on IL
  retroactive to a week earlier") is captured for reference only and is never
  used to decide what was knowable as of a decision time. This is the single
  highest-value correctness detail in the feature and has a dedicated
  regression test (`test_retroactively_backdated_effective_date_never_leaks_future_knowledge`
  in `tests/test_mlb_availability.py`);
- `features/mlb_player_availability.py` cross-references the ESPN-reported
  probable starter (via a new sibling helper,
  `data_sources/espn_probables.py::point_in_time_probable_starters`, exposing
  the point-in-time-archived starter *names* that the existing ERA-gap helper
  discarded) against the captured transaction history, and emits
  `probable_starter_unavailable_{home,away}` plus
  `availability_report_age_hours`;
- fail-closed handling for missing transaction data, stale live captures
  (default 24h), unrecognized transaction types, and post-decision
  observations; absence of any IL history for a player defaults to Active
  rather than failing closed, since a clean record is the overwhelming
  common case and the whole point is flagging genuine evidence of
  unavailability, not requiring an explicit "Active" transaction to exist;
- `learned_forward.py`'s generic feature-computation path gained its own
  MLB-scoped dispatch branch (a separate feature-name constant from WNBA's,
  not a union), but no production artifact's `feature_names` config lists
  these names yet — the branch is inert in live production today. This
  matches the WNBA precedent: computed and logged only when explicitly
  requested, never silently adjusting a live forecast;
- `cli.py`'s `daily` command captures roster and transaction snapshots for
  every scheduled MLB team each run (`step5c_mlb_availability`), independent
  of whether any artifact yet consumes the resulting features;
- **2026-08-02 fix**: `UNAVAILABLE_TRANSACTION_MARKERS` didn't recognize
  "sent RHP X on a rehab assignment to Y" (291 distinct real instances) as
  evidence of continued unavailability — a rehab-assignment transaction is
  common and unambiguous (still recovering, not yet activated), and without
  it a player whose original IL-placement transaction fell outside the
  transactions capture's lookback window would silently default to Active.
  Added `"rehab assignment"` to the marker list;
- **2026-08-02 addition**: `features/mlb_player_availability.py` now checks
  a fresh (within `maximum_report_age_hours`, strictly before the decision
  time) live 40-man roster snapshot *first*, falling back to the
  transactions-based reconstruction only when no sufficiently fresh roster
  read exists. The roster snapshot is a direct current-status signal, immune
  to the transactions path's inherent blind spot (an IL placement older than
  the transactions capture's own rolling lookback window, with no
  intervening rehab-assignment transaction to catch it) — this closes that
  gap for any live/near-live decision, while the transactions-based path
  remains the only option for a genuinely historical/backtest decision time
  (a live-only roster read can never cover the past). Result now exposes
  `{home,away}_probable_starter_source` (`"roster"` or `"transactions"`) so
  which path resolved each side is always visible, not just the flag itself.

Explicitly out of scope for this pass, same caveats as WNBA's own
still-unpromoted feature: lineup-regular/position-player tracking, any
probability-adjustment wiring, and any promotion claim. Unlike WNBA, this
feature does not need a prospective-only archive to backtest, because the
underlying transaction data is genuinely retroactively queryable — but it
still has zero track record of catching a real case where an announced
starter actually didn't pitch, since it was only wired up on 2026-08-01.
Do not claim any lift until that track record exists.

### MLB pitching-staff (bullpen) availability — 2026-08-02

Implemented, shadow only: a first real pass at rank-3's "bullpen
availability and quality... closer/setup absence." Coarser than that row's
full description — MLB Stats API roster data identifies position *type*
(Pitcher vs. everyone else), not bullpen *role* (closer/setup/long), so
this reports aggregate pitching-staff health, not a specific reliever's
availability.

`features/mlb_player_availability.py::team_pitching_staff_availability`/
`matchup_pitching_staff_availability` compute the share of a team's
current pitching staff that's unavailable due to injury or an
administrative list, reusing the same live roster-snapshot infrastructure
(and its freshness/future-capture safeguards) as probable-starter
availability. `capture_roster_snapshot` (`data_sources/mlb_injuries.py`)
now also stores each player's `position_type`, needed to identify pitchers
at all — a schema addition, so historical snapshots captured before this
change don't have it (same "no backlog, clock going forward" situation as
the KBO/NPB fix below).

A real design bug caught by testing against live data, before it shipped:
the first version counted `"Reassigned to Minors"` as "unavailable,"
which produced a nonsensical baseline (Yankees 43%, Dodgers 55% of their
pitching staff "unavailable") — being optioned to AAA is routine 40-man-
vs-26-man roster depth management, present for every team on any given
day regardless of health, not an injury signal. Fixed by excluding
`Reassigned to Minors` from both the numerator and denominator entirely
(new `INJURY_OR_ADMIN_LIST_STATUSES` constant in `mlb_injuries.py`,
deliberately narrower than the existing `STATUS_ACTIVE_PROBABILITIES` used
by the per-player starter check, which correctly treats "optioned" as
disqualifying for a *named* player but shouldn't for an *aggregate* health
read). Re-verified live afterward: Yankees 13.3% (2/15), Dodgers 43.5%
(10/23) — the Dodgers number checked against the real roster snapshot and
matches their real, well-documented 2026 pitching injuries (Blake Snell,
Tyler Glasnow, Bobby Miller, and five others, all genuinely on the IL).

Live-only by construction, same constraint as the starter feature's
roster-preferred path: a fresh roster snapshot cannot cover a historical
decision time, and unlike starter availability, there is no transactions-
based fallback for this feature — "how many pitchers are on the active
roster right now" isn't reconstructable from the IL-transaction log alone
(trades/options/call-ups change roster composition in ways a pure IL
filter wouldn't capture). This means, unlike the starter feature, this one
genuinely cannot be backtested against historical games — it only exists
going forward from today.

Wired into `learned_forward.py`'s generic feature-computation path with
its own dispatch branch and feature-name constant
(`PITCHING_STAFF_FEATURE_NAMES`), same shadow-only, inert-until-requested
design as every other availability feature. 7 new tests in
`tests/test_mlb_availability.py`.

### MLB position-player (lineup) availability — 2026-08-02

Implemented, shadow only: a real component of rank-2's "confirmed vs.
projected lineup" — availability, not the full batting-order-weighted
xwOBA/K%/BB%/barrel-rate quality that row describes (that needs Statcast
data this project doesn't have wired in yet). Refactored the pitching-
staff function above into a shared `_team_roster_group_availability`
helper parameterized by a position-type filter, then added
`team_position_player_availability`/`matchup_position_player_availability`
as the non-pitcher counterpart (infielders, outfielders, catchers, DH/
two-way players — everyone with a `position_type` other than `"Pitcher"`
or missing/empty, the latter excluded so a roster snapshot captured before
`position_type` existed in the schema isn't silently miscounted).

Re-verified live: Yankees 18.75% (3/16) unavailable, Dodgers 7.1% (1/14).
Cross-checked the Yankees number against the real roster entries directly
— Aaron Judge (Injured 60-Day), Cody Bellinger (Injured 10-Day), Giancarlo
Stanton (Injured 10-Day), all real, all genuinely on the IL, matches
well-documented real 2026 Yankees injuries. Same live-only constraint as
pitching-staff availability (no transactions-based fallback, forward-only
from today). Wired into `learned_forward.py` with its own dispatch branch
and feature-name constant (`POSITION_PLAYER_FEATURE_NAMES`), same
shadow-only design. 5 new tests in `tests/test_mlb_availability.py`.

---

## 9. NFL feature roadmap

NFL samples are small, games are path-dependent, and quarterback/injury news can
move the fair price materially. Team final scores alone throw away most of the
available signal.

| Rank | Feature group | Concrete construction | Why it can help | Timing/risk |
|---|---|---|---|---|
| 1 | Quarterback identity and uncertainty | Expected starter; backup probability; opponent-adjusted early-down EPA/dropback, CPOE, sack/pressure response, scramble value, designed-run share; injury/practice status | QB is the highest-impact identity feature and a primary no-call gate | Status changes through the week; model each possible starter rather than imputing team average |
| 2 | Stable unit efficiency | Offense/defense early-down pass EPA, rush EPA, success rate, explosive-play rate, sack rate, neutral-situation pace; opponent and game-state adjusted | More stable and predictive than raw points, wins, or late-game garbage-time stats | Exclude kneels and heavily downweight extreme win-probability states |
| 3 | Injury and lineup value | Snap-weighted availability by QB, OL, receiver, pass rusher, coverage and interior defense; unit continuity; replacement quality | Multiple medium injuries can matter more than one headline skill-player tag | Official practice/game reports need observed timestamps and active/inactive confirmation |
| 4 | Protection-pressure matchup | OL continuity and pass-block performance versus opponent pressure/pass-rush profile; pressure without blitz, time to throw, QB under-pressure efficiency | Captures interaction between line health, pass rush, and QB style | Public tracking is partly aggregated/proprietary; use only reproducible data |
| 5 | Drives, pace, and pass tendency | Expected drives, seconds per play, pass rate over expectation, early-down pass rate, no-huddle, fourth-down aggressiveness | Critical for totals and possession count | Coaching changes create regime breaks |
| 6 | Receiver-coverage matchup | Target share/air-yards share, separation/cushion where available, man/zone tendencies, coverage shell, personnel grouping rates | Adds matchup detail beyond aggregate passing efficiency | Tracking access and sample size are limiting; P2 unless licensed |
| 7 | Weather, roof, and surface | Wind speed/direction, precipitation, temperature, roof state, grass/turf, kicker range and QB hand-size/cold interactions only if predeclared | Wind can alter passing, kicking, pace, and totals | Avoid fishing for arbitrary temperature thresholds |
| 8 | Special teams and field position | Opponent-adjusted kicking, punting, return value, starting field position, kicker availability | Close games turn on hidden yards and field-goal conversion | Shrink heavily because attempts are sparse |
| 9 | Coaching/regime | Coordinator/head-coach continuity, play-caller change, fourth-down policy, neutral pace/PROE shift | Helps detect structural changes that Elo updates slowly | Must be effective-dated, not assigned retrospectively |

### NFL model form

Model expected drives and discrete scoring events, then derive the joint score
distribution. Apply Bayesian or empirical-Bayes shrinkage toward preseason and
prior-season priors, with roster continuity controlling how much prior strength
is retained. Widen uncertainty sharply for unresolved quarterback or offensive
line states.

### NFL first ablations

1. Elo/trend control.
2. `+ opponent-adjusted early-down unit efficiency`.
3. `+ starting-QB identity and uncertainty`.
4. `+ snap-weighted injury/line continuity`.
5. `+ pace/PROE/expected drives`.
6. `+ weather/roof` for totals and kicking cohorts.
7. Add protection-pressure matchup only when data coverage is reproducible.

---

## 10. Esports feature roadmap

“Esports” is a market category, not a sport. LoL and CS2 must have separate
models, feature schemas, calibration, holdouts, and artifacts. Call of Duty,
Valorant, Dota 2, Rocket League, Overwatch, and Rainbow Six are discoverable on
the Polymarket US gateway, but remain `MARKET_DISCOVERY_ONLY` until each title
has its own source contract and validation.

### Baseline already supported

The v1 baseline uses completed best-of matches/series, stable source team IDs,
neutral-site Elo, a validation-selected K factor, and a strictly chronological
60/20/20 split. It intentionally has no home advantage, no pooled title data,
no historical market ROI, and zero units. This baseline is a control model, not
evidence that esports is beatable.

| Rank | Shared feature group | Construction | Why it can help | Main failure mode |
|---|---|---|---|---|
| 1 | Point-in-time roster continuity | Starting five, substitutes, join/leave timestamps, days together, prior matches together, player-level rating aggregation | Organization/team IDs survive major roster changes that invalidate old team strength | Retrospectively corrected rosters leak future knowledge |
| 2 | Tournament and format context | Best-of length, stage, elimination status, online/LAN, tier, region, prize/qualification stakes, days since last match | Upset rates and preparation differ sharply by format and event quality | Tier labels and stage names drift across providers |
| 3 | Recency and inactivity | Multi-horizon opponent-adjusted form, time decay, inactivity regression, matches/maps in last 7/30 days | Esports team strength changes faster than major-league team strength | Tuning decay on the final test overfits regime changes |
| 4 | Patch/version regime | Game patch, days since patch, team/player experience on patch, feature missingness for new patches | Patches alter champion/map balance and invalidate stale history | Patch assignment by match date can be wrong near rollout boundaries |
| 5 | Market identity and liquidity | Exact team alias mapping, executable asks both sides, spread, depth, price age, first-to-close movement | Required to test whether probability lift is tradeable after costs | Fuzzy name matching can attach a forecast to the wrong organization |

### League of Legends priorities

1. Player/roster-strength priors by role with effective-dated roster snapshots.
2. Region- and tournament-strength partial pooling so academy/minor-league form
   is not treated as interchangeable with LCK/LPL/LEC competition.
3. Patch-aware champion draft strength: blue/red side, champion priority,
   bans, role flexibility, and composition interactions using only the draft
   known at the model's declared decision horizon.
4. Pre-match team style: gold/xp differential at 15, objective control, lane
   strength, tempo, and comeback/throw rates, opponent adjusted and shrunk.
5. Series conversion from map/game probabilities, explicitly modeling side
   selection and between-game information; never train a series market directly
   on later games from that same series.

Draft features belong to an in-series or post-draft model, not a day-ahead
series model. Mixing those horizons is look-ahead, not better modelling.

### Counter-Strike 2 priorities

1. Effective-dated five-player lineup, stand-ins, coach changes, roster tenure,
   and player-level form with team-context shrinkage.
2. Map-pool strength and veto simulation: per-map opponent-adjusted ratings,
   pick/ban order, side-start effects, and best-of-series conversion.
3. LAN/online, region, travel, event tier, stage, and schedule density.
4. Patch/map-pool era, especially map additions/removals and economy changes;
   legacy CS:GO (`game_version=1`) must never enter the CS2 baseline.
5. Round-level style only after the core path works: pistol/anti-eco conversion,
   T/CT splits, opening-duel conversion, clutch dependence, and economy-state
   performance with heavy shrinkage.

Raw player rating, head-to-head, and recent win percentage are weak shortcuts:
they entangle opponent quality, roster regimes, maps, and event tier. They may
be diagnostics, but should not displace roster and map-pool construction.

### Esports implementation order

1. Maintain the series-level LoL and CS2 backfill and its hashes.
2. Start prospective Polymarket US BBO snapshots now; historical profitability
   cannot be reconstructed honestly later.
3. Build a fail-closed Polymarket-to-source identity map with explicit aliases
   and validity dates.
4. Add effective-dated rosters and roster-aware ratings.
5. Add LoL patch/region/draft enrichment and CS2 map/veto/LAN enrichment in
   separate ablations.
6. Reserve a new prospective cohort after feature selection; do not promote
   from the v1 diagnostic holdout.

### Esports implementation status — 2026-08-02

Item 2 above ("start prospective BBO snapshots now") is done: real captured
Polymarket BBO exists for LOL/CS2/DOTA2/VALORANT since 2026-07-17 (16 days,
28,469 real snapshot lines as of this check). Live picks already price
against these real executable asks, not a synthetic assumption.

LOL/CS2/DOTA2/VALORANT were rebuilt v4->v5 this session: consolidated onto
one shared `expected_win_probability` Elo core (previously four independent
implementations), K-factor reselected by Brier score and confidence
threshold reselected by `units_at_minus_110` (previously both blended
together, which mechanically favors the most restrictive threshold with no
interior optimum — see the fix in `esports.py`).

**A real, unresolved gap, not yet fixed:** the K/confidence-threshold
selection backtest (`outputs/latest/esports-baseline-validation.json`) is
explicitly self-documented as `"profitability":
"not_established_no_point_in_time_market_prices"` — the *locked-test* numbers
used to choose each title's threshold assume a flat -110 price for every
match, because no real captured price history existed yet when that
methodology was built. That's now only partially true (16 days of real BBO
exist), but far too little to redo a fit that was originally validated
against ~1,900-2,900 locked-test matches per title without trading real
statistical power for noise.

This gap plausibly explains an observed divergence: LOL's locked-test
backtest shows 70.3% accuracy on 1,910 selected matches (large, real,
validated), but the small number of real live settled LOL picks so far
(11 research, 3 gated, all on `lol-tiered-elo-v5`) show only 36.4%/33.3%
hit rate. That's roughly 2.4 standard deviations below what the backtest
would predict for a sample this size — notable, but a sample of 11 is
still nowhere near large enough to distinguish "the model doesn't hold up
in live conditions" from "a real but unlucky small sample." CS2 shows a
smaller version of the same pattern (52.2% live vs. an implied ~60%+ from
its own threshold selection); DOTA2 and VALORANT do not show it.

**Recommendation:** do not refit thresholds against the current 16-day
real-price window — it's too small relative to the locked-test sample that
justified the current thresholds and would likely just replace signal with
noise. Instead, keep accumulating real captured BBO (it compounds for
free every day `daily` runs) and revisit a real-price-validated threshold
refit once there's a real captured-odds sample large enough to matter —
plausibly several months out, not immediately. Track LOL/CS2's live hit
rate specifically in the meantime; if the gap persists once the live
sample reaches the same order of magnitude as CS2's locked-test sample
(thousands, unrealistic soon) or even a few hundred picks (more
realistic), that would be real evidence worth acting on.

---

## 10B. KBO and NPB feature roadmap

KBO and NPB need separate artifacts and calibration. They also require a
different target from MLB: the current Polymarket US contracts settle a tied
game to `$0.50`, so the correct side value is `P(win) + 0.5 × P(tie)`. Dropping
ties or labeling them as losses manufactures target error.

The implemented v1 controls use official league regular-season results, stable
team identities, same-day-frozen home-field Elo, an independently measured
league tie rate, validation-selected parameters, and a locked chronological
test. Both remain research-only and zero-unit.

| Rank | Feature group | Point-in-time construction | Why it should help | Failure mode |
|---|---|---|---|---|
| 1 | Starting pitcher | Announced/confirmed identity, handedness, days rest, recent workload, component performance, change flag | Baseball moneylines are starter-sensitive; team Elo averages away the largest game-specific input | Postgame box-score starter leaks into a pregame horizon |
| 2 | Bullpen availability | Reliever pitches/innings and leverage over 1/3/7 days, consecutive use, closer/setup availability | The same bullpen talent has different value when its best arms are unavailable | Season ERA masquerades as current availability |
| 3 | Confirmed lineup and roster | Effective-dated batting order, absences, platoon matchup, foreign-player/active-roster status, player-strength aggregation | Organization identity survives sharp player-strength changes | Retrospectively corrected rosters leak future knowledge |
| 4 | Park and weather | Venue factors plus forecast issue time, temperature, humidity, wind, rain, roof | Run environment affects both win variance and tie likelihood | Realized weather is substituted for the forecast known pregame |
| 5 | Rest/travel/schedule | Distance, home/away sequence, days off, makeup/doubleheader, series game, extra innings prior day | Fatigue is concentrated in relievers and travel transitions | Local dates are converted incorrectly across Korea/Japan/UTC |
| 6 | League/rules regime | Central/Pacific or KBO context, interleague/DH, ball/rule/park changes, monthly scoring baseline | Ratings trained across regimes otherwise become stale | Regime labels are inferred after the fact |
| 7 | Game-specific tie probability | Expected runs, starter/bullpen strength, park, rules, and regulation/extra-inning environment | Contract settlement explicitly pays half on a tie | A binary model silently assumes `P(tie)=0` |
| 8 | Executable market state | Exact identity, BBO, spread, depth, price age, first/closing pregame observations, fees | Required to distinguish probability quality from tradeable edge | Indicative quote or future price is treated as executable evidence |

### International baseball implementation order

1. Maintain official KBO/NPB backfills, manifests, hashes, and canceled-game
   filters; keep postseason out of the regular-season model.
2. Capture every current Polymarket US KBO/NPB contract BBO prospectively.
3. Add official probable/confirmed starters with an `observed_at_utc` and an
   explicit late-change path.
4. Build pitcher and bullpen workload tables from only prior game logs.
5. Add effective-dated rosters and two forecast horizons: day-ahead and
   confirmed-lineup/starter.
6. Add park and archived point-in-time weather, then travel/rest.
7. Promote nothing until a new prospective cohort shows calibration and net
   value against executable asks after spread and fees.

The score-only locked tests are roughly 55% KBO and 57% NPB decisive accuracy.
That is a baseline, not a moat. Tuning more Elo constants is lower value than
acquiring reliable starter, bullpen, and lineup state.

### Real bug fixed 2026-08-02: zero picks were ever actually logged

Item 2 above ("capture every current Polymarket US KBO/NPB contract BBO
prospectively") was running correctly, and `daily` was correctly building
real, priced forecasts every day (5-6 real scheduled games/day) — but a
timestamp-ordering bug in `cli.py::_forecast_international_sport` meant
every single one was silently rejected by `PickRequest.validate()`
(`"observation timestamp cannot be in the future"`) before ever reaching
`research/kbo.xlsx` or `research/npb.xlsx`. Confirmed both ledgers had zero
rows, ever, despite running daily since implementation. Root cause: the
caller captured its own `observed_now` *before* calling
`forecast_international_baseball_slate`, but that function stamps each
contract's `observed_at_utc` using its *own*, later, internal clock read —
so the comparison always failed. Fixed by reordering the capture to happen
after the slate builder returns (see DEBUG.md's 2026-08-02 (later) entry
for the full trace and fix verification). This means every walk-forward/
promotion claim item above ("promote nothing until a new prospective
cohort...") starts from a genuinely empty real cohort as of this fix —
there is no backlog of silently-discarded real picks to recover, only a
clock going forward from today.

---

## 11. Experiment design

### Development and final test

Use nested chronology:

1. **Training folds:** fit coefficients and player/team states using only prior
   observations.
2. **Inner validation folds:** select features, regularization, transformations,
   calibration method, and decision thresholds.
3. **Final test:** evaluate the fully specified candidate once.
4. **Prospective shadow:** require timestamp-valid features and prices before
   economic activation.

Every feature and hyperparameter tried counts as a research trial. Record failed
variants. If the final test has already influenced a decision, relabel it as
development evidence and reserve a new final cohort.

### Ablation table

For each candidate report:

| Variant | Feature coverage | Brier | Log loss | ECE | Market Brier | Calls at executable +EV | Net ROI | CLV | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| incumbent | | | | | | | | | |
| + feature A | | | | | | | | | |
| + feature B | | | | | | | | | |
| + A+B | | | | | | | | | |

Also report results by season/month, favorite/underdog, home/away, probability
bucket, decision horizon, missingness cohort, and the largest team/player
concentrations. Do not order the table only by P&L; that encourages selection on
the noisiest metric.

### Uncertainty

- Bootstrap by event date or week, not by treating correlated games/features as
  fully independent.
- Report confidence intervals for score differences, calibration, CLV, and ROI.
- Track how many variants were evaluated; repeated search raises the bar for a
  claimed win.
- Predeclare the primary metric and minimum meaningful improvement.
- Prefer a simpler model when the interval includes no gain.

---

## 12. Ruthless implementation order

The highest-value work is data provenance, not a more complex algorithm.

**Status reframe, 2026-08-02**: steps 1-2 are substantially done project-
wide (see section 1's resolution log and each sport's own implementation-
status subsection). Center of gravity for new work should shift to step 3
onward. Concretely, in priority order, what's real and unstarted right now:

1. ~~**Repair current parity and truth labels.**~~ **Done** for MLB
   (pitcher_era_gap skew, weather timing — section 1). Park-factor
   season-versioning specifically still unverified (section 1, item 3).
2. ~~**Build prospective snapshot infrastructure from the no-signup stack.**~~
   **Done** for MLB availability (roster + IL transactions), WNBA
   availability (official injury PDFs), and esports/KBO/NPB Polymarket BBO
   (all real, running daily). **Not started**: NFL injury/lineup snapshots,
   NBA/WNBA possession-level snapshot infrastructure (play-by-play/lineup
   archival for the RAPM work in step 3).
3. **Build NBA/WNBA possession and availability models.** Not started.
   WNBA already has the availability *feature* (step 2) but not the
   possession/Four-Factors model or shrunk player-impact (RAPM) layer this
   step actually describes; NBA has neither. Highest-value untouched item
   for either league once each is back in season (both are off-season as
   of 2026-08-02 — 0 rows in any ledger for either right now, so this can
   wait without cost).
4. **Build NFL QB/unit-efficiency and injury states.** Not started at all.
   Also off-season right now (0 rows) — same "no cost to waiting" logic as
   NBA/WNBA.
5. **Build MLB starter/lineup/bullpen states.** Starter quality (rank 1 in
   section 8's table) is now real as of 2026-08-02 (the new availability
   shadow feature) — narrower than this step's full scope (opponent-
   adjusted K-BB%/xwOBA/pitch-count, not just unavailability), but the
   real building block. Lineup quality and bullpen-as-a-feature (distinct
   from Measured Edge's bullpen elasticity, which models aggregate bullpen
   strength, not pregame availability) remain fully unstarted. **This is
   the single highest-value next step given the season is live right now**
   — MLB is the only one of the four major leagues currently in season.
6. **Add environment and interaction features.** MLB park/weather already
   real and validated (section 1, item 3 partial). Pitch-mix/platoon,
   defense/catching, protection-pressure, shot-profile: unstarted.
7. **Build the market-residual/economic layer.** Unstarted project-wide as
   a *separate* layer (some individual models compute edge inline, but no
   dedicated, isolated market-residual model exists per section 5's
   design). Real gap once any independent-probability model is judged
   ready to test against executable prices.
8. **Promote only after a fresh test and user confirmation.** Standing
   policy, not a step to complete — see the Promotion rule in section 2.

What should not be prioritized now:

- deep neural networks on a few hundred WNBA/NFL games;
- raw head-to-head records or tiny batter-vs-pitcher samples;
- social-media sentiment;
- referee/umpire micro-effects before core availability data works;
- optimizing confidence thresholds against the already-opened holdout;
- calling generic `-110` units “profit.”

---

## 13. Verification checklist

Before presenting a feature recommendation:

- [ ] Historical and forward feature code produce the same semantic variable.
- [ ] The default path requires no new account, API key, or paid subscription.
- [ ] Every value has `observed_at_utc`, source, version, and missing reason.
- [ ] No postgame, closing, correction, or future-season information enters the
      independent feature.
- [ ] Train, validation, and final dates are complete and non-overlapping.
- [ ] The final test was not used to select the feature or threshold.
- [ ] Ablations include the incumbent and simple baseline.
- [ ] Proper scores, calibration, coverage, and uncertainty are reported.
- [ ] Economic claims use executable prices and costs from the same timestamp.
- [ ] Market-aware inputs are isolated from the independent model.
- [ ] Daily forward construction was tested with missing/stale providers.
- [ ] Artifact hashes and model/feature versions are reproducible.
- [ ] All research outputs remain zero-unit until explicit promotion.

Run code-quality checks after implementation, not during the research-only
stage:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

Do not regenerate production artifacts, change thresholds, enable filters, or
commit a promotion until Vincent explicitly approves the named candidate.

---

## 14. Research basis and data-source notes

These sources establish feature meaning or empirical motivation. They do not
guarantee that a feature will improve this repository's model.

### Forecast evaluation and profitability

- Gneiting and Raftery, [Strictly Proper Scoring Rules, Prediction, and
  Estimation](https://doi.org/10.1198/016214506000001437): basis for using proper
  probability scores rather than hit rate alone.
- Hubacek, Sourek, and Zelezny, [Machine learning for sports betting: Should
  model selection be based on accuracy or
  calibration?](https://www.sciencedirect.com/science/article/pii/S266682702400015X):
  NBA betting experiments found calibration-based selection more useful for
  profitability than accuracy-based selection.
- Lopez de Prado, [A Data Science Solution to the Multiple-Testing Crisis in
  Financial Research](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3177057):
  motivation for recording trials and discounting repeatedly selected backtests.

### NBA and WNBA

- NBA, [Stats glossary](https://www.nba.com/stats/help/glossary?hidenav=true) and
  [Stats FAQ](https://www.nba.com/stats/help/faq): definitions for possessions,
  pace, ratings, tracking categories, and Four Factors.
- NBA, [official injury report](https://official.nba.com/nba-injury-report-2025-26-season/):
  reporting schedule and continually updated participation states.
- WNBA, [Stats FAQ](https://stats.wnba.com/help/faq/) and
  [lineups tool](https://stats.wnba.com/lineups/lineups-tool/): WNBA-specific
  pace, Four Factors, advanced, and lineup data.
- WNBA, [official injury report](https://www.wnba.com/webview/wnba-injury-report):
  official availability reports and update cadence.
- Petridis and Pelechrinis, [Lineup Regularized Adjusted Plus-Minus
  (L-RAPM)](https://arxiv.org/abs/2601.15000): lineup sparsity and informed-prior
  regularization.

NBA/WNBA Stats data may be visible without being licensed for unrestricted bulk
reuse. Confirm access, archival, and commercial terms before making it a
production dependency.

- SportsDataverse publishes public [NBA `hoopR` and WNBA `wehoop` data
  repositories](https://github.com/orgs/sportsdataverse/repositories) and
  versioned season-level loaders/releases. Prefer release assets over repeated
  live requests, while retaining upstream source and generation timestamps.

### MLB

- MLB, [Statcast glossary](https://www.mlb.com/glossary/statcast): definitions
  and coverage for expected offense, pitch tracking, batted-ball quality,
  defense, running, and catching.
- MLB, [park factor](https://www.mlb.com/glossary/advanced-stats/park-factor),
  [Outs Above Average](https://www.mlb.com/glossary/statcast/outs-above-average),
  and [catcher framing](https://www.mlb.com/glossary/statcast/catcher-framing):
  official definitions for venue, defense, and receiving features.
- Koch and Panorska, [The Impact of Temperature on Major League
  Baseball](https://journals.ametsoc.org/view/journals/wcas/5/4/wcas-d-13-00002_1.xml):
  empirical motivation for a physically specified run-environment feature.
- Open-Meteo, [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api)
  and [Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api):
  archived forecasts and fixed lead-time forecasts. These are preferable to
  realized historical weather when reproducing what was knowable before a game.
- `pybaseball`, [Statcast data interface](https://github.com/jldbc/pybaseball):
  keyless access to Baseball Savant pitch-level data. It is a scraper, so cache
  date partitions and treat endpoint/schema failures as unavailable data.

### NFL

- NFL Next Gen Stats, [glossary](https://nextgenstats.nfl.com/glossary): official
  definitions for tracking, completion probability, CPOE, time to throw,
  separation, cushion, and rushing metrics.
- NFL Football Operations, [Next Gen Stats
  overview](https://operations.nfl.com/gameday/technology/nfl-next-gen-stats):
  tracking-data provenance and advanced-metric categories.
- NFL Football Operations, [Personnel (Injury) Report
  Policy](https://operations.nfl.com/media/2683/2017-nfl-injury-report-policy.pdf):
  practice participation and game-status semantics.
- nflverse, [play-by-play build and data releases](https://github.com/nflverse/nflverse-pbp):
  reproducible public play-by-play infrastructure for EPA, success, pace, and
  game-state features. Verify each release's field definitions and licensing.

Public Next Gen Stats pages do not imply that complete play-level tracking data
is freely downloadable. Treat tracking-dependent features as licensed/P2 until
the actual reproducible source is secured.

### Esports

- [BO3 CS2 match history](https://bo3.gg/matches/finished) and its public
  website data endpoints provide series IDs, timestamps, team IDs, scores,
  best-of format, tier, tournament, and game-version fields without signup.
  BO3 permits reproduction with attribution in its
  [Use of Services](https://bo3.gg/wiki/use-of-services), but does not publish a
  stable API guarantee; normalized snapshots therefore remain replaceable.
- [Oracle's Elixir downloads](https://oracleselixir.com/tools/downloads) provide
  yearly public LoL CSVs. They are richer than the series baseline but are
  game-level, so a market-aligned series pipeline must prevent games later in a
  series from entering an earlier prediction.
- Polymarket US's live `/v2/sports` taxonomy currently exposes LoL, CS2, Call of
  Duty, Valorant, Dota 2, Rocket League, Overwatch, and Rainbow Six. The public
  [Sports API](https://docs.polymarket.us/api-reference/sports/overview) supports
  league and sport event discovery without a trading credential.
- Liquipedia's [API terms](https://liquipedia.net/api-terms-of-use) require
  attribution and throttling, while its published free-plan policy rejects
  betting-related projects. It is deliberately excluded rather than treated as
  a convenient no-key loophole.

### KBO and NPB

- The official [KBO regular-season schedule](https://www.koreabaseball.com/Schedule/Schedule.aspx)
  provides monthly final scores and stable game/team identifiers without a key.
  The underlying website endpoint is not a promised bulk API, so extractions
  are cached and hashed.
- The official [NPB English calendar](https://npb.jp/bis/eng/2025/calendar/)
  provides stable game links, team codes, canceled-game markers, scores, and
  ties without signup. October is excluded in v1 because its calendar mixes
  regular season and postseason without a safe competition field.
- Official [NPB statistics](https://npb.jp/bis/eng/2025/stats/) and
  [player register](https://npb.jp/bis/eng/players/) are the first enrichment
  targets. Effective dates and decision-time availability must be retained.
- Open-Meteo's no-key archived forecast sources can support park/weather
  ablations only when forecast issue time and lead time are fixed.
- Polymarket US public league discovery currently exposes `kbo` and `npb`; the
  public Sports API provides event discovery and BBO access without a trading
  credential. Current contract text specifies 50-cent settlement on a tie.

### Keyless weather and market data

- Open-Meteo states that its free non-commercial API requires [no API
  key](https://open-meteo.com/en/about). Its free endpoint is rate-limited and
  has no uptime guarantee, so retain raw forecast snapshots and a missing-source
  path.
- The [Polymarket US public API](https://docs.polymarket.us/api-reference/introduction)
  exposes markets, events, order books, and BBO without an API key. Authenticated
  trading and private portfolio endpoints remain outside this research-source
  policy.
