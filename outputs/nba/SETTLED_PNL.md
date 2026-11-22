# NBA realised PnL on settled ledger picks

Ledger rows settled 2026-10-20 to 2026-11-21. Stored units and PnL, no replay.

| Slice | Picks | Games | PnL | ROI | 95% CI (game bootstrap) | Bootstrap P(ROI<=0) |
|---|---|---|---|---|---|---|
| Main tier, all settled | 171 | 123 | +7.83u | +3.4% | [-11.2%, +17.4%] | 32% |
|   Main moneyline | 33 | 33 | +4.00u | +11.5% | [-19.4%, +41.4%] | 23% |
|   Main spread | 72 | 72 | +5.10u | +4.9% | [-18.2%, +28.1%] | 34% |
|   Main total | 66 | 66 | -1.27u | -1.4% | [-26.4%, +22.5%] | 54% |
| Main tier, serving models only | 59 | 50 | +5.74u | +7.6% | [-17.7%, +34.3%] | 27% |
| Flat tier (every game called), all settled | 540 | 214 | -31.21u | -4.4% | [-13.0%, +4.2%] | 84% |
|   Flat moneyline | 178 | 178 | +2.77u | +1.5% | [-10.5%, +14.1%] | 41% |
|   Flat spread | 180 | 180 | -10.28u | -3.8% | [-18.4%, +11.0%] | 70% |
|   Flat total | 182 | 182 | -23.70u | -9.5% | [-24.1%, +5.6%] | 89% |

Main by week (start date):

| Week | Picks | PnL |
|---|---|---|
| 2026-10-19/2026-10-25 | 74 | +8.14u |
| 2026-10-26/2026-11-01 | 25 | -5.61u |
| 2026-11-02/2026-11-08 | 15 | +0.21u |
| 2026-11-09/2026-11-15 | 27 | +2.45u |
| 2026-11-16/2026-11-22 | 30 | +2.64u |

Reading it: the Main tier is the gated ledger and is positive; the Flat tier calls everything and is not.
The Main interval includes 0, so this is realised PnL, not proof of edge. Picks from retired model
versions are included in the all-settled rows; the serving-models row isolates the current ones.
