"""cfb-structural-v5.1: v5 with its documented input and selection bugs fixed. PAPER CHALLENGER, Flat only.

Operator approval 2026-11-21 ("Build v5.1 test model"): a challenger beside v6.2 and v7. v5 stays the NCAAF champion
and keeps serving Main; promotion is not approved. ``cli/forecast._forecast_cfb_sport`` sends any version outside
v2-v5 (exact match) to the research and Flat tiers only, and the auto-buyer whitelist is exact-match, so v5.1 rows
carry zero Main units and are never bought.

What changed against v5 (outputs/ncaaf/bug_hunt/models.md and pricing.md; evidence in
.planning/quick/261122-v51-ncaaf-v51/261122-v51-SUMMARY.md):

1. Conference flag (finding 3): season-keyed membership table, the same function in training and serving
   (``features/cfb_conference.py``).
2. "EPA" channel (finding 4): computed from history scores at decision time, or dropped; the stored per-row
   fields are never read (``features/cfb_features_v51.py``). Which of the two is chosen on validation and recorded
   in the artifact as ``features.epa_mode``.
3. Every fitted layer refit on the corrected features (train only): margin/total mean calibration (with an optional
   neutral-site intercept, chosen on validation), margin SD table and conference scale, total SD.
4. Margin distribution: an analytic integer margin pmf (Normal mass per point, no ties since CFB overtime, and
   optional key-number multipliers fitted on train and kept only if validated) instead of the 200k-draw NB engine,
   whose smooth margin puts 2.4% on a push at 3 or 7 against about 4-5% in history (finding 5). The NB engine
   (``cfb_distribution.py``) is untouched: its hash is stamped into every stored v5 snapshot.
5. Side selection (findings 1 and 2): the max-edge side for every market; a market is emitted only when that edge
   is above zero at ledger precision. No key-window side override, no higher-probability moneyline rule.
6. Quotes (pricing finding 3): a captured executable Polymarket book observed at or before the decision only; never
   ESPN consensus or the indicative event list.
7. Rows carry v5.1's own model id, artifact hash, code revision, rationale label and a feature-capture snapshot of
   every model input (finding 8; v6.2 finding 2).
8. History cut at the decision time, not 09:00Z of the game day (finding 9); postseason games use week 15 in
   training as in serving (finding 7).

Every number above lives in ``config/models/cfb-structural-v5.1.json`` (hash-verified, with the conference table's
sha256); nothing is hardcoded here. Fails closed with ``NO_CALL_V51_*`` reason codes.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import ndtr

from ..config import PROJECT_ROOT
from ..domain import parse_utc
from ..features.cfb_conference import ConferenceMembership
from ..features.cfb_features import CFBMatchupFeatures, _keyed_past_rows
from ..features.cfb_features_v51 import CFBFeatureExtractorV51
from ..features.cfb_team_identity import UNKNOWN, CFBTeamClasses, row_start
from ..production_registry import compute_artifact_hash

CFB_V51_MODEL_VERSION = "cfb-structural-v5.1"
CFB_V51_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / f"{CFB_V51_MODEL_VERSION}.json"
CAPTURED_BOOK = "polymarket_us"
MARGIN_SLICES = ("established", "thin_sample", "fbs_vs_fcs")
ROUTABLE_MARKETS = ("moneyline", "spread", "total")

NO_CALL_CONFERENCE_SEASON_MISSING = "NO_CALL_V51_CONFERENCE_SEASON_MISSING"
NO_CALL_CONFERENCE_TEAM_UNRESOLVED = "NO_CALL_V51_CONFERENCE_TEAM_UNRESOLVED"
NO_CALL_UNKNOWN_CLASS = "NO_CALL_V51_UNKNOWN_CLASS"
NO_CALL_HISTORY_STALE = "NO_CALL_V51_HISTORY_STALE"
NO_CALL_NO_SCHEDULE = "NO_CALL_V51_NO_SCHEDULE_CONTEXT"
NO_CALL_FEATURE_ERROR = "NO_CALL_V51_FEATURE_ERROR"
NO_CALL_STARTED = "NO_CALL_V51_EVENT_STARTED"

_HERE = Path(__file__).resolve()
_V51_SERVING_SOURCES = (
    _HERE,
    _HERE.parent / "cfb_structural_v62.py",  # EventQuotes, select_max_edge
    _HERE.parents[1] / "features" / "cfb_features_v51.py",
    _HERE.parents[1] / "features" / "cfb_conference.py",
    _HERE.parents[1] / "features" / "cfb_features.py",
    _HERE.parents[1] / "features" / "cfb_team_identity.py",
    _HERE.parents[1] / "features" / "cfb_features_v6.py",  # TeamResolver
    _HERE.parents[1] / "data_sources" / "cfb_data.py",  # static table: tiers, geography
)


def v51_serving_code_hash() -> str:
    """16-hex digest of every source file that computes a v5.1 row (never v5's or v6.2's revision)."""
    digest = hashlib.sha256()
    for path in _V51_SERVING_SOURCES:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


# --------------------------------------------------------------------------------------------- parameters
@dataclass(frozen=True)
class V51Params:
    """Every fitted or carried number v5.1 prices with; built only from the artifact (or the fit harness)."""

    margin_intercept: float
    margin_slope: float
    margin_neutral: float  # added to the calibrated home margin at neutral sites (0.0 when not selected)
    total_intercept: float
    total_slope: float
    margin_sd_table: Mapping[str, float]
    established_min_sample: int
    conference_margin_scale: float
    total_sd: float
    blowout_spread_threshold: float
    blowout_margin_scale: float
    blowout_cap_points: float
    total_deviation_threshold: float
    total_deviation_scale: float
    total_deviation_cap_points: float
    total_floor: float
    score_floor: float
    key_multipliers: Mapping[
        int, float
    ]  # |margin| -> multiplier on the Normal point mass; 0 -> 0.0 (no ties)
    max_margin: int

    @classmethod
    def from_mapping(cls, p: Mapping[str, Any]) -> V51Params:
        mc, tc = p["margin_calibration"], p["total_calibration"]
        table = {k: float(p["margin_sd_table"][k]) for k in MARGIN_SLICES}
        keys = {int(k): float(v) for k, v in dict(p["margin_pmf"]["key_multipliers"]).items()}
        params = cls(
            margin_intercept=float(mc["intercept"]),
            margin_slope=float(mc["slope"]),
            margin_neutral=float(mc.get("neutral_intercept", 0.0)),
            total_intercept=float(tc["intercept"]),
            total_slope=float(tc["slope"]),
            margin_sd_table=table,
            established_min_sample=int(p["established_min_sample"]),
            conference_margin_scale=float(p["conference_margin_scale"]),
            total_sd=float(p["total_sd"]),
            blowout_spread_threshold=float(p["sd_widening"]["blowout_spread_threshold"]),
            blowout_margin_scale=float(p["sd_widening"]["blowout_margin_scale"]),
            blowout_cap_points=float(p["sd_widening"]["blowout_cap_points"]),
            total_deviation_threshold=float(p["sd_widening"]["total_deviation_threshold"]),
            total_deviation_scale=float(p["sd_widening"]["total_deviation_scale"]),
            total_deviation_cap_points=float(p["sd_widening"]["total_deviation_cap_points"]),
            total_floor=float(p["floors"]["total_points"]),
            score_floor=float(p["floors"]["team_points"]),
            key_multipliers=keys,
            max_margin=int(p["margin_pmf"]["max_margin"]),
        )
        params.validate()
        return params

    def validate(self) -> None:
        if self.margin_slope <= 0 or self.total_slope <= 0:
            raise ValueError("cfb v5.1 calibration slopes must be positive")
        if (
            any(not 5.0 <= v <= 40.0 for v in self.margin_sd_table.values())
            or not 5.0 <= self.total_sd <= 40.0
        ):
            raise ValueError("cfb v5.1 SDs outside a sane range")
        if not 0.5 <= self.conference_margin_scale <= 1.5:
            raise ValueError("cfb v5.1 conference_margin_scale outside a sane range")
        if self.key_multipliers.get(0, 1.0) != 0.0:
            raise ValueError("cfb v5.1 margin pmf must put zero mass on a tie (CFB overtime)")
        if any(v < 0 or not math.isfinite(v) for v in self.key_multipliers.values()) or self.max_margin < 60:
            raise ValueError("cfb v5.1 margin pmf parameters invalid")


@dataclass(frozen=True)
class V51Pricing:
    mu_margin: float
    mu_total: float
    margin_sd: float
    total_sd: float
    margin_slice: str
    spread_home_line: float
    total_line: float
    probabilities: Mapping[str, float]


def margin_slice(params: V51Params, *, is_fbs_vs_fcs: bool, sample_games: int) -> str:
    if is_fbs_vs_fcs:
        return "fbs_vs_fcs"
    return "established" if sample_games >= params.established_min_sample else "thin_sample"


def margin_sd(
    params: V51Params, *, spread_home_line: float, is_fbs_vs_fcs: bool, sample_games: int, is_conference: bool
) -> float:
    name = margin_slice(params, is_fbs_vs_fcs=is_fbs_vs_fcs, sample_games=sample_games)
    sd = params.margin_sd_table[name]
    # The conference scale is fitted on established matchups only (as in v5).
    if is_conference and name == "established":
        sd *= params.conference_margin_scale
    excess = abs(spread_home_line) - params.blowout_spread_threshold
    if excess > 0:
        sd *= 1.0 + params.blowout_margin_scale * min(params.blowout_cap_points, excess)
    return sd


def total_sd(params: V51Params, *, mu_total: float, total_line: float) -> float:
    sd = params.total_sd
    excess = abs(mu_total - total_line) - params.total_deviation_threshold
    if excess > 0:
        sd *= 1.0 + params.total_deviation_scale * min(params.total_deviation_cap_points, excess)
    return sd


def calibrated_means(
    params: V51Params, *, raw_margin: float, raw_total: float, is_neutral_site: bool
) -> tuple[float, float]:
    """(margin, total) after mean calibration and v5's score floors (each team at least ``score_floor``)."""
    margin = params.margin_intercept + params.margin_slope * raw_margin
    if is_neutral_site:
        margin += params.margin_neutral
    total = max(params.total_floor, params.total_intercept + params.total_slope * raw_total)
    mu_home = max(params.score_floor, (total + margin) / 2.0)
    mu_away = max(params.score_floor, (total - margin) / 2.0)
    return mu_home - mu_away, mu_home + mu_away


