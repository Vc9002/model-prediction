# NCAAF v6.2 challenger serving path: bug hunt (2026-11-21)

Scope: `models/cfb_structural_v62.py`, its v6 base (`models/cfb_structural_v6.py`, `features/cfb_features_v6.py`,
`features/cfb_team_identity.py`), artifact `config/models/cfb-structural-v6.2.json`, the hook in
`models/college_football.py::build_cfb_slate`, and the Flat/research write in `cli/forecast.py::_forecast_cfb_sport`.
Read-only review plus experiments (scripts in the session scratchpad; ledger opened `mode=ro`). HEAD 292e87ac.
`cfb_structural_v61.py` is not imported by the v6.2 serving path.

Ledger facts (all v6.2 rows): 416 rows, all `flat`, all created 2026-11-21 (11:21Z to 23:54Z), artifact hash
`0ab674a6...`, code_revision `10dd43cc62965656`. 271 superseded, 81 open, 64 settled (32-31-1). Every one of the
416 rationales says `anchor-blended`: the model-only fallback has never served.

Artifact fact that frames everything: `blend.w_margin = w_total = 1.0` (raw 1.033 / 1.013, clipped). On anchored
rows the served mean IS the DraftKings line; v6 ratings only act as a no-call gate. v6.2 is "DraftKings line vs
Polymarket ask through a key-number pmf".

## Findings, ranked

### 1. MEDIUM, confirmed: zero-edge (floating-point noise) contracts are emitted and logged as 1U Flat calls
`cfb_structural_v62.py:463` `if edge <= 0 ...: return` compares a raw float. When the Polymarket line equals the
DraftKings half-point line and the asks are 0.50/0.50, the balanced pmf gives exactly 0.5 up to rounding
(anchor -5.5 / home line +5.5: home_cover 0.4999999999999987, away_cover 0.5000000000000014), so the away side
"wins" with edge 1.4e-15 and is emitted. `build_contracts` then rounds it to `edge_vs_executable_ask = 0.0`.

Repro (scratchpad `zero.py`): `build_contracts` with anchor margin -5.5, spread home_line 5.5, asks 0.5/0.5 emits
`spread away -5.5 p=0.5 edge=0.0`. The same at -20.5, +3.5, -7.5, +10.5.

Also: the total pmf at mean = line is not 50/50 (truncation at 0 renormalised): over = 0.500259 at 50.5/50.5, so
on equal asks the over is always emitted with edge 0.00026 (a systematic small over lean on ties).

Ledger impact (non-superseded rows): 5 rows with edge exactly 0 (all spread; 2 L, 1 W, 2 open), 12 rows with edge
< 1e-4 (7 total + 5 spread), 31 rows with edge < 0.001. Example: BYU @ Kansas spread away -5.5, p 0.5 vs ask 0.5,
edge 0.0, settled loss at 1U.

Fix: require a real edge before emitting, e.g. `if round(edge, 6) <= 0` (or an explicit epsilon such as 1e-6)
and compute `edge_vs_executable_ask` from the same rounded value; optionally a minimum edge for the challenger.

### 2. MEDIUM, confirmed (audit / PIT evidence): v6.2 rows persist none of their inputs
`build_contracts` sets `"inference_inputs": None` (line 488); `_forecast_cfb_sport` builds
`model_input_snapshot_json` only when `inference_inputs` is not None, and `feature_basis` is never written to
the ledger. Result on 2026-11-21: v5 1051/1051 rows carry an input snapshot and features; v6.2 0/416 (v7 0/47).
`feature_payload_json.features = {}` ("unavailable_not_recorded").

