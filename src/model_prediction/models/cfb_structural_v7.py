"""College Football Structural v7 research challenger (cfb-structural-v7). Never Main, never the auto-buyer.

v7 = point-in-time opponent-adjusted play-by-play ratings (``features/cfb_features_v7.py``, the rating core shared
with ``scripts/cfb_v7_fit.py``) -> a linear team-points model -> calibrated margin and total -> a mean-centred
discrete margin pmf with fitted key-number mass (v6.2's construction, refit for v7) and a discretised normal total
pmf. Moneyline, spread cover and total probabilities all come from those pmfs.

Everything numeric comes from the hash-verified artifact ``config/models/cfb-structural-v7.json``. The pmfs are
analytic (no RNG). ``cfb-structural-v7.1`` (quick-261122-v71, ``scripts/cfb_v71_fit.py``) is the same code with
the 2026-11-22 bug-hunt fixes switched on by its artifact: a neutral-site margin intercept, FBS-only CFBD
covariates, each game fit at min(run clock, its research decision time), points-only rows only for games ESPN will
never cover, and a model-input snapshot per result. v7.1 is routed after v6.2 in ``challengers.NCAAF``; v7 stays
registered, unrouted, and prices exactly as before. ``cfb-structural-v7.2`` (quick-261123-6q5) is v7.1's artifact
plus ``total_probability_logit_slope`` (totals shrunk toward 50% by ``cfb_replay.shrink_total_probabilities``) and
v5.2's ``gates`` block; it is the only version in ``CHAMPION_VERSIONS``, the one that may serve Main.

Serving fails closed (``V7NoCall`` with a ``NO_CALL_*`` reason) when a team has no rating history, when a team id is
unknown, when the artifact's prior uses CFBD and no covariates were supplied, or when a completed current-season game of either team has no play-by-play row yet (the crawl is behind),
because ratings built on a partial season would silently differ from the ones the artifact was validated on.

Live inputs come from ``RuntimePaths.ncaaf_v7_root``, kept current by the daily step ``step1o_ncaaf_v7_pbp``
(``data_sources/cfb_pbp_live.py``): ``price_v7_slate(data_root, decision_time, games)`` loads them as of the
decision time (``load_v7_serving_data``) and prices a slate, one result per game. Refits keep reading the research
copies (``scripts/cfb_v7_fit.py``).
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import ndtr

from ..cfb_replay import TOTAL_SLOPE_KEY, shrink_total_probabilities, validate_total_slope
from ..config import PROJECT_ROOT
from ..data_sources import cfb_pbp_live as live
from ..domain import parse_utc
from ..features import cfb_features_v7 as core
from ..production_registry import compute_artifact_hash

CFB_V7_MODEL_VERSION = "cfb-structural-v7"
CFB_V7_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / f"{CFB_V7_MODEL_VERSION}.json"
# quick-261122-v71: v7 refit with the 2026-11-22 bug-hunt fixes. Same code, switched on by artifact fields.
CFB_V71_MODEL_VERSION = "cfb-structural-v7.1"
CFB_V71_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / f"{CFB_V71_MODEL_VERSION}.json"
# quick-261123-6q5: v7.1's artifact plus a train-fitted total shrink and v5.2's Main gates, the NCAAF champion
# candidate. Moneyline and spread price exactly as v7.1; only P(over)/P(under) move toward 50%.
CFB_V72_MODEL_VERSION = "cfb-structural-v7.2"
V7_FAMILY = (CFB_V7_MODEL_VERSION, CFB_V71_MODEL_VERSION, CFB_V72_MODEL_VERSION)
# The v7-family versions that may serve as the NCAAF champion (Main and the buyer); v7 and v7.1 never do.
CHAMPION_VERSIONS = (CFB_V72_MODEL_VERSION,)
NO_CALL_PBP_MISSING = "NO_CALL_V7_PBP_MISSING"
NO_CALL_NO_CFBD = "NO_CALL_V7_NO_CFBD_PRIOR"
NO_CALL_NO_CLASS = "NO_CALL_V7_NO_CLASS_LABEL"
NO_CALL_NO_KICKOFF = "NO_CALL_V7_NO_KICKOFF"
# v7.1 artifact switches (absent in v7's artifact = v7's behaviour).
PER_GAME_FIT = "per_game_research_decision_time"
SCORE_ONLY_TERMINAL = "terminal_without_pbp"
FBS_POPULATION = "fbs"


def load_cfb_structural_v7_artifact(
    path: str | Path | None = None, *, model_version: str = CFB_V7_MODEL_VERSION
) -> dict[str, Any]:
    """A v7-family artifact (v7 or v7.1), hash-verified; ``model_version`` must match the file's."""
    if model_version not in V7_FAMILY:
        raise ValueError(f"not a cfb v7-family model: {model_version!r}")
    default = PROJECT_ROOT / "config" / "models" / f"{model_version}.json"
    artifact_path = Path(path) if path is not None else default
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if payload.get("model_version") != model_version:
        raise ValueError(f"cfb v7 artifact model_version mismatch: {payload.get('model_version')!r}")
    if payload.get("artifact_hash") != compute_artifact_hash(payload):
        raise ValueError(f"cfb v7 artifact_hash mismatch for {artifact_path}")
    return payload


