# MLB Calibration Comparison

dataset_hash: `b3d8249d46ec0bf4c06d8ef00327e09644c47150a11826b7c993bf82774443bd`  
oof_split_manifest_hash: `1c51e275a03424ad6bcda655826eae54b2374d01ae9bcd096d03922cb289db21`


## two_head (n=203)

| method | n_eval | log_loss | brier | ece | cal_intercept | cal_slope |
|---|---|---|---|---|---|---|
| identity | 153 | 0.8679 | 0.2937 | 0.1743 | -0.027 | -0.144 |
| platt | 153 | 0.7441 | 0.2725 | 0.1104 | 0.053 | -0.675 |
| temperature | 153 | 0.7228 | 0.2633 | 0.0823 | -0.012 | -0.687 |
| isotonic | 153 | 0.9169 | 0.2817 | 0.1221 | -0.028 | -0.316 |

**Selected: `temperature`** (lowest real cross-fit log loss; identity is always a valid winner, never forced out.)


### Reliability buckets (raw, uncalibrated)

| bucket | mean_predicted | observed_frequency | n |
|---|---|---|---|
| 0.05 | 0.050 | 0.400 | 5 |
| 0.15 | 0.150 | 0.500 | 12 |
| 0.25 | 0.250 | 0.600 | 15 |
| 0.35 | 0.350 | 0.529 | 17 |
| 0.45 | 0.450 | 0.506 | 89 |
| 0.55 | 0.550 | 0.489 | 47 |
| 0.65 | 0.650 | 0.500 | 8 |
| 0.75 | 0.750 | 1.000 | 1 |
| 0.85 | 0.850 | 0.500 | 8 |
| 0.95 | 0.950 | 0.000 | 1 |

### Cohort calibration (diagnostic only, no calibrator selection)


**By starters:**

| cohort | n | log_loss | brier |
|---|---|---|---|
| one_or_both_missing | 138 | 0.7415 | 0.2689 |
| both_available | 65 | 0.9967 | 0.3095 |

**By weather:**

| cohort | n | log_loss | brier |
|---|---|---|---|
| unavailable | 203 | 0.8232 | 0.2819 |

## xgb_two_head (n=203)

| method | n_eval | log_loss | brier | ece | cal_intercept | cal_slope |
|---|---|---|---|---|---|---|
| identity | 153 | 0.7840 | 0.2832 | 0.1660 | 0.017 | 0.079 |
| platt | 153 | 0.6968 | 0.2518 | 0.0371 | 0.012 | 0.027 |
| temperature | 153 | 0.6964 | 0.2515 | 0.0165 | 0.014 | 0.064 |
| isotonic | 153 | 0.7560 | 0.2720 | 0.0873 | 0.013 | -0.018 |

**Selected: `temperature`** (lowest real cross-fit log loss; identity is always a valid winner, never forced out.)


### Reliability buckets (raw, uncalibrated)

| bucket | mean_predicted | observed_frequency | n |
|---|---|---|---|
| 0.05 | 0.050 | 0.500 | 4 |
| 0.15 | 0.150 | 0.357 | 14 |
| 0.25 | 0.250 | 0.417 | 24 |
| 0.35 | 0.350 | 0.607 | 28 |
| 0.45 | 0.450 | 0.477 | 44 |
| 0.55 | 0.550 | 0.577 | 26 |
| 0.65 | 0.650 | 0.522 | 23 |
| 0.75 | 0.750 | 0.500 | 22 |
| 0.85 | 0.850 | 0.600 | 15 |
| 0.95 | 0.950 | 0.333 | 3 |

### Cohort calibration (diagnostic only, no calibrator selection)


**By starters:**

| cohort | n | log_loss | brier |
|---|---|---|---|
| one_or_both_missing | 138 | 0.7897 | 0.2796 |
| both_available | 65 | 0.7808 | 0.2857 |

**By weather:**

| cohort | n | log_loss | brier |
|---|---|---|---|
| unavailable | 203 | 0.7869 | 0.2816 |

## xgb_direct (n=203)

| method | n_eval | log_loss | brier | ece | cal_intercept | cal_slope |
|---|---|---|---|---|---|---|
| identity | 153 | 0.7273 | 0.2666 | 0.1139 | 0.028 | -0.263 |
| platt | 153 | 0.7918 | 0.2837 | 0.1165 | -0.011 | -0.290 |
| temperature | 153 | 0.7016 | 0.2542 | 0.0441 | 0.018 | -1.164 |
| isotonic | 153 | 1.0253 | 0.2656 | 0.0907 | -0.002 | -0.043 |

**Selected: `temperature`** (lowest real cross-fit log loss; identity is always a valid winner, never forced out.)


### Reliability buckets (raw, uncalibrated)

| bucket | mean_predicted | observed_frequency | n |
|---|---|---|---|
| 0.25 | 0.250 | 0.500 | 6 |
| 0.35 | 0.350 | 0.643 | 14 |
| 0.45 | 0.450 | 0.535 | 71 |
| 0.55 | 0.550 | 0.446 | 83 |
| 0.65 | 0.650 | 0.519 | 27 |
| 0.75 | 0.750 | 1.000 | 2 |

### Cohort calibration (diagnostic only, no calibrator selection)


**By starters:**

| cohort | n | log_loss | brier |
|---|---|---|---|
| one_or_both_missing | 138 | 0.7129 | 0.2595 |
| both_available | 65 | 0.7416 | 0.2740 |

**By weather:**

| cohort | n | log_loss | brier |
|---|---|---|---|
| unavailable | 203 | 0.7221 | 0.2641 |
