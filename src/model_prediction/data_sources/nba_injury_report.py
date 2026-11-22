"""Parse the official NBA injury report PDF into rows (quick-261120-nbf).

Two layouts exist. 2021-22 .. 2022-23 PDFs emit one text fragment per table cell in PDF
user space (y grows upward); 2023-24 onward emit one fragment per word under a flipped
transform (y grows downward). Both are handled the same way: fragments are placed in columns
by the header words' x positions, the y axis is oriented from the header to the first body
line, and every player row is anchored on its ``Current Status`` cell. Multi-line reasons are
vertically centred on their row in the newer layout, so each reason line joins its nearest
status anchor. Game date, time, matchup and team are top-aligned on the first row of their
group and carry forward (also across pages).

A team that has not filed appears as a team line with ``NOT YET SUBMITTED`` in the reason
column and no player: it is emitted as an explicit row (status ``NOT YET SUBMITTED``) because
absence from a report is not the same as healthy.

Every body fragment that cannot be placed is counted in ``unplaced`` so the caller can
report a parse failure rate instead of silently dropping text.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pypdf

STATUSES = ("Out", "Questionable", "Doubtful", "Probable", "Available")
NOT_SUBMITTED = "NOT YET SUBMITTED"
TEAM_TRICODES = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "LA Clippers": "LAC",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}
COLUMNS = ("game_date", "game_time", "matchup", "team", "player", "status", "reason")
_HEADER_TIME = re.compile(r"(\d{2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2})\s*([AP]M)")
_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_MATCHUP = re.compile(r"^([A-Z]{2,3})@([A-Z]{2,3})$")
ROW_TOL = 3.0  # same printed line
REASON_MAX_DIST = 14.0  # wrapped reason lines are ~7 apart, centred on the row; rows ~22 apart


@dataclass
class Fragment:
    x: float
    y: float
    text: str


@dataclass
class ParsedReport:
    header_time_text: str | None
    n_pages: int
    rows: list[dict[str, Any]] = field(default_factory=list)
    n_fragments: int = 0
    unplaced: list[str] = field(default_factory=list)


def _fragments(page: Any) -> list[Fragment]:
    out: list[Fragment] = []

    def visit(text: str, cm: Any, tm: Any, font_dict: Any, font_size: Any) -> None:
        clean = text.strip()
        if clean:
            out.append(Fragment(float(tm[4]), float(tm[5]), clean))

    page.extract_text(visitor_text=visit)
    return out


def _join(parts: list[str]) -> str:
    text = ""
    for part in parts:
        if not text:
            text = part
        elif (text.endswith("-") and not text.endswith(" -")) or part.startswith(","):
            text += part
        else:
            text += " " + part
    return re.sub(r"\s+", " ", text).strip()


def _header(frags: list[Fragment]) -> tuple[float, dict[str, float]] | None:
    """(header y, column -> start x) from the header line."""
    reason = next((f for f in frags if f.text == "Reason"), None)
    if reason is None:
        return None
    line = sorted((f for f in frags if abs(f.y - reason.y) <= ROW_TOL), key=lambda f: f.x)
    words: list[tuple[float, str]] = []
    for f in line:
        for i, word in enumerate(f.text.split()):
            words.append((f.x + i * 0.01, word))
    starts: dict[str, float] = {}
    game_seen = 0
    for x, word in words:
        if word == "Game":
            starts["game_date" if game_seen == 0 else "game_time"] = x
            game_seen += 1
        elif word == "Matchup":
            starts["matchup"] = x
        elif word == "Team":
            starts["team"] = x
        elif word == "Player":
            starts["player"] = x
        elif word == "Current":
            starts["status"] = x
        elif word == "Reason":
            starts["reason"] = x
    if set(starts) != set(COLUMNS):
        return None
    return reason.y, starts


def _column(x: float, starts: dict[str, float]) -> str:
    best = "game_date"
    for name in COLUMNS:
        if x + 4.0 >= starts[name]:
            best = name
    return best


def _lines(frags: list[Fragment]) -> list[tuple[float, str]]:
    """Group one column's fragments into printed lines: [(d, text)] sorted top-down."""
    lines: list[list[Fragment]] = []
    for f in sorted(frags, key=lambda f: (f.y, f.x)):
        if lines and abs(lines[-1][0].y - f.y) <= ROW_TOL:
            lines[-1].append(f)
        else:
            lines.append([f])
    return [(ln[0].y, _join([f.text for f in sorted(ln, key=lambda f: f.x)])) for ln in lines]


