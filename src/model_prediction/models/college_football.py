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
    CFB_DEFAULT_HOME_ADVANTAGE_POINTS,
    CFB_UNCERTAINTY_FLOOR,
    CFBFeatureExtractor,
    CFBGameRecord,
    CFBMatchupFeatures,
)
from ..pricing import implied_probability
from .base import GamePrediction
from .cfb_distribution import (
    CFBDistributionType,
    CFBJointDistributionEngine,
    CFBJointMarketProbabilities,
)

MODEL_VERSION = "cfb-structural-v4"
CFB_SPREAD_MODEL_VERSION = "cfb-structural-v4"
CFB_TOTAL_MODEL_VERSION = "cfb-structural-v4"
CFB_BLOWOUT_SPREAD_THRESHOLD = 21.5
CFB_BLOWOUT_MARGIN_SCALE = 0.025

# feat.uncertainty (0.04 floor -- established teams, no FBS/FCS mismatch,
# confirmed starters) previously reached the ledger only as sizing metadata;
# the margin distribution itself always used the same constant margin_sd
# regardless. Live Main/Flat NCAAF spread picks were losing at 41-43%
# (worse than a coin flip) while averaging ~68-79% claimed model probability
# and ~16-26% claimed edge -- most concentrated in FBS-vs-FCS mismatches and
# thin-sample-size teams. A chronological walk-forward residual check against
# 2,434 real 2023-2024 games (uncertainty.py::extract_features's own
# is_fbs_vs_fcs/sample_games signals) confirms this empirically: realized
# margin residual SD is ~16.7 for established matchups vs ~19.2 for
# FBS-vs-FCS/thin-sample ones -- about 15% wider, not the ~55% the raw
# uncertainty range would suggest if scaled 1:1. Total-points residual SD
# showed no comparable pattern by the same buckets (14.8-16.9 across all),
# so this scale intentionally widens margin_sd only, not total_sd.
CFB_UNCERTAINTY_MARGIN_SD_SCALE = 2.0

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
        spread_version: str | None = None,
        total_version: str | None = None,
        home_advantage_points: float = CFB_DEFAULT_HOME_ADVANTAGE_POINTS,
        margin_sd: float = 16.2,
        total_sd: float = 16.0,
        distribution_type: CFBDistributionType = CFBDistributionType.NEGATIVE_BINOMIAL,
    ) -> None:
        self.version = version
        self.spread_version = spread_version or CFB_SPREAD_MODEL_VERSION
        self.total_version = total_version or CFB_TOTAL_MODEL_VERSION
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

        # Preserve state before the engine advances it. A seed alone cannot
        # reproduce the second and later games in a slate.
        from ..cfb_replay import capture_inputs

        # Widen the margin distribution for exactly the games feat.uncertainty
        # flags as least reliable (FBS-vs-FCS mismatches, thin sample size) --
        # see CFB_UNCERTAINTY_MARGIN_SD_SCALE's docstring for the empirical
        # residual-SD evidence. Mutate-then-restore on the shared engine
        # (rather than a fresh per-call engine) so the RNG stream stays a
        # single advancing sequence across a slate, matching the seed pin
        # test in test_mlb_distribution_methods.py's stated invariant for
        # the analogous MLB engine. Captured BEFORE compute so the replay
        # record reflects the margin_sd actually used, not the baseline.
        effective_margin_sd = self.margin_sd * (
            1.0 + CFB_UNCERTAINTY_MARGIN_SD_SCALE * (feat.uncertainty - CFB_UNCERTAINTY_FLOOR)
        )
        if abs(sp_home_line) > CFB_BLOWOUT_SPREAD_THRESHOLD:
            excess = min(15.0, abs(sp_home_line) - CFB_BLOWOUT_SPREAD_THRESHOLD)
            effective_margin_sd *= 1.0 + CFB_BLOWOUT_MARGIN_SCALE * excess
        self.distribution_engine.margin_sd = effective_margin_sd
        try:
            inference_inputs = capture_inputs(
                self.distribution_engine,
                mu_home=feat.projected_home_points,
                mu_away=feat.projected_away_points,
                spread_home_line=sp_home_line,
                total_line=tot_line,
            )
            # 3. Derive Coherent Joint Probabilities
            joint_probs: CFBJointMarketProbabilities = self.distribution_engine.compute_market_probabilities(
                mu_home=feat.projected_home_points,
                mu_away=feat.projected_away_points,
                spread_home_line=sp_home_line,
                total_line=tot_line,
            )
        finally:
            self.distribution_engine.margin_sd = self.margin_sd

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
                inference_inputs=inference_inputs,
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
                model_version=self.spread_version,
                feature_basis=feat.to_dict(),
                inference_inputs=inference_inputs,
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
                model_version=self.total_version,
                feature_basis=feat.to_dict(),
                inference_inputs=inference_inputs,
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
                    rec = CFBGameRecord(
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
                        possessions=float(g["possessions"]) if g.get("possessions") is not None else None,
                        home_epa_per_play=float(g["home_epa_per_play"])
                        if g.get("home_epa_per_play") is not None
                        else None,
                        away_epa_per_play=float(g["away_epa_per_play"])
                        if g.get("away_epa_per_play") is not None
                        else None,
                        home_success_rate=float(g["home_success_rate"])
                        if g.get("home_success_rate") is not None
                        else None,
                        away_success_rate=float(g["away_success_rate"])
                        if g.get("away_success_rate") is not None
                        else None,
                        travel_distance_miles=float(g["travel_distance_miles"])
                        if g.get("travel_distance_miles") is not None
                        else None,
                        timezone_diff_hours=float(g["timezone_diff_hours"])
                        if g.get("timezone_diff_hours") is not None
                        else None,
                        elevation_ft=int(g["elevation_ft"]) if g.get("elevation_ft") is not None else None,
                        is_dome=bool(g["is_dome"]) if g.get("is_dome") is not None else False,
                        temperature_f=float(g["temperature_f"])
                        if g.get("temperature_f") is not None
                        else None,
                        wind_mph=float(g["wind_mph"]) if g.get("wind_mph") is not None else None,
                    )
                    records.append(rec)
                    seen_ids.add(eid)
                except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                    continue

    return sorted(records, key=lambda r: r.start)


