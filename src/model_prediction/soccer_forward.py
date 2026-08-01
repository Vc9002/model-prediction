"""Point-in-time soccer totals research against executable Polymarket BBOs.

The legacy learned soccer artifact is binary home-vs-not-home and therefore
cannot represent draws. Daily soccer research uses the existing Poisson/Dixon-
Coles score model instead and prices only the full-game 2.5-goal total that the
model natively supports. It remains research-only until separately validated.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .domain import parse_utc
from .features.base import FeatureStore
from .models.soccer import UpcomingMatch, soccer_model

_PARTIAL_MARKERS = ("-fh-", "-h1-", "-h2-", "-1h-", "-2h-")
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
    return re.findall(r"[a-z0-9]+", value.casefold())


def _team_matches_title(team: str, title: str) -> bool:
    team_words = _words(team)
    title_words = set(_words(title))
    if not team_words or not title_words:
        return False
    if " ".join(team_words) in " ".join(_words(title)):
        return True
    distinctive = [
        word
        for word in team_words
        if len(word) >= 3 and word not in _GENERIC_TEAM_WORDS
    ]
    return bool(distinctive) and all(word in title_words for word in distinctive)


def _latest_total_snapshots(
    data_root: str | Path,
    game_date: str,
) -> list[dict[str, Any]]:
    path = (
        Path(data_root)
        / "odds"
        / "soccer"
        / game_date
        / "polymarket_snapshots.jsonl"
    )
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
                or line_value != 2.5
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
    path = (
        Path(data_root)
        / "odds"
        / "soccer"
        / game_date
        / "polymarket_snapshots.jsonl"
    )
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
    path = (
        Path(data_root)
        / "odds"
        / "soccer"
        / game_date
        / "polymarket_snapshots.jsonl"
    )
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
                row.get("market_type") != "team_win"
                or not row.get("team")
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
) -> dict[str, Any]:
    store = FeatureStore(data_root)
    history = store.games_before("soccer", game_date)
    events: list[dict[str, Any]] = []
    for league in leagues:
        events.extend(client.scoreboard(league, game_date).get("events", []))

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
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            skipped.append({"event_id": event_id, "reason": str(error)})

    model = soccer_model()
    all_predictions = model.predict_games(history, upcoming)
    totals = [
        prediction
        for prediction in all_predictions
        if str(prediction.market_type) == "total" and prediction.line == 2.5
    ]
    moneylines = [
        prediction
        for prediction in all_predictions
        if str(prediction.market_type) == "moneyline"
    ]
    btts_predictions = [
        prediction
        for prediction in all_predictions
        if str(prediction.market_type) == "btts"
    ]
    snapshots = _latest_total_snapshots(data_root, game_date)
    moneyline_snapshots = _latest_moneyline_snapshots(data_root, game_date)
    btts_snapshots = _latest_btts_snapshots(data_root, game_date)
    priced: list[dict[str, Any]] = []
    unmatched: list[dict[str, str]] = []
    for prediction in totals:
        start = parse_utc(prediction.event_start_utc)
        candidates = []
        for snapshot in snapshots:
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
                    "reason": (
                        "no unique timestamp-valid full-game 2.5 total matched"
                    ),
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
                "event_start_utc": prediction.event_start_utc,
                "away_team": prediction.away_team,
                "home_team": prediction.home_team,
                "market_type": "total",
                "selection": selection,
                "line": 2.5,
                "model_probability": prediction.probabilities[selection],
                "model_uncertainty": prediction.uncertainty,
                "model_version": prediction.model_version,
                "feature_basis": prediction.feature_basis,
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
        # Draws aren't tradeable as a distinct outcome across two separate
        # per-team markets, so this compares only the two win probabilities,
        # then finds whichever matched team_win market concerns the
        # model-favored team -- matching by team name, not market ordering.
        # Same policy as every other sport: log the model's actual
        # prediction; the min_edge gate downstream decides whether that
        # call is also good enough value to become a real pick.
        selection = max(("home", "away"), key=lambda side: prediction.probabilities[side])
        selected_team = prediction.home_team if selection == "home" else prediction.away_team
        opponent_team = prediction.away_team if selection == "home" else prediction.home_team
        matching = [
            snapshot
            for snapshot in candidates
            if _team_matches_title(selected_team, str(snapshot.get("team") or ""))
            # A same-city/shared-mascot derby pair (e.g. "AC Milan" vs "Inter
            # Milan", or a short club prefix too short to survive
            # _team_matches_title's word-length filter) can both fuzzy-match
            # the same snapshot title. If the OPPONENT also matches this
            # snapshot, it's ambiguous or actually belongs to the opponent --
            # never guess; exclude it rather than risk pricing the wrong
            # team's contract.
            and not _team_matches_title(opponent_team, str(snapshot.get("team") or ""))
        ]
        if len(matching) != 1:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "no unique team_win market for the model-favored team",
                }
            )
            continue
        snapshot = matching[0]
        ask = (snapshot.get("long") or {}).get("ask")
        if ask is None or not 0 < float(ask) < 1:
            unmatched.append(
                {
                    "event_id": prediction.event_id,
                    "reason": "selected team_win side has no executable ask",
                }
            )
            continue
        priced.append(
            {
                "event_id": prediction.event_id,
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
            "full-game 2.5 totals; research-only pending locked validation."
        ),
    }