def _merge_team_lines(lines: list[tuple[float, str]]) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for d, text in lines:
        if (
            out
            and out[-1][1] not in TEAM_TRICODES
            and (
                f"{out[-1][1]} {text}" in TEAM_TRICODES
                or any(name.startswith(f"{out[-1][1]} {text}") for name in TEAM_TRICODES)
            )
        ):
            out[-1] = (out[-1][0], f"{out[-1][1]} {text}")
        else:
            out.append((d, text))
    return out


def _date_iso(text: str) -> str | None:
    if not _DATE.match(text):
        return None
    mm, dd, yyyy = text.split("/")
    return f"{yyyy}-{mm}-{dd}"


def parse_report(source: bytes | str | Path) -> ParsedReport:
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    reader = pypdf.PdfReader(io.BytesIO(data))
    report = ParsedReport(header_time_text=None, n_pages=len(reader.pages))
    state: dict[str, str | None] = {"game_date": None, "game_time": None, "matchup": None, "team": None}
    starts: dict[str, float] | None = None
    direction: int | None = None
    for page_no, page in enumerate(reader.pages, start=1):
        frags = _fragments(page)
        report.n_fragments += len(frags)
        found = _header(frags)
        if found is None and starts is None:
            report.unplaced.extend(f.text for f in frags)
            continue
        header_y = found[0] if found else None
        if found:
            starts = found[1]
        assert starts is not None
        if report.header_time_text is None:
            m = _HEADER_TIME.search(_join([f.text for f in _title_frags(frags)]))
            if m:
                report.header_time_text = " ".join(m.groups())
        body, stray, direction = _body(frags, header_y, direction)
        for f in sorted(stray, key=lambda f: f.x):
            if _column(f.x, starts) == "reason" and report.rows and report.rows[-1]["player_name"]:
                prev = report.rows[-1]
                prev["reason"] = _join([prev["reason"] or "", f.text])
                prev["reason_continued_on_next_page"] = True
            else:
                report.unplaced.append(f"stray:{f.text}")
        _parse_body(body, starts, state, report, page_no)
    return report


def _line_at(frags: list[Fragment], y: float) -> list[Fragment]:
    return sorted((f for f in frags if abs(f.y - y) <= ROW_TOL), key=lambda f: f.x)


def _title_frags(frags: list[Fragment]) -> list[Fragment]:
    for f in frags:
        if f.text.startswith("Injury"):
            line = _line_at(frags, f.y)
            if _join([g.text for g in line]).startswith("Injury Report:"):
                return line
    return []


_PAGE_LINE = re.compile(r"^Page \d+ of \d+$")


def _noise_ys(frags: list[Fragment]) -> list[float]:
    """y of the title line and the page-number footer (never body rows)."""
    out = [f.y for f in _title_frags(frags)[:1]]
    for f in frags:
        if f.text.startswith("Page") and _PAGE_LINE.match(_join([g.text for g in _line_at(frags, f.y)])):
            out.append(f.y)
    return out


def _body(
    frags: list[Fragment], header_y: float | None, direction: int | None
) -> tuple[list[Fragment], list[Fragment], int | None]:
    """(body, stray, direction). Body y is re-oriented to grow down the page from the header.

    ``direction`` (+1: y grows down, -1: y grows up) is fixed on the first page with a header
    and reused on header-less pages. ``stray`` = fragments on the far side of the header (a
    reason line wrapped over a page break lands above the next page's header).
    """
    noise_y = _noise_ys(frags)
    candidates = [
        f
        for f in frags
        if not (f.x == 0.0 and f.y == 0.0)  # zero-position duplicate glyph runs in the old layout
        and not any(abs(f.y - ny) <= ROW_TOL for ny in noise_y)
        and (header_y is None or abs(f.y - header_y) > ROW_TOL)
    ]
    if header_y is None:
        if direction is None:
            return [], candidates, None
        return [Fragment(f.x, direction * f.y, f.text) for f in candidates], [], direction
    above = [f for f in candidates if f.y > header_y]
    below = [f for f in candidates if f.y < header_y]
    if direction is None:
        direction = 1 if len(above) >= len(below) else -1
    main, stray = (above, below) if direction == 1 else (below, above)
    return [Fragment(f.x, direction * (f.y - header_y), f.text) for f in main], stray, direction


