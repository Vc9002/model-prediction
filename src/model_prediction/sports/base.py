"""Base abstractions and registry for plug-and-play sport adapters."""

from __future__ import annotations

import importlib
from typing import Any, ClassVar, Protocol, runtime_checkable

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType


@runtime_checkable
class SportAdapter(Protocol):
    """Protocol for modular sport prediction and market valuation adapters."""

    @property
    def sport_name(self) -> str:
        """Normalized lowercase sport identifier (e.g. 'mlb', 'darts', 'hockey')."""
        ...

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        """Markets supported by this sport adapter."""
        ...

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[Any]:
        """Retrieve upcoming matches/games valid at context.decision_time_utc."""
        ...

    def build_features(self, slate: list[Any], context: TimeContext) -> Any:
        """Compute point-in-Time features for the games."""
        ...

    def generate_probabilities(self, features: Any, context: TimeContext) -> list[dict[str, Any]]:
        """Calculate fair win/spread/total probabilities."""
        ...

    def grade_event(
        self,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Grade a completed game given box score information."""
        ...


class SportRegistry:
    """Central registry of sport forecasting adapters with resilient auto-discovery."""

    _adapters: ClassVar[dict[str, SportAdapter]] = {}

    ADAPTER_FACTORIES: ClassVar[dict[str, tuple[str, str]]] = {
        "basketball": ("model_prediction.sports.basketball", "BasketballAdapter"),
        "cs2": ("model_prediction.sports.cs2", "CS2Adapter"),
        "darts": ("model_prediction.sports.darts", "DartsAdapter"),
        "dota2": ("model_prediction.sports.dota2", "Dota2Adapter"),
        "hockey": ("model_prediction.sports.hockey", "HockeyAdapter"),
        "kbo": ("model_prediction.sports.kbo", "KBOAdapter"),
        "lol": ("model_prediction.sports.lol", "LoLAdapter"),
        "mlb": ("model_prediction.sports.mlb", "MLBAdapter"),
        "nba": ("model_prediction.sports.nba", "NBAAdapter"),
        "ncaab": ("model_prediction.sports.ncaab", "NCAABAdapter"),
        "ncaaf": ("model_prediction.sports.ncaaf", "NCAAFAdapter"),
        "nfl": ("model_prediction.sports.nfl", "NFLAdapter"),
        "npb": ("model_prediction.sports.npb", "NPBAdapter"),
        "rainbow_six": ("model_prediction.sports.rainbow_six", "RainbowSixAdapter"),
        "soccer": ("model_prediction.sports.soccer", "SoccerAdapter"),
        "tennis": ("model_prediction.sports.tennis", "TennisAdapter"),
        "valorant": ("model_prediction.sports.valorant", "ValorantAdapter"),
        "wnba": ("model_prediction.sports.wnba", "WNBAAdapter"),
    }

    @classmethod
    def register(cls, adapter: SportAdapter) -> None:
        """Register a sport adapter instance."""
        key = adapter.sport_name.strip().casefold()
        cls._adapters[key] = adapter

    @classmethod
    def _ensure_loaded(cls, key: str) -> None:
        """Ensure adapter for key is instantiated in _adapters."""
        if key not in cls._adapters and key in cls.ADAPTER_FACTORIES:
            mod_name, cls_name = cls.ADAPTER_FACTORIES[key]
            try:
                mod = importlib.import_module(mod_name)
                adapter_cls = getattr(mod, cls_name, None)
                if adapter_cls is not None and key not in cls._adapters:
                    cls._adapters[key] = adapter_cls()
            except (ImportError, ModuleNotFoundError, AttributeError):
                pass

    @classmethod
    def get(cls, sport: str) -> SportAdapter | None:
        """Retrieve the adapter for a sport by name, auto-instantiating if not yet loaded."""
        key = str(sport).strip().casefold()
        if key not in cls._adapters:
            cls._ensure_loaded(key)
        return cls._adapters.get(key)

    @classmethod
    def has(cls, sport: str) -> bool:
        """Check if an adapter is available or registered for a sport."""
        key = str(sport).strip().casefold()
        if key not in cls._adapters:
            cls._ensure_loaded(key)
        return key in cls._adapters

    @classmethod
    def list_sports(cls) -> list[str]:
        """List all currently registered and known sport names."""
        for sport in cls.ADAPTER_FACTORIES:
            if sport not in cls._adapters:
                cls._ensure_loaded(sport)
        return sorted(cls._adapters.keys())

    @classmethod
    def clear(cls) -> None:
        """Clear registry for test isolation."""
        cls._adapters.clear()