def margin_pmf(params: V51Params, mu: float, sd: float) -> np.ndarray:
    """P(home margin = k) for k = -max_margin..max_margin: Normal mass on [k-0.5, k+0.5] times the |k| multiplier.

    The end points absorb the tails; a tie gets multiplier 0 (overtime) and the rest is renormalised.
    """
    k = np.arange(-params.max_margin, params.max_margin + 1, dtype=float)
    upper = ndtr((k + 0.5 - mu) / sd)
    lower = ndtr((k - 0.5 - mu) / sd)
    upper[-1], lower[0] = 1.0, 0.0
    mass = upper - lower
    mult = np.ones_like(mass)
    for absk, m in params.key_multipliers.items():
        for idx in {params.max_margin + absk, params.max_margin - absk}:
            mult[idx] = m
    mass = mass * mult
    return mass / mass.sum()


def market_probabilities(
    params: V51Params,
    *,
    mu_margin: float,
    sd_margin: float,
    mu_total: float,
    sd_total: float,
    spread_home_line: float,
    total_line: float,
) -> dict[str, float]:
    pmf = margin_pmf(params, mu_margin, sd_margin)
    k = np.arange(-params.max_margin, params.max_margin + 1)
    p_home_win = float(pmf[k > 0].sum())
    implied = -float(spread_home_line)  # home covers when margin > -home line
    p_home_cover = float(pmf[k > implied].sum())
    p_away_cover = float(pmf[k < implied].sum())
    p_push = float(pmf[k == implied].sum()) if float(implied).is_integer() else 0.0
    if float(total_line).is_integer():
        p_under = float(ndtr((total_line - 0.5 - mu_total) / sd_total))
        p_over = 1.0 - float(ndtr((total_line + 0.5 - mu_total) / sd_total))
        p_push_total = max(0.0, 1.0 - p_over - p_under)
    else:
        p_under = float(ndtr((total_line - mu_total) / sd_total))
        p_over = 1.0 - p_under
        p_push_total = 0.0
    return {
        "home_win": p_home_win,
        "away_win": 1.0 - p_home_win,
        "home_cover": p_home_cover,
        "away_cover": p_away_cover,
        "spread_push": p_push,
        "over": p_over,
        "under": p_under,
        "total_push": p_push_total,
    }


