"""College Football Structural v6.2 challenger (cfb-structural-v6.2). Flat/research tiers only, never Main.

v6.2 = frozen v6 ratings and calibration (``cfb_structural_v6.py``, not refit) plus:

* a blend of the v6 mean with a TIMESTAMPED sportsbook anchor, ``fair = w * anchor + (1 - w) * v6`` on margin and
  total, w fitted on the train window (``scripts/cfb_v62_fit.py``). The anchor is the DraftKings line from the
  forward ESPN pregame capture (``data_sources/espn_cfb_pregame_capture.py``) as of the decision time: a record
  counts only if its own ``observed_at_utc`` is at or before the decision and before kickoff, latest wins. ESPN's
  untimestamped consensus is never an anchor. With no timestamped anchor the row is priced model-only (v6 means,
  v6 slice SDs) and ``feature_basis.anchor_status`` says so;
* a discrete margin pmf with fitted key-number mass (3, 7, 10, ...) and an SD that depends on the spread and total,
  and a discrete total pmf with a per-total-bucket SD. Moneyline, spread cover and total all come from those pmfs,
  so they are coherent; margin 0 has no mass (no ties exist in the data), and the pmf is symmetric about its
  location, so a pick'em is an exact 50/50 with no home bias at neutral sites;
* max-edge side selection in every market: the side whose fair probability minus the Polymarket ask is larger.

Everything numeric comes from the hash-verified artifact ``config/models/cfb-structural-v6.2.json``. The pmfs are
analytic (no RNG), so v6.2 never touches the shared simulation stream v5 draws from. Promotion would be a separate
governed decision (``model_promotion promote``).
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import ndtr

from ..config import PROJECT_ROOT
from ..domain import parse_utc
from ..production_registry import compute_artifact_hash

_logger = logging.getLogger(__name__)

CFB_V62_MODEL_VERSION = "cfb-structural-v6.2"
CFB_V62_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / f"{CFB_V62_MODEL_VERSION}.json"
ANCHOR_TIMESTAMPED = "timestamped_draftkings"
ANCHOR_MISSING = "model_only_no_timestamped_anchor"
_DRAFTKINGS_NAMES = frozenset({"draftkings"})
_POLYMARKET_PROVENANCE = {
    "polymarket_us": "polymarket_executable_ask",
    "polymarket_us_indicative": "polymarket_indicative_slate",
}


def load_cfb_structural_v62_artifact(path: str | Path | None = None) -> dict[str, Any]:
    artifact_path = Path(path) if path is not None else CFB_V62_ARTIFACT_PATH
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if payload.get("model_version") != CFB_V62_MODEL_VERSION:
        raise ValueError(f"cfb v6.2 artifact model_version mismatch: {payload.get('model_version')!r}")
    if payload.get("artifact_hash") != compute_artifact_hash(payload):
        raise ValueError(f"cfb v6.2 artifact_hash mismatch for {artifact_path}")
    return payload


# ------------------------------------------------------------------------------------------------ anchor
@dataclass(frozen=True)
class Anchor:
    """One timestamped sportsbook line: implied home margin (= -home spread) and total, as published then."""

    event_id: str
    margin_home: float
    total: float
    spread_home_line: float
    observed_at_utc: str
    provider: str
    source_kind: str  # "core_odds" | "scoreboard"
    record_hash: str | None


def _float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _anchor_candidates(rec: Mapping[str, Any]) -> list[Anchor]:
    eid = str(rec.get("event_id") or "")
    out: list[Anchor] = []
    core = rec.get("core_odds") or {}
    core_obs = core.get("observed_at_utc")
    for prov in core.get("providers") or []:
        if prov.get("is_live") or str(prov.get("provider_name") or "").casefold() not in _DRAFTKINGS_NAMES:
            continue
        spread = _float(prov.get("home_spread_current"))
        if spread is None:
            spread = _float(prov.get("home_spread_line"))
        total = _float(prov.get("total_current"))
        if total is None:
            total = _float(prov.get("over_under"))
        if spread is None or total is None or not core_obs:
            continue
        out.append(
            Anchor(
                eid, -spread, total, spread, str(core_obs), "DraftKings", "core_odds", rec.get("record_hash")
            )
        )
    board = rec.get("scoreboard") or {}
    line = board.get("line") or {}
    if str(line.get("provider_name") or "").casefold() in _DRAFTKINGS_NAMES and board.get("observed_at_utc"):
        spread, total = _float(line.get("home_spread_line")), _float(line.get("over_under"))
        if spread is not None and total is not None:
            out.append(
                Anchor(
                    eid,
                    -spread,
                    total,
                    spread,
                    str(board["observed_at_utc"]),
                    "DraftKings",
                    "scoreboard",
                    rec.get("record_hash"),
                )
            )
    return out


def load_anchor_index(path: str | Path, decision_time: datetime) -> dict[str, Anchor]:
    """Latest DraftKings anchor per ESPN event id observed at or before ``decision_time`` and before kickoff.

    The capture file is append-only and ingest-ordered, so "latest" is a max-scan over every record, never the last
    line. A record whose own observed_at is after the decision, or at/after the event start, is invisible.
    """
    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    capture = Path(path)
    best: dict[str, tuple[datetime, Anchor]] = {}
    if not capture.is_file():
        return {}
    with capture.open(encoding="utf-8") as f:
        for raw in f:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
                start = parse_utc(str(rec["event_start_utc"]))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            for cand in _anchor_candidates(rec):
                try:
                    seen = parse_utc(cand.observed_at_utc)
                except (TypeError, ValueError):
                    continue
                if seen > decision_time or seen >= start:
                    continue
                prior = best.get(cand.event_id)
                if prior is None or seen > prior[0]:
                    best[cand.event_id] = (seen, cand)
    return {eid: anchor for eid, (_, anchor) in best.items()}


# ------------------------------------------------------------------------------------------------ pmfs
def _interval_mass(grid: np.ndarray, loc: float, sd: float) -> np.ndarray:
    edges = np.append(grid - 0.5, grid[-1] + 0.5)
    z = (edges - loc) / sd
    cdf, sf = ndtr(z), ndtr(-z)
    return np.where(z[:-1] > 0, sf[:-1] - sf[1:], cdf[1:] - cdf[:-1])


@dataclass(frozen=True)
class V62Pricing:
    mu_margin: float
    mu_total: float
    margin_sd: float
    total_sd: float
    anchor_status: str
    probabilities: dict[str, float]


class CFBStructuralV62Model:
    """Prices one game from v6 means, an optional timestamped anchor and the artifact's fitted pmfs."""

    version: str = CFB_V62_MODEL_VERSION

    def __init__(self, artifact: Mapping[str, Any], v6_artifact: Mapping[str, Any]) -> None:
        if artifact.get("artifact_hash") != compute_artifact_hash(dict(artifact)):
            raise ValueError("cfb v6.2 artifact_hash mismatch")
        if artifact.get("base_artifact_hash") != v6_artifact.get("artifact_hash"):
            raise ValueError("cfb v6.2 artifact was built on a different frozen v6 artifact")
        blend = artifact["blend"]
        self.w_margin = float(blend["w_margin"])
        self.w_total = float(blend["w_total"])
        if not (0.0 <= self.w_margin <= 1.0 and 0.0 <= self.w_total <= 1.0):
            raise ValueError("blend weights must lie in [0, 1]")
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
        self.sd_params = tuple(float(v) for v in md["sd_params"])
        self.sd_total_centre = float(md["sd_total_centre"])
        self.sd_scale = float(md["sd_scale"])
        td = artifact["total_distribution"]
        self.total_grid = np.arange(0, int(td["support"]) + 1, dtype=float)
        self.total_buckets = [(float(lo), float(hi)) for lo, hi in td["buckets"]]
        self.total_bucket_sds = [float(s) for s in td["bucket_sds"]]
        self.v6_margin_calibration = {
            k: float(v6_artifact["margin_calibration"][k]) for k in ("intercept", "slope")
        }
        self.v6_total_calibration = {
            k: float(v6_artifact["total_calibration"][k]) for k in ("intercept", "slope")
        }
        self.v6_margin_sd_table = {k: float(v) for k, v in v6_artifact["margin_sd_table"].items()}
        self.v6_total_sd_table = {k: float(v) for k, v in v6_artifact["total_sd_table"].items()}
        self.v6_established_min_sample = int(v6_artifact["established_min_sample"])
        self.artifact_hash = str(artifact["artifact_hash"])

    # ---------------------------------------------------------------- v6 layer (frozen, read from the v6 artifact)
    def v6_means(self, projected_margin_home: float, projected_total: float) -> tuple[float, float]:
        """v6's calibrated means; the 24-point total floor is v6's engine guard, kept identical."""
        margin = (
            self.v6_margin_calibration["intercept"]
            + self.v6_margin_calibration["slope"] * projected_margin_home
        )
        total = self.v6_total_calibration["intercept"] + self.v6_total_calibration["slope"] * projected_total
        return margin, max(24.0, total)

    def v6_slice(self, *, is_fbs_vs_fcs: bool, sample_games: int) -> str:
        if is_fbs_vs_fcs:
            return "fbs_vs_fcs"
        return "established" if sample_games >= self.v6_established_min_sample else "thin_sample"

    # ---------------------------------------------------------------- pmfs
    def margin_sd(self, mu_margin: float, total_mean: float) -> float:
        s0, s1, s2 = self.sd_params
        return math.exp(
            s0
            + s1 * abs(mu_margin) / self.sd_scale
            + s2 * (total_mean - self.sd_total_centre) / self.sd_scale
        )

    def total_sd(self, total_mean: float) -> float:
        for (lo, hi), sd in zip(self.total_buckets, self.total_bucket_sds, strict=True):
            if lo <= total_mean < hi:
                return sd
        return self.total_bucket_sds[-1]

    def margin_pmf(self, mu: float, sd: float) -> np.ndarray:
        """Key-weighted discretised normal re-centred so its mean is ``mu`` (fixed Newton steps, as fitted)."""
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

    def market_probabilities(
        self, pm: np.ndarray, pt: np.ndarray, spread_home_line: float | None, total_line: float | None
    ) -> dict[str, float]:
        grid = self.margin_grid
        home_win = float(pm[grid > 0].sum())
        out = {"home_win": home_win, "away_win": float(pm[grid < 0].sum())}
        if spread_home_line is not None:
            thr = -float(spread_home_line)
            cover, push = float(pm[grid > thr].sum()), float(pm[grid == thr].sum())
            out.update(home_cover=cover, push_spread=push, away_cover=float(pm[grid < thr].sum()))
        if total_line is not None:
            tg, line = self.total_grid, float(total_line)
            out.update(
                over=float(pt[tg > line].sum()),
                push_total=float(pt[tg == line].sum()),
                under=float(pt[tg < line].sum()),
            )
        return out

    def price(
        self,
        *,
        v6_margin: float,
        v6_total: float,
        v6_slice: str,
        anchor: Anchor | None,
        spread_home_line: float | None,
        total_line: float | None,
    ) -> V62Pricing:
        if anchor is not None:
            mu_m = self.w_margin * anchor.margin_home + (1.0 - self.w_margin) * v6_margin
            mu_t = self.w_total * anchor.total + (1.0 - self.w_total) * v6_total
            sd_m, sd_t, status = self.margin_sd(mu_m, mu_t), self.total_sd(mu_t), ANCHOR_TIMESTAMPED
        else:
            # Model-only: v6's own residual SDs, wider than the anchor's, so the fallback is not overconfident.
            mu_m, mu_t = v6_margin, v6_total
            sd_m, sd_t = self.v6_margin_sd_table[v6_slice], self.v6_total_sd_table[v6_slice]
            status = ANCHOR_MISSING
        pm, pt = self.margin_pmf(mu_m, sd_m), self.total_pmf(mu_t, sd_t)
        return V62Pricing(
            mu_m, mu_t, sd_m, sd_t, status, self.market_probabilities(pm, pt, spread_home_line, total_line)
        )


