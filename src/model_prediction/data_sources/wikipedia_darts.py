"""Wikipedia (MediaWiki API) client for PDC darts match results.

Keyless and free. Tournament pages carry their draws as ``{N}TeamBracket``
templates whose ``RD{round}-team{NN}`` / ``RD{round}-score{NN}`` parameters
hold every knockout match. Consecutive entries (01/02, 03/04, ...) in one round
are one match. Requires a descriptive ``User-Agent``.

Only leg-play knockout brackets are parsed. Set-play rounds (World Championship)
are skipped so every score in the history is a leg count, and round-robin or
group stages are not covered. Walkovers and unplayed ties (non-integer scores)
are skipped, never guessed.
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

import httpx

BASE_URL = "https://en.wikipedia.org/w/api.php"
DEFAULT_USER_AGENT = "model-prediction/1.0 (vincent.chen9002@gmail.com)"
LEAGUE = "DARTS_PDC"
# Season index pages whose links enumerate that year's events.
INDEX_PAGE_TEMPLATES = ("{year} in darts", "{year} PDC Pro Tour", "{year} European Tour")

_PARAM = re.compile(r"^\s*\|\s*RD(\d+)-(team|score)(\d+)\s*=(.*)$", re.MULTILINE)
_ROUND_HEADER = re.compile(r"^\s*\|\s*RD(\d+)\s*=(.*)$", re.MULTILINE)
_DAY_MONTH = re.compile(
    r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)"
)
_MONTHS = {
    name: index
    for index, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}


def _clean_name(raw: str) -> str | None:
    text = raw.strip()
    flag = re.search(r"\{\{\s*PDCFlag\s*\|([^|}]+)", text, re.IGNORECASE)
    if flag:
        return flag.group(1).strip() or None
    link = re.search(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", text)
    return link.group(1).strip() if link else None


def _parse_score(raw: str) -> int | None:
    text = re.sub(r"<!--.*?-->|<[^>]*>", "", raw).strip()
    return int(text) if text.isdigit() else None


def parse_bracket_matches(wikitext: str, title: str, year: int) -> list[dict[str, Any]]:
    """Return completed leg-play matches from one tournament page's bracket."""
    headers = {int(rd): body for rd, body in _ROUND_HEADER.findall(wikitext)}
    teams: dict[tuple[int, int], str | None] = {}
    scores: dict[tuple[int, int], int | None] = {}
    for rd, kind, slot, body in _PARAM.findall(wikitext):
        key = (int(rd), int(slot))
        if kind == "team":
            teams[key] = _clean_name(body)
        else:
            scores[key] = _parse_score(body)

    matches: list[dict[str, Any]] = []
    for (rd, slot), name_a in sorted(teams.items()):
        if slot % 2 == 0:
            continue
        header = headers.get(rd, "")
        if re.search(r"\bsets?\b", header, re.IGNORECASE):
            continue
        name_b = teams.get((rd, slot + 1))
        legs_a, legs_b = scores.get((rd, slot)), scores.get((rd, slot + 1))
        if not name_a or not name_b or legs_a is None or legs_b is None or legs_a == legs_b:
            continue
        day = _DAY_MONTH.search(header)
        match_date = (
            date(year, _MONTHS[day.group(2)], int(day.group(1))).isoformat() if day else f"{year}-07-01"
        )
        matches.append(
            {
                "match_id": f"wikipedia:{title}:R{rd}:{slot:02d}",
                "match_date": match_date,
                "league": LEAGUE,
                "player_a": name_a,
                "player_b": name_b,
                "legs_a": legs_a,
                "legs_b": legs_b,
                "winner": name_a if legs_a > legs_b else name_b,
                "source": "wikipedia",
            }
        )
    return matches


class WikipediaDartsClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        pause_seconds: float = 0.2,
    ) -> None:
        self.base_url = base_url
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": user_agent})
        self.pause_seconds = pause_seconds

    def _query(self, params: dict[str, Any]) -> dict[str, Any]:
        response = self.client.get(self.base_url, params={"format": "json", "formatversion": 2, **params})
        response.raise_for_status()
        time.sleep(self.pause_seconds)
        return response.json()

    def event_titles(self, year: int) -> list[str]:
        """Candidate tournament pages for one season, from the season index pages."""
        titles: set[str] = set()
        for template in INDEX_PAGE_TEMPLATES:
            payload = self._query(
                {
                    "action": "query",
                    "titles": template.format(year=year),
                    "prop": "links",
                    "pllimit": 500,
                    "redirects": 1,
                }
            )
            for page in payload.get("query", {}).get("pages", []):
                for link in page.get("links", []):
                    title = str(link.get("title", ""))
                    if title.startswith(f"{year} ") and "series" not in title.lower():
                        titles.add(title)
        return sorted(titles)

    def wikitext(self, title: str) -> str | None:
        payload = self._query(
            {
                "action": "parse",
                "page": title,
                "prop": "wikitext",
                "redirects": 1,
            }
        )
        return (payload.get("parse") or {}).get("wikitext")

    def year_matches(self, year: int) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for title in self.event_titles(year):
            try:
                text = self.wikitext(title)
            except httpx.HTTPStatusError:
                continue  # index links include pages that do not exist or are not parseable
            if text and "-team" in text:
                matches.extend(parse_bracket_matches(text, title, year))
        return matches
