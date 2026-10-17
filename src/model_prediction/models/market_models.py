"""Dedicated market models for every sport and league.

Separates models by league AND by market type (Moneyline, Spread, Total).
Enforces zero cross-market bleed: each market model only emits predictions
for its dedicated market type with dedicated model versioning and calibrations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ..domain import MarketType
from .base import GamePrediction


def _norm_market(market: MarketType | str) -> str:
    """Normalize market type identifier to lowercase string."""
    m = str(market).lower().strip()
    return m.removeprefix("markettype.")


class MarketScopedModel:
    """Wraps a league model and strictly constrains its output to a single market type.

    Guarantees:
    1. Zero cross-market bleed: only predictions for `market_type` are emitted.
    2. Explicit market-specific model_version tagging.
    3. Point-in-time and league isolation inherited from underlying model.
    """

    def __init__(
        self,
        underlying_model: Any,
        market_type: MarketType | str,
        model_version: str | None = None,
        league: str | None = None,
    ) -> None:
        self.underlying_model = underlying_model
        self.market_type = (
            market_type if isinstance(market_type, MarketType) else MarketType(_norm_market(market_type))
        )
        self.target_market_str = _norm_market(market_type)
        self.league = str(league or getattr(underlying_model, "league", "") or "")
        self.model_version = model_version or f"{self.league.lower()}-{self.target_market_str}-v1"
        self.version = self.model_version

    def predict_games(self, *args: Any, **kwargs: Any) -> list[GamePrediction]:
        """Filter underlying predictions to only the target market type."""
        predict_fn = getattr(self.underlying_model, "predict_games", None)
        if predict_fn is None:
            predict_fn = getattr(self.underlying_model, "predict_matches", None)
        if predict_fn is None:
            predict_fn = getattr(self.underlying_model, "predict", None)
        if predict_fn is None:
            raise AttributeError(
                f"Underlying model {type(self.underlying_model).__name__} has no predict_games/predict method"
            )

        raw = predict_fn(*args, **kwargs)
        filtered: list[GamePrediction] = []
        for p in raw:
            if _norm_market(p.market_type) == self.target_market_str:
                filtered.append(replace(p, model_version=self.model_version))
        return filtered

    def predict_matches(self, *args: Any, **kwargs: Any) -> list[GamePrediction]:
        return self.predict_games(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.underlying_model, name)


# -----------------------------------------------------------------------------
# Per-League Market Model Factories
# -----------------------------------------------------------------------------


def _get_underlying_league_model(league_or_sport: str) -> Any:
    """Resolve the underlying domain engine for a given league/competition."""
    norm = str(league_or_sport or "").strip().upper()

    # Basketball leagues
    if norm in {
        "NBA",
        "WNBA",
        "NCAAB",
        "WNCAAB",
        "EUROLEAGUE",
        "EUROLG",
        "ACB",
        "BBL",
        "LNB",
        "ABA",
        "NBL",
        "CBB",
        "WCBB",
    }:
        from .basketball_leagues import DedicatedBasketballLeagueModel, resolve_basketball_config

        cfg = resolve_basketball_config(norm)
        return DedicatedBasketballLeagueModel(cfg)

    # Soccer leagues
    if norm in {
        "SOCCER",
        "EPL",
        "LA_LIGA",
        "BUNDESLIGA",
        "SERIE_A",
        "LIGUE_1",
        "MLS",
        "UCL",
        "UEL",
        "UECL",
        "CHAMPIONSHIP",
        "EREDIVISIE",
        "PRIMEIRA_LIGA",
        "LIGA_MX",
        "BRASILEIRAO",
        "FA_CUP",
        "EFL_CUP",
        "COPA_DEL_REY",
        "DFB_POKAL",
        "COPPA_ITALIA",
        "SCOTTISH_PREM",
        "BELGIAN_PRO",
        "TURKISH_SUPER",
        "SAUDI_PRO",
        "SWISS_SUPER",
        "AUSTRIAN_BUND",
        "DANISH_SUPER",
        "ALLSVENSKAN",
        "NORWEGIAN_ELITE",
        "GREEK_SUPER",
        "LA_LIGA_2",
        "SEGUNDA",
        "SERIE_B",
        "LIGUE_2",
        "LIBERTADORES",
        "SUDAMERICANA",
        "ARGENTINA",
        "COLOMBIA",
        "CHILE",
        "URUGUAY",
        "ECUADOR",
        "PERU",
        "J_LEAGUE",
        "A_LEAGUE",
        "CSL",
        "NWSL",
        "POLISH_EKSTRAKLASA",
        "BRAZIL_SERIE_B",
        "BUNDESLIGA_2",
    }:
        from .soccer import get_soccer_league_model

        return get_soccer_league_model(norm)

    # Hockey leagues
    if norm in {"HOCKEY", "NHL", "AHL", "KHL", "SHL", "DEL", "LIIGA", "CEHL", "SNHL"}:
        from .hockey import HockeyModel

        return HockeyModel()

    # Darts competitions
    if norm in {"DARTS", "DARTS_PDC", "DARTS_MODUS", "PDC", "MODUS"}:
        from .darts import DartsModel

        return DartsModel()

    # International baseball
    if norm in {"KBO", "NPB"}:
        from .international_baseball_v3 import InternationalBaseballV3Model

        return InternationalBaseballV3Model(league=norm)

    # MLB
    if norm == "MLB":
        from .mlb_structural_v10 import MLBStructuralV10Model

        return MLBStructuralV10Model()

    # NFL
    if norm == "NFL":
        from .nfl_structural_v5 import NFLStructuralV5Engine

        return NFLStructuralV5Engine()

    # NCAAF / College Football
    if norm in {"NCAAF", "CFB"}:
        from .college_football import cfb_model

        return cfb_model()

    # Tennis (ATP / WTA)
    if norm in {"TENNIS", "ATP", "WTA"}:
        from .tennis_markov import TennisMarkovEngine

        return TennisMarkovEngine()

    # Esports
    if norm in {"CS2", "DOTA2", "LOL", "VALORANT", "RAINBOW_SIX"}:
        from .esports_derivatives import price_esports_bo3_derivatives

        class _EsportsBo3Engine:
            def __init__(self, es_league: str) -> None:
                self.league = es_league

            def predict_games(
                self, matches: Sequence[Any], as_of_utc: str | None = None
            ) -> list[GamePrediction]:
                preds: list[GamePrediction] = []
                for m in matches:
                    p1_win = getattr(m, "prob_team1", 0.50)
                    derivs = price_esports_bo3_derivatives(p1_win)
                    event_id = str(getattr(m, "event_id", "es-match"))
                    event_start_utc = str(getattr(m, "event_start_utc", "2026-10-16T00:00:00Z"))
                    away_team = str(getattr(m, "away_team", getattr(m, "team2", "Team 2")))
                    home_team = str(getattr(m, "home_team", getattr(m, "team1", "Team 1")))
                    uncertainty = 0.08
                    feature_basis: dict[str, object] = {"p_match": p1_win}

                    preds.append(
                        GamePrediction(
                            event_id=event_id,
                            event_start_utc=event_start_utc,
                            league=self.league,
                            away_team=away_team,
                            home_team=home_team,
                            market_type=MarketType.MONEYLINE,
                            line=None,
                            probabilities={"home": derivs.match_win_p1, "away": derivs.match_win_p2},
                            uncertainty=uncertainty,
                            model_version=f"{self.league.lower()}-moneyline-v1",
                            feature_basis=feature_basis,
                            rationale="Esports series moneyline win probability",
                        )
                    )
                    preds.append(
                        GamePrediction(
                            event_id=event_id,
                            event_start_utc=event_start_utc,
                            league=self.league,
                            away_team=away_team,
                            home_team=home_team,
                            market_type=MarketType.SPREAD,
                            line=-1.5,
                            probabilities={
                                "home": derivs.spread_p1_cover_neg_1pt5,
                                "away": derivs.spread_p2_cover_pos_1pt5,
                            },
                            uncertainty=uncertainty,
                            model_version=f"{self.league.lower()}-spread-v1",
                            feature_basis=feature_basis,
                            rationale="Esports Bo3 map spread -1.5/+1.5 cover probability",
                        )
                    )
                    preds.append(
                        GamePrediction(
                            event_id=event_id,
                            event_start_utc=event_start_utc,
                            league=self.league,
                            away_team=away_team,
                            home_team=home_team,
                            market_type=MarketType.TOTAL,
                            line=2.5,
                            probabilities={
                                "over": derivs.total_maps_over_2pt5,
                                "under": derivs.total_maps_under_2pt5,
                            },
                            uncertainty=uncertainty,
                            model_version=f"{self.league.lower()}-total-v1",
                            feature_basis=feature_basis,
                            rationale="Esports Bo3 series total maps Over/Under 2.5",
                        )
                    )
                return preds

        return _EsportsBo3Engine(norm)

    raise ValueError(f"Unknown league or sport: {league_or_sport}")


def get_market_model(league_or_sport: str, market: MarketType | str) -> MarketScopedModel:
    """Universal resolver: get a dedicated model for any (league, market) pair."""
    underlying = _get_underlying_league_model(league_or_sport)
    norm_lg = str(league_or_sport or "").strip().upper()
    norm_mkt = _norm_market(market)

    # Standard model version identifier
    model_version = f"{norm_lg.lower()}-{norm_mkt}-v1"

    # Specific historical version overrides where canonical
    if norm_lg == "NBA" and norm_mkt == "moneyline":
        model_version = "nba-moneyline-elo-trend-lr-v4"
    elif norm_lg == "NBA" and norm_mkt == "spread":
        model_version = "nba-spread-margin-v2"
    elif norm_lg == "NBA" and norm_mkt == "total":
        model_version = "nba-total-margin-v2"
    elif norm_lg == "WNBA" and norm_mkt == "moneyline":
        model_version = "wnba-moneyline-elo-trend-lr-v4"
    elif norm_lg == "WNBA" and norm_mkt == "spread":
        model_version = "wnba-spread-margin-v2"
    elif norm_lg == "WNBA" and norm_mkt == "total":
        model_version = "wnba-total-margin-v2"
    elif norm_lg == "NFL" and norm_mkt == "spread":
        model_version = "nfl-spread-structural-v5"
    elif norm_lg == "NFL" and norm_mkt == "total":
        model_version = "nfl-total-structural-v5"
    elif norm_lg == "MLB" and norm_mkt == "spread":
        model_version = "measured-edge-margin-v3"
    elif norm_lg == "MLB" and norm_mkt == "total":
        model_version = "measured-edge-totals-v3"

    return MarketScopedModel(
        underlying_model=underlying,
        market_type=market,
        model_version=model_version,
        league=norm_lg,
    )


# -----------------------------------------------------------------------------
# Convenience Functions per Market
# -----------------------------------------------------------------------------


def moneyline_model(league_or_sport: str) -> MarketScopedModel:
    return get_market_model(league_or_sport, MarketType.MONEYLINE)


def spread_model(league_or_sport: str) -> MarketScopedModel:
    return get_market_model(league_or_sport, MarketType.SPREAD)


def total_model(league_or_sport: str) -> MarketScopedModel:
    return get_market_model(league_or_sport, MarketType.TOTAL)


# -----------------------------------------------------------------------------
# Concrete Model Classes / Aliases across all sports
# -----------------------------------------------------------------------------

# Basketball
NBAMoneylineModel = lambda: moneyline_model("NBA")
NBASpreadModel = lambda: spread_model("NBA")
NBATotalModel = lambda: total_model("NBA")

WNBAMoneylineModel = lambda: moneyline_model("WNBA")
WNBASpreadModel = lambda: spread_model("WNBA")
WNBATotalModel = lambda: total_model("WNBA")

NCAABMoneylineModel = lambda: moneyline_model("NCAAB")
NCAABSpreadModel = lambda: spread_model("NCAAB")
NCAABTotalModel = lambda: total_model("NCAAB")

WNCAABMoneylineModel = lambda: moneyline_model("WNCAAB")
WNCAABSpreadModel = lambda: spread_model("WNCAAB")
WNCAABTotalModel = lambda: total_model("WNCAAB")

EuroLeagueMoneylineModel = lambda: moneyline_model("EUROLEAGUE")
EuroLeagueSpreadModel = lambda: spread_model("EUROLEAGUE")
EuroLeagueTotalModel = lambda: total_model("EUROLEAGUE")

# Soccer
EPLMoneylineModel = lambda: moneyline_model("EPL")
EPLSpreadModel = lambda: spread_model("EPL")
EPLTotalModel = lambda: total_model("EPL")

LaLigaMoneylineModel = lambda: moneyline_model("LA_LIGA")
LaLigaSpreadModel = lambda: spread_model("LA_LIGA")
LaLigaTotalModel = lambda: total_model("LA_LIGA")

BundesligaMoneylineModel = lambda: moneyline_model("BUNDESLIGA")
BundesligaSpreadModel = lambda: spread_model("BUNDESLIGA")
BundesligaTotalModel = lambda: total_model("BUNDESLIGA")

SerieAMoneylineModel = lambda: moneyline_model("SERIE_A")
SerieASpreadModel = lambda: spread_model("SERIE_A")
SerieATotalModel = lambda: total_model("SERIE_A")

Ligue1MoneylineModel = lambda: moneyline_model("LIGUE_1")
Ligue1SpreadModel = lambda: spread_model("LIGUE_1")
Ligue1TotalModel = lambda: total_model("LIGUE_1")

MLSMoneylineModel = lambda: moneyline_model("MLS")
MLSSpreadModel = lambda: spread_model("MLS")
MLSTotalModel = lambda: total_model("MLS")

UCLMoneylineModel = lambda: moneyline_model("UCL")
UCLSpreadModel = lambda: spread_model("UCL")
UCLTotalModel = lambda: total_model("UCL")

UELMoneylineModel = lambda: moneyline_model("UEL")
UELSpreadModel = lambda: spread_model("UEL")
UELTotalModel = lambda: total_model("UEL")

UECLMoneylineModel = lambda: moneyline_model("UECL")
UECLSpreadModel = lambda: spread_model("UECL")
UECLTotalModel = lambda: total_model("UECL")

ScottishPremMoneylineModel = lambda: moneyline_model("SCOTTISH_PREM")
ScottishPremSpreadModel = lambda: spread_model("SCOTTISH_PREM")
ScottishPremTotalModel = lambda: total_model("SCOTTISH_PREM")

SaudiProMoneylineModel = lambda: moneyline_model("SAUDI_PRO")
SaudiProSpreadModel = lambda: spread_model("SAUDI_PRO")
SaudiProTotalModel = lambda: total_model("SAUDI_PRO")

BelgianProMoneylineModel = lambda: moneyline_model("BELGIAN_PRO")
BelgianProSpreadModel = lambda: spread_model("BELGIAN_PRO")
BelgianProTotalModel = lambda: total_model("BELGIAN_PRO")

TurkishSuperMoneylineModel = lambda: moneyline_model("TURKISH_SUPER")
TurkishSuperSpreadModel = lambda: spread_model("TURKISH_SUPER")
TurkishSuperTotalModel = lambda: total_model("TURKISH_SUPER")

LibertadoresMoneylineModel = lambda: moneyline_model("LIBERTADORES")
LibertadoresSpreadModel = lambda: spread_model("LIBERTADORES")
LibertadoresTotalModel = lambda: total_model("LIBERTADORES")

ArgentinaMoneylineModel = lambda: moneyline_model("ARGENTINA")
ArgentinaSpreadModel = lambda: spread_model("ARGENTINA")
ArgentinaTotalModel = lambda: total_model("ARGENTINA")

JLeagueMoneylineModel = lambda: moneyline_model("J_LEAGUE")
JLeagueSpreadModel = lambda: spread_model("J_LEAGUE")
JLeagueTotalModel = lambda: total_model("J_LEAGUE")

ALeagueMoneylineModel = lambda: moneyline_model("A_LEAGUE")
ALeagueSpreadModel = lambda: spread_model("A_LEAGUE")
ALeagueTotalModel = lambda: total_model("A_LEAGUE")


# Hockey
NHLMoneylineModel = lambda: moneyline_model("NHL")
NHLSpreadModel = lambda: spread_model("NHL")
NHLTotalModel = lambda: total_model("NHL")

# Darts
DartsPDCMoneylineModel = lambda: moneyline_model("DARTS_PDC")
DartsPDCSpreadModel = lambda: spread_model("DARTS_PDC")
DartsPDCTotalModel = lambda: total_model("DARTS_PDC")

DartsMODUSMoneylineModel = lambda: moneyline_model("DARTS_MODUS")
DartsMODUSSpreadModel = lambda: spread_model("DARTS_MODUS")
DartsMODUSTotalModel = lambda: total_model("DARTS_MODUS")

# Baseball
MLBMoneylineModel = lambda: moneyline_model("MLB")
MLBSpreadModel = lambda: spread_model("MLB")
MLBTotalModel = lambda: total_model("MLB")

KBOMoneylineModel = lambda: moneyline_model("KBO")
KBOSpreadModel = lambda: spread_model("KBO")
KBOTotalModel = lambda: total_model("KBO")

NPBMoneylineModel = lambda: moneyline_model("NPB")
NPBSpreadModel = lambda: spread_model("NPB")
NPBTotalModel = lambda: total_model("NPB")

# Football
NFLMoneylineModel = lambda: moneyline_model("NFL")
NFLSpreadModel = lambda: spread_model("NFL")
NFLTotalModel = lambda: total_model("NFL")

CFBMoneylineModel = lambda: moneyline_model("CFB")
CFBSpreadModel = lambda: spread_model("CFB")
CFBTotalModel = lambda: total_model("CFB")

# Tennis
ATPMoneylineModel = lambda: moneyline_model("ATP")
ATPSpreadModel = lambda: spread_model("ATP")
ATPTotalModel = lambda: total_model("ATP")

WTAMoneylineModel = lambda: moneyline_model("WTA")
WTASpreadModel = lambda: spread_model("WTA")
WTATotalModel = lambda: total_model("WTA")

# Esports
CS2MoneylineModel = lambda: moneyline_model("CS2")
CS2SpreadModel = lambda: spread_model("CS2")
CS2TotalModel = lambda: total_model("CS2")

LoLMoneylineModel = lambda: moneyline_model("LOL")
LoLSpreadModel = lambda: spread_model("LOL")
LoLTotalModel = lambda: total_model("LOL")

Dota2MoneylineModel = lambda: moneyline_model("DOTA2")
Dota2SpreadModel = lambda: spread_model("DOTA2")
Dota2TotalModel = lambda: total_model("DOTA2")

ValorantMoneylineModel = lambda: moneyline_model("VALORANT")
ValorantSpreadModel = lambda: spread_model("VALORANT")
ValorantTotalModel = lambda: total_model("VALORANT")

RainbowSixMoneylineModel = lambda: moneyline_model("RAINBOW_SIX")
RainbowSixSpreadModel = lambda: spread_model("RAINBOW_SIX")
RainbowSixTotalModel = lambda: total_model("RAINBOW_SIX")


__all__ = [
    "ALeagueMoneylineModel",
    "ALeagueSpreadModel",
    "ALeagueTotalModel",
    # Tennis
    "ATPMoneylineModel",
    "ATPSpreadModel",
    "ATPTotalModel",
    "ArgentinaMoneylineModel",
    "ArgentinaSpreadModel",
    "ArgentinaTotalModel",
    "BelgianProMoneylineModel",
    "BelgianProSpreadModel",
    "BelgianProTotalModel",
    "BundesligaMoneylineModel",
    "BundesligaSpreadModel",
    "BundesligaTotalModel",
    "CFBMoneylineModel",
    "CFBSpreadModel",
    "CFBTotalModel",
    # Esports
    "CS2MoneylineModel",
    "CS2SpreadModel",
    "CS2TotalModel",
    "DartsMODUSMoneylineModel",
    "DartsMODUSSpreadModel",
    "DartsMODUSTotalModel",
    # Darts
    "DartsPDCMoneylineModel",
    "DartsPDCSpreadModel",
    "DartsPDCTotalModel",
    "Dota2MoneylineModel",
    "Dota2SpreadModel",
    "Dota2TotalModel",
    # Soccer
    "EPLMoneylineModel",
    "EPLSpreadModel",
    "EPLTotalModel",
    "EuroLeagueMoneylineModel",
    "EuroLeagueSpreadModel",
    "EuroLeagueTotalModel",
    "JLeagueMoneylineModel",
    "JLeagueSpreadModel",
    "JLeagueTotalModel",
    "KBOMoneylineModel",
    "KBOSpreadModel",
    "KBOTotalModel",
    "LaLigaMoneylineModel",
    "LaLigaSpreadModel",
    "LaLigaTotalModel",
    "LibertadoresMoneylineModel",
    "LibertadoresSpreadModel",
    "LibertadoresTotalModel",
    "Ligue1MoneylineModel",
    "Ligue1SpreadModel",
    "Ligue1TotalModel",
    "LoLMoneylineModel",
    "LoLSpreadModel",
    "LoLTotalModel",
    # Baseball
    "MLBMoneylineModel",
    "MLBSpreadModel",
    "MLBTotalModel",
    "MLSMoneylineModel",
    "MLSSpreadModel",
    "MLSTotalModel",
    "MarketScopedModel",
    # Basketball
    "NBAMoneylineModel",
    "NBASpreadModel",
    "NBATotalModel",
    "NCAABMoneylineModel",
    "NCAABSpreadModel",
    "NCAABTotalModel",
    # Football
    "NFLMoneylineModel",
    "NFLSpreadModel",
    "NFLTotalModel",
    # Hockey
    "NHLMoneylineModel",
    "NHLSpreadModel",
    "NHLTotalModel",
    "NPBMoneylineModel",
    "NPBSpreadModel",
    "NPBTotalModel",
    "RainbowSixMoneylineModel",
    "RainbowSixSpreadModel",
    "RainbowSixTotalModel",
    "SaudiProMoneylineModel",
    "SaudiProSpreadModel",
    "SaudiProTotalModel",
    "ScottishPremMoneylineModel",
    "ScottishPremSpreadModel",
    "ScottishPremTotalModel",
    "SerieAMoneylineModel",
    "SerieASpreadModel",
    "SerieATotalModel",
    "TurkishSuperMoneylineModel",
    "TurkishSuperSpreadModel",
    "TurkishSuperTotalModel",
    "UCLMoneylineModel",
    "UCLSpreadModel",
    "UCLTotalModel",
    "UECLMoneylineModel",
    "UECLSpreadModel",
    "UECLTotalModel",
    "UELMoneylineModel",
    "UELSpreadModel",
    "UELTotalModel",
    "ValorantMoneylineModel",
    "ValorantSpreadModel",
    "ValorantTotalModel",
    "WNBAMoneylineModel",
    "WNBASpreadModel",
    "WNBATotalModel",
    "WNCAABMoneylineModel",
    "WNCAABSpreadModel",
    "WNCAABTotalModel",
    "WTAMoneylineModel",
    "WTASpreadModel",
    "WTATotalModel",
    "get_market_model",
    "moneyline_model",
    "spread_model",
    "total_model",
]