def price_features(
    params: V51Params,
    features: Mapping[str, Any],
    *,
    spread_home_line: float | None = None,
    total_line: float | None = None,
) -> V51Pricing:
    """The whole v5.1 pricing arithmetic from a feature dict; the fit harness and serving both call this.

    A missing line is projected from the RAW projection, as v5's ``project_missing_lines`` does: the spread line
    only enters a moneyline through blowout SD widening, the total line only through deviation widening.
    """
    raw_margin = float(features["projected_margin_home"])
    raw_total = float(features["projected_total"])
    mu_margin, mu_total = calibrated_means(
        params, raw_margin=raw_margin, raw_total=raw_total, is_neutral_site=bool(features["is_neutral_site"])
    )
    line = float(spread_home_line) if spread_home_line is not None else round(-raw_margin * 2.0) / 2.0
    tline = float(total_line) if total_line is not None else raw_total
    m_sd = margin_sd(
        params,
        spread_home_line=line,
        is_fbs_vs_fcs=bool(features["is_fbs_vs_fcs"]),
        sample_games=int(features["sample_games"]),
        is_conference=bool(features["is_conference_matchup"]),
    )
    t_sd = total_sd(params, mu_total=mu_total, total_line=tline)
    probs = market_probabilities(
        params,
        mu_margin=mu_margin,
        sd_margin=m_sd,
        mu_total=mu_total,
        sd_total=t_sd,
        spread_home_line=line,
        total_line=tline,
    )
    return V51Pricing(
        mu_margin=mu_margin,
        mu_total=mu_total,
        margin_sd=m_sd,
        total_sd=t_sd,
        margin_slice=margin_slice(
            params, is_fbs_vs_fcs=bool(features["is_fbs_vs_fcs"]), sample_games=int(features["sample_games"])
        ),
        spread_home_line=line,
        total_line=tline,
        probabilities=probs,
    )


