# Gate G1 for nba-player-v10 (player layer), validation 2025-11-03 .. 2026-02-22

Spec: `outputs/nba/v10_player/PREREG.md` (committed before this run). Split = the replay
harness's pinned split: train < 2025-11-03 <= validation < 2026-02-23; the locked holdout is
never loaded. Train rows start 2017-10-01 (2016-17 warms the ratings up). Closing lines are a
benchmark only (no capture time). Validation closes are the harness's own (same file v9 was
scored on); train closes come from the 2007-2026 archive. CIs: 95% percentile, 2000
date-cluster bootstrap draws (`nba_replay_harness.gap_statistics`, seed 20261120).

## G1 (gating)

| variant | market | n train | n val | lambda train [95% CI] | lambda val [95% CI] | corr val [95% CI] | sigma (train) | val RMSE close | val RMSE anchored | lambda train, v9-matched window | G1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| exavail | spread | 9958 | 762 | +0.142 [+0.064, +0.226] | -0.085 [-0.344, +0.156] | -0.020 [-0.079, +0.036] | 13.15 | 13.967 | 13.983 | +0.162 [-0.028, +0.347] (n 1968) | FAIL |
| exavail | total | 9958 | 762 | -0.031 [-0.113, +0.044] | -0.204 [-0.508, +0.102] | -0.051 [-0.130, +0.029] | 18.13 | 18.183 | 18.183 | -0.063 [-0.216, +0.101] (n 1968) | FAIL |
| avail | spread | 5147 | 759 | +0.283 [+0.143, +0.414] | +0.030 [-0.253, +0.329] | +0.009 [-0.047, +0.069] | 13.40 | 13.872 | 13.890 | +0.267 [+0.056, +0.504] (n 1911) | FAIL |
| avail | total | 5147 | 759 | -0.034 [-0.145, +0.082] | -0.158 [-0.481, +0.163] | -0.035 [-0.116, +0.045] | 17.84 | 18.175 | 18.175 | -0.050 [-0.235, +0.130] (n 1911) | FAIL |

Pass rule (identical to `outputs/nba/v10_harness/G1.md`): lambda (train) CI > 0, lambda
(validation refit) CI > 0, corr (validation) CI > 0, n val >= 700. A failing component ships
with lambda = 0 and is not re-tuned on validation.

## Same validation games: player layer vs v9 / v9.1 / close

Bias = mean(actual - model). corr = corr(model - close, actual - close); lambda = through-origin
weight of (actual - close) on (model - close), both refit on these validation games.

### spread (n common = 762)

| model | n | RMSE vs actual | bias | close RMSE | close bias | corr [95% CI] | lambda [95% CI] |
|---|---|---|---|---|---|---|---|
| player exavail | 762 | 14.400 | -0.76 | 13.967 | -0.77 | -0.020 [-0.079, +0.036] | -0.085 [-0.344, +0.156] |
| v9 | 762 | 14.591 | -0.27 | 13.967 | -0.77 | -0.015 [-0.085, +0.053] | -0.029 [-0.273, +0.214] |
| v9.1 | 762 | 14.590 | -0.27 | 13.967 | -0.77 | -0.015 [-0.085, +0.052] | -0.029 [-0.274, +0.213] |

Availability subset (n = 759):

| model | n | RMSE vs actual | bias | close RMSE | close bias | corr [95% CI] | lambda [95% CI] |
|---|---|---|---|---|---|---|---|
| player avail | 759 | 14.133 | -0.98 | 13.872 | -0.83 | +0.009 [-0.047, +0.069] | +0.030 [-0.253, +0.329] |
| player exavail | 759 | 14.307 | -0.82 | 13.872 | -0.83 | -0.019 [-0.078, +0.037] | -0.080 [-0.335, +0.160] |
| v9 | 759 | 14.463 | -0.33 | 13.872 | -0.83 | -0.007 [-0.074, +0.059] | +0.002 [-0.231, +0.238] |
| v9.1 | 759 | 14.463 | -0.33 | 13.872 | -0.83 | -0.007 [-0.075, +0.059] | +0.002 [-0.231, +0.238] |

