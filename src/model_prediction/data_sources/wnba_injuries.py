"""Point-in-time capture of the official WNBA injury report.

The WNBA publishes an index of timestamped PDF reports.  This module stores the
original bytes and a normalized JSON snapshot together.  A forecast must use a
snapshot observed before the decision; downloading today's report later is not
a historical substitute.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from pypdf import PdfReader

from ..domain import parse_utc, utc_now


REPORT_INDEX_URL = "https://www.wnba.com/api/injury-reports"
REPORT_HOST = "ak-static.cms.nba.com"
REPORT_PATH_PREFIX = "/referee/wnba_injury/Injury-Report_"
REPORT_SCHEMA_VERSION = "1"
EASTERN = ZoneInfo("America/New_York")

_REPORT_URL_RE = re.compile(
    r"Injury-Report_(?P<date>\d{4}-\d{2}-\d{2})_(?P<hour>\d{2})_(?P<minute>\d{2})(?P<ampm>AM|PM)\.pdf$"
)
_HEADER_RE = re.compile(
    r"Injury\s+Report:\s*(?P<date>\d{2}/\d{2}/\d{2})\s*(?P<time>\d{1,2}:\d{2})\s*(?P<ampm>AM|PM)",
    re.IGNORECASE,
)
_STATUSES = {"Available", "Probable", "Questionable", "Doubtful", "Out"}
_TEAM_BY_ABBREVIATION = {
    "ATL": "Atlanta Dream",
    "CHI": "Chicago Sky",
    "CON": "Connecticut Sun",
    "DAL": "Dallas Wings",
    "GSV": "Golden State Valkyries",
    "IND": "Indiana Fever",
    "LAS": "Los Angeles Sparks",
    "LVA": "Las Vegas Aces",
    "MIN": "Minnesota Lynx",
    "NYL": "New York Liberty",
    "PDX": "Portland Fire",
    "PHX": "Phoenix Mercury",
    "SEA": "Seattle Storm",
    "TOR": "Toronto Tempo",
    "WAS": "Washington Mystics",
}


@dataclass(frozen=True)
class InjuryEntry:
    game_date: str
    game_time_et: str
    matchup: str
    team: str
    player_name: str
    current_status: str
    reason: str


@dataclass(frozen=True)
class ParsedReport:
    report_at_utc: str
    teams_listed: tuple[str, ...]
    team_matchups: Mapping[str, str]
    team_report_status: Mapping[str, str]
    entries: tuple[InjuryEntry, ...]


@dataclass(frozen=True)
class _Token:
    page: int
    x: float
    y: float
    text: str


def report_time_from_url(url: str) -> datetime:
    """Return the official report timestamp encoded in a validated PDF URL."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != REPORT_HOST:
        raise ValueError(f"unexpected WNBA injury-report host: {parsed.netloc or '<missing>'}")
    if not parsed.path.startswith(REPORT_PATH_PREFIX):
        raise ValueError("unexpected WNBA injury-report path")
    match = _REPORT_URL_RE.search(parsed.path)
    if match is None:
        raise ValueError("WNBA injury-report URL has no parseable timestamp")
    local = datetime.strptime(
        f"{match.group('date')} {match.group('hour')}:{match.group('minute')} {match.group('ampm')}",
        "%Y-%m-%d %I:%M %p",
    ).replace(tzinfo=EASTERN)
    return local.astimezone(ZoneInfo("UTC"))


def available_report_links(payload: Mapping[str, Any]) -> list[tuple[datetime, str]]:
    """Validate and chronologically sort report links from the official index."""
    links: list[tuple[datetime, str]] = []
    for item in payload.get("links", []):
        if not isinstance(item, Mapping) or not item.get("href"):
            continue
        url = str(item["href"])
        links.append((report_time_from_url(url), url))
    return sorted(set(links))


def latest_report_at_or_before(payload: Mapping[str, Any], observed_at: datetime) -> tuple[datetime, str]:
    """Select a report that already existed at decision time; never look ahead."""
    observed = parse_utc(observed_at.isoformat())
    eligible = [item for item in available_report_links(payload) if item[0] <= observed]
    if not eligible:
        raise ValueError("NO_CALL_AVAILABILITY_UNAVAILABLE: no official report existed at observation time")
    return eligible[-1]


def _pdf_tokens(pdf_bytes: bytes) -> tuple[list[_Token], str]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    tokens: list[_Token] = []
    raw_text: list[str] = []
    for page_number, page in enumerate(reader.pages):
        raw_text.append(page.extract_text() or "")

        def visit(text: str, _cm: list[float], tm: list[float], _font: Any, _size: float) -> None:
            cleaned = " ".join(text.split())
            if cleaned:
                tokens.append(_Token(page_number, float(tm[4]), float(tm[5]), cleaned))

        page.extract_text(visitor_text=visit)
    if not tokens:
        raise ValueError("official WNBA injury PDF contained no extractable text")
    return tokens, "\n".join(raw_text)


