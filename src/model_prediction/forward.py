"""Forward slate builder for the MLB Measured Edge paired models.

Formerly ``research/mlb_forward.py`` (the v0.4/v0.6 paired path). Behavior is
versioned and deterministic. Research iteration is continuous, but any change
must be evaluated walk-forward and on a locked holdout before promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as _replace
from datetime import datetime
from pathlib import Path

from .data_sources.espn import ESPNMLBClient
from .data_sources.mlb_market_odds import MLBGameOdds, MLBMarketOddsFeed
from .domain import MarketType, parse_utc
from .models.mlb import FormulaSpec, MeasuredEdgeMarginModel, MeasuredEdgeTotalsModel
from .pricing import normalize_no_vig


@dataclass(frozen=True)
class MLBForwardCandidate:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    market_type: MarketType
    selection: str
    line: float | None
    sportsbook: str
    american_odds: int
    raw_probability: float
    shrunk_probability: float
    no_vig_probability: float
    uncertainty: float
    rationale: str
    risks: str
    observed_at_utc: str
    model_name: str
    model_version: str
    model_artifact_hash: str
    calibration_version: str
    feature_schema_version: str


def build_mlb_slate(
    game_date: str,
    client: ESPNMLBClient,
    spec: FormulaSpec,
    margin_model_path: str | Path,
    totals_model_path: str | Path,
    observed_at: datetime,
    odds_feed: MLBMarketOddsFeed,
) -> tuple[list[MLBForwardCandidate], list[dict[str, str]], int]:
    """Build a paired-model slate while reusing one set of ESPN game features."""
    margin_model = MeasuredEdgeMarginModel(margin_model_path, spec)
    totals_model = MeasuredEdgeTotalsModel(totals_model_path, spec)
    events = client.scoreboard(game_date).get("events", [])
    odds_feed.load(game_date)
    candidates: list[MLBForwardCandidate] = []
    skipped: list[dict[str, str]] = []
    for event in events:
        try:
            if parse_utc(event["date"]) <= observed_at:
                raise ValueError("event_started")
            away_team, home_team = _teams(event)
            odds = odds_feed.for_game(
                str(event["id"]),
                event["date"],
                away_team,
                home_team,
            )
            features = _replace(
                client.reconstructed_features(event),
                market_snapshot_hash=odds.snapshot_hash,
            )
            spread_market = odds.markets.get("spread")
            total_market = odds.markets.get("total")
            if spread_market is None or total_market is None:
                raise ValueError("required_market_unavailable")
            away_spread_line = spread_market["away"].line
            home_spread_line = spread_market["home"].line
            total_line = total_market["over"].line
            under_line = total_market["under"].line
            if (
                away_spread_line is None
                or home_spread_line is None
                or abs(away_spread_line + home_spread_line) > 1e-9
            ):
                raise ValueError("current_spread_lines_unavailable_or_incoherent")
            if total_line is None or under_line != total_line:
                raise ValueError("current_total_lines_unavailable_or_incoherent")
            margin_output = margin_model.predict(features, away_spread_line)
            totals_output = totals_model.predict(features, total_line)
            candidates.extend(
                _paired_event_candidates(
                    event,
                    odds,
                    margin_output,
                    totals_output,
                    margin_model,
                    totals_model,
                    observed_at,
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            skipped.append({"event_id": str(event.get("id", "unknown")), "reason": str(error)})
    return candidates, skipped, len(events)


def _paired_event_candidates(
    event,
    odds_snapshot: MLBGameOdds,
    margin_output,
    totals_output,
    margin_model,
    totals_model,
    observed_at,
):
    away_team, home_team = _teams(event)
    definitions = (
        (
            MarketType.MONEYLINE,
            margin_output.moneyline,
            ("away", "home"),
            margin_model,
            margin_output.run_estimate,
        ),
        (
            MarketType.SPREAD,
            margin_output.spread,
            ("away", "home"),
            margin_model,
            margin_output.run_estimate,
        ),
        (
            MarketType.TOTAL,
            totals_output.total,
            ("over", "under"),
            totals_model,
            totals_output.run_estimate,
        ),
    )
    output = []
    for market_type, distribution, sides, model, estimate in definitions:
        prices = odds_snapshot.markets[market_type.value]
        odds = {side: prices[side].american_odds for side in sides}
        probabilities = {
            sides[0]: distribution.first_win_probability,
            sides[1]: distribution.second_win_probability,
        }
        # Pick the side the model thinks is more likely to win/cover, not the
        # side with the best expected value against the market's price --
        # operator directive, 2026-07-30 ("all models should be picking
        # which side to win and logging that, not the edge"). Matches
        # learned_forward.py's moneyline selection (probability argmax);
        # this legacy path had never been updated to match.
        selection = max(sides, key=lambda side: probabilities[side])
        raw_probability = probabilities[selection]
        calibrated_probability = model.calibrate_selected_side(raw_probability)
        no_vig = dict(
            zip(
                sides,
                normalize_no_vig(tuple(prices[side].decision_probability for side in sides)),
            )
        )
        selected_line = None if market_type is MarketType.MONEYLINE else prices[selection].line
        output.append(
            MLBForwardCandidate(
                event_id=str(event["id"]),
                event_start_utc=event["date"],
                away_team=away_team,
                home_team=home_team,
                market_type=market_type,
                selection=selection,
                line=selected_line,
                sportsbook=odds_snapshot.provider,
                american_odds=odds[selection],
                raw_probability=round(raw_probability, 6),
                shrunk_probability=round(calibrated_probability, 6),
                no_vig_probability=round(no_vig[selection], 6),
                uncertainty=estimate.uncertainty,
                rationale=(
                    f"{model.raw['model_name']}: Trend Engine projected "
                    f"{estimate.away_expected_runs:.2f}-{estimate.home_expected_runs:.2f}; "
                    f"raw selected-side p={raw_probability:.4f}, calibrated "
                    f"p={calibrated_probability:.4f}."
                ),
                risks=(
                    "Research-only; lineup, bullpen, park, weather, xFIP, and wRC+ may be "
                    "neutral fallbacks; market availability and BBO depth may change before first pitch."
                ),
                observed_at_utc=odds_snapshot.observed_at_utc,
                model_name=str(model.raw["model_name"]),
                model_version=str(model.raw["model_version"]),
                model_artifact_hash=str(model.raw["artifact_hash"]),
                calibration_version=str(model.raw["calibration_version"]),
                feature_schema_version=estimate.feature_schema_version,
            )
        )
    return output


def _teams(event) -> tuple[str, str]:
    competitors = event["competitions"][0]["competitors"]
    by_side = {item["homeAway"]: item["team"]["displayName"] for item in competitors}
    return by_side["away"], by_side["home"]