def cfb_structural_v62_model() -> CFBStructuralV62Model:
    from .cfb_structural_v6 import load_cfb_structural_v6_artifact

    return CFBStructuralV62Model(load_cfb_structural_v62_artifact(), load_cfb_structural_v6_artifact())


# ------------------------------------------------------------------------------------------------ side selection
def select_max_edge(sides: Sequence[tuple[str, float, float]]) -> tuple[str, float, float, float] | None:
    """(selection, fair probability, ask, edge) of the side with the largest fair - ask; ties go to the first."""
    best: tuple[str, float, float, float] | None = None
    for selection, prob, ask in sides:
        edge = prob - ask
        if best is None or edge > best[3]:
            best = (selection, prob, ask, edge)
    return best


@dataclass(frozen=True)
class EventQuotes:
    """The Polymarket quotes build_cfb_slate already matched for one event (never ESPN consensus)."""

    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    is_neutral_site: bool
    moneyline: Mapping[str, Any] | None  # {"home_ask","away_ask","slug_base","sportsbook","lineage"}
    spread: Mapping[str, Any] | None  # {"home_line","home_ask","away_ask","slug","sportsbook","lineage"}
    total: Mapping[str, Any] | None  # {"line","over_ask","under_ask","slug","sportsbook","lineage"}
    home_team_id: str | None = None  # ESPN team ids from the slate event: identity lookup wins over the name
    away_team_id: str | None = None


