"""Esports Contextual v7 Architecture (cs2/dota2/lol/valorant/r6-contextual-v7).

Architecture:
    logit(P_true) = logit(P_TieredElo) + f(Context)

Context Feature Mechanisms:
1. CS2: Map-specific Elo differential, LAN/Online venue modifier, series format (Bo1/Bo3/Bo5).
2. LoL: Side advantage (Blue vs Red) by patch regime, draft tempo, objective control rating.
3. Dota 2: Radiant vs Dire balance, draft versatility, early game laning phase power.
4. Valorant: Map pool win rate, Attack/Defense side skew, agent meta familiarity.
5. Rainbow Six: Map balance, bomb site defense advantage, operator versatility.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ESPORTS_V7_TITLES = {"cs2", "dota2", "lol", "valorant", "rainbow_six"}


def _logit(p: float, eps: float = 1e-6) -> float:
    p_c = max(eps, min(1.0 - eps, p))
    return math.log(p_c / (1.0 - p_c))


def _expit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass(frozen=True)
class EsportsContextFeatures:
    game_title: str  # "cs2" | "dota2" | "lol" | "valorant" | "rainbow_six"
    team_a: str
    team_b: str
    elo_prob_a: float
    map_name: str | None = None
    is_lan: bool = True
    series_format: str = "Bo3"  # "Bo1", "Bo3", "Bo5"
    side_a: str = "blue"  # "blue"/"red", "radiant"/"dire", "ct"/"t", "atk"/"def"
    team_a_map_win_rate: float = 0.50
    team_b_map_win_rate: float = 0.50
    draft_tempo_advantage_a: float = 0.0
    roster_continuity_a: float = 1.0
    roster_continuity_b: float = 1.0


@dataclass(frozen=True)
class EsportsContextualForecast:
    game_title: str
    team_a: str
    team_b: str
    elo_prior_prob_a: float
    context_logit_adjustment: float
    prob_a_wins: float
    prob_b_wins: float
    edge_vs_elo_prior: float


class EsportsContextualV7Model:
    """Contextual residual learning engine over Tiered Elo priors for Esports."""

    def __init__(self, l2_shrinkage: float = 0.80) -> None:
        self.l2_shrinkage = l2_shrinkage

    def forecast_match(self, feat: EsportsContextFeatures) -> EsportsContextualForecast:
        prior_logit = _logit(feat.elo_prob_a)
        title = feat.game_title.lower()

        # 1. Map advantage
        map_delta = (feat.team_a_map_win_rate - feat.team_b_map_win_rate) * 0.40

        # 2. Side advantage
        side_adj = 0.0
        if title == "lol":
            # Blue side historically carries +2.5% to +4.0% win rate advantage
            if feat.side_a.lower() in {"blue", "team_1"}:
                side_adj = +0.10
            else:
                side_adj = -0.10
        elif title == "dota2":
            # Radiant advantage
            if feat.side_a.lower() in {"radiant", "team_1"}:
                side_adj = +0.08
            else:
                side_adj = -0.08
        elif title == "cs2" and feat.map_name and feat.map_name.lower() in {"nuke", "ancient", "anubis"}:
            side_adj = +0.05 if feat.side_a.lower() == "ct" else -0.05

        # 3. LAN vs Online experience
        lan_adj = 0.0
        if feat.is_lan:
            lan_adj = (feat.roster_continuity_a - feat.roster_continuity_b) * 0.15

        # 4. Draft tempo & synergy
        draft_adj = feat.draft_tempo_advantage_a * 0.20

        # Format scaling: Bo1 has higher variance (lower logit magnifications); Bo5 rewards higher-skill
        fmt_mult = 1.0
        if feat.series_format.upper() == "BO1":
            fmt_mult = 0.75
        elif feat.series_format.upper() == "BO5":
            fmt_mult = 1.30

        raw_context = (map_delta + side_adj + lan_adj + draft_adj) * fmt_mult
        shrunk_context = raw_context * self.l2_shrinkage

        posterior_logit = prior_logit + shrunk_context
        p_a = _expit(posterior_logit)
        p_b = 1.0 - p_a

        return EsportsContextualForecast(
            game_title=feat.game_title,
            team_a=feat.team_a,
            team_b=feat.team_b,
            elo_prior_prob_a=round(feat.elo_prob_a, 4),
            context_logit_adjustment=round(shrunk_context, 4),
            prob_a_wins=round(p_a, 4),
            prob_b_wins=round(p_b, 4),
            edge_vs_elo_prior=round(p_a - feat.elo_prob_a, 4),
        )
