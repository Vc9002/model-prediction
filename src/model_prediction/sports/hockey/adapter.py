"""Hockey modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.features.base import GameRecord
from model_prediction.models.hockey import UpcomingHockeyMatch, hockey_model
from model_prediction.sports.base import SportAdapter, SportRegistry


class HockeyAdapter(SportAdapter):
    """Modular forecasting and settlement adapter for NHL and international hockey leagues."""

    def __init__(self, data_root: Path | str = "data") -> None:
        self.data_root = Path(data_root)
        self._model = hockey_model()

    @property
    def sport_name(self) -> str:
        return "hockey"

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        return (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL)

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[UpcomingHockeyMatch]:
        """Fetch games for the hockey slate, verifying PIT observability."""
        provided = kwargs.get("matches") or kwargs.get("games")
        if provided is not None:
            slate = []
            for m in provided:
                if isinstance(m, UpcomingHockeyMatch):
                    slate.append(m)
                elif isinstance(m, dict):
                    slate.append(
                        UpcomingHockeyMatch(
                            event_id=str(m["event_id"]),
                            event_start_utc=str(m["event_start_utc"]),
                            away_team=str(m["away_team"]),
                            home_team=str(m["home_team"]),
                            league=str(m.get("league", "NHL")),
                        )
                    )
            return slate

        history_file = self.data_root / "historical/hockey_games_all.jsonl"
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
                        UpcomingHockeyMatch(
                            event_id=str(row["event_id"]),
                            event_start_utc=str(start_utc),
                            away_team=str(row["away_team"]),
                            home_team=str(row["home_team"]),
                            league=str(row.get("league", "NHL")),
                        )
                    )
        return slate

    def build_features(self, slate: list[UpcomingHockeyMatch], context: TimeContext) -> dict[str, Any]:
        """Construct Point-in-Time features strictly observable before decision_time_utc."""
        history_file = self.data_root / "historical/hockey_games_all.jsonl"
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
                                league=str(row.get("league", "NHL")),
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
        """Run Dixon-Coles bivariate Poisson model on PIT features."""
        slate: list[UpcomingHockeyMatch] = features.get("slate", [])
        history: list[GameRecord] = features.get("observable_history", [])

        if not slate:
            return []

        preds = self._model.predict_games(history, slate)
        predictions: list[dict[str, Any]] = []

        for p in preds:
            predictions.append(
                {
                    "event_id": p.event_id,
                    "sport": "hockey",
                    "league": p.league,
                    "market_type": p.market_type,
                    "line": p.line,
                    "model_id": getattr(p, "model_version", self._model.version),
                    "probabilities": p.probabilities,
                    "feature_basis": p.feature_basis,
                }
            )

        return predictions

    def grade_event(
        self,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Grade a hockey open pick against final event scores (including OT/SO)."""
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
            "settlement_source": "hockey_sport_adapter",
        }


# Auto-register HockeyAdapter in SportRegistry
SportRegistry.register(HockeyAdapter())
