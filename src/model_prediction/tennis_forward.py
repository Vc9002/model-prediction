"""Point-in-time tennis moneyline research against executable Polymarket BBOs.

Singles only -- Polymarket US never lists doubles markets, and ESPN's doubles
draws (``roster``-shaped competitors) are excluded at the ingestion layer
(``data_sources/espn.py::completed_tennis_singles_matches``) before this
module ever sees them.

Coverage is asymmetric and worth stating plainly: ESPN exposes ATP and WTA
scoreboards, but Polymarket US only lists WTA and ITF (men's/women's)
tennis leagues -- there is no ATP market on this gateway at all (verified via
``POLYMARKET_SPORT_LEAGUES["tennis"]``). ESPN in turn has no ITF scoreboard
path. The only tour where a real model prediction can ever be matched against
a real executable Polymarket ask is therefore WTA; this module only builds
that slate. It remains research-only until separately validated.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from .data_sources.espn import _infer_tennis_surface
from .domain import EASTERN, parse_utc
from .models.tennis import UpcomingMatch, tennis_model

_PARTIAL_MARKERS = ("-fh-", "-h1-", "-h2-", "-1h-", "-set-")

TENNIS_LEAGUE = "WTA"


def _tennis_history_before(data_root: str | Path, as_of_date: str) -> list[dict[str, Any]]:
    """Point-in-time WTA match history from ``data/processed/tennis/games.jsonl``.

    Tennis rows are player-vs-player (``winner``/``loser``/``surface``, no
    scores) -- structurally incompatible with ``features.base.FeatureStore``'s
    ``GameRecord``, which requires ``away_team``/``home_team``/``away_score``/
    ``home_score`` via direct dict subscript. Every tennis row raised
    ``KeyError`` there and was silently caught and skipped
    (``FeatureStore.load_games``'s broad ``except (KeyError, TypeError,
    ValueError): continue``), so ``games_before("tennis", ...)`` always
    returned an empty list -- meaning every match probability computed by
    this module defaulted to exactly 0.5 for every player, including
    well-known ones with hundreds of real matches on file, regardless of the
    combined-tournament tagging fix above. This reads the raw rows directly
    instead, using the same point-in-time cutoff ``games_before`` uses
    (midnight US-Eastern at the start of ``as_of_date``).
    """
    path = Path(data_root) / "processed" / "tennis" / "games.jsonl"
    if not path.exists():
        return []
    cutoff = datetime.combine(date.fromisoformat(as_of_date), time.min, tzinfo=EASTERN)
    history: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if parse_utc(str(row["event_start_utc"])) < cutoff:
                    history.append(row)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return history


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _name_matches(player: str, text: str) -> bool:
    player_words = [word for word in _words(player) if len(word) >= 2]
    text_words = set(_words(text))
    if not player_words or not text_words:
        return False
    if " ".join(player_words) in " ".join(_words(text)):
        return True
    return all(word in text_words for word in player_words)


def _upcoming_singles_matches(scoreboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Same singles/doubles split as the historical parser, for live events
    that have not necessarily finished (or even started) yet.

    Filters to Women's Singles specifically, not just "singles" generically:
    combined ATP+WTA tournaments return the same event -- with BOTH gender
    groupings -- from the WTA site-API path too (see
    completed_tennis_singles_matches's docstring), so a generic "singles"
    check here would also pick up Men's Singles matches, which can never be
    priced (Polymarket US has no ATP market) and could collide with a
    same-name WTA player's rating lookup.
    """
    matches: list[dict[str, Any]] = []
    for event in scoreboard.get("events", []):
        tournament = str(event.get("name", "unknown"))
        surface = _infer_tennis_surface(tournament)
        for grouping in event.get("groupings", []):
            for competition in grouping.get("competitions", []):
                slug = str(competition.get("type", {}).get("slug", ""))
                if "womens-singles" not in slug:
                    continue
                competitors = competition.get("competitors", [])
                if len(competitors) != 2:
                    continue
                by_side = {item.get("homeAway"): item for item in competitors}
                away, home = by_side.get("away"), by_side.get("home")
                if not away or not home:
                    continue
                away_name = (away.get("athlete") or {}).get("displayName")
                home_name = (home.get("athlete") or {}).get("displayName")
                if not away_name or not home_name:
                    continue
                start = competition.get("date") or event.get("date")
                matches.append(
                    {
                        "event_id": f"{event.get('id')}:{competition.get('id')}",
                        "event_start_utc": start,
                        "away_player": away_name,
                        "home_player": home_name,
                        "surface": surface,
                    }
                )
    return matches


