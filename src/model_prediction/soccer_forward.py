"""Point-in-time soccer totals research against executable Polymarket BBOs.

The legacy learned soccer artifact is binary home-vs-not-home and therefore
cannot represent draws. Daily soccer research uses the existing Poisson/Dixon-
Coles score model instead and prices only the full-game 2.5-goal total that the
model natively supports. It remains research-only until separately validated.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .domain import parse_utc
from .features.base import FeatureStore
from .models.soccer import UpcomingMatch, soccer_model

_PARTIAL_MARKERS = ("-fh-", "-h1-", "-h2-", "-1h-", "-2h-")
_GENERIC_TEAM_WORDS = {"fc", "sc", "cf", "club", "city", "united"}


def _teams(event: dict[str, Any]) -> tuple[str, str]:
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    by_side = {str(item.get("homeAway")): item for item in competitors}
    return (
        str(by_side["away"]["team"]["displayName"]),
        str(by_side["home"]["team"]["displayName"]),
    )


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _team_matches_title(team: str, title: str) -> bool:
    team_words = _words(team)
    title_words = set(_words(title))
    if not team_words or not title_words:
        return False
    if " ".join(team_words) in " ".join(_words(title)):
        return True
    distinctive = [
        word
        for word in team_words
        if len(word) >= 3 and word not in _GENERIC_TEAM_WORDS
    ]
    return bool(distinctive) and all(word in title_words for word in distinctive)


def _latest_total_snapshots(
    data_root: str | Path,
    game_date: str,
) -> list[dict[str, Any]]:
    path = (
        Path(data_root)
        / "odds"
        / "soccer"
        / game_date
        / "polymarket_snapshots.jsonl"
    )
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                line_value = float(row.get("line"))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            slug = str(row.get("market_slug") or "")
            if (
                row.get("market_type") != "total"
                or line_value != 2.5
                or any(marker in slug.casefold() for marker in _PARTIAL_MARKERS)
                or not bool(row.get("timestamp_valid", False))
                or not row.get("event_title")
            ):
                continue
            if slug not in latest or str(row.get("observed_at_utc") or "") > str(
                latest[slug].get("observed_at_utc") or ""
            ):
                latest[slug] = row
    return list(latest.values())


def build_soccer_total_slate(
    *,
    data_root: str | Path,
    game_date: str,
    client: Any,
    leagues: tuple[str, ...],
    observed_at: datetime,
) -> dict[str, Any]:
    store = FeatureStore(data_root)
    history = store.games_before("soccer", game_date)
    events: list[dict[str, Any]] = []
    for league in leagues:
        events.extend(client.scoreboard(league, game_date).get("events", []))

    upcoming: list[UpcomingMatch] = []
    skipped: list[dict[str, str]] = []
    for event in events:
        event_id = str(event.get("id", ""))
        try:
            start = parse_utc(str(event["date"]))
            if start <= observed_at:
                raise ValueError("event_started")
            away, home = _teams(event)
            upcoming.append(
                UpcomingMatch(
                    event_id=event_id,
                    event_start_utc=str(event["date"]),
                    away_team=away,
                    home_team=home,
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            skipped.append({"event_id": event_id, "reason": str(error)})

    model = soccer_model()
    totals = [
        prediction
        for prediction in model.predict_games(history, upcoming)
        if str(prediction.market_type) == "total" and prediction.line == 2.5
    ]
    snapshots = _latest_total_snapshots(data_root, game_date)
    priced: list[dict[str, Any]] = []
    unmatched: list[dict[str, str]] = []
    for prediction in totals:
        start = parse_utc(prediction.event_start_utc)
        candidates = []
        for snapshot in snapshots:
            try:
                snapshot_start = parse_utc(str(snapshot["event_start_utc"]))
                snapshot_at = parse_utc(str(snapshot["observed_at_utc"]))
            except (KeyError, TypeError, ValueError):
                continue
            if (
                abs((snapshot_start - start).total_seconds()) <= 30 * 60
                and snapshot_at < start
                and _team_matches_title(
                    prediction.away_team,
                    str(snapshot.get("event_title", "")),
                )
                and _team_matches_title(
                    prediction.home_team,
                    str(snapshot.get("event_title", "")),
                )
            ):
                candidates.append(snapshot)
        if len(candidates) != 1:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": (
                        "no unique timestamp-valid full-game 2.5 total matched"
                    ),
                }
            )
            continue
        snapshot = candidates[0]
        selection = max(
            ("over", "under"),
            key=lambda side: prediction.probabilities[side],
        )
        side_key = "long" if selection == "over" else "short"
        side = snapshot.get(side_key) or {}
        ask = side.get("ask")
        if ask is None or not 0 < float(ask) < 1:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "selected total side has no executable ask",
                }
            )
            continue
        priced.append(
            {
                "event_id": prediction.event_id,
                "event_start_utc": prediction.event_start_utc,
                "away_team": prediction.away_team,
                "home_team": prediction.home_team,
                "market_type": "total",
                "selection": selection,
                "line": 2.5,
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
    model_source = Path(__file__).with_name("models") / "soccer.py"
    code_hash = hashlib.sha256(model_source.read_bytes()).hexdigest()
    return {
        "sport": "soccer",
        "status": "research",
        "model_version": model.version,
        "model_code_hash": code_hash,
        "scheduled_games": len(upcoming),
        "priced_contracts": priced,
        "priced_count": len(priced),
        "unmatched": unmatched,
        "skipped": skipped,
        "note": (
            "Three-way-aware Poisson/Dixon-Coles score model priced against "
            "full-game 2.5 totals; research-only pending locked validation."
        ),
    }
