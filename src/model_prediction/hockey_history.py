"""Refresh completed NHL results for the existing hockey forecast engine."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import httpx

from .data_sources.espn import ESPNClient
from .data_sources.hockey_leagues import HockeyLeagueClient, refresh_other_hockey_leagues
from .data_sources.nhl_api import NHLClient
from .domain import utc_now

NHL_CLUBS = (
    "ANA",
    "BOS",
    "BUF",
    "CGY",
    "CAR",
    "CHI",
    "COL",
    "CBJ",
    "DAL",
    "DET",
    "EDM",
    "FLA",
    "LAK",
    "MIN",
    "MTL",
    "NSH",
    "NJD",
    "NYI",
    "NYR",
    "OTT",
    "PHI",
    "PIT",
    "SJS",
    "SEA",
    "STL",
    "TBL",
    "TOR",
    "UTA",
    "VAN",
    "VGK",
    "WSH",
    "WPG",
)
BACKFILL_SEASONS = 3
# A full season is ~1,400 games; below this the 45-day ESPN bootstrap is all there is.
BACKFILL_THRESHOLD = 1500


def _season_ids(game_date: date) -> list[str]:
    start_year = game_date.year if game_date.month >= 9 else game_date.year - 1
    return [f"{year}{year + 1}" for year in range(start_year - BACKFILL_SEASONS + 1, start_year + 1)]


def backfill_nhl_seasons(existing: dict, game_date: date, client: NHLClient | None = None) -> dict:
    """Add completed regular-season and playoff NHL games for recent seasons.

    Names follow the ESPN rows already in ``existing`` (matched on club nickname) so
    one club never carries two ratings; a club absent from ESPN rows uses
    "<place> <nickname>". Games already present from ESPN are not duplicated.
    """
    source = client or NHLClient()
    espn_names = {name for row in existing.values() for name in (row["home_team"], row["away_team"])}
    present = {(row["start"][:10], row["home_team"], row["away_team"]) for row in existing.values()}
    errors: list[dict] = []
    seen: set[int] = set()
    collected: list[tuple[dict, str, str]] = []  # (row, home abbrev, away abbrev)
    latest: dict[str, tuple[str, str]] = {}  # abbrev -> (start, name)
    for season in _season_ids(game_date):
        for club in NHL_CLUBS:
            try:
                games = source.club_season(club, season).get("games", [])
            except (httpx.HTTPError, OSError, ValueError) as exc:
                errors.append({"club": club, "season": season, "reason": str(exc)})
                continue
            for game in games:
                if game.get("gameType") not in (2, 3) or game.get("gameState") not in ("FINAL", "OFF"):
                    continue
                if game["id"] in seen:
                    continue
                seen.add(game["id"])
                home, away = game["homeTeam"], game["awayTeam"]
                if home.get("score") is None or away.get("score") is None:
                    continue
                start = str(game["startTimeUTC"])
                names = {}
                for key, team in (("home", home), ("away", away)):
                    nickname = team["commonName"]["default"]
                    place = team["placeName"]["default"]
                    full = nickname if nickname.startswith(place) else f"{place} {nickname}"
                    names[key] = next((n for n in sorted(espn_names) if n.endswith(nickname)), full)
                    if team["abbrev"] not in latest or start > latest[team["abbrev"]][0]:
                        latest[team["abbrev"]] = (start, names[key])
                collected.append(
                    (
                        {
                            "event_id": f"nhl:{game['id']}",
                            "start": start,
                            "league": "NHL",
                            "home_team": names["home"],
                            "away_team": names["away"],
                            "home_score": int(home["score"]),
                            "away_score": int(away["score"]),
                            "completed": True,
                            "source": "nhl_api",
                            "observed_at_utc": utc_now().isoformat(),
                        },
                        home["abbrev"],
                        away["abbrev"],
                    )
                )
    added = 0
    for row, home_abbrev, away_abbrev in collected:
        # One franchise, one rating: a renamed club (Utah) takes its latest name.
        row["home_team"], row["away_team"] = latest[home_abbrev][1], latest[away_abbrev][1]
        key = (row["start"][:10], row["home_team"], row["away_team"])
        if key in present:
            continue
        present.add(key)
        existing[row["event_id"]] = row
        added += 1
    return {"added": added, "errors": errors}


def refresh_hockey_history(
    data_root: str | Path,
    game_date: str,
    nhl_client: NHLClient | None = None,
    league_client: HockeyLeagueClient | None = None,
) -> dict:
    path = Path(data_root) / "processed/hockey/games.jsonl"
    existing = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            existing[str(row["event_id"])] = row
    target = date.fromisoformat(game_date)
    # Bootstrap a bounded recent sample; thereafter overlap two days so missed
    # final results self-heal. Never ingest today's unfinished zero scoreboards.
    days = 2 if existing else 45
    dates = [(target - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
    errors = []

    def fetch(day):
        try:
            return ESPNClient().scoreboard("NHL", day), None
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
            return {}, {"date": day, "reason": str(exc)}

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(fetch, dates))
    observed = utc_now().isoformat()
    for board, error in results:
        if error:
            errors.append(error)
        for event in board.get("events", []):
            if not event.get("id") or not event.get("status", {}).get("type", {}).get("completed"):
                continue
            competition = (event.get("competitions") or [{}])[0]
            sides = {side.get("homeAway"): side for side in competition.get("competitors", [])}
            home, away = sides.get("home", {}), sides.get("away", {})
            if (
                not event.get("date")
                or not home.get("team", {}).get("displayName")
                or not away.get("team", {}).get("displayName")
            ):
                continue
            if home.get("score") is None or away.get("score") is None:
                continue
            row = {
                "event_id": str(event["id"]),
                "start": event["date"],
                "league": "NHL",
                "home_team": home["team"]["displayName"],
                "away_team": away["team"]["displayName"],
                "home_score": int(home["score"]),
                "away_score": int(away["score"]),
                "completed": True,
                "source": "espn",
                "observed_at_utc": observed,
            }
            existing[row["event_id"]] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        "".join(json.dumps(r) + "\n" for r in sorted(existing.values(), key=lambda r: r["start"])),
        encoding="utf-8",
    )
    temporary.replace(path)
    backfill = None
    if len(existing) < BACKFILL_THRESHOLD:
        backfill = backfill_nhl_seasons(existing, target, nhl_client)
        errors.extend(backfill["errors"])
        if backfill["added"]:
            temporary.write_text(
                "".join(json.dumps(r) + "\n" for r in sorted(existing.values(), key=lambda r: r["start"])),
                encoding="utf-8",
            )
            temporary.replace(path)
    others = refresh_other_hockey_leagues(existing, target, league_client)
    errors.extend(others["errors"])
    if any(others["added"].values()):
        temporary.write_text(
            "".join(json.dumps(r) + "\n" for r in sorted(existing.values(), key=lambda r: r["start"])),
            encoding="utf-8",
        )
        temporary.replace(path)
    return {
        "status": "partial" if errors else "ok",
        "completed_games": len(existing),
        "other_leagues_added": others["added"],
        "backfill_added": None if backfill is None else backfill["added"],
        "errors": errors,
    }
