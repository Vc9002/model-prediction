"""Shared module-level state for the model_prediction CLI package.

Mechanical extraction from the former cli.py monolith (DD-6 split). This
module owns the pieces of state that several command-group modules share:

  * `_LEDGER_LOCK` -- the ONE process-wide lock serializing ledger appends
    across the concurrent forecast pools. It must remain a single object;
    tests pin `cli._LEDGER_LOCK.locked()`.
  * the sports constants -- tuples consumed across forecast/settle/daily.
  * `_LEDGER_LEAGUE_TO_ESPN` and `_TERMINAL_MARKET_STATES` -- settlement
    lookups.
  * `logger` -- hardcoded as logging.getLogger("model_prediction.cli") so
    every moved call site keeps emitting under the historical logger name.

`utc_now` and the other test-patched domain names deliberately do NOT live
here: they belong to the modules whose functions call them (forecast owns
the patched forecast-domain names), because monkeypatch.setattr only
affects lookups in the module the call site lives in.
"""

from __future__ import annotations

import logging
import threading

from ..data_sources.espn import SPORT_LEAGUES
from ..data_sources.polymarket_us import POLYMARKET_SPORT_LEAGUES

SPORTS = tuple(POLYMARKET_SPORT_LEAGUES)
ESPN_SPORTS = tuple(SPORT_LEAGUES)
ESPORTS_TITLES = ("lol", "cs2", "dota2", "valorant", "rainbow_six")
DAILY_LEARNED_SPORTS = ("mlb", "nba", "wnba", "nfl")
DAILY_INTERNATIONAL_BASEBALL_SPORTS = ("kbo", "npb")
FLAT_LEDGER_SPORTS = DAILY_LEARNED_SPORTS
# Sports whose _forecast_*_sport function writes BOTH main_ledger and
# flat_ledger unconditionally whenever log=True, regardless of which command
# (forecast/log vs. flat-forecast) ran -- every other sport only ever writes
# the one ledger matching is_flat. Any single-sport replace_today re-run must
# clear the *other* ledger too for sports in this set, or a second same-day
# run of the other command duplicates that sport's rows there (see the
# dual_ledger_sports handling in the forecast/log/flat-forecast dispatch).
DUAL_LEDGER_SPORTS = frozenset({"soccer", "tennis"})
RESEARCH_ONLY_DAILY_SPORTS = (
    "soccer",
    "tennis",
    *ESPORTS_TITLES,
    *DAILY_INTERNATIONAL_BASEBALL_SPORTS,
)

logger = logging.getLogger("model_prediction.cli")

_LEDGER_LOCK = threading.Lock()

# League value on a ledger row -> ESPN league key(s) to search for results.
# WORLD_CUP dropped 2026-07: tournament is over, no games left to forecast or settle.
_LEDGER_LEAGUE_TO_ESPN = {
    "MLB": ("MLB",),
    "NBA": ("NBA",),
    "WNBA": ("WNBA",),
    "NFL": ("NFL",),
    "SOCCER": (
        "EPL",
        "LA_LIGA",
        "BUNDESLIGA",
        "SERIE_A",
        "MLS",
        "UCL",
        "BRASILEIRAO",
        "BRAZIL_SERIE_B",
        "ARGENTINA",
        "ARGENTINA_2",
        "COLOMBIA",
        "CHILE",
        "URUGUAY",
        "ECUADOR",
        "PERU",
        "SUDAMERICANA",
        "FRIENDLIES",
        "CLUB_FRIENDLIES",
    ),
}

_TERMINAL_MARKET_STATES = {
    "MARKET_STATE_EXPIRED",
    "MARKET_STATE_RESOLVED",
    "MARKET_STATE_SETTLED",
}
