"""Historical head-to-head matchup outcomes from cached games."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .base import FeatureContext, GameRecord, register_feature


def head_to_head(games: Iterable[GameRecord], team_a: str, team_b: str, limit: int = 20) -> dict[str, Any]:
    matchups = sorted(
        (game for game in games if {game.away_team, game.home_team} == {team_a, team_b}),
        key=lambda game: game.start,
    )[-limit:]
    a_wins = 0
    b_wins = 0
    draws = 0
    totals: list[int] = []
    for game in matchups:
        if game.home_score == game.away_score:
            draws += 1
        else:
            winner = game.home_team if game.home_score > game.away_score else game.away_team
            if winner == team_a:
                a_wins += 1
            else:
                b_wins += 1
        totals.append(game.total)
    count = len(matchups)
    return {
        "games": count,
        "team_a": team_a,
        "team_b": team_b,
        "team_a_wins": a_wins,
        "team_b_wins": b_wins,
        "draws": draws,
        "team_a_win_rate": round(a_wins / count, 6) if count else None,
        "team_b_win_rate": round(b_wins / count, 6) if count else None,
        "draw_rate": round(draws / count, 6) if count else None,
        "average_total": round(sum(totals) / count, 6) if count else None,
        "last_meeting_utc": matchups[-1].event_start_utc if matchups else None,
    }


@register_feature("head_to_head")
def head_to_head_snapshot(context: FeatureContext) -> dict[str, Any]:
    """Pairwise H2H for every pairing seen in the cache (kept sparse)."""
    pairs: set[tuple[str, str]] = set()
    for game in context.games:
        pairs.add(tuple(sorted((game.away_team, game.home_team))))  # type: ignore[arg-type]
    return {"pairs": {f"{a} vs {b}": head_to_head(context.games, a, b) for a, b in sorted(pairs)}}
