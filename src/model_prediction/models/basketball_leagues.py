"""Independent, per-league basketball models and registry.

Enforces strict parameter and rating isolation across all basketball leagues:
  * NBA: 48 minutes, 24s shot clock, ~99 possessions, 12 min quarters
  * WNBA: 40 minutes, 24s shot clock, ~81 possessions, 10 min quarters
  * NCAAB (Men's College): 40 minutes, 30s shot clock, 20 min halves, high HCA (~3.6 pts)
  * WNCAAB (Women's College): 40 minutes, 30s shot clock, 10 min quarters
  * EuroLeague / European FIBA: 40 minutes, 24s shot clock, ~72 possessions
  * Domestic/International leagues: ACB, BBL, LNB, ABA, NBL, etc.

Guarantees zero parameter bleed: NBA games never update college/Euro ratings or baselines,
and vice versa.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..features.base import GameRecord
from .base import GamePrediction
from .basketball import BasketballModel, UpcomingGame


@dataclass(frozen=True)
class BasketballLeagueConfig:
    """League-specific physical constants and calibration parameters."""

    league_code: str
    model_version: str
    game_duration_minutes: int
    pace: float
    margin_sd: float
    total_sd: float
    home_court_points: float
    baseline_points_per_team: float
    elo_weight: float = 0.40
    trend_weight: float = 0.60
    rest_weight: float = 0.15
    trend_total_weight: float = 0.70
    team_total_weight: float = 0.20
    elo_k_factor: float = 20.0
    elo_margin_scale: float = 28.0


NBA_CONFIG = BasketballLeagueConfig(
    league_code="NBA",
    model_version="nba-spread-margin-v2",
    game_duration_minutes=48,
    pace=99.2,
    margin_sd=14.6,
    total_sd=19.2,
    home_court_points=1.8,
    baseline_points_per_team=114.2,
    elo_weight=0.65,
    trend_weight=0.65,
    rest_weight=0.30,
    trend_total_weight=0.85,
    team_total_weight=0.15,
)

WNBA_CONFIG = BasketballLeagueConfig(
    league_code="WNBA",
    model_version="wnba-elo-trend-v4",
    game_duration_minutes=40,
    pace=81.4,
    margin_sd=11.0,
    total_sd=15.0,
    home_court_points=2.8,
    baseline_points_per_team=82.5,
    elo_weight=0.45,
    trend_weight=0.55,
    rest_weight=0.10,
)

NCAAB_CONFIG = BasketballLeagueConfig(
    league_code="NCAAB",
    model_version="ncaab-tempo-v1",
    game_duration_minutes=40,
    pace=68.1,
    margin_sd=10.5,
    total_sd=14.0,
    home_court_points=3.6,  # Student sections, travel, court familiarity produce higher HCA
    baseline_points_per_team=71.4,
    elo_weight=0.50,
    trend_weight=0.50,
    rest_weight=0.15,
)

WNCAAB_CONFIG = BasketballLeagueConfig(
    league_code="WNCAAB",
    model_version="wncaab-tempo-v1",
    game_duration_minutes=40,
    pace=70.8,
    margin_sd=11.5,
    total_sd=15.0,
    home_court_points=3.2,
    baseline_points_per_team=68.2,
    elo_weight=0.50,
    trend_weight=0.50,
    rest_weight=0.10,
)

EUROLEAGUE_CONFIG = BasketballLeagueConfig(
    league_code="EUROLEAGUE",
    model_version="eurolg-tempo-v1",
    game_duration_minutes=40,
    pace=72.0,
    margin_sd=10.0,
    total_sd=14.5,
    home_court_points=3.2,  # Intense domestic/European fanbases
    baseline_points_per_team=80.1,
    elo_weight=0.40,
    trend_weight=0.60,
    rest_weight=0.15,
)

ACB_CONFIG = BasketballLeagueConfig(
    league_code="ACB",
    model_version="acb-tempo-v1",
    game_duration_minutes=40,
    pace=73.8,
    margin_sd=10.3,
    total_sd=14.8,
    home_court_points=3.0,
    baseline_points_per_team=82.8,
)

BBL_CONFIG = BasketballLeagueConfig(
    league_code="BBL",
    model_version="bbl-tempo-v1",
    game_duration_minutes=40,
    pace=74.6,
    margin_sd=10.6,
    total_sd=15.0,
    home_court_points=2.8,
    baseline_points_per_team=84.2,
)

LNB_CONFIG = BasketballLeagueConfig(
    league_code="LNB",
    model_version="lnb-tempo-v1",
    game_duration_minutes=40,
    pace=73.0,
    margin_sd=10.2,
    total_sd=14.5,
    home_court_points=2.9,
    baseline_points_per_team=81.0,
)

ABA_CONFIG = BasketballLeagueConfig(
    league_code="ABA",
    model_version="aba-tempo-v1",
    game_duration_minutes=40,
    pace=71.8,
    margin_sd=10.0,
    total_sd=14.2,
    home_court_points=3.3,
    baseline_points_per_team=79.5,
)

NBL_CONFIG = BasketballLeagueConfig(
    league_code="NBL",
    model_version="nbl-tempo-v1",
    game_duration_minutes=40,
    pace=78.2,
    margin_sd=10.8,
    total_sd=15.5,
    home_court_points=3.0,
    baseline_points_per_team=88.5,
)

_KNOWN_CONFIGS: dict[str, BasketballLeagueConfig] = {
    "NBA": NBA_CONFIG,
    "WNBA": WNBA_CONFIG,
    "NCAAB": NCAAB_CONFIG,
    "CBB": NCAAB_CONFIG,
    "MENS-COLLEGE-BASKETBALL": NCAAB_CONFIG,
    "WNCAAB": WNCAAB_CONFIG,
    "WCBB": WNCAAB_CONFIG,
    "WOMENS-COLLEGE-BASKETBALL": WNCAAB_CONFIG,
    "EUROLEAGUE": EUROLEAGUE_CONFIG,
    "EUROLG": EUROLEAGUE_CONFIG,
    "ACB": ACB_CONFIG,
    "BBL": BBL_CONFIG,
    "LNB": LNB_CONFIG,
    "ABA": ABA_CONFIG,
    "NBL": NBL_CONFIG,
}


def resolve_basketball_config(league: str) -> BasketballLeagueConfig:
    """Resolve league-specific config with sensible fallback."""
    norm = str(league or "").strip().upper()
    if norm in _KNOWN_CONFIGS:
        return _KNOWN_CONFIGS[norm]
    # Default international 40-minute configuration for uncatalogued leagues
    return BasketballLeagueConfig(
        league_code=norm or "BASKETBALL",
        model_version=f"{norm.lower() if norm else 'intl'}-tempo-v1",
        game_duration_minutes=40,
        pace=73.0,
        margin_sd=10.5,
        total_sd=14.5,
        home_court_points=3.0,
        baseline_points_per_team=80.0,
    )


class DedicatedBasketballLeagueModel:
    """Dedicated model for a specific basketball league."""

    def __init__(self, config: BasketballLeagueConfig) -> None:
        self.config = config
        self.league = config.league_code
        self.version = config.model_version
        self.sport = config.league_code.lower()
        self._underlying = BasketballModel(
            sport=self.sport,
            version=self.version,
            margin_sd=config.margin_sd,
            total_sd=config.total_sd,
            league=self.league,
            elo_weight=config.elo_weight,
            trend_weight=config.trend_weight,
            rest_weight=config.rest_weight,
            home_court_points=config.home_court_points,
            trend_total_weight=config.trend_total_weight,
            team_total_weight=config.team_total_weight,
        )

    def predict_games(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        """Predict games strictly using history from this league."""
        norm_lg = self.league.upper()
        # Filter history strictly to this league to prevent cross-league parameter distortion
        allowed_leagues = {norm_lg} if norm_lg not in ("NBA", "BASKETBALL") else {"NBA", "BASKETBALL"}
        isolated_history = [g for g in history if getattr(g, "league", "").strip().upper() in allowed_leagues]
        if (
            not isolated_history
            and history
            and all(not getattr(g, "league", "").strip() for g in history)
            and norm_lg in ("NBA", "BASKETBALL")
        ):
            isolated_history = list(history)

        return self._underlying.predict_games(isolated_history, upcoming)


class NBAModel(DedicatedBasketballLeagueModel):
    """Dedicated NBA Model (48-min, pace ~99, margin SD 12.0)."""

    def __init__(self) -> None:
        super().__init__(NBA_CONFIG)


class WNBAModel(DedicatedBasketballLeagueModel):
    """Dedicated WNBA Model (40-min, pace ~81, margin SD 11.0)."""

    def __init__(self) -> None:
        super().__init__(WNBA_CONFIG)


class NCAABModel(DedicatedBasketballLeagueModel):
    """Dedicated Men's College Basketball Model (40-min, pace ~68, high HCA)."""

    def __init__(self) -> None:
        super().__init__(NCAAB_CONFIG)


