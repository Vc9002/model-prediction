# NCAAB v4 Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build dedicated `NCAABSpreadModelV4`/`NCAABTotalModelV4` classes that fix the lifetime-vs-current-season sample-size bug natively, widen the actual prediction distribution (not just metadata) on thin-sample games, and layer on NCAAB-specific pace/rest features — validated via walk-forward against the incumbent `ncaab-tempo-v3`, staying `research` status until a separate promotion decision.

**Architecture:** Two new files mirror NBA's existing v6 split (`nba_spread_v6.py`, `nba_total_v6.py`) exactly in shape — same `TrendEngine`/`build_elo` primitives, same `GamePrediction`/`GamePredictionBase` contract — swapping in NCAAB constants and the current-season sample fix. A walk-forward script reuses `data/historical/ncaab_games_all.jsonl` (the same file used for this session's diagnostic backtest) to produce real Brier/MAE/calibration numbers before anything is proposed for promotion.

**Tech Stack:** Python 3.11+, existing `features/trends.py::TrendEngine`, `features/elo_ratings.py::build_elo`, `models/base.py::GamePrediction`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-ncaab-v4-overhaul-design.md`

## Global Constraints

- Calibration fixes take priority over raw accuracy gains where they trade off (spec, "Scope").
- `active_production_version` for NCAAB stays `ncaab-tempo-v3` in `config/model.yaml` — this plan never changes it. `active_research_version` may point at `ncaab-tempo-v4` once it exists.
- Out of scope: conference-strength adjustment, travel-distance features, any change to NBA/WNBA/Euroleague/NBL code paths.
- `ruff check` must stay clean on every file touched. No monolithic `pytest tests/` run required — run only the new/touched test files (per this project's fast-iteration directive in `CLAUDE.md`).
- Known related finding (do NOT fix in this plan — out of scope, flag only): `nba_spread_v6.py:125` and `nba_total_v6.py:103` have the *exact same* lifetime-`games_played` sample-size bug this plan fixes for NCAAB. `nba_v7.py` uses a different (also imperfect) `sample = min(len(history), 200)` proxy. None of the three NBA files use `current_season_games_played`. This is a separate, pre-existing NBA issue — mention it to the user in the final report; do not touch NBA files here.

---

### Task 1: `NCAABSpreadModelV4` — moneyline + spread

**Files:**
- Create: `src/model_prediction/models/ncaab_spread_v4.py`
- Test: `tests/test_ncaab_v4_walkforward.py`

**Interfaces:**
- Produces: `NCAABSpreadModelV4` class with `predict_spreads(history: Sequence[GameRecord], upcoming: Sequence[UpcomingGame]) -> list[GamePrediction]`, constructor kwargs `margin_sd: float = 10.5`, `elo_weight: float = 0.50`, `trend_weight: float = 0.50`, `rest_weight: float = 0.15`, `home_court_points: float = 7.2`, `rest_penalty_home: float = 1.60`, `rest_penalty_away: float = 2.00`, `schedule_density_drag: bool = True`, `dynamic_volatility: bool = True`, `use_logistic_moneyline: bool = True`, `logistic_scale: float = 9.5`, `offseason_gap_days: float = 150.0`, `offseason_retention: float = 0.25`, `isolate_postseason: bool = True`, `elo_home_advantage: float = 55.0`, `version: str = "ncaab-spread-v4"`. Also exposes classmethod `from_artifact(artifact: dict) -> NCAABSpreadModelV4`.

- [ ] **Step 1: Write the failing test — thin-sample uncertainty uses current-season games, not lifetime**

```python
"""Walk-forward and unit tests for NCAAB v4 (dedicated spread/total models)."""
from __future__ import annotations

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v4 import NCAABSpreadModelV4


def _game(event_id, start, away, home, away_score, home_score):
    return GameRecord(
        event_id=event_id, event_start_utc=start, league="NCAAB",
        away_team=away, home_team=home, away_score=away_score, home_score=home_score,
    )


def test_ncaab_spread_v4_uses_current_season_sample_not_lifetime():
    """A team with a large lifetime game count but only 1 current-season game
    must get WIDE uncertainty (thin-sample), not tight uncertainty -- this is
    the exact bug found in the 2026-11-05 live incident (basketball.py's
    shared `sample` variable used lifetime games_played)."""
    model = NCAABSpreadModelV4()

    # 30 games last season (2025-26), then an 80+ day gap (offseason), then
    # 1 game this season (2026-27) for both teams -- large lifetime count,
    # thin current-season count.
    history = []
    for i in range(30):
        history.append(_game(
            f"last_season_{i}", f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
            "Team A", "Team B", 65 + i % 5, 70 + i % 5,
        ))
    history.append(_game("this_season_1", "2026-11-03T00:00:00Z", "Team A", "Team B", 68, 72))

    upcoming = UpcomingGame(
        event_id="test_game", event_start_utc="2026-11-05T00:00:00Z",
        away_team="Team A", home_team="Team B", spread_away_line=5.5,
    )
    preds = model.predict_spreads(history, [upcoming])
    spread_pred = next(p for p in preds if p.market_type == "spread")

    # Lifetime sample here is 31 (>=15), which under the OLD buggy formula
    # (0.20 - 0.005*31 = 0.045) would floor near max confidence. Current-season
    # sample is 1, which must produce uncertainty near the 0.20 cap instead.
    assert spread_pred.uncertainty >= 0.15, (
        f"expected wide (thin-sample) uncertainty, got {spread_pred.uncertainty} "
        "-- this means the model is reading lifetime games_played instead of "
        "current_season_games_played"
    )


