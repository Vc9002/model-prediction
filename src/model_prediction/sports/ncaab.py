"""NCAAB modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v6 import ncaab_spread_v6_model
from model_prediction.models.ncaab_total_v6 import ncaab_total_v6_model
from model_prediction.sports.base import SportAdapter, SportRegistry


class NCAABAdapter(SportAdapter):
    """Modular forecasting and settlement adapter for NCAA Men's Basketball."""

    def __init__(self, data_root: Path | str = "data") -> None:
        self.data_root = Path(data_root)
        self._spread_model = ncaab_spread_v6_model()
        self._total_model = ncaab_total_v6_model()

    @property
    def sport_name(self) -> str:
        return "ncaab"

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        return (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL)

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[UpcomingGame]:
        """Fetch games for the slate, verifying PIT observability."""
        provided_games = kwargs.get("games")
        if provided_games is not None:
            slate = []
            for g in provided_games:
                if isinstance(g, UpcomingGame):
                    slate.append(g)
                elif isinstance(g, dict):
                    slate.append(
                        UpcomingGame(
                            event_id=str(g["event_id"]),
                            event_start_utc=str(g["event_start_utc"]),
                            away_team=str(g["away_team"]),
                            home_team=str(g["home_team"]),
                            spread_away_line=float(g.get("spread_away_line", 0.0)),
                            total_line=float(g.get("total_line", 145.0)),
                            neutral_site=bool(g.get("neutral_site", False)),
                        )
                    )
            return slate

        history_file = self.data_root / "historical/ncaab_games_all.jsonl"
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
                # Games scheduled at or after decision time
                if start_utc >= context.decision_time_utc.isoformat():
                    slate.append(
                        UpcomingGame(
                            event_id=str(row["event_id"]),
                            event_start_utc=str(start_utc),
                            away_team=str(row["away_team"]),
                            home_team=str(row["home_team"]),
                            spread_away_line=float(row.get("spread_away_line", 0.0)),
                            total_line=float(row.get("total_line", 145.0)),
                            neutral_site=bool(row.get("neutral_site", False)),
                        )
                    )
        return slate

    def build_features(self, slate: list[UpcomingGame], context: TimeContext) -> dict[str, Any]:
        """Construct Point-in-Time features strictly observable before decision_time_utc."""
        history_file = self.data_root / "historical/ncaab_games_all.jsonl"
        observable_records: list[GameRecord] = []

        if history_file.exists():
            with history_file.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    start_utc = row.get("event_start_utc")
                    # Strict PIT enforcement: only games started before context decision time
                    if start_utc and context.is_observable(start_utc):
                        observable_records.append(
                            GameRecord(
                                event_id=str(row["event_id"]),
                                event_start_utc=str(start_utc),
                                league="NCAAB",
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
        """Run dedicated v6 spread and total engines on PIT features."""
        slate: list[UpcomingGame] = features.get("slate", [])
        history: list[GameRecord] = features.get("observable_history", [])

        if not slate:
            return []

        predictions: list[dict[str, Any]] = []

        # 1. Spread and Moneyline predictions
        spread_preds = self._spread_model.predict_spreads(history, slate)
        for p in spread_preds:
            predictions.append(
                {
                    "event_id": p.event_id,
                    "sport": "ncaab",
                    "market_type": p.market_type,
                    "model_id": getattr(p, "model_version", "ncaab-spread-v6"),
                    "probabilities": p.probabilities,
                    "feature_basis": p.feature_basis,
                }
            )

        # 2. Total predictions
        total_preds = self._total_model.predict_totals(history, slate)
        for p in total_preds:
            predictions.append(
                {
                    "event_id": p.event_id,
                    "sport": "ncaab",
                    "market_type": p.market_type,
                    "model_id": getattr(p, "model_version", "ncaab-total-v6"),
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
        """Grade an NCAAB open pick against final event scores."""
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
            # home margin = home - away
            margin = h_val - a_val
            # If bet is on home +line: home covers if margin + line > 0
            # If bet is on away +line: away covers if -margin + line > 0
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
            "settlement_source": "ncaab_sport_adapter",
        }


# Auto-register NCAABAdapter in SportRegistry
SportRegistry.register(NCAABAdapter())
