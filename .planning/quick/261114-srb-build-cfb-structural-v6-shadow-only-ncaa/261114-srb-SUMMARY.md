---
phase: quick-261114-srb
plan: 01
subsystem: ncaaf/cfb-structural-v6
tags: [ncaaf, cfb-structural-v6, shadow-challenger, joint-ridge, market-relative-check, espn-backfill, espn-lines]
requires: []
provides:
  - cfb-structural-v6 research challenger (src feature module, model class, research-only artifact), inert
  - frozen v6 fit (sha256 6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70) with single-scored locked holdout
  - v6-only ESPN history backfill (16,378 events) and ESPN historical closing-line dataset (9,364 events)
  - weather / QB / pace input audit and history-gap audit of the live NCAAF history
affects: [NCAAF research; docs/DEBUG.md]
tech-stack:
  added: []
  patterns: [shadow-feature pattern, frozen-fit sha256 before holdout, bit-exact research/src parity gate]
key-files:
  created:
    - src/model_prediction/features/cfb_features_v6.py
    - src/model_prediction/models/cfb_structural_v6.py
    - config/models/cfb-structural-v6.json
    - tests/test_cfb_structural_v6.py
    - scripts/cfb_v6_lib.py
    - scripts/cfb_v6_history_backfill.py
    - scripts/cfb_v6_input_audit.py
    - scripts/cfb_v6_market_check.py
    - scripts/cfb_v6_research.py
    - scripts/cfb_v6_espn_lines.py
    - scripts/cfb_v6_lines_audit.py
    - outputs/ncaaf/cfb_v6_*.json and cfb_v6_*.md
  modified:
    - scripts/cfb_v6_research.py
    - docs/DEBUG.md
decisions:
  - "D-RES-BUFFER 5.0 h: a game feeds ratings only if kickoff + 5 h <= decision time"
  - "D-RATINGS: weighted ridge, mu and one home term unpenalised (h_away = -h_home for identifiability), previous-season weight, per-season pooled pseudo-team for lower-division singletons"
  - "D-NO-HANDSET-TERMS and D-NO-LINE-WIDENING: no weather, QB, altitude, travel, bye, tier or line-dependent term in v6"
  - "D-SELECTION: mean of margin and total Gaussian log score on validation, Brier tiebreak"
  - "D-FIT-COHORT / D-SPLIT: existing-history events, 2016 burn-in, train to 2022-11-11, validation to 2024-11-14"
  - "v6 is not promoted, not routed, not wired; v5 stays champion"
metrics:
  duration: "about 2 h 20 min (Task 1 from the 02:10Z as-of; Tasks 2 and 3 04:06Z to about 04:35Z)"
  completed: 2026-11-15
status: complete
actuals:
  tokens: 240000
  tasks: 3
  commits: 3
plan_head_before: d0cf17408aaa0a6f74a5fcb32272ccd7ea0b0c9d
plan_head_after: de3208dc516b77b2282a44dda489edd92c851756
---

# Phase quick-261114-srb Plan 01: cfb-structural-v6 shadow challenger Summary

v6 (additive matchup over a joint ridge rating solve, rolling league baseline, walk-forward refit with per-slice margin and total SDs) is a better-calibrated probability model than v5 on the locked holdout (Brier 0.17975 vs 0.19003, delta -0.01029, 95% CI [-0.01524, -0.00553]) and shows no market-relative edge (every correlation interval against the line includes zero); nothing was promoted, routed or wired.

`commits: 3` is measured at SUMMARY time (`git rev-list --count plan_head_before..HEAD`): `93b2d8b0` (Task 1), `8a5a7bcc` (ESPN closing-line robustness, same task), `de3208dc` (Task 2). The final planning-files docs commit (docs/DEBUG.md, this file, STATE.md, PLAN.md) follows it and is not counted. `actuals.tokens` is a chars/4 estimate over the changed files.

## Market-relative verdict

**v6 does not pass the market-relative test.** Correlation of (model - line) with (actual - line), FIRST LOOK, frozen fit `6e38b14c...`:

