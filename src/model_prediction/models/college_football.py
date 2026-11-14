"""College Football (NCAAF) production slate builder and legacy model alias.

The arithmetic lives in ``cfb_structural_v4.CFBStructuralV4Model``; ``CollegeFootballModel``
is that class with the legacy constructor (per-market version labels) kept for the
registry, replay tests and the ``models.cfb`` alias. Review 2026-11-11 #1: this module
used to carry a second, divergent implementation (margin_sd 16.2, no conference scaling,
no total-SD widening) that production served under the v4 label while every v4 test
exercised the other class. There is now exactly one implementation.

Generates coherent point-in-time predictions for:
1. Moneyline: Calibrated win probabilities derived from the joint scoring distribution.
2. Spread: Cover probabilities with exact push support and discrete key-number modeling.
3. Total: Over/Under probabilities with exact push support and weather/pace interactions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..domain import parse_utc, utc_now
from ..features.base import GameRecord
from ..features.cfb_features import CFBGameRecord
from ..pricing import implied_probability
from ..production_registry import ProductionModelRegistry, compute_artifact_hash
from .base import GamePrediction
from .cfb_distribution import CFBDistributionType
from .cfb_structural_v4 import (
    CFB_BLOWOUT_MARGIN_SCALE,
    CFB_BLOWOUT_SPREAD_THRESHOLD,
    CFB_UNCERTAINTY_MARGIN_SD_SCALE,
    CFB_V4_BASELINE_MARGIN_SD,
    CFB_V4_BASELINE_TOTAL_SD,
    CFB_V4_DEFAULT_HOME_ADVANTAGE,
    CFB_V4_N_SIMULATIONS,
    CFBStructuralV4Model,
)
from .cfb_structural_v5 import CFB_V5_MODEL_VERSION, CFBStructuralV5Model

MODEL_VERSION = "cfb-structural-v4"
CFB_SPREAD_MODEL_VERSION = "cfb-structural-v4"
CFB_TOTAL_MODEL_VERSION = "cfb-structural-v4"
# ESPN reports every bowl / CFP game as week 1 of season type 3 (postseason). Fed to the
# preseason-prior decay table that would be a 90% prior weight for a December game; the
# table is fully decayed from week 14 on, so postseason maps past it.
CFB_POSTSEASON_WEEK = 15
ESPN_SEASON_TYPE_POSTSEASON = 3

__all__ = [
    "CFB_BLOWOUT_MARGIN_SCALE",
    "CFB_BLOWOUT_SPREAD_THRESHOLD",
    "CFB_POSTSEASON_WEEK",
    "CFB_SPREAD_MODEL_VERSION",
    "CFB_TOTAL_MODEL_VERSION",
    "CFB_UNCERTAINTY_MARGIN_SD_SCALE",
    "MODEL_VERSION",
    "CollegeFootballModel",
    "UpcomingCFBGame",
    "build_cfb_slate",
    "cfb_model",
    "cfb_model_for_artifact",
    "cfb_serving_code_hash",
    "model_week_from_espn_event",
]


@dataclass(frozen=True)
class UpcomingCFBGame:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    spread_away_line: float | None = None  # e.g. +7.5 (Home favored by 7.5 -> spread_home_line = -7.5)
    spread_home_line: float | None = None  # e.g. -7.5
    total_line: float | None = None  # e.g. 54.5
    wind_mph: float | None = None
    temperature_f: float | None = None
    precipitation_in: float | None = None
    is_neutral_site: bool = False
    season_year: int = 2024
    week: int = 1
    qb_starter_prob_away: float = 1.0
    qb_starter_prob_home: float = 1.0


class CollegeFootballModel(CFBStructuralV4Model):
    """The served NCAAF model: v4 arithmetic with the legacy per-market version labels."""

    def __init__(
        self,
        version: str = MODEL_VERSION,
        spread_version: str | None = None,
        total_version: str | None = None,
        home_advantage_points: float = CFB_V4_DEFAULT_HOME_ADVANTAGE,
        margin_sd: float = CFB_V4_BASELINE_MARGIN_SD,
        total_sd: float = CFB_V4_BASELINE_TOTAL_SD,
        distribution_type: CFBDistributionType = CFBDistributionType.NEGATIVE_BINOMIAL,
        n_simulations: int = CFB_V4_N_SIMULATIONS,
        random_seed: int = 42,
    ) -> None:
        super().__init__(
            home_advantage_points=home_advantage_points,
            margin_sd=margin_sd,
            total_sd=total_sd,
            distribution_type=distribution_type,
            n_simulations=n_simulations,
            random_seed=random_seed,
        )
        self.version = version
        self.spread_version = spread_version or CFB_SPREAD_MODEL_VERSION
        self.total_version = total_version or CFB_TOTAL_MODEL_VERSION

    def predict_games(
        self,
        history: Sequence[Any],
        upcoming: Sequence[UpcomingCFBGame],
    ) -> list[GamePrediction]:
        """Predict a slate of upcoming CFB games."""
        all_preds: list[GamePrediction] = []
        for g in upcoming:
            all_preds.extend(self.predict_matchup(list(history), g))
        return all_preds


_SERVING_CLASSES: dict[str, type[CFBStructuralV4Model]] = {
    MODEL_VERSION: CollegeFootballModel,
    CFB_V5_MODEL_VERSION: CFBStructuralV5Model,
}


def cfb_model_for_artifact(artifact: Mapping[str, Any]) -> CFBStructuralV4Model:
    """The serving class for an NCAAF artifact, parameterised from that artifact (fail-closed)."""
    version = str(artifact.get("model_version") or "")
    cls = _SERVING_CLASSES.get(version)
    if cls is None:
        raise ValueError(f"no NCAAF serving class for artifact model_version {version!r}")
    return cls.from_artifact(artifact)


def cfb_model() -> CFBStructuralV4Model:
    """The NCAAF champion named by config/production.yaml, built from its own artifact.

    Serving follows the champion map rather than a class default so a promotion cannot
    leave production pricing with one model while labelling rows with another (the
    2026-11-11 review's finding #1). All three NCAAF markets must name the same artifact.
    """
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    champions = {market: registry.champion("NCAAF", market) for market in ("moneyline", "spread", "total")}
    missing = [m for m, c in champions.items() if c is None or not c.available]
    if missing:
        raise ValueError(f"NCAAF champion unavailable for {missing}")
    ids = {c.model_id for c in champions.values() if c is not None}
    if len(ids) != 1:
        raise ValueError(f"NCAAF markets name different champions: {sorted(ids)}")
    champion = champions["moneyline"]
    assert champion is not None and champion.artifact
    payload = json.loads((PROJECT_ROOT / champion.artifact).read_text(encoding="utf-8"))
    if payload.get("model_version") != champion.model_id:
        raise ValueError(f"NCAAF champion artifact model_version mismatch for {champion.model_id}")
    if payload.get("artifact_hash") != compute_artifact_hash(payload):
        raise ValueError(f"NCAAF champion artifact_hash mismatch for {champion.model_id}")
    return cfb_model_for_artifact(payload)


_SERVING_SOURCE_FILES = (
    "college_football.py",
    "cfb_structural_v4.py",
    "cfb_structural_v5.py",
    "cfb_distribution.py",
)


def cfb_serving_code_hash() -> str:
    """Short digest of the NCAAF serving source, for ledger ``code_revision``.

    The previous value hashed the version *string*, so it never changed when the code
    did and could not tell two code revisions apart.
    """
    digest = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in _SERVING_SOURCE_FILES:
        digest.update(name.encode("utf-8"))
        digest.update((here / name).read_bytes())
    digest.update((here.parent / "features" / "cfb_features.py").read_bytes())
    digest.update((here.parent / "features" / "cfb_qb_availability.py").read_bytes())
    return digest.hexdigest()[:16]


def model_week_from_espn_event(event: dict[str, Any], iso_date: str) -> tuple[int, int, int]:
    """(season_year, model_week, espn_season_type) for the preseason-prior decay schedule."""
    season = event.get("season") or {}
    season_year = int(season.get("year") or iso_date[:4])
    season_type = int(season.get("type") or 2)
    espn_week = int((event.get("week") or {}).get("number") or 1)
    model_week = CFB_POSTSEASON_WEEK if season_type == ESPN_SEASON_TYPE_POSTSEASON else espn_week
    return season_year, model_week, season_type


def _load_cfb_history(data_root: Path, cutoff_dt: datetime | None = None) -> list[GameRecord]:
    """Load historical completed CFB games from local cache up to cutoff_dt."""
    records: list[GameRecord] = []
    seen_ids: set[str] = set()

    for path in [
        data_root / "processed" / "ncaaf" / "games.jsonl",
        data_root / "historical" / "ncaaf_games_all.jsonl",
    ]:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    g = json.loads(line)
                    eid = str(g.get("event_id"))
                    if eid in seen_ids:
                        continue
                    start_str = g.get("event_start_utc") or g.get("game_start_utc") or ""
                    start_dt = parse_utc(start_str)
                    if cutoff_dt is not None and start_dt >= cutoff_dt:
                        continue
                    away_score = int(
                        g.get("away_score")
                        if g.get("away_score") is not None
                        else (g.get("away") or {}).get("score", 0)
                    )
                    home_score = int(
                        g.get("home_score")
                        if g.get("home_score") is not None
                        else (g.get("home") or {}).get("score", 0)
                    )
                    away_team = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
                    home_team = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""
                    if not away_team or not home_team:
                        continue
                    rec = CFBGameRecord(
                        event_id=eid,
                        event_start_utc=start_str,
                        league="NCAAF",
                        away_team=away_team,
                        home_team=home_team,
                        away_score=away_score,
                        home_score=home_score,
                        season_type=str(g.get("season_type", "regular")),
                        season_year=g.get("season_year"),
                        competition_type=str(g.get("competition_type", "STD")),
                        possessions=float(g["possessions"]) if g.get("possessions") is not None else None,
                        home_epa_per_play=float(g["home_epa_per_play"])
                        if g.get("home_epa_per_play") is not None
                        else None,
                        away_epa_per_play=float(g["away_epa_per_play"])
                        if g.get("away_epa_per_play") is not None
                        else None,
                        home_success_rate=float(g["home_success_rate"])
                        if g.get("home_success_rate") is not None
                        else None,
                        away_success_rate=float(g["away_success_rate"])
                        if g.get("away_success_rate") is not None
                        else None,
                        travel_distance_miles=float(g["travel_distance_miles"])
                        if g.get("travel_distance_miles") is not None
                        else None,
                        timezone_diff_hours=float(g["timezone_diff_hours"])
                        if g.get("timezone_diff_hours") is not None
                        else None,
                        elevation_ft=int(g["elevation_ft"]) if g.get("elevation_ft") is not None else None,
                        is_dome=bool(g["is_dome"]) if g.get("is_dome") is not None else False,
                        temperature_f=float(g["temperature_f"])
                        if g.get("temperature_f") is not None
                        else None,
                        wind_mph=float(g["wind_mph"]) if g.get("wind_mph") is not None else None,
                    )
                    records.append(rec)
                    seen_ids.add(eid)
                except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                    continue

    return sorted(records, key=lambda r: r.start)


def _clean_cfb_school(name: str) -> str:
    return name.replace("(", "").replace(")", "").strip()


def _canonical_market_snapshot_lineage(row: dict[str, Any], archive_path: Path) -> dict[str, Any] | None:
    """Bind a parsed prospective quote to its exact archived JSON record."""
    row = row.get("_archive_record", row)
    if not isinstance(row, dict):
        return None
    observed_at = str(row.get("observed_at_utc") or "")
    source = str(row.get("provider") or "")
    reconstructed = row.get("reconstructed")
    prospective_marker = row.get("usage") == "prospective_executable_bbo"
    if (
        not archive_path.is_file()
        or not observed_at
        or source != "polymarket_us"
        or row.get("timestamp_valid") is not True
        or not (reconstructed is False or (reconstructed is None and prospective_marker))
    ):
        return None
    payload = {key: value for key, value in row.items() if not str(key).startswith("_")}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "market_quote_observed_at_utc": observed_at,
        "market_quote_timestamp_valid": True,
        "market_quote_source": source,
        "market_quote_provenance": "decision_time_executable_quote",
        "market_quote_reconstructed": False,
        "market_snapshot_hash": digest,
        "market_snapshot_archive_path": str(archive_path.resolve()),
        "market_snapshot_record_id": digest,
    }


def _match_cfb_moneyline(
    away_team: str,
    home_team: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Match the newest pregame Polymarket moneyline quote for an ESPN matchup.

    Returns (quote_row, long_side) where long_side is 'home' or 'away'. The day's file
    holds one row per capture, so the match must be the latest observation, not the
    first row in file order (review 2026-11-11 #8a: returning the oldest capture made
    the 18:00 ET run trip STALE_DATA and Main moneylines could never be emitted).
    """
    from ..data_sources.cfb_data import resolve_team
    from ..learned_forward import _team_matches

    away_res = resolve_team(_clean_cfb_school(away_team))
    home_res = resolve_team(_clean_cfb_school(home_team))
    away_canon = away_res.canonical_name if away_res else away_team
    home_canon = home_res.canonical_name if home_res else home_team
    matched: list[tuple[str, dict[str, Any], str]] = []

    for r in rows:
        if r.get("market_type") != "moneyline":
            continue
        long_dict = r.get("long") or {}
        short_dict = r.get("short") or {}
        if long_dict.get("ask") is None or short_dict.get("ask") is None:
            continue

        title = str(r.get("event_title") or "")
        if " vs. " in title:
            p1, p2 = title.split(" vs. ", 1)
        elif " vs " in title:
            p1, p2 = title.split(" vs ", 1)
        elif " @ " in title:
            p1, p2 = title.split(" @ ", 1)
        elif " at " in title:
            p1, p2 = title.split(" at ", 1)
        else:
            continue

        p1_res = resolve_team(_clean_cfb_school(p1))
        p2_res = resolve_team(_clean_cfb_school(p2))
        p1_c = p1_res.canonical_name if p1_res else p1
        p2_c = p2_res.canonical_name if p2_res else p2

        h1 = (p1_c == home_canon) or _team_matches(home_team, p1)
        a2 = (p2_c == away_canon) or _team_matches(away_team, p2)
        if h1 and a2:
            team = str(r.get("team") or "")
            long_desc = str(long_dict.get("description") or "")
            is_home_long = (
                (p1_res is not None and resolve_team(_clean_cfb_school(team)) == home_res)
                or _team_matches(home_team, team)
                or _team_matches(home_team, long_desc)
            )
            matched.append((str(r.get("observed_at_utc") or ""), r, "home" if is_home_long else "away"))
            continue

        a1 = (p1_c == away_canon) or _team_matches(away_team, p1)
        h2 = (p2_c == home_canon) or _team_matches(home_team, p2)
        if a1 and h2:
            team = str(r.get("team") or "")
            long_desc = str(long_dict.get("description") or "")
            is_away_long = (
                (p1_res is not None and resolve_team(_clean_cfb_school(team)) == away_res)
                or _team_matches(away_team, team)
                or _team_matches(away_team, long_desc)
            )
            matched.append((str(r.get("observed_at_utc") or ""), r, "away" if is_away_long else "home"))

    if not matched:
        return None, None
    _, row, side = max(matched, key=lambda item: item[0])
    return row, side


def _match_cfb_spread(
    away_team: str,
    home_team: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Select the most balanced main-game Polymarket spread quote for this matchup."""
    from ..data_sources.cfb_data import resolve_team
    from ..learned_forward import _team_matches

    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-")
    cands: list[dict[str, Any]] = []
    away_res = resolve_team(_clean_cfb_school(away_team))
    home_res = resolve_team(_clean_cfb_school(home_team))
    away_canon = away_res.canonical_name if away_res else away_team
    home_canon = home_res.canonical_name if home_res else home_team

    for r in rows:
        if r.get("market_type") != "spread":
            continue
        slug = str(r.get("market_slug", "")).lower()
        if any(token in slug for token in partial_tokens):
            continue
        if r.get("line") is None:
            continue
        # Both sides must be quoted: a one-sided book used to be completed as 1 - ask,
        # which overstated the edge by the full spread (review 2026-11-11 #8b).
        l_ask = (r.get("long") or {}).get("ask")
        s_ask = (r.get("short") or {}).get("ask")
        if l_ask is None or s_ask is None:
            continue

        title = str(r.get("event_title") or "")
        if " vs. " in title:
            p1, p2 = title.split(" vs. ", 1)
        elif " vs " in title:
            p1, p2 = title.split(" vs ", 1)
        elif " @ " in title:
            p1, p2 = title.split(" @ ", 1)
        elif " at " in title:
            p1, p2 = title.split(" at ", 1)
        else:
            continue

        p1_res = resolve_team(_clean_cfb_school(p1))
        p2_res = resolve_team(_clean_cfb_school(p2))
        p1_c = p1_res.canonical_name if p1_res else p1
        p2_c = p2_res.canonical_name if p2_res else p2

        match_normal = ((p1_c == away_canon) or _team_matches(away_team, p1)) and (
            (p2_c == home_canon) or _team_matches(home_team, p2)
        )
        match_reverse = ((p1_c == home_canon) or _team_matches(home_team, p1)) and (
            (p2_c == away_canon) or _team_matches(away_team, p2)
        )
        if match_normal or match_reverse:
            cands.append(r)

    if not cands:
        return None, None

    def _spread_ask_balance(row: dict[str, Any]) -> float:
        return abs(float(row["long"]["ask"]) - 0.5)

    best = min(cands, key=_spread_ask_balance)
    anchor_team = str(best.get("team") or "")
    anchor_res = resolve_team(_clean_cfb_school(anchor_team)) if anchor_team else None
    if anchor_res and anchor_res == away_res:
        anchor_side = "away"
    elif anchor_res and anchor_res == home_res:
        anchor_side = "home"
    elif _team_matches(away_team, anchor_team):
        anchor_side = "away"
    else:
        anchor_side = "home"

    return best, anchor_side


def _match_cfb_total(
    away_team: str,
    home_team: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Select the most balanced main-game Polymarket total quote for this matchup."""
    from ..data_sources.cfb_data import resolve_team
    from ..learned_forward import _team_matches

    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "-tt-", "-tt1h-", "-tt2h-")
    cands: list[dict[str, Any]] = []
    away_res = resolve_team(_clean_cfb_school(away_team))
    home_res = resolve_team(_clean_cfb_school(home_team))
    away_canon = away_res.canonical_name if away_res else away_team
    home_canon = home_res.canonical_name if home_res else home_team

    for r in rows:
        if r.get("market_type") not in ("total", "over_under"):
            continue
        slug = str(r.get("market_slug", "")).lower()
        if any(token in slug for token in partial_tokens):
            continue
        if r.get("line") is None:
            continue
        # Both sides must be quoted: a one-sided book used to be completed as 1 - ask,
        # which overstated the edge by the full spread (review 2026-11-11 #8b).
        l_ask = (r.get("long") or {}).get("ask")
        s_ask = (r.get("short") or {}).get("ask")
        if l_ask is None or s_ask is None:
            continue

        title = str(r.get("event_title") or "")
        if " vs. " in title:
            p1, p2 = title.split(" vs. ", 1)
        elif " vs " in title:
            p1, p2 = title.split(" vs ", 1)
        elif " @ " in title:
            p1, p2 = title.split(" @ ", 1)
        elif " at " in title:
            p1, p2 = title.split(" at ", 1)
        else:
            continue

        p1_res = resolve_team(_clean_cfb_school(p1))
        p2_res = resolve_team(_clean_cfb_school(p2))
        p1_c = p1_res.canonical_name if p1_res else p1
        p2_c = p2_res.canonical_name if p2_res else p2

        match_normal = ((p1_c == away_canon) or _team_matches(away_team, p1)) and (
            (p2_c == home_canon) or _team_matches(home_team, p2)
        )
        match_reverse = ((p1_c == home_canon) or _team_matches(home_team, p1)) and (
            (p2_c == away_canon) or _team_matches(away_team, p2)
        )
        if match_normal or match_reverse:
            cands.append(r)

    if not cands:
        return None

    def _total_ask_balance(row: dict[str, Any]) -> float:
        return abs(float(row["long"]["ask"]) - 0.5)

    return min(cands, key=_total_ask_balance)


# A live event list fetched this long after the decision time postdates it and
# must not price that decision (point-in-time rule).
_INDICATIVE_LIST_MAX_LAG = timedelta(minutes=10)


def _indicative_quote_rows(events: list[dict[str, Any]], observed_at_utc: str) -> list[dict[str, Any]]:
    """Polymarket event-list markets in the snapshot-row shape the ``_match_cfb_*`` selectors read.

    Built exactly as ``capture_slate_snapshots`` lays out a captured row (same
    ``line``, same ``team`` anchor, long/short by ``is_long``), but each side's
    ask is the event list's indicative ``price_probability``, not a live book.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        for market in event.get("markets") or []:
            sides = market.get("sides") or []
            long_side = next((side for side in sides if side.get("is_long")), None)
            short_side = next((side for side in sides if not side.get("is_long")), None)
            if long_side is None or short_side is None:
                continue
            rows.append(
                {
                    "market_slug": market.get("market_slug"),
                    "market_type": market.get("market_type"),
                    "line": market.get("line"),
                    "team": next((side.get("team") for side in sides if side.get("team")), None),
                    "event_id": str(event.get("event_id") or ""),
                    "event_title": str(event.get("title") or ""),
                    "event_start_utc": event.get("event_start_utc"),
                    "observed_at_utc": observed_at_utc,
                    "long": {
                        "ask": long_side.get("price_probability"),
                        "description": long_side.get("description"),
                    },
                    "short": {
                        "ask": short_side.get("price_probability"),
                        "description": short_side.get("description"),
                    },
                }
            )
    return rows


def _indicative_quote_lineage(observed_at_utc: str) -> dict[str, Any]:
    """Ledger quote fields for a row priced off the event list (no archived book to bind)."""
    return {
        "market_quote_observed_at_utc": observed_at_utc,
        "market_quote_timestamp_valid": False,
        "market_quote_source": "polymarket_us",
        "market_quote_provenance": "indicative_slate",
        "market_quote_reconstructed": False,
    }


def _espn_consensus_quote_lineage() -> dict[str, Any]:
    """Ledger quote fields for a row priced off ESPN's de-vigged consensus.

    Until 2026-11-14 these rows carried no lineage at all, so the ledger's
    ``market_quote_provenance`` was blank and consensus rows could not be told
    apart from executable ones without parsing the rationale.
    """
    return {
        "market_quote_timestamp_valid": False,
        "market_quote_source": "espn_consensus",
        "market_quote_provenance": "espn_consensus_indicative",
        "market_quote_reconstructed": False,
    }


def _latest_capture_per_market(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the newest observation of each market_slug.

    The spread/total selectors choose the most balanced quote; run over raw rows they
    compared stale captures against fresh ones and could pick a line that no longer
    existed. Rows without a slug are kept as-is.
    """
    latest: dict[str, dict[str, Any]] = {}
    unslugged: list[dict[str, Any]] = []
    for row in rows:
        slug = str(row.get("market_slug") or "")
        if not slug:
            unslugged.append(row)
            continue
        current = latest.get(slug)
        if current is None or str(row.get("observed_at_utc") or "") >= str(
            current.get("observed_at_utc") or ""
        ):
            latest[slug] = row
    return list(latest.values()) + unslugged


def build_cfb_slate(
    data_root: str | Path,
    game_date: str,
    client: Any | None = None,
    observed_at: datetime | None = None,
    model: CFBStructuralV4Model | None = None,
    polymarket_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build prospective slate for College Football (Moneyline, Spread, Total).

    ``model`` defaults to the artifact-parameterised production model; tests inject a
    variant. ``observed_at`` is the decision time every point-in-time filter uses.

    Each market is priced off, in order: a captured executable Polymarket book, the
    Polymarket event list's indicative price, ESPN's sportsbook consensus. The event
    list is fetched live when ``client`` is None (production); ``polymarket_events``
    supplies one already observed no later than ``observed_at``.
    """
    from ..data_sources.espn import ESPNClient
    from ..features.cfb_qb_availability import (
        load_latest_injury_snapshot,
        passing_leader_from_competitor,
        resolve_cfb_qb_availability,
    )

    root = Path(data_root)
    obs = observed_at or utc_now()
    espn = client or ESPNClient()

    norm_date = game_date.replace("-", "")
    iso_date = f"{norm_date[:4]}-{norm_date[4:6]}-{norm_date[6:8]}" if len(norm_date) == 8 else game_date

    # 1. Fetch Scoreboard
    try:
        scoreboard = espn.scoreboard("NCAAF", norm_date)
        events = scoreboard.get("events", [])
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(
            "Failed to fetch ESPN NCAAF scoreboard for %s: %s", norm_date, exc
        )
        events = []

    # 2. Load History: cutoff at 09:00:00Z (5:00 AM EDT) captures late Friday Pacific/Mountain games
    start_of_day = parse_utc(f"{iso_date}T09:00:00Z")
    history = _load_cfb_history(root, cutoff_dt=start_of_day)

    # 3. Load Polymarket Snapshots if available
    from ..college_contract_evidence import evidence_root

    snapshot_path = evidence_root(root) / "odds" / "ncaaf" / iso_date / "polymarket_snapshots.jsonl"
    snapshot_rows: list[dict[str, Any]] = []
    if snapshot_path.is_file():
        try:
            with snapshot_path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                        if isinstance(r, dict):
                            obs_str = str(r.get("observed_at_utc") or "")
                            if obs_str:
                                try:
                                    if parse_utc(obs_str) <= obs:
                                        snapshot_rows.append(r)
                                except (ValueError, TypeError):
                                    pass
                            else:
                                snapshot_rows.append(r)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            snapshot_rows = []
    snapshot_rows = _latest_capture_per_market(snapshot_rows)

    # Polymarket event list, fetched once: the indicative fallback for a market
    # with no captured book, and the inventory for candidate evidence below. Without
    # it, every NCAAF market the throttled BBO capture missed fell straight through to
    # ESPN consensus (2026-11-14: every Main row of the day).
    listed_events: list[dict[str, Any]] | None = polymarket_events
    listed_events_error: str | None = None
    listed_observed = obs
    if listed_events is None and client is None:
        from ..data_sources.polymarket_us import PolymarketUSClient

        try:
            listed_events = PolymarketUSClient().slate("NCAAF", datetime.fromisoformat(iso_date).date())
            listed_observed = utc_now()
        except Exception as exc:  # noqa: BLE001 -- fail soft to ESPN consensus
            listed_events_error = f"{type(exc).__name__}: {exc}"
    indicative_rows = (
        _indicative_quote_rows(listed_events, listed_observed.isoformat())
        if listed_events and listed_observed - obs <= _INDICATIVE_LIST_MAX_LAG
        else []
    )

    model = model if model is not None else cfb_model()
    injury_snapshot = load_latest_injury_snapshot(root, obs)
    priced_contracts: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    candidate_bases: list[dict[str, Any]] = []

    for ev in events:
        competition = (ev.get("competitions") or [{}])[0]
        comps = {c.get("homeAway"): c for c in competition.get("competitors", [])}
        away = comps.get("away")
        home = comps.get("home")
        if not away or not home:
            continue

        away_team = (away.get("team") or {}).get("displayName") or ""
        home_team = (home.get("team") or {}).get("displayName") or ""
        if not away_team or not home_team:
            continue

        event_id = str(ev.get("id"))
        event_start_utc = str(ev.get("date"))
        is_neutral = bool(competition.get("neutralSite", False))
        venue = competition.get("venue") or {}
        venue_name = str(venue.get("fullName") or "").strip() or None
        venue_indoor = venue.get("indoor") if isinstance(venue.get("indoor"), bool) else None

        # 3a. Match Polymarket prospective quotes: a captured book first, else the
        # event list's indicative price for that market.
        pm_ml, ml_long_side = _match_cfb_moneyline(away_team, home_team, snapshot_rows)
        ml_sportsbook = "polymarket_us"
        if pm_ml is None and indicative_rows:
            pm_ml, ml_long_side = _match_cfb_moneyline(away_team, home_team, indicative_rows)
            ml_sportsbook = "polymarket_us_indicative"
        pm_sp, sp_anchor_side = _match_cfb_spread(away_team, home_team, snapshot_rows)
        sp_sportsbook = "polymarket_us"
        if pm_sp is None and indicative_rows:
            pm_sp, sp_anchor_side = _match_cfb_spread(away_team, home_team, indicative_rows)
            sp_sportsbook = "polymarket_us_indicative"
        pm_tot = _match_cfb_total(away_team, home_team, snapshot_rows)
        tot_sportsbook = "polymarket_us"
        if pm_tot is None and indicative_rows:
            pm_tot = _match_cfb_total(away_team, home_team, indicative_rows)
            tot_sportsbook = "polymarket_us_indicative"

        pm_ml_home_prob = None
        pm_ml_away_prob = None
        pm_ml_lineage = None
        if pm_ml is not None:
            pm_ml_home_prob = (
                float(pm_ml["long"]["ask"]) if ml_long_side == "home" else float(pm_ml["short"]["ask"])
            )
            pm_ml_away_prob = (
                float(pm_ml["short"]["ask"]) if ml_long_side == "home" else float(pm_ml["long"]["ask"])
            )
            pm_ml_lineage = (
                _canonical_market_snapshot_lineage(pm_ml, snapshot_path)
                if ml_sportsbook == "polymarket_us"
                else _indicative_quote_lineage(listed_observed.isoformat())
            )

        pm_spread_away_line = None
        pm_spread_home_line = None
        pm_spread_away_prob = None
        pm_spread_home_prob = None
        pm_spread_lineage = None
        if pm_sp is not None:
            sp_line = float(pm_sp["line"])
            pm_spread_away_line = sp_line if sp_anchor_side == "away" else -sp_line
            pm_spread_home_line = -sp_line if sp_anchor_side == "away" else sp_line
            long_ask = float(pm_sp["long"]["ask"])
            short_ask = float(pm_sp["short"]["ask"])
            pm_spread_away_prob = long_ask if sp_anchor_side == "away" else short_ask
            pm_spread_home_prob = short_ask if sp_anchor_side == "away" else long_ask
            pm_spread_lineage = (
                _canonical_market_snapshot_lineage(pm_sp, snapshot_path)
                if sp_sportsbook == "polymarket_us"
                else _indicative_quote_lineage(listed_observed.isoformat())
            )

        pm_total_line = None
        pm_total_over_prob = None
        pm_total_under_prob = None
        pm_total_lineage = None
        if pm_tot is not None:
            pm_total_line = float(pm_tot["line"])
            pm_total_over_prob = float(pm_tot["long"]["ask"])
            pm_total_under_prob = float(pm_tot["short"]["ask"])
            pm_total_lineage = (
                _canonical_market_snapshot_lineage(pm_tot, snapshot_path)
                if tot_sportsbook == "polymarket_us"
                else _indicative_quote_lineage(listed_observed.isoformat())
            )

        # 3b. Extract fallback sportsbook odds if available from ESPN
        odds_list = competition.get("odds", [])
        spread_away_line = None
        spread_home_line = None
        total_line = None
        market_ml_home_prob = None
        market_ml_away_prob = None
        market_spread_away_prob = None
        market_spread_home_prob = None
        market_total_over_prob = None
        market_total_under_prob = None

        if odds_list:
            first_odds = odds_list[0]

            def _safe_odds_int(val: Any) -> int | None:
                if val is None:
                    return None
                s = str(val).strip()
                if s.upper() in ("OFF", "N/A", "NONE", ""):
                    return None
                try:
                    n = int(s)
                    return n if (n <= -100 or n >= 100) else None
                except (ValueError, TypeError):
                    return None

            # Total Line & Odds
            tot_obj = first_odds.get("total") or {}
            ov_n = _safe_odds_int((tot_obj.get("over") or {}).get("close", {}).get("odds"))
            un_n = _safe_odds_int((tot_obj.get("under") or {}).get("close", {}).get("odds"))
            tot_line_str = (
                str(
                    (tot_obj.get("over") or {}).get("close", {}).get("line")
                    or first_odds.get("overUnder")
                    or ""
                )
                .replace("o", "")
                .replace("u", "")
                .strip()
            )
            try:
                if tot_line_str:
                    total_line = float(tot_line_str)
            except (ValueError, TypeError):
                total_line = None

            if total_line is not None and ov_n is not None and un_n is not None:
                market_total_over_prob = round(implied_probability(ov_n), 6)
                market_total_under_prob = round(implied_probability(un_n), 6)

            # Point Spread Line & Odds
            ps_obj = first_odds.get("pointSpread") or {}
            h_ps_n = _safe_odds_int((ps_obj.get("home") or {}).get("close", {}).get("odds"))
            a_ps_n = _safe_odds_int((ps_obj.get("away") or {}).get("close", {}).get("odds"))
            h_ps_line_str = str(
                (ps_obj.get("home") or {}).get("close", {}).get("line") or first_odds.get("spread") or ""
            ).strip()
            try:
                if h_ps_line_str:
                    spread_home_line = float(h_ps_line_str)
                    spread_away_line = -spread_home_line
            except (ValueError, TypeError):
                spread_home_line = None
                spread_away_line = None

            if spread_home_line is not None and h_ps_n is not None and a_ps_n is not None:
                market_spread_home_prob = round(implied_probability(h_ps_n), 6)
                market_spread_away_prob = round(implied_probability(a_ps_n), 6)

            # Moneyline parsing if available from ESPN (nested moneyline or top-level)
            ml_obj = first_odds.get("moneyline") or {}
            h_ml_n = _safe_odds_int(
                (ml_obj.get("home") or {}).get("close", {}).get("odds")
                or (first_odds.get("homeTeamOdds") or {}).get("moneyLine")
                or ((first_odds.get("moneylineWinner") or {}).get("home") or {}).get("odds")
            )
            a_ml_n = _safe_odds_int(
                (ml_obj.get("away") or {}).get("close", {}).get("odds")
                or (first_odds.get("awayTeamOdds") or {}).get("moneyLine")
                or ((first_odds.get("moneylineWinner") or {}).get("away") or {}).get("odds")
            )

            if h_ml_n is not None:
                market_ml_home_prob = round(implied_probability(h_ml_n), 6)
            if a_ml_n is not None:
                market_ml_away_prob = round(implied_probability(a_ml_n), 6)

        effective_spread_away = pm_spread_away_line if pm_sp is not None else spread_away_line
        effective_spread_home = pm_spread_home_line if pm_sp is not None else spread_home_line
        effective_total = pm_total_line if pm_tot is not None else total_line

        ev_season, ev_week, ev_season_type = model_week_from_espn_event(ev, iso_date)

        from ..features.multi_sport_weather import resolve_cfb_game_weather

        weather_evidence: dict[str, Any] = {}
        w_temp, w_wind, w_precip, is_dome = resolve_cfb_game_weather(
            root,
            home_team,
            iso_date,
            event_start_utc=event_start_utc,
            observed_at_utc=obs.isoformat(),
            provenance_out=weather_evidence,
            is_neutral_site=is_neutral,
            venue_name=venue_name,
            venue_indoor=venue_indoor,
        )
        weather_inputs_verified = is_dome or None not in (w_temp, w_wind, w_precip)

        away_qb_id, away_qb_name = passing_leader_from_competitor(away)
        home_qb_id, home_qb_name = passing_leader_from_competitor(home)
        away_qb = resolve_cfb_qb_availability(
            injury_snapshot,
            team=away_team,
            starter_athlete_id=away_qb_id,
            starter_name=away_qb_name,
            observed_at=obs,
        )
        home_qb = resolve_cfb_qb_availability(
            injury_snapshot,
            team=home_team,
            starter_athlete_id=home_qb_id,
            starter_name=home_qb_name,
            observed_at=obs,
        )

        upcoming = UpcomingCFBGame(
            event_id=event_id,
            event_start_utc=event_start_utc,
            away_team=away_team,
            home_team=home_team,
            spread_away_line=effective_spread_away,
            spread_home_line=effective_spread_home,
            total_line=effective_total,
            wind_mph=w_wind,
            temperature_f=w_temp,
            precipitation_in=w_precip,
            is_neutral_site=is_neutral,
            season_year=ev_season,
            week=ev_week,
            qb_starter_prob_away=away_qb.starter_prob,
            qb_starter_prob_home=home_qb.starter_prob,
        )

        preds = model.predict_matchup(history, upcoming, project_missing_lines=True)
        schedule_context = {
            "espn_season_type": ev_season_type,
            "espn_week": int((ev.get("week") or {}).get("number") or 1),
            "model_week": ev_week,
            "venue_name": venue_name,
            "venue_indoor": venue_indoor,
        }
        qb_context = {"away": away_qb.to_dict(), "home": home_qb.to_dict()}
        if preds and preds[0].inference_inputs is not None:
            candidate_bases.append(
                {
                    "event_id": event_id,
                    "event_start_utc": event_start_utc,
                    "away_team": away_team,
                    "home_team": home_team,
                    "features": preds[0].feature_basis,
                    "inference_inputs": preds[0].inference_inputs,
                }
            )

        for p in preds:
            mtype = p.market_type
            sportsbook = "espn_consensus"
            market_lineage = None

            if mtype == "moneyline":
                p_h = float(p.probabilities.get("home", 0.5))
                p_a = float(p.probabilities.get("away", 0.5))
                if pm_ml is not None and pm_ml_home_prob is not None and pm_ml_away_prob is not None:
                    h_ask = float(pm_ml_home_prob)
                    a_ask = float(pm_ml_away_prob)
                    slug_base = str(pm_ml.get("market_slug"))
                    sportsbook = ml_sportsbook
                    market_lineage = pm_ml_lineage
                    m_ver = p.model_version
                elif market_ml_home_prob is not None and market_ml_away_prob is not None:
                    h_ask = float(market_ml_home_prob)
                    a_ask = float(market_ml_away_prob)
                    slug_base = f"ncaaf-ml-{event_id}"
                    sportsbook = "espn_consensus"
                    market_lineage = _espn_consensus_quote_lineage()
                    m_ver = p.model_version
                else:
                    continue

                # Pure Probability Winner Selection: back the side with the higher win probability
                if p_h >= p_a:
                    selection, prob, ask = "home", p_h, h_ask
                else:
                    selection, prob, ask = "away", p_a, a_ask

                line_val = None
                slug = f"{slug_base}-{selection}"
            elif mtype == "spread":
                p_cov_a = float(p.probabilities.get("away", 0.5))
                p_cov_h = float(p.probabilities.get("home", 0.5))
                is_conf = bool((p.feature_basis or {}).get("is_conference_matchup", False))
                if pm_sp is not None and pm_spread_away_prob is not None and pm_spread_home_prob is not None:
                    edge_a = p_cov_a - float(pm_spread_away_prob)
                    edge_h = p_cov_h - float(pm_spread_home_prob)
                    is_h_fav = (pm_spread_home_line or 0.0) < 0
                    spread_mag = abs(pm_spread_home_line or 0.0)
                    if 3.5 < spread_mag <= 9.5:
                        # Key number 7 window: favorites cover only 45%. Require decisive edge > 0.08 to lay points.
                        pick_away = (
                            not (edge_h > 0.08 and edge_h > edge_a)
                            if is_h_fav
                            else (edge_a > 0.08 and edge_a > edge_h)
                        )
                    elif not is_conf:
                        pick_away = (
                            (edge_a > 0.08 and edge_a > edge_h)
                            if is_h_fav
                            else not (edge_h > 0.08 and edge_h > edge_a)
                        )
                    else:
                        pick_away = edge_a >= edge_h

                    if pick_away:
                        selection, prob, ask = "away", p_cov_a, float(pm_spread_away_prob)
                        line_val = pm_spread_away_line
                    else:
                        selection, prob, ask = "home", p_cov_h, float(pm_spread_home_prob)
                        line_val = pm_spread_home_line
                    slug = str(pm_sp.get("market_slug"))
                    sportsbook = sp_sportsbook
                    market_lineage = pm_spread_lineage
                    m_ver = p.model_version
                elif (
                    spread_away_line is not None
                    and spread_home_line is not None
                    and market_spread_away_prob is not None
                    and market_spread_home_prob is not None
                ):
                    a_ask = market_spread_away_prob
                    h_ask = market_spread_home_prob
                    edge_a = p_cov_a - float(a_ask)
                    edge_h = p_cov_h - float(h_ask)
                    is_h_fav = (spread_home_line or 0.0) < 0
                    spread_mag = abs(spread_home_line or 0.0)
                    if 3.5 < spread_mag <= 9.5:
                        # Key number 7 window: favorites cover only 45%. Require decisive edge > 0.08 to lay points.
                        pick_away = (
                            not (edge_h > 0.08 and edge_h > edge_a)
                            if is_h_fav
                            else (edge_a > 0.08 and edge_a > edge_h)
                        )
                    elif not is_conf:
                        pick_away = (
                            (edge_a > 0.08 and edge_a > edge_h)
                            if is_h_fav
                            else not (edge_h > 0.08 and edge_h > edge_a)
                        )
                    else:
                        pick_away = edge_a >= edge_h

                    if pick_away:
                        selection, prob, ask = "away", p_cov_a, float(a_ask)
                        line_val = spread_away_line
                    else:
                        selection, prob, ask = "home", p_cov_h, float(h_ask)
                        line_val = spread_home_line
                    slug = f"ncaaf-spread-{event_id}-{selection}"
                    sportsbook = "espn_consensus"
                    market_lineage = _espn_consensus_quote_lineage()
                    m_ver = p.model_version
                else:
                    continue
            else:  # total
                p_ov = float(p.probabilities.get("over", 0.5))
                p_un = float(p.probabilities.get("under", 0.5))
                if pm_tot is not None and pm_total_over_prob is not None and pm_total_under_prob is not None:
                    edge_o = p_ov - float(pm_total_over_prob)
                    edge_u = p_un - float(pm_total_under_prob)
                    pick_under = edge_u >= edge_o

                    if not pick_under:
                        selection, prob, ask = "over", p_ov, float(pm_total_over_prob)
                    else:
                        selection, prob, ask = "under", p_un, float(pm_total_under_prob)
                    line_val = pm_total_line
                    slug = str(pm_tot.get("market_slug"))
                    sportsbook = tot_sportsbook
                    market_lineage = pm_total_lineage
                    m_ver = p.model_version
                elif (
                    total_line is not None
                    and market_total_over_prob is not None
                    and market_total_under_prob is not None
                ):
                    o_ask = market_total_over_prob
                    u_ask = market_total_under_prob
                    edge_o = p_ov - float(o_ask)
                    edge_u = p_un - float(u_ask)
                    pick_under = edge_u >= edge_o

                    if not pick_under:
                        selection, prob, ask = "over", p_ov, float(o_ask)
                    else:
                        selection, prob, ask = "under", p_un, float(u_ask)
                    line_val = total_line
                    slug = f"ncaaf-total-{event_id}-{selection}"
                    sportsbook = "espn_consensus"
                    market_lineage = _espn_consensus_quote_lineage()
                    m_ver = p.model_version
                else:
                    continue

            # If no market price/line is available from the source, do NOT fabricate a fake edge
            if ask is None:
                continue

            # Operator decision 2026-11-11 (NFL, `5ffc2b40`), applied to NCAAF: a de-vigged
            # sportsbook consensus number is not an executable ask. The row must say which
            # it was priced against so CLV/PnL readers can separate the two populations.
            quote_provenance = {
                "polymarket_us": "polymarket_executable_ask",
                "polymarket_us_indicative": "polymarket_indicative_slate",
            }.get(sportsbook, "espn_consensus_indicative")
            rationale_text = p.rationale
            if sportsbook == "polymarket_us_indicative":
                rationale_text = (
                    f"{rationale_text} Priced off the Polymarket event-list price "
                    "(no captured book matched); indicative, not an executable CLOB ask."
                )
            elif sportsbook != "polymarket_us":
                rationale_text = (
                    f"{rationale_text} Priced off ESPN sportsbook consensus "
                    "(no Polymarket market matched); not an executable CLOB ask."
                )

            edge = round(prob - float(ask), 6)
            priced_contracts.append(
                {
                    "event_id": event_id,
                    "event_start_utc": event_start_utc,
                    "away_team": away_team,
                    "home_team": home_team,
                    "market_type": mtype,
                    "selection": selection,
                    "line": line_val,
                    "model_probability": prob,
                    "model_uncertainty": p.uncertainty,
                    "weather_inputs_verified": weather_inputs_verified,
                    "model_version": m_ver,
                    "feature_basis": {
                        **p.feature_basis,
                        "weather_inputs_verified": weather_inputs_verified,
                        "weather_evidence": weather_evidence,
                        "quote_provenance": quote_provenance,
                        "schedule_context": schedule_context,
                        "qb_availability": qb_context,
                    },
                    "inference_inputs": p.inference_inputs,
                    "rationale": rationale_text,
                    "market_slug": slug,
                    "executable_ask": float(ask),
                    "observed_at_utc": (
                        market_lineage["market_quote_observed_at_utc"]
                        if market_lineage is not None and market_lineage.get("market_quote_observed_at_utc")
                        else obs.isoformat()
                    ),
                    "timestamp_valid": True,
                    "edge_vs_executable_ask": edge,
                    "sportsbook": sportsbook,
                    "market_lineage": market_lineage,
                }
            )

    code_hash = cfb_serving_code_hash()

    candidate_capture: dict[str, Any] = {"status": "not_requested"}
    if client is None:
        try:
            from ..cfb_replay import replay_inputs
            from ..college_contract_evidence import build_candidate_evidence, save_candidate_evidence
            from ..learned_forward import _team_matches

            if listed_events is None:
                raise RuntimeError(listed_events_error or "NCAAF event list unavailable")
            candidate_probabilities: dict[str, dict[str, Any]] = {}
            for listed_event in listed_events:
                title = str(listed_event.get("title") or "")
                parts = next(
                    (title.split(sep, 1) for sep in (" vs. ", " vs ", " @ ", " at ") if sep in title), None
                )
                if parts is None:
                    continue
                matched = [
                    base
                    for base in candidate_bases
                    if (
                        _team_matches(base["away_team"], parts[0])
                        and _team_matches(base["home_team"], parts[1])
                    )
                    or (
                        _team_matches(base["home_team"], parts[0])
                        and _team_matches(base["away_team"], parts[1])
                    )
                ]
                if len(matched) != 1:
                    continue
                base = matched[0]
                for listed_market in listed_event.get("markets", []):
                    kind = str(listed_market.get("market_type") or "")
                    slug = str(listed_market.get("market_slug") or "")
                    if kind not in ("moneyline", "spread", "total") or not slug:
                        continue
                    inputs = json.loads(json.dumps(base["inference_inputs"]))
                    features = base["features"] or {}
                    # Re-derive both effective SDs with the model's own functions for the
                    # listed line, so a candidate is priced exactly as a pick at that
                    # line would have been (the slate's SDs belong to the slate's line).
                    if kind == "spread":
                        sides = listed_market.get("sides", [])
                        home_side = next((s for s in sides if s.get("selection") == "home"), None)
                        if home_side is None or home_side.get("line") is None:
                            continue
                        home_line = float(home_side["line"])
                        inputs["spread_home_line"] = home_line
                        inputs["parameters"]["margin_sd"] = model.margin_sd_for_features(features, home_line)
                    elif kind == "total":
                        if listed_market.get("line") is None:
                            continue
                        total_line = float(listed_market["line"])
                        inputs["total_line"] = total_line
                        inputs["parameters"]["total_sd"] = model.total_sd_for_features(
                            features, float(inputs["mu_home"]) + float(inputs["mu_away"]), total_line
                        )
                    try:
                        probability = replay_inputs(inputs, kind)
                    except (KeyError, TypeError, ValueError):
                        continue
                    candidate_probabilities[slug] = {
                        "model_version": MODEL_VERSION,
                        "model_event_id": base["event_id"],
                        "feature_packet_hash": hashlib.sha256(
                            json.dumps(
                                {"features": base["features"], "inference_inputs": base["inference_inputs"]},
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                        "probabilities": probability,
                    }
            candidate_rows, candidate_summary = build_candidate_evidence(
                sport="ncaaf",
                day=iso_date,
                events=listed_events,
                probabilities=candidate_probabilities,
                archive_root=root,
                decision_at=obs,
            )
            candidate_by_slug = {row["market_slug"]: row for row in candidate_rows}
            selected_joins = []
            for selected in priced_contracts:
                if selected.get("sportsbook") != "polymarket_us":
                    continue
                slug = str(selected.get("market_slug") or "")
                if selected.get("market_type") == "moneyline":
                    slug = slug.rsplit("-", 1)[0]
                candidate_row = candidate_by_slug.get(slug)
                selected_side = (
                    next(
                        (
                            side
                            for side in candidate_row["sides"]
                            if side["selection"] == selected["selection"]
                        ),
                        None,
                    )
                    if candidate_row
                    else None
                )
                selected_joins.append(
                    {
                        "event_id": selected["event_id"],
                        "market_slug": slug,
                        "selection": selected["selection"],
                        "decision_ask": selected["executable_ask"],
                        "decision_quote_hash": (
                            (selected.get("market_lineage") or {}).get("market_snapshot_hash")
                        ),
                        "candidate_packet_ask": selected_side.get("executable_ask")
                        if selected_side
                        else None,
                        "quote_hash": candidate_row.get("quote_hash") if candidate_row else None,
                        "same_quote_hash": (
                            (selected.get("market_lineage") or {}).get("market_snapshot_hash")
                            == candidate_row.get("quote_hash")
                            if candidate_row
                            else False
                        ),
                        "quote_reason": candidate_row.get("quote_reason") if candidate_row else "not_listed",
                    }
                )
            candidate_summary["selected_candidate_joins"] = selected_joins
            packet = save_candidate_evidence(candidate_rows, candidate_summary, root)
            candidate_capture = {**candidate_summary, "packet_path": str(packet), "status": "ok"}
        except Exception as exc:  # noqa: BLE001
            candidate_capture = {"status": "capture_error", "reason": f"{type(exc).__name__}: {exc}"}

    return {
        "game_date": iso_date,
        "sport": "ncaaf",
        "league": "NCAAF",
        "status": "research",
        "model_version": MODEL_VERSION,
        "model_code_hash": code_hash,
        "priced_contracts": priced_contracts,
        "unmatched_events": unmatched,
        "event_count": len(events),
        "history_games": len(history),
        "candidate_quote_coverage": candidate_capture,
    }
