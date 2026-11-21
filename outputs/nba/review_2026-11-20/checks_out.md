## v9 only
- ML: 10-1 (90.9%), pnl +3.12u, ROI +28.4%, model 62.8%, ask 73.8%, n=11; Brier(model)-Brier(mid) +0.0682 [+0.0015,+0.1256] n=11
    QUAL: n=0
- spread: 43-42 (50.6%), pnl +0.42u, ROI +0.3%, model 59.1%, ask 50.6%, n=85; Brier(model)-Brier(mid) +0.0099 [-0.0134,+0.0348] n=85
    QUAL: 39-39 (50.0%), pnl -0.64u, ROI -0.6%, model 59.1%, ask 50.7%, n=78
- total: 39-46 (45.9%), pnl -9.10u, ROI -8.3%, model 54.4%, ask 50.7%, n=85; Brier(model)-Brier(mid) -0.0023 [-0.0195,+0.0157] n=85
    QUAL: 30-37 (44.8%), pnl -9.25u, ROI -10.5%, model 55.7%, ask 50.4%, n=67
- v9 all markets: 92-89 (50.8%), pnl -5.55u, ROI -2.3%, model 57.1%, ask 52.1%, n=181
- all models, events since 11-08: 144-115 (55.6%), pnl -0.33u, ROI -0.1%, model 60.4%, ask 56.4%, n=259
v9 sides:
- spread home: 23-22 (51.1%), pnl -0.87u, ROI -1.4%, model 59.1%, ask 51.0%, n=45
- spread away: 20-20 (50.0%), pnl +1.30u, ROI +2.1%, model 59.1%, ask 50.1%, n=40
- spread fav: 17-21 (44.7%), pnl -7.61u, ROI -13.5%, model 58.5%, ask 50.5%, n=38
- spread dog: 26-21 (55.3%), pnl +8.03u, ROI +11.7%, model 59.6%, ask 50.7%, n=47
- spread |L|<=3.5: 14-9 (60.9%), pnl +9.99u, ROI +30.6%, model 58.3%, ask 50.6%, n=23
- spread |L| 4-9.5: 20-23 (46.5%), pnl -7.45u, ROI -11.6%, model 59.8%, ask 50.7%, n=43
- spread |L| 10+: 9-10 (47.4%), pnl -2.11u, ROI -7.6%, model 58.5%, ask 50.4%, n=19
- total over: 14-13 (51.9%), pnl +2.42u, ROI +6.7%, model 55.1%, ask 50.0%, n=27
- total under: 25-33 (43.1%), pnl -11.52u, ROI -15.8%, model 54.0%, ask 51.1%, n=58
- total line 235+: 5-8 (38.5%), pnl -4.31u, ROI -25.0%, model 57.3%, ask 51.1%, n=13
- total line <225: 15-21 (41.7%), pnl -7.64u, ROI -16.7%, model 53.7%, ask 50.3%, n=36

## Spread loss attribution (union)
- all quoted spreads: 81-89 (47.6%), pnl -11.50u, ROI -4.6%, model 59.9%, ask 50.8%, n=170
- FLAT or v5 (union): 15-25 (37.5%), pnl -15.01u, ROI -24.1%, model 61.4%, ask 51.6%, n=40
- complement: 66-64 (50.8%), pnl +3.51u, ROI +1.9%, model 59.5%, ask 50.6%, n=130
- FLAT and v5 overlap n=14
- all settled spread/total rows quoted? unquoted spread/total in deduped settled: 0

## fair_line validation (ask 47-53c)
n=307; |fair_dec - pick line|: median 0.17, p95 0.63, max 1.50; >1.5 pts: 0
Empty DataFrame
Columns: [mshort, selection, line, fair_dec, p_mkt, slug]
Index: []
|fair_close - fair_dec| max 5.28, >3 pts: 3
v9 totals: actual - pick line mean +2.96 (n=85), actual - fair_dec +3.67, actual - exp_total +3.91, SE 1.85

## residual corr CIs (one row per event x model, settled)
- spread: r=-0.075 [-0.227,+0.081], slope -0.27 (SE 0.29), n=160; v9 only r=+0.084 n=83
- total: r=-0.061 [-0.214,+0.094], slope -0.27 (SE 0.35), n=162; v9 only r=-0.031 n=82

## void/archive check
['sequence', 'event_id', 'pick_id', 'event_type', 'event_time_utc', 'payload_json', 'previous_hash', 'event_hash']
[('append', 3444), ('remove', 8), ('settle', 673), ('supersede', 2736), ('update', 4)]
runtime archive files mentioning nba: []
repo data/archive files mentioning nba: ['e:/MP/data/archive\\2026-08-31-retired-wnba-spread-v1', 'e:/MP/data/archive\\2026-08-10-main-ledger-archived-shadow-primary\\wnba.xlsx', 'e:/MP/data/archive\\2026-08-10-main-ledger-archived-shadow-primary\\wnba.xlsx.bak-v3', 'e:/MP/data/archive\\2026-08-10-main-ledger-archived-shadow-primary\\wnba.xlsx.lock', 'e:/MP/data/archive\\2026-08-10-main-ledger-archived-shadow-primary\\model_ledgers\\wnba-moneyline-elo-trend-lr.xlsx', 'e:/MP/data/archive\\2026-08-10-main-ledger-archived-shadow-primary\\model_ledgers\\wnba-moneyline-elo-trend-lr.xlsx.lock', 'e:/MP/data/archive\\2026-08-31-retired-wnba-spread-v1\\events.jsonl.lock', 'e:/MP/data/archive\\2026-08-31-retired-wnba-spread-v1\\flat_wnba_spread_v1_archive.xlsx', 'e:/MP/data/archive\\2026-08-31-retired-wnba-spread-v1\\flat_wnba_spread_v1_archive.xlsx.lock', 'e:/MP/data/archive\\2026-08-31-retired-wnba-spread-v1\\main_wnba_spread_v1_archive.xlsx']

## schedule coverage for B2B derivation
dates with 0 ledger games: ['2026-10-30', '2026-11-03']
games per date: {'2026-10-20': 3, '2026-10-21': 11, '2026-10-22': 2, '2026-10-23': 12, '2026-10-24': 8, '2026-10-25': 7, '2026-10-26': 9, '2026-10-27': 4, '2026-10-28': 12, '2026-10-29': 3, '2026-10-31': 7, '2026-11-01': 4, '2026-11-02': 15, '2026-11-04': 14, '2026-11-05': 2, '2026-11-06': 9, '2026-11-07': 7, '2026-11-08': 7, '2026-11-09': 8, '2026-11-10': 6, '2026-11-11': 10, '2026-11-12': 4, '2026-11-13': 9, '2026-11-14': 5, '2026-11-15': 9, '2026-11-16': 8, '2026-11-17': 5, '2026-11-18': 12, '2026-11-19': 4, '2026-11-20': 9}