# --------------------------------------------------------------------------------------------- artifact / model
def load_cfb_structural_v51_artifact(path: str | Path | None = None) -> dict[str, Any]:
    artifact_path = Path(path) if path is not None else CFB_V51_ARTIFACT_PATH
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if payload.get("model_version") != CFB_V51_MODEL_VERSION:
        raise ValueError(f"cfb v5.1 artifact model_version mismatch: {payload.get('model_version')!r}")
    if payload.get("artifact_hash") != compute_artifact_hash(payload):
        raise ValueError(f"cfb v5.1 artifact_hash mismatch for {artifact_path}")
    return payload


class CFBStructuralV51Model:
    version: str = CFB_V51_MODEL_VERSION

    def __init__(
        self,
        params: V51Params,
        membership: ConferenceMembership,
        *,
        epa_mode: str,
        home_advantage_points: float,
        max_history_staleness_days: float,
        artifact_hash: str = "",
    ) -> None:
        self.params = params
        self.membership = membership
        self.epa_mode = epa_mode
        self.extractor = CFBFeatureExtractorV51(
            membership, epa_mode=epa_mode, home_advantage_points=home_advantage_points
        )
        self.max_history_staleness_days = float(max_history_staleness_days)
        self.artifact_hash = artifact_hash

    @classmethod
    def from_artifact(
        cls, artifact: Mapping[str, Any], *, conference_table: str | Path | None = None
    ) -> CFBStructuralV51Model:
        source = artifact["conference_source"]
        membership = ConferenceMembership.load(
            conference_table if conference_table is not None else PROJECT_ROOT / str(source["path"]),
            expected_sha256=str(source["sha256"]),
        )
        return cls(
            V51Params.from_mapping(artifact["parameters"]),
            membership,
            epa_mode=str(artifact["features"]["epa_mode"]),
            home_advantage_points=float(artifact["features"]["home_advantage_points"]),
            max_history_staleness_days=float(artifact["serving"]["max_history_staleness_days"]),
            artifact_hash=str(artifact["artifact_hash"]),
        )

    def extract(self, history: Sequence[Any], game: Any) -> CFBMatchupFeatures:
        """v5.1 features of an ``UpcomingCFBGame``-shaped game (history is cut at kickoff inside)."""
        return self.extractor.extract_features(
            history=history,
            away_team=game.away_team,
            home_team=game.home_team,
            event_id=str(game.event_id),
            game_start_utc=game.event_start_utc,
            season_year=game.season_year,
            week=int(game.week),
            wind_mph=getattr(game, "wind_mph", None),
            temperature_f=getattr(game, "temperature_f", None),
            precipitation_in=getattr(game, "precipitation_in", None),
            is_neutral_site=bool(game.is_neutral_site),
            qb_starter_prob_away=float(getattr(game, "qb_starter_prob_away", 1.0)),
            qb_starter_prob_home=float(getattr(game, "qb_starter_prob_home", 1.0)),
            away_team_id=getattr(game, "away_team_id", None),
            home_team_id=getattr(game, "home_team_id", None),
        )

    def feature_dict(self, features: CFBMatchupFeatures) -> dict[str, Any]:
        """Full-precision inputs of the pricing arithmetic plus the recorded v5 feature dict."""
        return {
            **features.to_dict(),
            "projected_margin_home": float(features.projected_margin_home),
            "projected_total": float(features.projected_total),
            "is_neutral_site": bool(features.is_neutral_site),
            "away_epa_net": round(features.away_epa_net, 6),
            "home_epa_net": round(features.home_epa_net, 6),
        }


