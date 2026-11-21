# cfb-structural-v7: pre-registered 2027 forward gate (NCAAF overhaul plan, Step 5)

Pinned 2026-11-21, before the 2027 season. The binding text is `outputs/ncaaf/cfb_v7_2027_gate.json`
(sha256 `0f79b19884dae28ab0acc693b95fea1e293538f0992c8ba1aec3caf13a9b8f59`). This page summarises it; when they
differ, the JSON wins. Changing a threshold after 2027 data exists makes the gate void; a new gate needs a new id
and a fresh count.

## Why a forward gate

Every ESPN lined window 2017-2025 and the 2024-11-15+ holdout have been read by earlier NCAAF work (v5 recheck,
v6, v6.1, v6.2, v7), so any historical market-relative number is design-informed. No public rating system has
beaten the college-football closing line (Fair and Oster; Prediction Tracker 2026 MAE close 11.78, opener 12.31,
best computer 11.91), so edge, if any, is against stale prices. 2027 is the first clean test.

## What runs

v7 (the artifact frozen at 2027 week 0) prices every FBS-involved game; the max-edge side per market against the
captured Polymarket ask becomes a Flat-tier pick when fair minus ask is above zero. Never Main, never the
auto-buyer. The comparators are the consensus-only arm (fair = the timestamped DraftKings anchor at decision time,
the v6.2 w = 1 arm, same pick rule and asks), v5 as served, and the DraftKings opener.

## Metrics and thresholds

| tier | metric | pass |
|---|---|---|
| primary | CLV = close probability minus decision-time ask (same contract and side); close = last OPEN Polymarket book in [kickoff - 90 min, kickoff), mid when both sides exist | mean > 0 with date-clustered bootstrap 95% CI low > 0, and v7 mean CLV >= consensus-only mean CLV - 0.25 points; needs >= 300 picks with a valid close and >= 70% close coverage |
| secondary | paired margin MAE, v7 minus DraftKings opener (n >= 400) | 95% CI high < 0 |
| secondary | orthogonalised slope: coefficient on (v7 - close) in OLS of (result - close) on [1, close, v7 - close], spreads and totals (n >= 600) | 95% CI low > 0 |
| tertiary | moneyline Brier / log loss vs v5 and consensus-only | Brier delta (v7 - v5) <= +0.001, log loss <= +0.003 |

DraftKings line CLV in points (decision line to pre-kickoff close, signed toward the pick) is reported next to the
primary but does not decide it.

## Decision

- **PROMOTION_ELIGIBLE**: primary, at least one secondary, and tertiary all pass. The operator then decides through
  `python -m model_prediction.model_promotion promote`; nothing is automatic.
- **FAIL**: primary CI high < 0, or primary not passing with both secondaries on the wrong side.
- **INSUFFICIENT_DATA**: fewer than 300 picks with a valid close or close coverage under 70% after the last bowl
  (a capture problem, not a model verdict).
- **CONTINUE**: anything else; carry into 2028 with the same thresholds and a fresh count.

The verdict is computed once, after the last 2027 bowl. Monthly tables are descriptive and non-binding. A 53% ATS
rate needs about 1,100 bets to separate from 50%, which is why CLV, not win rate, is primary. v5 keeps serving and
picking throughout; nothing is disabled.

## Prerequisites the operator owns

- Pre-kickoff NCAAF close capture (reuse `pretip_close_capture.py` with `sport="ncaaf"`); without it the primary
  cannot be computed.
- The forward DraftKings capture (`espn_cfb_pregame_capture`) kept running for openers and closes.
- A Flat-pick route for v7: `challengers.NCAAF` holds one model id per market (v6.2 today), so routing v7 means
  replacing v6.2 there or adding multi-challenger support to `production_registry.py`. That is an operator decision.
- Live 2027 play-by-play for v7's in-season ratings (see the quick-261121-v7 SUMMARY for what is missing).
