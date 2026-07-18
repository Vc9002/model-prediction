"""Point-in-time forward moneyline forecasts from audited learned artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .domain import parse_utc
from .features.base import FeatureStore
from .features.elo_ratings import build_elo
from .features.trends import TrendEngine
from .models.learned_market import LearnedMarketArtifact


@dataclass(frozen=True)
class LearnedForwardCandidate:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    market_type: str
    selection: str
    model_probability: float
    home_probability: float
    confidence_threshold: float
    call: bool
    action: str
    reason: str
    model_version: str
    model_artifact_hash: str
    model_qualified: bool
    feature_basis: dict[str, float | int]
    feature_snapshot_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_learned_moneyline_slate(
    *,
    sport: str,
    game_date: str,
    store: FeatureStore,
    client: Any,
    artifact_path: str | Path,
    observed_at: datetime,
    minimum_history_games: int = 50,
    minimum_team_history_games: int = 10,
) -> tuple[list[LearnedForwardCandidate], list[dict[str, str]], int]:
    """Build scheduled-game decisions with the exact validation feature basis.

    History is cut off at midnight before ``game_date``. This intentionally
    mirrors the complete-date walk-forward audit and prevents same-day leakage.
    Market prices are not inputs.
    """
    key = sport.lower()
    artifact = LearnedMarketArtifact.load(artifact_path)
    if artifact.sport != key:
        raise ValueError(f"artifact sport {artifact.sport} does not match {key}")
    history = store.games_before(key, game_date)
    if len(history) < minimum_history_games:
        raise ValueError(
            f"{key} requires {minimum_history_games}+ cached games before {game_date}; "
            f"found {len(history)}"
        )
    scoreboard = client.scoreboard(key.upper(), game_date)
    events = scoreboard.get("events", [])
    elo = build_elo(history, key)
    trends = TrendEngine(history)
    candidates: list[LearnedForwardCandidate] = []
    skipped: list[dict[str, str]] = []
    for event in events:
        event_id = str(event.get("id", "unknown"))
        try:
            start = parse_utc(str(event["date"]))
            if start <= observed_at:
                raise ValueError("event_started")
            away_team, home_team = _teams(event)
            home_trend = trends.team_trend(home_team)
            away_trend = trends.team_trend(away_team)
            if min(home_trend.games_played, away_trend.games_played) < minimum_team_history_games:
                raise ValueError(
                    "insufficient_team_history: "
                    f"{away_team}={away_trend.games_played}, "
                    f"{home_team}={home_trend.games_played}, "
                    f"required={minimum_team_history_games}"
                )
            features = {
                "elo_probability": elo.expected_home_win(home_team, away_team),
                "trend_gap": home_trend.offensive_momentum - away_trend.offensive_momentum,
                "defensive_trend_gap": home_trend.defensive_momentum - away_trend.defensive_momentum,
            }
            decision = artifact.decide_binary("moneyline", features)
            home_probability = artifact.probability("moneyline", features)
            if not decision.call:
                action = "NO_CALL_BELOW_LEARNED_CONFIDENCE"
            elif artifact.qualified:
                action = "QUALIFIED_SHADOW_CALL"
            else:
                action = "RESEARCH_OBSERVATION"
            basis: dict[str, float | int] = {
                "elo_probability": round(features["elo_probability"], 10),
                "trend_gap": round(features["trend_gap"], 10),
                "defensive_trend_gap": round(features["defensive_trend_gap"], 10),
                "history_games": len(history),
                "home_history_games": home_trend.games_played,
                "away_history_games": away_trend.games_played,
            }
            candidates.append(
                LearnedForwardCandidate(
                    event_id=event_id,
                    event_start_utc=str(event["date"]),
                    away_team=away_team,
                    home_team=home_team,
                    market_type="moneyline",
                    selection=decision.selection,
                    model_probability=decision.probability,
                    home_probability=round(home_probability, 6),
                    confidence_threshold=decision.confidence_threshold,
                    call=decision.call,
                    action=action,
                    reason=decision.reason,
                    model_version=artifact.version,
                    model_artifact_hash=artifact.hash,
                    model_qualified=artifact.qualified,
                    feature_basis=basis,
                    feature_snapshot_hash=_feature_hash(key, game_date, event_id, basis),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            skipped.append({"event_id": event_id, "reason": str(error)})
    return candidates, skipped, len(events)


def match_executable_quote(
    data_root: str | Path,
    sport: str,
    game_date: str,
    candidate: LearnedForwardCandidate,
) -> dict[str, Any] | None:
    """Latest stored executable BBO for a candidate's moneyline side, or None.

    Reads only ``data/odds/{sport}/{date}/polymarket_snapshots.jsonl`` — the
    prospective snapshots captured at slate time. Matching is by team display
    name against the long/short side descriptions. The decision price is the
    ASK of the selected side; midpoint is carried as reference only.
    """
    path = Path(data_root) / "odds" / sport.lower() / game_date / "polymarket_snapshots.jsonl"
    if not path.exists():
        return None
    away = candidate.away_team
    home = candidate.home_team
    best: dict[str, Any] | None = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            if snap.get("market_type") != "moneyline":
                continue
            slug = str(snap.get("market_slug") or "").casefold()
            if any(marker in slug for marker in ("-f5-", "-f3-", "-f7-", "-1st-", "-h1-", "-h2-")):
                continue  # partial-game contracts never price a full-game pick
            long_desc = str((snap.get("long") or {}).get("description", ""))
            short_desc = str((snap.get("short") or {}).get("description", ""))
            # Polymarket uses short nicknames ("Storm"); ESPN uses full display
            # names ("Seattle Storm"). Require the two sides to map uniquely
            # onto the two teams, in either order.
            pairing = (
                (_team_matches(away, long_desc) and _team_matches(home, short_desc))
                or (_team_matches(home, long_desc) and _team_matches(away, short_desc))
            )
            if not pairing:
                continue
            if best is None or str(snap.get("observed_at_utc", "")) > str(
                best.get("observed_at_utc", "")
            ):
                best = snap
    if best is None:
        return None
    selected_team = home if candidate.selection == "home" else away
    long_desc = str((best.get("long") or {}).get("description", ""))
    side_key = "long" if _team_matches(selected_team, long_desc) else "short"
    other_key = "short" if side_key == "long" else "long"
    side = best.get(side_key) or {}
    other = best.get(other_key) or {}
    ask = side.get("ask")
    other_ask = other.get("ask")
    if ask is None or not 0 < float(ask) < 1:
        return None
    no_vig = None
    if other_ask is not None and 0 < float(other_ask) < 1:
        total = float(ask) + float(other_ask)
        if total > 0:
            no_vig = round(float(ask) / total, 6)
    return {
        "market_slug": best.get("market_slug"),
        "side": side_key,
        "executable_ask": round(float(ask), 6),
        "midpoint_reference": side.get("midpoint"),
        "no_vig_probability": no_vig,
        "observed_at_utc": best.get("observed_at_utc"),
        "timestamp_valid": bool(best.get("timestamp_valid", False)),
        "provider": "polymarket_us",
    }


def _team_matches(team_name: str, side_description: str) -> bool:
    """True when a Polymarket side description denotes this team.

    Handles nickname vs full-name mismatches: "Storm" ~ "Seattle Storm",
    "Toronto" ~ "Toronto Tempo". Exact match first; otherwise the shorter
    string must appear as a whole-word phrase inside the longer one.
    """
    team = " ".join(team_name.casefold().split())
    desc = " ".join(side_description.casefold().split())
    if not team or not desc:
        return False
    if team == desc:
        return True
    shorter, longer = (desc, team) if len(desc) <= len(team) else (team, desc)
    return f" {shorter} " in f" {longer} "


def _feature_hash(
    sport: str,
    game_date: str,
    event_id: str,
    features: Mapping[str, float | int],
) -> str:
    payload = {
        "sport": sport,
        "as_of_date": game_date,
        "event_id": event_id,
        "features": dict(features),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _teams(event: Mapping[str, Any]) -> tuple[str, str]:
    competitors = event["competitions"][0]["competitors"]
    by_side = {item["homeAway"]: item["team"]["displayName"] for item in competitors}
    return str(by_side["away"]), str(by_side["home"])