def build_contracts(
    model: CFBStructuralV62Model,
    quotes: EventQuotes,
    *,
    v6_projected_margin: float,
    v6_projected_total: float,
    is_fbs_vs_fcs: bool,
    sample_games: int,
    anchor: Anchor | None,
    decision_time: datetime,
    markets: Sequence[str],
) -> list[dict[str, Any]]:
    """Max-edge contracts for the routed markets; a market is emitted only when its best edge is above zero."""
    v6_margin, v6_total = model.v6_means(v6_projected_margin, v6_projected_total)
    v6_slice = model.v6_slice(is_fbs_vs_fcs=is_fbs_vs_fcs, sample_games=sample_games)
    spread_line = None if quotes.spread is None else float(quotes.spread["home_line"])
    total_line = None if quotes.total is None else float(quotes.total["line"])
    priced = model.price(
        v6_margin=v6_margin,
        v6_total=v6_total,
        v6_slice=v6_slice,
        anchor=anchor,
        spread_home_line=spread_line,
        total_line=total_line,
    )
    pr = priced.probabilities
    anchor_age_h = None
    if anchor is not None:
        anchor_age_h = round((decision_time - parse_utc(anchor.observed_at_utc)).total_seconds() / 3600.0, 3)
    basis_common: dict[str, Any] = {
        "challenger_of": "cfb-structural-v5",
        "anchor_status": priced.anchor_status,
        "anchor_observed_at_utc": None if anchor is None else anchor.observed_at_utc,
        "anchor_age_hours": anchor_age_h,
        "anchor_margin_home": None if anchor is None else anchor.margin_home,
        "anchor_total": None if anchor is None else anchor.total,
        "anchor_source": None if anchor is None else f"{anchor.provider}:{anchor.source_kind}",
        "anchor_record_hash": None if anchor is None else anchor.record_hash,
        "v6_margin": round(v6_margin, 4),
        "v6_total": round(v6_total, 4),
        "v6_slice": v6_slice,
        "is_fbs_vs_fcs": bool(is_fbs_vs_fcs),
        "sample_games": int(sample_games),
        "w_margin": model.w_margin,
        "w_total": model.w_total,
        "projected_margin_home": round(priced.mu_margin, 4),
        "projected_total": round(priced.mu_total, 4),
        "effective_margin_sd": round(priced.margin_sd, 4),
        "effective_total_sd": round(priced.total_sd, 4),
        "side_selection": "max_edge",
        "decision_time_utc": decision_time.isoformat(),
    }
    tag = "model-only (no timestamped anchor)" if anchor is None else "anchor-blended"
    contracts: list[dict[str, Any]] = []

    def emit(
        market: str,
        pick: tuple[str, float, float, float],
        line: float | None,
        slug: str,
        q: Mapping[str, Any],
    ):
        selection, prob, ask, edge = pick
        provenance = _POLYMARKET_PROVENANCE.get(str(q.get("sportsbook")))
        # Only a Polymarket ask is ever a v6.2 price; a sportsbook consensus number is the anchor's world, not an ask.
        if edge <= 0 or provenance is None:
            return
        lineage = q.get("lineage")
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
                "model_uncertainty": 0.0,
                "weather_inputs_verified": None,
                "model_version": CFB_V62_MODEL_VERSION,
                "model_artifact_hash": model.artifact_hash,
                "feature_basis": {**basis_common, "quote_provenance": provenance},
                "inference_inputs": None,
                "rationale": (
                    f"CFB v6.2 challenger ({tag}): fair margin {priced.mu_margin:+.2f}, total {priced.mu_total:.2f}, "
                    f"SD {priced.margin_sd:.2f}/{priced.total_sd:.2f}. Max-edge side {selection} at "
                    f"{100 * prob:.1f}% vs ask {100 * ask:.1f}%."
                ),
                "market_slug": slug,
                "executable_ask": float(ask),
                "observed_at_utc": (
                    lineage["market_quote_observed_at_utc"]
                    if lineage and lineage.get("market_quote_observed_at_utc")
                    else decision_time.isoformat()
                ),
                "timestamp_valid": True,
                "edge_vs_executable_ask": round(edge, 6),
                "sportsbook": str(q["sportsbook"]),
                "market_lineage": lineage,
            }
        )

    if "moneyline" in markets and quotes.moneyline is not None:
        q = quotes.moneyline
        pick = select_max_edge(
            [("home", pr["home_win"], float(q["home_ask"])), ("away", pr["away_win"], float(q["away_ask"]))]
        )
        if pick is not None:
            emit("moneyline", pick, None, f"{q['slug_base']}-{pick[0]}", q)
    if "spread" in markets and quotes.spread is not None and spread_line is not None:
        q = quotes.spread
        pick = select_max_edge(
            [
                ("home", pr["home_cover"], float(q["home_ask"])),
                ("away", pr["away_cover"], float(q["away_ask"])),
            ]
        )
        if pick is not None:
            emit("spread", pick, spread_line if pick[0] == "home" else -spread_line, str(q["slug"]), q)
    if "total" in markets and quotes.total is not None and total_line is not None:
        q = quotes.total
        pick = select_max_edge(
            [("over", pr["over"], float(q["over_ask"])), ("under", pr["under"], float(q["under_ask"]))]
        )
        if pick is not None:
            emit("total", pick, total_line, str(q["slug"]), q)
    return contracts


