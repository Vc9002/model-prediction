# All-model rebuild: implementation and evidence

There are 21 fitted research candidates covering every current model, with
archived inputs, inference, incumbent state capture/replay and outcome evaluation.
The current generation remains
`outputs/research/generation_20260912_clean_update/`: 17 generic candidates,
three joint CFB candidates and the corrected NRFI candidate. The later recency
experiments remain separate. **No candidate has established reliable improvement
in both accuracy and profitability. None is qualified as a production replacement.**

## Completed economic adapters

The new `capture_research_prices.py` captures a later pricing decision against an
already frozen forecast. Prediction time, request time, book observation and
pricing decision remain separate. It archives public event, market and book
responses, verifies the generation and rebuilds features from source before
pricing. It never attaches a later quote to an earlier prediction timestamp.

`score_research_prices.py` verifies the entire archive, rebuilds the forecast,
reconstructs its payout table and pregame choice, and then applies an independently
captured completed result. Unpriced, missing and conflicting evidence remains
visible. Costs, fees, whole-contract size and choices cannot change at settlement.
Results are hypothetical taker simulations, not fills or portfolio returns.

| Contract family | Supported settlement | Evidence limits |
|---|---|---|
| Existing binary moneylines | Existing exact paired-quote adapter | Requires a bound archived production decision and valid quote |
| MLB spread/total | Existing full-game half-point archive adapter | Integer/push terms remain unverified |
| MLB NRFI | Yes = either team scores in inning one; No = zero runs | Requires exact start, both teams, actual rule, fresh book and completed first-inning scores |
| CFB moneyline | Full game including overtime, binary official winner | Unresolved tied official results unavailable |
| CFB and WNBA spread/total | Consistent full-game half-point contracts including overtime | Positive long-side spread descriptions currently conflict or remain unverified; integers unavailable |
| KBO/NPB moneyline | Win $1, loss $0, tie $0.50 | A tie is not an entry-price refund; void pricing unavailable |
| Soccer moneyline | Explicit home-win, away-win or draw Yes/No contract over 90 minutes plus stoppage | Target fixed in fixture before quotes; default home-win; completed result must explicitly show two periods |

The soccer adapter retains all three probabilities. No on a home-win contract
wins on either an away win or a draw; Yes on the draw contract wins only on a draw.
It does not optimize retrospectively among three different captured contracts.
One event/market/line is admitted per batch. Cross-batch repeated decisions must
not be pooled as independent outcomes.

The common policy uses a $5 budget per event-market, whole contracts bounded by
displayed depth, a verified 0.06 fee coefficient and the existing per-fill fee
rounding. It selects a positive expected return per dollar or abstains. The
five-minute limit applies to the request window and book transaction timestamp.
Canceled/postponed/void settlement is not assumed to return principal.

Policy v1 is retained for the first NRFI batch, including its inaccurate inherited
full-game/retrospective metadata. Its actual payout tables and timestamps were
correct. v2 corrected that metadata and added CFB/KBO/NPB support; v3 adds WNBA
and soccer. Old policy identities and original choices still reproduce exactly.

## New prospective results

| Cohort | Forecast outcomes | Price evidence | Simulated result |
|---|---|---|---|
| Clean NRFI, September 12 | 6/14 correct; vector Brier 0.500933; log loss 0.694080 | No timely bound entry quotes | Unavailable |
| Clean NRFI, September 13 | 7/15 correct; vector Brier 0.507164; log loss 0.700319 | 6 valid opportunities; 2 calls | **-$9.75** on $9.75 cost, including $0.31 fees; both calls lost |
| Soccer, September 13 | 1/1 correct; vector Brier 0.417143; log loss 0.749270 | No pregame price captured | Unavailable; second fixture had no forecast |
| CFB, September 17 | Three forecasts for one future game | Moneyline quote valid, one simulated call; spread/total books stale | Awaiting outcome; no P&L |

Across the two clean NRFI days, 13 of 29 forecasts were correct. Those 29 outcome
forecasts are distinct from the six priced opportunities and two calls. The
small cohort and losing trades provide no support for promotion. The CFB game
has not happened as of September 14; its $4.77 simulated cost is not settled P&L.

The nine unpriced NRFI cases on September 13 were six stale books, two rate-limited
requests and one conflicting event/market start. Kansas City–Boston's event
showed 19:05 UTC while its NRFI contract still showed 17:35 UTC. The original
batch is retained without price retries or silent repairs. Discovery is now
cached once per league per batch to reduce redundant requests.

WNBA's current public event list was empty on September 13. A named archived
August 30 event supplied actual contract terms for tests. Those tests use
synthetic open books and are not prospective WNBA economics. KBO/NPB and soccer
tests likewise distinguish real provider metadata from synthetic prices.

