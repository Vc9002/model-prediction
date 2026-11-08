"""Soccer modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.models.soccer_dixon_coles_v2 import SoccerDixonColesV2Model
from model_prediction.sports.base import SportAdapter, SportRegistry


class SoccerAdapter(SportAdapter):
    """Modular forecasting and settlement adapter for Soccer."""

    def __init__(self, data_root: Path | str = "data") -> None:
        self.data_root = Path(data_root)
        self._model = SoccerDixonColesV2Model()

    @property
    def sport_name(self) -> str:
        return "soccer"

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        return (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL)

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[dict[str, Any]]:
        """Fetch games for Soccer slate."""
        provided = kwargs.get("games") or kwargs.get("matches")
        if provided is not None:
            return list(provided)

        history_file = self.data_root / "historical/soccer_games_all.jsonl"
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
            features.append(
                {
                    "event_id": str(g.get("event_id", "")),
                    "home_team": str(g.get("home_team", "")),
                    "away_team": str(g.get("away_team", "")),
                    "competition_id": str(g.get("competition_id", "global")),
                    "spread_line": float(g.get("spread_line", -0.5)),
                    "total_line": float(g.get("total_line", 2.5)),
                }
            )
        return features

    def generate_probabilities(self, features: Any, context: TimeContext) -> list[dict[str, Any]]:
        """Generate Dixon-Coles soccer probabilities."""
        if not features:
            return []

        predictions: list[dict[str, Any]] = []
        for feat in features:
            forecast = self._model.forecast_match(
                home_team=feat["home_team"],
                away_team=feat["away_team"],
                competition_id=feat["competition_id"],
            )

            # Moneyline (3-way)
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "soccer",
                    "market_type": "moneyline",
                    "line": 0.0,
                    "model_id": "soccer-dixon-coles-v2",
                    "probabilities": {
                        "home": forecast.prob_home_win,
                        "away": forecast.prob_away_win,
                        "draw": forecast.prob_draw,
                    },
                    "feature_basis": "hierarchical_dixon_coles",
                }
            )

            # Spread (-0.5 / +0.5)
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "soccer",
                    "market_type": "spread",
                    "line": feat["spread_line"],
                    "model_id": "soccer-dixon-coles-v2",
                    "probabilities": {
                        "home": forecast.prob_home_minus_0_5
                        if feat["spread_line"] <= -0.5
                        else forecast.prob_home_plus_0_5,
                        "away": 1.0
                        - (
                            forecast.prob_home_minus_0_5
                            if feat["spread_line"] <= -0.5
                            else forecast.prob_home_plus_0_5
                        ),
                    },
                    "feature_basis": "hierarchical_dixon_coles",
                }
            )

            # Total (Over / Under 2.5)
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "soccer",
                    "market_type": "total",
                    "line": feat["total_line"],
                    "model_id": "soccer-dixon-coles-v2",
                    "probabilities": {
                        "over": forecast.prob_over_2_5,
                        "under": forecast.prob_under_2_5,
                    },
                    "feature_basis": "hierarchical_dixon_coles",
                }
            )

        return predictions

    def grade_event(
        self,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Grade a soccer open pick against final event box score."""
        home_score = box_score.get("home_score")
        away_score = box_score.get("away_score")
        if home_score is None or away_score is None:
            return None

        h_val = float(home_score)
        a_val = float(away_score)
        m_type = str(open_pick.get("market_type") or "moneyline").lower()
        sel = str(open_pick.get("selection") or "").lower()
        line = float(open_pick.get("line") or 0.0)

        result: str
        if m_type == "moneyline":
            if h_val == a_val:
                if sel in ("draw", "tie", "x"):
                    result = "win"
                elif sel in ("dnb_home", "dnb_away"):
                    result = "push"
                else:
                    result = "loss"
            elif h_val > a_val:
                result = "win" if sel in ("home", "1", "dnb_home") else "loss"
            else:
                result = "win" if sel in ("away", "2", "dnb_away") else "loss"

        elif m_type == "spread":
            margin = h_val - a_val
            eff = (margin + line) if sel in ("home", "1") else (-margin + line)
            if abs(eff) < 1e-9:
                result = "push"
            elif eff > 0:
                result = "win"
            else:
                result = "loss"

        elif m_type == "total":
            total_goals = h_val + a_val
            diff = total_goals - line
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
            "settlement_source": "soccer_sport_adapter",
        }


# Auto-register SoccerAdapter in SportRegistry
SportRegistry.register(SoccerAdapter())
