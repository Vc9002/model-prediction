"""ESPN NBA summary payload -> research box-score rows (quick-261120-nbf).

The player rows are a superset of ``features.nba_player_logs.parse_nba_player_boxscore``
(every key it emits, same meaning) plus made/attempt splits, offensive/defensive rebounds,
fouls, plus-minus and team identity, so research loaders can read either. The stored
per-game file keeps the ``{event_id, payload}`` shape that ``load_nba_player_boxscores``
reads, with the payload trimmed to the parts a box-score/ratings model needs.
"""

from __future__ import annotations

from typing import Any

# Kept in the stored per-game file. Plays go to a separate compressed file; news, articles,
# standings, leaders and win-probability are presentation data and are dropped.
KEEP_KEYS = ("boxscore", "header", "gameInfo", "format", "meta", "pickcenter", "injuries")


def trim_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: payload[k] for k in KEEP_KEYS if k in payload}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "--", "-"):
        return None
    try:
        return float(text.lstrip("+"))
    except ValueError:
        return None


def _split(value: Any) -> tuple[float | None, float | None]:
    text = str(value or "")
    if "-" not in text:
        return None, None
    made, _, att = text.partition("-")
    return _num(made), _num(att)


def _f(value: float | None) -> float:
    return 0.0 if value is None else value


def _raw(stats: list[Any], idx: dict[str, int], name: str) -> Any:
    i = idx.get(name)
    return stats[i] if i is not None and i < len(stats) else None


def _sides(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """team id -> {home_away, score, winner, linescores} from the header competition."""
    comps = (payload.get("header") or {}).get("competitions") or [{}]
    out: dict[str, dict[str, Any]] = {}
    for comp in comps[0].get("competitors") or []:
        team = comp.get("team") or {}
        out[str(team.get("id"))] = {
            "home_away": comp.get("homeAway"),
            "score": _num(comp.get("score")),
            "winner": comp.get("winner"),
            "linescores": [_num(x.get("displayValue")) for x in comp.get("linescores") or []],
        }
    return out


def parse_player_rows(payload: dict[str, Any], event_id: str) -> list[dict[str, Any]]:
    box = payload.get("boxscore") or {}
    sides = _sides(payload)
    rows: list[dict[str, Any]] = []
    for team_entry in box.get("players") or []:
        team = team_entry.get("team") or {}
        team_id = str(team.get("id"))
        groups = team_entry.get("statistics") or []
        if not groups or not team.get("displayName"):
            continue
        keys = groups[0].get("keys") or []
        idx = {name: i for i, name in enumerate(keys)}
        for athlete in groups[0].get("athletes") or []:
            meta = athlete.get("athlete") or {}
            if meta.get("id") is None or meta.get("displayName") is None:
                continue
            stats = athlete.get("stats") or []
            fgm, fga = _split(_raw(stats, idx, "fieldGoalsMade-fieldGoalsAttempted"))
            fg3m, fg3a = _split(_raw(stats, idx, "threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
            ftm, fta = _split(_raw(stats, idx, "freeThrowsMade-freeThrowsAttempted"))
            minutes = _f(_num(_raw(stats, idx, "minutes")))
            rows.append(
                {
                    "event_id": str(event_id),
                    "team": team.get("displayName"),
                    "team_id": team_id,
                    "team_abbreviation": team.get("abbreviation"),
                    "home_away": (sides.get(team_id) or {}).get("home_away"),
                    "player_id": str(meta["id"]),
                    "name": meta["displayName"],
                    "position": (meta.get("position") or {}).get("abbreviation"),
                    "minutes": minutes,
                    "points": _f(_num(_raw(stats, idx, "points"))),
                    "rebounds": _f(_num(_raw(stats, idx, "rebounds"))),
                    "oreb": _f(_num(_raw(stats, idx, "offensiveRebounds"))),
                    "dreb": _f(_num(_raw(stats, idx, "defensiveRebounds"))),
                    "assists": _f(_num(_raw(stats, idx, "assists"))),
                    "steals": _f(_num(_raw(stats, idx, "steals"))),
                    "blocks": _f(_num(_raw(stats, idx, "blocks"))),
                    "turnovers": _f(_num(_raw(stats, idx, "turnovers"))),
                    "fouls": _f(_num(_raw(stats, idx, "fouls"))),
                    "fgm": _f(fgm),
                    "fga": _f(fga),
                    "fg3m": _f(fg3m),
                    "fg3a": _f(fg3a),
                    "ftm": _f(ftm),
                    "fta": _f(fta),
                    "plus_minus": _num(_raw(stats, idx, "plusMinus")),
                    "starter": bool(athlete.get("starter")),
                    "active": bool(athlete.get("active")) and minutes > 0,
                    "did_not_play": bool(athlete.get("didNotPlay")),
                    "dnp_reason": athlete.get("reason"),
                    "ejected": bool(athlete.get("ejected")),
                }
            )
    return rows


def parse_team_rows(payload: dict[str, Any], event_id: str) -> list[dict[str, Any]]:
    box = payload.get("boxscore") or {}
    sides = _sides(payload)
    rows: list[dict[str, Any]] = []
    for entry in box.get("teams") or []:
        team = entry.get("team") or {}
        team_id = str(team.get("id"))
        side = sides.get(team_id) or {}
        row: dict[str, Any] = {
            "event_id": str(event_id),
            "team": team.get("displayName"),
            "team_id": team_id,
            "team_abbreviation": team.get("abbreviation"),
            "home_away": side.get("home_away"),
            "points": side.get("score"),
            "winner": side.get("winner"),
            "linescores": side.get("linescores"),
        }
        for stat in entry.get("statistics") or []:
            name = stat.get("name")
            value = stat.get("displayValue")
            if not name:
                continue
            if "-" in name:
                made_key, _, att_key = name.partition("-")
                row[made_key], row[att_key] = _split(value)
            else:
                row[name] = _num(value)
        rows.append(row)
    return rows
