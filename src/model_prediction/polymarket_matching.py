"""Team-name matching between ledger rows and Polymarket US markets.

One implementation shared by the dashboard quote mapper (``dashboard/backtests.py``), which the
auto-buyer uses to choose a market and side, and the real-money execution gate
(``data_sources/polymarket_execute.py``), which re-checks that side against a live quote. They were
two hand-kept copies, alias table included, with nothing testing that they agreed.

Stdlib only, at the package top level: ``polymarket_execute`` loads ``.env`` credentials at import and
``data_sources/__init__`` pulls in an HTTP client, so neither is a neutral home for code the dashboard
imports.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

_RE_NAME_PUNCT = re.compile(r"[(),.\-_]")
_RE_WORD = re.compile(r"\w+")

# ESPN-style full names -> Polymarket US names (normalized). The loose
# containment match below cannot bridge these (no shared word), which left
# every game involving them with no executable quote ("no exact executable
# Polymarket US market mapping"). Found 2026-10-23 on NCAAF spreads: 35 of 273
# games failed to match. Keys and values are post-normalization.
TEAM_NAME_ALIASES: dict[str, str] = {
    "app state mountaineers": "appalachian state",
    "app state": "appalachian state",
    "central connecticut blue devils": "central connecticut state",
    "central connecticut": "central connecticut state",
    "grambling tigers": "grambling state",
    "long island university sharks": "liu",
    "long island university": "liu",
    "massachusetts minutemen": "umass",
    "massachusetts": "umass",
    "miami hurricanes": "miami fl",
    "se louisiana lions": "southeastern louisiana",
    "san jose state spartans": "san jose state",
    "san jos� state spartans": "san jose state",
    "ul monroe warhawks": "louisiana monroe",
    "ul monroe": "louisiana monroe",
    "nm state": "new mexico state",
    "nm state aggies": "new mexico state",
    "k-state": "kansas state",
    "k state": "kansas state",
    "k state wildcats": "kansas state",
    "ole miss": "mississippi",
    "ole miss rebels": "mississippi",
    "sam houston": "sam houston state",
    "sam houston bearkats": "sam houston state",
    "jax state": "jacksonville state",
    "jax state gamecocks": "jacksonville state",
    "fiu": "florida international",
    "fiu panthers": "florida international",
    "fau": "florida atlantic",
    "fau owls": "florida atlantic",
    "usf": "south florida",
    "usf bulls": "south florida",
    "ucf": "central florida",
    "ucf knights": "central florida",
    "pitt": "pittsburgh",
    "pitt panthers": "pittsburgh",
    "la tech": "louisiana tech",
    "la tech bulldogs": "louisiana tech",
    "nc state": "north carolina state",
    "nc state wolfpack": "north carolina state",
    "uconn": "connecticut",
    "uconn huskies": "connecticut",
}

_DISQUALIFYING_TEAM_PREFIXES = frozenset(
    {"western", "eastern", "northern", "southern", "central", "middle", "north", "south", "east", "west"}
)
_DISQUALIFYING_TEAM_SUFFIXES = frozenset(
    {"state", "tech", "a&m", "am", "christian", "baptist", "methodist", "valley"}
)


@lru_cache(maxsize=4096)
def normalize_name_tokens(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    normalized = " ".join(_RE_NAME_PUNCT.sub(" ", folded).casefold().split())
    return TEAM_NAME_ALIASES.get(normalized, normalized)


def team_matches(team_name: str, side_description: str) -> bool:
    team = normalize_name_tokens(team_name)
    description = normalize_name_tokens(side_description)
    if not team or not description:
        return False
    if team == description:
        return True
    shorter, longer = (description, team) if len(description) <= len(team) else (team, description)
    l_tokens = longer.split()
    s_tokens = shorter.split()
    s_len = len(s_tokens)
    for i in range(len(l_tokens) - s_len + 1):
        if l_tokens[i : i + s_len] == s_tokens:
            if i > 0 and l_tokens[i - 1] in _DISQUALIFYING_TEAM_PREFIXES:
                continue
            if i + s_len < len(l_tokens) and l_tokens[i + s_len] in _DISQUALIFYING_TEAM_SUFFIXES:
                continue
            return True
    # Franchise mascot / distinctive token match (e.g. "DEN Nuggets" vs "Denver Nuggets", "GS Warriors" vs "Golden State Warriors")
    t_tokens = team.split()
    d_tokens = set(description.split())
    if t_tokens and d_tokens:
        mascot = t_tokens[-1]
        if (
            len(mascot) > 3
            and mascot not in {"club", "city", "team", "state", "united"}
            and mascot in d_tokens
        ):
            return True
    return False


def tennis_player_matches(player_name: str, exchange_name: str) -> bool:
    """Match a tennis player when one source includes an omitted middle name or inverted names."""
    player_tokens = _RE_WORD.findall(player_name.casefold())
    exchange_tokens = _RE_WORD.findall(exchange_name.casefold())
    return (
        len(player_tokens) >= 2
        and len(exchange_tokens) >= 2
        and (
            (player_tokens[0] == exchange_tokens[0] and player_tokens[-1] == exchange_tokens[-1])
            or set(player_tokens) == set(exchange_tokens)
        )
    )


def participant_matches(row: Mapping[str, Any], participant: str, exchange_name: str) -> bool:
    if team_matches(participant, exchange_name):
        return True
    sport = str(row.get("league") or row.get("sport") or "").casefold()
    return sport == "tennis" and tennis_player_matches(participant, exchange_name)


def lines_match(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 1e-6


@dataclass(frozen=True)
class SpreadSideMatch:
    """``side`` is "long", "short", or None. ``market_team`` says which of the row's teams the
    market's own team matched: "away", "home", "both" or "neither"."""

    side: str | None
    market_team: str


def match_spread_side(
    row: Mapping[str, Any], row_line: float, market_team: Any, market_line: Any
) -> SpreadSideMatch:
    """Which side of a spread market a row's selection-relative line picks.

    A spread market's own ``line``/``team`` always describe its LONG side; SHORT is the exact
    negation, the opponent's line. So the row's line matches long only when the picked team IS the
    market's team, and must equal the negated line otherwise. The caller has already checked that
    the selection is home/away and parsed the row's line.

    The market team must be exactly one of the row's two teams. Real-money bug 2026-11-25:
    Polymarket's "UCA" matched neither "Central Arkansas" nor "Jacksonville", so it was taken for the
    opponent and a "Central Arkansas -3.5" pick mapped to the short side, Jacksonville -3.5. A team
    matching both ("Louisiana" vs "Louisiana-Monroe") is the same hazard.
    """
    market_is_away = participant_matches(row, str(row.get("away_team") or ""), str(market_team))
    market_is_home = participant_matches(row, str(row.get("home_team") or ""), str(market_team))
    if market_is_away == market_is_home:
        return SpreadSideMatch(None, "both" if market_is_away else "neither")
    role = "home" if market_is_home else "away"
    selection = str(row.get("selection") or "").casefold()
    is_market_team = market_is_home if selection == "home" else market_is_away
    if is_market_team and lines_match(row_line, float(market_line)):
        return SpreadSideMatch("long", role)
    if not is_market_team and lines_match(row_line, -float(market_line)):
        return SpreadSideMatch("short", role)
    return SpreadSideMatch(None, role)
