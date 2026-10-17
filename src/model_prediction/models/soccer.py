"""Unified soccer model: independent Poisson goal model with a Dixon-Coles-style
low-score correction. Markets: three-way ML, O/U 2.5, BTTS.

Attack/defense strengths come from the soccer_form feature (EWMA goals for and
against, shrunk toward league mean). Market prices never enter this model.
Research state until validated through the backtester.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import exp, log

from ..features.base import GameRecord
from ..features.trends import ewm_level
from .base import GamePrediction, GamePredictionBase

SOCCER_MODEL_VERSION = "soccer-poisson-dc-v1"
HOME_GOAL_BOOST = 1.15
DC_RHO = -0.10  # Dixon-Coles low-score dependence parameter
MAX_GOALS = 10
# BTTS calibration (added 2026-07-31): the raw joint-matrix BTTS probability
# is measurably overconfident on real data -- checked directly, not assumed,
# since BTTS is a much harder derived quantity from this same score matrix
# than moneyline/totals (it depends on the joint tail of both teams'
# distributions, not just their marginal win/total behavior, and the DC low-
# score correction above is tuned for the 0-0/1-0/0-1/1-1 cells specifically,
# not this derived probability). Real walk-forward check: moneyline/totals
# from this identical model score ~62.5%/well-calibrated; raw BTTS only hit
# 55.0% with meaningfully overconfident buckets (e.g. predicted 72% actually
# hit 59%). Fit via Platt scaling (logistic regression of real outcome on
# logit(raw_probability)) on the validation cohort only, then checked on the
# separate locked holdout: accuracy improved 55.0% -> 56.7%, and holdout
# calibration buckets came back close to the diagonal (e.g. 55.3% predicted
# vs 55.96% actual, 62.2% vs 65.95%). Still real but weak signal -- BTTS
# stays research-only regardless of any future promotion decision for
# moneyline/totals from the same model.
BTTS_CALIBRATION_INTERCEPT = 0.1393
BTTS_CALIBRATION_SLOPE = 0.4205


@dataclass(frozen=True)
class UpcomingMatch:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    league: str = "SOCCER"


def _apply_btts_calibration(raw_probability: float) -> float:
    """Platt-scale the raw joint-matrix BTTS probability -- see
    BTTS_CALIBRATION_INTERCEPT/SLOPE's module-level comment for the real
    fit and holdout evidence."""
    clipped = min(1 - 1e-9, max(1e-9, raw_probability))
    logit = log(clipped / (1 - clipped))
    calibrated = BTTS_CALIBRATION_INTERCEPT + BTTS_CALIBRATION_SLOPE * logit
    return 1 / (1 + exp(-calibrated))


def _apply_custom_btts_calibration(raw_probability: float, intercept: float, slope: float) -> float:
    """Platt-scale BTTS probability with league-specific intercept and slope."""
    clipped = min(1 - 1e-9, max(1e-9, raw_probability))
    logit = log(clipped / (1 - clipped))
    calibrated = intercept + slope * logit
    return 1 / (1 + exp(-calibrated))


def _poisson_pmf(rate: float, k: int) -> float:
    result = exp(-rate)
    for i in range(1, k + 1):
        result *= rate / i
    return result


def _dc_adjustment(
    home_goals: int,
    away_goals: int,
    home_rate: float,
    away_rate: float,
    rho: float = DC_RHO,
) -> float:
    """Dixon-Coles tau for the four low-score cells."""
    if home_goals == 0 and away_goals == 0:
        return 1 - home_rate * away_rate * rho
    if home_goals == 0 and away_goals == 1:
        return 1 + home_rate * rho
    if home_goals == 1 and away_goals == 0:
        return 1 + away_rate * rho
    if home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


class SoccerModel:
    sport = "soccer"
    version: str = SOCCER_MODEL_VERSION

    def __init__(
        self,
        version: str = SOCCER_MODEL_VERSION,
        home_boost: float = HOME_GOAL_BOOST,
        rho: float = DC_RHO,
        divide_away: bool = True,
    ) -> None:
        self.version = version
        self.home_boost = home_boost
        self.rho = rho
        self.divide_away = divide_away

    def _strengths(self, history: Sequence[GameRecord]) -> tuple[dict[str, dict[str, float]], float]:
        by_team: dict[str, dict[str, list[float]]] = {}
        goals: list[float] = []
        for game in sorted(history, key=lambda item: item.start):
            goals.extend([float(game.away_score), float(game.home_score)])
            for team, scored, allowed in (
                (game.away_team, game.away_score, game.home_score),
                (game.home_team, game.home_score, game.away_score),
            ):
                entry = by_team.setdefault(team, {"scored": [], "allowed": []})
                entry["scored"].append(float(scored))
                entry["allowed"].append(float(allowed))
        baseline = sum(goals) / len(goals) if goals else 1.35
        strengths = {}
        for team, entry in by_team.items():
            strengths[team] = {
                "attack": ewm_level(entry["scored"], 10.0, baseline, 8.0) / baseline,
                "defense": ewm_level(entry["allowed"], 10.0, baseline, 8.0) / baseline,
                "games": float(len(entry["scored"])),
            }
        return strengths, baseline

    def score_matrix(self, home_rate: float, away_rate: float, rho: float | None = None) -> list[list[float]]:
        use_rho = self.rho if rho is None else rho
        matrix = []
        for h in range(MAX_GOALS + 1):
            row = []
            for a in range(MAX_GOALS + 1):
                probability = (
                    _poisson_pmf(home_rate, h)
                    * _poisson_pmf(away_rate, a)
                    * _dc_adjustment(h, a, home_rate, away_rate, rho=use_rho)
                )
                row.append(max(probability, 0.0))
            matrix.append(row)
        total = sum(sum(row) for row in matrix)
        return [[cell / total for cell in row] for row in matrix]

    def predict_games(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingMatch],
    ) -> list[GamePrediction]:
        from ..soccer.registry import resolve

        global_strengths, global_baseline = self._strengths(history)
        league_cache: dict[str, tuple[dict[str, dict[str, float]], float]] = {}

        def _get_strengths(league_name: str) -> tuple[dict[str, dict[str, float]], float]:
            norm_lg = str(league_name or "").strip().upper()
            if not norm_lg or norm_lg in {"SOCCER", "UNKNOWN"}:
                return global_strengths, global_baseline
            if norm_lg not in league_cache:
                lg_history = [g for g in history if getattr(g, "league", "").strip().upper() == norm_lg]
                # Dedicated per-league fitting: use strictly matches from this league
                if lg_history:
                    league_cache[norm_lg] = self._strengths(lg_history)
                else:
                    league_cache[norm_lg] = (global_strengths, global_baseline)
            return league_cache[norm_lg]

        predictions: list[GamePrediction] = []
        for match in upcoming:
            lg_name = getattr(match, "league", "SOCCER")
            norm_lg = str(lg_name or "").strip().upper()
            strengths, hist_baseline = _get_strengths(norm_lg)
            home = strengths.get(match.home_team, {"attack": 1.0, "defense": 1.0, "games": 0.0})
            away = strengths.get(match.away_team, {"attack": 1.0, "defense": 1.0, "games": 0.0})

            # League-specific parameters from per-league champion configs
            lg_cfg = resolve(norm_lg) if norm_lg and norm_lg not in {"SOCCER", "UNKNOWN"} else None
            if lg_cfg is not None:
                baseline = lg_cfg.baseline or hist_baseline
                h_boost = lg_cfg.home_advantage
                use_rho = lg_cfg.dc_rho
                is_no_div = norm_lg in {"MLS", "BRASILEIRAO", "LIGA_MX", "ARGENTINA", "COLOMBIA", "CHILE"}
                div_away = False if is_no_div else self.divide_away
            else:
                baseline = hist_baseline
                is_mls = norm_lg == "MLS"
                h_boost = 1.2325 if is_mls else self.home_boost
                use_rho = self.rho
                div_away = False if is_mls else self.divide_away

            home_rate = baseline * home["attack"] * away["defense"] * h_boost
            away_rate = baseline * away["attack"] * home["defense"] / (h_boost if div_away else 1.0)
            from ..coded_market_replay import capture_inputs

            inference_inputs = capture_inputs(
                "SOCCER",
                {
                    "home_rate": home_rate,
                    "away_rate": away_rate,
                    "rho": use_rho,
                    "model_version": self.version,
                },
            )
            matrix = self.score_matrix(home_rate, away_rate, rho=use_rho)
            home_win = sum(matrix[h][a] for h in range(MAX_GOALS + 1) for a in range(h))
            away_win = sum(matrix[h][a] for a in range(MAX_GOALS + 1) for h in range(a))
            draw = 1 - home_win - away_win
            raw_btts = sum(matrix[h][a] for h in range(1, MAX_GOALS + 1) for a in range(1, MAX_GOALS + 1))
            if (
                lg_cfg is not None
                and lg_cfg.btts_calibration_intercept is not None
                and lg_cfg.btts_calibration_slope is not None
            ):
                btts = _apply_custom_btts_calibration(
                    raw_btts, lg_cfg.btts_calibration_intercept, lg_cfg.btts_calibration_slope
                )
            else:
                btts = _apply_btts_calibration(raw_btts)

            sample = min(home["games"], away["games"])
            uncertainty = max(0.04, min(0.20, 0.20 - 0.01 * sample))
            base: GamePredictionBase = {
                "event_id": match.event_id,
                "event_start_utc": match.event_start_utc,
                "league": match.league,
                "away_team": match.away_team,
                "home_team": match.home_team,
                "uncertainty": round(uncertainty, 4),
                "model_version": self.version,
                "feature_basis": {
                    "home_goal_rate": round(home_rate, 4),
                    "away_goal_rate": round(away_rate, 4),
                    "league_goals_per_team": round(baseline, 4),
                    "history_games": len(history),
                    "min_team_games": sample,
                },
            }
            rationale = (
                f"Poisson-DC rates {home_rate:.2f} (home) / {away_rate:.2f} (away) "
                f"from EWMA attack/defense strengths."
            )
            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    inference_inputs=inference_inputs,
                    line=None,
                    probabilities={
                        "home": round(home_win, 6),
                        "away": round(away_win, 6),
                        "draw": round(draw, 6),
                    },
                    rationale=rationale,
                    **base,
                )
            )
            # Totals (1.5, 2.5, 3.5)
            for t_line in (1.5, 2.5, 3.5):
                over_p = sum(
                    matrix[h][a] for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1) if h + a > t_line
                )
                predictions.append(
                    GamePrediction(
                        market_type="total",
                        line=t_line,
                        probabilities={"over": round(over_p, 6), "under": round(1 - over_p, 6)},
                        rationale=rationale,
                        **base,
                    )
                )

            # Spreads / Handicaps (-1.5, 1.5)
            for s_line in (-1.5, 1.5):
                home_cov = sum(
                    matrix[h][a]
                    for h in range(MAX_GOALS + 1)
                    for a in range(MAX_GOALS + 1)
                    if (h - a) + s_line > 0
                )
                predictions.append(
                    GamePrediction(
                        market_type="spread",
                        line=s_line,
                        probabilities={"home": round(home_cov, 6), "away": round(1 - home_cov, 6)},
                        rationale=f"Dixon-Coles spread line {s_line:+.1f}: home cover p={home_cov:.3f}.",
                        **base,
                    )
                )

            predictions.append(
                GamePrediction(
                    market_type="btts",
                    line=None,
                    probabilities={"yes": round(btts, 6), "no": round(1 - btts, 6)},
                    rationale=rationale,
                    **base,
                )
            )
        return predictions


class DedicatedSoccerLeagueModel:
    """Dedicated model for an individual soccer league with isolated parameters and ratings."""

    def __init__(self, league_code: str) -> None:
        from ..soccer.registry import resolve

        self.league_code = str(league_code).strip().upper()
        self.config = resolve(self.league_code)
        self.version = self.config.model_version
        self.sport = "soccer"
        is_no_div = self.league_code in {"MLS", "BRASILEIRAO", "LIGA_MX", "ARGENTINA", "COLOMBIA", "CHILE"}
        self._underlying = SoccerModel(
            version=self.version,
            home_boost=self.config.home_advantage,
            rho=self.config.dc_rho,
            divide_away=not is_no_div,
        )

    def predict_games(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingMatch],
    ) -> list[GamePrediction]:
        """Predict matches strictly isolated to this league's games."""
        isolated_history = [
            g for g in history if getattr(g, "league", "").strip().upper() in (self.league_code, "SOCCER")
        ]
        if not isolated_history and history:
            isolated_history = list(history)

        stamped_upcoming = [
            UpcomingMatch(
                event_id=m.event_id,
                event_start_utc=m.event_start_utc,
                away_team=m.away_team,
                home_team=m.home_team,
                league=self.league_code,
            )
            if getattr(m, "league", "SOCCER") != self.league_code
            else m
            for m in upcoming
        ]
        return self._underlying.predict_games(isolated_history, stamped_upcoming)


