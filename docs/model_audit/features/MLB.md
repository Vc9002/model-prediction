# MLB feature audit

Branch: `audit/model-feature-reconciliation-v1` @ `origin/main` 826c893.
Every claim below was checked against current source in this worktree
(`src/model_prediction/`, `config/tested_features.json`, `docs/FEATURE_REGISTRY.md`)
on 2026-08-11, not transcribed from the task brief or prior evidence without
verification. Where prior evidence (`docs/model_audit/prior_evidence/`) or the
`origin/rebuild/clean-slate-v1` branch is cited, that is marked explicitly and
is **not** current-main truth.

Verdict enum used below is exactly: `KEEP`, `KEEP_CORE`, `KEEP_BASELINE`,
`KEEP_RESEARCH_ONLY`, `RETEST_REQUIRED`, `REBUILD_IMPLEMENTATION`,
`BLOCKED_PIT`, `REMOVE`, `REJECT`, `SUPERSEDED`.

---

## Contradiction found and confirmed: `bullpen_weakness_gap` is live and material, but the registry still calls bullpen "untested"

`config/tested_features.json`'s `bullpen` entry (`verdict: "untested"`,
`status: "half_wired"`, last touched `tested_on: null`, `evidence_grade: "A"`)
says: *"One import, never reaches a feature vector... The data source
(espn.py:233) passes None because StatsAPI game snapshots are not yet
cached."* `docs/FEATURE_REGISTRY.md` repeats this under "Untested or
blocked."

This is stale and contradicted by current code and by both live model
artifacts:

- `src/model_prediction/learned_forward.py:123-136` computes
  `bullpen_weakness_gap` live from `features/bullpen.py`'s
  `team_recent_relief_lines`/`bullpen_profile` — real MLB Stats API boxscore
  snapshots (`data/mlb_statsapi/game_snapshots.jsonl`), not a `None`-passing
  stub.
- `config/models/mlb-elo-trend-lr-v7.json` and `mlb-elo-trend-lr-v8.json`
  both list `bullpen_weakness_gap` in `feature_names` with non-trivial,
  correctly-signed fitted coefficients (v7: `0.0729`, v8: `0.1520`).
- v7's own `training.note` says explicitly: *"bullpen_weakness_gap's live
  provider was added in this same change (features/bullpen.py's
  team_recent_relief_lines/bullpen_profile, same real functions Measured
  Edge already uses live)."* — dated 2026-07-30.
- `mlb-analyst-poisson-trend-v0.3.yaml` also fits a real, non-zero
  `bullpen_elasticity` (0.069018) from 1136 real games via
  `scripts/mlb_elasticity_refit.py`.

The registry entry predates the 2026-07-30 v7 promotion and was never
updated afterward. **Confirmed real, live-wired, production feature with
material fitted weight in three current artifacts** — see the `bullpen_weakness_gap`
entry below for its full record. The registry's own "untested"/"half_wired"
label should be corrected (out of scope for this audit to edit
`config/tested_features.json` directly — flagged here for the consolidating
process).

---

## Feature entries

### `elo_probability`

- **Model(s) using it**: `mlb-elo-trend-lr-v7`, `mlb-elo-trend-lr-v8` (moneyline). Also NBA/WNBA/NFL/SOCCER elo_trend models (shared implementation).
- **Source code location**: `src/model_prediction/features/elo_ratings.py` (`EloBook.expected_home_win`, `expected_win_probability`); wired in `learned_forward.py:65`.
- **Source provider**: Internal — Elo ratings computed chronologically from cached completed games (`GameRecord` history), zero external API.
- **Exact formula**: Standard logistic Elo: `1 / (1 + 10 ** (-(home_rating + home_advantage - away_rating) / 400))`. MLB config: `k=4.0`, `home_advantage=24.0`, `offseason_regression=0.0`.
- **Expected sign**: Positive (higher home Elo edge → higher home win probability). Confirmed: v7 coefficient `+3.144`, v8 `+3.040`.
- **PIT-safe?**: Yes — ratings update only from games strictly before the decision date; no look-ahead in the update loop.
- **Train/serve parity?**: Yes — same `EloBook`/`expected_win_probability` code path used in `validation.py` training rows and `learned_forward.py` live serving.
- **Coverage**: 100% (Elo rating always exists, defaulting to 1500 for a new/unseen team).
- **Missingness behavior**: N/A — never missing, defaults to `DEFAULT_ELO = 1500.0` for unseen teams.
- **Correlation notes**: Registry calls this "the only consistently material fitted signal in the five standard production models."
- **Coefficient/importance**: MLB fitted coefficient 3.319 (2026-07-22 snapshot, `tested_features.json`); current v7/v8 artifacts show 3.144/3.040 respectively — consistent order of magnitude.
- **Ablation deltas**: `production_ablation_summary` (mlb-elo-trend-lr-v5): holdout Brier delta -0.00029, validation Brier delta +0.00261 → strict decision `INCONCLUSIVE` for MLB specifically (strict KEEP only confirmed for NBA/SOCCER).
- **Calibration impact**: Not separately isolated for MLB; NBA/WNBA calibration slopes (1.79/1.27) suggest project-wide underconfidence patterns tied partly to Elo dominance, but this is not MLB-specific evidence.
- **Known bugs**: None found. Registry flags an **unresolved, unrelated** open question about NBA Elo (73.66% hit rate at 88.2% called rate, above the NBA favorite base rate — possible leakage) — not an MLB-specific concern but a project-wide caution the registry says blocks building further on top of Elo generally.
- `strict_statistical_verdict`: **INCONCLUSIVE** (MLB-specific; strict KEEP not established for MLB)
- `operator_retention_verdict`: **KEEP** (only materially-sized signal in the model; retained everywhere)
- **Verdict**: `KEEP_CORE`

