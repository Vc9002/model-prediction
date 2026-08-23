"""Point-in-time Empirical Bayes batter priors with Beta-Binomial shrinkage.

Inspired by Baseball Hydra and MLB quantitative research, this module replaces
noisy raw rolling stats with closed-form Bayesian shrinkage over plate appearances (PA).

Formulation:
    Given observed successes k_i out of n_i trials (PA or BIP):
        theta_hat_i = (k_i + alpha) / (n_i + alpha + beta)
                    = w_i * (k_i / n_i) + (1 - w_i) * mu_0
    where:
        mu_0 = alpha / (alpha + beta)  (league prior mean)
        M = alpha + beta               (shrinkage sample size / stabilization threshold)
        w_i = n_i / (n_i + M)          (sample size reliability weight)

Point-In-Time (PIT) Invariant:
    A player\'s prior state is computed strictly from games completed prior to
    event_start_utc. No game\'s own boxscore or plate appearances may enter its own prior.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

PRIOR_HYPERPARAMETERS: dict[str, tuple[float, float]] = {
    "k_pct": (0.225, 60.0),
    "bb_pct": (0.082, 120.0),
    "iso": (0.155, 160.0),
    "hard_hit_pct": (0.380, 80.0),
    "barrel_pct": (0.075, 100.0),
    "xwoba": (0.315, 200.0),
}

BATTING_ORDER_WEIGHTS: tuple[float, ...] = (
    0.125,  # 1st
    0.121,  # 2nd
    0.118,  # 3rd
    0.114,  # 4th
    0.111,  # 5th
    0.107,  # 6th
    0.104,  # 7th
    0.101,  # 8th
    0.099,  # 9th
)


def beta_binomial_shrink(k: float, n: float, metric: str) -> float:
    """Apply closed-form Empirical Bayes shrinkage to rate metric (successes / trials)."""
    if metric not in PRIOR_HYPERPARAMETERS:
        raise ValueError(f"Unknown metric for Beta-Binomial shrinkage: {metric}")
    mu_0, m = PRIOR_HYPERPARAMETERS[metric]
    if n <= 0:
        return mu_0
    alpha = mu_0 * m
    beta = (1.0 - mu_0) * m
    return (k + alpha) / (n + alpha + beta)


def continuous_empirical_bayes_shrink(obs_sum: float, n: float, metric: str) -> float:
    """Apply continuous Empirical Bayes shrinkage: (tau * mu_0 + obs_sum) / (tau + n)."""
    if metric not in PRIOR_HYPERPARAMETERS:
        raise ValueError(f"Unknown metric for continuous shrinkage: {metric}")
    mu_0, tau = PRIOR_HYPERPARAMETERS[metric]
    if n <= 0:
        return mu_0
    return (tau * mu_0 + obs_sum) / (tau + n)


@dataclass(slots=True)
class BatterGameRecord:
    player_id: str
    team_id: str
    game_date: str  # YYYY-MM-DD
    pa: int = 0
    ab: int = 0
    hits: int = 0
    doubles: int = 0
    triples: int = 0
    home_runs: int = 0
    strikeouts: int = 0
    walks: int = 0
    hit_by_pitch: int = 0
    hard_hit_count: int = 0
    barrel_count: int = 0
    bip_count: int = 0
    xwoba_sum: float = 0.0
    vs_hand: str = "all"  # 'R', 'L', or 'all'


@dataclass(slots=True)
class BatterPriorState:
    player_id: str
    total_pa: int = 0
    total_ab: int = 0
    total_hits: int = 0
    total_doubles: int = 0
    total_triples: int = 0
    total_home_runs: int = 0
    total_strikeouts: int = 0
    total_walks: int = 0
    total_hbp: int = 0
    total_bip: int = 0
    total_hard_hit: int = 0
    total_barrel: int = 0
    total_xwoba_sum: float = 0.0

    def shrunk_k_pct(self) -> float:
        return beta_binomial_shrink(self.total_strikeouts, self.total_pa, "k_pct")

    def shrunk_bb_pct(self) -> float:
        return beta_binomial_shrink(self.total_walks, self.total_pa, "bb_pct")

    def shrunk_iso(self) -> float:
        tb = self.total_hits + self.total_doubles + 2 * self.total_triples + 3 * self.total_home_runs
        iso_numerator = max(0, tb - self.total_hits)
        return continuous_empirical_bayes_shrink(iso_numerator, self.total_ab, "iso")

    def shrunk_hard_hit_pct(self) -> float:
        return beta_binomial_shrink(self.total_hard_hit, self.total_bip, "hard_hit_pct")

    def shrunk_barrel_pct(self) -> float:
        return beta_binomial_shrink(self.total_barrel, self.total_bip, "barrel_pct")

    def shrunk_xwoba(self) -> float:
        singles = max(0, self.total_hits - self.total_doubles - self.total_triples - self.total_home_runs)
        woba_sum = (
            0.690 * self.total_walks
            + 0.720 * self.total_hbp
            + 0.880 * singles
            + 1.240 * self.total_doubles
            + 1.560 * self.total_triples
            + 2.070 * self.total_home_runs
        )
        if self.total_xwoba_sum > 0:
            woba_sum = 0.5 * woba_sum + 0.5 * self.total_xwoba_sum
        return continuous_empirical_bayes_shrink(woba_sum, self.total_pa, "xwoba")


@dataclass
class LineupPriorVector:
    xwoba: float
    k_pct: float
    bb_pct: float
    iso: float
    barrel_pct: float
    hard_hit_pct: float
    sample_pa: int
    barrel_available: bool = False
    hard_hit_available: bool = False
    xwoba_available: bool = False


class PointInTimeBatterPriorEngine:
    """Maintains sequential PIT batter states and constructs order-weighted lineup features."""

    def __init__(self, snapshot_path: str | Path | None = None) -> None:
        self._player_history: dict[str, list[BatterGameRecord]] = {}
        self._team_history: dict[str, list[tuple[str, str, int]]] = {}
        if snapshot_path is not None:
            self._load_from_snapshot(Path(snapshot_path))

    def _load_from_snapshot(self, path: Path) -> None:
        if not path.exists():
            return
        import json

        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                game_date = str(row.get("game_date") or row.get("official_date") or "")[:10]
                for side in ("home", "away"):
                    team_name = row.get(f"{side}_team", "")
                    for p in row.get(f"{side}_batters", []):
                        pid = str(p.get("id") or p.get("player_id") or "")
                        if not pid:
                            continue
                        pa = int(p.get("pa") or p.get("plateAppearances") or 0)
                        ab = int(p.get("ab") or p.get("atBats") or pa)
                        hits = int(p.get("hits") or p.get("h") or 0)
                        doubles = int(p.get("doubles") or p.get("2b") or 0)
                        triples = int(p.get("triples") or p.get("3b") or 0)
                        hr = int(p.get("hr") or p.get("homeRuns") or 0)
                        bb = int(p.get("bb") or p.get("baseOnBalls") or 0)
                        so = int(p.get("so") or p.get("strikeOuts") or 0)
                        self.update_player_game(
                            BatterGameRecord(
                                player_id=pid,
                                game_date=game_date,
                                team_id=team_name,
                                pa=pa,
                                ab=ab,
                                hits=hits,
                                doubles=doubles,
                                triples=triples,
                                home_runs=hr,
                                walks=bb,
                                strikeouts=so,
                            )
                        )

    def update_player_game(self, record: BatterGameRecord) -> None:
        """Record a player game result sequentially."""
        self._player_history.setdefault(record.player_id, []).append(record)
        if record.team_id:
            self._team_history.setdefault(record.team_id, []).append(
                (record.player_id, record.game_date, record.pa)
            )

    def ingest_game_record(self, record: BatterGameRecord) -> None:
        self.update_player_game(record)

    def get_player_prior(
        self,
        player_id: str,
        as_of_date: str | None = None,
        vs_hand: str | None = None,
    ) -> BatterPriorState:
        """Retrieve shrunk prior for a player strictly as-of a given date, optionally by pitcher hand."""
        records = self._player_history.get(player_id, [])
        if as_of_date is not None:
            records = [r for r in records if r.game_date < as_of_date]

        if vs_hand in ("R", "L"):
            hand_records = [r for r in records if r.vs_hand in (vs_hand, "all")]
            if sum(r.pa for r in hand_records) >= 30:
                records = hand_records

        state = BatterPriorState(player_id=player_id)
        for r in records:
            state.total_pa += r.pa
            state.total_ab += r.ab
            state.total_hits += r.hits
            state.total_doubles += r.doubles
            state.total_triples += r.triples
            state.total_home_runs += r.home_runs
            state.total_strikeouts += r.strikeouts
            state.total_walks += r.walks
            state.total_hbp += r.hit_by_pitch
            state.total_bip += r.bip_count
            state.total_hard_hit += r.hard_hit_count
            state.total_barrel += r.barrel_count
            state.total_xwoba_sum += r.xwoba_sum
        return state

    def evaluate_confirmed_lineup(
        self,
        batting_order_player_ids: Sequence[str],
        as_of_date: str | None = None,
        opposing_pitcher_hand: str | None = None,
    ) -> LineupPriorVector:
        """Evaluate a confirmed 9-man batting order using order weights."""
        if len(batting_order_player_ids) != 9:
            weights = [1.0 / max(1, len(batting_order_player_ids))] * len(batting_order_player_ids)
        else:
            weights = list(BATTING_ORDER_WEIGHTS)

        agg_xwoba = 0.0
        agg_k = 0.0
        agg_bb = 0.0
        agg_iso = 0.0
        agg_barrel = 0.0
        agg_hard_hit = 0.0
        total_pa = 0

        has_xwoba = False
        has_barrel = False
        has_hard_hit = False

        for player_id, w in zip(batting_order_player_ids, weights):
            prior = self.get_player_prior(player_id, as_of_date=as_of_date, vs_hand=opposing_pitcher_hand)
            agg_xwoba += w * prior.shrunk_xwoba()
            agg_k += w * prior.shrunk_k_pct()
            agg_bb += w * prior.shrunk_bb_pct()
            agg_iso += w * prior.shrunk_iso()
            agg_barrel += w * prior.shrunk_barrel_pct()
            agg_hard_hit += w * prior.shrunk_hard_hit_pct()
            total_pa += prior.total_pa
            if prior.total_xwoba_sum > 0:
                has_xwoba = True
            if prior.total_barrel > 0:
                has_barrel = True
            if prior.total_hard_hit > 0:
                has_hard_hit = True

        return LineupPriorVector(
            xwoba=round(agg_xwoba, 4),
            k_pct=round(agg_k, 4),
            bb_pct=round(agg_bb, 4),
            iso=round(agg_iso, 4),
            barrel_pct=round(agg_barrel, 4),
            hard_hit_pct=round(agg_hard_hit, 4),
            sample_pa=total_pa,
            barrel_available=has_barrel,
            hard_hit_available=has_hard_hit,
            xwoba_available=has_xwoba,
        )

    def evaluate_projected_team_offense(
        self,
        team_id: str,
        as_of_date: str,
        lookback_games: int = 15,
        opposing_pitcher_hand: str | None = None,
    ) -> LineupPriorVector:
        """Estimate projected team offense from preceding games when order is not confirmed."""
        hist = self._team_history.get(team_id, [])
        valid_entries = [(p_id, dt, pa) for (p_id, dt, pa) in hist if dt < as_of_date]
        if not valid_entries:
            mu = PRIOR_HYPERPARAMETERS
            return LineupPriorVector(
                xwoba=mu["xwoba"][0],
                k_pct=mu["k_pct"][0],
                bb_pct=mu["bb_pct"][0],
                iso=mu["iso"][0],
                barrel_pct=mu["barrel_pct"][0],
                hard_hit_pct=mu["hard_hit_pct"][0],
                sample_pa=0,
            )

        # Select strictly preceding unique game dates for this team
        unique_dates = sorted({dt for _, dt, _ in valid_entries})
        selected_dates = set(unique_dates[-lookback_games:])
        recent = [e for e in valid_entries if e[1] in selected_dates]
        player_pa: dict[str, int] = {}
        for p_id, _, pa in recent:
            player_pa[p_id] = player_pa.get(p_id, 0) + pa

        total_recent_pa = sum(player_pa.values())
        if total_recent_pa <= 0:
            total_recent_pa = 1

        agg_xwoba = 0.0
        agg_k = 0.0
        agg_bb = 0.0
        agg_iso = 0.0
        agg_barrel = 0.0
        agg_hard_hit = 0.0
        total_sample_pa = 0

        has_xwoba = False
        has_barrel = False
        has_hard_hit = False

        for p_id, pa in player_pa.items():
            weight = pa / total_recent_pa
            prior = self.get_player_prior(p_id, as_of_date=as_of_date, vs_hand=opposing_pitcher_hand)
            agg_xwoba += weight * prior.shrunk_xwoba()
            agg_k += weight * prior.shrunk_k_pct()
            agg_bb += weight * prior.shrunk_bb_pct()
            agg_iso += weight * prior.shrunk_iso()
            agg_barrel += weight * prior.shrunk_barrel_pct()
            agg_hard_hit += weight * prior.shrunk_hard_hit_pct()
            total_sample_pa += prior.total_pa
            if prior.total_xwoba_sum > 0:
                has_xwoba = True
            if prior.total_barrel > 0:
                has_barrel = True
            if prior.total_hard_hit > 0:
                has_hard_hit = True

        return LineupPriorVector(
            xwoba=round(agg_xwoba, 4),
            k_pct=round(agg_k, 4),
            bb_pct=round(agg_bb, 4),
            iso=round(agg_iso, 4),
            barrel_pct=round(agg_barrel, 4),
            hard_hit_pct=round(agg_hard_hit, 4),
            sample_pa=total_sample_pa,
            barrel_available=has_barrel,
            hard_hit_available=has_hard_hit,
            xwoba_available=has_xwoba,
        )


BatterPriorEngine = PointInTimeBatterPriorEngine
