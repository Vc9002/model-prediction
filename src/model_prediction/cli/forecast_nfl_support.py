"""Leaf helpers split out of forecast.py (no clock/slate call sites, so no test-patch targets)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..domain import (
    EASTERN,
    parse_utc,
)
from ..pricing import implied_probability

logger = logging.getLogger("model_prediction.cli")


STANDARD_LAY_ASK = round(implied_probability(-110), 6)


def _main_call_eligible(eligibility: Any, *, edge: float, min_edge: float, max_edge: float) -> bool:
    """Main-ledger admission predicate for the NFL spread/total paths.

    A ``_downgrade_unserved()`` eligibility keeps ``decision == "CALL"`` so the
    row still logs to Flat with units, but marks it ``RESEARCH_OBSERVATION``.
    ``ledger.append_evaluated``'s Main branch then relabels whatever reaches it
    as QUALIFIED, so a Main gate that tested only ``decision`` admitted
    unqualified-input and unregistered-model research rows as QUALIFIED calls
    (21 such Main rows, 2026-11-02..09). The downgrade is only visible here,
    before the ledger, so this is where the record type must be checked.
    """
    return eligibility.decision == "CALL" and eligibility.qualified and min_edge <= edge <= max_edge


_NFL_PARTIAL_MARKET_TOKENS = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "-tt-", "-tt1h-", "-tt2h-")


def _nfl_serving_calibrator(model_version: str, market: str, data_root: str | Path) -> Any | None:
    """The fitted calibrator for (model, market) if -- and only if -- its own artifact passes
    the serving guard that calibration_pipeline.calibrate_for_serving applies for the buyer:
    a non-identity champion, at least MIN_SERVING_SAMPLE outcomes, and an out-of-fold Brier
    that beats the raw model by MIN_SERVING_BRIER_IMPROVEMENT. Anything else returns None
    and the raw engine probability is served. Returns the load_calibrator_artifact object so
    the row carries the artifact's file hash, which validate_decision_snapshot re-checks at
    request time and at settlement."""
    from ..calibration import load_calibrator_artifact
    from ..calibration_pipeline import MIN_SERVING_BRIER_IMPROVEMENT, MIN_SERVING_SAMPLE

    path = Path(data_root) / "calibration" / "models" / f"{model_version}.{market}_calibrator.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        if str(payload.get("champion_method") or "identity").lower() == "identity":
            return None
        if str(payload.get("market")) != market or str(payload.get("model_version")) != model_version:
            return None
        if int(payload.get("sample_size") or 0) < MIN_SERVING_SAMPLE:
            return None
        baseline, oof = payload.get("baseline_brier"), payload.get("calibrated_oof_brier")
        if baseline is None or oof is None or float(baseline) - float(oof) < MIN_SERVING_BRIER_IMPROVEMENT:
            return None
        return load_calibrator_artifact(path)
    except (TypeError, ValueError, KeyError, FileNotFoundError):
        return None


def _apply_serving_calibrator(
    raw_probability: float,
    calibrator: Any | None,
    *,
    default_method: str,
    model_version: str,
    model_artifact_hash: str,
) -> tuple[float, str, str, str]:
    """(served_probability, calibration_method, calibration_version, calibration_artifact_hash).

    Without a calibrator the row keeps the engine's own identity labels so the scalar
    replay knows no transform was applied. The served value is NOT clamped: the replay
    recomputes raw -> transform and compares to 2e-6, so any extra step here would make
    every calibrated pick fail validate() and vanish into `skipped`."""
    if calibrator is None:
        return raw_probability, default_method, model_version, model_artifact_hash
    meta = calibrator.metadata
    return (
        float(calibrator.transform(raw_probability)),
        meta.calibration_method,
        meta.calibration_version,
        meta.artifact_hash,
    )


def _select_nfl_spread_market(candidates: list[dict]) -> dict | None:
    """Select the most balanced spread market line for an NFL game."""
    return _select_latest_balanced_market(candidates)


def _select_nfl_total_market(candidates: list[dict]) -> dict | None:
    """Select the most balanced full-game total line for an NFL game.

    Shares the spread selector's rules. Until 2026-11-11 this picked the
    ask nearest 50% over every observation of the day (the daily job captures
    4x/day into an append-only archive), so a stale morning quote could beat
    the current one and be stamped as the decision-time ask; it also accepted
    one-sided rows, which the under branch then dropped silently.
    """
    return _select_latest_balanced_market(candidates)


def _select_latest_balanced_market(candidates: list[dict]) -> dict | None:
    """Latest two-sided quote per contract, then the line whose long ask is nearest 50%."""
    partial_tokens = _NFL_PARTIAL_MARKET_TOKENS
    latest_by_contract: dict[str, dict] = {}
    for row in candidates:
        slug = str(row.get("market_slug") or "")
        if (
            not slug
            or any(token in slug.lower() for token in partial_tokens)
            or row.get("line") is None
            or not isinstance(row.get("long"), dict)
            or not isinstance(row.get("short"), dict)
            or row["long"].get("ask") is None
            or row["short"].get("ask") is None
        ):
            continue
        try:
            long_ask = float(row["long"]["ask"])
            short_ask = float(row["short"]["ask"])
            observed = parse_utc(str(row.get("observed_at_utc") or ""))
        except (TypeError, ValueError):
            continue
        if not (0 < long_ask < 1 and 0 < short_ask < 1):
            continue
        previous = latest_by_contract.get(slug)
        if previous is None or observed > parse_utc(str(previous["observed_at_utc"])):
            latest_by_contract[slug] = row
    if not latest_by_contract:
        return None
    return min(latest_by_contract.values(), key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _nfl_is_neutral_site(event: dict) -> bool:
    """Determine whether an NFL game is played at a neutral site (international series, etc.)."""
    comp = (event.get("competitions") or [{}])[0]
    is_neutral_site = bool(comp.get("neutralSite", False))
    if not is_neutral_site:
        venue = comp.get("venue") or {}
        address = venue.get("address") or {}
        country = str(address.get("country") or "").strip().upper()
        city = str(address.get("city") or "").strip().lower()
        venue_name = str(venue.get("fullName") or "").strip().lower()
        if (
            country
            and country not in ("USA", "US", "UNITED STATES")
            or any(
                kw in city
                for kw in ("london", "munich", "frankfurt", "são paulo", "sao paulo", "mexico city")
            )
            or any(
                kw in venue_name
                for kw in (
                    "tottenham",
                    "wembley",
                    "twickenham",
                    "allianz arena",
                    "deutsche bank park",
                    "arena corinthians",
                    "estadio azteca",
                )
            )
        ):
            is_neutral_site = True
    return is_neutral_site


def _nfl_situational_features(
    history: list,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Extract point-in-time last game dates and starting QBs for NFL teams."""
    team_last_date: dict[str, Any] = {}
    team_starters: dict[str, str] = {}
    for g in history:
        try:
            g_dt = parse_utc(str(g.event_start_utc)).astimezone(EASTERN).date()
            team_last_date[g.home_team] = g_dt
            team_last_date[g.away_team] = g_dt
            if getattr(g, "home_starter_name", None) and g.home_starter_name != "Unknown":
                team_starters[g.home_team] = g.home_starter_name
            if getattr(g, "away_starter_name", None) and g.away_starter_name != "Unknown":
                team_starters[g.away_team] = g.away_starter_name
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return team_last_date, team_starters