def _join(tokens: Iterable[_Token]) -> str:
    return " ".join(token.text for token in sorted(tokens, key=lambda item: item.x)).strip()


def _group_lines(tokens: Iterable[_Token], tolerance: float = 1.0) -> list[list[_Token]]:
    lines: list[list[_Token]] = []
    for token in sorted(tokens, key=lambda item: (item.page, item.y, item.x)):
        if (
            not lines
            or token.page != lines[-1][0].page
            or abs(token.y - lines[-1][0].y) > tolerance
        ):
            lines.append([token])
        else:
            lines[-1].append(token)
    return lines


def _column_text(line: list[_Token], left: float, right: float) -> str:
    return _join(token for token in line if left <= token.x < right)


def _parse_coordinate_table(
    tokens: list[_Token],
) -> tuple[list[InjuryEntry], set[str], dict[str, str], dict[str, str]]:
    """Parse the stable seven-column official layout using its horizontal anchors."""
    entries: list[InjuryEntry] = []
    teams: set[str] = set()
    team_matchups: dict[str, str] = {}
    team_report_status: dict[str, str] = {}
    matchups_seen: set[str] = set()
    current_date = ""
    current_time = ""
    current_matchup = ""
    current_team = ""

    for page in sorted({token.page for token in tokens}):
        page_tokens = [token for token in tokens if token.page == page]
        header_candidates = [
            token.y for token in page_tokens if token.text.casefold() == "current" and 560 <= token.x < 640
        ]
        if header_candidates:
            header_y = header_candidates[0]
        else:
            title_candidates = [
                token.y for token in page_tokens if token.text.casefold() == "injury" and 250 <= token.x < 340
            ]
            if not title_candidates:
                continue
            header_y = title_candidates[0]
        data_lines = [
            line
            for line in _group_lines(page_tokens)
            if line[0].y > header_y + 8 and line[0].y < 500
        ]
        player_lines: list[tuple[float, list[_Token]]] = []
        for line in data_lines:
            status = _column_text(line, 560, 666)
            player = _column_text(line, 410, 560)
            if status in _STATUSES and player:
                player_lines.append((line[0].y, line))

        player_y = {round(value, 3) for value, _ in player_lines}
        for line in data_lines:
            date_text = _column_text(line, 0, 110)
            time_text = _column_text(line, 110, 195)
            matchup_text = _column_text(line, 195, 260)
            team_text = _column_text(line, 260, 410)
            if date_text:
                try:
                    current_date = datetime.strptime(date_text, "%m/%d/%Y").date().isoformat()
                except ValueError as error:
                    raise ValueError(f"invalid game date in official WNBA report: {date_text}") from error
            if time_text:
                current_time = time_text
            if matchup_text:
                current_matchup = matchup_text.replace(" ", "")
                matchups_seen.add(current_matchup)
            if team_text:
                current_team = team_text
                teams.add(current_team)
                if current_matchup:
                    team_matchups[current_team] = current_matchup
            if "NOT YET SUBMITTED" in _column_text(line, 410, 1000):
                if not current_team:
                    raise ValueError("official WNBA report has an unassigned submission status")
                team_report_status[current_team] = "not_yet_submitted"
            elif round(line[0].y, 3) in player_y and current_team:
                team_report_status[current_team] = "submitted"

        # Context was propagated in the pass above. Re-run the page rows to
        # construct player entries while preserving that same document order.
        current_date = ""
        current_time = ""
        current_matchup = ""
        current_team = ""
        # Restore context from preceding pages when a team spills to a new page.
        for prior_page in range(page):
            for prior_line in [
                item
                for item in _group_lines([token for token in tokens if token.page == prior_page])
                if item[0].y < 520
            ]:
                for left, right, kind in (
                    (0, 110, "date"),
                    (110, 195, "time"),
                    (195, 260, "matchup"),
                    (260, 410, "team"),
                ):
                    value = _column_text(prior_line, left, right)
                    if not value:
                        continue
                    if kind == "date":
                        with suppress(ValueError):
                            current_date = datetime.strptime(value, "%m/%d/%Y").date().isoformat()
                    elif kind == "time":
                        current_time = value
                    elif kind == "matchup":
                        current_matchup = value.replace(" ", "")
                    else:
                        current_team = value

        for row_y, line in player_lines:
            date_text = _column_text(line, 0, 110)
            time_text = _column_text(line, 110, 195)
            matchup_text = _column_text(line, 195, 260)
            team_text = _column_text(line, 260, 410)
            if date_text:
                try:
                    current_date = datetime.strptime(date_text, "%m/%d/%Y").date().isoformat()
                except ValueError as error:
                    raise ValueError(f"invalid game date in official WNBA report: {date_text}") from error
            if time_text:
                current_time = time_text
            if matchup_text:
                current_matchup = matchup_text.replace(" ", "")
            if team_text:
                current_team = team_text
                teams.add(current_team)
                if current_matchup:
                    team_matchups[current_team] = current_matchup
                team_report_status[current_team] = "submitted"
            if not all((current_date, current_matchup, current_team)):
                raise ValueError("official WNBA injury report row is missing game/team context")

            # Reasons sometimes wrap above and below the player baseline.  Use
            # only nearby reason-column fragments, bounded by adjacent players.
            other_y = [value for value, _ in player_lines if value != row_y]
            nearest = min((abs(value - row_y) for value in other_y), default=30.0)
            radius = min(10.0, max(4.0, nearest / 2 - 0.5))
            reason_tokens = [
                token
                for token in page_tokens
                if token.x >= 666 and abs(token.y - row_y) <= radius and token.y > header_y + 8
            ]
            reason = " ".join(
                _join(group)
                for group in _group_lines(reason_tokens)
                if _join(group)
            )
            entries.append(
                InjuryEntry(
                    game_date=current_date,
                    game_time_et=current_time,
                    matchup=current_matchup,
                    team=current_team,
                    player_name=_column_text(line, 410, 560),
                    current_status=_column_text(line, 560, 666),
                    reason=reason,
                )
            )
    # A submitted team with no reportable injuries is omitted from the player
    # rows.  If its opponent anchors the matchup, infer submitted/no entries.
    # Explicit NOT YET SUBMITTED always wins over this inference.
    for matchup in matchups_seen:
        abbreviations = matchup.split("@")
        if len(abbreviations) != 2:
            continue
        for abbreviation in abbreviations:
            team = _TEAM_BY_ABBREVIATION.get(abbreviation)
            if team is None:
                continue
            teams.add(team)
            team_matchups.setdefault(team, matchup)
            team_report_status.setdefault(team, "submitted_no_entries")
    if not entries:
        raise ValueError("official WNBA injury report contained no recognized player rows")
    return entries, teams, team_matchups, team_report_status


