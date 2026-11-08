"""KBO modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.models.international_baseball_v3 import InternationalBaseballV3Model
from model_prediction.sports.base import SportAdapter, SportRegistry


class KBOAdapter(SportAdapter):
    """Modular forecasting and settlement adapter for Korean Baseball Organization (KBO)."""

    def __init__(self, data_root: Path | str = "data") -> None:
        self.data_root = Path(data_root)
        self._model = InternationalBaseballV3Model(league="KBO")

    @property
    def sport_name(self) -> str:
        return "kbo"

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        return (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL)

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[dict[str, Any]]:
        """Fetch games for KBO slate."""
        provided = kwargs.get("games") or kwargs.get("matches")
        if provided is not None:
            return list(provided)
        return []

    def build_features(self, slate: list[dict[str, Any]], context: TimeContext) -> list[dict[str, Any]]:
        """Construct Point-in-Time features strictly observable before decision_time_utc."""
        features = []
        for g in slate:
            features.append(
                {
                    "event_id": str(g.get("event_id", "")),
                    "home_team": str(g.get("home_team", "")),
                    "away_team": str(g.get("away_team", "")),
                    "lambda_home": float(g.get("lambda_home", 4.5)),
                    "lambda_away": float(g.get("lambda_away", 4.1)),
                    "total_line": float(g.get("total_line", 8.5)),
                    "spread_line": float(g.get("spread_line", -1.5)),
                }
            )
        return features

    def generate_probabilities(self, features: Any, context: TimeContext) -> list[dict[str, Any]]:
        """Generate KBO probabilities using tie-aware bivariate Poisson model."""
        if not features:
            return []

        predictions: list[dict[str, Any]] = []
        for feat in features:
            forecast = self._model.forecast_match(
                home_team=feat["home_team"],
                away_team=feat["away_team"],
                lambda_home=feat["lambda_home"],
                lambda_away=feat["lambda_away"],
                total_line=feat["total_line"],
            )

            # Moneyline
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "kbo",
                    "market_type": "moneyline",
                    "line": 0.0,
                    "model_id": "kbo-structural-v3",
                    "probabilities": {
                        "home": forecast.prob_home_win,
                        "away": forecast.prob_away_win,
                        "draw": forecast.prob_draw,
                    },
                    "feature_basis": "tie_aware_bivariate_poisson",
                }
            )

            # Calculate runline cover from bivariate score matrix
            matrix = forecast.bivariate_score_matrix
            p_home_cover = sum(
                matrix[h][a]
                for h in range(len(matrix))
                for a in range(len(matrix[h]))
                if (h - a + feat["spread_line"]) > 0
            )

            # Spread (Runline)
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "kbo",
                    "market_type": "spread",
                    "line": feat["spread_line"],
                    "model_id": "kbo-structural-v3",
                    "probabilities": {
                        "home": p_home_cover,
                        "away": 1.0 - p_home_cover,
                    },
                    "feature_basis": "tie_aware_bivariate_poisson",
                }
            )

            # Total
            predictions.append(
                {
                    "event_id": feat["event_id"],
                    "sport": "kbo",
                    "market_type": "total",
                    "line": feat["total_line"],
                    "model_id": "kbo-structural-v3",
                    "probabilities": {
                        "over": forecast.prob_over_total,
                        "under": forecast.prob_under_total,
                    },
                    "feature_basis": "tie_aware_bivariate_poisson",
                }
            )

        return predictions

    def grade_event(
        self,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Grade a KBO open pick against final event box score, honoring tie rules."""
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
                result = "push"
            elif (h_val > a_val and sel in ("home", "1")) or (a_val > h_val and sel in ("away", "2")):
                result = "win"
            else:
                result = "loss"

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
            total_runs = h_val + a_val
            diff = total_runs - line
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
            "settlement_source": "kbo_sport_adapter",
        }


# Auto-register KBOAdapter in SportRegistry
SportRegistry.register(KBOAdapter())
