"""NCAAB v7 multi-season SHADOW replay components (inert, quick 261115-m5r).

Not registered anywhere: no entry in ``config/production.yaml`` / ``config/model.yaml``, no artifact, no
champion route. v6 is only read. This module differs from ``ncaab_v7_shadow`` in three ways, all driven
by the multi-season replay:

1. **As-of-season conference map.** ESPN stamps every scoreboard game with the teams' conference ids as
   they were that season, so realigned teams (Texas to the SEC, Colorado to the Big 12) carry the right
   tier prior in each season instead of today's. Conference ids resolve to conference names through the
   explicit table below; an id that is neither listed nor in ``UNMAPPED_D1_IDS`` raises (fail closed).
2. **Prior-season weights decay geometrically** (``carry ** age``, capped at ``MAX_CARRY_AGE`` seasons).
   ``ncaab_v7_shadow.season_weights`` gave every earlier season the same weight, which was harmless with
   a single prior season and is wrong with four.
3. **A pre-declared grid of ridge variants** (carryover, shrinkage toward the tier prior, tier-prior
   scale) is computed in one pass, so the research runner selects among them on the fit window only.

The component rows carry NO outcome fields: the runner joins outcomes by event id inside the stage that
is allowed to read them.

Point-in-time contract: a game only sees ``history`` (games before its ET calendar day). Conference
membership of the game's own season is read from the scoreboard (team membership is public before the
season starts; a result is never used).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.ncaab_scoreboard_stats import NCAABScoreboardStats
from .basketball import UpcomingGame
from .ncaab_spread_v6 import (
    ARTIFACT_PATH as SPREAD_V6_ARTIFACT,
)
from .ncaab_spread_v6 import (
    DEFAULT_CONFERENCE_TIER_PRIORS,
    ELO_POINTS_PER_MARGIN_POINT,
    NCAABSpreadModelV6,
    load_verified_artifact,
)
from .ncaab_total_v6 import ncaab_total_v6_model
from .ncaab_v7_shadow import HCA_PRIOR_POINTS, history_neutral

NCAAB_V7_MULTISEASON_VERSION = "ncaab-v7-multiseason-shadow"
OFFSEASON_GAP_DAYS = 150.0
MAX_CARRY_AGE = 3  # prior seasons older than this are dropped from the ridge
MIN_FIT_GAMES = 30
HCA_LAMBDA = 20.0
NON_D1_PRIOR = 1180.0

# Pre-declared ridge grid (pinned before any outcome is joined; see PIN in scripts/ncaab_v7_multiseason.py).
CARRY_GRID = (0.0, 0.1, 0.25, 0.5)
LAMBDA_GRID = (1.0, 2.0, 3.0, 8.0)
KAPPA_GRID = (
    1.0,
    1.4,
    2.0,
)  # scale on the conference-tier prior (the tier gap was compressed vs margins seen)

# ESPN scoreboard conference id -> conference name in DEFAULT_CONFERENCE_TIER_PRIORS. Derived from the
# 2025-26 / 2026-27 scoreboards by plurality against ``data/entities/ncaab_conferences.json``; id 30 is the
# WAC (renamed United Athletic Conference in 2023-24), id 21 the Pac-12.
CONFERENCE_ID_NAMES: dict[str, str] = {
    "1": "America East Conference",
    "2": "Atlantic Coast Conference",
    "3": "Atlantic 10 Conference",
    "4": "Big East Conference",
    "5": "Big Sky Conference",
    "6": "Big South Conference",
    "7": "Big Ten Conference",
    "8": "Big 12 Conference",
    "9": "Big West Conference",
    "10": "Coastal Athletic Association",
    "11": "Conference USA",
    "12": "Ivy League",
    "13": "Metro Conference",
    "14": "Mid-American Conference",
    "16": "Mid-Eastern Athletic Conference",
    "18": "Missouri Valley Conference",
    "19": "Northeast Conference",
    "20": "Ohio Valley Conference",
    "21": "Pac-12 Conference",
    "22": "Patriot League",
    "23": "Southeastern Conference",
    "24": "Southern Conference",
    "25": "Southland Conference",
    "26": "Southwestern Athletic Conference",
    "27": "Sun Belt Conference",
    "29": "West Coast Conference",
    "30": "United Athletic Conference",
    "44": "Mountain West Conference",
    "45": "Horizon League",
    "46": "Atlantic Sun Conference",
    "49": "Summit League",
    "62": "American Conference",
}
# D1 independents (Chicago State, Hartford in 2022-24): in the D1 map, scored with the non-D1 prior,
# exactly how v6 treats a team whose conference has no tier entry.
UNMAPPED_D1_IDS = frozenset({"43"})
# The tier table is post-realignment: its "Pac-12" (1550) describes the 2026 rebuilt league. Through
# 2023-24 id 21 was the power-conference Pac-12 (Arizona, UCLA, Oregon, Utah, Colorado), tier of the ACC.
LEGACY_PAC12_ID = "21"
LEGACY_PAC12_LAST_SEASON_YEAR = 2024
LEGACY_PAC12_TIER = 1660.0


def build_asof_conferences(events: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, str]]:
    """season_year -> team -> conference id, from the scoreboard (D1 teams only; id None is non-D1).

    Raises on a team that carries two different ids in one season, and (via ``tier_for``) on an id with
    no name.
    """
    out: dict[int, dict[str, str]] = {}
    for e in events:
        season = int(e["season_year"])
        for side in ("home", "away"):
            cid = e.get(f"{side}_conference_id")
            if cid is None:
                continue
            team = str(e[f"{side}_team"])
            seen = out.setdefault(season, {})
            if seen.setdefault(team, str(cid)) != str(cid):
                raise ValueError(f"{team} has two conference ids in season {season}")
    return out


def tier_for(
    conf_id: str, season_year: int, tiers: Mapping[str, float] = DEFAULT_CONFERENCE_TIER_PRIORS
) -> float:
    """Tier Elo prior of a conference id in a season (era override for the legacy Pac-12)."""
    if conf_id in UNMAPPED_D1_IDS:
        return NON_D1_PRIOR
    if conf_id not in CONFERENCE_ID_NAMES:
        raise ValueError(f"conference id {conf_id!r} has no name (season {season_year}); list it explicitly")
    if conf_id == LEGACY_PAC12_ID and season_year <= LEGACY_PAC12_LAST_SEASON_YEAR:
        return LEGACY_PAC12_TIER
    return float(tiers.get(CONFERENCE_ID_NAMES[conf_id], NON_D1_PRIOR))


def conference_name_for(conf_id: str) -> str | None:
    return CONFERENCE_ID_NAMES.get(conf_id)


def geometric_season_weights(
    seasons: np.ndarray, current_season: int, carry: float, max_age: int = MAX_CARRY_AGE
) -> np.ndarray:
    """``carry ** age`` for games from ``age`` seasons back (0 for age > max_age or a later season)."""
    age = current_season - seasons
    w = np.where((age >= 0) & (age <= max_age), carry ** np.clip(age, 0, None).astype(float), 0.0)
    return w.astype(float)


def ridge_by_accumulation(
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    margin: np.ndarray,
    weights: np.ndarray,
    venue: np.ndarray,
    n_teams: int,
    prior: np.ndarray,
    lam: float,
    hca_prior: float = HCA_PRIOR_POINTS,
    hca_lam: float = HCA_LAMBDA,
    gram: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple[np.ndarray, float]:
    """Massey ratings with a tier prior, built from a sparse accumulation of the normal equations.

    Same objective as ``ncaab_v7_shadow.ridge_team_strengths`` (tested equal); ``venue`` is 1.0 for a
    home-court game and 0.0 for a neutral one. ``gram`` reuses (G, b) across lambda/prior variants.
    """
    if len(margin) == 0:
        return prior.copy(), hca_prior
    g, b = (
        gram
        if gram is not None
        else accumulate_normal_equations(home_idx, away_idx, margin, weights, venue, n_teams)
    )
    reg = np.eye(n_teams + 1) * lam
    reg[n_teams, n_teams] = hca_lam
    target = np.concatenate([prior, [hca_prior]])
    sol = np.linalg.solve(g + reg, b + reg @ target)
    return sol[:n_teams], float(sol[n_teams])


def accumulate_normal_equations(
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    margin: np.ndarray,
    weights: np.ndarray,
    venue: np.ndarray,
    n_teams: int,
) -> tuple[np.ndarray, np.ndarray]:
    m = n_teams + 1
    g = np.zeros((m, m))
    b = np.zeros(m)
    wv = weights * venue
    np.add.at(g, (home_idx, home_idx), weights)
    np.add.at(g, (away_idx, away_idx), weights)
    np.add.at(g, (home_idx, away_idx), -weights)
    np.add.at(g, (away_idx, home_idx), -weights)
    np.add.at(g, (home_idx, n_teams), wv)
    np.add.at(g, (n_teams, home_idx), wv)
    np.add.at(g, (away_idx, n_teams), -wv)
    np.add.at(g, (n_teams, away_idx), -wv)
    g[n_teams, n_teams] = float(wv.sum())  # venue is 0/1, so venue**2 == venue
    np.add.at(b, home_idx, weights * margin)
    np.add.at(b, away_idx, -weights * margin)
    b[n_teams] = float((wv * margin).sum())
    return g, b


def variant_key(carry: float, lam: float, kappa: float) -> str:
    return f"rg_c{round(carry * 100):03d}_l{round(lam):02d}_k{round(kappa * 10):02d}"


def variant_grid() -> list[tuple[float, float, float]]:
    return [(c, lam, k) for c in CARRY_GRID for lam in LAMBDA_GRID for k in KAPPA_GRID]


class NCAABV7MultiSeasonModel:
    """Walk-forward feature components for the multi-season v7 replay (no outcomes, no combiner)."""

    def __init__(
        self,
        season_conference_ids: Mapping[int, Mapping[str, str]],
        stats_root: Path | str | None = None,
        asof_map: bool = True,
        rank_prior_per_slot: float = 8.0,
    ) -> None:
        self.asof_map = asof_map
        self.rank_prior_per_slot = rank_prior_per_slot
        self.version = NCAAB_V7_MULTISEASON_VERSION
        self._conf_ids = season_conference_ids
        self._stats = NCAABScoreboardStats(stats_root)
        self._v6 = NCAABSpreadModelV6.from_artifact(load_verified_artifact(SPREAD_V6_ARTIFACT))
        self._v6._stats = self._stats  # the replay reads archived ranks/neutral flags from the research root
        self._v6_total = ncaab_total_v6_model()
        if getattr(self._v6_total, "_stats", None) is not None:
            self._v6_total._stats = self._stats
        self._today_priors = dict(self._v6.team_priors)
        self._today_confs = dict(self._v6_total.team_conferences)
        # per-season tier prior of every D1 team and the D1 sets, built once
        self.seasons = sorted(self._conf_ids)
        self.tier_by_season: dict[int, dict[str, float]] = {
            s: {t: tier_for(cid, s) for t, cid in teams.items()} for s, teams in self._conf_ids.items()
        }
        self.team_index: dict[str, int] = {
            t: i for i, t in enumerate(sorted({t for teams in self._conf_ids.values() for t in teams}))
        }

    # -- season / map helpers
    def current_season(self, history: Sequence[GameRecord], start: datetime) -> int:
        last = max(history, key=lambda g: g.start)
        season = int(last.season_year or 0)
        if (start - last.start).total_seconds() / 86400.0 > OFFSEASON_GAP_DAYS:
            season += 1
        return season

    def _install_maps(self, season_now: int) -> dict[str, float]:
        """Point the v6 models at the season's conference map; returns the season's team -> tier prior."""
        if not self.asof_map:
            return self._today_priors
        tiers = self.tier_by_season.get(season_now, {})
        self._v6.team_priors = dict(tiers)
        self._v6_total.team_conferences = {
            t: CONFERENCE_ID_NAMES[cid]
            for t, cid in self._conf_ids.get(season_now, {}).items()
            if cid in CONFERENCE_ID_NAMES
        }
        return tiers

    # -- components
    def components(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[dict[str, Any]]:
        """One flat dict per upcoming game (features only, never an outcome)."""
        if not upcoming:
            return []
        start = parse_utc(upcoming[0].event_start_utc)
        season_now = self.current_season(history, start)
        tiers_now = self._install_maps(season_now)
        v6_preds = {
            p.event_id: p for p in self._v6.predict_games(history, upcoming) if p.market_type == "moneyline"
        }
        v6_tot = {
            p.event_id: p for p in self._v6_total.predict_games(history, upcoming) if p.market_type == "total"
        }
        ranks = self._stats.last_known_ranks(history) if self.rank_prior_per_slot > 0 else {}

        n = len(self.team_index)
        base_tier = np.zeros(n)
        for t, i in self.team_index.items():
            tier = tiers_now.get(t)
            if tier is None:  # not in this season's map: fall back to the latest earlier season it was in
                for s in reversed(self.seasons):
                    if s < season_now and t in self.tier_by_season[s]:
                        tier = self.tier_by_season[s][t]
                        break
            if tier is None:
                tier = 1500.0
            if t in ranks:
                tier += self.rank_prior_per_slot * (26 - ranks[t])
            base_tier[i] = (tier - 1500.0) / ELO_POINTS_PER_MARGIN_POINT

        d1_games = [
            g
            for g in history
            if g.home_team in self.team_index
            and g.away_team in self.team_index
            and g.home_team in self._conf_ids.get(int(g.season_year or 0), {})
            and g.away_team in self._conf_ids.get(int(g.season_year or 0), {})
        ]
        hidx = np.array([self.team_index[g.home_team] for g in d1_games], dtype=int)
        aidx = np.array([self.team_index[g.away_team] for g in d1_games], dtype=int)
        margin = np.array([g.margin for g in d1_games], dtype=float)
        gseason = np.array([int(g.season_year or 0) for g in d1_games])
        venue = np.array([0.0 if history_neutral(g, self._stats) else 1.0 for g in d1_games])

        fits: dict[str, tuple[np.ndarray, float]] = {}
        for carry in CARRY_GRID:
            w = geometric_season_weights(gseason, season_now, carry)
            keep = w > 0
            enough = int(keep.sum()) >= MIN_FIT_GAMES
            gram = (
                accumulate_normal_equations(hidx[keep], aidx[keep], margin[keep], w[keep], venue[keep], n)
                if enough
                else None
            )
            for lam in LAMBDA_GRID:
                for kappa in KAPPA_GRID:
                    prior = kappa * base_tier
                    key = variant_key(carry, lam, kappa)
                    if gram is None:
                        fits[key] = (prior.copy(), HCA_PRIOR_POINTS)
                    else:
                        fits[key] = ridge_by_accumulation(
                            hidx[keep],
                            aidx[keep],
                            margin[keep],
                            w[keep],
                            venue[keep],
                            n,
                            prior,
                            lam,
                            gram=gram,
                        )

        games_cur: dict[str, int] = {}
        for g in history:
            if int(g.season_year or 0) == season_now:
                games_cur[g.home_team] = games_cur.get(g.home_team, 0) + 1
                games_cur[g.away_team] = games_cur.get(g.away_team, 0) + 1

        d1_now = tiers_now if self.asof_map else self._today_priors
        rows: list[dict[str, Any]] = []
        for u in upcoming:
            p6 = v6_preds[u.event_id]
            fb = p6.feature_basis
            h_d1 = u.home_team in d1_now
            a_d1 = u.away_team in d1_now
            row: dict[str, Any] = {
                "event_id": u.event_id,
                "season_now": season_now,
                "neutral": bool(u.neutral_site),
                "d1_home": h_d1,
                "d1_away": a_d1,
                "tier_home": float(d1_now[u.home_team]) if h_d1 else None,
                "tier_away": float(d1_now[u.away_team]) if a_d1 else None,
                "games_home": games_cur.get(u.home_team, 0),
                "games_away": games_cur.get(u.away_team, 0),
                "v6_mean": float(p6.inference_inputs["mean"]),
                "v6_scale": float(p6.inference_inputs["scale"]),
                "v6_sd": float(p6.inference_inputs["sd"]),
                "v6_p_home": float(p6.probabilities["home"]),
                "elo_margin": float(fb["elo_margin"]),
                "trend_margin": float(fb["trend_margin"]),
                "sample": int(fb["current_season_sample"]),
            }
            t6 = v6_tot.get(u.event_id)
            if t6 is not None:
                row["v6_total"] = float(t6.inference_inputs["mean"])
                row["v6_total_sd"] = float(t6.inference_inputs["sd"])
            if h_d1 and a_d1 and u.home_team in self.team_index and u.away_team in self.team_index:
                hi, ai = self.team_index[u.home_team], self.team_index[u.away_team]
                for key, (r, hca) in fits.items():
                    row[key] = round(float(r[hi] - r[ai] + (0.0 if u.neutral_site else hca)), 4)
            rows.append(row)
        return rows