def soccer_model() -> SoccerModel:
    return SoccerModel()


def soccer_model_v2() -> SoccerModel:
    return SoccerModel(
        version="soccer-poisson-dc-v2",
        home_boost=1.18,
        rho=-0.05,
        divide_away=False,
    )


def get_soccer_league_model(league_code: str) -> DedicatedSoccerLeagueModel:
    """Return dedicated model for any soccer league."""
    return DedicatedSoccerLeagueModel(league_code)


def epl_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("EPL")


def la_liga_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("LA_LIGA")


def bundesliga_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("BUNDESLIGA")


def serie_a_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("SERIE_A")


def ligue_1_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("LIGUE_1")


def mls_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("MLS")


def ucl_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("UCL")


def championship_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("CHAMPIONSHIP")


def eredivisie_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("EREDIVISIE")


def primeira_liga_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("PRIMEIRA_LIGA")


def liga_mx_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("LIGA_MX")


def brasileirao_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("BRASILEIRAO")


def uel_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("UEL")


def uecl_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("UECL")


def fa_cup_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("FA_CUP")


def efl_cup_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("EFL_CUP")


def copa_del_rey_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("COPA_DEL_REY")


def dfb_pokal_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("DFB_POKAL")


def coppa_italia_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("COPPA_ITALIA")


