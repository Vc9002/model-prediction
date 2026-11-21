"""NBA market-anchored challenger ``nba-anchor-v10``: one Flat row per priced game and market.

Wiring spec: ``outputs/nba/v10_harness/ANCHOR_WIRING.md``; plan and decisions:
``.planning/quick/261121-nw1-nba-v10-wiring/261121-nw1-PLAN.md``. Gate G1 failed, so the artifact
ships lambda = 0: the v10 price IS the anchor (devigged sportsbook consensus, else the Polymarket
mid), and the only edge it can show is anchor vs the Polymarket ask (track T-B).

Isolation from the v9 champion is the point of this module:
- the NBA slates only collect plain inputs here (``spread_inputs`` / ``total_inputs``), after v9's
  priced contracts are final, and a failure there returns no inputs;
- rows are written after v9 has been logged, straight through ``flat_ledger.append_evaluated``
  (not ``_append_secondary_ledger``, whose callers count v9's Flat writes), never to Main, and never
  through the spread logger's moneyline-coherence supersede;
- every failure is contained by the caller and reported in the slate result.
Not registered, not whitelisted for the auto-buyer, never Main-eligible until gate G2.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import timedelta
from pathlib import Path
from statistics import NormalDist
from typing import Any

from ..config import PROJECT_ROOT, unit_policy
from ..data_sources.nba_pretip_capture import espn_odds_path
from ..data_sources.polymarket_us import probability_to_american
from ..domain import EASTERN, League, MarketType, ModelOrigin, PickRequest, parse_utc
from ..eligibility import evaluate_eligibility
from ..entities import EntityResolutionError
from ..ledger import DuplicatePickError
from ..models.nba_anchor_v10 import (
    NBA_ANCHOR_V10_VERSION,
    AnchorParams,
    AnchorQuote,
    CapturedAnchorSource,
    ContractQuote,
    FallbackAnchorSource,
    price_game,
)
from ..nba_anchor_v10_replay import build_snapshot as build_replay_snapshot
from ..production_registry import compute_artifact_hash

_logger = logging.getLogger(__name__)

ANCHOR_ARTIFACT = Path("config/models/research/nba-anchor-v10.json")
V91_TOTAL_ARTIFACT = Path("config/models/research/nba-total-margin-v9-1.json")
SOURCE_CONSENSUS = "espn_consensus_devig"
SOURCE_POLYMARKET_MID = "polymarket_mid"
# A capture older than this is not an anchor: near tip the consensus worker writes every 10 min and
# the Polymarket quote v9 prices is minutes old, while daily runs are hours apart, so 90 min admits
# this run's captures and rejects a previous run's line.
ANCHOR_MAX_AGE = timedelta(minutes=90)
TB_THRESHOLD = 0.030  # plan section 9, pinned: consensus prob minus Polymarket ask, gross of fee
REASON_ANCHOR_MISSING = "NO_CALL_ANCHOR_MISSING"
REASON_NO_ASK = "NO_EXECUTABLE_ASK"

_LOW_SIDE = {"spread": "away", "total": "under"}
_HIGH_SIDE = {"spread": "home", "total": "over"}
# Polymarket row sides: spread long = away at +line, short = home; total long = over, short = under.
_ROW_SIDE = {"spread": {"away": "long", "home": "short"}, "total": {"over": "long", "under": "short"}}


def _load_verified(relative: Path) -> dict[str, Any]:
    payload = json.loads((PROJECT_ROOT / relative).read_text(encoding="utf-8"))
    if payload.get("artifact_hash") != compute_artifact_hash(payload):
        raise ValueError(f"artifact_hash mismatch for {relative}")
    return payload


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _row_mid(side: Any) -> float | None:
    if not isinstance(side, dict):
        return None
    mid = _finite(side.get("midpoint"))
    if mid is None:
        bid, ask = _finite(side.get("bid")), _finite(side.get("ask"))
        mid = None if bid is None or ask is None else (bid + ask) / 2.0
    return mid if mid is not None and 0.0 < mid < 1.0 else None


def _row_ask(side: Any) -> float | None:
    ask = _finite(side.get("ask")) if isinstance(side, dict) else None
    return ask if ask is not None and 0.0 < ask < 1.0 else None


def _season_types(events: list[dict]) -> dict[str, str]:
    out = {}
    for event in events:
        try:
            out[str(event["id"])] = str((event.get("season") or {}).get("slug") or "")
        except (AttributeError, KeyError, TypeError):
            continue
    return out


def _contract_input(market_type: str, prediction: Any, market: dict, model_mean: float, **extra: Any) -> dict:
    sides = _ROW_SIDE[market_type]
    return {
        "event_id": str(prediction.event_id),
        "event_start_utc": str(prediction.event_start_utc),
        "away_team": prediction.away_team,
        "home_team": prediction.home_team,
        "market_type": market_type,
        # Spread: home-margin convention (away covers iff home margin < line), as v9 receives it.
        "contract_line": float(market["line"]),
        "asks": {side: _row_ask(market.get(row_side)) for side, row_side in sides.items()},
        "mids": {side: _row_mid(market.get(row_side)) for side, row_side in sides.items()},
        "quote_observed_at_utc": str(market.get("observed_at_utc") or ""),
        "market_slug": market.get("market_slug"),
        "market_lineage": market.get("_lineage"),
        "model_mean": float(model_mean),
        **extra,
    }


def spread_inputs(predictions: list, market_by_event_id: dict[str, dict], events: list[dict]) -> list[dict]:
    """v10 spread inputs: v9's own served home-margin mean on the contract v9 priced."""
    season_types = _season_types(events)
    out = []
    for prediction in predictions:
        mean = _finite((prediction.inference_inputs or {}).get("mean"))
        market = market_by_event_id.get(prediction.event_id)
        if mean is None or market is None:
            continue
        out.append(
            _contract_input(
                "spread",
                prediction,
                market,
                mean,
                model_source="nba-spread-margin-v9",
                season_type=season_types.get(str(prediction.event_id), ""),
            )
        )
    return out


