# NCAAB loss audit — 2026-11-10

## Scope and source

Read-only query of `E:/MP-runtime/ledgers/ledgers.db`, `ledger_records`, Main tier,
settled `CALL` rows with event dates October 28 through November 10, 2026.
Flat and research mirrors were excluded. Results are paper units, not fills or
realized profit. The ledger has no duplicate groups within the counted model,
event, market, side, and line keys.

## Findings

| Model | Calls | Wins | Paper P&L | Decision evidence |
| --- | ---: | ---: | ---: | --- |
| NCAAB tempo v2 | 297 | 124 | -44.78 units | No archived quote timestamps |
| NCAAB tempo v3 | 482 | 179 | -74.41 units | No archived quote timestamps |
| NCAAB spread v6 | 40 | 20 | +0.95 units | 31 of 40 quote joins marked `not_archived` |
| NCAAB total v6 | 36 | 11 | -25.78 units | 27 of 36 quote joins marked `not_archived` |

V3 away moneyline was 13 wins in 151 calls and lost 68.41 units. Its mean
recorded probability was 35.5%, versus an 8.6% observed hit rate. V2 away
spread was 38 wins in 92 calls and lost 36.15 units despite an 88.0% mean
recorded cover probability. These are severe calibration failures; they do not
identify a unique faulty feature.

V6 totals missed on both sides: 6 wins in 22 Overs and 5 wins in 14 Unders.
Their mean recorded selected-side probability was 61.2%, versus a 30.6% hit
rate. All 36 had a minimum current-season team sample below five; the mean was
1.72 games. On these same games, mean absolute projected-total error was 16.10
points, while the recorded market line missed by 13.61 points. This is a
same-cohort descriptive comparison, not a point-in-time replay of a new model.

The earlier retrospective v6 gain in `docs/ROADMAP.md` does not establish a
prospective gain. The older calls also lack executable quote provenance, so
their paper P&L cannot be described as executable return.

## Corrections and status

- Commit `93aa093c` paused NCAAB totals in Main. It did not establish that
  total probabilities improved.
- Commit `96d85261` requires a matched, fresh executable two-sided quote for
  new calls. Later open calls carry quote hashes and archive paths. There are
  no settled post-gate NCAAB calls yet.
- The current correction applies the existing `models.NCAAB.min_team_games`
  setting (eight) to `current_season_sample` before Main admission of spread
  or moneyline calls. Lifetime game counts still screen team identity. Thin
  or missing current-season samples remain visible in Flat and receive an
  explicit Main skip reason. The v6 formula and artifacts are unchanged.

## Falsifiable next check

After enough fresh, quoted games settle, compare the exact serving probability
against the outcome and the decision-time market probability on the same
contracts, by market and side. Report Brier score, log loss, calibration, and
line error before considering a new model version or restoring Main totals.
Do not fit a calibration curve or select a threshold on the evaluated dates.

Failure mode: the sample gate could reject useful early-season calls and does
not repair probability calibration. Rollback is the isolated Main admission
condition in `src/model_prediction/college_basketball_forward.py`; retain
the settled and Flat rows for comparison. No settled rows were changed.
