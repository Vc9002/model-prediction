"""Backfill CollegeFootballData (CFBD) preseason priors and betting lines for NCAAF v7 research.

Fetches season-level endpoints only (one call per season per dataset; /recruiting/teams and /teams are
one call in total), caches every raw response under ``<data-root>/research/ncaaf_v7/cfbd/raw`` and
writes, under ``<data-root>/research/ncaaf_v7/cfbd/``:

  team_season_priors.jsonl  one row per (season, school): returning production (offense only),
                            talent (this and last year), recruiting classes Y..Y-3, last season's SP+,
                            preseason polls, portal in/out, ESPN team id, observed_at_utc, flags
  lines.jsonl               one row per (game, provider): open / close spread and total, moneyline
                            implied probabilities (no American odds), ESPN event id when matched

and the coverage report ``outputs/ncaaf/cfbd_coverage.{json,md}`` in the repo.

Budget: the key's monthly calls are shared with CBBD. The client refuses past ``--max-calls`` attempts
recorded in the cache's call ledger (all processes, retries included) and below ``--quota-floor``
remaining. A rerun refetches nothing.

The key comes from ``CFBD_API_KEY`` (environment, else the main checkout's ``.env``); it is never
printed, logged or written.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from model_prediction.data_sources import cfbd
from model_prediction.runtime_paths import load_dotenv_into_environ

FIRST_SEASON, LAST_SEASON = 2014, 2026
PORTAL_FIRST = 2020  # CFBD portal data starts around 2021; 2020 is probed once and kept if non-empty
COVERAGE_JSON = REPO / "outputs" / "ncaaf" / "cfbd_coverage.json"
COVERAGE_MD = REPO / "outputs" / "ncaaf" / "cfbd_coverage.md"

_logger = logging.getLogger("cfbd_backfill")


def _main_checkout_env() -> Path:
    """``.env`` of the main checkout (a worktree has none of its own)."""
    try:
        common = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return Path(common).parent / ".env"
    except (OSError, subprocess.CalledProcessError):
        return REPO / ".env"


def _season_of(start_utc: str) -> int:
    year, month = int(start_utc[:4]), int(start_utc[5:7])
    return year - 1 if month <= 6 else year


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    os.replace(tmp, path)


class Fetcher:
    """Wraps the client: records per-dataset outcomes instead of aborting on a refused endpoint."""

    def __init__(self, client: cfbd.CFBDClient) -> None:
        self.client = client
        self.outcomes: dict[str, dict[str, Any]] = {}

    def get(self, label: str, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        cached = self.client.is_cached(endpoint, params)
        try:
            data = self.client.fetch(endpoint, params)
        except cfbd.CFBDQuotaError:
            raise
        except (cfbd.CFBDAuthError, RuntimeError) as exc:
            self.outcomes[label] = {"status": "refused_or_error", "error": str(exc)}
            _logger.warning("%s: %s", label, exc)
            return None
        meta = self.client.cached_meta(endpoint, params) or {}
        self.outcomes[label] = {
            "status": "cached" if cached else "fetched",
            "rows": len(data) if isinstance(data, list) else None,
            "fetched_at_utc": meta.get("fetched_at_utc"),
        }
        return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", type=Path, default=REPO / "data")
    parser.add_argument("--first-season", type=int, default=FIRST_SEASON)
    parser.add_argument("--last-season", type=int, default=LAST_SEASON)
    parser.add_argument("--max-calls", type=int, default=cfbd.DEFAULT_MAX_CALLS)
    parser.add_argument("--quota-floor", type=int, default=cfbd.DEFAULT_QUOTA_FLOOR)
    parser.add_argument("--offline", action="store_true", help="normalise from the cache only")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out_root = args.data_root / "research" / "ncaaf_v7" / "cfbd"
    snapshot_path = args.data_root / "research" / "ncaaf_v6" / "history_snapshot.jsonl"
    load_dotenv_into_environ(_main_checkout_env())
    client = cfbd.CFBDClient(
        cache_root=out_root / "raw", max_calls=args.max_calls, quota_floor=args.quota_floor
    )
    if args.offline:
        client.max_calls = 0  # any cache miss raises CFBDQuotaError instead of calling
    fetch = Fetcher(client)
    ledger_before = client.ledger_calls()
    info_before = None if args.offline else client.info().get("remainingCalls")
    seasons = list(range(args.first_season, args.last_season + 1))
    now = datetime.now(UTC)

    teams = fetch.get("teams", "teams") or []
    recruiting_rows = fetch.get("recruiting_teams_all_years", "recruiting/teams") or []
    talent_rows: list[dict[str, Any]] = []
    returning_rows: list[dict[str, Any]] = []
    sp_rows: list[dict[str, Any]] = []
    raw_by_season: dict[int, dict[str, Any]] = defaultdict(dict)
    for year in [seasons[0] - 1, *seasons]:
        talent = fetch.get(f"talent_{year}", "talent", {"year": year})
        talent_rows += talent or []
        raw_by_season[year]["talent"] = len(talent or [])
        if year < seasons[-1]:  # SP+ is end-of-season: year Y-1 only ever feeds season Y
            sp = fetch.get(f"sp_{year}", "ratings/sp", {"year": year})
            sp_rows += sp or []
            raw_by_season[year]["sp_final"] = len(sp or [])
    for year in seasons:
        ret = fetch.get(f"returning_{year}", "player/returning", {"year": year})
        returning_rows += ret or []
        raw_by_season[year]["returning"] = len(ret or [])
        raw_by_season[year]["rankings_weeks"] = len(
            fetch.get(f"rankings_{year}", "rankings", {"year": year}) or []
        )
        raw_by_season[year]["lines_games"] = len(fetch.get(f"lines_{year}", "lines", {"year": year}) or [])
        if year >= PORTAL_FIRST:
            raw_by_season[year]["portal"] = len(
                fetch.get(f"portal_{year}", "player/portal", {"year": year}) or []
            )
    for year, _school in cfbd.parse_recruiting(recruiting_rows):
        raw_by_season[year]["recruiting_classes"] = raw_by_season[year].get("recruiting_classes", 0) + 1

    snapshot = _read_jsonl(snapshot_path)
    events = cfbd.espn_event_index(snapshot)
    verified_ids = {r["home_id"] for r in snapshot if r.get("home_id")} | {
        r["away_id"] for r in snapshot if r.get("away_id")
    }
    espn_games_by_season = Counter(_season_of(r["start_utc"]) for r in snapshot)
    directory = cfbd.CFBDTeamDirectory(teams, verified_ids)
    returning = cfbd.parse_returning(returning_rows)
    talent = cfbd.parse_talent(talent_rows)
    recruiting = cfbd.parse_recruiting(recruiting_rows)
    sp = cfbd.parse_sp(sp_rows)

    prior_rows: list[dict[str, Any]] = []
    line_rows: list[dict[str, Any]] = []
    season_report: dict[int, dict[str, Any]] = {}
    portal_dates: dict[int, dict[str, str]] = {}
    for year in seasons:
        games = client.fetch("lines", {"year": year}) if client.is_cached("lines", {"year": year}) else []
        starts = [cfbd.parse_utc(g["startDate"]) for g in games if g.get("seasonType") == "regular"]
        observed = min(starts) if starts else datetime(year, 8, 24, tzinfo=UTC)
        polls_raw = (
            client.fetch("rankings", {"year": year}) if client.is_cached("rankings", {"year": year}) else []
        )
        polls = cfbd.parse_preseason_polls(polls_raw)
        portal_agg = None
        portal_stats = None
        if year >= PORTAL_FIRST and client.is_cached("player/portal", {"year": year}):
            portal_raw = client.fetch("player/portal", {"year": year})
            if portal_raw:
                portal_agg, portal_stats = cfbd.aggregate_portal(portal_raw, observed)
                stamps = sorted(str(r["transferDate"]) for r in portal_raw if r.get("transferDate"))
                portal_dates[year] = {"first": stamps[0][:10], "last": stamps[-1][:10]} if stamps else {}
        fetched = [
            (client.cached_meta(ep, p) or {}).get("fetched_at_utc")
            for ep, p in (
                ("player/returning", {"year": year}),
                ("talent", {"year": year}),
                ("recruiting/teams", {}),
                ("rankings", {"year": year}),
            )
        ]
        fetched_at = max((f for f in fetched if f), default=None)
        # fetched between this season's first kickoff and the following February: values are live
        in_season = fetched_at is not None and observed <= cfbd.parse_utc(fetched_at) < datetime(
            year + 1, 2, 1, tzinfo=UTC
        )
        inputs = cfbd.SeasonInputs(returning, talent, recruiting, sp, polls, portal_agg)
        rows = cfbd.build_team_season_priors(
            year, observed, directory, inputs, fetched_at_utc=fetched_at, in_season_fetch=in_season
        )
        prior_rows += rows
        lines_meta = client.cached_meta("lines", {"year": year}) or {}
        lines_as_of = (
            cfbd.parse_utc(lines_meta["fetched_at_utc"]) if lines_meta.get("fetched_at_utc") else now
        )
        lrows = cfbd.parse_lines(games, events, lines_as_of)
        line_rows += lrows
        season_report[year] = _season_summary(
            year,
            observed,
            rows,
            games,
            lrows,
            portal_stats,
            espn_games_by_season.get(year, 0),
            raw_by_season[year],
            events,
        )
        season_report[year]["portal_transfer_dates"] = portal_dates.get(year)

    _write_jsonl(out_root / "team_season_priors.jsonl", prior_rows)
    _write_jsonl(out_root / "lines.jsonl", line_rows)
    info_after = None if args.offline else client.info().get("remainingCalls")
    report = {
        "generated_at_utc": cfbd.iso_utc(now),
        "outputs": {
            "team_season_priors": str(out_root / "team_season_priors.jsonl"),
            "lines": str(out_root / "lines.jsonl"),
            "raw_cache": str(out_root / "raw"),
        },
        "calls": {
            "ledger_before": ledger_before,
            "ledger_after": client.ledger_calls(),
            "this_run_http_attempts": client.calls_made,
            "remaining_before": info_before,
            "remaining_after": info_after,
            "note": "/info does not consume quota (two back-to-back /info calls left remainingCalls unchanged)",
        },
        "ledger": _ledger_summary(client),
        "datasets": fetch.outcomes,
        "seasons": season_report,
        "unmatched_prior_names": sorted(
            {(r["cfbd_team"], r["espn_match_status"]) for r in prior_rows if r["espn_team_id"] is None}
        ),
        "name_check": _name_check(prior_rows, snapshot, directory),
        "totals": {"team_season_priors": len(prior_rows), "lines": len(line_rows)},
    }
    COVERAGE_JSON.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE_JSON.write_text(
        json.dumps(report, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    COVERAGE_MD.write_text(_render_md(report), encoding="utf-8")
    _logger.info(
        "wrote %d prior rows, %d line rows; ledger %d -> %d",
        len(prior_rows),
        len(line_rows),
        ledger_before,
        client.ledger_calls(),
    )
    return 0


def _season_summary(
    year: int,
    observed: datetime,
    priors: list[dict[str, Any]],
    games: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    portal_stats: cfbd.PortalStats | None,
    espn_games: int,
    raw_counts: dict[str, Any],
    events: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    fbs = [r for r in priors if r["classification_current"] == "fbs"]
    game_status = Counter(cfbd.event_match(g, events) for g in games)
    return {
        "observed_at_utc": cfbd.iso_utc(observed),
        "raw_rows": raw_counts,
        "priors_rows": len(priors),
        "priors_fbs_rows": len(fbs),
        "priors_espn_matched": sum(r["espn_team_id"] is not None for r in priors),
        "priors_fbs_espn_matched": sum(r["espn_team_id"] is not None for r in fbs),
        "priors_with": {
            "returning": sum(r["returning_off_ppa_pct"] is not None for r in priors),
            "talent": sum(r["talent"] is not None for r in priors),
            "talent_prev_season": sum(r["talent_prev_season"] is not None for r in priors),
            "recruiting_4yr": sum(r["recruiting_points_4yr_weighted"] is not None for r in priors),
            "sp_prev_season": sum(r["sp_prev_season_rating"] is not None for r in priors),
            "preseason_ap": sum(r["preseason_ap_rank"] is not None for r in priors),
            "portal": sum(bool(r["portal_available"]) for r in priors),
        },
        "lines_games": len(games),
        "lines_games_with_any_line": sum(bool(g.get("lines")) for g in games),
        "lines_rows": len(lines),
        "lines_completed_rows": sum(r["completed"] for r in lines),
        "lines_rows_with_open_spread": sum(r["spread_home_open"] is not None for r in lines),
        "lines_rows_with_ml": sum(r["home_ml_implied_prob_close"] is not None for r in lines),
        "lines_rows_ml_spread_sign_conflict": sum(bool(r["ml_spread_sign_conflict"]) for r in lines),
        "lines_rows_open_close_sign_conflict": sum(bool(r["open_close_sign_conflict"]) for r in lines),
        "lines_providers": dict(Counter(r["provider"] for r in lines)),
        "lines_game_espn_status": dict(game_status),
        "lines_sign_check": _sign_check(games, lines),
        "espn_history_games": espn_games,
        "portal": None if portal_stats is None else vars(portal_stats),
        "portal_zero_fbs_rows": sorted(
            r["cfbd_team"]
            for r in fbs
            if r["portal_available"] and r["portal_out_count"] + r["portal_in_count"] == 0
        ),
    }


def _sign_check(games: list[dict[str, Any]], lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Home-side sign of the spread: closing spread vs final margin, total vs points, and the raw
    ``formattedSpread`` favourite vs the spread's sign."""
    done = [r for r in lines if r["completed"] and r["spread_home_close"] is not None]
    totals = [r for r in done if r["total_close"] is not None]
    mismatch = checked = 0
    for game in games:
        for line in game.get("lines") or []:
            text, spread = line.get("formattedSpread"), line.get("spread")
            if not text or spread in (None, 0):
                continue
            fav_home = str(text).startswith(f"{game['homeTeam']} ")
            if fav_home == str(text).startswith(f"{game['awayTeam']} "):
                continue
            checked += 1
            mismatch += fav_home != (spread < 0)

    def corr(a: list[float], b: list[float]) -> float | None:
        return round(statistics.correlation(a, b), 3) if len(a) >= 20 else None

    return {
        "spread_close_vs_away_margin_corr": corr(
            [r["spread_home_close"] for r in done], [r["away_score"] - r["home_score"] for r in done]
        ),
        "total_close_vs_points_corr": corr(
            [r["total_close"] for r in totals], [r["away_score"] + r["home_score"] for r in totals]
        ),
        "formatted_spread_checked": checked,
        "formatted_spread_sign_mismatch": mismatch,
    }


