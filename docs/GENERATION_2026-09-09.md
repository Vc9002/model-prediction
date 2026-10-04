# All-model research generation — 2026-09-09

**Follow-up completed:** [exact-incumbent comparison and quote economics](INCUMBENT_COMPARISON_2026-09-09.md).
MLB replay passes but the candidate does not improve prediction reliably;
both common-policy simulations lose money. WNBA replay fails; 19 other markets
need decision evidence or replay support. The initial-run descriptions below
are historical and are superseded by that follow-up where indicated.

21 fitted research candidates now exist across all 14 registered sports. This is an initial fitted benchmark generation, not 21 proven improvements. None has passed exact incumbent comparison, prospective confirmation, or executable profitability evaluation.

Vincent requested improved prediction accuracy and profitability, and explicitly declared the previous frozen protocols obsolete. New research is authorized across every model, including MLB totals. Earlier artifacts and results remain comparison/rollback evidence.

## What was built

- Training runner: `scripts/train_research_generation.py`.
- Feature replay, fitted estimators, calibration, inference: `src/model_prediction/research_generation.py`.
- Verified artifacts and reports: `outputs/research/generation_20260909_v1_verified/`.
- One `model.joblib`, hash-bound `recipe.json`, `report.json`, and per-event evaluation predictions file per market. The top-level manifest pins source/code hashes and full split identities.
- Existing production registrations and model artifacts were hash-checked unchanged. No trading, daily-run, live-ledger, or promotion operation was invoked.

## Method and evidence boundaries

Each market independently selects among three regularized linear configurations and two small gradient-boosted tree configurations. Learned preprocessing is fit only on training data. Complete dates are split 60/10/10/20 into training, selection, calibration, and retrospective evaluation. Classification calibration fits one temperature on the calibration period. Continuous markets use a separate calibration residual distribution; probabilities require an explicit actual line and retain pushes. A signed home spread is converted to the opposite home-margin threshold.

The generic sports features use lagged ratings, short/recent form, scoring/defense, volatility, available-result age, and tennis surface information. All results are embargoed for two calendar days before entering those features. Tennis participants are ordered by stable identity, never by winner/loser. Soccer/KBO/NPB retain three outcome classes. MLB NRFI reuses its dedicated first-inning feature builder with training-period-only league priors. These are intentionally initial fitted benchmarks; most do not yet incorporate the full sport-specific player, roster, starter, lineup, venue, or patch information contemplated by the structural prototypes.

The raw scoreboards lack historical observation timestamps. Chronological construction therefore supports retrospective development, not a claim that every source field was historically observable. NRFI also needs a separate legacy feature-lineage audit. Previously examined data remains previously examined; the final historical split is not new prospective evidence.