Earlier results are preserved: on the September 11 paired MLB cohort, the
candidate lost $13.925 and the incumbent lost $26.130 across 24 quote
opportunities. Both lost overall; the smaller candidate loss is not profitability.
See `NRFI_AND_SETTLED_REBUILD_2026-09-12.md` for the full cohort and limitations.

## Every current model

These are chronological historical test metrics, not prospective trading
performance or claims of incumbent superiority. Historical feature publication
availability remains unverified. MAE measures score margin or total error in
the sport's units; it is not spread/total betting accuracy. Soccer, KBO and NPB
accuracy uses three outcomes. Separate calibration and selection precede the
test partitions; previously examined tests are development evidence.

| Candidate market | Historical test games | Metric |
|---|---:|---:|
| WNBA moneyline | 275 | 63.64% accuracy |
| WNBA spread | 275 | 10.513 MAE |
| WNBA total | 275 | 16.778 MAE |
| MLB moneyline | 1,492 | 54.22% accuracy |
| MLB spread | 1,492 | 3.534 MAE |
| MLB total | 1,492 | 3.556 MAE |
| MLB NRFI | 1,374 | 51.67% accuracy |
| NBA moneyline | 587 | 68.48% accuracy |
| NFL moneyline | 165 | 66.06% accuracy |
| CFB moneyline | 878 | 69.70% accuracy |
| CFB spread | 878 | 13.532 MAE |
| CFB total | 878 | 12.833 MAE |
| Soccer moneyline | 1,483 | 50.78% accuracy |
| Tennis moneyline | 5,527 | 63.38% accuracy |
| CS2 moneyline | 6,688 | 64.14% accuracy |
| Dota 2 moneyline | 1,698 | 65.37% accuracy |
| LoL moneyline | 2,399 | 67.40% accuracy |
| Valorant moneyline | 2,293 | 61.67% accuracy |
| Rainbow Six moneyline | 495 | 68.28% accuracy |
| KBO moneyline | 1,665 | 52.91% accuracy |
| NPB moneyline | 809 | 53.28% accuracy |

Exact model IDs, artifact hashes, proper-score metrics and adapter mapping are
in `outputs/research/pricing_verification_20260914/model_status.json`.
No new fitting was done against the September 13 outcomes. Missing prospective
cohorts and unavailable ROI/CLV for other models remain null, not zero or green.

## Verification and reproducible commands

All 21 current artifacts pass fresh identity and probability-inference checks,
including integer and half-point distribution checks. Integer inference does
not authorize settlement without contract rules. Scoped type checking passes
for six modules; Ruff passed for all 91 changed/new Python files at the saved
snapshot. That full suite passed **2,964 tests with 3 skipped**; the affected
pricing suite passed 139 tests after the final NO_FORECAST exit-code fix.
Further local edits followed. See
[the evening consolidation record](CONTROL_PLANE_CONSOLIDATION_2026-09-14.md)
for the current-tree verification and unresolved control-plane gaps.
Exact logs, code/fixture snapshots and validation hashes are retained in
`outputs/research/pricing_verification_20260914/`.

Run from the repository root with `PYTHONPATH=src:.` and `.venv/bin/python`:

```sh
scripts/capture_research_prices.py --forecasts EXISTING_FORECAST_DIRECTORY --output NEW_PRICE_DIRECTORY
scripts/score_research_prices.py --prices EXISTING_PRICE_DIRECTORY --outcomes COMPLETED_RESULTS_JSONL --output NEW_SCORE_DIRECTORY
```

An existing output directory is never reused. Partial pricing returns exit code
2; completed score batches with integrity failures also return 2. Price capture
requires public network access. The source/model/quote archives and code snapshots
are retained under each output directory.

Key evidence:

- `clean_nrfi_forward_20260913_v2/` and `clean_nrfi_prices_20260913_v1/`.
- `clean_nrfi_outcomes_20260914_v2/` and `clean_nrfi_priced_outcomes_20260914_v1/`.
- `soccer_forward_20260913_v1/` and `soccer_outcomes_20260914_v1/`.
- `cfb_forward_20260913_v2/`, `cfb_prices_20260913_v2/` and `cfb_price_replay_20260914_v2/`.
- `espn_outcomes_20260914_for_20260913/` preserves raw and normalized results.

All paths above are under `outputs/research/`. Production champions, protected
model JSON artifacts and the canonical trading ledger were not changed by this
pricing work; no orders were submitted. The separate existing YAML and runtime
state changes remain outside this work.

The implementation supports research across every current model. Demonstrating
better accuracy and profitability remains unfinished empirical work: collect
distinct future events with valid prices, preserve every abstention/failure,
and evaluate the fixed policy after settlement. Do not promote on the present
results, manufacture missing prices, or treat additional retraining as proof.
