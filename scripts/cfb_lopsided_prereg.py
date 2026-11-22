"""Evaluator for the pre-registered NCAAF lopsided / FBS-vs-FCS totals tilt (quick-261115-183, W5).

The registration lives in ``outputs/ncaaf/cfb_lopsided_prereg.json`` (hashed, timestamped, committed before
this script is ever run against data). This script has two subcommands and no others:

    verify    recompute the registration's canonical-JSON sha256, check that the committed blob has the same
              hash, and refuse if the file's first commit is later than registered_at_utc + 2 hours (or if it
              was never committed). Reads no game data.
    evaluate  the one test. Lists FBS-involved games with kickoff strictly after registered_at_utc from the ESPN
              scoreboards (groups 80 and 81), keeps completed non-forfeit games at least 10 h past kickoff
              (an operational proxy for "the game ended at least 6 h ago", no ESPN end-time field is used),
              fetches ESPN core odds, selects the closing lines with ``trim_item`` / ``select_lines`` from
              scripts/cfb_v6_espn_lines.py (rule espn-lines-rule-1, imported read-only), classifies each game,
              and prints n, the primary metric with its one-sided date-clustered bound and the secondary
              metric. Below the registered minimum n it prints INSUFFICIENT N and nothing else about the
              outcome (``--show-interim`` prints a labelled non-test running mean).

The test sample is the FIRST ``minimum_n`` qualifying games in (kickoff, event_id) order, so a later
re-run cannot change the sample or buy a second look with a larger n.

Pre-registration games are never tested. Season scoreboards are read in full only to learn which teams are
FBS that season (team ids and feed membership); every game at or before registered_at_utc is dropped before
any score or odds field is parsed, and none enters the metric.

Read-only GET, generic User-Agent, paced. No order, execute or account endpoint exists in this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
PREREG_PATH = REPO / "outputs" / "ncaaf" / "cfb_lopsided_prereg.json"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
GROUPS = (80, 81)
REGULAR_WEEKS = range(1, 18)
POST_WEEKS = range(1, 4)
END_PROXY_HOURS = 10.0  # kickoff + 10 h: a 4 h game ceiling plus the registered 6 h lag
COMMIT_SLACK = timedelta(hours=2)


# --------------------------------------------------------------------------- registration
def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def prereg_hash(prereg: dict[str, Any]) -> str:
    """sha256 over the canonical JSON of everything except the sha256 field (line-ending independent)."""
    body = {k: v for k, v in prereg.items() if k != "sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def parse_utc(text: str) -> datetime:
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def load_prereg(path: Path = PREREG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(path: Path = PREREG_PATH) -> list[str]:
    """Problems found (empty list = verified). Never reads game data."""
    problems: list[str] = []
    prereg = load_prereg(path)
    want = prereg.get("sha256")
    got = prereg_hash(prereg)
    if want != got:
        problems.append(f"sha256 mismatch: stored {want} recomputed {got}")
    rel = path.resolve().relative_to(REPO).as_posix()
    try:
        added = _git("log", "--diff-filter=A", "--format=%H\t%cI", "--", rel).splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return [*problems, f"git unavailable: {exc}"]
    if not added:
        return [*problems, "registration was never committed: no first-commit time exists"]
    first_hash, first_time = added[-1].split("\t")
    registered = parse_utc(prereg["registered_at_utc"])
    if parse_utc(first_time) > registered + COMMIT_SLACK:
        problems.append(
            f"first commit {first_time} is later than registered_at_utc {registered.isoformat()} + 2 h"
        )
    try:
        blob = json.loads(_git("show", f"{first_hash}:{rel}"))
        if prereg_hash(blob) != got:
            problems.append(f"working file differs from the first-committed blob {first_hash[:10]}")
    except (subprocess.CalledProcessError, ValueError) as exc:
        problems.append(f"cannot read the first-committed blob: {exc}")
    return problems


# --------------------------------------------------------------------------- scoreboard parsing
class EvaluationAbort(RuntimeError):
    """A fetch failed: the evaluation stops (non-zero exit) rather than testing a silently different sample."""


def season_of(start: datetime) -> int:
    return start.year - 1 if start.month in (1, 2) else start.year


def parse_event_ids_only(event: dict[str, Any], feed: int) -> dict[str, Any] | None:
    """Team ids and feed membership only: no score, status or line is read. Used for every event, including
    pre-registration ones (to learn the season's FBS set)."""
    competition = (event.get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): c for c in competition.get("competitors") or []}
    home, away = sides.get("home"), sides.get("away")
    if not home or not away or not event.get("id"):
        return None
    return {
        "event_id": str(event["id"]),
        "start": parse_utc(str(event.get("date") or competition.get("date"))),
        "home_id": str((home.get("team") or {}).get("id")),
        "away_id": str((away.get("team") or {}).get("id")),
        "feed": feed,
    }