---

### `trend_gap`

- **Model(s) using it**: `mlb-elo-trend-lr-v7`, `mlb-elo-trend-lr-v8` (moneyline). Also the other 4 sports' elo_trend models.
- **Source code location**: `src/model_prediction/features/trends.py` (`TrendEngine`, `opponent_adjusted_ewm_trend`); wired in `learned_forward.py:66` as `home_trend.offensive_momentum - away_trend.offensive_momentum`.
- **Source provider**: Internal — opponent-adjusted rolling EWM trend over cached completed games, three half-lives (3/10/25 games), shrunk toward league baseline (`PRIOR_STRENGTH_GAMES=12.0`).
- **Exact formula**: `offensive_momentum = hl3_level - hl25_level` (short vs. long half-life EWM offense level, opponent-adjusted, shrinkage-weighted). `trend_gap = home_offensive_momentum - away_offensive_momentum`.
- **Expected sign**: Positive (home team trending up relative to away → higher home win probability).
- **PIT-safe?**: Yes — same chronological-cache design as `elo_probability`.
- **Train/serve parity?**: Yes — same `TrendEngine` used in both paths.
- **Coverage**: 100%.
- **Missingness behavior**: N/A, always computable once at least one prior game exists; shrinkage handles small samples.
- **Correlation notes**: `tested_features.json` fitted coefficient for MLB: **-0.03** — near zero. v7 artifact: `-0.0211`; v8 artifact: `-0.0249`. Confirms the near-zero-coefficient claim in the task brief.
- **Coefficient/importance**: Near-zero across MLB, NBA (-0.004), WNBA (-0.007); somewhat larger for NFL (0.05) and SOCCER (-0.151).
- **Ablation deltas**: MLB production ablation (v5): holdout Brier delta -0.0000743, validation Brier delta -0.000583 → strict `INCONCLUSIVE`, zero-threshold retention policy calls it a directional removal candidate for MLB/SOCCER specifically, but the registry's overall verdict is `keep` because at least one out-of-sample cohort improves for NBA/NFL/WNBA (same feature/formula, shared module).
- **Calibration impact**: Not separately isolated.
- **Known bugs**: None. `retest_when` in the registry: *"Never as-is. If retested it must be as a RESIDUAL trend orthogonalized against elo_probability, not the raw EWMA difference."* — matches the task brief's exact suggested next step.
- `strict_statistical_verdict`: **INCONCLUSIVE** (near-zero coefficient, MLB/SOCCER directionally negative on removal-improves basis)
- `operator_retention_verdict`: **KEEP** (project-wide zero-threshold retention policy; feature stays wired for all 5 sports on one shared formula)
- **Verdict**: `RETEST_REQUIRED` — specifically as an elo_probability-orthogonalized residual, per the registry's own `retest_when` condition. Not currently rejected/removed; remains wired.

---

### `park_factor`