def total_inputs(
    history: list,
    upcoming: list,
    v9_predictions: list,
    market_by_event_id: dict[str, dict],
    events: list[dict],
) -> list[dict]:
    """v10 total inputs: the v9.1 totals engine (B5 + B6 + B7) on v9's history and upcoming games.

    ``season_types`` comes from the scoreboard (schedule information known before tip), so the B7
    playoff offset applies in the postseason.
    """
    from ..models.nba_v91 import NBAStructuralV91Engine

    if not upcoming:
        return []
    artifact = _load_verified(V91_TOTAL_ARTIFACT)
    season_types = _season_types(events)
    engine = NBAStructuralV91Engine.from_artifact(artifact)
    v91 = {
        str(p.event_id): p
        for p in engine.predict_games(history, upcoming, season_types=season_types)
        if p.market_type == "total"
    }
    out = []
    for prediction in v9_predictions:
        v91_prediction = v91.get(str(prediction.event_id))
        mean = _finite(((v91_prediction.inference_inputs or {}) if v91_prediction else {}).get("mean"))
        market = market_by_event_id.get(prediction.event_id)
        if mean is None or market is None:
            continue
        out.append(
            _contract_input(
                "total",
                prediction,
                market,
                mean,
                model_source=str(artifact.get("model_version")),
                model_artifact_hash=str(artifact["artifact_hash"]),
                v9_mean=_finite((prediction.inference_inputs or {}).get("mean")),
                season_type=season_types.get(str(prediction.event_id), ""),
            )
        )
    return out


def anchor_mean(line: float, p_low: float, sigma: float) -> float:
    """Line-space mean implied by a fair probability at a quoted line.

    ``p_low`` is P(away covers) for spreads (home margin below ``line``) or P(under) for totals. At
    lambda = 0 the anchored probability at the contract line is Phi((C - mean) / sigma), which gives
    back ``p_low`` exactly when C equals the quoted line.
    """
    return line - sigma * NormalDist().inv_cdf(p_low)