def training_features(
    extractor: Any, past: Sequence[Any], record: Any, *, season: int, week: int, neutral: bool
) -> CFBMatchupFeatures:
    """Features of a completed history game exactly as the fit harness computes them (scripts/cfb_v51_research.py).

    ``past`` is the history strictly before kickoff. History rows carry no verified weather or QB availability,
    so those take the extractor's defaults, which is what serving passes when it has none.
    """
    return extractor.extract_features(
        history=past,
        away_team=record.away_team,
        home_team=record.home_team,
        event_id=str(record.event_id),
        game_start_utc=record.event_start_utc,
        season_year=season,
        week=week,
        is_neutral_site=neutral,
        away_team_id=record.away_team_id,
        home_team_id=record.home_team_id,
    )


def cfb_structural_v51_model() -> CFBStructuralV51Model:
    return CFBStructuralV51Model.from_artifact(load_cfb_structural_v51_artifact())


# --------------------------------------------------------------------------------------------- serving
def _quote_time(q: Mapping[str, Any] | None, decision_time: datetime) -> str | None:
    """The captured book's own observation time when it may price a decision at ``decision_time``, else None."""
    if q is None or str(q.get("sportsbook")) != CAPTURED_BOOK:
        return None
    raw = (q.get("lineage") or {}).get("market_quote_observed_at_utc")
    if raw is None:
        return None
    try:
        seen = parse_utc(str(raw))
    except (TypeError, ValueError):
        return None
    return str(raw) if seen <= decision_time else None


@dataclass(frozen=True)
class SlateHistory:
    """What every no-call check reads, built ONCE per slate.

    Serving history is cut strictly before the decision and every priced game kicks off after it, so the rows
    before each kickoff are the same rows: one identity/class pass serves the whole slate (it was one pass per
    event, about a third of a second each on the 2026-11-21 slate).
    """

    identity: Any
    classes: CFBTeamClasses
    newest: datetime | None
    recent_seasons: frozenset[int]


def slate_history(history: Sequence[Any], decision_time: datetime) -> SlateHistory:
    identity, keyed = _keyed_past_rows(history, decision_time)
    starts = [d for g, _h, _a in keyed if (d := row_start(g)) is not None]
    # history is kickoff-ordered: the tail holds the newest rows
    recent = frozenset(d.year - 1 if d.month in (1, 2) else d.year for d in starts[-500:])
    return SlateHistory(
        identity, CFBTeamClasses.from_keyed_rows(keyed), max(starts) if starts else None, recent
    )


def serving_no_call(
    model: CFBStructuralV51Model, context: SlateHistory, game: Any, decision_time: datetime
) -> str | None:
    """Fail-closed reason for a game v5.1 must not price, else None."""
    if parse_utc(game.event_start_utc) <= decision_time:
        return NO_CALL_STARTED
    season = int(game.season_year)
    res = model.membership.is_conference_matchup(
        season,
        home_name=game.home_team,
        away_name=game.away_team,
        home_team_id=getattr(game, "home_team_id", None),
        away_team_id=getattr(game, "away_team_id", None),
    )
    if res.reason == "season_missing":
        return NO_CALL_CONFERENCE_SEASON_MISSING
    if not res.resolved:
        return NO_CALL_CONFERENCE_TEAM_UNRESOLVED
    for name, tid in ((game.away_team, game.away_team_id), (game.home_team, game.home_team_id)):
        label, _ = context.classes.classify(context.identity.key(name, str(tid) if tid else None), season)
        if label == UNKNOWN:
            return NO_CALL_UNKNOWN_CLASS
    # A frozen history (ingest stopped) prices November teams on September form: decline in season. Week 0/1
    # (no current-season rows yet) is exempt: the last row is legitimately last January's.
    if (
        season in context.recent_seasons
        and context.newest is not None
        and decision_time - context.newest > timedelta(days=model.max_history_staleness_days)
    ):
        return NO_CALL_HISTORY_STALE
    return None