Proper scoring rules reward honest probability forecasts; classification uses log loss and Brier alongside accuracy/calibration diagnostics. See [Gneiting and Raftery, Strictly Proper Scoring Rules, Prediction, and Estimation](https://stat.uw.edu/research/tech-reports/strictly-proper-scoring-rules-prediction-and-estimation).

## Fitted candidates

All 21 selected a regularized linear model on selection-period loss. The tree alternatives were evaluated but did not win selection. The table is descriptive. It must not be read as an incumbent leaderboard.

| Sport | Market | Current incumbent | Selected candidate | Evaluation n | Diagnostic |
|---|---|---|---|---:|---|
| WNBA | moneyline | `wnba-elo-trend-lr-v4` | `logistic_C0.1` | 275 | LL 0.6441; accuracy 63.6% |
| WNBA | spread | `wnba-spread-margin-v2` | `ridge_100.0` | 275 | MAE 10.513; bias -0.484 |
| WNBA | total | `wnba-total-margin-v2` | `ridge_1.0` | 275 | MAE 16.778; bias -6.208 |
| MLB | moneyline | `mlb-elo-trend-lr-v8` | `logistic_C0.1` | 1492 | LL 0.6888; accuracy 54.2% |
| MLB | spread | `measured-edge-margin-v3` | `ridge_1.0` | 1492 | MAE 3.534; bias +0.483 |
| MLB | total | `mlb-structural-v10-frozen` | `ridge_100.0` | 1492 | MAE 3.556; bias -0.642 |
| MLB | nrfi | `mlb-nrfi-v2` | `logistic_C0.1` | 1375 | LL 0.6923; accuracy 51.4% |
| NBA | moneyline | `nba-elo-trend-lr-v4` | `logistic_C0.1` | 587 | LL 0.6142; accuracy 68.5% |
| NFL | moneyline | `nfl-elo-trend-lr-v4` | `logistic_C0.1` | 165 | LL 0.6362; accuracy 66.1% |
| NCAAF | moneyline | `college-football-v1` | `logistic_C1.0` | 878 | LL 0.5690; accuracy 68.6% |
| NCAAF | spread | `cfb-spread-v1` | `ridge_1.0` | 878 | MAE 13.501; bias -0.785 |
| NCAAF | total | `cfb-total-v1` | `ridge_1.0` | 878 | MAE 12.847; bias -0.453 |
| SOCCER | moneyline | `soccer-poisson-dc-v1` | `logistic_C0.1` | 1483 | LL 1.0206; accuracy 50.8% |
| TENNIS | moneyline | `tennis-surface-elo-v1` | `logistic_C10.0` | 5527 | LL 0.6348; accuracy 63.4% |
| CS2 | moneyline | `cs2-tiered-elo-v6` | `logistic_C0.1` | 6688 | LL 0.6349; accuracy 64.1% |
| DOTA2 | moneyline | `dota2-tiered-elo-v6` | `logistic_C0.1` | 1698 | LL 0.6314; accuracy 65.4% |
| LOL | moneyline | `lol-tiered-elo-v6` | `logistic_C0.1` | 2399 | LL 0.6134; accuracy 67.4% |
| VALORANT | moneyline | `valorant-tiered-elo-v6` | `logistic_C10.0` | 2293 | LL 0.6485; accuracy 61.7% |
| RAINBOW_SIX | moneyline | `rainbow_six-tiered-elo-v6` | `logistic_C1.0` | 495 | LL 0.6038; accuracy 68.3% |
| KBO | moneyline | `kbo-tie-aware-elo-v2` | `logistic_C0.1` | 1665 | LL 0.7886; accuracy 52.9% |
| NPB | moneyline | `npb-tie-aware-elo-v2` | `logistic_C0.1` | 809 | LL 0.7711; accuracy 53.3% |

## What the first run says

- KBO does not beat even its constant training-prior reference on evaluation log loss. NPB and MLB spread/total have date-bootstrap improvement intervals that include zero against their respective simple references. These candidates do not establish a useful upgrade.
- WNBA totals has evaluation bias around -6.21 points and only about 67.3% coverage for its nominal 80% interval. That is a material predictive weakness despite beating a constant median on MAE.
- The remaining reference comparisons are useful sanity checks, not evidence of superiority to production. The exact incumbent reproduction gate remains UNVERIFIED for all 21.

## Profitability coverage audit

The canonical runtime `market_quotes.db` contains MLB moneyline, spread, and total rows only (112,603 / 446,487 / 644,713 quote rows at audit time). Its event IDs have zero direct overlap with this generation's evaluated scoreboard event IDs. This is an identity reconciliation problem to investigate, not proof that the underlying MLB games have no quotes. Other odds archives may contain additional data; this was not an exhaustive archive audit.

The warehouse schema has no contract-ID or fee column. Therefore the run does not pretend its records already provide complete executable economic inputs. `economic_data_coverage.json` records all 21 market counts and these limitations. ROI and CLV are explicitly null/UNVERIFIED. No -110 fallback, invented line, or requested-size-as-filled assumption is used.

Before profitability can be measured: reconcile event identity with unique game/time/team evidence (including doubleheaders), validate each actual contract and side/line, bind timely quotes and historical fees, model feasible fills/size, and evaluate a policy fixed before the evaluation period. Compare at the same risk budget and report uncertainty/drawdown plus no-bet coverage. Lower log loss alone does not establish profitability.

## Existing evaluation defects identified

- `scripts/evaluate_ready_challengers.py`: event-only joins drop decision horizon/contract/side distinctions; pushes map to outcome zero; missing/zero probabilities can become 0.5. Not changed by this isolated research implementation.
- `scripts/evaluate_cfb_v2.py`: reference is raw Elo, not the shipped CFB score-distribution model; absent lines become -3.5/52.5; the ML-only verdict is reused by the registry across markets. Its existing VALIDATED_OFFLINE label is not accepted as proof for this generation.
- `qualification_registry.py`: specific IDs receive CAPTURING_PROSPECTIVE without a live capture-health check; module importability can yield MECHANICS_VALIDATED. The live registry query returned zero linked historical/replay/prospective/synthetic evaluation rows for each market, although separate offline artifacts exist.
- MLB v10 operational audit returned NO_DATA, zero predictions; no confirmation-performance metrics were inspected.

## Verification and reproducibility

- 24 focused tests passed across the new module and existing first-inning/audit tests. Ruff and focused mypy passed. No full-suite claim.
- The first run trained 20 markets; NRFI stopped before fitting on a timezone-free prior-cutoff timestamp. The timestamp was corrected and NRFI trained separately. A clean second run completed all 21, preserving the first run.
- All 21 second-run predictive metrics exactly match their first successful runs. Each fitted artifact reproduces its own evaluation predictions after serialization. This is research-run reproducibility, not an incumbent-reproduction pass.
- `verification.json` records file-hash checks and unchanged production artifacts/configuration.

Run a new isolated experiment (output must not already exist):

```bash
env OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. \
  .venv/bin/python scripts/train_research_generation.py \
  --output outputs/research/<new-unique-experiment>
```

Do not select new hyperparameters by optimizing this report. Further experiments can use these historical results for development, but need future evidence for an independent confirmation claim. Active follow-up tasks live in `docs/ROADMAP.md`.
