"""Walk-forward chronological backtester with zero look-ahead.

For each date T in the window: build ratings/trends from every cached game
strictly before T, predict the games played on T, grade against the actual
results. Outputs Brier over time, calibration drift, cumulative flat P&L at
fair odds, per-market breakdown, and by-trend-strength cohorts.

Model qualification is accuracy-first: at least 50 selective calls with at
least a 60% hit rate on an explicitly locked holdout. Brier and calibration
are secondary reports. ROI and market-price economics are optional diagnostics.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scipy.stats import poisson, skellam

from .calibration import IdentityCalibrator, TrainablePlattCalibrator, calibration_metrics
from .domain import EASTERN, MarketType
from .features.base import FeatureStore, GameRecord
from .features.elo_ratings import build_elo
from .features.trends import TrendEngine
from .lifecycle import evaluate_locked_holdout
from .pricing import american_to_decimal, grade_pick


@dataclass(frozen=True)
class HistoricalMarketSide:
    line: float | None
    american_odds: int


@dataclass(frozen=True)
class HistoricalMarketEvent:
    event_id: str
    observed_at_utc: str
    timestamp_valid: bool
    source: str
    markets: dict[str, dict[str, HistoricalMarketSide]]


def walk_forward_backtest(
    store: FeatureStore,
    sport: str,
    start_date: str,
    end_date: str,
    minimum_history_games: int = 50,
    market_lines_path: str | Path | None = None,
    confidence_threshold: float | None = None,
    locked_holdout: bool = False,
) -> dict[str, Any]:
    """Backtest the sport's moneyline via Elo + trend blend, day by day."""
    if sport.lower() == "mlb":
        return walk_forward_mlb_backtest(
            store,
            start_date,
            end_date,
            minimum_history_games=minimum_history_games,
            market_lines_path=market_lines_path,
            confidence_threshold=confidence_threshold,
            locked_holdout=locked_holdout,
        )
    games = store.load_games(sport)
    if not games:
        return {"status": "no_cached_games", "sport": sport}
    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for game in games:
        by_date[game.start.astimezone(EASTERN).date().isoformat()].append(game)
    dates = sorted(day for day in by_date if start_date <= day <= end_date)
    predictions: list[dict[str, Any]] = []
    daily_brier: list[dict[str, Any]] = []
    for day in dates:
        history = [game for game in games if game.start.astimezone(EASTERN).date().isoformat() < day]
        if len(history) < minimum_history_games:
            continue
        elo = build_elo(history, sport)
        trend = TrendEngine(history)
        day_rows: list[dict[str, Any]] = []
        for game in by_date[day]:
            elo_p = elo.expected_home_win(game.home_team, game.away_team)
            momentum_gap = (
                trend.team_trend(game.home_team).offensive_momentum
                - trend.team_trend(game.away_team).offensive_momentum
            )
            # Blend Elo + trend: shrink Elo toward 0.50 by the momentum signal
            blend_p = 0.50 + (elo_p - 0.50) * (1.0 + 0.15 * momentum_gap)
            blend_p = max(0.05, min(0.95, blend_p))
            outcome = 1 if game.home_score > game.away_score else 0
            if game.home_score == game.away_score:
                continue  # no draw grading on the two-way moneyline path
            row: dict[str, Any] = {
                "date": day,
                "event_id": game.event_id,
                "probability": blend_p,
                "outcome": outcome,
                "momentum_gap": momentum_gap,
                "flat_pnl": (1 / elo_p - 1) if outcome == 1 else -1.0,
            }
            day_rows.append(row)
        if day_rows:
            predictions.extend(day_rows)
            day_brier = sum((r["probability"] - r["outcome"]) ** 2 for r in day_rows) / len(day_rows)
            daily_brier.append({"date": day, "brier": round(day_brier, 6), "games": len(day_rows)})
    if not predictions:
        return {"status": "insufficient_history", "sport": sport, "dates_examined": len(dates)}
    probabilities = [row["probability"] for row in predictions]
    outcomes = [row["outcome"] for row in predictions]
    cumulative = 0.0
    pnl_curve = []
    for row in predictions:
        cumulative += row["flat_pnl"]
        pnl_curve.append(round(cumulative, 4))
    returns = [row["flat_pnl"] for row in predictions]
    mean_return = sum(returns) / len(returns)
    sd_return = (
        (sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)) ** 0.5
        if len(returns) > 1
        else 0.0
    )
    sharpe = mean_return / sd_return * (252**0.5) if sd_return > 0 else None
    brier = round(
        sum((p - y) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / len(predictions), 6
    )
    calibration = calibration_metrics(probabilities, outcomes)
    qualification = _qualification_report(
        predictions,
        confidence_threshold=confidence_threshold,
        locked_holdout=locked_holdout,
        brier_score=brier,
        calibration=calibration,
    )
    return {
        "status": "ok",
        "sport": sport,
        "window": {"start": start_date, "end": end_date},
        "observations": len(predictions),
        "promotion_eligible": qualification["qualified"],
        "qualification": qualification,
        "brier": brier,
        "calibration": calibration,
        "daily_brier": daily_brier,
        "cumulative_flat_pnl": pnl_curve[-1],
        "flat_pnl_curve_tail": pnl_curve[-20:],
        "sharpe_annualized_per_bet": round(sharpe, 4) if sharpe is not None else None,
        "by_trend_strength": _trend_cohorts(predictions),
        "note": (
            "Flat P&L is graded at the model's own fair odds (no market prices in the "
            "backtest cache yet); it measures calibration, not beatable edge."
        ),
    }


def walk_forward_mlb_backtest(
    store: FeatureStore,
    start_date: str,
    end_date: str,
    minimum_history_games: int = 100,
    market_lines_path: str | Path | None = None,
    confidence_threshold: float | None = None,
    locked_holdout: bool = False,
) -> dict[str, Any]:
    """Three-market MLB walk-forward evaluation with explicit trend causality.

    The same opponent-adjusted expected-run state prices moneyline, run line,
    and total. When historical two-sided quotes are unavailable, spread and
    total use clearly labeled synthetic reference lines and no ROI/CLV claim is
    produced. Market prices remain diagnostics and never affect qualification.
    """
    games = store.load_games("mlb")
    if not games:
        return {"status": "no_cached_games", "sport": "mlb"}
    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for game in games:
        by_date[game.start.astimezone(EASTERN).date().isoformat()].append(game)
    dates = sorted(day for day in by_date if day <= end_date)
    quotes = _load_historical_market_events(market_lines_path)
    records: list[dict[str, Any]] = []
    calibration_history: dict[str, list[tuple[str, float, int]]] = defaultdict(list)
    for day in dates:
        history = [game for game in games if game.start.astimezone(EASTERN).date().isoformat() < day]
        if len(history) < minimum_history_games:
            continue
        elo = build_elo(history, "mlb")
        trends = TrendEngine(history)
        reference_total = math.floor(_mean_total(history)) + 0.5
        day_records: list[dict[str, Any]] = []
        for game in by_date[day]:
            projection = _mlb_trend_projection(history, trends, game)
            quote = quotes.get(game.event_id)
            day_records.extend(
                _mlb_market_records(
                    game,
                    projection,
                    elo.expected_home_win(game.home_team, game.away_team),
                    reference_total,
                    quote,
                )
            )
        _calibrate_mlb_day(day_records, calibration_history, day)
        if start_date <= day <= end_date:
            records.extend(day_records)
        for row in day_records:
            if row["outcome"] is not None:
                calibration_history[row["market_type"]].append((day, row["raw_probability"], row["outcome"]))
    if not records:
        return {"status": "insufficient_history", "sport": "mlb", "dates_examined": len(dates)}
    by_market = {
        market: _market_metrics([row for row in records if row["market_type"] == market])
        for market in ("moneyline", "spread", "total")
    }
    binary = [row for row in records if row["outcome"] is not None]
    probabilities = [row["probability"] for row in binary]
    outcomes = [row["outcome"] for row in binary]
    timestamp_valid_quotes = bool(quotes) and all(event.timestamp_valid for event in quotes.values())
    quoted_records = [row for row in records if row["american_odds"] is not None]
    brier = round(
        sum((p - y) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / len(binary),
        6,
    )
    calibration = calibration_metrics(probabilities, outcomes)
    economics = _market_economics(quoted_records)
    qualification = _qualification_report(
        binary,
        confidence_threshold=confidence_threshold,
        locked_holdout=locked_holdout,
        brier_score=brier,
        calibration=calibration,
        roi=economics.get("roi"),
        price_diagnostics=economics,
        outcomes_are_selected_side=True,
    )
    return {
        "status": "ok",
        "sport": "mlb",
        "model_version": "mlb-opponent-adjusted-trend-score-v2",
        "window": {"start": start_date, "end": end_date},
        "games": len({row["event_id"] for row in records}),
        "observations": len(binary),
        "promotion_eligible": qualification["qualified"],
        "qualification": qualification,
        "point_in_time": {
            "features": "strictly prior completed games",
            "market_quotes": ("timestamp_valid" if timestamp_valid_quotes else "missing_or_reconstructed"),
            "season_filter": "MLB preseason and All-Star games excluded",
        },
        "brier": brier,
        "calibration": calibration,
        "by_market": by_market,
        "by_trend_strength": _trend_cohorts_mlb(binary),
        "trend_causality": {
            "records_changed_vs_long_horizon_only": sum(
                abs(row["probability"] - row["long_horizon_probability"]) > 1e-9 for row in binary
            ),
            "mean_absolute_probability_change": round(
                sum(abs(row["probability"] - row["long_horizon_probability"]) for row in binary)
                / len(binary),
                6,
            ),
            "note": "Trend inputs alter expected runs before all three market probabilities are derived.",
        },
        "market_economics": economics,
        "limitations": [
            "No market ROI or CLV is claimed without stored two-sided pregame quotes.",
            "Score-only history cannot reconstruct starters, lineups, bullpen availability, park, or weather.",
            "Market economics are optional diagnostics and never change model qualification.",
        ],
    }


def _calibrate_mlb_day(
    rows: Sequence[dict[str, Any]],
    history: dict[str, list[tuple[str, float, int]]],
    day: str,
) -> None:
    cutoff = (date.fromisoformat(day) - timedelta(days=365)).isoformat()
    calibrators: dict[str, TrainablePlattCalibrator | IdentityCalibrator] = {}
    for market in {row["market_type"] for row in rows}:
        prior = [item for item in history[market] if item[0] >= cutoff]
        calibrators[market] = TrainablePlattCalibrator.fit(
            [item[1] for item in prior],
            [item[2] for item in prior],
            base_model_version="mlb-opponent-adjusted-trend-score-v2",
            version="mlb-trend-platt-rolling-365d-v1",
            minimum_sample=100,
        )
    for row in rows:
        calibrator = calibrators[row["market_type"]]
        prior = [item for item in history[row["market_type"]] if item[0] >= cutoff]
        calibrated_alternatives = []
        for alternative in row.pop("_alternatives"):
            probability = calibrator.transform(alternative["raw_probability"])
            long_probability = calibrator.transform(alternative["raw_long_horizon_probability"])
            decimal = (
                american_to_decimal(alternative["american_odds"]) if alternative["american_odds"] else None
            )
            non_push = 1 - alternative["push_probability"]
            expected_value = (
                probability * non_push * (decimal - 1) - (1 - probability) * non_push
                if decimal
                else probability - 0.5
            )
            calibrated_alternatives.append(
                {
                    **alternative,
                    "probability": probability,
                    "long_probability": long_probability,
                    "expected_value": expected_value,
                }
            )
        selected = max(calibrated_alternatives, key=lambda item: item["probability"])
        row.update(
            {
                "selection": selected["selection"],
                "line": selected["line"],
                "american_odds": selected["american_odds"],
                "raw_probability": selected["raw_probability"],
                "raw_long_horizon_probability": selected["raw_long_horizon_probability"],
                "probability": selected["probability"],
                "long_horizon_probability": selected["long_probability"],
                "push_probability": selected["push_probability"],
                "market_probability": selected["market_probability"],
            }
        )
        result = grade_pick(
            MarketType(row["market_type"]),
            row["selection"],
            row["line"],
            row.pop("away_score"),
            row.pop("home_score"),
        ).value
        row["result"] = result
        row["outcome"] = None if result == "push" else int(result == "win")
        row["edge"] = (
            row["probability"] - row["market_probability"] if row["market_probability"] is not None else None
        )
        if row["market_type"] == MarketType.MONEYLINE.value:
            row["baseline_probability"] = (
                row.pop("elo_home_probability")
                if row["selection"] == "home"
                else 1 - row.pop("elo_home_probability")
            )
        else:
            row.pop("elo_home_probability")
        row["calibration_version"] = calibrator.metadata.calibration_version
        row["calibration_sample_size"] = calibrator.metadata.sample_size
        if row["market_type"] != MarketType.MONEYLINE.value and prior:
            row["baseline_probability"] = sum(item[2] for item in prior) / len(prior)


def _mlb_trend_projection(
    history: Sequence[GameRecord], trends: TrendEngine, game: GameRecord
) -> dict[str, float]:
    baseline = max(trends.league_baseline, 1e-6)
    away = trends.team_trend(game.away_team)
    home = trends.team_trend(game.home_team)
    weights = {"hl3": 0.15, "hl10": 0.50, "hl25": 0.35}

    def level(values: dict[str, float], selected: dict[str, float] = weights) -> float:
        return sum(selected[key] * values[key] for key in selected)

    home_average = sum(item.home_score for item in history) / len(history)
    away_average = sum(item.away_score for item in history) / len(history)
    home_field = math.sqrt(max(home_average, 0.1) / max(away_average, 0.1))
    away_attack = level(away.offense)
    home_attack = level(home.offense)
    away_defense = level(away.defense)
    home_defense = level(home.defense)
    # Multiplicative attack-versus-defense matchup, followed by a bounded
    # short-versus-long momentum adjustment. Both are in runs/game units.
    away_momentum = (away.offensive_momentum - home.defensive_momentum) / baseline
    home_momentum = (home.offensive_momentum - away.defensive_momentum) / baseline
    away_runs = away_attack * home_defense / baseline / home_field * math.exp(0.08 * away_momentum)
    home_runs = home_attack * away_defense / baseline * home_field * math.exp(0.08 * home_momentum)
    long_away = away.offense["hl25"] * home.defense["hl25"] / baseline / home_field
    long_home = home.offense["hl25"] * away.defense["hl25"] / baseline * home_field
    return {
        "away_runs": _clip_run_mean(away_runs),
        "home_runs": _clip_run_mean(home_runs),
        "long_away_runs": _clip_run_mean(long_away),
        "long_home_runs": _clip_run_mean(long_home),
        "trend_strength": abs(home_momentum - away_momentum),
        "minimum_team_games": float(min(away.games_played, home.games_played)),
    }


def _mlb_market_records(
    game: GameRecord,
    projection: dict[str, float],
    elo_home_probability: float,
    reference_total: float,
    quote: HistoricalMarketEvent | None,
) -> list[dict[str, Any]]:
    definitions: list[tuple[MarketType, tuple[str, ...], dict[str, HistoricalMarketSide]]] = []
    if quote:
        definitions = [
            (MarketType.MONEYLINE, ("away", "home"), quote.markets.get("moneyline", {})),
            (MarketType.SPREAD, ("away", "home"), quote.markets.get("spread", {})),
            (MarketType.TOTAL, ("over", "under"), quote.markets.get("total", {})),
        ]
    else:
        definitions = [
            (
                MarketType.MONEYLINE,
                ("away", "home"),
                {"away": HistoricalMarketSide(None, 0), "home": HistoricalMarketSide(None, 0)},
            ),
            (
                MarketType.SPREAD,
                ("away", "home"),
                {"away": HistoricalMarketSide(1.5, 0), "home": HistoricalMarketSide(-1.5, 0)},
            ),
            (
                MarketType.TOTAL,
                ("over", "under"),
                {
                    "over": HistoricalMarketSide(reference_total, 0),
                    "under": HistoricalMarketSide(reference_total, 0),
                },
            ),
        ]
    output: list[dict[str, Any]] = []
    for market_type, selections, sides in definitions:
        candidates: list[dict[str, Any]] = []
        implied = {
            selection: 1 / american_to_decimal(sides[selection].american_odds)
            for selection in selections
            if selection in sides and sides[selection].american_odds
        }
        implied_total = sum(implied.values())
        for selection in selections:
            side = sides.get(selection)
            if side is None:
                continue
            probability, push_probability = _market_probability(
                projection["home_runs"],
                projection["away_runs"],
                market_type,
                selection,
                side.line,
            )
            long_probability, _ = _market_probability(
                projection["long_home_runs"],
                projection["long_away_runs"],
                market_type,
                selection,
                side.line,
            )
            conditional = probability / max(1 - push_probability, 1e-9)
            long_conditional = long_probability / max(1 - push_probability, 1e-9)
            candidates.append(
                {
                    "selection": selection,
                    "line": side.line,
                    "american_odds": side.american_odds or None,
                    "raw_probability": min(0.999999, max(0.000001, conditional)),
                    "raw_long_horizon_probability": min(0.999999, max(0.000001, long_conditional)),
                    "push_probability": push_probability,
                    "market_probability": (
                        implied[selection] / implied_total
                        if selection in implied and implied_total > 0
                        else None
                    ),
                }
            )
        if not candidates:
            continue
        structural_baseline = elo_home_probability if market_type is MarketType.MONEYLINE else 0.5
        output.append(
            {
                "date": game.start.astimezone(EASTERN).date().isoformat(),
                "event_id": game.event_id,
                "market_type": market_type.value,
                "selection": "",
                "line": None,
                "american_odds": None,
                "probability": 0.5,
                "long_horizon_probability": 0.5,
                "baseline_probability": structural_baseline,
                "elo_home_probability": elo_home_probability,
                "market_probability": None,
                "push_probability": 0.0,
                "result": "",
                "outcome": None,
                "away_score": game.away_score,
                "home_score": game.home_score,
                "_alternatives": candidates,
                "trend_strength": projection["trend_strength"],
                "away_expected_runs": projection["away_runs"],
                "home_expected_runs": projection["home_runs"],
                "quote_source": quote.source if quote else "synthetic_reference_line",
                "quote_timestamp_valid": quote.timestamp_valid if quote else False,
            }
        )
    return output


def _market_probability(
    home_mean: float,
    away_mean: float,
    market_type: MarketType,
    selection: str,
    line: float | None,
) -> tuple[float, float]:
    if market_type is MarketType.MONEYLINE:
        tie = float(skellam.pmf(0, home_mean, away_mean))
        home_regulation = float(skellam.sf(0, home_mean, away_mean))
        extra_home = home_mean / (home_mean + away_mean)
        home_win = home_regulation + tie * extra_home
        return (home_win if selection == "home" else 1 - home_win), 0.0
    if line is None:
        raise ValueError(f"{market_type.value} requires a line")
    if market_type is MarketType.SPREAD:
        first, second = (home_mean, away_mean) if selection == "home" else (away_mean, home_mean)
        threshold = -line
        win = float(skellam.sf(math.floor(threshold), first, second))
        push = float(skellam.pmf(int(threshold), first, second)) if threshold.is_integer() else 0.0
        return win, push
    total_mean = home_mean + away_mean
    if selection == "over":
        win = float(poisson.sf(math.floor(line), total_mean))
        push = float(poisson.pmf(int(line), total_mean)) if line.is_integer() else 0.0
    else:
        win = float(poisson.cdf(math.ceil(line) - 1, total_mean))
        push = float(poisson.pmf(int(line), total_mean)) if line.is_integer() else 0.0
    return win, push


def _market_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    binary = [row for row in rows if row["outcome"] is not None]
    if not binary:
        return {"observations": 0}
    probabilities = [row["probability"] for row in binary]
    outcomes = [row["outcome"] for row in binary]
    baseline = [row["baseline_probability"] for row in binary]
    result = {
        "observations": len(binary),
        "pushes": len(rows) - len(binary),
        "brier": round(
            sum((p - y) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / len(binary), 6
        ),
        "baseline_brier": round(
            sum((p - y) ** 2 for p, y in zip(baseline, outcomes, strict=True)) / len(binary), 6
        ),
        "log_loss": round(
            -sum(
                y * math.log(p) + (1 - y) * math.log(1 - p)
                for p, y in zip(probabilities, outcomes, strict=True)
            )
            / len(binary),
            6,
        ),
        "hit_rate": round(sum(outcomes) / len(outcomes), 6),
        "calibration": calibration_metrics(probabilities, outcomes),
        "line_source": sorted({row["quote_source"] for row in rows}),
    }
    market_binary = [row for row in binary if row["market_probability"] is not None]
    if market_binary:
        market_brier = sum((row["market_probability"] - row["outcome"]) ** 2 for row in market_binary) / len(
            market_binary
        )
        model_market_brier = sum((row["probability"] - row["outcome"]) ** 2 for row in market_binary) / len(
            market_binary
        )
        result.update(
            {
                "market_observations": len(market_binary),
                "market_brier": round(market_brier, 6),
                "model_brier_on_market_sample": round(model_market_brier, 6),
                "model_minus_market_brier": round(model_market_brier - market_brier, 6),
            }
        )
    result["market_economics"] = _market_economics([row for row in rows if row["american_odds"] is not None])
    return result


def _market_economics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "status": "unavailable_without_historical_two_sided_prices",
            "flat_pnl": None,
            "roi": None,
            "clv": None,
        }

    def evaluate(selected: Sequence[dict[str, Any]]) -> dict[str, Any]:
        pnl = 0.0
        for row in selected:
            if row["result"] == "win":
                pnl += american_to_decimal(row["american_odds"]) - 1
            elif row["result"] == "loss":
                pnl -= 1
        return {
            "observations": len(selected),
            "flat_pnl": round(pnl, 6),
            "roi": round(pnl / len(selected), 6) if selected else None,
        }

    all_rows = evaluate(rows)
    thresholds = {}
    for threshold in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10):
        selected = [row for row in rows if row.get("edge") is not None and row["edge"] >= threshold]
        thresholds[f"{threshold:.2f}"] = evaluate(selected)
    return {
        "status": "diagnostic" if not all(row["quote_timestamp_valid"] for row in rows) else "ok",
        **all_rows,
        "clv": None,
        "by_minimum_model_edge": thresholds,
    }


def _qualification_report(
    rows: Sequence[dict[str, Any]],
    *,
    confidence_threshold: float | None,
    locked_holdout: bool,
    brier_score: float,
    calibration: dict[str, Any],
    roi: float | None = None,
    price_diagnostics: dict[str, Any] | None = None,
    outcomes_are_selected_side: bool = False,
) -> dict[str, Any]:
    """Build an auditable qualification decision without price-based gates."""
    if confidence_threshold is None:
        return {
            "evaluated": False,
            "qualified": False,
            "reason": "confidence threshold not supplied",
            "required": {"locked_holdout": True, "minimum_calls": 50, "minimum_hit_rate": 0.60},
            "brier_score": brier_score,
            "calibration": calibration,
            "roi": roi,
            "price_diagnostics": price_diagnostics,
            "diagnostic_metrics_affect_qualification": False,
        }
    if not 0.5 <= confidence_threshold < 1:
        raise ValueError("confidence_threshold must be in [0.5, 1)")

    calls = hits = 0
    called_probabilities: list[float] = []
    called_outcomes: list[int] = []
    for row in rows:
        probability = float(row["probability"])
        confidence = probability if outcomes_are_selected_side else max(probability, 1 - probability)
        if confidence < confidence_threshold:
            continue
        calls += 1
        if outcomes_are_selected_side:
            selected_outcome = int(row["outcome"])
        else:
            selected_outcome = int(row["outcome"]) if probability >= 0.5 else 1 - int(row["outcome"])
        hits += selected_outcome
        called_probabilities.append(confidence)
        called_outcomes.append(selected_outcome)
    called_brier = (
        round(
            sum(
                (probability - outcome) ** 2
                for probability, outcome in zip(called_probabilities, called_outcomes, strict=True)
            )
            / calls,
            6,
        )
        if calls
        else None
    )
    called_calibration = calibration_metrics(called_probabilities, called_outcomes) if calls else None
    decision = evaluate_locked_holdout(
        calls=calls,
        hits=hits,
        total_predictions=len(rows),
        locked_holdout=locked_holdout,
        brier_score=called_brier,
        calibration=called_calibration,
        roi=roi,
        price_diagnostics=price_diagnostics,
    ).to_dict()
    return {
        "evaluated": True,
        "confidence_threshold": confidence_threshold,
        **decision,
        "secondary_reporting_population": "selective_calls",
        "overall_holdout_brier_score": brier_score,
        "overall_holdout_calibration": calibration,
        "diagnostic_metrics_affect_qualification": False,
    }


def _load_historical_market_events(
    path: str | Path | None,
) -> dict[str, HistoricalMarketEvent]:
    if path is None:
        return {}
    source = Path(path)
    if not source.exists():
        return {}
    events: dict[str, HistoricalMarketEvent] = {}
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            markets: dict[str, dict[str, HistoricalMarketSide]] = {}
            for market, sides in raw.get("markets", {}).items():
                markets[market] = {
                    side: HistoricalMarketSide(
                        None if quote.get("line") is None else float(quote["line"]),
                        int(quote["american_odds"]),
                    )
                    for side, quote in sides.items()
                    if quote.get("american_odds")
                }
            event_id = str(raw["event_id"])
            events[event_id] = HistoricalMarketEvent(
                event_id=event_id,
                observed_at_utc=str(raw.get("observed_at_utc", "")),
                timestamp_valid=bool(raw.get("timestamp_valid", False)),
                source=str(raw.get("source", "unknown")),
                markets=markets,
            )
    return events


def _trend_cohorts_mlb(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: row["trend_strength"])
    if len(ordered) < 9:
        return []
    size = len(ordered) // 3
    output = []
    for name, cohort in (
        ("weak_trend", ordered[:size]),
        ("medium_trend", ordered[size : 2 * size]),
        ("strong_trend", ordered[2 * size :]),
    ):
        output.append(
            {
                "cohort": name,
                "observations": len(cohort),
                "brier": round(
                    sum((row["probability"] - row["outcome"]) ** 2 for row in cohort) / len(cohort),
                    6,
                ),
                "hit_rate": round(sum(row["outcome"] for row in cohort) / len(cohort), 6),
                "mean_trend_strength": round(sum(row["trend_strength"] for row in cohort) / len(cohort), 6),
            }
        )
    return output


def _mean_total(history: Sequence[GameRecord]) -> float:
    return sum(game.total for game in history) / len(history)


def _clip_run_mean(value: float) -> float:
    return max(1.5, min(8.5, value))


def _trend_cohorts(predictions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket accuracy by |momentum gap| terciles."""
    rows = sorted(predictions, key=lambda row: abs(row["momentum_gap"]))
    if len(rows) < 9:
        return []
    cohorts = []
    third = len(rows) // 3
    for name, chunk in (
        ("weak_trend", rows[:third]),
        ("medium_trend", rows[third : 2 * third]),
        ("strong_trend", rows[2 * third :]),
    ):
        brier = sum((row["probability"] - row["outcome"]) ** 2 for row in chunk) / len(chunk)
        cohorts.append(
            {
                "cohort": name,
                "observations": len(chunk),
                "brier": round(brier, 6),
                "hit_rate": round(
                    sum(1 for row in chunk if (row["probability"] >= 0.5) == (row["outcome"] == 1))
                    / len(chunk),
                    6,
                ),
            }
        )
    return cohorts


def rolling_window(end_date: str, days: int = 90) -> tuple[str, str]:
    from datetime import date

    end = date.fromisoformat(end_date)
    return (end - timedelta(days=days)).isoformat(), end_date