def _nfl_inputs_fresh(
    *,
    qb_update_utc: str | None,
    last_game_date: date | None,
    injury_observed_utc: str | None,
    observed_at: datetime,
    max_age_hours: float,
) -> bool:
    """Are one team's projected-QB and injury inputs current enough to qualify a call?

    The projected QB1 comes from nflverse's depth chart, which publishes at its
    own cadence (one timestamp per week is normal), so the right test is
    ordering, not recency: it must be known at the decision time and published
    on/after the team's last game, so it reflects post-game changes. Injury
    captures run every daily cycle, so they get a plain age window.

    Replaces ``parse_utc(x).date() == observed_at.date()`` on both fields,
    which qualified or disqualified the same inputs depending on the run's
    time of day (the 00:30 ET run could never qualify; a 23:00Z run crossing
    midnight flipped mid-cycle) -- a date-granularity test on datetime data.
    """
    if not qb_update_utc or not injury_observed_utc:
        return False
    try:
        qb_update = parse_utc(str(qb_update_utc))
        injuries = parse_utc(str(injury_observed_utc))
    except (TypeError, ValueError):
        return False
    if qb_update > observed_at or injuries > observed_at:
        return False  # future-dated inputs are a PIT violation, never "fresh"
    if last_game_date is not None and qb_update.astimezone(EASTERN).date() < last_game_date:
        return False
    return (observed_at - injuries) <= timedelta(hours=max_age_hours)


