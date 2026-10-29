# NFL five-week loss review and prospective replay capture — 2026-10-29

## Cohort and arithmetic

Read-only audit of canonical settled NFL Flat calls in
`E:/model-prediction-runtime/ledgers/ledgers.db`, covering the five event weeks
beginning September 28 through October 26, 2026. There are 56 unique games,
131 selected contracts, 166.59 staked units, and **-11.2472 units (-6.75% ROI)**.
The final week currently has only one settled game. Flat is a paper comparison
ledger; these are recorded model-call units, not exchange fills or net cash.

| Market | Calls | Wins | Staked | P&L | ROI | Brier on selected sides |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Moneyline | 56 | 31 | 63.50u | -1.8791u | -2.96% | 0.2386 |
| Spread | 41 | 20 | 56.12u | -7.4448u | -13.27% | 0.2891 |
| Total | 34 | 16 | 46.97u | -1.9233u | -4.09% | 0.2938 |

The spread is the largest observed loss. For spread calls with model probability
0.55–0.65, 7 of 16 won; for 0.65–0.75, 4 of 9 won; both calls above 0.75
lost. For total calls with probability 0.65–0.75, 4 of 10 won, and the sole
call above 0.75 lost. These selected small buckets suggest overconfidence but
do not establish calibration of the unselected slate or a stable threshold.
Changing confidence gates based on these outcomes would be retrospective
selection.

The saved final-inference means give a complementary diagnostic: across 41
spread calls, actual home margin minus projected home margin averaged **-4.24
points**; error standard deviation was 12.31 versus mean configured spread SD
14.07. Across 34 total calls, mean error was -0.11 points; error SD was 13.13
versus configured total SD 13.83. The spread's selected-cohort mean bias is a
stronger lead than simply inflating its variance. These are not full-slate
estimates, and old upstream feature vintages are not fully certified.

## Why the joint challenger covers only 15 games

Fifteen of 56 games have no saved Flat spread and total contract; seven lack
total. Of the 34 complete triads, 18 carry an older spread/total engine source
SHA-256 that does not match the current or recorded recoverable Git versions.
The v2 evaluator further excludes missing decision-time moneyline quotes and
dates without previously settled training history. Its 15-game probability
comparison and the incumbent's full five-week P&L therefore answer different
questions. The joint challenger has no prespecified bet selection or sizing
policy; the original calls' P&L cannot be attributed to it.

## Testable improvement priorities

1. **Capture all scheduled games:** the new append-only NFL joint packet path
   records moneyline, spread, and total model snapshots and exact two-sided
   archived quotes when available, plus explicit gaps for no-calls and missing
   contracts. It does not rewrite old calls. Keep every source observation time,
   engine/artifact hash, line orientation, and quote archive binding.
2. **Variance and probability calibration:** on subsequent complete dates,
   compare forecast margin and total residual variance with realized variance,
   key-number mass, Brier/log loss, and reliability by probability range. Fit
   any correction on prior dates only and compare the corrected joint model
   with the current heads and decision-time market on paired events.
3. **QB/availability and market residual ablations:** use timed official
   starter/inactive releases and hash-valid efficiency captures. Test their
   paired incremental value over the market and over the current v6 inputs;
   retain an explicit missingness branch. A modern release cannot retroactively
   certify September or early October feature availability.
4. **Economic test:** freeze a challenger selection, stake, and executable-ask
   rule before scoring prospective games. Report skipped contracts, CLV,
   realized paper units, and uncertainty separately from probability scores.

No champion, historical data, existing model artifact, or order path changed.