def _interval_mass(grid: np.ndarray, loc: float, sd: float) -> np.ndarray:
    edges = np.append(grid - 0.5, grid[-1] + 0.5)
    z = (edges - loc) / sd
    cdf, sf = ndtr(z), ndtr(-z)
    return np.where(z[:-1] > 0, sf[:-1] - sf[1:], cdf[1:] - cdf[:-1])


@dataclass(frozen=True)
class V7Pricing:
    mu_margin: float
    mu_total: float
    margin_sd: float
    total_sd: float
    early: bool
    is_fbs_vs_fcs: bool
    probabilities: dict[str, float]
    # v7.2 only: the pmf's over/under before the total shrink (None when the artifact sets no slope).
    raw_total: dict[str, float] | None = None


class CFBStructuralV7Model:
    """Prices one matchup from v7 rating features and the artifact's fitted stage-2 model and pmfs."""

    version: str = CFB_V7_MODEL_VERSION

    def __init__(self, artifact: Mapping[str, Any]) -> None:
        if artifact.get("artifact_hash") != compute_artifact_hash(dict(artifact)):
            raise ValueError("cfb v7 artifact_hash mismatch")
        if artifact.get("model_version") not in V7_FAMILY:
            raise ValueError(f"not a cfb v7-family artifact: {artifact.get('model_version')!r}")
        self.version = str(artifact["model_version"])
        pit = artifact["point_in_time"]
        if float(pit["result_buffer_hours"]) != core.RESULT_BUFFER_HOURS:
            raise ValueError("cfb v7 artifact result buffer differs from the rating core")
        # v7.1 (bug hunt 2026-11-22 #3): fit each game at min(run clock, its research decision time) instead of
        # the run clock, and add points-only rows only for games ESPN will never give play-by-play for.
        fit_rule = pit.get("serving_fit_rule")
        serving = artifact.get("serving") or {}
        if fit_rule not in (None, PER_GAME_FIT) or serving.get("score_only_rows") not in (
            None,
            SCORE_ONLY_TERMINAL,
        ):
            raise ValueError("cfb v7 artifact serving rule is not one this module implements")
        self.per_game_fit = fit_rule == PER_GAME_FIT
        self.score_only_terminal = serving.get("score_only_rows") == SCORE_ONLY_TERMINAL
        # v7.1 (bug hunt item 6): contracts carry a feature-capture snapshot of the model inputs.
        self.feature_capture = bool(serving.get("feature_capture"))
        pr = artifact["prior"]
        self.params = core.PriorParams(
            rho_off={k: float(v) for k, v in pr["rho_off"].items()},
            rho_def={k: float(v) for k, v in pr["rho_def"].items()},
            lam_off={k: float(v) for k, v in pr["lam_off"].items()},
            lam_def={k: float(v) for k, v in pr["lam_def"].items()},
            half_life_days=None if pr["half_life_days"] is None else float(pr["half_life_days"]),
            cov_off={k: {c: float(g) for c, g in v.items()} for k, v in (pr.get("cov_off") or {}).items()},
            cov_def={k: {c: float(g) for c, g in v.items()} for k, v in (pr.get("cov_def") or {}).items()},
        )
        self.uses_cfbd = pr["arm"] != "core"
        if self.uses_cfbd and dict(pr["cfbd_fields"]) != core.CFBD_FIELDS:
            raise ValueError("cfb v7 artifact CFBD fields differ from the rating core")
        # v7.1 (#2): covariates z-scored over, and applied to, the season's FBS teams only.
        if pr.get("covariate_population") not in (None, FBS_POPULATION):
            raise ValueError("cfb v7 artifact covariate population is not one this module implements")
        self.covariate_population = pr.get("covariate_population")
        if (
            float(pr["finals_team_lambda"]) != core.FINALS_TEAM_LAMBDA
            or float(pr["league_lambda"]) != core.LEAGUE_LAMBDA
        ):
            raise ValueError("cfb v7 artifact ridge constants differ from the rating core")
        s2 = artifact["stage2"]
        self.features = tuple(s2["features"])
        self.coef = [float(v) for v in s2["coef"]]
        self.intercept = float(s2["intercept"])
        self.margin_cal = {k: float(v) for k, v in s2["margin_calibration"].items()}
        if set(self.margin_cal) - {"intercept", "slope", "neutral_intercept"}:
            raise ValueError("cfb v7 artifact margin calibration has terms this module does not implement")
        self.total_cal = {k: float(v) for k, v in s2["total_calibration"].items()}
        md = artifact["margin_distribution"]
        self.margin_grid = np.arange(-int(md["support"]), int(md["support"]) + 1, dtype=float)
        theta = np.asarray(md["key_log_multipliers"], float)
        key_max = int(md["key_max"])
        if len(theta) != key_max:
            raise ValueError("key multipliers do not match key_max")
        absm = np.abs(self.margin_grid).astype(int)
        g = np.ones_like(self.margin_grid)
        inner = (absm >= 1) & (absm <= key_max)
        g[inner] = np.exp(theta[absm[inner] - 1])
        g[absm == 0] = 0.0
        self.key_multipliers = g
        self.recentre_iterations = int(md["recentre_iterations"])
        self.sd_coef = [float(v) for v in md["sd_coef"]]
        if md["sd_terms"] != ["const", "abs_mu/10", "(total-55)/10", "early", "fbs_vs_fcs"]:
            raise ValueError("cfb v7 artifact SD terms are not the ones this module implements")
        self.sd_scale = float(md["sd_scale"])
        self.sd_total_centre = float(md["sd_total_centre"])
        self.early_games = int(md["early_games"])
        td = artifact["total_distribution"]
        self.total_grid = np.arange(0, int(td["support"]) + 1, dtype=float)
        self.total_buckets = [(float(lo), float(hi)) for lo, hi in td["buckets"]]
        self.total_bucket_sds = [float(s) for s in td["bucket_sds"]]
        # v7.2: v7.1's totals carry no information against the close, so the artifact's train-fitted logit slope
        # shrinks P(over)/P(under) toward 50% with v5.2's helper. A champion version must set it (fail closed);
        # v7 and v7.1 carry no slope and price exactly as before.
        slope = artifact.get(TOTAL_SLOPE_KEY)
        if slope is None and self.version in CHAMPION_VERSIONS:
            raise ValueError(f"cfb {self.version} artifact must set {TOTAL_SLOPE_KEY}")
        self.total_probability_logit_slope = None if slope is None else validate_total_slope(slope)
        self.artifact_hash = str(artifact["artifact_hash"])

    # ---------------------------------------------------------------- means
    def means(self, feat: core.MatchupFeatures) -> tuple[float, float]:
        ph, pa = core.project_points(feat, self.coef, self.intercept, self.features)
        raw_m = ph - pa
        raw_t = ph + pa
        # v7 pooled one intercept (-1.62) over home and neutral games; the rating core already carries home field
        # (zero at a neutral site), so at neutral sites it leaned every margin toward ESPN's away team (bug hunt
        # 2026-11-22 #1: actual minus model +2.62 +/- 0.77 on 477 neutral games). v7.1 fits its own neutral term.
        icpt = self.margin_cal["intercept"]
        if feat.neutral and "neutral_intercept" in self.margin_cal:
            icpt = self.margin_cal["neutral_intercept"]
        return (
            icpt + self.margin_cal["slope"] * raw_m,
            self.total_cal["intercept"] + self.total_cal["slope"] * raw_t,
        )

    def covariates(
        self, cfbd_rows: Sequence[Mapping[str, Any]], fbs_teams: Iterable[str] | None
    ) -> dict[str, dict[str, float]] | None:
        """This season's standardised CFBD covariates under the artifact's population rule (None: no rows, or
        the FBS-only rule without an FBS list)."""
        if not cfbd_rows:
            return None
        if self.covariate_population == FBS_POPULATION:
            if fbs_teams is None:
                return None
            return core.standardise_covariates(cfbd_rows, population=fbs_teams) or None
        return core.standardise_covariates(cfbd_rows) or None

    def margin_sd(self, mu: float, total: float, *, early: bool, fcs: bool) -> float:
        c = self.sd_coef
        return math.exp(
            c[0]
            + c[1] * abs(mu) / self.sd_scale
            + c[2] * (total - self.sd_total_centre) / self.sd_scale
            + c[3] * float(early)
            + c[4] * float(fcs)
        )

    def total_sd(self, total: float) -> float:
        for (lo, hi), sd in zip(self.total_buckets, self.total_bucket_sds, strict=True):
            if lo <= total < hi:
                return sd
        return self.total_bucket_sds[-1]

    # ---------------------------------------------------------------- pmfs
    def margin_pmf(self, mu: float, sd: float) -> np.ndarray:
        """Key-weighted discretised normal, mean-centred (E[m] = mu) by fixed Newton steps, exactly as fitted."""
        grid, g = self.margin_grid, self.key_multipliers
        loc = float(mu)
        for _ in range(self.recentre_iterations):
            w = _interval_mass(grid, loc, sd) * g
            p = w / w.sum()
            mean = float(p @ grid)
            var = float(p @ (grid**2)) - mean**2
            loc = loc + (mu - mean) * sd**2 / var
        w = _interval_mass(grid, loc, sd) * g
        return w / w.sum()

    def total_pmf(self, mu: float, sd: float) -> np.ndarray:
        w = _interval_mass(self.total_grid, float(mu), sd)
        return w / w.sum()

    def price(
        self,
        feat: core.MatchupFeatures,
        *,
        spread_home_line: float | None = None,
        total_line: float | None = None,
    ) -> V7Pricing:
        mu, tot = self.means(feat)
        early = min(feat.home_games, feat.away_games) < self.early_games
        sd_m = self.margin_sd(mu, tot, early=early, fcs=feat.is_fbs_vs_fcs)
        sd_t = self.total_sd(tot)
        pm, pt = self.margin_pmf(mu, sd_m), self.total_pmf(tot, sd_t)
        grid = self.margin_grid
        out = {"home_win": float(pm[grid > 0].sum()), "away_win": float(pm[grid < 0].sum())}
        if spread_home_line is not None:
            thr = -float(spread_home_line)
            out.update(
                home_cover=float(pm[grid > thr].sum()),
                push_spread=float(pm[grid == thr].sum()),
                away_cover=float(pm[grid < thr].sum()),
            )
        raw_total = None
        if total_line is not None:
            tg, line = self.total_grid, float(total_line)
            out.update(
                over=float(pt[tg > line].sum()),
                push_total=float(pt[tg == line].sum()),
                under=float(pt[tg < line].sum()),
            )
            if self.total_probability_logit_slope is not None:
                raw_total = {"over": out["over"], "under": out["under"]}
                out["over"], out["under"] = shrink_total_probabilities(
                    out["over"], out["under"], out["push_total"], self.total_probability_logit_slope
                )
        return V7Pricing(mu, tot, sd_m, sd_t, early, feat.is_fbs_vs_fcs, out, raw_total)