def parse_report_pdf(pdf_bytes: bytes) -> ParsedReport:
    """Parse one official PDF and retain the report's own publication timestamp."""
    tokens, raw_text = _pdf_tokens(pdf_bytes)
    compact = " ".join(raw_text.split())
    header = _HEADER_RE.search(compact)
    if header is None:
        raise ValueError("official WNBA injury PDF has no report timestamp")
    report_local = datetime.strptime(
        f"{header.group('date')} {header.group('time')} {header.group('ampm').upper()}",
        "%m/%d/%y %I:%M %p",
    ).replace(tzinfo=EASTERN)
    entries, teams, team_matchups, team_report_status = _parse_coordinate_table(tokens)
    return ParsedReport(
        report_at_utc=report_local.astimezone(ZoneInfo("UTC")).isoformat(),
        teams_listed=tuple(sorted(teams)),
        team_matchups=dict(sorted(team_matchups.items())),
        team_report_status=dict(sorted(team_report_status.items())),
        entries=tuple(entries),
    )


def capture_latest_report(
    data_root: str | Path,
    *,
    observed_at: datetime | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch and persist the latest eligible official report and normalized rows."""
    observed = parse_utc((observed_at or utc_now()).isoformat())
    http = client or httpx.Client(timeout=30, follow_redirects=True)
    index_response = http.get(
        REPORT_INDEX_URL,
        headers={"Accept": "application/json", "User-Agent": "model-prediction/1.0"},
    )
    index_response.raise_for_status()
    report_at, report_url = latest_report_at_or_before(index_response.json(), observed)
    pdf_response = http.get(report_url, headers={"Accept": "application/pdf"})
    pdf_response.raise_for_status()
    pdf_bytes = pdf_response.content
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("official WNBA injury-report response was not a PDF")
    parsed = parse_report_pdf(pdf_bytes)
    parsed_at = parse_utc(parsed.report_at_utc)
    if abs((parsed_at - report_at).total_seconds()) > 60:
        raise ValueError("official WNBA injury-report URL and PDF timestamps disagree")

    digest = hashlib.sha256(pdf_bytes).hexdigest()
    stamp = report_at.astimezone(EASTERN).strftime("%Y%m%dT%H%M%S%z")
    day = report_at.astimezone(EASTERN).date().isoformat()
    root = Path(data_root) / "availability" / "wnba"
    raw_path = root / "raw" / day / f"{stamp}-{digest[:12]}.pdf"
    snapshot_path = root / "snapshots" / day / f"{stamp}-{digest[:12]}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(pdf_bytes)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "sport": "wnba",
        "source": "official_wnba_injury_report",
        "source_index_url": REPORT_INDEX_URL,
        "source_pdf_url": report_url,
        "source_pdf_sha256": digest,
        "observed_at_utc": observed.isoformat(),
        "report_at_utc": parsed.report_at_utc,
        "teams_listed": list(parsed.teams_listed),
        "team_matchups": dict(parsed.team_matchups),
        "team_report_status": dict(parsed.team_report_status),
        "entries": [asdict(entry) for entry in parsed.entries],
    }
    snapshot_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        **payload,
        "raw_path": str(raw_path),
        "snapshot_path": str(snapshot_path),
        "entry_count": len(parsed.entries),
    }
