from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypedDict

from ..domain import MarketType


class ArtifactNotTrainedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScoreSimulation:
    away_scores: Sequence[int]
    home_scores: Sequence[int]
    uncertainty: float
    model_version: str

    def __post_init__(self) -> None:
        if not self.away_scores or len(self.away_scores) != len(self.home_scores):
            raise ValueError("score simulation requires equal non-empty score samples")

    def probability(self, market_type: MarketType, selection: str, line: float | None = None) -> float:
        wins = pushes = 0
        for away, home in zip(self.away_scores, self.home_scores, strict=True):
            if market_type is MarketType.MONEYLINE:
                margin: float = home - away if selection == "home" else away - home
            elif market_type is MarketType.SPREAD:
                if line is None:
                    raise ValueError("spread requires a selection-relative line")
                margin = (home - away if selection == "home" else away - home) + line
            else:
                if line is None:
                    raise ValueError("total requires a line")
                total = away + home
                margin = total - line if selection == "over" else line - total
            wins += margin > 0
            pushes += margin == 0
        return (wins + 0.5 * pushes) / len(self.away_scores)


class ScoreModel(Protocol):
    @property
    def version(self) -> str: ...

    @property
    def required_features(self) -> tuple[str, ...]: ...

    def fit(self, rows: object) -> None: ...

    def predict_distribution(self, features: object) -> ScoreSimulation: ...


@dataclass(frozen=True)
class GamePrediction:
    """Unified per-market output every sport model produces.

    ``probabilities`` maps a selection ("away", "home", "over", "under",
    "draw", "btts_yes", ...) to the model's independent probability. Market
    prices never enter here — market isolation is enforced upstream.
    """

    event_id: str
    event_start_utc: str
    league: str
    away_team: str
    home_team: str
    market_type: MarketType | str
    line: float | None
    probabilities: dict[str, float]
    uncertainty: float
    model_version: str
    feature_basis: dict[str, object]
    rationale: str

    def probability(self, selection: str) -> float:
        return self.probabilities[selection.lower()]


class GamePredictionBase(TypedDict):
    """The fields of ``GamePrediction`` shared across a game's markets.

    Sport models build one of these per game, then spread it into each
    per-market ``GamePrediction(**base, market_type=..., ...)`` call.
    """

    event_id: str
    event_start_utc: str
    league: str
    away_team: str
    home_team: str
    uncertainty: float
    model_version: str
    feature_basis: dict[str, object]


class SportModel(Protocol):
    """Interface every unified sport model implements.

    ``history``/``matches`` is each sport's chronological training data
    (``Sequence[GameRecord]`` for basketball/soccer, raw match dicts for
    tennis -- no single concrete type fits every sport's data shape, hence
    ``object`` here); ``upcoming`` is the slate to predict, one
    sport-specific "UpcomingX" type per implementation.
    """

    @property
    def version(self) -> str: ...

    @property
    def sport(self) -> str: ...

    def predict_games(
        self, history: Sequence[object], upcoming: Sequence[object]
    ) -> list[GamePrediction]: ...