# ------------------------------------------------------------------------------------------------ slate entry
def canonical_history(history: Sequence[Any]) -> tuple[list[Any], Any]:
    """v6 history with one display name per team identity, and a slate (name, ESPN id) -> history name lookup.

    ESPN renames teams mid-season and many daily rows carry no team id, so v6's own resolver can split one team into
    two partial histories (2026: App State, San Jose State, Hawaii). The identity rule is the shared
    ``features/cfb_team_identity.CFBTeamIdentity`` (v6's resolver plus the id-less name step and the serving-time ESPN
    id lookup); every game is relabelled with its identity's most frequent display name before the v6 extractor
    sees it. The v6 solver and its frozen parameters are untouched; only the grouping of the same results changes.
    """
    from dataclasses import replace

    from ..features.cfb_team_identity import CFBTeamIdentity

    rows = [
        {
            "home_team": g.home,
            "away_team": g.away,
            "home_team_id": g.home_id,
            "away_team_id": g.away_id,
            "event_start_utc": g.start,
            "season_year": g.season,
        }
        for g in history
    ]
    identity = CFBTeamIdentity.from_rows(rows)
    keys = [identity.row_keys(r) for r in rows]
    counts: dict[str, dict[str, int]] = {}
    for g, (hk, ak) in zip(history, keys, strict=True):
        for key, name in ((hk, g.home), (ak, g.away)):
            bucket = counts.setdefault(key, {})
            bucket[name] = bucket.get(name, 0) + 1
    rep = {key: max(names.items(), key=lambda kv: (kv[1], kv[0]))[0] for key, names in counts.items()}
    relabelled = [replace(g, home=rep[hk], away=rep[ak]) for g, (hk, ak) in zip(history, keys, strict=True)]

    def history_name(name: str, team_id: Any = None) -> str:
        return rep.get(identity.key(name, None if team_id in (None, "") else str(team_id)), name)

    return relabelled, history_name


