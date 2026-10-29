# NFL spread bias correction experiment — 2026-10-29

## Hypothesis and test

The five-week selected Flat cohort showed a -4.24 point average error in home
margin. This experiment tests whether adding a home-margin intercept fitted on
earlier settled complete game dates improves later spread probabilities on the
same exact saved lines. It uses the saved parent artifact and source-engine
hashes and requires exact incumbent probability replay, event/line orientation,
decision timing, and independent result agreement. Training outcomes must
settle before the later decision. No threshold, stake, champion, or ledger is
changed.

Run:

```powershell
$env:PYTHONPATH='src;.'
.venv\Scripts\python scripts/evaluate_nfl_spread_bias_v1.py E:\model-prediction-runtime\ledgers\ledgers.db
```

## Result

Only 16 of 41 settled spread calls have a replayable current engine hash;
25 older calls fail source-engine hash verification. The first replayable date
has no same-artifact settled training history. Thus 15 calls across four later
game dates are scored.

| Exact paired cohort | Incumbent Brier | Bias-corrected Brier | Gain (positive is better) |
| --- | ---: | ---: | ---: |
| 15 date-forward calls | 0.30037 | 0.35590 | **-0.05552** |
| 14 with saved quote archive present | 0.29583 | 0.34125 | **-0.04542** |

The raw selected-side ask-implied Brier on the 14 quote-backed calls is
0.25288. This is a price reference with margin, not a two-sided no-vig market
probability or executable expected-value result. Date-cluster bootstrap 95%
interval for incumbent-minus-corrected Brier is **[-0.12232, +0.04646]**.
There are too few dates for a stable performance claim. The correction moves
in the wrong direction on the observed paired sample.

## Decision and next test

**Reject this simple bias-intercept challenger.** Do not adjust the serving
spread mean or confidence gate from the five-week selected-call average. The
older artifact's mean residuals are not interchangeable with a current-engine
walk-forward training set. On future unselected full-slate packets, evaluate
margin residuals by game date, team side, QB state, and quote age, then test a
price-relative residual model against exact two-sided no-vig spread prices and
the existing head. Freeze that candidate before scoring prospective games.

The rollback is the unchanged v5/v6 spread artifacts and serving calculation.
No retrospective P&L is attributed to this diagnostic probability adjustment.