| set | totals v6 | totals v5 | spreads v6 | spreads v5 |
|---|---|---|---|---|
| 575 reference (design-informed, inside holdout window) | -0.011 [-0.091, +0.054] | -0.033 [-0.119, +0.041] | +0.042 [-0.023, +0.110] | +0.009 [-0.090, +0.105] |
| ESPN lines, holdout window, common set | -0.008 [-0.057, +0.041] (n 1,511) | -0.040 [-0.097, +0.016] | +0.019 [-0.027, +0.066] (n 1,512) | +0.019 [-0.032, +0.068] |
| ESPN lines, fit window, common set | +0.028 [-0.001, +0.058] (n 5,567) | +0.023 [-0.006, +0.051] | -0.009 [-0.041, +0.022] (n 5,574) | +0.004 [-0.024, +0.033] |

All intervals include zero. v6 fixes much of v5's spread compression on the 575 (margin SD 14.82 vs v5 11.78, line 16.35, actual 22.37; MAE 12.56 vs 14.64; 21+ line favourite margin 28.74 vs 16.64 vs line 34.22) but not the total (model SD 5.04 vs line 5.71); side records 232-256 (totals), 237-246 (spreads). Fixing compression was necessary, not sufficient.

Caveats: ESPN lines are closing lines (no `observed_at`), single book from 2024 (ESPN BET 2024-25, DraftKings 2025-26); the 575 reference is design-informed and inside the holdout window; against the 575 the pinned ESPN spreads show 13 sign flips of 576 (inferred reference-side line-pair repeats, not verified); the moneyline Brier gain is against v5's own calibration, not market prices.

## Task 1 (scripts, frozen fit, holdout, market checks) - commits 93b2d8b0, 8a5a7bcc

- v5 reproduction gate passed before any v6 number: totals corr -0.033, side 218-257, model SD 4.13; spreads corr +0.009, side 264-233, MAE 14.64 vs 11.65, margin SD 11.78 / 16.35 / 22.37.
- History gap (audit a): existing history 7,965 events (sha256 `659e33b2...`), ESPN groups=80/81 lists 16,378 over all 11 seasons (440 requests, 0 failed); 0 score disagreements on the 7,965 overlap. FBS-vs-FCS: history holds 6-12 per season vs 98-126 on ESPN (misses 90-95%); FCS-vs-FCS 0-16 vs 653-752 (98-100%). 2026: 878 of 1,384 missing (226 of 731 FBS-involved); FBS teams mean 2.46 missing (median 2); week 6 missing entirely (108 of 108); week 11 mostly ingest lag (93 of 104); "16 of 65 on 2026-09-26" confirmed by US-Eastern date. Backfill: 16,378 rows, sha256 `cc704059e4684edb9e580dff5033433424ee34e6a5fe96995c7c272ae82d8bf4`.
- Input audit (b): weather is plumbed with real forecasts but non-zero on 25 of 6,129 ledger rows (0.4%), 6 events, largest 1.8 points, never fitted (training history is placeholder 5 mph / 65 F on 7,542 rows, 0 / 72 on 182); QB adjustment dead (0 of 2,430 side cells, 387 injury entries over 129 snapshots with 0 QB entries, starter = season passing leader); no possession or drive data (`possessions` constant 12.5, success rate constant 0.42, EPA without provenance).
- ESPN closing-line dataset: 9,364 events, sha256 `2b19a2545fb2e5f9df4c55a1b57ee9f88b11eab371828a4a7ed88e8bdfb7fa54`, rule `espn-lines-rule-1` (rule sha256 `ee23e34e...`).
- Frozen fit `6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70`: ridge 0.1, previous-season weight 0.2, k 2, buffer 5 h; margin calibration 0.4044 + 0.8716 m; total 21.3898 + 0.6097 t; margin SD 16.35 / 16.89 / 16.76 and total SD 16.86 / 16.72 / 17.43 (established / thin / FBS-vs-FCS); train 4,102, validation 1,591. The grid was extended downward twice on validation before the freeze and the chosen ridge 0.1 is the smallest value in the final grid (edge selection, recorded in the frozen fit).
- Holdout (common cohort n 1,527, scored once; v5 incumbent check analytic Brier 0.19003 vs recorded engine 0.18933): v6 vs v5 Brier 0.17975 vs 0.19003 (-0.01029 [-0.01524, -0.00553]); log loss 0.5333 vs 0.5589; ECE 0.0225 vs 0.0448; margin log score +0.0479 [+0.0306, +0.0640]; total log score +0.0150 [+0.0062, +0.0240]; margin MAE 12.286 vs 12.885 (-0.599 [-0.847, -0.332]); Brier lower in 2024, 2025 and 2026 and every SD slice; backfill-only FBS-involved (n 384, separate): Brier 0.1046.
- Item 7 (market-anchored residual): blocked locally on the 575 lines at planning time; **not done, now feasible** with the 9,364 ESPN lines (8,279 totals and 8,376 spreads covered by v6). Recorded as a follow-up; nothing was fitted on any line.

