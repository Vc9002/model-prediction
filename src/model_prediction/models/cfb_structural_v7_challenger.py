"""Routing for the cfb-structural-v7 family (v7, v7.1) challengers: NCAAF Flat/research rows only, never Main or
the buyer.

quick-261121-v7r routed v7 after v6.2 for moneyline/spread/total; quick-261122-v71 routes the v7.1 refit there
instead (v7 stays registered, unrouted). Every function takes ``model_id`` (default v7), so one module serves both.
v7.1 contracts also carry ``feature_capture``: the forecast writer stores their model inputs as a
feature-capture-v1 snapshot (v7 rows stored none).
``models/college_football.build_cfb_slate`` calls ``price_challenger_slate`` after the champion's contracts and
after v6.2's, with the same per-event Polymarket quotes v6.2 receives. ``cli/forecast._forecast_cfb_sport`` sends
any version outside v2-v5 to the research and Flat tiers only, so v7 rows carry zero Main units.
quick-261123-6q5: the same functions also price the v7.2 champion (``role="champion"``, ``CHAMPION_VERSIONS``
only): ``build_cfb_slate`` calls ``price_challenger_slate`` with the serving model before the challengers, and
``cli/forecast.NCAAF_MAIN_VERSIONS`` lets v7.2 rows reach Main through v5.2's copied gates.

Pricing follows v6.2's operator rules (2026-11-21): only a captured Polymarket book (``polymarket_us``) whose own
``market_quote_observed_at_utc`` is at or before the decision; the max-edge side; a market is emitted only when
that edge is above zero. Rows carry v7's own model id, artifact hash and code revision.

v7 is a rating model whose inputs (play-by-play team-game rows, the FBS/FCS class map, CFBD priors, the schedule
that proves the pbp crawl is not behind) live under the runtime root. Reading them and fitting the ratings is
the v7 live-data work's (quick-261121-v7l), behind ONE seam:
``cfb_structural_v7.price_v7_slate(data_root, decision_time, games, *, model) -> (results, summary)``, one result
per game, either ``{"status": "ok", "probabilities": {...}, "mu_margin", "mu_total", "margin_sd", "total_sd",
"early", "is_fbs_vs_fcs"}`` or ``{"status": "no_call", "reason": "NO_CALL_V7_*"}``. Until that function exists the
slate no-calls as a whole with ``NO_CALL_V7_LIVE_DATA_UNWIRED`` and writes nothing. A no-call is always a
decline, never a guess, and every reason is counted in the summary.

Ledger slug hygiene: the ledger has no slug column, so settlement, the pre-kickoff close capture and the CLV
lookup read the slug out of the rationale. v6.2's "(anchor-blended)" token was taken for the slug, and the
moneyline "-home/-away" suffix names no real contract. v7 rows therefore record the real contract slug (the
moneyline contract unsuffixed) as an explicit ``market_slug=<slug>`` token and put nothing else in parentheses.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain import parse_utc
from ..features import cfb_features_v7 as core
from .cfb_structural_v7 import (
    CFB_V7_MODEL_VERSION,
    CHAMPION_VERSIONS,
    V7_FAMILY,
    CFBStructuralV7Model,
    V7Pricing,
    cfb_structural_v7_model,
)
from .cfb_structural_v62 import EventQuotes, select_max_edge

NO_CALL_LIVE_DATA_UNWIRED = "NO_CALL_V7_LIVE_DATA_UNWIRED"
NO_CALL_NO_RESULT = "NO_CALL_V7_NO_RESULT"
# The same rule v6.2 applies (operator decision 2026-11-21): only a captured executable Polymarket book.
CAPTURED_BOOK = "polymarket_us"
_ROUTABLE_MARKETS = ("moneyline", "spread", "total")
ROLES = ("challenger", "champion")
# quick-261123-6q5: what a v7.2 champion row records instead of v5's "cfb-v1" / "cfb_key_number_engine".
CHAMPION_FEATURE_SCHEMA = "cfb-v7-pit-ratings-v1"
CHAMPION_CALIBRATION_METHOD = "cfb_v7_keyed_margin_pmf"
_HERE = Path(__file__).resolve()
_V7_SERVING_SOURCES = (
    _HERE,
    _HERE.parent / "cfb_structural_v7.py",
    _HERE.parent / "cfb_structural_v62.py",  # EventQuotes, select_max_edge
    _HERE.parents[1] / "features" / "cfb_features_v7.py",
    # The live-data modules (quick-261121-v7l), hashed once they exist.
    _HERE.parents[1] / "data_sources" / "cfb_pbp_live.py",
    _HERE.parents[1] / "features" / "cfb_pbp_rows.py",
    # v7.2's total shrink (shrink_total_probabilities) runs on the serving path.
    _HERE.parents[1] / "cfb_replay.py",
)

SlatePricer = Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]]


def v7_serving_code_hash() -> str:
    """16-hex digest of the source files that compute a v7 row, so v7 rows never carry v5's or v6.2's revision."""
    digest = hashlib.sha256()
    for path in _V7_SERVING_SOURCES:
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def default_slate_pricer() -> SlatePricer | None:
    """The live-data seam ``cfb_structural_v7.price_v7_slate``, or None while it is not wired."""
    from . import cfb_structural_v7

    pricer = getattr(cfb_structural_v7, "price_v7_slate", None)
    return pricer if callable(pricer) else None


def routed_markets(repo_root: str | Path | None = None, model_id: str = CFB_V7_MODEL_VERSION) -> list[str]:
    """NCAAF markets whose ``challengers.NCAAF`` route lists ``model_id`` (and the registry has not failed it
    closed)."""
    from ..config import PROJECT_ROOT
    from ..production_registry import ProductionModelRegistry

    registry = ProductionModelRegistry.load(Path(repo_root) if repo_root is not None else PROJECT_ROOT)
    return [
        market
        for market in _ROUTABLE_MARKETS
        if any(e.model_id == model_id for e in registry.challengers_for("NCAAF", market))
    ]


def _quote_time(q: Mapping[str, Any] | None, decision_time: datetime) -> str | None:
    """The captured book's own observation time when it may be priced at ``decision_time``, else None."""
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


