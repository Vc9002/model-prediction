"""Operational adapter for the existing NCAAB tempo model."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .calibration import IdentityCalibrator, load_calibrator_artifact
from .college_contract_evidence import build_candidate_evidence, save_candidate_evidence
from .config import PROJECT_ROOT
from .data_sources.espn import ESPNClient
from .data_sources.polymarket_us import PolymarketUSClient, probability_to_american
from .domain import League, MarketType, ModelOrigin, PickRequest, parse_utc, utc_now
from .eligibility import EligibilityResult
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


def _main_sample_eligible(current_season_sample: object, required_games: int) -> bool:
    """Require the configured current-season sample before Main admission."""
    if required_games <= 0:
        return True
    return (
        isinstance(current_season_sample, (int, float))
        and not isinstance(current_season_sample, bool)
        and current_season_sample >= required_games
    )


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
    max_total_divergence = float(policy.get("max_total_divergence", 14.0))
    max_quote_age_seconds = float(policy.get("max_quote_age_seconds", 300.0))
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
    calibrators: dict[str, Any] = {}
    for kind_key in ("moneyline", "spread", "total"):
        mv = total_model.version if kind_key == "total" else spread_model.version
        cal_path = archive_root / "calibration" / "models" / f"{mv}.{kind_key}_calibrator.json"
        if cal_path.exists():
            try:
                calibrators[kind_key] = load_calibrator_artifact(cal_path)
            except (OSError, json.JSONDecodeError, ValueError):
                calibrators[kind_key] = IdentityCalibrator(mv)
        else:
            calibrators[kind_key] = IdentityCalibrator(mv)

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

            candidate_row = candidate_by_slug.get(str(market_def.get("market_slug") or ""))
            quote_matched = bool(candidate_row and candidate_row.get("quote_reason") == "matched")
            if quote_matched:
                quote_time = parse_utc(str(candidate_row["quote_observed_at_utc"]))
                quote_matched = max_quote_age_seconds > 0 and observed - quote_time <= timedelta(
                    seconds=max_quote_age_seconds
                )
            quoted_sides = (
                {side["selection"]: side for side in candidate_row["sides"]} if quote_matched else {}
            )
            selections = {"over", "under"} if kind == "total" else {"home", "away"}
            valid_sides = [
                side
                for side in sides
                if side.get("selection") in selections
                and isinstance(side.get("is_long"), bool)
                and (
                    (
                        (quoted := quoted_sides.get(side["selection"])) is not None
                        and quoted.get("is_long") == side["is_long"]
                        and quoted.get("line") == side.get("line")
                        and quoted.get("executable_ask") is not None
                        and 0 < float(quoted["executable_ask"]) < 1
                    )
                    or (
                        side.get("price_probability") is not None and 0 < float(side["price_probability"]) < 1
                    )
                )
            ]
            if not valid_sides:
                result["skipped"].append(
                    {"event_id": identity, "market_type": kind, "reason": "priced_side_unavailable"}
                )
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
                        prediction.probabilities[side["selection"]]
                        - float(
                            quoted_sides.get(side["selection"], {}).get("executable_ask")
                            or side["price_probability"]
                        )
                    ),
                )
            model_inst = total_model if kind == "total" else spread_model
            model_version = model_inst.version
            calibrator = calibrators.get(kind, IdentityCalibrator(model_version))

            for side in (best_side,):
                selection = side["selection"]
                candidate_side = quoted_sides.get(selection, {})
                has_executable_quote = bool(
                    candidate_side.get("executable_ask") is not None
                    and candidate_side.get("is_long") == side.get("is_long")
                    and candidate_side.get("line") == side.get("line")
                )
                ask = float(
                    candidate_side["executable_ask"] if has_executable_quote else side["price_probability"]
                )
                indicative_ask = side.get("price_probability")
                raw_probability = prediction.probabilities[selection]
                probability = calibrator.transform(raw_probability)
                edge = probability - float(ask)
                candidate_summary["serving_decision_joins"].append(
                    {
                        "event_id": str(identity),
                        "market_slug": market_def.get("market_slug"),
                        "selection": selection,
                        "indicative_ask": indicative_ask,
                        "executable_ask": ask,
                        "quote_hash": candidate_row.get("quote_hash") if has_executable_quote else None,
                        "quote_reason": "matched" if has_executable_quote else "indicative_only",
                    }
                )
                decision_line = (
                    (side.get("line") if side.get("line") is not None else line)
                    if kind != "moneyline"
                    else None
                )

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
                            "model_probability_raw": round(raw_probability, 6),
                            "history_games": len(history),
                            "history_scores_sha256": history_digest,
                            "history_cutoff_date": args_date,
                            "research_quote_join": {
                                "status": "matched" if has_executable_quote else "indicative_only",
                                "market_slug": market_def.get("market_slug"),
                                "quote_hash": candidate_row.get("quote_hash")
                                if has_executable_quote
                                else None,
                                "quote_archive_path": candidate_row.get("quote_archive_path")
                                if has_executable_quote
                                else None,
                                "quote_observed_at_utc": candidate_row.get("quote_observed_at_utc")
                                if has_executable_quote
                                else None,
                                "executable_ask": ask if has_executable_quote else None,
                                "indicative_ask": indicative_ask,
                            },
                        },
                    },
                    sport="NCAAB",
                    selection=selection,
                    line=decision_line,
                    probability=float(probability),
                    observed_at_utc=observed.isoformat(),
                )
                cal_meta = getattr(calibrator, "metadata", None)
                cal_method = cal_meta.calibration_method if cal_meta else "identity"
                cal_version = cal_meta.calibration_version if cal_meta else "identity-v1"
                cal_hash = cal_meta.artifact_hash if cal_meta else ""

                request = PickRequest(
                    event_start_utc=str(start),
                    event_id=str(identity),
                    league=League.NCAAB,
                    away_team=away,
                    home_team=home,
                    market_type=MarketType(kind),
                    selection=selection,
                    line=decision_line,
                    sportsbook="polymarket_us_executable_ask"
                    if has_executable_quote
                    else "polymarket_us_indicative",
                    american_odds=probability_to_american(float(ask)),
                    model_probability=probability,
                    model_uncertainty=prediction.uncertainty,
                    model_version=model_version,
                    rationale=prediction.rationale,
                    risks=(
                        "Paper model; executable quote is a predecision snapshot, not a guaranteed fill."
                        if has_executable_quote
                        else "Paper model; indicative market price, no executable quote."
                    ),
                    model_origin=ModelOrigin.STATISTICAL_MODEL,
                    model_enabled=True,
                    calibration_method=cal_method,
                    calibration_version=cal_version,
                    calibration_artifact_hash=cal_hash,
                    observed_at_utc=observed.isoformat(),
                    market_quote_observed_at_utc=candidate_row.get("quote_observed_at_utc")
                    if has_executable_quote
                    else None,
                    market_quote_timestamp_valid=has_executable_quote,
                    market_quote_source="polymarket_us",
                    market_quote_provenance="archived_two_sided_bbo"
                    if has_executable_quote
                    else "indicative_slate",
                    market_quote_reconstructed=False,
                    market_snapshot_hash=candidate_row.get("quote_hash") if has_executable_quote else None,
                    market_snapshot_archive_path=candidate_row.get("quote_archive_path")
                    if has_executable_quote
                    else None,
                    model_probability_raw=raw_probability,
                    market_probability_at_decision=ask,
                    serving_probability=probability,
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
                    False,
                    "CALL",
                    "PAPER_CALL_MODEL_UNVALIDATED",
                    dynamic_units,
                    round(probability * 100),
                    edge,
                    edge,
                    *teams,
                )
                if flat_ledger is not None:
                    try:
                        flat_ledger.append_evaluated(request, eligibility, now=observed)
                        result["flat_logged"] += 1
                    except DuplicatePickError:
                        pass
                    except (ValueError, OSError) as exc:
                        result["errors"].append(
                            {"event_id": identity, "ledger": "flat_logged", "reason": str(exc)}
                        )

                qualified = has_executable_quote and edge >= min_edge
                if kind == "moneyline" and ask < min_moneyline_ask:
                    qualified = False
                if kind == "spread":
                    exp_margin = (getattr(prediction, "inference_inputs", {}) or {}).get("mean")
                    if decision_line is not None and abs(float(decision_line)) > max_spread_line:
                        qualified = False
                    if (
                        exp_margin is not None
                        and decision_line is not None
                        and abs(float(exp_margin) - float(decision_line)) > max_margin_divergence
                    ):
                        qualified = False
                if kind == "total":
                    exp_tot = (getattr(prediction, "inference_inputs", {}) or {}).get("mean")
                    if (
                        exp_tot is not None
                        and decision_line is not None
                        and abs(float(exp_tot) - float(decision_line)) > max_total_divergence
                    ):
                        qualified = False
                # The configured history floor also applies to the current season
                # before a probability can enter Main. Lifetime games only establish
                # team identity; they do not validate a season-opening estimate.
                current_season_sample = (getattr(prediction, "feature_basis", None) or {}).get(
                    "current_season_sample"
                )
                main_sample_eligible = _main_sample_eligible(current_season_sample, min_team_games)
                if kind != "total" and qualified and not main_sample_eligible:
                    result["skipped"].append(
                        {
                            "event_id": identity,
                            "market_type": kind,
                            "reason": "insufficient_current_season_sample_for_main",
                            "current_season_sample": current_season_sample,
                            "required_games": min_team_games,
                        }
                    )
                destinations = (
                    ("research_ledger_logged", research_ledger, qualified),
                    ("gated_ledger_logged", gated_ledger, qualified),
                    (
                        "main_logged",
                        main_ledger,
                        qualified and kind != "total" and main_sample_eligible,
                    ),
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