def parse_outcome(event: dict[str, Any]) -> dict[str, Any] | None:
    """Completed flag, forfeit flag and total points of one event. Called only for post-registration games."""
    competition = (event.get("competitions") or [{}])[0]
    status_type = (competition.get("status") or {}).get("type") or {}
    sides = {c.get("homeAway"): c for c in competition.get("competitors") or []}
    try:
        total = float(sides["home"]["score"]) + float(sides["away"]["score"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "completed": bool(status_type.get("completed")),
        "forfeit": str(status_type.get("name")) == "STATUS_FORFEIT",
        "total": total,
    }


def fbs_teams_by_season(index: dict[str, dict[str, Any]]) -> dict[int, set[str]]:
    """A team is FBS in a season if it plays in at least one groups=80-only event that season (the division
    reading verified in scripts/cfb_v6_history_backfill.py). ``index`` maps event_id to
    {start, home_id, away_id, feeds:set}."""
    out: dict[int, set[str]] = {}
    for ev in index.values():
        if ev["feeds"] == {80}:
            teams = out.setdefault(season_of(ev["start"]), set())
            teams.add(ev["home_id"])
            teams.add(ev["away_id"])
    return out


def is_fbs_vs_fcs(ev: dict[str, Any], fbs: set[str]) -> bool:
    return (ev["home_id"] in fbs) != (ev["away_id"] in fbs)


def qualifies(fbs_vs_fcs: bool, home_spread_line: float | None, lopsided_threshold: float) -> bool:
    """Tilt membership: FBS-vs-FCS, or a closing home spread of at least ``lopsided_threshold`` points either way."""
    if fbs_vs_fcs:
        return True
    return home_spread_line is not None and abs(home_spread_line) >= lopsided_threshold


# --------------------------------------------------------------------------- statistics
def boot_lower_bound(
    values: np.ndarray, clusters: np.ndarray, *, B: int, seed: int, pct: float = 5.0
) -> tuple[float, float]:
    """(mean, one-sided lower bound) of date-clustered bootstrap means; a fresh generator per call."""
    values = np.asarray(values, float)
    uniq, codes = np.unique(clusters, return_inverse=True)
    g = len(uniq)
    sums = np.bincount(codes, weights=values, minlength=g)
    cnts = np.bincount(codes, minlength=g).astype(float)
    picks = np.random.default_rng(seed).integers(0, g, size=(B, g))
    means = sums[picks].sum(axis=1) / cnts[picks].sum(axis=1)
    return float(values.mean()), float(np.percentile(means, pct))


def summarize(games: list[dict[str, Any]], prereg: dict[str, Any]) -> dict[str, Any]:
    """Primary and secondary metrics over qualifying games (each has y = actual - closing total and a date)."""
    y = np.array([g["y"] for g in games], float)
    dates = np.array([g["date"] for g in games])
    boot = prereg["bootstrap"]
    mean, lower = boot_lower_bound(y, dates, B=boot["B"], seed=boot["seed"], pct=boot["one_sided_percentile"])
    decided = int((y != 0).sum())
    over = int((y > 0).sum())
    return {
        "n": len(games),
        "mean_actual_minus_close": mean,
        "one_sided_lower_bound": lower,
        "over_share_excluding_pushes": (over / decided) if decided else None,
        "overs": over,
        "unders": int((y < 0).sum()),
        "pushes": int((y == 0).sum()),
    }


# --------------------------------------------------------------------------- fetching
def _scoreboard_url(season: int, season_type: int, week: int, group: int) -> str:
    return f"{SCOREBOARD}?dates={season}&seasontype={season_type}&week={week}&groups={group}&limit=500"


def collect_events(
    fetcher: Any, seasons: list[int], registered: datetime
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], int]:
    """(index, post_registration_outcomes, failed_requests). ``index`` covers every event (ids and feeds only);
    outcomes are parsed only for events with kickoff strictly after ``registered``."""
    index: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    failed = 0  # always 0 on return: a failed request raises EvaluationAbort
    for season in seasons:
        for season_type, weeks in ((2, REGULAR_WEEKS), (3, POST_WEEKS)):
            for week in weeks:
                for group in GROUPS:
                    status, payload = fetcher.get(_scoreboard_url(season, season_type, week, group))
                    if status != 200 or not isinstance(payload, dict):
                        raise EvaluationAbort(
                            f"scoreboard fetch failed (status {status}) for season {season} type {season_type} "
                            f"week {week} groups={group}: a missing feed would misclassify FBS teams"
                        )
                    for raw in payload.get("events") or []:
                        ids = parse_event_ids_only(raw, group)
                        if ids is None:
                            continue
                        ev = index.setdefault(ids["event_id"], {**ids, "feeds": set()})
                        ev["feeds"].add(group)
                        if ev["start"] > registered and ids["event_id"] not in outcomes:
                            parsed = parse_outcome(raw)
                            if parsed is not None:
                                outcomes[ids["event_id"]] = parsed
    return index, outcomes, failed