def build_contracts(
    model: CFBStructuralV51Model,
    quotes: Any,
    features: Mapping[str, Any],
    conference: Mapping[str, Any],
    *,
    decision_time: datetime,
    markets: Sequence[str],
    uncertainty: float,
) -> list[dict[str, Any]]:
    """Max-edge v5.1 contracts against the captured books; a market is emitted only when its edge > 0."""
    from .cfb_structural_v62 import select_max_edge

    code_revision = v51_serving_code_hash()
    spread_q = quotes.spread if _quote_time(quotes.spread, decision_time) else None
    total_q = quotes.total if _quote_time(quotes.total, decision_time) else None
    spread_line = None if spread_q is None else float(spread_q["home_line"])
    total_line = None if total_q is None else float(total_q["line"])
    pricing = price_features(model.params, features, spread_home_line=spread_line, total_line=total_line)
    pr = pricing.probabilities
    basis: dict[str, Any] = {
        **features,
        "challenger_of": "cfb-structural-v5",
        "conference": dict(conference),
        "epa_mode": model.epa_mode,
        "calibrated_margin_home": round(pricing.mu_margin, 6),
        "calibrated_total": round(pricing.mu_total, 6),
        "effective_margin_sd": round(pricing.margin_sd, 6),
        "effective_total_sd": round(pricing.total_sd, 6),
        "margin_slice": pricing.margin_slice,
        "pricing_spread_home_line": pricing.spread_home_line,
        "pricing_total_line": pricing.total_line,
        "probabilities": {k: round(v, 6) for k, v in pr.items()},
        "side_selection": "max_edge",
        "decision_time_utc": decision_time.isoformat(),
        "quote_provenance": "polymarket_executable_ask",
    }
    contracts: list[dict[str, Any]] = []

    def emit(
        market: str, pick: tuple[str, float, float, float], line: float | None, slug: str, q: Any
    ) -> None:
        selection, prob, ask, edge = pick
        observed = _quote_time(q, decision_time)
        # Ledger precision: an edge of 1e-12 would log as a 0.0000-edge pick (v6.2, 7af9d47d). A slug without a
        # hyphen names no contract and could never settle.
        if round(edge, 4) <= 0 or observed is None or "-" not in slug:
            return
        contracts.append(
            {
                "event_id": quotes.event_id,
                "event_start_utc": quotes.event_start_utc,
                "away_team": quotes.away_team,
                "home_team": quotes.home_team,
                "market_type": market,
                "selection": selection,
                "line": line,
                "model_probability": round(prob, 6),
                "model_uncertainty": round(float(uncertainty), 4),
                "weather_inputs_verified": None,
                "model_version": CFB_V51_MODEL_VERSION,
                "model_artifact_hash": model.artifact_hash,
                "feature_basis": basis,
                "inference_inputs": None,
                # forecast._forecast_cfb_sport writes a feature-capture snapshot of feature_basis for these rows.
                "snapshot_mode": "feature_capture",
                # No parenthesised token: settlement reads the first hyphenated one as the slug.
                "rationale": (
                    f"CFB Structural v5.1 challenger: margin {pricing.mu_margin:+.2f} (SD {pricing.margin_sd:.2f}), "
                    f"total {pricing.mu_total:.2f} (SD {pricing.total_sd:.2f}). Max-edge side {selection} at "
                    f"{100 * prob:.1f}% vs ask {100 * ask:.1f}%. market_slug={slug}."
                ),
                "market_slug": slug,
                "executable_ask": float(ask),
                "observed_at_utc": observed,
                "timestamp_valid": True,
                "code_revision": code_revision,
                "edge_vs_executable_ask": round(edge, 6),
                "sportsbook": CAPTURED_BOOK,
                "market_lineage": q.get("lineage") or {},
            }
        )

    if "moneyline" in markets and _quote_time(quotes.moneyline, decision_time):
        q = quotes.moneyline
        pick = select_max_edge(
            [("home", pr["home_win"], float(q["home_ask"])), ("away", pr["away_win"], float(q["away_ask"]))]
        )
        if pick is not None:
            emit("moneyline", pick, None, str(q["slug_base"]), q)
    if "spread" in markets and spread_q is not None and spread_line is not None:
        pick = select_max_edge(
            [
                ("home", pr["home_cover"], float(spread_q["home_ask"])),
                ("away", pr["away_cover"], float(spread_q["away_ask"])),
            ]
        )
        if pick is not None:
            emit(
                "spread",
                pick,
                spread_line if pick[0] == "home" else -spread_line,
                str(spread_q["slug"]),
                spread_q,
            )
    if "total" in markets and total_q is not None and total_line is not None:
        pick = select_max_edge(
            [
                ("over", pr["over"], float(total_q["over_ask"])),
                ("under", pr["under"], float(total_q["under_ask"])),
            ]
        )
        if pick is not None:
            emit("total", pick, total_line, str(total_q["slug"]), total_q)
    return contracts