def _priceable_markets(quotes: EventQuotes, markets: Sequence[str], decision_time: datetime) -> list[str]:
    return [m for m in markets if _quote_time(getattr(quotes, m), decision_time) is not None]


def _check_role(model: CFBStructuralV7Model, role: str) -> None:
    """A champion row reaches Main and the buyer, so the role must match the version (fail closed both ways): only
    ``CHAMPION_VERSIONS`` price as champion, and a champion version never prices as a challenger (its rows would
    carry a Main-eligible id under a challenger label)."""
    if role not in ROLES:
        raise ValueError(f"unknown cfb v7 pricing role {role!r}")
    if (role == "champion") != (model.version in CHAMPION_VERSIONS):
        raise ValueError(f"cfb {model.version} cannot price as {role}")
    if role == "champion" and not model.feature_capture:
        # Champion rows have no NB replay inputs; the feature-capture snapshot is their only input record.
        raise ValueError(f"cfb {model.version} champion artifact must set serving.feature_capture")


def build_contracts(
    model: CFBStructuralV7Model,
    quotes: EventQuotes,
    pricing: V7Pricing,
    *,
    season: int | None,
    decision_time: datetime,
    markets: Sequence[str],
    data_stats: Mapping[str, Any] | None = None,
    model_inputs: Mapping[str, Any] | None = None,
    role: str = "challenger",
) -> list[dict[str, Any]]:
    """Max-edge v7 contracts against the captured books; a market is emitted only when its best edge is above 0.

    ``model_inputs`` (v7.1) is the seam's per-game input snapshot (fit time, expected metric values per side,
    games played, classes, CFBD z-scores); it goes into ``feature_basis`` and the ledger's feature snapshot.
    ``role="champion"`` (v7.2 only) drops ``challenger_of``, labels the rationale "champion" and records the v7
    feature schema and calibration method on the contract; the pricing and side selection are the same."""
    _check_role(model, role)
    champion = role == "champion"
    pr = pricing.probabilities
    code_revision = v7_serving_code_hash()
    label = model.version.removeprefix("cfb-structural-")
    basis_common: dict[str, Any] = {} if champion else {"challenger_of": "cfb-structural-v5"}
    basis_common |= {
        "season": season,
        "projected_margin_home": round(pricing.mu_margin, 4),
        "projected_total": round(pricing.mu_total, 4),
        "effective_margin_sd": round(pricing.margin_sd, 4),
        "effective_total_sd": round(pricing.total_sd, 4),
        "early_season": bool(pricing.early),
        "is_fbs_vs_fcs": bool(pricing.is_fbs_vs_fcs),
        "side_selection": "max_edge",
        "decision_time_utc": decision_time.isoformat(),
        "quote_provenance": "polymarket_executable_ask",
        "live_data": dict(data_stats or {}),
    }
    if model_inputs:
        basis_common["model_inputs"] = dict(model_inputs)
    contracts: list[dict[str, Any]] = []

    def emit(market: str, pick: tuple[str, float, float, float], line: float | None, slug: str, q) -> None:
        selection, prob, ask, edge = pick
        observed = _quote_time(q, decision_time)
        # A slug without a hyphen ("None", a bare side) names no contract: it could never settle or close.
        if edge <= 0 or observed is None or "-" not in slug:
            return
        contract: dict[str, Any] = {
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
            "model_version": model.version,
            "model_artifact_hash": model.artifact_hash,
            "feature_basis": basis_common,
            "inference_inputs": None,
            # No parenthesised token: settlement takes the first hyphenated one for the slug.
            "rationale": (
                f"CFB {label} {role}: fair margin {pricing.mu_margin:+.2f}, total {pricing.mu_total:.2f}, "
                f"SD {pricing.margin_sd:.2f}/{pricing.total_sd:.2f}. Max-edge side {selection} at "
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
            # The forecast writer stores feature_basis as a feature-capture-v1 snapshot for these rows.
            "feature_capture": bool(model.feature_capture),
        }
        if champion:
            contract["feature_schema_version"] = CHAMPION_FEATURE_SCHEMA
            contract["calibration_method"] = CHAMPION_CALIBRATION_METHOD
        contracts.append(contract)

    if "moneyline" in markets and quotes.moneyline is not None:
        q = quotes.moneyline
        pick = select_max_edge(
            [("home", pr["home_win"], float(q["home_ask"])), ("away", pr["away_win"], float(q["away_ask"]))]
        )
        if pick is not None:
            # The contract slug itself: v5 and v6.2 append the side, a slug no Polymarket contract has.
            emit("moneyline", pick, None, str(q["slug_base"]), q)
    if "spread" in markets and quotes.spread is not None and "home_cover" in pr:
        q = quotes.spread
        spread_line = float(q["home_line"])
        pick = select_max_edge(
            [
                ("home", pr["home_cover"], float(q["home_ask"])),
                ("away", pr["away_cover"], float(q["away_ask"])),
            ]
        )
        if pick is not None:
            emit("spread", pick, spread_line if pick[0] == "home" else -spread_line, str(q["slug"]), q)
    if "total" in markets and quotes.total is not None and "over" in pr:
        q = quotes.total
        pick = select_max_edge(
            [("over", pr["over"], float(q["over_ask"])), ("under", pr["under"], float(q["under_ask"]))]
        )
        if pick is not None:
            emit("total", pick, float(q["line"]), str(q["slug"]), q)
    return contracts


def _pricing(result: Mapping[str, Any]) -> V7Pricing:
    """A seam result's pricing; KeyError/TypeError/ValueError on a malformed result (the caller fails closed)."""
    probabilities = {str(k): float(v) for k, v in dict(result["probabilities"]).items()}
    return V7Pricing(
        mu_margin=float(result["mu_margin"]),
        mu_total=float(result["mu_total"]),
        margin_sd=float(result["margin_sd"]),
        total_sd=float(result["total_sd"]),
        early=bool(result["early"]),
        is_fbs_vs_fcs=bool(result["is_fbs_vs_fcs"]),
        probabilities=probabilities,
    )


def price_challenger_slate(
    data_root: str | Path,
    decision_time: datetime,
    events: Sequence[EventQuotes | Mapping[str, Any]],
    *,
    markets: Sequence[str] | None = None,
    model: CFBStructuralV7Model | None = None,
    slate_pricer: SlatePricer | None = None,
    expected_artifact_hash: str | None = None,
    model_id: str = CFB_V7_MODEL_VERSION,
    role: str = "challenger",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """v7-family contracts for one slate plus a summary. Fails closed slate-wide and per game; never raises
    V7NoCall.

    Only events with a priceable captured book in a routed market and both ESPN team ids reach the seam.
    ``expected_artifact_hash`` is the registry entry's hash; a model whose artifact differs is refused before
    anything is priced, and so is a model whose version is not ``model_id``.

    ``role="champion"`` (quick-261123-6q5) prices the NCAAF champion (``CHAMPION_VERSIONS`` only) through the same
    seam and quote rule; ``build_cfb_slate`` calls it with all three markets and the serving model.
    """
    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    if model_id not in V7_FAMILY:
        raise ValueError(f"not a cfb v7-family model: {model_id!r}")
    slate = [ev if isinstance(ev, EventQuotes) else EventQuotes(**ev) for ev in events]
    routed = list(markets) if markets is not None else routed_markets(model_id=model_id)
    summary: dict[str, Any] = {"model_version": model_id, "markets": routed, "events": len(slate)}
    if not routed or not slate:
        summary["status"] = "not_routed" if not routed else "no_events"
        return [], summary
    model = model or cfb_structural_v7_model(model_id)
    if model.version != model_id:
        raise ValueError(f"cfb v7 model {model.version} is not the routed {model_id}")
    _check_role(model, role)
    if role == "champion":
        summary["role"] = role
    if expected_artifact_hash is not None and model.artifact_hash != expected_artifact_hash:
        raise ValueError(
            f"cfb v7 model artifact {model.artifact_hash[:12]} is not the registered {expected_artifact_hash[:12]}"
        )
    no_calls: Counter[str] = Counter()
    priceable: list[tuple[EventQuotes, list[str]]] = []
    for ev in slate:
        ev_markets = _priceable_markets(ev, routed, decision_time)
        if not ev_markets:
            no_calls["no_captured_quote"] += 1
        elif not ev.home_team_id or not ev.away_team_id:
            no_calls[core.NO_CALL_UNKNOWN_TEAM] += 1
        else:
            priceable.append((ev, ev_markets))
    pricer = slate_pricer if slate_pricer is not None else default_slate_pricer()
    if not priceable or pricer is None:
        if priceable:
            no_calls[NO_CALL_LIVE_DATA_UNWIRED] += len(priceable)
            summary.update(status="no_call", reason=NO_CALL_LIVE_DATA_UNWIRED)
        else:
            summary["status"] = "ok"
        summary.update(priced=0, no_calls=dict(no_calls), contracts=0)
        return [], summary
    games = [
        {
            "event_id": ev.event_id,
            "home_team_id": str(ev.home_team_id),
            "away_team_id": str(ev.away_team_id),
            "is_neutral_site": ev.is_neutral_site,
            "event_start_utc": ev.event_start_utc,
            "spread_home_line": None if ev.spread is None else float(ev.spread["home_line"]),
            "total_line": None if ev.total is None else float(ev.total["line"]),
        }
        for ev, _ in priceable
    ]
    results, seam_summary = pricer(Path(data_root), decision_time, games, model=model)
    by_event = {str(r.get("event_id")): r for r in results}
    season = seam_summary.get("season")
    contracts: list[dict[str, Any]] = []
    priced = 0
    for ev, ev_markets in priceable:
        result = by_event.get(ev.event_id)
        if result is None:
            no_calls[NO_CALL_NO_RESULT] += 1
            continue
        if result.get("status") != "ok":
            no_calls[str(result.get("reason") or "NO_CALL_V7_UNSPECIFIED")] += 1
            continue
        priced += 1
        contracts.extend(
            build_contracts(
                model,
                ev,
                _pricing(result),
                season=season,
                decision_time=decision_time,
                markets=ev_markets,
                data_stats=seam_summary.get("data"),
                model_inputs=result.get("inputs") if model.feature_capture else None,
                role=role,
            )
        )
    summary.update(
        status="ok",
        season=season,
        priced=priced,
        no_calls=dict(no_calls),
        contracts=len(contracts),
        live_data=seam_summary.get("data"),
    )
    return contracts, summary
