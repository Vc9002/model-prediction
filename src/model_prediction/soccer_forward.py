"""Point-in-time soccer totals research against executable Polymarket BBOs.

The legacy learned soccer artifact is binary home-vs-not-home and therefore
cannot represent draws. Daily soccer research uses the existing Poisson/Dixon-
Coles score model instead and prices only the full-game 2.5-goal total that the
model natively supports. It remains research-only until separately validated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .domain import parse_utc
from .features.base import FeatureStore
from .models.soccer import UpcomingMatch, soccer_model

_PARTIAL_MARKERS = ("-fh-", "-sh-", "-h1-", "-h2-", "-1h-", "-2h-")
# Corporate-suffix words only -- never a word that legitimately distinguishes
# two real clubs. "city"/"united" were removed 2026-07-31: stripping them
# collapsed same-city derby pairs onto the same "distinctive" token (e.g.
# "Manchester United" -> {"manchester"} and "Manchester City" -> {"manchester"},
# a false match; same for "AC Milan" vs "Inter Milan" via generic "club"-style
# treatment of short words). When both that team's own market snapshot AND its
# derby rival's snapshot are present for one event, the resulting ambiguity is
# still caught downstream (len(matching) != 1); the real danger was the
# common case where only ONE side's snapshot is present, which silently
# priced the wrong team's contract.
_GENERIC_TEAM_WORDS = {"fc", "sc", "cf", "club"}


def _teams(event: dict[str, Any]) -> tuple[str, str]:
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    by_side = {str(item.get("homeAway")): item for item in competitors}
    return (
        str(by_side["away"]["team"]["displayName"]),
        str(by_side["home"]["team"]["displayName"]),
    )


def _words(value: str) -> list[str]:
    clean = str(value or "").replace(chr(0xFFFD), "e")
    clean = unicodedata.normalize("NFKD", clean).encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[a-z0-9]+", clean.casefold())


def _team_matches_title(team: str, title: str) -> bool:
    team_words = _words(team)
    title_words = set(_words(title))
    if not team_words or not title_words:
        return False
    if " ".join(team_words) in " ".join(_words(title)):
        return True
    distinctive = [word for word in team_words if len(word) >= 3 and word not in _GENERIC_TEAM_WORDS]
    return bool(distinctive) and all(word in title_words for word in distinctive)


def _latest_total_snapshots(
    data_root: str | Path,
    game_date: str,
) -> list[dict[str, Any]]:
    path = Path(data_root) / "odds" / "soccer" / game_date / "polymarket_snapshots.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                line_value = float(row.get("line"))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            slug = str(row.get("market_slug") or "")
            if (
                row.get("market_type") != "total"
                or line_value not in {0.5, 1.5, 2.5, 3.5, 4.5}
                or any(marker in slug.casefold() for marker in _PARTIAL_MARKERS)
                or not bool(row.get("timestamp_valid", False))
                or not row.get("event_title")
            ):
                continue
            if slug not in latest or str(row.get("observed_at_utc") or "") > str(
                latest[slug].get("observed_at_utc") or ""
            ):
                latest[slug] = row
    return list(latest.values())


def _latest_spread_snapshots(
    data_root: str | Path,
    game_date: str,
) -> list[dict[str, Any]]:
    path = Path(data_root) / "odds" / "soccer" / game_date / "polymarket_snapshots.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            slug = str(row.get("market_slug") or "")
            if (
                row.get("market_type") != "spread"
                or any(marker in slug.casefold() for marker in _PARTIAL_MARKERS)
                or not bool(row.get("timestamp_valid", False))
                or not row.get("event_title")
            ):
                continue
            if slug not in latest or str(row.get("observed_at_utc") or "") > str(
                latest[slug].get("observed_at_utc") or ""
            ):
                latest[slug] = row
    return list(latest.values())


def _latest_btts_snapshots(
    data_root: str | Path,
    game_date: str,
) -> list[dict[str, Any]]:
    """Same freshness/dedup logic as `_latest_total_snapshots`, for a BTTS
    (both teams to score) Yes/No market.

    Unlike team_win, a BTTS market (if one exists) is a single combined
    Yes/No contract per event, same shape as totals -- so this mirrors
    `_latest_total_snapshots` exactly, just without a `line` field to check.

    IMPORTANT: `market_type == "btts"` here is NOT yet a real, verified
    classification. Checked live 2026-07-31 across all 19 configured soccer
    leagues and across 4 real days of already-captured snapshots
    (2026-07-25, 07-27, 07-29, 07-30): Polymarket has never listed a BTTS
    market for soccer at all -- only spread/total/team_win have ever been
    seen. `data_sources/polymarket_us.py`'s `MARKET_TYPES` therefore has no
    BTTS entry (unlike team_win, which WAS added only after being verified
    live) -- adding a guessed raw type string there would be fabrication,
    not wiring. This function exists so pricing activates automatically,
    with zero further code changes, the moment a real BTTS market appears
    and someone adds its confirmed raw type string to MARKET_TYPES. Until
    then this always returns [] (no BTTS rows in the snapshot file, since
    none can ever be classified as "btts" yet).
    """
    path = Path(data_root) / "odds" / "soccer" / game_date / "polymarket_snapshots.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            slug = str(row.get("market_slug") or "")
            if (
                row.get("market_type") != "btts"
                or any(marker in slug.casefold() for marker in _PARTIAL_MARKERS)
                or not bool(row.get("timestamp_valid", False))
                or not row.get("event_title")
            ):
                continue
            if slug not in latest or str(row.get("observed_at_utc") or "") > str(
                latest[slug].get("observed_at_utc") or ""
            ):
                latest[slug] = row
    return list(latest.values())


def _latest_moneyline_snapshots(
    data_root: str | Path,
    game_date: str,
    *,
    include_draw: bool = False,
) -> list[dict[str, Any]]:
    """Same freshness/dedup logic as `_latest_total_snapshots`, but for
    soccer win markets.

    Unlike moneyline for other sports (one combined market with two named
    sides), Polymarket prices soccer full-time-result as three SEPARATE
    Yes/No markets per event -- "will <team> win?" for each side, plus a
    draw market -- each its own market_slug, classified as `team_win`
    (verified live 2026-07-27 against the raw gateway payload; the
    classifier previously didn't recognize this type at all, so these
    markets never reached pricing). Each captured snapshot carries a
    `team` field naming which team (or None, for the draw market) that
    specific snapshot's Yes side concerns.
    """
    path = Path(data_root) / "odds" / "soccer" / game_date / "polymarket_snapshots.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            slug = str(row.get("market_slug") or "")
            is_draw = "-draw" in slug.casefold() or slug.casefold().endswith("-draw") or not row.get("team")
            if (
                row.get("market_type") != "team_win"
                or (not include_draw and is_draw)
                or any(marker in slug.casefold() for marker in _PARTIAL_MARKERS)
                or not bool(row.get("timestamp_valid", False))
                or not row.get("event_title")
            ):
                continue
            if slug not in latest or str(row.get("observed_at_utc") or "") > str(
                latest[slug].get("observed_at_utc") or ""
            ):
                latest[slug] = row
    return list(latest.values())


def _latest_draw_snapshots(
    data_root: str | Path,
    game_date: str,
) -> list[dict[str, Any]]:
    """Same freshness/dedup logic as `_latest_total_snapshots`, but for
    soccer draw markets.

    Polymarket prices soccer draws as distinct Yes/No binary markets
    (classified with market_type == 'team_win' and market_slug ending with '-draw',
    or team is None/empty). Each snapshot carries the draw market's executable
    ask in the `long` side (Yes).
    """
    path = Path(data_root) / "odds" / "soccer" / game_date / "polymarket_snapshots.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            slug = str(row.get("market_slug") or "")
            is_draw = "-draw" in slug.casefold() or slug.casefold().endswith("-draw") or not row.get("team")
            if (
                row.get("market_type") != "team_win"
                or not is_draw
                or any(marker in slug.casefold() for marker in _PARTIAL_MARKERS)
                or not bool(row.get("timestamp_valid", False))
                or not row.get("event_title")
            ):
                continue
            if slug not in latest or str(row.get("observed_at_utc") or "") > str(
                latest[slug].get("observed_at_utc") or ""
            ):
                latest[slug] = row
    return list(latest.values())


def build_soccer_total_slate(
    *,
    data_root: str | Path,
    game_date: str,
    client: Any,
    leagues: tuple[str, ...],
    observed_at: datetime,
    include_draw: bool = False,
    include_double_chance: bool = False,
    model: Any = None,
    min_team_games: int = 5,
) -> dict[str, Any]:
    store = FeatureStore(data_root)
    history = store.games_before("soccer", game_date)
    events: list[dict[str, Any]] = []
    # One ESPN scoreboard call per league (soccer spans ~18 leagues in
    # config/model.yaml's SOCCER.leagues) -- independent reads, fetch
    # concurrently instead of one league at a time.
    if leagues:

        def _fetch_league_events(league_name: str) -> list[dict[str, Any]]:
            try:
                sb = client.scoreboard(league_name, game_date)
                evs = sb.get("events", [])
                for ev in evs:
                    if "league" not in ev:
                        ev["league"] = league_name
                return evs
            except Exception as exc:  # noqa: BLE001 -- external transport boundary
                logger.warning(
                    "Failed to fetch soccer scoreboard for %s on %s: %s",
                    league_name,
                    game_date,
                    exc,
                )
                return []

        with ThreadPoolExecutor(max_workers=min(32, len(leagues))) as pool:
            for ev_list in pool.map(_fetch_league_events, leagues):
                events.extend(ev_list)
    # Some competitions (e.g. a continental/cup fixture also listed under a
    # domestic league endpoint) return the SAME event from more than one of
    # the leagues above -- deduped by event_id, same as tennis_forward.py's
    # combined ATP+WTA tournament handling. Without this, a real duplicate
    # priced_contracts row (same event_id/market/selection, two separate
    # pick_ids) reached flat_picks.xlsx for every affected match (found
    # 2026-08-03 via 5 real duplicate pairs in production).
    seen_event_ids: set[str] = set()
    deduped_events: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event.get("id", ""))
        if event_id and event_id in seen_event_ids:
            continue
        if event_id:
            seen_event_ids.add(event_id)
        deduped_events.append(event)
    events = deduped_events

    upcoming: list[UpcomingMatch] = []
    skipped: list[dict[str, str]] = []
    for event in events:
        event_id = str(event.get("id", ""))
        try:
            start = parse_utc(str(event["date"]))
            if start <= observed_at:
                raise ValueError("event_started")
            away, home = _teams(event)
            upcoming.append(
                UpcomingMatch(
                    event_id=event_id,
                    event_start_utc=str(event["date"]),
                    away_team=away,
                    home_team=home,
                    league=str(event.get("league") or "SOCCER"),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            skipped.append({"event_id": event_id, "reason": str(error)})

    if model is None:
        model = soccer_model()
    all_predictions = model.predict_games(history, upcoming)
    skipped.extend(getattr(model, "skipped_predictions", []))
    totals = [prediction for prediction in all_predictions if str(prediction.market_type) == "total"]
    spreads = [prediction for prediction in all_predictions if str(prediction.market_type) == "spread"]
    moneylines = [prediction for prediction in all_predictions if str(prediction.market_type) == "moneyline"]
    btts_predictions = [prediction for prediction in all_predictions if str(prediction.market_type) == "btts"]
    snapshots = _latest_total_snapshots(data_root, game_date)
    spread_snapshots = _latest_spread_snapshots(data_root, game_date)
    moneyline_snapshots = _latest_moneyline_snapshots(data_root, game_date)
    btts_snapshots = _latest_btts_snapshots(data_root, game_date)
    priced: list[dict[str, Any]] = []
    unmatched: list[dict[str, str]] = []
    for prediction in totals:
        basis = prediction.feature_basis or {}
        min_g = float(basis.get("min_team_games") or 0.0)
        if min_g < min_team_games:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": f"insufficient_history: min_team_games {min_g:.0f} < {min_team_games}",
                }
            )
            continue
        start = parse_utc(prediction.event_start_utc)
        candidates = []
        for snapshot in snapshots:
            try:
                snapshot_start = parse_utc(str(snapshot["event_start_utc"]))
                snapshot_at = parse_utc(str(snapshot["observed_at_utc"]))
                snap_line = float(snapshot.get("line") or 0.0)
            except (KeyError, TypeError, ValueError):
                continue
            if (
                abs((snapshot_start - start).total_seconds()) <= 30 * 60
                and snapshot_at < start
                and abs(snap_line - float(prediction.line or 0.0)) < 1e-4
                and _team_matches_title(
                    prediction.away_team,
                    str(snapshot.get("event_title", "")),
                )
                and _team_matches_title(
                    prediction.home_team,
                    str(snapshot.get("event_title", "")),
                )
            ):
                candidates.append(snapshot)
        if len(candidates) != 1:
            if prediction.line == 2.5:
                unmatched.append(
                    {
                        "event_id": prediction.event_id,
                        "reason": ("no unique timestamp-valid full-game 2.5 total matched"),
                    }
                )
            continue
        snapshot = candidates[0]
        # Log the model's actual prediction (whichever side it thinks is
        # more likely), same policy as every other sport, e.g.
        # learned_forward.py's MLB moneyline. The min_edge gate downstream
        # decides whether that call is also good enough value to become a
        # real pick vs. a zero-unit research observation.
        selection = max(
            ("over", "under"),
            key=lambda side: prediction.probabilities[side],
        )
        side_key = "long" if selection == "over" else "short"
        side = snapshot.get(side_key) or {}
        ask = side.get("ask")
        if ask is None or not 0 < float(ask) < 1:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "selected total side has no executable ask",
                }
            )
            continue
        priced.append(
            {
                "event_id": prediction.event_id,
                "league": prediction.league,
                "event_start_utc": prediction.event_start_utc,
                "away_team": prediction.away_team,
                "home_team": prediction.home_team,
                "market_type": "total",
                "selection": selection,
                "line": prediction.line,
                "model_probability": prediction.probabilities[selection],
                "model_uncertainty": prediction.uncertainty,
                "model_version": prediction.model_version,
                "feature_basis": prediction.feature_basis,
                "inference_inputs": prediction.inference_inputs,
                "model_inputs_observed_at_utc": observed_at.isoformat(),
                "rationale": prediction.rationale,
                "market_slug": snapshot["market_slug"],
                "executable_ask": float(ask),
                "observed_at_utc": snapshot["observed_at_utc"],
                "timestamp_valid": True,
                "edge_vs_executable_ask": round(
                    prediction.probabilities[selection] - float(ask),
                    6,
                ),
            }
        )

    # Polymarket prices soccer full-time result as three separate per-team
    # Yes/No "team_win" markets (home wins / draw / away wins), not one
    # combined moneyline market -- verified live 2026-07-27. Up to two
    # candidates (home-win market, away-win market) can legitimately match
    # one event; the draw market is excluded at the loader level (no team).
    for prediction in moneylines:
        basis = prediction.feature_basis or {}
        min_g = float(basis.get("min_team_games") or 0.0)
        if min_g < min_team_games:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": f"insufficient_history: min_team_games {min_g:.0f} < {min_team_games}",
                }
            )
            continue
        start = parse_utc(prediction.event_start_utc)
        candidates = []
        for snapshot in moneyline_snapshots:
            try:
                snapshot_start = parse_utc(str(snapshot["event_start_utc"]))
                snapshot_at = parse_utc(str(snapshot["observed_at_utc"]))
            except (KeyError, TypeError, ValueError):
                continue
            if (
                abs((snapshot_start - start).total_seconds()) <= 30 * 60
                and snapshot_at < start
                and _team_matches_title(
                    prediction.away_team,
                    str(snapshot.get("event_title", "")),
                )
                and _team_matches_title(
                    prediction.home_team,
                    str(snapshot.get("event_title", "")),
                )
            ):
                candidates.append(snapshot)
        if not candidates:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "no timestamp-valid team_win market matched",
                }
            )
            continue
        # Match team_win market candidates for home and away separately and evaluate executable ask
        valid_options = []
        for side in ("home", "away", "draw"):
            if side == "draw":
                matching = [
                    s
                    for s in candidates
                    if str(s.get("team") or "").strip().lower() in ("draw", "tie")
                    or "draw" in str(s.get("market_slug") or "").lower()
                ]
            else:
                target_team = prediction.home_team if side == "home" else prediction.away_team
                opp_team = prediction.away_team if side == "home" else prediction.home_team
                matching = [
                    s
                    for s in candidates
                    if _team_matches_title(target_team, str(s.get("team") or ""))
                    and not _team_matches_title(opp_team, str(s.get("team") or ""))
                ]
            if len(matching) == 1:
                snap = matching[0]
                ask_val = (snap.get("long") or {}).get("ask")
                if ask_val is not None and 0 < float(ask_val) < 1:
                    side_prob = float(prediction.probabilities.get(side, 0.0))
                    side_edge = side_prob - float(ask_val)
                    valid_options.append(
                        {
                            "side": side,
                            "snapshot": snap,
                            "ask": float(ask_val),
                            "prob": side_prob,
                            "edge": side_edge,
                        }
                    )

        if not valid_options:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "no unique team_win market with executable ask",
                }
            )
            continue

        # Value-based edge selection: choose the side that offers the highest expected value (edge)
        # over its executable market ask, breaking ties by model probability.
        best_candidate = max(valid_options, key=lambda opt: (opt["edge"], opt["prob"]))
        selection = str(best_candidate["side"])
        cand_snapshot = best_candidate["snapshot"]
        assert isinstance(cand_snapshot, dict)
        ask = float(str(best_candidate["ask"]))

        # Cross-league underdog protection for continental cups:
        # Avoid uncalibrated longshots where team strength comes from lower-tier domestic play
        if prediction.league in {"UEL", "UECL", "UCL", "LIBERTADORES", "SUDAMERICANA"} and ask < 0.25:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": f"continental_cup_underdog_uncalibrated: ask {ask:.2f} < 0.25",
                }
            )
            continue

        priced.append(
            {
                "event_id": prediction.event_id,
                "league": prediction.league,
                "event_start_utc": prediction.event_start_utc,
                "away_team": prediction.away_team,
                "home_team": prediction.home_team,
                "market_type": "moneyline",
                "selection": selection,
                "line": None,
                "model_probability": prediction.probabilities[selection],
                "model_uncertainty": prediction.uncertainty,
                "model_version": prediction.model_version,
                "feature_basis": prediction.feature_basis,
                "inference_inputs": prediction.inference_inputs,
                "model_inputs_observed_at_utc": observed_at.isoformat(),
                "rationale": prediction.rationale,
                "market_slug": cand_snapshot["market_slug"],
                "executable_ask": float(ask),
                "observed_at_utc": cand_snapshot["observed_at_utc"],
                "timestamp_valid": True,
                "edge_vs_executable_ask": round(
                    prediction.probabilities[selection] - float(ask),
                    6,
                ),
            }
        )

    if include_draw:
        draw_snapshots = _latest_draw_snapshots(data_root, game_date)
        for prediction in moneylines:
            basis = prediction.feature_basis or {}
            min_g = float(basis.get("min_team_games") or 0.0)
            if min_g < min_team_games:
                continue
            start = parse_utc(prediction.event_start_utc)
            draw_candidates = []
            for snapshot in draw_snapshots:
                try:
                    snapshot_start = parse_utc(str(snapshot["event_start_utc"]))
                    snapshot_at = parse_utc(str(snapshot["observed_at_utc"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if (
                    abs((snapshot_start - start).total_seconds()) <= 30 * 60
                    and snapshot_at < start
                    and _team_matches_title(
                        prediction.away_team,
                        str(snapshot.get("event_title", "")),
                    )
                    and _team_matches_title(
                        prediction.home_team,
                        str(snapshot.get("event_title", "")),
                    )
                ):
                    draw_candidates.append(snapshot)
            if len(draw_candidates) != 1:
                unmatched.append(
                    {
                        "event_id": prediction.event_id,
                        "reason": "no unique timestamp-valid draw market matched",
                    }
                )
                continue
            snapshot = draw_candidates[0]
            ask = (snapshot.get("long") or {}).get("ask")
            if ask is None or not 0 < float(ask) < 1:
                unmatched.append(
                    {
                        "event_id": prediction.event_id,
                        "reason": "draw side has no executable ask",
                    }
                )
                continue

            if (
                prediction.league in {"UEL", "UECL", "UCL", "LIBERTADORES", "SUDAMERICANA"}
                and float(ask) < 0.20
            ):
                unmatched.append(
                    {
                        "event_id": prediction.event_id,
                        "reason": f"continental_cup_draw_uncalibrated: ask {float(ask):.2f} < 0.20",
                    }
                )
                continue

            draw_prob = float(prediction.probabilities.get("draw", 0.0))
            priced.append(
                {
                    "event_id": prediction.event_id,
                    "league": prediction.league,
                    "event_start_utc": prediction.event_start_utc,
                    "away_team": prediction.away_team,
                    "home_team": prediction.home_team,
                    "market_type": "moneyline",
                    "selection": "draw",
                    "line": None,
                    "model_probability": draw_prob,
                    "model_uncertainty": prediction.uncertainty,
                    "model_version": prediction.model_version,
                    "feature_basis": prediction.feature_basis,
                    "inference_inputs": prediction.inference_inputs,
                    "model_inputs_observed_at_utc": observed_at.isoformat(),
                    "rationale": f"Poisson-DC modeled draw probability: {draw_prob:.4f}.",
                    "market_slug": snapshot["market_slug"],
                    "executable_ask": float(ask),
                    "observed_at_utc": snapshot["observed_at_utc"],
                    "timestamp_valid": True,
                    "edge_vs_executable_ask": round(
                        draw_prob - float(ask),
                        6,
                    ),
                }
            )

    if include_double_chance:
        draw_snaps_dc = _latest_draw_snapshots(data_root, game_date)
        for prediction in moneylines:
            basis = prediction.feature_basis or {}
            min_g = float(basis.get("min_team_games") or 0.0)
            if min_g < min_team_games:
                continue
            start = parse_utc(prediction.event_start_utc)
            match_candidates = [
                s
                for s in moneyline_snapshots
                if _team_matches_title(prediction.away_team, str(s.get("event_title", "")))
                and _team_matches_title(prediction.home_team, str(s.get("event_title", "")))
            ]
            home_matches = [
                s
                for s in match_candidates
                if _team_matches_title(prediction.home_team, str(s.get("team") or ""))
                and not _team_matches_title(prediction.away_team, str(s.get("team") or ""))
            ]
            away_matches = [
                s
                for s in match_candidates
                if _team_matches_title(prediction.away_team, str(s.get("team") or ""))
                and not _team_matches_title(prediction.home_team, str(s.get("team") or ""))
            ]
            draw_matches = [
                s
                for s in draw_snaps_dc
                if _team_matches_title(prediction.away_team, str(s.get("event_title", "")))
                and _team_matches_title(prediction.home_team, str(s.get("event_title", "")))
            ]
            h_snap = home_matches[0] if len(home_matches) == 1 else None
            a_snap = away_matches[0] if len(away_matches) == 1 else None
            d_snap = draw_matches[0] if len(draw_matches) == 1 else None

            p_h = float(prediction.probabilities.get("home", 0.0))
            p_d = float(prediction.probabilities.get("draw", 0.0))
            p_a = float(prediction.probabilities.get("away", 0.0))

            # 1X (Home or Draw): Single NO on away or Bundle YES on home + YES on draw
            ask_1x_no = (a_snap.get("short") or {}).get("ask") if a_snap else None
            ask_h_yes = (h_snap.get("long") or {}).get("ask") if h_snap else None
            ask_d_yes = (d_snap.get("long") or {}).get("ask") if d_snap else None
            ask_1x_bundle = (float(ask_h_yes) + float(ask_d_yes)) if (ask_h_yes and ask_d_yes) else None
            options_1x = []
            if ask_1x_no and 0 < float(ask_1x_no) < 1 and a_snap is not None:
                options_1x.append((float(ask_1x_no), str(a_snap["market_slug"]), "no_away"))
            if ask_1x_bundle and 0 < float(ask_1x_bundle) < 1 and h_snap is not None and d_snap is not None:
                options_1x.append(
                    (
                        float(ask_1x_bundle),
                        f"bundle:{h_snap['market_slug']}+{d_snap['market_slug']}",
                        "bundle_1x",
                    )
                )
            if options_1x:
                best_ask_1x, slug_1x, route_1x = min(options_1x, key=lambda x: x[0])
                # Skip uncalibrated deep underdogs in continental competitions
                if not (
                    prediction.league in {"UEL", "UECL", "UCL", "LIBERTADORES", "SUDAMERICANA"}
                    and best_ask_1x < 0.25
                ):
                    prob_1x = round(p_h + p_d, 6)
                    priced.append(
                        {
                            "event_id": prediction.event_id,
                            "league": prediction.league,
                            "event_start_utc": prediction.event_start_utc,
                            "away_team": prediction.away_team,
                            "home_team": prediction.home_team,
                            "market_type": "double_chance",
                            "selection": "1X",
                            "line": None,
                            "model_probability": prob_1x,
                            "model_uncertainty": prediction.uncertainty,
                            "model_version": prediction.model_version,
                            "feature_basis": prediction.feature_basis,
                            "inference_inputs": prediction.inference_inputs,
                            "model_inputs_observed_at_utc": observed_at.isoformat(),
                            "rationale": f"Double Chance 1X (Home/Draw) via {route_1x} ask {best_ask_1x:.4f}.",
                            "market_slug": slug_1x,
                            "executable_ask": float(best_ask_1x),
                            "observed_at_utc": observed_at.isoformat(),
                            "timestamp_valid": True,
                            "edge_vs_executable_ask": round(prob_1x - float(best_ask_1x), 6),
                        }
                    )

            # X2 (Away or Draw): Single NO on home or Bundle YES on away + YES on draw
            ask_x2_no = (h_snap.get("short") or {}).get("ask") if h_snap else None
            ask_a_yes = (a_snap.get("long") or {}).get("ask") if a_snap else None
            ask_x2_bundle = (float(ask_a_yes) + float(ask_d_yes)) if (ask_a_yes and ask_d_yes) else None
            options_x2 = []
            if ask_x2_no and 0 < float(ask_x2_no) < 1 and h_snap is not None:
                options_x2.append((float(ask_x2_no), str(h_snap["market_slug"]), "no_home"))
            if ask_x2_bundle and 0 < float(ask_x2_bundle) < 1 and a_snap is not None and d_snap is not None:
                options_x2.append(
                    (
                        float(ask_x2_bundle),
                        f"bundle:{a_snap['market_slug']}+{d_snap['market_slug']}",
                        "bundle_x2",
                    )
                )
            if options_x2:
                best_ask_x2, slug_x2, route_x2 = min(options_x2, key=lambda x: x[0])
                # Skip uncalibrated deep underdogs in continental competitions
                if not (
                    prediction.league in {"UEL", "UECL", "UCL", "LIBERTADORES", "SUDAMERICANA"}
                    and best_ask_x2 < 0.25
                ):
                    prob_x2 = round(p_a + p_d, 6)
                    priced.append(
                        {
                            "event_id": prediction.event_id,
                            "league": prediction.league,
                            "event_start_utc": prediction.event_start_utc,
                            "away_team": prediction.away_team,
                            "home_team": prediction.home_team,
                            "market_type": "double_chance",
                            "selection": "X2",
                            "line": None,
                            "model_probability": prob_x2,
                            "model_uncertainty": prediction.uncertainty,
                            "model_version": prediction.model_version,
                            "feature_basis": prediction.feature_basis,
                            "inference_inputs": prediction.inference_inputs,
                            "model_inputs_observed_at_utc": observed_at.isoformat(),
                            "rationale": f"Double Chance X2 (Away/Draw) via {route_x2} ask {best_ask_x2:.4f}.",
                            "market_slug": slug_x2,
                            "executable_ask": float(best_ask_x2),
                            "observed_at_utc": observed_at.isoformat(),
                            "timestamp_valid": True,
                            "edge_vs_executable_ask": round(prob_x2 - float(best_ask_x2), 6),
                        }
                    )

    # BTTS: same complementary-sides shape as totals (one snapshot, long=yes/
    # short=no), so matching mirrors that loop exactly. In practice this will
    # currently always report "no unique timestamp-valid BTTS market matched"
    # -- honest, not a bug, since no BTTS market has ever been observed live
    # (see _latest_btts_snapshots's docstring). Kept in `priced` alongside
    # totals/moneyline so it activates automatically, with no further code
    # changes, once Polymarket ever lists one and its raw type string is
    # confirmed and added to MARKET_TYPES.
    for prediction in btts_predictions:
        start = parse_utc(prediction.event_start_utc)
        candidates = []
        for snapshot in btts_snapshots:
            try:
                snapshot_start = parse_utc(str(snapshot["event_start_utc"]))
                snapshot_at = parse_utc(str(snapshot["observed_at_utc"]))
            except (KeyError, TypeError, ValueError):
                continue
            if (
                abs((snapshot_start - start).total_seconds()) <= 30 * 60
                and snapshot_at < start
                and _team_matches_title(
                    prediction.away_team,
                    str(snapshot.get("event_title", "")),
                )
                and _team_matches_title(
                    prediction.home_team,
                    str(snapshot.get("event_title", "")),
                )
            ):
                candidates.append(snapshot)
        if len(candidates) != 1:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "no unique timestamp-valid BTTS market matched",
                }
            )
            continue
        snapshot = candidates[0]
        selection = max(("yes", "no"), key=lambda side: prediction.probabilities[side])
        side_key = "long" if selection == "yes" else "short"
        side = snapshot.get(side_key) or {}
        ask = side.get("ask")
        if ask is None or not 0 < float(ask) < 1:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "selected BTTS side has no executable ask",
                }
            )
            continue
        priced.append(
            {
                "event_id": prediction.event_id,
                "event_start_utc": prediction.event_start_utc,
                "away_team": prediction.away_team,
                "home_team": prediction.home_team,
                "market_type": "btts",
                "selection": selection,
                "line": None,
                "model_probability": prediction.probabilities[selection],
                "model_uncertainty": prediction.uncertainty,
                "model_version": prediction.model_version,
                "feature_basis": prediction.feature_basis,
                "inference_inputs": prediction.inference_inputs,
                "model_inputs_observed_at_utc": observed_at.isoformat(),
                "rationale": prediction.rationale,
                "market_slug": snapshot["market_slug"],
                "executable_ask": float(ask),
                "observed_at_utc": snapshot["observed_at_utc"],
                "timestamp_valid": True,
                "edge_vs_executable_ask": round(
                    prediction.probabilities[selection] - float(ask),
                    6,
                ),
            }
        )

    spreads_by_event: dict[str, dict[float, Any]] = {}
    for prediction in spreads:
        spreads_by_event.setdefault(prediction.event_id, {})[float(prediction.line)] = prediction
    for by_line in spreads_by_event.values():
        sample = next(iter(by_line.values()))
        min_g = float((sample.feature_basis or {}).get("min_team_games") or 0.0)
        if min_g < min_team_games:
            unmatched.append(
                {
                    "event_id": sample.event_id,
                    "reason": f"insufficient_history: min_team_games {min_g:.0f} < {min_team_games}",
                }
            )
            continue
        start = parse_utc(sample.event_start_utc)
        candidates = []
        for snapshot in spread_snapshots:
            try:
                snapshot_start = parse_utc(str(snapshot["event_start_utc"]))
                snapshot_at = parse_utc(str(snapshot["observed_at_utc"]))
                snap_line = float(snapshot["line"])
            except (KeyError, TypeError, ValueError):
                continue
            if (
                abs((snapshot_start - start).total_seconds()) <= 30 * 60
                and snapshot_at < start
                and _team_matches_title(sample.away_team, str(snapshot.get("event_title", "")))
                and _team_matches_title(sample.home_team, str(snapshot.get("event_title", "")))
                and any(abs(snap_line - line) < 1e-4 or abs(snap_line + line) < 1e-4 for line in by_line)
            ):
                candidates.append(snapshot)
        if not candidates:
            continue

        def _imbalance(snapshot: dict[str, Any]) -> float:
            asks = [float((snapshot.get(k) or {}).get("ask") or 0.0) for k in ("long", "short")]
            return max((abs(a - 0.5) for a in asks if 0 < a < 1), default=1.0)

        # Main line only: alternate handicaps repeat one opinion.
        snapshot = min(candidates, key=_imbalance)
        snap_line = float(snapshot["line"])
        anchor = str(snapshot.get("team") or "")
        anchor_is_home = _team_matches_title(sample.home_team, anchor)
        anchor_is_away = _team_matches_title(sample.away_team, anchor)
        if anchor_is_home == anchor_is_away:
            skipped.append({"event_id": sample.event_id, "reason": "spread_anchor_team_unresolved"})
            continue
        # The market line is the anchor team's handicap and its long side backs that team at
        # the line. Model lines are home handicaps; an away side at L covers when home fails at -L.
        home_line = snap_line if anchor_is_home else -snap_line
        pred = by_line.get(home_line)
        if pred is None:
            continue
        home_cover = pred.probabilities["home"]
        long_prob = home_cover if anchor_is_home else 1.0 - home_cover
        quotes = []
        for side_key, prob, ask_key in (
            ("long", long_prob, "long"),
            ("short", 1.0 - long_prob, "short"),
        ):
            ask = (snapshot.get(ask_key) or {}).get("ask")
            if ask is None or not 0 < float(ask) < 1:
                continue
            backs_anchor = side_key == "long"
            selects_home = backs_anchor == anchor_is_home
            side_line = snap_line if backs_anchor else -snap_line
            quotes.append(
                (prob - float(ask), prob, float(ask), "home" if selects_home else "away", side_line)
            )
        if not quotes:
            continue
        edge, prob, ask, selection, side_line = max(quotes, key=lambda q: (q[0], q[1]))
        priced.append(
            {
                "event_id": pred.event_id,
                "event_start_utc": pred.event_start_utc,
                "away_team": pred.away_team,
                "home_team": pred.home_team,
                "market_type": "spread",
                "selection": selection,
                "line": side_line,
                "model_probability": prob,
                "model_uncertainty": pred.uncertainty,
                "model_version": pred.model_version,
                "feature_basis": pred.feature_basis,
                "inference_inputs": pred.inference_inputs,
                "model_inputs_observed_at_utc": observed_at.isoformat(),
                "rationale": f"Dixon-Coles handicap {selection} {side_line:+.1f}: cover p={prob:.3f}.",
                "market_slug": snapshot["market_slug"],
                "executable_ask": ask,
                "observed_at_utc": snapshot["observed_at_utc"],
                "timestamp_valid": True,
                "edge_vs_executable_ask": round(edge, 6),
            }
        )

    model_source = Path(__file__).with_name("models") / "soccer.py"
    code_hash = hashlib.sha256(model_source.read_bytes()).hexdigest()
    return {
        "sport": "soccer",
        "status": "research",
        "model_version": model.version,
        "model_code_hash": code_hash,
        "scheduled_games": len(upcoming),
        "priced_contracts": priced,
        "priced_count": len(priced),
        "unmatched": unmatched,
        "skipped": skipped,
        "note": (
            "Three-way-aware Poisson/Dixon-Coles score model priced against "
            "moneyline, totals (1.5, 2.5, 3.5), spread handicaps, and BTTS."
        ),
    }