So the anchor value, anchor `observed_at_utc`, anchor record hash, `anchor_status` (anchored / model-only /
stale), SDs and v6 means exist only in memory; the only persisted trace is the rationale string. The point-in-time
rule for the anchor cannot be audited from the ledger, and a model-only fallback row could only be found by
parsing rationale text. I reconstructed the anchors from the capture file (anchors at `created_at` match the
rationale's fair margin/total on 416/416; anchor age 0.33 h median, 0.34 h max), so nothing wrong is visible
today, but nothing would show it if it were.

Fix: persist `feature_basis` (at least anchor_status, anchor_observed_at_utc, anchor_record_hash, anchor margin /
total, margin/total SD, v6 means) through a field the ledger keeps (an input-snapshot schema for v6.2, or a
dedicated JSON column in the decision payload).

### 3. LOW-MEDIUM, confirmed: no quote-age bound, asymmetric with the 12 h anchor cap
`ANCHOR_MAX_AGE_HOURS = 12` was added because "a day-old line priced against a live ask manufactures edge". The
reverse case has no guard: `emit` (line 467-472) only checks `quote_seen <= decision_time`. The champion's
`_load_decision_snapshot_rows` admits any Polymarket row since 00:00 ET of the day, and the per-slug "latest" plus
the most-balanced-rung selector can pick a rung that stopped being captured hours ago.

Ledger: 22 of 416 v6.2 rows were priced on a Polymarket quote 132-517 min older than the decision, against a
DraftKings anchor ~20 min old (quote_obs - anchor_obs as low as -496 min). Examples: Northwestern @ Minnesota
total under 42.5 (quote 517 min old, DK total at decision 41.5); Wofford @ Ole Miss spread home -48.5 (338 min).
Edges on these rows are not inflated in aggregate (median 0.98 pp vs 0.79 pp fresh), but a stale-rung
mismatch between DK and PM lines is exactly the pattern the anchor cap targets.

Fix: in `emit`, decline when the quote is older than a bound relative to the decision (or to the anchor),
e.g. 60-90 min; record the quote age in the basis.

### 4. LOW, confirmed latent: DraftKings filtered by name, not by the declared provider id
`_DRAFTKINGS_NAMES = {"draftkings"}` (line 51) and `.casefold()` matching drop items ESPN spells "Draft Kings".
The artifact declares `provider_id: "100"`. In the capture file, provider id 100 appears 3008 times as
"DraftKings" and 66 times as "Draft Kings" (all in the 2026-11-16T21 capture run; 54 of those records also have
scoreboard provider "Draft Kings", so they yield no anchor at all). No v6.2 decision was affected (no run used
that capture), but a capture run with the alternate spelling on game day would silently push every game to an
older anchor or to model-only pricing.

Fix: match `provider_id == "100"` (the artifact's declared id), or normalise whitespace before comparing.

### 5. LOW, confirmed latent: v6.2 ignores the routes the caller computed and has no registry hash check
`college_football.py:1465` calls `price_challenger_slate(root, obs, challenger_events)` without
`markets=challenger_markets` or an expected artifact hash (v7 gets both, lines 1469-1475). v6.2 then re-derives
its markets in `routed_markets()` (line 573) through `registry.challenger(...)`, which returns only the PRIMARY
(first-listed) challenger. Today v6.2 is listed first for all three markets, so behaviour is right; if the order in
`challengers.NCAAF` changes (v7 first), `_ncaaf_challenger_routes` still routes v6.2 but v6.2 prices nothing for
that market with status "ok". v6.2 also verifies only its own self-hash, not the registry's recorded hash.

Fix: pass `markets=challenger_markets` and an expected hash and fail closed on mismatch, as for v7.

### 6. LOW, confirmed: serving FBS/FCS label comes from the static team table, not feed classes
`price_challenger_slate` loads history with `backfill_path=None`, so no row carries `game_class` (0 of 16,405);
`TeamResolver.from_games` falls back to `resolve_team` tiers (`cfb_features_v6.py:154-163`). Training rows took
`is_fcs` from the backfill's ESPN feed class. Compared with `CFBTeamClasses` (feed groups) for 2026: 4 teams
labelled FCS that the feed says are FBS: Florida International, North Dakota State, Missouri State, Sacramento
State. On 232 sampled 2024-25 games, 5 serving labels differ from the training base rows. The label only picks the
model-only SD slice (fbs_vs_fcs 16.76 vs established 16.35) and an unpersisted basis field, so the impact is small
today (no model-only rows).

Fix: label with `CFBTeamClasses` (the v5 fix), or pass the backfill path the frozen fit used.

### 7. INFO / design risk: the model-only fallback is fail-open in effect
With no anchor, or an anchor older than 12 h, v6.2 prices pure v6 means against the Polymarket ask with no minimum
edge. Elsewhere v6 was found to have no market-relative skill; on the 2026-11-21 slate v6 differs from DK by up to
9 points (Tennessee Tech @ Mississippi State v6 +22.4 vs DK +31.5; Rutgers @ Penn State +20.3 vs +26.5), which
would become double-digit "edges" on every game if a capture failed. This has never fired (416/416 anchored).
It is a documented choice, not a code bug; the operator may want the summary to flag a slate where most rows are
model-only. (Not a recommendation to remove anything from production.)

### 8. INFO: provenance hash excludes files that move the price
`v62_serving_code_hash` (line 55-60) covers v62, v6, features_v6 and team_identity, but not
`data_sources/espn_cfb_pregame_capture.py` (anchor parsing and sign) or `data_sources/cfb_data.py`
(`resolve_team`). A change there changes v6.2 prices without changing `code_revision`.

## Checked and clean (with evidence)

- **Anchor sign / orientation.** `home_spread_current` (home-team point spread) agrees with the favourite-flag
  `home_spread_line` on 3001/3001 DraftKings items; `total_current == over_under` on all; scoreboard vs core line
  differ by 0 on 2992/3000. `margin_home = -spread` matches the fit (`a_margin = -home_spread_line`).
- **Anchor point in time.** `load_anchor_index` max-scans, drops `seen > decision` and `seen >= event start`;
  reconstruction at each row's `created_at` reproduces the rationale's fair margin/total on 416/416 rows; anchor
  age 0.33 h median, 0.34 h max.
- **Spread line handed to v6.2 and written back.** `home_line` comes from the champion matcher's title-first rule;
  `thr = -home_line`; contract line is `home_line` for home, `-home_line` for away, the same convention as the
  champion's `pm_spread_home_line` / `pm_spread_away_line`. (The 3 Arizona/Kansas State rows came from the
  already-fixed matcher label bug.)
- **Probability math.** Margin 0 has no mass so ML home + away = 1; push mass excluded from both cover sides on
  whole lines (conservative versus a refunded push, same as v5); serving `margin_sd`, Newton centring (balanced on
  anchored rows, mean on model-only rows), `total_sd` buckets and key multipliers are the fit script's formulas
  with the same inputs (blended mean and blended total).
- **v6 train/serve parity.** Serving v6 projections (canonical_history + extractor, cutoff = kickoff) reproduce the
  training base rows' grid `0.1|0.2` margin and total exactly (232 sampled 2024-25 FBS games: max |diff| 0.00,
  0 no-calls). History files contain only completed games (16,405 + 8,033 rows, no missing or 0-0 scores);
  admission is `kickoff + 5 h <= min(decision, kickoff)`.
- **Team identity.** ESPN-id lookup maps "Hawai'i", "San Jose State", "App State", "Florida International" to
  their history keys; the 4 "TBD" conference-championship placeholders raise `NO_CALL_CFB_V6_UNRATED_TEAM` and
  are skipped (no average-team default).
- **Started games.** v6.2 does not check kickoff, but `_forecast_cfb_sport` skips `event_start_utc <= now`
  before any write.
- **Flat/research only.** `is_qualified_call` requires a v2-v5 version, so v6.2 never reaches Main or gated.
