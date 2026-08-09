"""Free/open source provider contracts for the isolated rebuild.

Consolidated from five independently-evolved per-sport worktrees (mlb-v3,
wnba-v1, nfl-v1, soccer-v1, tennis-v1) into one shared package -- see the
`rebuild/free-first-providers-v1` PR description for the reconciliation
notes on where each file's "canonical" version came from and what changed.
"""

from .base import (
    DataUseContext,
    ProviderResult,
    ProviderStatus,
    SourceGrade,
    SourceResponseMetadata,
    SportsDataProvider,
    assert_economic_use_allowed,
    assert_frame_use_allowed,
)
from .cache import CachedResponse, ProviderRawCache, cache_key
from .config import ProviderPolicy, RebuildSourcesConfig, load_rebuild_sources_config
from .football_data import FOOTBALL_DATA_RIGHTS, FootballDataProvider
from .http import HttpFetch, HttpProviderClient, RetryPolicy
from .mlb_stats import MLB_STATS_RIGHTS, MLBStatsProvider
from .nflverse import NFLVERSE_RELEASE_ASSETS, NFLVerseAsset, NFLVerseProvider
from .open_meteo import OPEN_METEO_RIGHTS, OpenMeteoForecastProvider
from .polymarket import POLYMARKET_RIGHTS, PolymarketProvider
from .rights import SourceRightsProfile
from .soccer_espn import ESPN_SOCCER_RIGHTS, ESPNSoccerProvider
from .sportsdataverse import SPORTSDATAVERSE_RIGHTS, SportsDataverseProvider
from .statcast import STATCAST_RIGHTS, StatcastProvider
from .statsbomb import STATSBOMB_OPEN_RIGHTS, StatsBombOpenDataProvider
from .tennis_espn import TENNIS_ESPN_RIGHTS, ESPNTennisProvider
from .tennis_mylife import TENNIS_MYLIFE_RIGHTS, TennisMyLifeProvider

__all__ = [
    "ESPN_SOCCER_RIGHTS",
    "FOOTBALL_DATA_RIGHTS",
    "MLB_STATS_RIGHTS",
    "NFLVERSE_RELEASE_ASSETS",
    "OPEN_METEO_RIGHTS",
    "POLYMARKET_RIGHTS",
    "SPORTSDATAVERSE_RIGHTS",
    "STATCAST_RIGHTS",
    "STATSBOMB_OPEN_RIGHTS",
    "TENNIS_ESPN_RIGHTS",
    "TENNIS_MYLIFE_RIGHTS",
    "CachedResponse",
    "DataUseContext",
    "ESPNSoccerProvider",
    "ESPNTennisProvider",
    "FootballDataProvider",
    "HttpFetch",
    "HttpProviderClient",
    "MLBStatsProvider",
    "NFLVerseAsset",
    "NFLVerseProvider",
    "OpenMeteoForecastProvider",
    "PolymarketProvider",
    "ProviderPolicy",
    "ProviderRawCache",
    "ProviderResult",
    "ProviderStatus",
    "RebuildSourcesConfig",
    "RetryPolicy",
    "SourceGrade",
    "SourceResponseMetadata",
    "SourceRightsProfile",
    "SportsDataProvider",
    "SportsDataverseProvider",
    "StatcastProvider",
    "StatsBombOpenDataProvider",
    "TennisMyLifeProvider",
    "assert_economic_use_allowed",
    "assert_frame_use_allowed",
    "cache_key",
    "load_rebuild_sources_config",
]
