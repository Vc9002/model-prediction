"""B4: the v7/v9 market-blend hook shrinks toward ``spread_away_line`` as the HOME-margin line.

``spread_away_line`` is the line the cover formula ``normal_cdf(line, expected_margin, sd)`` uses, so
away covers iff home margin < line: it already is a home-margin line and must not be negated. The
blend weight is 0.0 in every shipped artifact, so these tests pin research-only behaviour.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v7 import NBAStructuralV7Engine
from model_prediction.models.nba_v9 import NBAStructuralV9Engine

TEAMS = ["Boston Celtics", "Denver Nuggets", "Miami Heat", "Utah Jazz", "Phoenix Suns", "Dallas Mavericks"]
# Far from any model margin so the blended mean can never coincide with the unblended one.
LINE = 25.0
ENGINES = [NBAStructuralV7Engine, NBAStructuralV9Engine]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _history() -> list[GameRecord]:
    rng = random.Random(7)
    games: list[GameRecord] = []
    start = datetime(2025, 11, 1, tzinfo=UTC)
    for day in range(60):
        order = TEAMS[:]
        rng.shuffle(order)
        for k in range(0, len(order), 2):
            games.append(
                GameRecord(
                    event_id=f"d{day}g{k}",
                    event_start_utc=_iso(start + timedelta(days=day, hours=k)),
                    league="NBA",
                    away_team=order[k],
                    home_team=order[k + 1],
                    away_score=int(rng.gauss(110, 10)),
                    home_score=int(rng.gauss(112, 10)),
                    season_type="regular-season",
                    season_year=2026,
                )
            )
    return games


def _upcoming(history: list[GameRecord], line: float | None) -> list[UpcomingGame]:
    tip = history[-1].start + timedelta(hours=20)
    return [
        UpcomingGame(
            event_id=f"up{i}",
            event_start_utc=_iso(tip + timedelta(minutes=i)),
            away_team=TEAMS[2 * i],
            home_team=TEAMS[2 * i + 1],
            spread_away_line=line,
            total_line=220.5,
        )
        for i in range(3)
    ]


def _spread_means(engine_cls: type, weight: float | None, line: float | None = LINE) -> dict[str, float]:
    history = _history()
    engine = engine_cls() if weight is None else engine_cls(market_blend_weight=weight)
    preds = engine.predict_games(history, _upcoming(history, line))
    return {p.event_id: p.inference_inputs["mean"] for p in preds if p.market_type == "spread"}


@pytest.mark.parametrize("engine_cls", ENGINES)
def test_full_weight_blend_lands_on_the_home_margin_line(engine_cls: type) -> None:
    means = _spread_means(engine_cls, 1.0)
    assert len(means) == 3
    for mean in means.values():
        assert mean == pytest.approx(LINE)


@pytest.mark.parametrize("engine_cls", ENGINES)
def test_partial_blend_moves_toward_the_line(engine_cls: type) -> None:
    base = _spread_means(engine_cls, 0.0)
    blended = _spread_means(engine_cls, 0.2)
    assert len(base) == len(blended) == 3
    for event_id, unblended in base.items():
        assert unblended != pytest.approx(LINE)
        assert unblended < blended[event_id] < LINE
        assert blended[event_id] == pytest.approx(0.8 * unblended + 0.2 * LINE)


@pytest.mark.parametrize("engine_cls", ENGINES)
@pytest.mark.parametrize("line", [LINE, None])
def test_zero_weight_is_bit_identical_to_the_default_engine(engine_cls: type, line: float | None) -> None:
    history = _history()
    upcoming = _upcoming(history, line)
    default = engine_cls().predict_games(history, upcoming)
    zero = engine_cls(market_blend_weight=0.0).predict_games(history, upcoming)
    assert len(default) == len(zero) > 0
    for a, b in zip(default, zero, strict=True):
        assert (a.event_id, a.market_type) == (b.event_id, b.market_type)
        assert a.probabilities == b.probabilities
        assert a.inference_inputs == b.inference_inputs