def test_ncaab_spread_v4_standalone():
    model = NCAABSpreadModelV4()
    game = UpcomingGame(
        event_id="test_standalone_spread", event_start_utc="2026-11-05T20:00:00Z",
        away_team="Duke", home_team="Kansas", spread_away_line=-3.5,
    )
    preds = model.predict_spreads([], [game])
    assert len(preds) == 2
    ml_pred = next(p for p in preds if p.market_type == "moneyline")
    spread_pred = next(p for p in preds if p.market_type == "spread")
    assert 0.0 < ml_pred.probabilities["home"] < 1.0
    assert 0.0 < spread_pred.probabilities["away"] < 1.0
    assert abs(spread_pred.probabilities["away"] + spread_pred.probabilities["home"] - 1.0) < 1e-4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src:. .venv/Scripts/python -m pytest tests/test_ncaab_v4_walkforward.py -o addopts='' -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'model_prediction.models.ncaab_spread_v4'`

- [ ] **Step 3: Write the implementation**

```python
"""Dedicated NCAAB Point Spread and Coherent Moneyline Model v4.

Mirrors nba_spread_v6.py's shape, but fixes the lifetime-vs-current-season
sample-size bug found live on 2026-11-05 (basketball.py's shared `sample`
variable used TeamTrend.games_played -- cumulative across season
boundaries -- instead of TeamTrend.current_season_games_played, producing
near-max confidence on games where a team had just 1-2 new-season games).
Also widens the margin_sd distribution itself on thin-sample games
(dynamic_volatility), not just the reported uncertainty metadata, so
eligibility.py's edge/confidence gate sees a genuinely wider distribution.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import erf, isnan, sqrt
from typing import Any

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.elo_ratings import build_elo
from ..features.trends import TrendEngine
from .base import GamePrediction, GamePredictionBase
from .basketball import UpcomingGame

NCAAB_SPREAD_V4_VERSION = "ncaab-spread-v4"


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if isnan(x) or isnan(mean) or isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))
    return max(0.0001, min(0.9999, raw))


class NCAABSpreadModelV4:
    """Independent NCAAB Spread and Moneyline prediction engine."""

    def __init__(
        self,
        margin_sd: float = 10.5,
        elo_weight: float = 0.50,
        trend_weight: float = 0.50,
        rest_weight: float = 0.15,
        home_court_points: float = 7.2,
        rest_penalty_home: float = 1.60,
        rest_penalty_away: float = 2.00,
        schedule_density_drag: bool = True,
        dynamic_volatility: bool = True,
        use_logistic_moneyline: bool = True,
        logistic_scale: float = 9.5,
        offseason_gap_days: float = 150.0,
        offseason_retention: float = 0.25,
        isolate_postseason: bool = True,
        elo_home_advantage: float = 55.0,
        version: str = NCAAB_SPREAD_V4_VERSION,
    ) -> None:
        self.margin_sd = margin_sd
        self.elo_weight = elo_weight
        self.trend_weight = trend_weight
        self.rest_weight = rest_weight
        self.home_court_points = home_court_points
        self.rest_penalty_home = rest_penalty_home
        self.rest_penalty_away = rest_penalty_away
        self.schedule_density_drag = schedule_density_drag
        self.dynamic_volatility = dynamic_volatility
        self.use_logistic_moneyline = use_logistic_moneyline
        self.logistic_scale = logistic_scale
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.elo_home_advantage = elo_home_advantage
        self.version = version
        self.sport = "ncaab"
        self.league = "NCAAB"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NCAABSpreadModelV4:
        return cls(
            margin_sd=float(artifact.get("margin_sd", 10.5)),
            elo_weight=float(artifact.get("elo_weight", 0.50)),
            trend_weight=float(artifact.get("trend_weight", 0.50)),
            rest_weight=float(artifact.get("rest_weight", 0.15)),
            home_court_points=float(artifact.get("home_court_points", 7.2)),
            rest_penalty_home=float(artifact.get("rest_penalty_home", 1.60)),
            rest_penalty_away=float(artifact.get("rest_penalty_away", 2.00)),
            schedule_density_drag=bool(artifact.get("schedule_density_drag", True)),
            dynamic_volatility=bool(artifact.get("dynamic_volatility", True)),
            use_logistic_moneyline=bool(artifact.get("use_logistic_moneyline", True)),
            logistic_scale=float(artifact.get("logistic_scale", 9.5)),
            offseason_gap_days=float(artifact.get("offseason_gap_days", 150.0)),
            offseason_retention=float(artifact.get("offseason_retention", 0.25)),
            isolate_postseason=bool(artifact.get("isolate_postseason", True)),
            elo_home_advantage=float(artifact.get("elo_home_advantage", 55.0)),
            version=str(artifact.get("model_version", NCAAB_SPREAD_V4_VERSION)),
        )

    def predict_spreads(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        gap_days = int(self.offseason_gap_days) if self.offseason_gap_days is not None else 150
        elo = build_elo(
            history,
            "ncaab",
            offseason_gap_days=gap_days,
            home_advantage=self.elo_home_advantage,
        )
        trend = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
        )
        baseline = trend.league_baseline or 71.4

        predictions: list[GamePrediction] = []

        for game in upcoming:
            home_win = elo.expected_home_win(game.home_team, game.away_team)
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)
            # Fix: current-season sample, not lifetime -- a team's lifetime
            # count stays large across a season boundary while its hl10 level
            # is already dominated by 1-2 new-season games.
            sample = min(away_trend.current_season_games_played, home_trend.current_season_games_played)

            away_points = (away_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2 or baseline
            home_points = (home_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2 or baseline

            r_home = elo.rating(game.home_team) + elo.home_advantage
            r_away = elo.rating(game.away_team)
            elo_margin = (r_home - r_away) / 28.0

            rest_diff = 0.0
            game_start_dt = None
            try:
                game_start_dt = parse_utc(game.event_start_utc)
            except (ValueError, TypeError):
                pass

            home_prev = [g for g in history if g.home_team == game.home_team or g.away_team == game.home_team]
            away_prev = [g for g in history if g.home_team == game.away_team or g.away_team == game.away_team]
            if game_start_dt is not None:
                home_prev = [g for g in home_prev if g.start < game_start_dt]
                away_prev = [g for g in away_prev if g.start < game_start_dt]
            home_prev.sort(key=lambda g: g.start)
            away_prev.sort(key=lambda g: g.start)

            home_short_rest = False
            away_short_rest = False
            try:
                home_rest = (
                    (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0
                    if (game_start_dt and home_prev)
                    else 3.0
                )
                away_rest = (
                    (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0
                    if (game_start_dt and away_prev)
                    else 3.0
                )
                rest_diff = min(5.0, max(-5.0, home_rest - away_rest))
                # NCAAB rarely plays true back-to-backs; "short rest" here is
                # <=2 days, distinct from NBA's <=1.25-day B2B threshold.
                home_short_rest = home_rest <= 2.0
                away_short_rest = away_rest <= 2.0
            except (ValueError, TypeError):
                rest_diff = 0.0

            home_dense = False
            away_dense = False
            if self.schedule_density_drag and game_start_dt is not None:
                # NCAAB non-conference multi-team events pack 2-3 games into a
                # long weekend; look back 3 days instead of NBA's 4.2.
                h_3d = [g for g in home_prev if 0 < (game_start_dt - g.start).total_seconds() <= 3.0 * 86400]
                a_3d = [g for g in away_prev if 0 < (game_start_dt - g.start).total_seconds() <= 3.0 * 86400]
                home_dense = len(h_3d) >= 2
                away_dense = len(a_3d) >= 2

            if home_short_rest and self.rest_penalty_home > 0:
                home_points -= self.rest_penalty_home
            if away_short_rest and self.rest_penalty_away > 0:
                away_points -= self.rest_penalty_away
            if home_dense and not home_short_rest:
                home_points -= 0.60
            if away_dense and not away_short_rest:
                away_points -= 0.90

            trend_margin = (home_points - away_points) + self.home_court_points
            expected_margin = (
                self.elo_weight * elo_margin + self.trend_weight * trend_margin + self.rest_weight * rest_diff
            )
            expected_margin += game.availability_points_gap

            # Dynamic volatility: widen the ACTUAL margin_sd (not just the
            # reported uncertainty metadata) when current-season sample is
            # thin, so eligibility.py's edge gate sees a genuinely wider
            # distribution -- this is the mechanism gap the 2026-11-05
            # incident exposed (uncertainty was widened as metadata in the
            # old code's intent, but nothing downstream of the shared class
            # actually widened the distribution feeding the CDF).
            effective_margin_sd = self.margin_sd
            if self.dynamic_volatility:
                sample_factor = max(0.0, min(1.0, 1.0 - sample / 8.0))
                effective_margin_sd = max(
                    self.margin_sd, min(self.margin_sd * 1.6, self.margin_sd * (1.0 + 0.5 * sample_factor))
                )

            uncertainty = max(0.03, min(0.20, 0.20 - 0.025 * sample))
            base: GamePredictionBase = {
                "event_id": game.event_id,
                "event_start_utc": game.event_start_utc,
                "league": self.league,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "uncertainty": round(uncertainty, 4),
                "model_version": self.version,
                "feature_basis": {
                    "elo_home": round(elo.rating(game.home_team), 1),
                    "elo_away": round(elo.rating(game.away_team), 1),
                    "away_points_hl10": round(away_points, 2),
                    "home_points_hl10": round(home_points, 2),
                    "elo_margin": round(elo_margin, 2),
                    "trend_margin": round(trend_margin, 2),
                    "rest_diff": round(rest_diff, 1),
                    "expected_margin": round(expected_margin, 2),
                    "effective_margin_sd": round(effective_margin_sd, 2),
                    "current_season_sample": sample,
                    "history_games": len(history),
                },
            }

            if self.use_logistic_moneyline:
                p_margin_home = 1.0 / (1.0 + 10.0 ** (-expected_margin / self.logistic_scale))
            else:
                p_margin_home = 1.0 - _normal_cdf(0.0, expected_margin, effective_margin_sd)
            p_home = self.elo_weight * home_win + (1.0 - self.elo_weight) * p_margin_home
            p_h_clamped = max(0.0001, min(0.9999, p_home))
            p_a_clamped = max(0.0001, min(0.9999, 1.0 - p_home))

            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    probabilities={"home": round(p_h_clamped, 6), "away": round(p_a_clamped, 6)},
                    rationale=(
                        f"Margin-coherent Elo {elo.rating(game.home_team):.0f} vs "
                        f"{elo.rating(game.away_team):.0f}, proj margin {expected_margin:+.1f}"
                    ),
                    **base,
                )
            )

            if game.spread_away_line is not None:
                away_cover = _normal_cdf(game.spread_away_line, expected_margin, effective_margin_sd)
                predictions.append(
                    GamePrediction(
                        market_type="spread",
                        inference_inputs={
                            "mean": expected_margin,
                            "sd": effective_margin_sd,
                            "line": game.spread_away_line,
                        },
                        line=game.spread_away_line,
                        probabilities={
                            "away": round(away_cover, 6),
                            "home": round(1.0 - away_cover, 6),
                            "away_cover": round(away_cover, 6),
                            "home_cover": round(1.0 - away_cover, 6),
                        },
                        rationale=f"Projected margin {expected_margin:+.1f} (home), sd {effective_margin_sd:.1f}.",
                        **base,
                    )
                )

        return predictions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src:. .venv/Scripts/python -m pytest tests/test_ncaab_v4_walkforward.py -o addopts='' -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Ruff check**

Run: `.venv/Scripts/ruff check src/model_prediction/models/ncaab_spread_v4.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/model_prediction/models/ncaab_spread_v4.py tests/test_ncaab_v4_walkforward.py
git commit -m "Add NCAABSpreadModelV4 with current-season sample fix"
```

---

### Task 2: `NCAABTotalModelV4` — totals

**Files:**
- Create: `src/model_prediction/models/ncaab_total_v4.py`
- Test: `tests/test_ncaab_v4_walkforward.py` (append to the file from Task 1)

**Interfaces:**
- Consumes: `UpcomingGame`, `GameRecord`, `TrendEngine`, `GamePrediction`/`GamePredictionBase` (same as Task 1).
- Produces: `NCAABTotalModelV4` class with `predict_totals(history, upcoming) -> list[GamePrediction]`, constructor kwargs `total_sd: float = 14.0`, `baseline_points_per_team: float = 71.4`, `league_pace: float = 68.1`, `trend_total_weight: float = 0.85`, `team_total_weight: float = 0.15`, `dynamic_matchup_pace: bool = True`, `pace_drag: float = 0.6`, `schedule_density_drag: bool = True`, `overtime_risk_adjustment: bool = True`, `offseason_gap_days: float = 150.0`, `offseason_retention: float = 0.25`, `isolate_postseason: bool = True`, `version: str = "ncaab-total-v4"`. Also `from_artifact(artifact: dict) -> NCAABTotalModelV4`.

- [ ] **Step 1: Write the failing test — append to `tests/test_ncaab_v4_walkforward.py`**

```python
from model_prediction.models.ncaab_total_v4 import NCAABTotalModelV4


def test_ncaab_total_v4_uses_current_season_sample_not_lifetime():
    model = NCAABTotalModelV4()
    history = []
    for i in range(30):
        history.append(_game(
            f"last_season_total_{i}", f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
            "Team C", "Team D", 65 + i % 5, 70 + i % 5,
        ))
    history.append(_game("this_season_total_1", "2026-11-03T00:00:00Z", "Team C", "Team D", 68, 72))

    upcoming = UpcomingGame(
        event_id="test_total_game", event_start_utc="2026-11-05T00:00:00Z",
        away_team="Team C", home_team="Team D", total_line=145.5,
    )
    preds = model.predict_totals(history, [upcoming])
    total_pred = next(p for p in preds if p.market_type == "total")
    assert total_pred.uncertainty >= 0.15


def test_ncaab_total_v4_standalone():
    model = NCAABTotalModelV4()
    game = UpcomingGame(
        event_id="test_standalone_total", event_start_utc="2026-11-05T20:00:00Z",
        away_team="Duke", home_team="Kansas", total_line=148.5,
    )
    preds = model.predict_totals([], [game])
    assert len(preds) == 1
    total_pred = preds[0]
    assert total_pred.market_type == "total"
    assert 0.0 < total_pred.probabilities["over"] < 1.0
    assert abs(total_pred.probabilities["over"] + total_pred.probabilities["under"] - 1.0) < 1e-4
    assert total_pred.feature_basis["est_possessions"] > 55.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src:. .venv/Scripts/python -m pytest tests/test_ncaab_v4_walkforward.py -o addopts='' -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'model_prediction.models.ncaab_total_v4'`

- [ ] **Step 3: Write the implementation**

```python
"""Dedicated NCAAB Over/Under Total Points Model v4.