Exploratory (not gating): versus the ESPN opening line, n = 762. 'close RMSE'
here is the opening line's RMSE; the 'close' row uses the closing line as the model.

| model | n | RMSE vs actual | bias | open RMSE | open bias | corr vs open [95% CI] | lambda vs open [95% CI] |
|---|---|---|---|---|---|---|---|
| player exavail | 762 | 14.400 | -0.76 | 14.315 | -0.75 | +0.070 [+0.007, +0.133] | +0.351 [+0.032, +0.664] |
| v9 | 762 | 14.591 | -0.27 | 14.315 | -0.75 | +0.030 [-0.046, +0.107] | +0.158 [-0.167, +0.486] |
| v9.1 | 762 | 14.590 | -0.27 | 14.315 | -0.75 | +0.030 [-0.046, +0.107] | +0.158 [-0.168, +0.486] |
| close | 762 | 13.967 | -0.77 | 14.315 | -0.75 | +0.228 [+0.163, +0.287] | +1.345 [+0.961, +1.730] |

### total (n common = 762)

| model | n | RMSE vs actual | bias | close RMSE | close bias | corr [95% CI] | lambda [95% CI] |
|---|---|---|---|---|---|---|---|
| player exavail | 762 | 19.041 | -1.04 | 18.183 | -0.58 | -0.051 [-0.130, +0.029] | -0.204 [-0.508, +0.102] |
| v9 | 762 | 19.423 | +3.34 | 18.183 | -0.58 | -0.018 [-0.089, +0.050] | +0.009 [-0.198, +0.201] |
| v9.1 | 762 | 19.025 | -0.34 | 18.183 | -0.58 | -0.030 [-0.107, +0.041] | -0.100 [-0.373, +0.158] |

Availability subset (n = 759):

| model | n | RMSE vs actual | bias | close RMSE | close bias | corr [95% CI] | lambda [95% CI] |
|---|---|---|---|---|---|---|---|
| player avail | 759 | 18.899 | -1.18 | 18.175 | -0.55 | -0.035 [-0.116, +0.045] | -0.158 [-0.481, +0.163] |
| player exavail | 759 | 19.030 | -0.99 | 18.175 | -0.55 | -0.050 [-0.130, +0.031] | -0.201 [-0.511, +0.109] |
| v9 | 759 | 19.413 | +3.37 | 18.175 | -0.55 | -0.016 [-0.087, +0.052] | +0.012 [-0.196, +0.203] |
| v9.1 | 759 | 19.010 | -0.30 | 18.175 | -0.55 | -0.028 [-0.107, +0.044] | -0.093 [-0.369, +0.170] |

Exploratory (not gating): versus the ESPN opening line, n = 762. 'close RMSE'
here is the opening line's RMSE; the 'close' row uses the closing line as the model.

| model | n | RMSE vs actual | bias | open RMSE | open bias | corr vs open [95% CI] | lambda vs open [95% CI] |
|---|---|---|---|---|---|---|---|
| player exavail | 762 | 19.041 | -1.04 | 18.694 | -0.32 | +0.028 [-0.045, +0.097] | +0.113 [-0.225, +0.427] |
| v9 | 762 | 19.423 | +3.34 | 18.694 | -0.32 | +0.035 [-0.032, +0.098] | +0.117 [-0.102, +0.342] |
| v9.1 | 762 | 19.025 | -0.34 | 18.694 | -0.32 | +0.031 [-0.032, +0.094] | +0.139 [-0.144, +0.421] |
| close | 762 | 18.183 | -0.58 | 18.694 | -0.32 | +0.235 [+0.174, +0.295] | +1.107 [+0.794, +1.458] |

## Hindsight diagnostic (NOT a model): ratings with the game's actual minutes

