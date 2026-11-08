"""Operational adapter for the existing NCAAB tempo model."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

from .college_contract_evidence import build_candidate_evidence, save_candidate_evidence
from .config import PROJECT_ROOT
from .data_sources.espn import ESPNClient
from .data_sources.polymarket_us import PolymarketUSClient, probability_to_american
from .domain import League, MarketType, ModelOrigin, ModelState, PickRequest, parse_utc, utc_now
from .eligibility import EligibilityResult, RecordType
from .entities import CanonicalTeam
from .feature_capture import contract_snapshot_json
from .features.base import FeatureStore
from .ledger import DuplicatePickError, defer_sqlite_xlsx_exports
from .models.basketball import UpcomingGame
from .models.ncaab_spread_v5 import ncaab_spread_v5_model
from .models.ncaab_spread_v6 import ncaab_spread_v6_model
from .models.ncaab_total_v6 import ncaab_total_v6_model
from .production_registry import ProductionModelRegistry

ncaab_spread_model = ncaab_spread_v6_model
ncaab_total_model = ncaab_total_v6_model
ncaab_model = ncaab_spread_v6_model


def _normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("utf-8")
    s = s.lower().replace("&", "and").replace(".", "").replace("'", "").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


_NCAAB_ALIASES: dict[str, str] = {
    "miami (fl)": "miami hurricanes",
    "miami fl": "miami hurricanes",
    "miami": "miami hurricanes",
    "florida": "florida gators",
    "north carolina": "north carolina tar heels",
    "penn state": "penn state nittany lions",
    "illinois": "illinois fighting illini",
    "oklahoma": "oklahoma sooners",
    "minnesota": "minnesota golden gophers",
    "north dakota": "north dakota fighting hawks",
    "louisiana": "louisiana ragin cajuns",
    "la tech": "louisiana tech bulldogs",
    "louisiana tech": "louisiana tech bulldogs",
    "louisiana monroe": "ul monroe warhawks",
    "ul monroe": "ul monroe warhawks",
    "maryland eastern shore": "maryland eastern shore hawks",
    "fdu": "fairleigh dickinson knights",
    "usc upstate": "south carolina upstate spartans",
    "central connecticut state": "central connecticut blue devils",
    "central connecticut": "central connecticut blue devils",
    "maine": "maine black bears",
    "st thomas (mn)": "st thomas tommies",
    "st thomas mn": "st thomas tommies",
    "saint thomas": "st thomas tommies",
    "uconn": "connecticut",
    "nc state": "nc state wolfpack",
    "penn": "pennsylvania quakers",
    "ole miss": "ole miss rebels",
    "pitt": "pittsburgh panthers",
    "pittsburgh": "pittsburgh panthers",
    "ucf": "ucf knights",
    "usf": "south florida bulls",
    "byu": "byu cougars",
    "smu": "smu mustangs",
    "tcu": "tcu horned frogs",
    "lsu": "lsu tigers",
    "vcu": "vcu rams",
    "unc": "north carolina tar heels",
    "unlv": "unlv rebels",
    "utep": "utep miners",
    "utsa": "utsa roadrunners",
    "umass": "umass minutemen",
    "liu": "long island university sharks",
}


def _resolve_ncaab_team(
    school: str,
    mascot: str,
    names: set[str],
    normalized_names: dict[str, str],
) -> str:
    norm_school = _normalize_name(school)
    norm_mascot = _normalize_name(mascot) if mascot and not any(c in mascot for c in "+-0123456789") else ""

    if norm_school in _NCAAB_ALIASES:
        target = _NCAAB_ALIASES[norm_school]
        for k, v in normalized_names.items():
            if k == target or k.startswith(target + " ") or target in k:
                return v

    if norm_mascot:
        combined = f"{norm_school} {norm_mascot}"
        if combined in normalized_names:
            return normalized_names[combined]
        for k, v in normalized_names.items():
            if k == combined:
                return v

    exact_prefix = [v for k, v in normalized_names.items() if k.startswith(norm_school + " ")]
    if len(exact_prefix) == 1:
        return exact_prefix[0]

    if norm_mascot:
        mascot_cands = [
            v for k, v in normalized_names.items() if k.endswith(" " + norm_mascot) and norm_school in k
        ]
        if len(mascot_cands) == 1:
            return mascot_cands[0]

    if norm_school.startswith("saint "):
        st_variant = "st " + norm_school[6:]
        cands = [v for k, v in normalized_names.items() if k.startswith(st_variant + " ")]
        if len(cands) == 1:
            return cands[0]
    elif norm_school.startswith("st "):
        st_variant = "saint " + norm_school[3:]
        cands = [v for k, v in normalized_names.items() if k.startswith(st_variant + " ")]
        if len(cands) == 1:
            return cands[0]

    school_tokens = set(norm_school.split())
    ranked = []
    for k, v in normalized_names.items():
        k_tokens = set(k.split())
        if school_tokens.issubset(k_tokens):
            diff = len(k_tokens) - len(school_tokens)
            ranked.append((diff, v))
    ranked.sort(key=lambda x: x[0])
    if ranked and (len(ranked) == 1 or ranked[0][0] < ranked[1][0]):
        return ranked[0][1]

    if school in names:
        return school
    combined_raw = f"{school} {mascot}".strip() if norm_mascot else school
    if combined_raw in names:
        return combined_raw

    return combined_raw


def _balance(market: dict[str, Any]) -> float:
    prices = [
        float(s["price_probability"])
        for s in market.get("sides", [])
        if s.get("price_probability") is not None
    ]
    return max((abs(p - 0.5) for p in prices), default=1.0)


def _main_line_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Moneyline plus, per spread/total, only the most balanced (main) line.

    Alternate lines are the same opinion repeated and would multiply correlated
    rows in the ledger.
    """
    kept = [m for m in markets if m.get("market_type") == "moneyline"]
    for kind in ("spread", "total"):
        candidates = [m for m in markets if m.get("market_type") == kind and len(m.get("sides", [])) == 2]
        if candidates:
            kept.append(min(candidates, key=_balance))
    return kept


