# Model Card: MLB Two-Head v1 (real Statcast features)

**Model ID:** `mlb-two-head-v1`
**Artifact:** `config/models/challengers/mlb-two-head-real-features-v1.json`
**Predictive status:** RESEARCH_ONLY
**Economic status:** REJECTED (structural — no real order-book depth source exists)
**Date:** 2026-08-07
**Branch/head:** `rebuild/clean-slate-v1` @ `e03688f` (858+ commit range this document reflects)

This supersedes the 2026-08-05 version of this card, which described the
pre-real-features rolling-scoreboard-only baseline (188 games, no
Statcast/weather/lineup/bullpen signal, accuracy 0.500/coin-flip). That
architecture no longer exists in the live pipeline.

## Architecture

Two-head model, real Statcast-derived pregame features:
- **Run-intensity head** (HistGradientBoosting): starter velocity/CSW%, bullpen workload, park factor, temperature.
- **Run-differential head** (ElasticNet): starter K%, bullpen workload differential.
- **Joint distribution**: Poisson simulation over both heads' expected runs, deriving moneyline, spread (exact signed line), and total (exact line) probabilities.
- **Conservative bound**: `BootstrapMLBEnsemble` — 20 independent resample-fit replicates, empirical [10th, 90th] percentile per market (not a flat haircut).

## Training data

- 167 real ESPN scoreboard rows deduped to 150 real completed games (`dedupe_scoreboard()` fixes a real duplicate-row bug — see `outputs/rebuild/takeover_status.md`).
- 126 of 150 matched to real Statcast pitch data (24 unmatched — no fabricated fallback for those).
- 39,692 real Statcast pitches, 270 real starter-game entries.
- Real, persisted split (`outputs/rebuild/mlb_split_manifest.json`, regenerated 2026-08-07, `final_test_consumed=true`):
  - Train: 84 games, 2026-07-26 to 2026-08-01
  - Calibration (Platt, fits calibrator only): 21 games, 2026-08-01 to 2026-08-02
  - **Final test (genuinely untouched until this single evaluation)**: 21 games, 2026-08-02 to 2026-08-04

## Performance

### Chronological validation folds (model selection only, not the final number)

| Fold | Train n | Val n | Log loss | Brier | ECE |
|---|---|---|---|---|---|
| 0 | 24 | 29 | 0.695 | 0.251 | 0.034 |
| 1 | 44 | 26 | 0.685 | 0.246 | 0.145 |
| 2 | 63 | 28 | 0.728 | 0.266 | 0.167 |

### Final held-out test (n=21, consumed once)

| Metric | Value | Coin-flip baseline |
|---|---|---|
| Log loss | 0.8652 | 0.693 |
| Brier | 0.3211 | 0.250 |
| ECE | 0.2638 | — |
| Accuracy | 0.381 | 0.500 |

### Quality-filtered subset (both starters have real prior history, n=18)

| Metric | Value |
|---|---|
| Brier | 0.2832 |
| Log loss | 0.7644 |
| Accuracy | 0.444 |

**Cold-start composition warning, disclosed not hidden**: train mean starter-availability=0.167 vs. test mean=0.929 — a real artifact of the short (10-day) real backfill window, not something the model "learned" wrong. The headline n=21 number should be read with this in mind; the n=18 quality-filtered number controls for it.

### Real conservative-probability bounds (BootstrapMLBEnsemble, 20 replicates)

Live 2026-08-06 slate example: a 0.49 calibrated point estimate produced a real [0.271, 0.671] bound; a 0.485 point estimate produced [0.136, 0.735]. These bounds are *wide*, not narrow — the honest interpretation is that 126 training games is too small a sample for the fitted heads to be stable under resampling, not that the bootstrap implementation is broken.

## Interpretation

With n=18–21, standard error is ~10-11%. Accuracy moved from 0.320 (pre train/calib/test-split-independence fix, n=25) to 0.381 (post-fix, n=21) — this reflects a real methodology correction (the calibrator was previously fit and evaluated on the same block), not a change in model quality, and both numbers sit inside plausible noise around a coin flip. **This remains squarely inconclusive, not a pass or a fail.**

## Limitations

- **Small sample**: n=18-21 on the final test is not enough to distinguish real skill from noise. More real backfill days is the only way to resolve this — not further feature engineering (see `outputs/rebuild/takeover_status.md`'s priority list).
- **Single model family**: only this one two-head architecture is trained. CLAUDE.md Part 2 specifies a control + statistical + linear + HistGradientBoosting + XGBoost challenger set combined via chronological OOF ensemble — that has not been built. `conservative_probability`'s `model_disagreement` component is consequently absent (bootstrap resampling of one architecture is not the same as disagreement across independently-trained families).
- **Only the "late" horizon exists** (start minus 60 minutes) — early/mid horizon datasets are not built.
- **No real order-book depth** — see Economic status below.

## Economic status

**REJECTED for structural reasons, not sample-size ones.** `real_market_candidates()` sets `depth_available=False` on every real candidate (fixed 2026-08-07 — previously fabricated `available_depth=999.0`), because the Polymarket source this system reads does not expose order-book depth. `decision.py`'s depth gate fails closed on that unconditionally, so **every real market currently produces `NO_BET` via `INSUFFICIENT_DEPTH` (or an earlier gate), regardless of price or edge, until a genuine depth-providing source is integrated.** This is not a sample that needs to grow — it is a missing capability. Live-verified: a real 2026-08-06 2-game slate, 32 real candidate markets, 0 BET.

## Verdict

**Not ready for promotion; correctly held at RESEARCH_ONLY / REJECTED.** The pipeline is real and runs end-to-end against real data (collection → real features → real chronological training → real calibration-independent held-out test → real market matching → winner-first decision → SQLite shadow persistence), which is a materially different and further-along state than the 2026-08-05 version of this card described. But predictive quality is genuinely unresolved on this sample size, and economic qualification is structurally blocked on missing depth data, not merely unproven. Next real steps: more backfill days (predictive), a genuine depth-providing data source (economic) — neither is satisfied by more code alone.