- **Model(s) using it**: `mlb-elo-trend-lr-v7`, `mlb-elo-trend-lr-v8` (moneyline); also `mlb-analyst-poisson-trend-v0.3`'s Trend Engine score model (`park_elasticity=0.259048`, fit against 1136 real games), feeding Measured Edge margin/totals.
- **Source code location**: `src/model_prediction/features/park_factors.py` (`park_factor()`, table `PARK_RUN_FACTORS`); regenerated by `mlb_baseline_refresh.refresh_park_factors`.
- **Source provider**: Internal — computed from 7,704 real completed games (2024-02-22 to 2026-07-25), credibility-shrunk toward 1.0 by games played. Table version tag: `2026-07-29-empirical`.
- **Exact formula**: Per-home-team static run factor (e.g. Colorado Rockies 1.193, Texas Rangers 0.912), looked up by `home_team`; `status: "unavailable_from_source"` returns neutral 1.0 for an unknown park.
- **Expected sign**: Negative in the moneyline logistic form (`elo_trend_lr` coefficients: v7 `-1.161`, v8 `-0.902` — i.e. a hitter-friendly park factor pushes the model's raw score down; this is a scale/sign artifact of how the feature enters alongside the other coefficients, confirmed materially fitted either way). In the Trend Engine, higher `park_factor` correctly raises expected runs for both teams (`park ** park_elasticity` multiplies both `away_expected` and `home_expected`).
- **PIT-safe?**: **No — blocked.** This is a single static table built from a 2024–2026 aggregate window and applied retroactively to every training row regardless of season. A 2024 game is scored using park behavior partly observed in 2025-2026, which the game's own decision time could not have known. `tested_features.json`'s strict production ablation explicitly marks this `REMOVE CANDIDATE` for exactly this reason: *"Material fitted coefficient, but the strict ablation marks it REMOVE CANDIDATE because a static 2025 three-year table is applied retroactively across seasons. Coefficient magnitude does not cure invalid point-in-time provenance."*
- **Train/serve parity?**: Yes at the mechanical level — `validation.py` (training) and `learned_forward.py` (serving) both call the identical `park_factor()` function against the identical table. The PIT problem above is orthogonal to parity: it's the same (technically PIT-invalid) number in both places, not a train/serve mismatch.
- **Coverage**: 30/30 current MLB home parks in the table; unknown park → neutral 1.0, `unavailable_from_source`.
- **Missingness behavior**: Fails soft to neutral 1.0 with an explicit status flag, not a crash.
- **Correlation notes**: MLB fitted coefficient -1.05 (`tested_features.json`, 2026-07-22 snapshot); current artifacts -1.161 (v7) / -0.902 (v8) — same order of magnitude, materially non-zero.
- **Coefficient/importance**: Second-largest-magnitude coefficient in both v7 and v8 after `elo_probability`.
- **Ablation deltas**: v5 production ablation: holdout Brier delta +0.000034 (worse), validation Brier delta -0.000074 (better) → tiny, mixed-direction, but zero-threshold retention policy keeps it (`KEEP`) despite the strict `REMOVE CANDIDATE` call.
- **Calibration impact**: Not separately isolated.
- **Known bugs**: None beyond the provenance issue above.
- `strict_statistical_verdict`: **REMOVE CANDIDATE** (per `tested_features.json`'s own strict ablation label — PIT provenance blocked, not a statistical non-significance call)
- `operator_retention_verdict`: **KEEP** (research-only; tiny positive holdout contribution retained per the project's zero-threshold policy, explicitly not claimed production-safe)
- **Verdict**: `BLOCKED_PIT` — retained in both active moneyline models today, but its own governing registry entry says point-in-time provenance is invalid until a season-correct, timestamped park-factor table exists and the chronological ablation is rerun. `retest_when`: *"Only after season-correct park factors have timestamped point-in-time provenance and the locked chronological ablation is rerun."*

---

### `weather_factor`

- **Model(s) using it**: `mlb-elo-trend-lr-v7`, `mlb-elo-trend-lr-v8` (moneyline); also the Trend Engine (`weather_elasticity=0.040295`).
- **Source code location**: `src/model_prediction/features/weather.py` (live serving, Open-Meteo); `validation.py::_lookup_weather` (training, `data/features/historical_weather.json`).
- **Source provider**: Open-Meteo free forecast API (live serving path, no key required, `hourly=temperature_2m,wind_speed_10m,relative_humidity_2m`) for live inference; a static historical weather cache (`data/features/historical_weather.json`, keyed by home team + game date) for training/backtest rows.
- **Exact formula**: `run_factor` multiplier (Trend Engine: `weather ** weather_elasticity`); dome teams hard-coded to neutral 1.0 with `available: True`.
- **Expected sign**: Negative logistic coefficient (v7 `-0.212`, v8 `-0.298`) — consistent direction and magnitude with `tested_features.json`'s `-0.318` snapshot.
- **PIT-safe?**: **No — blocked, for a different reason than park_factor.** `validation.py::_lookup_weather` reads `data/features/historical_weather.json`, which the registry states has *"no forecast issue time or observed_at timestamp. It cannot support a point-in-time production claim."* Confirmed in code: `_lookup_weather` looks up by `(home_team, game_date)` only, with no timestamp field carried at all — so there is no way to verify the cached value reflects only information knowable before the game's decision time.
- **Train/serve parity?**: **Weak.** Training reads a day-level historical cache (likely closer to actual observed conditions than a forecast); live serving fetches a real Open-Meteo *forecast* at request time (`features/weather.py`'s `_fetch_forecast_hourly`), which necessarily differs from what a fitted coefficient learned against. This is a genuine train/serve definitional mismatch layered on top of the PIT-provenance gap, not merely an untimestamped-cache issue — worth flagging as a second, distinct concern beyond what the registry's note states.
- **Coverage**: All non-dome parks; dome parks (Tampa Bay, Miami, Houston, and others per `DOME_TEAMS`) hard-coded neutral.
- **Missingness behavior**: Fails soft — `_fetch_forecast_hourly` returns `None` on any HTTP/parse failure, callers fall back to the neutral default (1.0).
- **Correlation notes**: Small but consistently negative across snapshots.
- **Coefficient/importance**: Smallest-but-one magnitude of the six v7/v8 coefficients.
- **Ablation deltas**: v5 production ablation: holdout Brier delta +0.0000063 (worse), validation Brier delta +0.000034 (worse) — both directions worse, yet strict decision recorded is `REMOVE CANDIDATE` while zero-threshold retention still calls it `KEEP` (tiny positive validation contribution elsewhere in the historical run, per registry text).
- **Calibration impact**: Not separately isolated.
- **Known bugs**: The training-vs-serving definitional gap described above (actuals-like cache vs. live forecast) does not currently have a tracked bug ticket in `MASTER.md`/`DEBUG.md` under this name — flagging it here as a real, previously undocumented train/serve concern distinct from the registry's PIT-timestamp note.
- `strict_statistical_verdict`: **REMOVE CANDIDATE**
- `operator_retention_verdict`: **KEEP** (research-only, not production-safe per registry's own `safety_override` clause)
- **Verdict**: `BLOCKED_PIT`

---

### `starter_era_gap` (v8's active implementation, PIT-safe live provider)

- **Model(s) using it**: `mlb-elo-trend-lr-v8` (moneyline) only — v7 uses `pitcher_era_gap` (team-level), not this.
- **Source code location — live serving**: `src/model_prediction/features/starter_history.py` (`starter_era_gap_live`, `starter_rolling_era`, `load_starter_index`); wired in `learned_forward.py:98-107`.
- **Source code location — v8's training/backtest fit**: `src/model_prediction/validation.py:2073-2152` (`_load_starter_era_map`, `_starter_era_gap`).
- **Source provider**: MLB Stats API boxscore snapshots (`data/mlb_statsapi/game_snapshots.jsonl`), kept current by `cli.py`'s daily `_capture_mlb_starter_snapshots` step (added 2026-08-04).
- **Exact formula**: Rolling ERA over a starter's last ≤5 real starts, requiring ≥2 prior starts, `home_era - away_era`. `9 * sum(earned_runs) / sum(innings)` over the lookback window, computed strictly on starts before the decision timestamp.
- **Expected sign**: Negative (worse — i.e. higher — home starter ERA → lower home win probability). Confirmed: v8 coefficient `-0.0190`; v4's historical coefficient `-0.0179`, same sign, same rough magnitude.
- **PIT-safe?**: **This specific implementation: yes**, verified in code — `starter_rolling_era` filters `[s for s in index... if s[0] < decision]` before computing the window, and `learned_forward.py` raises and fails the game closed (`NO_CALL_STARTER_ERA_GAP_INSUFFICIENT_HISTORY` / `NO_CALL_STARTER_ERA_GAP_NO_CONFIRMED_STARTER`) rather than guessing, when history is thin or the starter is unconfirmed.
- **Train/serve parity?**: **Confirmed matching methodology** — v8's `training.promotion_rationale` states the walk-forward ablation used `build_walk_forward_rows + chronological_split`, self-consistency-verified by first reproducing v7's exact stored holdout numbers, then replacing `pitcher_era_gap` with `starter_era_gap`. `validation.py::_load_starter_era_map`'s docstring states the same rolling-5-start, ≥2-prior-starts, strictly-chronological logic as `features/starter_history.py::starter_rolling_era`. This is architecturally the *same design*, independently reimplemented for training (event-id-keyed replay of history) vs. serving (name-keyed live lookup) — not the older, structurally broken `starter_era_gap_legacy_event_map` variant described below.
- **CRITICAL — do not confuse with the retired implementation**: an *older, different, permanently rejected* implementation of a feature with the exact same name (`_starter_era_gap`/`_load_starter_era_map` reading a dict keyed by `event_id`, replaying only historical completed box scores) shipped broken in `mlb-elo-trend-lr-v4` and was removed in v5 because an unplayed future game could never be a key in that map, so it silently served a constant `0.0` at prediction time while training on real ERA — textbook train/serve skew, confirmed by `tested_features.json`'s `production_incident` note (100% of v4's settled live picks were affected; practical damage judged small only because the coefficient was near-zero at the time). **Naming this entry `starter_era_gap_legacy_event_map`, per the task brief, is an accurate and useful disambiguation** — it should never be restored under any name; `retest_when: "Never."` v8's current implementation is a materially different, PIT-safe live provider, not a resurrection of the v4-era one.
- **Coverage**: Confirmed live 2026-08-04 in `MASTER.md` F-54: 13/15 real MLB games priced with non-zero `starter_era_gap` values; 2 correctly skipped for unresolved probable starters.
- **Missingness behavior**: Fails closed at serve time (raises, then `learned_forward.py` catches and defaults to 0.0 with an `unavailable` note logged) — never silently substitutes a stale or wrong pitcher.
- **Correlation notes**: v8's own `training_data_note`: *"starter_era_gap depends on data/mlb_statsapi/game_snapshots.jsonl, kept current as of this artifact's build by cli.py's daily _capture_mlb_starter_snapshots step... verify that capture is still running before trusting this feature live."* — an operational dependency, not a statistical caveat.
- **Coefficient/importance**: -0.0190 (v8).
- **Ablation deltas**: v8's own qualification block: replacing `pitcher_era_gap` with `starter_era_gap` produced locked-holdout hit rate 0.6081 (148 calls, 90 hits) vs. v7's 0.5847 (118 calls) at v7's own threshold — a real, positive holdout improvement — but validation Brier **regressed** vs. v7's incumbent feature set (0.24702 vs. 0.24655). This is the exact reason `qualified: false` on the current artifact (see model card for full detail).
- **Calibration impact**: Not separately isolated from the whole-model calibration in the artifact.
- **Known bugs**: `MASTER.md` F-48/F-55 — this feature (and `bullpen_weakness_gap`/`defensive_trend_gap` before it) was silently missing from the audit ledger for a period after promotion (correct model scoring, incomplete audit trail) — fixed same-day both times. Not a prediction-correctness bug.
- **Registry disambiguation, verified in code (2026-08-11)**: the task brief's claim that a servable `starter_era_gap_live`/PIT-history-based implementation exists in current code **is confirmed true** — `features/starter_history.py::starter_era_gap_live` is real, live-wired, and is the exact feature v8 uses. `tested_features.json`'s `addendum_2026_08_10` already flags this same finding but stops short of updating the feature's own verdict, deferring to "an operator explicitly re-evaluates this new implementation."
- `strict_statistical_verdict`: **INCONCLUSIVE** — the holdout improvement is real but the validation-set regression (the project's own promotion-rule tripwire) means this has not cleared a clean statistical bar; it was promoted only by explicit operator override.
- `operator_retention_verdict`: **KEEP** (as the live-serving choice for v8; already shipped and in production)
- **Verdict**: This audit recommends recording this specific implementation as `starter_era_gap_pit_history` (per the task brief's suggested disambiguated name) with verdict **RETEST_REQUIRED** — the live provider is now genuinely PIT-safe and already carries one real (if statistically ambiguous) walk-forward result, but the registry's own `addendum_2026_08_10` is correct that no operator has yet formally closed out a fresh, standalone re-evaluation of *this* implementation under its own name; the registry's headline `starter_era_gap` entry still shows `verdict: "remove"` / `retest_when: "Never"`, which is only true of the retired `starter_era_gap_legacy_event_map` implementation and should not be read as covering this one.

---

### `starter_era_gap_legacy_event_map` (retired, do not restore)

- **Model(s) using it**: None currently. Shipped in `mlb-elo-trend-lr-v4` only (retired, superseded by v5's `pitcher_era_gap`).
- **Source code location**: `src/model_prediction/validation.py` historically (the same function names — `_load_starter_era_map`/`_starter_era_gap` — were reused/rebuilt for v8's PIT-safe training-side replay; see the `starter_era_gap` entry above for how the *current* code at those same names differs in effect from what shipped in v4, per its own architecture — the v4-era failure mode was that no live provider existed in `learned_forward.py` at all, not a defect in `validation.py`'s training-side function itself).
- **Source provider**: MLB Stats API boxscores, replayed into an `event_id`-keyed dict — structurally cannot serve a future/unplayed event_id.
- **Exact formula**: Same rolling-ERA definition as the current implementation, but exposed only via a training-time replay dict with no live-serving path in v4.
- **Expected sign**: Negative; v4 fitted coefficient -0.0179.
- **PIT-safe?**: **No — this is the confirmed, historical failure mode.** At serve time, `_starter_era_gap(event_id)` on an unplayed game returns the dict's `.get(event_id, 0.0)` default, i.e. a **hardcoded constant zero**, while training used real ERA values. Textbook train/serve skew.
- **Train/serve parity?**: **No — this is exactly the bug.**
- **Coverage**: N/A — served as a constant at inference regardless of real coverage.
- **Missingness behavior**: Silently defaulted to 0.0 rather than failing closed — the defining defect.
- **Correlation notes**: N/A.
- **Coefficient/importance**: -0.0179 (v4) — near-zero, which `tested_features.json` notes is why "practical damage is small," not because the mechanism wasn't broken.
- **Ablation deltas**: N/A — v5 removed it and substituted the servable `pitcher_era_gap`; described in the registry as a bug fix, not feature bloat.
- **Calibration impact**: N/A.
- **Known bugs**: The defining bug — `production_incident` in `tested_features.json`: v4 made 100% of its settled live MLB picks with this feature silently zero at prediction time.
- `strict_statistical_verdict`: **REJECT** (structurally unservable, not a marginal-evidence call)
- `operator_retention_verdict`: **REJECT** (no operator override recorded or warranted)
- **Verdict**: `REMOVE` — permanent. `retest_when: "Never. Remove elo_trend_park_starter from FEATURE_VARIANTS so it cannot be selected again."` Never restore under any name; the current live `starter_era_gap` (see above) is architecturally distinct and should not be treated as a resurrection of this one.

---

### `starter_fip_gap`

- **Model(s) using it**: None currently promoted. Dormant/shadow — computed and available via `learned_forward.py:109-122` but not in `mlb-elo-trend-lr-v7` or `v8`'s `feature_names`.
- **Source code location — live**: `src/model_prediction/features/starter_history.py::starter_fip_gap_live`/`starter_rolling_fip` (added 2026-08-05, same day as the ERA pipeline, "widened for FIP" per the module's own comment on `_StarterRow`).
- **Source code location — training/backtest**: `src/model_prediction/validation.py:2155-2246` (`_load_starter_fip_map`, `_starter_fip_gap`) — mirrors `_load_starter_era_map`'s exact chronological point-in-time design, keyed by `event_id`, same rolling-5-start/≥2-prior-starts window.
- **Source provider**: Same MLB Stats API boxscore snapshots as `starter_era_gap`.
- **Exact formula**: FIP = `((13*HR) + 3*(BB+HBP) - 2*K) / IP + FIP_CONSTANT` (`FIP_CONSTANT = 3.10`), rolling over the last ≤5 real starts; `home_fip - away_fip`.
- **Expected sign**: Negative (worse/higher home starter FIP → lower home win probability), per the code comment claim below.
- **PIT-safe?**: Yes for the live provider (`starter_fip_gap_live`), same strictly-before-decision filtering as `starter_era_gap_live`, same fail-closed `ValueError` contract.
- **Train/serve parity?**: Architecturally the same split as `starter_era_gap`: a training-side historical-map replay (`validation.py`) and a separately-implemented live provider (`starter_history.py`) that mirror each other's methodology by design (confirmed by direct code comparison — both use rolling-5-start, ≥2-prior-starts windows and identical FIP arithmetic).
- **Coverage**: Not separately measured/reported in any committed artifact found in this audit.
- **Missingness behavior**: Fails closed, same pattern as ERA (`NO_CALL_STARTER_FIP_GAP_INSUFFICIENT_HISTORY`/`NO_CALL_STARTER_FIP_GAP_NO_CONFIRMED_STARTER`).
- **Correlation notes / ablation deltas**: `learned_forward.py:110-112`'s own code comment: *"F-68 (2026-08-05)... Locked-holdout shows +1pp hit rate, -39% ECE, +11 units vs ERA. Wire alongside ERA for v9+ artifacts."* `MASTER.md`'s F-68 entry gives the specific numbers: 1396 games, 60/20/20 split, hit rate 59.2% (FIP) vs. 58.2% (ERA), ECE 0.0228 vs. 0.0372, units at -110 +52.3 vs. +41.3. Also claims FIP's learned coefficient is roughly 2x ERA's (-0.031 vs -0.017), and ERA shrinks to near-zero (-0.007) when both are present simultaneously.
- **Verified independently in this audit**: **No committed evaluation artifact for this comparison was found.** Searched `outputs/`, `docs/model_audit/prior_evidence/`, and the whole repo for any `*fip*` output file or JSON — none exists. `docs/FEATURE_REGISTRY.md`'s own entry for this feature already flags this precisely: *"Code comment claims '+1pp hit rate, -39% ECE, +11 units vs ERA' from a locked holdout; not independently re-verified or backed by a committed evaluation artifact."* This audit reproduces and confirms that caveat rather than resolving it — the claim remains unverified pending a rerun.
- **Calibration impact**: Claimed (-39% ECE) but unverified per above.
- **Known bugs**: None found in the implementation itself; the open issue is evidentiary (unverified claim), not a code defect.
- `strict_statistical_verdict`: **INCONCLUSIVE** (real numbers are cited in a code comment and `MASTER.md`, but no reproducible, committed artifact backs them — cannot be independently confirmed as this audit's own strict result)
- `operator_retention_verdict`: **KEEP** (dormant scaffolding, real PIT-safe implementation, cheap to keep; not yet promoted to any model)
- **Verdict**: `RETEST_REQUIRED` — matches the task brief's framing exactly. Confirmed real, PIT-safe, architecturally sound, and NOT the same feature as `starting_pitcher_fip` below (different mechanism — rolling live per-start FIP vs. a season aggregate). Needs a fresh, independently-run, committed ablation artifact before its performance claim can be trusted for a v9+ promotion decision.

---

### `starting_pitcher_fip` (KEEP_REJECTED — season-aggregate FIP, distinct from `starter_fip_gap`)

- **Model(s) using it**: None. Orphaned module, never wired into `learned_forward.py`.
- **Source code location**: `src/model_prediction/features/starting_pitcher.py`.
- **Source provider**: Evaluated via `scripts/evaluate_orphaned_features.py` on branch `worktree-orphaned-feature-eval` (evidence grade B — not independently re-verified in this audit; branch not checked out).
- **Exact formula**: Not re-derived in this audit (module not read in depth — out of scope since it is confirmed orphaned/rejected and not a live candidate); registry describes it as collinear with `pitcher_era_gap`.
- **Expected sign**: Not separately recorded.
- **PIT-safe?**: Not separately assessed in this audit; irrelevant to current promotion status since it's rejected on evidence, not on a PIT concern.
- **Train/serve parity?**: N/A — never wired to serve.
- **Coverage**: 84% (per `tested_features.json`'s `measured_effect`).
- **Missingness behavior**: Not assessed.
- **Correlation notes**: Collinear with the already-active `pitcher_era_gap`.
- **Coefficient/importance**: Not recorded — "zero measurable effect on locked holdout" per the registry.
- **Ablation deltas**: "84% data coverage, zero measurable effect on locked holdout" (`tested_features.json`, evidence grade B, tested 2026-07-21).
- **Calibration impact**: Not recorded.
- **Known bugs**: None found; this is a clean rejection on evidence, not neglect.
- `strict_statistical_verdict`: **REJECT** (measured zero effect)
- `operator_retention_verdict`: **REJECT** (no override recorded — genuinely different from `trend_gap`'s near-zero-but-kept pattern because this one is also collinear with an existing feature, not just weakly informative on its own)
- **Verdict**: `REJECT`. `retest_when`: "Only if `pitcher_era_gap` is removed from the model, which would break the collinearity." **Confirmed distinct from `starter_fip_gap`** above — different mechanism (season-aggregate vs. rolling per-start), not a naming collision, per `docs/FEATURE_REGISTRY.md`'s explicit note: *"Not `starting_pitcher_fip` (different mechanism, not the collinear one above)."*

---

### `pitcher_era_gap`

- **Model(s) using it**: `mlb-elo-trend-lr-v7` only (v8 replaced it with `starter_era_gap`).
- **Source code location**: `src/model_prediction/features/team_runs.py` (`pitcher_era_gap_from_history`); wired in `learned_forward.py:92-97`.
- **Source provider**: Internal — rolling **team-level** runs-allowed gap from prior cached games (not a per-starter statistic; the name is somewhat misleading — it is a team-defense proxy, not a pitcher-specific one).
- **Exact formula**: Not re-derived line-by-line in this audit; confirmed via `learned_forward.py`'s own comment: *"Same definition as training (features/team_runs): rolling team runs-allowed gap from prior cached games. Never an ESPN starter ERA — that was a different quantity and caused train/serve skew."*
- **Expected sign**: v7 fitted coefficient `+0.0185` (near zero, and note the *opposite* sign from `starter_era_gap`'s `-0.0179`/`-0.0190` — worth flagging as a real, unexplained sign inversion between the team-level and starter-level versions of a conceptually similar "pitching quality gap" feature; not resolved further in this audit).
- **PIT-safe?**: Yes — rolling from prior cached games only.
- **Train/serve parity?**: Yes, per the code comment above — explicitly designed to avoid the exact train/serve skew that broke the legacy `starter_era_gap`.
- **Coverage**: Not separately reported; presumed near-100% given it's a team aggregate, not a per-player stat.
- **Missingness behavior**: Not separately assessed in this audit.
- **Correlation notes**: `tested_features.json`: "In MLB v5 with a near-zero coefficient. The strict leave-one-out result is INCONCLUSIVE; starter_era_gap is not a valid challenger because it is unservable" — that last clause is now stale (see the `starter_era_gap` entry above; a servable version does exist).
- **Coefficient/importance**: 0.022 (2026-07-22 snapshot); 0.0185 (current v7).
- **Ablation deltas**: v5 production ablation: holdout Brier delta -0.000246 (improves), validation Brier delta -0.000338 (improves) — both directions favor removal, yet `docs/FEATURE_REGISTRY.md` records this as **kept by explicit operator override** ("removal costs 3.4 units at frozen threshold. Profit over accuracy.") — a case where the strict statistical call and the operator's economic call diverge in the opposite direction from `trend_gap`'s pattern (here the strict call favors removal and the operator overrides toward keeping).
- **Calibration impact**: Not separately isolated.
- **Known bugs**: None found.
- `strict_statistical_verdict`: **INCONCLUSIVE** / directional REMOVE CANDIDATE (registry's `production_ablation_summary` marks strict_decision `INCONCLUSIVE` but `retention_decision: "REMOVE CANDIDATE"`)
- `operator_retention_verdict`: **KEEP** (explicit operator override, unit-economics-based, per `docs/FEATURE_REGISTRY.md`)
- **Verdict**: `KEEP` (v7 only; superseded by `starter_era_gap` in v8) — `SUPERSEDED` would also be a defensible verdict for v8's context specifically, since v8 no longer uses this feature at all.

---

### `bullpen_weakness_gap`

- **Model(s) using it**: `mlb-elo-trend-lr-v7`, `mlb-elo-trend-lr-v8` (moneyline); `mlb-analyst-poisson-trend-v0.3` (Trend Engine, `bullpen_elasticity=0.069018`).
- **Source code location**: `src/model_prediction/features/bullpen.py` (`bullpen_profile`, `team_recent_relief_lines`, `load_relief_appearance_index`); wired in `learned_forward.py:123-136`.
- **Source provider**: MLB Stats API boxscore snapshots (same `data/mlb_statsapi/game_snapshots.jsonl` source as the starter features), restricted to each team-game's relievers (all pitchers excluding `pitcher_order[0]`).
- **Exact formula**: `bullpen_weakness_index = era / LEAGUE_RELIEF_ERA` where `era` is credibility-weighted-shrunk relief ERA (`credibility = innings / (innings + 30.0)`, shrunk toward `LEAGUE_RELIEF_ERA = 4.0593`) over the team's last 10 completed games' relief appearances. `bullpen_weakness_gap = home_weakness - away_weakness`.
- **Expected sign**: Positive (weaker/higher-index home bullpen → higher moneyline coefficient value in v7/v8; v7 `+0.0729`, v8 `+0.1520` — direction consistent with "higher weakness index penalizing that side," though the raw sign interpretation depends on how `bullpen_weakness_gap` composes with `home - away`; not independently re-derived past confirming both artifacts fit the same positive sign).
- **PIT-safe?**: Yes — `team_recent_relief_lines` filters strictly `game[0] < decision`, matching the starter-history design pattern exactly (confirmed by direct code comparison — the module's own docstring states it "Mirrors `features/bullpen.py`'s design exactly").
- **Train/serve parity?**: Yes — `learned_forward.py`'s own comment is explicit: *"NOT validation.py's `_load_bullpen_map`, which is a historical event_id crosswalk with no path to a future game"* — i.e. the codebase is aware of, and has avoided, the exact `starter_era_gap_legacy_event_map`-style trap for this feature. (Note: `validation.py::_load_bullpen_map` does exist, mirroring the training-side pattern used for ERA/FIP — same chronological point-in-time design, not the broken pattern; confirmed by reading its docstring at `validation.py:2249-2262`, which explicitly states "mirroring `_load_starter_era_map`.")
- **Coverage**: Credibility-shrunk toward league average by sample size — no games with zero relief innings would return `unavailable_from_source`.
- **Missingness behavior**: `bullpen_profile` returns `bullpen_weakness_index: 1.0` (neutral) with `status: "unavailable_from_source"` when `relief_lines` is empty.
- **Correlation notes**: This is the feature this audit's contradiction section above is about — the registry's `bullpen` entry is stale (see top of this document).
- **Coefficient/importance**: v7 `0.0729`, v8 `0.1520` — v8's fitted weight is roughly double v7's, the largest coefficient shift of any shared feature between the two artifacts.
- **Ablation deltas**: No standalone ablation artifact for this specific feature was found in `docs/model_audit/prior_evidence/` or `outputs/`; v7's `training.note` describes it being added alongside the v6→v7 rebuild as part of a broader feature-set change, not isolated individually.
- **Calibration impact**: Not separately isolated.
- **Known bugs**: `MASTER.md` F-48 — was silently missing from the audit ledger (correct scoring, incomplete audit trail) for a period after v7 shipped; fixed 2026-08-04, confirmed 63/63 real v7 rows had it blank pre-fix, all residual discrepancies explained by the missing-log approximation (i.e., scoring was never wrong, only logging was incomplete).
- `strict_statistical_verdict`: **RETEST_REQUIRED** — no standalone, isolated ablation for this exact feature was located; it entered the model as part of a bundled feature-set change (v6→v7) rather than a feature-by-feature test.
- `operator_retention_verdict`: **KEEP** (real, live, materially fitted in both current models; correcting the registry's stale "untested" label is itself the primary actionable output of this entry)
- **Verdict**: `RETEST_REQUIRED` — the feature itself should stay wired (it's real and material), but the registry's factual description needs correcting and a standalone ablation (isolating this feature alone against the rest of the v7/v8 set) has not been located as ever having been run.

---

### `defensive_trend_gap`

- **Model(s) using it**: Not in MLB's active `mlb-elo-trend-lr-v7`/`v8` (their `feature_names` lists only the 6 features named in the task brief — confirmed no 7th feature). Computed unconditionally in `learned_forward.py:69` for every sport but only consumed by NBA/WNBA/SOCCER models per their own `feature_names`.
- **Source code location**: `src/model_prediction/features/trends.py` (`TeamTrend.defensive_momentum`).
- **Note**: Included here only because `learned_forward.py`'s `_compute_features` computes it unconditionally alongside `elo_probability`/`trend_gap` regardless of sport, which could cause confusion that it's an MLB v7/v8 input — **confirmed it is not**, by reading both artifacts' `feature_names` arrays directly.
- **Verdict**: Not applicable to the MLB moneyline audit scope; see NBA/WNBA/SOCCER registry entries for its real verdict (`remove_candidate` per `tested_features.json` — coefficients near zero in both leagues it's actually used in).

---

### Statcast-style starter/bullpen features on `origin/rebuild/clean-slate-v1` (candidates only — not adopted anywhere on current `main`)

Checked directly against `origin/rebuild/clean-slate-v1` (local pin: tag
`archive/model-source-clean-slate-70250b1` @ `70250b10889ce58452b9685c12dbf515028b7d81`)
via `git show`/`git ls-tree` — **not** current-`main` code, and none of these
are wired into `mlb-elo-trend-lr-v7`/`v8` or the Poisson Trend Engine on
`main`. Listed as research candidates per the task brief, with an honest
accounting of what's actually implemented there vs. only aspirational.

**Actually implemented** in `src/model_prediction/rebuild/mlb_features.py`
(`MLB_INTENSITY_FEATURES`/`MLB_DIFFERENTIAL_FEATURES`, confirmed by direct
read):

| Feature | Column name(s) | Head | Formula source |
|---|---|---|---|
| Starter average fastball velocity | `home_sp_avg_velocity`/`away_sp_avg_velocity` | Intensity | `release_speed.mean()` over a rolling Statcast pitch window |
| Starter CSW% (called-strike + whiff) | `home_sp_csw_pct`/`away_sp_csw_pct` | Intensity | `CSW_DESCRIPTIONS` count / total pitches |
| Starter K% | `home_sp_k_pct`/`away_sp_k_pct` | Differential | strikeouts / batters faced, rolling |
| Starter BB% | `home_sp_bb_pct`/`away_sp_bb_pct` | Differential | walks / batters faced, rolling |
| Starter days rest | `home_sp_days_rest`/`away_sp_days_rest` | Differential | days since prior start |
| Bullpen average velocity | `home_bp_bullpen_avg_velocity`/`away_bp_bullpen_avg_velocity` | Differential | relief `release_speed.mean()` |
| Bullpen pitch count (workload) | `home_bp_bullpen_pitches`/`away_bp_bullpen_pitches` | Intensity | rolling relief pitch count |
| Starter whiff% | (computed, `whiff_pct`) | — | swings-and-misses / swings — computed but **not** in either `MLB_INTENSITY_FEATURES` or `MLB_DIFFERENTIAL_FEATURES` list, i.e. built but not actually fed to either head |

All paired with `*_availability` missingness indicators (`home_sp_availability`,
`away_bp_availability`, etc.) per that branch's own "imputed value +
missingness indicator must be paired" convention, and all real fields are
`NaN` (not a fabricated 0.0) when unavailable.

**Named in the task brief but NOT found implemented** on
`rebuild/clean-slate-v1`, verified by grep across
`src/model_prediction/rebuild/` for each term:

- **K-BB%**: only appears as the string `"starter_k_bb_pct"` inside
  `horizons.py::horizon_specs_for_sport`'s aspirational per-horizon feature
  *name list* (a planning/spec dataclass describing what a horizon *should*
  eventually carry) — never computed anywhere, never a real DataFrame
  column.
- **Pitch mix**: not found under any name.
- **xwOBA**: appears once, in a comment in `ablation.py` explicitly
  labeling it as a **removed, aspirational, never-real** name:
  *"every feature name below used to be a legacy/aspirational name
  (elo_probability, starter_era_gap, lineup_xwoba, home_availability_pct,
  ...) that doesn't exist anywhere in the real rebuild feature schema...
  verified via grep — zero matches for any of the old names."* I.e. the
  clean-slate branch's own commit history already flagged and removed this
  exact aspirational name once.
- **Hard-hit%**: not found under any name.
- **Barrel%**: not found under any name.

**Verdict for the implemented subset** (velocity, CSW%, K%, BB%, days
rest, bullpen velocity/workload): `KEEP_RESEARCH_ONLY` — real, computed,
NaN-safe, used by the clean-slate branch's own `RunIntensityHead`/
`RunDifferentialHead`, but that branch's overall coherent-score benchmark
(see `MLB_CLEAN_SLATE_TWO_HEAD.md`) barely clears a naive-constant
baseline, so no individual feature within it has independent, isolated
evidence of value — and none of it is on `main` or wired into any promoted
model today.

**Verdict for K-BB%/pitch-mix/xwOBA/hard-hit%/barrel%**: `KEEP_RESEARCH_ONLY`
is too generous — these are **not implemented anywhere in this repository**
on either branch. They should be tracked as unbuilt candidates, not as
tested-and-pending features. Both `strict_statistical_verdict` and
`operator_retention_verdict`: **N/A (not built)**.

---

## Summary table

| Feature | Verdict | strict | operator |
|---|---|---|---|
| `elo_probability` | `KEEP_CORE` | INCONCLUSIVE (MLB) | KEEP |
| `trend_gap` | `RETEST_REQUIRED` (as orthogonalized residual) | INCONCLUSIVE | KEEP |
| `park_factor` | `BLOCKED_PIT` | REMOVE CANDIDATE | KEEP (research only) |
| `weather_factor` | `BLOCKED_PIT` | REMOVE CANDIDATE | KEEP (research only) |
| `starter_era_gap` (live, v8) | `RETEST_REQUIRED` (rename `starter_era_gap_pit_history`) | INCONCLUSIVE | KEEP |
| `starter_era_gap_legacy_event_map` | `REMOVE` (permanent) | REJECT | REJECT |
| `starter_fip_gap` | `RETEST_REQUIRED` | INCONCLUSIVE (unverified claim) | KEEP |
| `starting_pitcher_fip` | `REJECT` | REJECT | REJECT |
| `pitcher_era_gap` | `KEEP` (v7 only; `SUPERSEDED` in v8) | INCONCLUSIVE/REMOVE CANDIDATE | KEEP (override) |
| `bullpen_weakness_gap` | `RETEST_REQUIRED` (standalone ablation missing; registry entry stale) | RETEST_REQUIRED | KEEP |
| `defensive_trend_gap` | N/A to MLB (not in v7/v8 feature_names) | — | — |
| Clean-slate Statcast subset (velocity/CSW%/K%/BB%/days-rest/bullpen) | `KEEP_RESEARCH_ONLY` | — | — |
| K-BB%, pitch mix, xwOBA, hard-hit%, barrel% | Not implemented anywhere | N/A | N/A |