def cfb_structural_v7_model(model_version: str = CFB_V7_MODEL_VERSION) -> CFBStructuralV7Model:
    return CFBStructuralV7Model(load_cfb_structural_v7_artifact(model_version=model_version))


def cfb_structural_v71_model() -> CFBStructuralV7Model:
    return cfb_structural_v7_model(CFB_V71_MODEL_VERSION)


def cfb_structural_v72_model() -> CFBStructuralV7Model:
    """The v7.2 champion from its own hash-verified artifact; a missing or altered file raises (fail closed)."""
    return cfb_structural_v7_model(CFB_V72_MODEL_VERSION)


def fit_decision(model: CFBStructuralV7Model, run_decision: datetime, kickoff: datetime | None) -> datetime:
    """The time the ratings are fit at: v7 uses the run clock; v7.1 uses the validated research rule,
    min(run clock, min(kickoff, 08:00 ET on the kickoff's ET date)). The observation cutoff stays the run clock:
    a row fetched after the fit time but before the run is information the run has (no leak), and dropping it
    would turn a game admitted at the fit time into a false NO_CALL_V7_PBP_MISSING."""
    if not model.per_game_fit:
        return run_decision
    if kickoff is None:
        raise core.V7NoCall(NO_CALL_NO_KICKOFF, "v7.1 fits at the game's research decision time")
    return min(run_decision, core.decision_time_for(kickoff))