def _nfl_venue_weather(event: dict) -> tuple[bool, float, float]:
    """Extract dome status, wind speed, and temperature from NFL event."""
    comp = (event.get("competitions") or [{}])[0]
    venue = comp.get("venue") or {}
    is_dome = bool(venue.get("indoor", False)) or any(
        d in str(venue.get("fullName") or "").lower()
        for d in (
            "dome",
            "mercedes-benz",
            "ford field",
            "allegiant",
            "at&t stadium",
            "lucas oil",
            "nrg stadium",
            "u.s. bank",
            "state farm",
            "sofi",
        )
    )
    weather = comp.get("weather") or {}
    wind_mph = float(weather.get("windSpeed") or (0.0 if is_dome else 5.0))
    temperature_f = float(weather.get("temperature") or (70.0 if is_dome else 65.0))
    return is_dome, wind_mph, temperature_f


NFL_DIVISIONS: dict[str, str] = {
    "Buffalo Bills": "AFC_East",
    "Miami Dolphins": "AFC_East",
    "New England Patriots": "AFC_East",
    "New York Jets": "AFC_East",
    "Baltimore Ravens": "AFC_North",
    "Cincinnati Bengals": "AFC_North",
    "Cleveland Browns": "AFC_North",
    "Pittsburgh Steelers": "AFC_North",
    "Houston Texans": "AFC_South",
    "Indianapolis Colts": "AFC_South",
    "Jacksonville Jaguars": "AFC_South",
    "Tennessee Titans": "AFC_South",
    "Denver Broncos": "AFC_West",
    "Kansas City Chiefs": "AFC_West",
    "Las Vegas Raiders": "AFC_West",
    "Los Angeles Chargers": "AFC_West",
    "Dallas Cowboys": "NFC_East",
    "New York Giants": "NFC_East",
    "Philadelphia Eagles": "NFC_East",
    "Washington Commanders": "NFC_East",
    "Chicago Bears": "NFC_North",
    "Detroit Lions": "NFC_North",
    "Green Bay Packers": "NFC_North",
    "Minnesota Vikings": "NFC_North",
    "Atlanta Falcons": "NFC_South",
    "Carolina Panthers": "NFC_South",
    "New Orleans Saints": "NFC_South",
    "Tampa Bay Buccaneers": "NFC_South",
    "Arizona Cardinals": "NFC_West",
    "Los Angeles Rams": "NFC_West",
    "San Francisco 49ers": "NFC_West",
    "Seattle Seahawks": "NFC_West",
}


def _nfl_is_division_game(home_team: str, away_team: str) -> bool:
    """Return True if both teams belong to the same NFL division."""
    h_div = NFL_DIVISIONS.get(home_team)
    a_div = NFL_DIVISIONS.get(away_team)
    return h_div is not None and h_div == a_div


def _load_nfl_injuries_known_at(
    data_root: str | Path, observed_at: datetime
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """Return only verified, as-of injury entries and their source identity."""
    snap_dir = Path(data_root) / "availability" / "nfl" / "espn_injury_snapshots"
    if not snap_dir.exists():
        return [], None
    candidates = []
    for p in snap_dir.glob("*/*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            expected_hash = data.pop("snapshot_sha256", None)
            if (
                not expected_hash
                or hashlib.sha256(
                    json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                != expected_hash
            ):
                continue
            obs = parse_utc(data.get("observed_at_utc", ""))
            if obs <= observed_at:
                candidates.append((obs, data.get("entries", []), expected_hash, str(p)))
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            continue
    if not candidates:
        return [], None
    candidates.sort(key=lambda c: c[0])
    obs, entries, digest, path = candidates[-1]
    return entries, {"observed_at_utc": obs.isoformat(), "sha256": digest, "path": path}


def _espn_spread_no_vig_ask(first_odds: dict[str, Any], selection: str) -> float:
    """De-vigged ESPN consensus spread price for `selection`, falling back to the raw
    one-sided implied probability when the other side's close odds aren't available --
    ESPN's two-sided spread odds carry the same vig as moneyline, so pricing off one
    side alone (as this previously did) systematically overstates edge."""
    from ..learned_forward import _safe_odds_int

    ps_obj = first_odds.get("pointSpread") or {}
    sel_odds = _safe_odds_int((ps_obj.get(selection) or {}).get("close", {}).get("odds"))
    other_side = "away" if selection == "home" else "home"
    other_odds = _safe_odds_int((ps_obj.get(other_side) or {}).get("close", {}).get("odds"))
    if sel_odds is None:
        return STANDARD_LAY_ASK
    sel_prob = round(implied_probability(sel_odds), 6)
    if other_odds is None:
        return sel_prob
    other_prob = round(implied_probability(other_odds), 6)
    total = sel_prob + other_prob
    if total <= 0:
        return sel_prob
    return round(sel_prob / total, 6)