## Task 2 (src port, artifact, tests) - commit de3208dc

- `features/cfb_features_v6.py`: the research lib copied verbatim (imports made relative) plus `CFBV6MatchupFeatures` (raw wind / temperature / precipitation and QB starter probabilities recorded as passed, None stays None) and `CFBV6FeatureExtractor` (solver config from the artifact; unrated team raises `V6NoCall` with `NO_CALL_CFB_V6_UNRATED_TEAM`). `models/cfb_structural_v6.py`: new `CFBStructuralV6Model` (not a v4/v5 subclass), fail-closed loader (model_version, artifact hash, unread keys, unproduced features). `config/models/cfb-structural-v6.json`: written by `cfb_v6_research.py write-artifact` from the frozen fit (qualification `research`, `qualified: false`, no gates block, artifact_hash `db869a4d96642983697571a828301585cc08a834f4d28b738622a7f57b38e5d2`, verifies under `compute_artifact_hash`).
- **Parity (exact):** `cfb_v6_research.py parity` over every cached row: 14,639 projections, max abs difference 0.0 on margin, total, mu and home term (and window / sample metadata equal), 183 no-call rows match the cache; a 220-row stratified sample across 2017-2026 also 0.0. The src model's simulated moneyline probability vs the analytic Normal one on 50 holdout rows: max 0.035, mean 0.017 (informational: negative-binomial engine vs Normal).
- **Tests:** `tests/test_cfb_structural_v6.py` 20 tests, all pass; with `tests/test_artifact_hash_verification.py` 23 pass and 1 fails (see Deviations: pre-existing, 4 foreign artifacts). `ruff check src/ tests/` clean. Detached worktree `C:/wtsrb2` at `de3208dc` (confirmed `model_prediction.__file__` inside the worktree): v6 + hash + v5 test files give 27 passed, 1 failed (the same pre-existing hash test); `scripts/cfb_v6_research.py verify-frozen` ok there.
- **Prove-it-fails:** each src mutation below made the named test fail, then the file was restored and its sha256 compared equal: additive form replaced by the half-sum (assert 37.0 == 46.0); result buffer removed; future games admitted; unrated check removed; opponent rating dropped from the solve; mu fixed at 28.0; raw wind dropped; backfill overriding existing score; backfill sha check removed; unread-key check removed; artifact hash check removed. The inertness probe has its own negative control (the same probe reports v6, and v5, as loaded when they are imported). Formal RED-before-code was not done: the modules were ported before the tests (parity gate first, per the executor's ordering); each test is instead proven by mutation.
- Inertness: learned_forward, college_football and cli.daily do not load either v6 module (clean subprocess, `MODEL_PREDICTION_*` stripped); no src module other than the two v6 files names them; production.yaml and model.yaml do not name `cfb-structural-v6`; the v6 feature module imports nothing from the v5 feature module.

## Deviations from Plan

**1. [Orchestrator instruction over plan] `scripts/cfb_v6_lib.py` kept, not deleted.** The plan said delete it and repoint the scripts to src. It is kept byte-for-byte as the historical research copy because `verify-frozen` hash-pins it; **two implementations exist and the parity test (exact, 14,639 rows) is what ties them together**. `scripts/cfb_v6_market_check.py` is untouched. Only `cfb_v6_research.py` gained `parity` and `write-artifact`.

**2. [CRLF finding, reported not changed]** `git show HEAD:scripts/cfb_v6_lib.py` hashes to `85bd034f...` (LF blob) while the pin in the frozen fit is `fe42c288...`, which is the CRLF working file hash. With `core.autocrlf=true` a fresh checkout is CRLF, so verify-frozen passes there (verified in the detached worktree) but it would fail on an LF-only checkout such as a Linux CI. Not re-pinned; the params sha256 is untouched.

**3. [Plan deviation] V6Game, not the v5 record dataclass.** The plan said to reuse the v5 feature module's record dataclass; the frozen math runs on `V6Game`, so v6 imports nothing from the v5 feature module (also what the isolation test requires).

**4. [Pre-existing, out of scope] `tests/test_artifact_hash_verification.py::test_only_documented_evidence_artifacts_fail_self_verification` fails** on four committed artifacts that do not self-verify (`nba-moneyline-v8`, `nba-spread-margin-v8`, `nba-total-margin-v8`, `tennis-games-markov-v3`); it fails identically at the parent commit `HEAD~1` of Task 2, and v6 is not in the failing set. Not fixed, not changed (scope boundary); the pin was not touched.

**5. [Rule 2] Engine guards, no gates.** `calibrate_means` floors the total at 24.0 and each side mean at 3.0 exactly as v5 does (the replay contract requires positive means); `uncertainty` on `GamePrediction` is 0.0 (v6 has no scalar; the fitted SDs ride in `feature_basis`); QB / altitude / weather fields on the forecast are 0.0; `main_gates_from_artifact` is not called and a `gates` key is rejected as an unread parameter.

**6. [Extractor] The extractor reuses the last solve when the admitted-game count and season are unchanged** (bit-identical by construction; the parity gate covers it).

**Risk found, not changed:** a team name never seen in the history resolves to the season's pooled lower-division pseudo-team and is projected instead of declined (an invented home team against Ohio State projects a raw margin of -90.9). The frozen math is unchanged; any serving use of v6 would need a known-team gate first.

## Design decisions

- **D-RES-BUFFER:** 5.0 hours (more than a 3.5 h game plus overtime or delay); stricter than v5's `start < observed`; boundary inclusive (a game with start + 5 h exactly at the cutoff is admitted, tested).
- **D-RATINGS:** one weighted ridge solve per distinct eligible-game set; `mu` and one home term unpenalised with `h_away = -h_home` (identifiability: with only non-neutral games `mu`, `h_home`, `h_away` are collinear); previous-season rows weighted `w_prev`; forfeits (1-0 placeholder) and pool-vs-pool games left out; lower-division singletons pooled per season; team identity by resolve_team canonical name, then shared ESPN id, then `ID:`/`NAME:`; tier labels feed only the FBS-vs-FCS dispersion slice.
- **D-NO-HANDSET-TERMS:** altitude, travel, bye, weather and QB adjustments are not carried (never fitted; history has no real weather or point-in-time QB); raw inputs recorded in the payload.
- **D-NO-LINE-WIDENING:** SDs come only from fitted per-slice outcome residuals for margin and total.
- **D-SELECTION:** validation chooses by mean of margin and total Gaussian log score, Brier as tiebreak.
- **D-FIT-COHORT:** fit and holdout cohorts are the existing-history events (2016 burn-in removed); backfill games are rating inputs, scored separately.
- **D-SPLIT:** train to 2022-11-11, validation to 2024-11-14, holdout after (v5's holdout extended to the pinned as-of). `validation.build_walk_forward_rows` builds generic FeatureStore rows and does not fit a cached-projection cohort; the v5 harness uses only `chronological_split`, as here.

## Data limitations

No drive or play data (pace estimated from scoring); `home_epa_per_play` has no provenance and is not used; placeholder weather in training history; retrospective FBS/FCS tier labels (FBS-vs-FCS slice derived per season from the ESPN feed class); ESPN lines are closing benchmarks without decision-time quotes.

## Second-look statement

None. No arithmetic bug was found after the freeze (parity is exact), the holdout and market checks ran once against the single frozen hash, and nothing was re-frozen.

## Open items

Item 7 on the ESPN lines (follow-up); daily ingest lacks `groups=80/81` so v5's live ratings sit on the gap-ridden history; known-team gate before any serving use of v6; the four pre-existing non-verifying artifacts in the hash test; two copies of the v6 math (research and src) by design, tied by the parity test.

## Self-Check: PASSED

- FOUND: src/model_prediction/features/cfb_features_v6.py, src/model_prediction/models/cfb_structural_v6.py, config/models/cfb-structural-v6.json, tests/test_cfb_structural_v6.py
- FOUND commits: 93b2d8b0, 8a5a7bcc, de3208dc (ancestors of HEAD)
- Verified: parity max abs diff 0.0; v6 tests pass; ruff clean on src/ and tests/; verify-frozen ok; `v6 --check-only` ok (spread 576, total 575).