def forecast_ncaab_sport(
    *,
    data_root,
    args_date,
    config,
    research_ledger=None,
    gated_ledger=None,
    flat_ledger=None,
    main_ledger=None,
):
    board = ESPNClient().scoreboard("NCAAB", args_date)
    spread_model = (
        ncaab_spread_model()
        if ncaab_model in (ncaab_spread_v5_model, ncaab_spread_v6_model)
        else ncaab_model()
    )
    total_model = ncaab_total_model()
    result: dict[str, Any] = {
        "sport": "ncaab",
        "model_version": f"{spread_model.version}+{total_model.version}",
        "scheduled_games": len(board.get("events", [])),
        "logged": 0,
        "flat_logged": 0,
        "main_logged": 0,
        "research_ledger_logged": 0,
        "gated_ledger_logged": 0,
        "skipped": [],
        "errors": [],
    }
    if not result["scheduled_games"]:
        result["status"] = "no_scheduled_events"
        return result
    history = FeatureStore(data_root).games_before("ncaab", args_date)
    if not history:
        result.update(status="data_unavailable", reason="no_completed_ncaab_history")
        return result
    history_digest = hashlib.sha256(
        json.dumps(
            sorted(
                (game.event_id, game.event_start_utc, game.home_score, game.away_score) for game in history
            ),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    events = PolymarketUSClient().slate("NCAAB", date.fromisoformat(args_date))
    observed = utc_now()
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    for market, expected_model in (
        ("moneyline", spread_model),
        ("spread", spread_model),
        ("total", total_model),
    ):
        champion = registry.champion("NCAAB", market)
        if champion is None or not champion.available or champion.model_id != expected_model.version:
            raise ValueError(
                f"NCAAB {market} champion {champion.model_id if champion else None} does not match executable model {expected_model.version}"
            )
    policy = config.get("models", {}).get("NCAAB", {})
    min_edge = float(policy.get("min_edge", 0.035))
    min_team_games = int(policy.get("min_team_games", 0))
    min_moneyline_ask = float(policy.get("min_moneyline_ask", 0.20))
    max_spread_line = float(policy.get("max_spread_line", 16.0))
    max_margin_divergence = float(policy.get("max_margin_divergence", 12.0))
    games_by_team: dict[str, int] = {}
    for game in history:
        for name in (game.home_team, game.away_team):
            games_by_team[name] = games_by_team.get(name, 0) + 1
    names = {name for game in history for name in (game.home_team, game.away_team)}
    normalized_names = {_normalize_name(name): name for name in names}

    espn_neutral_pairs: set[frozenset[str]] = set()
    for espn_ev in board.get("events", []):
        comps = espn_ev.get("competitions", [])
        if comps and comps[0].get("neutralSite", False):
            competitors = comps[0].get("competitors", [])
            pair = frozenset(
                _resolve_ncaab_team(
                    c.get("team", {}).get("location") or c.get("team", {}).get("displayName", ""),
                    c.get("team", {}).get("name", ""),
                    names,
                    normalized_names,
                )
                for c in competitors
                if c.get("team")
            )
            if len(pair) == 2:
                espn_neutral_pairs.add(pair)

    planned: list[
        tuple[
            dict[str, Any],
            dict[str, Any],
            str,
            float | None,
            list[dict[str, Any]],
            str,
            str,
            str,
            str,
            str,
        ]
    ] = []
    upcoming_batch: list[UpcomingGame] = []
    market_game_keys: dict[str, tuple[str, str]] = {}

    for event in events:
        identity = event.get("event_id") or event.get("id")
        start = event.get("event_start_utc")
        if not identity or not start or parse_utc(start) <= observed:
            result["skipped"].append({"event_id": identity, "reason": "missing_identity_or_event_started"})
            continue

        away_school = None
        home_school = None
        away_mascot = ""
        home_mascot = ""

        for m in event.get("markets", []):
            sides = m.get("sides", [])
            a_side = next((s for s in sides if s.get("selection") == "away"), None)
            h_side = next((s for s in sides if s.get("selection") == "home"), None)
            if a_side and h_side:
                if a_side.get("team") and h_side.get("team"):
                    away_school = str(a_side["team"])
                    home_school = str(h_side["team"])
                    desc_a = str(a_side.get("description", ""))
                    desc_h = str(h_side.get("description", ""))
                    if desc_a and not any(c in desc_a for c in "+-0123456789"):
                        away_mascot = desc_a
                    if desc_h and not any(c in desc_h for c in "+-0123456789"):
                        home_mascot = desc_h
                    break
                if a_side.get("description") and h_side.get("description"):
                    desc_a = str(a_side["description"])
                    desc_h = str(h_side["description"])
                    if not any(c in desc_a for c in "+-0123456789") and not any(
                        c in desc_h for c in "+-0123456789"
                    ):
                        away_school = desc_a
                        home_school = desc_h
                        break

        if not away_school:
            title = event.get("title", "")
            for sep in (" vs. ", " vs ", " at ", " @ "):
                if sep in title:
                    parts = title.split(sep)
                    if len(parts) == 2:
                        away_school = parts[0].strip()
                        home_school = parts[1].strip()
                        break

        if not away_school or not home_school:
            result["skipped"].append({"event_id": identity, "reason": "team_identity_unavailable"})
            continue

        away = _resolve_ncaab_team(away_school, away_mascot, names, normalized_names)
        home = _resolve_ncaab_team(home_school, home_mascot, names, normalized_names)

        is_neutral = (
            bool(event.get("neutral_site", False))
            or bool(event.get("is_neutral", False))
            or frozenset({away, home}) in espn_neutral_pairs
        )

        if min_team_games > 0 and (
            games_by_team.get(away, 0) < min_team_games or games_by_team.get(home, 0) < min_team_games
        ):
            result["skipped"].append({"event_id": identity, "reason": "insufficient_history_non_d1"})
            continue

        main_market_ids = {id(m) for m in _main_line_markets(event.get("markets", []))}
        for market_def in event.get("markets", []):
            kind = market_def.get("market_type")
            if kind not in {"moneyline", "spread", "total"}:
                continue
            sides = market_def.get("sides", [])
            away_side: dict[str, Any] = next((s for s in sides if s.get("selection") == "away"), {})
            line = market_def.get("line")
            if kind == "spread" and away_side.get("line") is None:
                continue
            if kind == "total" and line is None:
                continue

            slug = str(market_def.get("market_slug") or "")
            game_key = f"{identity}_{len(upcoming_batch)}"
            upcoming = UpcomingGame(
                str(game_key),
                start,
                away,
                home,
                spread_away_line=away_side.get("line") if kind == "spread" else None,
                total_line=line if kind == "total" else None,
                neutral_site=is_neutral,
            )
            upcoming_batch.append(upcoming)
            if slug:
                market_game_keys[slug] = (game_key, kind)
            if id(market_def) in main_market_ids:
                planned.append(
                    (event, market_def, kind, line, sides, away, home, str(start), str(identity), game_key)
                )

    if not upcoming_batch:
        archive_root = Path(data_root)
        candidate_rows, candidate_summary = build_candidate_evidence(
            sport="ncaab",
            day=args_date,
            events=events,
            probabilities={},
            archive_root=archive_root,
            decision_at=observed,
        )
        packet = save_candidate_evidence(candidate_rows, candidate_summary, archive_root)
        result["candidate_quote_coverage"] = {**candidate_summary, "packet_path": str(packet)}
        result["logged"] = 0
        result["status"] = "ok"
        return result

    predictions = spread_model.predict_games(history, upcoming_batch) + total_model.predict_games(
        history, upcoming_batch
    )
    if predictions and all(hasattr(p, "event_id") for p in predictions):
        pred_lookup = {(p.event_id, p.market_type): p for p in predictions}
    else:
        pred_lookup = {
            (planned[i][9], getattr(p, "market_type", planned[i][2])): p
            for i, p in enumerate(predictions)
            if i < len(planned)
        }

    candidate_probabilities: dict[str, dict[str, Any]] = {}
    for slug, (game_key, kind) in market_game_keys.items():
        prediction = pred_lookup.get((game_key, kind))
        if prediction is None:
            continue
        model_version = total_model.version if kind == "total" else spread_model.version
        basis = getattr(prediction, "feature_basis", None) or {}
        candidate_probabilities[slug] = {
            "model_version": model_version,
            "model_event_id": game_key,
            "feature_packet_hash": hashlib.sha256(
                json.dumps(
                    {"features": basis, "history_scores_sha256": history_digest, "cutoff_date": args_date},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "probabilities": {
                key: value
                for key, value in prediction.probabilities.items()
                if key in ({"over", "under"} if kind == "total" else {"home", "away"})
            },
        }
    archive_root = Path(data_root)
    candidate_rows, candidate_summary = build_candidate_evidence(
        sport="ncaab",
        day=args_date,
        events=events,
        probabilities=candidate_probabilities,
        archive_root=archive_root,
        decision_at=observed,
    )
    candidate_by_slug = {row["market_slug"]: row for row in candidate_rows}
    candidate_summary["serving_decision_joins"] = []

    with defer_sqlite_xlsx_exports():
        for event, market_def, kind, line, sides, away, home, start, identity, game_key in planned:
            prediction = pred_lookup.get((game_key, kind))
            if prediction is None:
                continue

            selections = {"over", "under"} if kind == "total" else {"home", "away"}
            valid_sides = [
                side
                for side in sides
                if side.get("selection") in selections
                and side.get("price_probability") is not None
                and 0 < float(side["price_probability"]) < 1
            ]
            if not valid_sides:
                continue
            if kind == "moneyline":
                # Pure Probability Winner Selection: pick the projected winner (model_prob >= 0.50).
                # Do NOT pick underdogs to win straight-up when the model projects them to lose.
                winner_side = max(
                    valid_sides, key=lambda s: prediction.probabilities.get(s["selection"], 0.0)
                )
                if prediction.probabilities.get(winner_side["selection"], 0.0) < 0.50:
                    continue
                best_side = winner_side
            else:
                # Spread / total: only consider sides the model projects to cover/hit (prob >= 0.50)
                cover_sides = [
                    s for s in valid_sides if prediction.probabilities.get(s["selection"], 0.0) >= 0.50
                ]
                if not cover_sides:
                    continue
                best_side = max(
                    cover_sides,
                    key=lambda side: (
                        prediction.probabilities[side["selection"]] - float(side["price_probability"])
                    ),
                )
            for side in (best_side,):
                selection, ask = side["selection"], side["price_probability"]
                probability = prediction.probabilities[selection]
                edge = probability - float(ask)
                candidate_row = candidate_by_slug.get(str(market_def.get("market_slug") or ""))
                candidate_side = (
                    next((s for s in candidate_row["sides"] if s["selection"] == selection), None)
                    if candidate_row
                    else None
                )
                candidate_summary["serving_decision_joins"].append(
                    {
                        "event_id": str(identity),
                        "market_slug": market_def.get("market_slug"),
                        "selection": selection,
                        "indicative_ask": float(ask),
                        "executable_ask": candidate_side.get("executable_ask") if candidate_side else None,
                        "quote_hash": candidate_row.get("quote_hash") if candidate_row else None,
                        "quote_reason": candidate_row.get("quote_reason") if candidate_row else "not_listed",
                    }
                )
                decision_line = (
                    (side.get("line") if side.get("line") is not None else line)
                    if kind != "moneyline"
                    else None
                )

                model_inst = total_model if kind == "total" else spread_model
                model_version = model_inst.version

                # Gating for moneyline: do not bet extreme longshots where model lacks predictive resolution
                if kind == "moneyline" and float(ask) < min_moneyline_ask:
                    continue

                # Gating for spread: do not bet blowout spreads or severe model divergence
                if kind == "spread":
                    if decision_line is not None and abs(float(decision_line)) > max_spread_line:
                        continue
                    exp_margin = (getattr(prediction, "inference_inputs", {}) or {}).get("mean")
                    if (
                        exp_margin is not None
                        and decision_line is not None
                        and abs(float(exp_margin) - float(decision_line)) > max_margin_divergence
                    ):
                        continue

                # Minimum edge check
                if edge < min_edge:
                    continue

                snapshot = contract_snapshot_json(
                    {
                        "market_type": kind,
                        "model_version": model_version,
                        "event_id": str(identity),
                        "event_start_utc": str(start),
                        "home_team": home,
                        "away_team": away,
                        "feature_basis": {
                            **(getattr(prediction, "feature_basis", None) or {}),
                            "history_games": len(history),
                            "history_scores_sha256": history_digest,
                            "history_cutoff_date": args_date,
                            "research_quote_join": {
                                "status": candidate_row.get("quote_reason")
                                if candidate_row
                                else "not_listed",
                                "market_slug": market_def.get("market_slug"),
                                "quote_hash": candidate_row.get("quote_hash") if candidate_row else None,
                                "quote_archive_path": (
                                    candidate_row.get("quote_archive_path") if candidate_row else None
                                ),
                                "quote_observed_at_utc": (
                                    candidate_row.get("quote_observed_at_utc") if candidate_row else None
                                ),
                                "executable_ask": (
                                    candidate_side.get("executable_ask") if candidate_side else None
                                ),
                                "indicative_ask": float(ask),
                            },
                        },
                    },
                    sport="NCAAB",
                    selection=selection,
                    line=decision_line,
                    probability=float(probability),
                    observed_at_utc=observed.isoformat(),
                )
                request = PickRequest(
                    event_start_utc=str(start),
                    event_id=str(identity),
                    league=League.NCAAB,
                    away_team=away,
                    home_team=home,
                    market_type=MarketType(kind),
                    selection=selection,
                    line=decision_line,
                    sportsbook="model_opinion_indicative_market",
                    american_odds=probability_to_american(float(ask)),
                    model_probability=probability,
                    model_uncertainty=prediction.uncertainty,
                    model_version=model_version,
                    rationale=prediction.rationale,
                    risks="Paper tempo model; live market quote is an indicative reference, not verified executable BBO.",
                    model_origin=ModelOrigin.STATISTICAL_MODEL,
                    model_state=ModelState.SHADOW_QUALIFIED,
                    observed_at_utc=observed.isoformat(),
                    model_input_snapshot_json=snapshot,
                )
                teams = [
                    CanonicalTeam(name, League.NCAAB, name, name[:3], True, None, None, ())
                    for name in (away, home)
                ]
                dynamic_units = (
                    getattr(model_inst, "compute_dynamic_units", None)
                    and model_inst.compute_dynamic_units(edge, min_edge=min_edge, max_edge=0.08)
                ) or 1.00
                eligibility = EligibilityResult(
                    RecordType.RESEARCH_OBSERVATION,
                    "CALL",
                    "PAPER_CALL_MODEL_UNVALIDATED",
                    dynamic_units,
                    round(probability * 100),
                    edge,
                    edge,
                    *teams,
                )
                destinations = (
                    ("flat_logged", flat_ledger, True),
                    ("research_ledger_logged", research_ledger, edge >= min_edge),
                    ("gated_ledger_logged", gated_ledger, edge >= min_edge),
                    ("main_logged", main_ledger, edge >= min_edge),
                )
                for counter, destination, eligible in destinations:
                    if destination is None or not eligible:
                        continue
                    try:
                        destination.append_evaluated(request, eligibility, now=observed)
                        result[counter] += 1
                    except DuplicatePickError:
                        pass
                    except (ValueError, OSError) as exc:
                        result["errors"].append({"event_id": identity, "ledger": counter, "reason": str(exc)})

    result["logged"] = result["flat_logged"] or result["main_logged"]
    packet = save_candidate_evidence(candidate_rows, candidate_summary, archive_root)
    result["candidate_quote_coverage"] = {
        **{
            key: candidate_summary[key]
            for key in (
                "listed_contracts",
                "quote_matched",
                "quote_coverage",
                "model_scored",
                "complete",
                "quote_reasons",
                "by_market",
            )
        },
        "serving_decisions": len(candidate_summary["serving_decision_joins"]),
        "packet_path": str(packet),
    }
    result["status"] = "error" if result["errors"] else "ok"
    return result
