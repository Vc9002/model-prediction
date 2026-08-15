from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ..models.tennis import TennisPlayerForm

INVALID_SCORE_MARKERS = ("RET", "W/O", "DEF", "ABD")


def load_match_csvs(paths: Iterable[str | Path]) -> pd.DataFrame:
    frames = [pd.read_csv(path, low_memory=False) for path in paths]
    if not frames:
        raise ValueError("at least one tennis match CSV is required")
    return pd.concat(frames, ignore_index=True)


def player_form_from_matches(
    matches: pd.DataFrame,
    player_id: str | int,
    player_name: str,
    surface: str,
    decision_date: date,
    lookback_days: int = 730,
    recent_matches: int = 10,
    surface_elo: float = 1500,
    overall_elo: float = 1500,
) -> TennisPlayerForm:
    required = {
        "tourney_date",
        "surface",
        "score",
        "winner_id",
        "loser_id",
        "w_svpt",
        "w_1stWon",
        "w_2ndWon",
        "l_svpt",
        "l_1stWon",
        "l_2ndWon",
    }
    missing = required - set(matches.columns)
    if missing:
        raise ValueError(f"tennis match data missing columns: {sorted(missing)}")
    cutoff = int(decision_date.strftime("%Y%m%d"))
    start = int((decision_date - timedelta(days=lookback_days)).strftime("%Y%m%d"))
    player_key = str(player_id)
    rows = matches.copy()
    rows["winner_id"] = rows["winner_id"].map(_id_string)
    rows["loser_id"] = rows["loser_id"].map(_id_string)
    rows = rows[
        (pd.to_numeric(rows["tourney_date"], errors="coerce") < cutoff)
        & (pd.to_numeric(rows["tourney_date"], errors="coerce") >= start)
        & (rows["surface"].astype(str).str.casefold() == surface.casefold())
        & ((rows["winner_id"] == player_key) | (rows["loser_id"] == player_key))
    ].copy()
    score = rows["score"].fillna("").astype(str).str.upper()
    rows = rows[~score.map(lambda value: any(marker in value for marker in INVALID_SCORE_MARKERS))]
    rows = rows.sort_values("tourney_date")
    observations = [_point_observation(row, player_key) for _, row in rows.iterrows()]
    observations = [item for item in observations if item is not None]
    recent = observations[-recent_matches:]
    serve_won, serve_points, return_won, return_points = _sum_observations(observations)
    recent_serve_won, recent_serve_points, recent_return_won, recent_return_points = (
        _sum_observations(recent)
    )
    return TennisPlayerForm(
        player_id=player_key,
        name=player_name,
        serve_points_won=_rate(serve_won, serve_points, 0.5),
        serve_points=serve_points,
        return_points_won=_rate(return_won, return_points, 0.5),
        return_points=return_points,
        recent_serve_points_won=(
            _rate(recent_serve_won, recent_serve_points, 0.5) if recent_serve_points else None
        ),
        recent_serve_points=recent_serve_points,
        recent_return_points_won=(
            _rate(recent_return_won, recent_return_points, 0.5) if recent_return_points else None
        ),
        recent_return_points=recent_return_points,
        surface_elo=surface_elo,
        overall_elo=overall_elo,
    )


def surface_elo_ratings(
    matches: pd.DataFrame,
    surface: str,
    decision_date: date,
    k_factor: float = 24,
) -> dict[str, float]:
    rows = matches.copy()
    rows = rows[
        (pd.to_numeric(rows["tourney_date"], errors="coerce") < int(decision_date.strftime("%Y%m%d")))
        & (rows["surface"].astype(str).str.casefold() == surface.casefold())
    ].sort_values("tourney_date")
    ratings: dict[str, float] = {}
    for _, row in rows.iterrows():
        score = str(row.get("score", "")).upper()
        if any(marker in score for marker in INVALID_SCORE_MARKERS):
            continue
        winner, loser = _id_string(row["winner_id"]), _id_string(row["loser_id"])
        if not winner or not loser:
            continue
        winner_rating, loser_rating = ratings.get(winner, 1500), ratings.get(loser, 1500)
        expected = 1 / (1 + 10 ** ((loser_rating - winner_rating) / 400))
        change = k_factor * (1 - expected)
        ratings[winner] = winner_rating + change
        ratings[loser] = loser_rating - change
    return ratings


def _point_observation(row, player_key):
    won = row["winner_id"] == player_key
    own = "w" if won else "l"
    opponent = "l" if won else "w"
    values = pd.to_numeric(
        pd.Series(
            {
                "serve_points": row[f"{own}_svpt"],
                "first_won": row[f"{own}_1stWon"],
                "second_won": row[f"{own}_2ndWon"],
                "opponent_serve_points": row[f"{opponent}_svpt"],
                "opponent_first_won": row[f"{opponent}_1stWon"],
                "opponent_second_won": row[f"{opponent}_2ndWon"],
            }
        ),
        errors="coerce",
    )
    if values.isna().any():
        return None
    serve_points = int(values["serve_points"])
    serve_won = int(values["first_won"] + values["second_won"])
    return_points = int(values["opponent_serve_points"])
    return_won = int(
        values["opponent_serve_points"]
        - values["opponent_first_won"]
        - values["opponent_second_won"]
    )
    if min(serve_points, return_points, serve_won, return_won) < 0:
        return None
    return serve_won, serve_points, return_won, return_points


def _sum_observations(observations):
    return tuple(sum(item[index] for item in observations) for index in range(4))


def _id_string(value) -> str:
    if pd.isna(value):
        return ""
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)


def _rate(numerator, denominator, fallback):
    return fallback if denominator <= 0 else numerator / denominator
