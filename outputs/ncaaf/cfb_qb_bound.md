# NCAAF QB upper-bound test (quick-261115-183, W4)

**LEAKY UPPER BOUND. Starters are derived post hoc from box-score pass attempts. The closing line knows the announced starter and there are no historical timestamped injury or depth-chart reports locally, so nothing here can be shown to be observable before kickoff. The best possible verdict is UPPER_BOUND_PASS, and nothing here enters cfb-structural-v6.1.**

- rule_sha256: `1481273f1fbd860a5e35ecc25a864818e74818f564aa0f6d5710ea42f524145f`
- script_sha256: `a1a911446f6f895917afe22b1f2508e3da661dd22861c5c2462e92d8b7945c94`
- generated_at_utc: 2026-11-15T07:13:25Z; final run: True
- holdout_reads: 1

## Coverage by season

| season | events expected | fetched | with box score | sides resolved / expected | total-lined scored | backup_start_sum>0 | qb_changed_sum>0 | backup_start_diff!=0 | qb_changed_diff!=0 |
|---|---|---|---|---|---|---|---|---|---|
| 2022 | 896 | 896 | 896 | 1792 / 1792 | 776 | 266 | 200 | 227 | 181 |
| 2023 | 910 | 910 | 910 | 1820 / 1820 | 792 | 296 | 221 | 268 | 205 |
| 2024 | 919 | 919 | 919 | 1838 / 1838 | 772 | 251 | 183 | 222 | 172 |
| 2025 | 934 | 934 | 933 | 1866 / 1868 | 805 | 259 | 188 | 230 | 178 |
| 2026 | 731 | 731 | 731 | 1462 / 1462 | 570 | 177 | 129 | 152 | 122 |

Flag counts cover FBS-vs-FBS lined games with both sides resolved (select, validation and holdout windows; counts only, no outcome). Seasons start at 2022, so the select window of the pinned OLS is only the 2022 games up to 2022-11-11.

## Primary statistic: dMSE = MSE(line-only OLS) - MSE(line + QB flags OLS), validation (positive = helps)

| market | n select | n validation | n holdout | validation dMSE | 95% CI | holdout dMSE (point only) | verdict |
|---|---|---|---|---|---|---|---|
| total | 542 | 1566 | 1607 | 0.703 | [-0.449, 1.900] | -0.789 | NOT_WORTH_BUILDING |
| spread | 542 | 1565 | 1608 | 1.986 | [-0.229, 4.082] | 2.569 | NOT_WORTH_BUILDING |

Verdict reasons:

- total: NOT_WORTH_BUILDING (fails the pass rule (validation CI lower bound > 0 AND holdout point estimate > 0))
- spread: NOT_WORTH_BUILDING (fails the pass rule (validation CI lower bound > 0 AND holdout point estimate > 0))

Flagged rows per market (select / validation):

- total: {"backup_start_sum": {"select_flagged": 155, "validation_flagged": 571}, "qb_changed_sum": {"select_flagged": 134, "validation_flagged": 415}}
- spread: {"backup_start_diff": {"select_flagged": 134, "validation_flagged": 507}, "qb_changed_diff": {"select_flagged": 120, "validation_flagged": 386}}

## Exploratory (not a verdict input)

```json
{
 "label": "exploratory, validation window only, not a verdict input",
 "backup_start_sum": {
  "0": {
   "n": 995,
   "mean_actual_minus_close": 0.9711055276381909
  },
  "1": {
   "n": 507,
   "mean_actual_minus_close": 1.9985207100591715
  },
  "2": {
   "n": 64,
   "mean_actual_minus_close": -0.6875
  }
 },
 "qb_changed_sum": {
  "0": {
   "n": 1151,
   "mean_actual_minus_close": 1.5071676802780192
  },
  "1": {
   "n": 386,
   "mean_actual_minus_close": 0.5148963730569949
  },
  "2": {
   "n": 29,
   "mean_actual_minus_close": 0.06896551724137931
  }
 }
}
```

## Reading this

A pass here would mean only that knowing the actual starter after the game is worth something the closing line lacked. The line already knows the announced starter, so a pass is an upper bound, and the follow-up is the forward capture of pregame injuries and depth plus a point-in-time retest. A failure is informative: even with hindsight the QB flag does not help.
