# cfb-structural-v7.1 vs v7: confirmatory holdout read (quick-261122-v71)

Rule `cfb-v71-holdout-rule-1` sha256 `fe97151186fcca713cdba0a1dcd2c6787a5d06d9ae1d1698938023f86e532e9b`; read at 2026-11-22T03:13:54Z.

**SECOND READ. The v7 holdout window (2024-11-15 onwards) was read for v7 on 2026-11-21T21:12:25Z (cfb_v7_report.md), and the 2026-11-21/22 bug hunt read holdout neutral-site residuals (+2.02, n 115). This read is confirmatory only: nothing is selected or tuned on it, and the neutral-site before/after is design-informed.**

Verdict: **NOT_CONFIRMED**. Selected v7.1 candidate: `{"arm": "cfbd_portal", "combo": "cfbd_portal_m0.5_hl45", "feature_set": "full", "half_life_days": 45.0, "lambda_multiplier": 0.5}`.

v7 reproduction from its own files: PASS (n 1948, MAE 12.4674, Brier 0.16356).

| subset | n | v7 MAE | v7.1 MAE | v7 Brier | v7.1 Brier | v7 log loss | v7.1 log loss | MAE delta [CI] | Brier delta [CI] | log loss delta [CI] |
|---|---|---|---|---|---|---|---|---|---|---|
| all | 1948 | 12.4674 | 12.5057 | 0.16356 | 0.16409 | 0.48661 | 0.48769 | +0.038 [-0.082, +0.158] | +0.0005 [-0.0015, +0.0026] | +0.0011 [-0.0042, +0.0065] |
| home | 1833 | 12.4991 | 12.5556 | 0.1591 | 0.15972 | 0.47573 | 0.47709 | +0.057 [-0.062, +0.177] | +0.0006 [-0.0014, +0.0025] | +0.0014 [-0.0038, +0.0063] |
| neutral | 115 | 11.9611 | 11.7103 | 0.23472 | 0.2336 | 0.66011 | 0.65668 | -0.251 [-0.933, +0.447] | -0.0011 [-0.0179, +0.0158] | -0.0034 [-0.0397, +0.0341] |
| fbs_vs_fbs | 1694 | 12.4105 | 12.4484 | 0.18316 | 0.18371 | 0.54106 | 0.54251 | +0.038 [-0.080, +0.156] | +0.0006 [-0.0016, +0.0028] | +0.0014 [-0.0040, +0.0072] |
| fbs_vs_fcs | 254 | 12.8467 | 12.8876 | 0.03286 | 0.03318 | 0.12348 | 0.12212 | +0.041 [-0.297, +0.454] | +0.0003 [-0.0022, +0.0026] | -0.0014 [-0.0104, +0.0079] |
| weeks_0_4 | 645 | 12.9658 | 12.9768 | 0.11226 | 0.11204 | 0.35086 | 0.35054 | +0.011 [-0.268, +0.326] | -0.0002 [-0.0046, +0.0047] | -0.0003 [-0.0122, +0.0133] |
| weeks_5_14 | 1210 | 12.2529 | 12.3265 | 0.18564 | 0.18659 | 0.54603 | 0.54797 | +0.074 [-0.007, +0.152] | +0.0010 [-0.0006, +0.0025] | +0.0019 [-0.0018, +0.0057] |
| postseason | 93 | 11.7998 | 11.5707 | 0.23209 | 0.23221 | 0.65507 | 0.65469 | -0.229 [-1.007, +0.507] | +0.0001 [-0.0186, +0.0184] | -0.0004 [-0.0417, +0.0407] |

Mean actual minus model margin (se):

- v7: home -0.43 (0.37, n 1833), neutral +2.02 (1.36, n 115)
- v71: home -0.32 (0.37, n 1833), neutral -0.97 (1.34, n 115)
- v5: home +2.12 (0.39, n 1833), neutral +1.24 (1.38, n 115)

ECE: v7 0.0280, v7.1 0.0234. Total MAE: v7 12.8922, v7.1 12.8432.

v7.1 minus v5: MAE -0.667 [-0.926, -0.399], Brier -0.0079 [-0.0126, -0.0033], log loss -0.0244 [-0.0367, -0.0126]; v7's gate for v7.1: {'checks': {'coverage': True, 'significant_improvement': True, 'no_point_regression': True}, 'pass': True, 'significantly_worse': False}.

Market-relative vs ESPN close (DESIGN-INFORMED, not gating):

| arm | market | n | corr [CI] | orthogonalised slope [CI] | ATS 3+ | ATS 7+ | verdict |
|---|---|---|---|---|---|---|---|
| v7 | spread | 1891 | +0.0312 [-0.0091, 0.0684] | +0.1041 [-0.0129, 0.2234] | 489-448 (52.19%) | 179-135 (57.01%) | NO_MARKET_SKILL |
| v7 | total | 1899 | +0.0003 [-0.0475, 0.0424] | -0.0201 [-0.2066, 0.1509] | 392-416 (48.51%) | 75-80 (48.39%) | NO_MARKET_SKILL |
| v71 | spread | 1891 | +0.0280 [-0.0164, 0.0684] | +0.0887 [-0.039, 0.2074] | 476-466 (50.53%) | 146-131 (52.71%) | NO_MARKET_SKILL |
| v71 | total | 1899 | +0.0088 [-0.04, 0.0526] | +0.0193 [-0.1764, 0.2006] | 387-419 (48.01%) | 72-67 (51.8%) | NO_MARKET_SKILL |

Calibration by favourite size (projected favourite margin bucket: model win % vs realised win %):

- v7: 0-3 n 330: 53.6% vs 52.4%; 3-7 n 390: 61.7% vs 61.8%; 7-14 n 535: 73.1% vs 73.5%; 14-21 n 282: 84.6% vs 87.6%; 21+ n 411: 96.4% vs 97.6%
- v71: 0-3 n 307: 53.4% vs 52.4%; 3-7 n 401: 61.8% vs 60.8%; 7-14 n 547: 73.4% vs 73.3%; 14-21 n 267: 85.0% vs 86.9%; 21+ n 426: 96.6% vs 97.4%

