"""NFL modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.features.base import GameRecord
from model_prediction.models.nfl_structural_v5 import NFLStructuralV5Engine
from model_prediction.sports.base import SportAdapter, SportRegistry


class NFLAdapter(SportAdapter):
    """Modular forecasting and settlement adapter for the NFL."""

    def __init__(self, data_root: Path | str = "data") -> None:
        self.data_root = Path(data_root)
        self._engine = NFLStructuralV5Engine()

    @property
    def sport_name(self) -> str:
        return "nfl"

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        return (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL)

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[dict[str, Any]]:
        """Fetch games for the NFL slate, verifying PIT observability."""
        provided = kwargs.get("games") or kwargs.get("matches")
        if provided is not None:
            slate = []
            for g in provided:
                slate.append(
                    {
                        "event_id": str(g["event_id"]),
                        "event_start_utc": str(g["event_start_utc"]),
                        "away_team": str(g["away_team"]),
                        "home_team": str(g["home_team"]),
                        "spread_home_line": float(g.get("spread_home_line", 0.0)),
                        "total_line": float(g.get("total_line", 45.0)),
                    }
                )
            return slate

        history_file = self.data_root / "historical/nfl_games_all.jsonl"
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
                    slate.append(
                        {
                            "event_id": str(row["event_id"]),
                            "event_start_utc": str(start_utc),
                            "away_team": str(row["away_team"]),
                            "home_team": str(row["home_team"]),
                            "spread_home_line": float(row.get("spread_home_line", 0.0)),
                            "total_line": float(row.get("total_line", 45.0)),
                        }
                    )
        return slate

    def build_features(self, slate: list[dict[str, Any]], context: TimeContext) -> dict[str, Any]:
        """Construct Point-in-Time features strictly observable before decision_time_utc."""
        history_file = self.data_root / "historical/nfl_games_all.jsonl"
        observable_records: list[GameRecord] = []

        if history_file.exists():
            with history_file.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    start_utc = row.get("event_start_utc")
                    if start_utc and context.is_observable(start_utc):
                        observable_records.append(
                            GameRecord(
                                event_id=str(row["event_id"]),
                                event_start_utc=str(start_utc),
                                league="NFL",
                                away_team=str(row["away_team"]),
                                home_team=str(row["home_team"]),
                                away_score=row.get("away_score"),
                                home_score=row.get("home_score"),
                            )
                        )

        return {
            "slate": slate,
            "observable_history": observable_records,
            "context": context,
        }

    def generate_probabilities(self, features: Any, context: TimeContext) -> list[dict[str, Any]]:
        """Run joint structural score model on PIT features."""
        slate: list[dict[str, Any]] = features.get("slate", [])
        if not slate:
            return []

        predictions: list[dict[str, Any]] = []

        for game in slate:
            home = game["home_team"]
            away = game["away_team"]
            spread_line = float(game.get("spread_home_line", 0.0))
            total_line = float(game.get("total_line", 45.0))

            forecast = self._engine.forecast_game(
                home_team=home,
                away_team=away,
                spread_home_line=spread_line,
                total_line=total_line,
            )

            # 1. Moneyline
            predictions.append(
                {
                    "event_id": game["event_id"],
                    "sport": "nfl",
                    "market_type": "moneyline",
                    "model_id": "nfl-structural-v5",
                    "probabilities": {
                        "home": forecast.prob_home_win,
                        "away": forecast.prob_away_win,
                    },
                    "feature_basis": {
                        "projected_home_points": forecast.projected_home_points,
                        "projected_away_points": forecast.projected_away_points,
                        "projected_margin_home": forecast.projected_margin_home,
                    },
                }
            )

            # 2. Spread
            predictions.append(
                {
                    "event_id": game["event_id"],
                    "sport": "nfl",
                    "market_type": "spread",
                    "line": spread_line,
                    "model_id": "nfl-spread-structural-v5",
                    "probabilities": {
                        "home": forecast.prob_home_cover,
                        "away": forecast.prob_away_cover,
                    },
                    "feature_basis": {
                        "projected_margin_home": forecast.projected_margin_home,
                        "spread_home_line": spread_line,
                    },
                }
            )

            # 3. Total
            predictions.append(
                {
                    "event_id": game["event_id"],
                    "sport": "nfl",
                    "market_type": "total",
                    "line": total_line,
                    "model_id": "nfl-total-structural-v5",
                    "probabilities": {
                        "over": forecast.prob_over,
                        "under": forecast.prob_under,
                    },
                    "feature_basis": {
                        "projected_total": forecast.projected_total,
                        "total_line": total_line,
                    },
                }
            )

        return predictions

    def grade_event(
        self,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Grade an NFL open pick against final event scores."""
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
            total_points = h_val + a_val
            diff = total_points - line
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
            "settlement_source": "nfl_sport_adapter",
        }


# Auto-register NFLAdapter in SportRegistry
SportRegistry.register(NFLAdapter())
