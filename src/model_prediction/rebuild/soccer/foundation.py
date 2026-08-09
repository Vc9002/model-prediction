"""Offline-testable orchestration for raw-first soccer match observations."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from model_prediction.rebuild.providers.base import ProviderResult, ProviderStatus
from model_prediction.rebuild.providers.football_data import FootballDataProvider
from model_prediction.rebuild.providers.soccer_espn import ESPNSoccerProvider
from model_prediction.rebuild.providers.statsbomb_open import StatsBombOpenDataProvider

from .normalize import normalize_soccer_matches
from .store import SoccerNormalizedStore


class SoccerFoundation:
    def __init__(
        self,
        espn: ESPNSoccerProvider,
        normalized_root: str | Path,
        *,
        football_data: FootballDataProvider | None = None,
        statsbomb: StatsBombOpenDataProvider | None = None,
    ) -> None:
        self.espn = espn
        self.football_data = football_data
        self.statsbomb = statsbomb or StatsBombOpenDataProvider()
        self.store = SoccerNormalizedStore(normalized_root)

    def _persist(self, result: ProviderResult) -> int:
        if result.status is not ProviderStatus.AVAILABLE or result.frame is None or result.metadata is None:
            return 0
        normalized = normalize_soccer_matches(result.frame, result.metadata)
        self.store.write_matches(normalized)
        return normalized.height

    def collect_date(
        self,
        game_date: date,
        *,
        espn_leagues: tuple[str, ...] = ("eng.1",),
        football_data_competitions: tuple[str, ...] = (),
        force: bool = False,
    ) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        for league in espn_leagues:
            result = self.espn.current_schedule(game_date, league, force=force)
            reports.append(
                {
                    "provider": self.espn.provider_id,
                    "competition": league,
                    "status": result.status.value,
                    "reason": result.reason,
                    "rows_written": self._persist(result),
                }
            )
        for competition in football_data_competitions:
            if self.football_data is None:
                reports.append(
                    {
                        "provider": "football_data_v4",
                        "competition": competition,
                        "status": ProviderStatus.UNAVAILABLE.value,
                        "reason": "OPTIONAL_PROVIDER_NOT_CONFIGURED",
                        "rows_written": 0,
                    }
                )
                continue
            result = self.football_data.matches(competition, game_date, game_date, force=force)
            reports.append(
                {
                    "provider": self.football_data.provider_id,
                    "competition": competition,
                    "status": result.status.value,
                    "reason": result.reason,
                    "rows_written": self._persist(result),
                }
            )
        blocked = self.statsbomb.events(sport="soccer")
        reports.append(
            {
                "provider": self.statsbomb.provider_id,
                "competition": None,
                "status": blocked.status.value,
                "reason": blocked.reason,
                "rows_written": 0,
            }
        )
        return {"sport": "soccer", "date": game_date.isoformat(), "sources": reports}