# ------------------------------------------------------------------------------------------------ serving entry
def check_pbp_complete(
    rows: Sequence[core.TeamGame],
    completed_games: Mapping[str, Sequence[str]],
    team_ids: Sequence[str],
    season: int,
    decision: datetime,
) -> None:
    """Raise NO_CALL_V7_PBP_MISSING when a completed, admitted current-season game of a team has no pbp row.

    ``completed_games`` maps team id -> event ids of that team's completed current-season games from the schedule
    (the live score history); ``rows`` are the team-game rows the ratings will use."""
    have = {
        (r.team_id, r.event_id)
        for r in rows
        if r.season == season and core.admitted(r.kickoff, decision) and r.values.get("sr") is not None
    }
    for team in team_ids:
        for eid in completed_games.get(team, ()):
            if (team, eid) not in have:
                raise core.V7NoCall(NO_CALL_PBP_MISSING, f"team {team} event {eid}")


def price_game(
    model: CFBStructuralV7Model,
    rows: Sequence[core.TeamGame],
    *,
    season: int,
    home_id: str,
    away_id: str,
    neutral: bool,
    decision: datetime,
    classes: Mapping[tuple[str, int], str],
    covariates: Mapping[str, Mapping[str, float]] | None = None,
    spread_home_line: float | None = None,
    total_line: float | None = None,
) -> V7Pricing:
    """Ratings as of ``decision`` from ``rows`` (this season's admitted rows + last season's rows for the finals),
    then the artifact's pricing. ``covariates`` is ``core.standardise_covariates`` of this season's CFBD prior rows
    (required when the artifact's prior arm uses CFBD). Raises ``V7NoCall`` (fail closed).

    ``classes`` is a SEASON-LEVEL (team, season) -> FBS/FCS map from the schedule (the research labels came from
    each season's full schedule via ``core.season_classes``). It is required: derived from admitted rows alone, a
    week-0 FBS team would carry no label and silently get the FCS offset. Both teams and every team in this
    season's admitted rows must carry a label for ``season`` (NO_CALL_V7_NO_CLASS_LABEL otherwise)."""
    if decision.tzinfo is None:
        raise ValueError("decision must be timezone-aware")
    if model.uses_cfbd and not covariates:
        raise core.V7NoCall(NO_CALL_NO_CFBD, f"season {season}")
    cls = dict(classes)
    needed = {home_id, away_id} | {
        t
        for r in rows
        if r.season == season and core.admitted(r.kickoff, decision)
        for t in (r.team_id, r.opponent_id)
    }
    missing = sorted(t for t in needed if (t, season) not in cls)
    if missing:
        raise core.V7NoCall(NO_CALL_NO_CLASS, f"season {season}: {missing[:5]}")
    prev_rows = [r for r in rows if r.season == season - 1]
    finals = core.fit_finals(prev_rows, season - 1, cls) if prev_rows else None
    state = core.fit_state(rows, season, decision, cls, finals, model.params, (home_id, away_id), covariates)
    feat = core.matchup_features(state, home_id, away_id, neutral=neutral, classes=cls)
    return model.price(feat, spread_home_line=spread_home_line, total_line=total_line)


