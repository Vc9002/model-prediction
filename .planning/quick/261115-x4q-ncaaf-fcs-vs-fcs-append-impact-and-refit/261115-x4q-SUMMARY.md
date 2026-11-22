---
status: complete
---
# 261115-x4q SUMMARY
Verdict **ACCEPT** under cfb-fcs-append-rule-1 (sha256 f4668a71388eb177eefa92b72aa452504b70ca220d1f3d0e71117cd17075eba2, committed before any metric).

| holdout (1,527) | arm (i) live | arm (ii) live + FCS | delta | 95% CI |
|---|---|---|---|---|
| Brier | 0.19036 | 0.18974 | -0.00062 | [-0.00231, +0.00076] |
| margin log score | -4.2097 | -4.2103 | -0.00063 | [-0.00590, +0.00461] |
| total log score | -4.20731 | -4.20685 | +0.00046 | [-0.00084, +0.00185] |

All three checks pass but every interval spans zero: the append is neutral within noise, not an improvement. Accuracy 70.1% to 70.5%. 1,527 of 1,527 holdout probabilities changed; the largest single change is 46.7 percentage points (an FCS-heavy matchup), so individual picks can move a lot even though the aggregate does not.
Arm (i) Brier is 0.19036 (was 0.18933 before the 1,478-row FBS backfill: the waived +0.0010 cost, as measured).

## Applied
scripts/cfb_backfill_fcs_history.py ran once: 6,894 events (sha256 6bbf70ac...) appended to data/processed/ncaaf/games.jsonl only. processed sha256 7b50b030... -> c50d8a26...; data/historical unchanged (19ad99e2...); loaded history 9,498 -> 16,392 rows; backup data/research/ncaaf_v6/live_backup_fcs_20261115T212451Z; second run "already applied".

## Limitation
The daily ingest fetches FBS-involved games (groups 80) only. These FCS-vs-FCS rows are a one-time snapshot and are not maintained; FCS ratings go stale again. Maintaining them needs a decision to ingest groups=81 daily (not done).

## Refit
Not warranted by this evidence: the serving artifact's holdout metrics move by less than the CI half-width in every case. A refit on the 16,392-row history would be a new shadow challenger with its own validation, not a side effect here.