def _latest_moneyline_snapshots(
    data_root: str | Path,
    game_date: str,
    league: str,
) -> list[dict[str, Any]]:
    import json

    path = Path(data_root) / "odds" / "tennis" / game_date / "polymarket_snapshots.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            slug = str(row.get("market_slug") or "")
            if (
                row.get("market_type") != "moneyline"
                or str(row.get("league") or "").upper() != league
                or any(marker in slug.casefold() for marker in _PARTIAL_MARKERS)
                or not bool(row.get("timestamp_valid", False))
            ):
                continue
            if slug not in latest or str(row.get("observed_at_utc") or "") > str(
                latest[slug].get("observed_at_utc") or ""
            ):
                latest[slug] = row
    return list(latest.values())


def build_tennis_slate(
    *,
    data_root: str | Path,
    game_date: str,
    client: Any,
    observed_at: datetime,
) -> dict[str, Any]:
    history = [
        game
        for game in _tennis_history_before(data_root, game_date)
        if str(game.get("league", "")).upper() == TENNIS_LEAGUE
    ]
    scoreboard = client.scoreboard(TENNIS_LEAGUE, game_date)
    events = _upcoming_singles_matches(scoreboard)

    upcoming: list[UpcomingMatch] = []
    skipped: list[dict[str, str]] = []
    for item in events:
        event_id = item["event_id"]
        try:
            start = parse_utc(str(item["event_start_utc"]))
            if start <= observed_at:
                raise ValueError("event_started")
            upcoming.append(
                UpcomingMatch(
                    event_id=event_id,
                    event_start_utc=str(item["event_start_utc"]),
                    player_one=item["away_player"],
                    player_two=item["home_player"],
                    surface=str(item.get("surface") or "Hard"),
                    tour=TENNIS_LEAGUE,
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            skipped.append({"event_id": event_id, "reason": str(error)})

    model = tennis_model()
    predictions = model.predict_games(history, upcoming)
    snapshots = _latest_moneyline_snapshots(data_root, game_date, TENNIS_LEAGUE)
    priced: list[dict[str, Any]] = []
    unmatched: list[dict[str, str]] = []
    for prediction in predictions:
        start = parse_utc(prediction.event_start_utc)
        candidates = []
        for snapshot in snapshots:
            try:
                snapshot_start = parse_utc(str(snapshot["event_start_utc"]))
                snapshot_at = parse_utc(str(snapshot["observed_at_utc"]))
            except (KeyError, TypeError, ValueError):
                continue
            long_desc = str((snapshot.get("long") or {}).get("description", ""))
            short_desc = str((snapshot.get("short") or {}).get("description", ""))
            away_is_long = _name_matches(prediction.away_team, long_desc) and _name_matches(
                prediction.home_team, short_desc
            )
            away_is_short = _name_matches(prediction.away_team, short_desc) and _name_matches(
                prediction.home_team, long_desc
            )
            if (
                abs((snapshot_start - start).total_seconds()) <= 30 * 60
                and snapshot_at < start
                and (away_is_long or away_is_short)
            ):
                candidates.append((snapshot, "long" if away_is_long else "short"))
        if len(candidates) != 1:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "no unique timestamp-valid moneyline matched by player name",
                }
            )
            continue
        snapshot, away_side_key = candidates[0]
        home_side_key = "short" if away_side_key == "long" else "long"
        selection = max(("away", "home"), key=lambda side: prediction.probabilities[side])
        side_key = away_side_key if selection == "away" else home_side_key
        side = snapshot.get(side_key) or {}
        ask = side.get("ask")
        if ask is None or not 0 < float(ask) < 1:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "selected moneyline side has no executable ask",
                }
            )
            continue
        priced.append(
            {
                "event_id": prediction.event_id,
                "event_start_utc": prediction.event_start_utc,
                "away_team": prediction.away_team,
                "home_team": prediction.home_team,
                "market_type": "moneyline",
                "selection": selection,
                "line": None,
                "model_probability": prediction.probabilities[selection],
                "model_uncertainty": prediction.uncertainty,
                "model_version": prediction.model_version,
                "feature_basis": prediction.feature_basis,
                "rationale": prediction.rationale,
                "market_slug": snapshot["market_slug"],
                "executable_ask": float(ask),
                "observed_at_utc": snapshot["observed_at_utc"],
                "timestamp_valid": True,
                "edge_vs_executable_ask": round(
                    prediction.probabilities[selection] - float(ask),
                    6,
                ),
            }
        )

    model_source = Path(__file__).with_name("models") / "tennis.py"
    code_hash = hashlib.sha256(model_source.read_bytes()).hexdigest()
    return {
        "sport": "tennis",
        "status": "research",
        "model_version": model.version,
        "model_code_hash": code_hash,
        "scheduled_games": len(upcoming),
        "priced_contracts": priced,
        "priced_count": len(priced),
        "unmatched": unmatched,
        "skipped": skipped,
        "note": (
            "Surface-blended Elo priced against WTA moneyline only -- Polymarket US has no "
            "ATP market and ESPN has no ITF scoreboard, so those legs can never be matched; "
            "research-only pending locked validation."
        ),
    }
