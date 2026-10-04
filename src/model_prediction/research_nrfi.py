"""Corrected first-inning research features shared by training and inference.

Historical snapshots remain retrospective reconstructions. Event ordering plus
a two-calendar-day embargo is not proof of historical publication availability.
No production model, legacy builder, or ledger is changed by this module.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .domain import parse_utc
from .models.mlb_first_inning import (
    FEATURE_NAMES as LEGACY_FEATURE_NAMES,
)
from .models.mlb_first_inning import (
    MIN_STARTER_IP_FOR_FIP,
    PARK_PRIOR_GAMES,
    STARTER_PRIOR_STARTS,
    TEAM_POOL_LOOKBACK_GAMES,
    TEAM_PRIOR_GAMES,
    _days_rest,
    _parse_ip,
    _shrink,
    _top3_composite,
)
from .models.mlb_first_inning_live import _normalize_starter_name
from .research_generation import ResearchRow, digest, file_hash

FEATURE_NAMES = tuple(
    name.replace("top3_composite", "recent_batter_composite") for name in LEGACY_FEATURE_NAMES
)
MODEL_ID = "mlb-nrfi-clean-research-20260911-v2"
FAMILY = "nrfi_clean_history_v2"


def count(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or not float(value).is_integer()
    ):
        raise ValueError("invalid_explicit_count")
    return int(value)


def validate_snapshot(raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict):
        raise TypeError("snapshot_must_be_object")
    status = str(raw.get("status", ""))
    if status not in {"Final", "Completed Early", "Completed Early: Rain", "Completed Early: Wet Grounds"}:
        raise ValueError("not_completed")
    if raw.get("first_inning_complete") is False:
        raise ValueError("first_inning_not_completed")
    start = parse_utc(raw["game_start_utc"])
    if parse_utc(raw["observed_at_utc"]) <= start:
        raise ValueError("completed_result_observed_before_start")
    if count(raw["game_pk"]) == 0:
        raise ValueError("invalid_game_identity")
    runs = sum(count(raw[f"first_inning_runs_{side}"]) for side in ("away", "home"))
    if count(raw["yrfi"]) != int(runs > 0):
        raise ValueError("first_inning_label_mismatch")
    if (
        not raw.get("venue_name")
        or not raw["home"].get("team_name")
        or not raw["away"].get("team_name")
        or raw["home"]["team_name"] == raw["away"]["team_name"]
    ):
        raise ValueError("missing_or_ambiguous_matchup_identity")
    all_ids: set[int] = set()
    for side in ("away", "home"):
        team = raw[side]
        if not team.get("pitcher_order"):
            raise ValueError("missing_historical_starter")
        ids = [count(p["player_id"]) for p in team["players"]]
        if len(set(ids)) != len(ids) or all_ids.intersection(ids) or 0 in ids:
            raise ValueError("duplicate_or_cross_team_player_identity")
        all_ids.update(ids)
        starter = [p for p in team["players"] if p["player_id"] == team["pitcher_order"][0]]
        if len(starter) != 1 or not starter[0].get("name") or not starter[0].get("pitching"):
            raise ValueError("missing_historical_starter_record")
        for player in team["players"]:
            batting, pitching = player.get("batting") or {}, player.get("pitching") or {}
            if batting:
                for key in (
                    "plateAppearances",
                    "hits",
                    "baseOnBalls",
                    "hitByPitch",
                    "strikeOuts",
                    "totalBases",
                ):
                    count(batting[key])
            if pitching:
                for key in ("strikeOuts", "baseOnBalls", "battersFaced", "homeRuns"):
                    count(pitching[key])
                ip = str(pitching["inningsPitched"])
                whole, _, fraction = ip.partition(".")
                if not whole.isdigit() or fraction not in {"", "0", "1", "2"}:
                    raise ValueError("invalid_baseball_innings")


def load_snapshots(
    path: Path, *, observed_before: datetime | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    conflicts: set[int] = set()
    excluded: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        raw: dict[str, Any] = {}
        try:
            raw = json.loads(line)
            validate_snapshot(raw)
            if observed_before is not None and parse_utc(raw["observed_at_utc"]) > observed_before:
                raise ValueError("snapshot_not_observed_by_decision")
            identity = raw["game_pk"]
            if identity in by_id:
                if digest(by_id[identity]) != digest(raw):
                    conflicts.add(identity)
                else:
                    excluded.append(
                        {"line": line_number, "game_pk": identity, "reason": "duplicate_identical"}
                    )
            else:
                by_id[identity] = raw
        except (KeyError, ValueError, TypeError) as exc:
            excluded.append(
                {
                    "line": line_number,
                    "game_pk": raw.get("game_pk") if isinstance(raw, dict) else None,
                    "reason": str(exc),
                }
            )
    for identity in conflicts:
        del by_id[identity]
        excluded.append({"game_pk": identity, "reason": "conflicting_event_versions"})
    accepted = sorted(by_id.values(), key=lambda r: (parse_utc(r["game_start_utc"]), r["game_pk"]))
    return accepted, {
        "source_sha256": file_hash(path),
        "accepted": len(accepted),
        "excluded": excluded,
        "exclusion_counts": dict(Counter(r["reason"] for r in excluded)),
        "historical_publication_times_verified": False,
    }


def cutoff(start: datetime, observed: datetime) -> datetime:
    return min(
        observed.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0),
        start.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1),
    )


class FirstInningState:
    def __init__(self, priors: dict[str, float]):
        self.priors = priors
        self.teams: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0] * 3)
        self.parks: dict[str, list[float]] = defaultdict(lambda: [0.0] * 2)
        self.starters: dict[int, list[float]] = defaultdict(lambda: [0.0] * 7)
        self.names: dict[str, set[int]] = defaultdict(set)
        self.last: dict[int, datetime] = {}
        self.batters: dict[int, list[float]] = defaultdict(lambda: [0.0] * 4)
        self.pools: dict[str, deque] = defaultdict(lambda: deque(maxlen=TEAM_POOL_LOOKBACK_GAMES))
        self.latest = ""

    def observe(self, raw: dict[str, Any]) -> None:
        start = parse_utc(raw["game_start_utc"])
        for side, opponent in (("away", "home"), ("home", "away")):
            team = raw[side]
            scored, allowed = (
                float(raw[f"first_inning_runs_{side}"]),
                float(raw[f"first_inning_runs_{opponent}"]),
            )
            totals = self.teams[team["team_name"], side]
            totals[0] += 1
            totals[1] += scored
            totals[2] += allowed
            sid = team["pitcher_order"][0]
            player = next(p for p in team["players"] if p["player_id"] == sid)
            self.names[_normalize_starter_name(player["name"])].add(sid)
            pitching = player["pitching"]
            increment = [
                1.0,
                allowed,
                _parse_ip(pitching["inningsPitched"]),
                pitching["strikeOuts"],
                pitching["baseOnBalls"],
                pitching["battersFaced"],
                pitching["homeRuns"],
            ]
            self.starters[sid] = [a + b for a, b in zip(self.starters[sid], increment, strict=True)]
            self.last[sid] = start
            participants = {}
            # Critically, this pool contains only THIS team's players.
            for batter in team["players"]:
                stats = batter.get("batting") or {}
                pa = stats.get("plateAppearances", 0)
                if pa <= 0:
                    continue
                pid = batter["player_id"]
                increment = [
                    pa,
                    stats["hits"] + stats["baseOnBalls"] + stats["hitByPitch"],
                    stats["baseOnBalls"] - stats["strikeOuts"],
                    stats["totalBases"] - stats["hits"],
                ]
                self.batters[pid] = [a + b for a, b in zip(self.batters[pid], increment, strict=True)]
                participants[pid] = pa
            self.pools[team["team_name"]].append((start, participants))
        park = self.parks[raw["venue_name"]]
        park[0] += 1
        park[1] += raw["first_inning_runs_home"] + raw["first_inning_runs_away"]
        self.latest = start.date().isoformat()

    def features(self, fixture: dict[str, Any]) -> dict[str, float]:
        start = parse_utc(fixture["event_start_utc"])
        result = {}
        for side, opponent in (("away", "home"), ("home", "away")):
            name = fixture[f"{side}_team"]
            ids = self.names.get(_normalize_starter_name(fixture[f"{side}_starter_name"]), set())
            if len(ids) > 1:
                raise ValueError("ambiguous_starter_name")
            sid = next(iter(ids)) if ids else None
            starter = self.starters.get(sid, [0.0] * 7) if sid is not None else [0.0] * 7
            team = self.teams.get((name, side), [0.0] * 3)

            def shrunk(total: float, n: float, mass: float, prior: float) -> float:
                return _shrink(total / n, int(n), mass, prior) if n else prior

            result[f"{side}_starter_opp_1st_runs"] = round(
                shrunk(starter[1], starter[0], STARTER_PRIOR_STARTS, self.priors[f"half_{opponent}"]), 4
            )
            result[f"{side}_team_1st_scored_{side}"] = round(
                shrunk(team[1], team[0], TEAM_PRIOR_GAMES, self.priors[f"half_{side}"]), 4
            )
            result[f"{side}_team_1st_allowed_{side}"] = round(
                shrunk(team[2], team[0], TEAM_PRIOR_GAMES, self.priors[f"half_{opponent}"]), 4
            )
            if starter[2] <= MIN_STARTER_IP_FOR_FIP:
                fip, k, bb = (self.priors[n] for n in ("fip", "k_pct", "bb_pct"))
            else:
                ip, so, walks, bf, hr = starter[2:]
                fip = (13 * hr + 3 * walks - 2 * so) / ip + 3.10
                k, bb = so / bf if bf else so / max(1, 3 * ip), walks / bf if bf else walks / max(1, 3 * ip)
            result[f"{side}_starter_fip"] = round(fip, 3)
            result[f"{side}_starter_k_pct"] = round(k, 4)
            result[f"{side}_starter_bb_pct"] = round(bb, 4)
            result[f"{side}_starter_starts"] = round(math.log1p(starter[0]), 4)
            result[f"{side}_starter_days_rest"] = round(
                _days_rest(self.last.get(sid) if sid is not None else None, start, 4.0), 2
            )
            result[f"{side}_recent_batter_composite"] = round(
                _top3_composite(name, self.batters, list(self.pools.get(name, [])), self.priors), 5
            )
        park = self.parks.get(fixture["venue_name"], [0.0] * 2)
        result["park_1st_runs"] = round(
            _shrink(park[1] / park[0], int(park[0]), PARK_PRIOR_GAMES, self.priors["total"])
            if park[0]
            else self.priors["total"],
            4,
        )
        return {name: result[name] for name in FEATURE_NAMES}


def historical_fixture(raw: dict[str, Any]) -> dict[str, Any]:
    fixture = {
        "event_id": str(raw["game_pk"]),
        "event_start_utc": raw["game_start_utc"],
        "venue_name": raw["venue_name"],
    }
    for side in ("away", "home"):
        team = raw[side]
        player = next(p for p in team["players"] if p["player_id"] == team["pitcher_order"][0])
        fixture[f"{side}_team"] = team["team_name"]
        fixture[f"{side}_starter_name"] = player["name"]
    return fixture


def build_rows(snapshots: list[dict[str, Any]], priors: dict[str, float]) -> list[ResearchRow]:
    state = FirstInningState(priors)
    rows = []
    index = 0
    for raw in snapshots:
        start = parse_utc(raw["game_start_utc"])
        before = cutoff(start, start)
        while index < len(snapshots) and parse_utc(snapshots[index]["game_start_utc"]) < before:
            state.observe(snapshots[index])
            index += 1
        features = state.features(historical_fixture(raw))
        rows.append(
            ResearchRow(
                str(raw["game_pk"]),
                start.date().isoformat(),
                tuple(features[n] for n in FEATURE_NAMES),
                int(raw["yrfi"] == 0),
                0.0,
                float(raw["first_inning_runs_home"] + raw["first_inning_runs_away"]),
                state.latest or "NO_PRIOR_GAMES",
            )
        )
    return rows


def live_features(
    path: Path, fixture: dict[str, Any], observed: datetime, priors: dict[str, float]
) -> tuple[dict[str, float], str]:
    snapshots, _ = load_snapshots(path, observed_before=observed)
    state = FirstInningState(priors)
    before = cutoff(parse_utc(fixture["event_start_utc"]), observed)
    for raw in snapshots:
        if parse_utc(raw["game_start_utc"]) < before:
            state.observe(raw)
    return state.features(fixture), state.latest or "NO_PRIOR_GAMES"