def _clean_cfb_school(name: str) -> str:
    return name.replace("(", "").replace(")", "").strip()


def _canonical_market_snapshot_lineage(row: dict[str, Any], archive_path: Path) -> dict[str, Any] | None:
    """Bind a parsed prospective quote to its exact archived JSON record."""
    row = row.get("_archive_record", row)
    if not isinstance(row, dict):
        return None
    observed_at = str(row.get("observed_at_utc") or "")
    source = str(row.get("provider") or "")
    reconstructed = row.get("reconstructed")
    prospective_marker = row.get("usage") == "prospective_executable_bbo"
    if (
        not archive_path.is_file()
        or not observed_at
        or source != "polymarket_us"
        or row.get("timestamp_valid") is not True
        or not (reconstructed is False or (reconstructed is None and prospective_marker))
    ):
        return None
    payload = {key: value for key, value in row.items() if not str(key).startswith("_")}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "market_quote_observed_at_utc": observed_at,
        "market_quote_timestamp_valid": True,
        "market_quote_source": source,
        "market_quote_provenance": "decision_time_executable_quote",
        "market_quote_reconstructed": False,
        "market_snapshot_hash": digest,
        "market_snapshot_archive_path": str(archive_path.resolve()),
        "market_snapshot_record_id": digest,
    }