# ------------------------------------------------------------------------------------------------ live data
@dataclass(frozen=True)
class V7ServingData:
    """Everything ``price_game`` needs at one decision time, read from the runtime-root v7 store."""

    season: int
    decision: datetime
    rows: list[core.TeamGame]
    classes: dict[tuple[str, int], str]
    covariates: dict[str, dict[str, float]] | None
    expected_games: dict[str, list[str]]  # team -> admitted current-season games that must have pbp rows
    expected_kickoffs: dict[str, datetime]  # event -> kickoff for every event in expected_games
    slate_no_call: tuple[str, str] | None  # (reason, detail) when no game of the slate can be priced
    stats: dict[str, Any]


def _score_only_rows(rec: Mapping[str, Any]) -> list[core.TeamGame]:
    """Two points-only rows for a final game without play-by-play (``scripts/cfb_v7_fit.py::load_team_games``)."""
    kick = parse_utc(rec["kickoff_utc"])
    neutral = bool(rec.get("neutral_site"))
    out = []
    for team, opp, pts, home in (
        (rec["home_id"], rec["away_id"], rec["home_score"], True),
        (rec["away_id"], rec["home_id"], rec["away_score"], False),
    ):
        out.append(
            core.TeamGame(
                event_id=str(rec["event_id"]),
                season=int(rec["season"]),
                kickoff=kick,
                team_id=str(team),
                opponent_id=str(opp),
                home_sign=0 if neutral else (1 if home else -1),
                game_class=rec.get("game_class"),
                values={k: (float(pts) if k == "pts" else None) for k in core.METRICS},
            )
        )
    return out


