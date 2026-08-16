# Trend Score v2 backtest report

## Decision

`REJECT_CHALLENGER`. The feature is wired correctly, but its apparent Q2 gain is
too small and reverses in July. The reconstructed-price comparison also loses
to the no-vig market in every market.

## Structural-baseline comparison

| Window | Market | Model Brier | Baseline Brier | Difference |
|---|---:|---:|---:|---:|
| Q2 2026, 1,204 games | Moneyline | 0.249749 | 0.249984 | -0.000235 |
| Q2 2026, 1,204 games | Spread | 0.230043 | 0.230099 | -0.000056 |
| Q2 2026, 1,204 games | Total | 0.249736 | 0.250203 | -0.000467 |
| July 1-16, 170 games | Moneyline | 0.251033 | 0.247730 | +0.003303 |
| July 1-16, 170 games | Spread | 0.235368 | 0.235163 | +0.000205 |
| July 1-16, 170 games | Total | 0.252391 | 0.249951 | +0.002440 |

Lower is better. All three small Q2 improvements disappear on the later holdout.

## Reconstructed-price diagnostic, July 1-12

The cache contains 169 evaluated games; 162 have reconstructed quotes, producing
486 priced market records. Those quotes were obtained after games, so all ROI is
diagnostic and CLV is unavailable.

| Market | Model Brier on priced sample | Market Brier | Difference | Flat diagnostic ROI |
|---|---:|---:|---:|---:|
| Moneyline | 0.254072 | 0.246972 | +0.007100 | -1.12% |
| Spread | 0.268699 | 0.259974 | +0.008725 | +19.71% |
| Total | 0.250162 | 0.247445 | +0.002717 | -23.99% |

The spread profit is not credible evidence. Its Brier is worse than market, and
only 10 observations remain at the 2% model-edge threshold. Across all markets,
the 2% threshold has 220 observations and -1.62% diagnostic ROI.

## What to do next

Do not tune another arbitrary trend coefficient against July. Run a versioned
ablation of long-horizon only, unadjusted recent form, opponent-adjusted form,
and reliability-shrunk form on new chronological folds. In parallel, collect
prospective decision-time prices and the missing MLB context needed to test a
branched absolute run-intensity head for totals.
