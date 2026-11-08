"""Tennis modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.models.tennis_markov import (
    TennisMarkovEngine,
    TennisPlayerStats,
)
from model_prediction.sports.base import SportAdapter, SportRegistry


class TennisAdapter(SportAdapter):
    """Modular forecasting and settlement adapter for Professional Tennis (ATP/WTA)."""

    def __init__(self, data_root: Path | str = "data") -> None:
        self.data_root = Path(data_root)
        self._engine = TennisMarkovEngine()

    @property
    def sport_name(self) -> str:
        return "tennis"

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        return (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL)

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[dict[str, Any]]:
        """Fetch games for Tennis slate."""
        provided = kwargs.get("games") or kwargs.get("matches")
        if provided is not None:
            return list(provided)

        history_file = self.data_root / "historical/tennis_games_all.jsonl"
        if not history_file.exists():
            return []

        slate = []
        with history_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                start_utc = row.get("event_start_utc")
                if not start_utc:
                    continue
                if start_utc >= context.decision_time_utc.isoformat():
                    slate.append(row)
        return slate

    def build_features(self, slate: list[dict[str, Any]], context: TimeContext) -> list[dict[str, Any]]:
        """Construct Point-in-Time features strictly observable before decision_time_utc."""
        features = []
        for g in slate:
            player_a = str(g.get("player_a") or g.get("home_team") or "")
            player_b = str(g.get("player_b") or g.get("away_team") or "")
            features.append(
                {
                    "event_id": str(g.get("event_id", "")),
                    "player_a": player_a,
                    "player_b": player_b,
                    "surface": str(g.get("surface", "Hard")),
                    "match_format": str(g.get("match_format", "Bo3")),
                    "serve_pct_a": float(g.get("serve_pct_a", 0.65)),
                    "return_pct_a": float(g.get("return_pct_a", 0.38)),
                    "serve_pct_b": float(g.get("serve_pct_b", 0.64)),
                    "return_pct_b": float(g.get("return_pct_b", 0.37)),
                    "spread_line": float(g.get("spread_line", -1.5)),
                    "total_line": float(g.get("total_line", 22.5)),
                }
            )
        return features

    def generate_probabilities(self, features: Any, context: TimeContext) -> list[dict[str, Any]]:
        """Generate hierarchical Markov tennis probabilities."""
        if not features:
            return []

        predictions: list[dict[str, Any]] = []
        for feat in features:
            stat_a = TennisPlayerStats(
                player_id=feat["player_a"],
                name=feat["player_a"],
                serve_points_won_pct=feat["serve_pct_a"],
                return_points_won_pct=feat["return_pct_a"],
            )
            stat_b = TennisPlayerStats(
                player_id=feat["player_b"],
                name=feat["player_b"],
                serve_points_won_pct=feat["serve_pct_b"],
                return_points_won_pct=feat["return_pct_b"],
            )

            forecast = self._engine.forecast_match(
                player_a=stat_a,
                player_b=stat_b,
                surface=feat["surface"],
                match_format=feat["match_format"],
            )

            # Moneyline
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "tennis",
                    "market_type": "moneyline",
                    "line": 0.0,
                    "model_id": "tennis-markov-v2",
                    "probabilities": {
                        "home": forecast.prob_player_a_wins,
                        "away": forecast.prob_player_b_wins,
                    },
                    "feature_basis": "hierarchical_markov_chain",
                }
            )

            # Spread (Set spread -1.5)
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "tennis",
                    "market_type": "spread",
                    "line": feat["spread_line"],
                    "model_id": "tennis-markov-v2",
                    "probabilities": {
                        "home": forecast.prob_player_a_spread_sets,
                        "away": forecast.prob_player_b_spread_sets,
                    },
                    "feature_basis": "hierarchical_markov_chain",
                }
            )

            # Total Games
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "tennis",
                    "market_type": "total",
                    "line": feat["total_line"],
                    "model_id": "tennis-markov-v2",
                    "probabilities": {
                        "over": forecast.prob_over_games,
                        "under": forecast.prob_under_games,
                    },
                    "feature_basis": "hierarchical_markov_chain",
                }
            )

        return predictions

    def grade_event(
        self,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Grade a tennis open pick against final event box score.

        Honors retirements / walkovers as push where appropriate per Polymarket rules.
        """
        status = str(box_score.get("status") or "").lower()
        if status in ("retired", "walkover", "cancelled", "void"):
            # If match was abandoned or resolved via fair market price
            return {
                "pick_id": open_pick.get("pick_id"),
                "event_id": event_id,
                "status": "settled",
                "result": "push",
                "pnl_units": 0.0,
                "settlement_source": "tennis_sport_adapter",
            }

        home_score = box_score.get("home_score")
        away_score = box_score.get("away_score")
        if home_score is None or away_score is None:
            winner = box_score.get("winner")
            if winner:
                h_val = 1.0 if winner in ("home", "player_a", "1") else 0.0
                a_val = 1.0 if winner in ("away", "player_b", "2") else 0.0
            else:
                return None
        else:
            h_val = float(home_score)
            a_val = float(away_score)

        m_type = str(open_pick.get("market_type") or "moneyline").lower()
        sel = str(open_pick.get("selection") or "").lower()
        line = float(open_pick.get("line") or 0.0)

        result: str
        if m_type == "moneyline":
            if h_val == a_val:
                result = "push"
            elif (h_val > a_val and sel in ("home", "1", "player_a")) or (
                a_val > h_val and sel in ("away", "2", "player_b")
            ):
                result = "win"
            else:
                result = "loss"

        elif m_type == "spread":
            margin = h_val - a_val
            eff = (margin + line) if sel in ("home", "1", "player_a") else (-margin + line)
            if abs(eff) < 1e-9:
                result = "push"
            elif eff > 0:
                result = "win"
            else:
                result = "loss"

        elif m_type == "total":
            home_games = float(box_score.get("home_games") or h_val)
            away_games = float(box_score.get("away_games") or a_val)
            total_games = home_games + away_games
            diff = total_games - line
            if abs(diff) < 1e-9:
                result = "push"
            elif (diff > 0 and sel in ("over", "o")) or (diff < 0 and sel in ("under", "u")):
                result = "win"
            else:
                result = "loss"
        else:
            return None

        units = float(open_pick.get("units") or 1.0)
        pnl = units if result == "win" else (-units if result == "loss" else 0.0)

        return {
            "pick_id": open_pick.get("pick_id"),
            "event_id": event_id,
            "status": "settled",
            "result": result,
            "pnl_units": pnl,
            "home_score": int(h_val),
            "away_score": int(a_val),
            "settlement_source": "tennis_sport_adapter",
        }


# Auto-register TennisAdapter in SportRegistry
SportRegistry.register(TennisAdapter())