def _live_history_completed(
    path: Path, season: int, decision: datetime
) -> list[tuple[str, str, str, datetime]]:
    """(event, home, away, kickoff) of completed, admitted ``season`` games in the live NCAAF score history (FBS-involved
    only). An independent witness: when the v7 update failed, these games still demand play-by-play."""
    out: list[tuple[str, str, str, datetime]] = []
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                r.get("status") != "completed"
                or r.get("season_year") != season
                or not r.get("event_start_utc")
            ):
                continue
            kick = parse_utc(r["event_start_utc"])
            if not core.admitted(kick, decision):
                continue
            out.append((str(r.get("event_id")), str(r.get("home_team_id")), str(r.get("away_team_id")), kick))
    return out


def load_v7_serving_data(
    data_root: str | Path,
    decision: datetime,
    *,
    team_ids: Iterable[str] = (),
    model: CFBStructuralV7Model | None = None,
) -> V7ServingData:
    """Serving inputs as of ``decision`` from ``RuntimePaths.ncaaf_v7_root`` (resolved from ``data_root`` the way
    the daily step writes it).

    Rows: pbp rows of this and last season (observed at or before ``decision``) plus points-only rows for final
    schedule games without play-by-play, which is how the fit script built its spine. Classes: the season FBS
    lists (last season's too: its finals are the prior). Covariates: this season's CFBD rows. The slate no-calls
    as a whole when a season date up to the decision was never scanned (unseen games) or a class list is
    missing; per team, every admitted current-season schedule game must have a pbp row unless ESPN has none.

    ``model`` selects the v7.1 rules: points-only rows only for games ESPN will never give play-by-play for
    (v7 also added them for games whose pbp was merely not fetched yet, so a lagging crawl fed other teams'
    games in as points only), and the artifact's covariate population. ``kickoffs`` maps every expected event
    to its kickoff so a per-game fit time can re-filter ``expected_games``."""
    if decision.tzinfo is None:
        raise ValueError("decision must be timezone-aware")
    store = live.store_for(live.v7_paths(data_root))
    season = live.cfb_season(live.et_date(decision))
    seasons = (season - 1, season)
    pbp = live.read_pbp_rows(store, seasons, as_of=decision)
    rows = [core.team_game_from_row(r) for r in pbp]
    pbp_events = {r.event_id for r in rows}
    schedule = live.read_schedule(store, as_of=decision)
    terminal = live.terminal_without_pbp(store, schedule, as_of=decision)
    score_only_terminal = model is not None and model.score_only_terminal
    score_only = 0
    for rec in schedule.values():
        if int(rec["season"]) not in seasons or rec["status"] != live.FINAL or rec["event_id"] in pbp_events:
            continue
        if score_only_terminal and rec["event_id"] not in terminal:
            continue
        if rec.get("home_score") is None or rec.get("away_score") is None:
            continue
        rows.extend(_score_only_rows(rec))
        score_only += 1
    rows.sort(key=lambda r: (r.kickoff, r.event_id, -r.home_sign))

    slate_no_call: tuple[str, str] | None = None
    fbs = {s: live.read_fbs_teams(store, s, as_of=decision) for s in seasons}
    teams_by_season: dict[int, set[str]] = defaultdict(set)
    for r in rows:
        teams_by_season[r.season].update((r.team_id, r.opponent_id))
    teams_by_season[season].update(str(t) for t in team_ids if t)
    classes: dict[tuple[str, int], str] = {}
    for s, listed in fbs.items():
        if listed is None:
            slate_no_call = slate_no_call or (NO_CALL_NO_CLASS, f"no FBS team list for season {s}")
            continue
        for t in teams_by_season[s]:
            classes[(t, s)] = core.FBS if t in listed else core.FCS
    gap = live.scan_gap(store, season, decision)
    if gap is not None:
        slate_no_call = (NO_CALL_PBP_MISSING, gap)

    expected: dict[str, set[str]] = defaultdict(set)
    kickoffs: dict[str, datetime] = {}
    for rec in schedule.values():
        if int(rec["season"]) != season or rec["status"] in live.NOT_PLAYED_STATES:
            continue
        if rec["event_id"] in terminal or not core.admitted(parse_utc(rec["kickoff_utc"]), decision):
            continue
        kickoffs[str(rec["event_id"])] = parse_utc(rec["kickoff_utc"])
        expected[str(rec["home_id"])].add(str(rec["event_id"]))
        expected[str(rec["away_id"])].add(str(rec["event_id"]))
    not_played = {e for e, r in schedule.items() if r["status"] in live.NOT_PLAYED_STATES}
    witnessed = 0
    for eid, home, away, kick in _live_history_completed(
        Path(data_root) / "processed" / "ncaaf" / "games.jsonl", season, decision
    ):
        if eid in terminal or eid in not_played:
            continue
        kickoffs.setdefault(eid, kick)
        witnessed += eid not in expected.get(home, set())
        expected[home].add(eid)
        expected[away].add(eid)

    cfbd_rows = live.read_cfbd_rows(store, season)
    if model is not None:
        covariates = model.covariates(cfbd_rows, fbs.get(season))
    else:
        covariates = core.standardise_covariates(cfbd_rows) if cfbd_rows else None
    return V7ServingData(
        season=season,
        decision=decision,
        rows=rows,
        classes=classes,
        covariates=covariates or None,
        expected_games={t: sorted(v) for t, v in expected.items()},
        expected_kickoffs=kickoffs,
        slate_no_call=slate_no_call,
        stats={
            "pbp_rows": len(pbp),
            "score_only_games": score_only,
            "schedule_events": len(schedule),
            "terminal_without_pbp": len(terminal),
            "live_history_only_games": witnessed,
            "cfbd_rows": len(cfbd_rows),
            "fbs_teams": {s: (len(v) if v is not None else None) for s, v in fbs.items()},
        },
    )


