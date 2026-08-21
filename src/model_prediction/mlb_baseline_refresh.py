"""Periodic refresh of MLB's season-dependent real-data baselines.

Several Trend Engine constants (park factors, league-average ERA/K%/BB%,
league relief ERA) are computed from this project's own real historical
data rather than hand-typed guesses (see the 2026-07-29 Measured Edge
investigation). Real baselines drift as a season progresses -- new games
get played, run environments shift, a team can even change ballparks (the
Athletics did) -- so these need periodic regeneration from fresh data
rather than a one-time snapshot. This module is that regeneration job:
self-throttled via a small state file so it actually runs on a sane cadence
(default weekly) even though it's wired into the every-3-hours daily cron.

Cadence: weekly is the default and matches the actual rate these numbers
move in practice (park factors and league rates are full-season aggregates;
day-to-day noise dominates anything a more frequent refresh would catch).
Nothing here needs faster than that, and there's no benefit to slower --
a monthly refresh would mean occasionally serving a park factor that's
several thousand games out of date after a big shift (a relocation, a
rules change). ``--force`` bypasses the throttle for manual runs.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .domain import utc_now
from .features.bullpen import _baseball_innings

DEFAULT_MIN_DAYS_BETWEEN_REFRESH = 7.0
DEFAULT_PARK_PRIOR_GAMES = 50.0
DEFAULT_MIN_GAMES_PER_PARK = 20

_LEGACY_PARK_NAMES = {"Oakland Athletics", "American All-Stars", "National All-Stars"}


def _state_path(data_root: str | Path) -> Path:
    return Path(data_root) / "mlb_baseline_refresh_state.json"


def _load_state(data_root: str | Path) -> dict[str, Any]:
    path = _state_path(data_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(data_root: str | Path, state: dict[str, Any]) -> None:
    path = _state_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compute_park_factors(
    games_path: str | Path,
    *,
    prior_games: float = DEFAULT_PARK_PRIOR_GAMES,
    min_games: int = DEFAULT_MIN_GAMES_PER_PARK,
) -> tuple[dict[str, float], dict[str, int], str, str, float | None]:
    """Real per-park run factor from completed real games.

    Returns (factors, sample_sizes, earliest_date, latest_date,
    league_runs_per_team_game). Credibility-weighted shrinkage toward 1.0 by
    games played (``prior_games``) so a thin sample (a mid-season ballpark
    change) doesn't swing as hard as a well-established one.
    """
    by_park: dict[str, list[int]] = {}
    dates: list[str] = []
    with Path(games_path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("status") != "completed":
                continue
            home = row.get("home_team")
            if not home or home in _LEGACY_PARK_NAMES:
                continue
            by_park.setdefault(home, []).append(int(row["away_score"]) + int(row["home_score"]))
            dates.append(str(row["event_start_utc"])[:10])
    by_park = {team: totals for team, totals in by_park.items() if len(totals) >= min_games}
    if not by_park:
        return {}, {}, "", "", None
    all_totals = [total for totals in by_park.values() for total in totals]
    league_avg_total = sum(all_totals) / len(all_totals)
    league_runs_per_team_game = round(league_avg_total / 2, 4)
    factors: dict[str, float] = {}
    sample_sizes: dict[str, int] = {}
    for team, totals in by_park.items():
        n = len(totals)
        empirical = (sum(totals) / n) / league_avg_total
        credibility = n / (n + prior_games)
        shrunk = credibility * empirical + (1 - credibility) * 1.0
        factors[team] = round(shrunk, 3)
        sample_sizes[team] = n
    return factors, sample_sizes, min(dates), max(dates), league_runs_per_team_game


def compute_league_rates(snapshot_path: str | Path) -> tuple[dict[str, float], int, str, str]:
    """Real league-average starter ERA/K%/BB% and relief ERA from real boxscores."""
    starter_ip = starter_er = 0.0
    starter_k = starter_bb = starter_bf = 0.0
    relief_ip = relief_er = 0.0
    n_games = 0
    dates: list[str] = []
    path = Path(snapshot_path)
    if not path.exists():
        return {}, 0, "", ""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            snapshot = json.loads(line)
            game_start = str(snapshot.get("game_start_utc") or "")
            if not game_start:
                continue
            dates.append(game_start[:10])
            n_games += 1
            for side_key in ("home", "away"):
                side = snapshot.get(side_key) or {}
                pitcher_order = side.get("pitcher_order") or []
                starter_id = pitcher_order[0] if pitcher_order else None
                for player in side.get("players", []):
                    pitching = player.get("pitching")
                    if not pitching:
                        continue
                    innings = _baseball_innings(pitching.get("inningsPitched"))
                    earned = float(pitching.get("earnedRuns") or 0)
                    if player.get("player_id") == starter_id:
                        starter_ip += innings
                        starter_er += earned
                        starter_k += float(pitching.get("strikeOuts") or 0)
                        starter_bb += float(pitching.get("baseOnBalls") or 0)
                        starter_bf += float(pitching.get("battersFaced") or 0)
                    else:
                        relief_ip += innings
                        relief_er += earned
    if starter_ip <= 0 or starter_bf <= 0 or relief_ip <= 0:
        return {}, n_games, (min(dates) if dates else ""), (max(dates) if dates else "")
    rates = {
        "league_starter_era": round(9 * starter_er / starter_ip, 4),
        "league_strikeout_rate": round(starter_k / starter_bf, 4),
        "league_walk_rate": round(starter_bb / starter_bf, 4),
        "league_relief_era": round(9 * relief_er / relief_ip, 4),
    }
    return rates, n_games, min(dates), max(dates)


def write_park_factors_file(
    path: str | Path,
    factors: dict[str, float],
    sample_sizes: dict[str, int],
    earliest_date: str,
    latest_date: str,
    n_games_total: int,
) -> None:
    version = f"{datetime.now(UTC).date().isoformat()}-empirical"
    lines = [
        '"""MLB park-adjusted run environment.',
        "",
        "AUTO-GENERATED by mlb_baseline_refresh.refresh_park_factors -- do not",
        "hand-edit the table below; edit the computation in mlb_baseline_refresh.py",
        "and regenerate instead (``model-prediction refresh-mlb-baselines``).",
        "",
        (
            f"Computed from {n_games_total} real completed games "
            f"({earliest_date} to {latest_date}), credibility-shrunk toward 1.0 by"
        ),
        "games played. Unknown parks return neutral with an explicit status.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        f'PARK_FACTORS_VERSION = "{version}"',
        "",
        "# Home team display name -> run factor. See module docstring for source.",
        "PARK_RUN_FACTORS: dict[str, float] = {",
    ]
    for team, factor in sorted(factors.items(), key=lambda item: -item[1]):
        lines.append(f'    "{team}": {factor},  # n={sample_sizes[team]}')
    lines += [
        "}",
        "",
        "",
        "def park_factor(home_team: str) -> dict[str, Any]:",
        "    factor = PARK_RUN_FACTORS.get(home_team)",
        "    if factor is None:",
        '        return {"park_factor": 1.0, "status": "unavailable_from_source", "version": PARK_FACTORS_VERSION}',
        '    return {"park_factor": factor, "status": "available", "version": PARK_FACTORS_VERSION}',
        "",
    ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def update_league_rates_in_formula_spec(spec_path: str | Path, rates: dict[str, float]) -> bool:
    """Patch only the four league_* value lines in the yaml, in place.

    A full yaml.safe_dump round-trip would silently discard every hand-written
    comment in this file (it has many, documenting each parameter block) --
    a targeted regex substitution on just these four lines preserves
    everything else untouched.
    """
    path = Path(spec_path)
    text = path.read_text(encoding="utf-8")
    changed = False
    for key, value in rates.items():
        if key == "league_relief_era":
            continue  # lives in bullpen.py, not this spec
        pattern = re.compile(rf"^{re.escape(key)}:\s*[0-9.]+\s*$", re.MULTILINE)
        replacement = f"{key}: {value}"
        new_text, count = pattern.subn(replacement, text)
        if count == 1:
            text = new_text
            changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def update_league_relief_era_in_bullpen_module(bullpen_path: str | Path, value: float) -> bool:
    path = Path(bullpen_path)
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"^LEAGUE_RELIEF_ERA = [0-9.]+\s*$", re.MULTILINE)
    new_text, count = pattern.subn(f"LEAGUE_RELIEF_ERA = {value}", text)
    if count == 1:
        path.write_text(new_text, encoding="utf-8")
        return True
    return False