def _parse_body(
    body: list[Fragment],
    starts: dict[str, float],
    state: dict[str, str | None],
    report: ParsedReport,
    page_no: int,
) -> None:
    by_col: dict[str, list[Fragment]] = {c: [] for c in COLUMNS}
    for f in body:
        by_col[_column(f.x, starts)].append(f)
    status_lines = _lines(by_col["status"])
    player_lines = _lines(by_col["player"])
    reason_lines = _lines(by_col["reason"])
    team_lines = _merge_team_lines(_lines(by_col["team"]))
    group_lines = {c: _lines(by_col[c]) for c in ("game_date", "game_time", "matchup")}

    anchors: list[dict[str, Any]] = []
    for d, text in status_lines:
        if text in STATUSES:
            anchors.append({"d": d, "status": text, "reason_parts": []})
        else:
            report.unplaced.append(f"status:{text}")
    # NOT YET SUBMITTED lines are anchors too (a team line without a status).
    reason_rest: list[tuple[float, str]] = []
    for d, text in reason_lines:
        if NOT_SUBMITTED in text.upper():
            anchors.append({"d": d, "status": NOT_SUBMITTED, "reason_parts": [], "no_player": True})
        else:
            reason_rest.append((d, text))
    anchors.sort(key=lambda a: a["d"])
    near = [a for a in anchors if not a.get("no_player")]
    prev_row = report.rows[-1] if report.rows and report.rows[-1]["player_name"] else None
    for d, text in reason_rest:
        best = min(near, key=lambda a: (abs(a["d"] - d), a["d"])) if near else None
        if best is not None and abs(best["d"] - d) <= REASON_MAX_DIST:
            best["reason_parts"].append((d, text))
        elif prev_row is not None and (not near or d < near[0]["d"]):
            # The tail of the previous page's last reason, wrapped over the page break.
            prev_row["reason"] = _join([prev_row["reason"] or "", text])
            prev_row["reason_continued_on_next_page"] = True
        else:
            report.unplaced.append(f"reason:{text}")
    used_players: set[int] = set()
    for a in anchors:
        if a.get("no_player"):
            continue
        idx = next((i for i, (d, _) in enumerate(player_lines) if abs(d - a["d"]) <= ROW_TOL), None)
        if idx is None:
            a["player"] = None
            report.unplaced.append(f"anchor_without_player:{a['status']}")
        else:
            a["player"] = player_lines[idx][1]
            used_players.add(idx)
    for i, (_, text) in enumerate(player_lines):
        if i not in used_players:
            report.unplaced.append(f"player:{text}")

    events: list[tuple[float, int, str, str]] = []
    for col, lines in group_lines.items():
        for d, text in lines:
            events.append((d, 0, col, text))
    for d, text in team_lines:
        events.append((d, 0, "team", text))
    for k, a in enumerate(anchors):
        events.append((a["d"] + ROW_TOL, 1, "anchor", str(k)))
    for d, kind, col, text in sorted(events, key=lambda e: (e[0], e[1])):
        if kind == 0:
            if col == "game_date":
                iso = _date_iso(text)
                if iso is None:
                    report.unplaced.append(f"game_date:{text}")
                    continue
                state["game_date"] = iso
            elif col == "matchup" and not _MATCHUP.match(text):
                report.unplaced.append(f"matchup:{text}")
            else:
                state[col] = text
            continue
        a = anchors[int(text)]
        matchup = state["matchup"]
        m = _MATCHUP.match(matchup or "")
        team = state["team"]
        report.rows.append(
            {
                "page": page_no,
                "game_date": state["game_date"],
                "game_time_et": state["game_time"],
                "matchup": matchup,
                "away_tricode": m.group(1) if m else None,
                "home_tricode": m.group(2) if m else None,
                "team": team,
                "team_tricode": TEAM_TRICODES.get(team or ""),
                "player_name": a.get("player"),
                "status": a["status"],
                "reason": _join([t for _, t in sorted(a["reason_parts"])]) or None,
            }
        )


def row_is_consistent(row: dict[str, Any]) -> bool:
    """Team belongs to the matchup and every required field is present."""
    if not (row.get("game_date") and row.get("matchup") and row.get("team_tricode")):
        return False
    if row["team_tricode"] not in (row.get("away_tricode"), row.get("home_tricode")):
        return False
    return row["status"] == NOT_SUBMITTED or bool(row.get("player_name"))