def _consensus_quotes(
    data_root: Any, dates: set[str], sigmas: dict[str, float]
) -> tuple[dict[tuple[str, str], list[AnchorQuote]], dict[tuple, dict]]:
    quotes: dict[tuple[str, str], list[AnchorQuote]] = {}
    meta: dict[tuple, dict] = {}
    for day in sorted(dates):
        path = espn_odds_path(data_root, day)
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                try:
                    record = json.loads(raw)
                    event_id = str(record["event_id"])
                    observed = str(record["observed_at_utc"])
                    parse_utc(observed)
                    consensus = record.get("consensus") or {}
                except (KeyError, TypeError, ValueError):
                    continue
                spread = consensus.get("spread") or {}
                total = consensus.get("total") or {}
                home_line = _finite(spread.get("home_line"))
                candidates = {
                    # ESPN's home_line is negative when home is favoured; the anchor line is the
                    # home-margin line (positive when home is favoured).
                    "spread": (None if home_line is None else -home_line, _finite(spread.get("away"))),
                    "total": (_finite(total.get("line")), _finite(total.get("under"))),
                }
                books = {"spread": spread.get("n_books"), "total": total.get("n_books")}
                for market, (line, p_low) in candidates.items():
                    if line is None or p_low is None or not 0.0 < p_low < 1.0:
                        continue  # fail closed: a line without a devigged pair is not a consensus
                    mean = anchor_mean(line, p_low, sigmas[market])
                    quotes.setdefault((event_id, market), []).append(
                        AnchorQuote(event_id, market, mean, SOURCE_CONSENSUS, observed, benchmark_only=False)
                    )
                    meta[(event_id, market, SOURCE_CONSENSUS, observed)] = {
                        "anchor_quoted_line": line,
                        "anchor_fair_prob_low": p_low,
                        "anchor_n_books": books[market],
                        "anchor_providers": [
                            p.get("provider_name")
                            for p in record.get("providers") or []
                            if not p.get("is_live")
                        ],
                    }
    return quotes, meta


def _polymarket_mid_quotes(
    inputs: list[dict], sigmas: dict[str, float]
) -> tuple[dict[tuple[str, str], list[AnchorQuote]], dict[tuple, dict]]:
    quotes: dict[tuple[str, str], list[AnchorQuote]] = {}
    meta: dict[tuple, dict] = {}
    for entry in inputs:
        market = entry["market_type"]
        low, high = entry["mids"].get(_LOW_SIDE[market]), entry["mids"].get(_HIGH_SIDE[market])
        lows = [p for p in (low, None if high is None else 1.0 - high) if p is not None]
        observed = entry["quote_observed_at_utc"]
        if not lows or not observed:
            continue
        p_low = sum(lows) / len(lows)
        if not 0.0 < p_low < 1.0:
            continue
        line = float(entry["contract_line"])
        mean = anchor_mean(line, p_low, sigmas[market])
        key = (entry["event_id"], market)
        quotes.setdefault(key, []).append(
            AnchorQuote(
                entry["event_id"], market, mean, SOURCE_POLYMARKET_MID, observed, benchmark_only=False
            )
        )
        meta[(*key, SOURCE_POLYMARKET_MID, observed)] = {
            "anchor_quoted_line": line,
            "anchor_fair_prob_low": round(p_low, 6),
            "anchor_n_books": None,
            "anchor_providers": ["polymarket_us"],
        }
    return quotes, meta


def _tb_block(entry: dict, call: Any, anchor_meta: dict, sigma: float) -> dict[str, Any]:
    """Track T-B: consensus probability minus the Polymarket ask, per side, gross of fee."""
    if call.anchor_source != SOURCE_CONSENSUS or call.anchor_line is None:
        return {"evaluated": False, "flag": False, "threshold": TB_THRESHOLD}
    market = entry["market_type"]
    p_low = NormalDist().cdf((float(entry["contract_line"]) - float(call.anchor_line)) / sigma)
    probs = {_LOW_SIDE[market]: p_low, _HIGH_SIDE[market]: 1.0 - p_low}
    gaps = {
        side: round(prob - ask, 6)
        for side, prob in probs.items()
        if (ask := entry["asks"].get(side)) is not None
    }
    best = max(gaps, key=lambda side: gaps[side], default=None)
    return {
        "evaluated": True,
        "threshold": TB_THRESHOLD,
        "consensus_prob_at_contract": {side: round(p, 6) for side, p in probs.items()},
        "consensus_quoted_line": anchor_meta.get("anchor_quoted_line"),
        "gap_by_side": gaps,
        "side": best,
        "gap": None if best is None else gaps[best],
        "flag": best is not None and gaps[best] >= TB_THRESHOLD,
    }