class WNCAABModel(DedicatedBasketballLeagueModel):
    """Dedicated Women's College Basketball Model (40-min, pace ~71)."""

    def __init__(self) -> None:
        super().__init__(WNCAAB_CONFIG)


class EuroLeagueModel(DedicatedBasketballLeagueModel):
    """Dedicated EuroLeague Basketball Model (40-min FIBA, hostile HCA)."""

    def __init__(self) -> None:
        super().__init__(EUROLEAGUE_CONFIG)


class BasketballLeagueRegistry:
    """Registry maintaining per-league match records and strictly isolated league models."""

    def __init__(self) -> None:
        self._models: dict[str, DedicatedBasketballLeagueModel] = {}
        self._history: dict[str, list[GameRecord]] = {}

    def get_model(self, league: str) -> DedicatedBasketballLeagueModel:
        """Get or instantiate the dedicated model for a basketball league."""
        norm = str(league or "").strip().upper()
        if norm not in self._models:
            cfg = resolve_basketball_config(norm)
            self._models[norm] = DedicatedBasketballLeagueModel(cfg)
        return self._models[norm]

    def record_game(self, game: GameRecord) -> None:
        """Record game strictly into its league partition."""
        lg = str(getattr(game, "league", "") or "BASKETBALL").strip().upper()
        self._history.setdefault(lg, []).append(game)

    def predict_slate(
        self,
        upcoming: Sequence[UpcomingGame],
        history: Sequence[GameRecord] | None = None,
    ) -> list[GamePrediction]:
        """Predict a multi-league basketball slate by dispatching each game strictly to its league model."""
        if history:
            for g in history:
                self.record_game(g)

        # Partition upcoming games by league
        by_league: dict[str, list[UpcomingGame]] = {}
        for game in upcoming:
            lg = str(getattr(game, "league", "") or "NBA").strip().upper()
            by_league.setdefault(lg, []).append(game)

        all_predictions: list[GamePrediction] = []
        for lg, games in by_league.items():
            model = self.get_model(lg)
            lg_history = self._history.get(lg, [])
            preds = model.predict_games(lg_history, games)
            all_predictions.extend(preds)

        return all_predictions


def get_basketball_model(league: str) -> DedicatedBasketballLeagueModel:
    """Factory returning the dedicated model for any basketball league."""
    norm = str(league or "").strip().upper()
    if norm in ("NBA",):
        return NBAModel()
    if norm in ("WNBA",):
        return WNBAModel()
    if norm in ("NCAAB", "CBB", "MENS-COLLEGE-BASKETBALL"):
        return NCAABModel()
    if norm in ("WNCAAB", "WCBB", "WOMENS-COLLEGE-BASKETBALL"):
        return WNCAABModel()
    if norm in ("EUROLEAGUE", "EUROLG"):
        return EuroLeagueModel()
    return DedicatedBasketballLeagueModel(resolve_basketball_config(norm))


def nba_model() -> NBAModel:
    return NBAModel()


def wnba_model() -> WNBAModel:
    return WNBAModel()


def ncaab_model() -> NCAABModel:
    return NCAABModel()


def wncaab_model() -> WNCAABModel:
    return WNCAABModel()


def euroleague_model() -> EuroLeagueModel:
    return EuroLeagueModel()
