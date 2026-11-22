# P1_player_availability confirmation read (addendum v5) - SHUFFLED PLUMBING (no number means anything)

Addendum v5 sha256 `c11e1910cec9fbc1cc1c5ea7b9ef9422d4eb8b8eafae48e96f65c255b5df7b57`. Predictions sha256 `70c6248e69ae31ebdf0c3e490c59c171c3e23ab7b5c7e66642b0ca740f376173`. Coefficients (pooled 2023-2026, commit 411f2a19): b1 +5.8047, b2 -2.6836. Line source espn_backfill_261120. Points and probabilities only.

Usable seasons (>= 85% D1 box pass): [2018, 2019, 2020, 2022] rates {2018: 0.963, 2019: 0.9257, 2020: 0.9086, 2022: 0.9107}; dropped none.

| statistic | n | days | delta | one-sided 95% lower | two-sided 95% | corr cand | corr v7 |
|---|---|---|---|---|---|---|---|
| spread_corr_delta_vs_v7 (pooled) | 20826 | 564 | -0.019266 | -0.022638 | [-0.023288, -0.015445] | +0.056766 | +0.076031 |

**Verdict: PLUMBING_ONLY** (v4: CONFIRMED iff the plain one-sided 95% lower bound of the pooled corr delta > 0 (day-cluster bootstrap, 4000, seed 17); fewer than 3 usable seasons => CONFIRMATION_UNAVAILABLE)

| season | n | delta | corr cand | corr v7 | share adjusted |
|---|---|---|---|---|---|
| 2018 | 4432 | -0.020087 | +0.085541 | +0.105627 | 0.399 |
| 2019 | 5593 | -0.010263 | +0.027069 | +0.037332 | 0.356 |
| 2020 | 5325 | -0.017675 | +0.026083 | +0.043758 | 0.354 |
| 2022 | 5476 | -0.029205 | +0.086266 | +0.115470 | 0.409 |

## Secondary (no verdict)

- margin_rmse_delta_vs_v7: +0.051390 95% [+0.04267, +0.06008]
- cover_logloss_delta_vs_v7: +0.003277 95% [+0.00261, +0.00395]
- moneyline_logloss_delta_vs_v7: +0.001152 95% [+0.00084, +0.00147]
- RMSE cand / v7 / line: {"cand": {"n": 20826, "rmse": 16.6586, "bias": 0.0415}, "v7": {"n": 20826, "rmse": 16.6072, "bias": 0.0471}, "line": {"n": 20826, "rmse": 16.6003, "bias": 0.0247}}
- non-zero-adjustment subset: n 7884 (37.9%)
  - spread_corr_delta_vs_v7: -0.05882 95% [-0.07010, -0.04857]
  - margin_rmse_delta_vs_v7: +0.13786 95% [+0.11561, +0.15976]
  - cover_logloss_delta_vs_v7: +0.00865 95% [+0.00700, +0.01037]
  - adjustment: {"mean": 0.0148, "sd": 0.9721, "mean_abs": 0.8411, "p99_abs": 2.5035}

## Games by season (feature join)

{"2018": {"adjusted_nonzero": 2175, "all_inputs_non_null": 4703, "feature_rows_found_both_sides": 5533, "found_but_null_input": 830, "not_found": 0, "v7_rows": 5533}, "2019": {"adjusted_nonzero": 1991, "all_inputs_non_null": 4360, "feature_rows_found_both_sides": 5597, "found_but_null_input": 1237, "not_found": 0, "v7_rows": 5597}, "2020": {"adjusted_nonzero": 1888, "all_inputs_non_null": 3946, "feature_rows_found_both_sides": 5328, "found_but_null_input": 1382, "not_found": 0, "v7_rows": 5328}, "2022": {"adjusted_nonzero": 2243, "all_inputs_non_null": 4102, "feature_rows_found_both_sides": 5485, "found_but_null_input": 1383, "not_found": 0, "v7_rows": 5485}}

## Disclosures

- Coefficients: one pooled 2023-2026 fit (clause d), committed before any confirmation line or outcome was loaded; no refit.
- Backcast: v7 constants and P1 coefficients come from later seasons and are applied to 2018-2022.
- Date quirk (clause e): table date 0 or 1 day before the Eastern tip day; quirk rows kept.
- Transfers carrying a new ESPN id count as newcomers (v4 feature_table.transfers); P1's inputs are same-season only.
