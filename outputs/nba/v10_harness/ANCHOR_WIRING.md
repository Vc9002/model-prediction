# nba-anchor-v10 wiring spec (not wired; research only)

Status: `src/model_prediction/models/nba_anchor_v10.py` and the research artifact
`config/models/research/nba-anchor-v10.json` exist. Nothing calls them from `cli/forecast.py` or
the daily pipeline. Gate G1 failed for v9 and v9.1 in both markets (`G1.md`), so the
artifact ships lambda = 0 and `main_min_edge = null`: every call is a Flat call priced at the
anchor, and nothing is Main-eligible.

## Entry point

```python
from model_prediction.models.nba_anchor_v10 import (
    AnchorParams, CapturedAnchorSource, ContractQuote, FallbackAnchorSource, price_game,
)

params = AnchorParams.from_artifact(artifact)      # verify artifact_hash first
source = FallbackAnchorSource([consensus_source, polymarket_mid_source])
calls = price_game(
    event_id,
    {"spread": spread_pred.inference_inputs["mean"], "total": total_pred.inference_inputs["mean"]},
    {"spread": ContractQuote(line=home_margin_line, asks={"home": ask_h, "away": ask_a}),
     "total": ContractQuote(line=total_line, asks={"over": ask_o, "under": ask_u})},
    source, params, decision_time=decision_time, serving=True,
)
```

## Underlying model (v9.1, if used instead of v9)

v9.1 has no per-market wrapper classes. Build two engines, one per research artifact, and pass
the schedule season type (B7 playoff offset is silently off without it):

```python
from model_prediction.models.nba_v91 import NBAStructuralV91Engine

spread_engine = NBAStructuralV91Engine.from_artifact(load("config/models/research/nba-spread-margin-v9-1.json"))
total_engine = NBAStructuralV91Engine.from_artifact(load("config/models/research/nba-total-margin-v9-1.json"))
season_types = {event_id: espn_season_type}   # "regular-season" / "post-season" / "play-in-season"
spread_pred = [p for p in spread_engine.predict_games(history, upcoming, season_types=season_types)
               if p.market_type == "spread"]
total_pred = [p for p in total_engine.predict_games(history, upcoming, season_types=season_types)
              if p.market_type == "total"]
```

`season_type` is schedule information known before tip (ESPN scoreboard `season.type`), so it
is point-in-time safe. v9.1's `feature_basis["current_season_games_min"]` is still the lifetime
count (B1 was dropped on the replay), so the forecast.py thin-sample Over floor (B2) stays a
separate serving fix.

## What the forecast owner needs to do

1. Model means: take `inference_inputs["mean"]` from the NBA spread and total predictions
   (v9.1 artifacts in research, v9 today). Spread mean is the HOME margin.
2. Contract line: the executable Polymarket contract's line in the home-margin convention, the
   same value v9 receives as `spread_away_line` (away covers iff the home margin < line). Asks
   in probability (cents / 100), per side.
3. Anchor source: wrap the decision-time captures from the capture agent in
   `CapturedAnchorSource({(event_id, market): [AnchorQuote(..., observed_at_utc=..., benchmark_only=False)]})`;
   devigged sportsbook consensus first, Polymarket mid second (`FallbackAnchorSource`). The
   archived closing lines (`ClosingLineAnchorSource`) are benchmark-only; `serving=True`
   raises on them, on anchors with no `observed_at_utc`, and on anchors observed after
   `decision_time`.
4. `decision_time`: capture `utc_now()` AFTER the anchor captures are loaded and immediately
   before pricing, so a capture is never dropped or admitted by a stale clock (the
   two-`utc_now()` ordering bug in root CLAUDE.md).
5. "Always a call" holds per market that has a `ContractQuote`: `price_game` skips a market with
   no contract. If there is no Polymarket contract for a market, pass the anchor line as the
   contract line with empty asks to still get a Flat side/probability (`NO_EXECUTABLE_ASK`).
6. Ledgers: write EVERY `AnchorCall` to Flat (`side`, `probability`; one per market per game,
   including `NO_ANCHOR_MODEL_ONLY` and `ANCHORED_COIN_FLIP_SIDE_FROM_MODEL` calls). Route to
   Main only when `main_eligible` is true (net edge >= `main_min_edge` with a servable anchor;
   lambda = 0 does not block it, which is how track T-B would surface). Store `model_mean`, `blended_mean`, `anchor_line`,
   `anchor_source`, `anchor_observed_at_utc`, `lambda_`, `sigma`, `ask`, `fee`, `edge`,
   `reason_codes` in the row payload (this also fixes review B10 for this path).
7. Fees: `edge = probability - ask - 0.06 * ask * (1 - ask)` (Polymarket US taker fee per
   `research_incumbent_evaluation.POLICY`, coefficient from the artifact).

## Before anything is Main-eligible

- Refit lambda per market on decision-time anchors (forward captures; ESPN opening lines
  historically), not closes; it must pass G1 there too. Set `main_min_edge` on validation only.
- When the anchor is the sportsbook consensus and the contract is Polymarket, a nonzero
  anchored probability at lambda = 0 is exactly track T-B (consensus vs Polymarket); pre-register
  its threshold separately.
- Registration and any champion change go through `model_promotion promote` after G2.
