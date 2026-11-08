"""Base class for esports game adapters (CS2, Dota 2, LoL, Valorant, Rainbow Six)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.models.esports_contextual_v7 import (
    EsportsContextFeatures,
    EsportsContextualV7Model,
)
from model_prediction.sports.base import SportAdapter


class BaseEsportsAdapter(SportAdapter):
    """Base modular adapter for competitive esports titles."""

    def __init__(self, title: str, data_root: Path | str = "data") -> None:
        self.title = title.lower()
        self.data_root = Path(data_root)
        self._model = EsportsContextualV7Model()
        self._elo_cache: dict[str, float] = self._load_elo_ratings()

    @property
    def sport_name(self) -> str:
        return self.title

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        return (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL)

    def _load_elo_ratings(self) -> dict[str, float]:
        """Load ratings from versioned artifact if available."""
        artifact_path = self.data_root / "models" / f"{self.title}-tiered-elo-v6.json"
        if artifact_path.exists():
            try:
                data = json.loads(artifact_path.read_text(encoding="utf-8"))
                return {k.lower(): float(v) for k, v in data.get("ratings", {}).items()}
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[dict[str, Any]]:
        """Fetch slate of upcoming esports matches."""
        provided = kwargs.get("matches") or kwargs.get("games")
        if provided is not None:
            return list(provided)

        # In production/replay, matches can be supplied via kwargs or loaded from scheduled files
        return []

    def build_features(
        self, slate: list[dict[str, Any]], context: TimeContext
    ) -> list[EsportsContextFeatures]:
        """Construct Point-in-Time features strictly observable before decision_time_utc."""
        features: list[EsportsContextFeatures] = []
        for m in slate:
            team_a = str(m.get("team_a") or m.get("home_team") or "")
            team_b = str(m.get("team_b") or m.get("away_team") or "")
            elo_a = self._elo_cache.get(team_a.lower(), 1500.0)
            elo_b = self._elo_cache.get(team_b.lower(), 1500.0)
            elo_diff = elo_a - elo_b
            elo_prob_a = 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))

            feat = EsportsContextFeatures(
                game_title=self.title,
                team_a=team_a,
                team_b=team_b,
                elo_prob_a=elo_prob_a,
                map_name=m.get("map_name"),
                is_lan=bool(m.get("is_lan", True)),
                series_format=str(m.get("series_format", "Bo3")),
                side_a=str(m.get("side_a", "blue")),
                team_a_map_win_rate=float(m.get("team_a_map_win_rate", 0.50)),
                team_b_map_win_rate=float(m.get("team_b_map_win_rate", 0.50)),
                draft_tempo_advantage_a=float(m.get("draft_tempo_advantage_a", 0.0)),
                roster_continuity_a=float(m.get("roster_continuity_a", 1.0)),
                roster_continuity_b=float(m.get("roster_continuity_b", 1.0)),
            )
            features.append(feat)
        return features

    def generate_probabilities(self, features: Any, context: TimeContext) -> list[dict[str, Any]]:
        """Generate moneyline, spread, and total probabilities from contextual engine."""
        if not features:
            return []

        predictions: list[dict[str, Any]] = []
        for feat in features:
            forecast = self._model.forecast_match(feat)
            p_a = forecast.prob_a_wins
            p_b = forecast.prob_b_wins

            # Moneyline
            predictions.append(
                {
                    "event_id": f"{feat.team_a}_vs_{feat.team_b}",
                    "sport": self.title,
                    "market_type": "moneyline",
                    "line": 0.0,
                    "model_id": f"{self.title}-contextual-v7",
                    "probabilities": {"home": p_a, "away": p_b},
                    "feature_basis": "tiered_elo_plus_contextual",
                }
            )

            # For Bo3 matches, synthesize Map Spread (-1.5 / +1.5) and Map Total (O/U 2.5)
            if feat.series_format.upper() == "BO3":
                # Spread -1.5 for Team A means Team A sweeps 2-0: P(2-0) ~= p_a ^ 1.85
                p_sweep_a = max(0.01, min(0.99, p_a**1.85))
                p_sweep_b = max(0.01, min(0.99, p_b**1.85))
                predictions.append(
                    {
                        "event_id": f"{feat.team_a}_vs_{feat.team_b}",
                        "sport": self.title,
                        "market_type": "spread",
                        "line": -1.5,
                        "model_id": f"{self.title}-series-v7",
                        "probabilities": {"home": p_sweep_a, "away": 1.0 - p_sweep_a},
                        "feature_basis": "bo3_synthetic_spread",
                    }
                )

                # Over 2.5 maps means match goes to decider (2-1 either way) = 1 - (P(2-0) + P(0-2))
                p_decider = max(0.10, min(0.85, 1.0 - (p_sweep_a + p_sweep_b)))
                predictions.append(
                    {
                        "event_id": f"{feat.team_a}_vs_{feat.team_b}",
                        "sport": self.title,
                        "market_type": "total",
                        "line": 2.5,
                        "model_id": f"{self.title}-series-v7",
                        "probabilities": {"over": p_decider, "under": 1.0 - p_decider},
                        "feature_basis": "bo3_synthetic_total",
                    }
                )

        return predictions

    def grade_event(
        self,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Grade an esports match against completed game or map score."""
        home_score = box_score.get("home_score")
        away_score = box_score.get("away_score")

        if home_score is None or away_score is None:
            # Check for direct winner declaration in box score
            winner = box_score.get("winner")
            if winner:
                h_val = 1.0 if winner in ("home", "team_a", "1") else 0.0
                a_val = 1.0 if winner in ("away", "team_b", "2") else 0.0
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
            elif (h_val > a_val and sel in ("home", "1", "team_a")) or (
                a_val > h_val and sel in ("away", "2", "team_b")
            ):
                result = "win"
            else:
                result = "loss"

        elif m_type == "spread":
            margin = h_val - a_val
            eff = (margin + line) if sel in ("home", "1", "team_a") else (-margin + line)
            if abs(eff) < 1e-9:
                result = "push"
            elif eff > 0:
                result = "win"
            else:
                result = "loss"

        elif m_type == "total":
            total_maps = h_val + a_val
            diff = total_maps - line
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
            "settlement_source": f"{self.title}_sport_adapter",
        }