def _game_field(game: Any, name: str, default: Any = None) -> Any:
    if isinstance(game, Mapping):
        return game.get(name, default)
    return getattr(game, name, default)


def _kickoff(game: Any) -> datetime | None:
    raw = _game_field(game, "event_start_utc")
    if not raw:
        return None
    try:
        return parse_utc(str(raw))
    except (TypeError, ValueError):
        return None  # v7.1 then no-calls (NO_CALL_V7_NO_KICKOFF); v7 never reads it


def price_v7_slate(
    data_root: str | Path,
    decision_time: datetime,
    games: Sequence[Any],
    *,
    model: CFBStructuralV7Model | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """v7 prices (or a NO_CALL reason) for each game of a slate at ``decision_time``.

    ``games`` are mappings or objects with ``event_id``, ``home_team_id``, ``away_team_id`` (ESPN ids),
    ``is_neutral_site``, ``event_start_utc`` (required by v7.1) and optionally ``spread_home_line`` /
    ``total_line``. The rating state is fitted once per fit time (v7: the run clock, one fit per slate; v7.1:
    one fit per distinct research decision time); per game the arithmetic is ``price_game``'s (tested equal).
    Never raises ``V7NoCall``. A v7.1 result also carries ``inputs``, the model-input snapshot for the ledger."""
    model = model or cfb_structural_v7_model()
    team_ids = sorted(
        {str(t) for g in games for t in (_game_field(g, "home_team_id"), _game_field(g, "away_team_id")) if t}
    )
    data = load_v7_serving_data(data_root, decision_time, team_ids=team_ids, model=model)
    season, decision = data.season, data.decision
    slate_no_call = data.slate_no_call
    if slate_no_call is None and model.uses_cfbd and not data.covariates:
        slate_no_call = (NO_CALL_NO_CFBD, f"season {season}")
    finals = None
    if slate_no_call is None:
        prev_rows = [r for r in data.rows if r.season == season - 1]
        finals = core.fit_finals(prev_rows, season - 1, data.classes) if prev_rows else None
    states: dict[datetime, core.RatingState] = {}

    def state_at(fit_time: datetime) -> core.RatingState:
        if fit_time not in states:
            states[fit_time] = core.fit_state(
                data.rows, season, fit_time, data.classes, finals, model.params, team_ids, data.covariates
            )
        return states[fit_time]

    results: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for g in games:
        home = _game_field(g, "home_team_id")
        away = _game_field(g, "away_team_id")
        out: dict[str, Any] = {
            "event_id": str(_game_field(g, "event_id")),
            "home_team_id": None if home is None else str(home),
            "away_team_id": None if away is None else str(away),
            "model_version": model.version,
            "decision_utc": decision.isoformat(),
        }
        try:
            if slate_no_call is not None:
                raise core.V7NoCall(*slate_no_call)
            fit_time = fit_decision(model, decision, _kickoff(g))
            h, a = str(home), str(away)
            expected = {
                t: [e for e in evs if core.admitted(data.expected_kickoffs[e], fit_time)]
                for t, evs in data.expected_games.items()
                if t in (h, a)
            }
            check_pbp_complete(data.rows, expected, (h, a), season, fit_time)
            neutral = bool(_game_field(g, "is_neutral_site"))
            state = state_at(fit_time)
            feat = core.matchup_features(state, h, a, neutral=neutral, classes=data.classes)
            pricing = model.price(
                feat,
                spread_home_line=_game_field(g, "spread_home_line"),
                total_line=_game_field(g, "total_line"),
            )
        except core.V7NoCall as exc:
            out.update(status="no_call", reason=exc.reason, detail=str(exc))
            reasons[exc.reason] += 1
        else:
            out.update(
                status="ok",
                mu_margin=pricing.mu_margin,
                mu_total=pricing.mu_total,
                margin_sd=pricing.margin_sd,
                total_sd=pricing.total_sd,
                early=pricing.early,
                is_fbs_vs_fcs=pricing.is_fbs_vs_fcs,
                probabilities=pricing.probabilities,
                fit_decision_utc=fit_time.isoformat(),
            )
            if model.feature_capture:
                cov = data.covariates or {}
                out["inputs"] = {
                    "fit_decision_utc": fit_time.isoformat(),
                    "run_decision_utc": decision.isoformat(),
                    "neutral_site": neutral,
                    "home_class": data.classes.get((h, season)),
                    "away_class": data.classes.get((a, season)),
                    "home_games": feat.home_games,
                    "away_games": feat.away_games,
                    "home_expected": {k: round(v, 6) for k, v in feat.home.values.items()},
                    "away_expected": {k: round(v, 6) for k, v in feat.away.values.items()},
                    "home_cfbd_z": {k: round(v, 6) for k, v in cov.get(h, {}).items()},
                    "away_cfbd_z": {k: round(v, 6) for k, v in cov.get(a, {}).items()},
                }
                if model.total_probability_logit_slope is not None:
                    # v7.2: the shrink and the pmf's own over/under, so a row's total can be re-derived.
                    out["inputs"][TOTAL_SLOPE_KEY] = model.total_probability_logit_slope
                    if pricing.raw_total is not None:
                        out["inputs"]["raw_over"] = pricing.raw_total["over"]
                        out["inputs"]["raw_under"] = pricing.raw_total["under"]
        results.append(out)
    summary = {
        "model_version": model.version,
        "decision_utc": decision.isoformat(),
        "season": season,
        "games": len(results),
        "priced": sum(1 for r in results if r["status"] == "ok"),
        "no_call": dict(reasons),
        "data": data.stats,
    }
    return results, summary
