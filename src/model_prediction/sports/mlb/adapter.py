"""MLB modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.models.mlb_structural_v10 import (
    MLBStructuralV10Model,
    MLBv10FeatureVector,
    MLBv10Prediction,
)
from model_prediction.sports.base import SportAdapter, SportRegistry


class MLBAdapter(SportAdapter):
    """Modular forecasting and settlement adapter for Major League Baseball."""

    def __init__(self, data_root: Path | str = "data") -> None:
        self.data_root = Path(data_root)
        self._model = MLBStructuralV10Model()

    @property
    def sport_name(self) -> str:
        return "mlb"

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        return (
            MarketType.MONEYLINE,
            MarketType.SPREAD,
            MarketType.TOTAL,
            MarketType.NRFI,
            MarketType.YRFI,
        )

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[dict[str, Any]]:
        """Fetch games for the MLB slate."""
        provided = kwargs.get("games") or kwargs.get("matches")
        if provided is not None:
            return list(provided)

        history_file = self.data_root / "historical/mlb_games_all.jsonl"
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

    def build_features(self, slate: list[dict[str, Any]], context: TimeContext) -> list[MLBv10FeatureVector]:
        """Construct Point-in-Time features strictly observable before decision_time_utc."""
        features: list[MLBv10FeatureVector] = []
        for g in slate:
            feat = MLBv10FeatureVector(
                event_id=str(g.get("event_id", "")),
                home_team=str(g.get("home_team", "")),
                away_team=str(g.get("away_team", "")),
                event_start_utc=str(g.get("event_start_utc", context.decision_time_utc.isoformat())),
                home_sp_id=str(g.get("home_sp_id", "SP_H")),
                away_sp_id=str(g.get("away_sp_id", "SP_A")),
                spread_line=float(g.get("spread_line", -1.5)),
                total_line=float(g.get("total_line", 8.5)),
            )
            features.append(feat)
        return features

    def generate_probabilities(self, features: Any, context: TimeContext) -> list[dict[str, Any]]:
        """Generate MLB probabilities across moneyline, runline, and totals."""
        if not features:
            return []

        predictions: list[dict[str, Any]] = []
        for feat in features:
            pred: MLBv10Prediction = self._model.predict(feat)

            # Moneyline
            predictions.append(
                {
                    "event_id": feat.event_id,
                    "sport": "mlb",
                    "market_type": "moneyline",
                    "line": 0.0,
                    "model_id": "mlb-structural-v10",
                    "probabilities": {
                        "home": pred.prob_home_win,
                        "away": pred.prob_away_win,
                    },
                    "feature_basis": "structural_starter_bullpen_decomposition",
                }
            )

            # Runline / Spread (-1.5 on home)
            predictions.append(
                {
                    "event_id": feat.event_id,
                    "sport": "mlb",
                    "market_type": "spread",
                    "line": feat.spread_line,
                    "model_id": "mlb-structural-v10",
                    "probabilities": {
                        "home": pred.prob_home_cover_spread,
                        "away": pred.prob_away_cover_spread,
                    },
                    "feature_basis": "structural_starter_bullpen_decomposition",
                }
            )

            # Total
            predictions.append(
                {
                    "event_id": feat.event_id,
                    "sport": "mlb",
                    "market_type": "total",
                    "line": feat.total_line,
                    "model_id": "mlb-structural-v10",
                    "probabilities": {
                        "over": pred.prob_over,
                        "under": pred.prob_under,
                    },
                    "feature_basis": "structural_starter_bullpen_decomposition",
                }
            )

        return predictions

    def grade_event(
        self,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Grade an MLB open pick against final event box score."""
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

        elif m_type in ("first_inning", "nrfi", "yrfi"):
            home_1st = box_score.get("home_first_inning_score")
            away_1st = box_score.get("away_first_inning_score")
            if home_1st is None or away_1st is None:
                return None
            first_inning_runs = float(home_1st) + float(away_1st)
            if sel in ("nrfi", "under", "u", "0"):
                result = "win" if first_inning_runs == 0 else "loss"
            else:
                result = "win" if first_inning_runs > 0 else "loss"
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
            "settlement_source": "mlb_sport_adapter",
        }


# Auto-register MLBAdapter in SportRegistry
SportRegistry.register(MLBAdapter())
