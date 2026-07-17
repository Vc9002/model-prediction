---
name: shadow-bet-review
description: Log, classify, size, settle, audit, and diagnose shadow sports forecasts in Model Prediction. Use when Vincent pastes a line, asks for a side or units, asks to log a forecast, checks results, reviews performance, manages team bans, or asks why a pick lost. Covers MLB, NBA, WNBA, NFL, and tennis ML, spreads, and totals. Never places wagers.
---

# Shadow Bet Review

Operate from the project root. Read `ARCHITECTURE.md`, `AGENTS.md`, this skill, `README.md`, `config/model.yaml`, and only the relevant `docs/leagues/<LEAGUE>.md` contract.

## Build the compact research packet

Collect event/start, canonical teams, market/selection/line, decision price, decision timestamp, origin, model/state/version/hashes, calibrated probability, uncertainty, feature contributions, source IDs/timestamps, risk flags, ban result, and eligibility result. Do not paste full unrelated league context into the session.

Use provider APIs and immutable snapshots first. For page verification, use the workspace Dia bridge. Never invent a probability, lineup, injury, result, closing line, or artifact status.

## Record a prediction

1. Resolve both teams canonically. Unknown/ambiguous entities stop with `NO_CALL_ENTITY_UNRESOLVED`.
2. Freeze the supplied price before consulting closing/result data.
3. Build the independent game probability without market price input. Apply only versioned calibration. Market baselines must carry a baseline ID and are never independent edge.
4. Confirm freshness, uncertainty, primary drivers, and any large disagreement with the market.
5. Run `model-prediction call ...`. Every valid event gets a side prediction, but eligibility decides the record:
   - `RESEARCH_OBSERVATION`: zero units; calibration/raw evaluation only.
   - `QUALIFIED_SHADOW_CALL`: passed every gate; deterministic paper units.
   - `NO_CALL_TEAM_BANNED`: record canonical banned team and zero units, including game totals.
6. Report pick ID, record type, reason code, origin, model/calibration versions, probability, price, raw edge, uncertainty, and units.

The deprecated `--force-shadow-call` flag never bypasses bans, freshness, uncertainty, start time, lifecycle, edge, disagreement, duplicates, or exposure; it produces zero units.

## Team bans

Use `ban-team add/remove/list/check`. Add a governance reason and review date when known. Alias resolution and mutations are deterministic and audited. A ban may permit research but never qualified units.

## Settle and learn

1. Inspect open records and unresolved loss reviews.
2. Verify final score, grading, and same-selection closing line/price.
3. Run `settle` or `void`; never rewrite decision evidence.
4. Report qualified ROI separately from research, plus raw-probability CLV, calibration status, exposure, and review count.
5. Diagnose every loss as `bad_luck`, `missing_information`, `bad_data`, `model_error`, `market_or_rule_error`, or `process_error`. Cite specific evidence. Correct data/process defects; require a predeclared cohort/ablation for model changes.

## Integrity

- Run `exposure` before and after qualified calls.
- Never call an analyst, market baseline, synthetic test, unvalidated, degraded, suspended, or retired artifact qualified.
- Never add an LLM/API dependency for deterministic math.
- Never authenticate to or submit at a betting venue.

## MLB totals freeze

Measured Edge (`measured-edge-v1`) is immutable until at least 30 newly logged forward predictions settle. Before that gate, do not change the Trend Engine base formula, fixed `0.85p + 0.075` transform, raw expected-return selection, or totals logic.

After the gate, follow `docs/MLB_TOTALS_POST_FREEZE.md`: evaluate a totals-specific residual layer first, then a branched absolute run-intensity head only if needed. Preserve a reconciled joint score distribution and an untouched chronological test period. Never tune against the same small cohort until it looks profitable.