def price_challenger_slate(
    data_root: str | Path,
    decision_time: datetime,
    events: Sequence[Any],
    *,
    games: Mapping[str, Any],
    markets: Sequence[str],
    expected_artifact_hash: str | None = None,
    model: CFBStructuralV51Model | None = None,
    history: Sequence[Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """v5.1 contracts for one slate plus a summary; declines per game with ``NO_CALL_V51_*`` codes.

    ``events`` are the per-event captured quotes build_cfb_slate hands every challenger; ``games`` maps event id
    to the champion loop's ``UpcomingCFBGame`` (season, model week, neutral site, ESPN team ids, weather, QB).
    History is read up to the decision time (models.md finding 9: v5 cuts at 09:00Z of the game day).
    """
    from .cfb_structural_v62 import EventQuotes
    from .college_football import _load_cfb_history

    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    routed = [m for m in markets if m in ROUTABLE_MARKETS]
    slate = [ev if isinstance(ev, EventQuotes) else EventQuotes(**ev) for ev in events]
    summary: dict[str, Any] = {
        "model_version": CFB_V51_MODEL_VERSION,
        "markets": routed,
        "events": len(slate),
    }
    if not routed or not slate:
        summary["status"] = "not_routed" if not routed else "no_events"
        return [], summary
    model = model or cfb_structural_v51_model()
    if expected_artifact_hash is not None and model.artifact_hash != expected_artifact_hash:
        raise ValueError(
            f"cfb v5.1 model artifact {model.artifact_hash[:12]} is not the registered {expected_artifact_hash[:12]}"
        )
    if history is None:
        history = _load_cfb_history(Path(data_root), cutoff_dt=decision_time)
    else:
        history = [g for g in history if (d := row_start(g)) is not None and d < decision_time]
    no_calls: Counter[str] = Counter()
    contracts: list[dict[str, Any]] = []
    priced = 0
    context = slate_history(history, decision_time)
    for ev in slate:
        ev_markets = [m for m in routed if _quote_time(getattr(ev, m), decision_time) is not None]
        if not ev_markets:
            no_calls["no_captured_quote"] += 1
            continue
        game = games.get(ev.event_id)
        if game is None:
            no_calls[NO_CALL_NO_SCHEDULE] += 1
            continue
        # The quoted line is the market's; the game record keeps v5's effective lines out of v5.1's pricing.
        game = replace(game, spread_home_line=None, spread_away_line=None, total_line=None)
        reason = serving_no_call(model, context, game, decision_time)
        if reason is not None:
            no_calls[reason] += 1
            continue
        try:
            feats = model.extract(history, game)
        except (KeyError, TypeError, ValueError):
            no_calls[NO_CALL_FEATURE_ERROR] += 1
            continue
        conference = model.membership.is_conference_matchup(
            int(game.season_year),
            home_name=game.home_team,
            away_name=game.away_team,
            home_team_id=game.home_team_id,
            away_team_id=game.away_team_id,
        )
        priced += 1
        contracts.extend(
            build_contracts(
                model,
                ev,
                {**model.feature_dict(feats), "season": int(game.season_year), "model_week": int(game.week)},
                conference.to_dict(),
                decision_time=decision_time,
                markets=ev_markets,
                uncertainty=feats.uncertainty,
            )
        )
    summary.update(
        status="ok",
        priced=priced,
        no_calls=dict(no_calls),
        contracts=len(contracts),
        history_games=len(history),
    )
    return contracts, summary
