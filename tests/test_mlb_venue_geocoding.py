"""Pin the venue geocoding table's coverage and correctness invariants."""

from __future__ import annotations

from model_prediction.features.mlb_venue_geocoding import (
    MLB_VENUE_LOCATIONS,
    venue_location,
)

# Real venue_names observed in data/mlb_statsapi/game_snapshots.jsonl
# (2026-08-27 audit) that are deliberately NOT sourced -- rare one-off
# exhibition sites, ~1% of real games combined. See module docstring.
KNOWN_UNSOURCED_VENUES = {
    "Bristol Motor Speedway",
    "Estadio Alfredo Harp Helu",
    "Field of Dreams",
    "Gocheok Sky Dome",
    "Journey Bank Ballpark",
    "Las Vegas Ballpark",
    "London Stadium",
    "Rickwood Field",
    "Tokyo Dome",
}


def test_unknown_venue_returns_none_not_a_guess() -> None:
    assert venue_location("Some Made Up Park") is None
    for name in KNOWN_UNSOURCED_VENUES:
        assert venue_location(name) is None, f"{name} should stay unsourced until verified"


def test_relocated_franchises_use_current_verified_coordinates() -> None:
    # These parks are physically different sites than a stale pre-2020
    # dataset would show -- verified against Wikipedia 2026-08-27.
    truist = venue_location("Truist Park")
    assert truist is not None
    assert round(truist.latitude, 1) == 33.9
    assert round(truist.longitude, 1) == -84.5

    sutter = venue_location("Sutter Health Park")
    assert sutter is not None
    assert round(sutter.latitude, 1) == 38.6

    globe_life = venue_location("Globe Life Field")
    assert globe_life is not None
    assert globe_life.timezone == "America/Chicago"


def test_dodger_stadium_sponsor_overlay_shares_real_coordinates() -> None:
    dodger = venue_location("Dodger Stadium")
    uniqlo = venue_location("UNIQLO Field at Dodger Stadium")
    assert dodger is not None and uniqlo is not None
    assert dodger.latitude == uniqlo.latitude
    assert dodger.longitude == uniqlo.longitude


def test_every_covered_venue_has_a_real_iana_timezone() -> None:
    valid_zones = {
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Phoenix",
        "America/Los_Angeles",
        "America/Detroit",
        "America/Toronto",
    }
    for name, loc in MLB_VENUE_LOCATIONS.items():
        assert loc.timezone in valid_zones, f"{name}: unexpected timezone {loc.timezone}"
        assert -90 <= loc.latitude <= 90
        assert -180 <= loc.longitude <= 180