Uses the box score of the game being priced, so it can never be a decision input. It is
an upper bound that leaks outcome (actual minutes respond to the result, e.g. blowouts).

| market | n | RMSE vs actual | bias | close RMSE | close bias | corr [95% CI] | lambda [95% CI] |
|---|---|---|---|---|---|---|---|
| oracle spread | 762 | 13.984 | -0.85 | 13.967 | -0.77 | +0.096 [+0.035, +0.155] | +0.470 [+0.169, +0.761] |
| oracle total | 762 | 18.709 | -1.26 | 18.183 | -0.58 | +0.006 [-0.073, +0.084] | +0.003 [-0.321, +0.324] |

## Coefficients (fit on train)

- `exavail`: `{"margin": {"b2b_diff": 2.3657089330661973, "home_court": 2.0237435051806614, "strength_diff": 0.9124660502657966, "three_in_four_diff": 0.3409561099276599}, "n_margin": 9961, "n_total": 9961, "sigma_margin": 13.420608020851233, "sigma_total": 18.73041173820244, "total": {"b2b_count": -0.5506566491671474, "intercept": 17.460421774824063, "playoff": -8.45455237721284, "poss_x_eff": 0.9256804333697405, "three_in_four_count": -0.6337188029140622}, "variant": "exavail"}`
- `avail`: `{"margin": {"b2b_diff": 2.352877956940487, "home_court": 2.040196420516277, "strength_diff": 0.9233576992561194, "three_in_four_diff": 0.201440168879218}, "n_margin": 5149, "n_total": 5149, "sigma_margin": 13.542452007986052, "sigma_total": 18.402187246395318, "total": {"b2b_count": -0.31007757105257144, "intercept": 18.398898482055827, "playoff": -9.109867312766646, "poss_x_eff": 0.92311137282165, "three_in_four_count": -0.5973837671695507}, "variant": "avail"}`
- `oracle`: `{"margin": {"b2b_diff": 1.8579996227941193, "home_court": 1.9726956822141293, "strength_diff": 0.914013327171803, "three_in_four_diff": 0.44443529749037025}, "n_margin": 10047, "n_total": 10047, "sigma_margin": 13.274312379772505, "sigma_total": 18.55647471911463, "total": {"b2b_count": -0.3949247571965877, "intercept": 11.348124846460221, "playoff": -8.944744555222192, "poss_x_eff": 0.95311057658836, "three_in_four_count": -0.6517689719927544}, "variant": "oracle"}`

## Reading (hand-written after the run; nothing above was re-tuned)

**Verdict: G1 FAIL in all four gated cells (exavail and avail, spread and total).** Every
cell ships lambda = 0 in `config/models/research/nba-player-v10.json`; the anchored price is
the market. The failing condition is the same everywhere: the validation corr CI and the
validation lambda CI include 0 (spread corr -0.020 exavail / +0.009 avail; total -0.051 / -0.035).

- **Hyperparameters (train-only CV, `fits/cv.json`):** margin half-life 730 days, ridge 30;
  total half-life 365 days, ridge 30. Ridge residual beat box-prior-only (lambda = infinity) in
  both markets (margin RMSE 13.41 vs 13.77, efficiency 16.62 vs 16.87, oracle minutes).
- **What improved versus v9/v9.1 on the same 762 games:** spread RMSE 14.40 (exavail) and
  14.13 (avail, n 759) vs 14.59 for v9/v9.1; total RMSE 19.04 / 18.90 vs 19.42 (v9) and
  19.03 (v9.1); total bias -1.04 vs +3.34 (v9). The close is still better: 13.97 / 18.18.
  Availability helps (spread RMSE -0.17, total -0.13 on the same games) but not past the close.