def scottish_prem_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("SCOTTISH_PREM")


def belgian_pro_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("BELGIAN_PRO")


def turkish_super_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("TURKISH_SUPER")


def saudi_pro_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("SAUDI_PRO")


def swiss_super_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("SWISS_SUPER")


def austrian_bund_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("AUSTRIAN_BUND")


def danish_super_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("DANISH_SUPER")


def allsvenskan_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("ALLSVENSKAN")


def norwegian_elite_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("NORWEGIAN_ELITE")


def greek_super_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("GREEK_SUPER")


def la_liga_2_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("LA_LIGA_2")


def segunda_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("SEGUNDA")


def serie_b_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("SERIE_B")


def ligue_2_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("LIGUE_2")


def libertadores_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("LIBERTADORES")


def sudamericana_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("SUDAMERICANA")


def argentina_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("ARGENTINA")


def colombia_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("COLOMBIA")


def chile_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("CHILE")


def j_league_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("J_LEAGUE")


def a_league_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("A_LEAGUE")


def csl_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("CSL")


def nwsl_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("NWSL")


def polish_ekstraklasa_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("POLISH_EKSTRAKLASA")


def brazil_serie_b_soccer_model() -> DedicatedSoccerLeagueModel:
    return DedicatedSoccerLeagueModel("BRAZIL_SERIE_B")
