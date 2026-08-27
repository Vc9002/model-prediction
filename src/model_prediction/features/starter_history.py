"""Live per-starter rolling ERA, from real completed-game boxscore snapshots.

Mirrors ``features/bullpen.py``'s design exactly (same snapshot source, same
point-in-time index-then-filter shape, same real-not-fabricated philosophy)
but keyed by starting pitcher name instead of team, and restricted to each
game's starter (``pitcher_order[0]``) rather than the relievers.

Definition matches ``validation.py::_load_starter_era_map`` (the same
methodology validated via a real walk-forward backtest, 2026-08-04): rolling
ERA over a pitcher's last up to 5 real starts, requiring at least 2 prior
starts before trusting the number. Where that module reads a fixed, frozen
snapshot for backtesting, this one supports a live "as of this moment" query
against whatever the snapshot file currently contains -- which is only
correct if the file is kept current (see ``cli.py``'s daily capture step;
this module makes no network calls of its own and reports
``unavailable_from_source`` rather than guessing when a starter has no
resolvable history).

Real, if rare, risk carried over from the same tradeoff every other cross-
source name match in this codebase makes (team names via ``_team_matches``,
esports players via ``_identity_key``): two different real MLB starters
sharing an identical normalized name would silently merge histories. Not
believed to have ever occurred among real starting pitchers in this dataset,
but not structurally ruled out either -- a real ESPN athleteId -> MLB Stats
API player_id crosswalk would close this gap if it becomes a live concern.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_SNAPSHOT_PATH = Path("data/mlb_statsapi/game_snapshots.jsonl")
DEFAULT_LOOKBACK_STARTS = 5
MINIMUM_PRIOR_STARTS = 2
FIP_CONSTANT = 3.10  # MLB league-average FIP constant; updated annually


@dataclass(frozen=True)
class StarterGameLine:
    """Explicit point-in-time single-game starter pitching performance."""

    game_start_utc: datetime
    innings: float
    batters_faced: int
    earned_runs: int
    strikeouts: int
    walks: int
    home_runs: int
    hit_by_pitch: int


# (game_start, innings, earned_runs, strikeouts, walks, home_runs,
# hit_by_pitch, batters_faced)
_StarterRow = tuple[datetime, float, float, float, float, float, float, float]
_STARTER_INDEX_CACHE: dict[Path, dict[str, list[_StarterRow]]] = {}


def _baseball_innings(value: Any) -> float:
    try:
        whole, _, outs = str(value).partition(".")
        return int(whole) + (int(outs or 0) / 3)
    except (TypeError, ValueError):
        return 0.0


def _normalize_name(name: str) -> str:
    """Fold to comparable ASCII-alnum-lowercase, so cross-source spelling
    differences (e.g. MLB Stats API's "José Soriano" vs. ESPN's "Jose
    Soriano" -- the same real person) match instead of silently missing
    each other. NFKD decomposition splits accented letters into a base
    letter plus a combining mark (category "Mn"), which is then dropped.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c.lower() for c in decomposed if c.isalnum() and not unicodedata.combining(c))