def _selection_line(market: str, side: str, contract_line: float) -> float:
    # Mirrors v9's ledger convention so settlement grades the same way: away +C / home -C.
    if market == "spread":
        return contract_line if side == "away" else -contract_line
    return contract_line


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def log_nba_anchor_v10(
    *,
    forecast: dict[str, Any],
    market: str,
    data_root: Any,
    args_date: str,
    config: dict,
    registry: Any,
    bans: Any,
    flat_ledger: Any,
) -> dict[str, Any]:
    """Price every collected game with the anchor scaffold and append one Flat row per game."""
    from . import forecast as _forecast

    inputs = [entry for entry in forecast.get("anchor_v10_inputs") or [] if entry["market_type"] == market]
    summary: dict[str, Any] = {
        "model_version": NBA_ANCHOR_V10_VERSION,
        "market": market,
        "games": len(inputs),
        "logged": 0,
        "anchor_sources": {},
        "tb_flags": 0,
        "tb_flag_event_ids": [],
        "skip_counts": {},
        "skipped": [],
        "anchor_max_age_minutes": ANCHOR_MAX_AGE.total_seconds() / 60,
        "main": "never (main_min_edge unset; gate G2 not passed)",
    }
    if flat_ledger is None:
        return {**summary, "status": "no_flat_ledger"}
    artifact = _load_verified(ANCHOR_ARTIFACT)
    params = AnchorParams.from_artifact(artifact)
    artifact_hash = str(artifact["artifact_hash"])
    sigmas = {name: mp.sigma for name, mp in params.markets.items()}

    dates = {args_date}
    for entry in inputs:
        try:
            dates.add(parse_utc(entry["event_start_utc"]).astimezone(EASTERN).date().isoformat())
        except (TypeError, ValueError):
            continue
    consensus, consensus_meta = _consensus_quotes(data_root, dates, sigmas)
    mids, mid_meta = _polymarket_mid_quotes(inputs, sigmas)
    meta = {**consensus_meta, **mid_meta}
    source = FallbackAnchorSource(
        [
            CapturedAnchorSource(consensus, max_age=ANCHOR_MAX_AGE),
            CapturedAnchorSource(mids, max_age=ANCHOR_MAX_AGE),
        ]
    )
    # Captured AFTER both anchor sets are loaded, so a capture is never judged by a stale clock.
    decision_time = _forecast.utc_now()

    def _skip(event_id: str, reason: str) -> None:
        summary["skip_counts"][reason] = summary["skip_counts"].get(reason, 0) + 1
        summary["skipped"].append({"event_id": event_id, "reason": reason})

    for entry in inputs:
        event_id = entry["event_id"]
        try:
            calls = price_game(
                event_id,
                {market: entry["model_mean"]},
                {market: ContractQuote(line=float(entry["contract_line"]), asks=entry["asks"])},
                source,
                params,
                decision_time=decision_time,
                serving=True,
            )
        except (ArithmeticError, TypeError, ValueError) as error:
            _skip(event_id, f"pricing failed: {str(error)[:160]}")
            continue
        if not calls:
            _skip(event_id, "NO_CALL_NOT_PRICED")
            continue
        call = calls[0]
        if call.anchor_source is None:
            _skip(event_id, REASON_ANCHOR_MISSING)
            continue
        if call.ask is None:
            _skip(event_id, REASON_NO_ASK)
            continue
        anchor_meta = meta.get((event_id, market, call.anchor_source, call.anchor_observed_at_utc), {})
        mp = params.markets[market]
        tb = _tb_block(entry, call, anchor_meta, mp.sigma)
        age = (decision_time - parse_utc(str(call.anchor_observed_at_utc))).total_seconds()
        components = {
            "model_version": NBA_ANCHOR_V10_VERSION,
            "artifact_hash": artifact_hash,
            "market": market,
            "decision_time_utc": decision_time.isoformat(),
            "anchor_source": call.anchor_source,
            # Unrounded: the ledger replays the probability from these exact values.
            "anchor_mean": float(call.anchor_line or 0.0),
            "anchor_observed_at_utc": call.anchor_observed_at_utc,
            "anchor_age_seconds": round(age, 1),
            **anchor_meta,
            "model_mean": call.model_mean,
            "model_source": entry.get("model_source"),
            "model_artifact_hash": entry.get("model_artifact_hash"),
            "v9_mean": entry.get("v9_mean"),
            "season_type": entry.get("season_type"),
            "lambda": call.lambda_,
            "sigma": call.sigma,
            "blended_mean": round(call.blended_mean, 4),
            "contract_line": call.contract_line,
            "side": call.side,
            "probability": round(call.probability, 6),
            "ask": call.ask,
            "fee": None if call.fee is None else round(call.fee, 6),
            "net_edge": None if call.edge is None else round(call.edge, 6),
            "gross_gap": round(call.probability - call.ask, 6),
            "main_eligible": False,
            "reason_codes": list(call.reason_codes),
            "tb": tb,
            "market_slug": entry.get("market_slug"),
            "quote_observed_at_utc": entry.get("quote_observed_at_utc"),
        }
        try:
            snapshot_json = json.dumps(
                build_replay_snapshot(
                    components,
                    event_id=event_id,
                    event_start_utc=entry["event_start_utc"],
                    observed_at_utc=decision_time.isoformat(),
                ),
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            _skip(event_id, f"snapshot rejected: {str(error)[:160]}")
            continue
        if not tb["evaluated"]:
            tb_note = "T-B not evaluated (no consensus anchor)"
        else:
            tb_note = (
                f"T-B {'FLAG' if tb['flag'] else 'no flag'} (best gap {_pct(tb['gap'])} on {tb['side']})"
            )
        request = PickRequest(
            event_start_utc=entry["event_start_utc"],
            event_id=event_id,
            league=League.NBA,
            away_team=entry["away_team"],
            home_team=entry["home_team"],
            market_type=MarketType.SPREAD if market == "spread" else MarketType.TOTAL,
            selection=call.side,
            line=_selection_line(market, call.side, float(entry["contract_line"])),
            sportsbook="polymarket_us",
            american_odds=probability_to_american(call.ask),
            model_probability=round(call.probability, 6),
            model_uncertainty=None,
            model_version=NBA_ANCHOR_V10_VERSION,
            rationale=(
                f"Market-anchored v10 ({market}), lambda {call.lambda_:g}: anchor {call.anchor_source} "
                f"(observed {call.anchor_observed_at_utc}), model mean {call.model_mean:.1f} "
                f"({entry.get('model_source')}) at weight {call.lambda_:g}. P({call.side} at {call.contract_line:g}) "
                f"{_pct(call.probability)} vs ask {_pct(call.ask)}, net edge {_pct(call.edge)}; "
                f"{tb_note}."
            ),
            risks=(
                "Flat-only challenger: G1 failed, so the price is the anchor's and the model has no "
                "weight. Not Main-eligible until forward gate G2."
            ),
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_enabled=True,
            observed_at_utc=entry.get("quote_observed_at_utc") or None,
            model_artifact_hash=artifact_hash,
            calibration_method="anchor_normal",
            calibration_version=NBA_ANCHOR_V10_VERSION,
            calibration_artifact_hash=artifact_hash,
            feature_schema_version=NBA_ANCHOR_V10_VERSION,
            code_revision=artifact_hash,
            model_input_snapshot_json=snapshot_json,
            **(entry.get("market_lineage") or {}),
        )
        try:
            request.validate(now=decision_time)
            away = registry.resolve(request.league, request.away_team, request.event_start_utc)
            home = registry.resolve(request.league, request.home_team, request.event_start_utc)
            with _forecast._LEDGER_LOCK:
                eligibility = _forecast._downgrade_unserved(
                    evaluate_eligibility(
                        request,
                        registry,
                        bans,
                        flat_ledger.exposure(
                            request,
                            now=decision_time,
                            canonical_team_ids=(away.canonical_team_id, home.canonical_team_id),
                        ),
                        unit_policy(config),
                        now=decision_time,
                    )
                )
                flat_ledger.append_evaluated(request, eligibility, now=decision_time)
        except DuplicatePickError:
            _skip(event_id, "duplicate")
            continue
        except (EntityResolutionError, ValueError) as error:
            _skip(event_id, f"rejected: {str(error)[:160]}")
            continue
        summary["logged"] += 1
        summary["anchor_sources"][call.anchor_source] = (
            summary["anchor_sources"].get(call.anchor_source, 0) + 1
        )
        if tb["flag"]:
            summary["tb_flags"] += 1
            summary["tb_flag_event_ids"].append(event_id)
    return {**summary, "status": "ok"}