def _match_cfb_moneyline(
    away_team: str,
    home_team: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Match pregame Polymarket moneyline quote for an ESPN matchup.

    Returns (quote_row, long_side) where long_side is 'home' or 'away'.
    """
    from ..data_sources.cfb_data import resolve_team
    from ..learned_forward import _team_matches

    away_res = resolve_team(_clean_cfb_school(away_team))
    home_res = resolve_team(_clean_cfb_school(home_team))
    away_canon = away_res.canonical_name if away_res else away_team
    home_canon = home_res.canonical_name if home_res else home_team

    for r in rows:
        if r.get("market_type") != "moneyline":
            continue
        long_dict = r.get("long") or {}
        short_dict = r.get("short") or {}
        if long_dict.get("ask") is None or short_dict.get("ask") is None:
            continue

        title = str(r.get("event_title") or "")
        if " vs. " in title:
            p1, p2 = title.split(" vs. ", 1)
        elif " vs " in title:
            p1, p2 = title.split(" vs ", 1)
        elif " @ " in title:
            p1, p2 = title.split(" @ ", 1)
        else:
            continue

        p1_res = resolve_team(_clean_cfb_school(p1))
        p2_res = resolve_team(_clean_cfb_school(p2))
        p1_c = p1_res.canonical_name if p1_res else p1
        p2_c = p2_res.canonical_name if p2_res else p2

        h1 = (p1_c == home_canon) or _team_matches(home_team, p1)
        a2 = (p2_c == away_canon) or _team_matches(away_team, p2)
        if h1 and a2:
            team = str(r.get("team") or "")
            long_desc = str(long_dict.get("description") or "")
            is_home_long = (
                (p1_res is not None and resolve_team(_clean_cfb_school(team)) == home_res)
                or _team_matches(home_team, team)
                or _team_matches(home_team, long_desc)
            )
            return r, ("home" if is_home_long else "away")

        a1 = (p1_c == away_canon) or _team_matches(away_team, p1)
        h2 = (p2_c == home_canon) or _team_matches(home_team, p2)
        if a1 and h2:
            team = str(r.get("team") or "")
            long_desc = str(long_dict.get("description") or "")
            is_away_long = (
                (p1_res is not None and resolve_team(_clean_cfb_school(team)) == away_res)
                or _team_matches(away_team, team)
                or _team_matches(away_team, long_desc)
            )
            return r, ("away" if is_away_long else "home")

    return None, None


def _match_cfb_spread(
    away_team: str,
    home_team: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Select the most balanced main-game Polymarket spread quote for this matchup."""
    from ..data_sources.cfb_data import resolve_team
    from ..learned_forward import _team_matches

    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-")
    cands: list[dict[str, Any]] = []
    away_res = resolve_team(_clean_cfb_school(away_team))
    home_res = resolve_team(_clean_cfb_school(home_team))
    away_words = [w for w in away_team.casefold().split() if len(w) > 2]
    home_words = [w for w in home_team.casefold().split() if len(w) > 2]

    for r in rows:
        if r.get("market_type") != "spread":
            continue
        slug = str(r.get("market_slug", "")).lower()
        if any(token in slug for token in partial_tokens):
            continue
        if r.get("line") is None or (r.get("long") or {}).get("ask") is None:
            continue

        team = str(r.get("team") or "")
        title = str(r.get("event_title") or "")
        team_res = resolve_team(_clean_cfb_school(team)) if team else None

        matched_matchup = bool(
            (team_res and (team_res == away_res or team_res == home_res))
            or _team_matches(away_team, team)
            or _team_matches(home_team, team)
            or (
                any(w in title.casefold() or w in slug for w in away_words)
                and any(w in title.casefold() or w in slug for w in home_words)
            )
        )

        if matched_matchup:
            cands.append(r)

    if not cands:
        return None, None

    best = min(cands, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))
    anchor_team = str(best.get("team") or "")
    anchor_res = resolve_team(_clean_cfb_school(anchor_team)) if anchor_team else None
    if anchor_res and anchor_res == away_res:
        anchor_side = "away"
    elif anchor_res and anchor_res == home_res:
        anchor_side = "home"
    elif _team_matches(away_team, anchor_team):
        anchor_side = "away"
    else:
        anchor_side = "home"

    return best, anchor_side