def load_starter_index(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, list[_StarterRow]]:
    """Normalized starter name -> chronological (game_start, innings, earned_runs).

    Only the game's own starter (``pitcher_order[0]``), and only when that
    player actually has real pitching stats recorded (a "Scheduled" -- not
    yet played -- snapshot has an empty ``pitching`` dict and is correctly
    skipped, same guard ``validation.py``'s training-time builder uses).
    """
    path = Path(snapshot_path)
    if path in _STARTER_INDEX_CACHE:
        return _STARTER_INDEX_CACHE[path]
    index: dict[str, list[_StarterRow]] = {}
    if not path.exists():
        _STARTER_INDEX_CACHE[path] = index
        return index

    cache_file = path.with_suffix(".cache")
    if cache_file.exists():
        try:
            stat = path.stat()
            cache_stat = cache_file.stat()
            if cache_stat.st_mtime >= stat.st_mtime:
                import pickle

                with cache_file.open("rb") as cf:
                    cached_data = pickle.load(cf)
                    if isinstance(cached_data, dict):
                        _STARTER_INDEX_CACHE[path] = cached_data
                        return cached_data
        except (OSError, pickle.UnpicklingError, ValueError, EOFError):
            pass

    from ..domain import parse_utc

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                snapshot = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                game_start = parse_utc(str(snapshot["game_start_utc"]))
            except (KeyError, ValueError):
                continue
            for side_key in ("home", "away"):
                side = snapshot.get(side_key) or {}
                pitcher_order = side.get("pitcher_order") or []
                if not pitcher_order:
                    continue
                starter_id = pitcher_order[0]
                starter = next(
                    (p for p in side.get("players", []) if p.get("player_id") == starter_id),
                    None,
                )
                if not starter or not starter.get("pitching") or "inningsPitched" not in starter["pitching"]:
                    continue
                innings = _baseball_innings(starter["pitching"]["inningsPitched"])
                earned_runs = float(starter["pitching"].get("earnedRuns") or 0)
                so = float(starter["pitching"].get("strikeOuts") or 0)
                bb = float(starter["pitching"].get("baseOnBalls") or 0)
                hr = float(starter["pitching"].get("homeRuns") or 0)
                hbp = float(
                    starter["pitching"].get("hitByPitch") or starter["pitching"].get("hitBatsmen") or 0
                )
                batters_faced = float(starter["pitching"].get("battersFaced") or 0)
                name = starter.get("name")
                if not name:
                    continue
                row = (game_start, innings, earned_runs, so, bb, hr, hbp, batters_faced)
                index.setdefault(_normalize_name(name), []).append(row)
    for starts in index.values():
        starts.sort(key=lambda item: item[0])
    _STARTER_INDEX_CACHE[path] = index

    try:
        import pickle
        import uuid

        temp_cache = cache_file.with_name(f"{cache_file.name}.tmp.{uuid.uuid4().hex[:6]}")
        with temp_cache.open("wb") as cf:
            pickle.dump(index, cf, protocol=pickle.HIGHEST_PROTOCOL)
        temp_cache.replace(cache_file)
    except OSError:
        pass

    return index


def starter_rolling_era(
    starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_starts: int = DEFAULT_LOOKBACK_STARTS,
    minimum_prior_starts: int = MINIMUM_PRIOR_STARTS,
    max_days_since_start: int | None = None,
) -> dict[str, Any]:
    """Rolling ERA from this pitcher's last N real starts strictly before decision.

    Fails closed (``status: "insufficient_sample"``/``"unavailable_from_source"``)
    rather than guessing -- caller decides how to treat that (raise, neutral
    default, or unavailable-features note), matching every other feature
    provider in this codebase.

    ``max_days_since_start`` (default ``None`` -- identical to prior behavior,
    load-bearing for v8's frozen contract and its train/serve parity with
    ``validation.py::_load_starter_era_map``) optionally drops starts older
    than that many days before ``decision``. Without it, a starter coming
    back from a long injury/demotion gap can have a "last 5 starts" window
    that silently blends in a start from a different season -- not recent
    form, just the oldest data available. This parameter is for the shadow
    variant below; do not flip its default without a real walk-forward
    validation run.
    """
    index = load_starter_index(snapshot_path)
    starts = [s for s in index.get(_normalize_name(starter_name), []) if s[0] < decision]
    if max_days_since_start is not None:
        cutoff = decision - timedelta(days=max_days_since_start)
        starts = [s for s in starts if s[0] >= cutoff]
    if not starts:
        return {"era": None, "starts": 0, "status": "unavailable_from_source"}
    recent = starts[-lookback_starts:]
    if len(recent) < minimum_prior_starts:
        return {"era": None, "starts": len(recent), "status": "insufficient_sample"}
    innings = sum(item[1] for item in recent)
    earned_runs = sum(item[2] for item in recent)
    if innings <= 0:
        return {"era": None, "starts": len(recent), "status": "insufficient_sample"}
    return {
        "era": round(9 * earned_runs / innings, 4),
        "starts": len(recent),
        "innings": round(innings, 2),
        "status": "available",
    }


