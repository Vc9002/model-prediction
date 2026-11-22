# NCAAF v7 challenger serving path: bug hunt (2026-11-21/22)

Scope: `models/cfb_structural_v7.py`, `models/cfb_structural_v7_challenger.py`, `features/cfb_features_v7.py`,
`features/cfb_pbp_rows.py`, `data_sources/cfb_pbp_live.py`, `data_sources/cfbd.py` (prior rows),
`config/models/cfb-structural-v7.json`, runtime store `E:/model-prediction-runtime/research/ncaaf_v7`, and the
v7 hook in `models/college_football.py` (lines 1442-1486). Read-only; HEAD 292e87ac. Experiments were run from the
session scratchpad against the live store with `MODEL_PREDICTION_RUNTIME_ROOT=E:/model-prediction-runtime`.

Live state: v7 wrote its first rows in the 2026-11-21 23:54Z run (Flat tier: 16 moneyline, 16 spread,
15 total). Re-running `price_v7_slate(E:/MP/data, 2026-11-21T23:50Z, ...)` reproduces the logged fair margins
exactly (e.g. VT @ Miami +25.93, Arizona @ Kansas State -10.10, Hawai'i @ Nevada -0.57), 18/18 games priced,
0 no-calls, pbp_rows 6286, score-only games 36.

## Findings (ranked)

### 1. MEDIUM (confirmed): neutral-site games carry a -1.62 pt bias toward the ESPN-designated away team

- Where: `models/cfb_structural_v7.py:140-142` (`means`: `margin_cal["intercept"] + slope * raw_m`), artifact
  `stage2.margin_calibration.intercept = -1.6239`; fit in `scripts/cfb_v7_fit.py:634` (`np.polyfit` pooled
  over home and neutral games).
- Confirmed by construction: two identical teams at a neutral site price at margin -1.62, home win 47.6%
  (about 4 percentage points of ML/cover probability at SD ~16). The observed residual below (+2.6) is larger
  still; the extra ~1.0 +/- 0.8 pts is not explained (possibly who gets designated home at neutral sites).
- Mechanism: the rating core already carries home field (`m.home * sign`, sign 0 on neutral sites). The pooled
  linear margin calibration then adds a constant -1.62 to every game. On home games this offsets an
  over-stated home term; on neutral games there is no home term to offset, so the constant just moves the
  margin toward the away label. Two identical teams at a neutral site price at margin -1.62, home win 47.6%.
- Evidence (research walk `data/research/ncaaf_v7_work/walk_cfbd_portal_m0.5_hl45*.jsonl`, the selected combo,
  served artifact applied to the walk features):

  | split | games | mean(actual - v7 margin) | se |
  |---|---|---|---|
  | train+validation, home games | 6959 | -0.13 | 0.20 |
  | train+validation, neutral | 477 | **+2.62** | 0.77 |
  | holdout, home games | 1833 | -0.43 | 0.37 |
  | holdout, neutral | 115 | **+2.02** | 1.35 |

  Designated home teams at neutral sites outscore v7's projection by about 2.6 points (3.4 se).
- Impact: train/serve parity is intact (research has the same bias), so this is a model error, not a serving
  bug. It hits every neutral-site game: 46 FBS neutral games in the 2025 Dec/Jan postseason (conference
  championships, bowls, CFP), so roughly 45 more from 2026-12-05 on. ML and spread lean away by 1.6 pts by
  construction (2.6 observed); that is ~4-6 percentage points of cover probability, enough to flip the max-edge
  side on near-pick'em lines. Totals are unaffected by the intercept.
- Fix: refit the margin calibration with the intercept applied only to non-neutral games (for example
  `margin = a * (1 - neutral) + b * raw_m`, or a separate neutral intercept), as a new artifact version through
  the promotion command. Interim fail-closed option: no-call v7 on `is_neutral_site` games until the refit.
  Worth checking whether v6.2 has the same pooled-intercept construction (out of this fork's scope).

### 2. MEDIUM (confirmed, train/serve covariate shift): the talent covariate's z-scale changed in 2025/2026

- Where: `features/cfb_features_v7.py:161-184` (`standardise_covariates`: z-scores each field over that season's
  rows). Data: store `cfbd/team_season_priors.jsonl`, field `talent_prev_season`.
- Mechanism: the training seasons have talent for FBS plus many FCS teams. The 2025 and 2026 rows have it for
  FBS only. Verified by classification: talent is non-null for 2023 on 138 FBS + 90 FCS rows, 2024 on 137 FBS +
  50 FCS, 2025 on 134 FBS + 0 FCS, 2026 on 134 FBS + 0 FCS. `cfbd.parse_talent` applies no classification
  filter, so the CFBD `/talent` source itself evidently stopped carrying FCS teams; the fetch was not
  re-checked. Within-season z-scoring therefore measures an FBS team against a different population than the one
  the prior gammas were fit on:

  | season | rows with talent | mean | sd |
  |---|---|---|---|
  | 2016-2023 (train/val) | 156-231 | 318-447 | 254-290 |
  | 2024 | 187 | 445 | 282 |
  | 2025 (holdout) | 134 | 638 | 176 |
  | 2026 (serving) | 134 | 652 | 154 |

  A mid-pack FBS team (talent 450) gets z = -1.31 in 2026 but would have z = +0.35 on the training scale. The
  gamma is 0.77 pts/z on offensive points and -0.38 on defensive points, plus smaller terms through
  ppd/ypp/plays.
- Quantified (live store, served model; covariates recomputed with talent z on the pooled 2016-2023 scale,
  mean 354.7, sd 283.4):
  - Week-1 decision 2026-09-05T12:00Z, 30 FBS-FBS games: mean |delta margin| 0.69 pts, max 1.77.
  - Tonight's slate (2026-11-21T23:50Z, 16 games): mean |delta margin| 0.10 pts, max 0.21. The prior decays as
    games accumulate.
  - The 2025 holdout had the same shift, so the holdout PASS already includes it.
- Fix: freeze each covariate's reference mean/sd in the artifact (computed from the training seasons) and
  standardise with those, or standardise over a fixed population (FBS-only) in every season. Either way it is a
  refit. This is not the accepted "CFBD current-revised values" issue; it is a population/standardisation
  mismatch, and it matters mostly for the 2027 early-season forward gate.
- Smaller related gaps: `rec` coverage drifts from 228 to 169-183 rows. In 2026, Air Force (2005) and Navy (2426)
  have no talent value, and teams 2449 and 16 have no ret or talent, so their prior silently uses the season mean
  (z = 0). That is consistent with training but fail-open by design.

### 3. LOW (confirmed, parity deviation, not a leak): serving decision time differs from the validated decision rule

- Where: `models/cfb_structural_v7.py:459-470` (`price_v7_slate` fits ratings at the run's wall-clock
  `decision_time`). Research rule (artifact `point_in_time.research_decision_time`, `core.decision_time_for`):
  `min(kickoff, 08:00 ET on the kickoff date)`.
- Effect: the 23:50Z run admitted 92 team-game rows (same-day games of other teams) that the research rule
  would not. Against the 08:00 ET rule, tonight's 16 games move by mean |delta margin| 0.16 (max 0.37) and
  mean |delta total| 0.23 (max 0.82). Everything admitted was observed before the decision, so there is no PIT
  leak. It is just not the validated configuration.
- Related: `load_v7_serving_data:369-375` adds score-only rows for any FINAL schedule game without pbp. Research
  did this only for games ESPN never gives pbp. When the crawl is behind, non-slate teams' recent games feed
  points only, or (if the schedule record is still pending) nothing. Slate teams are protected by
  `check_pbp_complete` and fail closed.
- Fix: fit at `min(decision_time, decision_time_for(kickoff))` per game day (one fit per ET game date), and add
  score-only rows only for `terminal_without_pbp` events.

### 4. LOW (suspicion, no live instance): a single HTTP 404 permanently drops a game's play-by-play requirement

- Where: `data_sources/cfb_pbp_live.py` `_fetch_done` (`status == "http_404"` returns True immediately, so the
  game is never refetched) and `terminal_without_pbp` (`http_404` counts as terminal immediately). Compare
  `no_plays`, which is terminal only when fetched 24 h or more after kickoff (`NO_PLAYS_FINAL_AFTER`).
- Effect: if the ESPN summary endpoint briefly 404s for a just-final game, that game becomes score-only for the
  rest of the season and leaves `expected_games`. A slate team is then priced with points only for that game and
  no no-call is raised.
- Live count: 0 `http_404` records in the manifest today (869 seeded `no_plays`, 15,589 `ok`).
- Fix: apply the same 24 h guard to `http_404` (retry until kickoff + 24 h) in both `_fetch_done` and
  `terminal_without_pbp`.

## Freshness of the live pbp store (asked directly)

There is no wall-clock freshness check on the store. Staleness is caught by three mechanisms:

1. `scan_gap`: date-level. Every season date through the decision's ET date must have a
   `first_scanned_at_utc <= T`. Discovery also scans tomorrow, so this proves only that a date was seen once.
2. Slate teams: every admitted, not-not-played schedule game must have a pbp row (`check_pbp_complete`). A
   record still `pending` because its date was never rescanned also counts.
3. The `games.jsonl` completed-game witness.

Experiment with the same 18 games and the store frozen as of now:

| decision | priced | result |
|---|---|---|
| 2026-11-22T14:00Z | 0/18 | `NO_CALL_V7_PBP_MISSING` (tonight's games now admitted, no pbp) |
| 2026-11-23T14:00Z | 0/18 | `NO_CALL_V7_PBP_MISSING` ("1 unscanned date") |
| 2026-11-28T23:50Z | 0/18 | `NO_CALL_V7_PBP_MISSING` ("6 unscanned dates") |

So a stale crawl fails closed for slate teams. The gap is non-slate teams: their recent games that were never
fetched or rescanned drop out of the fit silently. If the schedule says FINAL they enter as points only; if it
says pending they are absent. Either way the opponent adjustment shifts. Low impact.

## Checked, no bug found

- **PIT.** Rows are admitted by `kickoff + 5h <= T` (timestamp, not date). pbp rows, schedule, manifest and the
  FBS lists are all filtered on `observed_at <= T`. Read in `_update_locked`: summaries are fetched only for
  schedule-FINAL events with `kickoff + 5h <= now`, and the summary header must also say final
  (`_summary_final`). CFBD `portal_out` is cut at season first kickoff (`aggregate_portal`); `portal_in` and
  current-season talent are never read.
- **CFBD in-season drift: not testable.** The 2026 rows are `in_season_fetch: true` and there is only one fetch
  (2026-11-21), so revision drift cannot be measured from the store. This falls under the accepted
  current-revised caveat; it was not cleared.
- **Orientation.** `spread_home_line` is passed through, `thr = -line`, `home_cover = P(m > -line)`. The emitted
  line is `+line` for home and `-line` for away. ML uses `P(m > 0)` / `P(m < 0)`, and the zero bin has 0 mass, so
  there are no ties. Neutral gives sign 0 in both the rating rows (`team_game_from_row`, `_score_only_rows`) and
  `matchup_features`. Live rows reproduce exactly.
- **Push.** Polymarket CFB spread and total lines are always half-points (536/536 `polymarket_us` spread rows
  since 11-01), so push mass is never priced.
- **Identity.** ESPN ids are used end to end (scoreboard -> pbp -> slate).
  - CFBD -> ESPN for 2026: 220/221 rows are `MATCHED_ESPN_ID`, and every one was checked against the ESPN display
    name from `games.jsonl`. The only non-prefix pairs are true aliases: Albany/UAlbany, Southeastern
    Louisiana/SE Louisiana, UTRGV/UT Rio Grande Valley. The one unmatched row is Grand Valley State (D-II).
  - No duplicate `espn_team_id` in 2024-2026.
  - The 10 odd FBS-list ids (3144-3147, 3193/3194/3197/3198, 125290/125291) appear in no schedule record and no
    pbp row. They are inert placeholders.
  - Unknown or TBD ids no-call (`NO_CALL_V7_UNKNOWN_TEAM_ID` / `NO_CALL_V7_NO_RATING_HISTORY`) by code reading.
    There was no live TBD game to test against.
- **Fail-open.** Seam exceptions propagate to the per-challenger try/except in `college_football.py:1466-1484`,
  which writes no v7 rows. Malformed results raise and are caught the same way. The artifact hash is checked
  against the registry before pricing.
- **Witness path.** `_live_history_completed` reads `E:/MP/data/processed/ncaaf/games.jsonl`, the same file
  v5/v6.2 use, and it resolves correctly. Note: that file's mtime was 06:01 ET, so it lagged the evening runs.
  That is ingestion cadence, outside v7.
- The Arizona @ Kansas State v7 spread row (+0.393 edge) is the known quote-side label bug (2d4596bc), already
  voided (292e87ac).
- The Hawai'i @ Nevada edges (ML +21.9pp, spread +20.8pp) reproduce exactly from the store, and the inputs look
  sane (10 admitted pbp games each, correct home signs). This is model opinion, not a bug.
- Started events are dropped downstream (`cli/forecast.py::_forecast_cfb_sport`, `event_started`), so v7
  never logs an in-progress game even though `price_challenger_slate` has no kickoff check of its own.
- Observation: v7 ledger rows persist no feature basis (`feature_payload_json.features == {}`,
  `observed_feature_count 0`; the decision payload has no `feature_basis`). Fair margin, total and SDs survive
  only in the rationale string. The contract dict carries `feature_basis` (`build_contracts`), but the ledger
  writer drops it. Worth persisting for the 2027 gate audit.
- Tree check at report time: HEAD 292e87ac; no working-tree changes to the v7 files, `cfbd.py`,
  `college_football.py` or the v7 artifact, so the line cites hold.

## Repro scripts (session scratchpad, not committed)

- `v7_live.py`: reprices the 23:50Z slate from the live store.
- `v7_neutral.py`: neutral vs home residuals on the research walk, plus the identical-teams check.
- `v7_cfbd.py`: per-season CFBD coverage and duplicates.
- `v7_talshift.py`: talent z rescale impact (args: decision, and optionally an alternative slate decision).
- `v7_dectime.py`: 23:50Z vs 08:00 ET fit.
