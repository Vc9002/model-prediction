"""Forward slate builder for the MLB Measured Edge paired models.

Formerly ``research/mlb_forward.py`` (the v0.4/v0.6 paired path). Behavior is
versioned and deterministic. Research iteration is continuous, but any change
must be evaluated walk-forward and on a locked holdout before promotion.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import replace as _replace
from datetime import datetime
from pathlib import Path

import httpx

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
    market_snapshot_hash: str | None = None
    market_snapshot_archive_path: str | None = None
    market_snapshot_record_id: str | None = None
    market_quote_timestamp_valid: bool | None = None
    market_quote_source: str | None = None
    market_quote_provenance: str | None = None
    market_quote_reconstructed: bool | None = None


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

    # reconstructed_features() makes 6 sequential ESPN calls per event
    # (2x schedule, 2x gamelog, 2x athlete). Run those calls across
    # events concurrently instead of one event at a time -- a
    # ~15-game slate serialized this into up to 90 sequential HTTP
    # round trips, and a single slow/timed-out ESPN response stalled
    # every event behind it (real 81-minute daily run, 2026-08-23).
    upcoming_events = [event for event in events if parse_utc(event["date"]) > observed_at]

    def _fetch_features(event: dict) -> tuple[dict, object]:
        try:
            return event, client.reconstructed_features(event)
        except (KeyError, TypeError, ValueError, httpx.HTTPError) as error:
            return event, error

    features_by_event_id: dict[str, object] = {}
    if upcoming_events:
        with ThreadPoolExecutor(max_workers=min(16, len(upcoming_events))) as pool:
            for event, result in pool.map(_fetch_features, upcoming_events):
                features_by_event_id[str(event["id"])] = result

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
            prefetched = features_by_event_id.get(str(event["id"]))
            if isinstance(prefetched, Exception):
                raise prefetched
            features = _replace(
                prefetched,
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
        except (KeyError, TypeError, ValueError, httpx.HTTPError) as error:
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
        # Calibrate first, then pick -- calibration shrinks toward 0.5
        # and can flip a raw over/under preference when both sides are
        # close, fixing the systematic over-selection bias (P1-17).
        # Swapped from the pre-2026-08-03 order (pick then calibrate)
        # where calibration could only rescale confidence in the already-
        # chosen side, never flip the pick back to the other side.
        calibrated_on_both = {side: model.calibrate_selected_side(probabilities[side]) for side in sides}
        selection = max(sides, key=lambda side: calibrated_on_both[side])
        raw_probability = probabilities[selection]
        calibrated_probability = calibrated_on_both[selection]
        no_vig = dict(
            zip(
                sides,
                normalize_no_vig(tuple(prices[side].decision_probability for side in sides)),
            )
        )
        selected_line = None if market_type is MarketType.MONEYLINE else prices[selection].line
        quote_timestamp_valid = parse_utc(odds_snapshot.observed_at_utc) < parse_utc(
            odds_snapshot.event_start_utc
        )
        source_book = odds_snapshot.raw_response.get("books", {}).get(market_type.value)
        reconstructed = (
            source_book.get("reconstructed")
            if isinstance(source_book, dict) and isinstance(source_book.get("reconstructed"), bool)
            else None
        )
        provenance = (
            "decision_time_executable_quote"
            if odds_snapshot.provider == "polymarket_us"
            else "decision_time_sportsbook_quote"
        )
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
                market_snapshot_hash=odds_snapshot.snapshot_hash,
                market_snapshot_archive_path=odds_snapshot.snapshot_archive_path,
                market_snapshot_record_id=odds_snapshot.snapshot_record_id,
                market_quote_timestamp_valid=quote_timestamp_valid,
                market_quote_source=odds_snapshot.provider,
                market_quote_provenance=provenance,
                market_quote_reconstructed=reconstructed,
            )
        )
    return output


def _teams(event) -> tuple[str, str]:
    competitors = event["competitions"][0]["competitors"]
    by_side = {item["homeAway"]: item["team"]["displayName"] for item in competitors}
    return by_side["away"], by_side["home"]
