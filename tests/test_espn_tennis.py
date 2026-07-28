from model_prediction.data_sources.espn import ESPNClient


def _scoreboard(*, singles_completed=True, doubles_completed=True, tournament="Roland Garros"):
    singles_competition = {
        "id": "c1",
        "date": "2026-05-30T13:00Z",
        "type": {"id": "1", "slug": "womens-singles", "text": "Women's Singles"},
        "round": {"displayName": "Round of 16"},
        "status": {"type": {"completed": singles_completed}},
        "competitors": [
            {"id": "101", "homeAway": "away", "winner": True, "athlete": {"displayName": "Player A"}},
            {"id": "102", "homeAway": "home", "winner": False, "athlete": {"displayName": "Player B"}},
        ],
    }
    doubles_competition = {
        "id": "c2",
        "date": "2026-05-30T15:00Z",
        "type": {"id": "3", "slug": "womens-doubles", "text": "Women's Doubles"},
        "round": {"displayName": "Round of 16"},
        "status": {"type": {"completed": doubles_completed}},
        "competitors": [
            {
                "id": "201-202",
                "homeAway": "away",
                "winner": True,
                "roster": {"athletes": [{"displayName": "Player C"}, {"displayName": "Player D"}]},
            },
            {
                "id": "203-204",
                "homeAway": "home",
                "winner": False,
                "roster": {"athletes": [{"displayName": "Player E"}, {"displayName": "Player F"}]},
            },
        ],
    }
    return {
        "events": [
            {
                "id": "e1",
                "date": "2026-05-30T10:00Z",
                "name": tournament,
                "groupings": [
                    {"grouping": {"displayName": "Women's Singles"}, "competitions": [singles_competition]},
                    {"grouping": {"displayName": "Women's Doubles"}, "competitions": [doubles_competition]},
                ],
            }
        ]
    }


def test_doubles_draws_are_excluded_entirely() -> None:
    matches = ESPNClient.completed_tennis_singles_matches(_scoreboard())
    assert len(matches) == 1
    assert matches[0]["winner"] == "Player A"
    assert matches[0]["loser"] == "Player B"
    assert matches[0]["winner_id"] == "101"
    assert matches[0]["event_id"] == "e1:c1"
    assert matches[0]["league"] == "WTA"


def _combined_tournament_scoreboard():
    """A shared ATP+WTA event, as ESPN actually returns it from BOTH the
    /tennis/atp and /tennis/wta site-API paths (verified live 2026-07-27
    against Brisbane International) -- the exact shape that caused every WTA
    player's combined-tournament matches to get silently misattributed to
    ATP when league was tagged by which endpoint served the payload instead
    of by each match's own grouping."""
    womens = {
        "id": "c1",
        "date": "2026-05-30T13:00Z",
        "type": {"slug": "womens-singles"},
        "round": {"displayName": "Final"},
        "status": {"type": {"completed": True}},
        "competitors": [
            {"id": "101", "homeAway": "away", "winner": True, "athlete": {"displayName": "Player A"}},
            {"id": "102", "homeAway": "home", "winner": False, "athlete": {"displayName": "Player B"}},
        ],
    }
    mens = {
        "id": "c2",
        "date": "2026-05-30T15:00Z",
        "type": {"slug": "mens-singles"},
        "round": {"displayName": "Final"},
        "status": {"type": {"completed": True}},
        "competitors": [
            {"id": "201", "homeAway": "away", "winner": True, "athlete": {"displayName": "Player X"}},
            {"id": "202", "homeAway": "home", "winner": False, "athlete": {"displayName": "Player Y"}},
        ],
    }
    return {
        "events": [
            {
                "id": "e1",
                "date": "2026-05-30T10:00Z",
                "name": "Brisbane International presented by ANZ",
                "groupings": [
                    {"grouping": {"displayName": "Women's Singles"}, "competitions": [womens]},
                    {"grouping": {"displayName": "Men's Singles"}, "competitions": [mens]},
                ],
            }
        ]
    }


def test_tour_is_derived_per_match_from_slug_not_from_which_endpoint_served_it() -> None:
    """Both the ATP and WTA scoreboard fetches for a combined tournament
    return the identical payload -- calling this once (as either endpoint's
    payload) must correctly split the women's match into WTA and the men's
    match into ATP, regardless of which site-API path the payload came from.
    """
    matches = ESPNClient.completed_tennis_singles_matches(_combined_tournament_scoreboard())
    by_winner = {row["winner"]: row["league"] for row in matches}
    assert by_winner == {"Player A": "WTA", "Player X": "ATP"}


def test_incomplete_singles_match_is_skipped() -> None:
    matches = ESPNClient.completed_tennis_singles_matches(_scoreboard(singles_completed=False))
    assert matches == []


def test_surface_is_inferred_from_known_tournament_names() -> None:
    clay = ESPNClient.completed_tennis_singles_matches(_scoreboard(tournament="Roland Garros"))
    grass = ESPNClient.completed_tennis_singles_matches(_scoreboard(tournament="Wimbledon"))
    hard = ESPNClient.completed_tennis_singles_matches(_scoreboard(tournament="US Open"))
    assert clay[0]["surface"] == "Clay"
    assert grass[0]["surface"] == "Grass"
    assert hard[0]["surface"] == "Hard"
