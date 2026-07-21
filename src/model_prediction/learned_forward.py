"""Point-in-time forward moneyline forecasts from audited learned artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .domain import parse_utc
from .features.base import FeatureStore
from .features.elo_ratings import build_elo
from .features.schedule_load import matchup_schedule_load
from .features.trends import TrendEngine
from .features.player_availability import FEATURE_NAMES as AVAILABILITY_FEATURE_NAMES
from .features.player_availability import matchup_player_availability
from .features.team_runs import pitcher_era_gap_from_history
from .models.learned_market import LearnedMarketArtifact
from .wnba_availability_evaluation import adjust_home_probability, historical_margin_sigma

logger = logging.getLogger(__name__)


# ── Feature registry ──────────────────────────────────────────────────────
# Each provider is a callable (home_team, away_team, event_id, game_date)
# returning a float. Registered features are only computed when the artifact
# requires them.

_FEATURE_PROVIDERS: dict[str, Any] = {}
_slate_cache: dict[tuple, tuple] = {}


def _compute_features(
    sport: str,
    artifact: LearnedMarketArtifact,
    home_team: str,
    away_team: str,
    event_id: str,
    game_date: str,
    event_start: datetime,
    history: list[Any],
    elo: Any,
    home_trend: Any,
    away_trend: Any,
    data_root: Path,
    observed_at: datetime,
) -> dict[str, float]:
    features: dict[str, float] = {
        "elo_probability": elo.expected_home_win(home_team, away_team),
        "trend_gap": home_trend.offensive_momentum - away_trend.offensive_momentum,
        "defensive_trend_gap": home_trend.defensive_momentum - away_trend.defensive_momentum,
    }
    wanted = set(artifact.raw.get("market_models", {}).get("moneyline", {}).get("feature_names", []))
    if "consistency_gap" in wanted:
        features["consistency_gap"] = home_trend.consistency - away_trend.consistency
    if "hot_cold_gap" in wanted:
        features["hot_cold_gap"] = home_trend.hot_cold_score - away_trend.hot_cold_score
    schedule_names = {
        "rest_disparity",
        "back_to_back_gap",
        "games_last_7_gap",
        "schedule_available",
    }
    if wanted & schedule_names:
        schedule = matchup_schedule_load(history, home_team, away_team, event_start)
        features.update({name: value for name, value in schedule.items() if name in wanted})
    if "pitcher_era_gap" in wanted:
        # Same definition as training (features/team_runs): rolling team
        # runs-allowed gap from prior cached games. Never an ESPN starter ERA —
        # that was a different quantity and caused train/serve skew.
        features["pitcher_era_gap"] = pitcher_era_gap_from_history(
            history, home_team, away_team
        )
    if wanted & AVAILABILITY_FEATURE_NAMES:
        if sport != "wnba":
            raise ValueError(
                "NO_CALL_AVAILABILITY_UNSUPPORTED: official player-availability feature is WNBA-only"
            )
        availability = matchup_player_availability(
            data_root=data_root,
            home_team=home_team,
            away_team=away_team,
            game_date=game_date,
            observed_at=observed_at,
            event_start=event_start,
            event_id=event_id,
        )
        features.update(
            {name: value for name, value in availability.items() if name in wanted}
        )
    _init_providers()
    for name in wanted:
        if name in features:
            continue
        provider = _FEATURE_PROVIDERS.get(name)
        if provider:
            features[name] = provider(home_team, away_team, event_id, game_date)
    return features


def _init_providers() -> None:
    """Providers for features computable without game history.

    History-dependent features (pitcher_era_gap) are computed inline in
    ``_compute_features`` with the SAME definition as validation. There is no
    ``starter_era_gap`` provider: its only source is a historical map that can
    never contain a future event, so serving it would silently emit 0.0 —
    an artifact requiring it now fails closed instead.
    """
    if _FEATURE_PROVIDERS:
        return
    # Lazy imports to avoid circular dependencies
    from .features.park_factors import park_factor as _pf
    _FEATURE_PROVIDERS["park_factor"] = lambda h, a, eid, gd: float(_pf(h).get("park_factor", 1.0))

    from .features.weather import live_weather
    _FEATURE_PROVIDERS["weather_factor"] = lambda h, a, eid, gd: float(live_weather(h).get("weather_run_factor", 1.0))


def _build_basis(features: dict[str, float], home_trend: Any, away_trend: Any, history_games: int) -> dict[str, float | int]:
    basis: dict[str, float | int] = {
        "history_games": history_games,
        "home_history_games": home_trend.games_played,
        "away_history_games": away_trend.games_played,
    }
    for k, v in features.items():
        basis[k] = round(v, 10)
    return basis


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
    # Cache: primary and flat forecasts call with identical params
    cache_key = (key, game_date, str(artifact_path), observed_at.isoformat())
    if cache_key in _slate_cache:
        return _slate_cache[cache_key]  # type: ignore[return-value]
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
    seen_hashes: set[str] = set()
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
            features = _compute_features(
                key,
                artifact,
                home_team,
                away_team,
                event_id,
                game_date,
                start,
                history,
                elo,
                home_trend,
                away_trend,
                store.data_root,
                observed_at,
            )
            home_probability = artifact.probability("moneyline", features)
            confidence_threshold = artifact.raw.get("market_models", {}).get("moneyline", {}).get("confidence_threshold", 0.50)

            # ── WNBA availability gate (5pp threshold) ──────────────────────
            if key == "wnba":
                try:
                    avail = matchup_player_availability(
                        data_root=store.data_root,
                        home_team=home_team,
                        away_team=away_team,
                        game_date=game_date,
                        observed_at=observed_at,
                        event_start=start,
                        event_id=event_id,
                    )
                    points_gap = float(avail.get("availability_points_gap", 0.0))
                    # Cache margin_sigma keyed on (game_date, observed_at)
                    if not hasattr(build_learned_moneyline_slate, "_wnba_sigma_cache"):
                        build_learned_moneyline_slate._wnba_sigma_cache = {}  # type: ignore[attr-defined]
                    cache = build_learned_moneyline_slate._wnba_sigma_cache  # type: ignore[attr-defined]
                    sigma_key = (game_date, observed_at.isoformat())
                    if sigma_key not in cache:
                        cache[sigma_key] = historical_margin_sigma(store, observed_at)
                    sigma = cache[sigma_key]
                    adjusted_home = adjust_home_probability(home_probability, points_gap, sigma)
                    delta = abs(adjusted_home - home_probability)
                    if delta >= 0.05:
                        features["availability_points_gap"] = points_gap
                        features["availability_adjusted"] = 1.0
                        home_probability = adjusted_home
                except (ValueError, KeyError, TypeError) as exc:
                    logger.debug(
                        "WNBA availability gate skipped for %s @ %s on %s: %s",
                        away_team, home_team, game_date, exc,
                    )

            # Recompute selection/call/action/reason from (possibly adjusted) probability
            selection = "home" if home_probability >= 0.5 else "away"
            model_probability = home_probability if selection == "home" else (1 - home_probability)
            call = model_probability >= confidence_threshold
            action = "QUALIFIED_SHADOW_CALL" if call else "NO_CALL_BELOW_LEARNED_CONFIDENCE"
            reason = "CALL_LEARNED_CONFIDENCE" if call else "NO_CALL_BELOW_LEARNED_CONFIDENCE"
            basis = _build_basis(features, home_trend, away_trend, len(history))
            snap_hash = _feature_hash(key, game_date, event_id, basis)
            
            # Deduplication: skip if this exact feature snapshot was already logged
            if snap_hash in seen_hashes:
                continue
            seen_hashes.add(snap_hash)
            candidates.append(
                LearnedForwardCandidate(
                    event_id=event_id,
                    event_start_utc=str(event["date"]),
                    away_team=away_team,
                    home_team=home_team,
                    market_type="moneyline",
                    selection=selection,
                    model_probability=round(model_probability, 6),
                    home_probability=round(home_probability, 6),
                    confidence_threshold=confidence_threshold,
                    call=call,
                    action=action,
                    reason=reason,
                    model_version=artifact.version,
                    model_artifact_hash=artifact.hash,
                    model_qualified=artifact.qualified,
                    feature_basis=basis,
                    feature_snapshot_hash=_feature_hash(key, game_date, event_id, basis),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            skipped.append({"event_id": event_id, "reason": str(error)})

    result = (candidates, skipped, len(events))
    _slate_cache[cache_key] = result
    return result


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
