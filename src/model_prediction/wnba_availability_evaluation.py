"""Small, explicitly retrospective WNBA availability challenger evaluation.

This is not a promotion-grade historical backtest.  It reconstructs projected
minutes and a heavily shrunk plus/minus impact proxy from games that ended
before each target event, then applies timestamped official injury PDFs.  The
report bytes were retrieved retrospectively, so results are diagnostic only.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import pstdev
from typing import Any, Mapping

from scipy.stats import norm

from .data_sources.wnba_injuries import ParsedReport
from .domain import parse_utc, utc_now
from .features.base import FeatureStore, GameRecord


LOOKBACK_TEAM_GAMES = 10
PLUS_MINUS_PRIOR_MINUTES = 400.0


def report_payload(parsed: ParsedReport, source_url: str, retrieved_at: datetime) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "sport": "wnba",
        "source": "official_wnba_injury_report",
        "source_pdf_url": source_url,
        "observed_at_utc": parse_utc(retrieved_at.isoformat()).isoformat(),
        "report_at_utc": parsed.report_at_utc,
        "provenance_class": "retrospective_official_publication_timestamp",
        "teams_listed": list(parsed.teams_listed),
        "team_matchups": dict(parsed.team_matchups),
        "team_report_status": dict(parsed.team_report_status),
        "entries": [entry.__dict__ for entry in parsed.entries],
    }


def _target_teams(event: Mapping[str, Any]) -> dict[str, str]:
    competitors = event["competitions"][0]["competitors"]
    return {str(item["team"]["displayName"]): str(item["team"]["id"]) for item in competitors}


def _team_games_before(
    games: list[GameRecord], team: str, event_start: datetime
) -> list[GameRecord]:
    eligible = [
        game
        for game in games
        if game.start < event_start and team in {game.home_team, game.away_team}
    ]
    return eligible[-LOOKBACK_TEAM_GAMES:]


def _cached_summary(client: Any, cache_root: Path, event_id: str) -> dict[str, Any]:
    path = cache_root / f"{event_id}.json"
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        payload = record["payload"]
        slim = {"boxscore": payload.get("boxscore", {}), "header": payload.get("header", {})}
        if payload != slim:
            record["payload"] = slim
            path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        return slim
    response = client.summary("WNBA", event_id)
    payload = {"boxscore": response.get("boxscore", {}), "header": response.get("header", {})}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source": "espn_public_summary",
                "retrieved_at_utc": utc_now().isoformat(),
                "event_id": event_id,
                "payload": payload,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return payload


def _cached_roster(
    client: Any, cache_root: Path, team_id: str, season: int
) -> dict[str, Any]:
    path = cache_root.parent / "espn_rosters" / f"{season}-{team_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["payload"]
    payload = client.roster("WNBA", team_id, season)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source": "espn_public_roster",
                "retrieved_at_utc": utc_now().isoformat(),
                "season": season,
                "team_id": team_id,
                "payload": payload,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return payload


def _number(value: Any) -> float | None:
    text = str(value).strip().replace("+", "")
    if not text or text in {"--", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def build_research_priors(
    *,
    store: FeatureStore,
    client: Any,
    event: Mapping[str, Any],
    cache_root: str | Path,
    observed_at: datetime,
) -> dict[str, Any]:
    """Construct pregame minutes and a shrunk lineup plus/minus proxy."""
    event_start = parse_utc(str(event["date"]))
    teams = _target_teams(event)
    games = store.load_games("wnba")
    selected_games: dict[str, GameRecord] = {}
    for team in teams:
        for game in _team_games_before(games, team, event_start):
            selected_games[game.event_id] = game

    appearances: dict[str, dict[str, list[tuple[datetime, float, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    roster_names: dict[str, set[str]] = {team: set() for team in teams}
    for event_id, game in sorted(selected_games.items(), key=lambda item: item[1].start):
        summary = _cached_summary(client, Path(cache_root), event_id)
        for group in summary.get("boxscore", {}).get("players", []):
            team = str(group.get("team", {}).get("displayName", ""))
            if team not in teams:
                continue
            for statistic in group.get("statistics", []):
                labels = [str(label) for label in statistic.get("labels", [])]
                if "MIN" not in labels or "+/-" not in labels:
                    continue
                minute_index = labels.index("MIN")
                plus_minus_index = labels.index("+/-")
                for row in statistic.get("athletes", []):
                    name = str(row.get("athlete", {}).get("displayName", "")).strip()
                    if not name:
                        continue
                    roster_names[team].add(name)
                    stats = row.get("stats", [])
                    if max(minute_index, plus_minus_index) >= len(stats):
                        continue
                    minutes = _number(stats[minute_index])
                    plus_minus = _number(stats[plus_minus_index])
                    if minutes is None or plus_minus is None or minutes <= 0:
                        continue
                    appearances[team][name].append((game.start, minutes, plus_minus))

    for team, team_id in teams.items():
        roster = _cached_roster(client, Path(cache_root), team_id, event_start.year)
        roster_names[team].update(
            str(player.get("displayName", "")).strip()
            for player in roster.get("athletes", [])
            if player.get("displayName")
        )

    rows: list[dict[str, Any]] = []
    team_diagnostics: dict[str, Any] = {}
    for team in teams:
        provisional: list[dict[str, Any]] = []
        for name in sorted(roster_names[team]):
            samples = sorted(appearances[team].get(name, []), key=lambda item: item[0])
            recent = samples[-5:]
            if recent:
                weights = list(range(1, len(recent) + 1))
                projected = sum(item[1] * weight for item, weight in zip(recent, weights, strict=True)) / sum(
                    weights
                )
            else:
                projected = 0.0
            total_minutes = sum(item[1] for item in samples)
            total_plus_minus = sum(item[2] for item in samples)
            raw_per_100 = (
                total_plus_minus / total_minutes * 40.0 * 1.25 if total_minutes > 0 else 0.0
            )
            shrinkage = total_minutes / (total_minutes + PLUS_MINUS_PRIOR_MINUTES)
            impact = max(-8.0, min(8.0, raw_per_100 * shrinkage))
            provisional.append(
                {
                    "team": team,
                    "player_name": name,
                    "projected_minutes": projected,
                    "impact_points_per_100": impact,
                    "source_minutes": total_minutes,
                    "source_games": len(samples),
                }
            )
        raw_total = sum(row["projected_minutes"] for row in provisional)
        if raw_total <= 0:
            raise ValueError(f"no pregame player-minute history for {team}")
        scale = 200.0 / raw_total
        active_impacts = sorted(
            row["impact_points_per_100"]
            for row in provisional
            if row["projected_minutes"] > 0
        )
        replacement = active_impacts[max(0, int(0.25 * (len(active_impacts) - 1)))]
        for row in provisional:
            row["projected_minutes"] = round(row["projected_minutes"] * scale, 6)
            row["impact_points_per_100"] = round(row["impact_points_per_100"], 6)
            row["replacement_impact_points_per_100"] = round(replacement, 6)
            rows.append(row)
        team_diagnostics[team] = {
            "players": len(provisional),
            "players_with_minutes": sum(row["projected_minutes"] > 0 for row in provisional),
            "raw_projected_minutes": round(raw_total, 6),
            "normalization_scale": round(scale, 6),
            "replacement_impact_points_per_100": round(replacement, 6),
        }

    game_date = event_start.date().isoformat()
    return {
        "schema_version": "1",
        "sport": "wnba",
        "observed_at_utc": parse_utc(observed_at.isoformat()).isoformat(),
        "valid_from": game_date,
        "valid_through": game_date,
        "method": "last_5_appearance_minutes_normalized_200_shrunk_box_plus_minus_v1",
        "lookback_team_games": LOOKBACK_TEAM_GAMES,
        "plus_minus_prior_minutes": PLUS_MINUS_PRIOR_MINUTES,
        "players": rows,
        "team_diagnostics": team_diagnostics,
    }


def historical_margin_sigma(store: FeatureStore, cutoff: datetime) -> float:
    margins = [float(game.margin) for game in store.load_games("wnba") if game.start < cutoff]
    if len(margins) < 30:
        raise ValueError("insufficient WNBA margins for availability probability transform")
    value = pstdev(margins)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("invalid WNBA historical margin dispersion")
    return value


def adjust_home_probability(base_home_probability: float, points_gap: float, margin_sigma: float) -> float:
    """Translate an expected point adjustment through an empirical probit margin model."""
    clipped = min(0.999999, max(0.000001, float(base_home_probability)))
    adjusted = norm.cdf(norm.ppf(clipped) + float(points_gap) / float(margin_sigma))
    return min(0.999999, max(0.000001, float(adjusted)))


def build_and_save_priors(
    *,
    store: FeatureStore,
    client: Any,
    game_date: str,
    data_root: str | Path,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build and persist player priors for all WNBA games on a date.

    Saves one JSON file per game date to ``data/player_priors/wnba/``.
    Returns a summary dict with per-event results.
    """
    import json as _json
    from datetime import datetime as _datetime, timezone as _timezone

    root = Path(data_root)
    observed = observed_at or _datetime.now(_timezone.utc)
    scoreboard = client.scoreboard("WNBA", game_date)
    events = scoreboard.get("events", [])
    if not events:
        return {"status": "no_wnba_events", "game_date": game_date}

    cache_root = root / "availability" / "wnba" / "espn_boxscores"
    prior_dir = root / "player_priors" / "wnba"
    prior_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {"status": "ok", "game_date": game_date, "events": {}}
    all_players: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event.get("id", "unknown"))
        try:
            priors = build_research_priors(
                store=store,
                client=client,
                event=event,
                cache_root=cache_root,
                observed_at=observed,
            )
            all_players.extend(priors.get("players", []))
            results["events"][event_id] = {
                "players": len(priors.get("players", [])),
                "teams": list(priors.get("team_diagnostics", {}).keys()),
            }
        except (ValueError, KeyError, TypeError, OSError) as exc:
            results["events"][event_id] = {"error": str(exc)}

    if not all_players:
        return results

    payload = {
        "schema_version": "1",
        "sport": "wnba",
        "observed_at_utc": parse_utc(observed.isoformat()).isoformat(),
        "valid_from": game_date,
        "valid_through": game_date,
        "method": "last_5_appearance_minutes_normalized_200_shrunk_box_plus_minus_v1",
        "lookback_team_games": LOOKBACK_TEAM_GAMES,
        "plus_minus_prior_minutes": PLUS_MINUS_PRIOR_MINUTES,
        "players": all_players,
    }
    prior_path = prior_dir / f"{game_date}.json"
    prior_path.write_text(_json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    results["prior_path"] = str(prior_path)
    results["total_players"] = len(all_players)
    return results