def _match_cfb_total(
    away_team: str,
    home_team: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Select the most balanced main-game Polymarket total quote for this matchup."""
    from ..data_sources.cfb_data import resolve_team
    from ..learned_forward import _team_matches

    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "-tt-", "-tt1h-", "-tt2h-")
    cands: list[dict[str, Any]] = []
    away_res = resolve_team(_clean_cfb_school(away_team))
    home_res = resolve_team(_clean_cfb_school(home_team))
    away_words = [w for w in away_team.casefold().split() if len(w) > 2]
    home_words = [w for w in home_team.casefold().split() if len(w) > 2]

    for r in rows:
        if r.get("market_type") not in ("total", "over_under"):
            continue
        slug = str(r.get("market_slug", "")).lower()
        if any(token in slug for token in partial_tokens):
            continue
        if r.get("line") is None or (r.get("long") or {}).get("ask") is None:
            continue

        team = str(r.get("team") or "")
        title = str(r.get("event_title") or "")
        team_res = resolve_team(_clean_cfb_school(team)) if team else None

        matched_matchup = bool(
            (team_res and (team_res == away_res or team_res == home_res))
            or _team_matches(away_team, team)
            or _team_matches(home_team, team)
            or (
                any(w in title.casefold() or w in slug for w in away_words)
                and any(w in title.casefold() or w in slug for w in home_words)
            )
        )

        if matched_matchup:
            cands.append(r)

    if not cands:
        return None

    return min(cands, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


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
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(
            "Failed to fetch ESPN NCAAF scoreboard for %s: %s", norm_date, exc
        )
        events = []

    # 2. Load History: cutoff at 09:00:00Z (5:00 AM EDT) captures late Friday Pacific/Mountain games
    start_of_day = parse_utc(f"{iso_date}T09:00:00Z")
    history = _load_cfb_history(root, cutoff_dt=start_of_day)

    # 3. Load Polymarket Snapshots if available
    snapshot_path = root / "odds" / "ncaaf" / iso_date / "polymarket_snapshots.jsonl"
    snapshot_rows: list[dict[str, Any]] = []
    if snapshot_path.is_file():
        try:
            with snapshot_path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                        if isinstance(r, dict):
                            obs_str = str(r.get("observed_at_utc") or "")
                            if obs_str:
                                try:
                                    if parse_utc(obs_str) <= obs:
                                        snapshot_rows.append(r)
                                except (ValueError, TypeError):
                                    pass
                            else:
                                snapshot_rows.append(r)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            snapshot_rows = []

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

        # 3a. Match Polymarket prospective quotes
        pm_ml, ml_long_side = _match_cfb_moneyline(away_team, home_team, snapshot_rows)
        pm_sp, sp_anchor_side = _match_cfb_spread(away_team, home_team, snapshot_rows)
        pm_tot = _match_cfb_total(away_team, home_team, snapshot_rows)

        pm_ml_home_prob = None
        pm_ml_away_prob = None
        pm_ml_lineage = None
        if pm_ml is not None:
            pm_ml_home_prob = (
                float(pm_ml["long"]["ask"]) if ml_long_side == "home" else float(pm_ml["short"]["ask"])
            )
            pm_ml_away_prob = (
                float(pm_ml["short"]["ask"]) if ml_long_side == "home" else float(pm_ml["long"]["ask"])
            )
            pm_ml_lineage = _canonical_market_snapshot_lineage(pm_ml, snapshot_path)

        pm_spread_away_line = None
        pm_spread_home_line = None
        pm_spread_away_prob = None
        pm_spread_home_prob = None
        pm_spread_lineage = None
        if pm_sp is not None:
            sp_line = float(pm_sp["line"])
            pm_spread_away_line = sp_line if sp_anchor_side == "away" else -sp_line
            pm_spread_home_line = -sp_line if sp_anchor_side == "away" else sp_line
            pm_spread_away_prob = (
                float(pm_sp["long"]["ask"]) if sp_anchor_side == "away" else float(pm_sp["short"]["ask"])
            )
            pm_spread_home_prob = (
                float(pm_sp["short"]["ask"]) if sp_anchor_side == "away" else float(pm_sp["long"]["ask"])
            )
            pm_spread_lineage = _canonical_market_snapshot_lineage(pm_sp, snapshot_path)

        pm_total_line = None
        pm_total_over_prob = None
        pm_total_under_prob = None
        pm_total_lineage = None
        if pm_tot is not None:
            pm_total_line = float(pm_tot["line"])
            pm_total_over_prob = float(pm_tot["long"]["ask"])
            pm_total_under_prob = float(pm_tot["short"]["ask"])
            pm_total_lineage = _canonical_market_snapshot_lineage(pm_tot, snapshot_path)

        # 3b. Extract fallback sportsbook odds if available from ESPN
        odds_list = competition.get("odds", [])
        spread_away_line = None
        spread_home_line = None
        total_line = None
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
                total_line = None

            if total_line is not None:
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
                spread_home_line = None
                spread_away_line = None

            if spread_home_line is not None:
                market_spread_home_prob = (
                    round(implied_probability(h_ps_n), 6) if h_ps_n else STANDARD_LAY_ASK
                )
                market_spread_away_prob = (
                    round(implied_probability(a_ps_n), 6) if a_ps_n else STANDARD_LAY_ASK
                )

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
                market_ml_away_prob = round(min(0.99, max(0.01, (1.0 - market_ml_home_prob) + 0.045)), 6)
            elif market_ml_away_prob is not None and market_ml_home_prob is None:
                market_ml_home_prob = round(min(0.99, max(0.01, (1.0 - market_ml_away_prob) + 0.045)), 6)

        effective_spread_away = pm_spread_away_line if pm_sp is not None else spread_away_line
        effective_spread_home = pm_spread_home_line if pm_sp is not None else spread_home_line
        effective_total = pm_total_line if pm_tot is not None else total_line

        ev_season = int((ev.get("season") or {}).get("year") or iso_date[:4])
        ev_week = int((ev.get("week") or {}).get("number") or 1)

        from ..features.multi_sport_weather import resolve_cfb_game_weather

        weather_evidence: dict[str, Any] = {}
        w_temp, w_wind, w_precip, is_dome = resolve_cfb_game_weather(
            root,
            home_team,
            iso_date,
            event_start_utc=event_start_utc,
            observed_at_utc=obs.isoformat(),
            provenance_out=weather_evidence,
        )
        weather_inputs_verified = is_dome or None not in (w_temp, w_wind, w_precip)

        upcoming = UpcomingCFBGame(
            event_id=event_id,
            event_start_utc=event_start_utc,
            away_team=away_team,
            home_team=home_team,
            spread_away_line=effective_spread_away,
            spread_home_line=effective_spread_home,
            total_line=effective_total,
            wind_mph=w_wind,
            temperature_f=w_temp,
            precipitation_in=w_precip,
            is_neutral_site=is_neutral,
            season_year=ev_season,
            week=ev_week,
        )

        preds = model.predict_matchup(history, upcoming)

        for p in preds:
            mtype = p.market_type
            sportsbook = "espn_consensus"
            market_lineage = None

            if mtype == "moneyline":
                p_h = float(p.probabilities.get("home", 0.5))
                p_a = float(p.probabilities.get("away", 0.5))
                if pm_ml is not None and pm_ml_home_prob is not None and pm_ml_away_prob is not None:
                    h_ask = float(pm_ml_home_prob)
                    a_ask = float(pm_ml_away_prob)
                    slug_base = str(pm_ml.get("market_slug"))
                    sportsbook = "polymarket_us"
                    market_lineage = pm_ml_lineage
                    m_ver = p.model_version
                elif market_ml_home_prob is not None or market_ml_away_prob is not None:
                    h_ask = float(
                        market_ml_home_prob
                        if market_ml_home_prob is not None
                        else (1.0 - market_ml_away_prob + 0.045)
                    )
                    a_ask = float(
                        market_ml_away_prob
                        if market_ml_away_prob is not None
                        else (1.0 - market_ml_home_prob + 0.045)
                    )
                    slug_base = f"ncaaf-ml-{event_id}"
                    sportsbook = "espn_consensus"
                    market_lineage = None
                    m_ver = p.model_version
                else:
                    continue

                # 3-Tier Moneyline Selection Rule (Eliminates catastrophic extreme dog trap)
                fav_side = "home" if h_ask >= 0.5 else "away"
                dog_side = "away" if fav_side == "home" else "home"
                fav_ask = h_ask if fav_side == "home" else a_ask
                dog_ask = 1.0 - fav_ask + 0.045
                p_fav = p_h if fav_side == "home" else p_a
                p_dog = p_a if fav_side == "home" else p_h

                if fav_ask <= 0.67:
                    # Moderate favorite (-110 to -200): back favorite
                    selection, prob, ask = fav_side, p_fav, fav_ask
                elif fav_ask <= 0.80 and (p_dog - dog_ask) > -0.05:
                    # Mid-dog (+175 to +350) with model edge: back dog
                    selection, prob, ask = dog_side, p_dog, dog_ask
                else:
                    # Deep chalk: back favorite (never extreme dogs > +350)
                    selection, prob, ask = fav_side, p_fav, fav_ask

                line_val = None
                slug = f"{slug_base}-{selection}"
            elif mtype == "spread":
                p_cov_a = float(p.probabilities.get("away", 0.5))
                p_cov_h = float(p.probabilities.get("home", 0.5))
                if pm_sp is not None and pm_spread_away_prob is not None and pm_spread_home_prob is not None:
                    edge_a = p_cov_a - float(pm_spread_away_prob)
                    edge_h = p_cov_h - float(pm_spread_home_prob)
                    if edge_a >= edge_h:
                        selection, prob, ask = "away", p_cov_a, float(pm_spread_away_prob)
                        line_val = pm_spread_away_line
                    else:
                        selection, prob, ask = "home", p_cov_h, float(pm_spread_home_prob)
                        line_val = pm_spread_home_line
                    slug = str(pm_sp.get("market_slug"))
                    sportsbook = "polymarket_us"
                    market_lineage = pm_spread_lineage
                    m_ver = p.model_version
                elif spread_away_line is not None and spread_home_line is not None:
                    a_ask = market_spread_away_prob or STANDARD_LAY_ASK
                    h_ask = market_spread_home_prob or STANDARD_LAY_ASK
                    edge_a = p_cov_a - float(a_ask)
                    edge_h = p_cov_h - float(h_ask)
                    if edge_a >= edge_h:
                        selection, prob, ask = "away", p_cov_a, float(a_ask)
                        line_val = spread_away_line
                    else:
                        selection, prob, ask = "home", p_cov_h, float(h_ask)
                        line_val = spread_home_line
                    slug = f"ncaaf-spread-{event_id}-{selection}"
                    sportsbook = "espn_consensus"
                    market_lineage = None
                    m_ver = p.model_version
                else:
                    continue
            else:  # total
                p_ov = float(p.probabilities.get("over", 0.5))
                p_un = float(p.probabilities.get("under", 0.5))
                if pm_tot is not None and pm_total_over_prob is not None and pm_total_under_prob is not None:
                    edge_o = p_ov - float(pm_total_over_prob)
                    edge_u = p_un - float(pm_total_under_prob)
                    if edge_o >= edge_u:
                        selection, prob, ask = "over", p_ov, float(pm_total_over_prob)
                    else:
                        selection, prob, ask = "under", p_un, float(pm_total_under_prob)
                    line_val = pm_total_line
                    slug = str(pm_tot.get("market_slug"))
                    sportsbook = "polymarket_us"
                    market_lineage = pm_total_lineage
                    m_ver = p.model_version
                elif total_line is not None:
                    o_ask = market_total_over_prob or STANDARD_LAY_ASK
                    u_ask = market_total_under_prob or STANDARD_LAY_ASK
                    edge_o = p_ov - float(o_ask)
                    edge_u = p_un - float(u_ask)
                    if edge_o >= edge_u:
                        selection, prob, ask = "over", p_ov, float(o_ask)
                    else:
                        selection, prob, ask = "under", p_un, float(u_ask)
                    line_val = total_line
                    slug = f"ncaaf-total-{event_id}-{selection}"
                    sportsbook = "espn_consensus"
                    market_lineage = None
                    m_ver = p.model_version
                else:
                    continue

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
                    "weather_inputs_verified": weather_inputs_verified,
                    "model_version": m_ver,
                    "feature_basis": {
                        **p.feature_basis,
                        "weather_inputs_verified": weather_inputs_verified,
                        "weather_evidence": weather_evidence,
                    },
                    "inference_inputs": p.inference_inputs,
                    "rationale": p.rationale,
                    "market_slug": slug,
                    "executable_ask": float(ask),
                    "observed_at_utc": (
                        market_lineage["market_quote_observed_at_utc"]
                        if market_lineage is not None and market_lineage.get("market_quote_observed_at_utc")
                        else obs.isoformat()
                    ),
                    "timestamp_valid": True,
                    "edge_vs_executable_ask": edge,
                    "sportsbook": sportsbook,
                    "market_lineage": market_lineage,
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
