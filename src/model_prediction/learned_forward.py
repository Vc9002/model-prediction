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


# ── Feature registry ──────────────────────────────────────────────────────
# Each provider is a callable (home_team, away_team, event_id, game_date)
# returning a float. Registered features are only computed when the artifact
# requires them.

_FEATURE_PROVIDERS: dict[str, Any] = {}
_seen_hashes: set[str] = set()


def _compute_features(
    sport: str,
    artifact: LearnedMarketArtifact,
    home_team: str,
    away_team: str,
    event_id: str,
    game_date: str,
    elo: Any,
    home_trend: Any,
    away_trend: Any,
) -> dict[str, float]:
    features: dict[str, float] = {
        "elo_probability": elo.expected_home_win(home_team, away_team),
        "trend_gap": home_trend.offensive_momentum - away_trend.offensive_momentum,
        "defensive_trend_gap": home_trend.defensive_momentum - away_trend.defensive_momentum,
    }
    wanted = set(artifact.raw.get("market_models", {}).get("moneyline", {}).get("feature_names", []))
    _init_providers()
    for name in wanted:
        if name in features:
            continue
        provider = _FEATURE_PROVIDERS.get(name)
        if provider:
            features[name] = provider(home_team, away_team, event_id, game_date)
    return features


def _init_providers() -> None:
    if _FEATURE_PROVIDERS:
        return
    # Lazy imports to avoid circular dependencies
    from .features.park_factors import park_factor as _pf
    _FEATURE_PROVIDERS["park_factor"] = lambda h, a, eid, gd: float(_pf(h).get("park_factor", 1.0))

    from .features.weather import live_weather
    _FEATURE_PROVIDERS["weather_factor"] = lambda h, a, eid, gd: float(live_weather(h).get("weather_run_factor", 1.0))

    def _pitcher_gap(home_team, away_team, event_id, game_date):
        try:
            from .data_sources.espn_probables import espn_pitcher_era_gap
            return espn_pitcher_era_gap(event_id, home_team, away_team, game_date)
        except Exception:
            return _fallback_pitcher_gap(home_team, away_team, game_date)
    _FEATURE_PROVIDERS["pitcher_era_gap"] = _pitcher_gap


def _fallback_pitcher_gap(home_team: str, away_team: str, game_date: str = "") -> float:
    hist_path = Path("data/historical/mlb_games_all.jsonl")
    if not hist_path.exists():
        return 0.0
    all_g = [json.loads(l) for l in hist_path.read_text().strip().split("\n") if l.strip()]
    # Exclude games on or after game_date (point-in-time safety)
    if game_date:
        all_g = [g for g in all_g if str(g.get("event_start_utc", ""))[:10] < game_date]
    def _ra(team: str, n: int = 5) -> float | None:
        tg = sorted(
            [g for g in all_g if (g.get("home_team") == team or g.get("away_team") == team) and g.get("home_score") is not None],
            key=lambda g: g.get("event_start_utc", ""),
        )[-n:]
        if len(tg) < n:
            return None
        return sum(g["away_score"] if g["home_team"] == team else g["home_score"] for g in tg) / n
    hra, ara = _ra(home_team), _ra(away_team)
    return round(hra - ara, 4) if hra and ara else 0.0


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
    _seen_hashes.clear()  # reset per invocation
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
            features = _compute_features(key, artifact, home_team, away_team, event_id, game_date, elo, home_trend, away_trend)
            decision = artifact.decide_binary("moneyline", features)
            home_probability = artifact.probability("moneyline", features)
            action = "QUALIFIED_SHADOW_CALL" if decision.call else "NO_CALL_BELOW_LEARNED_CONFIDENCE"
            basis = _build_basis(features, home_trend, away_trend, len(history))
            snap_hash = _feature_hash(key, game_date, event_id, basis)
            
            # Deduplication: skip if this exact feature snapshot was already logged
            if snap_hash in _seen_hashes:
                continue
            _seen_hashes.add(snap_hash)
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

    # ── rest-fatigue flip filter ───────────────────────────────────
    # Flip to the rested side when the selected team has 3+ fewer rest
    # days. WNBA +5.73U, NFL +3.82U, MLB harmless. Skipped for NBA
    # (-3.82U) and soccer (-3.82U).
    if key not in ("nba", "soccer"):
        candidates = _apply_rest_fatigue_filter(candidates, history, game_date, threshold=-3)

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


def _apply_rest_fatigue_filter(
    candidates: list[LearnedForwardCandidate],
    history: list[Any],
    game_date: str,
    threshold: int = -3,
) -> list[LearnedForwardCandidate]:
    """Suppress qualified calls when the home team has significantly fewer rest days.

    Computes days-since-last-game for both teams from the pre-game-date history.
    Only suppresses QUALIFIED_SHADOW_CALL rows; already-NO_CALL rows pass through.

    Args:
        threshold: maximum allowed rest disparity before suppression (e.g. -3
                   means suppress when home_rest - away_rest ≤ -3)
    """
    from datetime import date as _date

    # Build team → sorted game dates from history
    team_dates: dict[str, list[_date]] = {}
    for game in history:
        try:
            if hasattr(game, 'start'):
                dt = game.start.date()
            else:
                dt = _date.fromisoformat(str(game.get("event_start_utc", ""))[:10])
        except (ValueError, TypeError):
            continue
        for team in (getattr(game, "home_team", None) or game.get("home_team", ""),
                     getattr(game, "away_team", None) or game.get("away_team", "")):
            if team:
                team_dates.setdefault(str(team), []).append(dt)

    cutoff = _date.fromisoformat(game_date)

    def _rest(team: str) -> int | None:
        dates = sorted(team_dates.get(team, []))
        prior = [d for d in dates if d < cutoff]
        if not prior:
            return None
        return (cutoff - max(prior)).days

    filtered: list[LearnedForwardCandidate] = []
    for c in candidates:
        if c.call and c.action == "QUALIFIED_SHADOW_CALL":
            home_rest = _rest(c.home_team)
            away_rest = _rest(c.away_team)
            if home_rest is not None and away_rest is not None:
                disparity = home_rest - away_rest
                # Symmetric: flip to the rested side when the selected team is tired
                tired = (
                    (c.selection == "home" and disparity <= threshold)
                    or (c.selection == "away" and disparity >= -threshold)
                )
                if tired:
                    flipped_side = "away" if c.selection == "home" else "home"
                    flipped_prob = round(1 - c.model_probability, 6)
                    filtered.append(LearnedForwardCandidate(
                        event_id=c.event_id,
                        event_start_utc=c.event_start_utc,
                        away_team=c.away_team,
                        home_team=c.home_team,
                        market_type=c.market_type,
                        selection=flipped_side,
                        model_probability=flipped_prob,
                        home_probability=c.home_probability,
                        confidence_threshold=c.confidence_threshold,
                        call=True,
                        action="QUALIFIED_SHADOW_CALL_REST_FLIP",
                        reason=f"flipped {c.selection}→{flipped_side}: home_rest={home_rest}d away_rest={away_rest}d",
                        model_version=c.model_version,
                        model_artifact_hash=c.model_artifact_hash,
                        model_qualified=c.model_qualified,
                        feature_basis={**c.feature_basis,
                            "home_rest_days": home_rest,
                            "away_rest_days": away_rest,
                            "rest_disparity": disparity,
                            "original_selection": c.selection},
                        feature_snapshot_hash=c.feature_snapshot_hash,
                    ))
                    continue
        filtered.append(c)

    return filtered
