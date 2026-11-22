# v5 history-impact check (quick-261115-0pq)

Verdict: **REJECT** (rule `cfb-v5-history-impact-rule-1`, sha256 `f780ad86a2b3ab378382d873cdbd02455b5b5900c4c46e721726d3c6c2f5a236`; pinned and committed before the run).

As of 2026-11-15T05:56:33Z. Append set: 1478 FBS-involved completed non-forfeit events, sha256 `7d0f9cafff801ed758b7f20d913db28176a1a63466be8aa1adf072583da80cbb`.
By class {'FBS-FCS': 1101, 'FBS-FBS': 376, 'FCS-FCS': 1}; by season {'2016': 154, '2017': 137, '2018': 134, '2019': 146, '2020': 50, '2021': 127, '2022': 128, '2023': 135, '2024': 128, '2025': 148, '2026': 191}.
History rows: arm (i) 8014, arm (ii) 9492.

## Locked holdout (1,527 events, same ids both arms)

| metric | arm (i) live | arm (ii) live + append | delta (ii - i) | 95% CI |
|---|---|---|---|---|
| Brier (lower better) | 0.18933 | 0.19040 | +0.001065 | [-0.000815, +0.002998] |
| margin log score (higher better) | -4.21265 | -4.20970 | +0.002951 | [-0.004547, +0.010515] |
| total log score (higher better) | -4.21155 | -4.20731 | +0.004240 | [-0.001014, +0.009189] |
| log loss (informational) | 0.557265 | 0.559138 | 0.001873 | |

Holdout events whose home-win probability changed: 1527 of 1527 (max abs change 0.24523).

## Accept rule

Accept if Brier delta <= +0.001 and each log-score delta >= -0.005. Checks: {'brier': False, 'margin_log_score': True, 'total_log_score': True, 'all': False}. Arm (i) reproduction: {'n': True, 'brier': True, 'margin_log_score': True, 'total_log_score': True, 'reference_gate': True, 'all': True}.

## 575/576 reference lines (informational)

| market | arm | n | corr | 95% CI | side W-L | MAE model | SD model |
|---|---|---|---|---|---|---|---|
| total | (i) | 575 | -0.0328 | [-0.119, +0.041] | 218-257 | 13.152 | 4.133 |
| total | (ii) | 575 | +0.0005 | [-0.075, +0.071] | 209-242 | 13.019 | 4.574 |
| spread | (i) | 576 | +0.0090 | [-0.090, +0.105] | 264-233 | 14.640 | 11.777 |
| spread | (ii) | 576 | +0.0113 | [-0.081, +0.108] | 258-247 | 14.167 | 11.456 |

Reference projection pairs changed by the append: 1133 of 1151.

Arm (i) reference gate:

```
  ok   total n: got 575.0000, recorded 575 (+-0), diff +0.0000
  ok   spread n: got 576.0000, recorded 576 (+-0), diff +0.0000
  ok   total corr: got -0.0328, recorded -0.033 (+-0.005), diff +0.0002
  ok   total side wins: got 218.0000, recorded 218 (+-0), diff +0.0000
  ok   total side losses: got 257.0000, recorded 257 (+-0), diff +0.0000
  ok   total model SD: got 4.1335, recorded 4.13 (+-0.05), diff +0.0035
  ok   spread corr: got 0.0090, recorded 0.009 (+-0.005), diff -0.0000
  ok   spread side wins: got 264.0000, recorded 264 (+-0), diff +0.0000
  ok   spread side losses: got 233.0000, recorded 233 (+-0), diff +0.0000
  ok   spread mae_model: got 14.6398, recorded 14.64 (+-0.05), diff -0.0002
  ok   spread mae_line: got 11.6493, recorded 11.65 (+-0.05), diff -0.0007
  ok   spread sd_model: got 11.7768, recorded 11.78 (+-0.05), diff -0.0032
  ok   spread sd_line: got 16.3479, recorded 16.35 (+-0.05), diff -0.0021
  ok   spread sd_actual: got 22.3710, recorded 22.37 (+-0.05), diff +0.0010
```

Percentages and points only. No promotion; v5 parameters and gates unchanged.
