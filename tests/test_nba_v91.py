"""NBA v9.1 research challenger: v9 parity with every switch off, and each fix in isolation."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v9 import NBAStructuralV9Engine
from model_prediction.models.nba_v91 import NBAStructuralV91Engine, _season_rolling_baseline

ROOT = Path(__file__).resolve().parent.parent
TEAMS = ["Boston Celtics", "Denver Nuggets", "Miami Heat", "Utah Jazz", "Phoenix Suns", "Dallas Mavericks"]
V9_ARTIFACT = json.loads((ROOT / "config/models/nba-spread-margin-v9.json").read_text(encoding="utf-8"))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _history(season_points: tuple[float, float] = (110.0, 110.0), seed: int = 7) -> list[GameRecord]:
    """Two short seasons (Nov-Mar), games every day so back-to-backs occur."""
    rng = random.Random(seed)
    games: list[GameRecord] = []
    for season, (start, mean_pts) in enumerate(
        (
            (datetime(2024, 11, 1, 0, tzinfo=UTC), season_points[0]),
            (datetime(2025, 11, 1, 0, tzinfo=UTC), season_points[1]),
        )
    ):
        for day in range(120):
            order = TEAMS[:]
            rng.shuffle(order)
            for k in range(0, len(order), 2):
                games.append(
                    GameRecord(
                        event_id=f"s{season}d{day}g{k}",
                        event_start_utc=_iso(start + timedelta(days=day, hours=k)),
                        league="NBA",
                        away_team=order[k],
                        home_team=order[k + 1],
                        away_score=int(rng.gauss(mean_pts, 10)),
                        home_score=int(rng.gauss(mean_pts + 2, 10)),
                        season_type="regular-season",
                        season_year=2025 + season,
                    )
                )
    return games


def _upcoming(
    history: list[GameRecord], line: float | None = 3.5, total: float | None = 220.5
) -> list[UpcomingGame]:
    tip = history[-1].start + timedelta(hours=20)
    return [
        UpcomingGame(
            event_id=f"up{i}",
            event_start_utc=_iso(tip + timedelta(minutes=i)),
            away_team=TEAMS[2 * i],
            home_team=TEAMS[2 * i + 1],
            spread_away_line=line,
            total_line=total,
        )
        for i in range(3)
    ]


def _by(preds: list, market: str) -> dict[str, object]:
    return {p.event_id: p for p in preds if p.market_type == market}


def test_all_switches_off_reproduces_v9_bit_for_bit() -> None:
    history = _history()
    upcoming = _upcoming(history)
    v9 = NBAStructuralV9Engine.from_artifact(V9_ARTIFACT).predict_games(history, upcoming)
    v91 = NBAStructuralV91Engine.from_artifact(V9_ARTIFACT).predict_games(history, upcoming)
    assert len(v9) == len(v91) == 9
    for a, b in zip(v9, v91, strict=True):
        assert (a.event_id, a.market_type) == (b.event_id, b.market_type)
        assert a.probabilities == b.probabilities
        assert a.inference_inputs == b.inference_inputs


def test_b4_blend_uses_spread_away_line_as_home_margin_line() -> None:
    history = _history()
    upcoming = _upcoming(history, line=6.5)
    params = {**V9_ARTIFACT, "market_blend_weight": 1.0}
    spread = _by(NBAStructuralV91Engine.from_artifact(params).predict_games(history, upcoming), "spread")
    for pred in spread.values():
        assert pred.inference_inputs["mean"] == pytest.approx(6.5)  # type: ignore[attr-defined]
        assert pred.probabilities["away"] == pytest.approx(0.5)  # type: ignore[attr-defined]
    # v9 used to negate the line (B4, inert at weight 0); fixed in 261121-xbn, so v9 now agrees.
    v9 = _by(NBAStructuralV9Engine.from_artifact(params).predict_games(history, upcoming), "spread")
    assert next(iter(v9.values())).inference_inputs["mean"] == pytest.approx(6.5)  # type: ignore[attr-defined]


def test_b5_baseline_tracks_current_season_scoring() -> None:
    history = _history(season_points=(100.0, 120.0))
    all_history_mean = sum(g.total for g in history) / (2 * len(history))
    rolling = _season_rolling_baseline(
        history, rolling_games=300, prior_games=30.0, offseason_gap_days=60.0, isolate_postseason=True
    )
    assert all_history_mean < 113.0
    assert rolling > 118.0
    assert rolling < sum(g.total for g in history[-300:]) / 600.0 + 0.01


def test_b5_opening_week_is_shrunk_toward_previous_season() -> None:
    history = _history(season_points=(100.0, 120.0))
    opening = [g for g in history if g.season_year == 2025] + [g for g in history if g.season_year == 2026][
        :3
    ]
    rolling = _season_rolling_baseline(
        opening, rolling_games=300, prior_games=30.0, offseason_gap_days=60.0, isolate_postseason=True
    )
    assert 100.0 < rolling < 106.0


def test_b6_fitted_fatigue_moves_margin_and_total_by_one_points_model() -> None:
    history = _history()
    upcoming = _upcoming(history)  # every team played the previous day: all b2b
    tip = history[-1].start + timedelta(hours=20)
    # Home (TEAMS[1]) idle for three days; away (TEAMS[0]) played the night before.
    rested = [
        g for g in history if TEAMS[1] not in (g.home_team, g.away_team) or g.start < tip - timedelta(days=3)
    ]
    rested.append(
        GameRecord(
            "extra", _iso(tip - timedelta(hours=22)), "NBA", TEAMS[5], TEAMS[0], 110, 112, "regular-season"
        )
    )
    base = {**V9_ARTIFACT, "fatigue_mode": "fitted", "overtime_risk_adjustment": False}
    tired = {**base, "b2b_offense_penalty": 2.0, "b2b_defense_penalty": 0.5}
    zero = NBAStructuralV91Engine.from_artifact(base).predict_games(rested, upcoming)
    pen = NBAStructuralV91Engine.from_artifact(tired).predict_games(rested, upcoming)
    z_basis = _by(zero, "moneyline")["up0"].feature_basis  # type: ignore[attr-defined]
    assert z_basis["away_b2b"] and not z_basis["home_b2b"]
    z_s, p_s = _by(zero, "spread")["up0"], _by(pen, "spread")["up0"]
    z_t, p_t = _by(zero, "total")["up0"], _by(pen, "total")["up0"]
    # Away tired: home margin rises by off + def, total moves by def - off.
    assert p_s.inference_inputs["mean"] - z_s.inference_inputs["mean"] == pytest.approx(2.5)  # type: ignore[attr-defined]
    assert p_t.inference_inputs["mean"] - z_t.inference_inputs["mean"] == pytest.approx(-1.5)  # type: ignore[attr-defined]


def test_b7_playoff_offset_only_for_postseason() -> None:
    history = _history()
    upcoming = _upcoming(history)
    engine = NBAStructuralV91Engine.from_artifact({**V9_ARTIFACT, "playoff_total_offset": -12.0})
    regular = _by(engine.predict_games(history, upcoming), "total")
    playoff = _by(engine.predict_games(history, upcoming, season_types={"up0": "post-season"}), "total")
    assert playoff["up0"].inference_inputs["mean"] - regular["up0"].inference_inputs["mean"] == pytest.approx(
        -12.0
    )  # type: ignore[attr-defined]
    assert playoff["up1"].inference_inputs == regular["up1"].inference_inputs  # type: ignore[attr-defined]


@pytest.mark.parametrize("name", ["nba-spread-margin-v9-1.json", "nba-total-margin-v9-1.json"])
def test_research_artifacts_load_and_are_not_served(name: str) -> None:
    artifact = json.loads((ROOT / "config/models/research" / name).read_text(encoding="utf-8"))
    engine = NBAStructuralV91Engine.from_artifact(artifact)
    assert engine.market_blend_weight == 0.0
    assert engine.rolling_baseline_games == 300
    production = (ROOT / "config/production.yaml").read_text(encoding="utf-8")
    assert "v9-1" not in production
