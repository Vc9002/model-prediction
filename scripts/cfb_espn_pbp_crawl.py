"""Polite, resumable crawl of ESPN college-football play-by-play (NCAAF v7 research data foundation).

Fetches ``site.api.espn.com/.../college-football/summary?event=<id>`` for completed games and caches
each response gzipped at ``data/research/ncaaf_v7/raw/<season>/<id>.json.gz`` as a wrapper holding the
raw body text, ``fetched_at_utc`` and the sha256 of the raw bytes. ``data/research/`` is untracked.

Subcommands:
  sample  seeded stratified sample (season x game_class x early/late), fetch, write the coverage
          report to outputs/ncaaf/cfb_espn_pbp_coverage.{json,md}
  crawl   every completed game, FBS-involved (and unclassified) first, then FCS-FCS
  status  progress from the manifest

Safety:
* One instance at a time: an OS lock on ``crawl.lock`` held for the process lifetime (released by the
  OS if the process dies, so there is no stale-PID cleanup to get wrong).
* Resumable: an append-only ``crawl_manifest.jsonl``; the last record per event wins. Terminal
  outcomes (ok, no_plays, http_404) are skipped on the next run; errors and not-final are retried.
* Polite: >= 1 s between requests, exponential backoff honouring Retry-After, and a breaker that
  pauses 15 minutes after 5 consecutive 403/429 responses and exits after 3 such pauses. The
  production daily run hits the same host from this machine, so being slow is the point.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfb_pbp_features as feats

REPO = Path(__file__).resolve().parents[1]
V7_ROOT = REPO / "data" / "research" / "ncaaf_v7"
RAW_ROOT = V7_ROOT / "raw"
MANIFEST = V7_ROOT / "crawl_manifest.jsonl"
LOCK_PATH = V7_ROOT / "crawl.lock"
LOG_PATH = V7_ROOT / "crawl.log"
SAMPLE_IDS = V7_ROOT / "sample_ids.json"
SNAPSHOT = REPO / "data" / "research" / "ncaaf_v6" / "history_snapshot.jsonl"
GAMES = REPO / "data" / "processed" / "ncaaf" / "games.jsonl"
COVERAGE_JSON = REPO / "outputs" / "ncaaf" / "cfb_espn_pbp_coverage.json"
COVERAGE_MD = REPO / "outputs" / "ncaaf" / "cfb_espn_pbp_coverage.md"

URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary"
USER_AGENT = "Mozilla/5.0"  # same generic UA as the repo's other ESPN clients (cfb_v6_espn_lines.py)
MIN_INTERVAL_S = 1.0  # well under the 2 req/s ceiling
MAX_ATTEMPTS = 6
BLOCK_STREAK_LIMIT = 5
BLOCK_PAUSE_S = 15 * 60
MAX_BLOCK_PAUSES = 3
TERMINAL = frozenset({"ok", "no_plays", "http_404"})
COMPLETED_GRACE = timedelta(hours=6)  # kickoff must be this far in the past

# Coverage "usable" definition, fixed before the sample was looked at.
USABLE_MIN_SCRIMMAGE = 80
USABLE_MIN_FIELD_SHARE = 0.95

_logger = logging.getLogger("cfb_pbp_crawl")


@dataclass(frozen=True)
class Event:
    event_id: str
    start_utc: str
    season: int
    game_class: str
    home_id: str | None
    away_id: str | None
    home_score: int | None
    away_score: int | None

    @property
    def tier(self) -> int:
        return 1 if self.game_class == "FCS-FCS" else 0


def cfb_season(start_utc: str) -> int:
    """CFB season of a kickoff: January-June games (bowls, playoff, spring FCS) belong to the prior year."""
    dt = _parse_utc(start_utc)
    return dt.year - 1 if dt.month <= 6 else dt.year


def _parse_utc(text: str) -> datetime:
    text = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_events(now: datetime | None = None) -> list[Event]:
    """Completed games from the v6 snapshot plus newer completed rows of games.jsonl."""
    now = now or _now()
    events: dict[str, Event] = {}
    team_class: dict[str, tuple[str, str]] = {}  # team id -> (latest start seen, FBS|FCS)
    with SNAPSHOT.open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("is_forfeit") or r.get("home_score") is None or r.get("away_score") is None:
                continue
            cls = str(r["game_class"])
            events[str(r["event_id"])] = Event(
                event_id=str(r["event_id"]),
                start_utc=r["start_utc"],
                season=cfb_season(r["start_utc"]),
                game_class=cls,
                home_id=str(r["home_id"]),
                away_id=str(r["away_id"]),
                home_score=int(r["home_score"]),
                away_score=int(r["away_score"]),
            )
            if cls in ("FBS-FBS", "FCS-FCS"):
                level = cls.split("-")[0]
                for tid in (str(r["home_id"]), str(r["away_id"])):
                    if tid not in team_class or team_class[tid][0] < r["start_utc"]:
                        team_class[tid] = (r["start_utc"], level)
    if GAMES.exists():
        with GAMES.open(encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                eid = str(r.get("event_id"))
                if eid in events or r.get("status") not in (None, "completed"):
                    continue
                start = r.get("event_start_utc")
                if not start or r.get("home_score") is None or r.get("away_score") is None:
                    continue
                if _parse_utc(start) > now - COMPLETED_GRACE:
                    continue
                hid, aid = r.get("home_team_id"), r.get("away_team_id")
                levels = sorted(
                    team_class[str(t)][1] if t is not None and str(t) in team_class else "UNK"
                    for t in (hid, aid)
                )
                cls = "UNKNOWN" if "UNK" in levels else "-".join(levels)
                events[eid] = Event(
                    event_id=eid,
                    start_utc=start,
                    season=int(r.get("season_year") or cfb_season(start)),
                    game_class=cls,
                    home_id=None if hid is None else str(hid),
                    away_id=None if aid is None else str(aid),
                    home_score=int(r["home_score"]),
                    away_score=int(r["away_score"]),
                )
    return sorted(events.values(), key=lambda e: (e.start_utc, e.event_id))


def raw_path(event: Event) -> Path:
    return RAW_ROOT / str(event.season) / f"{event.event_id}.json.gz"


def load_manifest() -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if MANIFEST.exists():
        with MANIFEST.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:  # a torn last line from a killed run
                    continue
                latest[str(rec["event_id"])] = rec
    return latest


def is_done(event: Event, manifest: dict[str, dict[str, Any]]) -> bool:
    rec = manifest.get(event.event_id)
    if rec is None or rec.get("status") not in TERMINAL:
        return False
    return rec["status"] != "ok" or raw_path(event).exists()


def _append_manifest(record: dict[str, Any]) -> None:
    with MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


class InstanceLock:
    """Exclusive OS lock held for the process lifetime; the OS drops it if the process dies."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise SystemExit(f"another crawl holds {self.path}; not starting a second one") from None
        self.fd = fd
        return self

    def __exit__(self, *exc: object) -> None:
        if self.fd is None:
            return
        if os.name == "nt":
            import msvcrt

            os.lseek(self.fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(self.fd)
        self.fd = None


class BlockedError(RuntimeError):
    """The host kept refusing us (403/429); stop instead of hammering it."""


class Fetcher:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        self.last_request = 0.0
        self.block_streak = 0
        self.block_pauses = 0
        self.requests = 0

    def close(self) -> None:
        self.client.close()

    def _wait(self) -> None:
        delay = self.last_request + MIN_INTERVAL_S - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()

    def get(self, event_id: str) -> tuple[int, bytes | None, str | None]:
        """Return (http_status, body, error). Status 0 means no response after all retries."""
        error: str | None = None
        status = 0
        for attempt in range(MAX_ATTEMPTS):
            self._wait()
            self.requests += 1
            retry_after: float | None = None
            try:
                resp = self.client.get(URL, params={"event": event_id})
                status = resp.status_code
            except httpx.HTTPError as exc:
                status, error = 0, f"{type(exc).__name__}: {exc}"
            else:
                if status == 200:
                    self.block_streak = 0
                    return status, resp.content, None
                if status == 404:
                    self.block_streak = 0
                    return status, None, "not found"
                error = f"http {status}"
                header = resp.headers.get("Retry-After")
                if header and header.strip().isdigit():
                    retry_after = float(header.strip())
                if status in (403, 429):
                    self.block_streak += 1
                    if self.block_streak >= BLOCK_STREAK_LIMIT:
                        self._block_pause()
                elif status < 500:
                    return status, None, error  # other 4xx: not worth retrying now
            backoff = retry_after if retry_after is not None else min(300.0, 2.0 * 2**attempt)
            _logger.warning("event %s attempt %d: %s; sleeping %.0fs", event_id, attempt + 1, error, backoff)
            time.sleep(backoff + random.uniform(0, 1))
        return status, None, error

    def _block_pause(self) -> None:
        self.block_pauses += 1
        self.block_streak = 0
        if self.block_pauses > MAX_BLOCK_PAUSES:
            raise BlockedError("repeated 403/429 from ESPN; stopping (resume later with the same command)")
        _logger.warning("%d consecutive 403/429: pausing %d s", BLOCK_STREAK_LIMIT, BLOCK_PAUSE_S)
        time.sleep(BLOCK_PAUSE_S)


def _status_of(payload: dict[str, Any]) -> tuple[bool, int, int]:
    comps = (payload.get("header") or {}).get("competitions") or [{}]
    stype = ((comps[0].get("status") or {}).get("type")) or {}
    final = bool(stype.get("completed")) and stype.get("name") in ("STATUS_FINAL", "STATUS_FINAL_OVERTIME")
    if not final and stype.get("completed") and str(stype.get("state")) == "post":
        final = True
    drives = (payload.get("drives") or {}).get("previous") or []
    return final, len(drives), sum(len(d.get("plays") or []) for d in drives)


def fetch_event(fetcher: Fetcher, event: Event) -> dict[str, Any]:
    status, body, error = fetcher.get(event.event_id)
    fetched_at = _iso(_now())
    record: dict[str, Any] = {
        "event_id": event.event_id,
        "season": event.season,
        "game_class": event.game_class,
        "fetched_at_utc": fetched_at,
        "http_status": status,
    }
    if status == 404:
        record["status"] = "http_404"
        return record
    if body is None:
        record.update(status="error", error=error)
        return record
    try:
        text = body.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        record.update(status="error", error=f"bad body: {exc}")
        return record
    final, n_drives, n_plays = _status_of(payload)
    record.update(n_drives=n_drives, n_plays=n_plays)
    if not final:
        record["status"] = "not_final"  # not cached; retried on the next run
        return record
    sha = hashlib.sha256(body).hexdigest()
    wrapper = {
        "event_id": event.event_id,
        "season": event.season,
        "game_class": event.game_class,
        "start_utc": event.start_utc,
        "url": f"{URL}?event={event.event_id}",
        "fetched_at_utc": fetched_at,
        "http_status": status,
        "sha256": sha,
        "n_bytes": len(body),
        "body": text,
    }
    path = raw_path(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
        json.dump(wrapper, fh)
    os.replace(tmp, path)
    record.update(
        status="ok" if n_plays > 0 else "no_plays", sha256=sha, path=path.relative_to(REPO).as_posix()
    )
    return record


def run_events(events: list[Event], label: str) -> Counter[str]:
    manifest = load_manifest()
    todo = [e for e in events if not is_done(e, manifest)]
    _logger.info(
        "%s: %d events, %d already done, %d to fetch", label, len(events), len(events) - len(todo), len(todo)
    )
    outcomes: Counter[str] = Counter()
    fetcher = Fetcher()
    started = time.monotonic()
    try:
        for i, event in enumerate(todo, 1):
            record = fetch_event(fetcher, event)
            _append_manifest(record)
            outcomes[record["status"]] += 1
            if i % 100 == 0 or i == len(todo):
                rate = i / max(1e-9, time.monotonic() - started)
                eta_h = (len(todo) - i) / rate / 3600 if rate else float("nan")
                _logger.info(
                    "%s: %d/%d fetched (%s), %.2f ev/s, ETA %.1f h",
                    label,
                    i,
                    len(todo),
                    dict(outcomes),
                    rate,
                    eta_h,
                )
    except BlockedError as exc:
        _logger.error("%s", exc)
    finally:
        fetcher.close()
    return outcomes


# ---------------------------------------------------------------- sample + coverage


def stratified_sample(events: list[Event], per_cell: int, seed: int) -> list[Event]:
    by_season: dict[int, list[Event]] = defaultdict(list)
    for e in events:
        by_season[e.season].append(e)
    rng = random.Random(seed)
    chosen: list[Event] = []
    for season in sorted(by_season):
        season_events = by_season[season]
        cutoff = statistics.median_low(e.start_utc for e in season_events)
        cells: dict[tuple[str, str], list[Event]] = defaultdict(list)
        for e in season_events:
            half = "early" if e.start_utc <= cutoff else "late"
            cells[(e.game_class, half)].append(e)
        for key in sorted(cells):
            pool = sorted(cells[key], key=lambda e: e.event_id)
            chosen.extend(rng.sample(pool, min(per_cell, len(pool))))
    return chosen


def game_coverage(event: Event, payload: dict[str, Any]) -> dict[str, Any]:
    drives = (payload.get("drives") or {}).get("previous") or []
    plays = [p for d in drives for p in (d.get("plays") or [])]
    scrimmage = [
        p
        for p in plays
        if str((p.get("type") or {}).get("text") or "").strip().lower() in feats.SCRIMMAGE_TYPES
    ]

    def share(pred: Any) -> float | None:
        return sum(1 for p in scrimmage if pred(p)) / len(scrimmage) if scrimmage else None

    def has(value: Any) -> bool:
        return value is not None and value != ""

    fields = {
        "period": share(lambda p: has((p.get("period") or {}).get("number"))),
        "score": share(lambda p: has(p.get("homeScore")) and has(p.get("awayScore"))),
        "down": share(lambda p: (p.get("start") or {}).get("down") in (1, 2, 3, 4)),
        "distance": share(lambda p: has((p.get("start") or {}).get("distance"))),
        "yardsToEndzone": share(lambda p: has((p.get("start") or {}).get("yardsToEndzone"))),
        "clock": share(lambda p: has((p.get("clock") or {}).get("displayValue"))),
        "start_team": share(lambda p: has(((p.get("start") or {}).get("team") or {}).get("id"))),
        "statYardage": share(lambda p: has(p.get("statYardage"))),
    }
    final_match = None
    scored = [p for p in plays if has(p.get("homeScore")) and has(p.get("awayScore"))]
    if scored and event.home_score is not None:
        last = scored[-1]
        final_match = (int(last["homeScore"]), int(last["awayScore"])) == (event.home_score, event.away_score)
    fields_ok = all(v is not None and v >= USABLE_MIN_FIELD_SHARE for v in fields.values())
    usable = bool(drives) and len(scrimmage) >= USABLE_MIN_SCRIMMAGE and fields_ok and bool(final_match)
    return {
        "event_id": event.event_id,
        "season": event.season,
        "game_class": event.game_class,
        "start_utc": event.start_utc,
        "has_drives": bool(drives),
        "n_drives": len(drives),
        "n_plays": len(plays),
        "n_scrimmage": len(scrimmage),
        "field_share": fields,
        "fields_ok": fields_ok,
        "final_score_match": final_match,
        "usable": usable,
        "play_source": (((payload.get("header") or {}).get("competitions") or [{}])[0]).get(
            "playByPlaySource"
        ),
    }


def coverage_report(sample: list[Event]) -> dict[str, Any]:
    manifest = load_manifest()
    games: list[dict[str, Any]] = []
    census: Counter[str] = Counter()
    for event in sample:
        rec = manifest.get(event.event_id, {})
        path = raw_path(event)
        if not path.exists():
            games.append(
                {
                    "event_id": event.event_id,
                    "season": event.season,
                    "game_class": event.game_class,
                    "start_utc": event.start_utc,
                    "has_drives": False,
                    "n_plays": 0,
                    "n_scrimmage": 0,
                    "usable": False,
                    "fetch_status": rec.get("status", "missing"),
                }
            )
            continue
        _, payload = feats.load_raw(path)
        cov = game_coverage(event, payload)
        cov["fetch_status"] = rec.get("status")
        games.append(cov)
        for d in (payload.get("drives") or {}).get("previous") or []:
            for p in d.get("plays") or []:
                t = p.get("type") or {}
                census[f"{t.get('id')}|{t.get('text')}"] += 1

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rows)
        with_plays = [r for r in rows if r.get("n_scrimmage", 0) > 0]
        return {
            "games": n,
            "usable_share": sum(r["usable"] for r in rows) / n if n else None,
            "drives_plays_share": sum(1 for r in rows if r.get("has_drives") and r.get("n_plays", 0) > 0) / n
            if n
            else None,
            "median_plays": statistics.median(r["n_plays"] for r in with_plays) if with_plays else None,
            "median_scrimmage": statistics.median(r["n_scrimmage"] for r in with_plays)
            if with_plays
            else None,
            "final_score_match_share": (
                sum(1 for r in with_plays if r.get("final_score_match")) / len(with_plays)
                if with_plays
                else None
            ),
            "fields_ok_share": sum(1 for r in with_plays if r.get("fields_ok")) / len(with_plays)
            if with_plays
            else None,
        }

    by_cell: dict[str, Any] = {}
    for season in sorted({g["season"] for g in games}):
        for cls in ("FBS-FBS", "FBS-FCS", "FCS-FCS", "UNKNOWN"):
            rows = [g for g in games if g["season"] == season and g["game_class"] == cls]
            if rows:
                by_cell[f"{season}|{cls}"] = summarize(rows)
    fbs = [g for g in games if g["game_class"] != "FCS-FCS"]
    field_means = {
        k: statistics.fmean(
            g["field_share"][k] for g in games if g.get("field_share") and g["field_share"][k] is not None
        )
        for k in (
            "period",
            "score",
            "down",
            "distance",
            "yardsToEndzone",
            "clock",
            "start_team",
            "statYardage",
        )
    }
    gaps = [k for k, v in by_cell.items() if v["usable_share"] is not None and v["usable_share"] < 0.9]
    return {
        "generated_at_utc": _iso(_now()),
        "usable_definition": (
            f"drives present, >= {USABLE_MIN_SCRIMMAGE} scrimmage plays, period/score/down/distance/"
            f"yardsToEndzone/clock/start.team/statYardage on >= {USABLE_MIN_FIELD_SHARE:.0%} of scrimmage plays, "
            "last play's running score equals the known final"
        ),
        "overall": summarize(games),
        "fbs_involved": summarize(fbs),
        "fcs_fcs": summarize([g for g in games if g["game_class"] == "FCS-FCS"]),
        "by_season_class": by_cell,
        "field_presence_mean": field_means,
        "gap_cells_below_90pct": gaps,
        "failures": [
            {
                k: g.get(k)
                for k in (
                    "event_id",
                    "season",
                    "game_class",
                    "n_plays",
                    "n_scrimmage",
                    "fields_ok",
                    "final_score_match",
                    "fetch_status",
                )
            }
            for g in games
            if not g["usable"]
        ],
        "play_type_census": dict(census.most_common()),
        "games": games,
    }


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.0%}"


