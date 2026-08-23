"""Point-in-time forward moneyline forecasts from audited learned artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .data_sources.espn import _probable
from .domain import EASTERN, parse_utc
from .features.base import FeatureStore
from .features.batter_offense import matchup_offense_pit_gap
from .features.bullpen import FATIGUE_WINDOW_DAYS, bullpen_profile, team_recent_relief_lines
from .features.elo_ratings import build_elo
from .features.mlb_player_availability import FEATURE_NAMES as MLB_AVAILABILITY_FEATURE_NAMES
from .features.mlb_player_availability import (
    PITCHING_STAFF_FEATURE_NAMES as MLB_PITCHING_STAFF_FEATURE_NAMES,
)
from .features.mlb_player_availability import (
    POSITION_PLAYER_FEATURE_NAMES as MLB_POSITION_PLAYER_FEATURE_NAMES,
)
from .features.mlb_player_availability import (
    matchup_pitching_staff_availability,
    matchup_position_player_availability,
)
from .features.mlb_player_availability import (
    matchup_player_availability as matchup_mlb_player_availability,
)
from .features.park_factors_pit import park_factor_at
from .features.player_availability import FEATURE_NAMES as AVAILABILITY_FEATURE_NAMES
from .features.player_availability import matchup_player_availability
from .features.schedule_load import matchup_schedule_load
from .features.starter_history import starter_era_gap_live, starter_fip_gap_live, starter_kbb_gap_live
from .features.team_runs import pitcher_era_gap_from_history
from .features.trends import TrendEngine
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
    home_starter_name: str | None = None,
    away_starter_name: str | None = None,
) -> tuple[dict[str, float], tuple[str, ...]]:
    unavailable: list[str] = []
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
        "travel_tz_displacement",
        "schedule_available",
    }
    if wanted & schedule_names:
        schedule = matchup_schedule_load(history, home_team, away_team, event_start)
        features.update({name: value for name, value in schedule.items() if name in wanted})
    if "pitcher_era_gap" in wanted:
        # Same definition as training (features/team_runs): rolling team
        # runs-allowed gap from prior cached games. Never an ESPN starter ERA —
        # that was a different quantity and caused train/serve skew.
        features["pitcher_era_gap"] = pitcher_era_gap_from_history(history, home_team, away_team)
    if "starter_era_gap" in wanted:
        try:
            if not home_starter_name or not away_starter_name:
                raise ValueError("NO_CALL_STARTER_ERA_GAP_NO_CONFIRMED_STARTER")
            features["starter_era_gap"] = starter_era_gap_live(
                home_starter_name, away_starter_name, event_start
            )
        except ValueError as error:
            features["starter_era_gap"] = 0.0
            unavailable.append(str(error).split(":", 1)[0].strip() or "starter_era_gap_unavailable")
            logger.warning("starter_era_gap unavailable for %s: %s", event_id, error)
    if "starter_fip_gap" in wanted:
        # F-68 (2026-08-05): FIP pipeline — same point-in-time contract as ERA
        # but uses FIP instead. Locked-holdout shows +1pp hit rate, -39% ECE,
        # +11 units vs ERA. Wire alongside ERA for v9+ artifacts.
        try:
            if not home_starter_name or not away_starter_name:
                raise ValueError("NO_CALL_STARTER_FIP_GAP_NO_CONFIRMED_STARTER")
            features["starter_fip_gap"] = starter_fip_gap_live(
                home_starter_name, away_starter_name, event_start
            )
        except ValueError as error:
            features["starter_fip_gap"] = 0.0
            unavailable.append(str(error).split(":", 1)[0].strip() or "starter_fip_gap_unavailable")
            logger.warning("starter_fip_gap unavailable for %s: %s", event_id, error)
    if "starter_kbb_gap" in wanted:
        # Same point-in-time contract as starter_era_gap/starter_fip_gap but
        # computes (K-BB)/IP per starter from the same snapshot data.
        try:
            if not home_starter_name or not away_starter_name:
                raise ValueError("NO_CALL_STARTER_KBB_GAP_NO_CONFIRMED_STARTER")
            features["starter_kbb_gap"] = starter_kbb_gap_live(
                home_starter_name, away_starter_name, event_start
            )
        except ValueError as error:
            features["starter_kbb_gap"] = 0.0
            unavailable.append(str(error).split(":", 1)[0].strip() or "starter_kbb_gap_unavailable")
            logger.warning("starter_kbb_gap unavailable for %s: %s", event_id, error)
    if "bullpen_weakness_gap" in wanted:
        # Real per-team relief-appearance history (mlb_statsapi.py's boxscore
        # snapshots), same functions Measured Edge already serves live with
        # (features/bullpen.py) -- NOT validation.py's _load_bullpen_map,
        # which is a historical event_id crosswalk with no path to a future
        # game. home-minus-away, same sign convention as every other gap
        # feature in this module.
        home_weakness = bullpen_profile(team_recent_relief_lines(home_team, event_start))[
            "bullpen_weakness_index"
        ]
        away_weakness = bullpen_profile(team_recent_relief_lines(away_team, event_start))[
            "bullpen_weakness_index"
        ]
        features["bullpen_weakness_gap"] = round(home_weakness - away_weakness, 6)
    if "bullpen_fatigue_gap" in wanted:
        # Bullpen fatigue = total recent relief innings per team.
        # home-minus-away: positive = home bullpen has thrown more innings
        # recently (more fatigued), negative = away bullpen more fatigued.
        # Uses the same relief-appearance index as bullpen_weakness_gap
        # but sums innings rather than computing ERA/weakness-index. The
        # trailing window is FATIGUE_WINDOW_DAYS calendar days, identical to
        # validation.py's training-time map (2026-08-13 audit: this used to
        # be a last-N-games lookback with no calendar cap -- real skew).
        home_relief = team_recent_relief_lines(home_team, event_start, lookback_days=FATIGUE_WINDOW_DAYS)
        away_relief = team_recent_relief_lines(away_team, event_start, lookback_days=FATIGUE_WINDOW_DAYS)
        home_fatigue = sum(float(line.get("innings", 0)) for line in home_relief)
        away_fatigue = sum(float(line.get("innings", 0)) for line in away_relief)
        features["bullpen_fatigue_gap"] = round(home_fatigue - away_fatigue, 6)
    if "offense_pit_gap" in wanted:
        # Batter PIT priors (v9 Phase 3): calls the exact same function
        # validation.py's training-time _offense_pit_gap crosswalk delegates
        # to (features.batter_offense.matchup_offense_pit_gap) -- both sides
        # only need games strictly before the decision time, so there is no
        # separate live-serving reimplementation to keep in parity.
        gap, available = matchup_offense_pit_gap(home_team, away_team, event_start)
        features["offense_pit_gap"] = gap
        if not available:
            unavailable.append("offense_pit_gap_unavailable")
    if "residual_trend_gap" in wanted:
        # Elo-residualized trend: how much the home team's recent actual
        # win rate diverges from Elo's expectation. IDENTICAL to
        # validation.py's _trailing_home_rate plus the >=10-team-game gate:
        # same 30-day window, same home_team filter, same tie exclusion,
        # same 0.0 fallback. (2026-08-13 audit: the training side used to be
        # a league-wide rate -- a real train/serve skew; now team-specific.)
        cutoff = event_start.astimezone(EASTERN).date() - timedelta(days=30)
        game_date_dt = event_start.astimezone(EASTERN).date()
        recent_home = [
            g
            for g in history
            if cutoff <= g.start.astimezone(EASTERN).date() < game_date_dt
            and g.home_score != g.away_score
            and g.home_team == home_team
        ]
        if len(recent_home) >= 10:
            home_win_pct = sum(g.home_score > g.away_score for g in recent_home) / len(recent_home)
            features["residual_trend_gap"] = round(home_win_pct - features.get("elo_probability", 0.5), 6)
        else:
            features["residual_trend_gap"] = 0.0
    if "trailing_home_win_rate_30d" in wanted or "elo_neutral_probability" in wanted:
        # elo_trend_adaptive_hfa's two features were train-only until
        # 2026-08-16 (feature audit): the variant could never serve. Same
        # 30-day home-team rate definition as the residual_trend_gap
        # branch above and validation.py's _trailing_home_rate — raw
        # value here (the variant's own coefficients decide gating).
        if "trailing_home_win_rate_30d" in wanted:
            cutoff = event_start.astimezone(EASTERN).date() - timedelta(days=30)
            game_date_dt = event_start.astimezone(EASTERN).date()
            recent_home = [
                g
                for g in history
                if cutoff <= g.start.astimezone(EASTERN).date() < game_date_dt
                and g.home_score != g.away_score
                and g.home_team == home_team
            ]
            features["trailing_home_win_rate_30d"] = (
                sum(g.home_score > g.away_score for g in recent_home) / len(recent_home)
                if recent_home
                else 0.0
            )
        if "elo_neutral_probability" in wanted:
            features["elo_neutral_probability"] = elo.expected_neutral_win(home_team, away_team)
    if wanted & AVAILABILITY_FEATURE_NAMES:
        if sport != "wnba":
            raise ValueError(
                "NO_CALL_AVAILABILITY_UNSUPPORTED: official player-availability feature is WNBA-only"
            )
        try:
            availability = matchup_player_availability(
                data_root=data_root,
                home_team=home_team,
                away_team=away_team,
                game_date=game_date,
                observed_at=observed_at,
                event_start=event_start,
                event_id=event_id,
            )
            features.update({name: value for name, value in availability.items() if name in wanted})
        except (KeyError, TypeError, ValueError) as error:
            for name in wanted & AVAILABILITY_FEATURE_NAMES:
                features[name] = 0.0
            unavailable.append(str(error).split(":", 1)[0].strip() or "wnba_availability_unavailable")
    if wanted & MLB_AVAILABILITY_FEATURE_NAMES:
        # Shadow-only: no production artifact lists these feature names in
        # its own feature_names config today, so this branch is inert in
        # live production until a future artifact opts in explicitly. See
        # features/mlb_player_availability.py's module docstring.
        if sport != "mlb":
            raise ValueError(
                "NO_CALL_MLB_AVAILABILITY_UNSUPPORTED: probable-starter availability feature is MLB-only"
            )
        try:
            mlb_availability = matchup_mlb_player_availability(
                data_root=data_root,
                home_team=home_team,
                away_team=away_team,
                game_date=game_date,
                observed_at=observed_at,
                event_start=event_start,
                event_id=event_id,
            )
            features.update({name: value for name, value in mlb_availability.items() if name in wanted})
        except (KeyError, TypeError, ValueError) as error:
            for name in wanted & MLB_AVAILABILITY_FEATURE_NAMES:
                features[name] = 0.0
            unavailable.append(str(error).split(":", 1)[0].strip() or "mlb_availability_unavailable")
    if wanted & MLB_PITCHING_STAFF_FEATURE_NAMES:
        # Shadow-only, same inert-until-requested design as the starter
        # availability branch above -- see
        # features/mlb_player_availability.py's pitching-staff section.
        if sport != "mlb":
            raise ValueError(
                "NO_CALL_MLB_PITCHING_STAFF_UNSUPPORTED: pitching-staff availability feature is MLB-only"
            )
        try:
            pitching_staff = matchup_pitching_staff_availability(
                data_root=data_root,
                home_team=home_team,
                away_team=away_team,
                observed_at=observed_at,
                event_start=event_start,
            )
            features.update({name: value for name, value in pitching_staff.items() if name in wanted})
        except (KeyError, TypeError, ValueError) as error:
            for name in wanted & MLB_PITCHING_STAFF_FEATURE_NAMES:
                features[name] = 0.0
            unavailable.append(str(error).split(":", 1)[0].strip() or "mlb_pitching_staff_unavailable")
    if wanted & MLB_POSITION_PLAYER_FEATURE_NAMES:
        # Shadow-only, same inert-until-requested design as the other MLB
        # availability branches above.
        if sport != "mlb":
            raise ValueError(
                "NO_CALL_MLB_POSITION_PLAYERS_UNSUPPORTED: position-player availability feature is MLB-only"
            )
        try:
            position_players = matchup_position_player_availability(
                data_root=data_root,
                home_team=home_team,
                away_team=away_team,
                observed_at=observed_at,
                event_start=event_start,
            )
            features.update({name: value for name, value in position_players.items() if name in wanted})
        except (KeyError, TypeError, ValueError) as error:
            for name in wanted & MLB_POSITION_PLAYER_FEATURE_NAMES:
                features[name] = 0.0
            unavailable.append(str(error).split(":", 1)[0].strip() or "mlb_position_players_unavailable")
    _init_providers()
    if "park_factor_pit" in wanted:
        # PIT-correct empirical park factor from cached history -- exactly
        # validation.py's training definition (park_factor_at(home_team,
        # day, games_data=history)). Registered per-call because providers
        # don't receive history; the loop below consumes it immediately, so
        # the captured history is always this game's. Inert in production
        # until an artifact's feature_names lists it (repo pattern).
        _FEATURE_PROVIDERS["park_factor_pit"] = lambda h, a, eid, gd, es, _history=history: float(
            park_factor_at(h, es.astimezone(EASTERN).date().isoformat(), games_data=_history).get(
                "park_factor", 1.0
            )
        )
    for name in wanted:
        if name in features:
            continue
        provider = _FEATURE_PROVIDERS.get(name)
        if not provider:
            continue
        try:
            features[name] = provider(home_team, away_team, event_id, game_date, event_start)
        except ValueError:
            # A per-game provider (e.g. probable_starter_era_gap: ESPN hasn't
            # posted both starters yet) can genuinely fail for one game
            # without the game itself being un-forecastable. Default to 0.0
            # (neutral — no advantage either way, same convention as every
            # other era-gap-style feature's "no data" case) and note it on
            # the candidate so the ledger/rationale shows plainly that this
            # one input was missing, rather than silently forecasting on an
            # incomplete basis or dropping the game entirely.
            features[name] = 0.0
            unavailable.append(name)
    return features, tuple(unavailable)


def _init_providers() -> None:
    """Providers for features computable without game history.

    History-dependent features (pitcher_era_gap) are computed inline in
    ``_compute_features`` with the SAME definition as validation. There is no
    ``starter_era_gap`` provider: its only source is a historical map that can
    never contain a future event, so serving it would silently emit 0.0 —
    an artifact requiring it now fails closed instead.

    ``probable_starter_era_gap`` is different: it reads ESPN's own probable-
    starters endpoint (announced pre-game, real starter ERA), not a
    historical map, so it can genuinely serve live. It raises ValueError when
    a game's starters aren't announced yet — _compute_features catches that
    per-provider, defaults the feature to a neutral 0.0, and records the name
    in its returned unavailable-features tuple so the game still gets
    forecast on every OTHER feature rather than being dropped entirely.
    """
    if _FEATURE_PROVIDERS:
        return
    # Lazy imports to avoid circular dependencies
    from .features.park_factors import park_factor as _pf

    _FEATURE_PROVIDERS["park_factor"] = lambda h, a, eid, gd, es: float(_pf(h).get("park_factor", 1.0))

    from .features.weather import live_weather

    # Pass the real event start (not "now") -- a slate built hours before
    # first pitch (e.g. a morning cron run) must get the forecast for game
    # time, not the current-moment reading; live_weather's own hour-
    # alignment logic expects exactly this, matching validation.py's
    # training-time lookup which keys off game.start. Previously silently
    # ignored, a real train/serve skew (found 2026-07-31).
    _FEATURE_PROVIDERS["weather_factor"] = lambda h, a, eid, gd, es: float(
        live_weather(h, es.isoformat()).get("weather_run_factor", 1.0)
    )

    from .data_sources.espn_probables import espn_pitcher_era_gap

    _FEATURE_PROVIDERS["probable_starter_era_gap"] = lambda h, a, eid, gd, es: espn_pitcher_era_gap(
        eid, h, a, gd.replace("-", "")
    )


def _build_basis(
    features: dict[str, float], home_trend: Any, away_trend: Any, history_games: int
) -> dict[str, float | int]:
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
    unavailable_features: tuple[str, ...] = ()

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
    leagues: tuple[str, ...] | None = None,
) -> tuple[list[LearnedForwardCandidate], list[dict[str, str]], int]:
    """Build scheduled-game decisions with the exact validation feature basis.

    History is cut off at midnight before ``game_date``. This intentionally
    mirrors the complete-date walk-forward audit and prevents same-day leakage.
    Market prices are not inputs.

    ``leagues`` is required for sports that span more than one ESPN league key
    (soccer: EPL/LA_LIGA/BUNDESLIGA/.../WORLD_CUP). Every other learned sport
    maps 1:1 to a single ESPN league equal to its own name (e.g. "mlb" ->
    "MLB") and passes ``leagues=None`` to keep that single-scoreboard call.
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
            f"{key} requires {minimum_history_games}+ cached games before {game_date}; found {len(history)}"
        )
    espn_leagues = leagues or (key.upper(),)
    events: list[dict[str, Any]] = []
    if len(espn_leagues) == 1:
        events.extend(client.scoreboard(espn_leagues[0], game_date).get("events", []))
    else:
        # Soccer alone passes multiple leagues here (up to 18 -- EPL,
        # LA_LIGA, ... WORLD_CUP); fetching them one at a time made this
        # the single largest sequential ESPN-call chain in the daily
        # pipeline. Each league's scoreboard is an independent read, so
        # fan them out concurrently the same way forward.py's per-event
        # feature fetch was parallelized.
        with ThreadPoolExecutor(max_workers=min(8, len(espn_leagues))) as pool:
            scoreboards = pool.map(lambda league: client.scoreboard(league, game_date), espn_leagues)
        for scoreboard in scoreboards:
            events.extend(scoreboard.get("events", []))
    wanted = set(artifact.raw.get("market_models", {}).get("moneyline", {}).get("feature_names", []))
    if "weather_factor" in wanted:
        # The per-event loop below is sequential and each event's
        # weather_factor otherwise makes its own blocking Open-Meteo call
        # inline (via _FEATURE_PROVIDERS) -- warm every distinct home team's
        # forecast concurrently up front instead of one HTTP round trip per
        # event in turn.
        from .features.weather import prefetch_live_weather

        home_teams = []
        for event in events:
            try:
                home_teams.append(_teams(event)[1])
            except (KeyError, TypeError, ValueError):
                continue
        prefetch_live_weather(home_teams)
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
            away_starter_name: str | None = None
            home_starter_name: str | None = None
            if key == "mlb":
                # Caution gate, operator directive 2026-08-04: even a live
                # provider for starter_era_gap (added same day, see below)
                # needs a confirmed probable starter identity to look up --
                # an unresolved starter has nothing to key that lookup on,
                # same requirement Measured Edge's reconstructed_features
                # already enforces for spread/total.
                competitors = {
                    item.get("homeAway"): item
                    for item in (event.get("competitions") or [{}])[0].get("competitors", [])
                }
                away_probable = _probable(competitors.get("away") or {})
                home_probable = _probable(competitors.get("home") or {})
                if away_probable is None or home_probable is None:
                    raise ValueError(f"event {event_id} has an unresolved probable starter")
                away_starter_name = (away_probable.get("athlete") or {}).get("displayName")
                home_starter_name = (home_probable.get("athlete") or {}).get("displayName")
            home_trend = trends.team_trend(home_team)
            away_trend = trends.team_trend(away_team)
            if min(home_trend.games_played, away_trend.games_played) < minimum_team_history_games:
                raise ValueError(
                    "insufficient_team_history: "
                    f"{away_team}={away_trend.games_played}, "
                    f"{home_team}={home_trend.games_played}, "
                    f"required={minimum_team_history_games}"
                )
            features, unavailable_features = _compute_features(
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
                home_starter_name=home_starter_name,
                away_starter_name=away_starter_name,
            )
            home_probability = artifact.probability("moneyline", features)
            confidence_threshold = (
                artifact.raw.get("market_models", {}).get("moneyline", {}).get("confidence_threshold", 0.50)
            )

            # ── WNBA availability gate (5pp threshold) ──────────────────────
            availability_notes: list[str] = []
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
                    if int(avail.get("availability_source_conflict_count", 0)) > 0:
                        availability_notes.append("wnba_availability_source_conflict")
                    if delta >= 0.05:
                        features["availability_points_gap"] = points_gap
                        features["availability_adjusted"] = 1.0
                        home_probability = adjusted_home
                except (ValueError, KeyError, TypeError) as exc:
                    warning_code = str(exc).split(":", 1)[0].strip()
                    availability_notes.append(warning_code or "wnba_availability_unavailable")
                    logger.warning(
                        "WNBA availability context unavailable for %s @ %s on %s; "
                        "model call retained with Today warning: %s",
                        away_team,
                        home_team,
                        game_date,
                        exc,
                    )
            unavailable_features = tuple(dict.fromkeys((*unavailable_features, *availability_notes)))

            # Recompute selection/call/action/reason from (possibly adjusted) probability.
            # Operator directive (2026-07-30): every candidate is a real,
            # sized call regardless of the model's own learned confidence
            # threshold -- that threshold is kept on the candidate purely as
            # a reference number (confidence_threshold below) so a human can
            # see how confident the model was and decide for themselves
            # whether to act on it. This was previously a hard gate (below
            # threshold = zero-unit NO_CALL, never reaching the main
            # ledger); removed because it hid real, sized picks from the
            # person meant to make the final bet/no-bet decision.
            selection = "home" if home_probability >= 0.5 else "away"
            model_probability = home_probability if selection == "home" else (1 - home_probability)
            call = True
            cleared_confidence_threshold = model_probability >= confidence_threshold
            action = "QUALIFIED_SHADOW_CALL"
            reason = (
                "CALL_LEARNED_CONFIDENCE"
                if cleared_confidence_threshold
                else "CALL_BELOW_LEARNED_CONFIDENCE_OPERATOR_REVIEW"
            )
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
                    unavailable_features=unavailable_features,
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
    matched_slugs: set[str] = set()
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
            pairing = (_team_matches(away, long_desc) and _team_matches(home, short_desc)) or (
                _team_matches(home, long_desc) and _team_matches(away, short_desc)
            )
            if not pairing:
                continue
            observed_at = str(snap.get("observed_at_utc", ""))
            # Decision prices must be pregame. Without this check, capturing
            # BBO snapshots more than once a day (needed so late-day games
            # get a fresher line than a single morning snapshot) would let a
            # later, in-game/post-game snapshot win the "latest wins" compare
            # below for an earlier game that already started.
            try:
                if observed_at and parse_utc(observed_at) >= parse_utc(candidate.event_start_utc):
                    continue
            except ValueError:
                continue
            matched_slugs.add(slug)
            if best is None or observed_at > str(best.get("observed_at_utc", "")):
                best = snap
    if best is None:
        return None
    if len(matched_slugs) > 1:
        # Two distinct contracts matched the same team pair on one date — an
        # MLB doubleheader. Pricing both games off whichever snapshot is
        # newest would mis-price one of them; skip instead of guessing.
        return None
    selected_team = home if candidate.selection == "home" else away
    long_desc = str((best.get("long") or {}).get("description", ""))
    short_desc = str((best.get("short") or {}).get("description", ""))
    matches_long = _team_matches(selected_team, long_desc)
    matches_short = _team_matches(selected_team, short_desc)
    if matches_long == matches_short:
        # Same-city ambiguity ("New York" matches both sides) or no match at
        # all — the side cannot be resolved uniquely. Never default to long.
        return None
    side_key = "long" if matches_long else "short"
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
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _teams(event: Mapping[str, Any]) -> tuple[str, str]:
    competitors = event["competitions"][0]["competitors"]
    by_side = {item["homeAway"]: item["team"]["displayName"] for item in competitors}
    return str(by_side["away"]), str(by_side["home"])