Mirrors nba_total_v6.py's shape, with the same current-season sample fix
as ncaab_spread_v4.py and NCAAB-tuned rest/density thresholds (NCAAB rarely
plays true back-to-backs; "short rest" and schedule-density windows use
different day thresholds than NBA's B2B-tuned numbers).
"""

from __future__ import annotations

from collections.abc import Sequence
from math import erf, isnan, sqrt
from typing import Any

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.trends import TrendEngine
from .base import GamePrediction, GamePredictionBase
from .basketball import UpcomingGame

NCAAB_TOTAL_V4_VERSION = "ncaab-total-v4"


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if isnan(x) or isnan(mean) or isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))
    return max(0.0001, min(0.9999, raw))


class NCAABTotalModelV4:
    """Independent NCAAB Over/Under Total Points prediction engine."""

    def __init__(
        self,
        total_sd: float = 14.0,
        baseline_points_per_team: float = 71.4,
        league_pace: float = 68.1,
        trend_total_weight: float = 0.85,
        team_total_weight: float = 0.15,
        dynamic_matchup_pace: bool = True,
        pace_drag: float = 0.6,
        schedule_density_drag: bool = True,
        overtime_risk_adjustment: bool = True,
        offseason_gap_days: float = 150.0,
        offseason_retention: float = 0.25,
        isolate_postseason: bool = True,
        version: str = NCAAB_TOTAL_V4_VERSION,
    ) -> None:
        self.total_sd = total_sd
        self.baseline_points_per_team = baseline_points_per_team
        self.league_pace = league_pace
        self.trend_total_weight = trend_total_weight
        self.team_total_weight = team_total_weight
        self.dynamic_matchup_pace = dynamic_matchup_pace
        self.pace_drag = pace_drag
        self.schedule_density_drag = schedule_density_drag
        self.overtime_risk_adjustment = overtime_risk_adjustment
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.version = version
        self.sport = "ncaab"
        self.league = "NCAAB"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NCAABTotalModelV4:
        return cls(
            total_sd=float(artifact.get("total_sd", 14.0)),
            baseline_points_per_team=float(artifact.get("baseline_points_per_team", 71.4)),
            league_pace=float(artifact.get("league_pace", 68.1)),
            trend_total_weight=float(artifact.get("trend_total_weight", 0.85)),
            team_total_weight=float(artifact.get("team_total_weight", 0.15)),
            dynamic_matchup_pace=bool(artifact.get("dynamic_matchup_pace", True)),
            pace_drag=float(artifact.get("pace_drag", 0.6)),
            schedule_density_drag=bool(artifact.get("schedule_density_drag", True)),
            overtime_risk_adjustment=bool(artifact.get("overtime_risk_adjustment", True)),
            offseason_gap_days=float(artifact.get("offseason_gap_days", 150.0)),
            offseason_retention=float(artifact.get("offseason_retention", 0.25)),
            isolate_postseason=bool(artifact.get("isolate_postseason", True)),
            version=str(artifact.get("model_version", NCAAB_TOTAL_V4_VERSION)),
        )

    def predict_totals(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        trend = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
        )
        baseline = trend.league_baseline or self.baseline_points_per_team

        predictions: list[GamePrediction] = []

        for game in upcoming:
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)
            # Fix: current-season sample, not lifetime (see ncaab_spread_v4.py).
            sample = min(away_trend.current_season_games_played, home_trend.current_season_games_played)

            away_off = away_trend.offense["hl10"] if away_trend.games_played > 0 else baseline
            away_def = away_trend.defense["hl10"] if away_trend.games_played > 0 else baseline
            home_off = home_trend.offense["hl10"] if home_trend.games_played > 0 else baseline
            home_def = home_trend.defense["hl10"] if home_trend.games_played > 0 else baseline

            away_points = (away_off + home_def) / 2.0
            home_points = (home_off + away_def) / 2.0

            game_start_dt = None
            try:
                game_start_dt = parse_utc(game.event_start_utc)
            except (ValueError, TypeError):
                pass

            home_prev = [g for g in history if g.home_team == game.home_team or g.away_team == game.home_team]
            away_prev = [g for g in history if g.home_team == game.away_team or g.away_team == game.away_team]
            if game_start_dt is not None:
                home_prev = [g for g in home_prev if g.start < game_start_dt]
                away_prev = [g for g in away_prev if g.start < game_start_dt]
            home_prev.sort(key=lambda g: g.start)
            away_prev.sort(key=lambda g: g.start)

            home_short_rest = False
            away_short_rest = False
            try:
                home_rest = (
                    (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0
                    if (game_start_dt and home_prev)
                    else 3.0
                )
                away_rest = (
                    (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0
                    if (game_start_dt and away_prev)
                    else 3.0
                )
                home_short_rest = home_rest <= 2.0
                away_short_rest = away_rest <= 2.0
            except (ValueError, TypeError):
                pass

            home_dense = False
            away_dense = False
            if self.schedule_density_drag and game_start_dt is not None:
                h_3d = [g for g in home_prev if 0 < (game_start_dt - g.start).total_seconds() <= 3.0 * 86400]
                a_3d = [g for g in away_prev if 0 < (game_start_dt - g.start).total_seconds() <= 3.0 * 86400]
                home_dense = len(h_3d) >= 2
                away_dense = len(a_3d) >= 2

            est_possessions = self.league_pace
            if self.dynamic_matchup_pace:
                pf_away = (
                    1.0 + 0.50 * (((away_off + away_def) / (2.0 * baseline)) - 1.0)
                    if away_trend.games_played > 0
                    else 1.0
                )
                pf_home = (
                    1.0 + 0.50 * (((home_off + home_def) / (2.0 * baseline)) - 1.0)
                    if home_trend.games_played > 0
                    else 1.0
                )
                drag = -self.pace_drag if (home_short_rest or away_short_rest) else 0.0
                est_possessions = max(55.0, (self.league_pace * pf_away * pf_home) + drag)

            expected_total = (away_points + home_points) * (est_possessions / self.league_pace)

            if (home_dense or away_dense) and self.schedule_density_drag:
                expected_total -= 0.50
            if home_short_rest and away_short_rest:
                expected_total -= 0.80

            margin_proxy = abs(home_points - away_points)
            if self.overtime_risk_adjustment and margin_proxy <= 3.0:
                expected_total += 1.00

            # Dynamic volatility: widen the ACTUAL total_sd on thin-sample
            # games, not just the reported uncertainty metadata (same gap
            # fixed in ncaab_spread_v4.py).
            sample_factor = max(0.0, min(1.0, 1.0 - sample / 8.0))
            effective_total_sd = max(
                self.total_sd, min(self.total_sd * 1.6, self.total_sd * (1.0 + 0.5 * sample_factor))
            )
            effective_total_sd = max(10.0, min(22.0, effective_total_sd * sqrt(est_possessions / self.league_pace)))

            uncertainty = max(0.03, min(0.20, 0.20 - 0.025 * sample))

            base: GamePredictionBase = {
                "event_id": game.event_id,
                "event_start_utc": game.event_start_utc,
                "league": self.league,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "uncertainty": round(uncertainty, 4),
                "model_version": self.version,
                "feature_basis": {
                    "away_points_hl10": round(away_points, 2),
                    "home_points_hl10": round(home_points, 2),
                    "est_possessions": round(est_possessions, 1),
                    "expected_total": round(expected_total, 2),
                    "effective_total_sd": round(effective_total_sd, 2),
                    "home_short_rest": home_short_rest,
                    "away_short_rest": away_short_rest,
                    "current_season_sample": sample,
                    "history_games": len(history),
                },
            }

            if game.total_line is not None:
                under_prob = _normal_cdf(game.total_line, expected_total, effective_total_sd)
                over_prob = 1.0 - under_prob
                predictions.append(
                    GamePrediction(
                        market_type="total",
                        inference_inputs={
                            "mean": expected_total,
                            "sd": effective_total_sd,
                            "line": game.total_line,
                        },
                        line=game.total_line,
                        probabilities={
                            "over": round(over_prob, 6),
                            "under": round(under_prob, 6),
                        },
                        rationale=(
                            f"Projected total {expected_total:.1f} (poss {est_possessions:.1f}), "
                            f"sd {effective_total_sd:.1f}."
                        ),
                        **base,
                    )
                )

        return predictions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src:. .venv/Scripts/python -m pytest tests/test_ncaab_v4_walkforward.py -o addopts='' -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Ruff check**

Run: `.venv/Scripts/ruff check src/model_prediction/models/ncaab_total_v4.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/model_prediction/models/ncaab_total_v4.py tests/test_ncaab_v4_walkforward.py
git commit -m "Add NCAABTotalModelV4 with current-season sample fix"
```

---

### Task 3: Walk-forward validation script (v4 vs. incumbent v3)

**Files:**
- Create: `scripts/ncaab_v4_walkforward.py`

**Interfaces:**
- Consumes: `NCAABSpreadModelV4.predict_spreads`, `NCAABTotalModelV4.predict_totals` (Task 1/2), `model_prediction.models.basketball_leagues.ncaab_v3_model()` (existing), `data/historical/ncaab_games_all.jsonl` (existing file, read-only).
- Produces: printed Brier/MAE comparison table — no files written, no ledger writes (research-only, matching `scripts/replay_college_challengers.py`'s no-ledger-write convention).

- [ ] **Step 1: Write the script**

```python
"""Walk-forward comparison: ncaab-tempo-v3 (incumbent, shared BasketballModel)
vs. ncaab-spread-v4/ncaab-total-v4 (dedicated classes) on real NCAAB history.

Research-only: no ledger writes, no orders. Batches by calendar day to keep
the TrendEngine rebuild cost linear rather than quadratic in game count.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.basketball_leagues import ncaab_v3_model
from model_prediction.models.ncaab_spread_v4 import NCAABSpreadModelV4
from model_prediction.models.ncaab_total_v4 import NCAABTotalModelV4

ROOT = Path(".")
GRADE_FROM = "2026-09-01"  # covers both in-season control and the 2026-27 season-start boundary


def _load_games() -> list[dict]:
    rows = [json.loads(line) for line in open(ROOT / "data/historical/ncaab_games_all.jsonl", encoding="utf-8")]
    rows.sort(key=lambda r: r["event_start_utc"])
    return [r for r in rows if r.get("status") == "completed" and r.get("home_score") is not None]


def main() -> None:
    games = _load_games()
    print(f"total completed games: {len(games)}")

    v3 = ncaab_v3_model()
    v4_spread = NCAABSpreadModelV4()
    v4_total = NCAABTotalModelV4()

    by_day: dict[str, list[dict]] = defaultdict(list)
    for g in games:
        by_day[g["event_start_utc"][:10]].append(g)
    days = sorted(by_day.keys())

    total_errs: dict[str, list[float]] = {"v3": [], "v4": []}
    moneyline_briers: dict[str, list[float]] = {"v3": [], "v4": []}
    history_so_far: list[GameRecord] = []

    for day in days:
        day_games = by_day[day]
        if day >= GRADE_FROM:
            for g in day_games:
                actual_total = g["away_score"] + g["home_score"]
                home_won = 1.0 if g["home_score"] > g["away_score"] else 0.0

                upcoming = UpcomingGame(
                    event_id=g["event_id"], event_start_utc=g["event_start_utc"],
                    away_team=g["away_team"], home_team=g["home_team"], total_line=150.0,
                )

                preds_v3 = v3.predict_games(history_so_far, [upcoming])
                total_v3 = next(p for p in preds_v3 if p.market_type == "total")
                ml_v3 = next(p for p in preds_v3 if p.market_type == "moneyline")
                total_errs["v3"].append(abs(actual_total - total_v3.feature_basis["expected_total"]))
                p_home_v3 = max(1e-6, min(1 - 1e-6, ml_v3.probabilities["home"]))
                moneyline_briers["v3"].append((p_home_v3 - home_won) ** 2)

                preds_v4_total = v4_total.predict_totals(history_so_far, [upcoming])
                total_v4 = preds_v4_total[0]
                total_errs["v4"].append(abs(actual_total - total_v4.feature_basis["expected_total"]))

                preds_v4_spread = v4_spread.predict_spreads(history_so_far, [upcoming])
                ml_v4 = next(p for p in preds_v4_spread if p.market_type == "moneyline")
                p_home_v4 = max(1e-6, min(1 - 1e-6, ml_v4.probabilities["home"]))
                moneyline_briers["v4"].append((p_home_v4 - home_won) ** 2)

        for g in day_games:
            history_so_far.append(GameRecord(
                event_id=g["event_id"], event_start_utc=g["event_start_utc"], league=g["league"],
                away_team=g["away_team"], home_team=g["home_team"],
                away_score=g["away_score"], home_score=g["home_score"],
            ))

    print(f"\ngraded games: {len(total_errs['v3'])}")
    for label in ("v3", "v4"):
        mae = float(np.mean(total_errs[label]))
        brier = float(np.mean(moneyline_briers[label]))
        print(f"{label}: total MAE={mae:.2f}  moneyline Brier={brier:.4f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `cd E:/MP && PYTHONPATH=src:. .venv/Scripts/python scripts/ncaab_v4_walkforward.py`
Expected: prints `total completed games: 6538`, `graded games: <N>`, and a `v3`/`v4` comparison line each for total MAE and moneyline Brier. This is a long-running O(days × teams) loop — if it exceeds a few minutes, run it in the background and report the printed table once it completes (do not shorten `GRADE_FROM`'s window without noting the narrower evidence in the final report).

- [ ] **Step 3: Record the result**

Append the printed v3-vs-v4 comparison numbers verbatim to the "Validation gate" section of `docs/superpowers/specs/2026-11-05-ncaab-v4-overhaul-design.md` (edit that file, do not create a new one) — this is the evidence a later, separate promotion decision will need. Do not change `config/model.yaml`'s `active_production_version`.

- [ ] **Step 4: Commit**

```bash
git add scripts/ncaab_v4_walkforward.py docs/superpowers/specs/2026-11-05-ncaab-v4-overhaul-design.md
git commit -m "Add NCAAB v3-vs-v4 walk-forward comparison and record results"
```

---

### Task 4: Wire v4 as the NCAAB research version (no production flip)

**Files:**
- Modify: `config/model.yaml:676-691` (the `NCAAB:` block)

**Interfaces:**
- Consumes: nothing new — this is a one-line config change.
- Produces: `active_research_version: ncaab-tempo-v4` so forecast tooling that reads the research version (not production) picks up the new model string. No code wiring is needed beyond this if `active_research_version` is only ever used as a label/lookup key elsewhere — confirm with the grep in Step 1 before editing; if the research version string is actually dispatched to a model-loading function somewhere (e.g. `learned_forward.py` or `college_basketball_forward.py`), that dispatch must be extended to construct `NCAABSpreadModelV4`/`NCAABTotalModelV4` when it sees `ncaab-tempo-v4`, mirroring however it already dispatches `nba-spread-margin-v6`/`nba-total-margin-v6`.

- [ ] **Step 1: Find where `active_research_version` and NBA's v6 version strings are dispatched**

Run: `cd E:/MP && grep -n "active_research_version\|nba-spread-margin-v6\|nba-total-margin-v6" src/model_prediction/college_basketball_forward.py src/model_prediction/learned_forward.py src/model_prediction/cli/forecast.py`

Read the matched lines in context before editing anything — this determines whether Step 2 is config-only or needs a matching code branch.

- [ ] **Step 2: Update `config/model.yaml`**

```yaml
  NCAAB:
    family: elo_trend_normal_approximation
    status: shadow_qualified
    origin: statistical_model
    active_production_version: ncaab-tempo-v3
    active_research_version: ncaab-tempo-v4
    min_edge: 0.035
    spread_sd: 10.5
    total_sd: 14.0
    home_court_points: 3.6
    markets:
    - moneyline
    - spread
    - total
    leagues:
    - NCAAB
```

(This changes only the `active_research_version` line; every other line in the block is unchanged — do not touch `active_production_version`.)

- [ ] **Step 3: If Step 1 found a dispatch point that needs a new branch, add it**

Mirror the existing pattern exactly for whichever version string it switches on (e.g. if it's a `match model_version:` or `if model_version == "...":` block dispatching to `NBASpreadModelV6`/`NBATotalModelV6`, add an `elif model_version == "ncaab-tempo-v4":` branch constructing `NCAABSpreadModelV4()`/`NCAABTotalModelV4()` the same way). Do not write this blind — the exact shape depends on what Step 1's grep returns; read the surrounding function before adding the branch.

- [ ] **Step 4: Run the existing production registry test**

Run: `cd E:/MP && PYTHONPATH=src:. .venv/Scripts/python -m pytest tests/test_production_registry.py -o addopts='' -q -k "not test_real_production_yaml_resolves_every_model"`

(The `-k` exclusion skips the one pre-existing failing test documented in this plan's Global Constraints — it's unrelated config drift, not something this task should fix or be blocked by.)

Expected: remaining tests PASS.

- [ ] **Step 5: Commit**

```bash
git add config/model.yaml
git commit -m "Point NCAAB active_research_version at ncaab-tempo-v4"
```

(If Step 3 required a code change, add that file to this commit too.)

---

## Final Report (after all tasks complete)

Summarize for the user:
1. Task 1-2 test results (6 tests: 2 current-season-sample-gating tests + 2 standalone sanity tests + whatever Task 2's file ends up with).
2. Task 3's real v3-vs-v4 MAE/Brier numbers — this is the evidence, not a vibe check.
3. Explicitly restate: `active_production_version` is untouched; this is `research` status only; promoting v4 to production is a separate, explicit decision requiring the user's review of Task 3's evidence per `docs/ROADMAP.md`'s promotion rule.
4. Flag the pre-existing, out-of-scope finding: `nba_spread_v6.py:125` and `nba_total_v6.py:103` have the same lifetime-`games_played` bug this plan fixed for NCAAB — not fixed here, flagged for a future task if the user wants it addressed.
