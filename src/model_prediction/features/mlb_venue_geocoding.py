"""Static MLB venue geocoding table (latitude / longitude / elevation / timezone).

Keyed by ``venue_name`` -- the same string ``data/mlb_statsapi/game_snapshots.jsonl``
already carries per game -- not by team, because a stadium's physical
location never changes even when the team playing there does (the
Athletics: Oakland Coliseum through 2024, Sutter Health Park from 2025).
Keying by venue name makes this table trivially point-in-time correct: look
up whatever venue a given snapshot actually recorded for that game, with no
team-history/relocation-date logic needed.

Coordinates sourced 2026-08-27 from Wikipedia's individual stadium articles
(spot-verified for every park that has physically relocated since a stale
2012 baseline dataset: Truist Park, Globe Life Field, LoanDepot Park, Sutter
Health Park, George M. Steinbrenner Field) plus a public MLB
address/lat-lon gist for the remaining stable-site parks -- see
``docs/ROADMAP.md``'s data-expansion backlog entry. Elevation figures are
round-number approximations (nearest ~50 ft), not survey-grade; timezone is
the park's real IANA zone, high confidence.

Deliberately excludes rare one-off/exhibition venues seen in
``game_snapshots.jsonl`` that were not verified this session (a handful of
games each; getting these wrong would quietly corrupt a tiny number of
rows, so they are left out rather than guessed): Tokyo Dome, London
Stadium, Gocheok Sky Dome, Field of Dreams, Rickwood Field, Bristol Motor
Speedway, Journey Bank Ballpark, Las Vegas Ballpark, Estadio Alfredo Harp
Helu. ("UNIQLO Field at Dodger Stadium" is included, not excluded -- it is a
sponsor-branding overlay on the real Dodger Stadium; verified all 62
occurrences in the snapshot file are Dodgers home games.) Callers must
treat a missing venue as
unavailable (neutral/prior fallback), never as coordinates 0,0.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VenueLocation:
    latitude: float
    longitude: float
    elevation_ft: float
    timezone: str
    roof: str  # "open" | "retractable" | "fixed_dome"


MLB_VENUE_LOCATIONS: dict[str, VenueLocation] = {
    "American Family Field": VenueLocation(43.0280, -87.9712, 635, "America/Chicago", "retractable"),
    "Angel Stadium": VenueLocation(33.8003, -117.8827, 160, "America/Los_Angeles", "open"),
    "Busch Stadium": VenueLocation(38.6226, -90.1928, 465, "America/Chicago", "open"),
    "Chase Field": VenueLocation(33.4455, -112.0667, 1100, "America/Phoenix", "retractable"),
    "Citi Field": VenueLocation(40.7571, -73.8458, 10, "America/New_York", "open"),
    "Citizens Bank Park": VenueLocation(39.9061, -75.1665, 20, "America/New_York", "open"),
    "Comerica Park": VenueLocation(42.3390, -83.0485, 600, "America/Detroit", "open"),
    "Coors Field": VenueLocation(39.7559, -104.9942, 5200, "America/Denver", "open"),
    "Daikin Park": VenueLocation(29.7573, -95.3555, 40, "America/Chicago", "retractable"),
    "Minute Maid Park": VenueLocation(
        29.7573, -95.3555, 40, "America/Chicago", "retractable"
    ),  # same site, prior name
    "Dodger Stadium": VenueLocation(34.0739, -118.2400, 500, "America/Los_Angeles", "open"),
    "UNIQLO Field at Dodger Stadium": VenueLocation(
        34.0739, -118.2400, 500, "America/Los_Angeles", "open"
    ),  # sponsor overlay, same physical site -- verified all 62 occurrences are Dodgers home games
    "Fenway Park": VenueLocation(42.3467, -71.0972, 20, "America/New_York", "open"),
    "Globe Life Field": VenueLocation(32.7475, -97.0842, 550, "America/Chicago", "retractable"),
    "Great American Ball Park": VenueLocation(39.0975, -84.5074, 490, "America/New_York", "open"),
    "Kauffman Stadium": VenueLocation(39.0517, -94.4803, 750, "America/Chicago", "open"),
    "LoanDepot Park": VenueLocation(25.7781, -80.2197, 10, "America/New_York", "retractable"),
    "loanDepot park": VenueLocation(
        25.7781, -80.2197, 10, "America/New_York", "retractable"
    ),  # lowercase variant seen in snapshots
    "Nationals Park": VenueLocation(38.8730, -77.0074, 10, "America/New_York", "open"),
    "Oracle Park": VenueLocation(37.7786, -122.3893, 10, "America/Los_Angeles", "open"),
    "Oriole Park at Camden Yards": VenueLocation(39.2839, -76.6217, 35, "America/New_York", "open"),
    "Petco Park": VenueLocation(32.7076, -117.1570, 30, "America/Los_Angeles", "open"),
    "PNC Park": VenueLocation(40.4469, -80.0057, 730, "America/New_York", "open"),
    "Progressive Field": VenueLocation(41.4962, -81.6852, 660, "America/New_York", "open"),
    "Rate Field": VenueLocation(41.8299, -87.6338, 595, "America/Chicago", "open"),
    "Guaranteed Rate Field": VenueLocation(
        41.8299, -87.6338, 595, "America/Chicago", "open"
    ),  # same site, prior name
    "Rogers Centre": VenueLocation(43.6414, -79.3894, 250, "America/Toronto", "retractable"),
    "Sutter Health Park": VenueLocation(38.5804, -121.5138, 20, "America/Los_Angeles", "open"),
    "Oakland Coliseum": VenueLocation(
        37.7516, -122.2005, 10, "America/Los_Angeles", "open"
    ),  # A's home through 2024
    "T-Mobile Park": VenueLocation(47.5914, -122.3325, 30, "America/Los_Angeles", "retractable"),
    "Target Field": VenueLocation(44.9817, -93.2777, 815, "America/Chicago", "open"),
    "Tropicana Field": VenueLocation(27.7683, -82.6534, 10, "America/New_York", "fixed_dome"),
    "George M. Steinbrenner Field": VenueLocation(
        27.9803, -82.5067, 15, "America/New_York", "open"
    ),  # Rays' 2025 temporary home
    "Truist Park": VenueLocation(33.8900, -84.4680, 1050, "America/New_York", "open"),
    "Wrigley Field": VenueLocation(41.9484, -87.6553, 600, "America/Chicago", "open"),
    "Yankee Stadium": VenueLocation(40.8296, -73.9262, 55, "America/New_York", "open"),
}


def venue_location(venue_name: str) -> VenueLocation | None:
    """None for any venue not in the verified table -- callers must fall
    back to a neutral default, never guess coordinates."""
    return MLB_VENUE_LOCATIONS.get(venue_name)