def run_evaluation(fetcher: Any, prereg: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    registered = parse_utc(prereg["registered_at_utc"])
    minimum_n = int(prereg["minimum_n"])
    threshold = float(prereg["tilt_rule"]["abs_closing_home_spread_at_least"])
    seasons = list(range(season_of(registered), season_of(now) + 1))
    index, outcomes, failed = collect_events(fetcher, seasons, registered)
    fbs = fbs_teams_by_season(index)
    ready = sorted(
        (
            ev
            for eid, ev in index.items()
            if 80 in ev["feeds"]
            and ev["start"] > registered  # strictly after registration: nothing earlier is ever tested
            and eid in outcomes
            and outcomes[eid]["completed"]
            and not outcomes[eid]["forfeit"]
            and ev["start"] + timedelta(hours=END_PROXY_HOURS) <= now
        ),
        key=lambda e: (e["start"], e["event_id"]),
    )
    games: list[dict[str, Any]] = []
    skipped = {"no_closing_total": 0, "not_in_tilt": 0}
    for ev in ready:
        if len(games) >= minimum_n:
            break
        rec = fetcher.fetch_event({"event_id": ev["event_id"], "season_year": season_of(ev["start"])})
        if rec["no_line_reason"] == "fetch_failed":
            raise EvaluationAbort(f"odds fetch failed for event {ev['event_id']}: the sample would shift")
        selected = rec["selected"]
        close_total = selected.get("total_line")
        if close_total is None:
            skipped["no_closing_total"] += 1
            continue
        spread = selected.get("home_spread_line")
        vs_fcs = is_fbs_vs_fcs(ev, fbs.get(season_of(ev["start"]), set()))
        if not qualifies(vs_fcs, spread, threshold):
            skipped["not_in_tilt"] += 1
            continue
        games.append(
            {
                "event_id": ev["event_id"],
                "date": ev["start"].strftime("%Y-%m-%d"),
                "y": outcomes[ev["event_id"]]["total"] - float(close_total),
                "fbs_vs_fcs": vs_fcs,
            }
        )
    summary = summarize(games, prereg) if games else {"n": 0}
    return {
        "registered_at_utc": prereg["registered_at_utc"],
        "evaluated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "candidate_games_after_registration": len(ready),
        "scoreboard_requests_failed": failed,
        "skipped": skipped,
        "minimum_n": minimum_n,
        "sample_rule": "the first minimum_n qualifying games in (kickoff, event_id) order",
        "summary": summary,
    }


# --------------------------------------------------------------------------- commands
def cmd_verify(_: argparse.Namespace) -> int:
    prereg = load_prereg()
    problems = verify()
    registered = prereg["registered_at_utc"]
    if problems:
        for p in problems:
            print("VERIFY FAILED:", p)
        return 1
    print(f"verify ok: sha256 {prereg['sha256']} registered_at_utc {registered}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    problems = verify()
    if problems:
        for p in problems:
            print("REFUSING TO EVALUATE:", p)
        return 1
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cfb_v6_espn_lines import Fetcher

    prereg = load_prereg()
    try:
        result = run_evaluation(Fetcher(0.4), prereg)
    except EvaluationAbort as exc:
        print("EVALUATION ABORTED:", exc)
        return 2
    summary = result["summary"]
    n = summary["n"]
    print(f"registration {prereg['sha256']} registered_at_utc {prereg['registered_at_utc']}")
    print(f"qualifying games n = {n} of minimum {result['minimum_n']}; skipped {result['skipped']}")
    if n < result["minimum_n"]:
        print(f"INSUFFICIENT N: {n} < {result['minimum_n']}; no test is reported")
        if args.show_interim and n:
            print(
                "interim running mean of (actual - closing total), NOT a test: "
                f"{summary['mean_actual_minus_close']:+.2f} points on {n} games"
            )
        return 0
    held = summary["one_sided_lower_bound"] > 0
    print(
        f"PRIMARY mean(actual - closing total) = {summary['mean_actual_minus_close']:+.3f} points; "
        f"one-sided 5th-percentile bound = {summary['one_sided_lower_bound']:+.3f}"
    )
    over = summary["over_share_excluding_pushes"]
    print(
        f"SECONDARY over share excluding pushes = {over * 100:.1f}% "
        f"({summary['overs']} over, {summary['unders']} under, {summary['pushes']} push)"
    )
    print("RESULT:", "tilt SUPPORTED (bound > 0)" if held else "tilt NOT SUPPORTED (bound <= 0)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="verify the registration hash and commit time; reads no game data")
    ev = sub.add_parser("evaluate", help="the one pre-registered test")
    ev.add_argument(
        "--show-interim", action="store_true", help="print a labelled non-test running mean below min n"
    )
    args = parser.parse_args(argv)
    return {"verify": cmd_verify, "evaluate": cmd_evaluate}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