def write_coverage_md(report: dict[str, Any]) -> str:
    lines = [
        "# ESPN CFB play-by-play coverage (stratified sample)",
        "",
        f"Generated {report['generated_at_utc']}. Usable = {report['usable_definition']}.",
        "",
        "| Group | Games | Usable | Drives+plays | Median plays | Median scrimmage | Final score match |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, key in (("All", "overall"), ("FBS-involved", "fbs_involved"), ("FCS-FCS", "fcs_fcs")):
        s = report[key]
        lines.append(
            f"| {name} | {s['games']} | {_pct(s['usable_share'])} | {_pct(s['drives_plays_share'])} | "
            f"{s['median_plays']} | {s['median_scrimmage']} | {_pct(s['final_score_match_share'])} |"
        )
    lines += [
        "",
        "## By season and class",
        "",
        "| Season | Class | Games | Usable | Drives+plays | Median plays |",
        "|---|---|---|---|---|---|",
    ]
    for key, s in report["by_season_class"].items():
        season, cls = key.split("|")
        lines.append(
            f"| {season} | {cls} | {s['games']} | {_pct(s['usable_share'])} | {_pct(s['drives_plays_share'])} | "
            f"{s['median_plays']} |"
        )
    lines += ["", "## Field presence on scrimmage plays (mean per game)", ""]
    lines += [f"- {k}: {v:.1%}" for k, v in report["field_presence_mean"].items()]
    lines += ["", f"Cells below 90% usable: {', '.join(report['gap_cells_below_90pct']) or 'none'}", ""]
    lines += [
        "## Unusable games",
        "",
        "| Event | Season | Class | Plays | Scrimmage | Fields ok | Final match | Fetch |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in report["failures"]:
        lines.append(
            f"| {f['event_id']} | {f['season']} | {f['game_class']} | {f['n_plays']} | {f['n_scrimmage']} | "
            f"{f.get('fields_ok')} | {f.get('final_score_match')} | {f.get('fetch_status')} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- entry points


def _setup_logging() -> None:
    V7_ROOT.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_sample(args: argparse.Namespace) -> None:
    events = load_events()
    sample = stratified_sample(events, args.per_cell, args.seed)
    SAMPLE_IDS.write_text(json.dumps([e.event_id for e in sample]), encoding="utf-8")
    with InstanceLock(LOCK_PATH):
        run_events(sample, "sample")
    report = coverage_report(sample)
    report["sample_seed"] = args.seed
    report["per_cell"] = args.per_cell
    COVERAGE_JSON.parent.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in report.items() if k != "games"}
    COVERAGE_JSON.write_text(json.dumps(slim, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    COVERAGE_MD.write_text(write_coverage_md(report), encoding="utf-8")
    print(
        json.dumps(
            {k: report[k] for k in ("overall", "fbs_involved", "fcs_fcs", "gap_cells_below_90pct")}, indent=2
        )
    )


def cmd_crawl(args: argparse.Namespace) -> None:
    events = load_events()
    # FBS-involved (and unclassified) first, newest season first inside a tier.
    events.sort(key=lambda e: (e.tier, -e.season, e.start_utc, e.event_id))
    if args.limit:
        events = events[: args.limit]
    with InstanceLock(LOCK_PATH):
        outcomes = run_events(events, "crawl")
    _logger.info("crawl finished: %s", dict(outcomes))


def cmd_status(args: argparse.Namespace) -> None:
    events = load_events()
    manifest = load_manifest()
    done = [e for e in events if is_done(e, manifest)]
    statuses = Counter(manifest[e.event_id]["status"] for e in events if e.event_id in manifest)
    by_tier = Counter(e.tier for e in events)
    done_tier = Counter(e.tier for e in done)
    remaining = len(events) - len(done)
    print(
        json.dumps(
            {
                "events": len(events),
                "done": len(done),
                "remaining": remaining,
                "statuses": dict(statuses),
                "fbs_involved_done": f"{done_tier[0]}/{by_tier[0]}",
                "fcs_fcs_done": f"{done_tier[1]}/{by_tier[1]}",
                "eta_hours_at_1_2s_per_event": round(remaining * 1.2 / 3600, 1),
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_sample = sub.add_parser("sample")
    p_sample.add_argument("--per-cell", type=int, default=6)
    p_sample.add_argument("--seed", type=int, default=20261121)
    p_crawl = sub.add_parser("crawl")
    p_crawl.add_argument("--limit", type=int, default=0)
    sub.add_parser("status")
    args = parser.parse_args()
    if args.cmd != "status":
        _setup_logging()
    {"sample": cmd_sample, "crawl": cmd_crawl, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()