def _ledger_summary(client: cfbd.CFBDClient) -> dict[str, Any]:
    path = client.cache_root / "_calls.jsonl"
    try:
        records = _read_jsonl(path)
    except OSError:
        return {}
    data = [r for r in records if r.get("endpoint") != "info"]
    remaining = [r["remaining_after"] for r in records if r.get("remaining_after") is not None]
    return {
        "attempts_total": len(records),
        "attempts_excluding_info": len(data),
        "attempts_by_endpoint": dict(Counter(r["endpoint"] for r in data)),
        "non_200_attempts": [r for r in data if r.get("status") != 200],
        "first_remaining_seen": remaining[0] if remaining else None,
        "last_remaining_seen": remaining[-1] if remaining else None,
        "first_at_utc": records[0]["at_utc"] if records else None,
        "last_at_utc": records[-1]["at_utc"] if records else None,
    }


def _name_check(
    priors: list[dict[str, Any]], snapshot: list[dict[str, Any]], directory: cfbd.CFBDTeamDirectory
) -> dict[str, Any]:
    """Does each matched ESPN id's history name contain the CFBD school (or an alternate name)?"""
    espn_names: dict[str, set[str]] = defaultdict(set)
    for r in snapshot:
        for side in ("home", "away"):
            if r.get(f"{side}_id"):
                espn_names[str(r[f"{side}_id"])].add(cfbd.fold_name(str(r[side])))
    agree, disagree = 0, set()
    pairs = {(r["cfbd_team"], r["cfbd_team_id"], r["espn_team_id"]) for r in priors if r["espn_team_id"]}
    for school, cfbd_id, espn_id in pairs:
        team = directory.teams.get(cfbd_id) or {}
        candidates = {cfbd.fold_name(school), cfbd.fold_name(str(team.get("school") or ""))}
        candidates |= {cfbd.fold_name(str(a)) for a in team.get("alternateNames") or []}
        names = espn_names.get(espn_id, set())
        if any(c and any(c in n for n in names) for c in candidates):
            agree += 1
        else:
            disagree.add((school, espn_id, " / ".join(sorted(names))))
    return {"pairs": len(pairs), "agree": agree, "disagree": sorted(disagree)}


