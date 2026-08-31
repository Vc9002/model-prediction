"""College Football (NCAAF) Unified Production Model.

Generates coherent point-in-time predictions for:
1. Moneyline: Calibrated win probabilities derived from the joint scoring distribution.
2. Spread: Cover probabilities with exact push support and discrete key-number modeling.
3. Total: Over/Under probabilities with exact push support and weather/pace interactions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain import parse_utc, utc_now
from ..features.base import GameRecord
from ..features.cfb_features import (
    CFB_BASELINE_MARGIN_SD,
    CFB_BASELINE_TOTAL,
    CFB_BASELINE_TOTAL_SD,
    CFB_DEFAULT_HOME_ADVANTAGE_POINTS,
    CFBFeatureExtractor,
    CFBMatchupFeatures,
)
from ..pricing import implied_probability
from .base import GamePrediction
from .cfb_distribution import (
    CFBDistributionType,
    CFBJointDistributionEngine,
    CFBJointMarketProbabilities,
)

MODEL_VERSION = "college-football-v1"
CFB_SPREAD_MODEL_VERSION = "cfb-spread-v1"
CFB_TOTAL_MODEL_VERSION = "cfb-total-v1"

# Executable ask for a side laid at the standard -110 spread/total price.
# Matches the 0.5238 constant the research pipeline prices against, so a
# backtested edge and a served edge mean the same thing.
STANDARD_LAY_ASK = round(implied_probability(-110), 6)


@dataclass(frozen=True)
class UpcomingCFBGame:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    spread_away_line: float | None = None  # e.g. +7.5 (Home favored by 7.5 -> spread_home_line = -7.5)
    spread_home_line: float | None = None  # e.g. -7.5
    total_line: float | None = None  # e.g. 54.5
    wind_mph: float | None = None
    temperature_f: float | None = None
    precipitation_in: float | None = None
    is_neutral_site: bool = False
    season_year: int = 2024
    week: int = 1
    qb_starter_prob_away: float = 1.0
    qb_starter_prob_home: float = 1.0


class CollegeFootballModel:
    """Unified College Football model predicting Moneyline, Spread, and Total."""

    def __init__(
        self,
        version: str = MODEL_VERSION,
        home_advantage_points: float = CFB_DEFAULT_HOME_ADVANTAGE_POINTS,
        margin_sd: float = CFB_BASELINE_MARGIN_SD,
        total_sd: float = CFB_BASELINE_TOTAL_SD,
        distribution_type: CFBDistributionType = CFBDistributionType.NEGATIVE_BINOMIAL,
    ) -> None:
        self.version = version
        self.home_advantage_points = home_advantage_points
        self.margin_sd = margin_sd
        self.total_sd = total_sd
        self.distribution_type = distribution_type
        self.extractor = CFBFeatureExtractor(
            home_advantage_points=home_advantage_points,
            margin_sd=margin_sd,
            total_sd=total_sd,
        )
        self.distribution_engine = CFBJointDistributionEngine(
            distribution_type=distribution_type,
            margin_sd=margin_sd,
            total_sd=total_sd,
        )

    def predict_matchup(
        self,
        history: Sequence[Any],
        game: UpcomingCFBGame,
        elo_book: Any | None = None,
        trend_engine: Any | None = None,
    ) -> list[GamePrediction]:
        """Generate calibrated predictions for moneyline, spread, and total."""
        # 1. Extract PIT Matchup Features
        feat: CFBMatchupFeatures = self.extractor.extract_features(
            history=history,
            away_team=game.away_team,
            home_team=game.home_team,
            event_id=game.event_id,
            game_start_utc=game.event_start_utc,
            season_year=game.season_year,
            week=game.week,
            wind_mph=game.wind_mph,
            temperature_f=game.temperature_f,
            precipitation_in=game.precipitation_in,
            is_neutral_site=game.is_neutral_site,
            qb_starter_prob_away=game.qb_starter_prob_away,
            qb_starter_prob_home=game.qb_starter_prob_home,
        )

        # 2. Resolve Market Lines
        if game.spread_home_line is not None:
            sp_home_line = float(game.spread_home_line)
            sp_away_line = -sp_home_line
        elif game.spread_away_line is not None:
            sp_away_line = float(game.spread_away_line)
            sp_home_line = -sp_away_line
        else:
            sp_home_line = round(-feat.projected_margin_home * 2.0) / 2.0
            sp_away_line = -sp_home_line

        tot_line = float(game.total_line) if game.total_line is not None else feat.projected_total

        # 3. Derive Coherent Joint Probabilities
        joint_probs: CFBJointMarketProbabilities = self.distribution_engine.compute_market_probabilities(
            mu_home=feat.projected_home_points,
            mu_away=feat.projected_away_points,
            spread_home_line=sp_home_line,
            total_line=tot_line,
        )

        predictions: list[GamePrediction] = []

        # 1. Moneyline Prediction
        p_home_win = joint_probs.p_home_win
        p_away_win = joint_probs.p_away_win
        predictions.append(
            GamePrediction(
                event_id=game.event_id,
                event_start_utc=game.event_start_utc,
                league="NCAAF",
                away_team=game.away_team,
                home_team=game.home_team,
                market_type="moneyline",
                line=None,
                probabilities={
                    "home": round(p_home_win, 6),
                    "away": round(p_away_win, 6),
                },
                uncertainty=feat.uncertainty,
                model_version=self.version,
                feature_basis=feat.to_dict(),
                rationale=(
                    f"CFB Joint Model ({self.distribution_type.value}): "
                    f"proj score {game.away_team} {feat.projected_away_points:.1f} @ "
                    f"{game.home_team} {feat.projected_home_points:.1f} (Margin {feat.projected_margin_home:+.1f}). "
                    f"Possessions: {feat.projected_possessions:.1f}, HFA: +{feat.home_field_advantage_points:.1f} pts."
                ),
            )
        )

        # 2. Spread Prediction
        predictions.append(
            GamePrediction(
                event_id=game.event_id,
                event_start_utc=game.event_start_utc,
                league="NCAAF",
                away_team=game.away_team,
                home_team=game.home_team,
                market_type="spread",
                line=sp_away_line,
                probabilities={
                    "away": round(joint_probs.p_away_cover, 6),
                    "home": round(joint_probs.p_home_cover, 6),
                    "push": round(joint_probs.p_push_spread, 6),
                },
                uncertainty=feat.uncertainty,
                model_version=CFB_SPREAD_MODEL_VERSION,
                feature_basis=feat.to_dict(),
                rationale=(
                    f"Projected home margin {feat.projected_margin_home:+.1f} pts. "
                    f"Spread: Home {sp_home_line:+.1f} / Away {sp_away_line:+.1f} -> "
                    f"P(Away cover)={joint_probs.p_away_cover:.3f}, P(Home cover)={joint_probs.p_home_cover:.3f}, P(Push)={joint_probs.p_push_spread:.3f}."
                ),
            )
        )

        # 3. Total Prediction
        predictions.append(
            GamePrediction(
                event_id=game.event_id,
                event_start_utc=game.event_start_utc,
                league="NCAAF",
                away_team=game.away_team,
                home_team=game.home_team,
                market_type="total",
                line=tot_line,
                probabilities={
                    "over": round(joint_probs.p_over, 6),
                    "under": round(joint_probs.p_under, 6),
                    "push": round(joint_probs.p_push_total, 6),
                },
                uncertainty=feat.uncertainty,
                model_version=CFB_TOTAL_MODEL_VERSION,
                feature_basis=feat.to_dict(),
                rationale=(
                    f"Projected total {feat.projected_total:.1f} pts (Weather adj: {feat.weather_total_adjustment:+.1f} pts). "
                    f"Total line {tot_line:.1f} -> "
                    f"P(Over)={joint_probs.p_over:.3f}, P(Under)={joint_probs.p_under:.3f}, P(Push)={joint_probs.p_push_total:.3f}."
                ),
            )
        )

        return predictions

    def predict_games(
        self,
        history: Sequence[Any],
        upcoming: Sequence[UpcomingCFBGame],
    ) -> list[GamePrediction]:
        """Predict a slate of upcoming CFB games."""
        all_preds: list[GamePrediction] = []
        for g in upcoming:
            all_preds.extend(self.predict_matchup(history, g))
        return all_preds


def cfb_model() -> CollegeFootballModel:
    """Factory function for production model registry."""
    return CollegeFootballModel()


def cfb_spread_model() -> CollegeFootballModel:
    """Factory function for CFB Spread model."""
    return CollegeFootballModel(version=CFB_SPREAD_MODEL_VERSION)


def cfb_total_model() -> CollegeFootballModel:
    """Factory function for CFB Total model."""
    return CollegeFootballModel(version=CFB_TOTAL_MODEL_VERSION)


def _load_cfb_history(data_root: Path, cutoff_dt: datetime | None = None) -> list[GameRecord]:
    """Load historical completed CFB games from local cache up to cutoff_dt."""
    records: list[GameRecord] = []
    seen_ids: set[str] = set()

    for path in [
        data_root / "processed" / "ncaaf" / "games.jsonl",
        data_root / "historical" / "ncaaf_games_all.jsonl",
    ]:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    g = json.loads(line)
                    eid = str(g.get("event_id"))
                    if eid in seen_ids:
                        continue
                    start_str = g.get("event_start_utc") or g.get("game_start_utc") or ""
                    start_dt = parse_utc(start_str)
                    if cutoff_dt is not None and start_dt >= cutoff_dt:
                        continue
                    away_score = int(
                        g.get("away_score")
                        if g.get("away_score") is not None
                        else (g.get("away") or {}).get("score", 0)
                    )
                    home_score = int(
                        g.get("home_score")
                        if g.get("home_score") is not None
                        else (g.get("home") or {}).get("score", 0)
                    )
                    away_team = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
                    home_team = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""
                    if not away_team or not home_team:
                        continue
                    rec = GameRecord(
                        event_id=eid,
                        event_start_utc=start_str,
                        league="NCAAF",
                        away_team=away_team,
                        home_team=home_team,
                        away_score=away_score,
                        home_score=home_score,
                        season_type=str(g.get("season_type", "regular")),
                        season_year=g.get("season_year"),
                        competition_type=str(g.get("competition_type", "STD")),
                    )
                    records.append(rec)
                    seen_ids.add(eid)
                except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                    continue

    return sorted(records, key=lambda r: r.start)


def build_cfb_slate(
    data_root: str | Path,
    game_date: str,
    client: Any | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build prospective slate for College Football (Moneyline, Spread, Total)."""
    from ..data_sources.espn import ESPNClient

    root = Path(data_root)
    obs = observed_at or utc_now()
    espn = client or ESPNClient()

    norm_date = game_date.replace("-", "")
    iso_date = f"{norm_date[:4]}-{norm_date[4:6]}-{norm_date[6:8]}" if len(norm_date) == 8 else game_date

    # 1. Fetch Scoreboard
    try:
        scoreboard = espn.scoreboard("NCAAF", norm_date)
        events = scoreboard.get("events", [])
    except (KeyError, ValueError, OSError, TypeError):
        events = []

    # 2. Load History
    start_of_day = parse_utc(f"{iso_date}T00:00:00Z")
    history = _load_cfb_history(root, cutoff_dt=start_of_day)

    model = CollegeFootballModel()
    priced_contracts: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for ev in events:
        competition = (ev.get("competitions") or [{}])[0]
        comps = {c.get("homeAway"): c for c in competition.get("competitors", [])}
        away = comps.get("away")
        home = comps.get("home")
        if not away or not home:
            continue

        away_team = (away.get("team") or {}).get("displayName") or ""
        home_team = (home.get("team") or {}).get("displayName") or ""
        if not away_team or not home_team:
            continue

        event_id = str(ev.get("id"))
        event_start_utc = str(ev.get("date"))
        is_neutral = bool(competition.get("neutralSite", False))

        # Extract market odds if available from ESPN or odds sources
        odds_list = competition.get("odds", [])
        spread_away_line = 0.0
        spread_home_line = 0.0
        total_line = CFB_BASELINE_TOTAL
        market_ml_home_prob = None
        market_ml_away_prob = None
        market_spread_away_prob = None
        market_spread_home_prob = None
        market_total_over_prob = None
        market_total_under_prob = None

        if odds_list:
            first_odds = odds_list[0]

            def _safe_odds_int(val: Any) -> int | None:
                if val is None:
                    return None
                s = str(val).strip()
                if s.upper() in ("OFF", "N/A", "NONE", ""):
                    return None
                try:
                    n = int(s)
                    return n if (n <= -100 or n >= 100) else None
                except (ValueError, TypeError):
                    return None

            # Total Line & Odds
            tot_obj = first_odds.get("total") or {}
            ov_n = _safe_odds_int((tot_obj.get("over") or {}).get("close", {}).get("odds"))
            un_n = _safe_odds_int((tot_obj.get("under") or {}).get("close", {}).get("odds"))
            tot_line_str = (
                str(
                    (tot_obj.get("over") or {}).get("close", {}).get("line")
                    or first_odds.get("overUnder")
                    or ""
                )
                .replace("o", "")
                .replace("u", "")
                .strip()
            )
            try:
                if tot_line_str:
                    total_line = float(tot_line_str)
            except (ValueError, TypeError):
                total_line = CFB_BASELINE_TOTAL
            market_total_over_prob = round(implied_probability(ov_n), 6) if ov_n else STANDARD_LAY_ASK
            market_total_under_prob = round(implied_probability(un_n), 6) if un_n else STANDARD_LAY_ASK

            # Point Spread Line & Odds
            ps_obj = first_odds.get("pointSpread") or {}
            h_ps_n = _safe_odds_int((ps_obj.get("home") or {}).get("close", {}).get("odds"))
            a_ps_n = _safe_odds_int((ps_obj.get("away") or {}).get("close", {}).get("odds"))
            h_ps_line_str = str(
                (ps_obj.get("home") or {}).get("close", {}).get("line") or first_odds.get("spread") or ""
            ).strip()
            try:
                if h_ps_line_str:
                    spread_home_line = float(h_ps_line_str)
                    spread_away_line = -spread_home_line
            except (ValueError, TypeError):
                spread_home_line = 0.0
                spread_away_line = 0.0
            market_spread_home_prob = round(implied_probability(h_ps_n), 6) if h_ps_n else STANDARD_LAY_ASK
            market_spread_away_prob = round(implied_probability(a_ps_n), 6) if a_ps_n else STANDARD_LAY_ASK

            # Moneyline parsing if available from ESPN (nested moneyline or top-level)
            ml_obj = first_odds.get("moneyline") or {}
            h_ml_n = _safe_odds_int(
                (ml_obj.get("home") or {}).get("close", {}).get("odds")
                or (first_odds.get("homeTeamOdds") or {}).get("moneyLine")
                or ((first_odds.get("moneylineWinner") or {}).get("home") or {}).get("odds")
            )
            a_ml_n = _safe_odds_int(
                (ml_obj.get("away") or {}).get("close", {}).get("odds")
                or (first_odds.get("awayTeamOdds") or {}).get("moneyLine")
                or ((first_odds.get("moneylineWinner") or {}).get("away") or {}).get("odds")
            )

            if h_ml_n is not None:
                market_ml_home_prob = round(implied_probability(h_ml_n), 6)
            if a_ml_n is not None:
                market_ml_away_prob = round(implied_probability(a_ml_n), 6)

            if market_ml_home_prob is not None and market_ml_away_prob is None:
                market_ml_away_prob = round(1.0 - market_ml_home_prob, 6)
            elif market_ml_away_prob is not None and market_ml_home_prob is None:
                market_ml_home_prob = round(1.0 - market_ml_away_prob, 6)

        upcoming = UpcomingCFBGame(
            event_id=event_id,
            event_start_utc=event_start_utc,
            away_team=away_team,
            home_team=home_team,
            spread_away_line=spread_away_line,
            spread_home_line=spread_home_line,
            total_line=total_line,
            is_neutral_site=is_neutral,
        )

        preds = model.predict_matchup(history, upcoming)

        for p in preds:
            mtype = p.market_type
            if mtype == "moneyline":
                selection = "home" if p.probabilities["home"] >= 0.5 else "away"
                prob = p.probabilities[selection]
                line_val = None
                ask = market_ml_home_prob if selection == "home" else market_ml_away_prob
                slug = f"ncaaf-ml-{event_id}-{selection}"
                m_ver = MODEL_VERSION
            elif mtype == "spread":
                selection = "away" if p.probabilities["away"] >= p.probabilities["home"] else "home"
                prob = p.probabilities[selection]
                line_val = spread_away_line if selection == "away" else spread_home_line
                ask = market_spread_away_prob if selection == "away" else market_spread_home_prob
                slug = f"ncaaf-spread-{event_id}-{selection}"
                m_ver = CFB_SPREAD_MODEL_VERSION
            else:  # total
                selection = "over" if p.probabilities["over"] >= p.probabilities["under"] else "under"
                prob = p.probabilities[selection]
                line_val = total_line
                ask = market_total_over_prob if selection == "over" else market_total_under_prob
                slug = f"ncaaf-total-{event_id}-{selection}"
                m_ver = CFB_TOTAL_MODEL_VERSION

            # If no market price/line is available from the source, do NOT fabricate a fake edge
            if ask is None:
                continue

            edge = round(prob - float(ask), 6)
            priced_contracts.append(
                {
                    "event_id": event_id,
                    "event_start_utc": event_start_utc,
                    "away_team": away_team,
                    "home_team": home_team,
                    "market_type": mtype,
                    "selection": selection,
                    "line": line_val,
                    "model_probability": prob,
                    "model_uncertainty": p.uncertainty,
                    "model_version": m_ver,
                    "feature_basis": p.feature_basis,
                    "rationale": p.rationale,
                    "market_slug": slug,
                    "executable_ask": float(ask),
                    "observed_at_utc": obs.isoformat(),
                    "timestamp_valid": True,
                    "edge_vs_executable_ask": edge,
                }
            )

    code_hash = hashlib.sha256(MODEL_VERSION.encode("utf-8")).hexdigest()[:16]

    return {
        "game_date": iso_date,
        "sport": "ncaaf",
        "league": "NCAAF",
        "status": "research",
        "model_version": MODEL_VERSION,
        "model_code_hash": code_hash,
        "priced_contracts": priced_contracts,
        "unmatched_events": unmatched,
        "event_count": len(events),
        "history_games": len(history),
    }
