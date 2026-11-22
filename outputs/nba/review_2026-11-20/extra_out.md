C:\Users\Vince\AppData\Local\Temp\claude\3c4e37e4a6ec2934\725cd53a-6904-44cd-9660-1dcec21da8a9\scratchpad\nba_review\extra.py:71: FutureWarning: The default of observed=False is deprecated and will be changed to True in a future version of pandas. Pass observed=False to retain current behavior or observed=True to adopt the future default and silence this warning.
  for k, g in v9.groupby(pd.cut(v9.gap, [-50, -6, -3, 0, 3, 50])):
## Pooled headline (quoted)
- ML: all 92-46 (66.7%), pnl -3.61u, ROI -2.5%, n=138; QUAL 3-5 (37.5%), pnl -2.77u, ROI -34.7%, n=8; mean model 67.1% vs ask 67.2%
- spread: all 81-89 (47.6%), pnl -11.50u, ROI -4.6%, n=170; QUAL 73-72 (50.3%), pnl +4.59u, ROI +2.1%, n=145; mean model 59.9% vs ask 50.8%
- total: all 79-93 (45.9%), pnl -18.75u, ROI -8.0%, n=172; QUAL 59-70 (45.7%), pnl -12.38u, ROI -7.0%, n=129; mean model 57.8% vs ask 51.2%
- ALL: 252-228 (52.5%), pnl -33.86u, ROI -5.4%, n=480
- since v9 start (event >= 11-08): 144-115 (55.6%), pnl -0.33u, ROI -0.1%, n=259

## Availability sign (v2-v5 spreads)
- against pick: 1-0 (100.0%), pnl +2.00u, ROI +100.0%, n=1
- toward pick: 2-2 (50.0%), pnl -0.23u, ROI -2.9%, n=4
- |gap|<=0.5 (none): 20-35 (36.4%), pnl -18.78u, ROI -23.6%, n=55
avail_gap distribution: {'count': 60.0, 'mean': 0.16, 'std': 1.66, 'min': -7.55, '25%': 0.0, '50%': 0.0, '75%': 0.0, 'max': 7.3}
avail_total_loss distribution: {'count': 63.0, 'mean': 1.15, 'std': 3.86, 'min': 0.0, '25%': 0.0, '50%': 0.0, '75%': 0.0, 'max': 23.95}

## Back-to-back (derived from all ledger NBA game start times, any status)
schedule games: 225, first 10-20, last 11-21
- ML both B2B: 7-3 (70.0%), pnl +2.83u, ROI +25.1%, n=10
- ML neither: 58-35 (62.4%), pnl -12.46u, ROI -12.8%, n=93
- ML opponent on B2B: 12-3 (80.0%), pnl +0.82u, ROI +5.5%, n=15
- ML our team on B2B: 15-5 (75.0%), pnl +5.20u, ROI +26.0%, n=20
- spread both B2B: 6-6 (50.0%), pnl -2.34u, ROI -13.1%, n=12
- spread neither: 53-65 (44.9%), pnl -16.08u, ROI -9.3%, n=118
- spread opponent on B2B: 8-6 (57.1%), pnl +3.16u, ROI +15.9%, n=14
- spread our team on B2B: 14-12 (53.8%), pnl +3.76u, ROI +9.4%, n=26
- total over any-team-B2B=False: 8-23 (25.8%), pnl -17.76u, ROI -43.9%, n=31
- total over any-team-B2B=True: 10-5 (66.7%), pnl +7.20u, ROI +38.1%, n=15
- total under any-team-B2B=False: 40-47 (46.0%), pnl -9.99u, ROI -8.4%, n=87
- total under any-team-B2B=True: 21-18 (53.8%), pnl +1.80u, ROI +3.1%, n=39

## v9 totals rows with the 'Calibrated X -> Y' layer (11-10..11-12)
n=14; regimes {'FLAT': 10, 'QUAL': 4}; 4-10 (28.6%), pnl -6.49u, ROI -45.6%, n=14
of which calibrated model prob < 50% on the chosen side: 11
all statuses with calibrator text: 61; models {'nba-total-margin-v9': 61}; dates 11-11..11-13

## Model-vs-market gap direction for totals (v9): picks vs model below/above market
- model-market (-50, -6]: 2-2 (50.0%), pnl -0.34u, ROI -5.4%, n=4; overs 0
- model-market (-6, -3]: 8-10 (44.4%), pnl -4.49u, ROI -18.5%, n=18; overs 0
- model-market (-3, 0]: 13-20 (39.4%), pnl -8.36u, ROI -21.6%, n=33; overs 0
- model-market (0, 3]: 6-6 (50.0%), pnl +0.62u, ROI +4.1%, n=12; overs 11
- model-market (3, 50]: 8-7 (53.3%), pnl +1.80u, ROI +8.5%, n=15; overs 15

## Totals: actual minus market fair line, all v9 settled games
mean +3.67, n=82

## Market-quoted ROI if we had simply bet every v9 total/spread at the decision ask in the model's direction, by month
- ML week0 (from 10-21): 23-15 (60.5%), pnl -4.21u, ROI -9.6%, n=38
- ML week1 (from 10-27): 15-8 (65.2%), pnl -1.20u, ROI -5.2%, n=23
- ML week2 (from 11-03): 4-0 (100.0%), pnl +0.93u, ROI +23.1%, n=4
- ML week3 (from 11-10): 36-10 (78.3%), pnl +6.59u, ROI +14.3%, n=46
- ML week4 (from 11-17): 14-13 (51.9%), pnl -5.71u, ROI -21.2%, n=27
- spread week0 (from 10-21): 19-21 (47.5%), pnl +0.32u, ROI +0.5%, n=40
- spread week1 (from 10-27): 4-16 (20.0%), pnl -17.33u, ROI -62.1%, n=20
- spread week2 (from 11-03): 18-14 (56.2%), pnl +3.07u, ROI +6.4%, n=32
- spread week3 (from 11-10): 24-26 (48.0%), pnl -3.87u, ROI -5.3%, n=50
- spread week4 (from 11-17): 16-12 (57.1%), pnl +6.31u, ROI +15.7%, n=28
- total week0 (from 10-21): 19-22 (46.3%), pnl -1.83u, ROI -3.0%, n=41
- total week1 (from 10-27): 10-12 (45.5%), pnl -4.16u, ROI -12.7%, n=22
- total week2 (from 11-03): 15-16 (48.4%), pnl -3.09u, ROI -7.4%, n=31
- total week3 (from 11-10): 22-27 (44.9%), pnl -4.46u, ROI -7.4%, n=49
- total week4 (from 11-17): 13-16 (44.8%), pnl -5.20u, ROI -13.1%, n=29