VERDICT = """**Verdict: NOT verified as-published. Treat every CFBD prior as current-revised**
(`as_published_verified: false` on every row).

CFBD stores one value per team and season. It serves no publication timestamp, revision history or
as-of parameter for any of these tables, so the API cannot show that a historical value matches what
was public before that season's kickoff. Field by field:

* **Portal OUT** is point-in-time: `transferDate` is the date the player entered the portal, and the
  backfill counts only entries dated at or before `observed_at_utc`. A season's list runs from about
  the previous October to that season's kickoff. Entries dated after kickoff were excluded: 1 in
  2021-2025 (in 2022) and 18 in 2026.
* **Portal IN** is as of the fetch. `destination` has no date and is filled in (or overwritten)
  whenever the player commits, so a commitment made after the cutoff leaks into `portal_in_count`.
  The share with no destination falls from about 40% (2021-22) to about 16-22% (2024-26). That fits a
  field that keeps being updated, not one frozen at a past date.
* **Returning production** covers offense only and is derived from CFBD rosters. Roster snapshots are
  undated, so the value is current-revised by construction. `returning_def_pct` is always null
  because CFBD has no defensive figure.
* **Talent** (247 composite for year Y): the publication date is unknown and could fall after week 1.
  `talent_prev_season` (year Y-1) was public before season Y and is the safe field.
* **Recruiting classes**: class Y is final by February Y, so classes Y..Y-3 predate season Y. 247 can
  still re-rate players later, so small revisions are possible.
* **Prior-season SP+** is the final table for Y-1 (normally published in January). Restated versions
  are possible. The same-season SP+ is never used, because that would be hindsight.
* **Preseason AP / Coaches** (CFBD regular week 1) is a historical record, so revision risk is low.
  For 2023, week 1 was confirmed to be the preseason poll.
* **2026** was fetched on 2026-11-21, mid-season (`in_season_fetch: true`). Its priors are whatever
  CFBD holds now, not what was known at kickoff.
* **Lines**: CFBD's `spread` / `overUnder` is the provider's last stored number, with no timestamp.
  It is read as the close only for completed games (null otherwise). `spreadOpen` / `overUnderOpen`
  have no timestamp either. Each line row's `observed_at_utc` is the kickoff. Open lines and
  moneylines exist only from 2021."""


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def _render_md(report: dict[str, Any]) -> str:
    calls, ledger = report["calls"], report.get("ledger", {})
    out = [
        "# CFBD coverage (NCAAF v7 research priors and lines)",
        "",
        f"Generated {report['generated_at_utc']} by `scripts/cfbd_backfill.py`. Normalized outputs:",
        "",
        f"* `{report['outputs']['team_season_priors']}`",
        f"* `{report['outputs']['lines']}`",
        f"* raw cache `{report['outputs']['raw_cache']}`",
        "",
        "## As-published finding",
        "",
        VERDICT,
        "",
        "## Calls",
        "",
        (
            f"* Data-call attempts in the call ledger (all runs and probes, retries included, /info "
            f"excluded): {ledger.get('attempts_excluding_info')}. By endpoint: "
            f"{ledger.get('attempts_by_endpoint')}."
        ),
        (
            f"* Account remaining: {ledger.get('first_remaining_seen')} at the first call, "
            f"{calls.get('remaining_after')} after this run. The budget is shared with CBBD, so other "
            "sessions may account for part of the difference."
        ),
        f"* {calls['note']}.",
        "",
        "## Rows per dataset per season (raw CFBD rows)",
        "",
        (
            "| season | first kickoff (observed_at) | returning | talent | recruiting classes | SP+ final "
            "(same yr) | portal entries | lines games | ranking weeks |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    seasons = report["seasons"]
    for year in sorted(seasons, key=int):
        s = seasons[year]
        raw = s["raw_rows"]
        out.append(
            f"| {year} | {s['observed_at_utc']} | {raw.get('returning', '')} | {raw.get('talent', '')} | "
            f"{raw.get('recruiting_classes', '')} | {raw.get('sp_final', '')} | {raw.get('portal', '')} | "
            f"{raw.get('lines_games', '')} | {raw.get('rankings_weeks', '')} |"
        )
    out += [
        "",
        "## team_season_priors.jsonl",
        "",
        (
            "| season | rows | ESPN id matched | current-FBS rows matched | returning | talent | talent prev | "
            "recruiting 4yr | SP+ prev | preseason AP | portal |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for year in sorted(seasons, key=int):
        s = seasons[year]
        w = s["priors_with"]
        out.append(
            f"| {year} | {s['priors_rows']} | {s['priors_espn_matched']} "
            f"({_pct(s['priors_espn_matched'], s['priors_rows'])}) | "
            f"{s['priors_fbs_espn_matched']}/{s['priors_fbs_rows']} | {w['returning']} | {w['talent']} | "
            f"{w['talent_prev_season']} | {w['recruiting_4yr']} | {w['sp_prev_season']} | "
            f"{w['preseason_ap']} | {w['portal']} |"
        )
    out += [
        "",
        f"Unmatched prior names (no ESPN id): {report['unmatched_prior_names']}",
        "",
        (
            f"Name cross-check of matched ids: {report['name_check']['agree']} of "
            f"{report['name_check']['pairs']} (school, ESPN id) pairs have an ESPN-history name containing "
            f"the CFBD school or one of its alternate names. Disagreements: "
            f"{report['name_check']['disagree']}"
        ),
        "",
        "## lines.jsonl",
        "",
        (
            "| season | games | with any line | rows (game x provider) | completed rows | rows with open "
            "spread | rows with moneyline (ML/spread sign conflict) | ESPN event match (games) | "
            "ESPN history games |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for year in sorted(seasons, key=int):
        s = seasons[year]
        st = s["lines_game_espn_status"]
        matched = st.get(cfbd.EVENT_SAME_ORIENTATION, 0) + st.get(cfbd.EVENT_SWAPPED, 0)
        out.append(
            f"| {year} | {s['lines_games']} | {s['lines_games_with_any_line']} | {s['lines_rows']} | "
            f"{s['lines_completed_rows']} | {s['lines_rows_with_open_spread']} | {s['lines_rows_with_ml']} "
            f"({s['lines_rows_ml_spread_sign_conflict']}) | "
            f"{matched} ({_pct(matched, s['lines_games'])}; swapped {st.get(cfbd.EVENT_SWAPPED, 0)}) | "
            f"{s['espn_history_games']} |"
        )
    out += [
        "",
        (
            "Before 2016 the ESPN history is empty, so 2014-2015 games are unmatched by design: CFBD game ids "
            "are ESPN event ids, but the backfill only passes one on after the history has confirmed it. The "
            "unmatched 2026 games are later than the history snapshot or not yet played."
        ),
        "",
        "### Sign checks (home-side spread)",
        "",
        (
            "| season | close spread vs away margin (corr) | close total vs points (corr) | formattedSpread "
            "favourite vs spread sign: mismatches / checked | open and close >= 3 on opposite sides (rows) |"
        ),
        "|---|---|---|---|---|",
    ]
    for year in sorted(seasons, key=int):
        sc = seasons[year]["lines_sign_check"]
        out.append(
            f"| {year} | {sc['spread_close_vs_away_margin_corr']} | {sc['total_close_vs_points_corr']} | "
            f"{sc['formatted_spread_sign_mismatch']} / {sc['formatted_spread_checked']} | "
            f"{seasons[year]['lines_rows_open_close_sign_conflict']} |"
        )
    out += [
        "",
        (
            "Rows with conflicting signs are flagged per row (`ml_spread_sign_conflict`, "
            "`open_close_sign_conflict`), never dropped."
        ),
        "",
        "## Portal (per season, cutoff = first kickoff)",
        "",
        (
            "| season | entries | dated after cutoff (excluded) | counted with no destination | "
            "transferDate range | current-FBS rows with zero portal activity |"
        ),
        "|---|---|---|---|---|---|",
    ]
    for year in sorted(seasons, key=int):
        p = seasons[year].get("portal")
        if not p:
            continue
        dates = seasons[year].get("portal_transfer_dates") or {}
        out.append(
            f"| {year} | {p['entries']} | {p['after_cutoff']} | {p['counted_no_destination']} "
            f"({_pct(p['counted_no_destination'], p['counted'])}) | {dates.get('first')} .. "
            f"{dates.get('last')} | {', '.join(seasons[year]['portal_zero_fbs_rows']) or 'none'} |"
        )
    out += [
        "",
        (
            "Joins between tables (and to the portal) use the resolved CFBD team id, not the school name: "
            "CFBD spells three FCS schools differently across years and sources (Albany / UAlbany, "
            "Southeastern Louisiana / SE Louisiana, Saint Francis (PA) / Saint Francis). FIU's zero in 2021 "
            "is in the data: CFBD's 2021 portal list has no FIU entry under any name."
        ),
    ]
    out += [
        "",
        "## Semantics",
        "",
        (
            "* Team keys: `cfbd_team` (the CFBD school name used by the season tables), `cfbd_team_id` and "
            "`espn_team_id`. CFBD team ids are ESPN team ids. An ESPN id is set only when the ESPN history "
            "(`data/research/ncaaf_v6/history_snapshot.jsonl`) has seen that id; otherwise the row has "
            "`NO_CALL_*` in `espn_match_status`."
        ),
        (
            "* `spread_home_*` is from the CFBD home side (negative = home favoured). "
            "`spread_espn_home_*` is from the ESPN home side, with the sign flipped on neutral-site games "
            "where the two disagree (`MATCHED_HOME_AWAY_SWAPPED`)."
        ),
        (
            "* Moneylines are written only as implied probabilities (`*_ml_implied_prob_close`, vig "
            "included) plus a two-way no-vig home probability. No American odds appear in any normalized "
            "file."
        ),
        (
            "* The recruiting weights are fixed in advance, not fitted: class Y 0.15, Y-1 0.25, Y-2 0.30, "
            "Y-3 0.30. With one class missing the weights are renormalised; with two or more missing the "
            "value is null."
        ),
        "",
    ]
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
