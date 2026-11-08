"""Darts modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.models.darts import DartsModel
from model_prediction.sports.base import SportAdapter, SportRegistry


class DartsAdapter(SportAdapter):
    """Modular forecasting and settlement adapter for Professional Darts (PDC / WDF)."""

    def __init__(self, data_root: Path | str = "data") -> None:
        self.data_root = Path(data_root)
        self._model = DartsModel()

    @property
    def sport_name(self) -> str:
        return "darts"

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        return (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL)

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[dict[str, Any]]:
        """Fetch games for Darts slate."""
        provided = kwargs.get("games") or kwargs.get("matches")
        if provided is not None:
            return list(provided)
        return []

    def build_features(self, slate: list[dict[str, Any]], context: TimeContext) -> list[dict[str, Any]]:
        """Construct Point-in-Time features strictly observable before decision_time_utc."""
        features = []
        for g in slate:
            player_a = str(g.get("player_a") or g.get("home_team") or "")
            player_b = str(g.get("player_b") or g.get("away_team") or "")
            features.append(
                {
                    "event_id": str(g.get("event_id", f"{player_a}_vs_{player_b}")),
                    "player_a": player_a,
                    "player_b": player_b,
                    "best_of_legs": int(g.get("best_of_legs", 11)),
                    "spread_line": float(g.get("spread_line", -1.5)),
                    "total_line": float(g.get("total_line", 9.5)),
                }
            )
        return features

    def generate_probabilities(self, features: Any, context: TimeContext) -> list[dict[str, Any]]:
        """Generate Markov leg progression probabilities for Darts."""
        if not features:
            return []

        predictions: list[dict[str, Any]] = []
        for feat in features:
            pred = self._model.predict_match(
                player_a=feat["player_a"],
                player_b=feat["player_b"],
                best_of_legs=feat["best_of_legs"],
                spread_line=feat["spread_line"],
                total_line=feat["total_line"],
            )

            # Moneyline
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "darts",
                    "market_type": "moneyline",
                    "line": 0.0,
                    "model_id": self._model.version,
                    "probabilities": {
                        "home": pred["moneyline"]["a"],
                        "away": pred["moneyline"]["b"],
                    },
                    "feature_basis": "markov_leg_progression",
                }
            )

            # Spread
            spread_data = pred.get("spread", {})
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "darts",
                    "market_type": "spread",
                    "line": feat["spread_line"],
                    "model_id": self._model.version,
                    "probabilities": {
                        "home": spread_data.get("a_cover", 0.50),
                        "away": spread_data.get("b_cover", 0.50),
                    },
                    "feature_basis": "markov_leg_progression",
                }
            )

            # Total
            total_data = pred.get("total", {})
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "darts",
                    "market_type": "total",
                    "line": feat["total_line"],
                    "model_id": self._model.version,
                    "probabilities": {
                        "over": total_data.get("over", 0.50),
                        "under": total_data.get("under", 0.50),
                    },
                    "feature_basis": "markov_leg_progression",
                }
            )

        return predictions

    def grade_event(
        self,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Grade a darts open pick against final event legs score."""
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
            total_legs = h_val + a_val
            diff = total_legs - line
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
            "settlement_source": "darts_sport_adapter",
        }


# Auto-register DartsAdapter in SportRegistry
SportRegistry.register(DartsAdapter())