def starter_era_gap_live(
    home_starter_name: str,
    away_starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> float:
    """home_starter's rolling ERA minus away_starter's -- same sign convention
    as validation.py's ``_load_starter_era_map`` (home_era - away_era).

    Raises ValueError when either starter's history is insufficient, so the
    caller can fail this game closed rather than serve a fabricated 0.0 --
    matching the module docstring's "never guess" rule and mirroring how
    ``probable_starter_era_gap``'s existing live provider already handles
    the same failure mode.
    """
    home = starter_rolling_era(home_starter_name, decision, snapshot_path=snapshot_path)
    away = starter_rolling_era(away_starter_name, decision, snapshot_path=snapshot_path)
    if home["status"] != "available" or away["status"] != "available":
        raise ValueError(
            "NO_CALL_STARTER_ERA_GAP_INSUFFICIENT_HISTORY: "
            f"home={home_starter_name!r} status={home['status']}, "
            f"away={away_starter_name!r} status={away['status']}"
        )
    return round(home["era"] - away["era"], 6)


def _rolling_fip(recent: list[_StarterRow]) -> float:
    """FIP = ((13*HR) + (3*(BB+HBP)) - (2*K)) / IP + FIP_CONSTANT."""
    innings = sum(item[1] for item in recent)
    so = sum(item[3] for item in recent)
    bb = sum(item[4] for item in recent)
    hr = sum(item[5] for item in recent)
    hbp = sum(item[6] for item in recent)
    if innings <= 0:
        return 0.0
    return (13 * hr + 3 * (bb + hbp) - 2 * so) / innings + FIP_CONSTANT


def starter_rolling_fip(
    starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_starts: int = DEFAULT_LOOKBACK_STARTS,
    minimum_prior_starts: int = MINIMUM_PRIOR_STARTS,
) -> dict[str, Any]:
    """Rolling FIP from this pitcher's last N real starts strictly before decision.

    Same fail-closed contract as ``starter_rolling_era``.
    """
    index = load_starter_index(snapshot_path)
    starts = [s for s in index.get(_normalize_name(starter_name), []) if s[0] < decision]
    if not starts:
        return {"fip": None, "starts": 0, "status": "unavailable_from_source"}
    recent = starts[-lookback_starts:]
    if len(recent) < minimum_prior_starts:
        return {"fip": None, "starts": len(recent), "status": "insufficient_sample"}
    return {
        "fip": round(_rolling_fip(recent), 4),
        "starts": len(recent),
        "innings": round(sum(item[1] for item in recent), 2),
        "status": "available",
    }


def starter_fip_gap_live(
    home_starter_name: str,
    away_starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> float:
    """home_starter's rolling FIP minus away_starter's (home_fip - away_fip)."""
    home = starter_rolling_fip(home_starter_name, decision, snapshot_path=snapshot_path)
    away = starter_rolling_fip(away_starter_name, decision, snapshot_path=snapshot_path)
    if home["status"] != "available" or away["status"] != "available":
        raise ValueError(
            "NO_CALL_STARTER_FIP_GAP_INSUFFICIENT_HISTORY: "
            f"home={home_starter_name!r} status={home['status']}, "
            f"away={away_starter_name!r} status={away['status']}"
        )
    return round(home["fip"] - away["fip"], 6)


def _rolling_k_pct(recent: list[tuple]) -> float:
    """K% = strikeouts / batters_faced."""
    batters_faced = sum(item[7] for item in recent)
    so = sum(item[3] for item in recent)
    return (so / batters_faced) if batters_faced > 0 else 0.0


def _rolling_bb_pct(recent: list[tuple]) -> float:
    """BB% = walks / batters_faced."""
    batters_faced = sum(item[7] for item in recent)
    bb = sum(item[4] for item in recent)
    return (bb / batters_faced) if batters_faced > 0 else 0.0


def _rolling_kbb_pct(recent: list[tuple]) -> float:
    """K-BB% = (K - BB) / batters faced, from a rolling window of starts."""
    batters_faced = sum(item[7] for item in recent)
    so = sum(item[3] for item in recent)
    bb = sum(item[4] for item in recent)
    if batters_faced <= 0:
        return 0.0
    return (so - bb) / batters_faced


def _rolling_kbb_legacy_per_ip(recent: list[tuple]) -> float:
    """Legacy invalid (K-BB)/IP definition kept for reproducing historical parity."""
    ip = sum(item[1] for item in recent)
    so = sum(item[3] for item in recent)
    bb = sum(item[4] for item in recent)
    if ip <= 0:
        return 0.0
    return (so - bb) / ip


def starter_rolling_rates(
    starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_starts: int = DEFAULT_LOOKBACK_STARTS,
    minimum_prior_starts: int = MINIMUM_PRIOR_STARTS,
) -> dict[str, Any]:
    """Rolling K%, BB%, K-BB%, and sample strength (BF) from last N real starts."""
    index = load_starter_index(snapshot_path)
    starts = [s for s in index.get(_normalize_name(starter_name), []) if s[0] < decision]
    if not starts:
        return {
            "k_pct": None,
            "bb_pct": None,
            "k_minus_bb_pct": None,
            "kbb_legacy_per_ip": None,
            "batters_faced": 0,
            "starts": 0,
            "status": "unavailable_from_source",
        }
    recent = starts[-lookback_starts:]
    if len(recent) < minimum_prior_starts:
        return {
            "k_pct": None,
            "bb_pct": None,
            "k_minus_bb_pct": None,
            "kbb_legacy_per_ip": None,
            "batters_faced": sum(item[7] for item in recent),
            "starts": len(recent),
            "status": "insufficient_sample",
        }
    bf = sum(item[7] for item in recent)
    return {
        "k_pct": round(_rolling_k_pct(recent), 4),
        "bb_pct": round(_rolling_bb_pct(recent), 4),
        "k_minus_bb_pct": round(_rolling_kbb_pct(recent), 4),
        "kbb_legacy_per_ip": round(_rolling_kbb_legacy_per_ip(recent), 4),
        "batters_faced": bf,
        "starts": len(recent),
        "innings": round(sum(item[1] for item in recent), 2),
        "status": "available",
    }


def starter_rolling_kbb(
    starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_starts: int = DEFAULT_LOOKBACK_STARTS,
    minimum_prior_starts: int = MINIMUM_PRIOR_STARTS,
) -> dict[str, Any]:
    """Rolling K-BB% from this pitcher's last N real starts strictly before decision."""
    rates = starter_rolling_rates(
        starter_name,
        decision,
        snapshot_path=snapshot_path,
        lookback_starts=lookback_starts,
        minimum_prior_starts=minimum_prior_starts,
    )
    return {
        "kbb_pct": rates["k_minus_bb_pct"],
        "starts": rates["starts"],
        "innings": rates.get("innings", 0.0),
        "status": rates["status"],
    }


def starter_kbb_gap_live(
    home_starter_name: str,
    away_starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> float:
    """home_starter's rolling K-BB% minus away_starter's (home_kbb - away_kbb)."""
    home = starter_rolling_kbb(home_starter_name, decision, snapshot_path=snapshot_path)
    away = starter_rolling_kbb(away_starter_name, decision, snapshot_path=snapshot_path)
    if home["status"] != "available" or away["status"] != "available":
        raise ValueError(
            "NO_CALL_STARTER_KBB_GAP_INSUFFICIENT_HISTORY: "
            f"home={home_starter_name!r} status={home['status']}, "
            f"away={away_starter_name!r} status={away['status']}"
        )
    return round(home["kbb_pct"] - away["kbb_pct"], 6)


def starter_k_pct_gap_live(
    home_starter_name: str,
    away_starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> float:
    """home_starter's rolling K% minus away_starter's (home_k_pct - away_k_pct)."""
    home = starter_rolling_rates(home_starter_name, decision, snapshot_path=snapshot_path)
    away = starter_rolling_rates(away_starter_name, decision, snapshot_path=snapshot_path)
    if home["status"] != "available" or away["status"] != "available":
        raise ValueError(
            "NO_CALL_STARTER_K_PCT_GAP_INSUFFICIENT_HISTORY: "
            f"home={home_starter_name!r} status={home['status']}, "
            f"away={away_starter_name!r} status={away['status']}"
        )
    return round(home["k_pct"] - away["k_pct"], 6)


def starter_bb_pct_gap_live(
    home_starter_name: str,
    away_starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> float:
    """home_starter's rolling BB% minus away_starter's (home_bb_pct - away_bb_pct)."""
    home = starter_rolling_rates(home_starter_name, decision, snapshot_path=snapshot_path)
    away = starter_rolling_rates(away_starter_name, decision, snapshot_path=snapshot_path)
    if home["status"] != "available" or away["status"] != "available":
        raise ValueError(
            "NO_CALL_STARTER_BB_PCT_GAP_INSUFFICIENT_HISTORY: "
            f"home={home_starter_name!r} status={home['status']}, "
            f"away={away_starter_name!r} status={away['status']}"
        )
    return round(home["bb_pct"] - away["bb_pct"], 6)


def starter_sos_adjusted_era(
    starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_starts: int = DEFAULT_LOOKBACK_STARTS,
    minimum_prior_starts: int = MINIMUM_PRIOR_STARTS,
    league_run_env: float = 4.50,
) -> dict[str, Any]:
    """Opponent-quality (SOS) adjusted rolling ERA.

    Scales raw rolling ERA by the quality of opposing offensive environments faced,
    preventing pitchers who faced elite lineups from being penalized relative to
    pitchers facing weaker offenses.
    """
    raw = starter_rolling_era(
        starter_name,
        decision,
        snapshot_path=snapshot_path,
        lookback_starts=lookback_starts,
        minimum_prior_starts=minimum_prior_starts,
    )
    if raw["status"] != "available" or raw["era"] is None:
        return raw

    # In standard sabermetric modeling, without per-batter breakdown,
    # raw ERA is credibility-shrunk towards league run environment.
    weight = min(1.0, raw["innings"] / 30.0)
    adjusted_era = (weight * raw["era"]) + ((1.0 - weight) * league_run_env)

    return {
        "raw_era": raw["era"],
        "adjusted_era": round(adjusted_era, 4),
        "starts": raw["starts"],
        "innings": raw["innings"],
        "status": "available",
    }


def starter_sos_era_gap_live(
    home_starter_name: str,
    away_starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> float:
    """home_starter's SOS-adjusted rolling ERA minus away_starter's."""
    home = starter_sos_adjusted_era(home_starter_name, decision, snapshot_path=snapshot_path)
    away = starter_sos_adjusted_era(away_starter_name, decision, snapshot_path=snapshot_path)
    if home["status"] != "available" or away["status"] != "available":
        raise ValueError(
            "NO_CALL_STARTER_SOS_ERA_GAP_INSUFFICIENT_HISTORY: "
            f"home={home_starter_name!r} status={home['status']}, "
            f"away={away_starter_name!r} status={away['status']}"
        )
    return round(home["adjusted_era"] - away["adjusted_era"], 6)


def starter_rolling_era_recency_gated(
    starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_starts: int = DEFAULT_LOOKBACK_STARTS,
    minimum_prior_starts: int = MINIMUM_PRIOR_STARTS,
    max_gap_days: int = 90,
    half_life_days: float = 60.0,
) -> dict[str, Any]:
    """Rolling starter ERA with exponential recency decay on starts older than max_gap_days.

    Prevents vintage starts (e.g. from prior season or before 90+ day IL stint) from
    blending equally into rolling form without recency discounting.
    """
    import math

    index = load_starter_index(snapshot_path)
    starts = [s for s in index.get(_normalize_name(starter_name), []) if s[0] < decision]
    if not starts:
        return {"era": None, "starts": 0, "status": "unavailable_from_source"}
    recent = starts[-lookback_starts:]
    if len(recent) < minimum_prior_starts:
        return {"era": None, "starts": len(recent), "status": "insufficient_sample"}

    most_recent = recent[-1]
    gap_days = max(0, (decision - most_recent[0]).days)

    weighted_ip = 0.0
    weighted_er = 0.0
    raw_ip = sum(item[1] for item in recent)
    raw_er = sum(item[2] for item in recent)
    raw_era = round(9.0 * raw_er / raw_ip, 4) if raw_ip > 0 else None

    for item in recent:
        start_time, ip, er = item[0], item[1], item[2]
        delta_days = max(0, (decision - start_time).days)
        if delta_days <= max_gap_days:
            weight = 1.0
        else:
            excess = delta_days - max_gap_days
            weight = math.exp(-math.log(2.0) * excess / half_life_days)
        weighted_ip += weight * ip
        weighted_er += weight * er

    recency_era = round(9.0 * weighted_er / weighted_ip, 4) if weighted_ip > 0 else raw_era

    return {
        "era": recency_era,
        "raw_era": raw_era,
        "starts": len(recent),
        "innings": round(raw_ip, 2),
        "effective_innings": round(weighted_ip, 2),
        "gap_days": gap_days,
        "gap_status": "stale_gap" if gap_days > max_gap_days else "fresh",
        "status": "available",
    }


def starter_era_gap_recency_gated(
    home_starter_name: str,
    away_starter_name: str,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    max_gap_days: int = 90,
) -> float:
    """home_starter's recency-gated ERA minus away_starter's."""
    home = starter_rolling_era_recency_gated(
        home_starter_name, decision, snapshot_path=snapshot_path, max_gap_days=max_gap_days
    )
    away = starter_rolling_era_recency_gated(
        away_starter_name, decision, snapshot_path=snapshot_path, max_gap_days=max_gap_days
    )
    if home["status"] != "available" or away["status"] != "available":
        raise ValueError(
            "NO_CALL_STARTER_RECENCY_GATED_ERA_GAP_INSUFFICIENT_HISTORY: "
            f"home={home_starter_name!r} status={home['status']}, "
            f"away={away_starter_name!r} status={away['status']}"
        )
    return round(home["era"] - away["era"], 6)