- **Train says something, validation cannot confirm it.** Lambda on train is positive for
  spreads (exavail +0.142 [+0.064, +0.226]; avail +0.283 [+0.143, +0.414]). Per season
  (train only, point estimates, closes from the 2007-2026 archive): exavail spread corr -0.002
  (2017-18), +0.026, +0.099 (2019-20 bubble), -0.007, +0.013, +0.055, +0.030, +0.036 (2024-25);
  avail spread corr +0.050, +0.070, +0.042, +0.043 for 2021-22 .. 2024-25. A true corr of ~0.045
  is about 1.2 standard errors on 760 games, so the validation window is underpowered for an
  effect this small, and train lambda is partly in-sample (the box prior and the step 4/5
  coefficients were fit on those rows); the pre-2024 archive closes were only checked against
  the harness closes from 2024-01 on, so older lines are not verified as true closes. The
  pre-registered rule says FAIL; this is recorded as a
  reason to keep measuring forward (Phase 5), not as a pass.
- **Totals:** no sign of information beyond the close in train or validation.
- **Opening line (exploratory, not gating, one of many comparisons):** the exavail spread has
  corr +0.070 [+0.007, +0.133] and lambda +0.351 [+0.032, +0.664] against the DraftKings open,
  versus +0.030 for v9. The close itself has corr +0.228 against the open, so the model holds a
  small part of what moves the line between open and close. That would only matter for a
  decision taken at the open, and the opens carry no capture time while the model uses box
  scores through the previous ET date, so the model's information may postdate the open. It is
  not a decision-time edge, not a G1 result, and was not used to change anything.
- **Hindsight oracle (actual minutes of the priced game):** spread corr +0.096 [+0.035, +0.155],
  RMSE 13.98 (close 13.97). This is an upper bound, not a model: actual minutes are partly
  caused by the result (starters sit in blowouts), so it leaks outcome. It is consistent with
  the minutes/availability projection being a bottleneck, but the leak means it cannot say how
  much of the gap that is.

**PIT checks.** Ratings and features as of D are bit-identical when later games are appended
or same-day box scores are rewritten (`tests/test_nba_player_v10.py`, verified to fail when
same-day games enter the state first); injury rows observed after tip - 60 min are ignored
(test fails without the filter). All 12,386 box games carry an ET date equal to the ET date of
their tip (no UTC-date labels). Train and validation features come from the same single walk
and code path, so a feature leak would inflate validation too; validation shows nothing. The
v9-matched train window gives similar point estimates (exavail spread +0.162, CI includes 0 at
n 1,968; avail +0.267 [+0.056, +0.504]), so the train signal is not only an early-season
artifact. The remaining explanations are in-sample fitting of the prior and team coefficients,
and a genuinely small effect that 760 games cannot resolve.

**Data limitations found during the run.** About 163 games per season in 2016-17 and 2017-18
have no player minutes (the ESPN summary returns `--`); they are almost all New Orleans and
Chicago games (NOP 82 + 91, CHI 87 + 82 team-games), so this looks like a recoverable backfill
hole for two teams rather than an ESPN-wide gap (only one raw event was inspected). Those games
are skipped and cause most train NO_CALLs (HISTORY_SHORT 179, BOX_GAP 168 of 10,315 train rows).
Train only; validation has full coverage. No box
scores exist for the 2024-25 postseason. Validation has no exavail NO_CALLs; avail has 3
(team report NOT YET SUBMITTED).

**Post-registration code changes.** (1) Daily ridge solve switched to warm-started CG
(numerical only, same solution to rtol 1e-10). (2) `fit-minutes` re-run once the train injury
reports were complete (2021-10 .. 2025-11), as PREREG 3.3 states. (3) Spec-conformance fix
after the first G1 run: the margin and total ratings use different half-lives (730 / 365
days), and the merge had kept the total constant `c` of the 730-day prior (-11.81) instead of
the 365-day prior that supplies `delta` (-12.92). Fixed and re-run; the step-5 intercept had
absorbed it, every number moved in the third decimal at most (e.g. exavail total corr -0.051
both times, total RMSE 19.040 -> 19.041), and no verdict changed.