def refresh_if_due(
    data_root: str | Path,
    project_root: str | Path,
    *,
    min_days: float = DEFAULT_MIN_DAYS_BETWEEN_REFRESH,
    force: bool = False,
) -> dict[str, Any]:
    """Regenerate park factors and league rates if the last refresh is stale.

    Returns a report dict either way (``status: skipped_recent`` or
    ``status: refreshed``/``status: no_data``) so callers (the daily
    pipeline, or a manual CLI run) can log what happened without needing to
    inspect the state file themselves.
    """
    state = _load_state(data_root)
    last_refreshed = state.get("last_refreshed_utc")
    if not force and last_refreshed:
        try:
            age_days = (utc_now() - datetime.fromisoformat(last_refreshed)).total_seconds() / 86400
        except ValueError:
            age_days = min_days + 1
        if age_days < min_days:
            return {
                "status": "skipped_recent",
                "last_refreshed_utc": last_refreshed,
                "age_days": round(age_days, 2),
                "min_days": min_days,
            }
    project_root = Path(project_root)
    games_path = Path(data_root) / "historical" / "mlb_games_all.jsonl"
    snapshot_path = Path(data_root) / "mlb_statsapi" / "game_snapshots.jsonl"
    factors, sample_sizes, park_start, park_end, league_runs_per_team_game = compute_park_factors(games_path)
    rates, n_games, rate_start, rate_end = compute_league_rates(snapshot_path)
    if league_runs_per_team_game is not None:
        rates = {**rates, "league_runs_per_team_game": league_runs_per_team_game}
    if not factors and not rates:
        return {"status": "no_data"}
    result: dict[str, Any] = {"status": "refreshed", "refreshed_utc": utc_now().isoformat()}
    if factors:
        write_park_factors_file(
            project_root / "src/model_prediction/features/park_factors.py",
            factors,
            sample_sizes,
            park_start,
            park_end,
            sum(sample_sizes.values()),
        )
        result["park_factors"] = {"n_teams": len(factors), "date_range": [park_start, park_end]}
    if rates:
        update_league_rates_in_formula_spec(
            project_root / "config/models/mlb-analyst-poisson-trend-v0.3.yaml", rates
        )
        if "league_relief_era" in rates:
            update_league_relief_era_in_bullpen_module(
                project_root / "src/model_prediction/features/bullpen.py",
                rates["league_relief_era"],
            )
        result["league_rates"] = {**rates, "n_games": n_games, "date_range": [rate_start, rate_end]}
    _save_state(
        data_root,
        {
            "last_refreshed_utc": result["refreshed_utc"],
            "park_factors": result.get("park_factors"),
            "league_rates": result.get("league_rates"),
        },
    )
    return result