def routed_markets(repo_root: str | Path | None = None) -> list[str]:
    """NCAAF markets whose registered challenger is v6.2 (config/production.yaml ``challengers.NCAAF``)."""
    from ..production_registry import ProductionModelRegistry

    registry = ProductionModelRegistry.load(Path(repo_root) if repo_root is not None else PROJECT_ROOT)
    out = []
    for market in ("moneyline", "spread", "total"):
        entry = registry.challenger("NCAAF", market)
        if entry is not None and entry.model_id == CFB_V62_MODEL_VERSION:
            out.append(market)
    return out


def price_challenger_slate(
    data_root: str | Path,
    decision_time: datetime,
    events: Sequence[EventQuotes | Mapping[str, Any]],
    *,
    markets: Sequence[str] | None = None,
    model: CFBStructuralV62Model | None = None,
    capture_file: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """v6.2 contracts for one slate plus a summary. Fail-closed per game: an unrated team (``V6NoCall``) is skipped.

    v6 ratings come from the live NCAAF history under ``data_root`` as of ``decision_time`` (the v6 extractor's
    result buffer applies); the anchor index is read once from the forward capture.
    """
    from ..data_sources.espn_cfb_pregame_capture import capture_path
    from ..features.cfb_features_v6 import V6NoCall, load_history

    events = [ev if isinstance(ev, EventQuotes) else EventQuotes(**ev) for ev in events]
    routed = list(markets) if markets is not None else routed_markets()
    summary: dict[str, Any] = {
        "model_version": CFB_V62_MODEL_VERSION,
        "markets": routed,
        "events": len(events),
    }
    if not routed or not events:
        summary["status"] = "not_routed" if not routed else "no_events"
        return [], summary
    model = model or cfb_structural_v62_model()
    root = Path(data_root)
    history, _ = load_history(
        root / "processed" / "ncaaf" / "games.jsonl", root / "historical" / "ncaaf_games_all.jsonl", None
    )
    from .cfb_structural_v6 import cfb_structural_v6_model

    history, history_name = canonical_history(history)
    extractor = cfb_structural_v6_model().extractor_for(history)
    anchors = load_anchor_index(
        capture_file if capture_file is not None else capture_path(root), decision_time
    )
    contracts: list[dict[str, Any]] = []
    no_call = anchored = model_only = 0
    for ev in events:
        try:
            feat = extractor.extract_features(
                event_id=ev.event_id,
                game_start_utc=ev.event_start_utc,
                home_team=history_name(ev.home_team, ev.home_team_id),
                away_team=history_name(ev.away_team, ev.away_team_id),
                is_neutral_site=ev.is_neutral_site,
                decision_time=decision_time,
            )
        except V6NoCall:
            no_call += 1
            continue
        anchor = anchors.get(ev.event_id)
        anchored += anchor is not None
        model_only += anchor is None
        contracts.extend(
            build_contracts(
                model,
                ev,
                v6_projected_margin=float(feat.projected_margin_home),
                v6_projected_total=float(feat.projected_total),
                is_fbs_vs_fcs=bool(feat.is_fbs_vs_fcs),
                sample_games=int(feat.sample_games),
                anchor=anchor,
                decision_time=decision_time,
                markets=routed,
            )
        )
    summary.update(
        status="ok",
        history_games=len(history),
        anchors_indexed=len(anchors),
        priced_anchored=anchored,
        priced_model_only=model_only,
        v6_no_call=no_call,
        contracts=len(contracts),
    )
    return contracts, summary
